from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from filelock import FileLock, Timeout

from research_harness.blobs import Blobs, LocalBlobs, validate_hash
from research_harness.config import PipelineSpec, SourceSpec
from research_harness.records import NORMALIZER_VERSION, Capture, Issue, Record
from research_harness.util import canonical_json, digest, timestamp, utcnow

SCHEMA_VERSION = 4
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

# One schema for both dialects. {auto_pk} is an integer identity primary key. {seq_col} adds an
# insertion-order column on Postgres; SQLite orders by its implicit rowid instead.
TABLES = [
    """CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS source_runs (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), source_id TEXT NOT NULL,
    config_hash TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, record_count INTEGER NOT NULL DEFAULT 0,
    new_versions INTEGER NOT NULL DEFAULT 0, quarantined INTEGER NOT NULL DEFAULT 0, error TEXT{seq_col})""",
    """CREATE TABLE IF NOT EXISTS captures (
    id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL REFERENCES source_runs(id),
    source_id TEXT NOT NULL, url TEXT NOT NULL, observed_at TEXT NOT NULL,
    status_code INTEGER, body_hash TEXT, headers_json TEXT NOT NULL,
    context_json TEXT NOT NULL, error TEXT, reused_capture_id TEXT REFERENCES captures(id){seq_col})""",
    """CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, source_id TEXT NOT NULL,
    kind TEXT NOT NULL, record_key TEXT NOT NULL, content_hash TEXT NOT NULL,
    normalizer_version TEXT NOT NULL, data_json TEXT NOT NULL, published_at TEXT,
    UNIQUE(pipeline, source_id, kind, record_key, content_hash, normalizer_version))""",
    """CREATE TABLE IF NOT EXISTS observations (
    id {auto_pk}, version_id TEXT NOT NULL REFERENCES versions(id),
    source_run_id TEXT NOT NULL REFERENCES source_runs(id), capture_id TEXT NOT NULL REFERENCES captures(id),
    observed_at TEXT NOT NULL, available_at TEXT NOT NULL,
    UNIQUE(version_id, capture_id))""",
    """CREATE TABLE IF NOT EXISTS quarantine (
    id {auto_pk}, source_run_id TEXT NOT NULL REFERENCES source_runs(id),
    capture_id TEXT REFERENCES captures(id), location TEXT NOT NULL,
    error TEXT NOT NULL, candidate_text TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS source_state (
    pipeline TEXT NOT NULL, source_id TEXT NOT NULL, config_hash TEXT NOT NULL,
    last_success_at TEXT NOT NULL, checkpoint_json TEXT NOT NULL,
    PRIMARY KEY(pipeline, source_id))""",
    # Registry: research questions, the discoveries run for them, the proposals those produced,
    # and every candidate pipeline definition with its adoption history.
    """CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY, brief TEXT NOT NULL, brief_hash TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS discoveries (
    id TEXT PRIMARY KEY, question_id TEXT NOT NULL REFERENCES questions(id),
    model TEXT NOT NULL, status TEXT NOT NULL, output_ref TEXT,
    started_at TEXT NOT NULL, finished_at TEXT, usage_json TEXT, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY, discovery_id TEXT NOT NULL REFERENCES discoveries(id),
    question_id TEXT NOT NULL REFERENCES questions(id), status TEXT NOT NULL,
    proposal_json TEXT NOT NULL, proposal_md TEXT NOT NULL, gaps_json TEXT NOT NULL,
    verified_source_count INTEGER NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS pipeline_versions (
    id TEXT PRIMARY KEY, question_id TEXT NOT NULL REFERENCES questions(id),
    proposal_id TEXT REFERENCES proposals(id), name TEXT NOT NULL, fingerprint TEXT NOT NULL,
    spec_json TEXT NOT NULL, status TEXT NOT NULL, origin TEXT NOT NULL, label TEXT, notes TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(question_id, fingerprint))""",
    """CREATE TABLE IF NOT EXISTS pipeline_events (
    id {auto_pk}, pipeline_version_id TEXT NOT NULL REFERENCES pipeline_versions(id),
    at TEXT NOT NULL, event TEXT NOT NULL, from_status TEXT, to_status TEXT, note TEXT)""",
]
INDEXES = [
    "CREATE INDEX IF NOT EXISTS captures_url ON captures(source_id, url, observed_at)",
    "CREATE INDEX IF NOT EXISTS observations_asof ON observations(observed_at, available_at)",
    "CREATE INDEX IF NOT EXISTS runs_pipeline ON runs(pipeline, started_at)",
    "CREATE INDEX IF NOT EXISTS pipeline_versions_question ON pipeline_versions(question_id, status)",
]
DISCOVERY_TABLES = [
    """CREATE TABLE IF NOT EXISTS discovery_contexts (
    discovery_id TEXT PRIMARY KEY REFERENCES discoveries(id), limits_json TEXT NOT NULL,
    runtime_json TEXT NOT NULL, deadline_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS discovery_operations (
    discovery_id TEXT NOT NULL REFERENCES discoveries(id), operation_id TEXT NOT NULL,
    kind TEXT NOT NULL, request_hash TEXT NOT NULL, request_json TEXT NOT NULL,
    status TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL, finished_at TEXT,
    PRIMARY KEY(discovery_id, operation_id))""",
    """CREATE TABLE IF NOT EXISTS discovery_receipts (
    id TEXT PRIMARY KEY, discovery_id TEXT NOT NULL REFERENCES discoveries(id),
    operation_id TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(discovery_id, operation_id) REFERENCES discovery_operations(discovery_id, operation_id),
    UNIQUE(discovery_id, operation_id))""",
]
DISCOVERY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS discovery_operations_budget ON discovery_operations(discovery_id, kind, status)",
    "CREATE INDEX IF NOT EXISTS discovery_receipts_context ON discovery_receipts(discovery_id, kind)",
]
JOB_TABLES = [
    """CREATE TABLE IF NOT EXISTS research_jobs (
    id TEXT PRIMARY KEY, discovery_id TEXT NOT NULL REFERENCES discoveries(id),
    question_id TEXT NOT NULL REFERENCES questions(id),
    pipeline_version_id TEXT NOT NULL REFERENCES pipeline_versions(id),
    operation_id TEXT NOT NULL, kind TEXT NOT NULL, request_hash TEXT NOT NULL,
    request_json TEXT NOT NULL, status TEXT NOT NULL, run_id TEXT,
    created_at TEXT NOT NULL, deadline_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    cancel_requested_at TEXT, worker_pid INTEGER, result_json TEXT, error TEXT,
    UNIQUE(discovery_id, operation_id))""",
]
JOB_INDEXES = [
    "CREATE INDEX IF NOT EXISTS research_jobs_question ON research_jobs(question_id, created_at)",
    "CREATE INDEX IF NOT EXISTS research_jobs_pending ON research_jobs(status, created_at)",
]
MIGRATIONS = {
    1: TABLES[:7] + INDEXES[:3],
    2: TABLES[7:] + INDEXES[3:],
    3: DISCOVERY_TABLES + DISCOVERY_INDEXES,
    4: JOB_TABLES + JOB_INDEXES,
}
TABLES += DISCOVERY_TABLES + JOB_TABLES
INDEXES += DISCOVERY_INDEXES + JOB_INDEXES
INTERRUPTED = "Previous writer exited before completion"


