from __future__ import annotations

import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
from threading import Barrier

import pytest

import research_harness.evaluation.budget as budget_module
from research_harness.evaluation.budget import (
    AuthorizationRecord,
    BudgetExceeded,
    BudgetLedger,
    CancellationProof,
    RateCard,
    model_budget_plan,
)
from research_harness.execution import DiscoverySettings
from research_harness.util import digest


@pytest.fixture
def rates():
    return RateCard(
        model="fixture-model",
        snapshot="fixture-model-2026-01-01",
        input_usd_per_million="1",
        output_usd_per_million="2",
        max_input_tokens_per_request=2,
        price_source_url="https://prices.fixture.example/model",
        price_as_of="2026-01-01",
    )


def ledger(tmp_path, rates, ceiling="0.000016"):
    return BudgetLedger(tmp_path / "budget.json", rates=rates, ceiling_usd=ceiling)


def reserve(book, operation_id="request-1", *, output_tokens=3):
    return book.reserve(
        operation_id, request_sha256=digest(operation_id), max_output_tokens=output_tokens
    )


def settle(book, operation_id="request-1", **overrides):
    arguments = {
        "input_tokens": 1,
        "output_tokens": 1,
        "evidence_sha256": digest("verified-provider-response"),
        "verified": True,
        "completed": True,
        **overrides,
    }
    return book.settle(operation_id, **arguments)


def cancellation(operation_id="request-1", **overrides):
    return CancellationProof(
        request_sha256=digest(operation_id),
        evidence_sha256=digest("host-verified-no-dispatch"),
        no_dispatch=True,
        all_dispatch_paths_verified=True,
        reason="Host checked every dispatch path before releasing the request",
        **overrides,
    )


def test_plan_uses_explicit_frozen_rates_and_worst_case_request_counts(rates):
    settings = DiscoverySettings(max_rounds=3, max_output_tokens=3)
    plan = model_budget_plan(settings, 20, rates)
    assert plan["per_request"] == {"requests": 1, "nanodollars": 8000, "usd": "0.000008000"}
    assert plan["per_case"] == {"requests": 3, "nanodollars": 24000, "usd": "0.000024000"}
    assert plan["per_pair"] == {"requests": 6, "nanodollars": 48000, "usd": "0.000048000"}
    assert plan["full_benchmark"] == {"requests": 120, "nanodollars": 960000, "usd": "0.000960000"}
    assert plan["rates_sha256"] == rates.fingerprint()
    assert plan["rates"]["input_usd_per_million"] == "1"
    assert plan["authorization"] == "not established by this plan"
    assert RateCard.model_validate_json(rates.model_dump_json()) == rates
    with pytest.raises(ValueError, match="frozen"):
        rates.input_usd_per_million = Decimal("0")


@pytest.mark.parametrize("invalid", [0.1, True, "NaN", "Infinity", "-0.01"])
def test_rate_card_rejects_inexact_or_invalid_money(rates, invalid):
    with pytest.raises(ValueError):
        RateCard.model_validate({**rates.model_dump(), "input_usd_per_million": invalid})


def test_rate_math_is_independent_of_decimal_context_and_rounds_conservatively(tmp_path, rates):
    precise = RateCard.model_validate(
        {
            **rates.model_dump(),
            "input_usd_per_million": "0.123456789123456789",
            "output_usd_per_million": "0.987654321987654321",
        }
    )
    with localcontext() as context:
        context.prec = 3
        assert precise.cost_nanodollars(1_000_000, 1_000_000) == 1_111_111_112
    tiny = RateCard.model_validate(
        {
            **rates.model_dump(),
            "input_usd_per_million": "0.0001",
            "output_usd_per_million": "0.0001",
            "max_input_tokens_per_request": 1,
        }
    )
    book = ledger(tmp_path, tiny, "0.0000000019")
    first = reserve(book, output_tokens=1)
    assert first["reserved_nanodollars"] == 1
    assert book.snapshot()["ceiling_nanodollars"] == 1
    with pytest.raises(BudgetExceeded):
        reserve(book, "request-2", output_tokens=1)
    book.mark_dispatched("request-1")
    assert settle(book, input_tokens=0, output_tokens=1)["charged_nanodollars"] == 1
    with pytest.raises(BudgetExceeded):
        reserve(book, "request-2", output_tokens=1)
    with pytest.raises(ValueError, match="not float"):
        BudgetLedger(tmp_path / "inexact.json", rates=tiny, ceiling_usd=0.1)


