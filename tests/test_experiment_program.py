from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from research_harness.experiments import program


def test_program_is_validated_without_importing_and_rejects_links(tmp_path):
    source = tmp_path / "candidate.py"
    marker = tmp_path / "executed"
    source.write_text(f"open({str(marker)!r}, 'w').write('should never run on host')")
    assert program.read_program(source) == source.read_bytes()
    assert not marker.exists()
    link = tmp_path / "link.py"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="regular"):
        program.read_program(link)
    source.write_text("invalid python ???")
    with pytest.raises(SyntaxError):
        program.read_program(source)


class LocalFixtureEnvironment:
    """Trusted test child only. Production argv comes from inspected ExperimentDocker."""

    context_id = "fixture-trial"
    session_id = "fixture-container"

    def __init__(self, path):
        self.path = path
        self.stopped = []

    def program_argv(self, _remote):
        return [sys.executable, "-I", "-u", str(self.path)]

    async def upload_file(self, source, _remote):
        self.path.write_bytes(source.read_bytes())

    async def stop_service(self, service):
        self.stopped.append(service)


def test_full_program_controls_repeated_model_and_tool_loop(tmp_path, monkeypatch):
    candidate = Path(__file__).parents[1] / "examples/experiments/programs/python_loop.py"
    marker = tmp_path / "submitted.txt"
    calls = []

    async def model(request, *, model):
        calls.append(request)
        assert model == "fixed-model"
        if len(calls) == 1:
            command = f"{sys.executable} -c \"open({str(marker)!r}, 'w').write('experiment')\""
            output = [
                {
                    "type": "function_call",
                    "name": "shell",
                    "call_id": "call-1",
                    "arguments": json.dumps({"command": command}),
                }
            ]
        else:
            assert request["input"][-1]["type"] == "function_call_output"
            assert json.loads(request["input"][-1]["output"])["exit_code"] == 0
            output = [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]
        return {"status": "completed", "model": model, "output": output}

    monkeypatch.setattr(program, "model_response", model)
    environment = LocalFixtureEnvironment(tmp_path / "child.py")
    output = tmp_path / "evidence"
    contract = {
        "input_admission_tokens": 10000,
        "max_output_tokens": 1000,
        "input_usd_per_million": "0",
        "output_usd_per_million": "0",
    }
    result = asyncio.run(
        program.run_program(
            environment,
            candidate,
            "write a file",
            "fixed-model",
            output,
            timeout=10,
            model_contract=contract,
        )
    )
    assert result["status"] == "completed"
    assert result["model_calls"] == 2
    assert marker.read_text() == "experiment"
    assert environment.stopped == ["main"]
    trace = [json.loads(line) for line in (output / "protocol.jsonl").read_text().splitlines()]
    assert len(trace) == 5
    assert trace[0]["payload"]["instruction"] == "write a file"
    assert trace[0]["payload"]["model_contract"] == contract
    assert "shell:" in (output / "stderr.txt").read_text()


@pytest.mark.parametrize(
    "source",
    [
        "import time; time.sleep(60)",
        "import sys; sys.exit(3)",
        "print('not a JSON request', flush=True)",
        "import sys; sys.stderr.write('x' * (1024 * 1024 + 1)); sys.stderr.flush()",
        "print('x' * (1024 * 1024 + 1), flush=True)",
    ],
)
def test_program_failures_are_retained_and_container_stopped(tmp_path, source):
    candidate = tmp_path / "candidate.py"
    candidate.write_text(source)
    output = tmp_path / "evidence"
    environment = LocalFixtureEnvironment(tmp_path / "child.py")
    with pytest.raises((TimeoutError, ExceptionGroup)):
        asyncio.run(
            program.run_program(environment, candidate, "fixture", "fixed-model", output, timeout=1)
        )
    record = json.loads((output / "program.json").read_bytes())
    assert record["status"] == "error"
    assert record["container_stopped"] is True
    assert environment.stopped == ["main"]
    assert (output / "candidate.py").read_bytes() == candidate.read_bytes()
    assert (output / "stderr.txt").stat().st_size <= program.STDERR_LIMIT


def test_program_model_transport_fixes_identity_and_rejects_extra_controls(monkeypatch):
    monkeypatch.setenv("RH_MODEL_GATEWAY_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("RH_MODEL_GATEWAY_KEY", "controller-only-token")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"model": "fixed-model", "output": []})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        program.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(**kwargs, transport=httpx.MockTransport(respond)),
    )
    asyncio.run(program.model_response({"input": "task"}, model="fixed-model"))
    assert json.loads(requests[0].content) == {
        "input": "task",
        "model": "fixed-model",
        "stream": False,
        "store": False,
    }
    assert requests[0].headers["Authorization"] == "Bearer controller-only-token"
    for extra in ("model", "stream", "previous_response_id", "max_output_tokens", "url"):
        with pytest.raises(ValueError, match="documented"):
            asyncio.run(
                program.model_response({"input": "task", extra: "override"}, model="fixed-model")
            )
    assert len(requests) == 1


def test_cleanup_failure_prevents_completed_program(tmp_path):
    class FailedStop(LocalFixtureEnvironment):
        async def stop_service(self, service):
            raise RuntimeError("Docker unreachable")

    candidate = tmp_path / "candidate.py"
    candidate.write_text("import sys; sys.stdin.readline()")
    with pytest.raises(RuntimeError, match="Docker unreachable"):
        asyncio.run(
            program.run_program(
                FailedStop(tmp_path / "child.py"),
                candidate,
                "fixture",
                "model",
                tmp_path / "evidence",
                timeout=10,
            )
        )
    record = json.loads((tmp_path / "evidence/program.json").read_bytes())
    assert record["status"] == "error"
    assert record["container_stopped"] is False
