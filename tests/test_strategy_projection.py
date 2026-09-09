from copy import deepcopy

import pytest

from research_harness.strategies.projection import (
    observation_payload,
    project_observation,
    validate_observation_decision,
)


def receipt(**extra):
    return {
        "operation_id": "search-1",
        "discovery_id": "case",
        "receipt_id": "receipt",
        "results": [
            {"url": "https://fixture.invalid/a", "title": "Alpha"},
            {"url": "https://fixture.invalid/b", "title": "Beta"},
        ],
        "provider_response": {"original": "raw provider recording"},
        **extra,
    }


def test_ranked_view_retains_exact_source_objects_and_authoritative_metadata():
    original = receipt(error=None, future_validation_field={"passed": False})
    before = deepcopy(original)
    payload = observation_payload("search_sources", original)
    chosen = payload["items"][1]["id"]
    decision = validate_observation_decision(
        payload,
        {
            "order": [chosen],
            "replace_body": True,
            "rendered": [{"text": "Candidate interpretation", "references": [chosen]}],
            "stop_recommended": True,
            "stop_reason": "No more useful sources",
        },
        32768,
    )
    result = project_observation(payload, decision, "a" * 64)
    assert original == before
    assert result["results"] == [before["results"][1]]
    assert result["receipt_id"] == original["receipt_id"]
    assert result["future_validation_field"] == {"passed": False}
    assert result["error"] is None and "provider_response" not in result
    assert result["strategy_view"]["kind"] == "candidate_interpretation"
    assert result["strategy_view"]["stop_recommended"] is True


@pytest.mark.parametrize(
    "decision",
    [
        {"order": ["unknown"]},
        {"status": "verified_sample"},
        {"rendered": [{"text": "unsupported claim", "references": []}]},
        {"rendered": [{"text": "unsupported claim", "references": ["invented"]}]},
        {"replace_body": True},
        {"replace_body": "true"},
    ],
)
def test_candidate_cannot_invent_references_or_authoritative_fields(decision):
    with pytest.raises(ValueError):
        validate_observation_decision(
            observation_payload("search_sources", receipt()), decision, 32768
        )


def test_duplicate_ids_and_render_overflow_are_rejected():
    payload = observation_payload("search_sources", receipt())
    identity = payload["items"][0]["id"]
    with pytest.raises(ValueError, match="duplicate"):
        validate_observation_decision(payload, {"order": [identity, identity]}, 32768)
    with pytest.raises(ValueError, match="limit"):
        validate_observation_decision(payload, {"stop_reason": "x" * 500}, 256)


def test_probe_failure_limits_and_future_fields_cannot_be_summarized_away():
    result = receipt(
        status="failed",
        error="No timestamp",
        issues=["Timestamp missing"],
        limits=["One page only"],
        samples=[{"data_preview": "large body"}],
        publication_profile={"records_with_declared_timestamp": 0},
        future_validation={"safe": False},
    )
    payload = observation_payload("probe_source", result)
    identity = payload["items"][0]["id"]
    decision = validate_observation_decision(
        payload,
        {
            "replace_body": True,
            "rendered": [{"text": "A candidate summary", "references": [identity]}],
        },
        32768,
    )
    projected = project_observation(payload, decision, "a" * 64)
    for key in ("status", "error", "issues", "limits", "publication_profile", "future_validation"):
        assert projected[key] == result[key]
    assert "samples" not in projected


def test_inspection_alias_uses_the_same_candidate_event():
    result = receipt(preview="source content")
    assert observation_payload("inspect_url", result) == observation_payload(
        "inspect_source", result
    )


def test_default_and_empty_selection_are_distinct():
    payload = observation_payload("search_sources", receipt())
    for supplied, expected in [({}, 2), ({"order": []}, 0)]:
        decision = validate_observation_decision(payload, supplied, 32768)
        assert len(project_observation(payload, decision, "a" * 64)["results"]) == expected
