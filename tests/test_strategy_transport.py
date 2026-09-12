from __future__ import annotations

import json
import shutil

import anyio
import pytest
from test_strategy_session import bundle

import research_harness.cli as cli_module
from research_harness.evaluation.fixture_transport import fixture_server
from research_harness.execution import DiscoverySettings
from research_harness.integrations.omnigent import (
    PRIMARY_SESSION_ENV,
    _read_case,
    case_strategy_session,
    prepare_case,
)
from research_harness.strategies.session import StrategySession, StrategySessionError
from research_harness.util import write_json

memory = pytest.importorskip("mcp.shared.memory")


def prepare(tmp_path):
    authored = bundle(tmp_path)
    case = prepare_case(
        tmp_path / "case",
        brief="Collect fixture",
        max_spend_usd=0.1,
        settings=DiscoverySettings(),
        strategy=authored,
    )
    return authored, case


def test_case_freezes_code_outside_the_native_agent_tree_and_binds_its_state(tmp_path):
    authored, case = prepare(tmp_path)
    metadata = _read_case(case)
    frozen = metadata["code_strategy"]
    assert frozen["sha256"] == authored.sha256
    assert frozen["manifest_path"] == str(case.parent / "code-strategy/strategy.json")
    assert not (case.parent / "agent/code-strategy").exists()
    session = case_strategy_session(case)
    assert session.bundle.sha256 == authored.sha256
    assert session.root == case.parent / "strategy"
    assert session.status()["events"] == []


@pytest.mark.parametrize("change", ["drop", "state_path", "source"])
def test_case_strategy_cannot_be_removed_rebound_or_changed_after_freezing(tmp_path, change):
    _, case = prepare(tmp_path)
    metadata = json.loads(case.read_bytes())
    if change == "drop":
        del metadata["code_strategy"]
    elif change == "state_path":
        metadata["code_strategy"]["output"] = str(tmp_path / "elsewhere")
    else:
        (case.parent / "code-strategy/strategy.py").write_text("changed")
    write_json(case, metadata)
    with pytest.raises(ValueError):
        _read_case(case)


def test_fixture_mcp_launcher_projects_and_reuses_an_operation_after_restart(tmp_path, fake_runner):
    authored, case = prepare(tmp_path)
    fixture = tmp_path / "sources.json"
    original = [
        {"url": "https://fixture.invalid/a", "title": "Alpha"},
        {"url": "https://fixture.invalid/b", "title": "Beta"},
    ]
    write_json(fixture, {"search_results": original, "responses": []})
    env = {PRIMARY_SESSION_ENV: "fixture-session", "RUNNER_SERVER_URL": "http://127.0.0.1:12345"}

    async def call(server):
        async with memory.create_connected_server_and_client_session(server) as client:
            await client.call_tool("begin_research", {"brief": "Collect fixture"})
            response = await client.call_tool(
                "search_sources", {"query": "fixture", "operation_id": "search-1"}
            )
            return response.structuredContent

    with fixture_server(case, fixture, env=env) as server:
        first = anyio.run(call, server)
    with fixture_server(case, fixture, env=env) as server:
        second = anyio.run(call, server)
    assert first == second and first["status"] == "ok"
    assert first["data"]["results"] == list(reversed(original))
    assert first["data"]["strategy_view"]["strategy_sha256"] == authored.sha256
    assert fake_runner.calls == 1
    receipts = json.loads((case.parent / "research/receipts.json").read_bytes())
    assert receipts[0]["payload"]["results"] == original


@pytest.mark.parametrize("replace_session", [False, True])
def test_missing_or_replaced_state_cannot_reexecute_a_completed_operation(
    tmp_path, fake_runner, replace_session
):
    authored, case = prepare(tmp_path)
    fixture = tmp_path / "sources.json"
    write_json(fixture, {"search_results": [{"url": "https://fixture.invalid/a"}], "responses": []})
    env = {PRIMARY_SESSION_ENV: "fixture-session", "RUNNER_SERVER_URL": "http://127.0.0.1:12345"}

    async def run(server):
        async with memory.create_connected_server_and_client_session(server) as client:
            await client.call_tool("begin_research", {"brief": "Collect fixture"})
            result = await client.call_tool(
                "search_sources", {"query": "fixture", "operation_id": "search-1"}
            )
            assert result.structuredContent["status"] == "ok"

    original = case_strategy_session(case)
    with fixture_server(case, fixture, env=env) as server:
        anyio.run(run, server)
    assert fake_runner.calls == 1
    shutil.rmtree(original.root)
    if replace_session:
        replacement = StrategySession(original.root, authored)
        assert replacement.session_id != original.session_id
        with pytest.raises(StrategySessionError, match="identity"):
            original.status()
    with pytest.raises((OSError, ValueError, StrategySessionError)):
        with fixture_server(case, fixture, env=env) as server:
            anyio.run(run, server)
    assert fake_runner.calls == 1
    if not replace_session:
        assert not original.root.exists()


def test_discover_cli_passes_the_frozen_session_without_a_model_call(tmp_path, monkeypatch, capsys):
    authored = bundle(tmp_path)
    calls = []

    def discover(*args, **kwargs):
        calls.append(kwargs)
        return {"fixture": True}

    monkeypatch.setattr(cli_module, "discover", discover)
    assert (
        cli_module.main(
            [
                "discover",
                "Collect fixture",
                "--out",
                str(tmp_path / "discovery"),
                "--search-provider",
                "mcp",
                "--strategy",
                str(authored.manifest_path),
            ]
        )
        == 0
    )
    session = calls[0]["strategy"]
    assert isinstance(session, StrategySession)
    assert session.bundle.sha256 == authored.sha256
    assert json.loads(capsys.readouterr().out) == {"fixture": True}
    assert cli_module.main(["strategy", "status", "--out", str(session.root)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    assert cli_module.main(["strategy", "recover", "--out", str(session.root)]) == 0
    assert json.loads(capsys.readouterr().out)["replayed"] is False
