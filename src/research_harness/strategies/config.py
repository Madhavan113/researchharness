"""A single immutable Python strategy and its explicit execution controls."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, StrictBool, StrictInt, model_serializer

from research_harness.config import StrictModel
from research_harness.strategies.sandbox import SandboxConfig, _json_object
from research_harness.util import canonical_json, digest, write_json


class StrategyConfig(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
    source: str = Field(default="strategy.py", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.py$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sandbox: SandboxConfig
    observations: StrictBool = True
    context: StrictBool = False
    finalize_on_stop: StrictBool = False
    max_events: StrictInt = Field(default=128, ge=1, le=1000)
    max_state_bytes: StrictInt = Field(default=65536, ge=1024, le=1024 * 1024)
    max_render_bytes: StrictInt = Field(default=32768, ge=1024, le=1024 * 1024)

    @model_serializer(mode="wrap")
    def legacy_controls(self, handler):
        value = handler(self)
        # Disabled is the original advisory contract. Preserve its frozen digest.
        if not self.finalize_on_stop:
            value.pop("finalize_on_stop", None)
        return value


def _path(path: Path) -> Path:
    supplied = Path(path).expanduser().absolute()
    if ".." in supplied.parts or any(p.is_symlink() for p in (supplied, *supplied.parents)):
        raise ValueError("Strategy bundle paths cannot contain parent traversal or symlinks")
    return supplied.resolve()


def _bytes(path: Path, limit: int) -> bytes:
    path = _path(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Strategy files must be regular files without links")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Strategy file exceeds the configured limit")
    return raw


@dataclass(frozen=True)
class StrategyBundle:
    manifest_path: Path
    source: Path
    config: StrategyConfig
    sha256: str

    @classmethod
    def load(cls, manifest_path: Path) -> StrategyBundle:
        manifest = _path(manifest_path)
        value = _json_object(_bytes(manifest, 65536))
        if "schema_version" in value and type(value["schema_version"]) is not int:
            raise ValueError("Strategy schema_version must be an integer")
        config = StrategyConfig.model_validate(value)
        source = manifest.parent / config.source
        raw = _bytes(source, config.sandbox.max_source_bytes)
        if digest(raw) != config.source_sha256:
            raise ValueError("Strategy source differs from the declared digest")
        return cls(manifest, source, config, digest(canonical_json(config.model_dump(mode="json"))))

    def freeze(self, new_directory: Path) -> StrategyBundle:
        current = self.load(self.manifest_path)
        if current.sha256 != self.sha256:
            raise ValueError("Strategy configuration changed before freezing")
        raw = _bytes(current.source, current.config.sandbox.max_source_bytes)
        if digest(raw) != current.config.source_sha256:
            raise ValueError("Strategy source changed before freezing")
        target = _path(new_directory)
        target.mkdir(parents=True, exist_ok=False)
        with (target / current.config.source).open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        write_json(target / "strategy.json", current.config.model_dump(mode="json"))
        descriptor = os.open(target, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        frozen = self.load(target / "strategy.json")
        if frozen.sha256 != self.sha256 or self.load(self.manifest_path).sha256 != self.sha256:
            raise ValueError("Strategy changed while freezing")
        return frozen
