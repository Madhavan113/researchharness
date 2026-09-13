"""Run arbitrary candidate Python in a restricted, disposable Linux container.

Only copied code, the standalone worker and explicit JSON input are mounted.
The host owns evidence, model access and budgets. This is an execution boundary,
not an optimizer, evaluator, model client or proof against kernel/daemon exploits.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from filelock import FileLock
from pydantic import ConfigDict, Field, StrictInt

from research_harness.config import StrictModel
from research_harness.util import canonical_json, digest, timestamp, write_json

LABEL = "research-harness.strategy-execution"


class SandboxConfig(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    image: str = Field(pattern=r"^python@sha256:[a-f0-9]{64}$")
    timeout_seconds: StrictInt = Field(default=10, ge=1, le=120)
    memory_mb: StrictInt = Field(default=256, ge=64, le=1024)
    max_input_bytes: StrictInt = Field(default=1024 * 1024, ge=1024, le=8 * 1024 * 1024)
    max_output_bytes: StrictInt = Field(default=1024 * 1024, ge=1024, le=8 * 1024 * 1024)
    max_source_bytes: StrictInt = Field(default=1024 * 1024, ge=1024, le=4 * 1024 * 1024)


class StrategyExecutionError(RuntimeError):
    def __init__(self, message: str, output: Path):
        super().__init__(message)
        self.artifacts = output


def _json_object(raw: bytes) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate JSON object key")
            value[key] = item
        return value

    def nonfinite(value):
        raise ValueError(f"Nonfinite JSON number: {value}")

    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    if not isinstance(result, dict):
        raise ValueError("Strategy protocol requires a JSON object")
    # A finite literal such as 1e999 can overflow the host's float parser.
    json.dumps(result, allow_nan=False)
    return result


class DockerStrategyRunner:
    def __init__(self, config: SandboxConfig, *, docker: Path | None = None):
        self.config = SandboxConfig.model_validate(config)
        executable = str(docker) if docker else shutil.which("docker")
        if not executable:
            raise ValueError("Docker is required; there is no unsandboxed fallback")
        self.docker = str(Path(executable).expanduser().absolute())

    def _command(self, arguments: list[str], *, timeout: int = 15) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.docker, *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=True,
        )

    def _creation_rejection(self, error: subprocess.CalledProcessError, arguments: list[str]):
        """Recognize proven pre-creation rejection, never infer it from an exit code alone."""
        if error.returncode <= 0 or error.stdout or not isinstance(error.stderr, bytes):
            return None
        try:
            message = error.stderr.decode("utf-8").strip()
        except UnicodeError:
            return None
        # Moby looks up the image before allocating a container. With pulling
        # forbidden, this exact error cannot leave a pending create request.
        if (
            "--pull=never" in arguments
            and self.config.image in arguments
            and message == f"Error response from daemon: No such image: {self.config.image}"
        ):
            return "image_missing_before_creation"
        # Docker CLI flag parsing happens before its create handler. Match its
        # first diagnostic line and a flag actually supplied by this host;
        # CLI releases differ in the help text printed afterward.
        match = re.match(r"unknown flag: (--[a-z0-9-]+)(?:\n|$)", message)
        if (
            error.returncode == 125
            and match
            and any(argument.split("=", 1)[0] == match[1] for argument in arguments)
        ):
            return "cli_flag_rejected_before_creation"
        return None

    def _inspect_image(self) -> dict:
        image = json.loads(self._command(["image", "inspect", self.config.image]).stdout)[0]
        if image.get("Os") != "linux" or self.config.image not in image.get("RepoDigests", []):
            raise ValueError("A locally available, digest-pinned official Python image is required")
        if image.get("Config", {}).get("Volumes"):
            raise ValueError("The strategy image cannot declare implicit volumes")
        env = image.get("Config", {}).get("Env", [])
        if any(
            item.split("=", 1)[0]
            not in {"PATH", "LANG", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256"}
            for item in env
        ):
            raise ValueError("The strategy image has unexpected environment settings")
        return {
            key: image.get(key) for key in ("Id", "RepoDigests", "Os", "Architecture", "Config")
        }

    def _inspect_engine(self) -> dict:
        info = json.loads(self._command(["info", "--format", "{{json .}}"]).stdout)
        if info.get("OSType") != "linux" or not any(
            item.startswith("name=seccomp") for item in info.get("SecurityOptions", [])
        ):
            raise ValueError("The strategy runner requires a Linux Docker engine with seccomp")
        return {
            key: info.get(key)
            for key in (
                "OSType",
                "SecurityOptions",
                "CgroupVersion",
                "ServerVersion",
                "KernelVersion",
            )
        }

    def _create_arguments(self, source: Path, name: str, identity: str) -> list[str]:
        # Docker's mount parser uses comma-separated fields even without a shell.
        if any(char in str(source) for char in (",", "\n", "\r")):
            raise ValueError("The sandbox input path cannot contain mount-field delimiters")
        return [
            "create",
            "--pull=never",
            "--name",
            name,
            "--label",
            f"{LABEL}={identity}",
            "--network=none",
            "--read-only",
            "--user=65534:65534",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--pids-limit=32",
            f"--memory={self.config.memory_mb}m",
            f"--memory-swap={self.config.memory_mb}m",
            "--cpus=1",
            "--ulimit=nofile=128:128",
            "--ulimit=core=0:0",
            "--ulimit=fsize=8388608:8388608",
            "--shm-size=8m",
            "--ipc=private",
            "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
            "--workdir=/tmp",
            "--no-healthcheck",
            "--log-driver=none",
            "--mount",
            f"type=bind,source={source},target=/input,readonly",
            "--entrypoint=/usr/local/bin/python3",
            self.config.image,
            "-I",
            "-B",
            "/input/worker.py",
        ]

    def _remove_owned(self, name: str, identity: str) -> str:
        try:
            metadata = json.loads(self._command(["inspect", "--type", "container", name]).stdout)[0]
        except subprocess.CalledProcessError as exc:
            # A daemon failure or permission error is not evidence of absence.
            if (
                b"no such object" in exc.stderr.lower()
                or b"no such container" in exc.stderr.lower()
            ):
                return "absent"
            raise
        if metadata.get("Config", {}).get("Labels", {}).get(LABEL) != identity:
            raise ValueError("Container ownership does not match; refusing cleanup")
        self._command(["rm", "--force", metadata["Id"]])
        try:
            self._command(["inspect", "--type", "container", metadata["Id"]])
        except subprocess.CalledProcessError as exc:
            if (
                b"no such object" in exc.stderr.lower()
                or b"no such container" in exc.stderr.lower()
            ):
                return "removed"
            raise
        raise RuntimeError("Strategy container still exists after cleanup")

    def execute(self, source: Path, event: dict[str, Any], output: Path) -> dict[str, Any]:
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(output) + ".lock", timeout=0):
            try:
                return self._execute(source, event, output)
            except BaseException as exc:
                if (output / "execution.json").is_file() and not hasattr(exc, "artifacts"):
                    exc.artifacts = output
                raise

    def _check_container(self, metadata: dict, inputs: Path, identity: str, image: dict) -> None:
        host, config = metadata["HostConfig"], metadata["Config"]
        expected = {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "Memory": self.config.memory_mb * 1024 * 1024,
            "MemorySwap": self.config.memory_mb * 1024 * 1024,
            "NanoCpus": 1_000_000_000,
            "PidsLimit": 32,
            "PidMode": "",
            "IpcMode": "private",
            "ShmSize": 8 * 1024 * 1024,
            "LogConfig": {"Type": "none", "Config": {}},
            "Tmpfs": {"/tmp": "rw,nosuid,nodev,noexec,size=16m,mode=1777"},
        }
        if any(host.get(key) != value for key, value in expected.items()):
            raise ValueError("Docker did not apply the required isolation/resource configuration")
        mounts = metadata["Mounts"]
        if len(mounts) != 1 or any(
            mounts[0].get(key) != value
            for key, value in {
                "Type": "bind",
                "Source": str(inputs),
                "Destination": "/input",
                "RW": False,
            }.items()
        ):
            raise ValueError("Docker did not apply the exact read-only input mount")
        if (
            host.get("CapDrop") != ["ALL"]
            or host.get("CapAdd") not in (None, [])
            or host.get("Devices") not in (None, [])
            or host.get("DeviceRequests") not in (None, [])
            or host.get("SecurityOpt") != ["no-new-privileges:true"]
            or config.get("User") != "65534:65534"
            or config.get("Labels", {}).get(LABEL) != identity
            or config.get("Entrypoint") != ["/usr/local/bin/python3"]
            or config.get("Cmd") != ["-I", "-B", "/input/worker.py"]
            or config.get("WorkingDir") != "/tmp"
            or config.get("Env") != image["Config"].get("Env", [])
            or metadata.get("Image") != image["Id"]
        ):
            raise ValueError("Docker strategy process policy differs from the host configuration")
        limits = {row["Name"]: (row["Soft"], row["Hard"]) for row in host.get("Ulimits", [])}
        if limits != {"core": (0, 0), "fsize": (8388608, 8388608), "nofile": (128, 128)}:
            raise ValueError("Docker did not apply strategy process resource limits")

    def _execute(self, source: Path, event: dict[str, Any], output: Path) -> dict[str, Any]:
        """Execute once, retaining code/input/output/errors and a host-owned report.

        Reusing an existing output is refused, including failed/interrupted attempts.
        Candidate stdout/stderr are untrusted artifacts, never host instructions.
        """
        source, output = Path(source), Path(output).resolve()
        if source.is_symlink() or not source.is_file():
            raise ValueError("Strategy source must be a regular file, not a symlink")
        descriptor = os.open(
            source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("Strategy source must be a regular file")
            code = handle.read(self.config.max_source_bytes + 1)
        request = canonical_json(event).encode()
        _json_object(request)
        if len(code) > self.config.max_source_bytes or len(request) > self.config.max_input_bytes:
            raise ValueError("Strategy source/input exceeds the configured limit")
        output.mkdir(parents=True, exist_ok=False)
        inputs = output / "input"
        inputs.mkdir(mode=0o755)
        (inputs / "strategy.py").write_bytes(code)
        (inputs / "request.json").write_bytes(request)
        (inputs / "worker.py").write_bytes(Path(__file__).with_name("worker.py").read_bytes())
        for path in inputs.iterdir():
            path.chmod(0o444)
        identity = uuid4().hex
        name = f"rh-strategy-{identity}"
        report = {
            "schema_version": 1,
            "protocol": "research-strategy-v1",
            "execution_id": identity,
            "container_name": name,
            "status": "prepared",
            "started_at": timestamp(),
            "configuration": self.config.model_dump(mode="json"),
            "source_sha256": digest(code),
            "request_sha256": digest(request),
            "worker_sha256": digest((inputs / "worker.py").read_bytes()),
            "mounts": ["copied strategy.py, request.json and worker.py only; read-only"],
            "network": "none",
            "candidate_credentials": "none",
            "output_truncated": False,
            "cleanup": "not_created",
            "creation_acknowledged": False,
        }
        report_path = output / "execution.json"
        write_json(report_path, report)
        process = None
        readers = []
        overflow = threading.Event()
        reader_errors = []
        streams = {"stdout": bytearray(), "stderr": bytearray()}
        error = None
        decision = None
        created = False
        started = time.monotonic()

        def consume(stream, target):
            try:
                while chunk := stream.read(65536):
                    remaining = self.config.max_output_bytes - len(target)
                    target.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        overflow.set()
            except Exception as exc:
                reader_errors.append(str(exc))
            finally:
                stream.close()

        try:
            report["engine"] = self._inspect_engine()
            report["image"] = self._inspect_image()
            arguments = self._create_arguments(inputs, name, identity)
            report["status"] = "creating"
            write_json(report_path, report)
            created = True  # A failed/timed-out create may still have reached the daemon.
            try:
                container = self._command(arguments).stdout.decode().strip()
            except subprocess.CalledProcessError as exc:
                rejection = self._creation_rejection(exc, arguments)
                if rejection is not None:
                    created = False
                    report["creation_rejection"] = {
                        "reason": rejection,
                        "exit_code": exc.returncode,
                        "stderr": exc.stderr.decode("utf-8"),
                        "stdout": "",
                        "arguments": arguments,
                    }
                raise
            if not re.fullmatch(r"[a-f0-9]{64}", container):
                raise ValueError("Docker did not return a container identity")
            report["container_id"] = container
            report["creation_acknowledged"] = True
            metadata = json.loads(self._command(["inspect", container]).stdout)[0]
            self._check_container(metadata, inputs, identity, report["image"])
            report["container"] = {
                "HostConfig": metadata["HostConfig"],
                "Mounts": metadata["Mounts"],
                "Config": metadata["Config"],
                "Image": metadata["Image"],
            }
            report["status"] = "running"
            write_json(report_path, report)
            process = subprocess.Popen(
                [self.docker, "start", "--attach", container],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                thread = threading.Thread(target=consume, args=(stream, streams[name]), daemon=True)
                readers.append(thread)
                thread.start()
            deadline = time.monotonic() + self.config.timeout_seconds
            while process.poll() is None:
                if overflow.wait(0.02):
                    raise ValueError("Strategy output exceeded the configured limit")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Strategy exceeded its wall-clock limit")
            report["exit_code"] = process.returncode
            for thread in readers:
                thread.join(2)
            if any(thread.is_alive() for thread in readers):
                raise RuntimeError("Strategy output collection did not terminate")
            if overflow.is_set() or reader_errors:
                raise ValueError("Strategy output exceeded its limit or could not be read")
            if process.returncode != 0:
                raise ValueError(f"Strategy exited with status {process.returncode}")
            decision = _json_object(bytes(streams["stdout"]))
            write_json(output / "decision.json", decision)
        except BaseException as exc:
            error = exc
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if created:
                try:
                    report["cleanup"] = self._remove_owned(report["container_name"], identity)
                    if report["cleanup"] == "absent" and not report["creation_acknowledged"]:
                        report["cleanup"] = "absent_at_check_creation_unconfirmed"
                except BaseException as exc:
                    report["cleanup"] = "unknown"
                    report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
                    if error is None:
                        error = exc
            if process is not None:
                if process.poll() is None:
                    process.kill()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    report["attach_process_cleanup"] = "unknown"
                    if error is None:
                        error = RuntimeError("Docker attach process did not terminate")
            for thread in readers:
                thread.join(2)
            report.update(
                status="completed"
                if error is None
                else "failed"
                if isinstance(error, Exception)
                else "interrupted",
                finished_at=timestamp(),
                elapsed_seconds=time.monotonic() - started,
                output_truncated=overflow.is_set(),
                reader_errors=reader_errors,
                output_collection_complete=not any(thread.is_alive() for thread in readers)
                and not reader_errors
                and not overflow.is_set(),
            )
            for name, data in streams.items():
                (output / f"{name}.txt").write_bytes(bytes(data))
            report["artifact_hashes"] = {
                str(path.relative_to(output)): digest(path.read_bytes())
                for path in sorted(output.rglob("*"))
                if path.is_file() and path != report_path
            }
            write_json(report_path, report)
        if error is not None:
            if not isinstance(error, Exception):
                error.artifacts = output
                raise error
            raise StrategyExecutionError(str(error), output) from error
        return decision

    def recover(self, output: Path) -> dict:
        """Inspect/terminate only this stopped execution's container; never replay code."""
        output = Path(output).resolve()
        with FileLock(str(output) + ".lock", timeout=0):
            return self._recover(output)

    def _recover(self, output: Path) -> dict:
        path = output / "execution.json"
        report = _json_object(path.read_bytes())
        if report["configuration"] != self.config.model_dump(mode="json"):
            raise ValueError("Recovery configuration differs from the recorded execution")
        if report["status"] == "completed":
            return report
        never_created = (
            report["cleanup"] == "not_created"
            and report["status"] in {"prepared", "failed", "interrupted"}
            and report["creation_acknowledged"] is False
        )
        if not never_created:
            report["cleanup"] = self._remove_owned(report["container_name"], report["execution_id"])
            if report["cleanup"] == "absent" and not report["creation_acknowledged"]:
                report["cleanup"] = "absent_at_check_creation_unconfirmed"
        report["status"] = "failed"
        report["recovery"] = {"at": timestamp(), "replayed": False}
        write_json(path, report)
        return report


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    execute = commands.add_parser("execute")
    execute.add_argument("source", type=Path)
    execute.add_argument("--input", required=True, type=Path)
    execute.add_argument("--out", required=True, type=Path)
    recover = commands.add_parser("recover")
    recover.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    runner = DockerStrategyRunner(SandboxConfig.model_validate_json(args.config.read_bytes()))
    if args.command == "recover":
        result = runner.recover(args.output)
    else:
        with args.input.open("rb") as handle:
            raw = handle.read(runner.config.max_input_bytes + 1)
        if len(raw) > runner.config.max_input_bytes:
            raise ValueError("Strategy input exceeds the configured limit")
        result = runner.execute(args.source, _json_object(raw), args.out)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
