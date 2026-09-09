"""Case bundles and normal local Omnigent server/runner integration.

This module runs in Research Harness's environment. Omnigent remains in a
separate pinned environment and is accessed through processes and its HTTP API.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from filelock import FileLock

from research_harness.backend import Backend
from research_harness.execution import DiscoverySettings
from research_harness.services.research import ResearchService
from research_harness.strategies.config import StrategyBundle
from research_harness.strategies.session import StrategySession
from research_harness.util import canonical_json, digest, timestamp, utcnow, write_json

OMNIGENT_COMMIT = "be042b390e293a8d586cbb7e403a2ce0ce38fc62"
PRIMARY_SESSION_ENV = "OMNIGENT_RUNNER_PRIMARY_SESSION_ID"
MODEL = "gpt-5.4-mini"
PHASES = {"discovery", "workflow", "followup"}
TERMINAL_EVENTS = {"response.completed", "response.failed", "response.cancelled"}
DISCOVERY_TOOL_NAMES = (
    "research__begin_research",
    "research__get_research_context",
    "research__search_sources",
    "research__inspect_source",
    "research__probe_source",
    "research__get_evidence",
    "research__submit_proposal",
    "research__list_pipelines",
)
DISCOVERY_POLICY_MODULE = "research_discovery_policy"


def _discovery_policy(case_id: str) -> dict:
    return {
        "type": "function",
        "on": ["tool_call"],
        "function": {
            "path": f"{DISCOVERY_POLICY_MODULE}.controlled_discovery",
            "arguments": {"case_id": case_id},
        },
    }


def _tool_controls(case_id: str, enabled: bool) -> dict:
    return {
        "schema_version": 1,
        "controlled_discovery": enabled,
        "allowed_function_names": list(DISCOVERY_TOOL_NAMES) if enabled else None,
        "bootstrap_event": (
            {"name": "sys_agent_start", "agent_name": case_id, "harness": "openai-agents"}
            if enabled
            else None
        ),
        "host_config": {"policy_modules": [DISCOVERY_POLICY_MODULE]} if enabled else {},
    }


def bundle_fingerprints(bundle: Path) -> dict[str, str]:
    files = {
        str(path.relative_to(bundle)): digest(path.read_bytes())
        for path in sorted(bundle.rglob("*"))
        if path.is_file()
    }
    return {
        "authored_bundle_sha256": digest(canonical_json(files).encode()),
        "authored_config_sha256": digest((bundle / "config.yaml").read_bytes()),
        "strategy_sha256": digest((bundle / "instructions.md").read_bytes()),
    }


def _read_case(path: Path) -> dict[str, Any]:
    case = json.loads(path.read_text())
    if case.get("schema_version") != 1:
        raise ValueError("Unsupported Omnigent case manifest")
    if not re.fullmatch(r"research-[a-f0-9]{32}", case.get("case_id", "")):
        raise ValueError("Invalid research case id")
    fingerprints = bundle_fingerprints(Path(case["output"]) / "agent")
    if any(case.get(key) != value for key, value in fingerprints.items()):
        raise ValueError("The authored bundle differs from its frozen case manifest")
    if case.get("discovery_settings") is not None:
        controls = DiscoverySettings.model_validate(case["discovery_settings"])
        frozen = Path(case["output"]) / "agent" / "research-settings.json"
        if json.loads(frozen.read_text()) != controls.model_dump(mode="json"):
            raise ValueError("Discovery settings differ from the frozen authored bundle")
    code = case.get("code_strategy")
    binding_file = Path(case["output"]) / "agent" / "code-strategy-binding.json"
    if code is not None:
        expected_manifest = Path(case["output"]) / "code-strategy" / "strategy.json"
        if (
            not isinstance(code, dict)
            or set(code) != {"manifest_path", "sha256", "output", "session_id"}
            or not isinstance(code["session_id"], str)
            or re.fullmatch(r"[a-f0-9]{32}", code["session_id"]) is None
            or code["manifest_path"] != str(expected_manifest)
            or json.loads(binding_file.read_text()) != code
            or StrategyBundle.load(expected_manifest).sha256 != code["sha256"]
        ):
            raise ValueError("Code strategy differs from its frozen case binding")
    elif binding_file.exists():
        raise ValueError("The case omitted its frozen code strategy")
    enabled = case.get("controlled_discovery", False)
    if type(enabled) is not bool:
        raise ValueError("Controlled discovery must be a boolean")
    tool_controls = Path(case["output"]) / "agent" / "research-tool-controls.json"
    if tool_controls.exists():
        if json.loads(tool_controls.read_text()) != _tool_controls(case["case_id"], enabled):
            raise ValueError("Tool controls differ from the frozen authored bundle")
    elif enabled:
        raise ValueError("Controlled discovery requires frozen tool controls")
    if enabled:
        bundle = Path(case["output"]) / "agent"
        config = json.loads((bundle / "config.yaml").read_text())
        policy = config.get("guardrails", {}).get("policies", {}).get("research_discovery_tools")
        if policy != _discovery_policy(case["case_id"]):
            raise ValueError("Controlled discovery requires its execution policy")
        if not (bundle / "policies" / f"{DISCOVERY_POLICY_MODULE}.py").is_file():
            raise ValueError("Controlled discovery policy module is missing")
    return case


def refresh_unbound_bundle(path: Path) -> None:
    """Trusted preparation hook, for fixture/candidate wiring before any session exists."""
    with FileLock(str(path) + ".lock"):
        case = json.loads(path.read_text())
        if case["binding"] is not None:
            raise ValueError("A bound case's authored bundle cannot be changed")
        case.update(bundle_fingerprints(Path(case["output"]) / "agent"))
        write_json(path, case)


def prepare_case(
    output: Path,
    *,
    brief: str,
    research_python: Path | None = None,
    template: Path | None = None,
    model: str = MODEL,
    search_endpoint: str = "https://api.keenable.ai/mcp",
    max_spend_usd: float,
    settings: DiscoverySettings | None = None,
    instructions: str | None = None,
    controlled_discovery: bool = False,
    strategy: StrategyBundle | None = None,
    strategy_output: Path | None = None,
) -> Path:
    """Create a new case and authored bundle without starting discovery or a model."""
    if not brief.strip() or not model.strip():
        raise ValueError("Brief and model must be nonempty")
    if type(controlled_discovery) is not bool:
        raise ValueError("Controlled discovery must be a boolean")
    if strategy_output is not None and strategy is None:
        raise ValueError("A strategy output requires a code strategy")
    if not math.isfinite(max_spend_usd) or max_spend_usd <= 0 or max_spend_usd > 100:
        raise ValueError("Choose a positive pilot spend limit at most $100")
    output = output.expanduser().resolve()
    python = str((research_python or Path(sys.executable)).absolute())
    template = template or Path(__file__).resolve().parents[3] / "agents" / "research"
    config = json.loads((template / "config.yaml").read_text())
    instructions = (
        instructions if instructions is not None else (template / "instructions.md").read_text()
    )
    if not instructions.strip():
        raise ValueError("Research instructions must be nonempty")
    controls = DiscoverySettings.model_validate(settings) if settings is not None else None
    output.mkdir(parents=True, exist_ok=False)
    bundle = output / "agent"
    shutil.copytree(template, bundle)
    (bundle / "instructions.md").write_text(instructions)
    case_id = f"research-{uuid4().hex}"
    manifest = output / "case.json"
    case = {
        "schema_version": 1,
        "case_id": case_id,
        "created_at": timestamp(utcnow()),
        "brief": brief.strip(),
        "model": model,
        "adapter": "openai-agents",
        "omnigent_commit": OMNIGENT_COMMIT,
        "strategy_sha256": digest(instructions.encode()),
        "output": str(output),
        "research_output": str(output / "research"),
        "backend_root": str(output / "backend"),
        "search_endpoint": search_endpoint,
        "max_spend_usd": max_spend_usd,
        "binding": None,
        "discovery_settings": controls.model_dump(mode="json") if controls is not None else None,
        "controlled_discovery": controlled_discovery,
    }
    if strategy is not None:
        # Candidate Python stays outside Omnigent's authored-agent/plugin tree.
        frozen = strategy.freeze(output / "code-strategy")
        state_root = (strategy_output or output / "strategy").expanduser().resolve()
        for reserved in (bundle, output / "code-strategy", output / "research", output / "backend"):
            if state_root.is_relative_to(reserved) or reserved.is_relative_to(state_root):
                raise ValueError(
                    "Strategy state must be separate from the bundle and research backend"
                )
        case["code_strategy"] = {
            "manifest_path": str(frozen.manifest_path),
            "sha256": frozen.sha256,
            "output": str(state_root),
            "session_id": StrategySession(state_root, frozen).session_id,
        }
        write_json(bundle / "code-strategy-binding.json", case["code_strategy"])
    config["name"] = case_id
    config["executor"]["model"] = model
    if controls is not None:
        config["executor"]["max_iterations"] = controls.max_rounds
        config["executor"]["timeout"] = controls.deadline_seconds
        config["executor"]["reasoning_effort"] = controls.reasoning_effort
        # At this native spec pin, provider kwargs are parsed from llm into
        # LLMConfig.extra; executor.extra belongs to the legacy spec adapter.
        config["llm"] = {
            "model": model,
            "reasoning_effort": controls.reasoning_effort,
            "max_tokens": controls.max_output_tokens,
            "max_turns": controls.max_rounds,
        }
        write_json(bundle / "research-settings.json", controls.model_dump(mode="json"))
    config["guardrails"] = {
        "policies": {
            "research_cost_budget": {
                "type": "function",
                "function": {
                    "path": "omnigent.policies.builtins.cost.cost_budget",
                    "arguments": {"max_cost_usd": max_spend_usd, "expensive_models": []},
                },
            }
        }
    }
    write_json(
        bundle / "research-tool-controls.json", _tool_controls(case_id, controlled_discovery)
    )
    if controlled_discovery:
        if not (bundle / "policies" / f"{DISCOVERY_POLICY_MODULE}.py").is_file():
            raise ValueError("The controlled discovery template requires its execution policy")
        config["guardrails"]["policies"]["research_discovery_tools"] = _discovery_policy(case_id)
    # Upstream's YAML 1.1 loader interprets JSON's `1e-09` as a string.
    # Emit this numeric field in decimal notation while retaining valid JSON.
    config_text = json.dumps(config, indent=2).replace(
        f'"max_cost_usd": {json.dumps(max_spend_usd)}',
        f'"max_cost_usd": {format(Decimal(str(max_spend_usd)), "f")}',
    )
    (bundle / "config.yaml").write_text(config_text + "\n")
    write_json(
        bundle / "tools" / "mcp" / "research.yaml",
        {
            "name": "research",
            "transport": "stdio",
            "command": python,
            "args": [
                "-m",
                "research_harness.integrations.omnigent",
                "mcp",
                "--case",
                str(manifest),
            ],
            "env": {"RH_LOCAL_ROOT": case["backend_root"]},
            "timeout": 90,
        },
    )
    case.update(bundle_fingerprints(bundle))
    write_json(manifest, case)
    (output / "brief.md").write_text(case["brief"] + "\n")
    return manifest


def bind_case(path: Path, *, session_id: str, server_url: str) -> dict[str, Any]:
    """Bind a case once to a host-issued session, refusing cross-session reuse."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", session_id):
        raise ValueError("Invalid Omnigent session id")
    server_url = server_url.rstrip("/")
    parts = urlsplit(server_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
        raise ValueError("Invalid Omnigent server URL")
    binding = {"session_id": session_id, "server_url": server_url}
    with FileLock(str(path) + ".lock"):
        case = _read_case(path)
        if case["binding"] is not None and case["binding"] != binding:
            raise ValueError(
                "This research case belongs to another Omnigent session; resume it or prepare a new case"
            )
        case["binding"] = binding
        write_json(path, case)
    return case


def bound_service(path: Path, *, env: dict[str, str] | None = None) -> tuple[ResearchService, dict]:
    """Load a service using the normal runner's host-owned primary session identity."""
    env = dict(os.environ if env is None else env)
    case = _read_case(path)
    session_id = env.get(PRIMARY_SESSION_ENV)
    server_url = env.get("RUNNER_SERVER_URL")
    if not session_id or not server_url:
        raise ValueError(
            "Research MCP requires a dedicated Omnigent runner with primary session and server identity"
        )
    case = bind_case(path, session_id=session_id, server_url=server_url)
    settings = {key: value for key, value in env.items() if key.startswith("RH_")}
    settings["RH_LOCAL_ROOT"] = case["backend_root"]
    service = ResearchService(Path(case["research_output"]), backend=Backend.from_env(settings))
    context = service.output / "context.json"
    if context.exists():
        service.resume(json.loads(context.read_text())["discovery_id"])
    runtime = {
        "model": case["model"],
        "adapter": case["adapter"],
        "session_id": session_id,
        "server_url": server_url.rstrip("/"),
        "case_id": case["case_id"],
        "runtime_version": case["omnigent_commit"],
        "strategy_sha256": case["strategy_sha256"],
        "authored_bundle_sha256": case["authored_bundle_sha256"],
        "authored_config_sha256": case["authored_config_sha256"],
    }
    if "controlled_discovery" in case:
        runtime["tool_controls"] = _tool_controls(case["case_id"], case["controlled_discovery"])
    if case.get("discovery_settings") is not None:
        controls = DiscoverySettings.model_validate(case["discovery_settings"])
        runtime.update(
            discovery_settings=controls.model_dump(mode="json"),
            model_settings=controls.model_settings(),
            budgets=controls.budgets(),
        )
    if case.get("code_strategy") is not None:
        runtime["code_strategy_sha256"] = case["code_strategy"]["sha256"]
        runtime["strategy_session_id"] = case["code_strategy"]["session_id"]
    return service, runtime


def case_strategy_session(path: Path) -> StrategySession | None:
    case = _read_case(path)
    code = case.get("code_strategy")
    if code is None:
        return None
    session = StrategySession.open(Path(code["output"]))
    if session.bundle.sha256 != code["sha256"] or session.session_id != code["session_id"]:
        raise ValueError("The case requires its original strategy session state")
    return session


def create_case_server(path: Path):
    from research_harness.mcp.server import create_server
    from research_harness.services.search import McpSearchProvider

    service, runtime = bound_service(path)
    case = _read_case(path)
    controls = (
        DiscoverySettings.model_validate(case["discovery_settings"])
        if case.get("discovery_settings") is not None
        else None
    )
    return create_server(
        service,
        search_provider=McpSearchProvider(case["search_endpoint"]),
        runtime=runtime,
        strategy=case_strategy_session(path),
        **(
            {
                "research_limits": controls.limits(),
                "research_deadline_seconds": controls.deadline_seconds,
            }
            if controls is not None
            else {}
        ),
    )


def serve_mcp(path: Path) -> None:
    create_case_server(path).run(transport="stdio")


def check_runtime(python: Path) -> dict[str, Any]:
    script = (
        "import json,subprocess,omnigent,importlib.metadata; from pathlib import Path; "
        "root=Path(omnigent.__file__).resolve().parent.parent; "
        "print(json.dumps({'version':importlib.metadata.version('omnigent'),"
        "'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),"
        "'tracked_source_clean':subprocess.run(['git','diff','--quiet','HEAD','--'],cwd=root).returncode==0}))"
    )
    result = subprocess.run([str(python), "-c", script], capture_output=True, text=True, check=True)
    runtime = json.loads(result.stdout)
    if runtime["commit"] != OMNIGENT_COMMIT:
        raise ValueError(f"Omnigent checkout must use tested commit {OMNIGENT_COMMIT}")
    if not runtime["tracked_source_clean"]:
        raise ValueError("The pinned Omnigent checkout has tracked source changes")
    return runtime


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _bundle(path: Path) -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for item in sorted(path.rglob("*")):
            if item.is_file():
                archive.add(item, arcname=str(item.relative_to(path)), recursive=False)
    return data.getvalue()


def runtime_usage(
    events: bytes,
    *,
    context: dict,
    model: str,
    phase: str,
    response_phases: dict[str, str],
    source_event_file: str,
) -> dict:
    """Export host-observed aggregate turns; auxiliary provider usage remains unknown.

    Phase assignment belongs to the controller, never to tool/model arguments.
    The workflow export includes every turn. Other exports select only explicitly
    assigned turns. Repeated delivery of the same event is counted once.
    """
    if phase not in PHASES:
        raise ValueError("Invalid runtime usage phase")
    responses = {}
    started = set()
    for line in events.splitlines():
        event = json.loads(line)
        response = event.get("data", {}).get("response", {})
        response_id = response.get("id")
        if not response_id or (phase != "workflow" and response_phases.get(response_id) != phase):
            continue
        if phase != "workflow" and event.get("phase") != phase:
            raise ValueError(f"Source event phase differs for response {response_id}")
        if event["event"] == "response.in_progress":
            started.add(response_id)
        if event["event"] not in TERMINAL_EVENTS:
            continue
        usage = response.get("usage") or {}
        record = {
            "model": usage.get("model") or model,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "status": response.get("status"),
        }
        for field in ("input_tokens", "output_tokens"):
            value = record[field]
            if type(value) is not int or value < 0:
                record[field] = None
        if response_id in responses and responses[response_id] != record:
            raise ValueError(f"Conflicting terminal usage for response {response_id}")
        responses[response_id] = record
    input_tokens = sum(record["input_tokens"] or 0 for record in responses.values())
    output_tokens = sum(record["output_tokens"] or 0 for record in responses.values())
    return {
        "schema_version": 1,
        "question_id": context.get("question_id"),
        "discovery_id": context.get("discovery_id"),
        "phase": phase,
        "source_event_file": source_event_file,
        "source_event_sha256": digest(events),
        "responses": responses,
        "totals": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "complete": False,
        "auxiliary_usage_unknown": True,
        "interrupted": bool(started - responses.keys())
        or any(record["status"] != "completed" for record in responses.values()),
    }


class LocalOmnigent:
    """A foreground local pilot with a dedicated normal runner per case.

    The server, tunnel, harness process, policy engine and tool routing are all
    upstream Omnigent. This controller only uses normal process and REST APIs.
    """

    def __init__(
        self, case: Path, *, python: Path, port: int = 0, env: dict[str, str] | None = None
    ):
        self.path = case.resolve()
        self.case = _read_case(self.path)
        self.python = python.absolute()
        self.port = port
        self.env = dict(os.environ if env is None else env)
        self.processes: list[subprocess.Popen] = []
        self.client: httpx.Client | None = None
        self.output = Path(self.case["output"]) / "omnigent"
        self.session_id: str | None = None
        self.base_url: str | None = None
        self.last_submission: dict | None = None
        self._lock = FileLock(str(self.path.parent / "runtime.lock"))
        self._lock_held = False
        self._condition = threading.Condition()
        self._send_lock = threading.Lock()
        self._closing = threading.Event()
        self._reader: threading.Thread | None = None
        self._stream_errors: list[str] = []
        self._terminals: list[str] = []
        self._pending_phase: str | None = None
        phases = self.output / "response-phases.json"
        self._response_phases = json.loads(phases.read_text()) if phases.exists() else {}

    def _spawn(self, args: list[str], env: dict[str, str], name: str) -> subprocess.Popen:
        with (self.output / f"{name}.log").open("ab") as log:
            process = subprocess.Popen(
                args,
                cwd=self.output,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.processes.append(process)
        return process

    def _wait_ready(self, path: str, predicate, *, timeout: float = 45) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in self.processes):
                raise RuntimeError(f"Omnigent process exited; inspect logs in {self.output}")
            try:
                result = self.client.get(path, timeout=2)
                if result.status_code == 200 and predicate(result):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise TimeoutError(f"Omnigent did not become ready; inspect {self.output}")

    def __enter__(self):
        try:
            return self.start()
        except BaseException:
            self.close()
            raise

    def start(self):
        if self._lock_held:
            raise ValueError("This Omnigent controller is already started")
        self._lock.acquire(timeout=0)
        self._lock_held = True
        self.case = _read_case(self.path)
        runtime = check_runtime(self.python)
        self.output.mkdir(parents=True, exist_ok=True)
        binding = self.case["binding"]
        if binding:
            self.base_url = binding["server_url"]
            self.port = urlsplit(self.base_url).port
            if urlsplit(self.base_url).hostname != "127.0.0.1":
                raise ValueError("Local pilot resumes only its own loopback server")
        else:
            self.port = self.port or _free_port()
            self.base_url = f"http://127.0.0.1:{self.port}"
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", self.port))
            except OSError as exc:
                raise ValueError(
                    "The case server port is occupied; inspect the existing server before restarting"
                ) from exc
        token_path = self.output / "runner-token"
        if token_path.exists():
            token = token_path.read_text()
        else:
            token = secrets.token_hex(32)
            descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                handle.write(token)
        runner_id = (
            "runner_token_" + hashlib.sha256(f"omnigent-runner:{token}".encode()).hexdigest()[:32]
        )
        env = {
            **self.env,
            "OMNIGENT_CONFIG_HOME": str(self.output / "config"),
            "OMNIGENT_DATA_DIR": str(self.output / "data"),
            "OMNIGENT_AUTH_ENABLED": "0",
            "OMNIGENT_RUNNER_TUNNEL_TOKEN": token,
            "OMNIGENT_ACCOUNTS_AUTO_OPEN": "0",
        }
        env.pop("OMNIGENT_AUTH_PROVIDER", None)
        env.pop("PYTHONPATH", None)
        if self.case.get("controlled_discovery", False):
            # Normal admin configuration registers the frozen module for bundle
            # upload validation and the server/runner TOOL_CALL policy gates.
            # Only this policy directory enters the separate runtime's path.
            env["PYTHONPATH"] = str(Path(self.case["output"]) / "agent" / "policies")
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            write_json(
                self.output / "config" / "config.yaml",
                _tool_controls(self.case["case_id"], True)["host_config"],
            )
        self._spawn(
            [
                str(self.python),
                "-m",
                "omnigent.cli",
                "server",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--database-uri",
                f"sqlite:///{self.output / 'sessions.db'}",
                "--artifact-location",
                str(self.output / "artifacts"),
                "--no-open",
            ],
            env,
            "server",
        )
        self.client = httpx.Client(
            base_url=self.base_url, timeout=30, headers={"Origin": "omnigent://internal"}
        )
        self._wait_ready("/health", lambda response: True)
        if binding:
            self.session_id = binding["session_id"]
            self.client.get(f"/v1/sessions/{self.session_id}").raise_for_status()
        else:
            existing = self.client.get(
                "/v1/sessions", params={"agent_name": self.case["case_id"], "limit": 2}
            )
            existing.raise_for_status()
            sessions = existing.json()["data"]
            if len(sessions) > 1:
                raise ValueError(
                    "Multiple unbound Omnigent sessions exist for this case; reconcile before resuming"
                )
            if sessions:
                self.session_id = sessions[0]["id"]
            else:
                response = self.client.post(
                    "/v1/sessions",
                    data={"metadata": json.dumps({"title": self.case["brief"][:100]})},
                    files={
                        "bundle": (
                            "research.tar.gz",
                            _bundle(Path(self.case["output"]) / "agent"),
                            "application/gzip",
                        )
                    },
                )
                response.raise_for_status()
                self.session_id = response.json()["session_id"]
            self.case = bind_case(self.path, session_id=self.session_id, server_url=self.base_url)
        runner_env = {
            **env,
            "OMNIGENT_RUNNER_ID": runner_id,
            "OMNIGENT_RUNNER_TUNNEL_BINDING_TOKEN": token,
            "OMNIGENT_RUNNER_PARENT_PID": str(os.getpid()),
            "RUNNER_SERVER_URL": self.base_url,
            PRIMARY_SESSION_ENV: self.session_id,
            "OMNIGENT_RUNNER_WORKSPACE": self.case["output"],
        }
        self._spawn([str(self.python), "-m", "omnigent.runner._entry"], runner_env, "runner")
        self._wait_ready(
            f"/v1/runners/{runner_id}/status",
            lambda response: response.json().get("online") is True,
        )
        self.client.patch(
            f"/v1/sessions/{self.session_id}", json={"runner_id": runner_id}
        ).raise_for_status()
        write_json(
            self.output / "runtime.json",
            {
                **runtime,
                "session_id": self.session_id,
                "server_url": self.base_url,
                "runner_id": runner_id,
                "model": self.case["model"],
                "tool_controls": _tool_controls(
                    self.case["case_id"], self.case.get("controlled_discovery", False)
                ),
            },
        )
        self._start_recorder()
        return self

    def snapshot(self) -> dict:
        response = self.client.get(f"/v1/sessions/{self.session_id}")
        response.raise_for_status()
        return response.json()

    def _start_recorder(self) -> None:
        """Keep recording ordinary UI and controller turns until this host closes."""
        event_path = self.output / "events.jsonl"
        event_path.touch(exist_ok=True)
        ready = threading.Event()

        def subscribe():
            try:
                with self.client.stream(
                    "GET", f"/v1/sessions/{self.session_id}/stream", timeout=None
                ) as response:
                    response.raise_for_status()
                    ready.set()
                    event_type = None
                    with event_path.open("a") as handle:
                        for line in response.iter_lines():
                            if self._closing.is_set():
                                return
                            if line.startswith("event:"):
                                event_type = line[6:].strip()
                            elif line.startswith("data:"):
                                payload = json.loads(line[5:].strip())
                                with self._condition:
                                    response_id = payload.get("response", {}).get("id")
                                    if response_id and (
                                        event_type == "response.in_progress"
                                        or event_type in TERMINAL_EVENTS
                                    ):
                                        self._response_phases.setdefault(
                                            response_id, self._pending_phase or "workflow"
                                        )
                                        write_json(
                                            self.output / "response-phases.json",
                                            self._response_phases,
                                        )
                                    event_phase = self._response_phases.get(
                                        response_id, self._pending_phase or "workflow"
                                    )
                                    handle.write(
                                        canonical_json(
                                            {
                                                "event": event_type,
                                                "phase": event_phase,
                                                "data": payload,
                                            }
                                        )
                                        + "\n"
                                    )
                                    handle.flush()
                                    if event_type in TERMINAL_EVENTS and response_id:
                                        self._terminals.append(response_id)
                                        self._condition.notify_all()
                if not self._closing.is_set():
                    raise RuntimeError("Omnigent event stream ended")
            except Exception as exc:
                if not self._closing.is_set():
                    with self._condition:
                        self._stream_errors.append(str(exc))
                        self._condition.notify_all()
            finally:
                ready.set()

        self._reader = threading.Thread(target=subscribe, daemon=True)
        self._reader.start()
        if not ready.wait(10) or self._stream_errors:
            raise RuntimeError(f"Could not subscribe to Omnigent events: {self._stream_errors}")

    def send(self, prompt: str, *, timeout: float = 600, phase: str = "workflow") -> dict:
        """Send one normal user event. Phase is a trusted controller scope marker."""
        if phase not in PHASES:
            raise ValueError("Invalid runtime usage phase")
        with self._send_lock:
            if self.snapshot().get("status") not in {"idle", "created"}:
                raise ValueError("Resolve the current Omnigent turn before sending another")
            with self._condition:
                terminal_count = len(self._terminals)
                self._pending_phase = phase
            try:
                submission = self.client.post(
                    f"/v1/sessions/{self.session_id}/events",
                    json={
                        "type": "message",
                        "data": {
                            "role": "user",
                            "content": [{"type": "input_text", "text": prompt}],
                        },
                    },
                )
                submission.raise_for_status()
                self.last_submission = submission.json()
                with self._condition:
                    resolved = self._condition.wait_for(
                        lambda: len(self._terminals) > terminal_count or self._stream_errors,
                        timeout=timeout,
                    )
                if not resolved:
                    raise TimeoutError(
                        "Omnigent turn is unresolved; inspect the existing session before retrying"
                    )
                if self._stream_errors:
                    raise RuntimeError(f"Omnigent stream failed: {self._stream_errors}")
            finally:
                with self._condition:
                    self._pending_phase = None
            return self.export_trace()

    def export_trace(self) -> dict:
        snapshot = self.snapshot()
        write_json(self.output / "session.json", snapshot)
        with self._condition:
            events = (self.output / "events.jsonl").read_bytes()
            response_phases = dict(self._response_phases)
        event_file = f"events/{digest(events)}.jsonl"
        frozen = self.output / event_file
        frozen.parent.mkdir(exist_ok=True)
        frozen.write_bytes(events)
        context_path = Path(self.case["research_output"]) / "context.json"
        context = json.loads(context_path.read_text()) if context_path.exists() else {}
        for phase in sorted(PHASES):
            filename = (
                "runtime-usage.json" if phase == "workflow" else f"runtime-usage.{phase}.json"
            )
            write_json(
                self.output / filename,
                runtime_usage(
                    events,
                    context=context,
                    model=self.case["model"],
                    phase=phase,
                    response_phases=response_phases,
                    source_event_file=event_file,
                ),
            )
        files = [
            path
            for path in self.output.iterdir()
            if path.name
            in {
                "session.json",
                "runtime.json",
                "response-phases.json",
                "runtime-usage.json",
                "runtime-usage.discovery.json",
                "runtime-usage.followup.json",
            }
        ]
        manifest = {
            "session_id": self.session_id,
            "server_url": self.base_url,
            "exported_at": timestamp(utcnow()),
            "status": snapshot.get("status"),
            "artifacts": {
                **{path.name: digest(path.read_bytes()) for path in files},
                event_file: digest(events),
            },
            "hidden_reasoning_available": False,
            "provider_usage_complete": False,
            "stream_errors": list(self._stream_errors),
        }
        write_json(self.output / "trace-manifest.json", manifest)
        return snapshot

    def close(self):
        self._closing.set()
        for process in reversed(self.processes):
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
        self.processes.clear()
        if self._reader is not None:
            self._reader.join(timeout=3)
        if self.client is not None:
            self.client.close()
        if self._lock_held:
            self._lock.release()
            self._lock_held = False

    def __exit__(self, *args):
        self.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="Create a case bundle without starting model work")
    prepare.add_argument("--out", type=Path, required=True)
    prepare.add_argument("--brief", type=Path, required=True)
    prepare.add_argument("--model", default=MODEL)
    prepare.add_argument("--max-spend-usd", type=float, required=True)
    prepare.add_argument("--search-endpoint", default="https://api.keenable.ai/mcp")
    prepare.add_argument(
        "--strategy", type=Path, help="Manifest for isolated Python strategy hooks"
    )
    serve = sub.add_parser("serve", help="Start the local Omnigent server and dedicated runner")
    serve.add_argument("--case", type=Path, required=True)
    serve.add_argument("--omnigent-python", type=Path, required=True)
    serve.add_argument("--message")
    serve.add_argument("--port", type=int, default=0)
    mcp = sub.add_parser("mcp", help="Host-owned MCP launcher used by generated bundles")
    mcp.add_argument("--case", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        print(
            prepare_case(
                args.out,
                brief=args.brief.read_text(),
                model=args.model,
                max_spend_usd=args.max_spend_usd,
                search_endpoint=args.search_endpoint,
                strategy=StrategyBundle.load(args.strategy) if args.strategy else None,
            )
        )
    elif args.command == "mcp":
        serve_mcp(args.case)
    else:
        with LocalOmnigent(args.case, python=args.omnigent_python, port=args.port) as runtime:
            print(
                json.dumps({"url": runtime.base_url, "session_id": runtime.session_id}), flush=True
            )
            if args.message:
                runtime.send(args.message)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                runtime.export_trace()


if __name__ == "__main__":
    main()
