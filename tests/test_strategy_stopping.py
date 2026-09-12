"""Host finalization contracts; isolated actual-runtime coverage is in comparison_runtime."""

from __future__ import annotations

import json

import anyio
import httpx
import pytest
from openai import OpenAI
from test_discovery import api_response, draft
from test_strategy_context import BINDING, MODEL, post, provider, reindex
from test_strategy_session import bundle, receipt

from research_harness.backend import Backend
from research_harness.config import SourceSpec
from research_harness.discovery import Discovery
from research_harness.evaluation.fixtures import FixtureProvider
from research_harness.evaluation.gateway_usage import verify_gateway_usage
from research_harness.execution import DiscoverySettings
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.mcp.server import create_server
from research_harness.services.research import ResearchService
from research_harness.strategies.config import StrategyConfig
from research_harness.strategies.session import StrategySession, StrategySessionError
from research_harness.strategies.stopping import ResearchFinalizing, finalize_request, stop_views
from research_harness.util import canonical_json, digest, write_json

memory = pytest.importorskip("mcp.shared.memory")


@pytest.fixture
def stopping_runner(fake_runner, monkeypatch):
    """Supply authored worker output; never import or execute candidate source here."""
    original = fake_runner.execute
    fake_runner.stop_tools = {"probe_source"}
    fake_runner.stop_value = True

    def execute(self, source, event, output):
        raw = original(self, source, event, output)
        raw["decision"]["stop_recommended"] = (
            self.stop_value if event["payload"]["tool"] in self.stop_tools else False
        )
        write_json(output / "decision.json", raw)
        (output / "stdout.txt").write_text(canonical_json(raw))
        metadata = json.loads((output / "execution.json").read_bytes())
        metadata["artifact_hashes"] = {
            name: digest((output / name).read_bytes()) for name in metadata["artifact_hashes"]
        }
        write_json(output / "execution.json", metadata)
        return raw

    monkeypatch.setattr(fake_runner, "execute", execute)
    return fake_runner


def session(tmp_path):
    return StrategySession(tmp_path / "strategy", bundle(tmp_path, finalize_on_stop=True))


def search_stop(strategy, runner):
    runner.stop_tools = {"search_sources"}
    return strategy.project("search_sources", receipt())


def stopping_request(view):
    return {
        "model": MODEL,
        "instructions": "Frozen instructions",
        "input": [
            {"role": "user", "content": "Research policy"},
            {
                "type": "function_call",
                "name": "search_sources",
                "call_id": "search-call",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "search-call",
                "output": canonical_json(view),
            },
        ],
        "tools": [
            {"type": "function", "name": name, "parameters": {"type": "object", "properties": {}}}
            for name in ("search_sources", "submit_proposal")
        ],
    }


def test_legacy_configuration_retains_its_exact_canonical_digest(tmp_path):
    strategy = bundle(tmp_path)
    legacy = {
        "schema_version": 1,
        "source": "strategy.py",
        "source_sha256": strategy.config.source_sha256,
        "sandbox": strategy.config.sandbox.model_dump(mode="json"),
        "observations": True,
        "context": False,
        "max_events": 128,
        "max_state_bytes": 65536,
        "max_render_bytes": 32768,
    }
    assert strategy.config.model_dump(mode="json") == legacy
    assert strategy.sha256 == digest(canonical_json(legacy))
    assert (
        StrategyConfig.model_validate({**legacy, "finalize_on_stop": False}).model_dump(mode="json")
        == legacy
    )
    assert (
        StrategyConfig.model_validate({**legacy, "finalize_on_stop": True}).model_dump(mode="json")[
            "finalize_on_stop"
        ]
        is True
    )


