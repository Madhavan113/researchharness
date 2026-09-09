"""Read-only, independently specified collection/export checks.

This is a trusted controller API, never a candidate tool. It deliberately avoids
JobService.get_job(), Store.writer(), and the application's export selector: the
first two may recover/write state and the last would duplicate the implementation
being checked. Snapshots contain private dataset records and must stay outside a
candidate workspace. Reports are separate from discovery/optimization scores.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_harness.backend import Backend
from research_harness.blobs import LocalBlobs
from research_harness.config import PipelineSpec
from research_harness.store import SCHEMA_VERSION
from research_harness.util import canonical_json, digest, parse_timestamp, timestamp

Terminal = Literal["succeeded", "failed", "cancelled", "interrupted"]


class _Expected(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceOutcome(_Expected):
    status: Literal["succeeded", "degraded", "failed", "cancelled", "interrupted", "not_started"]
    observations: int | None = Field(default=None, ge=0)


class CollectionExpectation(_Expected):
    job_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    status: Terminal
    run_id: str | None = None
    run_exists: bool = True
    source_id: str | None = None
    due_only: bool = False
    sources: dict[str, SourceOutcome]


class ExportExpectation(_Expected):
    job_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    as_of: str
    kind: Literal["market", "orderbook", "document", "observation"] | None = None
    status: Terminal = "succeeded"
    record_count: int | None = Field(default=None, ge=0)
    returned_manifest: dict[str, Any] | None = None


class WorkflowExpectation(_Expected):
    pipeline_version_id: str = Field(min_length=1)
    pipeline_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    collections: list[CollectionExpectation] = Field(default_factory=list)
    exports: list[ExportExpectation] = Field(default_factory=list)
    # The trusted controller captures before/after around its actual replay/reopen.
    # Snapshots alone cannot prove that an external action was attempted.
    replay_job_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def requests(self):
        requests = self.collections + self.exports
        ids = [request.job_id for request in requests]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Specify at least one job and no repeated job ids")
        if len(set(self.replay_job_ids)) != len(self.replay_job_ids) or not set(
            self.replay_job_ids
        ).issubset(ids):
            raise ValueError("Replay ids must uniquely name evaluated jobs")
        for request in self.exports:
            parse_timestamp(request.as_of)
        return self


@dataclass
class WorkflowSnapshot:
    question_id: str
    pipeline_version_id: str
    pipeline: dict[str, Any]
    jobs: list[dict[str, Any]]
    operations: list[dict[str, Any]]
    tables: dict[str, list[dict[str, Any]]]
    registry_stable: bool
    blobs: dict[str, bytes] = field(repr=False)
    blob_errors: dict[str, str]

    def fingerprint(self) -> str:
        return digest(
            canonical_json(
                {
                    "question_id": self.question_id,
                    "pipeline_version_id": self.pipeline_version_id,
                    "pipeline": self.pipeline,
                    "jobs": self.jobs,
                    "operations": self.operations,
                    "tables": self.tables,
                    "registry_stable": self.registry_stable,
                    "blobs": {key: digest(value) for key, value in self.blobs.items()},
                    "blob_errors": self.blob_errors,
                }
            )
        )


@contextmanager
def _reader(backend: Backend, directory: Path):
    """Never create a database, migrate a schema, or recover a running writer."""
    db = None
    try:
        if backend.mode == "local":
            path = directory / "harness.sqlite3"
            if not path.is_file():
                yield None
                return
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            sql = lambda statement: statement  # noqa: E731
        else:
            import psycopg
            from psycopg.rows import dict_row

            db = psycopg.connect(
                backend.settings.database_url,
                row_factory=dict_row,
                prepare_threshold=None,
                autocommit=True,
            )
            db.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            # BackendSettings validates this schema identifier before a Backend exists.
            db.execute(f'SET LOCAL search_path TO "{backend.settings.schema}"')
            version = db.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            ).fetchone()["version"]
            sql = lambda statement: statement.replace("?", "%s")  # noqa: E731
        if version != SCHEMA_VERSION:
            raise ValueError(f"Workflow evaluation requires existing schema {SCHEMA_VERSION}")

        def query(statement, params=()):
            return [dict(row) for row in db.execute(sql(statement), params).fetchall()]

        yield query
    finally:
        if db is not None:
            try:
                db.rollback()
            finally:
                db.close()


def _registry(backend, question_id, pipeline_version_id):
    with _reader(backend, backend.settings.local_root / "registry") as query:
        if query is None:
            raise ValueError("No authoritative research registry exists")
        rows = query(
            "SELECT * FROM pipeline_versions WHERE id=? AND question_id=?",
            (pipeline_version_id, question_id),
        )
        if len(rows) != 1:
            raise ValueError("Pipeline is unavailable in this research question")
        jobs = query(
            "SELECT * FROM research_jobs WHERE question_id=? AND pipeline_version_id=? ORDER BY id",
            (question_id, pipeline_version_id),
        )
        operations = query(
            """SELECT o.* FROM discovery_operations o JOIN research_jobs j
               ON j.discovery_id=o.discovery_id AND j.operation_id=o.operation_id
               WHERE j.question_id=? AND j.pipeline_version_id=? ORDER BY o.discovery_id,o.operation_id""",
            (question_id, pipeline_version_id),
        )
        return rows[0], jobs, operations


def capture_snapshot(
    backend: Backend, *, question_id: str, pipeline_version_id: str
) -> WorkflowSnapshot:
    """Capture authoritative state for a controller-bound question, without mutation.

    Local registry/data files have separate read transactions. A changed registry
    marks the result unstable; use quiescent jobs for conclusive acceptance checks.
    The controller, not a model-supplied argument, must supply the question binding.
    """
    if len(question_id) != 32 or any(c not in "0123456789abcdef" for c in question_id):
        raise ValueError("A canonical controller-bound question id is required")
    registry_before = _registry(backend, question_id, pipeline_version_id)
    pipeline, jobs, operations = registry_before
    spec = PipelineSpec.model_validate_json(pipeline["spec_json"])
    directory = backend.settings.local_root / "pipelines" / question_id / spec.name
    scope = f"{question_id}/{spec.name}"
    tables = {key: [] for key in ("runs", "sources", "captures", "versions", "observations")}
    with _reader(backend, directory) as query:
        if query is not None:
            tables["runs"] = query("SELECT * FROM runs WHERE pipeline=? ORDER BY id", (scope,))
            seq = "s.rowid" if backend.mode == "local" else "s.seq"
            tables["sources"] = query(
                f"""SELECT s.*, {seq} AS evaluation_sequence FROM source_runs s
                    JOIN runs r ON r.id=s.run_id WHERE r.pipeline=? ORDER BY s.id""",
                (scope,),
            )
            tables["captures"] = query(
                """SELECT c.* FROM captures c JOIN source_runs s ON s.id=c.source_run_id
                   JOIN runs r ON r.id=s.run_id WHERE r.pipeline=? ORDER BY c.id""",
                (scope,),
            )
            tables["versions"] = query(
                "SELECT * FROM versions WHERE pipeline=? ORDER BY id", (scope,)
            )
            tables["observations"] = query(
                """SELECT o.* FROM observations o JOIN versions v ON v.id=o.version_id
                   WHERE v.pipeline=? ORDER BY o.id""",
                (scope,),
            )
    blobs: dict[str, bytes] = {}
    errors: dict[str, str] = {}
    references = {f"data/{row['body_hash']}" for row in tables["captures"] if row["body_hash"]}
    for job in jobs:
        if job["kind"] == "export" and job["result_json"]:
            result = json.loads(job["result_json"])
            for ref in result.get("artifact_refs", []):
                references.add(f"registry/{ref.get('sha256')}")
    for reference in sorted(references):
        kind, hashed = reference.split("/", 1)
        blob_store = (
            backend.blobs()
            if backend.mode == "postgres"
            else LocalBlobs(
                (directory if kind == "data" else backend.settings.local_root / "registry") / "raw"
            )
        )
        try:
            blobs[reference] = blob_store.get(hashed)
        except (OSError, RuntimeError, ValueError) as exc:
            errors[reference] = type(exc).__name__
    return WorkflowSnapshot(
        question_id,
        pipeline_version_id,
        pipeline,
        jobs,
        operations,
        tables,
        registry_before == _registry(backend, question_id, pipeline_version_id),
        blobs,
        errors,
    )


class _Checks:
    def __init__(self):
        self.items = []

    def add(self, name: str, passed: bool | None, detail: Any = None):
        self.items.append(
            {
                "check": name,
                "status": "unknown" if passed is None else "passed" if passed else "failed",
                "detail": detail,
            }
        )


def _index(rows):
    return {row["id"]: row for row in rows}


def _observations(snapshot, checks):
    """Validate publication lineage, then materialize each observation independently."""
    tables = snapshot.tables
    runs, sources, captures, versions = (
        _index(tables[key]) for key in ("runs", "sources", "captures", "versions")
    )
    records = []
    failures = []
    for observation in tables["observations"]:
        try:
            version = versions[observation["version_id"]]
            source = sources[observation["source_run_id"]]
            capture = captures[observation["capture_id"]]
            run = runs[source["run_id"]]
            run_spec = PipelineSpec.model_validate_json(run["config_json"])
            original_source = next(s for s in run_spec.sources if s.id == source["source_id"])
            data = json.loads(version["data_json"])
            content_hash = digest(
                canonical_json({"data": data, "published_at": version["published_at"]})
            )
            version_id = digest(
                canonical_json(
                    [
                        version["pipeline"],
                        version["source_id"],
                        version["kind"],
                        version["record_key"],
                        content_hash,
                        version["normalizer_version"],
                    ]
                )
            )
            valid = (
                source["status"] == "succeeded"
                and capture["source_run_id"] == source["id"]
                and capture["source_id"] == source["source_id"] == version["source_id"]
                and run["pipeline"] == version["pipeline"]
                and run["config_hash"] == run_spec.fingerprint()
                and source["config_hash"] == original_source.fingerprint()
                and version["id"] == version_id
                and version["content_hash"] == content_hash
                and observation["observed_at"] == capture["observed_at"]
                and observation["available_at"] == source["finished_at"]
                and parse_timestamp(observation["observed_at"])
                <= parse_timestamp(observation["available_at"])
                and f"data/{capture['body_hash']}" in snapshot.blobs
            )
            if not valid:
                failures.append(observation["id"])
            records.append(
                {
                    "observation_id": observation["id"],
                    "source_id": version["source_id"],
                    "kind": version["kind"],
                    "key": version["record_key"],
                    "version_id": version["id"],
                    "content_hash": version["content_hash"],
                    "normalizer_version": version["normalizer_version"],
                    "observed_at": observation["observed_at"],
                    "available_at": observation["available_at"],
                    "published_at": version["published_at"],
                    "data": data,
                    "lineage": {
                        "capture_id": capture["id"],
                        "source_run_id": source["id"],
                        "source_config_hash": source["config_hash"],
                        "source_url": capture["url"],
                        "body_sha256": capture["body_hash"],
                    },
                }
            )
        except (KeyError, ValueError, TypeError, StopIteration):
            failures.append(observation["id"])
    checks.add("publication.lineage", not failures, {"invalid_observation_ids": failures})
    checks.add("capture.integrity", not snapshot.blob_errors, snapshot.blob_errors)
    return records


def _job(snapshot, request, kind, checks, expected_request):
    label = f"{kind}.{request.job_id}"
    job = _index(snapshot.jobs).get(request.job_id)
    checks.add(f"{label}.exists_in_scope", job is not None)
    if job is None:
        return None
    expected_hash = digest(canonical_json({"kind": kind, "request": expected_request}))
    operations = [
        row
        for row in snapshot.operations
        if row["discovery_id"] == job["discovery_id"]
        and row["operation_id"] == request.operation_id
    ]
    valid = (
        job["question_id"] == snapshot.question_id
        and job["pipeline_version_id"] == snapshot.pipeline_version_id
        and job["operation_id"] == request.operation_id
        and job["kind"] == kind
        and json.loads(job["request_json"]) == expected_request
        and job["request_hash"] == expected_hash
        and len(operations) == 1
    )
    if operations:
        operation = operations[0]
        valid = valid and (
            operation["kind"] == f"job:{kind}"
            and operation["request_hash"] == expected_hash
            and json.loads(operation["request_json"]) == expected_request
            and operation["status"] == "completed"
            and json.loads(operation["result_json"])["job_id"] == job["id"]
        )
    checks.add(f"{label}.request_identity", valid)
    checks.add(
        f"{label}.terminal_status",
        None if job["status"] in {"queued", "running"} else job["status"] == request.status,
        {"expected": request.status, "observed": job["status"]},
    )
    return job


def _collection(snapshot, spec, request, checks):
    label = f"collection.{request.job_id}"
    job = _job(
        snapshot,
        request,
        "collection",
        checks,
        {
            "pipeline_version_id": snapshot.pipeline_version_id,
            "source_id": request.source_id,
            "due_only": request.due_only,
        },
    )
    if job is None:
        return
    if request.run_id is not None:
        checks.add(f"{label}.reserved_run_id", job["run_id"] == request.run_id)
    run = _index(snapshot.tables["runs"]).get(job["run_id"])
    checks.add(
        f"{label}.run_exists",
        None
        if run is None and job["status"] in {"queued", "running"} and request.run_exists
        else (run is not None) == request.run_exists,
    )
    sources = [s for s in snapshot.tables["sources"] if s["run_id"] == job["run_id"]]
    checks.add(
        f"{label}.explicit_source_expectations",
        set(request.sources) == {source.id for source in spec.sources},
    )
    source_configs = {source.id: source for source in spec.sources}
    checks.add(
        f"{label}.source_configurations",
        all(
            source["source_id"] in source_configs
            and source["config_hash"] == source_configs[source["source_id"]].fingerprint()
            for source in sources
        ),
    )
    if run:
        checks.add(
            f"{label}.original_configuration",
            run["config_hash"] == spec.fingerprint()
            and PipelineSpec.model_validate_json(run["config_json"]) == spec,
        )
        checks.add(
            f"{label}.run_status",
            None if run["status"] == "running" else run["status"] == request.status,
            {"expected": request.status, "observed": run["status"]},
        )
        if job["status"] not in {"queued", "running"}:
            result = json.loads(job["result_json"] or "null")
            reported = (result or {}).get("sources", [])
            actual_sources = {source["source_id"]: source for source in sources}
            checks.add(
                f"{label}.reported_result",
                isinstance(result, dict)
                and result.get("run_id") == run["id"]
                and result.get("status") == run["status"]
                and len(reported) == len(actual_sources)
                and {source["source_id"] for source in reported} == set(actual_sources)
                and all(
                    source.get("source_run_id", source.get("id"))
                    == actual_sources[source["source_id"]]["id"]
                    and source["status"] == actual_sources[source["source_id"]]["status"]
                    and source.get("records", source.get("record_count"))
                    == actual_sources[source["source_id"]]["record_count"]
                    for source in reported
                ),
            )
    for source_id, expected in request.sources.items():
        actual = [source for source in sources if source["source_id"] == source_id]
        observed_status = actual[0]["status"] if actual else "not_started"
        observations = [
            o
            for o in snapshot.tables["observations"]
            if o["source_run_id"] in {source["id"] for source in actual}
        ]
        checks.add(
            f"{label}.{source_id}.status",
            None if observed_status == "running" else observed_status == expected.status,
            {"expected": expected.status, "observed": observed_status},
        )
        checks.add(
            f"{label}.{source_id}.publication",
            len(actual) <= 1
            and all(source["record_count"] == len(observations) for source in actual)
            and (observed_status == "succeeded" or not observations)
            and (expected.observations is None or len(observations) == expected.observations),
            {"expected_count": expected.observations, "observed_count": len(observations)},
        )


def _selected(records, spec, request):
    eligible = {}
    cutoff = parse_timestamp(request.as_of)
    for record in records:
        if (
            record["source_id"] not in {s.id for s in spec.sources}
            or (request.kind is not None and record["kind"] != request.kind)
            or parse_timestamp(record["observed_at"]) > cutoff
            or parse_timestamp(record["available_at"]) > cutoff
        ):
            continue
        key = (record["source_id"], record["kind"], record["key"])
        eligible.setdefault(key, []).append(record)
    selected = []
    for key in sorted(eligible):
        candidates = eligible[key]
        latest = max(
            candidates, key=lambda r: (parse_timestamp(r["observed_at"]), r["observation_id"])
        )
        selected.append(
            {
                **{key: value for key, value in latest.items() if key != "observation_id"},
                "first_observed_at": min(
                    candidates, key=lambda r: parse_timestamp(r["observed_at"])
                )["observed_at"],
            }
        )
    return selected


def _source_health(snapshot, spec, cutoff):
    """Reconstruct manifest source metadata using the frozen rows, not Store.health."""
    at = parse_timestamp(cutoff)
    result = []
    for config in spec.sources:
        finished = [
            source
            for source in snapshot.tables["sources"]
            if source["source_id"] == config.id
            and source["finished_at"] is not None
            and parse_timestamp(source["finished_at"]) <= at
        ]
        latest = max(
            finished,
            key=lambda s: (parse_timestamp(s["finished_at"]), s["evaluation_sequence"]),
            default=None,
        )
        successful = [source for source in finished if source["status"] == "succeeded"]
        last_success = (
            max(successful, key=lambda s: parse_timestamp(s["finished_at"]))["finished_at"]
            if successful
            else None
        )
        result.append(
            {
                "source_id": config.id,
                "enabled": config.enabled,
                "latest_run_status": latest["status"] if latest else "never_run",
                "configuration_matches_latest_run": latest["config_hash"] == config.fingerprint()
                if latest
                else False,
                "last_success_at": last_success,
                "poll_interval_seconds": config.poll_interval_seconds,
                "error": latest["error"] if latest else None,
                "stale": last_success is None
                or (at - parse_timestamp(last_success)).total_seconds()
                > config.poll_interval_seconds,
            }
        )
    return result


def _export(snapshot, spec, records, request, checks):
    label = f"export.{request.job_id}"
    cutoff = timestamp(parse_timestamp(request.as_of))
    job = _job(
        snapshot,
        request,
        "export",
        checks,
        {
            "pipeline_version_id": snapshot.pipeline_version_id,
            "as_of": cutoff,
            "kind": request.kind,
        },
    )
    if job is None or job["status"] != "succeeded":
        if request.status == "succeeded":
            checks.add(f"{label}.archived_export", None, "No successful archived export to inspect")
        return
    try:
        result = json.loads(job["result_json"])
        refs = result["artifact_refs"]
        if len(refs) != 2 or {ref["kind"] for ref in refs} != {"manifest", "observations"}:
            raise ValueError("Export must identify exactly one manifest and observation artifact")
        content = {ref["kind"]: snapshot.blobs[f"registry/{ref['sha256']}"] for ref in refs}
        manifest = json.loads(content["manifest"])
        actual = [json.loads(line) for line in content["observations"].splitlines()]
        expected = _selected(records, spec, request)
        checks.add(f"{label}.fixed_cutoff_records", actual == expected)
        if request.record_count is not None:
            checks.add(f"{label}.expected_record_count", len(actual) == request.record_count)
        expected_manifest = {
            "pipeline": spec.name,
            "dataset_scope": snapshot.question_id,
            "config_fingerprint": spec.fingerprint(),
            "as_of": cutoff,
            "kind_filter": request.kind,
            "record_count": len(expected),
            "data_sha256": digest(content["observations"]),
            "normalizer_version": "1",
            "sources": _source_health(snapshot, spec, cutoff),
        }
        checks.add(
            f"{label}.manifest",
            all(manifest.get(key) == value for key, value in expected_manifest.items())
            and all(result.get(key) == value for key, value in manifest.items())
            and result.get("pipeline_version_id") == snapshot.pipeline_version_id,
        )
        checks.add(
            f"{label}.returned_manifest",
            None if request.returned_manifest is None else request.returned_manifest == manifest,
            "Provide the controller-observed returned manifest to measure delivery"
            if request.returned_manifest is None
            else None,
        )
    except (KeyError, ValueError, TypeError):
        checks.add(f"{label}.archived_export", False, "Missing, corrupt, or malformed archive")


def _replay(snapshot, before, expectation, checks):
    if not expectation.replay_job_ids:
        checks.add("replay.identity_and_publication", None, "No replay/reopen comparison requested")
        return
    if before is None:
        checks.add("replay.identity_and_publication", None, "A before snapshot is required")
        return
    same_scope = (
        before.question_id == snapshot.question_id
        and before.pipeline_version_id == snapshot.pipeline_version_id
        and before.pipeline == snapshot.pipeline
        and before.registry_stable
    )
    checks.add("replay.same_scope", same_scope)
    checks.add(
        "replay.scoped_publication_unchanged",
        before.tables == snapshot.tables,
        "Measure replay in an isolated interval; concurrent collections cannot establish this check",
    )
    before_jobs, after_jobs = _index(before.jobs), _index(snapshot.jobs)
    for job_id in expectation.replay_job_ids:
        original, current = before_jobs.get(job_id), after_jobs.get(job_id)
        if original is None or current is None:
            checks.add(f"replay.{job_id}.identity", False)
            continue
        keys = (
            "id",
            "run_id",
            "operation_id",
            "discovery_id",
            "request_hash",
            "pipeline_version_id",
        )
        checks.add(f"replay.{job_id}.identity", all(original[key] == current[key] for key in keys))
        if original["status"] in {"queued", "running"}:
            checks.add(f"replay.{job_id}.publication", None, "Before snapshot was not terminal")
            continue

        def publication(value, run_id=original["run_id"]):
            sources = [s for s in value.tables["sources"] if s["run_id"] == run_id]
            ids = {s["id"] for s in sources}
            observations = [o for o in value.tables["observations"] if o["source_run_id"] in ids]
            return {
                "runs": [r for r in value.tables["runs"] if r["id"] == run_id],
                "sources": sources,
                "observations": observations,
                "captures": [c for c in value.tables["captures"] if c["source_run_id"] in ids],
            }

        duplicates = [
            j
            for j in snapshot.jobs
            if j["operation_id"] == original["operation_id"]
            and j["discovery_id"] == original["discovery_id"]
        ]
        checks.add(
            f"replay.{job_id}.publication",
            publication(before) == publication(snapshot)
            and len(duplicates) == 1
            and original["result_json"] == current["result_json"]
            and original["status"] == current["status"],
        )


def evaluate_workflow(
    snapshot: WorkflowSnapshot,
    expectation: WorkflowExpectation | dict[str, Any],
    *,
    before: WorkflowSnapshot | None = None,
) -> dict[str, Any]:
    """Evaluate requested outcomes; never infer scientific usefulness or chat success.

    Missing delivery or replay observations remain unknown, even when database
    integrity passes. The expectation belongs to the trusted evaluation controller
    and must be authored independently of a candidate's success assertions.
    """
    expected = WorkflowExpectation.model_validate(expectation)
    checks = _Checks()
    checks.add("snapshot.stable_registry", True if snapshot.registry_stable else None)
    spec = PipelineSpec.model_validate_json(snapshot.pipeline["spec_json"])
    checks.add(
        "pipeline.original_configuration",
        snapshot.pipeline_version_id == expected.pipeline_version_id
        and snapshot.pipeline["fingerprint"] == expected.pipeline_fingerprint == spec.fingerprint(),
    )
    records = _observations(snapshot, checks)
    try:
        for request in expected.collections:
            _collection(snapshot, spec, request, checks)
        for request in expected.exports:
            _export(snapshot, spec, records, request, checks)
        _replay(snapshot, before, expected, checks)
    except (KeyError, ValueError, TypeError):
        checks.add("authoritative_record_schema", False, "Malformed authoritative record")
    counts = {
        status: sum(check["status"] == status for check in checks.items)
        for status in ("passed", "failed", "unknown")
    }
    return {
        "schema_version": 1,
        "measurement": "workflow_correctness",
        "status": "failed" if counts["failed"] else "incomplete" if counts["unknown"] else "passed",
        "question_id": snapshot.question_id,
        "pipeline_version_id": snapshot.pipeline_version_id,
        "snapshot_sha256": snapshot.fingerprint(),
        "before_snapshot_sha256": before.fingerprint() if before else None,
        "expectation_sha256": digest(canonical_json(expected.model_dump(mode="json"))),
        "counts": counts,
        "checks": checks.items,
        "unmeasured": ["source_content_entailment", "scientific_usefulness", "model_cost"],
    }
