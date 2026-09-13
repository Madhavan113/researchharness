from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from research_harness.cli import main
from research_harness.experiments.checks import check, collect_results, status
from research_harness.experiments.curation import CuratorStore
from research_harness.experiments.package import HARBOR_VERSION, files, load_prepared, prepare


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


def inventory(output):
    save(
        output / "files.json",
        {key: value for key, value in files(output).items() if key != "files.json"},
    )


@pytest.fixture
def reviewable(proposal, tmp_path):
    """Synthetic control receipts: test curation without claiming runtime evidence."""
    manifest, checkout, prepared, _ = proposal
    package = prepare(manifest, checkout, prepared)
    output = tmp_path / "check"
    trial(output, prepared, "oracle")
    trial(output, prepared, "nop")
    save(
        output / "check.json",
        {
            "kind": "benchmark_control_check",
            "status": "passed",
            "exit_code": 0,
            "input_sha256": package["input_sha256"],
            "prepared": str(prepared),
            "harbor_version": HARBOR_VERSION,
            **collect_results(prepared, output, package),
        },
    )
    inventory(output)
    return prepared, output, CuratorStore(tmp_path / "operator/reviews.sqlite3")


def decide(reviewable, decision="accept", **overrides):
    prepared, output, curator = reviewable
    view = curator.inspect(prepared, output)
    args = {
        "decision": decision,
        "subject": view["subject_sha256"],
        "after": view["head"],
        "reason": "Synthetic review for a test, not actual human acceptance.",
        **overrides,
    }
    return curator.decide(prepared, output, **args)


def test_inspection_is_read_only_and_acceptance_has_limited_scope(reviewable, monkeypatch):
    prepared, output, curator = reviewable
    view = curator.inspect(prepared, output)
    assert view["curation_status"] == "pending_review"
    assert view["head"] == "none"
    assert view["controls_passed"]
    assert "Human review pending" in view["plan"]
    assert view["tasks"]["task-a"]["environment"]["memory_mb"] == 256
    assert not curator.path.parent.exists()
    with pytest.raises(ValueError, match="not accepted"):
        curator.require_accepted(prepared, output)
    monkeypatch.setenv("USER", "not-a-curator-identity")
    accepted = decide(reviewable)
    assert accepted["scope"] == "benchmark_package"
    assert accepted["actor"]["kind"] == "local_os_account"
    assert accepted["actor"]["human_presence_verified"] is False
    assert accepted["subject_sha256"] == view["subject_sha256"]
    current = curator.require_accepted(prepared, output)
    assert current["head"] == accepted["id"]
    assert current["curation_status"] == "accepted"
    assert current["execution_authorized"] is False
    assert current["finding_accepted"] is False
    assert load_prepared(prepared)["curation_status"] == "pending_human_review"
    assert status(output)["status"] == "passed"


def test_review_requires_exact_subject_and_reason(reviewable):
    _, _, curator = reviewable
    with pytest.raises(ValueError, match="subject changed"):
        decide(reviewable, subject="0" * 64)
    with pytest.raises(ValueError, match="reason"):
        decide(reviewable, reason="  ")
    assert not curator.path.exists()


@pytest.mark.parametrize("location", ["prepared", "check"])
def test_curator_store_cannot_live_in_experiment_inputs_or_outputs(reviewable, location):
    prepared, output, _ = reviewable
    path = prepared if location == "prepared" else output
    curator = CuratorStore(path / "curator.sqlite3")
    with pytest.raises(ValueError, match="outside"):
        curator.inspect(prepared, output)
    assert not curator.path.exists()


@pytest.mark.parametrize("change", ["plan", "reward", "missing", "unrelated", "unfinished"])
def test_accepted_review_cannot_override_missing_or_changed_evidence(reviewable, change):
    prepared, output, curator = reviewable
    accepted = decide(reviewable)
    if change == "plan":
        (prepared / "inputs/plan.md").write_text("An entirely different experiment")
    elif change == "reward":
        (output / "jobs/controls/nop/verifier/reward.txt").write_text("1")
    elif change == "missing":
        (output / "jobs/controls/oracle/result.json").unlink()
    else:
        report = json.loads((output / "check.json").read_bytes())
        if change == "unrelated":
            report["input_sha256"] = "f" * 64
        else:
            report["status"] = "running"
        save(output / "check.json", report)
        inventory(output)
    with pytest.raises(ValueError):
        curator.require_accepted(prepared, output)
    # An operator can withdraw a prior acceptance even when evidence is unreadable.
    revoked = curator.withdraw("test-experiment", after=accepted["id"], reason="Evidence changed")
    assert revoked["supersedes"] == accepted["id"]
    assert [row["decision"] for row in curator.history("test-experiment")] == ["accept", "withdraw"]


