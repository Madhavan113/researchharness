from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from research_harness.execution import DiscoverySettings
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.optimization.proposer import (
    TOOL_NAMES,
    ProposalError,
    ProposalTask,
    ProposerConfig,
    ResponsesCodingProposer,
    proposal_binding,
)
from research_harness.optimization.workspace import WorkspaceConfig
from research_harness.strategies.sandbox import SandboxConfig
from research_harness.util import canonical_json, digest, write_json

IMAGE = "python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285"
CODE = """def apply(event):
    return {'decision': {'keep_group_ids': event['payload']['required_group_ids']},
            'state': {}}
"""
CHECK = """import runpy
strategy = runpy.run_path('/workspace/candidates/one/strategy.py')
result = strategy['apply']({'payload': {'required_group_ids': ['current']}})
assert result == {'decision': {'keep_group_ids': ['current']}, 'state': {}}
print('candidate checked')
"""


def task_at(tmp_path, **changes):
    feedback = tmp_path / "feedback"
    (feedback / "prior/raw").mkdir(parents=True)
    write_json(
        feedback / "prior/raw/response.json", {"marker": "complete raw trace", "output": ["B", "A"]}
    )
    (feedback / "prior/strategy.py").write_text("# previous complete source\n")
    (feedback / "prior/score.json").write_text('{"quality":0.5}\n')
    return ProposalTask(
        candidate_ids=changes.pop("candidate_ids", ("one",)),
        iteration=1,
        feedback_dir=feedback,
        output=tmp_path / "attempt",
        instructions="Explore development evidence and improve context selection.",
        strategy_contract={
            "apply": "Return {decision,state}; context selects ordered existing keep_group_ids."
        },
        execution_id="a" * 32,
        **changes,
    )


def settings(**changes):
    return DiscoverySettings(
        max_searches=0, max_inspections=0, max_probes=0, deadline_seconds=60, **changes
    )


def workspace_config():
    return WorkspaceConfig(
        sandbox=SandboxConfig(
            image=os.environ.get("RH_TEST_STRATEGY_IMAGE", IMAGE), max_output_bytes=4 * 1024 * 1024
        )
    )


def tool(name, arguments, index):
    return {
        "id": f"fc_{index}",
        "type": "function_call",
        "call_id": f"call_{index}",
        "name": name,
        "arguments": arguments if isinstance(arguments, str) else canonical_json(arguments),
        "status": "completed",
    }


def response(items, index, **changes):
    return {
        "id": f"resp_{index}",
        "model": "gpt-5.4-mini",
        "status": "completed",
        "output": items,
        "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        **changes,
    }


def steps(task, *, docker=False):
    result = [
        ("list_files", {"path": "feedback/", "offset": 0, "limit": 100}),
        ("read_file", {"path": "feedback/prior/raw/response.json", "offset": 0, "limit": 1000}),
        ("write_file", {"path": "feedback/forbidden.py", "content": "no", "encoding": "utf-8"}),
    ]
    for identity in task.candidate_ids:
        for filename, content in [
            ("strategy.py", CODE),
            ("instructions.md", "Use original evidence; preserve the active turn."),
        ]:
            result.append(
                (
                    "write_file",
                    {
                        "path": f"workspace/candidates/{identity}/{filename}",
                        "content": content,
                        "encoding": "utf-8",
                    },
                )
            )
    if docker:
        result += [
            ("write_file", {"path": "workspace/check.py", "content": CHECK, "encoding": "utf-8"}),
            ("run_python", {"path": "workspace/check.py", "arguments": []}),
        ]
    result.append(("submit_candidates", {"candidate_ids": list(task.candidate_ids)}))
    return result


