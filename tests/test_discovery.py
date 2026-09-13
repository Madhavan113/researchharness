from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

import research_harness.discovery as discovery_module
from research_harness import registry
from research_harness.backend import Backend
from research_harness.cli import main
from research_harness.config import PipelineSpec, SourceSpec
from research_harness.discovery import (
    Candidate,
    DataNeed,
    Discovery,
    ProposalDraft,
    compile_proposal,
)
from research_harness.execution import DiscoverySettings
from research_harness.services.search import McpSearchProvider, SearchFilters
from research_harness.util import digest, parse_timestamp


def candidate(source, probe_id="probe-1"):
    return Candidate(
        name="Official feed",
        purpose="Track official policy changes",
        covers=["policy"],
        status="ready",
        evidence_urls=[source.url],
        source=source,
        probe_id=probe_id,
        freshness_assessment="Publication is irregular; collect hourly",
        historical_coverage="Current feed window only",
        access_notes="Public GET sample succeeded",
        limitations=["Feed entries can be excerpts"],
    )


def draft(source, probe_id="probe-1"):
    return ProposalDraft(
        name="policy-monitor",
        title="Policy monitor",
        research_question="Collect official policy evidence",
        needs=[DataNeed(id="policy", description="Official policy changes", required=True)],
        candidates=[candidate(source, probe_id)],
        open_questions=[],
    )


def test_proposal_cannot_claim_verified_without_probe(source):
    with pytest.raises(ValueError, match="probe"):
        compile_proposal(draft(source), {}, {source.url})


def test_changed_configuration_invalidates_probe(source):
    proof = {"probe-1": {"status": "verified_sample", "source_fingerprint": source.fingerprint()}}
    altered = source.model_copy(update={"items_pointer": "/other"})
    with pytest.raises(ValueError, match="changed after probing"):
        compile_proposal(draft(altered), proof, {source.url})


def test_unsupported_sources_remain_visible_without_executable_pipeline(source):
    value = draft(source)
    value.candidates[0].status = "needs_access"
    value.candidates[0].source = None
    value.candidates[0].probe_id = None
    pipeline, gaps = compile_proposal(value, {}, {source.url})
    assert pipeline is None
    assert gaps == ["policy"]


def test_unobserved_citations_are_rejected(source):
    with pytest.raises(ValueError, match="actually observed"):
        compile_proposal(draft(source), {}, set())


