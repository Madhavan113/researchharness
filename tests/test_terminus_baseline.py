import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_harness.evaluation.budget import RateCard
from research_harness.experiments import terminus
from research_harness.experiments.terminus_runtime import LocalTaskEnvironment, response_cost


def test_export_preserves_the_real_upstream_source_and_prompts(tmp_path):
    python = os.environ.get("RH_TEST_HARBOR_PYTHON")
    if not python:
        if os.environ.get("RH_TEST_REQUIRE_RUNTIME") == "1":
            pytest.fail("RH_TEST_HARBOR_PYTHON is required for baseline validation")
        pytest.skip("Set RH_TEST_HARBOR_PYTHON to the separate Harbor interpreter")
    script = """
import ast, hashlib, importlib.metadata, json, sys
from pathlib import Path
from research_harness.experiments.terminus import export, SOURCE_SHA256, TEMPLATE_SHA256
output = Path(sys.argv[1])
record = export(output)
distribution = importlib.metadata.distribution('harbor')
source = Path(distribution.locate_file('harbor/agents/terminus_2/terminus_2.py')).read_bytes()
generated = (output / 'agent.py').read_bytes()
assert source in generated
assert hashlib.sha256(source).hexdigest() == SOURCE_SHA256
assert record['template_sha256'] == TEMPLATE_SHA256
assert 'Apache License' in (output / 'LICENSE').read_text()
native = ast.parse(source)
candidate = ast.parse(generated)
for node in native.body:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        match = next(item for item in candidate.body if type(item) is type(node) and item.name == node.name)
        assert ast.dump(node) == ast.dump(match), node.name
compile(generated, 'candidate.py', 'exec')  # Never execute candidate code on the host.
print(json.dumps(record))
"""
    result = subprocess.run(
        [python, "-c", script, str(tmp_path / "baseline")],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["measured"] is False


@pytest.mark.parametrize("change", ["version", "source", "prompt"])
def test_export_rejects_changed_baseline_before_writing(tmp_path, monkeypatch, change):
    root = tmp_path / "upstream"
    (root / "templates").mkdir(parents=True)
    source = b"class Terminus2: pass\n"
    (root / "terminus_2.py").write_bytes(source)
    (root / "templates/timeout.txt").write_text("original prompt")
    monkeypatch.setattr(terminus, "SOURCE_SHA256", hashlib.sha256(source).hexdigest())
    monkeypatch.setattr(
        terminus, "TEMPLATE_SHA256", {"timeout.txt": hashlib.sha256(b"original prompt").hexdigest()}
    )
    distribution = SimpleNamespace(version="0.23.0", locate_file=lambda _: root)
    monkeypatch.setattr(terminus.importlib.metadata, "distribution", lambda _: distribution)
    if change == "version":
        distribution.version = "unreviewed"
    elif change == "source":
        (root / "terminus_2.py").write_bytes(source + b"# changed\n")
    else:
        (root / "templates/timeout.txt").write_text("changed prompt")
    with pytest.raises(ValueError):
        terminus.export(tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


@pytest.mark.parametrize(
    "input_rate,output_rate", [("0", "0"), ("0.75", "4.5"), ("0.0000001", "0.0000002")]
)
def test_baseline_trace_cost_matches_controller_accounting(input_rate, output_rate):
    rates = RateCard(
        model="fixture",
        snapshot="fixture",
        input_usd_per_million=input_rate,
        output_usd_per_million=output_rate,
        max_input_tokens_per_request=1000,
        price_source_url="https://fixture.invalid/prices",
        price_as_of="2026-09-13",
    )
    usage = {
        "input_tokens": 101,
        "output_tokens": 23,
        "input_tokens_details": {"cached_tokens": 50},
    }
    contract = {"input_usd_per_million": input_rate, "output_usd_per_million": output_rate}
    assert response_cost(usage, contract) == rates.cost_nanodollars(101, 23) / 1_000_000_000


def test_local_environment_streams_and_timeout_cleanup(tmp_path):
    async def scenario():
        environment = LocalTaskEnvironment(tmp_path)
        result = await environment.exec(
            "printf observed; printf error >&2; exit 7", cwd=str(tmp_path)
        )
        assert (result.stdout, result.stderr, result.return_code) == ("observed", "error", 7)
        pid_file = tmp_path / "child.pid"
        with pytest.raises(TimeoutError):
            await environment.exec(
                "echo $$ > child.pid; sleep 60", cwd=str(tmp_path), timeout_sec=0.5
            )
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
        with pytest.raises(ValueError, match="unsupported"):
            await environment.exec("should not run", user="other")
        assert len((tmp_path / "commands.jsonl").read_text().splitlines()) == 2

    asyncio.run(scenario())
