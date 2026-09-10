"""Freeze development experience and close search before private final evaluation.

This is a trusted controller API, never a candidate tool. The evaluator callback
must independently evaluate artifacts; its Python identity is not authenticated
by a supplied fingerprint. The host must mount only ``feedback_dir`` for a
proposer, and revoke execution when search closes. File hashes detect changes;
they are not protection against a host user rewriting both files and journals.
Dedicated artifact directories must contain development data only. Every regular
file is copied unchanged; oversized or unsafe trees fail instead of being trimmed.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from filelock import FileLock
from pydantic import Field, StrictBool, StrictInt, model_validator

from research_harness.config import StrictModel
from research_harness.evaluation.benchmark import load_benchmark
from research_harness.strategies.sandbox import SandboxConfig
from research_harness.util import canonical_json, digest, timestamp, write_json

IDENTIFIER = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$"
SHA256 = r"^[0-9a-f]{64}$"


class ArchiveConfig(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=IDENTIFIER)
    execution: Literal["fixture", "model"]
    baseline_candidate_id: str = Field(default="baseline", pattern=IDENTIFIER)
    fixed_controls: dict[str, Any] = Field(min_length=1)
    max_files: StrictInt = Field(default=100_000, ge=1)
    max_bytes: StrictInt = Field(default=1_073_741_824, ge=1)

    @model_validator(mode="after")
    def measured_controls(self):
        if self.execution != "model":
            return self
        controls = self.fixed_controls
        required = {
            "runtime",
            "model",
            "model_settings",
            "provider",
            "provider_settings",
            "budgets",
            "sandbox",
        }
        if not required <= set(controls):
            raise ValueError(
                "Measured archives require explicit runtime/model/provider/settings/budgets/sandbox controls"
            )
        if any(
            not isinstance(controls[key], str) or not controls[key].strip()
            for key in ("runtime", "model", "provider")
        ):
            raise ValueError("Measured runtime, model, and provider must be explicit")
        if any(
            not isinstance(controls[key], dict)
            for key in ("model_settings", "provider_settings", "budgets", "sandbox")
        ):
            raise ValueError("Measured settings, budgets, and sandbox must be objects")
        budgets = controls["budgets"]
        if not {
            "search",
            "inspection",
            "probe",
            "deadline_seconds",
            "model_rounds",
            "max_output_tokens",
        } <= set(budgets):
            raise ValueError("Record every discovery operation and model budget")
        if any(type(value) is not int or value < 0 for value in budgets.values()) or any(
            budgets[key] == 0 for key in ("deadline_seconds", "model_rounds", "max_output_tokens")
        ):
            raise ValueError(
                "Measured budgets require nonnegative integers and positive model/deadline limits"
            )
        if (
            type(controls["model_settings"].get("max_output_tokens")) is not int
            or controls["model_settings"]["max_output_tokens"] != budgets["max_output_tokens"]
        ):
            raise ValueError("Model output cap must match the frozen budget")
        sandbox = SandboxConfig.model_validate(controls["sandbox"])
        if set(sandbox.model_dump()) != set(controls["sandbox"]):
            raise ValueError("Freeze all sandbox fields explicitly, including default limits")
        return self


class CaseEvidence(StrictModel):
    """Independent development outcome, including every failure and unknown cost."""

    case_id: str = Field(min_length=1)
    status: Literal["ok", "invalid", "execution_failed", "artifact_error", "unmeasured"]
    quality: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False, strict=True)
    total_tokens: StrictInt | None = Field(default=None, ge=0)
    completed_token_lower_bound: StrictInt | None = Field(default=None, ge=0)
    evidence_files: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self):
        if self.status == "ok" and (self.quality is None or not self.evidence_files):
            raise ValueError("Successful evidence needs measured quality and artifact references")
        if self.status == "unmeasured" and self.quality is not None:
            raise ValueError("Unmeasured quality must remain unknown")
        if self.status in {"invalid", "execution_failed", "artifact_error"} and (
            self.quality != 0 or not self.errors
        ):
            raise ValueError("Failures require zero quality and an explicit reason")
        if (
            self.total_tokens is not None
            and self.completed_token_lower_bound is not None
            and self.completed_token_lower_bound > self.total_tokens
        ):
            raise ValueError("A completed-token lower bound cannot exceed known total usage")
        if len(self.evidence_files) != len(set(self.evidence_files)):
            raise ValueError("Artifact references must be unique within a case")
        return self


class DevelopmentEvidence(StrictModel):
    """Returned by the host evaluator on the immutable input supplied below."""

    schema_version: Literal[1] = 1
    split: Literal["development"] = "development"
    candidate_id: str = Field(pattern=IDENTIFIER)
    candidate_sha256: str = Field(pattern=SHA256)
    artifacts_sha256: str = Field(pattern=SHA256)
    controls_sha256: str = Field(pattern=SHA256)
    evaluator_sha256: str = Field(pattern=SHA256)
    benchmark_sha256: str = Field(pattern=SHA256)
    execution: Literal["fixture", "model"]
    execution_verified: StrictBool = False
    cases: list[CaseEvidence] = Field(min_length=1)
    limitations: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class EvaluationInput:
    """Host-only paths; never serialize this object into proposer feedback."""

    artifacts_dir: Path
    candidate_dir: Path
    benchmark_path: Path
    case_ids: tuple[str, ...]
    binding: dict[str, Any]
    inventory: dict[str, str]

    def evidence(
        self, *, cases: list[CaseEvidence], execution_verified: bool = False, limitations: list[str]
    ) -> DevelopmentEvidence:
        return DevelopmentEvidence(
            **self.binding,
            cases=cases,
            execution_verified=execution_verified,
            limitations=limitations,
        )


def _read(path: Path) -> dict:
    path = _absolute(path)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    value = json.loads(
        path.read_bytes(),
        object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"Invalid JSON constant: {value}")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _save(path: Path, value: dict) -> None:
    path = _absolute(path)
    write_json(path, value)
    _sync_directory(path.parent)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_file(path: Path, raw: bytes) -> None:
    path = _absolute(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _sync_directories(root: Path) -> None:
    for directory, _, _ in os.walk(root, topdown=False):
        _sync_directory(Path(directory))
    _sync_directory(root.parent)


def _absolute(path: Path) -> Path:
    supplied = Path(path).absolute()
    if ".." in supplied.parts:
        raise ValueError("Paths must not contain parent traversal")
    for component in (supplied, *supplied.parents):
        if component.is_symlink():
            raise ValueError("Symlink paths are not allowed")
    return supplied.resolve()


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or path.as_posix() != value
    ):
        raise ValueError("Artifact references must be normalized local POSIX paths")
    return value


def _disjoint(first: Path, second: Path) -> None:
    if first.is_relative_to(second) or second.is_relative_to(first):
        raise ValueError(
            "Host state, feedback, inputs, and private final directories must be separate"
        )


def _inventory(root: Path, config: ArchiveConfig) -> dict[str, str]:
    root = _absolute(root)
    if not root.is_dir():
        raise ValueError("Expected a dedicated artifact directory")
    result, total = {}, 0
    for directory, names, files in os.walk(root, followlinks=False):
        for name in sorted(names + files):
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Archive inputs must contain regular files, without links")
            relative = _relative(path.relative_to(root).as_posix())
            total += info.st_size
            if len(result) >= config.max_files or total > config.max_bytes:
                raise ValueError("Complete artifact inventory exceeds configured limits")
            raw = path.read_bytes()
            if len(raw) != info.st_size:
                raise ValueError("Artifact changed while reading")
            result[relative] = digest(raw)
    return dict(sorted(result.items()))


def _copy(root: Path, target: Path, inventory: dict[str, str], config: ArchiveConfig) -> None:
    root, target = _absolute(root), _absolute(target)
    target.mkdir(parents=True, exist_ok=False)
    for relative, expected in inventory.items():
        source = root / _relative(relative)
        raw = source.read_bytes()
        if digest(raw) != expected:
            raise ValueError("Artifact changed while copying")
        destination = target / relative
        _write_file(destination, raw)
    if _inventory(root, config) != inventory or _inventory(target, config) != inventory:
        raise ValueError("Artifact inventory changed while copying")
    _sync_directories(target)


def _publish_copy(
    root: Path, target: Path, inventory: dict[str, str], config: ArchiveConfig, staging: Path
) -> None:
    """Finish only a journal-authorized copy, including a matching partial copy."""
    root, target = _absolute(root), _absolute(target)
    if _inventory(root, config) != inventory:
        raise ValueError("Pending publication source changed")
    target.mkdir(parents=True, exist_ok=True)
    staging = _absolute(staging)
    staging.mkdir(parents=True, exist_ok=True)
    staged = _inventory(staging, config)
    if not set(staged) <= {digest(name) + ".part" for name in inventory}:
        raise ValueError("Unexpected publication staging file")
    present = _inventory(target, config)
    if any(inventory.get(name) != value for name, value in present.items()):
        raise ValueError("Pending publication contains unexpected or changed files")
    for relative, expected in inventory.items():
        if relative not in present:
            raw = (root / _relative(relative)).read_bytes()
            if digest(raw) != expected:
                raise ValueError("Pending publication source changed")
            staged_path = staging / (digest(relative) + ".part")
            # Partial writes stay outside feedback and are never evidence. Only
            # this operation's authorized staging names may be recreated.
            staged_path.unlink(missing_ok=True)
            _write_file(staged_path, raw)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_path, destination)
    if _inventory(root, config) != inventory or _inventory(target, config) != inventory:
        raise ValueError("Pending publication inventory changed")
    _sync_directories(target)
    _sync_directories(staging)


def _fingerprint(inventory: Mapping[str, str]) -> str:
    return digest(canonical_json(dict(inventory)))


class OptimizationArchive:
    """Durable host archive. Opening never resumes an evaluator or a model turn."""

    def __init__(self, state_dir: Path):
        self.root = _absolute(state_dir)
        self.lock = FileLock(str(self.root) + ".lock")
        with self.lock:
            self._load()

    @classmethod
    def create(
        cls,
        state_dir: Path,
        feedback_dir: Path,
        *,
        config: ArchiveConfig,
        frozen_inputs: Mapping[str, Path],
    ) -> OptimizationArchive:
        """Freeze benchmark/evaluator/backend files in host storage, not feedback.

        ``development_manifest`` must be a supported development benchmark manifest.
        ``evaluator`` and ``backend`` are dedicated files/directories whose complete
        bytes identify the fixed implementation. Extra named inputs are allowed.
        ``fixed_controls`` must also freeze model/provider/budgets and runner limits.
        """
        config = ArchiveConfig.model_validate(config)
        canonical_json(config.model_dump(mode="json"))
        root, feedback = _absolute(state_dir), _absolute(feedback_dir)
        _disjoint(root, feedback)
        staging = feedback.parent / f".{feedback.name}-publication-{digest(str(root))[:20]}"
        _disjoint(root, staging)
        if not {"development_manifest", "evaluator", "backend"} <= set(frozen_inputs):
            raise ValueError("Freeze the development manifest, evaluator, and backend")
        if root.exists() or feedback.exists() or staging.exists():
            raise ValueError("Archive and feedback directories must be new")
        benchmark_path = _absolute(frozen_inputs["development_manifest"])
        benchmark = load_benchmark(benchmark_path)
        if benchmark.manifest.split != "development":
            raise ValueError("Only development benchmarks may enter search archives")
        if config.execution == "model" and any(
            case.review_status != "reviewed" for case in benchmark.cases.values()
        ):
            raise ValueError(
                "Measured optimization requires independently reviewed development cases"
            )
        inputs: dict[str, tuple[Path, dict[str, str]]] = {}
        for name, supplied in frozen_inputs.items():
            if not name.isidentifier():
                raise ValueError("Frozen input names must be simple identifiers")
            source = _absolute(supplied)
            _disjoint(root, source)
            _disjoint(feedback, source)
            _disjoint(staging, source)
            if name == "development_manifest":
                files = {benchmark_path.name: digest(benchmark_path.read_bytes())}
                for reference, case in zip(
                    benchmark.manifest.cases, benchmark.cases.values(), strict=True
                ):
                    for relative in (reference.path, case.fixtures.path):
                        relative = _relative(relative)
                        item = _absolute(benchmark_path.parent / relative)
                        if not item.is_relative_to(benchmark_path.parent):
                            raise ValueError("Benchmark file escapes its package")
                        files[relative] = digest(item.read_bytes())
                inputs[name] = (benchmark_path.parent, files)
            elif source.is_dir():
                inputs[name] = (source, _inventory(source, config))
            elif source.is_file() and source.stat().st_nlink == 1:
                inputs[name] = (source.parent, {source.name: digest(source.read_bytes())})
            else:
                raise ValueError("Frozen inputs must be regular files or directories")
        with FileLock(str(root) + ".lock"):
            root.mkdir(parents=True, exist_ok=False)
            frozen = {}
            for name, (source, files) in inputs.items():
                target = root / "frozen" / name
                target.mkdir(parents=True)
                for relative, expected in files.items():
                    raw = (source / relative).read_bytes()
                    if digest(raw) != expected:
                        raise ValueError("Frozen input changed while copying")
                    destination = target / relative
                    _write_file(destination, raw)
                if _inventory(target, config) != dict(sorted(files.items())):
                    raise ValueError("Frozen input copy is incomplete")
                if (
                    name != "development_manifest"
                    and _absolute(frozen_inputs[name]).is_dir()
                    and _inventory(source, config) != files
                ):
                    raise ValueError("Frozen source directory changed while copying")
                frozen[name] = files
            _sync_directories(root / "frozen")
            feedback.mkdir(parents=True, exist_ok=False)
            staging.mkdir(parents=True, exist_ok=False)
            plan = {
                "schema_version": 1,
                "created_at": timestamp(),
                "config": config.model_dump(mode="json"),
                "feedback_dir": str(feedback),
                "publication_staging_dir": str(staging),
                "frozen_inputs": frozen,
                "benchmark_path": f"frozen/development_manifest/{benchmark_path.name}",
                "benchmark_sha256": benchmark.sha256,
                "case_ids": list(benchmark.cases),
                "controls_sha256": digest(canonical_json(config.fixed_controls)),
                "evaluator_sha256": _fingerprint(frozen["evaluator"]),
            }
            _save(root / "archive.json", plan)
            _save(
                root / "journal.json",
                {
                    "schema_version": 1,
                    "archive_sha256": digest(canonical_json(plan)),
                    "phase": "search",
                    "operations": {},
                    "candidates": {},
                    "selection": None,
                    "final": None,
                },
            )
        return cls(root)

    def _load(self) -> tuple[dict, dict, ArchiveConfig]:
        plan, journal = _read(self.root / "archive.json"), _read(self.root / "journal.json")
        config = ArchiveConfig.model_validate(plan["config"])
        if journal.get("schema_version") != 1 or journal.get("archive_sha256") != digest(
            canonical_json(plan)
        ):
            raise ValueError("Archive journal does not match frozen inputs")
        feedback = _absolute(Path(plan["feedback_dir"]))
        staging = _absolute(Path(plan["publication_staging_dir"]))
        _disjoint(self.root, feedback)
        _disjoint(staging, feedback)
        _disjoint(staging, self.root)
        for name, expected in plan["frozen_inputs"].items():
            if (
                not name.isidentifier()
                or _inventory(self.root / "frozen" / name, config) != expected
            ):
                raise ValueError("Frozen host input changed")
        benchmark = load_benchmark(self.root / _relative(plan["benchmark_path"]))
        if (
            benchmark.sha256 != plan["benchmark_sha256"]
            or list(benchmark.cases) != plan["case_ids"]
        ):
            raise ValueError("Frozen development benchmark changed")
        feedback_files = {}
        for candidate_id, candidate in journal["candidates"].items():
            for key in ("bundle", "development"):
                record = candidate.get(key)
                if record is None:
                    continue
                relative = f"candidates/{_relative(candidate_id)}/{key}"
                expected = record["files"]
                if _inventory(self.root / relative, config) != expected:
                    raise ValueError("Immutable candidate artifact changed")
                if _inventory(feedback / relative, config) != expected:
                    raise ValueError("Proposer feedback artifact changed")
                feedback_files.update(
                    {f"{relative}/{name}": value for name, value in expected.items()}
                )
        for operation in journal["operations"].values():
            publication = operation.get("publication")
            if operation["status"] != "pending" or publication is None:
                continue
            candidate_id, key = publication["candidate_id"], publication["key"]
            if re.fullmatch(IDENTIFIER, candidate_id) is None or key not in {
                "bundle",
                "development",
            }:
                raise ValueError("Invalid pending publication binding")
            candidate = journal["candidates"].get(candidate_id)
            if candidate is None or candidate["status"] != "publishing" or key in candidate:
                raise ValueError("Pending publication conflicts with candidate state")
            relative = f"candidates/{candidate_id}/{key}"
            expected = publication["files"]
            if _inventory(self.root / relative, config) != expected:
                raise ValueError("Pending publication source changed")
            destination = feedback / relative
            present = _inventory(destination, config) if destination.exists() else {}
            if any(expected.get(name) != value for name, value in present.items()):
                raise ValueError("Pending publication contains unexpected or changed files")
            feedback_files.update({f"{relative}/{name}": value for name, value in present.items()})
        if _inventory(feedback, config) != dict(sorted(feedback_files.items())):
            raise ValueError("Unexpected files in proposer feedback")
        return plan, journal, config

    def status(self) -> dict:
        """Host status only; no evaluator is restarted by loading it."""
        with self.lock:
            _, journal, _ = self._load()
            return journal

    def _reserve(self, journal: dict, operation_id: str, kind: str, payload: dict) -> dict | None:
        if not operation_id or len(operation_id) > 200:
            raise ValueError("Provide a bounded nonempty operation id")
        request_hash = digest(canonical_json({"kind": kind, "payload": payload}))
        old = journal["operations"].get(operation_id)
        if old:
            if old["request_sha256"] != request_hash:
                raise ValueError("Operation id was reused with different inputs")
            if old["status"] != "completed":
                raise ValueError("Operation is unresolved; do not replay it automatically")
            return old["result"]
        journal["operations"][operation_id] = {
            "kind": kind,
            "request_sha256": request_hash,
            "status": "pending",
            "created_at": timestamp(),
        }
        _save(self.root / "journal.json", journal)
        return None

    def _complete(self, journal: dict, operation_id: str, result: dict) -> dict:
        journal["operations"][operation_id].update(status="completed", result=result)
        _save(self.root / "journal.json", journal)
        return result

    def _prepare_publication(
        self,
        plan: dict,
        journal: dict,
        config: ArchiveConfig,
        *,
        candidate_id: str,
        key: str,
        inventory: dict[str, str],
        updates: dict,
        result: dict,
        operation_id: str,
    ) -> dict:
        candidate = journal["candidates"][candidate_id]
        final_candidate = {
            **candidate,
            **updates,
            key: {"files": inventory, "sha256": _fingerprint(inventory)},
        }
        journal["operations"][operation_id]["publication"] = {
            "candidate_id": candidate_id,
            "key": key,
            "files": inventory,
            "candidate": final_candidate,
            "result": result,
        }
        candidate["status"] = "publishing"
        _save(self.root / "journal.json", journal)
        return self._finish_publication(plan, journal, config, operation_id)

    def _finish_publication(
        self, plan: dict, journal: dict, config: ArchiveConfig, operation_id: str
    ) -> dict:
        publication = journal["operations"][operation_id]["publication"]
        relative = f"candidates/{publication['candidate_id']}/{publication['key']}"
        _publish_copy(
            self.root / relative,
            Path(plan["feedback_dir"]) / relative,
            publication["files"],
            config,
            Path(plan["publication_staging_dir"]) / digest(operation_id),
        )
        journal["candidates"][publication["candidate_id"]] = publication["candidate"]
        return self._complete(journal, operation_id, publication["result"])

    def _repeat(
        self,
        plan: dict,
        journal: dict,
        config: ArchiveConfig,
        operation_id: str,
        kind: str,
        payload: dict,
    ) -> dict:
        operation = journal["operations"][operation_id]
        expected = digest(canonical_json({"kind": kind, "payload": payload}))
        if operation["request_sha256"] != expected:
            raise ValueError("Operation id was reused with different inputs")
        if operation["status"] == "completed":
            return operation["result"]
        if "publication" in operation:
            return self._finish_publication(plan, journal, config, operation_id)
        raise ValueError("Operation is unresolved; do not replay it automatically")

    def recover_publication(self, *, operation_id: str) -> dict:
        """Finish a previously recorded copy/commit without rerunning its callback."""
        with self.lock:
            plan, journal, config = self._load()
            self._search(journal)
            operation = journal["operations"].get(operation_id)
            if operation is None or "publication" not in operation:
                raise ValueError("No durable publication intent exists for this operation")
            if operation["status"] == "completed":
                return operation["result"]
            return self._finish_publication(plan, journal, config, operation_id)

    @staticmethod
    def _search(journal: dict) -> None:
        if journal["phase"] != "search":
            raise ValueError("Search is permanently closed for this archive")

    def register_candidate(
        self,
        candidate_id: str,
        source_dir: Path,
        *,
        instructions: str,
        manifest: dict[str, Any],
        operation_id: str,
    ) -> dict:
        """Copy a dedicated candidate source tree; never import or execute its code."""
        # Validate ids without assigning meaning to the runner's opaque manifest.
        if re.fullmatch(IDENTIFIER, candidate_id) is None:
            raise ValueError("Invalid candidate id")
        if not isinstance(instructions, str) or not isinstance(manifest, dict):
            raise ValueError("Provide text instructions and an object manifest")
        canonical_json(manifest)
        with self.lock:
            plan, journal, config = self._load()
            self._search(journal)
            source = _absolute(source_dir)
            _disjoint(source, self.root)
            _disjoint(source, Path(plan["feedback_dir"]))
            _disjoint(source, Path(plan["publication_staging_dir"]))
            files = _inventory(source, config)
            if not files:
                raise ValueError("Candidate source tree is empty")
            payload = {
                "candidate_id": candidate_id,
                "files": files,
                "instructions": instructions,
                "manifest": manifest,
            }
            if operation_id in journal["operations"]:
                return self._repeat(
                    plan, journal, config, operation_id, "register_candidate", payload
                )
            if candidate_id in journal["candidates"]:
                raise ValueError("Candidate ids cannot be reused")
            baseline = journal["candidates"].get(config.baseline_candidate_id)
            if candidate_id != config.baseline_candidate_id and not self._baseline_ready(
                baseline, config
            ):
                raise ValueError("Independently evaluate the baseline before admitting candidates")
            self._reserve(journal, operation_id, "register_candidate", payload)
            target = self.root / "candidates" / candidate_id / "bundle"
            journal["candidates"][candidate_id] = {
                "status": "archiving",
                "operation_id": operation_id,
            }
            _save(self.root / "journal.json", journal)
            _copy(source, target / "source", files, config)
            _write_file(target / "instructions.md", instructions.encode("utf-8"))
            _save(target / "manifest.json", manifest)
            inventory = _inventory(target, config)
            result = {"candidate_id": candidate_id, "candidate_sha256": _fingerprint(inventory)}
            return self._prepare_publication(
                plan,
                journal,
                config,
                candidate_id=candidate_id,
                key="bundle",
                inventory=inventory,
                updates={"status": "registered"},
                result=result,
                operation_id=operation_id,
            )

    @staticmethod
    def _baseline_ready(baseline: dict | None, config: ArchiveConfig) -> bool:
        if baseline is None or baseline.get("status") != "evaluated":
            return False
        summary = baseline["summary"]
        if summary["quality"] is None or summary["total_tokens"] is None:
            return False
        return config.execution == "fixture" or baseline["execution_verified"] is True

    def record_development(
        self,
        candidate_id: str,
        artifacts_dir: Path,
        *,
        evaluator: Callable[[EvaluationInput], DevelopmentEvidence],
        operation_id: str,
    ) -> dict:
        """Evaluate the frozen copy once and retain full artifacts even if scoring fails.

        The callback is host-owned and read-only. A reported evaluator fingerprint
        binds provenance, but cannot authenticate arbitrary Python callbacks.
        """
        with self.lock:
            plan, journal, config = self._load()
            self._search(journal)
            candidate = journal["candidates"].get(candidate_id)
            if candidate is None:
                raise ValueError("Unknown candidate")
            source = _absolute(artifacts_dir)
            _disjoint(source, self.root)
            _disjoint(source, Path(plan["feedback_dir"]))
            _disjoint(source, Path(plan["publication_staging_dir"]))
            files = _inventory(source, config)
            payload = {
                "candidate_id": candidate_id,
                "files": files,
                "candidate_sha256": candidate["bundle"]["sha256"],
            }
            if operation_id in journal["operations"]:
                return self._repeat(
                    plan, journal, config, operation_id, "record_development", payload
                )
            if candidate["status"] != "registered":
                raise ValueError("A candidate development evaluation may only run once")
            self._reserve(journal, operation_id, "record_development", payload)
            target = self.root / "candidates" / candidate_id / "development"
            candidate["status"] = "evaluating"
            candidate["evaluation_operation_id"] = operation_id
            candidate["development_input_files"] = files
            _save(self.root / "journal.json", journal)
            _copy(source, target / "artifacts", files, config)
            binding = {
                "candidate_id": candidate_id,
                "candidate_sha256": candidate["bundle"]["sha256"],
                "artifacts_sha256": _fingerprint(files),
                "execution": config.execution,
                **{
                    key: plan[key]
                    for key in ("controls_sha256", "evaluator_sha256", "benchmark_sha256")
                },
            }
            context = EvaluationInput(
                artifacts_dir=target / "artifacts",
                candidate_dir=self.root / "candidates" / candidate_id / "bundle",
                benchmark_path=self.root / plan["benchmark_path"],
                case_ids=tuple(plan["case_ids"]),
                binding=binding.copy(),
                inventory=files.copy(),
            )
            try:
                returned = evaluator(context)
                if not isinstance(returned, DevelopmentEvidence):
                    raise ValueError("Host evaluator must return typed DevelopmentEvidence")
                evidence = DevelopmentEvidence.model_validate(returned.model_dump(mode="json"))
                for key, expected in binding.items():
                    if getattr(evidence, key) != expected:
                        raise ValueError(f"Development evidence binding mismatch: {key}")
                ids = [case.case_id for case in evidence.cases]
                if len(ids) != len(set(ids)) or set(ids) != set(plan["case_ids"]):
                    raise ValueError("Evidence must include every development case exactly once")
                for case in evidence.cases:
                    if any(_relative(name) not in files for name in case.evidence_files):
                        raise ValueError("Case evidence references an unarchived artifact")
                if _inventory(target / "artifacts", config) != files:
                    raise ValueError("Evaluator changed frozen artifacts")
                _save(target / "evidence.json", evidence.model_dump(mode="json"))
                quality = [case.quality for case in evidence.cases]
                tokens = [case.total_tokens for case in evidence.cases]
                summary = {
                    "cases": len(evidence.cases),
                    "quality": sum(quality) / len(quality) if None not in quality else None,
                    "total_tokens": sum(tokens) if None not in tokens else None,
                    "known_token_subtotal": sum(value for value in tokens if value is not None),
                    "unknown_token_cases": sum(value is None for value in tokens),
                    "failed_cases": sum(
                        case.status in {"invalid", "execution_failed", "artifact_error"}
                        for case in evidence.cases
                    ),
                    "unmeasured_cases": sum(case.status == "unmeasured" for case in evidence.cases),
                }
                outcome = dict(
                    status="evaluated",
                    summary=summary,
                    execution_verified=evidence.execution_verified,
                )
            except Exception as exc:
                outcome = dict(status="evaluation_failed", error=f"{type(exc).__name__}: {exc}")
                _save(
                    target / "evaluation-error.json",
                    {"error": outcome["error"], "binding": binding},
                )
            inventory = _inventory(target, config)
            result = {
                "candidate_id": candidate_id,
                "status": outcome["status"],
                "development_sha256": _fingerprint(inventory),
            }
            return self._prepare_publication(
                plan,
                journal,
                config,
                candidate_id=candidate_id,
                key="development",
                inventory=inventory,
                updates=outcome,
                result=result,
                operation_id=operation_id,
            )

    def recover_development(self, candidate_id: str, *, operation_id: str, reason: str) -> dict:
        """Archive an explicitly stopped evaluator's available files without rerunning it.

        The host must first establish that the worker has stopped. The file lock
        prevents overlapping archive callbacks; this does not cancel external jobs.
        Missing files from an interrupted copy are recorded, never called complete.
        """
        if not reason:
            raise ValueError("Record why the evaluator is known to have stopped")
        with self.lock:
            plan, journal, config = self._load()
            self._search(journal)
            candidate = journal["candidates"].get(candidate_id)
            if candidate is None or candidate.get("evaluation_operation_id") != operation_id:
                raise ValueError("Unknown development evaluation operation")
            if "publication" in journal["operations"][operation_id]:
                if journal["operations"][operation_id]["status"] == "completed":
                    return journal["operations"][operation_id]["result"]
                return self._finish_publication(plan, journal, config, operation_id)
            if (
                candidate.get("status") == "evaluation_failed"
                and journal["operations"][operation_id]["status"] == "completed"
            ):
                return journal["operations"][operation_id]["result"]
            if (
                candidate["status"] != "evaluating"
                or journal["operations"][operation_id]["status"] != "pending"
            ):
                raise ValueError("Development evaluation is not unresolved")
            target = self.root / "candidates" / candidate_id / "development"
            (target / "artifacts").mkdir(parents=True, exist_ok=True)
            available = _inventory(target / "artifacts", config)
            expected = candidate["development_input_files"]
            if any(expected.get(name) != value for name, value in available.items()):
                raise ValueError("Interrupted development artifacts changed")
            _save(
                target / "evaluation-error.json",
                {
                    "error": reason,
                    "status": "interrupted",
                    "missing_artifacts": sorted(set(expected) - set(available)),
                    "expected_inventory_sha256": _fingerprint(expected),
                },
            )
            inventory = _inventory(target, config)
            return self._prepare_publication(
                plan,
                journal,
                config,
                candidate_id=candidate_id,
                key="development",
                inventory=inventory,
                updates={"status": "evaluation_failed", "error": reason},
                operation_id=operation_id,
                result={
                    "candidate_id": candidate_id,
                    "status": "evaluation_failed",
                    "development_sha256": _fingerprint(inventory),
                },
            )

    def feedback_path(self) -> Path:
        """Return development-only filesystem feedback while search is open."""
        with self.lock:
            plan, journal, _ = self._load()
            self._search(journal)
            if any(
                operation["status"] == "pending" and "publication" in operation
                for operation in journal["operations"].values()
            ):
                raise ValueError("Recover pending publication before exposing filesystem feedback")
            return Path(plan["feedback_dir"])

    def select(self, *, operation_id: str) -> dict:
        """Freeze the measured quality/token Pareto frontier and close search.

        Equal points remain on the frontier. Missing quality or cost is reported
        as unrankable, never silently converted to zero or used to dominate peers.
        """
        with self.lock:
            _, journal, config = self._load()
            if journal["phase"] != "search":
                old = journal["operations"].get(operation_id)
                if old and old["kind"] == "select" and old["status"] == "completed":
                    return old["result"]
                if (
                    journal["phase"] == "selecting"
                    and old
                    and old["kind"] == "select"
                    and old["status"] == "pending"
                ):
                    journal["phase"] = "selected"
                    return self._complete(journal, operation_id, journal["selection"])
                self._search(journal)
            if any(op["status"] == "pending" for op in journal["operations"].values()):
                raise ValueError("Resolve all pending operations before selection")
            if not self._baseline_ready(
                journal["candidates"].get(config.baseline_candidate_id), config
            ):
                raise ValueError("Selection requires independently evaluated baseline evidence")
            ranked, excluded = {}, {}
            for candidate_id, candidate in journal["candidates"].items():
                if candidate["status"] != "evaluated":
                    excluded[candidate_id] = candidate["status"]
                elif (
                    config.execution == "model" and candidate.get("execution_verified") is not True
                ):
                    excluded[candidate_id] = "unverified_model_execution"
                elif (
                    candidate["summary"]["quality"] is None
                    or candidate["summary"]["total_tokens"] is None
                ):
                    excluded[candidate_id] = "unknown_objective"
                else:
                    ranked[candidate_id] = candidate["summary"]
            frontier = []
            for candidate_id, score in ranked.items():
                if not any(
                    other["quality"] >= score["quality"]
                    and other["total_tokens"] <= score["total_tokens"]
                    and (
                        other["quality"] > score["quality"]
                        or other["total_tokens"] < score["total_tokens"]
                    )
                    for other in ranked.values()
                ):
                    frontier.append(candidate_id)
            result = {
                "schema_version": 1,
                "split": "development",
                "execution": config.execution,
                "objectives": ["maximize_macro_quality", "minimize_total_tokens"],
                "candidate_ids": sorted(frontier),
                "excluded": excluded,
                "ranked": ranked,
                "evidence": {
                    key: candidate["development"]["sha256"]
                    for key, candidate in journal["candidates"].items()
                    if "development" in candidate
                },
                "limitations": [
                    "Fixture selection is lifecycle testing, not measured model optimization.",
                    "Only supplied independent development evidence determines this frontier.",
                ],
            }
            journal.update(phase="selecting", selection=result)
            self._reserve(journal, operation_id, "select", {})
            journal["phase"] = "selected"
            return self._complete(journal, operation_id, result)

    def begin_final(self, private_dir: Path, *, operation_id: str) -> dict:
        """Reserve one private final attempt before mkdir; never launch or replay it."""
        with self.lock:
            plan, journal, _ = self._load()
            private = _absolute(private_dir)
            _disjoint(private, self.root)
            _disjoint(private, Path(plan["feedback_dir"]))
            _disjoint(private, Path(plan["publication_staging_dir"]))
            if journal["final"] is not None:
                final = journal["final"]
                if final["operation_id"] != operation_id or final["private_dir"] != str(private):
                    raise ValueError("The final attempt is already bound; search cannot reopen")
                if journal["operations"][operation_id]["status"] == "pending":
                    return self._complete(journal, operation_id, final)
                return final
            if journal["phase"] != "selected":
                raise ValueError("Freeze development selection before final evaluation")
            if private.exists():
                raise ValueError("Final evaluation needs a new separate private directory")
            if operation_id in journal["operations"]:
                raise ValueError("Final operation id is already used")
            final = {
                "operation_id": operation_id,
                "private_dir": str(private),
                "status": "started",
                "selection_sha256": digest(canonical_json(journal["selection"])),
            }
            journal.update(phase="final_started", final=final)
            self._reserve(journal, operation_id, "begin_final", {"private_dir": str(private)})
            self._complete(journal, operation_id, final)
            # A failure from this point leaves search permanently closed. Reopening
            # returns the attempt identity; it does not retry evaluator execution.
            private.mkdir(parents=True, exist_ok=False)
            _save(private / "selection.json", journal["selection"])
            return final

    def finish_final(
        self, *, operation_id: str, status: Literal["completed", "failed", "interrupted"]
    ) -> dict:
        """Seal available private results, including explicitly incomplete preparation.

        Failed/interrupted attempts may lack their original directory or selection
        file. Closing them records that absence; it never reconstructs test results
        or invokes evaluation. A seal intent makes the remaining writes recoverable.
        """
        if status not in {"completed", "failed", "interrupted"}:
            raise ValueError("Final evaluation requires an explicit terminal status")
        with self.lock:
            _, journal, config = self._load()
            final = journal["final"]
            if final is None or final["operation_id"] != operation_id:
                raise ValueError("Unknown final attempt")
            private = _absolute(Path(final["private_dir"]))
            if final["status"] != "started":
                if final["status"] != status:
                    raise ValueError("Final status is immutable")
                sealed = _read(private / "final-archive.json")
                files = _inventory(private, config)
                files.pop("final-archive.json", None)
                if (
                    files != sealed["files"]
                    or digest(canonical_json(sealed)) != final["archive_sha256"]
                ):
                    raise ValueError("Private final artifacts changed")
                return final
            if journal["phase"] != "final_started":
                raise ValueError("Final attempt is not active")
            files = _inventory(private, config) if private.exists() else {}
            if "seal_intent" not in final:
                missing = []
                if not private.exists():
                    missing.append("private_directory")
                if "selection.json" not in files:
                    missing.append("selection.json")
                elif (
                    digest(canonical_json(_read(private / "selection.json")))
                    != final["selection_sha256"]
                ):
                    raise ValueError("Final selection changed")
                if missing and status == "completed":
                    raise ValueError(
                        "Completed final evaluation requires original preparation evidence"
                    )
                files.pop("final-archive.json", None)
                final["seal_intent"] = {
                    "schema_version": 1,
                    "status": status,
                    "operation_id": operation_id,
                    "selection_sha256": final["selection_sha256"],
                    "files": files,
                    "missing_evidence": missing,
                }
                _save(self.root / "journal.json", journal)
            sealed = final["seal_intent"]
            files.pop("final-archive.json", None)
            if (
                sealed["status"] != status
                or sealed["operation_id"] != operation_id
                or sealed["selection_sha256"] != final["selection_sha256"]
                or files != sealed["files"]
            ):
                raise ValueError("Private evidence does not match the frozen final seal intent")
            private.mkdir(parents=True, exist_ok=True)
            sealed_path = private / "final-archive.json"
            if sealed_path.exists():
                if canonical_json(_read(sealed_path)) != canonical_json(sealed):
                    raise ValueError(
                        "Uncommitted final archive does not match operation/status/selection/files"
                    )
                # A prior archive write succeeded but its journal update failed.
                # Reconcile identical bytes only; never rerun final evaluation.
            else:
                _save(sealed_path, sealed)
            final.update(status=status, archive_sha256=digest(canonical_json(sealed)))
            journal["phase"] = f"final_{status}"
            _save(self.root / "journal.json", journal)
            return final