def api_response(output, number):
    return {
        "id": f"resp_{number}",
        "object": "response",
        "created_at": 1788782400,
        "status": "completed",
        "model": "gpt-5.4-mini",
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


@pytest.mark.parametrize("repair_first", [False, True])
@pytest.mark.parametrize("null_defaults", [False, True])
def test_actual_sdk_tool_loop_probes_and_compiles_a_pipeline(
    tmp_path, rss, repair_first, null_defaults
):
    from test_provider_schema import source_with_null_defaults

    source = SourceSpec(
        id="official-policy",
        name="Official policy feed",
        connector="rss",
        url="https://source.example/feed",
    )
    requests = []
    source_json = (
        json.dumps(source_with_null_defaults(source)) if null_defaults else source.model_dump_json()
    )

    def model_handler(request):
        from test_provider_schema import assert_strict_schema

        data = json.loads(request.content)
        requests.append(data)
        assert data["store"] is False
        assert data["text"]["format"]["strict"] is True
        assert_strict_schema(data["text"]["format"]["schema"])
        for entry in data["input"]:
            assert "parsed_arguments" not in entry
            if isinstance(entry.get("content"), list):
                assert all("parsed" not in content for content in entry["content"])
        if len(requests) == 1:
            if repair_first:
                return httpx.Response(
                    200,
                    json=api_response(
                        [
                            {
                                "id": "ws_1",
                                "type": "web_search_call",
                                "status": "completed",
                                "action": {
                                    "type": "search",
                                    "query": "official policy feed",
                                    "sources": [{"type": "url", "url": source.url}],
                                },
                            },
                            {
                                "id": "msg_unverified",
                                "type": "message",
                                "status": "completed",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": draft(source).model_dump_json(),
                                        "annotations": [],
                                    }
                                ],
                            },
                        ],
                        1,
                    ),
                )
            return httpx.Response(
                200,
                json=api_response(
                    [
                        {
                            "id": "ws_1",
                            "type": "web_search_call",
                            "status": "completed",
                            "action": {
                                "type": "search",
                                "query": "official policy feed",
                                "sources": [{"type": "url", "url": source.url}],
                            },
                        },
                        {
                            "id": "fc_1",
                            "type": "function_call",
                            "name": "probe_source",
                            "call_id": "call_1",
                            "status": "completed",
                            "arguments": json.dumps({"source_json": source_json}),
                        },
                    ],
                    1,
                ),
            )
        if repair_first and len(requests) == 2:
            assert any(
                "Application validation rejected" in entry.get("content", "")
                for entry in data["input"]
                if isinstance(entry.get("content"), str)
            )
            return httpx.Response(
                200,
                json=api_response(
                    [
                        {
                            "id": "fc_1",
                            "type": "function_call",
                            "name": "probe_source",
                            "call_id": "call_1",
                            "status": "completed",
                            "arguments": json.dumps({"source_json": source_json}),
                        }
                    ],
                    2,
                ),
            )
        tool_output = next(
            item for item in data["input"] if item.get("type") == "function_call_output"
        )
        proof = json.loads(tool_output["output"])
        assert proof["status"] == "verified_sample"
        answer = draft(source, proof["probe_id"])
        return httpx.Response(
            200,
            json=api_response(
                [
                    {
                        "id": "msg_2",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": answer.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                2,
            ),
        )

    with (
        httpx.Client(transport=httpx.MockTransport(model_handler)) as model_http,
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, content=rss, headers={"content-type": "application/rss+xml"}
                )
            )
        ) as source_http,
    ):
        with OpenAI(
            api_key="test-only-not-a-real-key", http_client=model_http, max_retries=0
        ) as client:
            result = Discovery(
                tmp_path / "discovery",
                client=client,
                http_client=source_http,
                public_only=False,
                backend=Backend.local(tmp_path / "backend"),
            ).run("Find official policy sources")
    assert result["status"] == "proposed"
    assert result["verified_sources"] == 1
    rounds = 3 if repair_first else 2
    assert result["usage"] == {"input_tokens": 100 * rounds, "output_tokens": 50 * rounds}
    pipeline = json.loads((tmp_path / "discovery/pipeline.json").read_text())
    assert pipeline["sources"][0]["url"] == source.url
    assert (tmp_path / "discovery/probes.json").exists()
    assert "test-only-not-a-real-key" not in (tmp_path / "discovery/trace.jsonl").read_text()
    assert len(requests) == rounds
    with Backend.local(tmp_path / "backend").open_registry() as reg:
        questions = registry.list_questions(reg)
        assert len(questions) == 1 and questions[0]["pipelines"] == 1
        versions = registry.list_pipelines(reg, question_id=questions[0]["id"])
        overview = registry.question_overview(reg, questions[0]["id"])
    assert versions[0]["origin"] == "discovery" and versions[0]["status"] == "proposed"
    assert versions[0]["fingerprint"] == PipelineSpec.model_validate(pipeline).fingerprint()
    assert result["registry"]["pipeline_version_id"] == versions[0]["id"]
    assert overview["discoveries"][0]["status"] == "proposed"
    assert overview["proposals"][0]["verified_source_count"] == 1
    assert (
        json.loads((tmp_path / "discovery/proposal.json").read_text())["registry"]
        == result["registry"]
    )


def test_round_limit_preserves_failure_and_never_creates_fake_pipeline(tmp_path, source):
    response = SimpleNamespace(
        id="r", status="completed", usage=None, output=[], output_parsed=draft(source)
    )
    client = SimpleNamespace(responses=SimpleNamespace(parse=lambda **kwargs: response))
    with pytest.raises(RuntimeError, match="round limit"):
        Discovery(
            tmp_path, client=client, max_rounds=2, backend=Backend.local(tmp_path / "backend")
        ).run("Research")
    assert (tmp_path / "failure.json").exists()
    assert not (tmp_path / "pipeline.json").exists()
    with Backend.local(tmp_path / "backend").open_registry() as reg:
        overview = registry.question_overview(reg, registry.list_questions(reg)[0]["id"])
    assert overview["discoveries"][0]["status"] == "failed"
    assert "round limit" in overview["discoveries"][0]["error"]
    assert overview["pipelines"] == [] and overview["proposals"] == []


