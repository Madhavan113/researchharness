from __future__ import annotations

import copy
import json
import sys
from datetime import timedelta
from importlib import import_module

import anyio
import httpx
import pytest

from research_harness.backend import Backend
from research_harness.discovery import Discovery
from research_harness.discovery_models import Candidate, DataNeed, ProposalDraft
from research_harness.services.jobs import JobService
from research_harness.services.research import ResearchService
from research_harness.util import parse_timestamp, timestamp, utcnow

memory = pytest.importorskip("mcp.shared.memory", reason="Install the optional MCP extra")
create_server = import_module("research_harness.mcp.server").create_server


class FixtureSearch:
    name = "recorded-fixture"

    def __init__(self, url):
        self.url = url
        self.calls = []

    def search(self, query, filters):
        self.calls.append((query, filters))
        return {
            "results": [{"url": self.url, "title": "Official policy"}],
            "provider_response": {
                "content": [{"type": "text", "text": "Recorded fixture response"}]
            },
            "provider_usage": {"fixture_calls": 1},
        }


@pytest.fixture
def setup(tmp_path, source, item):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={"items": [item]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = ResearchService(
            tmp_path / "discovery",
            backend=Backend.local(tmp_path / "backend"),
            http_client=client,
            public_only=False,
        )
        yield service, FixtureSearch(source.url), requests


def draft(source, probe_id):
    return ProposalDraft(
        name="policy-monitor",
        title="Policy monitor",
        research_question="Collect official policy evidence",
        needs=[DataNeed(id="policy", description="Official changes", required=True)],
        candidates=[
            Candidate(
                name="Official data",
                purpose="Monitor official policy",
                covers=["policy"],
                status="ready",
                evidence_urls=[source.url],
                source=source,
                probe_id=probe_id,
                freshness_assessment="Hourly",
                historical_coverage="Current response only",
                access_notes="Public fixture",
                limitations=["Sample only"],
            )
        ],
        open_questions=[],
    ).model_dump(mode="json")


async def call(client, name, arguments=None):
    result = await client.call_tool(name, arguments or {})
    assert not result.isError
    assert result.structuredContent is not None
    text = [block.text for block in result.content if block.type == "text"]
    assert len(text) == 1
    envelope = json.loads(text[0])
    assert envelope == result.structuredContent
    assert set(envelope) == {
        "operation_id",
        "status",
        "data",
        "evidence_refs",
        "error",
        "remaining",
    }
    return envelope


async def begin_and_search(client):
    started = await call(client, "begin_research", {"brief": "Collect official policy evidence"})
    searched = await call(
        client, "search_sources", {"query": "official policy", "operation_id": "search-1"}
    )
    assert started["status"] == searched["status"] == "ok"
    return started, searched


def test_protocol_tools_have_typed_schemas_and_no_agent_control_of_runtime_or_context(setup):
    service, provider, _ = setup
    server = create_server(service, provider)

    async def scenario():
        async with memory.create_connected_server_and_client_session(server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == {
                "begin_research",
                "get_research_context",
                "search_sources",
                "inspect_source",
                "probe_source",
                "get_evidence",
                "submit_proposal",
                "list_pipelines",
                "start_collection",
                "get_job",
                "list_jobs",
                "cancel_job",
                "export_observations",
                "read_export",
            }
            for tool in tools.values():
                assert tool.inputSchema["additionalProperties"] is False
                assert tool.outputSchema["type"] == "object"
                assert "discovery_id" not in tool.inputSchema["properties"]
                assert "model" not in tool.inputSchema["properties"]
            assert set(tools["begin_research"].inputSchema["properties"]) == {
                "brief",
                "question_id",
            }
            assert (
                tools["probe_source"]
                .inputSchema["properties"]["source"]["$ref"]
                .endswith("/SourceSpec")
            )
            assert (
                tools["submit_proposal"]
                .inputSchema["properties"]["draft"]["$ref"]
                .endswith("/ProposalDraft")
            )
            assert set(tools["search_sources"].inputSchema["required"]) == {"query", "operation_id"}
            assert tools["get_evidence"].annotations.readOnlyHint is True
            for name in ("get_research_context", "get_job", "list_jobs"):
                annotations = tools[name].annotations
                assert annotations.readOnlyHint is False
                assert annotations.destructiveHint is False
                assert annotations.idempotentHint is True
                assert annotations.openWorldHint is False
            unbound = await call(client, "get_research_context")
            assert unbound["error"]["code"] == "not_initialized"
            injected = await call(
                client, "begin_research", {"brief": "Policy", "model": "agent-selected"}
            )
            assert injected["status"] == "error"
            assert service.discovery_id is None
            started = await call(client, "begin_research", {"brief": "Policy"})
            assert started["data"]["model"] == "gpt-5.4-mini"

    anyio.run(scenario)


def test_protocol_completes_discovery_and_retries_without_duplicate_work(setup, source):
    service, provider, requests = setup
    runtime = {"model": "fixture-model", "adapter": "fixture-omnigent", "strategy_hash": "fixed"}
    server = create_server(service, provider, runtime)
    runtime["model"] = "changed-after-construction"

    async def scenario():
        async with memory.create_connected_server_and_client_session(server) as client:
            started, searched = await begin_and_search(client)
            again = await call(
                client, "begin_research", {"brief": "Collect official policy evidence"}
            )
            assert started["data"] == again["data"]
            assert started["data"]["model"] == "fixture-model"
            duplicate_search = await call(
                client, "search_sources", {"query": "official policy", "operation_id": "search-1"}
            )
            assert duplicate_search["data"] == searched["data"]
            assert len(provider.calls) == 1
            inspected = await call(
                client, "inspect_source", {"url": source.url, "operation_id": "inspect-1"}
            )
            proof = await call(
                client,
                "probe_source",
                {"source": source.model_dump(mode="json"), "operation_id": "probe-1"},
            )
            duplicate = await call(
                client,
                "probe_source",
                {"source": source.model_dump(mode="json"), "operation_id": "probe-1"},
            )
            assert duplicate["data"] == proof["data"]
            assert len(requests) == 2
            assert proof["remaining"] == {"search": 7, "inspection": 15, "probe": 11}
            page = await call(
                client, "get_evidence", {"receipt_id": inspected["data"]["receipt_id"], "limit": 10}
            )
            assert len(page["data"]["content"]) == 10 and page["data"]["next_offset"] == 10
            arguments = {
                "draft": draft(source, proof["data"]["probe_id"]),
                "operation_id": "proposal-1",
            }
            saved = await call(client, "submit_proposal", arguments)
            assert saved["status"] == "ok"
            ids = saved["data"]["registry"]
            assert ids["proposal_id"] and ids["pipeline_version_id"]
            repeated = await call(client, "submit_proposal", arguments)
            assert repeated["data"] == saved["data"]
            pipelines = await call(client, "list_pipelines")
            assert [pipeline["id"] for pipeline in pipelines["data"]["pipelines"]] == [
                ids["pipeline_version_id"]
            ]
            assert json.loads((service.output / "proposal.json").read_text())["registry"] == ids
            assert (service.output / "pipeline.json").exists()
            with service.backend.open_registry() as store:
                assert store.count("proposals") == store.count("pipeline_versions") == 1
            return ids

    ids = anyio.run(scenario)
    resumed = ResearchService(service.output, backend=service.backend)
    resumed.resume(ids["discovery_id"])

    async def reopen():
        async with memory.create_connected_server_and_client_session(
            create_server(resumed, provider)
        ) as client:
            context = await call(client, "get_research_context")
            assert context["data"]["discovery_id"] == ids["discovery_id"]
            assert context["data"]["pipelines"][0]["id"] == ids["pipeline_version_id"]

    anyio.run(reopen)


def test_protocol_rejects_changed_config_unobserved_citations_and_repairs_with_new_operation_ids(
    setup, source
):
    service, provider, _ = setup

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            await begin_and_search(client)
            proof = await call(
                client,
                "probe_source",
                {"source": source.model_dump(mode="json"), "operation_id": "probe-1"},
            )
            correct = draft(source, proof["data"]["probe_id"])
            changed = copy.deepcopy(correct)
            changed["candidates"][0]["source"]["items_pointer"] = "/unprobed"
            rejected = await call(
                client, "submit_proposal", {"draft": changed, "operation_id": "changed-config"}
            )
            assert rejected["status"] == "error"
            assert "changed after probing" in rejected["error"]["message"]
            unsupported_citation = copy.deepcopy(correct)
            unsupported_citation["candidates"][0]["evidence_urls"].append(
                "https://invented.example/"
            )
            rejected = await call(
                client,
                "submit_proposal",
                {"draft": unsupported_citation, "operation_id": "bad-citation"},
            )
            assert rejected["status"] == "error"
            assert "actually observed" in rejected["error"]["message"]
            with service.backend.open_registry() as store:
                assert store.count("proposals") == 0
            repaired = await call(
                client, "submit_proposal", {"draft": correct, "operation_id": "fixed"}
            )
            assert repaired["status"] == "ok"

    anyio.run(scenario)


def test_protocol_rejects_another_contexts_probes_receipts_and_context_arguments(
    setup, tmp_path, source
):
    service, provider, _ = setup
    other = ResearchService(
        tmp_path / "other",
        backend=service.backend,
        http_client=service.http_client,
        public_only=False,
    )
    other.begin("Other brief", model="fixture")
    alien = other.probe(source, operation_id="other-probe")

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            started, _ = await begin_and_search(client)
            for name, arguments, expected_code in (
                ("get_evidence", {"receipt_id": alien["receipt_id"]}, "evidence_not_found"),
                (
                    "submit_proposal",
                    {"draft": draft(source, alien["probe_id"]), "operation_id": "wrong-context"},
                    "invalid_argument",
                ),
                ("get_research_context", {"discovery_id": other.discovery_id}, "invalid_argument"),
                (
                    "list_pipelines",
                    {"question_id": other.get_context()["question_id"]},
                    "invalid_argument",
                ),
                ("get_job", {"job_id": "unknown-job"}, "evidence_not_found"),
                (
                    "begin_research",
                    {"brief": "Other brief", "question_id": other.get_context()["question_id"]},
                    "context_conflict",
                ),
            ):
                rejected = await call(client, name, arguments)
                assert rejected["status"] == "error"
                assert rejected["error"]["code"] == expected_code
                assert rejected["error"]["retryable"] is False
            context = await call(client, "get_research_context")
            assert context["data"]["discovery_id"] == started["data"]["discovery_id"]
            with service.backend.open_registry() as store:
                assert store.count("proposals") == 0

    anyio.run(scenario)


def test_protocol_operation_id_conflicts_and_invalid_arguments_do_not_repeat_network(setup, source):
    service, provider, requests = setup

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            await begin_and_search(client)
            await call(client, "inspect_source", {"url": source.url, "operation_id": "same"})
            conflict = await call(
                client, "inspect_source", {"url": "https://other.example/", "operation_id": "same"}
            )
            assert conflict["error"]["code"] == "operation_conflict"
            assert len(requests) == 1
            for name, arguments in (
                ("probe_source", {"source": {"id": "invalid"}, "operation_id": "bad-schema"}),
                ("search_sources", {"query": "policy"}),
                ("get_evidence", {"receipt_id": "x", "limit": 50_001}),
                ("submit_proposal", {"draft": {}, "operation_id": "bad-proposal"}),
                (
                    "search_sources",
                    {
                        "query": "policy",
                        "operation_id": "fake-result",
                        "results": [{"url": "https://invented.example/"}],
                    },
                ),
            ):
                rejected = await call(client, name, arguments)
                assert rejected["error"]["code"] == "invalid_argument"
            assert len(requests) == len(provider.calls) == 1

    anyio.run(scenario)


def test_unconfigured_provider_returns_actionable_envelope_without_claimed_search(setup):
    service, _, _ = setup

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service)
        ) as client:
            await call(client, "begin_research", {"brief": "Policy"})
            unavailable = await call(
                client, "search_sources", {"query": "policy", "operation_id": "missing-provider"}
            )
            assert unavailable["error"] == {
                "code": "provider_unavailable",
                "message": "No search provider is configured by the host.",
                "retryable": False,
            }
            assert unavailable["remaining"]["search"] == 8
            assert service.get_context()["observed_urls"] == []

    anyio.run(scenario)


