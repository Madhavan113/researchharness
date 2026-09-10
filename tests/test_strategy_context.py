from __future__ import annotations

import json
import os
import runpy
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

import research_harness.strategies.session as session_module
from research_harness.evaluation.gateway_usage import verify_gateway_usage
from research_harness.execution import DiscoverySettings, GatewayBinding
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.strategies.config import StrategyBundle
from research_harness.strategies.context import (
    apply_context_decision,
    context_payload,
    project_context,
    validate_context_decision,
)
from research_harness.strategies.sandbox import SandboxConfig
from research_harness.strategies.session import StrategySession
from research_harness.util import canonical_json, digest, write_json

IMAGE = "python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285"
MODEL = "gpt-5.4-mini"
BINDING = GatewayBinding(
    execution_id="a" * 32,
    case_id="context-fixture",
    runtime="direct-controlled-gateway",
    task_sha256="b" * 64,
)
CODE = """def apply(event):
    return {'decision': {'keep_group_ids': event['payload']['required_group_ids']},
            'state': {'calls': event['state'].get('calls', 0) + 1}}
"""


def history():
    return [
        {"role": "system", "content": "Frozen host rules"},
        {"role": "user", "content": "Previous user task"},
        {"type": "reasoning", "id": "rs_old", "summary": [], "encrypted_content": "opaque-old"},
        {
            "type": "function_call",
            "id": "fc_a",
            "call_id": "a",
            "name": "search_sources",
            "arguments": "{}",
            "status": "completed",
        },
        {
            "type": "function_call",
            "id": "fc_b",
            "call_id": "b",
            "name": "search_sources",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "b", "output": "Result B"},
        {"type": "function_call_output", "call_id": "a", "output": "Result A"},
        {
            "type": "message",
            "id": "msg_old",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "Previous answer", "annotations": []}],
        },
        {"role": "user", "content": "Current user task"},
        {"type": "reasoning", "id": "rs_new", "summary": [], "encrypted_content": "opaque-current"},
        {
            "type": "function_call",
            "id": "fc_c",
            "call_id": "c",
            "name": "search_sources",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "c", "output": "Current result"},
    ]


