from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from test_research_budget import rates as rates

from research_harness.evaluation import budget
from research_harness.evaluation.budget import AuthorizationRecord, BudgetLedger
from research_harness.util import canonical_json, digest, write_json


def paths(tmp_path):
    ledger = tmp_path / "budget.json"
    return ledger, Path(str(ledger) + ".pilots.json"), Path(str(ledger) + ".initializing.json")


def initialize(tmp_path, rates, **changes):
    return BudgetLedger.prepare_registered(
        paths(tmp_path)[0], **{"rates": rates, "ceiling_usd": "0.000016", **changes}
    )


def fail_write(monkeypatch, target, *, after=False):
    original = budget._persist

    def fail(path, data):
        if path == target and not after:
            raise OSError("Authored initialization I/O failure")
        original(path, data)
        if path == target and after:
            raise OSError("Authored initialization I/O failure")

    monkeypatch.setattr(budget, "_persist", fail)


def saved_files(tmp_path):
    return {path.name: path.read_bytes() for path in paths(tmp_path) if path.exists()}


@pytest.mark.parametrize("component", ["intent", "ledger", "registry", "unlink"])
@pytest.mark.parametrize("after", [False, True])
def test_io_failure_at_each_initialization_stage_recovers_exact_state(
    tmp_path, rates, monkeypatch, component, after
):
    ledger, registry, intent = paths(tmp_path)
    with monkeypatch.context() as patch:
        if component == "unlink":
            unlink = Path.unlink

            def fail_unlink(path, *args, **kwargs):
                if path == intent and not after:
                    raise OSError("Authored initialization I/O failure")
                unlink(path, *args, **kwargs)
                if path == intent and after:
                    raise OSError("Authored initialization I/O failure")

            patch.setattr(Path, "unlink", fail_unlink)
        else:
            fail_write(
                patch,
                {"ledger": ledger, "registry": registry, "intent": intent}[component],
                after=after,
            )
        with pytest.raises(OSError, match="initialization I/O failure"):
            initialize(tmp_path, rates)
    before = ledger.read_bytes() if ledger.exists() else None
    expected = json.loads(intent.read_bytes())["ledger"] if intent.exists() else None
    book = initialize(tmp_path, rates)
    if before is not None:
        assert ledger.read_bytes() == before
    if expected is not None:
        assert json.loads(ledger.read_bytes()) == expected
    assert json.loads(registry.read_bytes()) == {"schema_version": 1, "pilots": {}}
    assert not intent.exists()
    assert not book.snapshot()["reservations"]
    assert book.snapshot()["accounting"]["remaining_nanodollars"] == 16000
    complete = saved_files(tmp_path)
    initialize(tmp_path, rates)
    assert saved_files(tmp_path) == complete


