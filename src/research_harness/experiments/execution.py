"""Execute a curated package with fixed, budgeted model access and retained trials."""

from __future__ import annotations

import importlib.metadata
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import httpx

from research_harness.evaluation.dispatch_budget import DispatchBudget
from research_harness.experiments.bundle import tool_policy
from research_harness.experiments.curation import CuratorStore
from research_harness.experiments.package import HARBOR_VERSION, files, load_prepared
from research_harness.experiments.program import read_program
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.integrations.omnigent import check_runtime
from research_harness.util import digest, timestamp, write_json

AGENT = "research_harness.experiments.harbor_agent:OmnigentAgent"
ENVIRONMENT = "research_harness.experiments.harbor_environment:ExperimentDocker"


def snapshot_source(output: Path) -> dict[str, str]:
    package = Path(__file__).resolve().parent.parent
    paths = sorted(package.rglob("*.py"))
    for path in paths:
        if path.is_symlink():
            raise ValueError("Execution source must not contain symlinks")
        target = output / "research_harness" / path.relative_to(package)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    for name in ("pyproject.toml", "uv.lock"):
        path = package.parents[1] / name
        if path.is_file():
            (output / name).write_bytes(path.read_bytes())
    return files(output)


def inventory(output: Path) -> dict[str, str]:
    """Seal exported evidence, excluding private Omnigent databases and credentials."""
    result = {}

    def add(path):
        if path.is_symlink():
            raise ValueError("Experiment evidence must not contain symlinks")
        if path.is_file():
            result[path.relative_to(output).as_posix()] = digest(path.read_bytes())

    roots = [output / name for name in ("source", "gateway", "jobs", "candidate")]
    if (output / "controller").is_symlink():
        raise ValueError("Experiment evidence must not contain symlinks")
    for child in (output / "controller").glob("*"):
        add(child)
        if child.is_dir():
            roots.extend(child / name for name in ("commands", "runtime", "bundle", "program"))
            for name in (
                "command.json",
                "driver.json",
                "driver.log",
                "cleanup.json",
                "dependencies.json",
            ):
                path = child / name
                add(path)
    for root in roots:
        if root.exists() or root.is_symlink():
            result.update(
                {
                    (root.relative_to(output) / name).as_posix(): value
                    for name, value in files(root).items()
                }
            )
    for name in (
        "execution.json",
        "review.json",
        "source-sha256.json",
        "job.json",
        "harbor.log",
        "dependencies.json",
    ):
        add(output / name)
    return result


def status(output: Path) -> dict:
    report = json.loads((output / "execution.json").read_bytes())
    if report["status"] in {"starting", "running"}:
        return {**report, "liveness": "not_checked", "retry": "inspect existing run; do not replay"}
    if json.loads((output / "files.json").read_bytes()) != inventory(output):
        raise ValueError("Experiment evidence changed or is missing")
    return report


