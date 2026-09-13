import json
import os
import subprocess
from pathlib import Path

import pytest

from research_harness.experiments.bundle import build_bundle


def test_pinned_omnigent_accepts_bundle_without_host_os_or_arbitrary_spawn(tmp_path):
    python = os.environ.get("RH_TEST_OMNIGENT_PYTHON")
    if not python:
        if os.environ.get("RH_TEST_REQUIRE_RUNTIME") == "1":
            pytest.fail("RH_TEST_OMNIGENT_PYTHON is required for experiment bundle validation")
        pytest.skip("Set RH_TEST_OMNIGENT_PYTHON for pinned experiment bundle validation")
    bundle = build_bundle(
        tmp_path / "bundle",
        name="experiment-fixture",
        model="gpt-5.4-mini",
    )
    script = """
import json, sys
from pathlib import Path
from omnigent.spec import load
from omnigent.policies.builtins.cel import cel_policy
from research_harness.experiments.bundle import tool_policy
spec = load(Path(sys.argv[1]), expand_env=False)
assert spec.executor.type == 'omnigent'
assert len(spec.sub_agents) == 1
worker = spec.sub_agents[0]
assert spec.tools.agents == ['worker']
assert worker.name == 'worker'
assert worker.max_sessions == 1
for agent in [spec, worker]:
    assert agent.os_env is None
    assert not agent.spawn
    assert not agent.tools.builtins
assert not spec.local_tools and not spec.mcp_servers and not worker.mcp_servers
assert len(worker.local_tools) == 1
connector = worker.local_tools[0]
assert connector.name == 'workspace_execute'
assert connector.path == 'research_harness.experiments.worker_tools.execute'
policy = cel_policy(**tool_policy()['factory_params'])
for name in ['workspace_execute', 'sys_session_send', 'sys_read_inbox']:
    assert policy({'type': 'tool_call', 'data': {'name': name}})['result'] == 'ALLOW'
for name in ['sys_os_shell', 'browser_navigate', 'sys_agent_download',
             'sys_scheduled_task_list', 'sys_agent_list', 'sys_call_async', 'sys_add_policy']:
    assert policy({'type': 'tool_call', 'data': {'name': name}})['result'] == 'DENY'
print(json.dumps({'validated': True, 'worker': worker.name}))
"""
    result = subprocess.run(
        [python, "-c", script, str(bundle)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["validated"] is True
