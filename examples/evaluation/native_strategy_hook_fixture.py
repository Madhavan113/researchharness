"""Offline verification of native Omnigent observation and request-policy hooks."""

import argparse
import ast
import json
import os
import runpy
from pathlib import Path

import httpx

from research_harness.execution import DiscoverySettings
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.integrations.omnigent import (
    DISCOVERY_TOOL_NAMES,
    MODEL,
    LocalOmnigent,
    prepare_case,
    refresh_unbound_bundle,
)
from research_harness.util import write_json

REPO = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--out", required=True, type=Path)
parser.add_argument("--omnigent-python", required=True, type=Path)
args = parser.parse_args()
OUT = args.out.resolve()
OUT.mkdir()
fixture = runpy.run_path(str(REPO / "examples/omnigent/normal_runtime_fixture.py"))
model = fixture["ModelFixture"](fixture["SOURCE"])
settings = DiscoverySettings(max_rounds=4, deadline_seconds=60)
case = prepare_case(
    OUT / "case",
    brief="Offline native strategy hook probe",
    max_spend_usd=1,
    settings=settings,
    controlled_discovery=True,
    instructions="NATIVE_ORIGINAL_INSTRUCTIONS",
)
sources = [
    {"url": "https://fixture.example/a", "title": "Original A", "snippet": "first"},
    {"url": "https://fixture.example/b", "title": "Original B", "snippet": "second"},
]
write_json(OUT / "sources.json", {"search_results": sources, "responses": []})
bundle = case.parent / "agent"
launch_path = bundle / "tools/mcp/research.yaml"
launch = json.loads(launch_path.read_text())
launch["args"] = [
    "-m",
    "research_harness.evaluation.fixture_transport",
    "--case",
    str(case),
    "--fixture",
    str(OUT / "sources.json"),
]
write_json(launch_path, launch)
policy = bundle / "policies/research_discovery_policy.py"
policy.write_text(
    policy.read_text()
    + """

def fixture_projection(*, record_path):
    import json
    from pathlib import Path
    def evaluate(event):
        phase = event.get('type')
        if phase == 'llm_request':
            with Path(record_path).open('a') as handle:
                handle.write(json.dumps({'phase':phase, 'data':event.get('data')})+'\\n')
            return {'result':'ALLOW','data':{'system_prompt_preview':'LLM_REQUEST_REPLACEMENT_MARKER'}}
        if phase == 'tool_result' and event.get('target') == 'research__search_sources':
            envelope = json.loads(event['data']['result'])
            envelope['data']['results'].reverse()
            envelope['data']['results'][0]['title'] = 'TOOL_RESULT_FORMATTED_MARKER'
            envelope['strategy_stop_recommendation'] = {'recommend':True,'reason':'fixture advice only'}
            with Path(record_path).open('a') as handle:
                handle.write(json.dumps({'phase':phase,'target':event['target'],
                    'request_data':event.get('request_data')})+'\\n')
            return {'result':'ALLOW','data':json.dumps(envelope)}
        return {'result':'ALLOW'}
    return evaluate

POLICY_REGISTRY.append({'handler':'research_discovery_policy.fixture_projection','kind':'factory',
    'name':'Ephemeral fixture projection','params_schema':{'type':'object',
    'properties':{'record_path':{'type':'string'}},'required':['record_path']}})
"""
)
config_path = bundle / "config.yaml"
config = json.loads(config_path.read_text())
config["guardrails"]["policies"]["fixture_projection"] = {
    "type": "function",
    "function": {
        "path": "research_discovery_policy.fixture_projection",
        "arguments": {"record_path": str(OUT / "native-policy-events.jsonl")},
    },
}
write_json(config_path, config)
refresh_unbound_bundle(case)

calls = [
    ("research__begin_research", {"brief": "Offline native strategy hook probe"}),
    ("research__search_sources", {"query": "offline", "operation_id": "search-1"}),
]


def respond(request):
    payload = json.loads(request.content)
    model.requests.append(payload)
    index = len(model.responses)
    if index < len(calls):
        name, args = calls[index]
        output = {
            "type": "function_call",
            "id": f"fc_{index}",
            "call_id": f"call_{index}",
            "name": name,
            "arguments": json.dumps(args),
            "status": "completed",
        }
    else:
        output = {
            "type": "message",
            "id": f"msg_{index}",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": "Native hook fixture complete.", "annotations": []}
            ],
        }
    return httpx.Response(
        200, content=model.stream(output, index), headers={"Content-Type": "text/event-stream"}
    )


with httpx.Client(transport=httpx.MockTransport(respond)) as upstream:
    with ResponsesGateway(
        OUT / "gateway",
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
            runtime.send("Execute the fixture tool sequence.", phase="discovery", timeout=60)
            runtime.export_trace()
assert len(model.requests) == 3, len(model.requests)
write_json(OUT / "model-requests.json", model.requests)
returned = next(
    item["output"]
    for item in model.requests[-1]["input"]
    if item.get("type") == "function_call_output" and item["call_id"] == "call_1"
)
projected = ast.literal_eval(returned) if isinstance(returned, str) else returned
assert projected["data"]["results"][0]["url"] == sources[1]["url"], projected
assert projected["data"]["results"][0]["title"] == "TOOL_RESULT_FORMATTED_MARKER"
assert projected["strategy_stop_recommendation"]["recommend"] is True
events = [
    json.loads(line) for line in (OUT / "native-policy-events.jsonl").read_text().splitlines()
]
assert any(event["phase"] == "llm_request" for event in events), events
assert "LLM_REQUEST_REPLACEMENT_MARKER" not in json.dumps(model.requests)
assert "NATIVE_ORIGINAL_INSTRUCTIONS" in json.dumps(model.requests)
receipt = case.parent / "research/receipts.json"
stored = json.loads(receipt.read_text())
assert stored[0]["payload"]["results"] == sources
assert "TOOL_RESULT_FORMATTED_MARKER" not in receipt.read_text()
assert "Original A" in receipt.read_text() and "Original B" in receipt.read_text()
write_json(
    OUT / "acceptance.json",
    {
        "normal_server_runner": True,
        "live_provider": False,
        "source_network": False,
        "tool_result_replacement_visible": True,
        "source_order_reversed": True,
        "stop_recommendation_visible": True,
        "stored_evidence_unchanged": True,
        "llm_request_policy_called": True,
        "llm_request_replacement_ignored": True,
        "model_requests": len(model.requests),
        "native_policy_events": events,
        "llm_request_policy_invocations": sum(e["phase"] == "llm_request" for e in events),
        "receipt": str(receipt),
    },
)
write_json(OUT / "projected-observation.json", projected)
print(json.dumps({"acceptance": str(OUT / "acceptance.json"), "requests": len(model.requests)}))