def test_failed_probe_still_returns_evidence_and_consumes_budget(setup, source):
    service, provider, requests = setup
    empty = source.model_copy(update={"items_pointer": "/absent"})
    service.begin("Policy", model="fixture", limits={"probe": 1})

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            failed = await call(
                client,
                "probe_source",
                {"source": empty.model_dump(mode="json"), "operation_id": "failed-probe"},
            )
            assert failed["error"]["code"] == "source_validation_failed"
            assert failed["data"]["status"] == "failed"
            assert failed["evidence_refs"][0]["receipt_id"] == failed["data"]["receipt_id"]
            exhausted = await call(
                client,
                "probe_source",
                {"source": source.model_dump(mode="json"), "operation_id": "exhausted"},
            )
            assert exhausted["error"]["code"] == "budget_exhausted"
            assert len(requests) == 1

    anyio.run(scenario)


def test_failed_discovery_context_remains_readable_as_a_successful_lookup(setup):
    service, provider, _ = setup
    service.begin("Failed case", model="fixture")
    service.finish_failure(RuntimeError("Fixture interruption"), usage={})

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            context = await call(client, "get_research_context")
            assert context["status"] == "ok"
            assert context["data"]["status"] == "failed"
            denied = await call(
                client, "search_sources", {"query": "policy", "operation_id": "late"}
            )
            assert denied["error"]["code"] == "discovery_finished"
            assert denied["error"]["retryable"] is False
            assert denied["remaining"]["search"] == 8 and provider.calls == []

    anyio.run(scenario)