@pytest.fixture
def factory():
    clients = []

    def make(task, calls=None, *, handler=None, config=None, gateway_options=None):
        config = config or ProposerConfig(settings=settings())
        calls = steps(task) if calls is None else calls
        seen = []

        def upstream(request):
            payload = json.loads(request.content)
            seen.append(payload)
            index = len(seen) - 1
            if handler:
                return handler(request, payload, index)
            name, arguments = calls[index]
            reasoning = {
                "type": "reasoning",
                "id": f"rs_{index}",
                "summary": [],
                "encrypted_content": f"opaque-{index}",
                "provider_extension": {"kept": True},
            }
            return httpx.Response(
                200, json=response([reasoning, tool(name, arguments, index)], index)
            )

        client = httpx.Client(transport=httpx.MockTransport(upstream))
        clients.append(client)

        def gateway(task):
            options = {
                "model": config.model,
                "settings": config.settings,
                "binding": proposal_binding(task, config),
                "upstream_base_url": "https://fixture.invalid/v1",
                "client": client,
                "allowed_function_names": set(TOOL_NAMES),
            }
            options.update(gateway_options or {})
            return ResponsesGateway(task.output / "gateway", **options)

        return ResponsesCodingProposer(config, workspace_config(), gateway_factory=gateway), seen

    yield make
    for client in clients:
        client.close()


def assert_archive(output):
    archive = json.loads((output / "archive.json").read_text())
    assert archive == {
        "schema_version": 1,
        "files": {
            p.relative_to(output).as_posix(): digest(p.read_bytes())
            for p in output.rglob("*")
            if p.is_file() and p != output / "archive.json"
        },
    }


def test_raw_feedback_error_repair_exact_submission_and_complete_history(tmp_path, factory):
    task = task_at(tmp_path, candidate_ids=("one", "two"))
    proposer, requests = factory(task)
    result = proposer.propose(task)
    assert result.closed and result.quiescent
    assert set(result.candidates) == {"one", "two"}
    assert all(value.source.read_text() == CODE for value in result.candidates.values())
    assert len(requests) == 8
    assert all(set(tool["name"] for tool in request["tools"]) == TOOL_NAMES for request in requests)
    assert all(tool["strict"] for request in requests for tool in request["tools"])
    outputs = [item for item in requests[-1]["input"] if item.get("type") == "function_call_output"]
    read = json.loads(outputs[1]["output"])
    assert "complete raw trace" in base64.b64decode(read["content"]).decode()
    assert json.loads(outputs[2]["output"])["ok"] is False
    assert json.loads(outputs[3]["output"])["ok"] is True
    for index, request in enumerate(requests[1:], 1):
        prior = json.loads((task.output / f"rounds/round-{index:04d}/response.body").read_bytes())
        assert all(item in request["input"] for item in prior["output"])
    assert str(task.feedback_dir) not in canonical_json(requests)
    assert str(task.output) not in canonical_json(requests)
    usage = json.loads((task.output / "gateway-usage.json").read_text())
    assert usage["status"] == "verified_complete" and usage["total_tokens"] == 880
    assert (
        usage["binding"]["phase"] == "proposer" and usage["upstream_transport"] == "caller-supplied"
    )
    assert_archive(task.output)
    before = digest((task.output / "archive.json").read_bytes())
    assert (
        proposer.recover(task, reason="Controller lost commit acknowledgment")["status"]
        == "already_sealed"
    )
    assert digest((task.output / "archive.json").read_bytes()) == before
    assert_archive(task.output)
    with pytest.raises(FileExistsError):
        proposer.propose(task)
    assert len(requests) == 8


def test_binding_covers_all_feedback_and_controls_without_absolute_paths(tmp_path):
    task = task_at(tmp_path)
    config = ProposerConfig(settings=settings())
    binding = proposal_binding(task, config)
    copy = tmp_path / "copied-feedback"
    shutil.copytree(task.feedback_dir, copy)
    assert (
        proposal_binding(replace(task, feedback_dir=copy, output=tmp_path / "other"), config)
        == binding
    )
    for changed in (
        replace(task, iteration=2),
        replace(task, instructions="Different host instructions"),
        replace(task, candidate_ids=("other",)),
        replace(task, strategy_contract={"different": "contract"}),
    ):
        assert proposal_binding(changed, config).task_sha256 != binding.task_sha256
    (task.feedback_dir / "prior/raw/response.json").write_text('{"raw":"changed"}')
    assert proposal_binding(task, config).task_sha256 != binding.task_sha256


@pytest.mark.parametrize("ids", [("../bad",), ("one", "one"), (), (["bad"],)])
def test_candidate_identifiers_are_fixed_safe_and_unique(tmp_path, ids):
    with pytest.raises(ValueError):
        task_at(tmp_path, candidate_ids=ids)


