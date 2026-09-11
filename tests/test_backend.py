from __future__ import annotations

import io
import json
import multiprocessing
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from threading import Barrier

import httpx
import pytest
from filelock import FileLock, Timeout

from research_harness import registry
from research_harness.backend import Backend, BackendSettings
from research_harness.blobs import LocalBlobs, S3Blobs
from research_harness.cli import main
from research_harness.config import PipelineSpec
from research_harness.engine import run_pipeline
from research_harness.store import (
    MIGRATIONS,
    SCHEMA_VERSION,
    TABLES,
    PostgresDialect,
    SqliteDialect,
    Store,
    WriterBusy,
)
from research_harness.util import digest


def test_local_settings_are_default_and_postgres_requires_blob_settings(tmp_path):
    settings = BackendSettings.from_env({}, cwd=tmp_path)
    assert settings.mode == "local"
    assert settings.local_root == tmp_path / ".researchharness"
    with pytest.raises(ValueError, match="RH_BLOB_BUCKET"):
        BackendSettings.from_env(
            {"RH_DATABASE_URL": "postgresql://u:p@db.example/rh"}, cwd=tmp_path
        )
    settings = BackendSettings.from_env(
        {
            "RH_DATABASE_URL": "postgresql://postgres.ref:very-secret@pooler.example:5432/postgres",
            "RH_BLOB_BUCKET": "research-harness",
            "RH_BLOB_ENDPOINT": "https://ref.storage.example/storage/v1/s3",
            "RH_BLOB_ACCESS_KEY": "access-key-example",
            "RH_BLOB_SECRET_KEY": "secret-key-example",
            "RH_BLOB_REGION": "eu-west-1",
        },
        cwd=tmp_path,
    )
    described = settings.describe()
    assert settings.mode == described["mode"] == "postgres"
    assert described["blob_region"] == "eu-west-1"
    assert "very-secret" not in json.dumps(described)
    assert "secret-key-example" not in json.dumps(described)
    with pytest.raises(ValueError, match="postgresql://"):
        BackendSettings(
            database_url="mysql://u:p@h/db",
            blob_bucket="b",
            blob_access_key="a",
            blob_secret_key="s",
        ).validate()


def test_postgres_dialect_translates_placeholders_and_renders_identity_columns():
    dialect = PostgresDialect("postgresql://u:p@localhost/rh", "research_harness")
    assert (
        dialect.sql("SELECT * FROM runs WHERE id=? AND pipeline=?")
        == "SELECT * FROM runs WHERE id=%s AND pipeline=%s"
    )
    rendered = "\n".join(dialect.render(table) for table in TABLES)
    assert "GENERATED ALWAYS AS IDENTITY PRIMARY KEY" in rendered
    assert "seq BIGINT GENERATED ALWAYS AS IDENTITY" in rendered
    assert "{" not in rendered
    sqlite = "\n".join(SqliteDialect(Path("x")).render(table) for table in TABLES)
    assert "seq BIGINT" not in sqlite and "AUTOINCREMENT" in sqlite and "{" not in sqlite
    with pytest.raises(ValueError, match="identifier"):
        PostgresDialect("postgresql://u:p@h/db", "Bad-Schema")


def test_writer_lock_is_exclusive_and_sweeps_only_its_pipeline(tmp_path, clock, spec):
    other = spec.model_copy(update={"name": "other-pipeline"})
    with Store(tmp_path / "data", clock=clock) as store:
        abandoned = store.start_run(spec)
        store.start_source(abandoned, spec.sources[0])
        other_run = store.start_run(other)
        with store.writer(spec.name):
            details = store.run_details(abandoned)
            assert details["status"] == "interrupted"
            assert details["sources"][0]["status"] == "interrupted"
            assert store.run_details(other_run)["status"] == "running"
            with Store(tmp_path / "data", clock=clock) as second:
                with pytest.raises(RuntimeError, match="writer"), second.writer(spec.name):
                    pass
        with store.writer("source-probes", shared=True):
            assert store.run_details(other_run)["status"] == "running"


