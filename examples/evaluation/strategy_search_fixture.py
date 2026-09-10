"""Complete code-strategy search through actual Omnigent and Docker, with fixtures.

The provider responses are authored software fixtures. This exercises the real
coding proposer, controller, research runtime and private final evaluator, but
does not measure model quality or cost improvement. No paid provider is used.
"""

from __future__ import annotations

import argparse
import base64
import json
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

import httpx
from search_runtime_fixtures import ResearchModelFixture, benchmark

from research_harness.evaluation.controller import ComparisonConfig, source_fingerprints
from research_harness.evaluation.gateway_usage import verify_gateway_usage
from research_harness.evaluation.runtime_executor import RuntimeExecutor, task_strategy_session
from research_harness.execution import DiscoverySettings
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.optimization.archive import _inventory
from research_harness.optimization.controller import SearchConfig, SearchController
from research_harness.optimization.proposer import (
    TOOL_NAMES,
    ProposalError,
    ProposerConfig,
    ResponsesCodingProposer,
    proposal_binding,
)
from research_harness.optimization.workspace import WorkspaceConfig
from research_harness.strategies.config import StrategyBundle, StrategyConfig
from research_harness.strategies.sandbox import SandboxConfig
from research_harness.util import canonical_json, digest, write_json

DOMAIN_TOOLS = {
    "begin_research",
    "get_research_context",
    "search_sources",
    "inspect_source",
    "probe_source",
    "get_evidence",
    "submit_proposal",
    "list_pipelines",
}
BASELINE = """def apply(event):
    payload = event['payload']
    if event['kind'] == 'context':
        decision = {'keep_group_ids': [group['id'] for group in payload['groups']]}
    else:
        decision = {'order': [item['id'] for item in payload['items']]}
    return {'decision': decision, 'state': {'calls': event['state'].get('calls', 0) + 1}}
"""
INSTRUCTIONS = """Find sources for the user's brief. Inspect and probe the actual source,
retain original evidence, and submit a validated proposal with honest limitations.
"""
CHECK = """import runpy
from pathlib import Path
for candidate in sorted(Path('/workspace/candidates').iterdir()):
    apply = runpy.run_path(str(candidate / 'strategy.py'))['apply']
    context = apply({'kind': 'context', 'state': {}, 'payload': {
        'groups': [{'id': 'old', 'required': False}, {'id': 'current', 'required': True}],
        'required_group_ids': ['current']}})
    assert 'current' in context['decision']['keep_group_ids']
    observation = apply({'kind': 'observation', 'state': {}, 'payload': {
        'items': [{'id': 'one', 'value': {'title': 'B'}}, {'id': 'two', 'value': {'title': 'A'}}]}})
    assert sorted(observation['decision']['order']) == ['one', 'two']
    print('checked', candidate.name)
"""


def read(path):
    return json.loads(path.read_bytes())


def candidate_code(iteration, slot):
    # Deliberately authored alternatives; fixed synthetic usage does not reward
    # either policy or establish that these algorithms improve research.
    keep = (
        "[group['id'] for group in payload['groups']]"
        if slot == 0
        else "payload['required_group_ids']"
    )
    return f"""import json
def apply(event):
    payload = event['payload']
    if event['kind'] == 'context':
        decision = {{'keep_group_ids': {keep}}}
    else:
        items = sorted(payload['items'], key=lambda item: json.dumps(item['value'], sort_keys=True), reverse={bool(slot)})
        decision = {{'order': [item['id'] for item in items]}}
    return {{'decision': decision, 'state': {{'calls': event['state'].get('calls', 0) + 1, 'iteration': {iteration}}}}}
"""