def test_proposer_has_no_research_tools_and_config_snapshot_cannot_mutate(tmp_path, factory):
    with pytest.raises(ValueError, match="zero research"):
        ProposerConfig(settings=DiscoverySettings())
    task = task_at(tmp_path)
    proposer, _ = factory(task)
    snapshot = proposer.config
    snapshot.settings.max_rounds = 1
    assert proposer.config.settings.max_rounds == 16


def budget_config(tmp_path, task, *, frozen=True):
    from research_harness.evaluation.budget import BudgetLedger, RateCard
    from research_harness.evaluation.dispatch_budget import DispatchBudget, DispatchPolicy

    controls = settings(service_tier="default")
    ledger = BudgetLedger(
        tmp_path / "ledger.json",
        rates=RateCard(
            model="gpt-5.4-mini",
            snapshot="gpt-5.4-mini",
            input_usd_per_million="1",
            output_usd_per_million="5",
            max_input_tokens_per_request=100000,
            price_source_url="https://fixture.invalid/prices",
            price_as_of="2026-09-08",
        ),
        ceiling_usd="10",
    )
    policy = DispatchPolicy(
        mode="fixture", model="gpt-5.4-mini", upstream_base_url="https://fixture.invalid/v1"
    )
    metadata = DispatchBudget.configuration_metadata(ledger, policy=policy, settings=controls)
    config = ProposerConfig(settings=controls, budget_control=metadata if frozen else None)
    budget = DispatchBudget(
        ledger, policy=policy, settings=controls, binding=proposal_binding(task, config)
    )
    return config, budget


@pytest.mark.parametrize("mismatch", ["missing", "unexpected", "changed"])
def test_budget_control_must_match_frozen_configuration_before_gateway_start(
    tmp_path, factory, monkeypatch, mismatch
):
    task = task_at(tmp_path)
    config, budget = budget_config(tmp_path, task, frozen=mismatch != "unexpected")
    if mismatch == "changed":
        budget._metadata["ceiling_nanodollars"] += 1
    proposer, requests = factory(
        task,
        config=config,
        gateway_options={"dispatch_budget": None if mismatch == "missing" else budget},
    )
    monkeypatch.setattr(
        ResponsesGateway, "start", lambda self: pytest.fail("Mismatched gateway must not start")
    )
    with pytest.raises(ProposalError, match="gateway differs"):
        proposer.propose(task)
    assert not requests


def test_budget_metadata_is_frozen_and_independently_verified(tmp_path, factory, monkeypatch):
    task = task_at(tmp_path)
    config, budget = budget_config(tmp_path, task)
    calls = steps(task)

    def handler(request, payload, index):
        name, arguments = calls[index]
        # Budgeted context uses the recorded supported function protocol.
        return httpx.Response(200, json=response([tool(name, arguments, index)], index))

    proposer, requests = factory(
        task, config=config, handler=handler, gateway_options={"dispatch_budget": budget}
    )
    original = proposer.config.budget_control
    config.budget_control["ceiling_nanodollars"] += 1
    snapshot = proposer.config
    snapshot.budget_control["ceiling_nanodollars"] += 1
    assert proposer.config.budget_control == original == budget.metadata()
    assert proposal_binding(task, config) != proposal_binding(task, proposer.config)
    # The fixture gateway factory closes over this caller-owned config as well.
    config.budget_control["ceiling_nanodollars"] = original["ceiling_nanodollars"]
    close_gateway = ResponsesGateway.close

    def changed_readback_after_close(gateway):
        close_gateway(gateway)
        budget._metadata["ceiling_nanodollars"] += 1

    # Independent verification must use the frozen controls, even if the live
    # adapter's metadata readback later differs from its sealed recorded evidence.
    monkeypatch.setattr(ResponsesGateway, "close", changed_readback_after_close)
    proposer.propose(task)
    usage = json.loads((task.output / "gateway-usage.json").read_bytes())
    assert usage["status"] == "verified_complete"
    assert usage["budget_control_verified"] and usage["budget_control"] == original
    assert usage["total_tokens"] == len(requests) * 110 == 660


@pytest.mark.parametrize(
    "options",
    [
        {"allowed_function_names": {"read_file"}},
        {"model": "wrong-model"},
        {"settings": settings(max_rounds=2)},
    ],
)
def test_gateway_control_mismatch_denies_before_dispatch(tmp_path, factory, options):
    task = task_at(tmp_path)
    proposer, requests = factory(task, gateway_options=options)
    with pytest.raises(ProposalError, match="gateway differs") as error:
        proposer.propose(task)
    assert not requests
    assert error.value.artifacts == task.output


