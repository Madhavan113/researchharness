"""Actual isolated observation strategy through direct and normal Omnigent/MCP paths.

Source/model responses are authored fixtures. This verifies projection and retry
behavior, not complete proposal quality, optimization or held-out evaluation.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import runpy
from pathlib import Path

import httpx

from research_harness.backend import Backend
from research_harness.discovery import Discovery
from research_harness.evaluation.fixture_transport import FixtureSources
from research_harness.execution import DiscoverySettings
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.integrations.omnigent import (
    DISCOVERY_TOOL_NAMES,
    MODEL,
    LocalOmnigent,
    case_strategy_session,
    prepare_case,
    refresh_unbound_bundle,
)
from research_harness.strategies.config import StrategyBundle
from research_harness.strategies.session import StrategySession
from research_harness.util import canonical_json, digest, write_json

REPO = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--omnigent-python", required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = REPO / "examples/strategies/research_strategy.py"
    authored = out / "authored"
    authored.mkdir()
    marker = out / "candidate-was-imported-on-host"
    # Both native runtimes can see out; the isolated candidate sees only /input.
    guard = f"import os\nif os.path.isdir({str(out)!r}):\n    open({str(marker)!r}, 'w').write('host import')\n\n"
    code = guard + source.read_text()
    (authored / "research_strategy.py").write_text(code)
    config = json.loads((REPO / "examples/strategies/research_strategy.json").read_text())
    config["source_sha256"] = digest(code)
    write_json(authored / "strategy.json", config)
    strategy = StrategyBundle.load(authored / "strategy.json")
    sources = [
        {"url": "https://fixture.example/a", "title": "Original A", "snippet": "first"},
        {"url": "https://fixture.example/b", "title": "Original B", "snippet": "second"},
    ]
    fixture_path = out / "sources.json"
    write_json(fixture_path, {"search_results": sources, "responses": []})
    settings = DiscoverySettings(max_rounds=5, deadline_seconds=90)
    direct_session = StrategySession(out / "direct-strategy", strategy)
    direct = Discovery(
        out / "direct",
        client=None,
        backend=Backend.local(out / "direct-backend"),
        search_provider=FixtureSources(fixture_path).provider,
        settings=settings,
        strategy=direct_session,
    )
    brief = "Verify isolated observation projection"
    direct.register_start(brief)
    arguments = canonical_json({"query": "offline", "filters": None})
    direct_result = direct.tool("search_sources", arguments, operation_id="search-1")
    assert direct.tool("search_sources", arguments, operation_id="search-1") == direct_result
    write_json(out / "direct-observation.json", direct_result)
    case = prepare_case(
        out / "case",
        brief=brief,
        max_spend_usd=1,
        settings=settings,
        controlled_discovery=True,
        strategy=strategy,
    )
    launch_path = case.parent / "agent/tools/mcp/research.yaml"
    launch = json.loads(launch_path.read_text())
    launch["args"] = [
        "-m",
        "research_harness.evaluation.fixture_transport",
        "--case",
        str(case),
        "--fixture",
        str(fixture_path),
    ]
    write_json(launch_path, launch)
    refresh_unbound_bundle(case)
    helper = runpy.run_path(str(REPO / "examples/omnigent/normal_runtime_fixture.py"))
    model = helper["ModelFixture"](helper["SOURCE"])
    calls = [
        ("research__begin_research", {"brief": brief}),
        ("research__search_sources", {"query": "offline", "operation_id": "search-1"}),
        ("research__search_sources", {"query": "offline", "operation_id": "search-1"}),
    ]

    def respond(request):
        model.requests.append(json.loads(request.content))
        index = len(model.responses)
        if index < len(calls):
            name, arguments = calls[index]
            output = {
                "type": "function_call",
                "id": f"fc_{index}",
                "call_id": f"call_{index}",
                "name": name,
                "arguments": json.dumps(arguments),
                "status": "completed",
            }
        else:
            output = {
                "type": "message",
                "id": f"msg_{index}",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Projection fixture complete.",
                        "annotations": [],
                    }
                ],
            }
        return httpx.Response(
            200, content=model.stream(output, index), headers={"Content-Type": "text/event-stream"}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as upstream:
        with ResponsesGateway(
            out / "gateway",
            model=MODEL,
            settings=settings,
            upstream_base_url="https://fixture.invalid/v1",
            client=upstream,
            allowed_function_names=set(DISCOVERY_TOOL_NAMES),
        ) as gateway:
            with LocalOmnigent(
                case,
                python=args.omnigent_python.expanduser().absolute(),
                env={
                    **os.environ,
                    "OPENAI_API_KEY": gateway.api_key,
                    "OPENAI_BASE_URL": gateway.base_url,
                    "OPENAI_AGENTS_DISABLE_TRACING": "1",
                },
            ) as runtime:
                snapshot = runtime.send(
                    "Execute the fixture sequence, including the exact retry.",
                    phase="discovery",
                    timeout=90,
                )
                assert snapshot["status"] == "idle", snapshot
                runtime.export_trace()
    write_json(out / "model-requests.json", model.requests)
    assert len(model.requests) == 4
    outputs = [
        item["output"]
        for item in model.requests[-1]["input"]
        if item.get("type") == "function_call_output" and item["call_id"] in {"call_1", "call_2"}
    ]
    projected = [ast.literal_eval(value) if isinstance(value, str) else value for value in outputs]
    assert len(projected) == 2 and projected[0] == projected[1]
    assert projected[0]["data"]["results"] == direct_result["results"] == list(reversed(sources))
    assert projected[0]["data"]["strategy_view"] == direct_result["strategy_view"]
    write_json(out / "omnigent-observation.json", projected[0])
    normal_session = case_strategy_session(case)
    for session, research in (
        (direct_session, out / "direct"),
        (normal_session, case.parent / "research"),
    ):
        session.assert_ready()
        state = session.status()
        assert len(state["events"]) == 1 and state["state"] == {"observations": 1}
        receipts = json.loads((research / "receipts.json").read_bytes())
        assert len(receipts) == 1 and receipts[0]["payload"]["results"] == sources
        assert "strategy_view" not in receipts[0]["payload"]
    assert not marker.exists(), "Candidate Python was imported on the host"
    write_json(
        out / "acceptance.json",
        {
            "status": "passed",
            "paid_calls": 0,
            "live_provider": False,
            "source_network": False,
            "normal_server_runner": True,
            "direct_tool_dispatch": True,
            "actual_strategy_containers": 2,
            "model_requests": len(model.requests),
            "strategy_sha256": strategy.sha256,
            "matched_projection": True,
            "stored_receipts_unchanged": True,
            "exact_retry_without_strategy_reexecution": True,
            "candidate_host_import_guard_passed": True,
            "scope": "Observation projection and recovery fixtures, not proposal-quality or optimization evaluation",
        },
    )
    print(json.dumps({"acceptance": str(out / "acceptance.json"), "requests": len(model.requests)}))


if __name__ == "__main__":
    main()