class WriterBusy(RuntimeError):
    pass


def _dict_row(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {column[0]: value for column, value in zip(cursor.description, row, strict=True)}


class SqliteDialect:
    """Embedded storage: one database file per data directory plus an OS file lock."""

    name = "sqlite"
    seq = "rowid"

    def __init__(self, root: Path):
        self.root = root

    @property
    def location(self) -> str:
        return str(self.root / "harness.sqlite3")

    def connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / "harness.sqlite3", timeout=5)
        db.row_factory = _dict_row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def sql(self, statement: str) -> str:
        return statement

    def render(self, ddl: str) -> str:
        return ddl.format(auto_pk="INTEGER PRIMARY KEY AUTOINCREMENT", seq_col="")

    def ensure_schema(self, db: sqlite3.Connection) -> None:
        version = db.execute("PRAGMA user_version").fetchone()["user_version"]
        if version < 0 or version > SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported database schema {version}; refusing to modify it")
        if version == SCHEMA_VERSION:
            return
        with self.transaction(db):
            for number, statements in MIGRATIONS.items():
                if number > version:
                    for statement in statements:
                        db.execute(self.render(statement))
                    db.execute(f"PRAGMA user_version = {number}")

    def schema_version(self, db: sqlite3.Connection) -> int:
        return db.execute("PRAGMA user_version").fetchone()["user_version"]

    @contextmanager
    def transaction(self, db: sqlite3.Connection) -> Iterator[None]:
        # SAVEPOINT starts a transaction when needed and preserves outer atomicity on nesting.
        name = f"tx_{uuid4().hex}"
        db.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            db.execute(f"ROLLBACK TO SAVEPOINT {name}")
            db.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            db.execute(f"RELEASE SAVEPOINT {name}")

    @contextmanager
    def lock(self, db: sqlite3.Connection, scope: str, shared: bool) -> Iterator[None]:
        try:
            with FileLock(self.root / "writer.lock", timeout=0):
                yield
        except Timeout as exc:
            raise WriterBusy(f"Another writer is using {self.root}; wait for it to finish") from exc


