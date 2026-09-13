"""Snapshot a proposed experiment for human review without executing its code.

Harbor owns task/environment formats. These local operator commands do not
authenticate a human, approve a proposal, or expose evaluator files to an agent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from research_harness.config import StrictModel
from research_harness.util import canonical_json, digest, timestamp, write_json

HARBOR_VERSION = "0.23.0"


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("Paths must stay inside their input package")
    if str(path) != value or value == ".":
        raise ValueError("Paths must use canonical relative names")
    return value


class Task(StrictModel):
    path: str
    overlay: str | None = None

    @model_validator(mode="after")
    def local_paths(self):
        relative_path(self.path)
        if self.overlay is not None:
            relative_path(self.overlay)
        return self


class Experiment(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    question: str = Field(min_length=1)
    plan: str
    benchmark_repository: str = Field(pattern=r"^https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?$")
    benchmark_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    tasks: list[Task] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def coherent(self):
        relative_path(self.plan)
        paths = [task.path for task in self.tasks]
        if len(paths) != len(set(paths)):
            raise ValueError("Experiment task paths must be unique")
        for left in paths:
            if any(right != left and right.startswith(left + "/") for right in paths):
                raise ValueError("Experiment task directories must not overlap")
        return self


def files(root: Path) -> dict[str, str]:
    """Fingerprint regular files; never follow links into another workspace."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Expected a real directory: {root}")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise ValueError(f"Only regular files/directories are allowed: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return result


def contained(root: Path, name: str) -> Path:
    relative_path(name)
    path = root / name
    # Reject symlink components, even if the link happens to point inside root.
    for parent in [path, *path.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("Input paths cannot traverse symbolic links")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Input path escapes its package")
    return path


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30
    )
    if result.returncode:
        raise ValueError("Cannot verify benchmark checkout")
    return result.stdout.strip()


def task_details(path: Path) -> dict:
    config = tomllib.loads((path / "task.toml").read_text())
    for name in ("instruction.md", "solution/solve.sh", "tests/test.sh"):
        if not (path / name).is_file():
            raise ValueError(f"Task requires {name}")
    if config.get("verifier", {}).get("environment_mode") != "separate":
        raise ValueError("Experiment checks require a separate Harbor verifier environment")
    if not config.get("artifacts"):
        raise ValueError("A separate verifier needs explicit candidate artifacts")
    verifier_image = config["verifier"].get("environment", {}).get("docker_image")
    if not (
        verifier_image
        or config.get("environment", {}).get("docker_image")
        or (path / "tests/Dockerfile").is_file()
        or (path / "tests/docker-compose.yaml").is_file()
    ):
        raise ValueError("A separate verifier requires its own image or tests/Dockerfile")
    if config.get("steps"):
        raise ValueError("This first experiment checker supports single-step tasks")
    for section in ("agent", "environment", "verifier", "solution"):
        if config.get(section, {}).get("env"):
            raise ValueError("Credential-bearing task environments are not supported by checks")
    environment = config.get("environment", {})
    if environment.get("gpus", 0) or environment.get("tpu"):
        raise ValueError("Benchmark controls currently run on local CPU Docker only")
    return {
        "name": config.get("task", {}).get("name", path.name),
        "environment": environment,
        "agent": config.get("agent", {}),
        "verifier": config["verifier"],
        "artifacts": config["artifacts"],
    }


def prepare(manifest: Path, checkout: Path, output: Path) -> dict:
    manifest, checkout, output = manifest.resolve(), checkout.resolve(), output.resolve()
    spec = Experiment.model_validate_json(manifest.read_bytes())
    plan_path = contained(manifest.parent, spec.plan)
    plan = plan_path.read_bytes()
    if Path(_git(checkout, "rev-parse", "--show-toplevel")) != checkout:
        raise ValueError("Pass the benchmark repository root as --checkout")
    if _git(checkout, "rev-parse", "HEAD") != spec.benchmark_revision:
        raise ValueError("Benchmark checkout differs from the proposed revision")
    origin = _git(checkout, "remote", "get-url", "origin").removesuffix(".git")
    if origin != spec.benchmark_repository.removesuffix(".git"):
        raise ValueError("Benchmark origin differs from the proposed repository")
    if output.is_relative_to(checkout) or output.is_relative_to(manifest.parent):
        raise ValueError("Prepared output must be outside its input directories")
    source_roots = {}
    overlays = {}
    for task in spec.tasks:
        source = contained(checkout, task.path)
        # Include ignored additions: they would otherwise silently change the task.
        if _git(checkout, "status", "--porcelain", "--ignored", "--", task.path):
            raise ValueError(f"Benchmark task has uncommitted or ignored changes: {task.path}")
        source_roots[task.path] = (source, files(source))
        if task.overlay:
            overlay = contained(manifest.parent, task.overlay)
            overlays[task.path] = (overlay, files(overlay))
    license_path = contained(checkout, "LICENSE")
    if _git(checkout, "status", "--porcelain", "--ignored", "--", "LICENSE"):
        raise ValueError("Benchmark license has uncommitted changes")
    license_bytes = license_path.read_bytes()
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    inputs.mkdir()
    write_json(inputs / "experiment.json", spec.model_dump(mode="json"))
    (inputs / "plan.md").write_bytes(plan)
    (inputs / "LICENSE.benchmark").write_bytes(license_bytes)
    details = {}
    try:
        for task in spec.tasks:
            source, fingerprint = source_roots[task.path]
            original = inputs / "upstream" / task.path
            shutil.copytree(source, original)
            if files(original) != fingerprint:
                raise ValueError("Benchmark changed during preparation")
            destination = inputs / "tasks" / task.path
            shutil.copytree(original, destination)
            if task.path in overlays:
                overlay, expected = overlays[task.path]
                saved = inputs / "overlays" / task.path
                shutil.copytree(overlay, saved)
                if files(saved) != expected:
                    raise ValueError("Task overlay changed during preparation")
                shutil.copytree(saved, destination, dirs_exist_ok=True)
            details[task.path] = task_details(destination)
        inventory = files(inputs)
        record = {
            "schema_version": 1,
            "created_at": timestamp(),
            "experiment": spec.model_dump(mode="json"),
            "harbor_version": HARBOR_VERSION,
            "input_sha256": digest(canonical_json(inventory)),
            "files": inventory,
            "tasks": details,
            "curation_status": "pending_human_review",
        }
        write_json(output / "prepared.json", record)
        return record
    except BaseException:
        write_json(output / "preparation-error.json", {"status": "incomplete"})
        raise


def load_prepared(output: Path) -> dict:
    record = json.loads((output / "prepared.json").read_bytes())
    inventory = files(output / "inputs")
    if inventory != record["files"] or digest(canonical_json(inventory)) != record["input_sha256"]:
        raise ValueError("Prepared experiment inputs changed; prepare and review a new version")
    spec = Experiment.model_validate_json((output / "inputs/experiment.json").read_bytes())
    if spec.model_dump(mode="json") != record["experiment"]:
        raise ValueError("Prepared manifest does not match its record")
    if set(record["tasks"]) != {task.path for task in spec.tasks}:
        raise ValueError("Prepared task list does not match its manifest")
    if record["harbor_version"] != HARBOR_VERSION:
        raise ValueError("Prepared package requires a different Harbor version")
    for task in spec.tasks:
        if task_details(output / "inputs/tasks" / task.path) != record["tasks"][task.path]:
            raise ValueError("Prepared task details do not match their record")
    # This command cannot promote a proposal to human acceptance.
    return {**record, "curation_status": "pending_human_review"}
