from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anyio
import pytest

import research_harness.evaluation.runtime_executor as executor_module
from research_harness.backend import Backend
from research_harness.discovery_models import Candidate, DataNeed, ProposalDraft
from research_harness.evaluation.controller import CaseTask, ComparisonConfig
from research_harness.evaluation.fixture_transport import FixtureSources, fixture_server
from research_harness.evaluation.fixtures import FixtureProvider
from research_harness.evaluation.runtime_executor import RuntimeExecutionError, RuntimeExecutor
from research_harness.execution import DiscoverySettings
from research_harness.integrations.omnigent import (
    PRIMARY_SESSION_ENV,
    bind_case,
    prepare_case,
)
from research_harness.util import canonical_json, digest, write_json


@pytest.fixture
def recording(tmp_path, source, item):
    path = tmp_path / "sources.json"
    write_json(
        path,
        {
            "authorship": "Synthetic source bytes; no model or live-source quality claim",
            "not_model_input": "host-only-marker",
            "search_results": [{"url": source.url, "title": "Official policy fixture"}],
            "responses": [
                {
                    "url": source.url,
                    "headers": {"content-type": "application/json"},
                    "body": {"items": [item]},
                },
                {
                    "url": "https://source.example/page",
                    "headers": {"content-type": "text/html"},
                    "body": "<p>Authored source page: π</p>\n",
                },
            ],
        },
    )
    return path


def task_for(tmp_path, recording, arm="direct"):
    return CaseTask(
        case_id="policy",
        brief="Find an official source of policy changes.",
        arm=arm,
        output=tmp_path / arm,
        fixture_path=recording,
        instructions="Use the shared research policy.\n",
        config=ComparisonConfig(
            execution="model",
            settings=DiscoverySettings(
                max_rounds=6,
                max_output_tokens=777,
                max_searches=1,
                max_inspections=2,
                max_probes=1,
                deadline_seconds=45,
            ),
        ),
    )


def executor(base_url="http://127.0.0.1:12345/v1"):
    return RuntimeExecutor(
        base_url=base_url,
        api_key="local-gateway-fixture-key",
        omnigent_python=Path(sys.executable),
        max_spend_usd=0.25,
    )


@contextmanager
def model_server(respond):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": payload,
                }
            )
            try:
                status, result = respond(payload, len(requests))
            except Exception as exc:
                status, result = 500, {"error": {"message": str(exc), "type": "fixture_error"}}
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def response(output, number):
    return {
        "id": f"resp_{number}",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "gpt-5.4-mini",
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    }


def call(name, arguments, number):
    return {
        "id": f"fc_{number}",
        "type": "function_call",
        "name": name,
        "call_id": f"call_{number}",
        "status": "completed",
        "arguments": json.dumps(arguments),
    }


def proposal(source, probe_id):
    return ProposalDraft(
        name="policy",
        title="Policy source",
        research_question="Find an official source of policy changes.",
        needs=[DataNeed(id="policy", description="Policy publications", required=True)],
        candidates=[
            Candidate(
                name="Policy fixture",
                purpose="Collect policy publications",
                covers=["policy"],
                status="ready",
                evidence_urls=[source.url],
                source=source,
                probe_id=probe_id,
                freshness_assessment="Authored fixture",
                historical_coverage="Only the sample",
                access_notes="Offline source recording",
                limitations=["No live availability claim"],
            )
        ],
        open_questions=[],
    )


def test_shared_fixture_source_bytes_and_no_live_fallback(recording, source, item):
    fixtures = FixtureSources(recording)
    assert fixtures.sha256 == digest(recording.read_bytes())
    assert fixtures.provider.name == FixtureProvider.name
    with fixtures.client() as client:
        assert client.get(source.url).content == canonical_json({"items": [item]}).encode()
        assert (
            client.get("https://source.example/page").content
            == "<p>Authored source page: π</p>\n".encode()
        )
        with pytest.raises(RuntimeError, match="network access is disabled"):
            client.get("https://unrecorded.example/data")
        with pytest.raises(RuntimeError, match="network access is disabled"):
            client.post(source.url)
    fixture = json.loads(recording.read_text())
    fixture["responses"].append(fixture["responses"][0])
    write_json(recording, fixture)
    with pytest.raises(ValueError, match="URLs must be unique"):
        FixtureSources(recording)