def request():
    return {
        "model": MODEL,
        "input": history(),
        "instructions": "Fixed instructions",
        "tools": [
            {
                "type": "function",
                "name": "search_sources",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        "reasoning": {"effort": "none"},
    }


def selection(request):
    return {"keep_group_ids": context_payload(request)["required_group_ids"]}


def bundle(tmp_path, **config):
    root = tmp_path / "authored"
    root.mkdir()
    (root / "strategy.py").write_text(CODE)
    write_json(
        root / "strategy.json",
        {
            "schema_version": 1,
            "source": "strategy.py",
            "source_sha256": digest(CODE),
            "sandbox": SandboxConfig(
                image=os.environ.get("RH_TEST_STRATEGY_IMAGE", IMAGE)
            ).model_dump(mode="json"),
            "observations": False,
            "context": True,
            **config,
        },
    )
    return StrategyBundle.load(root / "strategy.json")


@pytest.fixture
def fake_runner(monkeypatch):
    """Authored sandbox evidence for host tests, without executing candidate code."""

    class FakeRunner:
        calls = 0
        gate = None
        entered = threading.Event()

        def __init__(self, config):
            self.config = config

        def execute(self, source, event, output):
            type(self).calls += 1
            self.entered.set()
            if self.gate is not None:
                assert self.gate.wait(10), "Fixture was not released"
            inputs = output / "input"
            inputs.mkdir(parents=True)
            (inputs / "strategy.py").write_bytes(source.read_bytes())
            (inputs / "request.json").write_text(canonical_json(event))
            (inputs / "worker.py").write_text("# authored sandbox fixture\n")
            raw = {
                "decision": {"keep_group_ids": event["payload"]["required_group_ids"]},
                "state": {"calls": event["state"].get("calls", 0) + 1},
            }
            write_json(output / "decision.json", raw)
            (output / "stdout.txt").write_text(canonical_json(raw))
            (output / "stderr.txt").write_text("")
            write_json(
                output / "execution.json",
                {
                    "status": "completed",
                    "cleanup": "removed",
                    "configuration": self.config.model_dump(mode="json"),
                    "source_sha256": digest(source.read_bytes()),
                    "request_sha256": digest(canonical_json(event)),
                    "output_truncated": False,
                    "output_collection_complete": True,
                    "artifact_hashes": {
                        p.relative_to(output).as_posix(): digest(p.read_bytes())
                        for p in output.rglob("*")
                        if p.is_file()
                    },
                },
            )
            return raw

    monkeypatch.setattr(session_module, "DockerStrategyRunner", FakeRunner)
    return FakeRunner


def test_complete_old_interaction_can_be_removed_without_changing_controls_or_active_turn():
    original = request()
    frozen = deepcopy(original)
    payload = context_payload(original)
    assert len(payload["groups"]) == 5
    assert payload["groups"][2]["items"] == history()[2:8]
    assert payload["groups"][2]["required"] is False
    projected = apply_context_decision(original, selection(original))
    assert projected["input"] == history()[:2] + history()[8:]
    assert {key: value for key, value in projected.items() if key != "input"} == {
        key: value for key, value in original.items() if key != "input"
    }
    assert original == frozen
    assert "opaque-current" in canonical_json(projected)
    assert "opaque-old" not in canonical_json(projected)


def test_active_single_brief_interaction_is_entirely_required():
    original = {"input": history()[:8]}
    payload = context_payload(original)
    assert all(group["required"] for group in payload["groups"])
    assert apply_context_decision(original, selection(original)) == original


def test_mid_interaction_instruction_keeps_its_entire_dependency_group():
    original = request()
    original["input"].insert(4, {"role": "developer", "content": "Always retain this"})
    payload = context_payload(original)
    assert all(group["required"] for group in payload["groups"])
    assert apply_context_decision(original, selection(original)) == original


def test_string_input_and_repeated_identical_user_messages_are_preserved():
    original = {"input": "Exact brief", "instructions": "Unchanged"}
    assert apply_context_decision(original, selection(original)) == original
    duplicate = {"input": [{"role": "user", "content": "Same"}] * 2}
    ids = context_payload(duplicate)["required_group_ids"]
    assert len(set(ids)) == 2


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["keep_group_ids"].reverse(),
        lambda p: p["keep_group_ids"].append(p["keep_group_ids"][0]),
        lambda p: p["keep_group_ids"].append("invented"),
        lambda p: p["keep_group_ids"].pop(),
        lambda p: p.update(input=[]),
        lambda p: p.update(keep_group_ids="all"),
    ],
)
def test_candidate_cannot_reorder_duplicate_invent_remove_required_or_replace_messages(change):
    payload = context_payload(request())
    decision = selection(request())
    change(decision)
    with pytest.raises(ValueError):
        validate_context_decision(payload, decision)


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"role": "assistant", "content": "No task"}],
        [{"role": "user", "content": [{"type": "input_image", "image_url": "x"}]}],
        [{"role": "user", "content": "Task"}, {"type": "item_reference", "id": "remote"}],
        [
            {"role": "user", "content": "Task"},
            {"type": "configuration_update", "reasoning": {"effort": "high"}},
        ],
        history()[:-1],
        history() + [history()[-1]],
        history() + [{"type": "function_call_output", "call_id": "unknown", "output": "x"}],
        history()[:4] + [{"role": "user", "content": "Interrupt pending call"}],
        [{"role": [], "content": "invalid"}],
        [{"role": "user", "content": "Task"}, {"type": "reasoning", "summary": []}],
    ],
)
def test_unsupported_or_unmatched_history_is_rejected(items):
    with pytest.raises(ValueError):
        context_payload({"input": items})


def test_group_manifest_tampering_is_rebuilt_and_rejected():
    payload = context_payload(request())
    payload["groups"][2]["required"] = True
    with pytest.raises(ValueError, match="requirements changed"):
        validate_context_decision(payload, selection(request()))
    with pytest.raises(ValueError, match="Provider-managed"):
        context_payload({**request(), "previous_response_id": "remote"})


