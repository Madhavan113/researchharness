"""Strict provider schemas with host-owned defaults and unchanged domain validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import model_validator

from research_harness.discovery_models import ProposalDraft
from research_harness.util import canonical_json


def _resolve(schema: dict, root: dict) -> dict:
    seen = set()
    while "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/") or reference in seen:
            raise ValueError("Provider schema needs an acyclic local reference")
        seen.add(reference)
        target = root
        for part in reference[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        schema = {**target, **{key: value for key, value in schema.items() if key != "$ref"}}
    return schema


def _nullable(schema: dict, root: dict) -> bool:
    schema = _resolve(schema, root)
    kind = schema.get("type")
    return (
        kind == "null"
        or isinstance(kind, list)
        and "null" in kind
        or any(_nullable(branch, root) for branch in schema.get("anyOf", []))
    )


def strict_provider_schema(schema: dict) -> dict:
    """Adapt a domain schema; no defaults, closed objects, all properties required.

    Defaulted nonnullable fields accept null on the wire. Existing nullable fields
    keep their meaning. The caller must normalize against the original domain
    schema before invoking the domain validator, which still owns all defaults.
    """
    root = deepcopy(schema)

    def visit(node: dict) -> dict:
        result = {key: deepcopy(value) for key, value in node.items() if key != "default"}
        for key in ("$defs", "definitions"):
            if key in node:
                result[key] = {name: visit(child) for name, child in node[key].items()}
        if node.get("type") == "object" or "properties" in node:
            if node.get("additionalProperties", False) is not False:
                raise ValueError("Strict provider objects need named properties, not open mappings")
            properties = {}
            required = set(node.get("required", []))
            for name, child in node.get("properties", {}).items():
                adapted = visit(child)
                if name not in required and not _nullable(child, root):
                    note = "Set null to use the host default"
                    if "default" in child:
                        note += f" ({canonical_json(child['default'])})"
                    adapted = {"anyOf": [adapted, {"type": "null"}], "description": note + "."}
                properties[name] = adapted
            result.update(
                properties=properties, required=list(properties), additionalProperties=False
            )
        if isinstance(node.get("items"), dict):
            result["items"] = visit(node["items"])
        for key in ("anyOf", "allOf", "oneOf"):
            if key in node:
                result[key] = [visit(child) for child in node[key]]
        return result

    return visit(root)


def apply_provider_defaults(value: Any, schema: dict) -> Any:
    """Copy and remove only newly nullable values, preserving all explicit legacy inputs."""

    def visit(item: Any, node: dict) -> Any:
        node = _resolve(node, schema)
        if item is None:
            return None
        if "anyOf" in node:
            branches = [branch for branch in node["anyOf"] if branch.get("type") != "null"]
            if len(branches) != 1:
                raise ValueError("Default normalization supports nullable unions only")
            return visit(item, branches[0])
        if isinstance(item, dict) and (node.get("type") == "object" or "properties" in node):
            properties, required = node.get("properties", {}), set(node.get("required", []))
            result = deepcopy(item)
            for name, child in properties.items():
                if name not in item:
                    continue
                if item[name] is None and name not in required and not _nullable(child, schema):
                    result.pop(name)
                else:
                    result[name] = visit(item[name], child)
            return result
        if isinstance(item, list) and isinstance(node.get("items"), dict):
            return [visit(child, node["items"]) for child in item]
        return deepcopy(item)

    return visit(value, schema)


class ProviderProposalDraft(ProposalDraft):
    """OpenAI output adapter; the saved proposal remains the domain ProposalDraft."""

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return strict_provider_schema(ProposalDraft.model_json_schema(*args, **kwargs))

    @model_validator(mode="before")
    @classmethod
    def provider_defaults(cls, value):
        return apply_provider_defaults(value, ProposalDraft.model_json_schema())
