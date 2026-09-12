from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
from filelock import FileLock, Timeout
from test_strategy_session import bundle

from research_harness.evaluation import pilot
from research_harness.evaluation.budget import AuthorizationRecord, BudgetLedger, RateCard
from research_harness.evaluation.controller import ARMS, ComparisonConfig, ExecutionArtifacts
from research_harness.evaluation.dispatch_budget import DispatchPolicy
from research_harness.evaluation.pilot import (
    PilotConfig,
    execute_pilot_case,
    finalize_pilot,
    prepare_pilot,
    recover_pilot_case,
)
from research_harness.execution import DiscoverySettings
from research_harness.strategies.session import StrategySession
from research_harness.util import write_json

DEVELOPMENT = Path(__file__).resolve().parent / "fixtures/research-lifecycle"
CASE = "filing-stable-accession"


@pytest.fixture
def package(tmp_path):
    path = tmp_path / "input"
    shutil.copytree(DEVELOPMENT, path)
    manifest = json.loads((path / "manifest.json").read_bytes())
    manifest["cases"] = [entry for entry in manifest["cases"] if Path(entry["path"]).stem == CASE]
    write_json(path / "manifest.json", manifest)
    return path / "manifest.json"


def test_registered_pilot_hashes_are_reused_but_restored_mtime_edits_fail(
    package, tmp_path, monkeypatch, settled_verification
):
    import os

    from research_harness.evaluation.dispatch_budget import _BUDGET_VERIFICATION

    output, ledger = prepare(package, tmp_path)
    install_runtime(monkeypatch)
    requests = []
    execute_pilot_case(
        output, "direct", CASE, omnigent_python=Path("unused"), fixture_handler=provider(requests)
    )
    path = output / "direct/cases" / CASE / "gateway/gateway.json"
    path.chmod(0o644)
    settled_verification(_BUDGET_VERIFICATION)
    pilot._check_registered_pilots(ledger)
    observed, original = [], Path.read_bytes

    def read_bytes(file):
        if file == path:
            observed.append(file)
        return original(file)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    pilot._check_registered_pilots(ledger)
    pilot._check_registered_pilots(ledger)
    assert not observed
    before_ledger, before_requests = ledger.path.read_bytes(), len(requests)
    before, raw = path.stat(), original(path)
    path.write_bytes(b"!" + raw[1:])
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match="gateway budget evidence"):
        pilot._check_registered_pilots(ledger)
    assert observed and len(requests) == before_requests
    assert ledger.path.read_bytes() == before_ledger


def test_moved_registered_pilot_is_a_ledger_error_before_new_work(package, tmp_path):
    from research_harness.evaluation.budget import LedgerEvidenceError

    older, ledger = prepare(package, tmp_path, name="older")
    current, _ = prepare(package, tmp_path, name="current")
    before = ledger.path.read_bytes()
    journal = current / "direct/journal.json"
    before_journal = journal.read_bytes()
    moved = tmp_path / "moved-older"
    older.rename(moved)
    with pytest.raises(LedgerEvidenceError, match="Registered pilot evidence is missing or moved"):
        execute_pilot_case(current, "direct", CASE, omnigent_python=Path("unused"))
    assert ledger.path.read_bytes() == before and journal.read_bytes() == before_journal
    moved.rename(older)
    pilot._check_registered_pilots(ledger)
    assert ledger.path.read_bytes() == before


def configuration(*, ceiling="1", mode="fixture", authorization=None, purpose="compatibility"):
    model = "gpt-5.4-mini-2026-03-17"
    return PilotConfig(
        purpose=purpose,
        policy=DispatchPolicy(
            mode=mode,
            model=model,
            upstream_base_url="https://fixture.invalid/v1"
            if mode == "fixture"
            else "https://api.openai.com/v1",
        ),
        authorization=authorization or AuthorizationRecord(),
        ceiling_usd=ceiling,
        rates=RateCard(
            model=model,
            snapshot=model,
            input_usd_per_million="0.75",
            output_usd_per_million="4.50",
            max_input_tokens_per_request=400000,
            price_source_url="https://developers.openai.com/api/docs/models/gpt-5.4-mini",
            price_as_of="2026-09-08",
        ),
        comparison=ComparisonConfig(
            execution="fixture" if mode == "fixture" else "model",
            model=model,
            settings=DiscoverySettings(max_rounds=2, service_tier="default"),
        ),
    )


