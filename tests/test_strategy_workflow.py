from __future__ import annotations

import json

import anyio
import httpx
import pytest
from test_strategy_session import bundle

from research_harness.backend import Backend
from research_harness.discovery import Discovery
from research_harness.discovery_models import Candidate, DataNeed, ProposalDraft
from research_harness.mcp.server import create_server
from research_harness.services.research import ResearchService
from research_harness.strategies.session import StrategySession, StrategySessionError

memory = pytest.importorskip("mcp.shared.memory")


class Provider:
    name = "projection-fixture"

    def __init__(self):
        self.calls = 0
        self.results = [
            {"url": "https://fixture.invalid/a", "title": "Alpha"},
            {"url": "https://fixture.invalid/b", "title": "Beta"},
        ]

    def search(self, query, filters):
        self.calls += 1
        return {"results": self.results, "provider_response": {"raw": self.results}}


def test_direct_and_mcp_use_the_same_projection_and_preserve_receipts(tmp_path, fake_runner):
    authored = bundle(tmp_path)
    direct_provider, mcp_provider = Provider(), Provider()
    direct_session = StrategySession(tmp_path / "direct-strategy", authored)
    discovery = Discovery(
        tmp_path / "direct",
        client=None,
        backend=Backend.local(tmp_path / "direct-backend"),
        search_provider=direct_provider,
        strategy=direct_session,
    )
    discovery.register_start("Collect fixture source evidence")
    direct = discovery.tool(
        "search_sources", '{"query":"fixture","filters":null}', operation_id="search-1"
    )
    service = ResearchService(tmp_path / "mcp", backend=Backend.local(tmp_path / "mcp-backend"))
    mcp_session = StrategySession(tmp_path / "mcp-strategy", authored)
    server = create_server(service, mcp_provider, strategy=mcp_session)

    async def scenario():
        async with memory.create_connected_server_and_client_session(server) as client:
            await client.call_tool("begin_research", {"brief": "Collect fixture source evidence"})
            arguments = {"query": "fixture", "operation_id": "search-1"}
            first = (await client.call_tool("search_sources", arguments)).structuredContent
            second = (await client.call_tool("search_sources", arguments)).structuredContent
            assert first == second
            return first

    mcp = anyio.run(scenario)
    assert mcp["status"] == "ok"
    assert direct["results"] == mcp["data"]["results"] == list(reversed(direct_provider.results))
    assert direct["strategy_view"] == mcp["data"]["strategy_view"]
    assert fake_runner.calls == 2 and direct_provider.calls == mcp_provider.calls == 1
    for output in (discovery.output, service.output):
        receipt = json.loads((output / "receipts.json").read_bytes())[0]["payload"]
        assert receipt["results"] == direct_provider.results
        assert "strategy_view" not in receipt
    with pytest.raises(ValueError, match="original code strategy"):
        create_server(service, mcp_provider)
    create_server(service, mcp_provider, strategy=StrategySession(mcp_session.root, authored))
    with pytest.raises(ValueError, match="original strategy session state"):
        create_server(
            service, mcp_provider, strategy=StrategySession(tmp_path / "replacement", authored)
        )


def test_failed_strategy_blocks_mcp_proposal_and_terminates_direct_tool_path(tmp_path, fake_runner):
    authored = bundle(tmp_path)
    fake_runner.invalid = True
    direct = Discovery(
        tmp_path / "direct",
        client=None,
        backend=Backend.local(tmp_path / "direct-backend"),
        search_provider=Provider(),
        strategy=StrategySession(tmp_path / "direct-strategy", authored),
    )
    direct.register_start("Collect fixture source evidence")
    with pytest.raises(StrategySessionError):
        direct.tool("search_sources", '{"query":"fixture","filters":null}', operation_id="search-1")
    service = ResearchService(tmp_path / "mcp", backend=Backend.local(tmp_path / "mcp-backend"))
    server = create_server(
        service, Provider(), strategy=StrategySession(tmp_path / "mcp-strategy", authored)
    )
    draft = ProposalDraft(
        name="uncovered",
        title="Uncovered",
        research_question="Collect fixture source evidence",
        needs=[DataNeed(id="policy", description="Source evidence", required=True)],
        candidates=[
            Candidate(
                name="Missing access",
                purpose="Source evidence",
                covers=["policy"],
                status="needs_access",
                evidence_urls=[],
                source=None,
                probe_id=None,
                freshness_assessment="Unknown",
                historical_coverage="Unknown",
                access_notes="Access unavailable",
                limitations=["No evidence"],
            )
        ],
        open_questions=["No verified sources"],
    ).model_dump(mode="json")

    async def scenario():
        async with memory.create_connected_server_and_client_session(server) as client:
            await client.call_tool("begin_research", {"brief": "Collect fixture source evidence"})
            search = (
                await client.call_tool(
                    "search_sources", {"query": "fixture", "operation_id": "search-1"}
                )
            ).structuredContent
            assert search["status"] == "error" and search["error"]["code"] == "strategy_failed"
            proposed = (
                await client.call_tool(
                    "submit_proposal", {"draft": draft, "operation_id": "submit-1"}
                )
            ).structuredContent
            assert proposed["status"] == "error" and proposed["error"]["code"] == "strategy_failed"

    anyio.run(scenario)
    assert not (service.output / "proposal.json").exists()
    assert fake_runner.calls == 2


def test_probe_failure_remains_an_error_envelope_after_projection(tmp_path, source, fake_runner):
    authored = bundle(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as http:
        service = ResearchService(
            tmp_path / "research",
            backend=Backend.local(tmp_path / "backend"),
            http_client=http,
            public_only=False,
        )
        server = create_server(
            service, Provider(), strategy=StrategySession(tmp_path / "strategy", authored)
        )

        async def scenario():
            async with memory.create_connected_server_and_client_session(server) as client:
                await client.call_tool("begin_research", {"brief": "Probe fixture"})
                response = (
                    await client.call_tool(
                        "probe_source",
                        {"source": source.model_dump(mode="json"), "operation_id": "probe-1"},
                    )
                ).structuredContent
                assert response["status"] == "error"
                assert response["error"]["code"] == "source_validation_failed"
                assert response["data"]["status"] == "failed"
                assert response["data"]["limits"] and response["data"]["error"]
                assert response["data"]["strategy_view"]["kind"] == "candidate_interpretation"

        anyio.run(scenario)
