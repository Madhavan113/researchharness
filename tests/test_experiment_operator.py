import json

import httpx
import pytest
from test_experiments import decide
from test_experiments import proposal as proposal
from test_experiments import reviewable as reviewable

from research_harness.cli import main
from research_harness.evaluation.budget import AuthorizationRecord, BudgetLedger, RateCard
from research_harness.evaluation.dispatch_budget import STANDARD_MODEL
from research_harness.experiments import execution, operator
from research_harness.util import digest, write_json


@pytest.fixture
def run_inputs(reviewable, tmp_path):
    prepared, checked, curator = reviewable
    rates = RateCard(
        model=STANDARD_MODEL,
        snapshot=STANDARD_MODEL,
        input_usd_per_million="0.75",
        output_usd_per_million="4.50",
        max_input_tokens_per_request=400000,
        price_source_url="https://fixture.invalid/rates",
        price_as_of="2026-09-13",
    )
    ledger = BudgetLedger.prepare_registered(
        tmp_path / "operator/budget.json", rates=rates, ceiling_usd="10"
    )
    settings = tmp_path / "settings.json"
    write_json(settings, {"service_tier": "default", "max_rounds": 12})
    candidate = tmp_path / "agent.py"
    candidate.write_text("raise AssertionError('Never execute candidate code on the host')\n")
    arguments = [prepared, checked, curator.path, ledger.path, settings, candidate]
    options = {
        "provider": "openai-standard",
        "output": tmp_path / "run",
        "harbor": tmp_path / "harbor",
        "omnigent_python": tmp_path / "omnigent-python",
    }
    return arguments, options, ledger


def cli_run(arguments, options, *extra):
    prepared, checked, store, ledger, settings, candidate = arguments
    return main(
        [
            "experiment",
            "run",
            str(prepared),
            "--check",
            str(checked),
            "--store",
            str(store),
            "--budget",
            str(ledger),
            "--settings",
            str(settings),
            "--candidate",
            str(candidate),
            "--provider",
            options["provider"],
            "--out",
            str(options["output"]),
            "--harbor",
            str(options["harbor"]),
            "--omnigent-python",
            str(options["omnigent_python"]),
            *extra,
        ]
    )


def test_cli_preview_does_not_execute_or_approve(run_inputs, monkeypatch, capsys):
    arguments, options, ledger = run_inputs
    monkeypatch.setattr(execution, "run", lambda *a, **k: pytest.fail("Must not execute"))
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-secret")
    before = ledger.path.read_bytes()
    assert cli_run(arguments, options, "--dry-run") == 0
    raw = capsys.readouterr().out
    result = json.loads(raw)
    assert "unit-test-secret" not in raw
    assert result["execution_started"] is False
    assert result["runtime_checked"] is False
    assert result["candidate_sha256"] == digest(arguments[-1].read_bytes())
    assert result["prerequisites"] == {
        "benchmark_accepted": False,
        "spending_approval_recorded": False,
        "can_reserve_one_request": True,
    }
    assert ledger.path.read_bytes() == before
    assert not options["output"].exists()