def prepare(package, tmp_path, config=None, name="pilot"):
    config = config or configuration()
    output, ledger = tmp_path / name, tmp_path / "shared-budget.json"
    prepare_pilot(
        package, output, ledger_path=ledger, instructions="Unit controller policy", config=config
    )
    return output, BudgetLedger(ledger, rates=config.rates, ceiling_usd=config.ceiling_usd)


def provider(requests, *, unknown_second=False):
    def respond(task, request):
        requests.append((task.arm, request))
        if unknown_second and len(requests) == 2:
            raise httpx.ReadError("Fixture interrupted after dispatch")
        return httpx.Response(
            200,
            json={
                "id": f"response-{len(requests)}",
                "object": "response",
                "model": task.config.model,
                "status": "completed",
                "service_tier": "default",
                "output": [],
                "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
            },
        )

    return respond


def install_runtime(monkeypatch, *, interrupt=False):
    class RuntimeFixture:
        """Real gateway HTTP followed by a task failure; no model-quality fixture."""

        def __init__(self, **options):
            self.options = options

        def __call__(self, task):
            with httpx.Client(trust_env=False) as client:
                for number in range(2):
                    response = client.post(
                        self.options["base_url"] + "/responses",
                        headers={"Authorization": "Bearer " + self.options["api_key"]},
                        json={
                            "model": task.config.model,
                            "input": f"{task.brief} Request {number}",
                        },
                    )
                    response.raise_for_status()
            if interrupt:
                raise KeyboardInterrupt
            research = task.output / "research"
            research.mkdir()
            return ExecutionArtifacts(
                research=research, metadata={"fixture": "controller failure after two responses"}
            )

    monkeypatch.setattr(pilot, "RuntimeExecutor", RuntimeFixture)


def test_reviewed_provider_errors_preserve_case_witnesses_and_unknown_usage(
    package, tmp_path, monkeypatch
):
    from test_provider_accounting import accounting_cli, confirmation, retained_files

    install_runtime(monkeypatch)
    output, ledger = prepare(package, tmp_path)
    sent = []
    with pytest.raises(ValueError, match="Finish or resolve every case"):
        finalize_pilot(output)

    def provider_error(task, request):
        sent.append(task.arm)
        return httpx.Response(
            500,
            json={"error": {"message": "Authored request failure"}},
            headers={"x-request-id": f"req_fixture_{task.arm}"},
        )

    for arm in ARMS:
        entry = execute_pilot_case(
            output, arm, CASE, omnigent_python=Path("unused"), fixture_handler=provider_error
        )
        assert entry["status"] == "failed"
    assert finalize_pilot(output)["pilot_budget"]["comparison_accounting_complete"] is False
    for arm in ARMS:
        archive = output / arm / "cases" / CASE / "gateway/archive.json"
        saved = retained_files(archive.parent)
        proof = tmp_path / f"{arm}-confirmation.json"
        write_json(proof, confirmation(f"req_fixture_{arm}", "0.000003").model_dump(mode="json"))
        result = accounting_cli(ledger.path, archive, proof)
        assert result.returncode == 0, result.stderr
        assert retained_files(archive.parent) == saved
        # Completed cases must still validate and return without provider work.
        assert (
            execute_pilot_case(output, arm, CASE, omnigent_python=Path("unused"))["status"]
            == "failed"
        )

    report = finalize_pilot(output)["pilot_budget"]
    assert report["comparison_accounting_complete"] is True
    assert report["comparison_settlement_complete"] is False
    assert report["comparison_held_nanodollars"] == 0
    assert report["comparison_settled_nanodollars"] == 6000
    assert len(report["operator_reviewed_accounting_operations"]) == 2
    saved_ledger = ledger.path.read_bytes()
    prepare(package, tmp_path, name="next-pilot")
    assert ledger.path.read_bytes() == saved_ledger
    assert sent == list(ARMS)


