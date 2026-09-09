"""Select immutable, complete Responses history groups without rewriting them.

The host policy preserves the active user turn, following the reasoning guide's
context-management guidance. This is deliberately stricter than JSON schema:
single-brief uninterrupted research may have no removable groups. Older complete
interactions can be omitted, including their associated opaque reasoning, while
every user/system/developer message and all non-input request controls remain.

https://developers.openai.com/api/docs/guides/reasoning#keeping-reasoning-items-in-context
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from research_harness.util import canonical_json, digest

if TYPE_CHECKING:
    from research_harness.strategies.session import StrategySession

POLICY = "responses-complete-interactions-v1"
_PROTECTED = {"user", "system", "developer"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(content: Any) -> None:
    if isinstance(content, str):
        return
    _require(isinstance(content, list), "Context supports only text content")
    for block in content:
        _require(isinstance(block, dict), "Invalid context content block")
        kind = block.get("type")
        _require(
            isinstance(kind, str) and kind in {"input_text", "output_text", "text", "refusal"},
            "Unsupported context content",
        )
        _require(
            isinstance(block.get("refusal" if kind == "refusal" else "text"), str),
            "Invalid context text",
        )


def context_payload(request: dict) -> dict:
    """Build host-owned groups from the original input; reject ambiguous protocols."""
    _require(isinstance(request, dict), "Context request must be an object")
    _require(
        not (
            {"previous_response_id", "conversation", "prompt", "context_management"}
            & request.keys()
        ),
        "Provider-managed context cannot be projected",
    )
    original = request.get("input")
    if isinstance(original, str):
        items, input_type = [original], "string"
    else:
        _require(
            isinstance(original, list) and bool(original),
            "Context requires nonempty explicit input",
        )
        items, input_type = original, "list"
    groups, pending, seen_calls, seen_ids = [], set(), set(), set()
    current, start, latest_user, protected = [], 0, -1, False

    def flush():
        nonlocal current, protected
        if current:
            _require(not pending, "Context contains an unmatched function call")
            groups.append({"start": start, "items": deepcopy(current), "required": protected})
            current = []
            protected = False

    for index, item in enumerate(items):
        if input_type == "string":
            latest_user = index
            groups.append({"start": index, "items": [item], "required": True})
            continue
        _require(isinstance(item, dict), "Context items must be objects")
        kind = item.get("type", "message")
        identity = item.get("id")
        if identity is not None:
            _require(isinstance(identity, str) and bool(identity), "Invalid context item id")
            _require(identity not in seen_ids, "Duplicate context item id")
            seen_ids.add(identity)
        _require(
            item.get("status") is None or item.get("status") == "completed",
            "Context item is not completed",
        )
        if kind == "message":
            role = item.get("role")
            _require(
                isinstance(role, str) and role in _PROTECTED | {"assistant"},
                "Unsupported context message role",
            )
            _text(item.get("content"))
            if role == "user":
                flush()
                groups.append({"start": index, "items": [deepcopy(item)], "required": True})
                latest_user = index
                continue
            if role in {"system", "developer"}:
                # Do not split an interaction's opaque dependencies around an
                # instruction inserted midway through that interaction.
                protected = True
        elif kind == "function_call":
            call_id = item.get("call_id")
            _require(isinstance(call_id, str) and bool(call_id), "Invalid function call id")
            _require(call_id not in seen_calls, "Duplicate function call id")
            _require(
                isinstance(item.get("name"), str) and bool(item["name"]), "Missing function name"
            )
            _require(
                isinstance(item.get("arguments"), str), "Function arguments must be recorded text"
            )
            pending.add(call_id)
            seen_calls.add(call_id)
        elif kind == "function_call_output":
            call_id = item.get("call_id")
            _require(
                isinstance(call_id, str) and call_id in pending,
                "Unmatched or duplicate function output",
            )
            _text(item.get("output"))
            pending.remove(call_id)
        elif kind == "reasoning":
            _require(
                isinstance(identity, str) and bool(identity), "Reasoning requires its original id"
            )
            _require(
                isinstance(item.get("summary"), list), "Reasoning requires its original summary"
            )
            _require(
                item.get("encrypted_content") is None or isinstance(item["encrypted_content"], str),
                "Invalid opaque reasoning content",
            )
        else:
            raise ValueError(f"Unsupported context item type: {kind}")
        if not current:
            start = index
        current.append(item)
    flush()
    _require(latest_user >= 0, "Context requires an explicit user task")
    for group in groups:
        group["required"] |= group["start"] >= latest_user
        group["id"] = "group-" + digest(
            canonical_json({"start": group["start"], "items": group["items"]})
        )
    return {
        "schema_version": 1,
        "policy": POLICY,
        "input_type": input_type,
        "groups": groups,
        "required_group_ids": [group["id"] for group in groups if group["required"]],
    }


def validate_context_decision(payload: dict, decision: dict) -> dict:
    """Rebuild static groups before checking a candidate's ordered selection."""
    _require(isinstance(payload, dict), "Context payload must be an object")
    groups = payload.get("groups")
    _require(isinstance(groups, list) and bool(groups), "Missing context groups")
    _require(
        all(isinstance(group, dict) and isinstance(group.get("items"), list) for group in groups),
        "Invalid context groups",
    )
    flattened = [item for group in groups for item in group["items"]]
    original = (
        flattened[0] if payload.get("input_type") == "string" and len(flattened) == 1 else flattened
    )
    rebuilt = context_payload({"input": original})
    _require(
        canonical_json(rebuilt) == canonical_json(payload),
        "Context group contents or requirements changed",
    )
    _require(
        isinstance(decision, dict) and set(decision) == {"keep_group_ids"},
        "Context decision must contain only keep_group_ids",
    )
    selected = decision["keep_group_ids"]
    _require(
        isinstance(selected, list) and all(isinstance(value, str) for value in selected),
        "Context selection must be a list of group ids",
    )
    ids = [group["id"] for group in groups]
    _require(
        len(selected) == len(set(selected)) and set(selected) <= set(ids),
        "Context selection contains duplicate or unknown ids",
    )
    _require(
        [identity for identity in ids if identity in selected] == selected,
        "Context groups must retain their original order",
    )
    _require(
        set(payload["required_group_ids"]) <= set(selected),
        "Required context groups cannot be omitted",
    )
    return {"keep_group_ids": list(selected)}


def apply_context_decision(request: dict, decision: dict) -> dict:
    """Apply a selection using original items, never candidate-authored messages."""
    payload = context_payload(request)
    selected = validate_context_decision(payload, decision)["keep_group_ids"]
    projected = deepcopy(request)
    if payload["input_type"] == "list":
        projected["input"] = [
            deepcopy(item)
            for group in payload["groups"]
            if group["id"] in selected
            for item in group["items"]
        ]
    return projected


def project_context(session: StrategySession, request: dict, *, operation_id: str) -> dict:
    if not session.bundle.config.context:
        return deepcopy(request)
    payload = context_payload(request)
    decision = session.apply(
        "context",
        payload,
        operation_id=operation_id,
        validate=lambda value: validate_context_decision(payload, value),
    )
    return apply_context_decision(request, decision)