@pytest.mark.parametrize("missing", ["review", "approval", "key", "funds"])
def test_cli_refuses_missing_prerequisites_before_runtime(
    run_inputs, reviewable, monkeypatch, capsys, missing
):
    arguments, options, ledger = run_inputs
    if missing == "funds":
        ledger = BudgetLedger.prepare_registered(
            ledger.path.with_name("empty.json"), rates=ledger.rates, ceiling_usd="0"
        )
        arguments[3] = ledger.path
    monkeypatch.setattr(execution, "run", lambda *a, **k: pytest.fail("Must not execute"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if missing != "review":
        decide(reviewable)
    if missing in {"key", "funds"}:
        ledger.record_authorization(
            AuthorizationRecord(status="approved", reference="Unit test only")
        )
    assert cli_run(arguments, options) == 1
    error = json.loads(capsys.readouterr().err)["error"]
    expected = {
        "review": "benchmark_accepted",
        "approval": "spending_approval_recorded",
        "key": "OPENAI_API_KEY",
        "funds": "can_reserve_one_request",
    }
    assert expected[missing] in error
    assert not options["output"].exists()
    assert not ledger.snapshot()["reservations"]


def test_cli_binds_the_existing_budget_without_disclosing_key(
    run_inputs, reviewable, monkeypatch, capsys
):
    arguments, options, ledger = run_inputs
    review = decide(reviewable)
    ledger.record_authorization(AuthorizationRecord(status="approved", reference="Unit test only"))
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-secret")
    calls = []

    def dispatch(prepared, checked, curator, budget, **kwargs):
        calls.append(budget.binding.execution_id)
        assert budget.ledger.path == ledger.path
        assert budget.binding.task_sha256 == review["input_sha256"]
        assert budget.binding.runtime == "omnigent-experiment"
        assert budget.binding.phase == "workflow"
        assert budget.settings.max_rounds == 12
        assert kwargs["candidate"] == arguments[-1]
        assert kwargs["provider_api_key"] == "unit-test-secret"
        return {"status": "completed", "fixture_stub": True}

    monkeypatch.setattr(execution, "run", dispatch)
    assert cli_run(arguments, options) == 0
    assert len(calls) == 1
    assert "unit-test-secret" not in capsys.readouterr().out


def test_fixture_api_rejects_a_network_client(run_inputs, reviewable, monkeypatch):
    arguments, options, _ = run_inputs
    decide(reviewable)
    options["provider"] = "fixture"
    monkeypatch.setattr(execution, "run", lambda *a, **k: pytest.fail("Must not execute"))
    with httpx.Client() as client, pytest.raises(ValueError, match="MockTransport"):
        operator.run(*arguments, **options, client=client)
    assert not options["output"].exists()


def test_preview_cannot_recreate_a_missing_budget(run_inputs):
    arguments, options, ledger = run_inputs
    ledger.path.unlink()
    with pytest.raises(OSError):
        operator.run(*arguments, **options, dry_run=True)
    assert not ledger.path.exists()


def test_budget_command_retains_balance_and_refuses_lost_ledger(run_inputs, tmp_path, capsys):
    _, _, ledger = run_inputs
    rates_path = tmp_path / "rates.json"
    write_json(rates_path, ledger.rates.model_dump(mode="json"))
    ledger.reserve("retained", request_sha256="a" * 64, max_output_tokens=6000)
    before = ledger.path.read_bytes()
    args = [
        "experiment",
        "budget",
        "--rates",
        str(rates_path),
        "--out",
        str(ledger.path),
        "--ceiling-usd",
        "10",
    ]
    assert main(args) == 0
    assert ledger.path.read_bytes() == before
    assert json.loads(capsys.readouterr().out)["accounting"]["committed_nanodollars"] > 0
    ledger.path.unlink()
    assert main(args) == 1
    assert "never recreate spent funds" in capsys.readouterr().err
    assert not ledger.path.exists()


def test_budget_authorization_changes_only_when_explicit(run_inputs, tmp_path):
    _, _, ledger = run_inputs
    rates = tmp_path / "rates.json"
    authorization = tmp_path / "authorization.json"
    write_json(rates, ledger.rates.model_dump(mode="json"))
    write_json(authorization, {"status": "approved", "reference": "Unit test only"})
    operator.prepare_budget(rates, ledger.path, "10", authorization)
    operator.prepare_budget(rates, ledger.path, "10", None)
    assert ledger.snapshot()["authorization"]["status"] == "approved"
    write_json(authorization, {"status": "draft"})
    operator.prepare_budget(rates, ledger.path, "10", authorization)
    snapshot = ledger.snapshot()
    assert snapshot["authorization"]["status"] == "draft"
    assert len(snapshot["authorization_history"]) == 3
