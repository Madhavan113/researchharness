"""Trusted comparison execution through a caller-owned local model gateway.

This launches actual runtime adapters against authored source recordings. It
does not authorize spending, construct a paid provider, or choose source answers.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from openai import OpenAI

from research_harness.backend import Backend
from research_harness.discovery import Discovery
from research_harness.evaluation.controller import CaseTask, ExecutionArtifacts
from research_harness.evaluation.fixture_transport import FixtureSources
from research_harness.integrations.omnigent import (
    LocalOmnigent,
    prepare_case,
    refresh_unbound_bundle,
)
from research_harness.strategies.config import StrategyBundle
from research_harness.strategies.session import StrategySession
from research_harness.util import digest, timestamp, write_json


class RuntimeExecutionError(RuntimeError):
    """A failed execution with any partial evidence and usage still accessible."""

    def __init__(self, message: str, *, artifacts: ExecutionArtifacts):
        super().__init__(message)
        self.artifacts = artifacts


def task_strategy_session(task: CaseTask) -> StrategySession | None:
    """Reconstruct the same case-bound session for runtime and gateway hooks."""
    if task.strategy_path is None:
        if task.code_strategy_sha256 is not None or task.strategy_session_id is not None:
            raise ValueError("Code strategy controls require a frozen manifest")
        return None
    bundle = StrategyBundle.load(task.strategy_path)
    if task.code_strategy_sha256 != bundle.sha256:
        raise ValueError("Code strategy differs from the task's frozen identity")
    session = (
        StrategySession.open(task.output / "strategy")
        if task.strategy_session_id is not None
        else StrategySession(task.output / "strategy", bundle)
    )
    if session.bundle.sha256 != bundle.sha256 or (
        task.strategy_session_id is not None and session.session_id != task.strategy_session_id
    ):
        raise ValueError("Strategy session differs from the task's recorded identity")
    return session


class RuntimeExecutor:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        omnigent_python: Path,
        max_spend_usd: float,
    ):
        endpoint = urlsplit(base_url)
        if (
            endpoint.scheme != "http"
            or endpoint.hostname not in {"127.0.0.1", "::1"}
            or endpoint.port is None
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("Comparison models require a caller-owned loopback HTTP gateway")
        if not api_key:
            raise ValueError("A local gateway key is required")
        if not math.isfinite(max_spend_usd) or not 0 < max_spend_usd <= 100:
            raise ValueError("Choose a positive pilot spend limit at most $100")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.omnigent_python = Path(omnigent_python)
        self.max_spend_usd = max_spend_usd

    def _environment(self, model: str) -> dict[str, str]:
        # Pass operating-system basics, not ambient provider/backend credentials,
        # proxies, or a previous runner's identity. Never change the parent env.
        allowed = {
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "TMPDIR",
            "TMP",
            "TEMP",
            "LANG",
            "LANGUAGE",
            "TZ",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
        }
        env = {
            key: value
            for key, value in os.environ.items()
            if key in allowed or key.startswith("LC_")
        }
        env.update(
            OPENAI_API_KEY=self.api_key,
            OPENAI_BASE_URL=self.base_url,
            OPENAI_MODEL=model,
            OPENAI_AGENTS_DISABLE_TRACING="1",
            NO_PROXY="127.0.0.1,localhost,::1",
        )
        return env

    def _artifacts(self, task: CaseTask, metadata: dict[str, Any]) -> ExecutionArtifacts:
        runtime_root = task.output / "runtime"
        research = task.output / "research" if task.arm == "direct" else runtime_root / "research"
        usage = runtime_root / "omnigent" / "runtime-usage.discovery.json"
        attachments = [task.output / "execution.json", task.output / "source-fixtures.json"]
        if task.strategy_path is not None:
            attachments.append(task.output / "strategy")
        if task.arm == "omnigent":
            attachments.extend([runtime_root / "case.json", runtime_root / "agent"])
            if task.strategy_path is not None:
                attachments.append(runtime_root / "code-strategy")
            # Whitelist exported provenance. Databases, runner tokens and runtime
            # account/config directories can contain authentication material.
            attachments.extend(
                runtime_root / "omnigent" / name
                for name in (
                    "runtime.json",
                    "session.json",
                    "trace-manifest.json",
                    "response-phases.json",
                    "events.jsonl",
                    "events",
                    "artifacts",
                    "runtime-usage.discovery.json",
                )
            )
        return ExecutionArtifacts(
            research=research,
            runtime_usage=usage if task.arm == "omnigent" and usage.is_file() else None,
            metadata=dict(metadata),
            attachments=tuple(path for path in attachments if path.exists()),
        )

    def _direct(
        self, task: CaseTask, fixtures: FixtureSources, strategy: StrategySession | None = None
    ) -> None:
        with (
            fixtures.client() as source_http,
            httpx.Client(trust_env=False) as model_http,
            OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                http_client=model_http,
                max_retries=0,
                timeout=min(90, task.config.settings.deadline_seconds),
            ) as client,
        ):
            Discovery(
                task.output / "research",
                client=client,
                model=task.config.model,
                backend=Backend.local(task.output / "backend"),
                http_client=source_http,
                public_only=False,
                search_provider=fixtures.provider,
                settings=task.config.settings,
                instructions=task.instructions,
                strategy=strategy,
                strategy_requests_at_gateway=task.gateway_binding is not None,
            ).run(task.brief)

    def _omnigent(
        self, task: CaseTask, metadata: dict[str, Any], strategy: StrategySession | None = None
    ) -> None:
        case = prepare_case(
            task.output / "runtime",
            brief=task.brief,
            model=task.config.model,
            max_spend_usd=self.max_spend_usd,
            settings=task.config.settings,
            instructions=task.instructions,
            search_endpoint="fixture://authored-development-fixtures",
            controlled_discovery=True,
            strategy=strategy.bundle if strategy is not None else None,
            strategy_output=strategy.root if strategy is not None else None,
        )
        launch_path = case.parent / "agent" / "tools" / "mcp" / "research.yaml"
        launch = json.loads(launch_path.read_text())
        launch["args"] = [
            "-m",
            "research_harness.evaluation.fixture_transport",
            "--case",
            str(case),
            "--fixture",
            str(task.output / "source-fixtures.json"),
        ]
        write_json(launch_path, launch)
        refresh_unbound_bundle(case)
        runtime = LocalOmnigent(
            case, python=self.omnigent_python, env=self._environment(task.config.model)
        )
        primary: BaseException | None = None
        try:
            runtime.start()
            snapshot = runtime.send(
                task.brief, phase="discovery", timeout=task.config.settings.deadline_seconds
            )
            metadata["runtime_status"] = snapshot.get("status")
            if snapshot.get("status") != "idle":
                raise RuntimeError(
                    f"Omnigent discovery did not finish: {snapshot.get('last_task_error') or snapshot.get('status')}"
                )
        except BaseException as exc:
            primary = exc
        # A start/send error can still leave billable work, receipts or events.
        # Export and close independently; neither should hide the original error.
        for action, operation in (("export_trace", runtime.export_trace), ("close", runtime.close)):
            try:
                operation()
            except BaseException as exc:
                metadata.setdefault("cleanup_errors", []).append(
                    {"operation": action, "error": f"{type(exc).__name__}: {exc}"}
                )
                if primary is None:
                    primary = exc
        if primary is not None:
            raise primary

    def __call__(self, task: CaseTask) -> ExecutionArtifacts:
        if task.arm not in {"direct", "omnigent"}:
            raise ValueError("Unknown comparison runtime")
        task.output.mkdir(parents=True, exist_ok=True)
        record = task.output / "execution.json"
        if record.exists():
            raise ValueError("This case execution already started; inspect it instead of retrying")
        metadata = {
            "status": "running",
            "started_at": timestamp(),
            "arm": task.arm,
            "model": task.config.model,
            "model_gateway": self.base_url,
            "execution_settings": task.config.settings.model_dump(mode="json"),
            "instructions_sha256": digest(task.instructions),
            "source_mode": "authored-fixtures",
            "external_source_network": False,
            "discovery_only": True,
            "gateway_binding": task.gateway_binding.model_dump(mode="json")
            if task.gateway_binding
            else None,
            "max_spend_usd": self.max_spend_usd,
            "spend_limit_semantics": "soft upstream between-turn policy; not a billing cap",
        }
        write_json(record, metadata)
        try:
            strategy = task_strategy_session(task)
            if strategy is not None:
                strategy.assert_ready()
                metadata.update(
                    code_strategy_sha256=strategy.bundle.sha256,
                    code_strategy_config=strategy.bundle.config.model_dump(mode="json"),
                    strategy_session_id=strategy.session_id,
                )
            fixtures = FixtureSources(task.fixture_path)
            (task.output / "source-fixtures.json").write_bytes(fixtures.raw)
            metadata.update(fixture_sha256=fixtures.sha256, search_provider=fixtures.provider.name)
            write_json(record, metadata)
            if task.arm == "direct":
                self._direct(task, fixtures, strategy)
            else:
                self._omnigent(task, metadata, strategy)
            if strategy is not None:
                strategy.assert_ready()
            artifacts = self._artifacts(task, metadata)
            if not (artifacts.research / "proposal.json").is_file():
                raise RuntimeError("Discovery did not save a validated proposal")
            if task.arm == "omnigent" and artifacts.runtime_usage is None:
                raise RuntimeError("Omnigent discovery usage could not be exported")
        except BaseException as exc:
            metadata.update(
                status="failed" if isinstance(exc, Exception) else "interrupted",
                finished_at=timestamp(),
                error=f"{type(exc).__name__}: {exc}",
            )
            write_json(record, metadata)
            if isinstance(exc, Exception):
                raise RuntimeExecutionError(
                    str(exc), artifacts=self._artifacts(task, metadata)
                ) from exc
            exc.artifacts = self._artifacts(task, metadata)
            raise
        metadata.update(status="completed", finished_at=timestamp())
        write_json(record, metadata)
        return self._artifacts(task, metadata)
