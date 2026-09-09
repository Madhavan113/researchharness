from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from research_harness.execution import DiscoverySettings
from research_harness.integrations.omnigent import (
    DISCOVERY_TOOL_NAMES,
    MODEL,
    OMNIGENT_COMMIT,
    PRIMARY_SESSION_ENV,
    LocalOmnigent,
    bind_case,
    bound_service,
    prepare_case,
    refresh_unbound_bundle,
    runtime_usage,
)
from research_harness.util import digest


def make_case(tmp_path, **kwargs):
    return prepare_case(
        tmp_path / "case", brief="Monitor official policy", max_spend_usd=1, **kwargs
    )


def test_prepare_creates_unique_authored_case_without_discovery_or_model_work(tmp_path):
    path = make_case(tmp_path)
    case = json.loads(path.read_text())
    assert case["binding"] is None
    assert case["controlled_discovery"] is False
    assert case["model"] == MODEL
    assert case["omnigent_commit"] == OMNIGENT_COMMIT
    assert not Path(case["research_output"]).exists()
    config = json.loads((path.parent / "agent/config.yaml").read_text())
    assert config["executor"]["config"]["harness"] == "openai-agents"
    assert config["executor"]["max_iterations"] == 32
    args = config["guardrails"]["policies"]["research_cost_budget"]["function"]["arguments"]
    assert args == {"max_cost_usd": 1, "expensive_models": []}
    assert "research_discovery_tools" not in config["guardrails"]["policies"]
    tool = json.loads((path.parent / "agent/tools/mcp/research.yaml").read_text())
    assert Path(tool["command"]).is_absolute()
    assert tool["args"][-1] == str(path)
    assert tool["env"] == {"RH_LOCAL_ROOT": case["backend_root"]}
    with pytest.raises(FileExistsError):
        make_case(tmp_path)
    second = prepare_case(tmp_path / "second", brief="Another case", max_spend_usd=1)
    assert json.loads(second.read_text())["case_id"] != case["case_id"]


def test_controlled_discovery_freezes_execution_policy_and_flag(tmp_path):
    path = make_case(tmp_path, controlled_discovery=True)
    case = json.loads(path.read_text())
    bundle = path.parent / "agent"
    config = json.loads((bundle / "config.yaml").read_text())
    policy = config["guardrails"]["policies"]["research_discovery_tools"]
    assert policy["on"] == ["tool_call"]
    assert policy["function"] == {
        "path": "research_discovery_policy.controlled_discovery",
        "arguments": {"case_id": case["case_id"]},
    }
    controls = json.loads((bundle / "research-tool-controls.json").read_text())
    assert controls["allowed_function_names"] == list(DISCOVERY_TOOL_NAMES)
    assert controls["host_config"] == {"policy_modules": ["research_discovery_policy"]}
    env = {PRIMARY_SESSION_ENV: "conv_controlled", "RUNNER_SERVER_URL": "http://127.0.0.1:12345"}
    _, runtime = bound_service(path, env=env)
    assert runtime["tool_controls"] == controls
    case = json.loads(path.read_text())
    case["controlled_discovery"] = False
    path.write_text(json.dumps(case))
    with pytest.raises(ValueError, match="Tool controls differ"):
        bound_service(path, env=env)
    case["controlled_discovery"] = True
    path.write_text(json.dumps(case))
    module = bundle / "policies/research_discovery_policy.py"
    module.write_text(module.read_text() + "\n# altered policy\n")
    with pytest.raises(ValueError, match="frozen case manifest"):
        bound_service(path, env=env)


