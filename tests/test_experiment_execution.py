from __future__ import annotations

import json
import os
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest
from test_experiments import decide
from test_experiments import proposal as proposal
from test_experiments import reviewable as reviewable

from research_harness.evaluation.budget import BudgetLedger, RateCard
from research_harness.evaluation.dispatch_budget import DispatchBudget, DispatchPolicy
from research_harness.execution import DiscoverySettings, GatewayBinding
from research_harness.experiments import execution
from research_harness.experiments.package import load_prepared
from research_harness.util import write_json


def budget_for(prepared, tmp_path, **binding):
    package = load_prepared(prepared)
    rates = RateCard(
        model="fixture-model",
        snapshot="fixture-model",
        input_usd_per_million="0",
        output_usd_per_million="0",
        max_input_tokens_per_request=10000,
        price_source_url="https://fixture.invalid/prices",
        price_as_of="2026-09-13",
    )
    return DispatchBudget(
        BudgetLedger(tmp_path / "operator/budget.json", rates=rates, ceiling_usd="0"),
        policy=DispatchPolicy(
            mode="fixture", upstream_base_url="https://fixture.invalid/v1", model="fixture-model"
        ),
        settings=DiscoverySettings(service_tier="default"),
        binding=GatewayBinding(
            **{
                "execution_id": uuid4().hex,
                "case_id": package["experiment"]["id"],
                "runtime": "omnigent-experiment",
                "phase": "workflow",
                "task_sha256": package["input_sha256"],
                **binding,
            }
        ),
    )


def arguments(tmp_path):
    return {
        "output": tmp_path / "execution",
        "harbor": tmp_path / "harbor",
        "omnigent_python": tmp_path / "omnigent-python",
    }


def test_execution_requires_current_curation_before_any_runtime_or_provider(
    reviewable, tmp_path, monkeypatch
):
    prepared, checked, curator = reviewable
    budget = budget_for(prepared, tmp_path)
    monkeypatch.setattr(execution, "check_runtime", lambda _: pytest.fail("Runtime must not start"))
    with pytest.raises(ValueError, match="not accepted"):
        execution.run(prepared, checked, curator, budget, **arguments(tmp_path))
    assert not (tmp_path / "execution").exists()
    accepted = decide(reviewable)
    curator.withdraw("test-experiment", after=accepted["id"], reason="Fixture withdrawn")
    with pytest.raises(ValueError, match="withdrawn"):
        execution.run(prepared, checked, curator, budget, **arguments(tmp_path))


@pytest.mark.parametrize(
    "binding",
    [{"task_sha256": "0" * 64}, {"case_id": "other"}, {"phase": "discovery"}, {"runtime": "other"}],
)
def test_execution_budget_must_belong_to_the_curated_experiment(
    reviewable, tmp_path, monkeypatch, binding
):
    prepared, checked, curator = reviewable
    decide(reviewable)
    budget = budget_for(prepared, tmp_path, **binding)
    monkeypatch.setattr(execution, "check_runtime", lambda _: pytest.fail("Runtime must not start"))
    with pytest.raises(ValueError, match="not bound"):
        execution.run(prepared, checked, curator, budget, **arguments(tmp_path))
    assert not (tmp_path / "execution").exists()


def test_fixture_execution_cannot_use_a_network_client(reviewable, tmp_path, monkeypatch):
    from types import SimpleNamespace

    prepared, checked, curator = reviewable
    decide(reviewable)
    budget = budget_for(prepared, tmp_path)
    monkeypatch.setattr(execution, "check_runtime", lambda _: {"commit": "fixture-only"})
    monkeypatch.setattr(
        execution.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout="0.23.0")
    )
    monkeypatch.setattr(
        execution.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Harbor must not start"),
    )
    with httpx.Client(trust_env=False) as client:
        result = execution.run(
            prepared, checked, curator, budget, client=client, **arguments(tmp_path)
        )
    assert result["status"] == "error"
    assert "MockTransport" in result["error"]
    assert json.loads((tmp_path / "execution/execution.json").read_bytes())["status"] == "error"
    assert budget.ledger.snapshot()["reservations"] == {}
    assert execution.status(tmp_path / "execution")["status"] == "error"


def result_fixture(prepared, output, *, reward=0):
    trial_id = str(uuid4())
    trial = output / "jobs/experiment/task-a__fixture"
    record = {
        "id": trial_id,
        "task_name": "task-a",
        "finished_at": "2026-09-13T00:00:00Z",
        "exception_info": None,
        "verifier_environment_mode": "separate",
        "verifier_result": {"rewards": {"reward": reward}},
        "config": {
            "task": {"path": str(prepared / "inputs/tasks/task-a")},
            "agent": {"import_path": execution.AGENT, "model_name": "fixture-model"},
            "environment": {"import_path": execution.ENVIRONMENT},
        },
    }
    write_json(trial / "result.json", record)
    (trial / "verifier").mkdir()
    (trial / "verifier/reward.txt").write_text(str(reward))
    write_json(
        output / "controller" / trial_id / "runtime/runtime.json",
        {
            "status": "completed",
            "session_id": "fixture-session",
            "delegation_observed": True,
        },
    )
    write_json(
        output / "controller" / trial_id / "runtime/tool-policy.json",
        {"submitted": execution.tool_policy()},
    )
    for role in ("env", "verifier__trial"):
        write_json(
            output / "controller" / f"environment-{role}.json",
            {
                "session_id": trial.name + "__" + role,
                "context_id": trial_id,
                "mounts": [],
                "privileged": False,
                "network_mode": "none",
                "memory_bytes": 1024,
                "nano_cpus": 1_000_000_000,
            },
        )
    return trial


