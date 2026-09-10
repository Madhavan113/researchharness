"""Exercise real Omnigent/Agents/MCP code with model responses at an HTTP fixture.

Run with the pinned Omnigent checkout's Python, not Research Harness's environment.
No model request can leave the injected httpx.MockTransport.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import omnigent
import yaml
from omnigent.inner.executor import ExecutorConfig
from omnigent.inner.openai_agents_sdk_executor import OpenAIAgentsSDKExecutor
from omnigent.runner.mcp_manager import RunnerMcpManager
from omnigent.spec.parser import parse
from openai import AsyncOpenAI

PIN = "be042b390e293a8d586cbb7e403a2ce0ce38fc62"
MODEL = "gpt-5.4-mini"
MARKER = "RESEARCH-HARNESS-M0-INSTRUCTIONS-v1"
TOOL = "research_fixture__fixture_probe"
ARGUMENTS = {
    "operation_id": "fixture-operation-001",
    "source": {
        "url": "https://fixture.invalid/policy?limit=2&country=US",
        "connector": "json",
        "labels": ["trade policy", "美国", 'quotes: "preserved"'],
        "options": {"items_path": "data.items", "literal": "${not-expanded}"},
    },
}


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def encode_event(event: dict) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()


def response_body(index: int, output: list[dict], *, status: str = "completed") -> dict:
    return {
        "id": f"resp_fixture_{index}",
        "object": "response",
        "created_at": 1788825600,
        "status": status,
        "model": MODEL,
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens": 10,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 110,
        },
    }


def streamed_response(index: int, output: list[dict]) -> bytes:
    events = [
        {"type": "response.created", "response": response_body(index, [], status="in_progress")},
        {"type": "response.output_item.added", "output_index": 0, "item": output[0]},
    ]
    if output[0]["type"] == "message":
        events.append(
            {
                "type": "response.output_text.delta",
                "item_id": output[0]["id"],
                "output_index": 0,
                "content_index": 0,
                "delta": output[0]["content"][0]["text"],
            }
        )
    events.extend(
        [
            {"type": "response.output_item.done", "output_index": 0, "item": output[0]},
            {"type": "response.completed", "response": response_body(index, output)},
        ]
    )
    return b"".join(encode_event(dict(event, sequence_number=i)) for i, event in enumerate(events))


def message(text: str, index: int) -> dict:
    return {
        "type": "message",
        "id": f"msg_fixture_{index}",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


class WaitingStream(httpx.AsyncByteStream):
    """A stream that stays live until the real SDK cancels and closes it."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def __aiter__(self):
        yield encode_event(
            {
                "type": "response.created",
                "sequence_number": 0,
                "response": response_body(99, [], status="in_progress"),
            }
        )
        self.started.set()
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed.set()


class FixtureTransport:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: list[dict] = []
        self.cancel_stream: WaitingStream | None = None
        self.receipt: dict | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "fixture.invalid", "Unexpected provider destination"
        assert request.url.path == "/v1/responses", "Unexpected provider operation"
        payload = json.loads(request.content)
        self.requests.append(payload)
        assert payload["model"] == MODEL
        assert MARKER in payload["instructions"]
        assert payload["stream"] is True
        assert any(tool["name"] == TOOL for tool in payload["tools"])
        index = len(self.requests)
        if self.cancel_stream is not None:
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=self.cancel_stream
            )
        if index == 1:
            output = [
                {
                    "type": "function_call",
                    "id": "fc_fixture_1",
                    "call_id": "call_fixture_1",
                    "name": TOOL,
                    "arguments": json.dumps(ARGUMENTS),
                    "status": "completed",
                }
            ]
        elif index == 2:
            tool_outputs = [
                item for item in payload["input"] if item.get("type") == "function_call_output"
            ]
            assert len(tool_outputs) == 1, "SDK must send the real MCP result to its second request"
            assert json.loads(tool_outputs[0]["output"]) == self.receipt
            output = [message("Saved fixture-evidence-001", index)]
        else:
            assert "INSTRUCTIONS-v2" in payload["instructions"], "Updated instructions were lost"
            assert "Saved fixture-evidence-001" in json.dumps(payload["input"]), (
                "History was not restored"
            )
            output = [message("Reopened fixture-evidence-001", index)]
        self.responses.append(response_body(index, output))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=streamed_response(index, output),
        )