@pytest.mark.parametrize(
    "changed",
    [{"model": "another-model"}, {"session_id": "other-session"}, {"adapter": "another-adapter"}],
)
def test_resumed_server_rejects_conflicting_host_runtime_binding(setup, changed):
    service, provider, _ = setup
    service.begin(
        "Policy", model="fixture", runtime={"session_id": "original-session", "adapter": "mcp"}
    )
    with pytest.raises(ValueError, match="Runtime binding conflicts"):
        create_server(service, provider, changed)
    assert create_server(service, provider, {"model": "fixture", "session_id": None})


def test_host_budgets_are_enforced_and_cannot_change_on_resume(setup):
    service, provider, _ = setup
    now = utcnow()
    service.backend.clock = lambda: now
    limits = {"search": 0, "inspection": 1, "probe": 2}
    server = create_server(service, provider, research_limits=limits, research_deadline_seconds=17)

    async def scenario():
        async with memory.create_connected_server_and_client_session(server) as client:
            tools = (await client.list_tools()).tools
            begin_tool = next(tool for tool in tools if tool.name == "begin_research")
            assert "research_limits" not in begin_tool.inputSchema["properties"]
            assert "research_deadline_seconds" not in begin_tool.inputSchema["properties"]
            assert (await call(client, "begin_research", {"brief": "Check fixed budgets"}))[
                "status"
            ] == "ok"
            context = service.get_context()
            assert context["limits"] == limits
            assert parse_timestamp(context["deadline_at"]) == now + timedelta(seconds=17)
            denied = await call(
                client, "search_sources", {"query": "official policy", "operation_id": "search"}
            )
            assert denied["error"]["code"] == "budget_exhausted"
            assert provider.calls == []

    anyio.run(scenario)
    original_deadline = service.get_context()["deadline_at"]
    assert create_server(service, provider, research_limits=limits, research_deadline_seconds=17)
    assert service.get_context()["deadline_at"] == original_deadline
    with pytest.raises(ValueError, match="limits conflict"):
        create_server(service, provider, research_limits={"search": 5})
    with pytest.raises(ValueError, match="deadline conflicts"):
        create_server(service, provider, research_deadline_seconds=18)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"research_limits": {"search": True}},
        {"research_limits": {"unknown": 1}},
        {"research_deadline_seconds": True},
        {"research_deadline_seconds": 0},
    ],
)
def test_invalid_host_budgets_rejected_before_discovery(setup, kwargs):
    service, provider, _ = setup
    with pytest.raises(ValueError, match="Host research"):
        create_server(service, provider, **kwargs)
    assert service.discovery_id is None


