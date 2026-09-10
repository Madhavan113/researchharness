from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

import research_harness.strategies.session as session_module
from research_harness.strategies.config import StrategyBundle
from research_harness.strategies.sandbox import SandboxConfig
from research_harness.strategies.session import StrategySession, StrategySessionError
from research_harness.util import canonical_json, digest, write_json

IMAGE = "python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285"
CODE = """def apply(event):
    items = event['payload']['items']
    return {'decision': {'order': [item['id'] for item in reversed(items)]},
            'state': {'calls': event['state'].get('calls', 0) + 1}}
"""


def bundle(tmp_path, **config):
    directory = tmp_path / "authored"
    directory.mkdir()
    (directory / "strategy.py").write_text(CODE)
    write_json(
        directory / "strategy.json",
        {
            "schema_version": 1,
            "source": "strategy.py",
            "source_sha256": digest(CODE),
            "sandbox": SandboxConfig(image=IMAGE).model_dump(mode="json"),
            **config,
        },
    )
    return StrategyBundle.load(directory / "strategy.json")


def receipt(operation_id="search-1"):
    return {
        "operation_id": operation_id,
        "discovery_id": "case",
        "receipt_id": "receipt-" + operation_id,
        "results": [{"url": "https://fixture.invalid/a"}, {"url": "https://fixture.invalid/b"}],
    }


@pytest.fixture
def fake_runner(monkeypatch):
    """Authored evidence for host journal tests; never executes candidate Python."""

    class FakeRunner:
        calls = 0
        invalid = False

        def __init__(self, config):
            self.config = config

        def execute(self, source, event, output):
            type(self).calls += 1
            inputs = output / "input"
            inputs.mkdir(parents=True)
            (inputs / "strategy.py").write_bytes(source.read_bytes())
            (inputs / "request.json").write_text(canonical_json(event))
            (inputs / "worker.py").write_text("# authored fixture worker evidence\n")
            decision = {"order": [item["id"] for item in reversed(event["payload"]["items"])]}
            if self.invalid:
                decision["order"] = ["invented-result"]
            raw = {"decision": decision, "state": {"calls": event["state"].get("calls", 0) + 1}}
            write_json(output / "decision.json", raw)
            (output / "stdout.txt").write_text(canonical_json(raw))
            (output / "stderr.txt").write_text("")
            write_json(
                output / "execution.json",
                {
                    "status": "completed",
                    "cleanup": "removed",
                    "configuration": self.config.model_dump(mode="json"),
                    "source_sha256": digest(source.read_bytes()),
                    "request_sha256": digest(canonical_json(event)),
                    "output_truncated": False,
                    "output_collection_complete": True,
                    "artifact_hashes": {
                        p.relative_to(output).as_posix(): digest(p.read_bytes())
                        for p in sorted(output.rglob("*"))
                        if p.is_file()
                    },
                },
            )
            return raw

        def recover(self, output):
            return json.loads((output / "execution.json").read_bytes())

    monkeypatch.setattr(session_module, "DockerStrategyRunner", FakeRunner)
    return FakeRunner


def test_restart_and_exact_retry_reuse_the_recorded_decision_and_state(tmp_path, fake_runner):
    original = bundle(tmp_path)
    session = StrategySession(tmp_path / "session", original)
    result = session.project("search_sources", receipt())
    assert result["results"] == list(reversed(receipt()["results"]))
    reopened = StrategySession(session.root, original)
    assert reopened.project("search_sources", receipt()) == result
    assert fake_runner.calls == 1
    assert json.loads((session.root / "session.json").read_bytes())["state"] == {"calls": 1}
    proof = reopened.event_record("observation:case:search-1")
    assert proof["result"]["state"] == {"calls": 1}
    assert proof["input"]["payload"]["observation"] == receipt()
    assert "execution/input/strategy.py" in proof["files"]
    reopened.project("search_sources", receipt("search-2"))
    assert fake_runner.calls == 2
    reopened.assert_ready()