def test_shared_session_projects_once_and_replays_completed_decision(tmp_path, fake_runner):
    session = StrategySession(tmp_path / "strategy", bundle(tmp_path))
    first = project_context(session, request(), operation_id="context:one")
    assert project_context(session, request(), operation_id="context:one") == first
    assert fake_runner.calls == 1
    proof = session.event_record("context:one")
    assert proof["input"]["payload"] == context_payload(request())
    assert proof["result"]["state"] == {"calls": 1}


def provider(request):
    return httpx.Response(
        200,
        json={
            "id": "resp-1",
            "model": MODEL,
            "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        },
    )


def post(gateway, payload=None):
    return httpx.post(
        gateway.base_url + "/responses",
        json=payload or request(),
        headers={"Authorization": "Bearer " + gateway.api_key},
        timeout=20,
    )


def verify(path, session):
    return verify_gateway_usage(
        path, BINDING, expected_model=MODEL, expected_strategy_sha256=session.bundle.sha256
    )


def reindex(path):
    write_json(
        path / "archive.json",
        {
            "schema_version": 1,
            "files": {
                p.relative_to(path).as_posix(): digest(p.read_bytes())
                for p in path.rglob("*")
                if p.is_file() and p != path / "archive.json"
            },
        },
    )


@pytest.fixture
def projected_archive(tmp_path, fake_runner):
    session = StrategySession(tmp_path / "strategy", bundle(tmp_path))
    received = []

    def model(request):
        received.append(json.loads(request.content))
        return provider(request)

    output = tmp_path / "gateway"
    with httpx.Client(transport=httpx.MockTransport(model)) as client:
        with ResponsesGateway(
            output,
            model=MODEL,
            settings=DiscoverySettings(),
            upstream_base_url="https://fixture.invalid/v1",
            client=client,
            binding=BINDING,
            strategy=session,
        ) as gateway:
            assert post(gateway).status_code == 200
    assert received[0]["input"] == history()[:2] + history()[8:]
    return output, session


def test_gateway_archives_exact_original_projection_and_independently_verified_event(
    projected_archive,
):
    path, session = projected_archive
    result = verify(path, session)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["total_tokens"] == 110
    assert result["strategy_control_verified"]
    record = json.loads((path / "request-0001/record.json").read_text())
    assert record["context_projection_pending"] is False
    assert json.loads((path / "request-0001/incoming.json").read_text())["input"] == history()
    assert (path / "request-0001/strategy-event/execution/input/strategy.py").read_text() == CODE


@pytest.mark.parametrize(
    "name",
    [
        "input.json",
        "result.json",
        "record.json",
        "execution/stdout.txt",
        "execution/input/strategy.py",
    ],
)
def test_verifier_rejects_tampered_isolated_context_evidence_even_when_outer_archive_rehashed(
    projected_archive, name
):
    path, session = projected_archive
    target = path / "request-0001/strategy-event" / name
    target.write_text(target.read_text() + " ")
    reindex(path)
    assert verify(path, session)["status"] == "invalid"


def test_verifier_rejects_projection_that_changes_fixed_controls_after_all_projection_hashes_update(
    projected_archive,
):
    path, session = projected_archive
    directory = path / "request-0001"
    projected = json.loads((directory / "projected.json").read_text())
    projected["instructions"] = "Altered host instructions"
    write_json(directory / "projected.json", projected)
    digest_projected = digest((directory / "projected.json").read_bytes())
    proof = json.loads((directory / "context-projection.json").read_text())
    proof["projected_sha256"] = digest_projected
    write_json(directory / "context-projection.json", proof)
    report = json.loads((path / "report.json").read_text())
    report["requests"][0].update(
        projected_sha256=digest_projected,
        context_projection_sha256=digest((directory / "context-projection.json").read_bytes()),
    )
    write_json(directory / "record.json", report["requests"][0])
    write_json(path / "report.json", report)
    reindex(path)
    result = verify(path, session)
    assert result["status"] == "invalid"
    assert any("validated selection" in error for error in result["errors"])


def test_verifier_revalidates_protocol_decision_after_all_nested_hashes_are_updated(
    projected_archive,
):
    path, session = projected_archive
    directory = path / "request-0001"
    event = directory / "strategy-event"
    result = json.loads((event / "result.json").read_text())
    result["decision"] = {"keep_group_ids": []}
    write_json(event / "result.json", result)
    write_json(event / "execution/decision.json", result)
    (event / "execution/stdout.txt").write_text(canonical_json(result))
    execution = json.loads((event / "execution/execution.json").read_text())
    execution["artifact_hashes"] = {
        p.relative_to(event / "execution").as_posix(): digest(p.read_bytes())
        for p in (event / "execution").rglob("*")
        if p.is_file() and p != event / "execution/execution.json"
    }
    write_json(event / "execution/execution.json", execution)
    record = json.loads((event / "record.json").read_text())
    record["result_sha256"] = digest(canonical_json(result))
    record["files"] = {
        p.relative_to(event).as_posix(): digest(p.read_bytes())
        for p in event.rglob("*")
        if p.is_file() and p != event / "record.json"
    }
    write_json(event / "record.json", record)
    proof = json.loads((directory / "context-projection.json").read_text())
    proof["event_files"] = {
        **record["files"],
        "record.json": digest((event / "record.json").read_bytes()),
    }
    write_json(directory / "context-projection.json", proof)
    report = json.loads((path / "report.json").read_text())
    report["requests"][0]["context_projection_sha256"] = digest(
        (directory / "context-projection.json").read_bytes()
    )
    write_json(directory / "record.json", report["requests"][0])
    write_json(path / "report.json", report)
    reindex(path)
    checked = verify(path, session)
    assert checked["status"] == "invalid"
    assert any("Required context groups" in error for error in checked["errors"])


def test_observation_only_session_failure_is_checked_again_before_dispatch(
    tmp_path, fake_runner, monkeypatch
):
    session = StrategySession(
        tmp_path / "strategy", bundle(tmp_path, observations=True, context=False)
    )
    calls = 0

    def readiness():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("A concurrent observation failed")

    monkeypatch.setattr(session, "assert_ready", readiness)
    output = tmp_path / "gateway"
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("No provider dispatch"))
    ) as client:
        with ResponsesGateway(
            output,
            model=MODEL,
            settings=DiscoverySettings(),
            upstream_base_url="https://fixture.invalid/v1",
            client=client,
            binding=BINDING,
            strategy=session,
        ) as gateway:
            assert post(gateway).status_code == 400
    result = verify(output, session)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["total_tokens"] == result["dispatched_requests"] == 0


