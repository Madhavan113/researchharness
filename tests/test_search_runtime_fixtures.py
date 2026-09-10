from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import pytest

from research_harness.backend import Backend
from research_harness.evaluation.benchmark import load_benchmark, validate_splits
from research_harness.evaluation.discovery import score
from research_harness.evaluation.fixture_transport import FixtureSources
from research_harness.services.research import ResearchService

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "search_runtime_fixtures", ROOT / "examples/evaluation/search_runtime_fixtures.py"
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def completed(stream):
    events = [
        json.loads(line[6:]) for line in stream.decode().splitlines() if line.startswith("data: ")
    ]
    return next(event["response"] for event in events if event["type"] == "response.completed")


def test_authored_benchmarks_are_disjoint_and_use_the_public_source_contract(tmp_path):
    packages = [
        load_benchmark(FIXTURE.benchmark(tmp_path / split, split=split))
        for split in ("development", "heldout")
    ]
    assert validate_splits(*packages) == {"development_cases": 1, "heldout_cases": 1}
    hosts, briefs, payloads = set(), set(), set()
    for package in packages:
        case = next(iter(package.cases.values()))
        assert case.review_status == "authored"
        assert case.fixture_plan.source_ids == ["feed"]
        source = case.sources[0]
        assert (source.id, source.connector, source.items_pointer) == ("feed", "json", "/items")
        assert source.id_pointer == "/id" and source.published_pointer == "/published_at"
        assert source.required_pointers == ["/title"]
        fixture = package.path.parent / case.fixtures.path
        public = json.loads(fixture.read_bytes())
        response = public["responses"][0]
        assert response["url"] == source.url == public["search_results"][0]["url"]
        assert set(response["body"]["items"][0]) == {"id", "published_at", "title"}
        hosts.add(urlsplit(source.url).hostname)
        briefs.add(case.brief)
        payloads.add(fixture.read_bytes())
    assert len(hosts) == len(briefs) == len(payloads) == 2
    with pytest.raises(FileExistsError):
        FIXTURE.benchmark(tmp_path / "development", split="development")
    with pytest.raises(ValueError, match="split"):
        FIXTURE.benchmark(tmp_path / "invalid", split="invalid")
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("split", ["development", "heldout"])
@pytest.mark.parametrize("encoding", ["json", "python-literal"])
def test_seven_sse_responses_use_actual_mcp_receipts_and_the_supplied_brief(
    tmp_path, split, encoding
):
    memory = pytest.importorskip("mcp.shared.memory", reason="Install the optional MCP extra")
    from research_harness.mcp.server import create_server

    package = load_benchmark(FIXTURE.benchmark(tmp_path / "benchmark", split=split))
    case = next(iter(package.cases.values()))
    fixture_path = package.path.parent / case.fixtures.path
    public_url = json.loads(fixture_path.read_bytes())["responses"][0]["url"]
    model = FIXTURE.ResearchModelFixture(case.brief, public_url)
    source_io = FixtureSources(fixture_path)
    with source_io.client() as http_client:
        service = ResearchService(
            tmp_path / "discovery",
            backend=Backend.local(tmp_path / "backend"),
            http_client=http_client,
            public_only=False,
        )
        server = create_server(service, source_io.provider)

        async def scenario():
            async with memory.create_connected_server_and_client_session(server) as client:
                tools = [
                    {
                        "type": "function",
                        "name": "research__" + tool.name,
                        "parameters": tool.inputSchema,
                    }
                    for tool in (await client.list_tools()).tools
                ]
                history = [{"role": "user", "content": case.brief}]
                receipts = {}
                for index in range(7):
                    payload = {"model": "gpt-5.4-mini", "input": history, "tools": tools}
                    response = completed(model.respond(payload))
                    assert response == model.responses[-1]
                    output = response["output"][0]
                    if index == 6:
                        assert output["type"] == "message"
                        assert (
                            json.loads(
                                output["content"][0]["text"].removeprefix(
                                    "Saved verified pipeline "
                                )
                            )
                            == receipts["submit_proposal"]["data"]["registry"]
                        )
                        break
                    name = output["name"].removeprefix("research__")
                    arguments = json.loads(output["arguments"])
                    if name == "begin_research":
                        assert arguments["brief"] == case.brief != FIXTURE._NORMAL.BRIEF
                    if name == "submit_proposal":
                        assert arguments["draft"]["research_question"] == case.brief
                        assert (
                            arguments["draft"]["candidates"][0]["probe_id"]
                            == receipts["probe_source"]["data"]["probe_id"]
                        )
                    result = await client.call_tool(name, arguments)
                    assert not result.isError
                    envelope = result.structuredContent
                    assert envelope is not None
                    if index:
                        assert envelope["status"] == "ok", envelope
                    receipts[name] = envelope
                    # Keep only the latest interaction; receipt state must remain
                    # instance-local when a context policy removes older groups.
                    history = [
                        history[0],
                        output,
                        {
                            "type": "function_call_output",
                            "call_id": output["call_id"],
                            "output": json.dumps(envelope)
                            if encoding == "json"
                            else repr(envelope),
                        },
                    ]
                return receipts

        receipts = anyio.run(scenario)
    assert len(model.requests) == len(model.responses) == 7
    assert sum(response["usage"]["total_tokens"] for response in model.responses) == 770
    assert model.probe_id == receipts["probe_source"]["data"]["probe_id"]
    assert model.saved == receipts["submit_proposal"]["data"]["registry"]
    result = score(service.output, case.specification)
    assert result["status"] == "ok" and result["quality"] == 1


def test_receipts_must_come_from_the_issued_successful_probe_and_submission():
    model = FIXTURE.ResearchModelFixture(
        "A different public research brief", "https://other.example/feed.json"
    )
    tools = [
        {"type": "function", "name": "research__" + name}
        for name in (
            "get_research_context",
            "begin_research",
            "search_sources",
            "inspect_source",
            "probe_source",
            "submit_proposal",
        )
    ]
    payload = {"model": "gpt-5.4-mini", "input": [], "tools": tools}
    for _ in range(5):
        model.respond(payload)
    probe = {"status": "ok", "data": {"status": "verified_sample", "probe_id": "returned-probe-id"}}

    def returned(call_id, envelope):
        return {
            **payload,
            "input": [
                {"type": "function_call_output", "call_id": call_id, "output": json.dumps(envelope)}
            ],
        }

    with pytest.raises(ValueError, match="verified probe receipt"):
        model.respond(returned("unrelated-call", probe))
    with pytest.raises(ValueError, match="verified probe receipt"):
        model.respond(returned("call_4", {**probe, "status": "error"}))
    response = completed(model.respond(returned("call_4", probe)))
    draft = json.loads(response["output"][0]["arguments"])["draft"]
    assert draft["research_question"] == model.brief
    assert draft["candidates"][0]["source"]["url"] == model.source.url
    assert draft["candidates"][0]["probe_id"] == "returned-probe-id"
    registry = {
        key: "returned-" + key
        for key in ("question_id", "discovery_id", "proposal_id", "pipeline_version_id")
    }
    with pytest.raises(ValueError, match="saved domain ids"):
        model.respond(returned("call_3", {"status": "ok", "data": {"registry": registry}}))
    final = completed(
        model.respond(returned("call_5", {"status": "ok", "data": {"registry": registry}}))
    )
    assert json.dumps(registry, sort_keys=True) in final["output"][0]["content"][0]["text"]
    with pytest.raises(ValueError, match="already completed"):
        model.respond(payload)
