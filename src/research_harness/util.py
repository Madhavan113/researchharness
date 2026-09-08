from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pydantic import ValidationError


def utcnow() -> datetime:
    return datetime.now(UTC)


def timestamp(value: datetime | None = None) -> str:
    value = value or utcnow()
    if value.tzinfo is None:
        raise ValueError("A timestamp must include a timezone")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Include a timezone, e.g. 2026-09-07T19:00:00Z")
    return parsed.astimezone(UTC)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def error_message(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return canonical_json(
            error.errors(include_input=False, include_context=False, include_url=False)
        )
    return str(error)


def digest(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def atomic_write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = content.encode("utf-8") if isinstance(content, str) else content
    fd, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def http_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Use an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("Credentials must not be embedded in URLs")
    if parsed.fragment:
        raise ValueError("Source URLs must not contain fragments")
    for key, _value in parse_qsl(parsed.query):
        if key.lower().replace("-", "_") in {
            "api_key",
            "apikey",
            "access_token",
            "token",
            "secret",
            "password",
            "authorization",
        }:
            raise ValueError("Source URLs must not contain credentials")
    return value


def json_pointer(value: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 pointer without evaluating expressions or code."""
    if not pointer:
        return value
    if not pointer.startswith("/"):
        raise ValueError("JSON pointers must be empty or start with /")
    for encoded in pointer[1:].split("/"):
        if "~" in encoded.replace("~0", "").replace("~1", ""):
            raise ValueError("Invalid JSON pointer escape")
        key = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not key.isdecimal() or (len(key) > 1 and key.startswith("0")):
                raise ValueError(f"Invalid array index in pointer: {key}")
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise ValueError(f"Cannot traverse pointer segment: {key}")
    return value
