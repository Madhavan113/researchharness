"""Host-authored execution policy for the pinned controlled discovery runtime.

Loaded by Omnigent's normal ``policy_modules`` configuration. This file has no
Research Harness dependencies, so the separate Omnigent environment can import
the exact policy bytes frozen in the case bundle.
"""

import re

ALLOWED_TOOLS = frozenset(
    {
        "research__begin_research",
        "research__get_research_context",
        "research__search_sources",
        "research__inspect_source",
        "research__probe_source",
        "research__get_evidence",
        "research__submit_proposal",
        "research__list_pipelines",
    }
)
DENIAL = "Controlled discovery permits only the eight research discovery tools."


def controlled_discovery(*, case_id):
    if not isinstance(case_id, str) or not re.fullmatch(r"research-[a-f0-9]{32}", case_id):
        raise ValueError("Controlled discovery requires a frozen research case id")

    def evaluate(event):
        # At this pin function policies ignore the authored `on` annotation;
        # they must self-select. These phases do not execute a callback.
        if isinstance(event, dict) and event.get("type") in (
            "request",
            "response",
            "llm_request",
            "llm_response",
            "tool_result",
        ):
            return {"result": "ALLOW"}
        # Inspect the name only; arbitrary arguments (including large numeric
        # values) cannot cause expression conversion to abstain/fail open.
        allowed = False
        if isinstance(event, dict) and event.get("type") == "tool_call":
            target = event.get("target")
            data = event.get("data")
            if isinstance(target, str) and isinstance(data, dict) and data.get("name") == target:
                allowed = target in ALLOWED_TOOLS
                if target == "sys_agent_start":
                    # At this pin the runner emits a synthetic policy event
                    # before launching the harness. It does not register a
                    # callable sys_agent_start tool with the model/SDK.
                    arguments = data.get("arguments")
                    allowed = (
                        isinstance(arguments, dict)
                        and arguments.get("agent_name") == case_id
                        and arguments.get("harness") == "openai-agents"
                    )
        return {"result": "ALLOW" if allowed else "DENY", "reason": DENIAL}

    return evaluate


POLICY_REGISTRY = [
    {
        "handler": "research_discovery_policy.controlled_discovery",
        "kind": "factory",
        "name": "Controlled research discovery tools",
        "description": DENIAL,
        "params_schema": {
            "type": "object",
            "properties": {"case_id": {"type": "string"}},
            "required": ["case_id"],
            "additionalProperties": False,
        },
    }
]