def test_bound_fixture_mcp_uses_shared_limits_evidence_and_rejects_jobs(
    tmp_path, recording, source, item
):
    memory = pytest.importorskip("mcp.shared.memory")
    task = task_for(tmp_path, recording, "omnigent")
    case = prepare_case(
        task.output,
        brief=task.brief,
        instructions=task.instructions,
        settings=task.config.settings,
        max_spend_usd=0.25,
    )
    env = {
        PRIMARY_SESSION_ENV: "conv_fixture",
        "RUNNER_SERVER_URL": "http://127.0.0.1:12345",
        "RH_DATABASE_URL": "must-not-select-a-shared-database",
        "RH_BLOB_BUCKET": "must-not-select-a-shared-bucket",
    }

    async def scenario():
        with fixture_server(case, recording, env=env) as server:
            async with memory.create_connected_server_and_client_session(server) as client:

                async def invoke(name, args):
                    result = await client.call_tool(name, args)
                    assert not result.isError
                    return result.structuredContent

                begun = await invoke("begin_research", {"brief": task.brief})
                assert begun["status"] == "ok"
                found = await invoke("search_sources", {"query": "policy", "operation_id": "s1"})
                assert found["status"] == "ok"
                denied = await invoke(
                    "search_sources", {"query": "more policy", "operation_id": "s2"}
                )
                assert "budget exhausted" in denied["error"]["message"]
                inspected = await invoke(
                    "inspect_source", {"url": source.url, "operation_id": "i1"}
                )
                assert inspected["status"] == "ok"
                unknown = await invoke(
                    "inspect_source",
                    {"url": "https://unrecorded.example/data", "operation_id": "i2"},
                )
                assert "network access is disabled" in json.dumps(unknown)
                probed = await invoke(
                    "probe_source", {"source": source.model_dump(mode="json"), "operation_id": "p1"}
                )
                assert probed["data"]["status"] == "verified_sample"
                for name, arguments in [
                    ("start_collection", {"pipeline_version_id": "unused", "operation_id": "c1"}),
                    (
                        "export_observations",
                        {
                            "pipeline_version_id": "unused",
                            "operation_id": "e1",
                            "as_of": "2026-01-01T00:00:00Z",
                        },
                    ),
                ]:
                    result = await invoke(name, arguments)
                    assert "discovery-only comparison" in result["error"]["message"]
            context = server.research_service.get_context()
            assert server.research_service.backend.mode == "local"
            assert context["limits"] == task.config.settings.limits()
            assert context["runtime"]["host_research_deadline_seconds"] == 45
            assert context["runtime"]["fixture_sha256"] == digest(recording.read_bytes())
            assert context["runtime"]["instructions_sha256"] == digest(task.instructions)
            assert context["runtime"]["search"]["provider"] == FixtureProvider.name
            search = next(
                receipt["payload"] for receipt in context["receipts"] if receipt["kind"] == "search"
            )
            assert (
                search["provider_response"]["results"]
                == json.loads(recording.read_text())["search_results"]
            )
            assert "host-only-marker" not in json.dumps(context)
            assert server.research_service.backend.settings.local_root == task.output / "backend"
            with server.research_service.backend.open_registry() as store:
                assert store.scalar("SELECT COUNT(*) FROM research_jobs") == 0

    anyio.run(scenario)


def test_fixture_transport_runs_through_actual_stdio_protocol(tmp_path, recording, source):
    mcp = pytest.importorskip("mcp")
    stdio = pytest.importorskip("mcp.client.stdio")
    task = task_for(tmp_path, recording, "omnigent")
    case = prepare_case(
        task.output,
        brief=task.brief,
        settings=task.config.settings,
        instructions=task.instructions,
        max_spend_usd=0.25,
    )

    async def scenario():
        params = mcp.StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "research_harness.evaluation.fixture_transport",
                "--case",
                str(case),
                "--fixture",
                str(recording),
            ],
            env={
                PRIMARY_SESSION_ENV: "conv_stdio",
                "RUNNER_SERVER_URL": "http://127.0.0.1:12345",
                "RH_DATABASE_URL": "not-a-backend",
            },
        )
        async with stdio.stdio_client(params) as streams, mcp.ClientSession(*streams) as client:
            await client.initialize()
            begun = await client.call_tool("begin_research", {"brief": task.brief})
            assert begun.structuredContent["status"] == "ok"
            observed = await client.call_tool(
                "inspect_source", {"url": source.url, "operation_id": "i1"}
            )
            assert observed.structuredContent["status"] == "ok"
            context = await client.call_tool("get_research_context", {})
            assert context.structuredContent["data"]["observed_urls"] == [source.url]

    anyio.run(scenario)