def test_reservation_retry_and_restart_never_create_a_second_charge(tmp_path, rates):
    book = ledger(tmp_path, rates)
    first = reserve(book)
    assert first["reserved_nanodollars"] == 8000
    assert reserve(book) == first
    reopened = ledger(tmp_path, rates)
    assert reserve(reopened) == first
    assert len(reopened.snapshot()["reservations"]) == 1
    assert reopened.snapshot()["accounting"]["committed_nanodollars"] == 8000
    with pytest.raises(ValueError, match="different arguments"):
        reopened.reserve("request-1", request_sha256=digest("different"), max_output_tokens=3)
    with pytest.raises(ValueError, match="different arguments"):
        reserve(reopened, output_tokens=4)
    with pytest.raises(ValueError, match="immutable rate card"):
        ledger(tmp_path, rates, "0.000017")
    changed = RateCard.model_validate({**rates.model_dump(), "snapshot": "fixture-new-snapshot"})
    with pytest.raises(ValueError, match="immutable rate card"):
        ledger(tmp_path, changed)


@pytest.mark.parametrize("held", [False, True])
def test_settlement_requires_a_durable_dispatch_marker(tmp_path, rates, held):
    book = ledger(tmp_path, rates)
    reserve(book)
    if held:
        book.hold("request-1", reason="Unknown reservation acknowledgment")
    before = book.path.read_bytes()
    with pytest.raises(ValueError, match="dispatch"):
        settle(book)
    assert book.path.read_bytes() == before
    assert book.snapshot()["accounting"]["held_nanodollars"] == 8000


def test_reopening_settlement_rejects_a_lost_dispatch_marker(tmp_path, rates):
    book = ledger(tmp_path, rates)
    reserve(book)
    book.mark_dispatched("request-1")
    settle(book)
    state = json.loads(book.path.read_text())
    state["reservations"]["request-1"]["dispatched_at"] = None
    book.path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="settled"):
        ledger(tmp_path, rates)


def test_settlement_releases_only_verified_remainder_and_is_terminal(tmp_path, rates):
    book = ledger(tmp_path, rates)
    reserve(book)
    reserve(book, "request-2")
    with pytest.raises(BudgetExceeded):
        reserve(book, "request-3")
    book.mark_dispatched("request-1")
    book.mark_dispatched("request-2")
    first = settle(book)
    assert first["status"] == "settled" and first["charged_nanodollars"] == 3000
    assert settle(book) == first
    with pytest.raises(ValueError, match="different usage"):
        settle(book, output_tokens=0)
    with pytest.raises(ValueError, match="different usage"):
        settle(book, evidence_sha256=digest("different-evidence"))
    assert book.snapshot()["accounting"]["committed_nanodollars"] == 11000
    with pytest.raises(BudgetExceeded):
        reserve(book, "request-3")
    settle(book, "request-2", input_tokens=0, output_tokens=0)
    reserve(book, "request-3")
    assert book.snapshot()["accounting"] == {
        "settled_nanodollars": 3000,
        "held_nanodollars": 8000,
        "committed_nanodollars": 11000,
        "remaining_nanodollars": 5000,
        "committed_usd": "0.000011000",
        "remaining_usd": "0.000005000",
    }
    with pytest.raises(ValueError, match="cannot be held"):
        book.hold("request-1", reason="late uncertainty")
    with pytest.raises(ValueError, match="cannot be released"):
        book.cancel_before_dispatch("request-1", proof=cancellation())


@pytest.mark.parametrize(
    "invalid",
    [
        {"input_tokens": None},
        {"output_tokens": -1},
        {"input_tokens": True},
        {"input_tokens": 3},
        {"output_tokens": 4},
        {"verified": False},
        {"completed": False},
        {"evidence_sha256": "unverified"},
    ],
)
def test_unknown_incomplete_or_out_of_bound_usage_cannot_release_budget(tmp_path, rates, invalid):
    book = ledger(tmp_path, rates, "0.000008")
    reserve(book)
    book.mark_dispatched("request-1")
    with pytest.raises(ValueError):
        settle(book, **invalid)
    reopened = ledger(tmp_path, rates, "0.000008")
    assert reopened.snapshot()["accounting"]["held_nanodollars"] == 8000
    with pytest.raises(BudgetExceeded):
        reserve(reopened, "request-2")