def test_unknown_tool_cannot_execute_commands(tmp_path):
    discovery = Discovery(tmp_path, client=None, backend=Backend.local(tmp_path / "backend"))
    result = discovery.tool("execute_shell", '{"command":"touch bad"}')
    assert result["status"] == "error"
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("scenario", ["budget", "failed_search"])
def test_native_search_is_bounded_before_request_and_failures_can_be_repaired(
    tmp_path, source, scenario
):
    calls = []
    answer = draft(source)
    answer.candidates[0].status = "needs_access"
    answer.candidates[0].source = None
    answer.candidates[0].probe_id = None
    final_round = 9 if scenario == "budget" else 2

    def handler(request):
        data = json.loads(request.content)
        calls.append(data)
        number = len(calls)
        output = []
        if number < final_round or scenario == "failed_search":
            assert {"type": "web_search"} in data["tools"]
            if scenario == "budget":
                assert data["max_tool_calls"] <= 9 - number
            output.append(
                {
                    "id": f"ws_{number}",
                    "type": "web_search_call",
                    "status": "failed"
                    if scenario == "failed_search" and number == 1
                    else "completed",
                    "action": {
                        "type": "search",
                        "query": "official sources",
                        "sources": [{"type": "url", "url": source.url}],
                    },
                }
            )
        else:
            assert {"type": "web_search"} not in data["tools"]
        if number == final_round:
            output.append(
                {
                    "id": f"msg_{number}",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": answer.model_dump_json(), "annotations": []}
                    ],
                }
            )
        return httpx.Response(200, json=api_response(output, number))

    with httpx.Client(transport=httpx.MockTransport(handler)) as model_http:
        with OpenAI(api_key="test-only", http_client=model_http, max_retries=0) as client:
            result = Discovery(
                tmp_path / "discovery", client=client, backend=Backend.local(tmp_path / "backend")
            ).run("Find official policy sources")
    assert result["status"] == "research_only" and len(calls) == final_round
    saved = json.loads((tmp_path / "discovery/proposal.json").read_text())
    assert saved["web_search_calls"] == (8 if scenario == "budget" else 2)
    assert saved["successful_searches"] == (8 if scenario == "budget" else 1)


def function_call(name, arguments, number, *, call_id=None):
    return {
        "id": f"fc_{number}",
        "type": "function_call",
        "name": name,
        "call_id": call_id or f"call_{number}",
        "status": "completed",
        "arguments": json.dumps(arguments),
    }


def proposal_message(answer, number):
    return {
        "id": f"msg_{number}",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": answer.model_dump_json(), "annotations": []}],
    }


def research_only(source):
    answer = draft(source)
    answer.candidates[0].status = "needs_access"
    answer.candidates[0].source = None
    answer.candidates[0].probe_id = None
    return answer


@pytest.fixture
def mcp_search_http(monkeypatch, source):
    """Run the real MCP client/provider over synthetic HTTP responses."""
    pytest.importorskip("mcp")
    raw = {
        "content": [{"type": "text", "text": "Official fixture source"}],
        "structuredContent": {"results": [{"url": source.url, "title": "Official fixture source"}]},
        "isError": False,
    }
    fixture = SimpleNamespace(calls=[], responses=[], raw=raw)

    def handler(request):
        assert request.url == "https://search.example/mcp"
        body = json.loads(request.content)
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture-search", "version": "1"},
            }
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": "search_web_pages", "inputSchema": {"type": "object"}}]}
        else:
            assert body["method"] == "tools/call"
            assert body["params"]["name"] == "search_web_pages"
            fixture.calls.append(body["params"]["arguments"])
            result = fixture.responses.pop(0) if fixture.responses else fixture.raw
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "research_harness.services.search.httpx.AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return fixture


def assert_wrapped_request(data):
    assert all(tool["type"] != "web_search" for tool in data["tools"])
    assert "max_tool_calls" not in data
    assert "web_search_call.action.sources" not in data["include"]
    assert "Use search_sources" in data["instructions"]
    for entry in data["input"]:
        assert "parsed_arguments" not in entry