def test_direct_executor_actual_sdk_requests_produce_fixture_proposal(
    tmp_path, recording, source, monkeypatch
):
    task = task_for(tmp_path, recording)
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-provider-key-must-not-be-used")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unused-provider.example")
    monkeypatch.setenv("RH_DATABASE_URL", "must-not-select-shared-storage")

    def respond(payload, number):
        assert payload["model"] == task.config.model
        assert payload["max_output_tokens"] == 777
        assert payload["reasoning"] == {"effort": "none"}
        assert "parallel_tool_calls" not in payload
        assert all(tool["type"] != "web_search" for tool in payload["tools"])
        assert payload["instructions"].startswith(task.instructions)
        assert payload["input"][0] == {"role": "user", "content": task.brief}
        assert "host-only-marker" not in json.dumps(payload)
        if number == 1:
            output = [call("search_sources", {"query": "policy", "filters": None}, number)]
        elif number == 2:
            output = [call("probe_source", {"source_json": source.model_dump_json()}, number)]
        else:
            proof = json.loads(payload["input"][-1]["output"])
            answer = proposal(source, proof["probe_id"])
            output = [
                {
                    "id": "msg",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": answer.model_dump_json(), "annotations": []}
                    ],
                }
            ]
        return 200, response(output, number)

    with model_server(respond) as (base_url, requests):
        result = executor(base_url)(task)
    assert len(requests) == 3
    assert all(
        request["authorization"] == "Bearer local-gateway-fixture-key" for request in requests
    )
    assert all(request["path"] == "/v1/responses" for request in requests)
    assert result.research == task.output / "research"
    assert result.runtime_usage is None
    assert json.loads((result.research / "proposal.json").read_text())["status"] == "proposed"
    assert result.metadata["fixture_sha256"] == digest(recording.read_bytes())
    assert result.metadata["instructions_sha256"] == digest(task.instructions)
    assert (task.output / "source-fixtures.json").read_bytes() == recording.read_bytes()
    assert task.output / "execution.json" in result.attachments
    assert result.metadata["status"] == "completed"
    assert "local-gateway-fixture-key" not in (task.output / "execution.json").read_text()
    with Backend.local(task.output / "backend").open_registry() as store:
        assert store.scalar("SELECT COUNT(*) FROM proposals") == 1


def test_direct_failure_is_not_retried_and_retains_partial_artifacts(tmp_path, recording):
    task = task_for(tmp_path, recording)

    def respond(payload, number):
        return 500, {"error": {"message": "authored gateway failure", "type": "server_error"}}

    with model_server(respond) as (base_url, requests):
        run = executor(base_url)
        with pytest.raises(RuntimeExecutionError, match="authored gateway failure") as raised:
            run(task)
        assert len(requests) == 1
        with pytest.raises(ValueError, match="already started"):
            run(task)
        assert len(requests) == 1
    partial = raised.value.artifacts
    assert partial.research == task.output / "research"
    assert partial.metadata["status"] == "failed"
    assert (partial.research / "failure.json").exists()
    assert task.output / "execution.json" in partial.attachments
    assert not (partial.research / "proposal.json").exists()


