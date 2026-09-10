"""Host-owned finalization controls derived from isolated strategy decisions."""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from typing import Any

from research_harness.strategies.projection import observation_payload, project_observation

POLICY = "verified-observation-finalization-v1"
NOTICE = (
    "Research discovery is finalizing under the selected strategy. Do not search, inspect, "
    "or probe new sources. Use the saved evidence to submit or repair a proposal, preserving "
    "unsupported and unverified gaps. Existing call, deadline and spending limits still apply."
)
FINALIZATION_TOOLS = {
    prefix + name
    for prefix in ("", "research__")
    for name in (
        "begin_research",
        "get_research_context",
        "get_evidence",
        "submit_proposal",
        "list_pipelines",
    )
}


class ResearchFinalizing(RuntimeError):
    """A valid strategy has stopped new research, while submission remains available."""


def finalize_request(request: dict) -> dict:
    result = deepcopy(request)
    instructions = result.get("instructions") or ""
    if not isinstance(instructions, str):
        raise ValueError("Finalization requires text instructions")
    result["instructions"] = instructions + "\n\n" + NOTICE
    result["tools"] = [
        tool
        for tool in result.get("tools", [])
        if tool.get("type") == "function" and tool.get("name") in FINALIZATION_TOOLS
    ]
    result["tool_choice"] = "auto" if result["tools"] else "none"
    return result


def _objects(value: Any, depth: int = 4):
    """Read only known direct/MCP text-output envelopes, not arbitrary nested assertions."""
    if depth <= 0:
        return
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            # Pinned Omnigent/Agents serializes structured MCP results with str(dict).
            # Parse literals only; never evaluate tool-output code or expressions.
            try:
                decoded = ast.literal_eval(value)
            except (ValueError, SyntaxError, RecursionError):
                return
        yield from _objects(decoded, depth - 1)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}:
                yield from _objects(item.get("text"), depth - 1)
    elif isinstance(value, dict):
        yield value
        if isinstance(value.get("data"), dict):
            yield value["data"]
        if isinstance(value.get("content"), list):
            yield from _objects(value["content"], depth - 1)


def stop_views(request: dict, strategy_sha256: str) -> list[dict]:
    items = request.get("input", [])
    if not isinstance(items, list):
        return []
    return [
        value
        for item in items
        if isinstance(item, dict) and item.get("type") == "function_call_output"
        for value in _objects(item.get("output"))
        if isinstance(value.get("strategy_view"), dict)
        and value["strategy_view"].get("strategy_sha256") == strategy_sha256
        and value["strategy_view"].get("stop_recommended") is True
    ]


def validate_stop_event(request: dict, event: dict, strategy_sha256: str) -> None:
    payload = event["input"]["payload"]
    observation = payload.get("observation", {})
    if (
        event["input"]["kind"] != "observation"
        or event["record"]["kind"] != "observation"
        or event["result"]["decision"].get("stop_recommended") is not True
        or payload != observation_payload(payload["tool"], observation)
        or event["record"]["operation_id"]
        != f"observation:{observation['discovery_id']}:{observation['operation_id']}"
        or project_observation(payload, event["result"]["decision"], strategy_sha256)
        not in stop_views(request, strategy_sha256)
    ):
        raise ValueError(
            "Stopping evidence is not bound to an observed tool output in this request"
        )
