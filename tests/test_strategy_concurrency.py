from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import anyio
import pytest
from test_strategy_session import bundle
from test_strategy_session import fake_runner as fake_runner
from test_strategy_workflow import Provider

from research_harness.backend import Backend
from research_harness.discovery import Discovery
from research_harness.discovery_models import Candidate, DataNeed, ProposalDraft
from research_harness.mcp.server import create_server
from research_harness.services.research import ResearchService
from research_harness.strategies.session import StrategySession, StrategySessionError

memory = pytest.importorskip("mcp.shared.memory")


def draft(brief):
    return ProposalDraft(
        name="fixture-access",
        title="Fixture access",
        research_question=brief,
        needs=[DataNeed(id="policy", description="Source evidence", required=True)],
        candidates=[
            Candidate(
                name="Access needed",
                purpose="Source evidence",
                covers=["policy"],
                status="needs_access",
                evidence_urls=["https://fixture.invalid/a"],
                source=None,
                probe_id=None,
                freshness_assessment="Unknown",
                historical_coverage="Unknown",
                access_notes="Source discovery only",
                limitations=["No passing probe"],
            )
        ],
        open_questions=["Obtain access"],
    )


def test_parallel_mcp_submission_cannot_commit_across_strategy_failure(
    tmp_path, fake_runner, monkeypatch
):
    brief = "Find fixture evidence"
    session = StrategySession(tmp_path / "strategy", bundle(tmp_path))
    service = ResearchService(tmp_path / "research", backend=Backend.local(tmp_path / "backend"))
    server = create_server(service, Provider(), strategy=session)
    entered, release, search_admitted = (threading.Event() for _ in range(3))
    original_submit, original_search = service.submit_proposal, service.search
    sequence = []

    def submit(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        result = original_submit(*args, **kwargs)
        sequence.append("proposal_committed")
        return result

    def search(*args, **kwargs):
        if kwargs.get("operation_id") == "invalid-search":
            sequence.append("search_admitted")
            search_admitted.set()
        return original_search(*args, **kwargs)

    monkeypatch.setattr(service, "submit_proposal", submit)
    monkeypatch.setattr(service, "search", search)

    async def scenario():
        async with memory.create_connected_server_and_client_session(server) as client:
            await client.call_tool("begin_research", {"brief": brief})
            await client.call_tool("search_sources", {"query": "first", "operation_id": "first"})
            results = {}

            async def call(name, arguments):
                results[name] = (await client.call_tool(name, arguments)).structuredContent

            async with anyio.create_task_group() as group:
                group.start_soon(
                    call,
                    "submit_proposal",
                    {"draft": draft(brief).model_dump(mode="json"), "operation_id": "submit"},
                )
                try:
                    assert await anyio.to_thread.run_sync(entered.wait, 5)
                    fake_runner.invalid = True
                    group.start_soon(
                        call, "search_sources", {"query": "bad", "operation_id": "invalid-search"}
                    )
                    # The submitted domain operation owns admission. The later
                    # projection cannot become pending/failed across that commit.
                    assert not await anyio.to_thread.run_sync(search_admitted.wait, 0.2)
                finally:
                    release.set()
            assert results["submit_proposal"]["status"] == "ok"
            # Once the winning submission commits, the domain also rejects
            # further discovery work before the invalid strategy can run.
            assert results["search_sources"]["error"]["code"] == "discovery_finished"

    anyio.run(scenario)
    assert sequence == ["proposal_committed", "search_admitted"]
    assert session.status()["status"] == "ready"
    assert fake_runner.calls == 1


def test_direct_response_cannot_save_a_proposal_after_an_inflight_strategy_failure(
    tmp_path, fake_runner
):
    brief = "Find fixture evidence"
    session = StrategySession(tmp_path / "strategy", bundle(tmp_path))

    def respond(**kwargs):
        arguments = json.dumps({"query": "fixture", "filters": None})
        discovery.tool("search_sources", arguments, operation_id="first")
        fake_runner.invalid = True
        with pytest.raises(StrategySessionError):
            discovery.tool("search_sources", arguments, operation_id="invalid")
        return SimpleNamespace(
            id="response-after-failure",
            status="completed",
            usage=None,
            output=[],
            output_parsed=draft(brief),
        )

    discovery = Discovery(
        tmp_path / "research",
        client=SimpleNamespace(responses=SimpleNamespace(parse=respond)),
        backend=Backend.local(tmp_path / "backend"),
        search_provider=Provider(),
        strategy=session,
    )
    with pytest.raises(StrategySessionError):
        discovery.run(brief)
    assert not (discovery.output / "proposal.json").exists()
