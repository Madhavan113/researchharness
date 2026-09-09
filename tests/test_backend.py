from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest

from research_harness import registry
from research_harness.backend import Backend, BackendSettings
from research_harness.blobs import S3Blobs
from research_harness.cli import main
from research_harness.config import PipelineSpec
from research_harness.engine import run_pipeline
from research_harness.store import TABLES, PostgresDialect, SqliteDialect, Store
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

    def put_object(self, Bucket: str, Key: str, Body: bytes, **_: object) -> None:
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
    with pytest.raises(FileNotFoundError):
        blobs.get("0" * 64)
    with pytest.raises(ValueError):
        blobs.get("not-a-hash")
    with Store(tmp_path / "data", clock=clock, blobs=S3Blobs(client, "bucket")) as store:
        stored = store.put_blob(b"through the store")
        assert store.read_blob(stored) == b"through the store"
        assert store.describe()["blobs"] == "bucket bucket/raw"


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
