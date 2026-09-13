"""Capture and check provider schemas offline; never contact OpenAI or a search provider.

Uses the actual OpenAI SDK with MockTransport and an in-memory MCP connection.
An optional separate Omnigent Python checks its Agents SDK strict conversion.
This does not establish live API acceptance, model quality, or billing behavior.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from importlib.metadata import version
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator
from mcp.shared.memory import create_connected_server_and_client_session
from openai import OpenAI

from research_harness.backend import Backend
from research_harness.config import SourceSpec
from research_harness.discovery import SEARCH_TOOL, TOOLS
from research_harness.discovery_models import ProposalDraft
from research_harness.integrations.provider_schema import (
    ProviderProposalDraft,
    strict_provider_schema,
)
from research_harness.mcp.server import create_server
from research_harness.services.research import ResearchService
from research_harness.util import canonical_json, digest, write_json

AGENTS_CHECK = """
import json, sys
from importlib.metadata import version
from pathlib import Path
from agents.mcp import MCPServerStdio
from agents.mcp.util import MCPUtil
from mcp.types import Tool

# Construction/conversion only; never connect, launch a tool process, or invoke a model.
server = MCPServerStdio(params={"command": sys.executable, "args": ["-c", "raise RuntimeError('must not execute')"]})
tools = []
for value in json.loads(Path(sys.argv[1]).read_text())["tools"]:
    converted = MCPUtil.to_function_tool(Tool.model_validate(value), server, True)
    if not converted.strict_json_schema:
        raise RuntimeError("Agents SDK fell back from strict mode")
    tools.append({"name": converted.name, "strict": converted.strict_json_schema, "parameters": converted.params_json_schema})
Path(sys.argv[2]).write_text(json.dumps({"agents_version": version("openai-agents"), "tools": tools}, indent=2) + "\\n")
"""


def check_schema(schema: dict) -> None:
    Draft202012Validator.check_schema(schema)
    if strict_provider_schema(schema) != schema:
        raise ValueError("Schema is not closed, required and free of default annotations")


def run(out: Path, *, omnigent_python: Path | None = None) -> dict:
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = SourceSpec(
        id="fixture-feed",
        name="Schema fixture",
        connector="rss",
        url="https://schema.fixture.example/feed",
    )
    draft = ProposalDraft.model_validate(
        {
            "name": "schema-fixture",
            "title": "Offline schema fixture",
            "research_question": "Check schemas only",
            "needs": [{"id": "schema", "description": "Schema fixture", "required": True}],
            "candidates": [
                {
                    "name": "Fixture",
                    "purpose": "Schema only",
                    "covers": ["schema"],
                    "status": "ready",
                    "evidence_urls": [source.url],
                    "source": source.model_dump(mode="json"),
                    "probe_id": "schema-fixture-not-service-evidence",
                    "freshness_assessment": "Unmeasured",
                    "historical_coverage": "Unmeasured",
                    "access_notes": "No source request",
                    "limitations": ["Authored schema value, not a service-verified proposal"],
                }
            ],
            "open_questions": [],
        }
    )
    wire = draft.model_dump(mode="json")
    for name, field in SourceSpec.model_fields.items():
        if not field.is_required():
            wire["candidates"][0]["source"][name] = None
    requests = []

    def respond(request):
        if request.url.host != "schema.invalid":
            raise ValueError("Offline preflight must use its synthetic endpoint")
        payload = json.loads(request.content)
        requests.append(payload)
        schema = payload["text"]["format"]["schema"]
        check_schema(schema)
        Draft202012Validator(schema).validate(wire)
        for tool in payload["tools"]:
            check_schema(tool["parameters"])
        return httpx.Response(
            200,
            json={
                "id": "resp_schema_fixture",
                "object": "response",
                "created_at": 0,
                "model": payload["model"],
                "status": "completed",
                "output": [
                    {
                        "id": "msg_schema_fixture",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": canonical_json(wire), "annotations": []}
                        ],
                    }
                ],
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        with OpenAI(
            api_key="offline-schema-fixture",
            base_url="https://schema.invalid/v1",
            http_client=transport,
            max_retries=0,
        ) as client:
            response = client.responses.parse(
                model="gpt-5.4-mini",
                input="Return the authored schema fixture.",
                tools=[SEARCH_TOOL, *TOOLS[1:]],
                text_format=ProviderProposalDraft,
                store=False,
            )
    if len(requests) != 1 or response.output_parsed.model_dump(mode="json") != draft.model_dump(
        mode="json"
    ):
        raise ValueError("SDK parsing did not restore the domain defaults")
    write_json(out / "openai-sdk-request.json", requests[0])
    write_json(out / "wire-response.json", wire)
    write_json(out / "parsed-proposal.json", response.output_parsed.model_dump(mode="json"))

    async def mcp_tools():
        service = ResearchService(
            out / "unused-case", backend=Backend.local(out / "unused-backend")
        )
        async with create_connected_server_and_client_session(create_server(service)) as client:
            tools = (await client.list_tools()).tools
            for tool in tools:
                check_schema(tool.inputSchema)
            return [tool.model_dump(mode="json", exclude_none=True) for tool in tools]

    tools = asyncio.run(mcp_tools())
    write_json(out / "mcp-tools.json", {"tools": tools})
    agents = None
    if omnigent_python is not None:
        target = out / "agents-sdk-tools.json"
        subprocess.run(
            [str(omnigent_python), "-c", AGENTS_CHECK, str(out / "mcp-tools.json"), str(target)],
            check=True,
            timeout=30,
        )
        agents = json.loads(target.read_bytes())
        if len(agents["tools"]) != len(tools):
            raise ValueError("Agents SDK conversion dropped a tool")
        for tool in agents["tools"]:
            check_schema(tool["parameters"])
    files = {path.name: digest(path.read_bytes()) for path in sorted(out.glob("*.json"))}
    report = {
        "schema_version": 1,
        "status": "passed",
        "mode": "offline-schema-fixture",
        "model_label": "gpt-5.4-mini",
        "openai_version": version("openai"),
        "pydantic_version": version("pydantic"),
        "direct_function_schemas": len(requests[0]["tools"]),
        "proposal_schema_checked": True,
        "mcp_input_schemas": len(tools),
        "agents_strict_conversions": len(agents["tools"]) if agents else None,
        "agents_version": agents["agents_version"] if agents else None,
        "nullable_source_defaults_restored": True,
        "provider_requests": 0,
        "files": files,
        "script_sha256": digest(Path(__file__).read_bytes()),
        "limitations": [
            "SDK serialization, JSON Schema validation and conversion checks only; not live API acceptance.",
            "The separate Agents conversion does not establish which strict flag the native Omnigent runner selects.",
            "Source/proposal values are authored schema fixtures, not verified research evidence.",
        ],
    }
    write_json(out / "report.json", report)
    print(canonical_json(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--omnigent-python", type=Path)
    args = parser.parse_args()
    run(args.out, omnigent_python=args.omnigent_python)
