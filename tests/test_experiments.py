from __future__ import annotations

import json
import subprocess
import sys

import pytest

from research_harness.cli import main
from research_harness.experiments.checks import check, collect_results, status
from research_harness.experiments.package import HARBOR_VERSION, load_prepared, prepare


def save(path, value):
    path.write_text(json.dumps(value))


@pytest.fixture
def proposal(tmp_path):
    checkout = tmp_path / "benchmark"
    task = checkout / "task-a"
    for name in ("environment", "solution", "tests"):
        (task / name).mkdir(parents=True)
    (checkout / "LICENSE").write_text("Test license\n")
    (task / "instruction.md").write_text("Produce /app/result.\n")
    (task / "solution/solve.sh").write_text("echo result > /app/result\n")
    (task / "tests/test.sh").write_text("test -f /app/result\n")
    (task / "environment/Dockerfile").write_text("FROM python:3.13\n")
    (task / "tests/Dockerfile").write_text("FROM python:3.13\n")
    (task / "task.toml").write_text(
        'artifacts = ["/app/result"]\n'
        '[task]\nname = "test/task-a"\n'
        '[verifier]\nenvironment_mode = "separate"\n'
        "[environment]\ncpus = 1\nmemory_mb = 256\n"
    )

    def git(*args):
        return subprocess.check_output(["git", "-C", str(checkout), *args], text=True).strip()

    git("init", "-q")
    git("remote", "add", "origin", "https://github.com/example/benchmark.git")
    git("add", ".")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    inputs = tmp_path / "proposal"
    inputs.mkdir()
    (inputs / "plan.md").write_text("Proposed experiment. Human review pending.\n")
    manifest = inputs / "experiment.json"
    spec = {
        "id": "test-experiment",
        "question": "Does the implementation solve the task?",
        "plan": "plan.md",
        "benchmark_repository": "https://github.com/example/benchmark",
        "benchmark_revision": git("rev-parse", "HEAD"),
        "tasks": [{"path": "task-a"}],
    }
    save(manifest, spec)
    return manifest, checkout, tmp_path / "prepared", spec


def test_prepare_retains_original_and_overlay_without_human_acceptance(proposal):
    manifest, checkout, output, spec = proposal
    overlay = manifest.parent / "overlay"
    overlay.mkdir()
    (overlay / "instruction.md").write_text("Revised proposed instruction.\n")
    spec["tasks"][0]["overlay"] = "overlay"
    save(manifest, spec)
    result = prepare(manifest, checkout, output)
    assert result["curation_status"] == "pending_human_review"
    assert (output / "inputs/upstream/task-a/instruction.md").read_bytes() == (
        checkout / "task-a/instruction.md"
    ).read_bytes()
    assert (output / "inputs/tasks/task-a/instruction.md").read_bytes() == (
        overlay / "instruction.md"
    ).read_bytes()
    assert load_prepared(output)["input_sha256"] == result["input_sha256"]
    # Self-assertion in metadata cannot promote a proposal to human acceptance.
    result["curation_status"] = "reviewed"
    save(output / "prepared.json", result)
    assert load_prepared(output)["curation_status"] == "pending_human_review"


@pytest.mark.parametrize("change", ["revision", "dirty", "ignored", "symlink", "license"])
def test_prepare_rejects_unpinned_or_escaping_inputs(proposal, change):
    manifest, checkout, output, spec = proposal
    if change == "revision":
        spec["benchmark_revision"] = "0" * 40
        save(manifest, spec)
    elif change == "dirty":
        (checkout / "task-a/instruction.md").write_text("Changed")
    elif change == "ignored":
        (checkout / ".git/info/exclude").write_text("ignored.py\n")
        (checkout / "task-a/ignored.py").write_text("unrecorded code")
    elif change == "symlink":
        (manifest.parent / "overlay").symlink_to(checkout / "task-a", target_is_directory=True)
        spec["tasks"][0]["overlay"] = "overlay"
        save(manifest, spec)
    else:
        (checkout / "LICENSE").write_text("Changed license")
    with pytest.raises(ValueError):
        prepare(manifest, checkout, output)
    assert not output.exists()


@pytest.mark.parametrize("change", ["content", "added_task", "settings"])
def test_changed_prepared_inputs_cannot_be_used(proposal, change):
    manifest, checkout, output, _ = proposal
    record = prepare(manifest, checkout, output)
    if change == "content":
        (output / "inputs/tasks/task-a/tests/test.sh").write_text("echo always-pass")
    elif change == "added_task":
        record["tasks"]["../../elsewhere"] = record["tasks"]["task-a"]
        save(output / "prepared.json", record)
    else:
        record["tasks"]["task-a"]["environment"]["cpus"] = 8
        save(output / "prepared.json", record)
    with pytest.raises(ValueError):
        load_prepared(output)