def test_controlled_discovery_policy_denies_unknown_tools_without_inspecting_arguments(tmp_path):
    path = make_case(tmp_path, controlled_discovery=True)
    case_id = json.loads(path.read_text())["case_id"]
    module = runpy.run_path(str(path.parent / "agent/policies/research_discovery_policy.py"))
    assert module["ALLOWED_TOOLS"] == frozenset(DISCOVERY_TOOL_NAMES)
    policy = module["controlled_discovery"](case_id=case_id)

    def call(name, arguments=None):
        return policy(
            {"type": "tool_call", "target": name, "data": {"name": name, "arguments": arguments}}
        )["result"]

    for name in DISCOVERY_TOOL_NAMES:
        assert call(name, {}) == "ALLOW"
    for name in (
        "sys_session_rename",
        "sys_os_shell",
        "plugin__callback",
        "research__list_jobs",
        "research__start_collection",
        "research__export_observations",
        "mcp__research__get_evidence",
        "research__get_evidence_suffix",
        None,
        [],
    ):
        assert call(name, {"number": 10**100, "value": float("inf")}) == "DENY"
    bootstrap = {"agent_name": case_id, "harness": "openai-agents"}
    assert call("sys_agent_start", bootstrap) == "ALLOW"
    assert call("sys_agent_start", {**bootstrap, "agent_name": "other"}) == "DENY"
    assert call("sys_agent_start", {**bootstrap, "harness": "shell"}) == "DENY"
    for malformed in (None, [], {}, {"type": "tool_call", "target": "research__get_evidence"}):
        assert policy(malformed)["result"] == "DENY"
    for phase in ("request", "response", "llm_request", "llm_response", "tool_result"):
        assert policy({"type": phase})["result"] == "ALLOW"
    with pytest.raises(ValueError, match="frozen research case"):
        module["controlled_discovery"](case_id="other")


def test_controlled_discovery_flag_cannot_enable_policy_on_legacy_case(tmp_path):
    path = make_case(tmp_path)
    case = json.loads(path.read_text())
    case["controlled_discovery"] = True
    path.write_text(json.dumps(case))
    with pytest.raises(ValueError, match="Tool controls differ"):
        bind_case(path, session_id="conv_alpha", server_url="http://127.0.0.1:12345")


def test_normal_runner_denies_unadvertised_registered_tools_before_execution(tmp_path):
    python = os.environ.get("RH_TEST_OMNIGENT_PYTHON")
    if not python:
        pytest.skip("Set RH_TEST_OMNIGENT_PYTHON for the pinned normal runtime policy regression")
    from research_harness.integrations.model_gateway import ResponsesGateway

    repo = Path(__file__).resolve().parents[1]
    fixture = runpy.run_path(str(repo / "examples/omnigent/normal_runtime_fixture.py"))
    model = fixture["ModelFixture"](fixture["SOURCE"])
    calls = [
        ("research__begin_research", {"brief": "Offline tool policy regression"}),
        ("sys_session_rename", {"title": "FORBIDDEN RENAME"}),
        ("research__list_jobs", {}),
        ("research__get_research_context", {}),
    ]

    def respond(request):
        payload = json.loads(request.content)
        model.requests.append(payload)
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
                "content": [{"type": "output_text", "text": "Policy verified.", "annotations": []}],
            }
        return httpx.Response(
            200, content=model.stream(output, index), headers={"Content-Type": "text/event-stream"}
        )

    settings = DiscoverySettings(max_rounds=6, deadline_seconds=90)
    path = make_case(tmp_path, controlled_discovery=True, settings=settings)
    with httpx.Client(transport=httpx.MockTransport(respond)) as upstream:
        with ResponsesGateway(
            tmp_path / "gateway",
            model=MODEL,
            settings=settings,
            upstream_base_url="https://fixture.invalid/v1",
            client=upstream,
            allowed_function_names=set(DISCOVERY_TOOL_NAMES),
        ) as gateway:
            with LocalOmnigent(
                path,
                python=Path(python),
                env={
                    **os.environ,
                    "OPENAI_API_KEY": gateway.api_key,
                    "OPENAI_BASE_URL": gateway.base_url,
                    "OPENAI_AGENTS_DISABLE_TRACING": "1",
                },
            ) as runtime:
                before = runtime.snapshot()
                runtime.send("Exercise the offline policy fixture.", phase="discovery", timeout=90)
                after = runtime.snapshot()
                assert before["title"] == after["title"]
    assert len(model.requests) == 5
    for payload in model.requests:
        assert {tool["name"] for tool in payload["tools"]} == set(DISCOVERY_TOOL_NAMES)
    outputs = {
        item["call_id"]: str(item["output"])
        for payload in model.requests
        for item in payload["input"]
        if item.get("type") == "function_call_output"
    }
    assert "discovery_id" in outputs["call_0"]
    assert "Denied by policy" in outputs["call_1"]
    assert "Denied by policy" in outputs["call_2"]
    assert "discovery_id" in outputs["call_3"]
    first = json.loads((tmp_path / "gateway/request-0001/incoming.json").read_text())
    registered = {tool["name"] for tool in first["tools"]}
    assert {"sys_session_rename", "research__list_jobs"} <= registered
    assert "sys_agent_start" not in registered
    # The runtime's policy import must leave the frozen bundle unchanged.
    bind_case(path, session_id=runtime.session_id, server_url=runtime.base_url)


