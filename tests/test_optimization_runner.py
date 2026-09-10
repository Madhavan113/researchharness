"""Budgeted orchestration fixtures; real Omnigent/Docker acceptance is separate."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from filelock import FileLock, Timeout
from test_optimization_proposer import response, tool
from test_research_controller import fixture_executor
from test_research_pilot import configuration
from test_search_runtime_fixtures import FIXTURE
from test_strategy_session import CODE, bundle
from test_strategy_session import fake_runner as fake_runner

from research_harness.evaluation import pilot
from research_harness.evaluation.budget import AuthorizationRecord
from research_harness.evaluation.pilot import prepare_pilot
from research_harness.optimization.controller import SearchConfig
from research_harness.optimization.proposer import ProposalError, ProposerConfig
from research_harness.optimization.runner import (
    BudgetedSearchRun,
    SearchRunConfig,
    prepare_search_run,
)
from research_harness.optimization.workspace import WorkspaceConfig
from research_harness.util import digest, write_json


def read(path):
    return json.loads(path.read_bytes())


@pytest.fixture
def setup(tmp_path, fake_runner):
    package = FIXTURE.benchmark(tmp_path / "development", split="development")
    baseline = bundle(tmp_path)
    pilot_config = configuration()
    proposer_settings = pilot_config.comparison.settings.model_copy(
        update={"max_searches": 0, "max_inspections": 0, "max_probes": 0}
    )
    config = SearchRunConfig(
        **pilot_config.model_dump(exclude={"schema_version", "purpose", "comparison"}),
        search=SearchConfig(
            run_id="budgeted-search",
            arm="direct",
            comparison=pilot_config.comparison,
            proposer=ProposerConfig(model=pilot_config.policy.model, settings=proposer_settings),
            workspace=WorkspaceConfig(sandbox=baseline.config.sandbox),
            iterations=1,
            candidates_per_iteration=1,
        ),
    )
    return package, baseline, config


def prepare(tmp_path, setup, *, name="search", config=None):
    package, baseline, default = setup
    prepare_search_run(
        package,
        tmp_path / name,
        feedback=tmp_path / (name + "-feedback"),
        ledger_path=tmp_path / "shared-budget.json",
        baseline=baseline,
        instructions="Use original verified evidence.",
        config=config or default,
    )
    return BudgetedSearchRun(tmp_path / name)


def install_runtime(monkeypatch, *, interrupt=False):
    class RuntimeFixture:
        def __init__(self, **options):
            self.options = options

        def __call__(self, task):
            with httpx.Client(trust_env=False) as client:
                result = client.post(
                    self.options["base_url"] + "/responses",
                    headers={"Authorization": "Bearer " + self.options["api_key"]},
                    json={"model": task.config.model, "input": task.brief},
                )
                result.raise_for_status()
            if interrupt:
                raise KeyboardInterrupt("Known stopped runtime fixture")
            return fixture_executor(task)

    monkeypatch.setattr(pilot, "RuntimeExecutor", RuntimeFixture)


def handlers(run, seen, *, unknown=False):
    def observed(task, request, phase):
        data = json.loads(request.content)
        matching = [
            row
            for row in run.ledger.snapshot()["reservations"].values()
            if row["request_sha256"] == digest(request.content) and row["status"] == "dispatched"
        ]
        assert matching and all(row["dispatched_at"] for row in matching)
        seen.append((phase, request))
        assert str(request.url) == "https://fixture.invalid/v1/responses"
        assert data["service_tier"] == "default"
        return data

    def research(task, request):
        observed(task, request, "research")
        if unknown:
            raise httpx.ReadError("Provider outcome unknown")
        return httpx.Response(
            200, json=response([], len(seen), model=task.config.model, service_tier="default")
        )

    def proposer(task, request):
        data = observed(task, request, "proposer")
        if not any(item.get("type") == "function_call_output" for item in data["input"]):
            calls = []
            for identity in task.candidate_ids:
                for filename, content in (
                    ("strategy.py", CODE),
                    ("instructions.md", "Use original evidence."),
                ):
                    calls.append(
                        tool(
                            "write_file",
                            {
                                "path": f"workspace/candidates/{identity}/{filename}",
                                "content": content,
                                "encoding": "utf-8",
                            },
                            len(calls),
                        )
                    )
        else:
            calls = [tool("submit_candidates", {"candidate_ids": list(task.candidate_ids)}, 100)]
        return httpx.Response(
            200,
            json=response(calls, len(seen), model=run.config.policy.model, service_tier="default"),
        )

    return research, proposer


def test_prepare_registers_frozen_budget_and_preserves_config_copy(tmp_path, setup):
    run = prepare(tmp_path, setup)
    plan = read(run.root / "search.json")
    assert (
        plan["config"]["comparison"]["budget_control"]
        == plan["config"]["proposer"]["budget_control"]
    )
    assert str(run.root) in read(tmp_path / "shared-budget.json.pilots.json")["searches"]
    assert run.status()["budget"]["committed_nanodollars"] == 0
    changed = run.config
    changed.search.comparison.model = "different"
    assert run.config.search.comparison.model != "different"
    assert run.status()["search"]["phase"] == "search"


def test_complete_search_and_private_final_share_settlements_without_retry(
    tmp_path, setup, monkeypatch
):
    install_runtime(monkeypatch)
    run = prepare(tmp_path, setup)
    seen = []
    research, proposer = handlers(run, seen)
    result = run.run(
        omnigent_python=Path("unit-fixture"), research_handler=research, proposer_handler=proposer
    )
    assert result["search"]["phase"] == "selected"
    assert [phase for phase, _ in seen] == ["research", "proposer", "proposer", "research"]
    rows = run.ledger.snapshot()["reservations"]
    assert len(rows) == 4 and all(row["status"] == "settled" for row in rows.values())
    assert result["budget"]["held_nanodollars"] == 0
    assert result["budget"]["settled_nanodollars"] == 480000
    feedback_before = {
        str(p): digest(p.read_bytes())
        for p in (tmp_path / "search-feedback").rglob("*")
        if p.is_file()
    }
    heldout = FIXTURE.benchmark(tmp_path / "private-package", split="heldout")
    final = run.final(
        heldout_manifest=heldout,
        output=tmp_path / "private-final",
        omnigent_python=Path("unit-fixture"),
        research_handler=research,
    )
    assert final["status"] == "completed"
    assert len(seen) == 6
    assert all(row["status"] == "settled" for row in run.ledger.snapshot()["reservations"].values())
    assert feedback_before == {
        str(p): digest(p.read_bytes())
        for p in (tmp_path / "search-feedback").rglob("*")
        if p.is_file()
    }
    assert (
        BudgetedSearchRun(run.root).run(omnigent_python=Path("unused"))["search"]["phase"]
        == "finalized"
    )
    assert (
        run.final(
            heldout_manifest=heldout,
            output=tmp_path / "private-final",
            omnigent_python=Path("unused"),
        )
        == final
    )
    assert len(seen) == 6
    assert run.status()["budget"]["settled_nanodollars"] == 720000
    sealed = {
        str(p): digest(p.read_bytes()) for p in (run.root / "proposals").rglob("*") if p.is_file()
    }
    run.reconcile()
    assert sealed == {
        str(p): digest(p.read_bytes()) for p in (run.root / "proposals").rglob("*") if p.is_file()
    }


def test_old_pilot_and_search_revisions_detect_lost_search_spending(tmp_path, setup, monkeypatch):
    install_runtime(monkeypatch)
    package, baseline, config = setup
    prepare_pilot(
        package,
        tmp_path / "pilot",
        ledger_path=tmp_path / "shared-budget.json",
        instructions="Shared budget",
        config=config.pilot_config(),
    )
    run = prepare(tmp_path, setup)
    second = prepare(tmp_path, setup, name="second-search")
    before = run.ledger.path.read_bytes()
    research, proposer = handlers(run, [])
    run.run(
        omnigent_python=Path("unit-fixture"), research_handler=research, proposer_handler=proposer
    )
    assert second.status()["budget"]["settled_nanodollars"] == 480000
    run.ledger.path.write_bytes(before)
    with pytest.raises(ValueError, match="lost|missing|rolled back"):
        second.status()
    with pytest.raises(ValueError, match="lost|missing|rolled back"):
        pilot._load_pilot(tmp_path / "pilot")
    with pytest.raises(ValueError, match="lost|missing|rolled back"):
        prepare(tmp_path, setup, name="third-search")


def test_budget_exhaustion_never_dispatches_unreserved_proposal_request(
    tmp_path, setup, monkeypatch
):
    install_runtime(monkeypatch)
    config = setup[2].model_copy(update={"ceiling_usd": "0.33"})
    run = prepare(tmp_path, setup, config=config)
    seen = []
    research, proposer = handlers(run, seen)
    with pytest.raises(ProposalError):
        run.run(
            omnigent_python=Path("unit-fixture"),
            research_handler=research,
            proposer_handler=proposer,
        )
    assert [phase for phase, _ in seen] == ["research", "proposer"]
    assert all(row["status"] == "settled" for row in run.ledger.snapshot()["reservations"].values())
    assert run.controller.status()["proposals"]["iteration-0001"]["status"] == "unresolved"
    recovered = run.recover_proposal(1, reason="Original runner exited after exhausted reservation")
    assert recovered["closed"] and recovered["quiescent"]
    assert len(seen) == 2
    assert run.controller.status()["candidates"]["candidate-0001-01"]["status"] == "proposal_failed"


def test_unknown_response_retains_hold_and_does_not_replay(tmp_path, setup, monkeypatch):
    install_runtime(monkeypatch)
    run = prepare(tmp_path, setup)
    seen = []
    research, proposer = handlers(run, seen, unknown=True)
    with pytest.raises(ValueError, match="baseline"):
        run.run(
            omnigent_python=Path("unit-fixture"),
            research_handler=research,
            proposer_handler=proposer,
        )
    assert len(seen) == 1
    assert run.status()["budget"]["held_nanodollars"] == 327000000
    with pytest.raises(ValueError, match="baseline"):
        run.run(
            omnigent_python=Path("unit-fixture"),
            research_handler=research,
            proposer_handler=proposer,
        )
    run.reconcile()
    assert run.status()["budget"]["held_nanodollars"] == 327000000 and len(seen) == 1


def test_recovery_attaches_surviving_gateway_under_controller_lock(tmp_path, setup, monkeypatch):
    install_runtime(monkeypatch, interrupt=True)
    run = prepare(tmp_path, setup)
    seen = []
    research, proposer = handlers(run, seen)
    with pytest.raises(KeyboardInterrupt):
        run.run(
            omnigent_python=Path("unit-fixture"),
            research_handler=research,
            proposer_handler=proposer,
        )
    comparison = run.root / "comparisons/baseline"
    case_id = read(comparison / "comparison.json")["case_ids"][0]
    with FileLock(str(comparison / "direct/controller.lock")):
        with pytest.raises(Timeout):
            run.recover_case("baseline", case_id, reason="Runtime fixture stopped")
    result = run.recover_case("baseline", case_id, reason="Runtime fixture stopped")
    assert result["status"] == "failed" and result["gateway_usage"] == "gateway/archive.json"
    assert "gateway/archive.json" in result["artifact_hashes"]
    assert len(seen) == 1 and run.status()["budget"]["held_nanodollars"] == 0


@pytest.mark.parametrize("argument", ["missing_handlers", "unexpected_key"])
def test_fixture_mode_never_falls_back_to_live_transport(tmp_path, setup, argument):
    run = prepare(tmp_path, setup)
    options = {"api_key": "fixture-not-a-secret"} if argument == "unexpected_key" else {}
    with pytest.raises(ValueError, match="Fixture execution"):
        run.run(omnigent_python=Path("unused"), **options)
    assert not run.ledger.snapshot()["reservations"]


def test_registry_and_ledger_are_required_on_reopen(tmp_path, setup):
    run = prepare(tmp_path, setup)
    registry = Path(str(run.ledger.path) + ".pilots.json")
    original = registry.read_bytes()
    registry.unlink()
    with pytest.raises(ValueError, match="registry is missing"):
        BudgetedSearchRun(run.root)
    registry.write_bytes(original)
    run.ledger.path.unlink()
    with pytest.raises(ValueError, match="missing"):
        BudgetedSearchRun(run.root)
    with pytest.raises(ValueError, match="both exist"):
        prepare(tmp_path, setup, name="another")


def test_model_mode_requires_review_before_search_preparation(tmp_path, setup):
    source = setup[2].model_dump(mode="json")
    source["policy"].update(mode="openai-standard", upstream_base_url="https://api.openai.com/v1")
    source["search"]["comparison"]["execution"] = "model"
    source["authorization"] = AuthorizationRecord(
        status="approved", reference="unit fixture; not actual approval"
    ).model_dump(mode="json")
    with pytest.raises(ValueError, match="reviewed development"):
        prepare(tmp_path, setup, config=SearchRunConfig.model_validate(source))
    assert read(tmp_path / "shared-budget.json")["reservations"] == {}
    assert read(tmp_path / "shared-budget.json.pilots.json")["pilots"] == {}


@pytest.mark.parametrize("revoke", [False, True])
def test_model_execution_requires_current_external_authorization_before_runtime(
    tmp_path, setup, monkeypatch, revoke
):
    package, baseline, config = setup
    manifest = read(package)
    for entry in manifest["cases"]:
        path = package.parent / entry["path"]
        case = read(path)
        case["review_status"] = "reviewed"  # Unit fixture only, never the real benchmark.
        write_json(path, case)
        entry["sha256"] = digest(path.read_bytes())
    write_json(package, manifest)
    data = config.model_dump(mode="json")
    data["policy"].update(mode="openai-standard", upstream_base_url="https://api.openai.com/v1")
    data["search"]["comparison"]["execution"] = "model"
    if revoke:
        data["authorization"] = {"status": "approved", "reference": "Unit test only"}
    run = prepare(tmp_path, setup, config=SearchRunConfig.model_validate(data))
    if revoke:
        run.ledger.record_authorization(AuthorizationRecord())
    with pytest.raises(ValueError, match="current matching external spending approval"):
        run.run(omnigent_python=Path("must-not-launch"), api_key="unit-test-placeholder")
    assert not run.ledger.snapshot()["reservations"]
    assert run.status()["search"]["phase"] == "search"


def test_missing_recorded_gateway_is_not_silently_ignored(tmp_path, setup, monkeypatch):
    install_runtime(monkeypatch)
    run = prepare(tmp_path, setup)
    research, proposer = handlers(run, [])
    run.run(
        omnigent_python=Path("unit-fixture"), research_handler=research, proposer_handler=proposer
    )
    archive = next((run.root / "comparisons/baseline/direct/cases").glob("*/gateway/archive.json"))
    archive.unlink()
    with pytest.raises(ValueError, match="gateway budget evidence changed or is missing"):
        BudgetedSearchRun(run.root)
