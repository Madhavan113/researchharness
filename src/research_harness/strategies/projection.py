"""Validated, model-facing views of already persisted research observations.

Only supplied result references may be selected. Candidate text is interpretation;
receipt identity, validation status and the stored evidence remain host-owned.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import Field, StrictBool

from research_harness.config import StrictModel
from research_harness.util import canonical_json, digest

TOOLS = {"search_sources", "inspect_source", "probe_source"}
ALIASES = {"inspect_url": "inspect_source"}
# Only these bodies may be replaced. Unknown/new fields remain authoritative.
BODY_FIELDS = {
    "search_sources": {"provider_response"},
    "inspect_source": {"preview", "links"},
    "probe_source": {"samples"},
}


class RenderedObservation(StrictModel):
    text: str = Field(max_length=32_768)
    references: list[str] = Field(max_length=200)


class ObservationDecision(StrictModel):
    # None retains every supplied item in its original order; [] selects none.
    order: list[str] | None = Field(default=None, max_length=200)
    rendered: list[RenderedObservation] = Field(default_factory=list, max_length=100)
    replace_body: StrictBool = False
    stop_recommended: StrictBool = False
    stop_reason: str = Field(default="", max_length=2000)


def observation_payload(tool: str, result: dict[str, Any]) -> dict:
    tool = ALIASES.get(tool, tool)
    if tool not in TOOLS:
        raise ValueError("This operation does not support strategy projection")
    if not isinstance(result, dict) or not all(
        isinstance(result.get(key), str) and result[key]
        for key in ("operation_id", "discovery_id", "receipt_id")
    ):
        raise ValueError("Strategy observations require persisted service receipt identities")
    values = result.get("results", []) if tool == "search_sources" else [result]
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        raise ValueError("Malformed supplied observations")
    items = [
        {"id": f"item-{index}-{digest(canonical_json(value))}", "value": deepcopy(value)}
        for index, value in enumerate(values)
    ]
    return {"tool": tool, "observation": deepcopy(result), "items": items}


def validate_observation_decision(payload: dict, decision: dict, max_render_bytes: int) -> dict:
    parsed = ObservationDecision.model_validate(decision)
    known = {item["id"] for item in payload["items"]}
    if parsed.order is not None and (
        len(parsed.order) != len(set(parsed.order)) or not set(parsed.order) <= known
    ):
        raise ValueError("Strategy selected duplicate or unknown observation references")
    for rendered in parsed.rendered:
        if (
            not rendered.references
            or len(rendered.references) != len(set(rendered.references))
            or not set(rendered.references) <= known
        ):
            raise ValueError("Rendered interpretation requires supplied observation references")
    if parsed.replace_body and not parsed.rendered:
        raise ValueError("Replacing the observation body requires a referenced interpretation")
    value = parsed.model_dump(mode="json")
    if len(canonical_json(value).encode()) > max_render_bytes:
        raise ValueError("Strategy observation decision exceeds its rendering limit")
    return value


def project_observation(payload: dict, decision: dict, strategy_sha256: str) -> dict:
    """Apply an already validated decision without changing the saved receipt."""
    original = payload["observation"]
    result = deepcopy(original)
    supplied = {item["id"]: item["value"] for item in payload["items"]}
    order = decision["order"]
    selected = list(supplied) if order is None else order
    if decision["replace_body"]:
        result = {
            key: deepcopy(value)
            for key, value in original.items()
            if key not in BODY_FIELDS[payload["tool"]]
        }
    if payload["tool"] == "search_sources":
        # Search results are always exact supplied objects, even with a summary.
        result["results"] = [deepcopy(supplied[key]) for key in selected]
    result["strategy_view"] = {
        "kind": "candidate_interpretation",
        "strategy_sha256": strategy_sha256,
        "selected_references": selected,
        "rendered": deepcopy(decision["rendered"]),
        "body_replaced": decision["replace_body"],
        "stop_recommended": decision["stop_recommended"],
        "stop_reason": decision["stop_reason"],
        "notice": "Generated interpretation and stop advice; original receipts remain authoritative.",
    }
    return result
