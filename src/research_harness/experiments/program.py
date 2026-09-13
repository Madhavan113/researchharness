"""A full Python agent program, executed in the task container, never imported here.

stdin starts with the task. Each subsequent stdout line is a model request;
the controller writes one Responses JSON object back to stdin. The candidate
owns the loop and local tools. Only the host gateway knows provider credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from research_harness.util import canonical_json, digest, timestamp, write_json

REQUEST_LIMIT = 1024 * 1024
RESPONSE_LIMIT = 8 * 1024 * 1024
TRACE_LIMIT = 64 * 1024 * 1024
STDERR_LIMIT = 1024 * 1024
REQUEST_FIELDS = {"input", "instructions", "tools", "tool_choice", "text"}


def read_program(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Candidate program must be a regular Python file")
    if path.stat().st_size > REQUEST_LIMIT:
        raise ValueError("Candidate program exceeds 1 MiB")
    source = path.read_bytes()
    # Compile validates syntax without executing or importing candidate code.
    compile(source.decode("utf-8"), "candidate.py", "exec")
    return source


async def model_response(request: dict, *, model: str) -> dict:
    """Forward only explicit context/tool declarations to the fixed host gateway."""
    if not isinstance(request, dict) or "input" not in request or set(request) - REQUEST_FIELDS:
        raise ValueError("Program requests require input and only documented context/tool fields")
    endpoint = os.environ["RH_MODEL_GATEWAY_URL"].rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("Program model access requires the controller's loopback gateway")
    async with httpx.AsyncClient(trust_env=False, timeout=None) as client:
        async with client.stream(
            "POST",
            endpoint + "/responses",
            headers={"Authorization": "Bearer " + os.environ["RH_MODEL_GATEWAY_KEY"]},
            json={**request, "model": model, "stream": False, "store": False},
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > RESPONSE_LIMIT:
                    raise ValueError("Program model response exceeds 8 MiB")
                body.extend(chunk)
    result = json.loads(body)
    if not isinstance(result, dict) or result.get("model") != model:
        raise ValueError("Program received an invalid or different model response")
    return result


async def exchange(process, instruction: str, model: str, output: Path, call_model) -> int:
    """Bound pipe buffering and retain observed traffic outside candidate access."""
    trace_bytes = 0
    calls = 0
    initial = {"protocol": 1, "instruction": instruction, "model": model}

    def record(direction, payload):
        nonlocal trace_bytes
        line = (canonical_json({"direction": direction, "payload": payload}) + "\n").encode()
        trace_bytes += len(line)
        if trace_bytes > TRACE_LIMIT:
            raise ValueError("Program protocol trace exceeds 64 MiB")
        with (output / "protocol.jsonl").open("ab") as stream:
            stream.write(line)

    async def send(payload):
        record("controller_to_program", payload)
        process.stdin.write((canonical_json(payload) + "\n").encode())
        await process.stdin.drain()

    async def read_stderr():
        size = 0
        with (output / "stderr.txt").open("wb") as stream:
            while chunk := await process.stderr.read(65536):
                stream.write(chunk[: max(0, STDERR_LIMIT - size)])
                size += len(chunk)
                if size > STDERR_LIMIT:
                    raise ValueError("Program stderr exceeds 1 MiB")

    async def dialog():
        nonlocal calls
        await send(initial)
        while raw := await process.stdout.readline():
            if len(raw) > REQUEST_LIMIT or not raw.endswith(b"\n"):
                raise ValueError("Program request must be a newline-terminated JSON frame <=1 MiB")
            # Retain even malformed frames as observations before parsing them.
            record("program_to_controller", {"raw": raw.decode(errors="replace")})
            request = json.loads(raw)
            calls += 1
            await send(await call_model(request))
        process.stdin.close()
        await process.wait()
        if process.returncode != 0:
            raise RuntimeError(f"Candidate program exited with code {process.returncode}")

    async with asyncio.TaskGroup() as group:
        group.create_task(read_stderr())
        group.create_task(dialog())
    return calls


async def run_program(
    environment, candidate: Path, instruction: str, model: str, output: Path, *, timeout: int
) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    source = read_program(candidate)
    frozen = output / "candidate.py"
    frozen.write_bytes(source)
    remote = f"/tmp/rh-program-{uuid4().hex}.py"
    argv = environment.program_argv(remote)
    report = {
        "status": "starting",
        "source_sha256": digest(source),
        "argv": argv,
        "started_at": timestamp(),
        "context_id": str(environment.context_id),
        "environment_session_id": environment.session_id,
    }
    write_json(output / "program.json", report)
    process = None
    try:
        async with asyncio.timeout(timeout):
            await environment.upload_file(frozen, remote)
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=REQUEST_LIMIT,
            )
            report.update(status="running", pid=process.pid)
            write_json(output / "program.json", report)

            async def call_model(request):
                return await model_response(request, model=model)

            report["model_calls"] = await exchange(process, instruction, model, output, call_model)
            report.update(status="completed", exit_code=process.returncode)
    except BaseException as exc:
        report.update(status="error", error=repr(exc))
        raise
    finally:
        # Stop all processes in the candidate container before Harbor copies the
        # submission to the separate verifier, including detached child processes.
        try:
            await asyncio.wait_for(environment.stop_service("main"), timeout=30)
            report["container_stopped"] = True
        except BaseException as exc:
            report.update(status="error", cleanup_error=repr(exc), container_stopped=False)
            raise
        finally:
            try:
                if process is not None and process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await asyncio.wait_for(process.wait(), timeout=5)
            finally:
                report["exit_code"] = process.returncode if process is not None else None
                report["finished_at"] = timestamp()
                write_json(output / "program.json", report)
    return report