def test_close_settles_strategy_writer_before_publishing_archive_and_blocks_late_dispatch(
    tmp_path, fake_runner
):
    session = StrategySession(tmp_path / "strategy", bundle(tmp_path))
    fake_runner.gate = threading.Event()
    output = tmp_path / "gateway"
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("No provider dispatch"))
    ) as client:
        gateway = ResponsesGateway(
            output,
            model=MODEL,
            settings=DiscoverySettings(),
            upstream_base_url="https://fixture.invalid/v1",
            client=client,
            binding=BINDING,
            strategy=session,
        ).start()
        with ThreadPoolExecutor() as pool:
            sending = pool.submit(post, gateway)
            assert fake_runner.entered.wait(5)
            closing = pool.submit(gateway.close)
            assert gateway._closing.wait(5)
            assert not closing.done() and not (output / "archive.json").exists()
            fake_runner.gate.set()
            closing.result(timeout=10)
            try:
                sending.result(timeout=10)
            except httpx.HTTPError:
                pass
    result = verify(output, session)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["total_tokens"] == 0
    assert session.status()["status"] == "ready"
    assert (
        json.loads((output / "request-0001/record.json").read_text())["context_projection_pending"]
        is True
    )


def test_actual_docker_context_selection_and_gateway_verification(tmp_path):
    if not os.environ.get("RH_TEST_STRATEGY_IMAGE"):
        pytest.skip("Set RH_TEST_STRATEGY_IMAGE for actual isolated context execution")
    session = StrategySession(tmp_path / "strategy", bundle(tmp_path))
    output = tmp_path / "gateway"
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        with ResponsesGateway(
            output,
            model=MODEL,
            settings=DiscoverySettings(),
            upstream_base_url="https://fixture.invalid/v1",
            client=client,
            binding=BINDING,
            strategy=session,
        ) as gateway:
            assert post(gateway).status_code == 200
    result = verify(output, session)
    assert result["status"] == "verified_complete", result["errors"]
    assert session.status()["state"] == {"calls": 1}