@pytest.mark.parametrize("boundary", ["before_journal_commit", "after_journal_commit"])
def test_commit_failure_recovers_completed_execution_without_replay(
    tmp_path, monkeypatch, fake_runner, boundary
):
    authored = bundle(tmp_path)
    session = StrategySession(tmp_path / "session", authored)
    original = session_module._save

    def fail(path, value):
        if (
            path == session.root / "session.json"
            and value["events"]
            and value["events"][-1]["status"] == "completed"
        ):
            if boundary == "after_journal_commit":
                original(path, value)
            raise OSError("journal write interrupted")
        original(path, value)

    monkeypatch.setattr(session_module, "_save", fail)
    with pytest.raises(OSError, match="interrupted"):
        session.project("search_sources", receipt())
    monkeypatch.setattr(session_module, "_save", original)
    reopened = StrategySession(session.root, authored)
    assert reopened.recover()["replayed"] is False
    reopened.assert_ready()
    assert reopened.project("search_sources", receipt())["results"] == list(
        reversed(receipt()["results"])
    )
    assert fake_runner.calls == 1


def test_invalid_candidate_is_a_sticky_failure_not_a_baseline_result(tmp_path, fake_runner):
    session = StrategySession(tmp_path / "session", bundle(tmp_path))
    fake_runner.invalid = True
    with pytest.raises(StrategySessionError, match="unknown observation"):
        session.project("search_sources", receipt())
    with pytest.raises(StrategySessionError):
        session.assert_ready()
    assert session.recover()["status"] == "failed"
    with pytest.raises(StrategySessionError):
        session.project("search_sources", receipt("search-2"))
    assert fake_runner.calls == 1


def test_event_budget_failure_cannot_be_hidden_by_a_valid_earlier_decision(tmp_path, fake_runner):
    session = StrategySession(tmp_path / "session", bundle(tmp_path, max_events=1))
    session.project("search_sources", receipt())
    with pytest.raises(StrategySessionError, match="budget"):
        session.project("search_sources", receipt("search-2"))
    with pytest.raises(StrategySessionError):
        session.assert_ready()
    assert fake_runner.calls == 1


def test_conflicting_operation_cannot_reuse_a_projection(tmp_path, fake_runner):
    session = StrategySession(tmp_path / "session", bundle(tmp_path))
    session.project("search_sources", receipt())
    changed = receipt()
    changed["results"] = []
    with pytest.raises(StrategySessionError, match="different inputs"):
        session.project("search_sources", changed)
    assert fake_runner.calls == 1


@pytest.mark.parametrize("artifact", ["input.json", "result.json", "execution/stdout.txt"])
def test_changed_archived_event_cannot_be_reused(tmp_path, fake_runner, artifact):
    session = StrategySession(tmp_path / "session", bundle(tmp_path))
    session.project("search_sources", receipt())
    event = Path(session.event_record("observation:case:search-1")["directory"])
    (event / artifact).write_text("{}")
    with pytest.raises((KeyError, ValueError)):
        session.assert_ready()


def test_active_session_recovery_cannot_overlap_execution(tmp_path):
    session = StrategySession(tmp_path / "session", bundle(tmp_path))
    with FileLock(str(session.root) + ".lock"):
        with pytest.raises(Timeout):
            session.recover()


def test_actual_strategy_state_and_retry_through_docker(tmp_path):
    if not os.environ.get("RH_TEST_STRATEGY_IMAGE"):
        pytest.skip("Set RH_TEST_STRATEGY_IMAGE for actual isolated strategy-session acceptance")
    session = StrategySession(tmp_path / "session", bundle(tmp_path))
    result = session.project("search_sources", receipt())
    assert result["results"] == list(reversed(receipt()["results"]))
    assert session.project("search_sources", receipt()) == result
    proof = session.event_record("observation:case:search-1")
    assert proof["result"]["state"] == {"calls": 1}
    assert len(list((session.root / "events").iterdir())) == 1
