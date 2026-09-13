"""Storage backend selection.

Local mode (default): SQLite plus raw files per data directory, and a shared local registry.
Shared mode: Postgres plus an S3-compatible bucket, selected by RH_DATABASE_URL and RH_BLOB_*.
Supabase provides both; any other Postgres and S3 endpoint works the same way."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from research_harness.blobs import Blobs, S3Blobs
from research_harness.config import PipelineSpec, data_path
from research_harness.store import PostgresDialect, Store
from research_harness.util import digest, utcnow

ENV_PREFIX = "RH_"
CHECK_PAYLOAD = b"research-harness backend check\n"


def backend_errors() -> tuple[type[BaseException], ...]:
    """Driver exception classes the CLI reports as ordinary errors."""
    errors: list[type[BaseException]] = []
    try:
        import psycopg

        errors.append(psycopg.Error)
    except ImportError:  # pragma: no cover - dependency is pinned
        pass
    try:
        from botocore.exceptions import BotoCoreError, ClientError

        errors.extend([BotoCoreError, ClientError])
    except ImportError:  # pragma: no cover - dependency is pinned
        pass
    return tuple(errors)


def _mask(value: str | None) -> str | None:
    if not value:
        return value
    return value[:4] + "…" if len(value) > 8 else "…"


def _mask_url(url: str | None) -> str | None:
    if not url:
        return url
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.password:
        netloc = parts.netloc.replace(f":{parts.password}@", ":…@")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


@dataclass(frozen=True)
class BackendSettings:
    database_url: str | None = None
    schema: str = "research_harness"
    blob_bucket: str | None = None
    blob_endpoint: str | None = None
    blob_access_key: str | None = None
    blob_secret_key: str | None = None
    blob_region: str = "us-east-1"
    blob_prefix: str = "raw"
    local_root: Path = Path(".researchharness")

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, cwd: Path | None = None
    ) -> BackendSettings:
        env = os.environ if env is None else env
        get = lambda name, default=None: env.get(ENV_PREFIX + name) or default  # noqa: E731
        root = Path(get("LOCAL_ROOT", ".researchharness")).expanduser()
        if not root.is_absolute():
            root = (cwd or Path.cwd()) / root
        settings = cls(
            database_url=get("DATABASE_URL"),
            schema=get("DATABASE_SCHEMA", "research_harness"),
            blob_bucket=get("BLOB_BUCKET"),
            blob_endpoint=get("BLOB_ENDPOINT"),
            blob_access_key=get("BLOB_ACCESS_KEY"),
            blob_secret_key=get("BLOB_SECRET_KEY"),
            blob_region=get("BLOB_REGION", "us-east-1"),
            blob_prefix=get("BLOB_PREFIX", "raw"),
            local_root=root,
        )
        settings.validate()
        return settings

    @property
    def mode(self) -> str:
        return "postgres" if self.database_url else "local"

    def validate(self) -> None:
        if self.database_url:
            missing = [
                name
                for name, value in [
                    ("RH_BLOB_BUCKET", self.blob_bucket),
                    ("RH_BLOB_ACCESS_KEY", self.blob_access_key),
                    ("RH_BLOB_SECRET_KEY", self.blob_secret_key),
                ]
                if not value
            ]
            if missing:
                raise ValueError(
                    "RH_DATABASE_URL is set, so raw response bodies need an S3-compatible bucket; also set "
                    + ", ".join(missing)
                    + " (and RH_BLOB_ENDPOINT unless using AWS S3)"
                )
            PostgresDialect(self.database_url, self.schema)  # validates URL and schema name

    def describe(self) -> dict[str, Any]:
        if self.mode == "local":
            return {"mode": "local", "local_root": str(self.local_root)}
        return {
            "mode": "postgres",
            "database_url": _mask_url(self.database_url),
            "schema": self.schema,
            "blob_bucket": self.blob_bucket,
            "blob_endpoint": self.blob_endpoint,
            "blob_region": self.blob_region,
            "blob_prefix": self.blob_prefix,
            "blob_access_key": _mask(self.blob_access_key),
        }


def s3_client(settings: BackendSettings) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.blob_endpoint,
        aws_access_key_id=settings.blob_access_key,
        aws_secret_access_key=settings.blob_secret_key,
        region_name=settings.blob_region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


class Backend:
    def __init__(
        self,
        settings: BackendSettings,
        *,
        clock: Callable[[], datetime] = utcnow,
        blobs: Blobs | None = None,
    ):
        settings.validate()
        self.settings = settings
        self.clock = clock
        self._blobs = blobs

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, cwd: Path | None = None, **kwargs: Any
    ) -> Backend:
        return cls(BackendSettings.from_env(env, cwd=cwd), **kwargs)

    @classmethod
    def local(cls, root: Path, **kwargs: Any) -> Backend:
        return cls(BackendSettings(local_root=root), **kwargs)

    @property
    def mode(self) -> str:
        return self.settings.mode

    def blobs(self) -> Blobs:
        if self._blobs is None:
            if self.mode != "postgres":
                raise RuntimeError("Local mode stores blobs beside each database")
            self._blobs = S3Blobs(
                s3_client(self.settings),
                self.settings.blob_bucket or "",
                self.settings.blob_prefix,
            )
        return self._blobs

    def open_store(
        self,
        local_dir: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        dataset_scope: str | None = None,
    ) -> Store:
        """Open storage for one data directory. Shared mode ignores the directory."""
        clock = clock or self.clock
        if self.mode == "postgres":
            return Store(
                dialect=PostgresDialect(self.settings.database_url or "", self.settings.schema),
                blobs=self.blobs(),
                clock=clock,
                dataset_scope=dataset_scope,
            )
        return Store(local_dir, clock=clock, dataset_scope=dataset_scope)

    def open_registry(self, *, clock: Callable[[], datetime] | None = None) -> Store:
        return self.open_store(self.settings.local_root / "registry", clock=clock)

    def describe_pipelines(
        self, store: Store, pipelines: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Attach run state from each registered dataset, including separate local databases."""
        from research_harness import registry

        for pipeline in pipelines:
            _, spec = registry.pipeline_spec(store, pipeline["id"])
            pipeline["legacy_unscoped_data"] = self.legacy_pipeline_data(spec)
            with self.pipeline_store(spec, None, question_id=pipeline["question_id"]) as data:
                pipeline["latest_run"] = data.latest_run(spec.name, spec.fingerprint())
        return pipelines

    def pipeline_store(
        self,
        spec: PipelineSpec,
        config_path: Path | None,
        *,
        clock: Callable[[], datetime] | None = None,
        question_id: str | None = None,
    ) -> Store:
        if question_id is not None:
            if not re.fullmatch(r"[0-9a-f]{32}", question_id) or config_path is not None:
                raise ValueError(
                    "Registered storage requires a canonical question id and no file path"
                )
            local_dir = self.settings.local_root / "pipelines" / question_id / spec.name
        elif config_path is not None:
            local_dir = data_path(spec, config_path)
        else:
            local_dir = self.settings.local_root / "pipelines" / spec.name
        return self.open_store(local_dir, clock=clock, dataset_scope=question_id)

    def ensure_bucket(self) -> dict[str, Any]:
        """Verify the bucket exists, creating it when the endpoint allows it."""
        blobs = self.blobs()
        if not isinstance(blobs, S3Blobs):
            return {"bucket": None, "status": "not_applicable"}
        try:
            blobs.client.head_bucket(Bucket=blobs.bucket)
            return {"bucket": blobs.bucket, "status": "exists"}
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
        try:
            blobs.client.create_bucket(Bucket=blobs.bucket)
        except Exception as exc:
            raise RuntimeError(
                f"Bucket {blobs.bucket!r} does not exist and could not be created here ({exc}). "
                "Create a private bucket with that name in the storage dashboard, then rerun."
            ) from exc
        return {"bucket": blobs.bucket, "status": "created"}

    def legacy_pipeline_data(self, spec: PipelineSpec) -> dict[str, Any] | None:
        """Report pre-namespace data without attributing it to an arbitrary research question."""
        root = self.settings.local_root / "pipelines" / spec.name
        if self.mode == "local" and not (root / "harness.sqlite3").is_file():
            return None
        with self.open_store(root) as store:
            count = int(store.scalar("SELECT COUNT(*) FROM runs WHERE pipeline=?", (spec.name,)))
        if not count:
            return None
        return {
            "run_count": count,
            "location": str(root) if self.mode == "local" else self.settings.schema,
            "automatically_imported": False,
            "access": "Use --legacy-unscoped with rh status, export, or replay and the registered pipeline id.",
        }

    def check(self) -> dict[str, Any]:
        """Connectivity report: schema version, registry counts, and a blob round trip."""
        with self.open_registry() as store:
            report: dict[str, Any] = {
                **self.settings.describe(),
                **store.describe(),
                "questions": store.count("questions"),
                "pipeline_versions": store.count("pipeline_versions"),
                "runs": store.count("runs"),
            }
            if self.mode == "postgres":
                report["bucket"] = self.ensure_bucket()
            hashed = store.put_blob(CHECK_PAYLOAD)
            report["blob_round_trip"] = store.read_blob(hashed) == CHECK_PAYLOAD
            report["check_blob_sha256"] = digest(CHECK_PAYLOAD)
        return report