def test_budget_reserves_final_projected_request_only(tmp_path, fake_runner):
    from research_harness.evaluation.budget import BudgetLedger, RateCard
    from research_harness.evaluation.dispatch_budget import DispatchBudget, DispatchPolicy

    settings = DiscoverySettings(service_tier="default")
    session = StrategySession(tmp_path / "strategy", bundle(tmp_path))
    budget = DispatchBudget(
        BudgetLedger(
            tmp_path / "ledger.json",
            rates=RateCard(
                model=MODEL,
                snapshot=MODEL,
                input_usd_per_million="1",
                output_usd_per_million="5",
                max_input_tokens_per_request=1000,
                price_source_url="https://fixture.invalid/prices",
                price_as_of="2026-09-08",
            ),
            ceiling_usd="1",
        ),
        policy=DispatchPolicy(
            mode="fixture", model=MODEL, upstream_base_url="https://fixture.invalid/v1"
        ),
        settings=settings,
        binding=BINDING,
    )
    output = tmp_path / "gateway"

    def upstream(http_request):
        payload = json.loads(http_request.content)
        assert payload["input"] == history()[:2] + history()[8:]
        record = json.loads((output / "request-0001/record.json").read_text())
        row = budget.ledger.snapshot()["reservations"][record["budget_operation_id"]]
        assert row["status"] == "dispatched"
        assert row["request_sha256"] == record["forwarded_sha256"] == digest(http_request.content)
        assert record["context_projection_pending"] is False
        return httpx.Response(
            200, json={**provider(http_request).json(), "service_tier": "default"}
        )

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        with ResponsesGateway(
            output,
            model=MODEL,
            settings=settings,
            upstream_base_url="https://fixture.invalid/v1",
            client=client,
            binding=BINDING,
            strategy=session,
            dispatch_budget=budget,
        ) as gateway:
            assert post(gateway).status_code == 200
    result = verify_gateway_usage(
        output,
        BINDING,
        expected_model=MODEL,
        expected_strategy_sha256=session.bundle.sha256,
        expected_budget_control=budget.metadata(),
        expected_model_settings=settings.model_settings(),
        expected_budgets=settings.budgets(),
    )
    assert result["status"] == "verified_complete", result["errors"]
    assert result["total_tokens"] == 110
    # Independent reconciliation, rather than the gateway's own parser, grants credit.
    budget.reconcile_archive(output)
    assert next(iter(budget.ledger.snapshot()["reservations"].values()))["status"] == "settled"


def test_deadline_during_context_execution_never_reserves_or_dispatches(tmp_path, fake_runner):
    session = StrategySession(tmp_path / "strategy", bundle(tmp_path))
    fake_runner.gate = threading.Event()
    settings = DiscoverySettings(deadline_seconds=1)
    output = tmp_path / "gateway"
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("No provider dispatch"))
    ) as client:
        with ResponsesGateway(
            output,
            model=MODEL,
            settings=settings,
            upstream_base_url="https://fixture.invalid/v1",
            client=client,
            binding=BINDING,
            strategy=session,
        ) as gateway:
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(post, gateway)
                assert fake_runner.entered.wait(5)
                try:
                    with pytest.raises(httpx.HTTPError):
                        pending.result(timeout=4)
                    record = json.loads((output / "request-0001/record.json").read_text())
                    assert record["reason"] == "deadline_exhausted"
                    assert "reserved_attempt_number" not in record
                finally:
                    fake_runner.gate.set()
    result = verify(output, session)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["total_tokens"] == result["dispatched_requests"] == 0
    assert session.status()["status"] == "ready"


def test_external_strategy_identity_cannot_be_replaced(projected_archive):
    path, _ = projected_archive
    result = verify_gateway_usage(
        path, BINDING, expected_model=MODEL, expected_strategy_sha256="0" * 64
    )
    assert result["status"] == "invalid"