def test_actual_sdk_and_mcp_search_share_receipts_and_idempotent_calls(
    tmp_path, source, item, mcp_search_http
):
    requests = []
    search_args = {
        "query": "official policy",
        "filters": SearchFilters(
            site="source.example",
            max_results=2,
            snippet_max_length=300,
            published_after="2020-01-01",
        ).model_dump(),
    }

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        assert_wrapped_request(data)
        number = len(requests)
        search_tool = next(tool for tool in data["tools"] if tool.get("name") == "search_sources")
        from test_provider_schema import assert_strict_schema

        assert_strict_schema(search_tool["parameters"])
        schema = search_tool["parameters"]
        assert search_tool["strict"] and schema["additionalProperties"] is False
        assert set(schema["required"]) == {"query", "filters"}
        assert schema["properties"]["query"]["maxLength"] == 2000
        filters = schema["$defs"]["SearchFilters"]
        assert filters["additionalProperties"] is False
        assert set(filters["required"]) == set(filters["properties"])
        maximum = filters["properties"]["max_results"]
        assert maximum["anyOf"][0]["maximum"] == 10
        assert maximum["anyOf"][1] == {"type": "null"}
        if number in {1, 2, 3}:
            args = search_args if number < 3 else {**search_args, "query": "different query"}
            output = [function_call("search_sources", args, number, call_id="same-search")]
        elif number == 4:
            results = [
                json.loads(entry["output"])
                for entry in data["input"]
                if entry.get("type") == "function_call_output"
            ]
            assert results[0] == results[1]
            assert "different arguments" in results[2]["error"]
            output = [
                function_call("probe_source", {"source_json": source.model_dump_json()}, number)
            ]
        else:
            proof = json.loads(data["input"][-1]["output"])
            output = [proposal_message(draft(source, proof["probe_id"]), number)]
        return httpx.Response(200, json=api_response(output, number))

    provider = McpSearchProvider("https://search.example/mcp")
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as model_http,
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
        ) as source_http,
        OpenAI(api_key="fixture-only", http_client=model_http, max_retries=0) as client,
    ):
        discovery = Discovery(
            tmp_path / "discovery",
            client=client,
            http_client=source_http,
            public_only=False,
            backend=Backend.local(tmp_path / "backend"),
            search_provider=provider,
        )
        result = discovery.run("Find official policy sources")
    assert result["status"] == "proposed"
    assert len(mcp_search_http.calls) == 1
    assert mcp_search_http.calls[0]["mode"] == "realtime"
    assert mcp_search_http.calls[0]["max_results"] == 2
    assert mcp_search_http.calls[0]["site"] == "source.example"
    assert mcp_search_http.calls[0]["snippet_max_length"] == 300
    assert mcp_search_http.calls[0]["published_after"] == "2020-01-01"
    context = discovery.service.get_context()
    assert context["counts"]["search"] == 1
    receipt = next(r["payload"] for r in context["receipts"] if r["kind"] == "search")
    assert receipt["operation_id"] == "same-search"
    assert receipt["provider"] == provider.name
    assert receipt["provider_response"] == mcp_search_http.raw
    assert receipt["provider_arguments"] == mcp_search_http.calls[0]
    assert receipt["provider_usage"] is None
    request = json.loads((discovery.output / "request.json").read_text())
    assert request["search"] == context["runtime"]["search"] == discovery.search_config
    assert request["search"]["endpoint"] == provider.endpoint
    assert context["observed_urls"] == [source.url]


@pytest.mark.parametrize("explicit_settings", [False, True])
def test_wrapped_search_budget_stops_provider_before_network_and_removes_tool(
    tmp_path, source, mcp_search_http, explicit_settings
):
    requests = []
    settings = DiscoverySettings(max_searches=1, max_rounds=3) if explicit_settings else None
    search_budget = settings.max_searches if settings else 8
    final_round = search_budget + 2

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        assert_wrapped_request(data)
        number = len(requests)
        exposed = any(tool.get("name") == "search_sources" for tool in data["tools"])
        assert exposed == (number <= search_budget)
        if number < final_round:
            output = [
                function_call(
                    "search_sources",
                    {"query": f"official policy {number}", "filters": None},
                    number,
                )
            ]
        else:
            assert "Search budget exhausted" in json.loads(data["input"][-1]["output"])["error"]
            output = [proposal_message(research_only(source), number)]
        return httpx.Response(200, json=api_response(output, number))

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as model_http,
        OpenAI(api_key="fixture-only", http_client=model_http, max_retries=0) as client,
    ):
        discovery = Discovery(
            tmp_path / "discovery",
            client=client,
            backend=Backend.local(tmp_path / "backend"),
            search_provider=McpSearchProvider("https://search.example/mcp"),
            settings=settings,
        )
        result = discovery.run("Find official policy sources")
    assert result["status"] == "research_only" and len(mcp_search_http.calls) == search_budget
    assert discovery.service.get_context()["remaining"]["search"] == 0
    saved = json.loads((discovery.output / "proposal.json").read_text())
    assert saved["web_search_calls"] == saved["successful_searches"] == search_budget


