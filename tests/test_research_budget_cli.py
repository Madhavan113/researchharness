from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from research_harness.evaluation.budget import BudgetLedger, RateCard
from research_harness.util import digest, write_json


@pytest.fixture
def book(tmp_path):
    ledger = BudgetLedger(
        tmp_path / "shared-budget.json",
        rates=RateCard(
            model="fixture",
            snapshot="fixture-snapshot",
            input_usd_per_million="1",
            output_usd_per_million="2",
            max_input_tokens_per_request=2,
            price_source_url="https://prices.fixture.example/model",
            price_as_of="2026-09-10",
        ),
        ceiling_usd="0.000016",
    )
    ledger.reserve("fixture-request", request_sha256=digest("request"), max_output_tokens=3)
    ledger.mark_dispatched("fixture-request")
    ledger.hold("fixture-request", reason="Authored unresolved response")
    return ledger


def run_cli(path, authorization):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "research_harness.evaluation.budget",
            "record-authorization",
            str(path),
            "--authorization",
            str(authorization),
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_authorization_cli_retains_spending_and_records_idempotent_history(book, tmp_path):
    before = book.snapshot()
    supplied = tmp_path / "authorization.json"
    approved = {"status": "approved", "reference": "external-approval-fixture-only"}
    write_json(supplied, approved)
    first = run_cli(book.path, supplied)
    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["authorization"] == approved
    assert result["authorization_is_dispatch_permission"] is False
    assert result["accounting"] == before["accounting"]
    assert len(book.snapshot()["authorization_history"]) == 2
    saved = book.path.read_bytes()
    assert run_cli(book.path, supplied).returncode == 0
    assert book.path.read_bytes() == saved
    write_json(supplied, {"status": "draft", "reference": "fixture revocation"})
    revoked = run_cli(book.path, supplied)
    assert revoked.returncode == 0, revoked.stderr
    after = BudgetLedger.open_existing(book.path).snapshot()
    assert after["authorization"]["status"] == "draft"
    assert len(after["authorization_history"]) == 3
    for key in (
        "rates",
        "rates_sha256",
        "ceiling_usd",
        "ceiling_nanodollars",
        "reservations",
        "accounting",
    ):
        assert after[key] == before[key]


@pytest.mark.parametrize(
    "failure",
    ["missing_ledger", "corrupt_ledger", "unreferenced_approval", "missing_authorization"],
)
def test_authorization_cli_refuses_invalid_inputs_without_writing(book, tmp_path, failure):
    supplied = tmp_path / "authorization.json"
    write_json(supplied, {"status": "approved", "reference": "external-approval-fixture-only"})
    if failure == "missing_ledger":
        book.path.unlink()
    elif failure == "corrupt_ledger":
        state = json.loads(book.path.read_bytes())
        state["rates_sha256"] = "0" * 64
        write_json(book.path, state)
    elif failure == "unreferenced_approval":
        write_json(supplied, {"status": "approved"})
    else:
        supplied.unlink()
    before = book.path.read_bytes() if book.path.exists() else None
    result = run_cli(book.path, supplied)
    assert result.returncode != 0
    assert "error:" in result.stderr and not result.stdout
    assert (book.path.read_bytes() if book.path.exists() else None) == before


def test_existing_ledger_open_never_recreates_a_file_lost_during_open(book, monkeypatch):
    initialize = BudgetLedger.__init__

    def remove_before_initialize(self, path, **kwargs):
        Path(path).unlink()
        initialize(self, path, **kwargs)

    monkeypatch.setattr(BudgetLedger, "__init__", remove_before_initialize)
    with pytest.raises(ValueError, match="missing"):
        BudgetLedger.open_existing(book.path)
    assert not book.path.exists()