def test_false_pass_summary_cannot_be_accepted_even_with_new_artifact_hashes(reviewable):
    _, output, curator = reviewable
    path = output / "jobs/controls/nop/result.json"
    result = json.loads(path.read_bytes())
    result["verifier_result"]["rewards"]["reward"] = 1
    save(path, result)
    (path.parent / "verifier/reward.txt").write_text("1")
    # A rehashed but incorrect summary must not substitute for checking raw results.
    inventory(output)
    with pytest.raises(ValueError, match="positive and negative"):
        decide(reviewable)
    rejected = decide(reviewable, "reject")
    assert rejected["decision"] == "reject"
    assert curator.history("test-experiment") == [rejected]


def test_withdrawal_preserves_history_and_requires_a_new_decision_to_restore(reviewable):
    prepared, output, curator = reviewable
    accepted = decide(reviewable)
    revoked = curator.withdraw("test-experiment", after=accepted["id"], reason="Reconsidering task")
    assert curator.inspect(prepared, output)["curation_status"] == "withdrawn"
    with pytest.raises(ValueError, match="not accepted"):
        curator.require_accepted(prepared, output)
    with pytest.raises(ValueError, match="history changed"):
        decide(reviewable, after=accepted["id"])
    restored = decide(reviewable)
    assert restored["supersedes"] == revoked["id"]
    assert curator.require_accepted(prepared, output)["head"] == restored["id"]
    assert curator.history("test-experiment") == [accepted, revoked, restored]


def test_different_check_needs_review_and_cannot_resurrect_an_older_acceptance(
    reviewable, tmp_path
):
    import shutil

    prepared, output, curator = reviewable
    accepted = decide(reviewable)
    other = tmp_path / "other-check"
    shutil.copytree(output, other)
    # Retain a distinguishable independent check receipt; original task paths stay bound.
    report = json.loads((other / "check.json").read_bytes())
    report["started_at"] = "2026-09-13T12:00:00Z"
    save(other / "check.json", report)
    inventory(other)
    assert curator.inspect(prepared, other)["curation_status"] == "pending_review"
    with pytest.raises(ValueError, match="not accepted"):
        curator.require_accepted(prepared, other)
    rejected = decide((prepared, other, curator), "reject")
    assert rejected["supersedes"] == accepted["id"]
    assert curator.inspect(prepared, other)["curation_status"] == "rejected"
    with pytest.raises(ValueError, match="not accepted"):
        curator.require_accepted(prepared, output)


def test_concurrent_reviews_cannot_overwrite_an_unseen_decision(reviewable):
    _, _, curator = reviewable

    def attempt():
        try:
            return decide(reviewable, after="none")
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert len([result for result in results if isinstance(result, dict)]) == 1
    assert len([result for result in results if "history changed" in str(result)]) == 1
    assert len(curator.history("test-experiment")) == 1


def test_withdraw_unknown_experiment_does_not_break_the_empty_store(reviewable):
    prepared, output, curator = reviewable
    with pytest.raises(ValueError, match="no current acceptance"):
        curator.withdraw("test-experiment", after="none", reason="No review exists")
    assert curator.history("test-experiment") == []
    assert curator.inspect(prepared, output)["head"] == "none"


def test_interrupted_initialization_is_readable_and_can_be_completed(reviewable):
    prepared, output, curator = reviewable
    curator.path.parent.mkdir()
    curator.path.touch()
    assert curator.inspect(prepared, output)["curation_status"] == "pending_review"
    accepted = decide(reviewable)
    assert curator.require_accepted(prepared, output)["head"] == accepted["id"]


def test_cli_inspect_accept_history_and_withdraw(reviewable, capsys):
    prepared, output, curator = reviewable
    command = [
        "experiment",
        "review",
        str(prepared),
        "--check",
        str(output),
        "--store",
        str(curator.path),
    ]
    assert main(command) == 0
    view = json.loads(capsys.readouterr().out)
    assert main([*command, "--decision", "accept"]) == 1
    assert "requires" in capsys.readouterr().err
    assert (
        main(
            [
                *command,
                "--decision",
                "accept",
                "--subject",
                view["subject_sha256"],
                "--after",
                view["head"],
                "--reason",
                "Synthetic CLI acceptance test",
            ]
        )
        == 0
    )
    accepted = json.loads(capsys.readouterr().out)
    assert main(["experiment", "reviews", "test-experiment", "--store", str(curator.path)]) == 0
    assert json.loads(capsys.readouterr().out)["reviews"] == [accepted]
    assert (
        main(
            [
                "experiment",
                "withdraw",
                "test-experiment",
                "--store",
                str(curator.path),
                "--after",
                accepted["id"],
                "--reason",
                "Synthetic CLI withdrawal test",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["decision"] == "withdraw"
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out)["curation_status"] == "withdrawn"