def test_registry_tracks_alternatives_adoption_and_runs(store, spec, clock, item):
    brief = "# Trade policy\n\nFind official export-control sources."
    question = registry.register_question(store, brief)
    assert question["created"] and question["title"] == "Trade policy"
    assert registry.register_question(store, brief)["created"] is False
    first = registry.register_pipeline(store, question["id"], spec, origin="manual", label="v1")
    assert first["created"] and first["status"] == "proposed" and first["spec"]["name"] == spec.name
    assert registry.register_pipeline(store, question["id"], spec)["created"] is False
    alternative = spec.model_copy(update={"description": "Alternative source set"})
    second = registry.register_pipeline(store, question["id"][:8], alternative)
    assert second["created"] and second["id"] != first["id"]

    assert (
        registry.adopt_pipeline(store, first["id"][:8], note="Best coverage")["status"] == "adopted"
    )
    assert registry.adopt_pipeline(store, second["id"])["status"] == "adopted"
    assert registry.get_pipeline(store, first["id"])["status"] == "superseded"
    events = [event["event"] for event in registry.pipeline_history(store, first["id"])]
    assert events == ["registered", "adopted", "superseded"]
    adopted = registry.list_pipelines(store, question_id=question["id"], status="adopted")
    assert [row["id"] for row in adopted] == [second["id"]]
    assert adopted[0]["sources"] == ["policy"] and adopted[0]["latest_run"] is None

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        with Store(
            store.root,
            dialect=store.dialect,
            blobs=store.blobs,
            clock=clock,
            dataset_scope=question["id"],
        ) as scoped:
            run_pipeline(spec, scoped, client=client, public_only=False, clock=clock)
    by_id = {row["id"]: row for row in registry.list_pipelines(store, question_id=question["id"])}
    assert by_id[first["id"]]["latest_run"]["status"] == "succeeded"
    assert by_id[second["id"]]["latest_run"] is None

    assert registry.retire_pipeline(store, first["id"])["status"] == "retired"
    with pytest.raises(ValueError, match="retired"):
        registry.adopt_pipeline(store, first["id"])
    assert registry.set_status(store, first["id"], "proposed")["status"] == "proposed"
    overview = registry.question_overview(store, question["id"])
    assert len(overview["pipelines"]) == 2 and overview["discoveries"] == []
    summary = registry.list_questions(store)[0]
    assert summary["pipelines"] == 2 and summary["adopted"] == 1
    with pytest.raises(ValueError, match="Not a registry id"):
        registry.get_pipeline(store, "not-an-id")
    with pytest.raises(ValueError, match="No pipeline_versions"):
        registry.get_pipeline(store, "ffffff")
    with pytest.raises(ValueError, match="Unknown status"):
        registry.list_pipelines(store, status="approved")