@pytest.mark.parametrize("model", [None, "saved-custom-fixture"])
def test_cli_stdio_transport_initializes_and_reopens_saved_context(tmp_path, model):
    mcp = import_module("mcp")
    stdio = import_module("mcp.client.stdio")
    output = tmp_path / "stdio-discovery"
    parameters = stdio.StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "research_harness",
            "mcp",
            "serve",
            "--out",
            str(output),
            "--session-id",
            "stdio-session",
        ],
        env={
            "RH_LOCAL_ROOT": str(tmp_path / "stdio-backend"),
            "RH_DATABASE_URL": "",
            "RH_BLOB_BUCKET": "",
        },
    )
    initial_parameters = (
        parameters.model_copy(update={"args": [*parameters.args, "--model", model]})
        if model
        else parameters
    )
    expected_model = model or "gpt-5.4-mini"

    async def scenario():
        with (tmp_path / "server-stderr.log").open("w") as diagnostics:
            async with stdio.stdio_client(initial_parameters, errlog=diagnostics) as (read, write):
                async with mcp.ClientSession(read, write) as client:
                    await client.initialize()
                    assert len((await client.list_tools()).tools) == 14
                    begun = await call(client, "begin_research", {"brief": "Saved stdio case"})
                    assert begun["status"] == "ok"
                    assert begun["data"]["model"] == expected_model
                    discovery_id = begun["data"]["discovery_id"]
                    assert begun["data"]["runtime"]["session_id"] == "stdio-session"
            async with stdio.stdio_client(parameters, errlog=diagnostics) as (read, write):
                async with mcp.ClientSession(read, write) as client:
                    await client.initialize()
                    context = await call(client, "get_research_context")
                    assert context["data"]["discovery_id"] == discovery_id
                    assert context["data"]["brief"] == "Saved stdio case"
                    assert context["data"]["model"] == expected_model
                    assert context["data"]["runtime"]["session_id"] == "stdio-session"
        assert json.loads((output / "context.json").read_text())["discovery_id"] == discovery_id

    anyio.run(scenario)


