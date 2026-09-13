"""Appended to the pinned Terminus-2 source; executes only inside the task container.

This module supplies transport and local process access. The exported upstream
source still owns terminal interaction, parsing, context management and stopping.
"""

import asyncio
import json
import os
import shutil
import signal
import sys
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace


class LocalTaskEnvironment:
    """Harbor's environment operations, when the agent is already in the container."""

    default_user = None
    session_id = "terminus-program"

    def __init__(self, logs: Path):
        self.logs = logs
        self.trial_paths = SimpleNamespace(agent_dir=logs)

    async def exec(self, command, *, cwd=None, env=None, user=None, timeout_sec=None):
        if user not in (None, 0, "root"):
            raise ValueError("The baseline task image uses root; other users are unsupported")
        with (self.logs / "commands.jsonl").open("a") as stream:
            stream.write(
                json.dumps({"command": command, "cwd": cwd, "timeout": timeout_sec}) + "\n"
            )
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
                await process.communicate()
        return SimpleNamespace(
            return_code=process.returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )

    async def is_dir(self, path):
        return Path(path).is_dir()

    async def upload_file(self, source_path, target_path):
        source, target = Path(source_path), Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)

    async def download_file(self, source_path, target_path):
        await self.upload_file(source_path, target_path)


def response_cost(usage, contract):
    """Match the controller rate card's full-input pricing and nanodollar rounding."""
    exact = (
        Fraction(contract["input_usd_per_million"]) * usage["input_tokens"]
        + Fraction(contract["output_usd_per_million"]) * usage["output_tokens"]
    ) * 1000
    return ((exact.numerator + exact.denominator - 1) // exact.denominator) / 1_000_000_000


async def run_baseline(terminus_type, initial, protocol_output, templates, logs):
    # Imports are deliberately deferred: export/inspection never executes this
    # adapter, and Harbor remains a separate pinned dependency.
    from harbor.llms.base import BaseLLM, LLMResponse, OutputLengthExceededError
    from harbor.models.agent.context import AgentContext
    from harbor.models.metric import UsageInfo

    contract = initial["model_contract"]
    context_limit, output_limit = contract["input_admission_tokens"], contract["max_output_tokens"]
    if (
        type(context_limit) is not int
        or type(output_limit) is not int
        or not 0 < output_limit < context_limit
    ):
        raise ValueError(
            "Terminus requires an explicit input admission bound larger than the output cap"
        )

    class Model(BaseLLM):
        async def call(self, prompt, **kwargs):
            history = list(kwargs.get("message_history") or [])
            payload = {"input": [*history, {"role": "user", "content": prompt}]}
            print(json.dumps(payload), file=protocol_output, flush=True)
            response = json.loads(sys.stdin.readline())
            text = "".join(
                part["text"]
                for item in response.get("output", [])
                if item.get("type") == "message"
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            )
            if response["model"] != initial["model"]:
                raise ValueError("The baseline received a different model")
            if response["status"] == "incomplete":
                raise OutputLengthExceededError("Controller response was incomplete", text)
            if response["status"] != "completed":
                raise RuntimeError("The baseline model call did not complete")
            usage = response["usage"]
            result = LLMResponse(
                content=text,
                model_name=response["model"],
                usage=UsageInfo(
                    prompt_tokens=usage["input_tokens"],
                    completion_tokens=usage["output_tokens"],
                    cache_tokens=usage.get("input_tokens_details", {}).get("cached_tokens", 0),
                    cost_usd=response_cost(usage, contract),
                ),
            )
            # All history is explicit. Do not carry provider-side response IDs,
            # which could load context outside the retained request.
            if path := kwargs.get("logging_path"):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(
                    json.dumps({"request": payload, "response": response}, indent=2)
                )
            return result

        def get_model_context_limit(self):
            # Operational admission bound, not an assertion about native context size.
            return context_limit

        def get_model_output_limit(self):
            return output_limit

    logs.mkdir(parents=True, exist_ok=True)
    template_dir = logs / "templates"
    template_dir.mkdir()
    for name, text in templates.items():
        (template_dir / name).write_text(text)

    class Baseline(terminus_type):
        @staticmethod
        def _init_llm(**kwargs):
            return Model()

        def _get_prompt_template_path(self):
            return template_dir / super()._get_prompt_template_path().name

        def _get_timeout_template_path(self):
            return template_dir / super()._get_timeout_template_path().name

    agent = Baseline(logs_dir=logs, model_name=initial["model"])
    environment = LocalTaskEnvironment(logs)
    context = AgentContext()
    try:
        await agent.setup(environment)
        await agent.run(initial["instruction"], environment, context)
    finally:
        (logs / "context.json").write_text(context.model_dump_json(indent=2))
        (logs / "transport.json").write_text(
            json.dumps(
                {
                    "model_contract": contract,
                    "model_transport": "controller Responses pipe; full explicit history",
                    "cost_authority": "controller gateway; local values use the same rate card",
                    "environment": "local commands inside the assigned task container",
                },
                indent=2,
            )
        )


def baseline_main(terminus_type, protocol_output, templates):
    initial = json.loads(sys.stdin.readline())
    if initial["protocol"] != 1:
        raise ValueError("Unsupported research program protocol")
    asyncio.run(
        run_baseline(
            terminus_type, initial, protocol_output, templates, Path("/logs/agent/terminus")
        )
    )