def test_preparation_freezes_one_shared_budget_without_dispatch(package, tmp_path):
    output, ledger = prepare(package, tmp_path)
    assert ledger.snapshot()["accounting"]["committed_nanodollars"] == 0
    plan = json.loads((output / "comparison.json").read_bytes())
    assert (
        plan["controls"]["direct"]["budget_control"]
        == plan["controls"]["omnigent"]["budget_control"]
    )
    assert plan["controls"]["direct"]["model_settings"]["service_tier"] == "default"
    assert plan["controls"]["direct"]["budget_control"]["authorization"]["status"] == "draft"
    assert all(
        json.loads((output / arm / "journal.json").read_bytes())["cases"][CASE]["status"]
        == "pending"
        for arm in ARMS
    )


def test_budgeted_gateway_uses_controller_session_and_retains_failed_strategy_artifacts(
    package, tmp_path, monkeypatch
):
    strategy = bundle(tmp_path)
    output = tmp_path / "strategy-pilot"
    prepare_pilot(
        package,
        output,
        ledger_path=tmp_path / "strategy-budget.json",
        instructions="Shared strategy budget fixture",
        config=configuration(),
        strategy=strategy,
    )
    install_runtime(monkeypatch)
    original_gateway = pilot.ResponsesGateway
    observed = []

    def gateway(*args, **kwargs):
        session = kwargs["strategy"]
        session.assert_ready()
        observed.append(session.session_id)
        return original_gateway(*args, **kwargs)

    monkeypatch.setattr(pilot, "ResponsesGateway", gateway)
    requests = []
    for arm in ARMS:
        entry = execute_pilot_case(
            output,
            arm,
            CASE,
            omnigent_python=Path("unused-fixture-python"),
            fixture_handler=provider(requests),
        )
        assert entry["status"] == "failed"  # The software runtime deliberately saves no proposal.
        session = StrategySession.open(output / arm / "cases" / CASE / "strategy")
        assert entry["strategy_session_id"] == observed[-1] == session.session_id
        assert "strategy/session.json" in entry["artifact_hashes"]
        assert "strategy/bundle/strategy.py" in entry["artifact_hashes"]
    assert len(set(observed)) == 2
    report = finalize_pilot(output)
    assert report["pilot_budget"]["comparison_settled_nanodollars"] == 480000


def test_both_arms_settle_failed_tasks_and_do_not_redispatch_terminals(
    package, tmp_path, monkeypatch
):
    output, ledger = prepare(package, tmp_path)
    install_runtime(monkeypatch)
    requests = []
    for arm in ARMS:
        entry = execute_pilot_case(
            output,
            arm,
            CASE,
            omnigent_python=Path("unused-fixture-python"),
            fixture_handler=provider(requests),
        )
        assert entry["status"] == "failed" and entry["run"]["gateway_usage"]
        assert execute_pilot_case(output, arm, CASE, omnigent_python=Path("unused")) == entry
    assert len(requests) == 4
    state = ledger.snapshot()
    assert state["accounting"]["settled_nanodollars"] == 480000
    assert state["accounting"]["held_nanodollars"] == 0
    report = finalize_pilot(output)
    assert report["pilot_budget"]["comparison_settled_nanodollars"] == 480000
    assert all(arm["summary"]["correctness"]["execution_failed"] == 1 for arm in report["arms"])
    assert all(
        arm["summary"]["efficiency"]["model_tokens"]["total"] == 220 for arm in report["arms"]
    )
    second, same_ledger = prepare(package, tmp_path, name="new-revision")
    assert second != output and same_ledger.snapshot()["accounting"] == state["accounting"]


def test_budget_exhaustion_denies_before_any_provider_dispatch(package, tmp_path, monkeypatch):
    output, ledger = prepare(package, tmp_path, configuration(ceiling="0.1"))
    install_runtime(monkeypatch)
    requests = []
    for arm in ARMS:
        result = execute_pilot_case(
            output, arm, CASE, omnigent_python=Path("unused"), fixture_handler=provider(requests)
        )
        assert result["status"] == "failed"
    assert requests == [] and ledger.snapshot()["reservations"] == {}
    report = finalize_pilot(output)
    assert all(arm["summary"]["efficiency"]["model_tokens"]["total"] == 0 for arm in report["arms"])


