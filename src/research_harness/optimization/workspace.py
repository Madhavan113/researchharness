"""Revocable proposer tools over complete feedback and a disposable Python sandbox.

Generated Python is never imported by the host. Each run receives read-only feedback
and a read-only seed, edits a bounded container tmpfs, and publishes validated file
bytes only after successful execution and confirmed container removal. Failed or
aborted runs discard edits; all available raw execution output remains in tools/.
This is a trusted-host/Docker boundary, not protection against a compromised daemon.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import os
import shutil
import stat
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from filelock import FileLock
from pydantic import ConfigDict, Field, StrictInt, model_validator

from research_harness.config import StrictModel
from research_harness.strategies.sandbox import DockerStrategyRunner, SandboxConfig, _json_object
from research_harness.util import atomic_write, canonical_json, digest, timestamp, write_json
from research_harness.verification import VerificationMemo


class WorkspaceConfig(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
    sandbox: SandboxConfig
    max_files: StrictInt = Field(default=128, ge=2, le=2048)
    max_file_bytes: StrictInt = Field(default=262144, ge=1024, le=4 * 1024 * 1024)
    max_workspace_bytes: StrictInt = Field(default=262144, ge=1024, le=4 * 1024 * 1024)
    max_feedback_files: StrictInt = Field(default=100000, ge=1, le=1000000)
    max_feedback_bytes: StrictInt = Field(default=1024 * 1024 * 1024, ge=1024, le=16 * 1024**3)
    max_path_chars: StrictInt = Field(default=512, ge=64, le=1024)
    max_calls: StrictInt = Field(default=256, ge=1, le=10000)
    max_tool_output_bytes: StrictInt = Field(default=65536, ge=2048, le=1024 * 1024)
    max_stream_bytes: StrictInt = Field(default=16384, ge=256, le=262144)

    @model_validator(mode="after")
    def protocol_fits(self):
        # Conservative JSON/base64 bound, independent of generated program claims.
        bound = (
            (self.max_workspace_bytes + 2 * self.max_stream_bytes) * 4 // 3
            + self.max_files * (self.max_path_chars * 6 + 32)
            + 4096
        )
        if bound > self.sandbox.max_output_bytes:
            raise ValueError("Sandbox output limit cannot hold the bounded workspace protocol")
        if self.max_file_bytes > self.max_workspace_bytes:
            raise ValueError("Per-file limit cannot exceed the workspace limit")
        if self.max_stream_bytes * 8 // 3 + 2048 > self.max_tool_output_bytes:
            raise ValueError("Tool output limit cannot hold both bounded base64 streams")
        if self.max_path_chars * 6 + 1024 > self.max_tool_output_bytes:
            raise ValueError("Tool output limit cannot hold one maximum-length file entry")
        return self


# Executed only by the restricted container's existing standalone worker. Even this
# wrapper's returned metadata is untrusted; the host rechecks all imported bytes.
_WRAPPER = r"""
import base64, os, pathlib, signal, stat, subprocess, sys, threading, time

def apply(event):
    root = pathlib.Path('/workspace')
    for rel in event['seed_files']:
        source = pathlib.Path('/seed') / rel
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    command = ['/usr/local/bin/python3', '-I', '-B', '/seed/' + event['script'], *event['arguments']]
    child = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8'},
                             start_new_session=True)
    streams = {'stdout': bytearray(), 'stderr': bytearray()}
    overflow = threading.Event()
    errors = []
    def collect(name, pipe):
        try:
            while True:
                chunk = os.read(pipe.fileno(), 4096)
                if not chunk:
                    break
                remaining = event['max_stream_bytes'] - len(streams[name])
                saved = chunk[:max(0, remaining)]
                streams[name].extend(saved)
                # Retains available output even if wrapper is subsequently terminated.
                os.write(2, saved)
                if len(chunk) > remaining:
                    overflow.set()
                    break
        except BaseException as exc:
            errors.append(type(exc).__name__)
    readers = [threading.Thread(target=collect, args=(n, p), daemon=True)
               for n, p in [('stdout', child.stdout), ('stderr', child.stderr)]]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + event['child_timeout_seconds']
    timed_out = False
    while child.poll() is None:
        if overflow.is_set() or errors or time.monotonic() >= deadline:
            timed_out = time.monotonic() >= deadline
            break
        time.sleep(0.01)
    # Kill the entire group, including background descendants after a normal exit.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=2)
    for reader in readers:
        reader.join(1)
    complete = not any(t.is_alive() for t in readers) and not errors and not overflow.is_set()
    result = {'status': 'completed' if child.returncode == 0 and complete and not timed_out else 'failed',
              'exit_code': child.returncode, 'timed_out': timed_out,
              'output_complete': complete, 'output_truncated': overflow.is_set(),
              'stdout_base64': base64.b64encode(streams['stdout']).decode(),
              'stderr_base64': base64.b64encode(streams['stderr']).decode(), 'files': {}}
    if result['status'] != 'completed':
        return result
    total = 0
    entries = 0
    try:
        for parent, dirs, names in os.walk(root, followlinks=False):
            entries += len(dirs) + len(names)
            if entries > event['max_entries']:
                raise ValueError('workspace entry limit exceeded')
            for name in dirs + names:
                path = pathlib.Path(parent) / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise ValueError('workspace contains a link or special file')
                rel = path.relative_to(root).as_posix()
                if len(rel) > event['max_path_chars']:
                    raise ValueError('workspace path limit exceeded')
                if stat.S_ISDIR(info.st_mode):
                    continue
                if info.st_nlink != 1 or info.st_size > event['max_file_bytes']:
                    raise ValueError('workspace file is linked or oversized')
                raw = path.read_bytes()
                total += len(raw)
                if total > event['max_workspace_bytes'] or len(result['files']) >= event['max_files']:
                    raise ValueError('workspace size or file limit exceeded')
                result['files'][rel] = base64.b64encode(raw).decode()
    except BaseException as exc:
        result.update(status='failed', files={}, file_error=str(exc))
    return result