@pytest.mark.parametrize(
    "case", ["missing_submit", "incomplete", "spoofed_output", "repeated_call", "budget"]
)
def test_nonterminal_or_replayed_work_fails_without_automatic_retry(tmp_path, factory, case):
    task = task_at(tmp_path)
    config = ProposerConfig(
        settings=settings(max_rounds=2), max_tool_calls=1 if case == "budget" else 64
    )

    def handler(request, payload, index):
        if case == "missing_submit":
            items = [{"type": "message", "role": "assistant", "content": []}]
        elif case == "spoofed_output":
            items = [
                {
                    "type": "function_call_output",
                    "call_id": "fake",
                    "output": "invented host evidence",
                }
            ]
        else:
            items = [
                tool(
                    "list_files",
                    {"path": "feedback/", "offset": 0, "limit": 100},
                    0 if case == "repeated_call" else index,
                )
            ]
        return httpx.Response(
            200,
            json=response(
                items, index, status="incomplete" if case == "incomplete" else "completed"
            ),
        )

    proposer, requests = factory(task, handler=handler, config=config)
    with pytest.raises(ProposalError) as error:
        proposer.propose(task)
    assert error.value.metadata["closed"] and error.value.metadata["quiescent"]
    assert len(requests) == (2 if case in {"repeated_call", "budget"} else 1)
    assert not (task.output / "submitted").exists()
    assert_archive(task.output)


def test_provider_failure_is_one_dispatch_with_saved_unknown_usage(tmp_path, factory):
    task = task_at(tmp_path)
    proposer, requests = factory(
        task,
        handler=lambda *args: httpx.Response(
            503, json={"error": {"message": "fixture unavailable"}}
        ),
    )
    with pytest.raises(ProposalError):
        proposer.propose(task)
    assert len(requests) == 1
    usage = json.loads((task.output / "gateway-usage.json").read_text())
    assert usage["total_tokens"] is None
    assert usage["dispatched_requests"] == 1
    assert_archive(task.output)


def test_invalid_submission_is_repairable_and_cannot_claim_scores(tmp_path, factory):
    task = task_at(tmp_path)
    calls = [("submit_candidates", {"candidate_ids": ["one"], "score": 1})] + steps(task)
    proposer, requests = factory(task, calls)
    proposer.propose(task)
    result = json.loads(
        next(
            item["output"]
            for item in requests[1]["input"]
            if item.get("type") == "function_call_output"
        )
    )
    assert result["ok"] is False
    assert "exactly the requested IDs" in result["error"]


