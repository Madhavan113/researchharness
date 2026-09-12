"""Score sampled source selection without accepting the agent's coverage claims.

Artifact files must be produced by the trusted runner. Content hashes provide
reproducibility, not authentication or isolation from generated strategy code.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from pydantic import StrictInt, ValidationError, model_validator

from research_harness.config import PipelineSpec, SourceSpec, StrictModel
from research_harness.evaluation.gateway_usage import verify_gateway_usage
from research_harness.evaluation.usage import verify_runtime_usage
from research_harness.execution import GatewayBinding
from research_harness.util import canonical_json, digest, http_url

EVALUATOR_VERSION = 2
GAP_CREDIT = 0.5


class GapPredicate(StrictModel):
    """An independently authored blocker, not a candidate's explanation."""

    url: str
    status: str
    status_code: StrictInt
    content_type: str | None = None

    @model_validator(mode="after")
    def coherent(self):
        http_url(self.url)
        if self.status == "needs_connector":
            if self.status_code != 200 or self.content_type not in {
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.ms-excel",
            }:
                raise ValueError("Connector gaps require HTTP 200 and a PDF/workbook content type")
        elif self.status in {"needs_access", "unavailable"}:
            allowed = {401, 403} if self.status == "needs_access" else {404, 410}
            if self.status_code not in allowed or self.content_type is not None:
                raise ValueError("Access/availability gaps require a corresponding HTTP status")
        else:
            raise ValueError("Unknown gap status")
        return self


def matches(value: Any, expected: Any, *, _path: tuple[str, ...] = ()) -> bool:
    """Objects are subsets; required_pointers is a set subset; other lists are exact."""
    if _path == ("required_pointers",):
        return (
            isinstance(value, list)
            and isinstance(expected, list)
            and all(isinstance(item, str) for item in [*value, *expected])
            and set(expected) <= set(value)
        )
    if isinstance(expected, dict):
        return isinstance(value, dict) and all(
            key in value and matches(value[key], target, _path=(*_path, key))
            for key, target in expected.items()
        )
    return type(value) is type(expected) and value == expected


def validate_requirements(requirements: Any) -> list[dict]:
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("Supply at least one independent requirement")
    seen: set[str] = set()
    for requirement in requirements:
        if not isinstance(requirement, dict):
            raise TypeError("Requirements must be objects")
        identity = requirement.get("id")
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            raise ValueError("Requirement ids must be nonempty and unique")
        seen.add(identity)
        weight = requirement.get("weight", 1)
        if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
            raise ValueError("Requirement weights must be finite and positive")
        alternatives = requirement.get("any_of")
        gaps = requirement.get("gap_any_of", [])
        if not isinstance(gaps, list):
            raise ValueError(f"{identity}: gap alternatives must be a list")
        for gap in gaps:
            GapPredicate.model_validate(gap)
        if not isinstance(alternatives, list) or (not alternatives and not gaps):
            raise ValueError(f"{identity}: supply acceptable source predicates")
        for alternative in alternatives:
            if not isinstance(alternative, dict) or not isinstance(alternative.get("url"), str):
                raise ValueError(f"{identity}: each source predicate needs an exact URL")
            http_url(alternative["url"])
            if not isinstance(alternative.get("connector"), str) or not alternative["connector"]:
                raise ValueError(f"{identity}: each predicate needs a connector")
            unknown = set(alternative) - set(SourceSpec.model_fields)
            if unknown:
                raise ValueError(f"{identity}: unknown source fields {sorted(unknown)}")
            if "required_pointers" in alternative:
                pointers = alternative["required_pointers"]
                if not isinstance(pointers, list) or any(
                    not isinstance(pointer, str) or (pointer != "" and not pointer.startswith("/"))
                    for pointer in pointers
                ):
                    raise ValueError(
                        f"{identity}: required_pointers must be a list of JSON pointers"
                    )
    return requirements