class FakeError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.puts = 0

    def head_object(self, Bucket: str, Key: str) -> dict:
        if (Bucket, Key) not in self.objects:
            raise FakeError("404")
        return {}

    def put_object(self, Bucket: str, Key: str, Body: bytes, IfNoneMatch=None, **_: object) -> None:
        if IfNoneMatch == "*" and (Bucket, Key) in self.objects:
            raise FakeError("PreconditionFailed")
        self.puts += 1
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket: str, Key: str) -> dict:
        if (Bucket, Key) not in self.objects:
            raise FakeError("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def get_paginator(self, _name: str):
        objects = self.objects

        class Paginator:
            def paginate(self, Bucket: str, Prefix: str):
                yield {
                    "Contents": [
                        {"Key": key}
                        for bucket, key in objects
                        if bucket == Bucket and key.startswith(Prefix)
                    ]
                }

        return Paginator()


def test_s3_blobs_deduplicate_verify_integrity_and_count(tmp_path, clock):
    client = FakeS3()
    blobs = S3Blobs(client, "bucket", "raw")
    hashed = blobs.put(b"body")
    assert hashed == digest(b"body") and blobs.key(hashed) == f"raw/{hashed[:2]}/{hashed}"
    assert blobs.put(b"body") == hashed and client.puts == 1
    assert blobs.get(hashed) == b"body" and blobs.count() == 1
    client.objects[("bucket", blobs.key(hashed))] = b"tampered"
    with pytest.raises(RuntimeError, match="integrity"):
        blobs.get(hashed)
    with pytest.raises(RuntimeError, match="integrity"):
        blobs.put(b"body")
    assert client.puts == 1
    assert client.objects[("bucket", blobs.key(hashed))] == b"tampered"
    with pytest.raises(FileNotFoundError):
        blobs.get("0" * 64)
    with pytest.raises(ValueError):
        blobs.get("not-a-hash")
    with Store(tmp_path / "data", clock=clock, blobs=S3Blobs(client, "bucket")) as store:
        stored = store.put_blob(b"through the store")
        assert store.read_blob(stored) == b"through the store"
        assert store.describe()["blobs"] == "bucket bucket/raw"


@pytest.mark.parametrize("racing_body", [b"body", b"tampered"])
def test_s3_deduplication_race_verifies_without_overwriting(racing_body):
    class RacingS3(FakeS3):
        def head_object(self, Bucket, Key):
            self.objects[(Bucket, Key)] = racing_body
            raise FakeError("404")

    client = RacingS3()
    blobs = S3Blobs(client, "bucket")
    if racing_body == b"body":
        assert blobs.put(b"body") == digest(b"body")
    else:
        with pytest.raises(RuntimeError, match="integrity"):
            blobs.put(b"body")
    assert client.puts == 0
    assert client.objects[("bucket", blobs.key(digest(b"body")))] == racing_body


@pytest.mark.parametrize("version", [1, 4])
def test_forward_migration_repairs_historical_missing_index_without_losing_runs(tmp_path, version):
    dialect = SqliteDialect(tmp_path)
    with sqlite3.connect(dialect.location) as db:
        db.executescript((Path(__file__).parent / "fixtures/sqlite-v1-ee2148b.sql").read_text())
        for number, statements in MIGRATIONS.items():
            if 1 < number <= version:
                for statement in statements:
                    db.execute(dialect.render(statement))
        db.execute(f"PRAGMA user_version={version}")
        db.execute(
            "INSERT INTO runs (id,pipeline,config_hash,config_json,started_at,status) VALUES (?,?,?,?,?,?)",
            ("preserved", "saved-pipeline", "hash", "{}", "2026-09-01T00:00:00Z", "succeeded"),
        )
    for _ in range(2):
        with Store(tmp_path) as upgraded:
            assert upgraded.describe()["schema_version"] == SCHEMA_VERSION
            indexes = {row["name"] for row in upgraded.query("PRAGMA index_list('runs')")}
            assert "runs_pipeline" in indexes
            assert (
                upgraded.query_one("SELECT * FROM runs WHERE id='preserved'")["status"]
                == "succeeded"
            )


def test_old_sqlite_version_fails_before_creating_storage(tmp_path, monkeypatch):
    root = tmp_path / "too-old"
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 0))
    with pytest.raises(RuntimeError, match="SQLite 3.35"):
        Store(root)
    assert not root.exists()


def test_blob_deduplication_rejects_corruption_in_the_actual_backend(store, monkeypatch):
    body = b"original stored fixture"
    hashed = store.put_blob(body)
    blobs = store.blobs
    if isinstance(blobs, LocalBlobs):
        blobs.path(hashed).write_bytes(b"tampered")
    else:
        assert isinstance(blobs, S3Blobs)
        blobs.client.put_object(Bucket=blobs.bucket, Key=blobs.key(hashed), Body=b"tampered")
    with pytest.raises(RuntimeError, match="integrity"):
        store.put_blob(body)
    if isinstance(blobs, S3Blobs):
        # Force a stale HEAD result to exercise the endpoint's conditional PUT as well.
        monkeypatch.setattr(blobs, "exists", lambda _: False)
        with pytest.raises(RuntimeError, match="integrity"):
            store.put_blob(body)
        response = blobs.client.get_object(Bucket=blobs.bucket, Key=blobs.key(hashed))
        with response["Body"] as stream:
            assert stream.read() == b"tampered"
    else:
        assert blobs.path(hashed).read_bytes() == b"tampered"


def test_forward_index_repair_also_applies_to_already_upgraded_shared_stores(store, spec, clock):
    run_id = store.start_run(spec)
    store.finish_run(run_id, "succeeded")
    with store.transaction():
        store.execute("DROP INDEX runs_pipeline")
        if store.mode == "sqlite":
            store.execute("PRAGMA user_version=4")
        else:
            store.execute("DELETE FROM schema_migrations WHERE version=5")
    with Store(store.root, dialect=store.dialect, blobs=store.blobs, clock=clock) as upgraded:
        assert upgraded.describe()["schema_version"] == SCHEMA_VERSION
        indexes = (
            upgraded.query("PRAGMA index_list('runs')")
            if upgraded.mode == "sqlite"
            else upgraded.query(
                "SELECT indexname AS name FROM pg_indexes WHERE schemaname=current_schema() AND tablename='runs'"
            )
        )
        assert "runs_pipeline" in {row["name"] for row in indexes}
        assert upgraded.run_details(run_id)["status"] == "succeeded"


