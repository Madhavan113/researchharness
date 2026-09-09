"""Durable strategy state shared by direct, MCP and controlled gateway hooks.

Candidate code only executes in Docker. This trusted host journal serializes
events, preserves exact inputs and decisions, and never replays an uncertain run.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from filelock import FileLock

from research_harness.strategies.config import StrategyBundle
from research_harness.strategies.projection import (
    ALIASES,
    TOOLS,
    observation_payload,
    project_observation,
    validate_observation_decision,
)
from research_harness.strategies.sandbox import DockerStrategyRunner, _json_object
from research_harness.util import canonical_json, digest, timestamp, write_json


class StrategySessionError(RuntimeError):
    """A strategy failure is not a successful baseline execution."""


def _path(path: Path) -> Path:
    supplied = Path(path).expanduser().absolute()
    if ".." in supplied.parts or any(p.is_symlink() for p in (supplied, *supplied.parents)):
        raise ValueError("Strategy state paths must not traverse parents or links")
    return supplied.resolve()


def _read(path: Path) -> dict:
    return _json_object(_path(path).read_bytes())


def _save(path: Path, value: dict) -> None:
    write_json(_path(path), value)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _files(root: Path) -> dict[str, str]:
    result = {}
    for parent, dirs, files in os.walk(_path(root), followlinks=False):
        for name in dirs + files:
            path = Path(parent) / name
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Strategy evidence must contain regular unlinked files")
            if path == root / "record.json":
                continue
            result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return dict(sorted(result.items()))


def _execution(root: Path, bundle: StrategyBundle, event: dict) -> dict:
    """Check persisted execution bytes without executing strategy code."""
    output = root / "execution"
    report = _read(output / "execution.json")
    raw = _read(output / "decision.json")
    if (
        report.get("status") != "completed"
        or report.get("cleanup") not in {"removed", "absent"}
        or report.get("configuration") != bundle.config.sandbox.model_dump(mode="json")
        or report.get("source_sha256") != bundle.config.source_sha256
        or report.get("request_sha256") != digest(canonical_json(event))
        or report.get("output_truncated") is not False
        or report.get("output_collection_complete") is not True
    ):
        raise ValueError("Strategy execution did not complete under the frozen configuration")
    actual = {name: value for name, value in _files(output).items() if name != "execution.json"}
    if actual != report["artifact_hashes"]:
        raise ValueError("Strategy execution artifact bytes changed")
    if (
        digest((output / "input/strategy.py").read_bytes()) != bundle.config.source_sha256
        or (output / "input/request.json").read_bytes() != canonical_json(event).encode()
        or _json_object((output / "stdout.txt").read_bytes()) != raw
    ):
        raise ValueError("Strategy source/input/output binding changed")
    if set(raw) != {"decision", "state"} or not all(isinstance(v, dict) for v in raw.values()):
        raise ValueError("Candidate must return exactly decision and state objects")
    if len(canonical_json(raw["state"]).encode()) > bundle.config.max_state_bytes:
        raise ValueError("Strategy state exceeds the configured limit")
    return raw


def verify_event(directory: Path, bundle: StrategyBundle) -> dict:
    """Verify one completed event, without trusting its claimed decision alone."""
    root = _path(directory)
    record, event, result = (
        _read(root / name) for name in ("record.json", "input.json", "result.json")
    )
    if (
        record["status"] != "completed"
        or record["strategy_sha256"] != bundle.sha256
        or record["input_sha256"] != digest(canonical_json(event))
        or record["payload_sha256"] != digest(canonical_json(event["payload"]))
        or record["result_sha256"] != digest(canonical_json(result))
        or record["state_before_sha256"] != digest(canonical_json(event["state"]))
        or record["state_after_sha256"] != digest(canonical_json(result["state"]))
        or record["files"] != _files(root)
    ):
        raise ValueError("Immutable strategy event changed")
    raw = _execution(root, bundle, event)
    if raw["state"] != result["state"]:
        raise ValueError("Recorded strategy state differs from isolated execution")
    validated = _validate(bundle, event, raw["decision"])
    if validated != result["decision"]:
        raise ValueError("Recorded strategy decision differs from independent validation")
    return {
        "directory": str(root),
        "record": record,
        "record_sha256": digest((root / "record.json").read_bytes()),
        "input": event,
        "result": result,
        "files": record["files"],
        "strategy_config": bundle.config.model_dump(mode="json"),
    }


def _validate(bundle: StrategyBundle, event: dict, decision: dict) -> dict:
    if event["kind"] == "observation":
        return validate_observation_decision(
            event["payload"], decision, bundle.config.max_render_bytes
        )
    if event["kind"] == "context":
        from research_harness.strategies.context import validate_context_decision

        return validate_context_decision(event["payload"], decision)
    raise ValueError("Unknown strategy event kind")


class StrategySession:
    @classmethod
    def open(cls, root: Path) -> StrategySession:
        root = _path(root)
        journal = _read(root / "session.json")
        manifest = Path(journal["manifest"])
        if manifest.is_absolute() or ".." in manifest.parts:
            raise ValueError("Invalid frozen strategy manifest path")
        return cls(root, StrategyBundle.load(root / manifest))

    def __init__(self, root: Path, bundle: StrategyBundle):
        self.root = _path(root)
        self.lock = FileLock(
            str(self.root) + ".lock", timeout=bundle.config.sandbox.timeout_seconds + 90
        )
        self.bundle = bundle
        with self.lock:
            if not self.root.exists():
                self.root.mkdir(parents=True)
                frozen = bundle.freeze(self.root / "bundle")
                _save(
                    self.root / "session.json",
                    {
                        "schema_version": 1,
                        "strategy_sha256": frozen.sha256,
                        "manifest": frozen.manifest_path.relative_to(self.root).as_posix(),
                        "status": "ready",
                        "state": {},
                        "events": [],
                    },
                )
            self._load()

    def _load(self) -> dict:
        journal = _read(self.root / "session.json")
        manifest = journal["manifest"]
        if Path(manifest).is_absolute() or ".." in Path(manifest).parts:
            raise ValueError("Invalid frozen strategy manifest path")
        frozen = StrategyBundle.load(self.root / manifest)
        # Revalidate the caller's bundle too: changed bytes cannot silently reuse state.
        supplied = StrategyBundle.load(self.bundle.manifest_path)
        if journal["strategy_sha256"] != frozen.sha256 or frozen.sha256 != supplied.sha256:
            raise ValueError("Strategy session identity changed")
        self.bundle = frozen
        state, seen, pending = {}, set(), False
        for entry in journal["events"]:
            if entry["operation_id"] in seen or pending:
                raise ValueError("Invalid strategy event ordering")
            seen.add(entry["operation_id"])
            directory = self.root / "events" / entry["directory"]
            if directory.parent != self.root / "events" or not entry["directory"].startswith(
                "event-"
            ):
                raise ValueError("Invalid strategy event directory")
            if entry["status"] == "completed":
                proof = verify_event(directory, frozen)
                if (
                    proof["record_sha256"] != entry["record_sha256"]
                    or proof["input"]["state"] != state
                ):
                    raise ValueError("Strategy state chain changed")
                state = proof["result"]["state"]
            elif entry["status"] in {"pending", "failed"}:
                pending = True
            else:
                raise ValueError("Unknown strategy event status")
        if state != journal["state"]:
            raise ValueError("Strategy state no longer matches committed events")
        return journal

    def assert_ready(self) -> None:
        with self.lock:
            journal = self._load()
            if journal["status"] != "ready" or any(
                e["status"] != "completed" for e in journal["events"]
            ):
                raise StrategySessionError("Strategy session failed or requires explicit recovery")

    def status(self) -> dict:
        with self.lock:
            return deepcopy(self._load())

    def _commit(
        self, journal: dict, entry: dict, record: dict, event: dict, raw: dict, validated: dict
    ) -> dict:
        directory = self.root / "events" / entry["directory"]
        result = {"decision": validated, "state": raw["state"]}
        _save(directory / "result.json", result)
        record.update(
            status="completed",
            result_sha256=digest(canonical_json(result)),
            state_after_sha256=digest(canonical_json(raw["state"])),
            finished_at=timestamp(),
        )
        record["files"] = _files(directory)
        _save(directory / "record.json", record)
        entry.update(
            status="completed", record_sha256=digest((directory / "record.json").read_bytes())
        )
        journal.update(state=raw["state"], status="ready")
        _save(self.root / "session.json", journal)
        return deepcopy(validated)

    def apply(
        self, kind: str, payload: dict, *, operation_id: str, validate: Callable[[dict], dict]
    ) -> dict:
        if (
            kind not in {"observation", "context"}
            or not isinstance(operation_id, str)
            or not 1 <= len(operation_id) <= 512
        ):
            raise ValueError("Provide a supported strategy event kind and bounded operation id")
        if not isinstance(payload, dict):
            raise ValueError("Strategy payload must be an object")
        payload = _json_object(canonical_json(payload).encode())
        with self.lock:
            journal = self._load()
            previous = next(
                (e for e in journal["events"] if e["operation_id"] == operation_id), None
            )
            if previous:
                if previous["kind"] != kind or previous["payload_sha256"] != digest(
                    canonical_json(payload)
                ):
                    raise StrategySessionError(
                        "Strategy operation id was reused with different inputs"
                    )
                if previous["status"] == "completed":
                    return deepcopy(
                        verify_event(self.root / "events" / previous["directory"], self.bundle)[
                            "result"
                        ]["decision"]
                    )
                raise StrategySessionError(
                    "Strategy operation is unresolved; recover it explicitly without replay"
                )
            if journal["status"] != "ready" or any(
                e["status"] != "completed" for e in journal["events"]
            ):
                raise StrategySessionError("Strategy session failed or has an unresolved event")
            if len(journal["events"]) >= self.bundle.config.max_events:
                journal.update(status="failed", error="Strategy event budget exhausted")
                _save(self.root / "session.json", journal)
                raise StrategySessionError("Strategy event budget exhausted")
            enabled = (
                self.bundle.config.observations
                if kind == "observation"
                else self.bundle.config.context
            )
            if not enabled:
                raise StrategySessionError("This strategy event kind is disabled by the host")
            event = {
                "schema_version": 1,
                "kind": kind,
                "payload": payload,
                "state": deepcopy(journal["state"]),
            }
            if len(canonical_json(event).encode()) > self.bundle.config.sandbox.max_input_bytes:
                journal.update(
                    status="failed",
                    error="Strategy event input exceeds the configured limit",
                    rejected_input_sha256=digest(canonical_json(event)),
                )
                _save(self.root / "session.json", journal)
                raise StrategySessionError("Strategy event input exceeds the configured limit")
            entry = {
                "operation_id": operation_id,
                "kind": kind,
                "status": "pending",
                "payload_sha256": digest(canonical_json(payload)),
                "directory": f"event-{len(journal['events']) + 1:04d}-{digest(operation_id)[:16]}",
            }
            journal["events"].append(entry)
            _save(self.root / "session.json", journal)
            directory = self.root / "events" / entry["directory"]
            directory.mkdir(parents=True, exist_ok=False)
            record = {
                **entry,
                "schema_version": 1,
                "strategy_sha256": self.bundle.sha256,
                "source_sha256": self.bundle.config.source_sha256,
                "input_sha256": digest(canonical_json(event)),
                "state_before_sha256": digest(canonical_json(event["state"])),
                "started_at": timestamp(),
            }
            _save(directory / "input.json", event)
            _save(directory / "record.json", record)
            try:
                DockerStrategyRunner(self.bundle.config.sandbox).execute(
                    self.bundle.source, event, directory / "execution"
                )
                raw = _execution(directory, self.bundle, event)
                validated = validate(deepcopy(raw["decision"]))
                if validated != _validate(self.bundle, event, raw["decision"]):
                    raise ValueError("Strategy validator differs from the fixed host contract")
            except BaseException as exc:
                # Do not mask the primary error if a disk failure also prevents its report.
                try:
                    record.update(
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                        finished_at=timestamp(),
                    )
                    record["files"] = _files(directory)
                    _save(directory / "record.json", record)
                    entry["status"] = "failed"
                    journal["status"] = "failed"
                    _save(self.root / "session.json", journal)
                except Exception:
                    pass
                if not isinstance(exc, Exception):
                    raise
                raise StrategySessionError(f"Strategy execution failed: {exc}") from exc
            # Commit failures leave a pending operation and its complete execution evidence.
            return self._commit(journal, entry, record, event, raw, validated)

    def event_record(self, operation_id: str) -> dict:
        with self.lock:
            journal = self._load()
            entry = next((e for e in journal["events"] if e["operation_id"] == operation_id), None)
            if entry is None or entry["status"] != "completed":
                raise StrategySessionError("No completed strategy event for this operation")
            return verify_event(self.root / "events" / entry["directory"], self.bundle)

    def project(self, tool: str, result: dict) -> dict:
        tool = ALIASES.get(tool, tool)
        if not self.bundle.config.observations or tool not in TOOLS:
            return deepcopy(result)
        payload = observation_payload(tool, result)
        operation_id = f"observation:{result['discovery_id']}:{result['operation_id']}"
        decision = self.apply(
            "observation",
            payload,
            operation_id=operation_id,
            validate=lambda value: validate_observation_decision(
                payload, value, self.bundle.config.max_render_bytes
            ),
        )
        return project_observation(payload, decision, self.bundle.sha256)

    def recover(self) -> dict:
        """Reconcile a stopped event or clean its container; never rerun candidate code."""
        with self.lock.acquire(timeout=0):
            journal = self._load()
            pending = next((e for e in journal["events"] if e["status"] != "completed"), None)
            if pending is None:
                return {"status": journal["status"], "replayed": False}
            directory = self.root / "events" / pending["directory"]
            output = directory / "execution"
            record = (
                _read(directory / "record.json")
                if (directory / "record.json").exists()
                else {**pending}
            )
            if pending["status"] == "failed":
                if (output / "execution.json").exists():
                    DockerStrategyRunner(self.bundle.config.sandbox).recover(output)
                return {
                    "status": "failed",
                    "replayed": False,
                    "operation_id": pending["operation_id"],
                }
            try:
                event = _read(directory / "input.json")
                if (output / "execution.json").exists():
                    DockerStrategyRunner(self.bundle.config.sandbox).recover(output)
                raw = _execution(directory, self.bundle, event)
                validated = _validate(self.bundle, event, raw["decision"])
            except Exception as exc:
                directory.mkdir(parents=True, exist_ok=True)
                record.update(
                    status="failed",
                    error=f"Recovery: {type(exc).__name__}: {exc}",
                    finished_at=timestamp(),
                )
                record["files"] = _files(directory)
                _save(directory / "record.json", record)
                pending["status"] = "failed"
                journal["status"] = "failed"
                _save(self.root / "session.json", journal)
                return {
                    "status": "failed",
                    "replayed": False,
                    "operation_id": pending["operation_id"],
                }
            self._commit(journal, pending, record, event, raw, validated)
            return {"status": "ready", "replayed": False, "operation_id": pending["operation_id"]}