def test_interrupted_dispatch_keeps_reservation_across_restart_and_cannot_be_resent(
    tmp_path, rates
):
    book = ledger(tmp_path, rates, "0.000008")
    reserve(book)
    dispatched = book.mark_dispatched("request-1")
    assert dispatched["dispatched_at"]
    with pytest.raises(ValueError, match="dispatched again"):
        book.mark_dispatched("request-1")
    held = book.hold("request-1", reason="worker interrupted; provider outcome unknown")
    assert held["charged_nanodollars"] == held["reserved_nanodollars"] == 8000
    assert book.hold("request-1", reason=held["hold_reasons"][-1]) == held
    reopened = ledger(tmp_path, rates, "0.000008")
    assert reserve(reopened) == held
    with pytest.raises(ValueError, match="dispatched again"):
        reopened.mark_dispatched("request-1")
    with pytest.raises(ValueError, match="cannot be released"):
        reopened.cancel_before_dispatch("request-1", proof=cancellation())
    with pytest.raises(BudgetExceeded):
        reserve(reopened, "request-2")
    assert settle(reopened)["charged_nanodollars"] == 3000


def test_pre_dispatch_release_requires_complete_bound_proof_and_does_not_rearm_id(tmp_path, rates):
    book = ledger(tmp_path, rates, "0.000008")
    reserve(book)
    book.hold("request-1", reason="preparation interrupted; checking whether dispatch occurred")
    with pytest.raises(ValueError, match="different request"):
        book.cancel_before_dispatch("request-1", proof=cancellation("foreign-request"))
    proof_data = cancellation().model_dump()
    for field in ["no_dispatch", "all_dispatch_paths_verified"]:
        with pytest.raises(ValueError, match="complete evidence"):
            book.cancel_before_dispatch("request-1", proof={**proof_data, field: False})
    assert book.snapshot()["accounting"]["held_nanodollars"] == 8000
    proof = cancellation()
    released = book.cancel_before_dispatch("request-1", proof=proof)
    assert released["status"] == "released" and released["charged_nanodollars"] == 0
    assert book.cancel_before_dispatch("request-1", proof=proof) == released
    assert reserve(book) == released
    with pytest.raises(ValueError, match="different cancellation evidence"):
        book.cancel_before_dispatch(
            "request-1", proof={**proof_data, "evidence_sha256": digest("other-proof")}
        )
    with pytest.raises(ValueError, match="dispatched again"):
        book.mark_dispatched("request-1")
    with pytest.raises(ValueError, match="cannot be settled"):
        settle(book)
    reserve(book, "request-2")


def test_failed_atomic_settlement_keeps_the_previous_full_reservation(tmp_path, rates, monkeypatch):
    book = ledger(tmp_path, rates, "0.000008")
    reserve(book)
    book.mark_dispatched("request-1")

    def failed_write(*args, **kwargs):
        raise OSError("fixture disk write failure")

    with monkeypatch.context() as patch:
        patch.setattr(budget_module, "write_json", failed_write)
        with pytest.raises(OSError, match="disk write failure"):
            settle(book)
    reopened = ledger(tmp_path, rates, "0.000008")
    assert reopened.snapshot()["reservations"]["request-1"]["status"] == "dispatched"
    assert reopened.snapshot()["accounting"]["held_nanodollars"] == 8000
    with pytest.raises(BudgetExceeded):
        reserve(reopened, "request-2")


def test_authorization_is_separate_caller_metadata_and_retains_history(tmp_path, rates):
    book = ledger(tmp_path, rates)
    reserve(book)
    original = book.snapshot()
    assert original["authorization"] == {"status": "draft", "reference": None}
    assert original["authorization_is_dispatch_permission"] is False
    with pytest.raises(ValueError, match="external authorization"):
        AuthorizationRecord(status="approved")
    reported = AuthorizationRecord(
        status="approved", reference="caller-recorded-external-reference"
    )
    book.record_authorization(reported)
    book.record_authorization(reported)
    snapshot = ledger(tmp_path, rates).snapshot()
    assert len(snapshot["authorization_history"]) == 2
    assert snapshot["authorization"] == reported.model_dump()
    assert snapshot["authorization_is_dispatch_permission"] is False
    assert snapshot["reservations"] == original["reservations"]
    assert snapshot["accounting"] == original["accounting"]