def _gap_captures(
    events: list[dict], receipts: list[dict], discovery_id: str | None, errors: list[str]
) -> list[dict]:
    """Only scoped service exports establish blocker evidence; deduplicate trace copies."""
    if not isinstance(discovery_id, str) or not discovery_id:
        return []
    captures: dict[str, dict] = {}
    incomplete: list[dict] = []
    exports = [
        (item.get("kind"), item.get("payload"), item.get("discovery_id")) for item in receipts
    ] + [
        (event.get("kind"), event.get("result"), event.get("discovery_id"))
        for event in events
        if event.get("event") == "service_receipt"
    ]
    for kind, result, scope in exports:
        if (
            scope != discovery_id
            or not isinstance(result, dict)
            or kind not in {"inspection", "probe"}
        ):
            continue
        if result.get("discovery_id", scope) != scope:
            errors.append("Gap evidence result belongs to another discovery")
            continue
        items = [result] if kind == "inspection" else result.get("captures", [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            capture = {
                key: item.get(key) for key in ("capture_id", "url", "status_code", "body_sha256")
            }
            content_type = item.get("content_type")
            if isinstance(content_type, str):
                capture["content_type"] = content_type.split(";", 1)[0].strip().lower()
            elif kind == "inspection":
                capture["content_type"] = None
            identity = item.get("capture_id")
            if not isinstance(identity, str) or not identity:
                incomplete.append(capture)
                continue
            previous = captures.get(identity)
            if previous is not None:
                if any(previous[key] != value for key, value in capture.items() if key in previous):
                    errors.append("Conflicting service exports for the same capture")
                captures[identity] = {**previous, **capture}
            else:
                captures[identity] = capture
    return [*captures.values(), *incomplete]


def _gap_matches(candidate: dict, predicate: dict, captures: list[dict]) -> bool:
    rule = GapPredicate.model_validate(predicate)
    if candidate.get("status") != rule.status or rule.url not in candidate.get("evidence_urls", []):
        return False
    observations = [capture for capture in captures if capture.get("url") == rule.url]
    proved = False
    for capture in observations:
        status = capture.get("status_code")
        # A contradictory or incomplete capture cannot establish a consistent blocker in this run.
        if (
            not isinstance(capture.get("capture_id"), str)
            or not capture["capture_id"]
            or type(status) is not int
            or status != rule.status_code
            or not isinstance(capture.get("body_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", capture["body_sha256"])
        ):
            return False
        content_type = capture.get("content_type")
        if rule.content_type is not None:
            if "content_type" in capture and content_type != rule.content_type:
                return False
            proved |= content_type == rule.content_type
        else:
            proved = True
    return proved


def _objects(value: Any, label: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be a list of objects")
    return value


def _result_urls(kind: str, result: dict) -> set[str]:
    def urls(items: Any) -> set[str]:
        return (
            {
                item["url"]
                for item in items
                if isinstance(item, dict) and isinstance(item.get("url"), str)
            }
            if isinstance(items, list)
            else set()
        )

    if kind == "search":
        return (
            urls(result.get("results"))
            if result.get("status") not in {"error", "failed"}
            else set()
        )
    observed = urls(result.get("captures"))
    # A failed request's configured URL alone does not prove the page was observed.
    captured = (kind == "inspection" and result.get("capture_id")) or (
        kind == "probe" and result.get("status") == "verified_sample"
    )
    if captured and isinstance(result.get("url"), str):
        observed.add(result["url"])
    return observed


def observed_urls(
    events: list[dict], receipts: list[dict], discovery_id: str | None, errors: list[str]
) -> set[str]:
    observed: set[str] = set()

    def receipt(kind: Any, result: Any, scope: Any) -> None:
        if kind not in {"search", "inspection", "probe"} or not isinstance(result, dict):
            return
        if discovery_id is not None and scope != discovery_id:
            errors.append("Evidence receipt belongs to a different or unspecified discovery")
            return
        observed.update(_result_urls(kind, result))

    for item in receipts:
        receipt(item.get("kind"), item.get("payload"), item.get("discovery_id"))
    for event in events:
        if event.get("event") == "service_receipt":
            receipt(event.get("kind"), event.get("result"), event.get("discovery_id"))
        if event.get("event") == "model_response":
            for item in _objects(event.get("output", []), "Model response output"):
                if item.get("type") == "web_search_call" and item.get("status") == "completed":
                    action = item.get("action") or {}
                    if isinstance(action, dict):
                        observed.update(_result_urls("search", {"results": action.get("sources")}))
        if event.get("event") == "tool_result":
            kind = {
                "inspect_url": "inspection",
                "inspect_source": "inspection",
                "probe_source": "probe",
            }.get(event.get("tool"))
            result = event.get("result")
            if kind and isinstance(result, dict):
                observed.update(_result_urls(kind, result))
    return observed


def token_usage(proposal: dict) -> int | None:
    usage = proposal.get("usage")
    if not isinstance(usage, dict):
        return None
    values = [usage.get("input_tokens"), usage.get("output_tokens")]
    if any(type(value) is not int or value < 0 for value in values):
        return None
    return sum(values)


def score(
    artifacts: Path,
    specification: dict,
    *,
    runtime_usage: Path | None = None,
    gateway_usage: Path | None = None,
    gateway_binding: GatewayBinding | None = None,
    expected_model_settings: dict | None = None,
    expected_budgets: dict | None = None,
    expected_budget_control: dict | None = None,
    expected_strategy_sha256: str | None = None,
) -> dict:
    """Read one completed run and grade source selection, not full ingestion.

    Bad/missing input files or malformed specification raise ValueError/OSError.
    Inconsistent research evidence yields status=invalid and zero quality.
    """
    if not isinstance(specification, dict):
        raise ValueError("The independent specification must be an object")
    requirements = validate_requirements(specification.get("requirements"))
    artifacts = Path(artifacts)
    hashes: dict[str, str] = {}

    def read(name: str, default: Any = None, *, optional: bool = False) -> Any:
        path = artifacts / name
        if optional and not path.exists():
            return default
        raw = path.read_bytes()
        hashes[name] = digest(raw)
        if name.endswith(".jsonl"):
            return [json.loads(line) for line in raw.splitlines() if line.strip()]
        return json.loads(raw)

    proposal = read("proposal.json")
    if not isinstance(proposal, dict) or not isinstance(proposal.get("proposal"), dict):
        raise ValueError("proposal.json must contain a saved proposal object")
    usage_path = (
        Path(runtime_usage)
        if runtime_usage is not None
        else artifacts / "omnigent/runtime-usage.discovery.json"
    )
    usage_evidence = None
    if runtime_usage is not None or usage_path.exists():
        usage_evidence = verify_runtime_usage(usage_path, proposal)
        hashes.update({key: value["sha256"] for key, value in usage_evidence["files"].items()})
    gateway_evidence = None
    if gateway_usage is not None:
        if gateway_binding is None or gateway_binding.phase != "discovery":
            gateway_evidence = {
                "status": "invalid",
                "total_tokens": None,
                "completed_token_lower_bound": None,
                "files": {},
                "errors": [
                    "Discovery usage requires an expected host binding with discovery phase"
                ],
            }
        else:
            gateway_evidence = verify_gateway_usage(
                gateway_usage,
                gateway_binding,
                expected_model=proposal.get("model"),
                expected_model_settings=expected_model_settings,
                expected_budgets=expected_budgets,
                expected_budget_control=expected_budget_control,
                expected_strategy_sha256=expected_strategy_sha256,
            )
    candidates = _objects(proposal["proposal"].get("candidates"), "Proposal candidates")
    if not candidates:
        raise ValueError("A saved proposal must contain candidates")
    traces = _objects(read("trace.jsonl"), "Trace events")
    probes = _objects(read("probes.json", [], optional=True), "Probes")
    receipts = _objects(read("receipts.json", [], optional=True), "Receipts")
    pipeline = read("pipeline.json", optional=True)
    errors: list[str] = []
    registration = proposal.get("registry") or {}
    discovery_id = registration.get("discovery_id") if isinstance(registration, dict) else None
    observed = observed_urls(traces, receipts, discovery_id, errors)
    captures = _gap_captures(traces, receipts, discovery_id, errors)
    by_probe: dict[str, dict] = {}
    for probe in probes:
        identity = probe.get("probe_id")
        if not isinstance(identity, str) or not identity or identity in by_probe:
            errors.append("Probe ids must be present and unique")
            continue
        if discovery_id and probe.get("discovery_id", discovery_id) != discovery_id:
            errors.append(f"{identity}: probe belongs to another discovery")
            continue
        by_probe[identity] = probe
        observed.update(_result_urls("probe", probe))

    ready: dict[str, dict] = {}
    valid_sources: list[dict] = []
    gaps: list[dict] = []
    for index, candidate in enumerate(candidates):
        before = len(errors)
        label = candidate.get("name", f"candidate {index}")
        citations = candidate.get("evidence_urls")
        if (
            not isinstance(citations, list)
            or not citations
            or any(not isinstance(url, str) or url not in observed for url in citations)
        ):
            errors.append(f"{label}: missing or unobserved source citation")
        status = candidate.get("status")
        if status not in {"ready", "needs_connector", "needs_access", "unavailable"}:
            errors.append(f"{label}: unknown candidate status")
        if status != "ready":
            if len(errors) == before:
                gaps.append(candidate)
            continue
        try:
            spec = SourceSpec.model_validate(candidate.get("source"))
        except ValidationError:
            errors.append(f"{label}: ready candidate has an invalid SourceSpec")
            continue
        source = spec.model_dump(mode="json")
        if spec.id in ready:
            errors.append(f"{spec.id}: duplicate source id")
        ready[spec.id] = source
        probe_id = candidate.get("probe_id")
        proof = by_probe.get(probe_id) if isinstance(probe_id, str) else None
        if not spec.enabled:
            errors.append(f"{spec.id}: ready source is disabled")
        if not proof or proof.get("status") != "verified_sample":
            errors.append(f"{spec.id}: missing successful probe")
        elif proof.get("source_fingerprint") != spec.fingerprint():
            errors.append(f"{spec.id}: source changed after its probe")
        elif type(proof.get("record_count")) is not int or proof["record_count"] <= 0:
            errors.append(f"{spec.id}: probe did not produce records")
        if len(errors) == before:
            valid_sources.append(source)

    compiled: dict[str, dict] = {}
    if pipeline is not None:
        try:
            compiled_spec = PipelineSpec.model_validate(pipeline)
            compiled = {
                source.id: source.model_dump(mode="json") for source in compiled_spec.sources
            }
        except ValidationError:
            errors.append("Compiled pipeline is not a valid PipelineSpec")
    if compiled != ready:
        errors.append("Compiled pipeline does not match the ready candidate configurations")

    coverage = []
    gap_coverage = []
    relevant: set[int] = set()
    relevant_gaps: set[int] = set()
    claim_for_gap: dict[tuple[str, str], int] = {}
    total_weight = covered_weight = gap_weight = 0.0
    for requirement in requirements:
        weight = requirement.get("weight", 1)
        total_weight += weight
        matching = [
            index
            for index, source in enumerate(valid_sources)
            if any(matches(source, predicate) for predicate in requirement["any_of"])
        ]
        relevant.update(matching)
        if matching:
            covered_weight += weight
        coverage.append(
            {
                "id": requirement["id"],
                "covered": bool(matching),
                "source_ids": [valid_sources[index]["id"] for index in matching],
            }
        )
        matched_gaps: set[int] = set()
        if not matching:
            for predicate in requirement.get("gap_any_of", []):
                key = (predicate["url"], predicate["status"])
                for index, candidate in enumerate(gaps):
                    if _gap_matches(candidate, predicate, captures):
                        # Multiple copies of the same URL/status claim get only one credit.
                        matched_gaps.add(claim_for_gap.setdefault(key, index))
                        break
        relevant_gaps.update(matched_gaps)
        if matched_gaps:
            gap_weight += weight
        gap_coverage.append(
            {
                "id": requirement["id"],
                "gap_reported": bool(matched_gaps),
                "claim_indexes": sorted(matched_gaps),
            }
        )
    recall = covered_weight / total_weight
    precision = len(relevant) / len(ready) if ready else 0.0
    research_recall = (covered_weight + GAP_CREDIT * gap_weight) / total_weight
    claims = len(ready) + len(gaps)
    research_precision = (len(relevant) + len(relevant_gaps)) / claims if claims else 0.0
    f1 = (
        2 * research_precision * research_recall / (research_precision + research_recall)
        if research_precision + research_recall
        else 0.0
    )
    selected_usage = gateway_evidence if gateway_evidence is not None else usage_evidence
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "scope": "sampled_source_selection_and_gaps",
        "status": "invalid" if errors else "ok",
        "quality": 0.0 if errors else f1,
        "requirement_recall": recall,
        "source_precision": precision,
        "research_recall": research_recall,
        "research_precision": research_precision,
        "gap_recall": gap_weight / total_weight,
        "gap_credit": GAP_CREDIT,
        "total_tokens": selected_usage["total_tokens"] if selected_usage else token_usage(proposal),
        "completed_token_lower_bound": selected_usage["completed_token_lower_bound"]
        if selected_usage
        else token_usage(proposal),
        "runtime_usage": usage_evidence,
        "gateway_usage": gateway_evidence,
        "token_evidence_source": "gateway"
        if gateway_evidence is not None
        else "runtime"
        if usage_evidence is not None
        else "proposal",
        "reported_model": proposal.get("model"),
        "reported_search_providers": sorted(
            {
                receipt["payload"]["provider"]
                for receipt in receipts
                if receipt.get("kind") == "search"
                and isinstance(receipt.get("payload"), dict)
                and isinstance(receipt["payload"].get("provider"), str)
            }
        ),
        "coverage": coverage,
        "gap_coverage": gap_coverage,
        "errors": errors,
        "artifacts": str(artifacts.resolve()),
        "specification_sha256": digest(canonical_json(specification)),
        "artifact_sha256": hashes,
    }


def frontier(results: list[dict]) -> list[dict]:
    """Maximize quality and minimize known model tokens for one specification."""
    specifications = {
        result["specification_sha256"] for result in results if "specification_sha256" in result
    }
    if len(specifications) > 1:
        raise ValueError("Only compare runs scored against the same specification")
    if len({result.get("evaluator_version", 1) for result in results}) > 1:
        raise ValueError("Only compare runs scored with the same evaluator version")
    eligible = [
        result
        for result in results
        if result.get("status") == "ok"
        and type(result.get("total_tokens")) is int
        and result["total_tokens"] >= 0
        and type(result.get("quality")) in (int, float)
        and math.isfinite(result["quality"])
        and 0 <= result["quality"] <= 1
    ]
    return [
        point
        for point in eligible
        if not any(
            other["quality"] >= point["quality"]
            and other["total_tokens"] <= point["total_tokens"]
            and (
                other["quality"] > point["quality"] or other["total_tokens"] < point["total_tokens"]
            )
            for other in eligible
        )
    ]