"""


def _relative(value: str, limit: int, *, directory: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("Invalid virtual path")
    value = value.removesuffix("/") if directory else value
    parts = value.split("/")
    if (
        not value
        or any(part in ("", ".", "..") for part in parts)
        or "\\" in value
        or not value.isprintable()
        or PurePosixPath(value).is_absolute()
    ):
        raise ValueError("Use a relative virtual path without traversal or links")
    return value


def _regular(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError("Files must be bounded regular files without links")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("File exceeds its byte limit")
    return raw


def _stat_identity(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns)


def _inventory(
    root: Path,
    config: WorkspaceConfig,
    *,
    feedback: bool = False,
    memo: VerificationMemo | None = None,
) -> dict:
    if memo is not None:
        return memo.verify(
            ("workspace-inventory", feedback, config.model_dump_json()),
            [root],
            lambda: _inventory(root, config, feedback=feedback),
        )
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Workspace roots must be existing unlinked directories")
    maximum = config.max_feedback_files if feedback else config.max_files
    byte_limit = config.max_feedback_bytes if feedback else config.max_workspace_bytes
    device = root.stat().st_dev
    result, total, entries = {}, 0, 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        entries += len(dirs) + len(files)
        if entries > maximum * 8:
            raise ValueError("Directory entry limit exceeded")
        for name in dirs + files:
            path = Path(parent) / name
            rel = _relative(path.relative_to(root).as_posix(), config.max_path_chars)
            info = path.lstat()
            if info.st_dev != device or stat.S_ISLNK(info.st_mode):
                raise ValueError("Links and nested mounts are forbidden")
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Only regular, unlinked files are allowed")
            total += info.st_size
            if total > byte_limit or len(result) >= maximum:
                raise ValueError("Tree exceeds the configured file/byte limits")
            if not feedback and info.st_size > config.max_file_bytes:
                raise ValueError("Workspace file exceeds its byte limit")
            hasher = hashlib.sha256()
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as handle:
                current = os.fstat(handle.fileno())
                if _stat_identity(current) != _stat_identity(info):
                    raise ValueError("File changed while inventorying")
                consumed = 0
                while chunk := handle.read(min(65536, byte_limit - consumed + 1)):
                    consumed += len(chunk)
                    if consumed > info.st_size:
                        raise ValueError("File changed while inventorying")
                    hasher.update(chunk)
                if consumed != info.st_size or _stat_identity(
                    os.fstat(handle.fileno())
                ) != _stat_identity(info):
                    raise ValueError("File changed while inventorying")
            result[rel] = {"sha256": hasher.hexdigest(), "bytes": info.st_size}
    return dict(sorted(result.items()))


def _hashes(inventory: dict) -> dict:
    return {name: row["sha256"] for name, row in inventory.items()}


class _WorkspaceRunner(DockerStrategyRunner):
    def __init__(self, config: WorkspaceConfig, feedback: Path, seed: Path):
        super().__init__(config.sandbox)
        self.workspace_config, self.feedback, self.seed = config, feedback, seed
        # Page rounding leaves room for directory metadata but imposes a hard bound.
        size = max(1024 * 1024, config.max_workspace_bytes * 2 + config.max_files * 8192)
        self.workspace_tmpfs = (
            f"rw,nosuid,nodev,noexec,size={size},nr_inodes={config.max_files * 8 + 32},mode=1777"
        )

    def _create_arguments(self, source: Path, name: str, identity: str) -> list[str]:
        result = super()._create_arguments(source, name, identity)
        additions = [f"--tmpfs=/workspace:{self.workspace_tmpfs}"]
        for path, destination in ((self.feedback, "/feedback"), (self.seed, "/seed")):
            if any(char in str(path) for char in (",", "\n", "\r")):
                raise ValueError("Mount paths cannot contain Docker field delimiters")
            additions += ["--mount", f"type=bind,source={path},target={destination},readonly"]
        index = result.index(self.config.image)
        return result[:index] + additions + result[index:]

    def _check_container(self, metadata: dict, inputs: Path, identity: str, image: dict) -> None:
        checked = copy.deepcopy(metadata)
        mounts = checked["Mounts"]
        if len(mounts) != 3:
            raise ValueError("Workspace requires exactly three read-only bind mounts")
        for path, destination in ((self.feedback, "/feedback"), (self.seed, "/seed")):
            matches = [row for row in mounts if row.get("Destination") == destination]
            if len(matches) != 1 or any(
                matches[0].get(key) != value
                for key, value in {"Type": "bind", "Source": str(path), "RW": False}.items()
            ):
                raise ValueError("Workspace bind mount differs from the required read-only tree")
            mounts.remove(matches[0])
        tmpfs = checked["HostConfig"]["Tmpfs"]
        if tmpfs.pop("/workspace", None) != self.workspace_tmpfs:
            raise ValueError("Workspace tmpfs resource limits were not applied")
        super()._check_container(checked, inputs, identity, image)


class ProposalWorkspace:
    """One non-replayable tool session. ``close`` permanently revokes all tools.

    ``snapshot_files`` is a trusted-host read-only operation available after a
    quiescent close. Recovery terminates recorded owned containers and closes the
    session; it never executes a pending tool or publishes interrupted script edits.
    """

    def __init__(self, feedback: Path, output: Path, config: WorkspaceConfig):
        self.config = WorkspaceConfig.model_validate(config)
        self._verification = VerificationMemo()
        if Path(feedback).is_symlink() or Path(output).is_symlink():
            raise ValueError("Workspace and feedback roots cannot be symlinks")
        self.feedback_source = Path(feedback).expanduser().resolve()
        self.output = Path(output).expanduser().resolve()
        self.feedback = self.feedback_source
        if (
            self.feedback == self.output
            or self.feedback in self.output.parents
            or self.output in self.feedback.parents
        ):
            raise ValueError("Feedback and workspace outputs must be disjoint trees")
        inventory = _inventory(self.feedback, self.config, feedback=True)
        self.output.mkdir(parents=True, exist_ok=False)
        self.workspace = self.output / "workspace"
        self.workspace.mkdir()
        (self.output / "tools").mkdir()
        self._lock = FileLock(str(self.output / "owner.lock"), timeout=0)
        identity = uuid4().hex
        readable = True
        for parent, _dirs, files in os.walk(self.feedback_source):
            readable = readable and Path(parent).stat().st_mode & 0o005 == 0o005
            readable = readable and all(
                (Path(parent) / name).stat().st_mode & 0o004 for name in files
            )
        view_parent = Path(tempfile.gettempdir()).resolve()
        if not readable:
            self.feedback = view_parent / f".rh-proposer-feedback-{identity}" / "feedback"
        self._state = {
            "schema_version": 1,
            "workspace_id": identity,
            "status": "preparing",
            "preparation_complete": False,
            "configuration": config.model_dump(mode="json"),
            "feedback": str(self.feedback),
            "feedback_source": str(self.feedback_source),
            "feedback_view_owned": not readable,
            "feedback_view_parent": str(view_parent),
            "feedback_view_layout": "source" if readable else "private-parent-v1",
            "feedback_view_removed": False,
            "feedback_inventory": inventory,
            "workspace_inventory": {},
            "calls": [],
            "created_at": timestamp(),
        }
        self._save()
        if not readable:
            # Record ownership before creation, and restrict host traversal before
            # copying any bytes. Only the readable child is mounted into Docker.
            self.feedback.parent.mkdir(mode=0o700)
            self.feedback.parent.chmod(0o700)
            self.feedback.mkdir(mode=0o755)
            for name, row in inventory.items():
                target = self.feedback / name
                target.parent.mkdir(parents=True, exist_ok=True)
                # No original permissions change and no recursive embedding in the
                # proposer archive: the parent retains the complete original snapshot.
                descriptor = os.open(
                    self.feedback_source / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                )
                with os.fdopen(descriptor, "rb") as source, target.open("xb") as dest:
                    info = os.fstat(source.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or info.st_size != row["bytes"]
                    ):
                        raise ValueError("Feedback changed before copying")
                    remaining = row["bytes"]
                    hasher = hashlib.sha256()
                    while chunk := source.read(min(65536, remaining + 1)):
                        if len(chunk) > remaining:
                            raise ValueError("Feedback grew while copying")
                        dest.write(chunk)
                        hasher.update(chunk)
                        remaining -= len(chunk)
                    if remaining or hasher.hexdigest() != row["sha256"]:
                        raise ValueError("Feedback changed while copying")
                target.chmod(0o444)
            for parent, _dirs, _files in os.walk(self.feedback):
                Path(parent).chmod(0o555)
        self._check_trees()
        self._state.update(status="ready", preparation_complete=True)
        self._save()

    @property
    def feedback_inventory(self) -> dict:
        return _hashes(self._state["feedback_inventory"])

    def _save(self) -> None:
        write_json(self.output / "workspace.json", self._state)

    def _load(self) -> None:
        value = _json_object(_regular(self.output / "workspace.json", 64 * 1024 * 1024))
        if value["configuration"] != self.config.model_dump(mode="json"):
            raise ValueError("Workspace configuration changed")
        if value["workspace_id"] != self._state["workspace_id"]:
            raise ValueError("Workspace identity changed")
        self._state = value

    def _check_trees(self, *, fresh: bool = False) -> dict:
        memo = None if fresh else self._verification
        if self._state["feedback_view_owned"] and not self._state["feedback_view_removed"]:
            root, private = self._owned_feedback_root()
            if private and stat.S_IMODE(root.stat().st_mode) != 0o700:
                raise ValueError("Temporary feedback parent must remain private (0700)")
        if _inventory(self.feedback_source, self.config, feedback=True, memo=memo) != self._state[
            "feedback_inventory"
        ] or (
            not self._state["feedback_view_removed"]
            and _inventory(self.feedback, self.config, feedback=True, memo=memo)
            != self._state["feedback_inventory"]
        ):
            raise ValueError("Feedback changed after workspace binding")
        current = _inventory(self.workspace, self.config, memo=memo)
        if current != self._state["workspace_inventory"]:
            raise ValueError("Workspace changed outside a completed tool operation")
        return current

    def _virtual(self, value: str, *, directory: bool = False) -> tuple[str, Path]:
        value = _relative(value, self.config.max_path_chars + 10, directory=directory)
        root, _, relative = value.partition("/")
        if root not in ("feedback", "workspace") or (not relative and not directory):
            raise ValueError("Paths must begin with feedback/ or workspace/")
        if relative:
            _relative(relative, self.config.max_path_chars)
        base = self.feedback if root == "feedback" else self.workspace
        path = base / relative
        for ancestor in (path, *path.parents):
            if ancestor == base.parent:
                break
            if ancestor.is_symlink():
                raise ValueError("Symlink paths are forbidden")
        return root, path

    @staticmethod
    def tool_schemas() -> list[dict]:
        integer = {"type": "integer", "minimum": 0}
        string = {"type": "string"}
        specs = [
            (
                "list_files",
                "List complete file inventories under feedback/ or workspace/, with pagination.",
                {"path": string, "offset": integer, "limit": {"type": "integer", "minimum": 1}},
            ),
            (
                "read_file",
                "Read bytes from feedback/ or workspace/. Base64 pages can reconstruct the complete file.",
                {"path": string, "offset": integer, "limit": {"type": "integer", "minimum": 1}},
            ),
            (
                "write_file",
                "Create or replace a workspace/ file. feedback/ is read-only.",
                {
                    "path": string,
                    "content": string,
                    "encoding": {"type": "string", "enum": ["utf-8", "base64"]},
                },
            ),
            (
                "run_python",
                "Execute a workspace/ Python file in disposable network-disabled Docker. Only successful validated runs persist edits. Feedback is /feedback; writable files are /workspace.",
                {"path": string, "arguments": {"type": "array", "items": string, "maxItems": 64}},
            ),
        ]
        return [
            {
                "type": "function",
                "name": name,
                "description": description,
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            }
            for name, description, properties in specs
        ]

    def _arguments(self, name: str, arguments: dict) -> dict:
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        defaults = {
            "list_files": {"offset": 0, "limit": 100},
            "read_file": {"offset": 0, "limit": 65536},
            "write_file": {"encoding": "utf-8"},
            "run_python": {"arguments": []},
        }
        schemas = {row["name"]: row["parameters"]["properties"] for row in self.tool_schemas()}
        if name not in schemas:
            raise ValueError("Unknown workspace tool")
        result = {**defaults[name], **arguments}
        if set(result) != set(schemas[name]):
            raise ValueError("Tool arguments contain missing or unexpected fields")
        for key in ("path", "content", "encoding"):
            if key in result and not isinstance(result[key], str):
                raise ValueError("Text tool arguments must be strings")
        for key in ("offset", "limit"):
            if key in result and (
                type(result[key]) is not int or result[key] < (1 if key == "limit" else 0)
            ):
                raise ValueError("Pagination must use bounded nonnegative integers")
        if name == "run_python" and (
            not isinstance(result["arguments"], list)
            or len(result["arguments"]) > 64
            or any(
                not isinstance(arg, str) or len(arg) > 4096 or "\0" in arg
                for arg in result["arguments"]
            )
        ):
            raise ValueError("Invalid Python arguments")
        return result

    def call(self, name: str, arguments: dict) -> dict:
        with self._lock:
            self._load()
            if self._state["status"] != "ready":
                raise ValueError("Workspace is closed, interrupted, or requires recovery")
            self._check_trees()
            if len(self._state["calls"]) >= self.config.max_calls:
                raise ValueError("Workspace tool-call limit exhausted")
            request_text = canonical_json({"name": name, "arguments": arguments})
            # Preserve lone surrogates as JSON escapes in the request evidence;
            # reject them through the ordinary, repairable tool-error path below.
            raw = request_text.encode("utf-8", errors="backslashreplace")
            invalid_unicode = any(0xD800 <= ord(char) <= 0xDFFF for char in request_text)
            if len(raw) > self.config.max_file_bytes * 2 + 65536:
                raise ValueError("Tool request exceeds its byte limit")
            call_id = f"tool-{len(self._state['calls']) + 1:06d}"
            artifact = self.output / "tools" / call_id
            artifact.mkdir()
            atomic_write(artifact / "request.json", raw)
            row = {
                "id": call_id,
                "status": "pending",
                "request_sha256": digest(raw),
                "started_at": timestamp(),
            }
            self._state["calls"].append(row)
            self._state["status"] = "pending"
            self._save()
            try:
                if invalid_unicode:
                    raise ValueError("Tool arguments must contain valid Unicode scalar values")
                args = self._arguments(name, arguments)
                result = self._operate(name, args, artifact)
                if len(canonical_json(result).encode()) > self.config.max_tool_output_bytes:
                    raise ValueError("Tool response exceeds its byte limit")
                row["status"] = "completed"
            except Exception as exc:
                # Detailed host paths stay in proof, never in model-facing errors.
                write_json(
                    artifact / "error.json", {"type": type(exc).__name__, "detail": str(exc)}
                )
                result = {
                    "ok": False,
                    "error": "Tool failed; inspect arguments, file limits, or execution output.",
                    "error_type": type(exc).__name__,
                }
                row["status"] = "failed"
            except BaseException:
                # The durable pending call cannot be replayed. Runner already attempted
                # owner-only cleanup; explicit recovery can retry cleanup after restart.
                raise
            write_json(artifact / "result.json", result)
            row.update(result_sha256=digest(canonical_json(result)), finished_at=timestamp())
            # An import or host write can fail midway. Never bless an unknown tree.
            current = _inventory(self.workspace, self.config)
            if current != self._state["workspace_inventory"]:
                self._state["status"] = "uncertain"
            elif not self._executions_quiescent():
                self._state["status"] = "uncertain"
            else:
                self._state["status"] = "ready"
            self._save()
            if self._state["status"] != "ready":
                raise RuntimeError(
                    "Workspace state or cleanup is uncertain; explicit recovery is required"
                )
            return result

    def _operate(self, name: str, args: dict, artifact: Path) -> dict:
        kind, path = self._virtual(args["path"], directory=name == "list_files")
        inventory = self._state[
            "feedback_inventory" if kind == "feedback" else "workspace_inventory"
        ]
        base = self.feedback if kind == "feedback" else self.workspace
        relative = path.relative_to(base).as_posix()
        if name == "list_files":
            if not path.is_dir():
                raise ValueError("Listing requires a directory")
            prefix = "" if relative == "." else relative + "/"
            files = [
                {"path": f"{kind}/{key}", **row}
                for key, row in inventory.items()
                if key.startswith(prefix)
            ]
            selected = []
            start = min(args["offset"], len(files))
            for entry in files[start : start + min(args["limit"], 1000)]:
                if (
                    len(canonical_json(selected + [entry]).encode())
                    > self.config.max_tool_output_bytes - 256
                ):
                    break
                selected.append(entry)
            return {
                "ok": True,
                "files": selected,
                "offset": start,
                "next_offset": start + len(selected)
                if start + len(selected) < len(files)
                else None,
                "total_files": len(files),
            }
        if name == "read_file":
            row = inventory[relative]
            limit = min(args["limit"], (self.config.max_tool_output_bytes - 1024) * 3 // 4)
            # Full-tree validation preceded the call; no candidate runs concurrently.
            with path.open("rb") as handle:
                handle.seek(args["offset"])
                raw = handle.read(limit)
            end = min(row["bytes"], args["offset"] + len(raw))
            return {
                "ok": True,
                "encoding": "base64",
                "content": base64.b64encode(raw).decode(),
                "offset": args["offset"],
                "next_offset": end if end < row["bytes"] else None,
                "total_bytes": row["bytes"],
                "sha256": row["sha256"],
            }
        if kind != "workspace":
            raise ValueError("Feedback is read-only")
        if name == "write_file":
            raw = self._decode(args["content"], args["encoding"])
            planned = {**inventory, relative: {"sha256": digest(raw), "bytes": len(raw)}}
            self._check_planned(planned)
            # Failed earlier writes can leave empty directories, which are not
            # part of the file inventory. Reject their aliases before mkdir/write.
            parent = self.workspace
            for part in Path(relative).parts:
                if not parent.is_dir():
                    break
                normalized = unicodedata.normalize("NFC", part).casefold()
                if any(
                    entry.name != part
                    and unicodedata.normalize("NFC", entry.name).casefold() == normalized
                    for entry in parent.iterdir()
                ):
                    raise ValueError("Write path aliases an existing workspace entry")
                parent /= part
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, raw)
            observed = _inventory(self.workspace, self.config)
            if observed != dict(sorted(planned.items())):
                raise ValueError("Written workspace differs from intended file bytes")
            self._state["workspace_inventory"] = observed
            return {"ok": True, "path": args["path"], "sha256": digest(raw), "bytes": len(raw)}
        if not relative.endswith(".py") or relative not in inventory:
            raise ValueError("run_python requires an existing workspace Python file")
        return self._run(relative, args["arguments"], artifact)

    def _decode(self, content: str, encoding: str) -> bytes:
        if encoding == "utf-8":
            raw = content.encode()
        elif encoding == "base64":
            raw = base64.b64decode(content, validate=True)
        else:
            raise ValueError("Encoding must be utf-8 or base64")
        if len(raw) > self.config.max_file_bytes:
            raise ValueError("File exceeds its byte limit")
        return raw

    def _check_planned(self, inventory: dict) -> None:
        if (
            len(inventory) > self.config.max_files
            or sum(row["bytes"] for row in inventory.values()) > self.config.max_workspace_bytes
        ):
            raise ValueError("Workspace file or byte limit exceeded")
        paths = {}
        for name in inventory:
            parts = _relative(name, self.config.max_path_chars).split("/")
            for length in range(1, len(parts) + 1):
                prefix = "/".join(parts[:length])
                portable = unicodedata.normalize("NFC", prefix).casefold()
                entry = (prefix, "file" if length == len(parts) else "directory")
                if portable in paths and paths[portable] != entry:
                    raise ValueError("Workspace paths collide by case, normalization or file type")
                paths[portable] = entry

    def _run(self, relative: str, arguments: list[str], artifact: Path) -> dict:
        seed = artifact / "seed"
        seed.mkdir(mode=0o755)
        for name, row in self._state["workspace_inventory"].items():
            raw = _regular(self.workspace / name, self.config.max_file_bytes)
            if digest(raw) != row["sha256"]:
                raise ValueError("Workspace changed before execution")
            target = seed / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(0o444)
        wrapper = artifact / "wrapper.py"
        wrapper.write_text(_WRAPPER)
        runner = _WorkspaceRunner(self.config, self.feedback, seed)
        event = {
            "script": relative,
            "arguments": arguments,
            "seed_files": list(self._state["workspace_inventory"]),
            "max_stream_bytes": self.config.max_stream_bytes,
            "child_timeout_seconds": max(0.1, self.config.sandbox.timeout_seconds - 0.5),
            "max_files": self.config.max_files,
            "max_entries": self.config.max_files * 8,
            "max_path_chars": self.config.max_path_chars,
            "max_file_bytes": self.config.max_file_bytes,
            "max_workspace_bytes": self.config.max_workspace_bytes,
        }
        self._state["calls"][-1]["execution_expected"] = True
        self._save()
        result = runner.execute(wrapper, event, artifact / "execution")
        report = _json_object((artifact / "execution" / "execution.json").read_bytes())
        if not self._report_quiescent(report):
            raise ValueError("Execution cleanup is not confirmed")
        streams = {}
        for stream in ("stdout", "stderr"):
            raw = base64.b64decode(result[f"{stream}_base64"], validate=True)
            if len(raw) > self.config.max_stream_bytes:
                raise ValueError("Execution stream exceeds its bound")
            (artifact / f"{stream}.bin").write_bytes(raw)
            streams[stream] = base64.b64encode(raw).decode()
        success = (
            result.get("status") == "completed"
            and result.get("exit_code") == 0
            and result.get("output_complete") is True
            and result.get("output_truncated") is False
        )
        if success:
            self._publish(result["files"], artifact)
        return {
            "ok": success,
            "status": "completed" if success else "failed",
            "exit_code": result.get("exit_code"),
            "encoding": "base64",
            "stdout": streams["stdout"],
            "stderr": streams["stderr"],
            "output_complete": result.get("output_complete"),
            "output_truncated": result.get("output_truncated"),
            "edits_persisted": success,
            "file_error": str(result.get("file_error", ""))[:128],
            "cleanup": report["cleanup"],
        }

    def _publish(self, encoded: dict, artifact: Path) -> None:
        if not isinstance(encoded, dict) or len(encoded) > self.config.max_files:
            raise ValueError("Invalid execution file inventory")
        decoded = {
            _relative(name, self.config.max_path_chars): self._decode(value, "base64")
            for name, value in encoded.items()
        }
        planned = {
            name: {"sha256": digest(raw), "bytes": len(raw)}
            for name, raw in sorted(decoded.items())
        }
        self._check_planned(planned)
        staging = artifact / "workspace-after"
        staging.mkdir()
        for name, raw in decoded.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, raw)
        if _inventory(staging, self.config) != planned:
            raise ValueError("Staged workspace differs from returned bytes")
        # A stopped publication is never auto-replayed or admitted as candidates.
        write_json(artifact / "publication.json", {"status": "prepared", "files": planned})
        self.workspace.rename(artifact / "workspace-before")
        staging.rename(self.workspace)
        write_json(artifact / "publication.json", {"status": "completed", "files": planned})
        self._state["workspace_inventory"] = planned

    @staticmethod
    def _report_quiescent(report: dict) -> bool:
        return (
            report.get("cleanup") in ("removed", "absent")
            or (
                report.get("cleanup") == "not_created"
                and report.get("status") in ("prepared", "failed", "interrupted")
                and report.get("creation_acknowledged") is False
            )
        ) and report.get("attach_process_cleanup") != "unknown"

    def _executions_quiescent(self) -> bool:
        for row in self._state["calls"]:
            report = self.output / "tools" / row["id"] / "execution" / "execution.json"
            if row.get("execution_expected") and not report.exists():
                return False
            if report.exists() and not self._report_quiescent(
                _json_object(_regular(report, 8 * 1024 * 1024))
            ):
                return False
        return True

    def close(self) -> dict:
        with self._lock:
            self._load()
            self._cleanup()
            return self._close(recovered=False)

    def _close(self, *, recovered: bool) -> dict:
        errors = []
        current = {}
        try:
            current = self._check_trees(fresh=True)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        quiescent = self._executions_quiescent()
        if not quiescent:
            errors.append("Owned execution cleanup is not confirmed")
        if any(row["status"] == "pending" for row in self._state["calls"]):
            errors.append("An interrupted tool has no committed result")
        if not self._state["preparation_complete"]:
            errors.append("Workspace preparation did not complete")
        if quiescent:
            try:
                self._remove_feedback_view()
            except Exception as exc:
                errors.append(f"Temporary feedback cleanup failed: {type(exc).__name__}: {exc}")
                quiescent = False
        prior = self._state.get("close")
        if prior and not prior["snapshot_valid"]:
            # Revocation cannot retroactively bless bytes after a failed close,
            # including a corrupt temporary feedback view that was then removed.
            errors = list(dict.fromkeys([*prior["errors"], *errors]))
        proof = {
            "schema_version": 1,
            "workspace_id": self._state["workspace_id"],
            "closed": True,
            "quiescent": quiescent,
            "snapshot_valid": not errors,
            "errors": errors,
            "files": _hashes(current),
            "feedback_files": self.feedback_inventory,
            "feedback_sha256": digest(canonical_json(self.feedback_inventory)),
            "feedback_view_removed": self._state["feedback_view_removed"],
            "recovered": recovered,
            "replayed": False,
            "closed_at": timestamp(),
        }

        def stable(value):
            return {
                key: item for key, item in value.items() if key not in ("closed_at", "recovered")
            }

        if prior and stable(prior) == stable(proof):
            return prior
        self._state.update(status="closed", close=proof)
        self._save()
        return proof

    def snapshot_files(self, paths: list[str]) -> dict[str, bytes]:
        with self._lock:
            self._load()
            proof = self._state.get("close", {})
            if self._state["status"] != "closed" or not all(
                proof.get(key) for key in ("closed", "quiescent", "snapshot_valid")
            ):
                raise ValueError("Snapshot requires a valid closed and quiescent workspace")
            self._check_trees()
            result = {}
            for name in paths:
                kind, path = self._virtual(name)
                if kind != "workspace":
                    raise ValueError("Candidate snapshots require workspace paths")
                relative = path.relative_to(self.workspace).as_posix()
                raw = _regular(path, self.config.max_file_bytes)
                if digest(raw) != proof["files"].get(relative):
                    raise ValueError("Candidate differs from the closed workspace inventory")
                result[name] = raw
            return result

    @classmethod
    def recover(cls, output: Path, config: WorkspaceConfig) -> dict:
        """Cleanup a stopped owner, revoke tools and retain unknown work without replay."""
        output = Path(output).expanduser().resolve()
        if not output.is_dir() or output.is_symlink():
            raise ValueError("Recovery requires an existing workspace output")
        with FileLock(str(output / "owner.lock"), timeout=0):
            state = _json_object(_regular(output / "workspace.json", 64 * 1024 * 1024))
            if state["configuration"] != config.model_dump(mode="json"):
                raise ValueError("Recovery configuration differs from the workspace")
            instance = cls.__new__(cls)
            instance.config, instance.output, instance._state = config, output, state
            instance._verification = VerificationMemo()
            instance.feedback, instance.workspace = Path(state["feedback"]), output / "workspace"
            instance._lock = FileLock(str(output / "owner.lock"), timeout=0)
            instance.feedback_source = Path(state["feedback_source"])
            instance._cleanup()
            return instance._close(recovered=True)

    def _cleanup(self) -> None:
        errors = []
        for row in self._state["calls"]:
            artifact = self.output / "tools" / _relative(row["id"], 64)
            execution = artifact / "execution"
            if (execution / "execution.json").exists():
                try:
                    report = _json_object(_regular(execution / "execution.json", 8 * 1024 * 1024))
                    if not self._report_quiescent(report):
                        runner = _WorkspaceRunner(self.config, self.feedback, artifact / "seed")
                        runner.recover(execution)
                except Exception as exc:
                    errors.append(
                        {"call": row["id"], "error": str(exc), "type": type(exc).__name__}
                    )
        if errors:
            write_json(self.output / "cleanup-errors.json", errors)

    def _owned_feedback_root(self) -> tuple[Path, bool]:
        """Validate the recorded layout, including legacy flat copies, without deleting."""
        parent = Path(self._state["feedback_view_parent"])
        identity = self._state["workspace_id"]
        if (
            not parent.is_absolute()
            or parent.resolve() != parent
            or len(identity) != 32
            or any(char not in "0123456789abcdef" for char in identity)
        ):
            raise ValueError("Temporary feedback ownership does not match")
        layout = self._state.get("feedback_view_layout", "legacy-flat")
        if layout not in {"legacy-flat", "private-parent-v1"}:
            raise ValueError("Unknown temporary feedback layout")
        root = parent / f".rh-proposer-feedback-{identity}"
        private = layout == "private-parent-v1"
        expected = root / "feedback" if private else root
        if self.feedback != expected or self.feedback.is_symlink():
            raise ValueError("Temporary feedback ownership does not match")
        try:
            info = root.lstat()
        except FileNotFoundError:
            return root, private
        if not stat.S_ISDIR(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
            raise ValueError("Temporary feedback ownership does not match")
        if private and any(child.name != "feedback" for child in root.iterdir()):
            raise ValueError("Unexpected files in temporary feedback ownership directory")
        return root, private

    def _remove_feedback_view(self) -> None:
        if not self._state["feedback_view_owned"] or self._state["feedback_view_removed"]:
            return
        root, private = self._owned_feedback_root()
        if private and root.exists():
            root.chmod(0o700)
        if self.feedback.exists():
            for parent, _dirs, _files in os.walk(self.feedback, followlinks=False):
                Path(parent).chmod(0o755)
            shutil.rmtree(self.feedback)
        if private and root.exists():
            # Never recursively remove the parent: unexpected contents are not ours.
            root.rmdir()
        self._state["feedback_view_removed"] = True
