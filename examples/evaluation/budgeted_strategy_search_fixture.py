"""Verify one shared budget through actual code search and private final runtimes.

All provider responses and source payloads are authored fixtures. This command
does not access a paid provider or establish model quality or optimization gains.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from search_runtime_fixtures import ResearchModelFixture, benchmark
from strategy_search_fixture import BASELINE, INSTRUCTIONS, CodingModelFixture

from research_harness.evaluation.budget import AuthorizationRecord, RateCard
from research_harness.evaluation.controller import ComparisonConfig, source_fingerprints
from research_harness.evaluation.dispatch_budget import DispatchPolicy
from research_harness.execution import DiscoverySettings
from research_harness.optimization.archive import _inventory
from research_harness.optimization.controller import SearchConfig
from research_harness.optimization.proposer import ProposerConfig
from research_harness.optimization.runner import (
    BudgetedSearchRun,
    SearchRunConfig,
    prepare_search_run,
)
from research_harness.optimization.workspace import WorkspaceConfig
from research_harness.strategies.config import StrategyBundle, StrategyConfig
from research_harness.strategies.sandbox import SandboxConfig
from research_harness.strategies.session import StrategySession
from research_harness.util import canonical_json, digest, write_json

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_bytes())


def run(output: Path, omnigent_python: Path, image: str):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    omnigent_python = omnigent_python.expanduser().absolute()
    if not omnigent_python.is_file():
        raise ValueError("Install the pinned separate Omnigent environment first")
    frozen = source_fingerprints()
    examples = {}
    for name in (
        "examples/evaluation/budgeted_strategy_search_fixture.py",
        "examples/evaluation/strategy_search_fixture.py",
        "examples/evaluation/search_runtime_fixtures.py",
        "examples/omnigent/normal_runtime_fixture.py",
    ):
        source = ROOT / name
        target = output / "reproduction" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        examples[name] = digest(source.read_bytes())
    write_json(output / "reproduction/source-hashes.json", {**frozen, **examples})
    development = benchmark(output / "development-package", split="development")
    baseline_dir = output / "baseline"
    baseline_dir.mkdir()
    (baseline_dir / "strategy.py").write_text(BASELINE)
    sandbox = SandboxConfig(image=image)
    strategy = StrategyConfig(
        source="strategy.py", source_sha256=digest(BASELINE), sandbox=sandbox, context=True
    )
    write_json(baseline_dir / "strategy.json", strategy.model_dump(mode="json"))
    baseline = StrategyBundle.load(baseline_dir / "strategy.json")
    settings = DiscoverySettings(max_rounds=10, deadline_seconds=120, service_tier="default")
    model = "gpt-5.4-mini"
    config = SearchRunConfig(
        policy=DispatchPolicy(
            mode="fixture", upstream_base_url="https://fixture.invalid/v1", model=model
        ),
        authorization=AuthorizationRecord(),
        ceiling_usd="10",
        rates=RateCard(
            model=model,
            snapshot=model,
            input_usd_per_million="0.75",
            output_usd_per_million="4.50",
            max_input_tokens_per_request=400000,
            price_source_url="https://developers.openai.com/api/docs/models/gpt-5.4-mini",
            price_as_of="2026-09-08",
        ),
        search=SearchConfig(
            run_id="budgeted-runtime-search-fixture",
            arm="omnigent",
            comparison=ComparisonConfig(model=model, settings=settings),
            proposer=ProposerConfig(
                model=model,
                settings=settings.model_copy(
                    update={
                        "max_rounds": 16,
                        "max_searches": 0,
                        "max_inspections": 0,
                        "max_probes": 0,
                    }
                ),
            ),
            workspace=WorkspaceConfig(sandbox=sandbox),
        ),
    )
    prepare_search_run(
        development,
        output / "search",
        feedback=output / "development-feedback",
        ledger_path=output / "shared-budget.json",
        baseline=baseline,
        instructions=INSTRUCTIONS,
        config=config,
    )
    runner = BudgetedSearchRun(output / "search")
    research_models, coding_models, tasks = {}, {}, {}
    observed, used = [], set()

    def witness(execution_id, phase, request):
        assert str(request.url) == "https://fixture.invalid/v1/responses"
        payload = json.loads(request.content)
        assert payload["service_tier"] == "default"
        state = runner.ledger.snapshot()
        matching = [
            row
            for identity, row in state["reservations"].items()
            if identity.startswith(execution_id + "/")
            and identity not in used
            and row["status"] == "dispatched"
            and row["request_sha256"] == digest(request.content)
        ]
        assert len(matching) == 1, (
            "Actual provider dispatch requires a matching durable reservation"
        )
        row = matching[0]
        assert row["dispatched_at"] and row["reserved_nanodollars"] == 327000000
        assert state["accounting"]["committed_nanodollars"] <= state["ceiling_nanodollars"]
        used.add(row["operation_id"])
        observed.append(
            {
                "phase": phase,
                "operation_id": row["operation_id"],
                "request_sha256": row["request_sha256"],
                "reserved_nanodollars": row["reserved_nanodollars"],
            }
        )
        write_json(output / "dispatch-observations.json", observed)
        return payload

    def research(task, request):
        identity = task.gateway_binding.execution_id
        if identity not in research_models:
            url = read(task.fixture_path)["responses"][0]["url"]
            research_models[identity] = ResearchModelFixture(task.brief, url)
            tasks[identity] = task
            print(f"research: {task.output.relative_to(output)}", flush=True)
        payload = witness(identity, "research", request)
        raw = research_models[identity].respond(payload)
        lines = []
        for line in raw.decode().splitlines(keepends=True):
            if line.startswith("data: {"):
                event = json.loads(line[6:])
                if isinstance(event.get("response"), dict):
                    event["response"]["service_tier"] = "default"
                line = "data: " + json.dumps(event) + "\n"
            lines.append(line)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content="".join(lines)
        )

    def proposer(task, request):
        if task.execution_id not in coding_models:
            coding_models[task.execution_id] = CodingModelFixture(task)
            print(f"proposer: iteration {task.iteration}", flush=True)
        payload = witness(task.execution_id, "proposer", request)
        response = coding_models[task.execution_id].respond(payload)
        response["service_tier"] = "default"
        return httpx.Response(200, json=response)

    selected = runner.run(
        omnigent_python=omnigent_python, research_handler=research, proposer_handler=proposer
    )
    assert selected["search"]["phase"] == "selected"
    assert all(row["status"] == "evaluated" for row in selected["search"]["candidates"].values())
    assert len(research_models) == 7 and len(coding_models) == 3
    assert selected["search"]["proposer_revocation"]["revoked"] is True
    limits = runner.controller._load()[3]
    snapshots = _inventory(output / "search/proposal-inputs", limits)
    print("selection frozen; proposer revoked; preparing private final fixture", flush=True)
    heldout = benchmark(output / "private-heldout-package", split="heldout")
    final = runner.final(
        heldout_manifest=heldout,
        output=output / "private-final",
        omnigent_python=omnigent_python,
        research_handler=research,
    )
    assert final["status"] == "completed" and len(final["candidate_ids"]) == 7
    assert all(
        row["summary"]["macro_quality"] == 1 and row["summary"]["execution_verified"] is False
        for row in final["candidates"]
    )
    assert len(research_models) == 14 and len(observed) == 125
    assert all(len(model.requests) == 7 for model in research_models.values())
    assert all(
        len(model.requests) == 9 and model.read_path and model.raw_trace
        for model in coding_models.values()
    )
    strategy_executions = 0
    for task in tasks.values():
        session = StrategySession.open(task.output / "strategy")
        session.assert_ready()
        events = session.status()["events"]
        assert len(events) == 10 and all(event["status"] == "completed" for event in events)
        strategy_executions += len(events)
    ledger = runner.ledger.snapshot()
    assert len(ledger["reservations"]) == 125
    assert all(row["status"] == "settled" for row in ledger["reservations"].values())
    assert ledger["accounting"]["held_nanodollars"] == 0
    assert ledger["accounting"]["settled_nanodollars"] == 15000000
    repeated = BudgetedSearchRun(output / "search")
    assert repeated.run(omnigent_python=omnigent_python)["search"]["phase"] == "finalized"
    assert (
        repeated.final(
            heldout_manifest=heldout,
            output=output / "private-final",
            omnigent_python=omnigent_python,
        )
        == final
    )
    assert len(observed) == 125
    assert _inventory(output / "search/proposal-inputs", limits) == snapshots
    private_brief = read(heldout.parent / read(heldout)["cases"][0]["path"])["brief"].encode()
    for name in ("development-feedback", "search/proposal-inputs", "search/proposals"):
        assert all(
            private_brief not in p.read_bytes() for p in (output / name).rglob("*") if p.is_file()
        )
    assert source_fingerprints() == frozen
    assert all(
        digest((ROOT / path).read_bytes()) == expected for path, expected in examples.items()
    )
    summary = {
        "schema_version": 1,
        "status": "passed",
        "paid_calls": 0,
        "actual_omnigent": True,
        "actual_coding_proposer": True,
        "actual_docker_strategies": True,
        "iterations": 3,
        "candidates_per_iteration": 2,
        "development_evaluations": 7,
        "private_final_evaluations": 7,
        "synthetic_requests": 125,
        "synthetic_tokens": 13750,
        "research_strategy_executions": strategy_executions,
        "all_dispatches_reserved_before_http": True,
        "settled_operations": 125,
        "ledger": ledger["accounting"],
        "cost_semantics": "Fixture token-rate accounting only; no real money was spent",
        "selection_and_revocation_precede_final": True,
        "final_retries_did_not_dispatch": True,
        "private_final_absent_from_proposer_files": True,
        "model_measurement": False,
        "source_fingerprints": {**frozen, **examples},
        "limitations": [
            "All model/source responses are authored fixtures.",
            "Human review, provider access, external spending authorization and measured evaluation remain required.",
            "Private final packages/executions and the separate private benchmark must never enter proposer feedback or publication.",
        ],
    }
    write_json(output / "acceptance.json", summary)
    print(
        canonical_json(
            {key: value for key, value in summary.items() if key != "source_fingerprints"}
        ),
        flush=True,
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--omnigent-python", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    run(args.out, args.omnigent_python, args.image)
