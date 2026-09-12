from __future__ import annotations

import json
import subprocess
import sys

import httpx
import pytest
from test_dispatch_budget import BINDING, MODEL, make_budget, payload

from research_harness.evaluation.budget import BudgetLedger, ProviderAccountingConfirmation
from research_harness.evaluation.dispatch_budget import DispatchBudget
from research_harness.evaluation.pilot import _check_ledger_witness
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.util import digest, write_json


def confirmation(provider_id="req_fixture_error", cost="0"):
    # Authored fixture only, never a real provider statement or permission.
    evidence = f"Fixture final USD charge confirmation for {provider_id}: {cost}."
    return ProviderAccountingConfirmation(
        provider_request_id=provider_id,
        confirmed_cost_usd=cost,
        currency="usd",
        final_charge_confirmed=True,
        reference="fixture-support-case-1",
        reviewed_by="fixture-operator",
        provider_evidence=evidence,
        provider_evidence_sha256=digest(evidence.encode("utf-8")),
    )


def error_archive(
    path,
    budget,
    *,
    status=500,
    provider_id="req_fixture_error",
    body=None,
    count=1,
    failure=None,
    content_type="application/json",
):
    sent = []

    def upstream(request):
        sent.append(request)
        if failure:
            raise failure
        headers = {"content-type": content_type}
        if provider_id is not None:
            headers["x-request-id"] = provider_id
        return httpx.Response(
            status,
            content=json.dumps(
                {"error": {"message": "Authored provider failure"}} if body is None else body
            ).encode(),
            headers=headers,
        )

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        with ResponsesGateway(
            path,
            model=MODEL,
            settings=budget.settings,
            binding=budget.binding,
            upstream_base_url=budget.policy.upstream_base_url,
            client=client,
            dispatch_budget=budget,
        ) as gateway:
            for _ in range(count):
                httpx.post(
                    gateway.base_url + "/responses",
                    json=payload(),
                    headers={"Authorization": "Bearer " + gateway.api_key},
                    timeout=5,
                )
    return path / "archive.json", sent


def retained_files(path):
    return {
        str(item.relative_to(path)): item.read_bytes() for item in path.rglob("*") if item.is_file()
    }


def record(budget, archive, proof=None):
    return budget.record_provider_accounting(
        archive, "request-0001", confirmation=proof or confirmation()
    )


@pytest.mark.parametrize("status", [429, 500])
@pytest.mark.parametrize("cost,charged", [("0", 0), ("0.000003", 3000), ("0.0000000001", 1)])
def test_reviewed_error_charge_settles_without_replay_or_invented_usage(
    tmp_path, status, cost, charged
):
    budget = make_budget(tmp_path)
    archive, sent = error_archive(tmp_path / "gateway", budget, status=status)
    sealed = retained_files(archive.parent)
    first = budget.reconcile_archive(archive)
    assert first["archive_valid"], first["errors"]
    assert first["accounting"]["held_nanodollars"] == 120000
    before = budget.ledger.snapshot()
    proof = confirmation(cost=cost)
    result = record(budget, archive, proof)
    assert result["status"] == "accounted"
    assert result["token_usage_known"] is False
    assert result["provider_authenticity_verified_by_software"] is False
    assert result["accounting"]["held_nanodollars"] == 0
    assert result["accounting"]["settled_nanodollars"] == charged
    row = result["operation"]
    assert row["settlement"]["confirmation"] == proof.model_dump(mode="json")
    assert "input_tokens" not in row["settlement"]
    after = budget.ledger.snapshot()
    _check_ledger_witness(before, after)
    _check_ledger_witness(after, after)
    saved = budget.ledger.path.read_bytes()
    assert record(budget, archive, proof) == result
    assert budget.reconcile_archive(archive)["status"] == "reconciled"
    assert budget.archive_consistency(archive)["status"] == "consistent"
    assert BudgetLedger.open_existing(budget.ledger.path).snapshot() == after
    assert budget.ledger.path.read_bytes() == saved
    assert retained_files(archive.parent) == sealed
    assert len(sent) == 1
    verified = budget._verify_archive(archive)
    assert verified["budget_settlement_complete"] is False
    assert verified["verified_requests"][0]["usage"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_id": None},
        {"provider_id": "bad id"},
        {"status": 200},
        {"body": {"error": {"message": ""}}},
        {"body": {"message": "failure"}},
        {"body": {"error": {"message": "failure"}, "usage": None}},
        {"body": {"error": {"message": "failure"}, "status": "failed"}},
        {"content_type": "text/plain"},
        {"failure": httpx.ReadError("Interrupted response")},
    ],
)
def test_insufficient_error_evidence_keeps_hold(tmp_path, changes):
    budget = make_budget(tmp_path)
    archive, _ = error_archive(tmp_path / "gateway", budget, **changes)
    assert budget.reconcile_archive(archive)["accounting"]["held_nanodollars"] == 120000
    saved = budget.ledger.path.read_bytes()
    with pytest.raises(ValueError, match="matching sealed provider error"):
        record(budget, archive)
    assert budget.ledger.path.read_bytes() == saved