@pytest.mark.parametrize("filters", [{"max_results": 0}, {"snippet_max_length": 179}, {"extra": 1}])
def test_invalid_filters_have_the_same_admission_behavior_in_both_paths(setup, tmp_path, filters):
    service, provider, _ = setup
    direct_provider = FixtureSearch(provider.url)
    direct = Discovery(
        tmp_path / "direct",
        client=None,
        backend=Backend.local(tmp_path / "direct-backend"),
        search_provider=direct_provider,
    )
    direct.service.begin("Policy", model="fixture")

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            await call(client, "begin_research", {"brief": "Policy"})
            arguments = {"query": "policy", "filters": filters}
            rejected = await call(client, "search_sources", {**arguments, "operation_id": "fix"})
            direct_rejected = direct._tool(
                "search_sources", json.dumps(arguments), operation_id="fix"
            )
            assert rejected["error"]["code"] == "invalid_argument"
            assert direct_rejected["status"] == "error"
            assert service.get_context()["operations"] == []
            assert direct.service.get_context()["operations"] == []
            assert rejected["remaining"] == direct.service.get_remaining()
            assert rejected["remaining"]["search"] == 8
            assert provider.calls == direct_provider.calls == []
            repaired = {"query": "policy", "filters": {"max_results": 1}}
            accepted = await call(client, "search_sources", {**repaired, "operation_id": "fix"})
            direct_accepted = direct._tool(
                "search_sources", json.dumps(repaired), operation_id="fix"
            )
            assert accepted["status"] == "ok" and direct_accepted["results"]
            assert len(provider.calls) == len(direct_provider.calls) == 1
            assert accepted["remaining"] == direct.service.get_remaining()
            assert accepted["remaining"]["search"] == 7

    anyio.run(scenario)


