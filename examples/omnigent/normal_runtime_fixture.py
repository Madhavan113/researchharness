"""Save a pipeline through the normal Omnigent server/runner with offline model/source fixtures."""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import os
import platform
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from research_harness.config import SourceSpec
from research_harness.discovery_models import Candidate, DataNeed, ProposalDraft
from research_harness.execution import DiscoverySettings
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.integrations.omnigent import (
    LocalOmnigent,
    prepare_case,
    refresh_unbound_bundle,
)
from research_harness.util import digest, timestamp, utcnow, write_json

BRIEF = "Find a public feed of official policy changes with stable ids and publication timestamps."
SOURCE = SourceSpec(
    id="policy",
    name="Fixture policy feed",
    connector="json",
    url="https://fixture.example/policy.json",
    items_pointer="/items",
    id_pointer="/id",
    published_pointer="/published_at",
    required_pointers=["/title"],
)


def draft(probe_id, source):
    return ProposalDraft(
        name="fixture-policy",
        title="Policy feed",
        research_question=BRIEF,
        needs=[DataNeed(id="policy", description="Official policy changes", required=True)],
        candidates=[
            Candidate(
                name="Policy feed",
                purpose="Monitor official changes",
                covers=["policy"],
                status="ready",
                evidence_urls=[source.url],
                source=source,
                probe_id=probe_id,
                freshness_assessment="Current sample",
                historical_coverage="No historical claim",
                access_notes="Offline fixture",
                limitations=["Fixture does not measure live source quality"],
            )
        ],
        open_questions=[],
    ).model_dump(mode="json")


