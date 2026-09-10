"""Independently verify usage in a sealed, host-owned model gateway archive.

Hashes establish consistency with the supplied archive and execution binding.
They are not signatures or proof that candidate code could not write the files.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import zlib
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any

from research_harness.execution import DiscoverySettings, GatewayBinding
from research_harness.strategies.config import StrategyBundle, StrategyConfig
from research_harness.strategies.context import POLICY as CONTEXT_POLICY
from research_harness.strategies.context import apply_context_decision, context_payload
from research_harness.strategies.session import verify_event
from research_harness.util import canonical_json, digest

_ADAPTER = "controlled-responses-gateway"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_FILE_BYTES = 64 * 1024 * 1024
_REQUEST_FIELDS = {
    "model",
    "input",
    "instructions",
    "tools",
    "tool_choice",
    "text",
    "stream",
    "include",
    "max_output_tokens",
    "reasoning",
    "parallel_tool_calls",
    "store",
    "metadata",
    "background",
    "service_tier",
}
_TERMINALS = {"response.completed", "response.incomplete", "response.failed"}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def _json(raw: bytes | str) -> Any:
    return json.loads(raw, object_pairs_hook=_object, parse_constant=_invalid_constant)


def _same(left: Any, right: Any) -> bool:
    # Python equality alone treats True, 1, and 1.0 as identical.
    options = {"sort_keys": True, "separators": (",", ":"), "allow_nan": False}
    return json.dumps(left, **options) == json.dumps(right, **options)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _count(value: Any, name: str) -> int:
    _require(type(value) is int and value >= 0, f"Invalid {name} counter")
    return value


def _dict(value: Any, name: str) -> dict:
    _require(isinstance(value, dict), f"{name} must be an object")
    return value


def _local_file(root: Path, relative: str) -> Path:
    _require(isinstance(relative, str) and bool(relative), "Invalid archive file path")
    item = PurePosixPath(relative)
    _require(
        not item.is_absolute()
        and str(item) == relative
        and ".." not in item.parts
        and "\\" not in relative,
        "Archive file paths must be normalized local relative paths",
    )
    path = root
    for part in item.parts:
        path = path / part
        _require(not path.is_symlink(), "Archive cannot contain symlinks")
    _require(path.is_file(), f"Missing archive file: {relative}")
    _require(path.stat().st_size <= _MAX_FILE_BYTES, "Archive file exceeds verification limit")
    return path


def _read_archive(path: Path, result: dict) -> tuple[dict[str, bytes], dict]:
    _require(not path.is_symlink(), "Archive path cannot be a symlink")
    path = path / "archive.json" if path.is_dir() else path
    _require(path.name == "archive.json", "Expected gateway archive.json or its directory")
    root = path.parent.resolve()
    index_path = _local_file(root, "archive.json")
    index_raw = index_path.read_bytes()
    result["files"]["archive.json"] = {"path": str(index_path), "sha256": digest(index_raw)}
    index = _dict(_json(index_raw), "Archive index")
    _require(set(index) == {"schema_version", "files"}, "Unsupported archive index fields")
    _require(
        type(index["schema_version"]) is int and index["schema_version"] == 1,
        "Unsupported archive schema",
    )
    entries = _dict(index["files"], "Archive files")
    _require("archive.json" not in entries, "Archive cannot index itself")
    actual = set()
    for item in root.rglob("*"):
        _require(not item.is_symlink(), "Archive cannot contain symlinks")
        if item.is_file() and item != index_path:
            actual.add(item.relative_to(root).as_posix())
    _require(actual == set(entries), "Archive file inventory does not match the directory")
    contents = {}
    for relative, expected in entries.items():
        _require(
            isinstance(expected, str) and bool(_HASH.fullmatch(expected)),
            "Invalid archive file hash",
        )
        file = _local_file(root, relative)
        raw = file.read_bytes()
        observed = digest(raw)
        result["files"][relative] = {"path": str(file), "sha256": observed}
        _require(observed == expected, f"Archive hash mismatch: {relative}")
        contents[relative] = raw
    _require({"gateway.json", "report.json"} <= set(contents), "Missing gateway or report file")
    return contents, _dict(_json(contents["gateway.json"]), "Gateway metadata")


def _provider_response(
    raw: bytes, content_type: str, encoding: str | None
) -> tuple[dict | None, str | None, dict]:
    """Parse provider bytes independently; absent/partial usage stays unknown."""
    metadata = {
        "models": set(),
        "identities": set(),
        "service_tiers": set(),
        "invalid_service_tier": False,
        "observed_usage": [],
    }

    def remember(response):
        if isinstance(response, dict):
            if isinstance(response.get("model"), str):
                metadata["models"].add(response["model"])
            if isinstance(response.get("id"), str) and response["id"]:
                metadata["identities"].add(response["id"])
            tier = response.get("service_tier")
            if tier is not None:
                if isinstance(tier, str) and tier:
                    metadata["service_tiers"].add(tier)
                else:
                    metadata["invalid_service_tier"] = True
            if isinstance(response.get("usage"), dict):
                metadata["observed_usage"].append(response["usage"])

    try:
        if encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(_MAX_FILE_BYTES + 1)
            _require(len(raw) <= _MAX_FILE_BYTES, "Decoded response exceeds verification limit")
        elif encoding not in {None, "identity"}:
            return None, "unsupported_response_encoding", metadata
        responses = []
        if "text/event-stream" in content_type.lower():
            data = []
            declared_type = None
            for line in raw.decode("utf-8").splitlines() + [""]:
                if line.startswith("event:"):
                    declared_type = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line:
                    if data:
                        payload = "\n".join(data)
                        if payload != "[DONE]":
                            event = _dict(_json(payload), "SSE event")
                            remember(event.get("response"))
                            kind = event.get("type")
                            if kind in _TERMINALS:
                                _require(
                                    declared_type in {None, kind},
                                    "SSE terminal event type conflict",
                                )
                                response = _dict(event.get("response"), "Terminal response")
                                _require(
                                    response.get("status") == kind.removeprefix("response."),
                                    "SSE terminal status conflict",
                                )
                                responses.append(response)
                    data = []
                    declared_type = None
        else:
            responses = [_dict(_json(raw), "Provider response")]
            remember(responses[0])
    except (ValueError, OSError, EOFError, UnicodeError, TypeError, zlib.error) as exc:
        return None, f"unreadable_provider_response:{type(exc).__name__}", metadata
    if len(responses) > 1:
        raise ValueError("Multiple terminal provider responses in one dispatch")
    _require(
        len(metadata["identities"]) <= 1, "Conflicting provider response ids within one dispatch"
    )
    return (
        (responses[0] if responses else None),
        (None if responses else "missing_terminal_response"),
        metadata,
    )


def _observation(response: dict | None) -> tuple[dict | None, str | None]:
    if response is None:
        return None, "missing_provider_response"
    if response.get("status") not in {"completed", "incomplete", "failed"}:
        return None, "nonterminal_response"
    if not isinstance(response.get("id"), str) or not response["id"]:
        return None, "missing_response_id"
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None, "missing_provider_usage"
    try:
        input_tokens = _count(usage.get("input_tokens"), "provider input tokens")
        output_tokens = _count(usage.get("output_tokens"), "provider output tokens")
        if "total_tokens" in usage:
            _require(
                _count(usage["total_tokens"], "provider total tokens")
                == input_tokens + output_tokens,
                "Inconsistent provider token counters",
            )
        details = usage.get("input_tokens_details")
        if details is not None:
            _dict(details, "Provider input-token details")
            if "cached_tokens" in details:
                _require(
                    _count(details["cached_tokens"], "cached input tokens") <= input_tokens,
                    "Cached tokens exceed input tokens",
                )
    except ValueError:
        return None, "invalid_provider_usage_counters"
    return {
        "response_id": response["id"],
        "model": response.get("model"),
        "status": response["status"],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "provider_usage": usage,
    }, None


def _budget_policy(control: Any, model: str, settings: DiscoverySettings) -> dict | None:
    """Validate declared pricing bounds without trusting ledger summaries."""
    if control is None:
        return None
    control = _dict(control, "Budget control")
    _require(
        set(control)
        == {
            "schema_version",
            "policy",
            "rates",
            "rates_sha256",
            "ceiling_nanodollars",
            "authorization",
            "authorization_is_dispatch_permission",
            "input_bound",
        },
        "Unsupported budget control metadata",
    )
    _require(
        type(control["schema_version"]) is int and control["schema_version"] == 1,
        "Unsupported budget control schema",
    )
    policy = _dict(control["policy"], "Budget provider policy")
    _require(
        set(policy) == {"mode", "upstream_base_url", "model", "service_tier"},
        "Unsupported budget provider policy",
    )
    _require(
        policy["mode"] in {"fixture", "openai-standard"}
        and isinstance(policy["upstream_base_url"], str)
        and bool(policy["upstream_base_url"]),
        "Invalid budget provider mode or endpoint",
    )
    _require(
        policy["upstream_base_url"]
        == (
            "https://fixture.invalid/v1"
            if policy["mode"] == "fixture"
            else "https://api.openai.com/v1"
        ),
        "Budget provider endpoint mismatch",
    )
    _require(
        policy["model"] == model
        and policy["service_tier"] == "default"
        and settings.service_tier == policy["service_tier"],
        "Budget model or service tier mismatch",
    )
    rates = _dict(control["rates"], "Budget rates")
    _require(
        set(rates)
        == {
            "model",
            "snapshot",
            "input_usd_per_million",
            "output_usd_per_million",
            "max_input_tokens_per_request",
            "price_source_url",
            "price_as_of",
        },
        "Unsupported rate-card fields",
    )
    _require(
        rates["model"] == rates["snapshot"] == model,
        "Budget rates must identify the exact returned model",
    )
    _require(
        control["rates_sha256"] == digest(canonical_json(rates)), "Budget rate-card hash mismatch"
    )
    bound = _dict(control["input_bound"], "Budget input bound")
    _require(
        set(bound) == {"method", "max_input_tokens", "locally_tokenized"},
        "Invalid input-bound metadata",
    )
    maximum_input = _count(rates["max_input_tokens_per_request"], "budget input bound")
    _require(
        maximum_input > 0
        and _same(bound["max_input_tokens"], maximum_input)
        and bound["locally_tokenized"] is False,
        "Budget input bound mismatch",
    )
    method = "fixture_host_bound" if policy["mode"] == "fixture" else "provider_context_limit"
    _require(bound["method"] == method, "Budget input-bound method mismatch")
    authorization = _dict(control["authorization"], "Budget authorization metadata")
    _require(
        set(authorization) == {"status", "reference"}
        and authorization["status"] in {"draft", "approved"}
        and (authorization["reference"] is None or isinstance(authorization["reference"], str))
        and control["authorization_is_dispatch_permission"] is False,
        "Unsupported budget authorization metadata",
    )
    ceiling = _count(control["ceiling_nanodollars"], "budget ceiling")
    prices = []
    for field in ("input_usd_per_million", "output_usd_per_million"):
        _require(isinstance(rates[field], str), "Budget prices must be exact decimal strings")
        try:
            price = Decimal(rates[field])
        except InvalidOperation as exc:
            raise ValueError("Invalid budget price") from exc
        _require(price.is_finite() and price >= 0, "Invalid budget price")
        prices.append(Fraction(price))
    maximum_cost = (prices[0] * maximum_input + prices[1] * settings.max_output_tokens) * 1000
    reserved = (maximum_cost.numerator + maximum_cost.denominator - 1) // maximum_cost.denominator
    return {
        "max_input_tokens": maximum_input,
        "max_output_tokens": settings.max_output_tokens,
        "reserved_nanodollars": reserved,
        "ceiling_nanodollars": ceiling,
        "service_tier": policy["service_tier"],
    }


def _budget_record(record: dict, policy: dict | None, seen_operations: set[str]) -> dict:
    names = {
        "budget_operation_id",
        "budget_reserved_nanodollars",
        "budget_reservation_pending",
        "budget_dispatch_pending",
        "budget_dispatch_marked",
    }
    markers = {
        name: record.get(name, False) for name in names if name.endswith(("pending", "marked"))
    }
    _require(all(type(value) is bool for value in markers.values()), "Invalid budget state marker")
    operation = record.get("budget_operation_id")
    amount = record.get("budget_reserved_nanodollars")
    reserved = record.get("reserved_attempt_number") is not None
    if policy is None:
        _require(not (names & set(record)), "Unbudgeted request has budget state")
    elif reserved or operation is not None:
        _require(
            record.get("budget_reservation_pending") is False,
            "Reserved request has uncertain budget reservation",
        )
        _require(
            isinstance(operation, str) and 0 < len(operation) <= 200,
            "Reserved request lacks a budget operation id",
        )
        _require(operation not in seen_operations, "Duplicate budget operation id")
        seen_operations.add(operation)
        _require(
            _count(amount, "reserved nanodollars") == policy["reserved_nanodollars"]
            and amount <= policy["ceiling_nanodollars"],
            "Budget reservation amount mismatch",
        )
        if record["dispatch_started"]:
            _require(
                record.get("budget_dispatch_pending") is False
                and record.get("budget_dispatch_marked") is True,
                "Dispatched request lacks settled pre-dispatch budget markers",
            )
    else:
        _require(
            operation is None
            and amount is None
            and not markers["budget_dispatch_pending"]
            and not markers["budget_dispatch_marked"],
            "Unreserved request has budget dispatch state",
        )
        _require(
            "budget_reservation_pending" not in record or markers["budget_reservation_pending"],
            "Unreserved request claims a successful budget reservation",
        )
    _require(
        not (markers["budget_dispatch_pending"] and markers["budget_dispatch_marked"]),
        "Conflicting budget dispatch markers",
    )
    return {"budget_operation_id": operation, "budget_reserved_nanodollars": amount, **markers}


def _verify_forwarded(
    incoming: dict, forwarded: dict, record: dict, gateway: dict, settings: dict
) -> None:
    _require(not (set(incoming) - _REQUEST_FIELDS), "Unsupported forwarded request controls")
    _require(incoming.get("background", False) is False, "Background provider work is forbidden")
    _require(
        "service_tier" not in incoming or "service_tier" in settings,
        "Undeclared service tier in provider request",
    )
    if "text" in incoming:
        _require(
            isinstance(incoming["text"], dict) and not (set(incoming["text"]) - {"format"}),
            "Unsupported text controls",
        )
    items = incoming.get("input", [])
    _require(
        not (
            isinstance(items, list)
            and any(
                isinstance(item, dict) and item.get("type") == "item_reference" for item in items
            )
        ),
        "Opaque provider context reference",
    )
    tools = incoming.get("tools", [])
    _require(
        isinstance(tools, list)
        and all(isinstance(tool, dict) and tool.get("type") == "function" for tool in tools),
        "Provider-native tools cannot establish bounded gateway usage",
    )
    expected = deepcopy(incoming)
    stripped = []
    allowed = gateway.get("allowed_function_names")
    if allowed is not None:
        _require(
            isinstance(allowed, list) and all(isinstance(name, str) for name in allowed),
            "Invalid allowed function names",
        )
        expected["tools"] = [tool for tool in tools if tool.get("name") in allowed]
        stripped = [tool.get("name") for tool in tools if tool.get("name") not in allowed]
        choice = incoming.get("tool_choice")
        _require(
            not isinstance(choice, dict) or choice.get("name") in allowed, "Forbidden tool choice"
        )
    expected.update(settings)
    expected.pop("parallel_tool_calls", None)
    _require(_same(expected, forwarded), "Forwarded request does not match recorded controls")
    _require(
        _same(record.get("stripped_function_names"), stripped), "Stripped tool metadata mismatch"
    )
    changes = {
        key: {"incoming": incoming.get(key), "forwarded": forwarded.get(key)}
        for key in ("max_output_tokens", "reasoning", "parallel_tool_calls", "service_tier")
        if not _same(incoming.get(key), forwarded.get(key))
        or (key in incoming) != (key in forwarded)
    }
    _require(
        _same(record.get("control_changes"), changes), "Request control-change metadata mismatch"
    )


def _verify_context(
    contents: dict[str, bytes],
    result: dict,
    record: dict,
    binding: GatewayBinding,
    strategy: dict | None,
    expected_files: set[str],
) -> dict | None:
    prefix = record["id"] + "/"
    enabled = strategy is not None and strategy["config"]["context"]
    has_projection = "projected_sha256" in record
    context_fields = {
        "projected_sha256",
        "context_projection_sha256",
        "context_operation_id",
        "context_projection_pending",
    }
    if not enabled:
        _require(not (context_fields & record.keys()), "Unexpected context projection evidence")
        return None
    if "context_operation_id" in record:
        _require(
            record["context_operation_id"] == f"context:{binding.execution_id}:{record['id']}",
            "Context event belongs to another request or execution",
        )
        _require(
            type(record.get("context_projection_pending")) is bool, "Missing context pending marker"
        )
    if not has_projection:
        _require(
            record.get("reserved_attempt_number") is None and not record["dispatch_started"],
            "Context-enabled request dispatched without a verified projection",
        )
        _require(
            "context_projection_sha256" not in record, "Incomplete context projection metadata"
        )
        _require(
            record.get("context_projection_pending") is not False,
            "Missing completed context projection",
        )
        return None
    _require(
        record.get("context_projection_pending") is False, "Completed projection is still pending"
    )
    expected_files.update({prefix + "projected.json", prefix + "context-projection.json"})
    projected_raw, proof_raw = (
        contents[prefix + name] for name in ("projected.json", "context-projection.json")
    )
    _require(
        digest(projected_raw) == record["projected_sha256"]
        and digest(proof_raw) == record.get("context_projection_sha256"),
        "Context projection hash mismatch",
    )
    proof = _dict(_json(proof_raw), "Context projection proof")
    _require(
        set(proof)
        == {
            "schema_version",
            "policy",
            "binding",
            "operation_id",
            "strategy_sha256",
            "incoming_sha256",
            "projected_sha256",
            "event_files",
        },
        "Unexpected context proof fields",
    )
    _require(
        type(proof["schema_version"]) is int
        and proof["schema_version"] == 1
        and proof["policy"] == CONTEXT_POLICY
        and _same(proof["binding"], binding.model_dump(mode="json"))
        and proof["operation_id"] == record.get("context_operation_id")
        and proof["strategy_sha256"] == strategy["strategy_sha256"]
        and proof["incoming_sha256"] == record["incoming_sha256"]
        and proof["projected_sha256"] == record["projected_sha256"],
        "Context proof binding mismatch",
    )
    event_files = _dict(proof["event_files"], "Context event inventory")
    for name, value in event_files.items():
        path = prefix + "strategy-event/" + name
        _require(
            path in contents and digest(contents[path]) == value, "Context event file hash mismatch"
        )
        expected_files.add(path)
    event_path = Path(result["files"][prefix + "strategy-event/record.json"]["path"]).parent
    config = StrategyConfig.model_validate(strategy["config"])
    bundle = StrategyBundle(
        event_path / "strategy.json",
        event_path / "execution/input/strategy.py",
        config,
        strategy["strategy_sha256"],
    )
    event = verify_event(event_path, bundle)
    _require(
        _same(event_files, {**event["files"], "record.json": event["record_sha256"]}),
        "Context event inventory differs from isolated evidence",
    )
    incoming = _dict(_json(contents[prefix + "incoming.json"]), "Original context request")
    worker_input = event["input"]
    _require(
        set(worker_input) == {"schema_version", "kind", "payload", "state"}
        and type(worker_input["schema_version"]) is int
        and worker_input["schema_version"] == 1
        and worker_input["kind"] == event["record"].get("kind") == "context"
        and event["record"].get("operation_id") == proof["operation_id"]
        and event["record"].get("source_sha256") == config.source_sha256
        and _same(worker_input["payload"], context_payload(incoming)),
        "Context candidate input binding mismatch",
    )
    projected = _dict(_json(projected_raw), "Projected request")
    _require(
        _same(apply_context_decision(incoming, event["result"]["decision"]), projected),
        "Projected request differs from the independently validated selection",
    )
    return projected


def _verify_stopping(
    contents: dict[str, bytes],
    result: dict,
    record: dict,
    binding: GatewayBinding,
    strategy: dict | None,
    gateway: dict,
    expected_files: set[str],
    projected: dict | None,
) -> dict | None:
    from research_harness.strategies.stopping import (
        POLICY,
        finalize_request,
        stop_views,
        validate_stop_event,
    )

    enabled = strategy is not None and strategy["config"].get("finalize_on_stop", False)
    fields = {"stopping_checked", "stopping_projection_sha256"}
    if not enabled:
        _require(
            gateway.get("stopping_policy") is None and not (fields & record.keys()),
            "Unexpected stopping control evidence",
        )
        return projected
    _require(gateway.get("stopping_policy") == POLICY, "Missing or unknown stopping policy")
    admitted = (
        record.get("reserved_attempt_number") is not None
        or record.get("budget_operation_id") is not None
        or record.get("forwarded_sha256") is not None
    )
    if "stopping_checked" not in record:
        _require(
            not admitted and not (fields & record.keys()),
            "Request admitted without a stopping check",
        )
        return projected
    _require(record["stopping_checked"] is True, "Invalid stopping check marker")
    prefix = record["id"] + "/"
    incoming = _dict(_json(contents[prefix + "incoming.json"]), "Original stopping request")
    if "stopping_projection_sha256" not in record:
        _require(
            not stop_views(incoming, strategy["strategy_sha256"]),
            "Stopping tool output lacks independently verified proof",
        )
        return projected
    descriptor_name = prefix + "stopping-projection.json"
    expected_files.add(descriptor_name)
    descriptor_raw = contents[descriptor_name]
    _require(
        digest(descriptor_raw) == record["stopping_projection_sha256"],
        "Stopping proof hash mismatch",
    )
    descriptor = _dict(_json(descriptor_raw), "Stopping proof")
    _require(
        set(descriptor)
        == {
            "schema_version",
            "policy",
            "binding",
            "strategy_sha256",
            "incoming_sha256",
            "event_files",
        }
        and type(descriptor["schema_version"]) is int
        and descriptor["schema_version"] == 1
        and descriptor["policy"] == POLICY
        and _same(descriptor["binding"], binding.model_dump(mode="json"))
        and descriptor["strategy_sha256"] == strategy["strategy_sha256"]
        and descriptor["incoming_sha256"] == record["incoming_sha256"],
        "Stopping proof binding mismatch",
    )
    inventory = _dict(descriptor["event_files"], "Stopping event inventory")
    for name, hashed in inventory.items():
        path = prefix + "stopping-event/" + name
        _require(
            path in contents and digest(contents[path]) == hashed,
            "Stopping event file hash mismatch",
        )
        expected_files.add(path)
    event_root = Path(result["files"][prefix + "stopping-event/record.json"]["path"]).parent
    config = StrategyConfig.model_validate(strategy["config"])
    bundle = StrategyBundle(
        event_root / "strategy.json",
        event_root / "execution/input/strategy.py",
        config,
        strategy["strategy_sha256"],
    )
    event = verify_event(event_root, bundle)
    _require(
        _same(inventory, {**event["files"], "record.json": event["record_sha256"]}),
        "Stopping event inventory differs from isolated evidence",
    )
    validate_stop_event(incoming, event, strategy["strategy_sha256"])
    return finalize_request(projected if projected is not None else incoming)


def verify_gateway_usage(
    archive_path: Path,
    expected_binding: GatewayBinding,
    *,
    expected_model: str,
    expected_model_settings: dict | None = None,
    expected_budgets: dict | None = None,
    expected_budget_control: dict | None = None,
    expected_strategy_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify an execution's raw provider usage without requiring a proposal.

    A complete result measures provider token accounting, including terminal
    failed/incomplete responses. It does not establish task success or cost.
    Duplicate provider ids invalidate the evidence rather than inflate totals.

    ``verified_requests`` is published only after the whole archive passes.
    Budgeted entries require an externally matched ``expected_budget_control``
    before they can have settlement status ``complete`` or ``not_dispatched``.
    Pending ledger writes and missing returned tiers remain ``unknown`` even
    when token totals are known. ``budget_settlement_complete`` covers these
    archived request entries, not the state of an external ledger.
    """
    result: dict[str, Any] = {
        "status": "invalid",
        "total_tokens": None,
        "completed_token_lower_bound": None,
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "cached_input_token_lower_bound": None,
        "returned_models": [],
        "dispatched_requests": None,
        "reserved_attempts": None,
        "response_count": 0,
        "files": {},
        "errors": [],
        "unknown_requests": [],
        "responses": [],
        "verified_requests": [],
        "budget_control": None,
        "budget_control_verified": False,
        "strategy_sha256": None,
        "strategy_control_verified": False,
        "upstream_transport": None,
        "sdk_retry_policy": None,
        "budget_settlement_complete": None,
        "cost_usd": None,
        "limitations": [
            "Host archive consistency is not a signature or access-isolation proof.",
            "Token accounting does not establish task success or provider billing.",
        ],
    }
    try:
        binding = GatewayBinding.model_validate(expected_binding)
        _require(
            isinstance(expected_model, str) and bool(expected_model.strip()),
            "Expected model is required",
        )
        contents, gateway = _read_archive(Path(archive_path), result)
        _require(
            gateway.get("schema_version") == 1
            and type(gateway.get("schema_version")) is int
            and gateway.get("adapter") == _ADAPTER,
            "Unsupported gateway metadata",
        )
        observed_binding = GatewayBinding.model_validate(gateway.get("binding"))
        _require(observed_binding == binding, "Gateway execution binding mismatch")
        result["binding"] = observed_binding.model_dump(mode="json")
        _require(gateway.get("model") == expected_model, "Gateway model mismatch")
        transport = gateway.get("upstream_transport")
        _require(
            transport is None or transport in ("httpx-default", "caller-supplied"),
            "Unknown gateway upstream transport provenance",
        )
        result["upstream_transport"] = transport
        retry_policy = gateway.get("sdk_retry_policy")
        _require(
            retry_policy is None or retry_policy == "reject_automatic_retries_v1",
            "Unknown gateway SDK retry policy",
        )
        result["sdk_retry_policy"] = retry_policy
        settings = DiscoverySettings.model_validate(gateway.get("discovery_settings"))
        strategy = gateway.get("strategy_control")
        if strategy is not None:
            _require(
                isinstance(strategy, dict) and set(strategy) == {"strategy_sha256", "config"},
                "Invalid gateway strategy control",
            )
            strategy_config = StrategyConfig.model_validate(strategy["config"])
            _require(
                digest(canonical_json(strategy_config.model_dump(mode="json")))
                == strategy["strategy_sha256"],
                "Gateway strategy configuration hash mismatch",
            )
            result["strategy_sha256"] = strategy["strategy_sha256"]
        if expected_strategy_sha256 is not None:
            _require(
                strategy is not None and strategy["strategy_sha256"] == expected_strategy_sha256,
                "Gateway strategy differs from the expected code strategy",
            )
            result["strategy_control_verified"] = True
        budget_control = gateway.get("budget_control")
        if expected_budget_control is not None:
            _require(
                _same(budget_control, expected_budget_control), "Gateway budget control mismatch"
            )
        budget_policy = _budget_policy(budget_control, expected_model, settings)
        budget_verified = budget_policy is not None and expected_budget_control is not None
        model_settings = settings.model_settings()
        if expected_model_settings is not None:
            _require(
                _same(expected_model_settings, model_settings), "Gateway model settings mismatch"
            )
        if expected_budgets is not None:
            _require(
                _same(expected_budgets, settings.budgets()), "Gateway execution budgets mismatch"
            )
        _require(
            gateway.get("auxiliary_endpoint_policy") == "reject_without_forwarding"
            and gateway.get("parallel_tool_calls") == "omitted",
            "Unsupported gateway control policy",
        )
        report = _dict(_json(contents["report.json"]), "Gateway report")
        _require(
            type(report.get("schema_version")) is int
            and report["schema_version"] == 1
            and report.get("adapter") == _ADAPTER,
            "Unsupported gateway report",
        )
        _require(report.get("closed") is True, "Gateway report is not sealed and closed")
        _require(report.get("model") == expected_model, "Gateway report model mismatch")
        _require(
            _same(report.get("max_rounds"), settings.max_rounds)
            and _same(report.get("deadline_seconds"), settings.deadline_seconds),
            "Report limits mismatch",
        )
        _require(report.get("auxiliary_usage_unknown") is False, "Unknown auxiliary usage policy")
        records = report.get("requests")
        _require(isinstance(records, list), "Gateway requests must be a list")
        expected_files = {"gateway.json", "report.json"}
        reserved, dispatched, observations, response_ids, models, unknown = (
            [],
            [],
            [],
            set(),
            set(),
            [],
        )
        denials = []
        verified_requests = []
        budget_operations = set()
        interrupted = False
        upstream_request_ids = []
        for index, value in enumerate(records, 1):
            record = _dict(value, "Request record")
            identity = f"request-{index:04d}"
            _require(record.get("id") == identity, "Request identity/order mismatch")
            prefix = identity + "/"
            expected_files.update({prefix + "record.json", prefix + "incoming.json"})
            _require(
                _same(_json(contents[prefix + "record.json"]), record),
                "Report request differs from record file",
            )
            _require(
                record.get("incoming_sha256") == digest(contents[prefix + "incoming.json"]),
                "Incoming request hash mismatch",
            )
            outcome = record.get("outcome")
            _require(
                outcome in {"completed", "interrupted", "denied"}, "Nonterminal archived request"
            )
            _require(isinstance(record.get("finished_at"), str), "Request has no finish time")
            _require(
                ("sdk_retry_headers" in record) == (retry_policy is not None),
                "SDK retry evidence differs from gateway policy",
            )
            if retry_policy is not None:
                retry_headers = record["sdk_retry_headers"]
                _require(
                    isinstance(retry_headers, list)
                    and all(isinstance(value, str) for value in retry_headers),
                    "Invalid SDK retry header evidence",
                )
                if retry_headers not in ([], ["0"]):
                    _require(
                        outcome == "denied"
                        and record.get("reason") == "automatic_retry_rejected"
                        and record.get("reserved_attempt_number") is None
                        and record.get("budget_operation_id") is None
                        and not record.get("budget_reservation_pending", False)
                        and not record.get("dispatch_started"),
                        "Automatic SDK retry was admitted",
                    )
                else:
                    _require(
                        record.get("reason") != "automatic_retry_rejected",
                        "SDK retry denial lacks matching header evidence",
                    )
            _require(
                type(record.get("forwarded")) is bool
                and type(record.get("dispatch_started")) is bool
                and record["forwarded"] == record["dispatch_started"],
                "Dispatch flags mismatch",
            )
            budget_state = _budget_record(record, budget_policy, budget_operations)
            projected = _verify_context(
                contents, result, record, observed_binding, strategy, expected_files
            )
            projected = _verify_stopping(
                contents,
                result,
                record,
                observed_binding,
                strategy,
                gateway,
                expected_files,
                projected,
            )
            if budget_state["budget_operation_id"] is not None:
                _require(
                    budget_state["budget_operation_id"]
                    == f"{observed_binding.execution_id}/{identity}",
                    "Budget operation belongs to another execution or request",
                )
            reservation = record.get("reserved_attempt_number")
            if reservation is None:
                _require(
                    not record["dispatch_started"] and outcome == "denied",
                    "Unadmitted request reports provider activity",
                )
            if budget_state["budget_operation_id"] is not None:
                _require(
                    "forwarded_sha256" in record,
                    "Acknowledged budget reservation lacks its prepared request",
                )
            if reservation is not None:
                _require(
                    _count(reservation, "reserved attempt") > 0, "Invalid reserved attempt number"
                )
                reserved.append(reservation)
            if reservation is not None or (
                budget_policy is not None and "forwarded_sha256" in record
            ):
                expected_files.add(prefix + "forwarded.json")
                raw_forwarded = contents[prefix + "forwarded.json"]
                _require(
                    record.get("forwarded_sha256") == digest(raw_forwarded),
                    "Forwarded request hash mismatch",
                )
                incoming = _dict(_json(contents[prefix + "incoming.json"]), "Incoming request")
                forwarded = _dict(_json(raw_forwarded), "Forwarded request")
                _require(
                    record.get("path") == "/v1/responses"
                    and incoming.get("model") == expected_model
                    and forwarded.get("model") == expected_model,
                    "Forwarded model/path mismatch",
                )
                _verify_forwarded(
                    projected if projected is not None else incoming,
                    forwarded,
                    record,
                    gateway,
                    model_settings,
                )
            else:
                _require(
                    "forwarded_sha256" not in record
                    and not record["dispatch_started"]
                    and outcome == "denied",
                    "Unreserved request has forwarding evidence",
                )
            if record["dispatch_started"]:
                number = _count(record.get("upstream_request_number"), "dispatched request")
                _require(number > 0, "Invalid dispatch number")
                dispatched.append(number)
            else:
                _require(
                    "upstream_request_number" not in record and record.get("usage") is None,
                    "Undispatched request reports provider activity",
                )
            if outcome == "denied":
                _require(
                    reservation is None and isinstance(record.get("reason"), str),
                    "Invalid denied request",
                )
                denials.append({"id": identity, "reason": record["reason"]})
            interrupted |= outcome == "interrupted"
            body = None
            if "response_sha256" in record:
                expected_files.add(prefix + "response.body")
                body = contents[prefix + "response.body"]
                _require(
                    record["response_sha256"] == digest(body), "Response capture hash mismatch"
                )
            _require(
                outcome == "denied" or body is not None,
                "Sealed accepted request lacks its terminal capture",
            )
            _require(
                outcome != "completed" or (record["dispatch_started"] and body is not None),
                "Completion lacks dispatched response capture",
            )
            request_evidence = {
                "request_id": identity,
                "request_sha256": record.get("forwarded_sha256"),
                "incoming_sha256": record["incoming_sha256"],
                "forwarded_sha256": record.get("forwarded_sha256"),
                "dispatched": record["dispatch_started"],
                "usage": None,
                "provider_error": None,
                "response_service_tier": None,
                "status": "unknown" if record["dispatch_started"] else "not_dispatched",
                "issues": [],
                "evidence_sha256": digest(
                    canonical_json(
                        {
                            "schema_version": 1,
                            "archive_sha256": result["files"]["archive.json"]["sha256"],
                            "binding": observed_binding.model_dump(mode="json"),
                            "request_id": identity,
                            "files": {
                                name: evidence["sha256"]
                                for name, evidence in result["files"].items()
                                if name.startswith(prefix)
                            },
                        }
                    )
                ),
                **budget_state,
            }
            for pending in ("budget_reservation_pending", "budget_dispatch_pending"):
                if budget_state[pending]:
                    request_evidence["status"] = "unknown"
                    request_evidence["issues"].append(pending)
            if not record["dispatch_started"] and budget_state["budget_dispatch_marked"]:
                request_evidence["status"] = "unknown"
                request_evidence["issues"].append("budget_marked_without_observed_dispatch")
            if budget_policy is not None and not budget_verified:
                request_evidence["status"] = "unknown"
                request_evidence["issues"].append("budget_control_not_independently_matched")
            if not record["dispatch_started"]:
                _require(not body, "Undispatched request contains response bytes")
                verified_requests.append(request_evidence)
                continue
            upstream_request_id = record.get("upstream_request_id")
            if isinstance(upstream_request_id, str) and re.fullmatch(
                r"[!-~]{1,200}", upstream_request_id
            ):
                upstream_request_ids.append(upstream_request_id)
            response, issue = None, "missing_response_headers"
            provider_metadata = {
                "models": set(),
                "identities": set(),
                "service_tiers": set(),
                "invalid_service_tier": False,
                "observed_usage": [],
            }
            if "response_content_type" in record or "response_content_encoding" in record:
                content_type, encoding = (
                    record.get("response_content_type"),
                    record.get("response_content_encoding"),
                )
                _require(
                    "response_content_encoding" in record
                    and isinstance(content_type, str)
                    and (encoding is None or isinstance(encoding, str)),
                    "Invalid response format metadata",
                )
                _require(body is not None, "Response metadata lacks capture")
                response, issue, provider_metadata = _provider_response(
                    body, content_type, encoding
                )
            returned_models, provider_ids = (
                provider_metadata["models"],
                provider_metadata["identities"],
            )
            models.update(returned_models)
            result["returned_models"] = sorted(models)
            _require(not (returned_models - {expected_model}), "Returned provider model mismatch")
            _require(
                len(provider_ids) <= 1, "Conflicting provider response ids within one dispatch"
            )
            _require(not (provider_ids & response_ids), "Duplicate provider response id")
            response_ids.update(provider_ids)
            tiers = provider_metadata["service_tiers"]
            if len(tiers) == 1:
                request_evidence["response_service_tier"] = next(iter(tiers))
            if budget_policy is not None:
                _require(
                    not (tiers - {budget_policy["service_tier"]}),
                    "Returned provider service tier conflicts with budget policy",
                )
                if provider_metadata["invalid_service_tier"]:
                    request_evidence["issues"].append("invalid_response_service_tier")
                elif not tiers:
                    request_evidence["issues"].append("missing_response_service_tier")
                for observed_usage in provider_metadata["observed_usage"]:
                    for counter, bound_name in (
                        ("input_tokens", "max_input_tokens"),
                        ("output_tokens", "max_output_tokens"),
                    ):
                        if counter in observed_usage:
                            _require(
                                _count(observed_usage[counter], "observed provider tokens")
                                <= budget_policy[bound_name],
                                "Provider usage exceeds reserved token bounds",
                            )
            observation, usage_issue = _observation(response)
            if budget_policy is not None and observation is not None:
                _require(
                    observation["input_tokens"] <= budget_policy["max_input_tokens"]
                    and observation["output_tokens"] <= budget_policy["max_output_tokens"],
                    "Provider usage exceeds reserved token bounds",
                )
            if outcome != "completed":
                _require(record.get("usage") is None, "Interrupted dispatch claims terminal usage")
                unknown.append({"id": identity, "reason": "interrupted_dispatch"})
                request_evidence["issues"].append("interrupted_dispatch")
            else:
                _require(
                    _same(record.get("usage"), observation),
                    "Recorded usage differs from raw provider response",
                )
                if observation is None:
                    unknown.append({"id": identity, "reason": issue or usage_issue})
                    request_evidence["issues"].append(issue or usage_issue)
                else:
                    _require(
                        observation["model"] == expected_model,
                        "Usage has no expected provider model",
                    )
                    observations.append(observation)
                    request_evidence["usage"] = observation
                    if not request_evidence["issues"]:
                        request_evidence["status"] = "complete"
            # This identifies evidence an operator can reconcile against a
            # provider confirmation. It does not make cost or usage known.
            if (
                budget_verified
                and request_evidence["budget_operation_id"] is not None
                and request_evidence["budget_dispatch_marked"]
                and not request_evidence["budget_dispatch_pending"]
                and not request_evidence["budget_reservation_pending"]
                and outcome == "completed"
                and type(record.get("upstream_status")) is int
                and 400 <= record["upstream_status"] <= 599
                and isinstance(upstream_request_id, str)
                and re.fullmatch(r"[!-~]{1,200}", upstream_request_id)
                and "json" in record.get("response_content_type", "").lower()
                and isinstance(response, dict)
                and not ({"id", "status", "usage", "output"} & set(response))
                and isinstance(response.get("error"), dict)
                and isinstance(response["error"].get("message"), str)
                and response["error"]["message"].strip()
                and "nonterminal_response" in request_evidence["issues"]
                and set(request_evidence["issues"])
                <= {"nonterminal_response", "missing_response_service_tier"}
            ):
                request_evidence["provider_error"] = {
                    "http_status": record["upstream_status"],
                    "provider_request_id": upstream_request_id,
                }
            verified_requests.append(request_evidence)
        for request in verified_requests:
            error = request["provider_error"]
            if error and upstream_request_ids.count(error["provider_request_id"]) != 1:
                request["provider_error"] = None
                request["issues"].append("ambiguous_provider_request_id")
        _require(
            set(contents) == expected_files, "Archive contains unbound or missing request files"
        )
        _require(
            sorted(reserved) == list(range(1, len(reserved) + 1))
            and len(reserved) <= settings.max_rounds,
            "Reserved attempt sequence/limit mismatch",
        )
        _require(
            sorted(dispatched) == list(range(1, len(dispatched) + 1)), "Dispatch sequence mismatch"
        )
        _require(
            _count(report.get("reserved_attempts"), "report reservations") == len(reserved)
            and _count(report.get("upstream_requests"), "report dispatches") == len(dispatched),
            "Report request counts mismatch",
        )
        _require(
            _same(report.get("denials"), denials) and report.get("interrupted") is interrupted,
            "Report terminal-state summary mismatch",
        )
        input_tokens = sum(item["input_tokens"] for item in observations)
        output_tokens = sum(item["output_tokens"] for item in observations)
        total = input_tokens + output_tokens
        totals = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total,
        }
        _require(
            _same(report.get("totals"), totals), "Report totals differ from raw provider usage"
        )
        complete = not unknown
        _require(
            report.get("complete") is complete,
            "Report completeness differs from dispatched evidence",
        )
        if budget_policy is not None:
            _require(
                report.get("budget_policy_violation") is None
                and all(record.get("budget_policy_violation") is None for record in records),
                "Gateway flagged an unresolved budget policy violation",
            )
        cached = [
            (item["provider_usage"].get("input_tokens_details") or {}).get("cached_tokens")
            for item in observations
        ]
        cached_lower = sum(value for value in cached if value is not None)
        result.update(
            status="verified_complete" if complete else "verified_lower_bound",
            total_tokens=total if complete else None,
            completed_token_lower_bound=total,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_lower
            if complete and all(value is not None for value in cached)
            else None,
            cached_input_token_lower_bound=cached_lower,
            returned_models=sorted(models),
            dispatched_requests=len(dispatched),
            reserved_attempts=len(reserved),
            response_count=len(observations),
            interrupted=interrupted,
            auxiliary_usage_unknown=False,
            unknown_requests=unknown,
            responses=observations,
            verified_requests=verified_requests,
            budget_control=deepcopy(budget_control),
            budget_control_verified=budget_verified,
            budget_settlement_complete=budget_verified
            and all(item["status"] != "unknown" for item in verified_requests)
            if budget_policy is not None
            else None,
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    return result