def test_case_binding_requires_host_identity_and_rejects_other_conversation(tmp_path):
    path = make_case(tmp_path)
    with pytest.raises(ValueError, match="dedicated Omnigent runner"):
        bound_service(path, env={})
    first = bind_case(path, session_id="conv_alpha", server_url="http://127.0.0.1:12345")
    again = bind_case(path, session_id="conv_alpha", server_url="http://127.0.0.1:12345/")
    assert first == again
    with pytest.raises(ValueError, match="another Omnigent session"):
        bind_case(path, session_id="conv_beta", server_url="http://127.0.0.1:12345")
    with pytest.raises(ValueError, match="another Omnigent session"):
        bind_case(path, session_id="conv_alpha", server_url="http://127.0.0.1:12346")
    with pytest.raises(ValueError, match="Invalid Omnigent session"):
        bind_case(path, session_id="../../case", server_url="http://127.0.0.1:12345")
    assert json.loads(path.read_text()) == first


def test_bundle_fingerprint_rejects_changed_config_on_resume_and_bound_refresh(tmp_path):
    path = make_case(tmp_path)
    bound = bind_case(path, session_id="conv_alpha", server_url="http://127.0.0.1:12345")
    assert bound["authored_bundle_sha256"] and bound["authored_config_sha256"]
    config = path.parent / "agent/config.yaml"
    changed = json.loads(config.read_text())
    changed["executor"]["model"] = "changed-model"
    config.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="frozen case manifest"):
        bound_service(
            path,
            env={PRIMARY_SESSION_ENV: "conv_alpha", "RUNNER_SERVER_URL": "http://127.0.0.1:12345"},
        )
    with pytest.raises(ValueError, match="bound case"):
        refresh_unbound_bundle(path)


def test_explicit_settings_and_instructions_are_frozen_and_recorded(tmp_path):
    controls = DiscoverySettings(max_rounds=9, max_searches=2, max_inspections=3, max_probes=4)
    instructions = "Use the shared research strategy.\nKeep the exact second line.\n"
    path = make_case(tmp_path, settings=controls, instructions=instructions)
    case = json.loads(path.read_text())
    assert case["discovery_settings"] == controls.model_dump(mode="json")
    assert (path.parent / "agent/instructions.md").read_text() == instructions
    config = json.loads((path.parent / "agent/config.yaml").read_text())
    assert config["executor"]["max_iterations"] == 9
    assert config["executor"]["reasoning_effort"] == "none"
    assert config["llm"]["max_tokens"] == 6000
    env = {PRIMARY_SESSION_ENV: "conv_settings", "RUNNER_SERVER_URL": "http://127.0.0.1:12345"}
    _, runtime = bound_service(path, env=env)
    assert runtime["discovery_settings"] == controls.model_dump(mode="json")
    assert runtime["model_settings"] == controls.model_settings()
    assert runtime["budgets"] == controls.budgets()
    case["discovery_settings"]["max_rounds"] = 8
    path.write_text(json.dumps(case))
    with pytest.raises(ValueError, match="Discovery settings differ"):
        bound_service(path, env=env)


