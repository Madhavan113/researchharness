"""Trusted Harbor adapter that delegates task execution through Omnigent.

Only the trusted adapter runs on the host. Model-authored shell/code executes
through WorkspaceBridge in ExperimentDocker, which has no host mounts.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import signal
import sys
from pathlib import Path

from harbor.agents.base import BaseAgent

from research_harness.experiments.bundle import build_bundle
from research_harness.experiments.harbor_environment import ExperimentDocker
from research_harness.experiments.workspace import WorkspaceBridge
from research_harness.util import write_json


class OmnigentAgent(BaseAgent):
    def __init__(
        self,
        *args,
        controller_root: str,
        omnigent_python: str,
        max_commands: int = 20,
        execution_timeout: int = 300,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.controller_root = Path(controller_root).resolve()
        self.omnigent_python = omnigent_python
        self.max_commands = max_commands
        self.execution_timeout = execution_timeout
        if self.controller_root.is_relative_to(self.logs_dir.resolve()):
            raise ValueError("Controller records must be outside candidate agent logs")

    @staticmethod
    def name():
        return "research-omnigent"

    def version(self):
        return "experiment-adapter-1"

    async def setup(self, environment):
        if not isinstance(environment, ExperimentDocker):
            raise ValueError("Omnigent experiment workers require ExperimentDocker")

    async def run(self, instruction, environment, context):
        if self.context_id is None:
            raise ValueError("A Harbor trial identity is required")
        output = self.controller_root / str(self.context_id)
        output.mkdir(parents=True, exist_ok=False)
        write_json(
            output / "dependencies.json",
            {
                "python": sys.version,
                "executable": sys.executable,
                "distributions": {
                    d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
                },
            },
        )
        bridge = WorkspaceBridge(
            environment,
            output / "commands",
            max_commands=self.max_commands,
            timeout=self.execution_timeout,
        )
        process = None
        try:
            connection = output / "workspace-connection.json"
            write_json(connection, {"url": bridge.url, "token": bridge.key})
            bundle = build_bundle(
                output / "bundle",
                name=f"experiment-{self.context_id}",
                model=self.model_name,
            )
            config = {
                "bundle": str(bundle),
                "output": str(output / "runtime"),
                "instruction": instruction,
                "timeout": self.execution_timeout,
            }
            write_json(output / "driver.json", config)
            allowed = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
            env = {key: value for key, value in os.environ.items() if key in allowed}
            env.update(
                PYTHONPATH=str(Path(__file__).resolve().parents[2]),
                PYTHONDONTWRITEBYTECODE="1",
                OMNIGENT_CONFIG_HOME=str(output / "runtime-config"),
                OMNIGENT_DATA_DIR=str(output / "runtime-data"),
                OMNIGENT_AUTH_ENABLED="0",
                OMNIGENT_ACCOUNTS_AUTO_OPEN="0",
                OPENAI_AGENTS_DISABLE_TRACING="1",
                OPENAI_MODEL=self.model_name,
                OPENAI_API_KEY=os.environ["RH_MODEL_GATEWAY_KEY"],
                OPENAI_BASE_URL=os.environ["RH_MODEL_GATEWAY_URL"],
                NO_PROXY="127.0.0.1,localhost,::1",
                RH_WORKSPACE_CONNECTION_FILE=str(connection),
            )
            command = [
                self.omnigent_python,
                "-m",
                "research_harness.experiments.omnigent_driver",
                str(output / "driver.json"),
            ]
            write_json(
                output / "command.json", {"argv": command, "context_id": str(self.context_id)}
            )
            with (output / "driver.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=output,
                    env=env,
                    stdout=log,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
                await asyncio.wait_for(process.wait(), timeout=self.execution_timeout + 90)
            runtime = json.loads((output / "runtime/runtime.json").read_bytes())
            if process.returncode or runtime["status"] != "completed":
                raise RuntimeError(f"Omnigent execution failed; inspect {output}")
        finally:
            if process is not None and process.returncode is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    await asyncio.wait_for(process.wait(), timeout=20)
                except TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
                    write_json(
                        output / "cleanup.json", {"status": "inspect_recorded_runtime_processes"}
                    )
            await bridge.close()