@pytest.mark.parametrize(
    "changes",
    [
        {"final_charge_confirmed": False},
        {"final_charge_confirmed": 1},
        {"currency": "eur"},
        {"reference": " "},
        {"reviewed_by": " "},
        {"provider_evidence": ""},
        {"provider_evidence_sha256": "0" * 64},
        {"provider_request_id": "req_other"},
        {"confirmed_cost_usd": 0.1},
        {"confirmed_cost_usd": True},
        {"confirmed_cost_usd": "-1"},
        {"confirmed_cost_usd": "NaN"},
        {"confirmed_cost_usd": "Infinity"},
    ],
)
def test_invalid_confirmation_and_unvalidated_model_copy_are_rejected(changes):
    original = confirmation()
    with pytest.raises(ValueError):
        ProviderAccountingConfirmation.model_validate({**original.model_dump(), **changes})
    with pytest.raises(ValueError):
        ProviderAccountingConfirmation.model_validate(original.model_copy(update=changes))


@pytest.mark.parametrize("cost", ["0.000121", "1"])
def test_charge_above_reservation_cannot_reduce_the_hold(tmp_path, cost):
    budget = make_budget(tmp_path)
    archive, _ = error_archive(tmp_path / "gateway", budget)
    budget.reconcile_archive(archive)
    saved = budget.ledger.path.read_bytes()
    with pytest.raises(ValueError, match="exceeds the reservation"):
        record(budget, archive, confirmation(cost=cost))
    assert budget.ledger.path.read_bytes() == saved


def test_wrong_request_or_confirmation_cannot_settle(tmp_path):
    budget = make_budget(tmp_path)
    archive, _ = error_archive(tmp_path / "gateway", budget)
    budget.reconcile_archive(archive)
    saved = budget.ledger.path.read_bytes()
    with pytest.raises(ValueError, match="matching sealed provider error"):
        record(budget, archive, confirmation("req_different"))
    with pytest.raises(ValueError, match="No matching archived request"):
        budget.record_provider_accounting(archive, "request-9999", confirmation=confirmation())
    assert budget.ledger.path.read_bytes() == saved


def test_repeated_provider_id_within_archive_remains_ambiguous(tmp_path):
    budget = make_budget(tmp_path)
    archive, sent = error_archive(tmp_path / "gateway", budget, count=2)
    budget.reconcile_archive(archive)
    saved = budget.ledger.path.read_bytes()
    verified = budget._verify_archive(archive)
    assert len(sent) == 2
    assert all(
        "ambiguous_provider_request_id" in row["issues"] and row["provider_error"] is None
        for row in verified["verified_requests"]
    )
    with pytest.raises(ValueError):
        record(budget, archive)
    assert budget.ledger.path.read_bytes() == saved


def test_same_provider_confirmation_cannot_credit_two_archived_operations(tmp_path):
    first = make_budget(tmp_path)
    second = DispatchBudget(
        first.ledger,
        policy=first.policy,
        settings=first.settings,
        binding=BINDING.model_copy(update={"execution_id": "c" * 32}),
    )
    archive, _ = error_archive(tmp_path / "first", first)
    other, _ = error_archive(tmp_path / "second", second)
    first.reconcile_archive(archive)
    second.reconcile_archive(other)
    record(first, archive)
    saved = first.ledger.path.read_bytes()
    with pytest.raises(ValueError, match="duplicate provider accounting"):
        record(second, other)
    assert first.ledger.path.read_bytes() == saved
    assert first.ledger.snapshot()["accounting"]["held_nanodollars"] == 120000