def test_host_bound_service_restores_saved_discovery_and_runtime(tmp_path):
    path = make_case(tmp_path)
    env = {PRIMARY_SESSION_ENV: "conv_alpha", "RUNNER_SERVER_URL": "http://127.0.0.1:12345"}
    service, runtime = bound_service(path, env=env)
    assert service.discovery_id is None
    ids = service.begin("Monitor official policy", model=MODEL, runtime=runtime)
    recovered, restored = bound_service(path, env=env)
    assert recovered.discovery_id == ids["discovery_id"]
    assert restored == runtime == recovered.get_context()["runtime"]
    assert restored["session_id"] == "conv_alpha"
    assert restored["strategy_sha256"]
    with pytest.raises(ValueError, match="another Omnigent session"):
        bound_service(path, env={**env, PRIMARY_SESSION_ENV: "conv_beta"})


@pytest.mark.parametrize("spend", [0, -1, 101, float("nan"), float("inf")])
def test_case_budget_must_be_a_bounded_positive_amount(tmp_path, spend):
    with pytest.raises(ValueError, match="spend limit"):
        prepare_case(tmp_path / "case", brief="Research", max_spend_usd=spend)


def usage_event(response_id, *, input_tokens=100, output_tokens=10, status="completed"):
    return {
        "event": "response." + status,
        "phase": "discovery" if response_id == "r1" else "followup",
        "data": {
            "response": {
                "id": response_id,
                "model": "research-agent-bundle-name",
                "status": status,
                "usage": {
                    "model": MODEL,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            }
        },
    }


def usage(events, phase="discovery"):
    raw = "\n".join(json.dumps(event) for event in events).encode()
    return runtime_usage(
        raw,
        context={"question_id": "q1", "discovery_id": "d1"},
        model=MODEL,
        phase=phase,
        response_phases={"r1": "discovery", "r2": "followup"},
        source_event_file=f"events/{digest(raw)}.jsonl",
    )


def test_usage_is_bound_to_discovery_and_excludes_followups_and_duplicate_delivery():
    events = [usage_event("r1"), usage_event("r2", input_tokens=900), usage_event("r1")]
    report = usage(events)
    assert report["question_id"] == "q1" and report["discovery_id"] == "d1"
    assert report["totals"] == {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    assert list(report["responses"]) == ["r1"]
    assert report["responses"]["r1"]["model"] == MODEL
    assert report["complete"] is False and report["auxiliary_usage_unknown"] is True
    assert report["interrupted"] is False
    assert usage(events, phase="workflow")["totals"]["input_tokens"] == 1000
    assert usage(events, phase="followup")["totals"]["input_tokens"] == 900


def test_usage_preserves_unknown_and_interrupted_usage_and_rejects_conflicting_replay():
    report = usage([usage_event("r1", input_tokens=None, output_tokens=None, status="cancelled")])
    assert report["responses"]["r1"]["input_tokens"] is None
    assert report["interrupted"] is True
    active = usage([usage_event("r1", status="in_progress")])
    assert active["responses"] == {} and active["interrupted"] is True
    with pytest.raises(ValueError, match="Conflicting terminal usage"):
        usage([usage_event("r1"), usage_event("r1", input_tokens=999)])
    foreign = {**usage_event("r1"), "phase": "followup"}
    with pytest.raises(ValueError, match="Source event phase differs"):
        usage([foreign])


def test_normal_server_runner_mcp_path_saves_and_recovers_a_pipeline(tmp_path):
    python = os.environ.get("RH_TEST_OMNIGENT_PYTHON")
    if not python:
        pytest.skip(
            "Set RH_TEST_OMNIGENT_PYTHON to the pinned separate runtime for offline process verification"
        )
    repo = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(repo / "examples/omnigent/normal_runtime_fixture.py"),
            "--out",
            str(tmp_path / "normal-runtime"),
            "--omnigent-python",
            python,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=repo,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    acceptance = json.loads((tmp_path / "normal-runtime/acceptance.json").read_text())
    assert acceptance["normal_server_runner"]
    assert acceptance["server_runner_restart_preserved_case"]
    assert acceptance["saved"]["proposal_id"]
    assert acceptance["saved"]["pipeline_version_id"]
    assert acceptance["live_provider_verified"] is False
    assert acceptance["collection_and_export_reopened"]
    assert acceptance["usage_scopes_verified"]
    assert acceptance["source_http_requests"] >= 3