def test_writer_hostname_404_and_its_replay_are_nonretryable(setup, monkeypatch):
    service, provider, _ = setup
    requests = []

    def missing(request):
        requests.append(str(request.url))
        return httpx.Response(404)

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            await call(client, "begin_research", {"brief": "Policy"})
            arguments = {"url": "https://writer.example/missing", "operation_id": "missing"}
            for _ in range(2):
                result = await call(client, "inspect_source", arguments)
                assert result["error"]["code"] == "operation_failed"
                assert result["error"]["retryable"] is False
                assert "404" in result["error"]["message"]
                assert result["remaining"]["inspection"] == 15
            assert requests == [arguments["url"]]
            assert service.get_context()["observed_urls"] == []

    with httpx.Client(transport=httpx.MockTransport(missing)) as http_client:
        monkeypatch.setattr(service, "http_client", http_client)
        anyio.run(scenario)


@pytest.mark.parametrize(
    "message",
    [
        "writer unavailable",
        "begin or resume",
        "different arguments",
        "budget exhausted",
        "does not belong",
        "already bound",
        "discovery is finished",
    ],
)
def test_provider_messages_cannot_impersonate_domain_errors(setup, monkeypatch, message):
    service, provider, _ = setup
    attempts = []

    def fail(query, filters):
        attempts.append(query)
        raise RuntimeError(message)

    monkeypatch.setattr(provider, "search", fail)

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            await call(client, "begin_research", {"brief": "Policy"})
            for _ in range(2):
                failed = await call(
                    client, "search_sources", {"query": "policy", "operation_id": "failed"}
                )
                assert failed["error"]["code"] == "operation_failed"
                assert failed["error"]["retryable"] is False
                assert message in failed["error"]["message"]
                assert failed["remaining"]["search"] == 7
            assert attempts == ["policy"]

    anyio.run(scenario)


def test_actual_writer_contention_is_retryable_without_admitting_an_operation(setup):
    service, provider, _ = setup
    service.begin("Policy", model="fixture")

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            arguments = {"query": "policy", "operation_id": "retry"}
            with service.backend.open_registry() as store:
                with store.operation_lock(f"discovery:{service.discovery_id}"):
                    busy = await call(client, "search_sources", arguments)
                    assert busy["error"]["code"] == "operation_busy"
                    assert busy["error"]["retryable"] is True
                    assert busy["remaining"]["search"] == 8
                    assert provider.calls == []
                    assert service.get_context()["operations"] == []
            result = await call(client, "search_sources", arguments)
            assert result["status"] == "ok" and result["remaining"]["search"] == 7
            assert len(provider.calls) == 1

    anyio.run(scenario)


def test_envelope_budget_reporting_does_not_depend_on_loading_full_context(setup, monkeypatch):
    service, provider, _ = setup

    def unavailable_context():
        raise AssertionError("Full context is unavailable")

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            await call(client, "begin_research", {"brief": "Policy"})
            monkeypatch.setattr(service, "get_context", unavailable_context)
            result = await call(
                client, "search_sources", {"query": "policy", "operation_id": "search"}
            )
            assert result["status"] == "ok"
            assert result["remaining"] == {"search": 7, "inspection": 16, "probe": 12}
            assert len(provider.calls) == 1

    anyio.run(scenario)


def test_budget_read_failure_preserves_completed_receipt_and_allows_replay_after_repair(
    setup, monkeypatch
):
    service, provider, _ = setup
    budget_reads = []

    def unavailable_budget():
        budget_reads.append(True)
        raise OSError("Registry budget read failed")

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            await call(client, "begin_research", {"brief": "Policy"})
            arguments = {"query": "policy", "operation_id": "search"}
            with monkeypatch.context() as patch:
                patch.setattr(service, "get_remaining", unavailable_budget)
                result = await call(client, "search_sources", arguments)
            assert result["status"] == "error" and result["remaining"] is None
            assert result["error"] == {
                "code": "budget_state_unavailable",
                "message": "Registry budget read failed",
                "retryable": False,
            }
            assert result["data"]["receipt_id"] == result["evidence_refs"][0]["receipt_id"]
            assert result["operation_id"] == "search" and len(budget_reads) == 1
            replay = await call(client, "search_sources", arguments)
            assert replay["status"] == "ok" and replay["data"] == result["data"]
            assert replay["remaining"]["search"] == 7 and len(provider.calls) == 1

    anyio.run(scenario)