def test_conflicting_repeat_cannot_rewrite_terminal_evidence(tmp_path):
    budget = make_budget(tmp_path)
    archive, _ = error_archive(tmp_path / "gateway", budget)
    record(budget, archive, confirmation(cost="0.000001"))
    saved = budget.ledger.path.read_bytes()
    with pytest.raises(ValueError, match="already settled"):
        record(budget, archive)
    assert budget.ledger.path.read_bytes() == saved


@pytest.mark.parametrize("tamper", ["dispatch", "operation", "request", "charge"])
def test_reopening_tampered_accounting_refuses_it(tmp_path, tamper):
    budget = make_budget(tmp_path)
    archive, _ = error_archive(tmp_path / "gateway", budget)
    record(budget, archive)
    raw = json.loads(budget.ledger.path.read_bytes())
    row = next(iter(raw["reservations"].values()))
    if tamper == "dispatch":
        row["dispatched_at"] = None
    elif tamper == "charge":
        row["charged_nanodollars"] = 1
    else:
        key = "operation_id" if tamper == "operation" else "request_sha256"
        row["settlement"][key] = "e" * 64
    write_json(budget.ledger.path, raw)
    with pytest.raises(ValueError):
        BudgetLedger.open_existing(budget.ledger.path)


def test_accounting_evidence_must_continue_to_match_sealed_archive(tmp_path):
    budget = make_budget(tmp_path)
    archive, _ = error_archive(tmp_path / "gateway", budget)
    record(budget, archive)
    raw = json.loads(budget.ledger.path.read_bytes())
    row = next(iter(raw["reservations"].values()))
    row["settlement"]["evidence_sha256"] = "f" * 64
    write_json(budget.ledger.path, raw)
    saved = budget.ledger.path.read_bytes()
    assert budget.archive_consistency(archive)["status"] == "inconsistent"
    with pytest.raises(ValueError, match="consistent sealed budget evidence"):
        record(budget, archive)
    assert budget.ledger.path.read_bytes() == saved


def test_failed_accounting_write_keeps_original_hold(tmp_path, monkeypatch):
    from research_harness import util

    budget = make_budget(tmp_path)
    archive, _ = error_archive(tmp_path / "gateway", budget)
    budget.reconcile_archive(archive)
    saved = budget.ledger.path.read_bytes()
    original = util.atomic_write

    def fail(path, data):
        if path == budget.ledger.path:
            raise OSError("Authored accounting write failure")
        return original(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(util, "atomic_write", fail)
        with pytest.raises(OSError, match="accounting write failure"):
            record(budget, archive)
    assert budget.ledger.path.read_bytes() == saved
    assert record(budget, archive)["accounting"]["held_nanodollars"] == 0


def accounting_cli(ledger, archive, proof_path):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "research_harness.evaluation.dispatch_budget",
            "record-provider-accounting",
            str(ledger),
            str(archive),
            "request-0001",
            "--confirmation",
            str(proof_path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_cli_settles_existing_error_idempotently_and_never_creates_ledger(tmp_path):
    budget = make_budget(tmp_path)
    archive, sent = error_archive(tmp_path / "gateway", budget)
    budget.reconcile_archive(archive)
    proof = tmp_path / "confirmation.json"
    write_json(proof, confirmation(cost="0.000002").model_dump(mode="json"))
    first = accounting_cli(budget.ledger.path, archive, proof)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["accounting"]["settled_nanodollars"] == 2000
    saved = budget.ledger.path.read_bytes()
    second = accounting_cli(budget.ledger.path, archive.parent, proof)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout) == json.loads(first.stdout)
    assert budget.ledger.path.read_bytes() == saved
    assert len(sent) == 1
    missing = tmp_path / "missing.json"
    assert accounting_cli(missing, archive, proof).returncode != 0
    assert not missing.exists()


def test_cli_rejects_changed_archive_without_touching_hold(tmp_path):
    budget = make_budget(tmp_path)
    archive, _ = error_archive(tmp_path / "gateway", budget)
    budget.reconcile_archive(archive)
    saved = budget.ledger.path.read_bytes()
    proof = tmp_path / "confirmation.json"
    write_json(proof, confirmation().model_dump(mode="json"))
    metadata = archive.parent / "gateway.json"
    raw = json.loads(metadata.read_bytes())
    raw["budget_control"]["policy"]["model"] = "unpriced-model"
    write_json(metadata, raw)
    result = accounting_cli(budget.ledger.path, archive, proof)
    assert result.returncode != 0
    assert budget.ledger.path.read_bytes() == saved