def test_wrapped_provider_failure_replays_without_spend_and_fresh_id_repairs(
    tmp_path, source, mcp_search_http
):
    failed = {
        "content": [{"type": "text", "text": "Provider temporarily unavailable"}],
        "isError": True,
    }
    mcp_search_http.responses.append(failed)
    requests = []

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        assert_wrapped_request(data)
        number = len(requests)
        if number < 4:
            output = [
                function_call(
                    "search_sources",
                    {"query": "official policy", "filters": None},
                    number,
                    call_id="failed-search" if number < 3 else "fresh-search",
                )
            ]
        else:
            outputs = [
                json.loads(entry["output"])
                for entry in data["input"]
                if entry.get("type") == "function_call_output"
            ]
            assert all(result["status"] == "error" for result in outputs[:2])
            assert outputs[-1]["receipt_id"]
            output = [proposal_message(research_only(source), number)]
        return httpx.Response(200, json=api_response(output, number))

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as model_http,
        OpenAI(api_key="fixture-only", http_client=model_http, max_retries=0) as client,
    ):
        discovery = Discovery(
            tmp_path / "discovery",
            client=client,
            backend=Backend.local(tmp_path / "backend"),
            search_provider=McpSearchProvider("https://search.example/mcp"),
        )
        result = discovery.run("Find official policy sources")
    assert result["status"] == "research_only" and len(mcp_search_http.calls) == 2
    context = discovery.service.get_context()
    assert context["counts"]["search"] == 2 and len(context["receipts"]) == 1
    operation = next(o for o in context["operations"] if o["operation_id"] == "failed-search")
    assert operation["status"] == "failed" and operation["result"]["provider_response"] == failed
    assert context["observed_urls"] == [source.url]


def test_wrapped_discovery_cannot_grant_evidence_from_native_results(
    tmp_path, source, mcp_search_http
):
    requests = []
    fabricated = "https://unobserved.example/native"

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        assert_wrapped_request(data)
        number = len(requests)
        if number == 1:
            output = [
                {
                    "id": "ws_unexpected",
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {
                        "type": "search",
                        "query": "unexpected native search",
                        "sources": [{"type": "url", "url": fabricated}],
                    },
                },
                proposal_message(research_only(source), number),
            ]
        elif number == 2:
            assert "Use search_sources" in data["input"][-1]["content"]
            output = [
                function_call(
                    "search_sources", {"query": "official policy", "filters": None}, number
                )
            ]
        else:
            answer = research_only(source)
            if number == 3:
                answer.candidates[0].evidence_urls.append(fabricated)
            else:
                assert "actually observed" in data["input"][-1]["content"]
            output = [proposal_message(answer, number)]
        return httpx.Response(200, json=api_response(output, number))

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as model_http,
        OpenAI(api_key="fixture-only", http_client=model_http, max_retries=0) as client,
    ):
        discovery = Discovery(
            tmp_path / "discovery",
            client=client,
            backend=Backend.local(tmp_path / "backend"),
            search_provider=McpSearchProvider("https://search.example/mcp"),
        )
        result = discovery.run("Find official policy sources")
    assert result["status"] == "research_only" and len(requests) == 4
    assert discovery.service.get_context()["observed_urls"] == [source.url]
    assert discovery.searches == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"query": "x" * 2001, "filters": None},
        {"query": "official policy", "filters": {"max_results": 11}},
        {"query": "official policy", "filters": None, "results": [{"url": "https://fake.example"}]},
    ],
)
def test_wrapped_search_rejects_invalid_arguments_before_provider(
    tmp_path, source, mcp_search_http, invalid
):
    requests = []

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        number = len(requests)
        if number <= 2:
            if number == 2:
                assert json.loads(data["input"][-1]["output"])["status"] == "error"
                assert not mcp_search_http.calls
            output = [
                function_call(
                    "search_sources",
                    invalid if number == 1 else {"query": "official policy", "filters": None},
                    number,
                )
            ]
        else:
            output = [proposal_message(research_only(source), number)]
        return httpx.Response(200, json=api_response(output, number))

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as model_http,
        OpenAI(api_key="fixture-only", http_client=model_http, max_retries=0) as client,
    ):
        discovery = Discovery(
            tmp_path / "discovery",
            client=client,
            backend=Backend.local(tmp_path / "backend"),
            search_provider=McpSearchProvider("https://search.example/mcp"),
        )
        result = discovery.run("Find official policy sources")
    assert result["status"] == "research_only" and len(mcp_search_http.calls) == 1
    assert discovery.searches == 1


