"""Trusted connector; only the bridge executes commands, in its task container.

The model supplies command/timeout only. The operator sets the connection file
in the runner's environment; neither its path nor bearer token is a tool input.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


async def execute(command: str, timeout: int = 30) -> dict:
    """Run a shell command in the assigned task container, retaining its receipt."""
    connection = json.loads(Path(os.environ["RH_WORKSPACE_CONNECTION_FILE"]).read_bytes())
    endpoint = connection["url"]
    token = connection["token"]
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/execute":
        raise ValueError("Workspace tools require the host's loopback endpoint")
    async with httpx.AsyncClient(trust_env=False, timeout=70) as client:
        response = await client.post(
            endpoint,
            headers={"Authorization": "Bearer " + token},
            json={"id": str(uuid4()), "command": command, "timeout": timeout},
        )
        response.raise_for_status()
        return response.json()