def collect(
    prepared: Path, output: Path, model: str, candidate_sha256: str | None = None
) -> list[dict]:
    expected = {
        str((prepared / "inputs/tasks" / path).resolve())
        for path in load_prepared(prepared)["tasks"]
    }
    rows = []
    for path in sorted((output / "jobs/experiment").glob("*/result.json")):
        raw = json.loads(path.read_bytes())
        task_path = str(Path(raw["config"]["task"]["path"]).resolve())
        if task_path not in expected:
            raise ValueError("Unexpected or duplicate experiment task result")
        expected.remove(task_path)
        row = {
            "task": raw["task_name"],
            "trial_id": raw["id"],
            "result": str(path.relative_to(output)),
            "exception": raw.get("exception_info"),
            "reward": None,
            "verified": False,
        }
        if raw.get("finished_at") and not row["exception"]:
            reward = (raw.get("verifier_result") or {}).get("rewards", {}).get("reward")
            reward_path = path.parent / "verifier/reward.txt"
            controller = output / "controller" / raw["id"]
            runtime_path = controller / "runtime/runtime.json"
            runtime = json.loads(runtime_path.read_bytes()) if runtime_path.is_file() else {}
            policy_path = controller / "runtime/tool-policy.json"
            policy = json.loads(policy_path.read_bytes()) if policy_path.is_file() else {}
            program_path = controller / "program/program.json"
            program = json.loads(program_path.read_bytes()) if program_path.is_file() else {}
            program_verified = candidate_sha256 is None or (
                program.get("status") == "completed"
                and program.get("exit_code") == 0
                and program.get("source_sha256") == candidate_sha256
                and program.get("container_stopped") is True
                and program.get("context_id") == raw["id"]
                and program.get("environment_session_id") == path.parent.name + "__env"
            )
            attestations = [
                value
                for evidence_path in (output / "controller").glob("environment-*.json")
                if (value := json.loads(evidence_path.read_bytes())).get("context_id") == raw["id"]
            ]
            isolation_verified = (
                len(attestations) == 2
                and {value["session_id"] for value in attestations}
                == {
                    path.parent.name + "__env",
                    path.parent.name + "__verifier__trial",
                }
                and all(
                    value["mounts"] == []
                    and value["privileged"] is False
                    and value["network_mode"] == "none"
                    and value["memory_bytes"] > 0
                    and value["nano_cpus"] > 0
                    for value in attestations
                )
            )
            row.update(
                reward=reward,
                runtime_session_id=runtime.get("session_id"),
                delegation_observed=runtime.get("delegation_observed", False),
                verified=(
                    type(reward) in (int, float)
                    and reward in (0, 1)
                    and reward_path.is_file()
                    and float(reward_path.read_text()) == reward
                    and raw["config"]["agent"]["import_path"] == AGENT
                    and raw["config"]["agent"]["model_name"] == model
                    and raw["config"]["environment"]["import_path"] == ENVIRONMENT
                    and raw["verifier_environment_mode"] == "separate"
                    and runtime.get("status") == "completed"
                    and runtime.get("delegation_observed") is True
                    and policy.get("submitted") == tool_policy(program=candidate_sha256 is not None)
                    and program_verified
                    and isolation_verified
                ),
            )
        rows.append(row)
    if expected:
        raise ValueError("Experiment did not retain every expected task result")
    return rows


