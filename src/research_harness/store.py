from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock

from research_harness.config import PipelineSpec, SourceSpec
from research_harness.records import NORMALIZER_VERSION, Capture, Issue, Record
from research_harness.util import atomic_write, canonical_json, digest, timestamp, utcnow

DDL = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS source_runs (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), source_id TEXT NOT NULL,
    config_hash TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, record_count INTEGER NOT NULL DEFAULT 0,
    new_versions INTEGER NOT NULL DEFAULT 0, quarantined INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS captures (
    id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL REFERENCES source_runs(id),
    source_id TEXT NOT NULL, url TEXT NOT NULL, observed_at TEXT NOT NULL,
    status_code INTEGER, body_hash TEXT, headers_json TEXT NOT NULL,
    context_json TEXT NOT NULL, error TEXT, reused_capture_id TEXT REFERENCES captures(id)
);
CREATE INDEX IF NOT EXISTS captures_url ON captures(source_id, url, observed_at);
CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, source_id TEXT NOT NULL,
    kind TEXT NOT NULL, record_key TEXT NOT NULL, content_hash TEXT NOT NULL,
    normalizer_version TEXT NOT NULL, data_json TEXT NOT NULL, published_at TEXT,
    UNIQUE(pipeline, source_id, kind, record_key, content_hash, normalizer_version)
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT NOT NULL REFERENCES versions(id),
    source_run_id TEXT NOT NULL REFERENCES source_runs(id), capture_id TEXT NOT NULL REFERENCES captures(id),
    observed_at TEXT NOT NULL, available_at TEXT NOT NULL,
    UNIQUE(version_id, capture_id)
);
CREATE INDEX IF NOT EXISTS observations_asof ON observations(observed_at, available_at);
CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_run_id TEXT NOT NULL REFERENCES source_runs(id),
    capture_id TEXT REFERENCES captures(id), location TEXT NOT NULL,
    error TEXT NOT NULL, candidate_text TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_state (
    pipeline TEXT NOT NULL, source_id TEXT NOT NULL, config_hash TEXT NOT NULL,
    last_success_at TEXT NOT NULL, checkpoint_json TEXT NOT NULL,
    PRIMARY KEY(pipeline, source_id)
);
PRAGMA user_version = 1;
"""


class Store:
    def __init__(self, root: Path, *, clock: Callable[[], datetime] = utcnow):
        self.root = root
        self.clock = clock
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "harness.sqlite3", timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, 1}:
            self.db.close()
            raise RuntimeError(f"Unsupported database schema {version}; refusing to modify it")
        self.db.executescript(DDL)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @contextmanager
    def writer(self) -> Iterator[None]:
        with FileLock(self.root / "writer.lock", timeout=0):
            # The OS lock is authoritative. An abandoned 'running' row is historical evidence.
            now = timestamp(self.clock())
            with self.db:
                self.db.execute(
                    "UPDATE source_runs SET status='interrupted', finished_at=?, error='Previous writer exited before completion' WHERE status='running'",
                    (now,),
                )
                self.db.execute(
                    "UPDATE runs SET status='interrupted', finished_at=?, error='Previous writer exited before completion' WHERE status='running'",
                    (now,),
                )
            yield

    def put_blob(self, body: bytes) -> str:
        hashed = digest(body)
        path = self.root / "raw" / hashed[:2] / hashed
        if path.exists():
            if digest(path.read_bytes()) != hashed:
                raise RuntimeError(f"Stored response failed its integrity check: {hashed}")
        else:
            atomic_write(path, body)
        return hashed

    def read_blob(self, hashed: str) -> bytes:
        if len(hashed) != 64 or any(c not in "0123456789abcdef" for c in hashed):
            raise ValueError("Invalid response content hash")
        body = (self.root / "raw" / hashed[:2] / hashed).read_bytes()
        if digest(body) != hashed:
            raise RuntimeError(f"Stored response failed its integrity check: {hashed}")
        return body

    def start_run(self, spec: PipelineSpec) -> str:
        run_id = uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, NULL, 'running', NULL)",
                (
                    run_id,
                    spec.name,
                    spec.fingerprint(),
                    canonical_json(spec.model_dump(mode="json")),
                    timestamp(self.clock()),
                ),
            )
        return run_id

    def start_source(self, run_id: str, source: SourceSpec) -> str:
        source_run_id = uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO source_runs (id, run_id, source_id, config_hash, started_at, status) VALUES (?, ?, ?, ?, ?, 'running')",
                (source_run_id, run_id, source.id, source.fingerprint(), timestamp(self.clock())),
            )
        return source_run_id

    def save_capture(self, source_run_id: str, capture: Capture) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO captures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    capture.id,
                    source_run_id,
                    capture.source_id,
                    capture.url,
                    capture.observed_at,
                    capture.status_code,
                    capture.body_hash,
                    canonical_json(capture.headers),
                    canonical_json(capture.context),
                    capture.error,
                    capture.reused_capture_id,
                ),
            )

    def cached_capture(self, pipeline: str, source_id: str, url: str) -> Capture | None:
        row = self.db.execute(
            """SELECT c.* FROM captures c JOIN source_runs s ON s.id=c.source_run_id
               JOIN runs r ON r.id=s.run_id WHERE r.pipeline=? AND c.source_id=? AND c.url=?
               AND c.status_code=200 AND c.error IS NULL AND c.body_hash IS NOT NULL
               ORDER BY c.observed_at DESC, c.rowid DESC LIMIT 1""",
            (pipeline, source_id, url),
        ).fetchone()
        return self._capture(row) if row else None

    @staticmethod
    def _capture(row: sqlite3.Row) -> Capture:
        return Capture(
            id=row["id"],
            source_id=row["source_id"],
            url=row["url"],
            observed_at=row["observed_at"],
            status_code=row["status_code"],
            body_hash=row["body_hash"],
            headers=json.loads(row["headers_json"]),
            context=json.loads(row["context_json"]),
            error=row["error"],
            reused_capture_id=row["reused_capture_id"],
        )

    def finish_source(
        self,
        pipeline: str,
        source_run_id: str,
        source: SourceSpec,
        records: list[tuple[Record, Capture]],
        issues: list[tuple[str | None, Issue]],
        *,
        error: str | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = "failed" if error else "degraded" if issues else "succeeded"
        now = timestamp(self.clock())
        new_versions = 0
        accepted = 0
        with self.db:
            for capture_id, issue in issues:
                self.db.execute(
                    "INSERT INTO quarantine (source_run_id, capture_id, location, error, candidate_text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        source_run_id,
                        capture_id,
                        issue.location,
                        issue.error[:8000],
                        repr(issue.candidate)[:8000],
                        now,
                    ),
                )
            # Publish the complete source snapshot, or publish none of it. Raw evidence survives both.
            if status == "succeeded":
                for record, capture in records:
                    version_id = digest(
                        canonical_json(
                            [
                                pipeline,
                                source.id,
                                record.kind,
                                record.key,
                                record.content_hash,
                                NORMALIZER_VERSION,
                            ]
                        )
                    )
                    inserted = self.db.execute(
                        "INSERT OR IGNORE INTO versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            version_id,
                            pipeline,
                            source.id,
                            record.kind,
                            record.key,
                            record.content_hash,
                            NORMALIZER_VERSION,
                            canonical_json(record.data),
                            record.published_at,
                        ),
                    ).rowcount
                    new_versions += inserted
                    accepted += self.db.execute(
                        "INSERT OR IGNORE INTO observations (version_id, source_run_id, capture_id, observed_at, available_at) VALUES (?, ?, ?, ?, ?)",
                        (version_id, source_run_id, capture.id, capture.observed_at, now),
                    ).rowcount
                self.db.execute(
                    "INSERT INTO source_state VALUES (?, ?, ?, ?, ?) ON CONFLICT(pipeline, source_id) DO UPDATE SET config_hash=excluded.config_hash, last_success_at=excluded.last_success_at, checkpoint_json=excluded.checkpoint_json",
                    (
                        pipeline,
                        source.id,
                        source.fingerprint(),
                        now,
                        canonical_json(checkpoint or {}),
                    ),
                )
            self.db.execute(
                "UPDATE source_runs SET finished_at=?, status=?, record_count=?, new_versions=?, quarantined=?, error=? WHERE id=?",
                (now, status, accepted, new_versions, len(issues), error, source_run_id),
            )
        return {
            "source_id": source.id,
            "source_run_id": source_run_id,
            "status": status,
            "records": accepted,
            "new_versions": new_versions,
            "quarantined": len(issues),
            "error": error,
        }

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE runs SET status=?, finished_at=?, error=? WHERE id=?",
                (status, timestamp(self.clock()), error, run_id),
            )

    def state(self, pipeline: str, source_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM source_state WHERE pipeline=? AND source_id=?", (pipeline, source_id)
        ).fetchone()
        return dict(row) if row else None

    def run_details(self, run_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown run: {run_id}")
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        result["sources"] = [
            dict(s)
            for s in self.db.execute(
                "SELECT * FROM source_runs WHERE run_id=? ORDER BY started_at, id", (run_id,)
            )
        ]
        return result

    def captures_for_run(self, run_id: str) -> list[Capture]:
        return [
            self._capture(row)
            for row in self.db.execute(
                "SELECT c.* FROM captures c JOIN source_runs s ON s.id=c.source_run_id WHERE s.run_id=? ORDER BY c.observed_at, c.rowid",
                (run_id,),
            )
        ]

    def as_of(self, pipeline: str, cutoff: str, kind: str | None = None) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """WITH eligible AS (
                 SELECT v.*, o.capture_id, o.source_run_id, o.observed_at, o.available_at,
                   MIN(o.observed_at) OVER (PARTITION BY v.pipeline,v.source_id,v.kind,v.record_key) AS first_observed_at,
                   ROW_NUMBER() OVER (PARTITION BY v.pipeline,v.source_id,v.kind,v.record_key
                     ORDER BY o.observed_at DESC, o.id DESC) AS rank
                 FROM versions v JOIN observations o ON o.version_id=v.id
                 WHERE v.pipeline=? AND o.observed_at<=? AND o.available_at<=?
               ) SELECT e.*, c.url, c.body_hash, s.config_hash AS source_config_hash
                 FROM eligible e JOIN captures c ON c.id=e.capture_id
                 JOIN source_runs s ON s.id=e.source_run_id
                 WHERE e.rank=1 AND (? IS NULL OR e.kind=?) ORDER BY e.source_id,e.kind,e.record_key""",
            (pipeline, cutoff, cutoff, kind, kind),
        ).fetchall()
        return [
            {
                "source_id": row["source_id"],
                "kind": row["kind"],
                "key": row["record_key"],
                "version_id": row["id"],
                "content_hash": row["content_hash"],
                "normalizer_version": row["normalizer_version"],
                "first_observed_at": row["first_observed_at"],
                "observed_at": row["observed_at"],
                "available_at": row["available_at"],
                "published_at": row["published_at"],
                "data": json.loads(row["data_json"]),
                "lineage": {
                    "capture_id": row["capture_id"],
                    "source_run_id": row["source_run_id"],
                    "source_config_hash": row["source_config_hash"],
                    "source_url": row["url"],
                    "body_sha256": row["body_hash"],
                },
            }
            for row in rows
        ]

    def health(self, spec: PipelineSpec, cutoff: str) -> list[dict[str, Any]]:
        result = []
        for source in spec.sources:
            row = self.db.execute(
                """SELECT s.* FROM source_runs s JOIN runs r ON r.id=s.run_id
                   WHERE r.pipeline=? AND s.source_id=? AND s.finished_at<=?
                   ORDER BY s.finished_at DESC, s.rowid DESC LIMIT 1""",
                (spec.name, source.id, cutoff),
            ).fetchone()
            successful = self.db.execute(
                """SELECT MAX(s.finished_at) FROM source_runs s JOIN runs r ON r.id=s.run_id
                   WHERE r.pipeline=? AND s.source_id=? AND s.status='succeeded' AND s.finished_at<=?""",
                (spec.name, source.id, cutoff),
            ).fetchone()[0]
            result.append(
                {
                    "source_id": source.id,
                    "enabled": source.enabled,
                    "latest_run_status": row["status"] if row else "never_run",
                    "configuration_matches_latest_run": row["config_hash"] == source.fingerprint()
                    if row
                    else False,
                    "last_success_at": successful,
                    "poll_interval_seconds": source.poll_interval_seconds,
                    "error": row["error"] if row else None,
                }
            )
        return result

    def quarantine_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT q.* FROM quarantine q JOIN source_runs s ON s.id=q.source_run_id WHERE s.run_id=? ORDER BY q.id",
                (run_id,),
            )
        ]

    def capture_dict(self, capture: Capture) -> dict[str, Any]:
        return asdict(capture)