@pytest.mark.parametrize("explicit_settings", [False, True])
def test_cli_mcp_provider_options_reach_actual_sdk_and_saved_configuration(
    tmp_path, source, mcp_search_http, monkeypatch, capsys, explicit_settings
):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-only")
    monkeypatch.setenv("RH_LOCAL_ROOT", str(tmp_path / "backend"))
    monkeypatch.delenv("RH_DATABASE_URL", raising=False)
    requests = []
    settings = DiscoverySettings(max_rounds=3, max_searches=1, max_output_tokens=777)
    instructions = "Follow the shared research policy exactly.\n"
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(settings.model_dump_json(), encoding="utf-8")
    instructions_path = tmp_path / "instructions.md"
    instructions_path.write_text(instructions, encoding="utf-8")

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        assert_wrapped_request(data)
        if explicit_settings:
            assert data["instructions"].startswith(instructions)
            for key, value in settings.model_settings().items():
                assert data[key] == value
            assert "parallel_tool_calls" not in data
        else:
            assert "reasoning" not in data
            assert data["max_output_tokens"] == 6000
            assert data["parallel_tool_calls"] is False
        number = len(requests)
        output = (
            [function_call("search_sources", {"query": "official policy", "filters": None}, number)]
            if number == 1
            else [proposal_message(research_only(source), number)]
        )
        return httpx.Response(200, json=api_response(output, number))

    with httpx.Client(transport=httpx.MockTransport(handler)) as model_http:
        monkeypatch.setattr(
            discovery_module,
            "OpenAI",
            lambda **kwargs: OpenAI(http_client=model_http, **kwargs),
        )
        code = main(
            [
                "discover",
                "Find official policy sources",
                "--out",
                str(tmp_path / "discovery"),
                "--search-provider",
                "mcp",
                "--search-endpoint",
                "https://search.example/mcp",
            ]
            + (
                ["--settings", str(settings_path), "--instructions", str(instructions_path)]
                if explicit_settings
                else []
            )
        )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert json.loads(captured.out)["status"] == "research_only"
    saved = json.loads((tmp_path / "discovery/request.json").read_text())
    assert saved["search"]["provider"] == "https://search.example/mcp#search_web_pages"
    if explicit_settings:
        assert saved["execution_settings"] == settings.model_dump()
        assert saved["instructions_sha256"] == digest(instructions)
        assert saved["max_rounds"] == settings.max_rounds
    else:
        assert saved["execution_settings"] is None
        assert saved["max_rounds"] == 16
    assert len(mcp_search_http.calls) == 1


@pytest.mark.parametrize(
    "mode", ["native_endpoint", "missing_extra", "credentialed_endpoint", "empty_endpoint"]
)
def test_cli_search_configuration_fails_before_model_client(tmp_path, monkeypatch, capsys, mode):
    def unexpected_client(**kwargs):
        pytest.fail("Invalid search settings must not create a model client")

    monkeypatch.setattr(discovery_module, "OpenAI", unexpected_client)
    argv = ["discover", "Research", "--out", str(tmp_path / "discovery")]
    if mode == "native_endpoint":
        argv.extend(["--search-endpoint", "https://search.example/mcp"])
    elif mode == "missing_extra":
        monkeypatch.setattr(discovery_module, "find_spec", lambda _: None)
        argv.extend(["--search-provider", "mcp"])
    elif mode == "empty_endpoint":
        argv.extend(["--search-provider", "mcp", "--search-endpoint", ""])
    else:
        argv.extend(
            [
                "--search-provider",
                "mcp",
                "--search-endpoint",
                "https://search.example/?api_key=secret",
            ]
        )
    assert main(argv) == 1
    captured = capsys.readouterr()
    assert "error" in captured.err and captured.out == ""
    assert not (tmp_path / "discovery").exists()