class ModelFixture:
    def __init__(self, source):
        self.source = source
        self.requests = []
        self.responses = []
        self.auxiliary_requests = []
        self.errors = []
        self.probe_id = None
        self.saved = None
        self.mode = "discovery"
        self.stage = 0
        self.jobs = {}
        self.collection_id = None
        self.export_id = None
        self.artifacts = {}
        self.reopened_jobs = []
        self.control_path = None

    def diagnostics(self):
        """Bounded state from this authored fixture, without request/response content."""
        last = self.responses[-1].get("output", []) if self.responses else []
        return {
            "phase": self.mode,
            "stage": self.stage,
            "model_requests": len(self.requests),
            "model_responses": len(self.responses),
            "auxiliary_requests": len(self.auxiliary_requests),
            "model_error_count": len(self.errors),
            "last_model_errors": [str(error)[:1000] for error in self.errors[-5:]],
            "last_response_outputs": [
                {key: item.get(key) for key in ("type", "name", "status")} for item in last[-5:]
            ],
            "jobs": {
                job_id: {
                    **{
                        key: job.get(key)
                        for key in ("kind", "status", "worker_active", "recovery_pending")
                    },
                    "error": str(job["error"])[:1000] if job.get("error") else None,
                }
                for job_id, job in list(self.jobs.items())[-5:]
            },
            "job_count": len(self.jobs),
            "export_artifacts_observed": sorted(self.artifacts),
            "interpretation": "Last observed fixture state; not proof a pending job stopped",
        }

    def job_call(self):
        if self.mode == "context":
            self.stage += 1
            return ("get_research_context", {}) if self.stage == 1 else None
        if self.mode == "reopen":
            calls = [
                ("get_research_context", {}),
                ("list_jobs", {}),
                ("read_export", {"job_id": self.export_id, "artifact": "observations"}),
            ]
            index = self.stage
            self.stage += 1
            return calls[index] if index < len(calls) else None
        if self.stage == 0:
            self.stage = 1
            return "list_pipelines", {}
        if self.stage == 1:
            self.stage = 2
            return "start_collection", {
                "pipeline_version_id": self.saved["pipeline_version_id"],
                "operation_id": "collect-1",
            }
        if self.stage == 2:
            assert self.collection_id, "No durable collection job returned"
            job = self.jobs[self.collection_id]
            if job["status"] != "succeeded":
                assert job["status"] in {"queued", "running"}, job
                time.sleep(0.1)
                return "get_job", {"job_id": self.collection_id}
            self.stage = 3
        if self.stage == 3:
            self.stage = 4
            return "export_observations", {
                "pipeline_version_id": self.saved["pipeline_version_id"],
                "operation_id": "export-1",
                "as_of": timestamp(utcnow()),
            }
        if self.stage == 4:
            assert self.export_id, "No durable export job returned"
            job = self.jobs[self.export_id]
            if job["status"] != "succeeded":
                assert job["status"] in {"queued", "running"}, job
                time.sleep(0.1)
                return "get_job", {"job_id": self.export_id}
            self.stage = 5
        if self.stage in {5, 6}:
            artifact = "observations" if self.stage == 5 else "manifest"
            self.stage += 1
            return "read_export", {"job_id": self.export_id, "artifact": artifact}
        return None

    def respond(self, payload):
        if self.control_path and self.control_path.exists():
            mode = json.loads(self.control_path.read_text())["mode"]
            assert mode in {"discovery", "collection", "reopen"}
            if mode != self.mode:
                self.mode, self.stage = mode, 0
        self.requests.append(payload)
        assert payload["model"] == "gpt-5.4-mini"
        assert "research__submit_proposal" in json.dumps(payload["tools"])
        outputs = [item for item in payload["input"] if item.get("type") == "function_call_output"]
        for item in outputs:
            raw = item["output"]
            if not isinstance(raw, str):
                continue
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                result = ast.literal_eval(raw)
            data = result.get("data") or {}
            if data.get("probe_id"):
                self.probe_id = data["probe_id"]
            if data.get("registry"):
                self.saved = data["registry"]
            if data.get("kind") in {"collection", "export"} and data.get("id"):
                self.jobs[data["id"]] = data
                if data["kind"] == "collection":
                    self.collection_id = data["id"]
                else:
                    self.export_id = data["id"]
            if data.get("artifact") in {"observations", "manifest"}:
                self.artifacts[data["artifact"]] = data
            if data.get("jobs"):
                self.reopened_jobs = data["jobs"]
        index = len(self.responses)
        calls = [
            ("get_research_context", {}),
            ("begin_research", {"brief": BRIEF}),
            ("search_sources", {"query": "official policy feed", "operation_id": "search-1"}),
            ("inspect_source", {"url": self.source.url, "operation_id": "inspect-1"}),
            (
                "probe_source",
                {"source": self.source.model_dump(mode="json"), "operation_id": "probe-1"},
            ),
        ]
        if index < len(calls):
            name, arguments = calls[index]
        elif index == 5:
            assert self.probe_id, "The normal runner did not return a probe receipt"
            name, arguments = (
                "submit_proposal",
                {"draft": draft(self.probe_id, self.source), "operation_id": "submit-1"},
            )
        else:
            call = self.job_call() if self.mode != "discovery" else None
            if call is not None:
                name, arguments = call
                return self.tool_stream(payload, index, name, arguments)
            assert self.saved, "The normal runner did not return saved domain ids"
            text = "Saved verified pipeline " + json.dumps(self.saved, sort_keys=True)
            output = {
                "type": "message",
                "id": f"msg_{index}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
            return self.stream(output, index)
        return self.tool_stream(payload, index, name, arguments)

    def tool_stream(self, payload, index, name, arguments):
        full_name = next(
            tool["name"]
            for tool in payload["tools"]
            if tool.get("name", "").endswith("research__" + name)
        )
        output = {
            "type": "function_call",
            "id": f"fc_{index}",
            "call_id": f"call_{index}",
            "name": full_name,
            "arguments": json.dumps(arguments),
            "status": "completed",
        }
        return self.stream(output, index)

    def stream(self, output, index):
        response = {
            "id": f"resp_{index}",
            "object": "response",
            "created_at": 1788825600,
            "status": "completed",
            "model": "gpt-5.4-mini",
            "output": [output],
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 10,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 110,
            },
        }
        self.responses.append(response)
        events = [
            {
                "type": "response.created",
                "response": {**response, "status": "in_progress", "output": []},
            },
            {"type": "response.output_item.added", "output_index": 0, "item": output},
        ]
        if output["type"] == "message":
            events.append(
                {
                    "type": "response.output_text.delta",
                    "item_id": output["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "delta": output["content"][0]["text"],
                }
            )
        events += [
            {"type": "response.output_item.done", "output_index": 0, "item": output},
            {"type": "response.completed", "response": response},
        ]
        return "".join(
            f"event: {event['type']}\ndata: {json.dumps({**event, 'sequence_number': i})}\n\n"
            for i, event in enumerate(events)
        ).encode()


def interactive_loop(case, python, env, model, out):
    """Expose the same fixture via the actual UI until host sentinel files arrive."""
    model.control_path = out / "control.json"
    write_json(model.control_path, {"mode": "discovery"})
    runtime = None
    generation = 0
    try:
        while not (out / "stop").exists():
            if runtime is None:
                runtime = LocalOmnigent(case, python=python, env=env)
                runtime.start()
                write_json(
                    out / "runtime-ready.json",
                    {
                        "url": runtime.base_url,
                        "session_id": runtime.session_id,
                        "case": str(case),
                        "generation": generation,
                    },
                )
            if (out / "export").exists():
                runtime.export_trace()
                (out / "export").unlink()
            if (out / "restart").exists():
                runtime.export_trace()
                runtime.close()
                runtime = None
                generation += 1
                (out / "restart").unlink()
            time.sleep(0.1)
    finally:
        if runtime is not None:
            runtime.export_trace()
            runtime.close()
        write_json(out / "jobs.json", model.jobs)
        write_json(out / "export-artifacts.json", model.artifacts)


def run(
    out: Path,
    python: Path,
    *,
    interactive: bool = False,
    budget_check: bool = False,
    shared_settings: bool = False,
    settings: DiscoverySettings | None = None,
    controlled_gateway: bool = False,
    gateway_limit_check: bool = False,
):
    if interactive and budget_check:
        raise ValueError("Choose interactive or budget-check mode")
    model = ModelFixture(SOURCE)
    source_requests = []
    items = [
        {"id": "policy-1", "title": "Fixture export policy", "published_at": "2026-09-01T00:00:00Z"}
    ]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != "/policy.json":
                self.send_error(404)
                return
            source_requests.append({"path": self.path, "at": timestamp(utcnow())})
            body = json.dumps({"items": items}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                if self.path == "/v1/responses/compact":
                    payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    model.auxiliary_requests.append(
                        {"path": self.path, "request": payload, "status": 404}
                    )
                    self.send_error(404, "Offline fixture does not implement provider compaction")
                    return
                assert self.path == "/v1/responses", f"Unexpected model operation {self.path}"
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body = model.respond(payload)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                model.errors.append(str(exc))
                self.send_error(500, str(exc))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model.source = SOURCE.model_copy(
        update={"url": f"http://127.0.0.1:{server.server_port}/policy.json"}
    )
    configured_cap = 0.000000001 if budget_check else 1
    controls = (
        settings
        if settings is not None
        else DiscoverySettings()
        if shared_settings or controlled_gateway or gateway_limit_check
        else None
    )
    if gateway_limit_check:
        controls = DiscoverySettings(max_rounds=2)
        controlled_gateway = True
    case = prepare_case(out, brief=BRIEF, max_spend_usd=configured_cap, settings=controls)
    repo = Path(__file__).resolve().parents[2]
    source_files = [
        "src/research_harness/integrations/omnigent.py",
        "src/research_harness/integrations/model_gateway.py",
        "src/research_harness/execution.py",
        "examples/omnigent/normal_runtime_fixture.py",
        "examples/omnigent/fixture_research_mcp.py",
        "agents/research/config.yaml",
        "agents/research/instructions.md",
    ]
    write_json(
        out / "metadata.json",
        {
            "recorded_at": timestamp(utcnow()),
            "command": [sys.executable, *sys.argv],
            "python": platform.python_version(),
            "research_dependencies": {
                name: importlib.metadata.version(name) for name in ["httpx", "mcp"]
            },
            "source_sha256": {name: digest((repo / name).read_bytes()) for name in source_files},
            "synthetic_model": True,
            "source_transport": "local HTTP",
            "budget_check": budget_check,
            "interactive": interactive,
            "controlled_gateway": controlled_gateway,
            "gateway_limit_check": gateway_limit_check,
            "discovery_settings": controls.model_dump(mode="json")
            if controls is not None
            else None,
        },
    )
    fixture_path = out / "fixture.json"
    write_json(
        fixture_path,
        {
            "source": model.source.model_dump(mode="json"),
            "items": items,
        },
    )
    mcp_path = out / "agent" / "tools" / "mcp" / "research.yaml"
    tool = json.loads(mcp_path.read_text())
    tool["args"] = [
        str(Path(__file__).with_name("fixture_research_mcp.py").resolve()),
        "--case",
        str(case),
        "--fixture",
        str(fixture_path),
    ]
    write_json(mcp_path, tool)
    refresh_unbound_bundle(case)
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("RH_")},
        "OPENAI_API_KEY": "offline-model-fixture",
        "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    for key in ["DATABRICKS_CONFIG_PROFILE", "DATABRICKS_TOKEN", "ANTHROPIC_API_KEY"]:
        env.pop(key, None)
    proxy = None
    if controlled_gateway:
        proxy = ResponsesGateway(
            out / "gateway",
            model="gpt-5.4-mini",
            settings=controls,
            upstream_base_url=env["OPENAI_BASE_URL"],
            upstream_api_key="offline-model-fixture",
            allowed_function_names={
                "research__" + name
                for name in [
                    "begin_research",
                    "get_research_context",
                    "search_sources",
                    "inspect_source",
                    "probe_source",
                    "get_evidence",
                    "submit_proposal",
                    "list_pipelines",
                ]
            },
        ).start()
        env.update(OPENAI_BASE_URL=proxy.base_url, OPENAI_API_KEY=proxy.api_key)
    try:
        if interactive:
            interactive_loop(case, python, env, model, out)
            return
        with LocalOmnigent(case, python=python, env=env) as runtime:
            snapshot = runtime.send(BRIEF, timeout=90, phase="discovery")
            if gateway_limit_check:
                report = proxy.report()
                assert report["upstream_requests"] == len(model.requests) == 2
                assert not model.saved
                assert "model_rounds_exhausted" in json.dumps(snapshot.get("last_task_error"))
                assert any(
                    denial["reason"] == "model_rounds_exhausted" for denial in report["denials"]
                )
                write_json(
                    out / "acceptance.json",
                    {
                        "status": "passed",
                        "normal_server_runner": True,
                        "controlled_gateway": True,
                        "model_and_source_fixtures": True,
                        "live_provider_verified": False,
                        "requested_rounds": 2,
                        "upstream_requests": 2,
                        "proposal_saved": False,
                        "excess_model_request_denied": True,
                    },
                )
                return
            assert snapshot["status"] == "idle", snapshot.get("last_task_error")
            assert model.saved and not model.errors, model.errors
            proposal = json.loads((out / "research" / "proposal.json").read_text())
            assert proposal["registry"] == model.saved
            first_session = runtime.session_id
            first_context = json.loads((out / "research" / "context.json").read_text())
            root_page = runtime.client.get("/")
            ui_available = root_page.status_code == 200 and "<html" in root_page.text.lower()
            if controlled_gateway:
                model.mode, model.stage = "context", 0
                followup = runtime.send(
                    "Reopen the same saved context.", timeout=90, phase="followup"
                )
                assert followup["status"] == "idle"
                report = proxy.report()
                assert report["complete"] and report["upstream_requests"] == len(model.requests)
                assert all(
                    all(
                        request.get(key) == value
                        for key, value in controls.model_settings().items()
                    )
                    for request in model.requests
                )
                assert all("parallel_tool_calls" not in request for request in model.requests)
                assert all(
                    tool["name"].startswith("research__")
                    for request in model.requests
                    for tool in request["tools"]
                )
                write_json(
                    out / "acceptance.json",
                    {
                        "status": "passed",
                        "normal_server_runner": True,
                        "controlled_gateway": True,
                        "model_and_source_fixtures": True,
                        "live_provider_verified": False,
                        "saved": model.saved,
                        "upstream_requests": report["upstream_requests"],
                        "generation_controls_verified": True,
                        "builtin_function_schemas_removed": True,
                        "followup_succeeded": True,
                        "auxiliary_rejected_without_forwarding": any(
                            denial["reason"] == "unsupported_auxiliary_endpoint"
                            for denial in report["denials"]
                        ),
                    },
                )
                return
            if budget_check:
                observed_cost = snapshot["usage_by_model"]["gpt-5.4-mini"]["total_cost_usd"]
                assert observed_cost > configured_cap
                requests_before = len(model.requests)
                denied = runtime.send(
                    "Continue with another model turn.", timeout=20, phase="followup"
                )
                assert denied["status"] == "idle"
                assert runtime.last_submission["denied"] is True
                assert "budget" in runtime.last_submission["reason"].lower()
                assert len(model.requests) == requests_before
                write_json(out / "budget-denial.json", runtime.last_submission)
                write_json(
                    out / "acceptance.json",
                    {
                        "status": "passed",
                        "normal_server_runner": True,
                        "model_and_source_fixtures": True,
                        "live_provider_verified": False,
                        "configured_cap_usd": configured_cap,
                        "first_turn_catalog_cost_usd": observed_cost,
                        "first_turn_exceeded_cap": True,
                        "next_request_denied": True,
                        "provider_requests_before_denial": requests_before,
                        "provider_requests_after_denial": len(model.requests),
                        "all_models_blocked": True,
                        "auxiliary_usage_unknown": True,
                    },
                )
                return
            model.mode = "collection"
            collected = runtime.send(
                "Collect the saved policy pipeline now, export the published observations, and read the export with its provenance.",
                timeout=90,
                phase="followup",
            )
            assert collected["status"] == "idle", collected.get("last_task_error")
            assert not model.errors, model.errors
            assert model.jobs[model.collection_id]["status"] == "succeeded"
            assert model.jobs[model.export_id]["status"] == "succeeded"
            assert "Fixture export policy" in model.artifacts["observations"]["content"]
            assert model.artifacts["manifest"]["content"]
        with LocalOmnigent(case, python=python, env=env) as runtime:
            assert runtime.session_id == first_session
            model.mode = "reopen"
            model.stage = 0
            recovered = runtime.send(
                "Reopen the saved research case and report its pipeline id.",
                timeout=90,
                phase="followup",
            )
            assert recovered["status"] == "idle"
            assert json.loads((out / "research" / "context.json").read_text()) == first_context
            assert {job["id"] for job in model.reopened_jobs} == {
                model.collection_id,
                model.export_id,
            }
            assert all(job["status"] == "succeeded" for job in model.reopened_jobs)
            discovery_usage = json.loads(
                (out / "omnigent/runtime-usage.discovery.json").read_text()
            )
            followup_usage = json.loads((out / "omnigent/runtime-usage.followup.json").read_text())
            assert discovery_usage["discovery_id"] == first_context["discovery_id"]
            assert discovery_usage["totals"]["total_tokens"] == 770
            assert followup_usage["totals"]["total_tokens"] == (len(model.responses) - 7) * 110
            assert set(discovery_usage["responses"]).isdisjoint(followup_usage["responses"])
            assert not discovery_usage["complete"] and discovery_usage["auxiliary_usage_unknown"]
        write_json(
            out / "acceptance.json",
            {
                "status": "passed",
                "normal_server_runner": True,
                "model_and_source_fixtures": True,
                "live_provider_verified": False,
                "ui_page_available": ui_available,
                "browser_walkthrough_verified": False,
                "saved": model.saved,
                "session_id": first_session,
                "server_runner_restart_preserved_case": True,
                "model_requests": len(model.requests),
                "usage_scopes_verified": True,
                "provider_usage_complete": False,
                "collection_job_id": model.collection_id,
                "export_job_id": model.export_id,
                "collection_and_export_reopened": True,
                "source_http_requests": len(source_requests),
            },
        )
        write_json(out / "jobs.json", model.jobs)
        write_json(out / "export-artifacts.json", model.artifacts)
    finally:
        # Preserve the fixture's progress even when a runtime turn remains unresolved.
        # Full authored traffic/errors remain in their separate original captures.
        write_json(out / "fixture-state.json", model.diagnostics())
        if proxy is not None:
            proxy.close()
        write_json(out / "model-requests.json", model.requests)
        write_json(out / "model-responses.json", model.responses)
        write_json(out / "model-errors.json", model.errors)
        write_json(out / "model-auxiliary-requests.json", model.auxiliary_requests)
        write_json(out / "source-requests.json", source_requests)
        if controls is not None:
            expected = controls.model_settings()
            observed = [{key: payload.get(key) for key in expected} for payload in model.requests]
            write_json(
                out / "generation-controls.json",
                {
                    "requested": expected,
                    "observed_requests": observed,
                    "all_requests_match": bool(observed)
                    and all(value == expected for value in observed),
                },
            )
        server.shutdown()
        server.server_close()
    print(json.dumps({"status": "passed", "artifacts": str(out), "ids": model.saved}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--omnigent-python", type=Path, required=True)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--budget-check", action="store_true")
    parser.add_argument("--shared-settings", action="store_true")
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--controlled-gateway", action="store_true")
    parser.add_argument("--gateway-limit-check", action="store_true")
    args = parser.parse_args()
    run(
        args.out.expanduser().resolve(),
        args.omnigent_python,
        interactive=args.interactive,
        budget_check=args.budget_check,
        shared_settings=args.shared_settings,
        settings=DiscoverySettings.model_validate_json(args.settings.read_text())
        if args.settings
        else None,
        controlled_gateway=args.controlled_gateway,
        gateway_limit_check=args.gateway_limit_check,
    )
