import importlib.util
import json
import os
from pathlib import Path

import pytest

from research_harness.util import digest

pytest.importorskip("mcp", reason="Install the optional MCP extra for schema preflight")
SCRIPT = Path(__file__).resolve().parents[1] / "examples/omnigent/provider_schema_preflight.py"
SPEC = importlib.util.spec_from_file_location("provider_schema_preflight", SCRIPT)
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


@pytest.mark.parametrize("with_omnigent", [False, True])
def test_offline_preflight_captures_real_sdk_schemas_and_preserves_its_artifacts(
    tmp_path, with_omnigent
):
    python = os.environ.get("RH_TEST_OMNIGENT_PYTHON") if with_omnigent else None
    if with_omnigent and not python:
        pytest.skip("Set RH_TEST_OMNIGENT_PYTHON for the separate Agents SDK conversion")
    root = tmp_path / "preflight"
    report = PREFLIGHT.run(root, omnigent_python=Path(python) if python else None)
    assert report["status"] == "passed" and report["provider_requests"] == 0
    assert report["direct_function_schemas"] == 3 and report["mcp_input_schemas"] == 14
    assert report["agents_strict_conversions"] == (14 if with_omnigent else None)
    for name, sha256 in report["files"].items():
        assert digest((root / name).read_bytes()) == sha256
    wire = json.loads((root / "wire-response.json").read_bytes())
    parsed = json.loads((root / "parsed-proposal.json").read_bytes())
    assert wire["candidates"][0]["source"]["poll_interval_seconds"] is None
    assert parsed["candidates"][0]["source"]["poll_interval_seconds"] == 900
    assert wire["candidates"][0]["source"]["pagination"] is None
    assert parsed["candidates"][0]["source"]["pagination"]["mode"] == "none"
    before = (root / "report.json").read_bytes()
    with pytest.raises(FileExistsError):
        PREFLIGHT.run(root)
    assert (root / "report.json").read_bytes() == before