def test_registration_does_not_hide_unrelated_constraints_or_abort_outer_transaction(store, spec):
    with store.transaction():
        question = registry.register_question(store, "Constraint recovery")
        with pytest.raises(Exception, match="(?i)foreign key"):
            registry.register_pipeline(store, question["id"], spec, proposal_id="missing-proposal")
        assert store.count("pipeline_versions") == store.count("pipeline_events") == 0
        pipeline = registry.register_pipeline(store, question["id"], spec)
    assert pipeline["created"] is True
    assert len(registry.pipeline_history(store, pipeline["id"])) == 1


def test_writer_scopes_readers_and_reentry_preserve_live_runs(store, spec, clock):
    with Store(store.root, dialect=store.dialect, blobs=store.blobs, clock=clock) as other:
        with store.writer(spec.name):
            run_id = store.start_run(spec)
            for shared in (False, True):
                with store.writer(spec.name, shared=shared):
                    assert store.run_details(run_id)["status"] == "running"
                with pytest.raises(WriterBusy), other.writer(spec.name, shared=shared):
                    pass
            with other.writer("another-pipeline"):
                assert store.run_details(run_id)["status"] == "running"
            store.finish_run(run_id, "succeeded")
        with store.writer(spec.name, shared=True):
            with store.writer(spec.name, shared=True), other.writer(spec.name, shared=True):
                assert store.run_details(run_id)["status"] == "succeeded"
            with pytest.raises(WriterBusy), store.writer(spec.name):
                pass
            with pytest.raises(WriterBusy), other.writer(spec.name):
                pass
        with pytest.raises(RuntimeError, match="fixture error"):
            with store.writer(spec.name), store.writer(spec.name):
                raise RuntimeError("fixture error")
        with other.writer(spec.name):
            assert other.run_details(run_id)["status"] == "succeeded"


def test_new_sqlite_locks_remain_exclusive_against_legacy_writers(tmp_path):
    with Store(tmp_path) as store:
        with FileLock(tmp_path / "writer.lock", timeout=0):
            for shared in (True, False):
                with pytest.raises(WriterBusy), store.writer("current", shared=shared):
                    pass
        for shared in (True, False):
            with store.writer("current", shared=shared):
                with pytest.raises(Timeout), FileLock(tmp_path / "writer.lock", timeout=0):
                    pass


def _hold_writer_in_process(root, shared, ready, release):
    with Store(root) as store, store.writer("held", shared=shared):
        ready.set()
        release.wait(20)


@pytest.mark.parametrize("shared", [True, False])
def test_sqlite_scope_locks_work_across_processes_and_release_after_termination(tmp_path, shared):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    with Store(tmp_path) as store:
        process = context.Process(
            target=_hold_writer_in_process, args=(tmp_path, shared, ready, release)
        )
        process.start()
        try:
            assert ready.wait(10)
            with store.writer("independent"):
                pass
            if shared:
                with store.writer("held", shared=True):
                    pass
            else:
                with pytest.raises(WriterBusy), store.writer("held", shared=True):
                    pass
            with pytest.raises(WriterBusy), store.writer("held"):
                pass
        finally:
            process.terminate()
            process.join(timeout=5)
        assert not process.is_alive()
        with store.writer("held"):
            pass