def test_stop_persists_across_restart_and_later_nonstopping_state(tmp_path, stopping_runner):
    strategy = session(tmp_path)
    first = search_stop(strategy, stopping_runner)
    stopping_runner.stop_tools = set()
    strategy.project("search_sources", receipt("already-admitted-output"))
    reopened = StrategySession.open(strategy.root)
    assert (
        reopened.stopping_event()["input"]["payload"]["observation"]["operation_id"] == "search-1"
    )
    with pytest.raises(ResearchFinalizing):
        reopened.admit_observation("search_sources", "case", "new-search")
    reopened.admit_observation("search_sources", "another-discovery", "new-search")
    reopened.admit_observation("search_sources", "case", "search-1")
    assert reopened.project("search_sources", receipt()) == first
    assert stopping_runner.calls == 2


def test_legacy_advice_does_not_silently_change_old_execution_controls(tmp_path, stopping_runner):
    strategy = StrategySession(tmp_path / "strategy", bundle(tmp_path))
    search_stop(strategy, stopping_runner)
    assert strategy.stopping_event() is None
    strategy.admit_observation("search_sources", "case", "next-search")


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_malformed_stop_decisions_fail_closed(tmp_path, stopping_runner, value):
    strategy = session(tmp_path)
    stopping_runner.stop_value = value
    with pytest.raises(StrategySessionError, match="valid boolean"):
        search_stop(strategy, stopping_runner)
    assert strategy.status()["status"] == "failed"


def test_journal_cannot_relabel_a_stop_as_another_event_kind(tmp_path, stopping_runner):
    strategy = session(tmp_path)
    search_stop(strategy, stopping_runner)
    path = strategy.root / "session.json"
    data = json.loads(path.read_bytes())
    data["events"][0]["kind"] = "context"
    write_json(path, data)
    with pytest.raises(ValueError):
        StrategySession.open(strategy.root)


@pytest.fixture
def stopped_gateway(tmp_path, stopping_runner):
    strategy = session(tmp_path)
    request = stopping_request(search_stop(strategy, stopping_runner))
    received = []

    def upstream(incoming):
        received.append(json.loads(incoming.content))
        return provider(incoming)

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        with ResponsesGateway(
            tmp_path / "gateway",
            model=MODEL,
            settings=DiscoverySettings(),
            upstream_base_url="https://fixture.invalid/v1",
            client=client,
            binding=BINDING,
            strategy=strategy,
            allowed_function_names={"search_sources", "submit_proposal"},
        ) as gateway:
            assert post(gateway, request).status_code == 200
    assert received[0]["tools"] == [request["tools"][1]]
    assert received[0]["tool_choice"] == "auto"
    return gateway.output, strategy


def verify(path, strategy):
    return verify_gateway_usage(
        path, BINDING, expected_model=MODEL, expected_strategy_sha256=strategy.bundle.sha256
    )


def test_gateway_finalization_has_independently_verified_usage(stopped_gateway):
    path, strategy = stopped_gateway
    result = verify(path, strategy)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["total_tokens"] == 110
    assert any(name.endswith("stopping-projection.json") for name in result["files"])


@pytest.mark.parametrize("serialization", ["json", "python-literal", "mcp-text"])
def test_stop_views_accepts_the_recorded_direct_and_mcp_envelopes(
    tmp_path, stopping_runner, serialization
):
    strategy = session(tmp_path)
    view = search_stop(strategy, stopping_runner)
    envelope = {"status": "ok", "data": view, "error": None}
    output = {
        "json": canonical_json(envelope),
        "python-literal": str(envelope),
        "mcp-text": [{"type": "text", "text": canonical_json(envelope)}],
    }[serialization]
    request = stopping_request(view)
    request["input"][-1]["output"] = output
    assert stop_views(request, strategy.bundle.sha256) == [view]


def test_stop_output_expressions_are_never_executed(tmp_path):
    marker = tmp_path / "untrusted-output-executed"
    request = stopping_request({})
    request["input"][-1]["output"] = f"open({str(marker)!r}, 'w').write('executed')"
    assert stop_views(request, "a" * 64) == []
    assert not marker.exists()


