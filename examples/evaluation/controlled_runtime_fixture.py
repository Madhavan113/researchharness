"""Offline acceptance of both real runtimes through the frozen comparison controller.

The authored response policy is a software fixture, not a measured model. No API
key or external source access is used. Raw requests and responses stay in --out.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import httpx

from research_harness.evaluation.benchmark import BenchmarkCase, BenchmarkManifest, FileRef
from research_harness.evaluation.controller import (
    ARMS,
    ComparisonConfig,
    finalize_comparison,
    prepare_comparison,
    run_case,
)
from research_harness.evaluation.runtime_executor import RuntimeExecutor
from research_harness.execution import DiscoverySettings
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.util import digest, write_json

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "normal_fixture", ROOT / "examples/omnigent/normal_runtime_fixture.py"
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
DOMAIN_TOOLS = {
    "begin_research",
    "get_research_context",
    "search_sources",
    "inspect_source",
    "probe_source",
    "get_evidence",
    "submit_proposal",
    "list_pipelines",
}


def benchmark(output):
    source = FIXTURE.SOURCE
    fixture = {
        "authorship": "Synthetic acceptance fixture; no live source sampled",
        "search_results": [{"url": source.url, "title": "Fixture official policy feed"}],
        "responses": [
            {
                "url": source.url,
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body": {
                    "items": [
                        {
                            "id": "policy-1",
                            "title": "Fixture export policy",
                            "published_at": "2026-09-01T00:00:00Z",
                        }
                    ]
                },
            }
        ],
    }
    path = output / "fixtures/policy.json"
    write_json(path, fixture)
    case = BenchmarkCase(
        id="controlled-policy",
        title="Controlled policy fixture",
        brief=FIXTURE.BRIEF,
        topic_group="fixture-policy",
        source_families=["fixture-policy"],
        tags=["runtime-acceptance"],
        review_status="authored",
        review_notes="Software acceptance only; not independently human reviewed",
        specification={
            "requirements": [
                {
                    "id": "policy",
                    "weight": 1,
                    "any_of": [
                        {
                            "url": source.url,
                            "connector": "json",
                            "id_pointer": "/id",
                            "published_pointer": "/published_at",
                            "required_pointers": ["/title"],
                        }
                    ],
                }
            ]
        },
        fixtures=FileRef(path="fixtures/policy.json", sha256=digest(path.read_bytes())),
        sources=[source],
        fixture_plan={"source_ids": [source.id], "unsupported": []},
        manual_checks=[
            "This deterministic model response is not evidence of live model performance"
        ],
    )
    case_path = output / "cases/controlled-policy.json"
    write_json(case_path, case.model_dump(mode="json"))
    manifest = BenchmarkManifest(
        id="controlled-runtime-acceptance",
        split="development",
        description="One authored software fixture for two actual runtime paths",
        cases=[FileRef(path="cases/controlled-policy.json", sha256=digest(case_path.read_bytes()))],
    )
    write_json(output / "manifest.json", manifest.model_dump(mode="json"))
    return output / "manifest.json"


class DirectResponses:
    def __init__(self):
        self.number = 0
        self.probe_id = None

    def respond(self, request):
        for item in request["input"]:
            if item.get("type") == "function_call_output":
                result = json.loads(item["output"])
                if result.get("probe_id"):
                    self.probe_id = result["probe_id"]
        self.number += 1
        calls = [
            ("search_sources", {"query": "official policy feed", "filters": None}),
            ("inspect_url", {"url": FIXTURE.SOURCE.url}),
            ("probe_source", {"source_json": FIXTURE.SOURCE.model_dump_json()}),
        ]
        if self.number <= len(calls):
            name, arguments = calls[self.number - 1]
            output = {
                "type": "function_call",
                "id": f"fc_{self.number}",
                "call_id": f"call_{self.number}",
                "name": name,
                "arguments": json.dumps(arguments),
                "status": "completed",
            }
        else:
            assert self.probe_id
            output = {
                "type": "message",
                "id": f"msg_{self.number}",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(FIXTURE.draft(self.probe_id, FIXTURE.SOURCE)),
                        "annotations": [],
                    }
                ],
            }
        return {
            "id": f"resp_direct_{self.number}",
            "object": "response",
            "created_at": 1788825600,
            "status": "completed",
            "model": "gpt-5.4-mini",
            "output": [output],
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 10,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 110,
            },
        }


def run(output: Path, python: Path):
    output.mkdir(parents=True, exist_ok=False)
    programs = {}
    for program in (Path(__file__).resolve(), Path(SPEC.origin)):
        relative = str(program.relative_to(ROOT))
        target = output / "fixture-programs" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(program.read_bytes())
        programs[relative] = digest(target.read_bytes())
    manifest = benchmark(output / "input")
    prepared = output / "comparison"
    prepare_comparison(
        manifest,
        prepared,
        instructions=(ROOT / "agents/comparison/instructions.md").read_text(),
        config=ComparisonConfig(settings=DiscoverySettings(max_rounds=8)),
    )
    gateways = {}

    def execute(task):
        policy = DirectResponses() if task.arm == "direct" else FIXTURE.ModelFixture(FIXTURE.SOURCE)

        def upstream(request):
            assert request.url == "https://fixture.invalid/v1/responses"
            payload = json.loads(request.content)
            assert payload["max_output_tokens"] == task.config.settings.max_output_tokens
            assert payload["reasoning"] == {"effort": "none"}
            assert "parallel_tool_calls" not in payload
            if task.arm == "direct":
                return httpx.Response(200, json=policy.respond(payload))
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=policy.respond(payload)
            )

        allowed = (
            {"search_sources", "inspect_url", "probe_source"}
            if task.arm == "direct"
            else {f"research__{name}" for name in DOMAIN_TOOLS}
        )
        with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
            gateway = ResponsesGateway(
                task.output / "gateway",
                model=task.config.model,
                settings=task.config.settings,
                upstream_base_url="https://fixture.invalid/v1",
                client=client,
                allowed_function_names=allowed,
                binding=task.gateway_binding,
            )
            try:
                with gateway:
                    result = RuntimeExecutor(
                        base_url=gateway.base_url,
                        api_key=gateway.api_key,
                        omnigent_python=python,
                        max_spend_usd=1.0,
                    )(task)
            except Exception as exc:
                if getattr(exc, "artifacts", None) is not None:
                    exc.artifacts = replace(
                        exc.artifacts,
                        attachments=(*exc.artifacts.attachments, gateway.output),
                        gateway_usage=gateway.output / "archive.json"
                        if (gateway.output / "archive.json").is_file()
                        else None,
                    )
                raise
        gateways[task.arm] = gateway.report()
        return replace(
            result,
            attachments=(*result.attachments, gateway.output),
            gateway_usage=gateway.output / "archive.json",
        )

    for arm in ARMS:
        entry = run_case(prepared, arm, "controlled-policy", execute)
        print(
            json.dumps({"arm": arm, "status": entry["status"], "error": entry["run"].get("error")}),
            flush=True,
        )
    report = finalize_comparison(prepared)
    acceptance = {
        "schema_version": 1,
        "execution": "fixture",
        "paid_calls": False,
        "source_network": False,
        "fixture_program_sha256": programs,
        "benchmark_sha256": report["benchmark_sha256"],
        "fixed_controls": report["fixed_controls"],
        "arms": [{"name": arm["name"], "summary": arm["summary"]} for arm in report["arms"]],
        "gateways": {
            arm: {key: value for key, value in value.items() if key != "requests"}
            for arm, value in gateways.items()
        },
        "report_sha256": digest((prepared / "report.json").read_bytes()),
        "limitations": [
            "Deterministic model response policies; no model-quality or cost conclusion",
            "Discovery-only fixture; workflow dimensions remain unmeasured",
            "Dollar totals remain unknown; request limits are not a billing cap",
        ],
    }
    write_json(output / "acceptance.json", acceptance)
    if any(arm["summary"]["correctness"]["valid"] != 1 for arm in report["arms"]):
        raise RuntimeError(
            "Controlled runtime fixture did not produce two independently valid artifacts"
        )
    assert gateways["direct"]["upstream_requests"] == 4
    assert gateways["omnigent"]["upstream_requests"] == 7
    for arm in report["arms"]:
        score = arm["results"][0]["score"]
        assert score["token_evidence_source"] == "gateway"
        assert score["gateway_usage"]["status"] == "verified_complete"
        assert score["total_tokens"] == {"direct": 440, "omnigent": 770}[arm["name"]]
    return acceptance


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--omnigent-python", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(run(args.out.resolve(), args.omnigent_python.expanduser().absolute()), indent=2)
    )