@pytest.mark.parametrize("nested", [False, True])
def test_concurrent_registration_returns_one_identity_and_one_event(store, spec, clock, nested):
    start = Barrier(2)

    def register(number):
        with Store(store.root, dialect=store.dialect, blobs=store.blobs, clock=clock) as other:
            start.wait(timeout=5)
            with other.transaction() if nested else nullcontext():
                question = registry.register_question(
                    other, "Concurrent registration", title=f"Owner {number}"
                )
                pipeline = registry.register_pipeline(
                    other, question["id"], spec, notes=f"Owner {number}"
                )
            return question, pipeline

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(register, range(2)))
    assert len({question["id"] for question, _ in results}) == 1
    assert len({pipeline["id"] for _, pipeline in results}) == 1
    assert sum(question["created"] for question, _ in results) == 1
    assert sum(pipeline["created"] for _, pipeline in results) == 1
    assert len({question["title"] for question, _ in results}) == 1
    assert len({pipeline["notes"] for _, pipeline in results}) == 1
    assert [event["event"] for event in registry.pipeline_history(store, results[0][1]["id"])] == [
        "registered"
    ]


def test_cli_lists_latest_run_from_the_correct_local_question(
    tmp_path, spec, item, clock, monkeypatch, capsys
):
    backend = Backend.local(tmp_path / "backend", clock=clock)
    monkeypatch.setenv("RH_LOCAL_ROOT", str(backend.settings.local_root))
    monkeypatch.delenv("RH_DATABASE_URL", raising=False)
    with backend.open_registry() as store:
        questions = [registry.register_question(store, brief) for brief in ("First", "Second")]
        pipelines = [
            registry.register_pipeline(store, question["id"], spec) for question in questions
        ]
    runs = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        for question in questions:
            with backend.pipeline_store(spec, None, question_id=question["id"]) as data:
                run_pipeline(spec, data, client=client, public_only=False, clock=clock)
                runs.append(data.latest_run(spec.name, spec.fingerprint())["id"])
            clock.advance()
    assert runs[0] != runs[1]
    for index, question in enumerate(questions):
        assert main(["pipelines", "list", "--question", question["id"]]) == 0
        listed = json.loads(capsys.readouterr().out)
        assert listed[0]["id"] == pipelines[index]["id"]
        assert listed[0]["latest_run"]["id"] == runs[index]
        assert listed[0]["latest_run"]["status"] == "succeeded"
        assert main(["questions", "show", question["id"]]) == 0
        overview = json.loads(capsys.readouterr().out)
        assert overview["pipelines"][0]["latest_run"]["id"] == runs[index]


def test_cli_registry_commands_and_registry_ids_resolve(tmp_path, spec, monkeypatch, capsys):
    monkeypatch.setenv("RH_LOCAL_ROOT", str(tmp_path / "state"))
    monkeypatch.delenv("RH_DATABASE_URL", raising=False)

    def run(*argv: str):
        code = main(list(argv))
        captured = capsys.readouterr()
        return code, json.loads(captured.out) if captured.out.strip() else None, captured.err

    code, question, _ = run(
        "questions", "add", "Find official trade-policy sources", "--title", "Trade"
    )
    assert code == 0 and question["created"] and question["title"] == "Trade"
    path = tmp_path / "pipeline.json"
    path.write_text(json.dumps(spec.model_dump(mode="json")))
    code, version, _ = run(
        "pipelines", "register", str(path), "--question", question["id"][:8], "--label", "hand-made"
    )
    assert code == 0 and version["status"] == "proposed" and version["label"] == "hand-made"
    code, listed, _ = run("pipelines", "list", "--question", question["id"])
    assert code == 0 and [row["id"] for row in listed] == [version["id"]]
    code, valid, _ = run("validate", version["id"][:10])
    assert code == 0 and valid["pipeline"] == spec.name and valid["backend"] == "local"
    assert valid["registry"]["pipeline_version_id"] == version["id"]
    assert valid["data_dir"].endswith(f"state/pipelines/{question['id']}/{spec.name}")
    code, adopted, _ = run("pipelines", "adopt", version["id"], "--note", "go")
    assert code == 0 and adopted["status"] == "adopted"
    code, exported, _ = run(
        "pipelines", "export", version["id"], "--out", str(tmp_path / "out.json")
    )
    assert code == 0 and exported["status"] == "adopted"
    restored = PipelineSpec.model_validate_json((tmp_path / "out.json").read_text())
    assert restored.fingerprint() == version["fingerprint"]
    code, shown, _ = run("questions", "show", question["id"])
    assert code == 0 and shown["pipelines"][0]["status"] == "adopted"
    code, history, _ = run("pipelines", "history", version["id"])
    assert [event["event"] for event in history] == ["registered", "adopted"]
    code, info, _ = run("backend", "info")
    assert code == 0 and info["mode"] == "local"
    code, check, _ = run("backend", "check")
    assert code == 0 and check["blob_round_trip"] is True and check["pipeline_versions"] == 1
    code, _, err = run("validate", "missing.json")
    assert code == 1 and "not found" in err
    code, _, err = run("pipelines", "show", "ffffff")
    assert code == 1 and "No pipeline_versions" in err
    assert not (Path.cwd() / ".researchharness" / "registry").exists()