class PostgresDialect:
    """Shared storage: one Postgres schema for every pipeline, with advisory writer locks."""

    name = "postgres"
    seq = "seq"

    def __init__(self, url: str, schema: str = "research_harness"):
        if not IDENTIFIER.match(schema):
            raise ValueError("The database schema name must be a lowercase identifier")
        if urlsplit(url).scheme not in {"postgres", "postgresql"}:
            raise ValueError("RH_DATABASE_URL must be a postgresql:// connection URL")
        self.url = url
        self.schema = schema

    @property
    def location(self) -> str:
        parts = urlsplit(self.url)
        return f"{parts.hostname}:{parts.port or 5432}{parts.path} schema={self.schema}"

    def connect(self) -> Any:
        import psycopg
        from psycopg.rows import dict_row

        # Autocommit with explicit transaction blocks keeps single reads cheap and works with
        # session-mode connection poolers. Prepared statements are disabled for pooler safety.
        db = psycopg.connect(
            self.url, autocommit=True, row_factory=dict_row, prepare_threshold=None
        )
        try:
            db.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
        except psycopg.errors.UniqueViolation:
            pass  # Two agents created the schema at the same moment.
        db.execute(f'SET search_path TO "{self.schema}"')
        return db

    def sql(self, statement: str) -> str:
        return statement.replace("?", "%s")

    def render(self, ddl: str) -> str:
        return ddl.format(
            auto_pk="BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY",
            seq_col=", seq BIGINT GENERATED ALWAYS AS IDENTITY",
        )

    def ensure_schema(self, db: Any) -> None:
        with db.transaction():
            db.execute("SELECT pg_advisory_xact_lock(hashtext('research-harness:schema'))")
            db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            versions = {
                row["version"]
                for row in db.execute("SELECT version FROM schema_migrations").fetchall()
            }
            if versions and max(versions) > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema {max(versions)} is newer than this version supports; refusing to modify it"
                )
            current = max(versions, default=0)
            for number, statements in MIGRATIONS.items():
                if number > current:
                    for statement in statements:
                        db.execute(self.render(statement))
                    db.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                        (number, timestamp()),
                    )

    def schema_version(self, db: Any) -> int:
        row = db.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        return row["version"] or 0

    @contextmanager
    def transaction(self, db: Any) -> Iterator[None]:
        with db.transaction():
            yield

    @contextmanager
    def lock(self, db: Any, scope: str, shared: bool) -> Iterator[None]:
        key = f"research-harness:{scope}"
        acquire = "pg_try_advisory_lock_shared" if shared else "pg_try_advisory_lock"
        release = "pg_advisory_unlock_shared" if shared else "pg_advisory_unlock"
        if not db.execute(f"SELECT {acquire}(hashtext(%s)) AS ok", (key,)).fetchone()["ok"]:
            raise WriterBusy(f"Another writer holds the lock for {scope}; wait for it to finish")
        try:
            yield
        finally:
            db.execute(f"SELECT {release}(hashtext(%s))", (key,))


