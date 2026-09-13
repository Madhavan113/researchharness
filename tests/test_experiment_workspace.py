from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx

from research_harness.experiments.workspace import OUTPUT_LIMIT, WorkspaceBridge


class Environment:
    context_id = "fixture-trial"
    session_id = "fixture-container"

    def __init__(self):
        self.calls = []

    async def exec(self, command, *, timeout_sec):
        self.calls.append((command, timeout_sec))
        if command == "runtime-error":
            raise RuntimeError("Container connection lost")
        return SimpleNamespace(stdout="x" * (OUTPUT_LIMIT + 9), stderr="", return_code=0)


def test_workspace_credential_validation_limits_and_no_replay(tmp_path):
    async def scenario():
        environment = Environment()
        bridge = WorkspaceBridge(environment, tmp_path / "commands", max_commands=1, timeout=60)
        request = {"id": str(uuid4()), "command": "task command", "timeout": 5}
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                denied = await client.post(bridge.url, json=request)
                assert denied.status_code == 403
                assert environment.calls == []
                client.headers["Authorization"] = "Bearer " + bridge.key
                malformed = await client.post(
                    bridge.url, json={**request, "host_path": "/tmp/host"}
                )
                assert malformed.status_code == 400
                assert environment.calls == []
                first = await client.post(bridge.url, json=request)
                assert first.status_code == 200
                result = first.json()
                assert result["status"] == "completed"
                assert result["context_id"] == environment.context_id
                assert len(result["stdout"]) == OUTPUT_LIMIT
                assert result["stdout_bytes"] == OUTPUT_LIMIT + 9
                assert result["stdout_truncated"] is True
                repeated = await client.post(bridge.url, json=request)
                assert repeated.json() == result
                assert len(environment.calls) == 1
                changed = await client.post(bridge.url, json={**request, "command": "different"})
                assert changed.status_code == 400
                excess = await client.post(bridge.url, json={**request, "id": str(uuid4())})
                assert excess.status_code == 400
                assert len(environment.calls) == 1
                receipt = json.loads((bridge.output / f"{request['id']}.json").read_bytes())
                assert receipt == result
        finally:
            await bridge.close()

    asyncio.run(scenario())


def test_uncertain_workspace_error_remains_visible_and_is_not_replayed(tmp_path):
    async def scenario():
        environment = Environment()
        bridge = WorkspaceBridge(environment, tmp_path / "commands", max_commands=2, timeout=60)
        try:
            async with httpx.AsyncClient(
                headers={"Authorization": "Bearer " + bridge.key}, trust_env=False
            ) as client:
                request = {"id": str(uuid4()), "command": "runtime-error", "timeout": 2}
                failed = await client.post(bridge.url, json=request)
                assert failed.json()["status"] == "error"
                assert failed.json()["execution_outcome"] == "unresolved"
                assert "Container connection lost" in failed.json()["error"]
                retried = await client.post(bridge.url, json=request)
                assert retried.json() == failed.json()
                assert len(environment.calls) == 1
                bridge.deadline = 0
                expired = await client.post(bridge.url, json={**request, "id": str(uuid4())})
                assert expired.status_code == 400
                assert len(environment.calls) == 1
        finally:
            await bridge.close()

    asyncio.run(scenario())


def test_closing_workspace_drains_admitted_execution(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        finish = asyncio.Event()

        class SlowEnvironment(Environment):
            async def exec(self, command, *, timeout_sec):
                entered.set()
                await finish.wait()
                return SimpleNamespace(stdout="finished", stderr="", return_code=0)

        bridge = WorkspaceBridge(
            SlowEnvironment(), tmp_path / "commands", max_commands=2, timeout=60
        )
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + bridge.key}, trust_env=False
        ) as client:
            sending = asyncio.create_task(
                client.post(bridge.url, json={"id": str(uuid4()), "command": "work", "timeout": 5})
            )
            await asyncio.wait_for(entered.wait(), 2)
            closing = asyncio.create_task(bridge.close())
            await asyncio.sleep(0.1)
            assert not closing.done()
            finish.set()
            assert (await sending).json()["stdout"] == "finished"
            await closing
            assert all(
                json.loads(path.read_bytes())["status"] == "completed"
                for path in bridge.output.glob("*.json")
            )

    asyncio.run(scenario())


def test_program_endpoint_runs_frozen_program_once_and_disallows_commands(tmp_path):
    async def scenario():
        calls = []

        async def program(remaining):
            calls.append(remaining)
            return {"status": "completed", "container_stopped": True}

        bridge = WorkspaceBridge(
            Environment(), tmp_path / "commands", max_commands=20, timeout=60, program=program
        )
        try:
            async with httpx.AsyncClient(
                trust_env=False,
                headers={
                    "Authorization": "Bearer " + bridge.key,
                },
            ) as client:
                request = {"id": str(uuid4())}
                denied = await client.post(
                    bridge.url, json={**request, "command": "host code", "timeout": 5}
                )
                assert denied.status_code == 400
                assert calls == []
                first = await client.post(bridge.url, json=request)
                assert first.json()["program"]["status"] == "completed"
                repeated = await client.post(bridge.url, json=request)
                assert repeated.json() == first.json()
                new_id = await client.post(bridge.url, json={"id": str(uuid4())})
                assert new_id.status_code == 400
                assert len(calls) == 1
        finally:
            await bridge.close()

    asyncio.run(scenario())
