"""Durable collection/export jobs with detached workers and publication-aware recovery."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from research_harness import registry
from research_harness.backend import Backend
from research_harness.config import PipelineSpec
from research_harness.engine import export_dataset, run_pipeline
from research_harness.http import OperationCancelled
from research_harness.services.errors import ResearchError
from research_harness.services.research import ResearchService
from research_harness.store import Store, WriterBusy
from research_harness.util import (
    canonical_json,
    digest,
    error_message,
    parse_timestamp,
    timestamp,
    write_json,
)

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def _public(row: dict[str, Any]) -> dict[str, Any]:
    value = {key: item for key, item in row.items() if key not in {"request_json", "result_json"}}
    value["request"] = json.loads(row["request_json"])
    value["result"] = json.loads(row["result_json"]) if row["result_json"] else None
    return value


def worker_environment(backend: Backend) -> dict[str, str]:
    """Pass backend credentials through the subprocess environment, never command arguments."""
    settings = backend.settings
    env = dict(os.environ)
    values = {
        "RH_LOCAL_ROOT": str(settings.local_root.resolve()),
        "RH_DATABASE_URL": settings.database_url,
        "RH_DATABASE_SCHEMA": settings.schema,
        "RH_BLOB_BUCKET": settings.blob_bucket,
        "RH_BLOB_ENDPOINT": settings.blob_endpoint,
        "RH_BLOB_ACCESS_KEY": settings.blob_access_key,
        "RH_BLOB_SECRET_KEY": settings.blob_secret_key,
        "RH_BLOB_REGION": settings.blob_region,
        "RH_BLOB_PREFIX": settings.blob_prefix,
    }
    for key, value in values.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


class JobService:
    def __init__(
        self, research: ResearchService, *, auto_launch: bool = True, deadline_seconds: int = 600
    ):
        if not 1 <= deadline_seconds <= 86_400:
            raise ValueError("Job deadline must be between 1 and 86400 seconds")
        self.research = research
        self.backend = research.backend
        self.auto_launch = auto_launch
        self.deadline_seconds = deadline_seconds

    def _context(self, store: Store) -> dict[str, Any]:
        return self.research._load(store)

    def _job(self, store: Store, job_id: str) -> dict[str, Any]:
        context = self._context(store)
        row = store.query_one(
            "SELECT * FROM research_jobs WHERE id=? AND question_id=?",
            (job_id, context["question_id"]),
        )
        if not row:
            raise ResearchError(
                "evidence_not_found", "Job does not belong to this research question"
            )
        return row

    def _pipeline(self, store: Store, pipeline_id: str) -> tuple[dict[str, Any], PipelineSpec]:
        context = self._context(store)
        row, spec = registry.pipeline_spec(store, pipeline_id)
        if row["question_id"] != context["question_id"]:
            raise ResearchError(
                "evidence_not_found", "Pipeline does not belong to this research question"
            )
        return row, spec

    def _directory(self, job_id: str) -> Path:
        with self.backend.open_registry() as store:
            job = self._job(store, job_id)
            discovery = store.query_one(
                "SELECT output_ref FROM discoveries WHERE id=?", (job["discovery_id"],)
            )
        return Path(discovery["output_ref"]) / "jobs" / job_id

    def _finish(
        self,
        store: Store,
        row: dict[str, Any],
        status: str,
        result: dict[str, Any] | None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if status not in TERMINAL:
            raise ValueError("A finished job needs a terminal status")
        with store.transaction():
            store.execute(
                "UPDATE research_jobs SET status=?, result_json=?, error=?, finished_at=? WHERE id=?",
                (
                    status,
                    canonical_json(result) if result is not None else None,
                    error,
                    timestamp(store.clock()),
                    row["id"],
                ),
            )
        updated = self._job(store, row["id"])
        try:
            write_json(self._directory(row["id"]) / "job.json", _public(updated))
            self.research.event(
                {
                    "event": "job_complete",
                    "job_id": row["id"],
                    "kind": row["kind"],
                    "status": status,
                    "run_id": row["run_id"],
                }
            )
        except OSError:
            pass  # These files are exports of the committed job result, not its authority.
        return updated

    def _enqueue(
        self, kind: str, pipeline_id: str, operation_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:
            raise ValueError("Operation id must contain between 1 and 200 characters")
        with self.backend.open_registry() as store:
            context = self._context(store)
            version, _ = self._pipeline(store, pipeline_id)
            request = {"pipeline_version_id": version["id"], **arguments}
            hashed = digest(canonical_json({"kind": kind, "request": request}))
            with (
                store.operation_lock(f"discovery:{context['id']}"),
                store.operation_lock(f"jobs:{context['question_id']}"),
            ):
                operation = store.query_one(
                    "SELECT request_hash FROM discovery_operations WHERE discovery_id=? AND operation_id=?",
                    (context["id"], operation_id),
                )
                existing = store.query_one(
                    "SELECT * FROM research_jobs WHERE discovery_id=? AND operation_id=?",
                    (context["id"], operation_id),
                )
                if operation and (not existing or operation["request_hash"] != hashed):
                    raise ResearchError(
                        "operation_conflict",
                        "Operation id was already used with different arguments",
                    )
                if existing:
                    if existing["request_hash"] != hashed:
                        raise ResearchError(
                            "operation_conflict",
                            "Operation id was already used with different arguments",
                        )
                    job_id = existing["id"]
                else:
                    pending = store.scalar(
                        "SELECT COUNT(*) FROM research_jobs WHERE question_id=? AND status IN ('queued', 'running')",
                        (context["question_id"],),
                    )
                    if pending >= 4:
                        raise ValueError(
                            "Pending job limit reached; inspect or cancel existing jobs"
                        )
                    job_id = uuid4().hex
                    with store.transaction():
                        store.execute(
                            """INSERT INTO discovery_operations
                               (discovery_id, operation_id, kind, request_hash, request_json,
                                status, result_json, created_at, finished_at)
                               VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?)""",
                            (
                                context["id"],
                                operation_id,
                                f"job:{kind}",
                                hashed,
                                canonical_json(request),
                                canonical_json({"job_id": job_id, "accepted": True, "kind": kind}),
                                timestamp(store.clock()),
                                timestamp(store.clock()),
                            ),
                        )
                        store.execute(
                            """INSERT INTO research_jobs
                               (id, discovery_id, question_id, pipeline_version_id, operation_id, kind,
                                request_hash, request_json, status, run_id, created_at, deadline_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)""",
                            (
                                job_id,
                                context["id"],
                                context["question_id"],
                                version["id"],
                                operation_id,
                                kind,
                                hashed,
                                canonical_json(request),
                                uuid4().hex if kind == "collection" else None,
                                timestamp(store.clock()),
                                timestamp(store.clock() + timedelta(seconds=self.deadline_seconds)),
                            ),
                        )
                    self.research.event(
                        {
                            "event": "job_queued",
                            "job_id": job_id,
                            "kind": kind,
                            "operation_id": operation_id,
                        }
                    )
        current = self.get_job(job_id)
        if self.auto_launch and current["status"] == "queued" and not current["worker_active"]:
            self._launch(job_id)
            current = self.get_job(job_id)
        return current

    def start_collection(
        self,
        pipeline_version_id: str,
        *,
        operation_id: str,
        source_id: str | None = None,
        due_only: bool = False,
    ) -> dict[str, Any]:
        with self.backend.open_registry() as store:
            _, spec = self._pipeline(store, pipeline_version_id)
        if source_id and source_id not in {source.id for source in spec.sources}:
            raise ValueError(f"Unknown source: {source_id}")
        return self._enqueue(
            "collection",
            pipeline_version_id,
            operation_id,
            {"source_id": source_id, "due_only": due_only},
        )

    def export_observations(
        self,
        pipeline_version_id: str,
        *,
        as_of: str,
        operation_id: str,
        kind: str | None = None,
    ) -> dict[str, Any]:
        cutoff = parse_timestamp(as_of)
        if cutoff > self.backend.clock():
            raise ValueError("Export cutoff cannot be in the future")
        if kind not in {None, "market", "orderbook", "document", "observation"}:
            raise ValueError("Unknown observation kind")
        return self._enqueue(
            "export", pipeline_version_id, operation_id, {"as_of": timestamp(cutoff), "kind": kind}
        )

    def _launch(self, job_id: str) -> None:
        directory = self._directory(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "research_harness.services.jobs", "--job-id", job_id]
        if not self.research.public_only:
            command.append("--allow-local-sources")
        try:
            with (directory / "worker.log").open("ab") as log:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=log,
                    env=worker_environment(self.backend),
                    start_new_session=True,
                    close_fds=True,
                )
            threading.Thread(target=process.wait, daemon=True).start()
            with self.backend.open_registry() as store, store.transaction():
                store.execute(
                    "UPDATE research_jobs SET worker_pid=? WHERE id=?", (process.pid, job_id)
                )
        except OSError as exc:
            with self.backend.open_registry() as store, store.transaction():
                store.execute(
                    "UPDATE research_jobs SET error=? WHERE id=? AND status='queued'",
                    (f"Worker launch failed: {type(exc).__name__}", job_id),
                )
            raise RuntimeError("Could not launch worker; retry the same operation id") from exc

    def _completed_export(
        self, store: Store, row: dict[str, Any], spec: PipelineSpec
    ) -> dict[str, Any] | None:
        output = self._directory(row["id"]) / "observations.jsonl"
        manifest_path = output.with_suffix(".jsonl.manifest.json")
        if not output.is_file() or not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text())
            payload = output.read_bytes()
            request = json.loads(row["request_json"])
            if (
                manifest["data_sha256"] != digest(payload)
                or manifest["config_fingerprint"] != spec.fingerprint()
                or manifest["as_of"] != request["as_of"]
                or manifest["kind_filter"] != request["kind"]
            ):
                return None
        except (KeyError, ValueError, OSError):
            return None
        data_hash = store.put_blob(payload)
        manifest_hash = store.put_blob(manifest_path.read_bytes())
        return {
            **manifest,
            "output": str(output),
            "manifest": str(manifest_path),
            "pipeline_version_id": row["pipeline_version_id"],
            "artifact_refs": [
                {"kind": "observations", "sha256": data_hash, "bytes": len(payload)},
                {"kind": "manifest", "sha256": manifest_hash},
            ],
        }

    def _reconcile_locked(self, store: Store, row: dict[str, Any]) -> dict[str, Any]:
        if row["status"] in TERMINAL:
            return row
        _, spec = self._pipeline(store, row["pipeline_version_id"])
        if row["kind"] == "collection":
            with self.backend.pipeline_store(spec, None, question_id=row["question_id"]) as data:
                run = data.query_one("SELECT id, status FROM runs WHERE id=?", (row["run_id"],))
                if run:
                    if run["status"] == "running":
                        # A job lock without an owner is insufficient: obtain the real writer lock too.
                        with data.writer(spec.name):
                            pass
                    details = data.run_details(row["run_id"])
                    status = details["status"]
                    if status not in TERMINAL:
                        raise ValueError(f"Unexpected collection run status: {status}")
                    return self._finish(
                        store,
                        row,
                        status,
                        {**details, "run_id": row["run_id"], "recovered": True},
                        details.get("error"),
                    )
        else:
            result = self._completed_export(store, row, spec)
            if result:
                return self._finish(store, row, "succeeded", {**result, "recovered": True})
        if row["cancel_requested_at"]:
            return self._finish(store, row, "cancelled", None, "Cancelled before work completed")
        if store.clock() >= parse_timestamp(row["deadline_at"]):
            return self._finish(store, row, "interrupted", None, "Job deadline exhausted")
        if row["status"] == "running":
            with store.transaction():
                store.execute(
                    "UPDATE research_jobs SET status='queued', error=? WHERE id=?",
                    (
                        "Previous worker stopped before a collection run or complete export was recorded; retry the same operation id",
                        row["id"],
                    ),
                )
            return self._job(store, row["id"])
        return row

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.backend.open_registry() as store:
            row = self._job(store, job_id)
            active = False
            recovery_pending = False
            if row["status"] not in TERMINAL:
                try:
                    with store.operation_lock(f"job:{job_id}"):
                        try:
                            row = self._reconcile_locked(store, self._job(store, job_id))
                        except WriterBusy:
                            recovery_pending = True
                except WriterBusy:
                    active = True
                    row = self._job(store, job_id)
            result = _public(row)
            result.update(worker_active=active, recovery_pending=recovery_pending)
            if row["run_id"]:
                _, spec = self._pipeline(store, row["pipeline_version_id"])
                with self.backend.pipeline_store(
                    spec, None, question_id=row["question_id"]
                ) as data:
                    if data.query_one("SELECT id FROM runs WHERE id=?", (row["run_id"],)):
                        result["collection"] = data.run_details(row["run_id"])
            return result

    def list_jobs(self) -> dict[str, Any]:
        with self.backend.open_registry() as store:
            context = self._context(store)
            ids = store.query(
                "SELECT id FROM research_jobs WHERE question_id=? ORDER BY created_at, id",
                (context["question_id"],),
            )
        return {
            "question_id": context["question_id"],
            "jobs": [self.get_job(row["id"]) for row in ids],
        }

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self.backend.open_registry() as store:
            self._job(store, job_id)
            with store.transaction():
                store.execute(
                    """UPDATE research_jobs SET cancel_requested_at=COALESCE(cancel_requested_at, ?)
                       WHERE id=? AND status IN ('queued', 'running')""",
                    (timestamp(store.clock()), job_id),
                )
        self.research.event({"event": "job_cancel_requested", "job_id": job_id})
        return self.get_job(job_id)

    def read_export(
        self, job_id: str, *, artifact: str = "observations", offset: int = 0, limit: int = 8000
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 50_000:
            raise ValueError("Export range must have offset >= 0 and limit between 1 and 50000")
        job = self.get_job(job_id)
        if job["kind"] != "export" or job["status"] != "succeeded":
            raise ValueError("Export is not ready")
        reference = next(
            (ref for ref in job["result"]["artifact_refs"] if ref["kind"] == artifact), None
        )
        if not reference:
            raise ValueError("Unknown export artifact")
        with self.backend.open_registry() as store:
            body = store.read_blob(reference["sha256"]).decode("utf-8")
        return {
            "job_id": job_id,
            "artifact": artifact,
            "sha256": reference["sha256"],
            "content": body[offset : offset + limit],
            "offset": offset,
            "next_offset": offset + limit if offset + limit < len(body) else None,
            "total_characters": len(body),
        }

    def run_job(
        self,
        job_id: str,
        *,
        client: httpx.Client | None = None,
        on_progress: Any = None,
    ) -> dict[str, Any]:
        with self.backend.open_registry() as store:
            row = self._job(store, job_id)
            with store.operation_lock(f"job:{job_id}"):
                row = self._reconcile_locked(store, self._job(store, job_id))
                if row["status"] in TERMINAL:
                    return _public(row)
                _, spec = self._pipeline(store, row["pipeline_version_id"])
                request = json.loads(row["request_json"])
                with store.transaction():
                    store.execute(
                        "UPDATE research_jobs SET status='running', started_at=COALESCE(started_at, ?), error=NULL WHERE id=?",
                        (timestamp(store.clock()), job_id),
                    )

                def check_cancelled():
                    current = self._job(store, job_id)
                    if current["cancel_requested_at"]:
                        raise OperationCancelled("Collection cancelled by request")
                    if store.clock() >= parse_timestamp(current["deadline_at"]):
                        raise OperationCancelled("Job deadline exhausted")

                def progress(summary):
                    self.research.event({"event": "job_progress", "job_id": job_id, **summary})
                    if on_progress:
                        on_progress(summary)

                try:
                    if row["kind"] == "collection":
                        while True:
                            check_cancelled()
                            try:
                                with self.backend.pipeline_store(
                                    spec, None, question_id=row["question_id"]
                                ) as data:
                                    result = run_pipeline(
                                        spec,
                                        data,
                                        client=client,
                                        only_source=request["source_id"],
                                        due_only=request["due_only"],
                                        public_only=self.research.public_only,
                                        clock=self.backend.clock,
                                        run_id=row["run_id"],
                                        check_cancelled=check_cancelled,
                                        on_progress=progress,
                                    )
                                break
                            except WriterBusy:
                                time.sleep(0.25)
                        row = self._finish(
                            store, row, result["status"], result, result.get("error")
                        )
                    else:
                        check_cancelled()
                        output = self._directory(job_id) / "observations.jsonl"
                        with self.backend.pipeline_store(
                            spec, None, question_id=row["question_id"]
                        ) as data:
                            export_dataset(
                                spec,
                                data,
                                parse_timestamp(request["as_of"]),
                                output,
                                request["kind"],
                            )
                        result = self._completed_export(store, row, spec)
                        if result is None:
                            raise RuntimeError("Export checksum or manifest validation failed")
                        row = self._finish(store, row, "succeeded", result)
                except OperationCancelled as exc:
                    row = self._finish(store, row, "cancelled", None, str(exc))
                except Exception as exc:
                    # A source/run may already be published. Recover that truth before classifying
                    # a worker/reporting failure, and never rerun a recorded collection here.
                    try:
                        row = self._reconcile_locked(store, self._job(store, job_id))
                    except WriterBusy:
                        return _public(self._job(store, job_id))
                    if row["status"] not in TERMINAL:
                        row = self._finish(
                            store,
                            row,
                            "failed",
                            None,
                            f"{type(exc).__name__}: {error_message(exc)}",
                        )
                # BaseException/process death deliberately leaves a reconcilable record.
                return _public(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one durable research job")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--allow-local-sources", action="store_true")
    args = parser.parse_args()
    backend = Backend.from_env()
    with backend.open_registry() as store:
        row = store.query_one("SELECT discovery_id FROM research_jobs WHERE id=?", (args.job_id,))
    if not row:
        raise ValueError("Unknown research job")
    research = ResearchService(
        backend.settings.local_root, backend=backend, public_only=not args.allow_local_sources
    )
    research.resume(row["discovery_id"])
    try:
        result = JobService(research, auto_launch=False).run_job(args.job_id)
    except WriterBusy:
        return 0  # The current owner will finish; this delivery has no work to repeat.
    return 0 if result["status"] in {"succeeded", "cancelled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