def run(
    prepared: Path,
    check: Path,
    curator: CuratorStore,
    budget: DispatchBudget,
    *,
    output: Path,
    harbor: Path,
    omnigent_python: Path,
    max_commands: int = 20,
    timeout: int = 1800,
    provider_api_key: str | None = None,
    client: httpx.Client | None = None,
    candidate: Path | None = None,
) -> dict:
    """Trusted operator API. Provider access/budget authorization precedes this call.

    Fixture mode requires MockTransport in ResponsesGateway. A paid provider is
    never selected implicitly; its existing DispatchBudget must match this task.
    Withdrawal affects subsequent starts, not an already running attempt.
    """
    prepared, check, output = prepared.resolve(), check.resolve(), output.resolve()
    review = curator.require_accepted(prepared, check)
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in (prepared, check)):
        raise ValueError("Execution output must be separate from prepared/check inputs")
    if curator.path.is_relative_to(output) or budget.ledger.path.is_relative_to(output):
        raise ValueError("Operator curation and budget records must stay outside run output")
    if (
        budget.binding.case_id != review["experiment_id"]
        or budget.binding.task_sha256 != review["input_sha256"]
        or budget.binding.phase != "workflow"
        or budget.binding.runtime != "omnigent-experiment"
    ):
        raise ValueError("Dispatch budget is not bound to this experiment")
    if type(max_commands) is not int or not 1 <= max_commands <= 100:
        raise ValueError("Choose between 1 and 100 workspace commands per task")
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("Choose an execution timeout between 1 and 86400 seconds")
    candidate_source = read_program(candidate) if candidate is not None else None
    runtime = check_runtime(omnigent_python)
    version = subprocess.run(
        [str(harbor), "--version"], capture_output=True, text=True, check=True, timeout=30
    )
    if version.stdout.strip() != HARBOR_VERSION:
        raise ValueError(f"Experiment execution requires Harbor {HARBOR_VERSION}")
    output.mkdir(parents=True, exist_ok=False)
    candidate_sha256 = None
    if candidate_source is not None:
        (output / "candidate").mkdir()
        (output / "candidate/agent.py").write_bytes(candidate_source)
        candidate_sha256 = digest(candidate_source)
    write_json(output / "review.json", review)
    source_root = output / "source"
    source_hashes = snapshot_source(source_root)
    write_json(output / "source-sha256.json", source_hashes)
    write_json(
        output / "dependencies.json",
        {
            "python": sys.version,
            "executable": sys.executable,
            "distributions": {
                d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
            },
        },
    )
    report = {
        "schema_version": 1,
        "kind": "agent_experiment",
        "status": "starting",
        "started_at": timestamp(),
        "execution_id": budget.binding.execution_id,
        "input_sha256": review["input_sha256"],
        "review_id": review["head"],
        "model": budget.policy.model,
        "model_transport": budget.policy.mode,
        "model_settings": budget.settings.model_dump(mode="json"),
        "budget": budget.metadata(),
        "omnigent": runtime,
        "harbor_version": HARBOR_VERSION,
        "runtime_network_override": "none (task and verifier); image builds may use networking",
        "execution_interface": "python_program_v1" if candidate_sha256 else "workspace_commands",
        "max_commands_per_task": None if candidate_sha256 else max_commands,
        "max_program_starts_per_task": 1 if candidate_sha256 else None,
        "timeout_seconds": timeout,
        "finding_accepted": False,
        "candidate_sha256": candidate_sha256,
    }
    write_json(output / "execution.json", report)
    config = {
        "job_name": "experiment",
        "jobs_dir": str(output / "jobs"),
        "n_attempts": 1,
        "n_concurrent_trials": 1,
        "quiet": True,
        "retry": {"max_retries": 0},
        "environment": {
            "type": "docker",
            "import_path": ENVIRONMENT,
            "kwargs": {"evidence_root": str(output / "controller")},
            "delete": True,
            "force_build": True,
        },
        "agents": [
            {
                "import_path": AGENT,
                "model_name": budget.policy.model,
                "kwargs": {
                    "controller_root": str(output / "controller"),
                    "omnigent_python": str(omnigent_python.absolute()),
                    "max_commands": max_commands,
                    "candidate": str(output / "candidate/agent.py") if candidate_sha256 else None,
                    "execution_timeout": min(budget.settings.deadline_seconds, 3600),
                },
            }
        ],
        "tasks": [{"path": str(prepared / "inputs/tasks" / task)} for task in review["tasks"]],
    }
    write_json(output / "job.json", config)
    gateway = None
    process = None
    try:
        gateway = ResponsesGateway(
            output / "gateway",
            model=budget.policy.model,
            settings=budget.settings,
            upstream_base_url=budget.policy.upstream_base_url,
            upstream_api_key=provider_api_key,
            client=client,
            binding=budget.binding,
            dispatch_budget=budget,
        )
        with gateway:
            curator.require_accepted(prepared, check)
            env = {
                k: v
                for k, v in os.environ.items()
                if k
                in {
                    "PATH",
                    "HOME",
                    "TMPDIR",
                    "LANG",
                    "LC_ALL",
                    "DOCKER_HOST",
                    "DOCKER_CONTEXT",
                }
            }
            env.update(
                PYTHONPATH=str(source_root),
                PYTHONDONTWRITEBYTECODE="1",
                RH_MODEL_GATEWAY_KEY=gateway.api_key,
                RH_MODEL_GATEWAY_URL=gateway.base_url,
            )
            command = [str(harbor.absolute()), "run", "--config", str(output / "job.json")]
            report.update(status="running", command=command)
            with (output / "harbor.log").open("wb") as log:
                process = subprocess.Popen(
                    command,
                    cwd=output,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                report["pid"] = process.pid
                write_json(output / "execution.json", report)
                try:
                    report["exit_code"] = process.wait(timeout=timeout)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGINT)
                        try:
                            process.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                        report["cleanup_status"] = "inspect_recorded_runtime_and_docker_resources"
        load_prepared(prepared)
        if files(source_root) != source_hashes:
            raise ValueError("The frozen execution adapter changed during the run")
        if (
            candidate_sha256
            and digest((output / "candidate/agent.py").read_bytes()) != candidate_sha256
        ):
            raise ValueError("The frozen candidate program changed during the run")
        report["trials"] = collect(prepared, output, budget.policy.model, candidate_sha256)
        report["status"] = (
            "completed"
            if report["exit_code"] == 0 and all(r["verified"] for r in report["trials"])
            else "failed"
        )
    except (Exception, KeyboardInterrupt) as exc:
        report.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "error", error=str(exc)
        )
    finally:
        if gateway is not None:
            report["usage"] = gateway.report()
            archive = output / "gateway/archive.json"
            if archive.is_file():
                try:
                    report["accounting"] = budget.reconcile_archive(archive)
                except Exception as exc:
                    report["accounting_error"] = str(exc)
        report["finished_at"] = timestamp()
        write_json(output / "execution.json", report)
        write_json(output / "files.json", inventory(output))
    return report