def test_unknown_dispatched_response_keeps_full_reservation(package, tmp_path, monkeypatch):
    output, ledger = prepare(package, tmp_path)
    install_runtime(monkeypatch)
    requests = []
    entry = execute_pilot_case(
        output,
        "direct",
        CASE,
        omnigent_python=Path("unused"),
        fixture_handler=provider(requests, unknown_second=True),
    )
    assert entry["status"] == "failed" and len(requests) == 2
    assert ledger.snapshot()["accounting"]["settled_nanodollars"] == 120000
    assert ledger.snapshot()["accounting"]["held_nanodollars"] == 327000000


def test_recovery_cannot_race_execution_or_repeat_provider_work(package, tmp_path, monkeypatch):
    output, ledger = prepare(package, tmp_path)
    install_runtime(monkeypatch, interrupt=True)
    requests = []
    with pytest.raises(KeyboardInterrupt):
        execute_pilot_case(
            output,
            "direct",
            CASE,
            omnigent_python=Path("unused"),
            fixture_handler=provider(requests),
        )
    before = ledger.snapshot()["accounting"]
    with FileLock(str(output / "direct/controller.lock")):
        with pytest.raises(Timeout):
            recover_pilot_case(output, "direct", CASE, reason="Unit fixture stopped")
    result = recover_pilot_case(output, "direct", CASE, reason="Unit fixture stopped")
    assert result["status"] == "failed" and result["run"]["gateway_usage"]
    assert len(requests) == 2 and ledger.snapshot()["accounting"] == before


@pytest.mark.parametrize("change", ["missing-ledger", "ledger-reference", "pilot-config"])
def test_pilot_cannot_reset_or_redirect_frozen_budget(package, tmp_path, change):
    output, ledger = prepare(package, tmp_path)
    if change == "missing-ledger":
        ledger.path.unlink()
    else:
        path = output / "pilot.json"
        manifest = json.loads(path.read_bytes())
        if change == "ledger-reference":
            manifest["ledger_path"] = str(tmp_path / "fresh-budget.json")
        else:
            manifest["configuration"]["ceiling_usd"] = "100"
        write_json(path, manifest)
    with pytest.raises(ValueError, match="missing|changed"):
        execute_pilot_case(output, "direct", CASE, omnigent_python=Path("unused"))
    assert not (tmp_path / "fresh-budget.json").exists()
    if change == "missing-ledger":
        assert not ledger.path.exists()


def test_draft_live_pilot_stays_pending_and_makes_no_request(package, tmp_path, monkeypatch):
    output, ledger = prepare(package, tmp_path, configuration(mode="openai-standard"))
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-provider-key-unit-test")
    with pytest.raises(ValueError, match="external spending approval"):
        execute_pilot_case(output, "direct", CASE, omnigent_python=Path("unused"))
    assert ledger.snapshot()["reservations"] == {}
    assert (
        json.loads((output / "direct/journal.json").read_bytes())["cases"][CASE]["status"]
        == "pending"
    )


def test_live_baseline_requires_review_before_dispatch(package, tmp_path, monkeypatch):
    config = configuration(
        mode="openai-standard",
        purpose="baseline",
        authorization=AuthorizationRecord(
            status="approved", reference="unit-test assertion; no real authorization"
        ),
    )
    output, ledger = prepare(package, tmp_path, config)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="reviewed development cases"):
        execute_pilot_case(output, "direct", CASE, omnigent_python=Path("unused"))
    assert ledger.snapshot()["reservations"] == {}


def test_revoked_approval_leaves_live_case_pending(package, tmp_path, monkeypatch):
    config = configuration(
        mode="openai-standard",
        authorization=AuthorizationRecord(
            status="approved", reference="unit-test assertion; no real authorization"
        ),
    )
    output, ledger = prepare(package, tmp_path, config)
    ledger.record_authorization(
        AuthorizationRecord(status="draft", reference="unit-test revocation")
    )
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-provider-key-unit-test")
    with pytest.raises(ValueError, match="current matching external spending approval"):
        execute_pilot_case(output, "direct", CASE, omnigent_python=Path("unused"))
    assert (
        json.loads((output / "direct/journal.json").read_bytes())["cases"][CASE]["status"]
        == "pending"
    )
    assert ledger.snapshot()["reservations"] == {}