class CodingModelFixture:
    """Select an actual listed raw response, then write/check/submit candidate code.

    This fixture receives the same public task identifiers as the model and
    obtains feedback paths/content only from returned workspace tool results.
    """

    def __init__(self, task):
        self.ids, self.iteration = task.candidate_ids, task.iteration
        self.requests, self.responses = [], []
        self.read_path, self.raw_trace = None, None
        self.queue = []
        for slot, identity in enumerate(self.ids):
            for filename, content in (
                ("strategy.py", candidate_code(self.iteration, slot)),
                ("instructions.md", INSTRUCTIONS),
            ):
                self.queue.append(
                    (
                        "write_file",
                        {
                            "path": f"workspace/candidates/{identity}/{filename}",
                            "content": content,
                            "encoding": "utf-8",
                        },
                    )
                )
        self.queue += [
            ("write_file", {"path": "workspace/check.py", "content": CHECK, "encoding": "utf-8"}),
            ("run_python", {"path": "workspace/check.py", "arguments": []}),
            ("submit_candidates", {"candidate_ids": list(self.ids)}),
        ]
        self.previous = None

    def respond(self, payload):
        self.requests.append(payload)
        if self.previous is None:
            name, arguments = "list_files", {"path": "feedback/", "offset": 0, "limit": 1000}
        else:
            outputs = [row for row in payload["input"] if row.get("type") == "function_call_output"]
            result = json.loads(outputs[-1]["output"])
            assert result["ok"], result
            if self.previous == "list_files":
                paths = [
                    row["path"] for row in result["files"] if row["path"].endswith("/response.body")
                ]
                if paths:
                    self.read_path = paths[0]
                    name, arguments = (
                        "read_file",
                        {"path": self.read_path, "offset": 0, "limit": 45000},
                    )
                else:
                    assert result["next_offset"] is not None, (
                        "No complete recorded raw response found"
                    )
                    name, arguments = (
                        "list_files",
                        {"path": "feedback/", "offset": result["next_offset"], "limit": 1000},
                    )
            else:
                if self.previous == "read_file":
                    self.raw_trace = base64.b64decode(result["content"])
                    assert self.raw_trace and result["next_offset"] is None
                name, arguments = self.queue.pop(0)
        self.previous = name
        index = len(self.responses)
        response = {
            "id": f"proposer_{self.iteration}_response_{index}",
            "model": payload["model"],
            "status": "completed",
            "output": [
                {
                    "id": f"fc_{index}",
                    "type": "function_call",
                    "call_id": f"call_{index}",
                    "name": name,
                    "arguments": canonical_json(arguments),
                    "status": "completed",
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        }
        self.responses.append(response)
        return response


def run(output: Path, *, omnigent_python: Path, image: str) -> dict:
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Keep the virtualenv executable path; resolving its symlink loses the venv.
    omnigent_python = omnigent_python.expanduser().absolute()
    assert omnigent_python.is_file(), "Install the pinned separate Omnigent runtime first"
    sandbox = SandboxConfig(image=image)
    frozen_source = source_fingerprints()
    development = benchmark(output / "development-package", split="development")
    baseline = output / "baseline"
    baseline.mkdir()
    (baseline / "strategy.py").write_text(BASELINE)
    strategy = StrategyConfig(source_sha256=digest(BASELINE), sandbox=sandbox, context=True)
    write_json(baseline / "strategy.json", strategy.model_dump(mode="json"))
    config = SearchConfig(
        run_id="actual-runtime-search-fixture",
        comparison=ComparisonConfig(
            execution="fixture", settings=DiscoverySettings(max_rounds=10, deadline_seconds=120)
        ),
        proposer=ProposerConfig(
            settings=DiscoverySettings(
                max_searches=0, max_inspections=0, max_probes=0, max_rounds=16, deadline_seconds=120
            )
        ),
        workspace=WorkspaceConfig(sandbox=sandbox),
    )
    controller = SearchController.create(
        output / "search",
        output / "development-feedback",
        development_manifest=development,
        baseline=StrategyBundle.load(baseline / "strategy.json"),
        instructions=INSTRUCTIONS,
        config=config,
    )
    execution_rows, models, tasks = [], [], []

    def execute(task):
        phase = "final" if task.output.is_relative_to(output / "private-final") else "development"
        print(f"{phase}: {task.output.parent.parent.name}/{task.case_id}", flush=True)
        session = task_strategy_session(task)
        source_url = read(task.fixture_path)["responses"][0]["url"]
        model = ResearchModelFixture(task.brief, source_url)

        def upstream(request):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=model.respond(json.loads(request.content)),
            )

        with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
            gateway = ResponsesGateway(
                task.output / "gateway",
                model=task.config.model,
                settings=task.config.settings,
                upstream_base_url="https://fixture.invalid/v1",
                client=client,
                allowed_function_names={f"research__{name}" for name in DOMAIN_TOOLS},
                binding=task.gateway_binding,
                strategy=session,
            )
            try:
                with gateway:
                    result = RuntimeExecutor(
                        base_url=gateway.base_url,
                        api_key=gateway.api_key,
                        omnigent_python=omnigent_python,
                        max_spend_usd=1,
                    )(task)
            except Exception as exc:
                if getattr(exc, "artifacts", None) is not None:
                    exc.artifacts = replace(
                        exc.artifacts,
                        attachments=(*exc.artifacts.attachments, gateway.output),
                        gateway_usage=gateway.output / "archive.json"
                        if (gateway.output / "archive.json").is_file()
                        else None,
                    )
                raise
        session.assert_ready()
        usage = verify_gateway_usage(
            gateway.output,
            task.gateway_binding,
            expected_model=task.config.model,
            expected_model_settings=task.config.settings.model_settings(),
            expected_budgets=task.config.settings.budgets(),
            expected_strategy_sha256=session.bundle.sha256,
        )
        assert usage["status"] == "verified_complete", usage["errors"]
        execution_rows.append(
            {
                "phase": phase,
                "case_id": task.case_id,
                "output": str(task.output.relative_to(output)),
                "strategy_sha256": session.bundle.sha256,
                "provider_requests": len(model.requests),
                "synthetic_tokens": usage["total_tokens"],
                "strategy_executions": len(session.status()["events"]),
                "execution_verified_as_model": False,
            }
        )
        write_json(output / "runtime-progress.json", {"executions": execution_rows})
        print(
            f"  completed: {len(model.requests)} requests, {usage['total_tokens']} synthetic tokens",
            flush=True,
        )
        return replace(
            result,
            gateway_usage=gateway.output / "archive.json",
            attachments=(*result.attachments, gateway.output),
        )

    with ExitStack() as stack:

        def proposer_gateway(task):
            print(
                f"proposer: iteration {task.iteration}, {len(task.candidate_ids)} candidates",
                flush=True,
            )
            model = CodingModelFixture(task)
            models.append(model)
            tasks.append(task)
            client = stack.enter_context(
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200, json=model.respond(json.loads(request.content))
                        )
                    )
                )
            )
            return ResponsesGateway(
                task.output / "gateway",
                model=config.proposer.model,
                settings=config.proposer.settings,
                upstream_base_url="https://fixture.invalid/v1",
                client=client,
                allowed_function_names=set(TOOL_NAMES),
                binding=proposal_binding(task, config.proposer),
            )

        proposer = ResponsesCodingProposer(
            config.proposer, config.workspace, gateway_factory=proposer_gateway
        )
        selected = controller.run(executor=execute, proposer=proposer)
        assert selected["phase"] == "selected" and len(selected["candidates"]) == 7
        assert all(row["status"] == "evaluated" for row in selected["candidates"].values())
        assert len(models) == 3 and all(model.read_path and model.raw_trace for model in models)
        assert len(execution_rows) == 7
        assert selected["proposer_revocation"]["revoked"] is True
        assert source_fingerprints() == frozen_source
        # A revoked proposer must reject access before any new dispatch.
        try:
            proposer.propose(tasks[-1])
        except ProposalError as exc:
            assert "permanently revoked" in str(exc)
        else:
            raise AssertionError("Search left proposer access live")
        snapshots = {
            str(task.feedback_dir): _inventory(task.feedback_dir, controller._load()[3])
            for task in tasks
        }
        print(
            "selection frozen; proposer revoked; creating private authored final package",
            flush=True,
        )
        heldout = benchmark(output / "private-heldout-package", split="heldout")
        final = controller.final(
            heldout_manifest=heldout, output=output / "private-final", executor=execute
        )
        assert final["status"] == "completed"
        assert set(final["candidate_ids"]) == set(
            selected["archive"]["selection"]["candidate_ids"]
        ) | {"baseline"}
        assert all(
            row["summary"]["macro_quality"] == 1 and row["summary"]["execution_verified"] is False
            for row in final["candidates"]
        )
        before = len(execution_rows), [len(model.requests) for model in models]
        assert (
            controller.final(
                heldout_manifest=heldout, output=output / "private-final", executor=execute
            )
            == final
        )
        assert controller.run(executor=execute, proposer=proposer)["phase"] == "finalized"
        assert before == (len(execution_rows), [len(model.requests) for model in models])
        for task in tasks:
            assert (
                _inventory(task.feedback_dir, controller._load()[3])
                == snapshots[str(task.feedback_dir)]
            )
        private_case = read(heldout.parent / read(heldout)["cases"][0]["path"])
        for tree in [*(task.feedback_dir for task in tasks), *(task.output for task in tasks)]:
            for path in tree.rglob("*"):
                if path.is_file():
                    assert private_case["brief"].encode() not in path.read_bytes(), path
        assert source_fingerprints() == frozen_source
        summary = {
            "schema_version": 1,
            "status": "passed",
            "paid_calls": 0,
            "iterations": 3,
            "candidates_per_iteration": 2,
            "actual_omnigent": True,
            "actual_coding_proposer": True,
            "actual_docker_strategies": True,
            "research_executions": execution_rows,
            "proposer_requests": [len(model.requests) for model in models],
            "proposer_raw_trace_paths": [model.read_path for model in models],
            "development_candidates": 7,
            "final_candidates": len(final["candidate_ids"]),
            "selection_frozen_before_final": True,
            "proposer_revoked_before_final": True,
            "final_retries_did_not_execute": True,
            "private_final_absent_from_proposer_files": True,
            "source_fingerprints": frozen_source,
            "limitations": [
                "Authored source/model fixtures establish combined software behavior, not model quality or optimization gains.",
                "Human review, live provider access, the pending spending decision and a measured baseline/search/final run remain required.",
                "Complete raw outputs are in this run directory; private-final and private-heldout-package must never be mounted as search feedback or published as development artifacts.",
            ],
        }
        write_json(output / "acceptance.json", summary)
    print(canonical_json(summary), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--omnigent-python", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    run(args.out, omnigent_python=args.omnigent_python, image=args.image)