def test_completed_execution_can_be_a_failed_task_without_claiming_success(reviewable, tmp_path):
    prepared, _, _ = reviewable
    output = tmp_path / "execution"
    result_fixture(prepared, output, reward=0)
    rows = execution.collect(prepared, output, "fixture-model")
    assert rows[0]["verified"] is True
    assert rows[0]["reward"] == 0
    assert rows[0]["runtime_session_id"] == "fixture-session"


def test_program_result_requires_exact_source_success_and_stopped_container(reviewable, tmp_path):
    prepared, _, _ = reviewable
    output = tmp_path / "execution"
    trial = result_fixture(prepared, output)
    trial_id = json.loads((trial / "result.json").read_bytes())["id"]
    controller = output / "controller" / trial_id
    write_json(
        controller / "runtime/tool-policy.json", {"submitted": execution.tool_policy(program=True)}
    )
    record = {
        "status": "completed",
        "exit_code": 0,
        "source_sha256": "a" * 64,
        "container_stopped": True,
        "context_id": trial_id,
        "environment_session_id": trial.name + "__env",
    }
    path = controller / "program/program.json"
    assert not execution.collect(prepared, output, "fixture-model", "a" * 64)[0]["verified"]
    write_json(path, record)
    assert execution.collect(prepared, output, "fixture-model", "a" * 64)[0]["verified"]
    for mutation in (
        {"source_sha256": "b" * 64},
        {"exit_code": 1},
        {"status": "error"},
        {"container_stopped": False},
        {"context_id": "other"},
    ):
        write_json(path, {**record, **mutation})
        assert not execution.collect(prepared, output, "fixture-model", "a" * 64)[0]["verified"]


@pytest.mark.parametrize(
    "change",
    [
        "reward",
        "model",
        "shared",
        "mount",
        "privileged",
        "missing_attestation",
        "policy",
        "delegation",
        "network",
    ],
)
def test_result_collection_requires_score_and_isolation_evidence(reviewable, tmp_path, change):
    prepared, _, _ = reviewable
    output = tmp_path / "execution"
    trial = result_fixture(prepared, output, reward=1)
    if change == "reward":
        (trial / "verifier/reward.txt").write_text("0")
    elif change in {"model", "shared"}:
        value = json.loads((trial / "result.json").read_bytes())
        if change == "model":
            value["config"]["agent"]["model_name"] = "unreviewed-model"
        else:
            value["verifier_environment_mode"] = "shared"
        write_json(trial / "result.json", value)
    elif change in {"policy", "delegation"}:
        runtime = next((output / "controller").glob("*/runtime"))
        if change == "policy":
            (runtime / "tool-policy.json").unlink()
        else:
            value = json.loads((runtime / "runtime.json").read_bytes())
            value["delegation_observed"] = False
            write_json(runtime / "runtime.json", value)
    else:
        path = output / "controller/environment-env.json"
        if change == "missing_attestation":
            path.unlink()
        else:
            value = json.loads(path.read_bytes())
            if change == "network":
                value["network_mode"] = "bridge"
            else:
                value["mounts" if change == "mount" else "privileged"] = (
                    ["/host"] if change == "mount" else True
                )
            write_json(path, value)
    assert execution.collect(prepared, output, "fixture-model")[0]["verified"] is False


def test_frozen_source_executes_outside_checkout_without_changing_inventory(tmp_path):
    source = tmp_path / "source"
    recorded = execution.snapshot_source(source)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from research_harness.experiments.bundle import build_bundle; "
            "from pathlib import Path; build_bundle(Path('bundle'), name='fixture', model='fixture')",
        ],
        env={**os.environ, "PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=tmp_path,
        check=True,
        timeout=30,
    )
    assert execution.files(source) == recorded
    assert "research_harness/integrations/model_gateway.py" in recorded
    assert "uv.lock" in recorded


@pytest.mark.parametrize("change", ["alter", "remove", "symlink", "controller_symlink"])
def test_terminal_status_detects_changed_or_missing_evidence(tmp_path, change):
    output = tmp_path / "run"
    write_json(output / "execution.json", {"status": "completed", "trials": [{"reward": 0}]})
    record = output / "controller/trial/runtime/runtime.json"
    write_json(record, {"status": "completed"})
    write_json(output / "files.json", execution.inventory(output))
    assert execution.status(output)["trials"][0]["reward"] == 0
    if change == "alter":
        record.write_text("{}")
    elif change == "controller_symlink":
        # A top-level controller link must never be followed or exported.
        (output / "controller/private.json").symlink_to(record)
    else:
        record.unlink()
        if change == "symlink":
            record.symlink_to(tmp_path / "nonexistent")
    with pytest.raises(ValueError):
        execution.status(output)


def test_running_status_never_implies_liveness_or_replays_work(tmp_path):
    write_json(tmp_path / "execution.json", {"status": "running", "pid": 99999999})
    report = execution.status(tmp_path)
    assert report["liveness"] == "not_checked"
    assert "do not replay" in report["retry"]