@pytest.mark.parametrize(
    "change",
    [
        "forwarded-tools",
        "binding",
        "event-output",
        "missing-proof",
        "missing-check",
        "missing-policy",
    ],
)
def test_independent_verifier_rejects_tampered_stopping_controls(stopped_gateway, change):
    path, strategy = stopped_gateway
    record_path = path / "request-0001/record.json"
    record = json.loads(record_path.read_bytes())
    if change == "forwarded-tools":
        target = path / "request-0001/forwarded.json"
        value = json.loads(target.read_bytes())
        value["tools"].append({"type": "function", "name": "search_sources"})
        write_json(target, value)
        record["forwarded_sha256"] = digest(target.read_bytes())
    elif change == "binding":
        target = path / "request-0001/stopping-projection.json"
        value = json.loads(target.read_bytes())
        value["binding"]["case_id"] = "foreign"
        write_json(target, value)
        record["stopping_projection_sha256"] = digest(target.read_bytes())
    elif change == "event-output":
        target = path / "request-0001/stopping-event/result.json"
        value = json.loads(target.read_bytes())
        value["decision"]["stop_recommended"] = False
        write_json(target, value)
    elif change == "missing-proof":
        record.pop("stopping_projection_sha256")
    elif change == "missing-check":
        record.pop("stopping_checked")
    else:
        target = path / "gateway.json"
        value = json.loads(target.read_bytes())
        value.pop("stopping_policy")
        write_json(target, value)
    write_json(record_path, record)
    reindex(path)
    assert verify(path, strategy)["status"] == "invalid"


def test_stop_proof_must_match_the_request_not_a_user_assertion(tmp_path, stopping_runner):
    strategy = session(tmp_path)
    request = stopping_request(search_stop(strategy, stopping_runner))
    request["input"] = [{"role": "user", "content": canonical_json(request["input"])}]
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected provider dispatch"))
    ) as client:
        with ResponsesGateway(
            tmp_path / "gateway",
            model=MODEL,
            settings=DiscoverySettings(),
            upstream_base_url="https://fixture.invalid/v1",
            client=client,
            binding=BINDING,
            strategy=strategy,
        ) as gateway:
            assert post(gateway, request).status_code == 400
    result = verify(gateway.output, strategy)
    assert result["total_tokens"] == 0, result


def test_finalize_request_keeps_evidence_reads_and_requires_direct_structured_output():
    request = {
        "input": [{"role": "user", "content": "Research"}],
        "tools": [{"type": "function", "name": "probe_source"}],
        "tool_choice": "required",
    }
    result = finalize_request(request)
    assert result["tools"] == [] and result["tool_choice"] == "none"
    assert result["input"] == request["input"]
    assert request["tools"]