def test_actual_normal_runner_followup_prunes_only_the_previous_completed_interaction(tmp_path):
    python = os.environ.get("RH_TEST_OMNIGENT_PYTHON")
    if not python or not os.environ.get("RH_TEST_STRATEGY_IMAGE"):
        pytest.skip(
            "Set RH_TEST_OMNIGENT_PYTHON and RH_TEST_STRATEGY_IMAGE for normal runner context proof"
        )
    from research_harness.integrations.omnigent import (
        DISCOVERY_TOOL_NAMES,
        LocalOmnigent,
        prepare_case,
    )

    fixture = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/omnigent/normal_runtime_fixture.py")
    )
    model = fixture["ModelFixture"](fixture["SOURCE"])
    calls = [
        ("begin_research", {"brief": "Offline conversation context fixture"}),
        ("get_research_context", {}),
        None,
        ("get_research_context", {}),
        None,
    ]

    def respond(http_request):
        payload = json.loads(http_request.content)
        model.requests.append(payload)
        index = len(model.responses)
        call = calls[index]
        if call:
            raw = model.tool_stream(payload, index, *call)
        else:
            raw = model.stream(
                {
                    "type": "message",
                    "id": f"msg_{index}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Context fixture complete.",
                            "annotations": [],
                        }
                    ],
                },
                index,
            )
        return httpx.Response(200, content=raw, headers={"Content-Type": "text/event-stream"})

    settings = DiscoverySettings(max_rounds=8, deadline_seconds=120)
    session = StrategySession(tmp_path / "strategy", bundle(tmp_path))
    binding = BINDING.model_copy(
        update={"runtime": "omnigent-controlled-gateway", "phase": "workflow"}
    )
    case = prepare_case(
        tmp_path / "case",
        brief="Offline conversation context fixture",
        max_spend_usd=1,
        settings=settings,
        controlled_discovery=True,
        search_endpoint="https://fixture.invalid/mcp",
    )
    output = tmp_path / "gateway"
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with ResponsesGateway(
            output,
            model=MODEL,
            settings=settings,
            upstream_base_url="https://fixture.invalid/v1",
            client=client,
            binding=binding,
            strategy=session,
            allowed_function_names=set(DISCOVERY_TOOL_NAMES),
        ) as gateway:
            with LocalOmnigent(
                case,
                python=Path(python),
                env={
                    **os.environ,
                    "OPENAI_API_KEY": gateway.api_key,
                    "OPENAI_BASE_URL": gateway.base_url,
                    "OPENAI_AGENTS_DISABLE_TRACING": "1",
                },
            ) as runtime:
                first = runtime.send(
                    "Begin the offline context fixture.", phase="workflow", timeout=120
                )
                second = runtime.send(
                    "Reopen the same research context.", phase="followup", timeout=120
                )
                write_json(tmp_path / "turn-results.json", {"first": first, "second": second})
    assert len(model.requests) == 5
    for index in range(1, 4):
        directory = output / f"request-{index:04d}"
        incoming = json.loads((directory / "incoming.json").read_text())
        projected = json.loads((directory / "projected.json").read_text())
        assert incoming["input"] == projected["input"]
    directory = output / "request-0004"
    incoming = json.loads((directory / "incoming.json").read_text())
    projected = json.loads((directory / "projected.json").read_text())
    assert len(projected["input"]) < len(incoming["input"])
    assert not any(
        item.get("type") in {"function_call", "function_call_output"} for item in projected["input"]
    )
    assert [item for item in projected["input"] if item.get("role") == "user"] == [
        item for item in incoming["input"] if item.get("role") == "user"
    ]
    assert any(
        item.get("type") == "function_call_output" and "discovery_id" in str(item["output"])
        for item in model.requests[-1]["input"]
    )
    result = verify_gateway_usage(
        output,
        binding,
        expected_model=MODEL,
        expected_strategy_sha256=session.bundle.sha256,
        expected_model_settings=settings.model_settings(),
        expected_budgets=settings.budgets(),
    )
    write_json(tmp_path / "verified-usage.json", result)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["total_tokens"] == 550
    assert session.status()["state"] == {"calls": 5}