class Store:
    def __init__(
        self,
        root: Path | None = None,
        *,
        clock: Callable[[], datetime] = utcnow,
        dialect: SqliteDialect | PostgresDialect | None = None,
        blobs: Blobs | None = None,
        dataset_scope: str | None = None,
    ):
        if dialect is None:
            if root is None:
                raise ValueError("A local store needs a data directory")
            dialect = SqliteDialect(root)
        if blobs is None:
            if root is None:
                raise ValueError("A store needs blob storage")
            blobs = LocalBlobs(root / "raw")
        self.root = root
        self.dialect = dialect
        self.blobs = blobs
        self.clock = clock
        self.dataset_scope = dataset_scope
        self.db = dialect.connect()
        try:
            dialect.ensure_schema(self.db)
        except Exception:
            self.db.close()
            raise

    @property
    def mode(self) -> str:
        return self.dialect.name

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "database": self.dialect.location,
            "blobs": self.blobs.describe(),
            "schema_version": self.dialect.schema_version(self.db),
            "dataset_scope": self.dataset_scope,
        }

    def _pipeline(self, name: str) -> str:
        return f"{self.dataset_scope}/{name}" if self.dataset_scope else name

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # Low-level access. SQL is written with '?' placeholders and translated per dialect.
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        return self.db.execute(self.dialect.sql(sql), params)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        row = self.execute(sql, params).fetchone()
        return dict(row) if row else None

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = self.execute(sql, params).fetchone()
        return next(iter(row.values())) if row else None

    def count(self, table: str) -> int:
        if not IDENTIFIER.match(table):
            raise ValueError(f"Invalid table name: {table}")
        return int(self.scalar(f"SELECT COUNT(*) AS n FROM {table}"))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.dialect.transaction(self.db):
            yield

    @contextmanager
    def operation_lock(self, scope: str) -> Iterator[None]:
        """Serialize one service context without holding an SQL transaction during network work."""
        if isinstance(self.dialect, SqliteDialect):
            path = self.dialect.root / f"operation-{digest(scope)}.lock"
            try:
                with FileLock(path, timeout=0):
                    yield
            except Timeout as exc:
                raise WriterBusy(
                    f"Another writer holds the lock for {scope}; wait for it to finish"
                ) from exc
        else:
            with self.dialect.lock(self.db, scope, False):
                yield

    @contextmanager
    def writer(self, pipeline: str, *, shared: bool = False) -> Iterator[None]:
        """Hold the pipeline's writer lock. Exclusive writers also sweep abandoned runs.

        Shared mode is for bounded probes and inspections that never publish or advance state;
        several agents may hold it at once, so nothing is swept."""
        pipeline = self._pipeline(pipeline)
        with self.dialect.lock(self.db, pipeline, shared):
            if not shared:
                now = timestamp(self.clock())
                with self.transaction():
                    self.execute(
                        "UPDATE source_runs SET status='interrupted', finished_at=?, error=? WHERE status='running' AND run_id IN (SELECT id FROM runs WHERE pipeline=? AND status='running')",
                        (now, INTERRUPTED, pipeline),
                    )
                    self.execute(
                        "UPDATE runs SET status='interrupted', finished_at=?, error=? WHERE status='running' AND pipeline=?",
                        (now, INTERRUPTED, pipeline),
                    )
            yield

    def put_blob(self, body: bytes) -> str:
        return self.blobs.put(body)

    def read_blob(self, hashed: str) -> bytes:
        return self.blobs.get(validate_hash(hashed))

    def start_run(self, spec: PipelineSpec, *, run_id: str | None = None) -> str:
        run_id = run_id or uuid4().hex
        with self.transaction():
            self.execute(
                "INSERT INTO runs (id, pipeline, config_hash, config_json, started_at, finished_at, status, error) VALUES (?, ?, ?, ?, ?, NULL, 'running', NULL)",
                (
                    run_id,
                    self._pipeline(spec.name),
                    spec.fingerprint(),
                    canonical_json(spec.model_dump(mode="json")),
                    timestamp(self.clock()),
                ),
            )
        return run_id

    def start_source(self, run_id: str, source: SourceSpec) -> str:
        source_run_id = uuid4().hex
        with self.transaction():
            self.execute(
                "INSERT INTO source_runs (id, run_id, source_id, config_hash, started_at, status) VALUES (?, ?, ?, ?, ?, 'running')",
                (source_run_id, run_id, source.id, source.fingerprint(), timestamp(self.clock())),
            )
        return source_run_id

    def save_capture(self, source_run_id: str, capture: Capture) -> None:
        with self.transaction():
            self.execute(
                "INSERT INTO captures (id, source_run_id, source_id, url, observed_at, status_code, body_hash, headers_json, context_json, error, reused_capture_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        row = self.query_one(
            f"""SELECT c.* FROM captures c JOIN source_runs s ON s.id=c.source_run_id
               JOIN runs r ON r.id=s.run_id WHERE r.pipeline=? AND c.source_id=? AND c.url=?
               AND c.status_code=200 AND c.error IS NULL AND c.body_hash IS NOT NULL
               ORDER BY c.observed_at DESC, c.{self.dialect.seq} DESC LIMIT 1""",
            (self._pipeline(pipeline), source_id, url),
        )
        return self._capture(row) if row else None

    @staticmethod
    def _capture(row: dict[str, Any]) -> Capture:
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

    def _quarantine(
        self, source_run_id: str, issues: list[tuple[str | None, Issue]], now: str
    ) -> None:
        for capture_id, issue in issues:
            self.execute(
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
        failure_status: str = "failed",
    ) -> dict[str, Any]:
        if failure_status not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("Invalid source failure status")
        pipeline = self._pipeline(pipeline)
        status = failure_status if error else "degraded" if issues else "succeeded"
        now = timestamp(self.clock())
        new_versions = 0
        accepted = 0
        with self.transaction():
            self._quarantine(source_run_id, issues, now)
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
                    inserted = self.execute(
                        "INSERT INTO versions (id, pipeline, source_id, kind, record_key, content_hash, normalizer_version, data_json, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
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
                    accepted += self.execute(
                        "INSERT INTO observations (version_id, source_run_id, capture_id, observed_at, available_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                        (version_id, source_run_id, capture.id, capture.observed_at, now),
                    ).rowcount
                self.execute(
                    "INSERT INTO source_state (pipeline, source_id, config_hash, last_success_at, checkpoint_json) VALUES (?, ?, ?, ?, ?) ON CONFLICT(pipeline, source_id) DO UPDATE SET config_hash=excluded.config_hash, last_success_at=excluded.last_success_at, checkpoint_json=excluded.checkpoint_json",
                    (
                        pipeline,
                        source.id,
                        source.fingerprint(),
                        now,
                        canonical_json(checkpoint or {}),
                    ),
                )
            self.execute(
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

    def finish_probe(
        self, source_run_id: str, issues: list[tuple[str | None, Issue]], error: str | None
    ) -> None:
        """Record a sample probe. Captures and quarantine are kept; nothing is published."""
        now = timestamp(self.clock())
        with self.transaction():
            self.execute(
                "UPDATE source_runs SET status=?, finished_at=?, quarantined=?, error=? WHERE id=?",
                (
                    "probed" if not error and not issues else "probe_failed",
                    now,
                    len(issues),
                    error,
                    source_run_id,
                ),
            )
            self._quarantine(source_run_id, issues, now)

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        with self.transaction():
            self.execute(
                "UPDATE runs SET status=?, finished_at=?, error=? WHERE id=?",
                (status, timestamp(self.clock()), error, run_id),
            )

    def state(self, pipeline: str, source_id: str) -> dict[str, Any] | None:
        return self.query_one(
            "SELECT * FROM source_state WHERE pipeline=? AND source_id=?",
            (self._pipeline(pipeline), source_id),
        )

    def run_details(self, run_id: str) -> dict[str, Any]:
        result = self.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
        if not result:
            raise ValueError(f"Unknown run: {run_id}")
        result["config"] = json.loads(result.pop("config_json"))
        if self.dataset_scope:
            if result["pipeline"] != self._pipeline(result["config"]["name"]):
                raise ValueError("Run does not belong to this dataset")
            result["pipeline"] = result["config"]["name"]
            result["dataset_scope"] = self.dataset_scope
        result["sources"] = [
            {key: value for key, value in row.items() if key != "seq"}
            for row in self.query(
                "SELECT * FROM source_runs WHERE run_id=? ORDER BY started_at, id", (run_id,)
            )
        ]
        return result

    def recent_runs(self, pipeline: str, limit: int = 10) -> list[dict[str, Any]]:
        return self.query(
            "SELECT id, started_at, finished_at, status FROM runs WHERE pipeline=? ORDER BY started_at DESC LIMIT ?",
            (self._pipeline(pipeline), limit),
        )

    def latest_run(self, pipeline: str, config_hash: str | None = None) -> dict[str, Any] | None:
        if config_hash is None:
            return self.query_one(
                "SELECT id, status, started_at, finished_at FROM runs WHERE pipeline=? ORDER BY started_at DESC LIMIT 1",
                (self._pipeline(pipeline),),
            )
        return self.query_one(
            "SELECT id, status, started_at, finished_at FROM runs WHERE pipeline=? AND config_hash=? ORDER BY started_at DESC LIMIT 1",
            (self._pipeline(pipeline), config_hash),
        )

    def captures_for_run(self, run_id: str) -> list[Capture]:
        return [
            self._capture(row)
            for row in self.query(
                f"SELECT c.* FROM captures c JOIN source_runs s ON s.id=c.source_run_id WHERE s.run_id=? ORDER BY c.observed_at, c.{self.dialect.seq}",
                (run_id,),
            )
        ]

    def as_of(self, pipeline: str, cutoff: str, kind: str | None = None) -> list[dict[str, Any]]:
        kind_filter = "AND e.kind=?" if kind is not None else ""
        params: tuple[Any, ...] = (self._pipeline(pipeline), cutoff, cutoff) + (
            (kind,) if kind is not None else ()
        )
        rows = self.query(
            f"""WITH eligible AS (
                 SELECT v.*, o.capture_id, o.source_run_id, o.observed_at, o.available_at,
                   MIN(o.observed_at) OVER (PARTITION BY v.pipeline,v.source_id,v.kind,v.record_key) AS first_observed_at,
                   ROW_NUMBER() OVER (PARTITION BY v.pipeline,v.source_id,v.kind,v.record_key
                     ORDER BY o.observed_at DESC, o.id DESC) AS rn
                 FROM versions v JOIN observations o ON o.version_id=v.id
                 WHERE v.pipeline=? AND o.observed_at<=? AND o.available_at<=?
               ) SELECT e.*, c.url, c.body_hash, s.config_hash AS source_config_hash
                 FROM eligible e JOIN captures c ON c.id=e.capture_id
                 JOIN source_runs s ON s.id=e.source_run_id
                 WHERE e.rn=1 {kind_filter} ORDER BY e.source_id,e.kind,e.record_key""",
            params,
        )
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
            row = self.query_one(
                f"""SELECT s.* FROM source_runs s JOIN runs r ON r.id=s.run_id
                   WHERE r.pipeline=? AND s.source_id=? AND s.finished_at<=?
                   ORDER BY s.finished_at DESC, s.{self.dialect.seq} DESC LIMIT 1""",
                (self._pipeline(spec.name), source.id, cutoff),
            )
            successful = self.scalar(
                """SELECT MAX(s.finished_at) AS last_success FROM source_runs s JOIN runs r ON r.id=s.run_id
                   WHERE r.pipeline=? AND s.source_id=? AND s.status='succeeded' AND s.finished_at<=?""",
                (self._pipeline(spec.name), source.id, cutoff),
            )
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
        return self.query(
            "SELECT q.* FROM quarantine q JOIN source_runs s ON s.id=q.source_run_id WHERE s.run_id=? ORDER BY q.id",
            (run_id,),
        )

    def capture_dict(self, capture: Capture) -> dict[str, Any]:
        return asdict(capture)
