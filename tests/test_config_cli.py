from __future__ import annotations

import json

import pytest

from research_harness.cli import main
from research_harness.config import PipelineSpec, SourceSpec
from research_harness.util import json_pointer, parse_timestamp


def test_duplicate_source_ids_and_unknown_config_fields_fail(source):
    with pytest.raises(ValueError, match="unique"):
        PipelineSpec(name="test", description="test", sources=[source, source])
    with pytest.raises(ValueError):
        SourceSpec.model_validate({**source.model_dump(), "typo_poll_seconds": 900})


def test_json_identity_and_kalshi_pagination_must_be_explicit():
    with pytest.raises(ValueError, match="id_pointer"):
        SourceSpec(id="x", name="x", connector="json", url="https://source.example")
    with pytest.raises(ValueError, match="pagination"):
        SourceSpec(
            id="x",
            name="x",
            connector="kalshi",
            url="https://external-api.kalshi.com/trade-api/v2/markets",
        )


def test_json_pointer_escapes_and_array_indices():
    assert json_pointer({"a/b": {"~key": ["value"]}}, "/a~1b/~0key/0") == "value"
    for pointer in ["bad", "/~2", "/00", "/-"]:
        with pytest.raises((ValueError, KeyError)):
            json_pointer(["x"], pointer)


def test_naive_cutoffs_are_rejected():
    with pytest.raises(ValueError, match="timezone"):
        parse_timestamp("2026-09-07T12:00:00")


def test_validate_is_offline_and_missing_key_is_actionable(tmp_path, spec, monkeypatch, capsys):
    path = tmp_path / "pipeline.json"
    path.write_text(spec.model_dump_json())
    assert main(["validate", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["network_requests"] == 0
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["discover", "Research trade", "--out", str(tmp_path / "discovery")]) == 1
    assert "OPENAI_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "discovery/pipeline.json").exists()


def test_unbounded_polymarket_catalog_cannot_be_called_a_complete_source():
    with pytest.raises(ValueError, match="unpaginated"):
        SourceSpec(
            id="x",
            name="All markets",
            connector="polymarket",
            url="https://gamma-api.polymarket.com/markets",
        )


def test_source_credentials_are_rejected_before_being_persisted():
    with pytest.raises(ValueError, match="credentials"):
        SourceSpec(
            id="x", name="Private", connector="html", url="https://source.example/?api_key=secret"
        )
