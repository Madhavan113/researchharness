"""Offline MCP fixture, launched by the pinned Omnigent runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import BaseModel, Field

server = FastMCP("research-fixture")


class FixtureSource(BaseModel):
    url: str
    connector: str
    labels: list[str] = Field(default_factory=list)
    options: dict[str, str] = Field(default_factory=dict)


@server.tool()
def fixture_probe(operation_id: str, source: FixtureSource) -> dict:
    """Verify JSON transport and return a synthetic receipt; never fetch a URL."""
    source_data = source.model_dump(mode="json")
    fingerprint = hashlib.sha256(
        json.dumps(source_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result = {
        "operation_id": operation_id,
        "status": "succeeded",
        "data": {"fingerprint": fingerprint, "source": source_data},
        "evidence": ["fixture-evidence-001"],
        "error": None,
        "retryable": False,
        "remaining": {"probes": 11},
    }
    capture_path = os.environ.get("RESEARCH_FIXTURE_CAPTURE")
    if capture_path:
        with Path(capture_path).open("a") as capture:
            capture.write(
                json.dumps(
                    {
                        "arguments": {"operation_id": operation_id, "source": source_data},
                        "result": result,
                    }
                )
                + "\n"
            )
    return result


@server.tool(structured_output=False)
def structured_only() -> CallToolResult:
    """Expose whether a client preserves MCP structuredContent without text."""
    return CallToolResult(content=[], structuredContent={"marker": "structured-only"})


if __name__ == "__main__":
    server.run(transport="stdio")