def _reserve_in_process(path, rates_json, operation_id, start, results):
    try:
        rates = RateCard.model_validate_json(rates_json)
        book = BudgetLedger(path, rates=rates, ceiling_usd="0.000024")
        if not start.wait(15):
            raise RuntimeError("Fixture worker start timed out")
        results.put(("reserved", reserve(book, operation_id)))
    except BudgetExceeded:
        results.put(("denied", operation_id))
    except BaseException as exc:
        results.put(("error", f"{type(exc).__name__}: {exc}"))


@pytest.mark.parametrize("same_operation", [False, True])
def test_concurrent_process_reservations_never_exceed_the_ceiling(tmp_path, rates, same_operation):
    book = ledger(tmp_path, rates, "0.000024")
    context = multiprocessing.get_context("spawn")
    start, results = context.Event(), context.Queue()
    processes = [
        context.Process(
            target=_reserve_in_process,
            args=(
                book.path,
                rates.model_dump_json(),
                "same-request" if same_operation else f"request-{index}",
                start,
                results,
            ),
        )
        for index in range(6)
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        outcomes = [results.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=5)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        results.close()
    assert not [outcome for outcome in outcomes if outcome[0] == "error"], outcomes
    accepted = [row for status, row in outcomes if status == "reserved"]
    assert len(accepted) == (6 if same_operation else 3)
    snapshot = book.snapshot()
    assert len(snapshot["reservations"]) == (1 if same_operation else 3)
    assert snapshot["accounting"]["held_nanodollars"] == (8000 if same_operation else 24000)
    if same_operation:
        assert all(row == accepted[0] for row in accepted)


def test_racing_settlements_cannot_replace_the_first_verified_usage(tmp_path, rates):
    book = ledger(tmp_path, rates, "0.000008")
    reserve(book)
    book.mark_dispatched("request-1")
    barrier = Barrier(2)

    def complete(tokens):
        reopened = ledger(tmp_path, rates, "0.000008")
        barrier.wait(timeout=5)
        try:
            return settle(reopened, input_tokens=tokens, output_tokens=0)
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(complete, [1, 2]))
    settled = [result for result in results if isinstance(result, dict)]
    assert len(settled) == 1
    assert any(isinstance(result, str) and "different usage" in result for result in results)
    assert book.snapshot()["reservations"]["request-1"] == settled[0]
    assert book.snapshot()["accounting"]["settled_nanodollars"] in {1000, 2000}


def _crash_after_dispatch_marker(path, rates_json):
    book = BudgetLedger(
        path, rates=RateCard.model_validate_json(rates_json), ceiling_usd="0.000008"
    )
    reserve(book)
    book.mark_dispatched("request-1")
    os._exit(17)


def test_process_crash_after_dispatch_marker_preserves_the_full_charge(tmp_path, rates):
    book = ledger(tmp_path, rates, "0.000008")
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_after_dispatch_marker, args=(book.path, rates.model_dump_json())
    )
    process.start()
    try:
        process.join(timeout=15)
        assert process.exitcode == 17
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
    reopened = ledger(tmp_path, rates, "0.000008")
    assert reserve(reopened)["status"] == "dispatched"
    assert reopened.snapshot()["accounting"]["held_nanodollars"] == 8000
    with pytest.raises(BudgetExceeded):
        reserve(reopened, "request-2")


@pytest.mark.parametrize(
    "corruption", ["negative_charge", "unknown_status", "missing_hold", "invalid_json"]
)
def test_corrupt_journal_fails_closed_instead_of_reinitializing(tmp_path, rates, corruption):
    book = ledger(tmp_path, rates, "0.000008")
    reserve(book)
    raw = json.loads(book.path.read_text())
    row = raw["reservations"]["request-1"]
    if corruption == "negative_charge":
        row["charged_nanodollars"] = -1
    elif corruption == "unknown_status":
        row["status"] = "free"
    elif corruption == "missing_hold":
        row["charged_nanodollars"] = 0
    book.path.write_text("{" if corruption == "invalid_json" else json.dumps(raw))
    with pytest.raises(ValueError):
        ledger(tmp_path, rates, "0.000008")
    with pytest.raises(ValueError):
        reserve(book, "request-2")