@pytest.mark.parametrize("target", ["budget-reconciliation.json", "budget-execution.json"])
def test_report_write_failure_retains_provider_archive(package, tmp_path, monkeypatch, target):
    output, ledger = prepare(package, tmp_path)
    install_runtime(monkeypatch)
    original = pilot.write_json

    def fail_selected(path, value):
        if path.name == target:
            raise OSError("Fixture report persistence failure")
        return original(path, value)

    monkeypatch.setattr(pilot, "write_json", fail_selected)
    requests = []
    for arm in ARMS:
        entry = execute_pilot_case(
            output, arm, CASE, omnigent_python=Path("unused"), fixture_handler=provider(requests)
        )
        assert entry["status"] == "failed" and entry["run"]["gateway_usage"]
        assert entry["artifact_hashes"]["gateway/archive.json"]
    report = finalize_pilot(output)
    assert len(requests) == 4 and ledger.snapshot()["accounting"]["settled_nanodollars"] == 480000
    assert all(
        arm["summary"]["efficiency"]["model_tokens"]["total"] == 220 for arm in report["arms"]
    )


@pytest.mark.parametrize("action", ["finalize", "next-case"])
def test_archived_spending_cannot_disappear_after_ledger_restore(
    package, tmp_path, monkeypatch, action
):
    output, ledger = prepare(package, tmp_path)
    initial = ledger.path.read_bytes()
    install_runtime(monkeypatch)
    requests = []
    execute_pilot_case(
        output, "direct", CASE, omnigent_python=Path("unused"), fixture_handler=provider(requests)
    )
    if action == "finalize":
        execute_pilot_case(
            output,
            "omnigent",
            CASE,
            omnigent_python=Path("unused"),
            fixture_handler=provider(requests),
        )
    observed_calls = len(requests)
    ledger.path.write_bytes(initial)
    with pytest.raises(ValueError, match="lost a witnessed operation|consistency"):
        if action == "finalize":
            finalize_pilot(output)
        else:
            execute_pilot_case(
                output,
                "omnigent",
                CASE,
                omnigent_python=Path("unused"),
                fixture_handler=provider(requests),
            )
    assert len(requests) == observed_calls


def test_initial_witness_preserves_spending_across_prepared_revisions(
    package, tmp_path, monkeypatch
):
    first, ledger = prepare(package, tmp_path)
    initial = ledger.path.read_bytes()
    install_runtime(monkeypatch)
    requests = []
    execute_pilot_case(
        first, "direct", CASE, omnigent_python=Path("unused"), fixture_handler=provider(requests)
    )
    second, _ = prepare(package, tmp_path, name="next-revision")
    ledger.path.write_bytes(initial)
    with pytest.raises(ValueError, match="lost a witnessed operation"):
        execute_pilot_case(
            second,
            "direct",
            CASE,
            omnigent_python=Path("unused"),
            fixture_handler=provider(requests),
        )
    assert len(requests) == 2


def test_witness_prevents_rollback_of_revocation(package, tmp_path):
    output, ledger = prepare(package, tmp_path)
    initial = ledger.path.read_bytes()
    ledger.record_authorization(AuthorizationRecord(reference="fixture revocation record"))
    second, _ = prepare(
        package,
        tmp_path,
        configuration(authorization=AuthorizationRecord(reference="fixture revocation record")),
        name="new-authorization",
    )
    ledger.path.write_bytes(initial)
    with pytest.raises(ValueError, match="authorization|history"):
        execute_pilot_case(second, "direct", CASE, omnigent_python=Path("unused"))
    assert output.is_dir()


@pytest.mark.parametrize("action", ["older-pilot", "prepare-next"])
def test_prepared_revisions_share_spending_witnesses(package, tmp_path, monkeypatch, action):
    older, ledger = prepare(package, tmp_path)
    newer, _ = prepare(package, tmp_path, name="newer")
    initial = ledger.path.read_bytes()
    install_runtime(monkeypatch)
    requests = []
    execute_pilot_case(
        newer, "direct", CASE, omnigent_python=Path("unused"), fixture_handler=provider(requests)
    )
    ledger.path.write_bytes(initial)
    with pytest.raises(ValueError, match="lost a witnessed operation|consistency"):
        if action == "older-pilot":
            execute_pilot_case(
                older,
                "direct",
                CASE,
                omnigent_python=Path("unused"),
                fixture_handler=provider(requests),
            )
        else:
            prepare(package, tmp_path, name="third")
    assert len(requests) == 2