async def collect(executor, messages, schemas, instructions):
    events = []
    config = ExecutorConfig(
        model=MODEL, extra={"parallel_tool_calls": False, "max_turns": 4, "max_tokens": 512}
    )
    async for event in executor.run_turn(messages, schemas, instructions, config):
        events.append({"type": type(event).__name__, **dataclasses.asdict(event)})
    return events


def prepare_bundle(out: Path) -> Path:
    template = Path(__file__).with_name("compatibility-agent")
    bundle = out / "agent"
    shutil.copytree(template, bundle)
    tool_dir = bundle / "tools" / "mcp"
    tool_dir.mkdir(parents=True)
    config = {
        "name": "research_fixture",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(Path(__file__).with_name("fixture_server.py").resolve())],
        "env": {"RESEARCH_FIXTURE_CAPTURE": str(out / "mcp-calls.jsonl")},
        "timeout": 15,
    }
    (tool_dir / "research_fixture.yaml").write_text(yaml.safe_dump(config))
    return bundle


async def run(out: Path) -> None:
    checkout = Path(omnigent.__file__).resolve().parent.parent
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    assert commit == PIN, f"Use the tested Omnigent commit {PIN}, got {commit}"
    metadata = {
        "evidence_type": "real-runtime-offline-model-fixture",
        "omnigent_commit": commit,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "model": MODEL,
        "adapter": "openai-agents",
        "paid_model_calls": 0,
        "ui_verified": False,
        "upstream_lock_sha256": hashlib.sha256((checkout / "uv.lock").read_bytes()).hexdigest(),
        "source_sha256": {
            str(path.relative_to(Path(__file__).parent)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in [
                Path(__file__),
                Path(__file__).with_name("fixture_server.py"),
                Path(__file__).with_name("compatibility-agent") / "config.yaml",
                Path(__file__).with_name("compatibility-agent") / "instructions.md",
            ]
        },
        "packages": {
            name: importlib.metadata.version(name)
            for name in (
                "omnigent",
                "omnigent-client",
                "omnigent-ui-sdk",
                "openai",
                "openai-agents",
                "mcp",
                "httpx",
            )
        },
    }
    write_json(out / "metadata.json", metadata)
    spec = parse(prepare_bundle(out))
    assert spec.executor.config["harness"] == "openai-agents"
    assert spec.executor.model == MODEL and MARKER in spec.instructions
    instructions = spec.instructions
    manager = RunnerMcpManager()
    transport = FixtureTransport()
    client = AsyncOpenAI(
        api_key="offline-fixture-no-real-key",
        base_url="https://fixture.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    executor = OpenAIAgentsSDKExecutor(client=client, model=MODEL)
    all_events = {}
    checks = {}
    try:
        discovered = await manager.schemas_for(spec)
        assert not discovered.failures, discovered.failures
        assert TOOL in discovered.tool_names
        write_json(out / "tool-schemas.json", discovered.schemas)
        raw_result = await manager.call_tool(spec, TOOL, ARGUMENTS, session_id="fixture-session")
        receipt = json.loads(raw_result)
        assert receipt["data"]["source"] == ARGUMENTS["source"]
        transport.receipt = receipt
        write_json(out / "receipt.json", receipt)
        structured_only = await manager.call_tool(spec, "research_fixture__structured_only", {})
        assert structured_only == "(empty response)"
        checks["mcp_json_text_preserved"] = True
        checks["mcp_structured_content_only_lost"] = True
        invalid_result = await manager.call_tool(
            spec, TOOL, {"operation_id": "invalid", "source": {}}
        )
        assert invalid_result.startswith("Error: "), invalid_result
        write_json(out / "validation-error.json", {"result": invalid_result})
        checks["mcp_schema_validation_error_preserved"] = True

        async def dispatch(name, arguments):
            assert name == TOOL and arguments == ARGUMENTS
            return await manager.call_tool(spec, name, arguments, session_id="fixture-session")

        executor._tool_executor = dispatch
        messages = [
            {
                "role": "user",
                "content": "Probe this source: " + json.dumps(ARGUMENTS),
                "session_id": "fixture-session",
            }
        ]
        events = await collect(executor, messages, discovered.schemas, instructions)
        all_events["first_turn"] = events
        assert any(e["type"] == "ToolCallRequest" and e["args"] == ARGUMENTS for e in events), (
            events
        )
        assert any(
            e["type"] == "ToolCallComplete" and json.loads(e["result"]) == receipt for e in events
        ), events
        terminal = next(e for e in events if e["type"] == "TurnComplete")
        assert terminal["response"] == "Saved fixture-evidence-001"
        assert terminal["usage"] == {
            "input_tokens": 200,
            "output_tokens": 20,
            "total_tokens": 220,
            "context_tokens": 110,
            "model": MODEL,
        }
        checks["real_sdk_instruction_argument_result_roundtrip"] = True
        checks["synthetic_usage_aggregation"] = True
        checks["cache_discount_metadata_lost_at_pinned_adapter"] = True
        messages.extend(
            [
                {"role": "assistant", "content": terminal["response"]},
                {"role": "user", "content": "Reopen the evidence", "session_id": "fixture-session"},
            ]
        )
        updated = instructions + "\nINSTRUCTIONS-v2: Recover saved evidence."
        all_events["warm_followup"] = await collect(executor, messages, discovered.schemas, updated)
        assert any(e["type"] == "TurnComplete" for e in all_events["warm_followup"])
        checks["updated_instructions_and_warm_history"] = True

        await executor.close()
        executor = OpenAIAgentsSDKExecutor(client=client, model=MODEL)
        executor._tool_executor = dispatch
        all_events["cold_followup"] = await collect(executor, messages, discovered.schemas, updated)
        assert any(e["type"] == "TurnComplete" for e in all_events["cold_followup"])
        checks["fresh_executor_replays_supplied_history"] = True

        cancel_stream = WaitingStream()
        transport.cancel_stream = cancel_stream
        cancel_messages = [
            {"role": "user", "content": "Wait for interruption", "session_id": "cancel-session"}
        ]
        task = asyncio.create_task(
            collect(executor, cancel_messages, discovered.schemas, instructions)
        )
        await asyncio.wait_for(cancel_stream.started.wait(), timeout=10)
        assert await executor.interrupt_session("cancel-session") is True
        all_events["cancelled_turn"] = await asyncio.wait_for(task, timeout=10)
        await asyncio.wait_for(cancel_stream.closed.wait(), timeout=10)
        assert not any(e["type"] == "TurnComplete" for e in all_events["cancelled_turn"])
        assert await executor.interrupt_session("cancel-session") is False
        checks["interruption_closes_sdk_http_stream"] = True
        checks["interrupted_turn_does_not_report_completion_usage"] = True
    finally:
        await executor.close()
        await client.close()
        await manager.shutdown()
        write_json(out / "events.json", all_events)
        write_json(out / "model-requests.json", transport.requests)
        write_json(out / "model-responses.json", transport.responses)
        write_json(out / "checks.json", checks)
    metadata["checks_passed"] = len(checks)
    write_json(out / "metadata.json", metadata)
    print(
        json.dumps(
            {
                "status": "passed",
                "checks": len(checks),
                "artifacts": str(out),
                "live_model": False,
                "ui_verified": False,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="New artifact directory; refuses overwrite"
    )
    args = parser.parse_args()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    asyncio.run(run(out))


if __name__ == "__main__":
    main()
