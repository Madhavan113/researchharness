from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from research_harness.util import atomic_write, digest

MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


def validate_hash(hashed: str) -> str:
    if len(hashed) != 64 or any(c not in "0123456789abcdef" for c in hashed):
        raise ValueError("Invalid response content hash")
    return hashed


def _error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error") or {}
        code = error.get("Code") or response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return str(code) if code is not None else None
    return None


class Blobs(Protocol):
    """Immutable response bodies addressed by their SHA-256 digest."""

    def put(self, body: bytes) -> str: ...

    def get(self, hashed: str) -> bytes: ...

    def count(self) -> int: ...

    def describe(self) -> str: ...


class LocalBlobs:
    def __init__(self, root: Path):
        self.root = root

    def path(self, hashed: str) -> Path:
        return self.root / hashed[:2] / hashed

    def put(self, body: bytes) -> str:
        hashed = digest(body)
        path = self.path(hashed)
        if path.exists():
            if digest(path.read_bytes()) != hashed:
                raise RuntimeError(f"Stored response failed its integrity check: {hashed}")
        else:
            atomic_write(path, body)
        return hashed

    def get(self, hashed: str) -> bytes:
        validate_hash(hashed)
        body = self.path(hashed).read_bytes()
        if digest(body) != hashed:
            raise RuntimeError(f"Stored response failed its integrity check: {hashed}")
        return body

    def count(self) -> int:
        if not self.root.exists():
            return 0
        return sum(1 for _ in self.root.rglob("?" * 64))

    def describe(self) -> str:
        return f"local directory {self.root}"


class S3Blobs:
    """Bodies in an S3-compatible bucket (Supabase Storage, R2, AWS S3)."""

    def __init__(self, client: Any, bucket: str, prefix: str = "raw"):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def key(self, hashed: str) -> str:
        tail = f"{hashed[:2]}/{hashed}"
        return f"{self.prefix}/{tail}" if self.prefix else tail

    def exists(self, hashed: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.key(hashed))
        except Exception as exc:
            if _error_code(exc) in MISSING_CODES:
                return False
            raise
        return True

    def put(self, body: bytes) -> str:
        hashed = digest(body)
        if not self.exists(hashed):
            self.client.put_object(
                Bucket=self.bucket,
                Key=self.key(hashed),
                Body=body,
                ContentType="application/octet-stream",
                Metadata={"sha256": hashed},
            )
        return hashed

    def get(self, hashed: str) -> bytes:
        validate_hash(hashed)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.key(hashed))
        except Exception as exc:
            if _error_code(exc) in MISSING_CODES:
                raise FileNotFoundError(f"Stored response is missing: {hashed}") from exc
            raise
        body = response["Body"].read()
        if digest(body) != hashed:
            raise RuntimeError(f"Stored response failed its integrity check: {hashed}")
        return body

    def count(self) -> int:
        total = 0
        prefix = f"{self.prefix}/" if self.prefix else ""
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        ):
            total += len(page.get("Contents") or [])
        return total

    def describe(self) -> str:
        return f"bucket {self.bucket}/{self.prefix}"