def trial(output, prepared, agent, *, reward=None, error=None, mode="separate"):
    path = output / "jobs/controls" / agent
    (path / "verifier").mkdir(parents=True)
    value = (1 if agent == "oracle" else 0) if reward is None else reward
    result = {
        "task_name": "task-a",
        "config": {
            "task": {"path": str(prepared / "inputs/tasks/task-a")},
            "agent": {"name": agent},
            "environment": {"type": "docker"},
            "verifier": {"disable": False},
        },
        "finished_at": "2026-09-13T00:00:00Z",
        "verifier_environment_mode": mode,
        "verifier_result": {"rewards": {"reward": value}},
        "exception_info": error,
    }
    save(path / "result.json", result)
    (path / "verifier/reward.txt").write_text(str(value))
    return path


@pytest.mark.parametrize(
    "failure", ["missing", "always_pass", "exception", "shared", "nan", "raw_mismatch"]
)
def test_controls_reject_misleading_or_incomplete_results(proposal, tmp_path, failure):
    manifest, checkout, prepared, _ = proposal
    record = prepare(manifest, checkout, prepared)
    output = tmp_path / "results"
    positive = trial(output, prepared, "oracle")
    if failure != "missing":
        trial(
            output,
            prepared,
            "nop",
            reward=1 if failure == "always_pass" else float("nan") if failure == "nan" else 0,
            error={"exception_type": "EnvironmentError"} if failure == "exception" else None,
            mode="shared" if failure == "shared" else "separate",
        )
    if failure == "raw_mismatch":
        (positive / "verifier/reward.txt").write_text("0")
    assert not collect_results(prepared, output, record)["controls_passed"]


def test_positive_and_negative_controls_are_not_model_performance(proposal, tmp_path):
    manifest, checkout, prepared, _ = proposal
    record = prepare(manifest, checkout, prepared)
    output = tmp_path / "results"
    trial(output, prepared, "oracle")
    trial(output, prepared, "nop")
    result = collect_results(prepared, output, record)
    assert result["controls_passed"]
    assert [row["reward"] for row in result["trials"]] == [0, 1]
    assert result["observed_trials"] == 2


def runtime_script(tmp_path, body, *, version=HARBOR_VERSION):
    path = tmp_path / "harbor"
    path.write_text(
        f"#!{sys.executable}\nimport sys, time, os\n"
        f"if '--version' in sys.argv:\n    print({version!r})\n    sys.exit(0)\n"
        "assert 'OPENAI_API_KEY' not in os.environ\n" + body
    )
    path.chmod(0o755)
    return path


def test_zero_exit_without_results_fails_and_keeps_logs(proposal, tmp_path, monkeypatch):
    manifest, checkout, prepared, _ = proposal
    prepare(manifest, checkout, prepared)
    harbor = runtime_script(tmp_path, "print('No trials ran')\n")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-control-runtime")
    output = tmp_path / "check"
    result = check(prepared, output, harbor)
    assert result["exit_code"] == 0
    assert result["status"] == "failed"
    assert result["curation_status"] == "pending_human_review"
    assert result["research_baseline_measured"] is False
    assert (output / "harbor.log").read_text().strip() == "No trials ran"
    assert status(output)["status"] == "failed"
    with pytest.raises(FileExistsError):
        check(prepared, output, harbor)
    (output / "harbor.log").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        status(output)


def test_runtime_timeout_is_retained_without_claiming_cleanup(proposal, tmp_path):
    manifest, checkout, prepared, _ = proposal
    prepare(manifest, checkout, prepared)
    harbor = runtime_script(tmp_path, "time.sleep(60)\n")
    output = tmp_path / "check"
    result = check(prepared, output, harbor, timeout=1)
    assert result["status"] == "timed_out"
    assert "inspect" in result["cleanup_status"]
    assert status(output)["status"] == "timed_out"


def test_wrong_runtime_version_cannot_start_check(proposal, tmp_path):
    manifest, checkout, prepared, _ = proposal
    prepare(manifest, checkout, prepared)
    harbor = runtime_script(tmp_path, "raise AssertionError('must not execute')\n", version="other")
    output = tmp_path / "check"
    with pytest.raises(ValueError, match="Install Harbor"):
        check(prepared, output, harbor)
    assert not output.exists()


def test_cli_prepares_without_launching_a_model(proposal, capsys):
    manifest, checkout, prepared, _ = proposal
    assert (
        main(
            [
                "experiment",
                "prepare",
                str(manifest),
                "--checkout",
                str(checkout),
                "--out",
                str(prepared),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["curation_status"] == "pending_human_review"
    assert result["tasks"]["task-a"]["verifier"]["environment_mode"] == "separate"