@pytest.mark.parametrize("provider_mode", ["native", "mcp"])
@pytest.mark.parametrize(
    "effort,service_tier", [(None, None), ("none", None), ("high", None), ("none", "default")]
)
def test_actual_sdk_records_shared_controls_without_changing_legacy_defaults(
    tmp_path, source, clock, mcp_search_http, provider_mode, effort, service_tier
):
    requests = []
    settings = (
        DiscoverySettings(
            max_rounds=3,
            max_output_tokens=777,
            reasoning_effort=effort,
            service_tier=service_tier,
            max_searches=1,
            max_inspections=0,
            max_probes=0,
            deadline_seconds=37,
        )
        if effort is not None
        else None
    )
    # Preserve the text, including tool-like words and whitespace, before runtime guidance.
    semantic_instructions = "Shared policy: the web_search example is only an example.\n\n"
    instructions = semantic_instructions if settings else None

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        number = len(requests)
        if settings:
            assert data["instructions"].startswith(semantic_instructions)
            for key, value in settings.model_settings().items():
                assert data[key] == value
            assert "parallel_tool_calls" not in data
            if provider_mode == "native":
                assert data["max_tool_calls"] == 1
            assert any(
                tool["type"] == "web_search" or tool.get("name") == "search_sources"
                for tool in data["tools"]
            ) == (number == 1)
        else:
            expected = discovery_module.INSTRUCTIONS
            if provider_mode == "mcp":
                expected = expected.replace("web_search", "search_sources")
            assert data["instructions"].startswith(expected + "\nConnector catalog:\n")
            assert "reasoning" not in data
            assert data["max_output_tokens"] == 6000
            assert data["parallel_tool_calls"] is False
        if provider_mode == "mcp":
            assert_wrapped_request(data)
        elif number == 1:
            assert {"type": "web_search"} in data["tools"]
            assert "web_search_call.action.sources" in data["include"]
        if number == 1:
            output = (
                [
                    {
                        "id": "ws_1",
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {
                            "type": "search",
                            "query": "official policy",
                            "sources": [{"type": "url", "url": source.url}],
                        },
                    }
                ]
                if provider_mode == "native"
                else [
                    function_call(
                        "search_sources", {"query": "official policy", "filters": None}, number
                    )
                ]
            )
        else:
            output = [proposal_message(research_only(source), number)]
        return httpx.Response(200, json=api_response(output, number))

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as model_http,
        OpenAI(api_key="fixture-only", http_client=model_http, max_retries=0) as client,
    ):
        discovery = Discovery(
            tmp_path / "discovery",
            client=client,
            backend=Backend.local(tmp_path / "backend", clock=clock),
            search_provider=McpSearchProvider("https://search.example/mcp")
            if provider_mode == "mcp"
            else None,
            settings=settings,
            instructions=instructions,
        )
        result = discovery.run("Find official policy sources")
    assert result["status"] == "research_only" and len(requests) == 2
    assert len(mcp_search_http.calls) == (1 if provider_mode == "mcp" else 0)
    context = discovery.service.get_context()
    saved = json.loads((discovery.output / "request.json").read_text())
    expected_settings = settings.model_dump() if settings else None
    expected_omissions = ["parallel_tool_calls"] if settings else ["reasoning"]
    if service_tier is None:
        expected_omissions.append("service_tier")
    for metadata in [saved, context["runtime"]]:
        assert metadata["execution_settings"] == expected_settings
        assert metadata["omitted_model_settings"] == expected_omissions
        assert metadata["instructions_sha256"] == digest(discovery.instructions)
        assert metadata["instructions_source"] == ("provided" if settings else "default")
    if settings:
        assert saved["budgets"] == settings.budgets()
        assert context["limits"] == settings.limits()
        assert (parse_timestamp(context["deadline_at"]) - clock()).total_seconds() == 37
    trace = [
        json.loads(line) for line in (discovery.output / "trace.jsonl").read_text().splitlines()
    ]
    traced_requests = [event for event in trace if event["event"] == "model_request"]
    assert len(traced_requests) == len(requests)
    for event, request in zip(traced_requests, requests, strict=True):
        assert event["instructions"] == request["instructions"]
        assert event["omitted_model_settings"] == expected_omissions
        assert event["instructions_sha256"] == saved["instructions_sha256"]
        for key, value in event["model_settings"].items():
            assert request[key] == value