def test_budget_read_failure_keeps_primary_error_and_disables_automatic_retry(setup, monkeypatch):
    service, provider, _ = setup
    service.begin("Policy", model="fixture")

    def unavailable_budget():
        raise OSError("Registry budget read failed")

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider)
        ) as client:
            monkeypatch.setattr(service, "get_remaining", unavailable_budget)
            with service.backend.open_registry() as store:
                with store.operation_lock(f"discovery:{service.discovery_id}"):
                    failed = await call(
                        client, "search_sources", {"query": "policy", "operation_id": "retry"}
                    )
            assert failed["status"] == "error" and failed["remaining"] is None
            assert failed["error"]["code"] == "operation_busy"
            assert failed["error"]["retryable"] is False
            assert failed["error"]["remaining_error"]["code"] == "budget_state_unavailable"
            assert "Registry budget read failed" in failed["error"]["remaining_error"]["message"]
            assert provider.calls == []

    anyio.run(scenario)


def test_protocol_collection_export_and_reconnect_use_published_state(setup, source):
    service, provider, requests = setup
    jobs = JobService(service, auto_launch=False)

    async def scenario():
        async with memory.create_connected_server_and_client_session(
            create_server(service, provider, jobs=jobs)
        ) as client:
            await begin_and_search(client)
            proof = await call(
                client,
                "probe_source",
                {"source": source.model_dump(mode="json"), "operation_id": "probe"},
            )
            saved = await call(
                client,
                "submit_proposal",
                {"draft": draft(source, proof["data"]["probe_id"]), "operation_id": "proposal"},
            )
            pipeline = saved["data"]["registry"]["pipeline_version_id"]
            args = {"pipeline_version_id": pipeline, "operation_id": "collect"}
            queued = await call(client, "start_collection", args)
            assert queued["status"] == "ok" and queued["data"]["status"] == "queued"
            job_id = queued["data"]["id"]
            assert (await call(client, "start_collection", args))["data"]["id"] == job_id
            await anyio.to_thread.run_sync(lambda: jobs.run_job(job_id, client=service.http_client))
            completed = await call(client, "get_job", {"job_id": job_id})
            assert completed["data"]["status"] == "succeeded"
            assert completed["data"]["collection"]["sources"][0]["record_count"] == 1
            cancelled = await call(client, "cancel_job", {"job_id": job_id})
            assert cancelled["data"]["status"] == "succeeded"
            export = await call(
                client,
                "export_observations",
                {
                    "pipeline_version_id": pipeline,
                    "operation_id": "export",
                    "as_of": timestamp(utcnow()),
                },
            )
            export_id = export["data"]["id"]
            await anyio.to_thread.run_sync(lambda: jobs.run_job(export_id))
            text = await call(client, "read_export", {"job_id": export_id})
            assert json.loads(text["data"]["content"])["lineage"]["source_url"] == source.url
            pending = await call(
                client, "start_collection", {**args, "operation_id": "never-start"}
            )
            cancelled = await call(client, "cancel_job", {"job_id": pending["data"]["id"]})
            assert cancelled["data"]["status"] == "cancelled"
            assert len(requests) == 2  # One proof and one published collection.
        resumed = ResearchService(service.output, backend=service.backend)
        resumed.resume(service.discovery_id)
        async with memory.create_connected_server_and_client_session(
            create_server(resumed, provider, jobs=JobService(resumed, auto_launch=False))
        ) as client:
            context = await call(client, "get_research_context")
            listed = await call(client, "list_jobs")
            assert (
                {job["id"] for job in context["data"]["jobs"]}
                == {job["id"] for job in listed["data"]["jobs"]}
                == {job_id, export_id, pending["data"]["id"]}
            )
            manifest = await call(
                client, "read_export", {"job_id": export_id, "artifact": "manifest"}
            )
            assert json.loads(manifest["data"]["content"])["record_count"] == 1

    anyio.run(scenario)
