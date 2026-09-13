import copy
import json

import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError

from research_harness.config import SourceSpec
from research_harness.discovery import SEARCH_TOOL, SearchArguments
from research_harness.discovery_models import ProposalDraft
from research_harness.integrations.provider_schema import (
    ProviderProposalDraft,
    apply_provider_defaults,
    strict_provider_schema,
)
from research_harness.services.search import SearchFilters


def assert_strict_schema(schema):
    """Check schema nodes, without confusing a property named default with an annotation."""
    assert "default" not in schema
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
    for key in ("$defs", "definitions", "properties"):
        for child in schema.get(key, {}).values():
            assert_strict_schema(child)
    if isinstance(schema.get("items"), dict):
        assert_strict_schema(schema["items"])
    for key in ("anyOf", "allOf", "oneOf"):
        for child in schema.get(key, []):
            assert_strict_schema(child)


def source_with_null_defaults(source):
    value = source.model_dump(mode="json")
    for name, field in SourceSpec.model_fields.items():
        if field.is_required():
            continue
        default = field.get_default(call_default_factory=True)
        if hasattr(default, "model_dump"):
            default = default.model_dump(mode="json")
        if value[name] == default:
            value[name] = None
    return value


@pytest.mark.parametrize("model", [SearchArguments, ProposalDraft])
def test_provider_schema_is_closed_default_free_and_does_not_mutate_domain_schema(model):
    original = model.model_json_schema()
    before = copy.deepcopy(original)
    result = strict_provider_schema(original)
    assert_strict_schema(result)
    assert original == before == model.model_json_schema()
    assert result["$defs"]["SearchFilters" if model is SearchArguments else "SourceSpec"]


def test_actual_sdk_schema_conversion_retains_nullable_defaults_and_bounds():
    assert_strict_schema(SEARCH_TOOL["parameters"])
    schema = to_strict_json_schema(ProviderProposalDraft)
    assert_strict_schema(schema)
    source = schema["$defs"]["SourceSpec"]
    interval, null = source["properties"]["poll_interval_seconds"]["anyOf"]
    assert interval["type"] == "integer" and interval["minimum"] == 60
    assert interval["maximum"] == 2592000 and null == {"type": "null"}
    assert schema["properties"]["candidates"]["maxItems"] == 12
    assert {"id", "name", "connector", "url"} <= set(source["required"])
    assert "anyOf" not in source["properties"]["url"]
    assert any(
        branch.get("type") == "null" for branch in source["properties"]["pagination"]["anyOf"]
    )


def test_search_null_defaults_preserve_existing_nulls_and_raw_explicit_values():
    value = {
        "query": "policy",
        "filters": {"site": None, "max_results": None, "snippet_max_length": "300"},
    }
    before = copy.deepcopy(value)
    normalized = apply_provider_defaults(value, SearchArguments.model_json_schema())
    assert normalized == {"query": "policy", "filters": {"site": None, "snippet_max_length": "300"}}
    assert value == before
    parsed = SearchArguments.model_validate(normalized)
    assert parsed.filters == SearchFilters(snippet_max_length=300)
    assert apply_provider_defaults(
        {"query": "policy", "filters": None}, SearchArguments.model_json_schema()
    ) == {"query": "policy", "filters": None}


@pytest.mark.parametrize("value", [0, 11, "not-an-integer"])
def test_search_bounds_and_invalid_values_are_still_domain_errors(value):
    raw = {"query": "policy", "filters": {"max_results": value}}
    with pytest.raises(ValidationError):
        SearchArguments.model_validate(
            apply_provider_defaults(raw, SearchArguments.model_json_schema())
        )


def test_nested_proposal_null_defaults_match_probe_config_without_mutating_input(source):
    from test_discovery import draft

    value = draft(source, "fixture-probe").model_dump(mode="json")
    original = copy.deepcopy(value)
    value["candidates"][0]["source"] = source_with_null_defaults(source)
    assert value["candidates"][0]["source"]["pagination"] is None
    before = copy.deepcopy(value)
    parsed = ProviderProposalDraft.model_validate_json(json.dumps(value))
    assert parsed.model_dump(mode="json") == original
    assert parsed.candidates[0].source == source
    assert value == before
    # Domain callers still reject newly nullable wire values until adapted.
    with pytest.raises(ValidationError):
        ProposalDraft.model_validate(value)


def test_normalization_preserves_false_zero_empty_and_required_null_errors(source):
    value = source.model_dump(mode="json")
    value.update(enabled=False, min_records=0, items_pointer="", parameters=[])
    assert apply_provider_defaults(value, SourceSpec.model_json_schema()) == value
    invalid = {**value, "url": None}
    normalized = apply_provider_defaults(invalid, SourceSpec.model_json_schema())
    assert normalized["url"] is None
    with pytest.raises(ValidationError):
        SourceSpec.model_validate(normalized)


def test_schema_property_names_and_reference_escaping_are_not_rewritten():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"default": {"$ref": "#/$defs/a~1b~0c"}},
        "$defs": {"a/b~c": {"type": "integer", "default": 2}},
    }
    adapted = strict_provider_schema(schema)
    assert_strict_schema(adapted)
    assert "default" in adapted["properties"]
    assert apply_provider_defaults({"default": None}, schema) == {}


def test_provider_adapter_refuses_open_mapping_instead_of_silently_removing_valid_keys():
    with pytest.raises(ValueError, match="named properties"):
        strict_provider_schema({"type": "object", "additionalProperties": {"type": "string"}})