def test_finalization_checks_the_snapshot_used_for_cost_report(package, tmp_path, monkeypatch):
    output, ledger = prepare(package, tmp_path)
    initial = ledger.path.read_bytes()
    install_runtime(monkeypatch)
    requests = []
    for arm in ARMS:
        execute_pilot_case(
            output, arm, CASE, omnigent_python=Path("unused"), fixture_handler=provider(requests)
        )
    original = pilot.finalize_comparison

    def restore_after_scoring(path):
        report = original(path)
        ledger.path.write_bytes(initial)
        return report

    monkeypatch.setattr(pilot, "finalize_comparison", restore_after_scoring)
    with pytest.raises(ValueError, match="lost a witnessed operation|consistency"):
        finalize_pilot(output)
    assert not (output / "pilot-report.json").exists()


def test_pilot_preflight_serializes_concurrent_ledger_writers(package, tmp_path, monkeypatch):
    import threading

    output, ledger = prepare(package, tmp_path)
    entered, finished = threading.Event(), threading.Event()
    failures = []

    def writer():
        try:
            entered.set()
            ledger.reserve("parallel-fixture", request_sha256="a" * 64, max_output_tokens=6000)
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=writer)
    original = pilot._check_ledger_witness

    def witnessed(before, current):
        if not entered.is_set():
            thread.start()
            assert entered.wait(2)
            assert not finished.wait(0.1), "A writer must not race the checked snapshot"
        return original(before, current)

    monkeypatch.setattr(pilot, "_check_ledger_witness", witnessed)
    pilot._load_pilot(output)
    thread.join(2)
    assert finished.is_set() and not failures
    assert "parallel-fixture" in ledger.snapshot()["reservations"]


def test_failed_initial_preparation_leaves_a_reusable_empty_registry(
    package, tmp_path, monkeypatch
):
    def fail(*args, **kwargs):
        raise OSError("fixture preparation failure")

    with monkeypatch.context() as patch:
        patch.setattr(pilot, "prepare_comparison", fail)
        with pytest.raises(OSError, match="preparation failure"):
            prepare(package, tmp_path)
    ledger_path = tmp_path / "shared-budget.json"
    before = ledger_path.read_bytes()
    registry_path = Path(str(ledger_path) + ".pilots.json")
    assert json.loads(registry_path.read_bytes()) == {"schema_version": 1, "pilots": {}}
    output, book = prepare(package, tmp_path, name="retry")
    assert ledger_path.read_bytes() == before
    assert str(output) in json.loads(registry_path.read_bytes())["pilots"]
    assert book.snapshot()["accounting"]["held_nanodollars"] == 0


def test_failed_first_registry_write_can_resume_the_same_ledger(package, tmp_path, monkeypatch):
    from research_harness import util

    atomic_write = util.atomic_write

    def fail_registry(path, *args, **kwargs):
        if str(path).endswith(".pilots.json"):
            raise OSError("Authored initial registry write failure")
        return atomic_write(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(util, "atomic_write", fail_registry)
        with pytest.raises(OSError, match="registry write failure"):
            prepare(package, tmp_path)
    ledger_path = tmp_path / "shared-budget.json"
    before = ledger_path.read_bytes()
    output, book = prepare(package, tmp_path, name="recovered-initialization")
    assert ledger_path.read_bytes() == before
    assert str(output) in json.loads(Path(str(ledger_path) + ".pilots.json").read_bytes())["pilots"]
    assert book.snapshot()["accounting"]["held_nanodollars"] == 0
    assert not Path(str(ledger_path) + ".initializing.json").exists()


def test_preparation_cannot_reinitialize_a_missing_registry(package, tmp_path):
    output, ledger = prepare(package, tmp_path)
    Path(str(ledger.path) + ".pilots.json").unlink()
    with pytest.raises(ValueError, match="no pilot registry"):
        prepare(package, tmp_path, name="forgotten-history")
    assert output.is_dir() and not (tmp_path / "forgotten-history").exists()
