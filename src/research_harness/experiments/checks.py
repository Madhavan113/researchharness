"""Run known-solution and no-op controls in Harbor, retaining raw evidence.

This is an operator benchmark check, not agent execution, human approval, a
general sandbox boundary, or evidence of research quality. Task definitions
must be inspected before execution, just like other local build recipes.
"""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
from pathlib import Path

from research_harness.experiments.package import HARBOR_VERSION, files, load_prepared
from research_harness.util import timestamp, write_json


def collect_results(prepared: Path, output: Path, record: dict) -> dict:
    expected = {
        (str((prepared / "inputs/tasks" / task).resolve()), agent): reward
        for task in record["tasks"]
        for agent, reward in (("oracle", 1.0), ("nop", 0.0))
    }
    rows = []
    seen = set()
    errors = []
    result_paths = sorted((output / "jobs/controls").glob("*/result.json"))
    for path in result_paths:
        result = {}
        try:
            result = json.loads(path.read_bytes())
            config = result["config"]
            key = (str(Path(config["task"]["path"]).resolve()), config["agent"]["name"])
            if key not in expected or key in seen:
                raise ValueError("Unexpected or duplicate task/control result")
            seen.add(key)
            rewards = (result.get("verifier_result") or {}).get("rewards")
            value = rewards.get("reward") if isinstance(rewards, dict) else None
            reward_file = path.parent / "verifier/reward.txt"
            if not reward_file.is_file():
                raise ValueError("Missing raw verifier reward.txt")
            raw_reward = float(reward_file.read_text().strip())
            valid_reward = (
                type(value) in (int, float)
                and math.isfinite(value)
                and value == raw_reward
                and value == expected[key]
            )
            passed = (
                valid_reward
                and not result.get("exception_info")
                and bool(result.get("finished_at"))
                and result.get("verifier_environment_mode") == "separate"
                and not config.get("verifier", {}).get("disable", False)
                and config["environment"]["type"] == "docker"
                and config["agent"].get("model_name") is None
                and config["agent"].get("import_path") is None
            )
            rows.append(
                {
                    "task": result["task_name"],
                    "control": key[1],
                    "expected_reward": expected[key],
                    "reward": value,
                    "passed": passed,
                    "exception": result.get("exception_info"),
                    "result": path.relative_to(output).as_posix(),
                    "verifier_environment_mode": result.get("verifier_environment_mode"),
                }
            )
        except (KeyError, TypeError, ValueError, OSError) as exc:
            errors.append(
                {
                    "result": path.relative_to(output).as_posix(),
                    "error": str(exc),
                    "exception": result.get("exception_info") if isinstance(result, dict) else None,
                }
            )
    return {
        "controls_passed": (
            seen == set(expected) and not errors and all(row["passed"] for row in rows)
        ),
        "expected_trials": len(expected),
        "observed_trials": len(result_paths),
        "trials": rows,
        "errors": errors,
    }


def check(prepared: Path, output: Path, harbor: Path, *, timeout: int = 1800) -> dict:
    prepared, output, harbor = prepared.resolve(), output.resolve(), harbor.absolute()
    record = load_prepared(prepared)
    if output.is_relative_to(prepared) or prepared.is_relative_to(output):
        raise ValueError("Check output must be separate from the prepared package")
    if not 1 <= timeout <= 86400:
        raise ValueError("Check timeout must be between 1 and 86400 seconds")
    # Preserve actual local Docker configuration, but do not forward provider keys.
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "DOCKER_HOST", "DOCKER_CONTEXT"}
    }
    version = subprocess.run(
        [str(harbor), "--version"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=True,
    ).stdout.strip()
    if version != HARBOR_VERSION:
        raise ValueError(f"Install Harbor {HARBOR_VERSION} in a separate Python 3.12 environment")
    output.mkdir(parents=True, exist_ok=False)
    config = {
        "job_name": "controls",
        "jobs_dir": str(output / "jobs"),
        "n_attempts": 1,
        "n_concurrent_trials": 1,
        "quiet": True,
        "retry": {"max_retries": 0},
        "environment": {"type": "docker", "delete": True, "force_build": True},
        "agents": [{"name": "oracle"}, {"name": "nop"}],
        "tasks": [{"path": str(prepared / "inputs/tasks" / task)} for task in record["tasks"]],
    }
    config_path = output / "job.json"
    write_json(config_path, config)
    command = [str(harbor), "run", "--config", str(config_path)]
    report = {
        "schema_version": 1,
        "kind": "benchmark_control_check",
        "status": "running",
        "started_at": timestamp(),
        "input_sha256": record["input_sha256"],
        "prepared": str(prepared),
        "harbor_version": version,
        "command": command,
        "curation_status": "pending_human_review",
        "research_baseline_measured": False,
    }
    write_json(output / "check.json", report)
    process = None
    try:
        with (output / "harbor.log").open("wb") as log:
            process = subprocess.Popen(
                command,
                cwd=output,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            report["pid"] = process.pid
            write_json(output / "check.json", report)
            report["exit_code"] = process.wait(timeout=timeout)
        load_prepared(prepared)
        report.update(collect_results(prepared, output, record))
        report["status"] = (
            "passed" if report["exit_code"] == 0 and report["controls_passed"] else "failed"
        )
    except (KeyboardInterrupt, subprocess.TimeoutExpired) as exc:
        report["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "timed_out"
        if process is not None and process.poll() is None:
            # Let Harbor tear down its containers. A forced process stop is not
            # proof that Docker resources were cleaned up.
            os.killpg(process.pid, signal.SIGINT)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        report["cleanup_status"] = "inspect_harbor_log_and_docker_resources"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report["status"] = "error"
        report["error"] = str(exc)
    finally:
        report["finished_at"] = timestamp()
        write_json(output / "check.json", report)
        # Raw outputs are retained even when the process or evaluator failed.
        write_json(
            output / "files.json", {k: v for k, v in files(output).items() if k != "files.json"}
        )
    return report


def status(output: Path) -> dict:
    report = json.loads((output / "check.json").read_bytes())
    if report["status"] == "running":
        # A receipt alone does not prove liveness after a controller interruption.
        return {**report, "liveness": "not_checked", "retry": "inspect existing run; do not replay"}
    expected = json.loads((output / "files.json").read_bytes())
    actual = {k: v for k, v in files(output).items() if k != "files.json"}
    if expected != actual:
        raise ValueError("Check artifacts changed or are missing")
    return report
