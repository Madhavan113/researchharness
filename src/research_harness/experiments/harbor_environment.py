"""Harbor 0.23.0 Docker extension with no host mounts or runtime networking.

Import this module in the separate Harbor environment. Harbor's default Docker
mounts include verifier output in the agent container, even for separate grading.
Use Harbor's existing upload/download path instead, with controller records kept
outside its agent-log directory (which remains candidate-authored evidence).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from harbor.environments.docker.docker import DockerEnvironment

from research_harness.util import digest, timestamp, write_json


class ExperimentDocker(DockerEnvironment):
    def __init__(self, *args, evidence_root: str, **kwargs):
        kwargs["mounts"] = []
        if kwargs.get("extra_docker_compose"):
            raise ValueError("Experiment environments do not accept compose overrides")
        super().__init__(*args, **kwargs)
        if self._uses_compose or self.task_env_config.os.value != "linux":
            raise ValueError("Experiment execution requires a single Linux Dockerfile environment")
        if self._enable_egress_control:
            raise ValueError(
                "Experiment execution uses Docker network:none, not Harbor's egress sidecar"
            )
        self.evidence_root = Path(evidence_root).resolve()
        if self.evidence_root.is_relative_to(self.trial_paths.agent_dir.resolve()):
            raise ValueError("Controller evidence must be outside candidate agent logs")
        self._network_override = self.evidence_root / f"network-{digest(self.session_id)[:16]}.json"
        write_json(self._network_override, {"services": {"main": {"network_mode": "none"}}})

    @property
    def _docker_compose_paths(self):
        return [*super()._docker_compose_paths, self._network_override]

    @property
    def capabilities(self):
        return super().capabilities.model_copy(update={"mounted": False, "docker_compose": False})

    async def start(self, force_build: bool):
        await super().start(force_build)
        try:
            listing = await self._run_docker_compose_command(["ps", "-q", "main"], timeout_sec=20)
            ids = (listing.stdout or "").split()
            if len(ids) != 1:
                raise ValueError("Expected exactly one experiment container")
            process = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                ids[0],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            if process.returncode:
                raise RuntimeError(f"Cannot inspect experiment container: {stderr.decode()}")
            actual = json.loads(stdout)[0]
            self.program_container_id = actual["Id"]
            record = {
                "session_id": self.session_id,
                "context_id": str(self.context_id),
                "checked_at": timestamp(),
                "container_id": actual["Id"],
                "image_id": actual["Image"],
                "mounts": actual["Mounts"],
                "privileged": actual["HostConfig"]["Privileged"],
                "memory_bytes": actual["HostConfig"]["Memory"],
                "nano_cpus": actual["HostConfig"]["NanoCpus"],
                "network_mode": actual["HostConfig"]["NetworkMode"],
            }
            write_json(
                self.evidence_root / f"environment-{digest(self.session_id)[:16]}.json", record
            )
            if record["mounts"] or record["privileged"] or record["network_mode"] != "none":
                raise ValueError("Experiment container has mounts, privileged access or networking")
            await self.ensure_dirs(["/logs/agent", "/logs/verifier", "/logs/artifacts"])
        except BaseException:
            await asyncio.shield(self.stop(delete=True))
            raise

    def program_argv(self, path: str) -> list[str]:
        if not re.fullmatch(r"/tmp/rh-program-[0-9a-f]{32}\.py", path):
            raise ValueError("Unexpected program entry point")
        container = self.program_container_id
        if not re.fullmatch(r"[0-9a-f]{64}", container):
            raise ValueError("Expected the inspected Docker container identity")
        return ["docker", "exec", "-i", container, "python", "-I", "-u", path]