def test_mcp_stop_blocks_new_io_but_replays_and_saves_a_valid_proposal(tmp_path, stopping_runner):
    strategy = session(tmp_path)
    source = SourceSpec(
        id="notices",
        name="Notices",
        url="https://fixture.invalid/notices.json",
        connector="json",
        items_pointer="/items",
        id_pointer="/id",
    )
    fetches = []

    def fetch(request):
        fetches.append(str(request.url))
        return httpx.Response(200, json={"items": [{"id": "notice-1"}]})

    with httpx.Client(transport=httpx.MockTransport(fetch)) as http:
        service = ResearchService(
            tmp_path / "research",
            backend=Backend.local(tmp_path / "backend"),
            http_client=http,
            public_only=False,
        )
        server = create_server(
            service, search_provider=FixtureProvider([{"url": source.url}]), strategy=strategy
        )

        async def exercise():
            async with memory.create_connected_server_and_client_session(server) as client:
                await client.call_tool(
                    "begin_research", {"brief": "Collect official policy evidence"}
                )
                await client.call_tool(
                    "search_sources", {"query": "notices", "operation_id": "search-1"}
                )
                arguments = {"source": source.model_dump(mode="json"), "operation_id": "probe-1"}
                proof = (await client.call_tool("probe_source", arguments)).structuredContent
                assert proof["status"] == "ok"
                before = service.get_context()["counts"]
                for tool, args in [
                    ("search_sources", {"query": "more"}),
                    ("inspect_source", {"url": source.url}),
                    ("probe_source", {"source": source.model_dump(mode="json")}),
                ]:
                    result = (
                        await client.call_tool(tool, {**args, "operation_id": "after-stop"})
                    ).structuredContent
                    assert result["error"]["code"] == "research_finalizing"
                    assert result["error"]["retryable"] is False
                assert service.get_context()["counts"] == before
                replay = (await client.call_tool("probe_source", arguments)).structuredContent
                assert replay == proof
                changed = (
                    await client.call_tool(
                        "probe_source",
                        {
                            **arguments,
                            "source": {**arguments["source"], "url": source.url + "?changed"},
                        },
                    )
                ).structuredContent
                assert changed["status"] == "error"
                assert service.get_context()["counts"] == before
                result = (
                    await client.call_tool(
                        "submit_proposal",
                        {
                            "draft": draft(source, proof["data"]["probe_id"]).model_dump(
                                mode="json"
                            ),
                            "operation_id": "submit",
                        },
                    )
                ).structuredContent
                assert result["status"] == "ok", result
                assert result["data"]["registry"]["pipeline_version_id"]

        anyio.run(exercise)
    assert fetches == [source.url]
    assert stopping_runner.calls == 2
    assert StrategySession.open(strategy.root).stopping_event() is not None


@pytest.mark.parametrize("max_rounds", [3, 4])
def test_direct_sdk_finalizes_and_denies_a_late_tool_call_within_fixed_limits(
    tmp_path, stopping_runner, max_rounds
):
    strategy = session(tmp_path)
    source = SourceSpec(
        id="notices",
        name="Notices",
        url="https://fixture.invalid/notices.json",
        connector="json",
        items_pointer="/items",
        id_pointer="/id",
    )
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        number = len(requests)
        if number <= 3:
            name, arguments = [
                ("search_sources", {"query": "notices", "filters": None}),
                ("probe_source", {"source_json": source.model_dump_json()}),
                ("inspect_url", {"url": source.url}),
            ][number - 1]
            output = [
                {
                    "type": "function_call",
                    "id": f"fc_{number}",
                    "call_id": f"call_{number}",
                    "name": name,
                    "arguments": json.dumps(arguments),
                    "status": "completed",
                }
            ]
        else:
            proof = next(iter(discovery.probes.values()))
            output = [
                {
                    "type": "message",
                    "id": "msg_final",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": draft(source, proof["probe_id"]).model_dump_json(),
                            "annotations": [],
                        }
                    ],
                }
            ]
        return httpx.Response(200, json=api_response(output, number))

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as model_http,
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"items": [{"id": "notice-1"}]})
            )
        ) as source_http,
    ):
        client = OpenAI(
            api_key="synthetic",
            base_url="https://fixture.invalid/v1",
            http_client=model_http,
            max_retries=0,
        )
        discovery = Discovery(
            tmp_path / "research",
            client=client,
            backend=Backend.local(tmp_path / "backend"),
            http_client=source_http,
            public_only=False,
            search_provider=FixtureProvider([{"url": source.url}]),
            strategy=strategy,
            settings=DiscoverySettings(max_rounds=max_rounds),
        )
        if max_rounds == 3:
            with pytest.raises(RuntimeError):
                discovery.run("Collect official policy evidence")
        else:
            assert discovery.run("Collect official policy evidence")["status"] == "proposed"
    assert len(requests) == max_rounds
    assert requests[2]["tools"] == [] and requests[2]["tool_choice"] == "none"
    assert discovery.service.get_context()["counts"] == {
        "search": 1,
        "probe": 1,
        **({"proposal": 1} if max_rounds == 4 else {}),
    }
    assert stopping_runner.calls == 2
