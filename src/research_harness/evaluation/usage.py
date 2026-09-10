"""Verify host-owned discovery usage against the hashed runtime event export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt

from research_harness.config import StrictModel
from research_harness.util import digest


class ResponseUsage(StrictModel):
    model: str = Field(min_length=1)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    status: str


class UsageTotals(StrictModel):
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)


class RuntimeUsage(StrictModel):
    schema_version: Literal[1]
    discovery_id: str
    question_id: str
    phase: Literal["discovery", "workflow", "followup"]
    source_event_file: str
    source_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    responses: dict[str, ResponseUsage]
    totals: UsageTotals
    complete: StrictBool
    auxiliary_usage_unknown: StrictBool
    interrupted: StrictBool


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key in runtime usage or event JSON")
        result[key] = value
    return result


def verify_runtime_usage(path: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    """Return a verified total or only the completed-response token lower bound.

    This verifies consistency of trusted host artifacts; it is not a signature or
    proof of access isolation from candidate code. Usage errors do not alter source quality.
    """
    result: dict[str, Any] = {
        "status": "invalid",
        "total_tokens": None,
        "completed_token_lower_bound": None,
        "errors": [],
        "files": {},
    }
    try:
        path = Path(path).resolve()
        raw = path.read_bytes()
        result["files"]["runtime_usage"] = {"path": str(path), "sha256": digest(raw)}
        usage = RuntimeUsage.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
        registration = proposal.get("registry") or {}
        if not isinstance(registration, dict) or not registration.get("discovery_id"):
            raise ValueError("Saved proposal has no discovery binding for runtime usage")
        if usage.discovery_id != registration[
            "discovery_id"
        ] or usage.question_id != registration.get("question_id"):
            raise ValueError("Runtime usage belongs to a different discovery or question")
        if usage.phase != "discovery":
            raise ValueError("Only discovery-phase runtime usage can supply discovery tokens")
        event_path = (path.parent / usage.source_event_file).resolve()
        if Path(usage.source_event_file).is_absolute() or not event_path.is_relative_to(
            path.parent
        ):
            raise ValueError("Runtime event file must stay inside the usage export directory")
        events_raw = event_path.read_bytes()
        result["files"]["runtime_usage_events"] = {
            "path": str(event_path),
            "sha256": digest(events_raw),
        }
        if digest(events_raw) != usage.source_event_sha256:
            raise ValueError("Runtime source event hash mismatch")
        observed: dict[str, ResponseUsage] = {}
        interrupted = False
        for line in events_raw.splitlines():
            if not line.strip():
                continue
            event = json.loads(line, object_pairs_hook=_unique_object)
            if not isinstance(event, dict):
                raise ValueError("Runtime events must be JSON objects")
            event_type = event.get("event")
            if event_type not in {"response.completed", "response.failed", "response.cancelled"}:
                continue
            payload = event.get("data") or {}
            response = payload.get("response", payload)
            identity = response.get("id") or response.get("response_id")
            if not isinstance(identity, str) or not identity:
                raise ValueError("Completed runtime event has no response id")
            if identity not in usage.responses:
                continue
            if event.get("phase") != "discovery":
                raise ValueError("Selected response lacks a controller-recorded discovery phase")
            if event_type != "response.completed":
                interrupted = True
                continue
            reported = response.get("usage") or {}
            parsed = ResponseUsage.model_validate(
                {
                    "model": reported.get("model"),
                    "input_tokens": reported.get("input_tokens"),
                    "output_tokens": reported.get("output_tokens"),
                    "status": response.get("status", "completed"),
                }
            )
            if parsed.status != "completed":
                raise ValueError("Completed event contains a non-completed response")
            if identity in observed and observed[identity] != parsed:
                raise ValueError("Conflicting repeated runtime response id")
            observed[identity] = parsed
        lower_bound = sum(
            response.input_tokens + response.output_tokens for response in observed.values()
        )
        result["completed_token_lower_bound"] = lower_bound
        if observed != usage.responses:
            raise ValueError("Runtime usage responses do not match discovery-phase source events")
        if any(response.model != proposal.get("model") for response in observed.values()):
            raise ValueError("Runtime usage model conflicts with the saved proposal")
        totals = UsageTotals(
            input_tokens=sum(response.input_tokens for response in observed.values()),
            output_tokens=sum(response.output_tokens for response in observed.values()),
            total_tokens=lower_bound,
        )
        if totals != usage.totals:
            raise ValueError("Runtime usage totals do not match deduplicated responses")
        complete = (
            usage.complete
            and not usage.auxiliary_usage_unknown
            and not usage.interrupted
            and not interrupted
        )
        if complete and not observed:
            raise ValueError("No completed responses establish runtime usage completeness")
        if usage.complete and not complete:
            raise ValueError(
                "Runtime usage declares completeness despite interruption or unknown auxiliary usage"
            )
        result.update(
            status="verified_complete" if complete else "verified_lower_bound",
            total_tokens=lower_bound if complete else None,
            response_count=len(observed),
            auxiliary_usage_unknown=usage.auxiliary_usage_unknown,
            interrupted=usage.interrupted or interrupted,
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    return result