@pytest.mark.parametrize("stop", ["before_first_request", "after_unknown_calls", "round_limit"])
def test_settings_stop_model_requests_even_when_unknown_calls_bypass_service(tmp_path, clock, stop):
    requests = []
    settings = DiscoverySettings(
        max_rounds=2 if stop == "round_limit" else 6,
        deadline_seconds=5,
        max_searches=0,
        max_inspections=0,
        max_probes=0,
    )

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        if stop == "after_unknown_calls":
            clock.advance(2 if len(requests) == 1 else 3)
        return httpx.Response(
            200,
            json=api_response([function_call("unknown_tool", {}, len(requests))], len(requests)),
        )

    def progress(event):
        if stop == "before_first_request" and event["event"] == "registered":
            clock.advance(5)

    error = "round limit" if stop == "round_limit" else "deadline exceeded before model request"
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as model_http,
        OpenAI(api_key="fixture-only", http_client=model_http, max_retries=0) as client,
    ):
        discovery = Discovery(
            tmp_path / "discovery",
            client=client,
            backend=Backend.local(tmp_path / "backend", clock=clock),
            settings=settings,
            progress=progress,
        )
        with pytest.raises(RuntimeError, match=error):
            discovery.run("Find official policy sources")
    expected_requests = 0 if stop == "before_first_request" else 2
    assert len(requests) == expected_requests
    context = discovery.service.get_context()
    assert context["status"] == "failed" and context["operations"] == []
    failure = json.loads((discovery.output / "failure.json").read_text())
    assert error in failure["error"]
    assert failure["usage"] == {
        "input_tokens": expected_requests * 100,
        "output_tokens": expected_requests * 50,
    }
    assert not (discovery.output / "proposal.json").exists()


@pytest.mark.parametrize("limit", ["max_rounds", "max_probes", "max_inspections"])
def test_legacy_limits_cannot_override_shared_settings(tmp_path, limit):
    settings = DiscoverySettings(max_rounds=2, max_probes=1, max_inspections=3)
    with pytest.raises(ValueError, match=f"{limit} conflicts"):
        Discovery(
            tmp_path / "discovery",
            client=None,
            settings=settings,
            **{limit: getattr(settings, limit) + 1},
        )
    assert not (tmp_path / "discovery").exists()
    discovery = Discovery(
        tmp_path / "discovery",
        client=None,
        backend=Backend.local(tmp_path / "backend"),
        settings=settings,
        **{limit: getattr(settings, limit)},
    )
    assert getattr(discovery, limit) == getattr(settings, limit)


@pytest.mark.parametrize("invalid", ["settings_json", "conflicting_rounds", "empty_instructions"])
def test_cli_execution_configuration_fails_before_model_client(
    tmp_path, monkeypatch, capsys, invalid
):
    def unexpected_client(**kwargs):
        pytest.fail("Invalid execution settings must not create a model client")

    monkeypatch.setattr(discovery_module, "OpenAI", unexpected_client)
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"max_rounds": 2}', encoding="utf-8")
    argv = ["discover", "Research", "--out", str(tmp_path / "discovery")]
    if invalid == "settings_json":
        settings_path.write_text('{"max_rounds": "2"}', encoding="utf-8")
        argv.extend(["--settings", str(settings_path)])
    elif invalid == "conflicting_rounds":
        argv.extend(["--settings", str(settings_path), "--max-rounds", "3"])
    else:
        instructions_path = tmp_path / "instructions.md"
        instructions_path.write_text(" \n", encoding="utf-8")
        argv.extend(["--instructions", str(instructions_path)])
    assert main(argv) == 1
    captured = capsys.readouterr()
    assert "error" in captured.err and captured.out == ""
    assert not (tmp_path / "discovery").exists()