def test_legacy_registered_data_remains_explicitly_readable(
    tmp_path, spec, item, clock, monkeypatch, capsys
):
    backend = Backend.local(tmp_path / "state", clock=clock)
    monkeypatch.setenv("RH_LOCAL_ROOT", str(backend.settings.local_root))
    monkeypatch.delenv("RH_DATABASE_URL", raising=False)
    with backend.open_registry() as store:
        question = registry.register_question(store, "Legacy policy case")
        version = registry.register_pipeline(store, question["id"], spec)
    with backend.pipeline_store(spec, None) as store:
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
        ) as client:
            collected = run_pipeline(spec, store, client=client, public_only=False, clock=clock)
        legacy_run = store.run_details(collected["run_id"])

    def run(*argv):
        code = main(list(argv))
        captured = capsys.readouterr()
        assert code == 0, captured.err
        return json.loads(captured.out)

    current = run("status", version["id"])
    assert current["recent_runs"] == []
    assert current["legacy_unscoped_data"]["run_count"] == 1
    assert current["legacy_unscoped_data"]["automatically_imported"] is False
    assert current["reading_legacy_unscoped"] is False
    legacy = run("status", version["id"], "--legacy-unscoped")
    assert legacy["recent_runs"][0]["id"] == collected["run_id"]
    assert legacy["reading_legacy_unscoped"] is True
    scoped_export = tmp_path / "scoped.jsonl"
    legacy_export = tmp_path / "legacy.jsonl"
    exported = run("export", version["id"], "--out", str(scoped_export))
    assert exported["legacy_unscoped_data"]["run_count"] == 1
    manifest = json.loads(Path(exported["manifest"]).read_text())
    assert manifest["legacy_unscoped_data"] == exported["legacy_unscoped_data"]
    assert manifest["dataset_scope"] == question["id"]
    legacy_result = run("export", version["id"], "--out", str(legacy_export), "--legacy-unscoped")
    assert legacy_result["reading_legacy_unscoped"] is True
    assert legacy_result["dataset_scope"] is None
    assert scoped_export.read_text() == ""
    assert json.loads(legacy_export.read_text())["data"]["id"] == item["id"]
    replayed = tmp_path / "replay.json"
    run(
        "replay",
        version["id"],
        "--run-id",
        collected["run_id"],
        "--out",
        str(replayed),
        "--legacy-unscoped",
    )
    assert len(json.loads(replayed.read_text())["records"]) == 1
    with backend.pipeline_store(spec, None) as store:
        assert store.run_details(collected["run_id"]) == legacy_run
        assert store.count("runs") == store.count("observations") == 1


@pytest.mark.parametrize("command", ["status", "replay"])
def test_legacy_cli_run_lookup_rejects_modern_scoped_run(
    tmp_path, spec, clock, monkeypatch, capsys, command
):
    backend = Backend.local(tmp_path / "state", clock=clock)
    monkeypatch.setenv("RH_LOCAL_ROOT", str(backend.settings.local_root))
    monkeypatch.delenv("RH_DATABASE_URL", raising=False)
    with backend.open_registry() as store:
        question = registry.register_question(store, "Legacy question")
        version = registry.register_pipeline(store, question["id"], spec)
    # A shared physical database models how Postgres holds legacy and modern rows together.
    root = backend.settings.local_root / "pipelines" / spec.name
    with Store(root, dataset_scope="a" * 32, clock=clock) as scoped:
        other_run = scoped.start_run(spec)
    args = [command, version["id"], "--run-id", other_run, "--legacy-unscoped"]
    output = tmp_path / "replayed.json"
    if command == "replay":
        args.extend(["--out", str(output)])
    assert main(args) == 1
    captured = capsys.readouterr()
    assert "does not belong to this pipeline dataset" in captured.err
    assert captured.out == "" and not output.exists()