@pytest.mark.parametrize("failure", [None, "start", "send", "export", "send_and_export", "close"])
def test_omnigent_executor_sends_once_exports_failures_and_always_closes(
    tmp_path, recording, monkeypatch, failure
):
    task = task_for(tmp_path, recording, "omnigent")
    observed = {}
    for key in [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DATABRICKS_TOKEN",
        "CUSTOM_SECRET",
        "RH_DATABASE_URL",
        "RH_BLOB_SECRET_KEY",
    ]:
        monkeypatch.setenv(key, "ambient-secret-must-not-reach-runner")
    monkeypatch.setenv(PRIMARY_SESSION_ENV, "previous-session")
    monkeypatch.setenv("HTTPS_PROXY", "http://unused-proxy.example")

    class RuntimeDouble:
        def __init__(self, case, *, python, env):
            observed.update(case=case, env=env, sends=[], exports=0, closes=0)
            self.case = json.loads(case.read_text())
            bind_case(case, session_id="conv_executor", server_url="http://127.0.0.1:12345")
            self.output = case.parent / "omnigent"
            self.output.mkdir()
            (self.output / "runner-token").write_text("must-not-be-an-attachment")
            (self.output / "sessions.db").write_text("must-not-be-an-attachment")

        def start(self):
            if failure == "start":
                raise RuntimeError("original start failure")

        def send(self, prompt, *, phase, timeout):
            observed["sends"].append((prompt, phase, timeout))
            if failure in {"send", "send_and_export"}:
                raise RuntimeError("original send failure")
            write_json(
                Path(self.case["research_output"]) / "proposal.json",
                {"fixture": "lifecycle double"},
            )
            return {"status": "idle"}

        def export_trace(self):
            observed["exports"] += 1
            write_json(
                self.output / "runtime-usage.discovery.json", {"fixture": "lifecycle double"}
            )
            write_json(self.output / "trace-manifest.json", {"fixture": True})
            if failure in {"export", "send_and_export"}:
                raise RuntimeError("trace export failure")

        def close(self):
            observed["closes"] += 1
            if failure == "close":
                raise RuntimeError("close failure")

    monkeypatch.setattr(executor_module, "LocalOmnigent", RuntimeDouble)
    run = executor()
    if failure:
        message = "original send failure" if failure == "send_and_export" else failure
        with pytest.raises(RuntimeExecutionError, match=message) as raised:
            run(task)
        result = raised.value.artifacts
        assert result.metadata["status"] == "failed"
    else:
        result = run(task)
        assert result.metadata["status"] == "completed"
    assert observed["sends"] == ([] if failure == "start" else [(task.brief, "discovery", 45)])
    assert observed["exports"] == observed["closes"] == 1
    assert observed["env"]["OPENAI_API_KEY"] == "local-gateway-fixture-key"
    assert observed["env"]["OPENAI_BASE_URL"] == run.base_url
    assert "ambient-secret-must-not-reach-runner" not in observed["env"].values()
    assert not any(key.startswith("RH_") for key in observed["env"])
    assert PRIMARY_SESSION_ENV not in observed["env"] and "HTTPS_PROXY" not in observed["env"]
    assert os.environ["OPENAI_API_KEY"] == "ambient-secret-must-not-reach-runner"
    case = observed["case"]
    assert (case.parent / "agent/instructions.md").read_text() == task.instructions
    assert (
        json.loads((case.parent / "agent/research-settings.json").read_text())
        == task.config.settings.model_dump()
    )
    launch = json.loads((case.parent / "agent/tools/mcp/research.yaml").read_text())
    assert launch["args"] == [
        "-m",
        "research_harness.evaluation.fixture_transport",
        "--case",
        str(case),
        "--fixture",
        str(task.output / "source-fixtures.json"),
    ]
    assert launch["env"] == {"RH_LOCAL_ROOT": str(case.parent / "backend")}
    assert result.research == case.parent / "research"
    assert result.runtime_usage == case.parent / "omnigent/runtime-usage.discovery.json"
    assert case.parent / "agent" in result.attachments
    assert not any(path.name in {"runner-token", "sessions.db"} for path in result.attachments)
    if failure == "send_and_export":
        assert result.metadata["cleanup_errors"] == [
            {"operation": "export_trace", "error": "RuntimeError: trace export failure"}
        ]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com/v1",
        "http://localhost/v1",
        "http://secret@127.0.0.1:1234/v1",
        "http://127.0.0.1:1234/v1?token=secret",
    ],
)
def test_executor_refuses_to_bypass_local_gateway(base_url):
    with pytest.raises(ValueError, match="loopback HTTP gateway"):
        executor(base_url)
