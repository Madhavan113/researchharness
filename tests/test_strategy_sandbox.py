from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from research_harness.strategies.sandbox import (
    DockerStrategyRunner,
    SandboxConfig,
    StrategyExecutionError,
    _json_object,
)
from research_harness.util import digest

IMAGE = "python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285"


def source(tmp_path, code):
    path = tmp_path / "strategy.py"
    path.write_text(code)
    return path


def actual_runner(**settings):
    image = os.environ.get("RH_TEST_STRATEGY_IMAGE")
    if not image:
        pytest.skip("Set RH_TEST_STRATEGY_IMAGE to a locally available pinned Python image")
    return DockerStrategyRunner(SandboxConfig(image=image, **settings))


def report(output):
    return json.loads((output / "execution.json").read_bytes())


@pytest.mark.parametrize("raw", [b"[]", b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b"{}{}"])
def test_untrusted_protocol_rejects_ambiguous_or_nonfinite_values(raw):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        _json_object(raw)


@pytest.mark.parametrize("image", ["python:3.13-slim", "python:latest", "other@sha256:" + "a" * 64])
def test_strategy_runtime_requires_immutable_python_image(image):
    with pytest.raises(ValueError):
        SandboxConfig(image=image)


def test_candidate_is_never_imported_on_host_even_when_docker_fails(tmp_path):
    sentinel = tmp_path / "host-executed"
    path = source(tmp_path, f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n")
    runner = DockerStrategyRunner(SandboxConfig(image=IMAGE), docker=Path("/usr/bin/false"))
    output = tmp_path / "attempt"
    with pytest.raises(StrategyExecutionError) as error:
        runner.execute(path, {}, output)
    assert error.value.artifacts == output and not sentinel.exists()
    assert report(output)["cleanup"] == "not_created"
    assert report(output)["status"] == "failed"
    with pytest.raises(FileExistsError):
        runner.execute(path, {}, output)


def test_source_and_input_limits_are_checked_before_execution(tmp_path):
    runner = DockerStrategyRunner(
        SandboxConfig(image=IMAGE, max_source_bytes=1024), docker=Path("/no/docker")
    )
    path = source(tmp_path, "#" * 1025)
    with pytest.raises(ValueError, match="exceeds"):
        runner.execute(path, {}, tmp_path / "large")
    assert not (tmp_path / "large").exists()
    path.write_text("def apply(event): return event\n")
    link = tmp_path / "linked.py"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        runner.execute(link, {}, tmp_path / "linked")
    with pytest.raises(ValueError):
        runner.execute(path, {"invalid": float("nan")}, tmp_path / "nan")
    assert not (tmp_path / "nan").exists()


def test_recovery_cannot_interrupt_an_active_execution(tmp_path):
    output = tmp_path / "active"
    runner = DockerStrategyRunner(SandboxConfig(image=IMAGE), docker=Path("/no/docker"))
    with FileLock(str(output) + ".lock"):
        with pytest.raises(Timeout):
            runner.recover(output)


def test_actual_candidate_projects_data_and_preserves_complete_artifacts(tmp_path):
    runner = actual_runner()
    path = Path(__file__).resolve().parents[1] / "examples/strategies/source_order.py"
    event = {
        "sources": [
            {"id": "a", "title": "Alpha", "url": "https://fixture.invalid/a", "priority": 0},
            {"id": "b", "title": "Beta", "url": "https://fixture.invalid/b", "priority": 1},
        ]
    }
    output = tmp_path / "run"
    result = runner.execute(path, event, output)
    assert result["ranked_source_ids"] == ["b", "a"]
    assert result["observations"]["b"] == "Beta: https://fixture.invalid/b"
    assert event["sources"][0]["id"] == "a"
    saved = report(output)
    assert saved["status"] == "completed" and saved["cleanup"] == "removed"
    assert saved["source_sha256"] == digest(path.read_bytes())
    for name, expected in saved["artifact_hashes"].items():
        assert digest((output / name).read_bytes()) == expected


def test_actual_candidate_cannot_access_host_secrets_files_or_network(tmp_path, monkeypatch):
    runner = actual_runner()
    heldout = tmp_path / "private-heldout.txt"
    heldout.write_text("PRIVATE_HELDOUT_CANARY")
    monkeypatch.setenv("OPENAI_API_KEY", "HOST_CREDENTIAL_CANARY")
    code = """import os, pathlib, socket

def apply(event):
    result = {'host_env_absent': 'OPENAI_API_KEY' not in os.environ,
              'host_file_absent': not pathlib.Path(event['private']).exists(),
              'nonroot': os.getuid() != 0, 'docker_socket_absent': not pathlib.Path('/var/run/docker.sock').exists()}
    for name, path in [('input_readonly', '/input/strategy.py'), ('root_readonly', '/etc/strategy-test')]:
        try:
            pathlib.Path(path).write_text('changed')
            result[name] = False
        except OSError:
            result[name] = True
    try:
        socket.create_connection(('1.1.1.1', 53), timeout=0.3).close()
        result['network_denied'] = False
    except OSError:
        result['network_denied'] = True
    return result
"""
    output = tmp_path / "isolation"
    result = runner.execute(source(tmp_path, code), {"private": str(heldout)}, output)
    assert result and all(result.values())
    assert heldout.read_text() == "PRIVATE_HELDOUT_CANARY"
    assert "PRIVATE_HELDOUT_CANARY" not in (output / "stdout.txt").read_text()
    assert "HOST_CREDENTIAL_CANARY" not in (output / "stdout.txt").read_text()
    assert report(output)["cleanup"] == "removed"


@pytest.mark.parametrize(
    "code",
    [
        'def apply(event): raise RuntimeError("candidate failure")',
        "def apply(event): return []",
        'def apply(event): return {"bad": float("nan")}',
        'import os\ndef apply(event): os.write(1, b"{}{}"); os._exit(0)',
    ],
)
def test_actual_invalid_candidates_fail_without_fallback(tmp_path, code):
    runner = actual_runner()
    output = tmp_path / "invalid"
    with pytest.raises(StrategyExecutionError):
        runner.execute(source(tmp_path, code), {}, output)
    saved = report(output)
    assert saved["status"] == "failed" and saved["cleanup"] == "removed"
    assert not (output / "decision.json").exists()


@pytest.mark.parametrize(
    "code, expected",
    [
        ("def apply(event):\n while True: pass", "wall-clock"),
        ('def apply(event):\n while True: print("x" * 65536)', "output"),
        ('def apply(event): return {"large": str(bytearray(512 * 1024 * 1024))}', "status"),
    ],
)
def test_actual_runaway_candidates_are_bounded_and_removed(tmp_path, code, expected):
    runner = actual_runner(timeout_seconds=1, max_output_bytes=1024, memory_mb=64)
    output = tmp_path / "bounded"
    with pytest.raises(StrategyExecutionError, match=expected):
        runner.execute(source(tmp_path, code), {}, output)
    saved = report(output)
    assert saved["status"] == "failed" and saved["cleanup"] == "removed"
    assert (output / "stdout.txt").stat().st_size <= 1024
    assert (output / "stderr.txt").stat().st_size <= 1024
    assert runner.recover(output)["recovery"]["replayed"] is False


def test_actual_host_interruption_preserves_attempt_and_removes_container(tmp_path):
    import signal
    import subprocess
    import sys
    import time

    runner = actual_runner(timeout_seconds=30)
    path = source(tmp_path, "def apply(event):\n while True: pass\n")
    config = tmp_path / "config.json"
    config.write_text(runner.config.model_dump_json())
    request = tmp_path / "input.json"
    request.write_text("{}")
    output = tmp_path / "interrupted"
    with (tmp_path / "host.log").open("wb") as log:
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "research_harness.strategies.sandbox",
                "--config",
                str(config),
                "execute",
                str(path),
                "--input",
                str(request),
                "--out",
                str(output),
            ],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 10
            while not (output / "execution.json").exists() or report(output)["status"] != "running":
                assert child.poll() is None, (tmp_path / "host.log").read_text()
                if time.monotonic() >= deadline:
                    pytest.fail("Strategy host did not enter running state")
                time.sleep(0.02)
            child.send_signal(signal.SIGINT)
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
                runner.recover(output)
    saved = report(output)
    assert saved["status"] == "interrupted" and saved["cleanup"] == "removed"
    with pytest.raises(FileExistsError):
        runner.execute(path, {}, output)
