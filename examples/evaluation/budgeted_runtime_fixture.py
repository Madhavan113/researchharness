"""Offline acceptance of budget reservations through both actual runtime paths."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import httpx

from research_harness.evaluation.budget import AuthorizationRecord, BudgetLedger, RateCard
from research_harness.evaluation.controller import ARMS, ComparisonConfig
from research_harness.evaluation.dispatch_budget import DispatchPolicy
from research_harness.evaluation.pilot import (
    PilotConfig,
    execute_pilot_case,
    finalize_pilot,
    prepare_pilot,
)
from research_harness.execution import DiscoverySettings
from research_harness.util import digest, write_json

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "controlled_fixture", Path(__file__).with_name("controlled_runtime_fixture.py")
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def run(output: Path, python: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    programs = {}
    for path in (Path(__file__).resolve(), Path(SPEC.origin), Path(FIXTURE.SPEC.origin)):
        relative = path.relative_to(ROOT)
        target = output / "fixture-programs" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        programs[str(relative)] = digest(path.read_bytes())
    settings = DiscoverySettings(max_rounds=8, service_tier="default")
    model = "gpt-5.4-mini"
    rates = RateCard(
        model=model,
        snapshot=model,
        input_usd_per_million="0.75",
        output_usd_per_million="4.50",
        max_input_tokens_per_request=400000,
        price_source_url="https://developers.openai.com/api/docs/models/gpt-5.4-mini",
        price_as_of="2026-09-08",
    )
    config = PilotConfig(
        policy=DispatchPolicy(
            mode="fixture", upstream_base_url="https://fixture.invalid/v1", model=model
        ),
        authorization=AuthorizationRecord(),
        ceiling_usd="10",
        rates=rates,
        comparison=ComparisonConfig(settings=settings),
    )
    ledger_path = output / "shared-budget.json"
    prepared = output / "comparison"
    prepare_pilot(
        FIXTURE.benchmark(output / "input"),
        prepared,
        ledger_path=ledger_path,
        instructions=(ROOT / "agents/comparison/instructions.md").read_text(),
        config=config,
    )
    ledger = BudgetLedger(ledger_path, rates=rates, ceiling_usd="10")
    policies = {
        "direct": FIXTURE.DirectResponses(),
        "omnigent": FIXTURE.FIXTURE.ModelFixture(FIXTURE.FIXTURE.SOURCE),
    }
    observed = []
    operations = set()

    def handler(task, request):
        assert str(request.url) == "https://fixture.invalid/v1/responses"
        data = json.loads(request.content)
        assert (
            data["service_tier"] == "default"
            and data["max_output_tokens"] == settings.max_output_tokens
        )
        state = ledger.snapshot()
        matching = [
            row
            for identity, row in state["reservations"].items()
            if identity.startswith(task.gateway_binding.execution_id + "/")
            and identity not in operations
            and row["status"] == "dispatched"
            and row["request_sha256"] == digest(request.content)
        ]
        assert len(matching) == 1, "Each actual HTTP dispatch needs its prior durable reservation"
        row = matching[0]
        assert row["charged_nanodollars"] == row["reserved_nanodollars"] == 327000000
        assert state["accounting"]["committed_nanodollars"] <= state["ceiling_nanodollars"]
        operations.add(row["operation_id"])
        observed.append(
            {
                "operation_id": row["operation_id"],
                "request_sha256": row["request_sha256"],
                "reserved_nanodollars": row["reserved_nanodollars"],
                "arm": task.arm,
            }
        )
        if task.arm == "direct":
            response = policies[task.arm].respond(data)
            response["service_tier"] = "default"
            return httpx.Response(200, json=response)
        raw = policies[task.arm].respond(data)
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

    for arm in ARMS:
        entry = execute_pilot_case(
            prepared, arm, "controlled-policy", omnigent_python=python, fixture_handler=handler
        )
        print(
            json.dumps({"arm": arm, "status": entry["status"], "error": entry["run"].get("error")}),
            flush=True,
        )
    report = finalize_pilot(prepared)
    snapshot = ledger.snapshot()
    write_json(output / "dispatch-observations.json", observed)
    acceptance = {
        "schema_version": 1,
        "execution": "fixture",
        "paid_calls": False,
        "source_network": False,
        "fixture_program_sha256": programs,
        "arms": [{"name": arm["name"], "summary": arm["summary"]} for arm in report["arms"]],
        "dispatches": {arm: sum(item["arm"] == arm for item in observed) for arm in ARMS},
        "budget": report["pilot_budget"],
        "ledger_sha256": digest(ledger_path.read_bytes()),
        "report_sha256": digest((prepared / "pilot-report.json").read_bytes()),
        "limitations": [
            "Synthetic model/source responses; no live model-quality or billing result",
            "Rate-based fixture accounting, not real money",
            "Discovery acceptance only; workflow behavior is verified separately",
        ],
    }
    write_json(output / "acceptance.json", acceptance)
    assert acceptance["dispatches"] == {"direct": 4, "omnigent": 7}
    for arm in report["arms"]:
        assert arm["summary"]["correctness"]["valid"] == 1
        assert (
            arm["summary"]["efficiency"]["model_tokens"]["total"]
            == {"direct": 440, "omnigent": 770}[arm["name"]]
        )
    assert snapshot["accounting"]["held_nanodollars"] == 0
    assert snapshot["accounting"]["settled_nanodollars"] == 1320000
    assert len(snapshot["reservations"]) == 11
    assert all(row["status"] == "settled" for row in snapshot["reservations"].values())
    return acceptance


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--omnigent-python", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(run(args.out.resolve(), args.omnigent_python.expanduser().absolute()), indent=2)
    )