@pytest.mark.parametrize(
    "component,after", [("ledger", False), ("ledger", True), ("registry", True), ("unlink", True)]
)
def test_fresh_process_recovers_after_initializer_dies(tmp_path, rates, component, after):
    code = """
import json, os, sys
from pathlib import Path
from research_harness.evaluation import budget
ledger = Path(sys.argv[1])
component, after = sys.argv[3], sys.argv[4] == "True"
target = Path(str(ledger) + ".pilots.json") if component == "registry" else ledger
persist = budget._persist
def stop(path, data):
    if component != "unlink" and path == target and not after:
        os._exit(83)
    persist(path, data)
    if component != "unlink" and path == target and after:
        os._exit(83)
budget._persist = stop
unlink = Path.unlink
def stop_unlink(path, *args, **kwargs):
    unlink(path, *args, **kwargs)
    if component == "unlink" and path == Path(str(ledger) + ".initializing.json"):
        os._exit(83)
Path.unlink = stop_unlink
budget.BudgetLedger.prepare_registered(ledger, rates=budget.RateCard.model_validate_json(sys.argv[2]), ceiling_usd="0.000016")
"""
    ledger, registry, intent = paths(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", code, str(ledger), rates.model_dump_json(), component, str(after)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 83, result.stderr
    before = ledger.read_bytes() if ledger.exists() else None
    expected = json.loads(intent.read_bytes())["ledger"] if intent.exists() else None
    book = initialize(tmp_path, rates)
    assert registry.is_file() and not intent.exists()
    if before is not None:
        assert ledger.read_bytes() == before
    if expected is not None:
        assert json.loads(ledger.read_bytes()) == expected
    assert book.snapshot()["accounting"]["committed_nanodollars"] == 0


@pytest.mark.parametrize("changed", ["ledger", "registry"])
def test_initialization_intent_cannot_replace_changed_or_spent_state(
    tmp_path, rates, monkeypatch, changed
):
    ledger, registry, intent = paths(tmp_path)
    with monkeypatch.context() as patch:
        fail_write(patch, registry)
        with pytest.raises(OSError):
            initialize(tmp_path, rates)
    if changed == "ledger":
        book = BudgetLedger.open_existing(ledger)
        book.reserve("uncertain-operation", request_sha256="a" * 64, max_output_tokens=3)
    else:
        write_json(registry, {"schema_version": 1, "pilots": {"existing-run": "a" * 64}})
        ledger.unlink()
    before = saved_files(tmp_path)
    with pytest.raises(ValueError, match="changed since initialization"):
        initialize(tmp_path, rates)
    assert saved_files(tmp_path) == before and intent.exists()


@pytest.mark.parametrize("mutation", ["binding", "checksum", "schema", "history", "reservations"])
def test_invalid_initialization_evidence_never_creates_budget_files(
    tmp_path, rates, monkeypatch, mutation
):
    ledger, registry, intent = paths(tmp_path)
    with monkeypatch.context() as patch:
        fail_write(patch, ledger)
        with pytest.raises(OSError):
            initialize(tmp_path, rates)
    data = json.loads(intent.read_bytes())
    if mutation == "binding":
        data["ledger_path"] = str(tmp_path / "another-ledger.json")
    elif mutation == "checksum":
        data["ledger_sha256"] = "0" * 64
    elif mutation == "schema":
        data["schema_version"] = True
    elif mutation == "history":
        data["ledger"]["authorization_history"] *= 2
        data["ledger_sha256"] = digest(canonical_json(data["ledger"]))
    else:
        other = BudgetLedger(tmp_path / "other.json", rates=rates, ceiling_usd="0.000016")
        other.reserve("existing-operation", request_sha256="a" * 64, max_output_tokens=3)
        data["ledger"]["reservations"] = other.snapshot()["reservations"]
        data["ledger_sha256"] = digest(canonical_json(data["ledger"]))
    write_json(intent, data)
    before = intent.read_bytes()
    with pytest.raises(ValueError, match="initialization"):
        initialize(tmp_path, rates)
    assert intent.read_bytes() == before
    assert not ledger.exists() and not registry.exists()


@pytest.mark.parametrize("change", ["rates", "ceiling", "authorization"])
def test_recovery_requires_matching_initial_terms(tmp_path, rates, monkeypatch, change):
    ledger, _, _ = paths(tmp_path)
    with monkeypatch.context() as patch:
        fail_write(patch, ledger)
        with pytest.raises(OSError):
            initialize(tmp_path, rates)
    changes = {
        "rates": {"rates": rates.model_copy(update={"snapshot": "other-snapshot"})},
        "ceiling": {"ceiling_usd": "2"},
        "authorization": {
            "authorization": AuthorizationRecord(
                status="approved", reference="external-fixture-only"
            )
        },
    }
    before = saved_files(tmp_path)
    selected = dict(changes[change])
    selected_rates = selected.pop("rates", rates)
    with pytest.raises(ValueError):
        initialize(tmp_path, selected_rates, **selected)
    assert saved_files(tmp_path) == before


def test_concurrent_initializers_share_one_original_ledger(tmp_path, rates):
    barrier = Barrier(2)

    def start(_):
        barrier.wait(timeout=3)
        return initialize(tmp_path, rates).snapshot()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(start, range(2))
    assert first == second
    assert len(first["authorization_history"]) == 1
    assert not paths(tmp_path)[2].exists()
