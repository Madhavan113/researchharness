from __future__ import annotations

import json

import pytest
from test_strategy_session import bundle

from research_harness.strategies.config import StrategyBundle
from research_harness.util import digest, write_json


def test_freezing_preserves_identity_and_does_not_import_candidate_code(tmp_path):
    authored = bundle(tmp_path)
    marker = tmp_path / "candidate-imported-on-host"
    code = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    authored.source.write_text(code)
    value = authored.config.model_dump(mode="json")
    value["source_sha256"] = digest(code)
    write_json(authored.manifest_path, value)
    authored = StrategyBundle.load(authored.manifest_path)
    frozen = authored.freeze(tmp_path / "frozen")
    assert frozen.sha256 == authored.sha256
    assert frozen.source.read_bytes() == authored.source.read_bytes()
    assert not marker.exists()
    with pytest.raises(FileExistsError):
        authored.freeze(tmp_path / "frozen")


def test_changed_execution_limits_get_a_different_identity(tmp_path):
    authored = bundle(tmp_path)
    value = authored.config.model_dump(mode="json")
    value["max_events"] -= 1
    write_json(authored.manifest_path, value)
    changed = StrategyBundle.load(authored.manifest_path)
    assert changed.sha256 != authored.sha256
    with pytest.raises(ValueError, match="changed"):
        authored.freeze(tmp_path / "frozen")


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "../private.py"),
        ("source", "/tmp/private.py"),
        ("source", "nested/strategy.py"),
        ("schema_version", True),
        ("max_events", True),
        ("max_state_bytes", 1.5),
        ("observations", "yes"),
        ("context", 1),
        ("unknown_setting", 4),
    ],
)
def test_unsafe_or_ambiguous_configuration_is_rejected(tmp_path, field, value):
    authored = bundle(tmp_path)
    config = authored.config.model_dump(mode="json")
    config[field] = value
    write_json(authored.manifest_path, config)
    with pytest.raises(ValueError):
        StrategyBundle.load(authored.manifest_path)


@pytest.mark.parametrize(
    "tamper", ["source", "manifest_symlink", "source_symlink", "source_hardlink"]
)
def test_changed_or_linked_bundle_files_are_rejected(tmp_path, tamper):
    authored = bundle(tmp_path)
    if tamper == "source":
        authored.source.write_text("changed")
    elif tamper == "manifest_symlink":
        original = tmp_path / "original.json"
        authored.manifest_path.rename(original)
        authored.manifest_path.symlink_to(original)
    else:
        original = tmp_path / "original.py"
        authored.source.rename(original)
        if tamper == "source_symlink":
            authored.source.symlink_to(original)
        else:
            authored.source.hardlink_to(original)
    with pytest.raises(ValueError):
        StrategyBundle.load(authored.manifest_path)


def test_manifest_and_source_reads_are_bounded(tmp_path):
    authored = bundle(tmp_path)
    config = authored.config.model_dump(mode="json")
    config["sandbox"]["max_source_bytes"] = 1024
    code = "#" * 1025
    authored.source.write_text(code)
    config["source_sha256"] = digest(code)
    write_json(authored.manifest_path, config)
    with pytest.raises(ValueError, match="limit"):
        StrategyBundle.load(authored.manifest_path)
    authored.manifest_path.write_text(" " * 65536 + json.dumps(config))
    with pytest.raises(ValueError, match="limit"):
        StrategyBundle.load(authored.manifest_path)


def test_duplicate_manifest_keys_are_not_silently_overridden(tmp_path):
    authored = bundle(tmp_path)
    value = authored.manifest_path.read_text().rstrip()
    authored.manifest_path.write_text(value[:-1] + ',"source":"other.py"}')
    with pytest.raises(ValueError, match="Duplicate"):
        StrategyBundle.load(authored.manifest_path)