def test_revocation_is_permanent_and_cannot_claim_quiescence_while_active(tmp_path, factory):
    task = task_at(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def handler(*args):
        entered.set()
        assert release.wait(10)
        return httpx.Response(200, json=response([], 0))

    proposer, requests = factory(task, handler=handler)
    with ThreadPoolExecutor() as pool:
        run = pool.submit(proposer.propose, task)
        assert entered.wait(5)
        with pytest.raises(RuntimeError, match="active"):
            proposer.close()
        with pytest.raises(RuntimeError, match="active"):
            proposer.recover(task, reason="not stopped")
        replacement, _ = factory(task)
        with pytest.raises(RuntimeError, match="active proposal owner"):
            replacement.recover(task, reason="Another object cannot replace the active owner")
        release.set()
        with pytest.raises(ProposalError):
            run.result(timeout=10)
    assert (
        proposer.close()
        == proposer.revoke()
        == {"closed": True, "quiescent": True, "revoked": True, "active_attempts": 0}
    )
    with pytest.raises(ProposalError, match="revoked"):
        proposer.propose(replace(task, output=tmp_path / "another"))
    assert len(requests) == 1


@pytest.mark.parametrize("replacement", [False, True])
def test_live_gateway_close_failure_blocks_quiescence_until_owned_resource_recovery(
    tmp_path, factory, monkeypatch, replacement
):
    task = task_at(tmp_path)
    proposer, requests = factory(task)
    make_gateway = proposer.gateway_factory
    gateways = []

    def capture(task):
        gateway = make_gateway(task)
        gateways.append(gateway)
        return gateway

    proposer.gateway_factory = capture
    recoverer = factory(task)[0] if replacement else proposer
    original_close = ResponsesGateway.close

    def failed_close(self):
        raise OSError("Fixture gateway close acknowledgment unavailable")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(ResponsesGateway, "close", failed_close)
            with pytest.raises(ProposalError) as failed:
                proposer.propose(task)
            assert not failed.value.metadata["closed"]
            assert not failed.value.metadata["quiescent"]
            assert gateways[0]._thread.is_alive()
            with pytest.raises(RuntimeError, match="explicit recovery"):
                proposer.close()
            with pytest.raises(ProposalError, match="revoked"):
                proposer.propose(replace(task, output=tmp_path / "another"))
            with pytest.raises(RuntimeError, match="gateway cleanup remains unresolved"):
                recoverer.recover(task, reason="Model loop stopped but gateway cleanup failed")
            assert gateways[0]._thread.is_alive()
            assert not (task.output / "recovery.json").exists()
        proof = recoverer.recover(task, reason="Retry cleanup of the retained owned gateway")
        assert proof["closed"] and proof["quiescent"]
        assert proof["gateway_usage_status"] == "verified_complete"
        assert not gateways[0]._thread.is_alive()
        assert not (task.output / "submitted").exists()
        assert not proof["model_replayed"] and not proof["candidate_replayed"]
        local = json.loads((task.output / "owned-resource-close.json").read_bytes())
        assert local["gateway_closed"] and local["client_closed"]
        assert local["binding"] == proof["binding"]
        assert proposer.close()["quiescent"]
        assert recoverer.recover(task, reason="Already closed") == proof
        assert len(requests) == 6
    finally:
        for gateway in gateways:
            original_close(gateway)


def test_actual_docker_program_execution_before_explicit_submission(tmp_path, factory):
    if not os.environ.get("RH_TEST_STRATEGY_IMAGE"):
        pytest.skip("Set RH_TEST_STRATEGY_IMAGE for actual isolated proposer execution")
    task = task_at(tmp_path)
    proposer, requests = factory(task, steps(task, docker=True))
    result = proposer.propose(task)
    assert result.closed and result.quiescent
    tool_output = json.loads(
        [item for item in requests[-1]["input"] if item.get("type") == "function_call_output"][-1][
            "output"
        ]
    )
    assert tool_output["ok"] is True, tool_output
    assert "candidate checked" in canonical_json(tool_output) or any(
        b"candidate checked" in p.read_bytes()
        for p in (task.output / "workspace-access/tools").rglob("stdout.bin")
    )
    assert any((task.output / "workspace-access/tools").rglob("execution.json"))
    assert_archive(task.output)


def test_process_death_retains_unsealed_model_work_and_recovery_never_replays(tmp_path, factory):
    task = task_at(tmp_path)
    entered, release = threading.Event(), threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers["Content-Length"])))
            entered.set()
            release.wait(10)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    parameters = tmp_path / "parameters.json"
    write_json(
        parameters,
        {
            "feedback": str(task.feedback_dir),
            "output": str(task.output),
            "execution_id": task.execution_id,
            "instructions": task.instructions,
            "strategy_contract": task.strategy_contract,
            "workspace": workspace_config().model_dump(mode="json"),
            "upstream": f"http://127.0.0.1:{server.server_port}/v1",
        },
    )
    script = """
import json,sys
from pathlib import Path
from research_harness.optimization.proposer import ProposalTask,ProposerConfig,ResponsesCodingProposer,proposal_binding,TOOL_NAMES
from research_harness.optimization.workspace import WorkspaceConfig
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.execution import DiscoverySettings
p=json.loads(Path(sys.argv[1]).read_text())
task=ProposalTask(('one',),1,Path(p['feedback']),Path(p['output']),p['instructions'],p['strategy_contract'],p['execution_id'])
config=ProposerConfig(settings=DiscoverySettings(max_searches=0,max_inspections=0,max_probes=0,deadline_seconds=60))
def gateway(task):
    return ResponsesGateway(task.output/'gateway',model=config.model,settings=config.settings,upstream_base_url=p['upstream'],binding=proposal_binding(task,config),allowed_function_names=set(TOOL_NAMES))
ResponsesCodingProposer(config,WorkspaceConfig.model_validate(p['workspace']),gateway_factory=gateway).propose(task)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(parameters)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert entered.wait(10), "Child did not dispatch its bounded fixture model request"
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate(timeout=5)
        assert not (task.output / "archive.json").exists()
        assert not (task.output / "gateway/archive.json").exists()
        proposer, replayed = factory(task)
        proof = proposer.recover(task, reason="Fixture process killed during provider headers")
        assert proof["closed"] and proof["quiescent"]
        assert proof["gateway_usage_status"] == "unknown"
        assert not proof["model_replayed"] and not proof["candidate_replayed"]
        assert proposer.recover(task, reason="Same stopped attempt") == proof
        assert not replayed and len(requests) == 1
        assert not (task.output / "gateway/archive.json").exists()
        assert not (task.output / "submitted").exists()
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=5)
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_escaping_workspace_failure_stops_next_model_dispatch(tmp_path, factory, monkeypatch):
    from research_harness.optimization.workspace import ProposalWorkspace

    task = task_at(tmp_path)
    proposer, requests = factory(task)

    def failed(*args):
        raise ValueError("Workspace requires recovery")

    monkeypatch.setattr(ProposalWorkspace, "call", failed)
    with pytest.raises(ProposalError, match="requires recovery"):
        proposer.propose(task)
    assert len(requests) == 1


def test_recovery_cannot_target_another_execution_or_rehashed_feedback(tmp_path, factory):
    task = task_at(tmp_path)
    proposer, _ = factory(task, handler=lambda *args: httpx.Response(200, json=response([], 0)))
    with pytest.raises(ProposalError):
        proposer.propose(task)
    before = digest((task.output / "archive.json").read_bytes())
    with pytest.raises(ValueError, match="binding changed"):
        proposer.recover(replace(task, execution_id="b" * 32), reason="Wrong reserved attempt")
    (task.feedback_dir / "prior/strategy.py").write_text("# changed feedback\n")
    with pytest.raises(ValueError, match="binding changed"):
        proposer.recover(task, reason="Changed bound bytes")
    assert digest((task.output / "archive.json").read_bytes()) == before


def test_binding_includes_fixed_system_prompt_and_tool_protocol(tmp_path, monkeypatch):
    import research_harness.optimization.proposer as module

    task = task_at(tmp_path)
    config = ProposerConfig(settings=settings())
    original = proposal_binding(task, config)
    monkeypatch.setattr(module, "_SYSTEM", module._SYSTEM + "\nChanged protocol rules")
    assert proposal_binding(task, config).task_sha256 != original.task_sha256


def test_recovery_can_advance_cleanup_append_only_without_admitting_candidates(
    tmp_path, factory, monkeypatch
):
    from research_harness.optimization.workspace import ProposalWorkspace

    task = task_at(tmp_path)
    proposer, requests = factory(
        task, handler=lambda *args: httpx.Response(200, json=response([], 0))
    )
    with pytest.raises(ProposalError):
        proposer.propose(task)
    # Fixture for a stopped attempt whose cleanup acknowledgment was unavailable.
    (task.output / "archive.json").unlink()
    state = json.loads((task.output / "workspace-close.json").read_text())
    first = {**state, "quiescent": False, "snapshot_valid": False, "errors": ["Cleanup unknown"]}
    later = {**first, "quiescent": True, "errors": ["Snapshot remains invalid after interruption"]}
    outcomes = iter([first, first, later, later])
    monkeypatch.setattr(
        ProposalWorkspace, "recover", classmethod(lambda cls, *args: next(outcomes))
    )
    failed = proposer.recover(task, reason="Docker cleanup acknowledgment unavailable")
    original = (task.output / "recovery.json").read_bytes()
    assert failed["closed"] and not failed["quiescent"]
    assert proposer.recover(task, reason="Same uncertain cleanup") == failed
    completed = proposer.recover(task, reason="Owned container removal now confirmed")
    assert completed["closed"] and completed["quiescent"]
    assert completed["workspace"]["snapshot_valid"] is False
    assert completed["previous_recovery_sha256"] == digest(original)
    assert (task.output / "recovery.json").read_bytes() == original
    assert json.loads((task.output / "recovery-0002.json").read_text()) == completed
    assert proposer.recover(task, reason="Already confirmed") == completed
    assert len(requests) == 1 and not (task.output / "submitted").exists()
