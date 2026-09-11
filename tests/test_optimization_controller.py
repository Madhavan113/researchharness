"""Coordinator software fixtures; actual model/runtime acceptance is separate."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
from test_optimization_proposer import response, tool
from test_research_controller import fixture_executor
from test_strategy_session import CODE, bundle
from test_strategy_session import fake_runner as fake_runner

from research_harness.evaluation import controller as comparison_module
from research_harness.evaluation.controller import ComparisonConfig
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.optimization.controller import SearchConfig, SearchController
from research_harness.optimization.proposer import (
    TOOL_NAMES,
    CandidateFiles,
    ProposalResult,
    ProposerConfig,
    ResponsesCodingProposer,
    proposal_binding,
)
from research_harness.optimization.workspace import WorkspaceConfig
from research_harness.util import digest, write_json

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_bytes())


@pytest.fixture
def search(tmp_path, monkeypatch, fake_runner, request):
    # Freeze a stable backend for these coordinator-only fixtures while other
    # independent agent modules are edited. Runtime acceptance freezes all code.
    backend = ROOT / "src/research_harness/config.py"
    monkeypatch.setattr(
        comparison_module,
        "source_fingerprints",
        lambda: {"src/research_harness/config.py": digest(backend.read_bytes())},
    )
    package = tmp_path / "development"
    shutil.copytree(ROOT / "examples/evaluation/development", package)
    manifest = read(package / "manifest.json")
    manifest["cases"] = [
        ref for ref in manifest["cases"] if Path(ref["path"]).stem == "export-notices"
    ]
    if getattr(request, "param", None) == "zero-quality":
        reference = manifest["cases"][0]
        path = package / reference["path"]
        case = read(path)
        case["specification"]["requirements"][0]["any_of"][0]["url"] += "/unmatched"
        write_json(path, case)
        reference["sha256"] = digest(path.read_bytes())
    write_json(package / "manifest.json", manifest)
    code = bundle(tmp_path)
    config = SearchConfig(
        run_id="three-by-two-fixture",
        arm="direct",
        comparison=ComparisonConfig(execution="fixture"),
        proposer=ProposerConfig(),
        workspace=WorkspaceConfig(sandbox=code.config.sandbox, max_files=64),
    )
    return SearchController.create(
        tmp_path / "host",
        tmp_path / "feedback",
        development_manifest=package / "manifest.json",
        baseline=code,
        instructions="Shared fixture instructions.\n",
        config=config,
    )


class Executor:
    def __init__(self):
        self.calls = []

    def __call__(self, task):
        self.calls.append((task.code_strategy_sha256, task.case_id))
        result = fixture_executor(task)
        proposal = read(result.research / "proposal.json")
        proposal["usage"] = {"input_tokens": 10, "output_tokens": 1}
        write_json(result.research / "proposal.json", proposal)
        (result.research / "full-raw-trace.txt").write_text(
            "Complete authored trace for inspection.\n"
        )
        return result


class Proposer:
    def __init__(self, controller, *, interrupt=False, invalid=False):
        config = SearchConfig.model_validate(read(controller.root / "search.json")["config"])
        self.config, self.workspace_config = config.proposer, config.workspace
        self.calls, self.snapshots = [], []
        self.interrupt, self.invalid, self.closed = interrupt, invalid, False

    def propose(self, task):
        assert not self.closed
        self.calls.append(task.execution_id)
        self.snapshots.append(task.feedback_dir)
        assert list(task.feedback_dir.rglob("full-raw-trace.txt"))
        assert not list(task.feedback_dir.rglob("*private-evaluator*"))
        assert not (task.feedback_dir / "benchmark").exists()
        task.output.mkdir(parents=True)
        binding = proposal_binding(task, self.config).model_dump(mode="json")
        reservation = read(task.output.parents[1] / "journal.json")["proposals"][
            f"iteration-{task.iteration:04d}"
        ]
        assert reservation["binding"] == binding
        write_json(task.output / "task.json", {"binding": binding})
        write_json(
            task.output / "raw-proposal-turn.json",
            {"fixture": True, "execution_id": task.execution_id},
        )
        if self.interrupt:
            raise KeyboardInterrupt("Fixture interruption after possible provider dispatch")
        candidates = {}
        for i, identity in enumerate(task.candidate_ids):
            target = task.output / "workspace/candidates" / identity
            target.mkdir(parents=True)
            source, instructions = target / "strategy.py", target / "instructions.md"
            source.write_text(
                "def invalid syntax\n" if self.invalid and i == 0 else CODE + f"\n# {identity}\n"
            )
            instructions.write_text(f"Research instructions for {identity}.\n")
            candidates[identity] = CandidateFiles(source, instructions)
        write_json(task.output / "closed.json", {"closed": True, "quiescent": True})
        return ProposalResult(candidates, task.output, True, True, {"fixture": True})

    def recover(self, task, *, reason):
        write_json(task.output / "interruption.json", {"reason": reason, "model_replayed": False})
        return {
            "closed": True,
            "quiescent": True,
            "usage": "unknown",
            "binding": proposal_binding(task, self.config).model_dump(mode="json"),
        }

    def close(self):
        self.closed = True
        return {"closed": True, "quiescent": True}


def test_three_iterations_two_candidates_preserve_experience_and_close_search(search):
    executor, proposer = Executor(), Proposer(search)
    # A private independent evaluator canary must never enter proposer snapshots.
    (search.root / "private-evaluator-canary.txt").write_text("host-private-evaluator")
    result = search.run(executor=executor, proposer=proposer)
    assert result["phase"] == "selected" and proposer.closed
    assert len(executor.calls) == 7 and len(proposer.calls) == 3
    assert len(result["archive"]["selection"]["candidate_ids"]) == 7
    assert all(c["status"] == "evaluated" for c in result["candidates"].values())
    assert all(c["execution_verified"] is False for c in result["archive"]["candidates"].values())
    assert len(list(proposer.snapshots[2].glob("proposal-attempts/*/raw-proposal-turn.json"))) == 2
    assert len(list(proposer.snapshots[2].glob("candidates/*/development/evidence.json"))) == 5
    before = len(executor.calls), len(proposer.calls)
    reopened = SearchController(search.root)
    assert reopened.run(executor=executor, proposer=proposer)["phase"] == "selected"
    assert (len(executor.calls), len(proposer.calls)) == before
    with pytest.raises(ValueError, match="closed"):
        reopened.archive.feedback_path()


def test_invalid_candidate_is_retained_and_does_not_trigger_evaluation(search):
    executor, proposer = Executor(), Proposer(search, invalid=True)
    result = search.run(executor=executor, proposer=proposer)
    assert len(executor.calls) == 4
    invalid = [c for c in result["candidates"].values() if c["status"] == "invalid"]
    assert len(invalid) == 3
    for candidate in invalid:
        assert (
            search.root / candidate["attempt"] / "source/strategy.py"
        ).read_text() == "def invalid syntax\n"
        assert read(search.root / candidate["leakage_audit"])["status"] == "no_match"
    assert len(list(proposer.snapshots[-1].glob("candidate-attempts/*/admission.json"))) == 4


def test_development_leak_findings_survive_admission_restart_and_proposer_feedback(search):
    url = "https://export-office.fixture.example/notices.json"

    class LeakingProposer(Proposer):
        def propose(self, task):
            result = super().propose(task)
            for files in result.candidates.values():
                files.source.write_text(files.source.read_text() + f"\n# {url}\n")
                files.instructions.write_text(f"Use the export-notices case at {url}.\n")
            return result

    executor, proposer = Executor(), LeakingProposer(search)
    assert search.step(executor=executor)["status"] == "evaluated"
    assert search.status()["candidates"]["baseline"]["leak_suspect"] is False
    assert search.step(executor=executor, proposer=proposer)["status"] == "proposed"
    before = search.status()["candidates"]["candidate-0001-01"]
    assert before["status"] == "prepared" and before["leak_suspect"]
    report = read(search.root / before["leakage_audit"])
    assert any(f["kind"] == "url" and f["term"] == url for f in report["findings"])
    assert report["file_sha256"]["strategy.py"] == digest(
        (search.root / before["source"] / "strategy.py").read_bytes()
    )
    admission = read(search.root / before["attempt"] / "admission.json")
    assert (
        admission["leak_suspect"]
        and admission["leakage_audit_sha256"] == before["leakage_audit_sha256"]
    )
    reopened = SearchController(search.root)
    assert reopened.status()["candidates"]["candidate-0001-01"] == before
    result = reopened.run(executor=executor, proposer=proposer)
    assert len(executor.calls) == 7 and len(proposer.calls) == 3
    assert all(c["status"] == "evaluated" for c in result["candidates"].values())
    assert "candidate-0001-01" in result["archive"]["selection"]["candidate_ids"]
    assert read(proposer.snapshots[1] / before["leakage_audit"]) == report
    assert (proposer.snapshots[0] / "candidate-audits/baseline.json").is_file()
    assert not list(proposer.snapshots[-1].rglob("leakage-audit-inputs.json"))
    assert (search.root / "leakage-audit-inputs.json").is_file()


@pytest.mark.parametrize("path", ["leakage-audit-inputs.json", "candidate-audits/baseline.json"])
def test_reopened_search_refuses_changed_audit_catalog_or_report(search, path):
    target = search.root / path
    original = target.read_bytes()
    target.write_bytes(original + b"\n")
    with pytest.raises(ValueError, match="audit.*changed"):
        SearchController(search.root)
    target.write_bytes(original)
    assert SearchController(search.root).status()["phase"] == "search"


def test_interrupted_proposer_requires_recovery_and_never_replays(search):
    executor, proposer = Executor(), Proposer(search, interrupt=True)
    assert search.step(executor=executor)["status"] == "evaluated"
    with pytest.raises(KeyboardInterrupt):
        search.step(executor=executor, proposer=proposer)
    reopened = SearchController(search.root)
    with pytest.raises(ValueError, match="explicit recovery"):
        reopened.step(executor=executor, proposer=proposer)
    assert len(proposer.calls) == 1
    recovered = reopened.recover_proposal(1, proposer=proposer, reason="Stopped fixture proposer")
    assert recovered["status"] == "failed" and recovered["recovery"]["usage"] == "unknown"
    proposer.interrupt = False
    result = reopened.run(executor=executor, proposer=proposer)
    assert len(proposer.calls) == 3 and len(executor.calls) == 5
    assert sum(c["status"] == "proposal_failed" for c in result["candidates"].values()) == 2
    assert list(proposer.snapshots[-1].rglob("interruption.json"))


def test_lost_evaluation_ack_does_not_rerun_case_or_change_evidence(search):
    executor = Executor()
    assert search.step(executor=executor)["status"] == "evaluated"
    journal = read(search.root / "journal.json")
    journal["candidates"]["baseline"]["status"] = "evaluating"
    write_json(search.root / "journal.json", journal)
    evidence = search.root / "archive/candidates/baseline/development/evidence.json"
    before = evidence.read_bytes()
    assert SearchController(search.root).step(executor=executor)["status"] == "evaluated"
    assert len(executor.calls) == 1 and evidence.read_bytes() == before


def test_unknown_baseline_cost_blocks_generation(search):
    proposer = Proposer(search)

    def failure(task):
        raise RuntimeError("Fixture provider outcome unknown")

    assert search.step(executor=failure)["status"] == "evaluated"
    with pytest.raises(ValueError, match="baseline is not eligible"):
        search.step(executor=failure, proposer=proposer)
    assert not proposer.calls


def test_changed_feedback_is_detected_before_next_model_call(search):
    executor, proposer = Executor(), Proposer(search)
    search.step(executor=executor)
    search.step(executor=executor, proposer=proposer)
    (proposer.snapshots[0] / "unexpected.txt").write_text("changed")
    with pytest.raises(ValueError, match="Immutable proposal input"):
        SearchController(search.root)
    assert len(proposer.calls) == 1


def test_frozen_proposer_settings_and_search_budget_are_enforced(search):
    executor, proposer = Executor(), Proposer(search)
    search.step(executor=executor)
    proposer.config = ProposerConfig(model="foreign-model")
    with pytest.raises(ValueError, match="Proposer settings"):
        search.step(executor=executor, proposer=proposer)
    assert not proposer.calls
    with pytest.raises(ValueError, match="configured search iterations"):
        search.select(proposer=proposer)


def test_final_does_not_read_heldout_before_search_and_revocation(search, tmp_path):
    with pytest.raises(ValueError, match="revocation"):
        search.final(
            heldout_manifest=tmp_path / "must-not-be-read.json",
            output=tmp_path / "final",
            executor=Executor(),
        )
    assert not (tmp_path / "final").exists()


@pytest.mark.parametrize("search", ["zero-quality"], indirect=True)
def test_ineligible_baseline_does_not_start_controller_final(search, tmp_path):
    executor, proposer = Executor(), Proposer(search)
    state = search.run(executor=executor, proposer=proposer)
    assert state["archive"]["selection"]["excluded"]["baseline"] == "zero_quality"
    before = (search.root / "journal.json").read_bytes()
    calls = len(executor.calls)
    with pytest.raises(ValueError, match="baseline.*ineligible"):
        search.final(
            heldout_manifest=tmp_path / "must-not-be-read.json",
            output=tmp_path / "private-final",
            executor=executor,
        )
    assert (search.root / "journal.json").read_bytes() == before
    assert search.status()["phase"] == "selected"
    assert search.archive.status()["final"] is None
    assert len(executor.calls) == calls
    assert not (tmp_path / "private-final").exists()


def test_repeat_selection_preserves_completed_private_final_phase(search, tmp_path):
    from test_optimization_final import package as make_package

    executor, proposer = Executor(), Proposer(search)
    search.run(executor=executor, proposer=proposer)
    package = tmp_path / "heldout"
    make_package(package, ["weather-alert-feed"], split="heldout")
    final = search.final(
        heldout_manifest=package / "manifest.json",
        output=tmp_path / "private-final",
        executor=executor,
    )
    assert final["split"] == "heldout" and final["status"] == "completed"
    assert len(final["candidate_ids"]) == 7
    before = (search.root / "journal.json").read_bytes()
    calls = len(executor.calls)
    assert search.select()["status"] == "finalized"
    assert search.run(executor=executor, proposer=proposer)["phase"] == "finalized"
    assert (
        search.final(
            heldout_manifest=package / "manifest.json",
            output=tmp_path / "private-final",
            executor=executor,
        )
        == final
    )
    with pytest.raises(ValueError, match="Final attempt is already bound"):
        search.final(
            heldout_manifest=package / "manifest.json",
            output=tmp_path / "private-final",
            executor=executor,
            operation_id="different-final",
        )
    assert (search.root / "journal.json").read_bytes() == before
    assert len(executor.calls) == calls


def test_foreign_proposer_task_artifacts_are_not_admitted(search):
    executor, proposer = Executor(), Proposer(search)
    search.step(executor=executor)
    original = proposer.propose

    def foreign_task(task):
        result = original(task)
        record = read(task.output / "task.json")
        record["binding"]["execution_id"] = "f" * 32
        write_json(task.output / "task.json", record)
        return result

    proposer.propose = foreign_task
    with pytest.raises(ValueError, match="task artifact differs"):
        search.step(executor=executor, proposer=proposer)
    status = search.status()
    assert status["proposals"]["iteration-0001"]["status"] == "unresolved"
    assert set(status["candidates"]) == {"baseline"}
    assert len(proposer.calls) == 1 and len(executor.calls) == 1


def test_changed_proposer_protocol_cannot_rebind_recovery(search, monkeypatch):
    from research_harness.optimization import proposer as proposer_module

    executor, proposer = Executor(), Proposer(search, interrupt=True)
    search.step(executor=executor)
    with pytest.raises(KeyboardInterrupt):
        search.step(executor=executor, proposer=proposer)
    monkeypatch.setattr(proposer_module, "_SYSTEM", "Changed coding protocol")
    with pytest.raises(ValueError, match="reserved binding"):
        search.recover_proposal(1, proposer=proposer, reason="Stopped owner")
    assert not (search.root / "proposals/iteration-0001/interruption.json").exists()
    assert len(proposer.calls) == 1


def test_foreign_recovery_proof_cannot_close_an_attempt(search):
    executor, proposer = Executor(), Proposer(search, interrupt=True)
    search.step(executor=executor)
    with pytest.raises(KeyboardInterrupt):
        search.step(executor=executor, proposer=proposer)
    proposer.recover = lambda task, **kw: {"closed": True, "quiescent": True, "binding": {}}
    with pytest.raises(ValueError, match="recovery differs"):
        search.recover_proposal(1, proposer=proposer, reason="Stopped owner")
    assert search.status()["proposals"]["iteration-0001"]["status"] == "unresolved"
    assert len(proposer.calls) == 1


@pytest.mark.parametrize("tampered", [False, True])
def test_controller_independently_checks_actual_coding_gateway(search, tampered):
    config = SearchConfig.model_validate(read(search.root / "search.json")["config"])
    seen = []
    calls = []

    def upstream(request):
        seen.append(json.loads(request.content))
        index = len(seen) - 1
        name, arguments = calls[index]
        return httpx.Response(200, json=response([tool(name, arguments, index)], index))

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:

        def gateway(task):
            for identity in task.candidate_ids:
                for filename, content in (("strategy.py", CODE), ("instructions.md", "Fixture")):
                    calls.append(
                        (
                            "write_file",
                            {
                                "path": f"workspace/candidates/{identity}/{filename}",
                                "content": content,
                                "encoding": "utf-8",
                            },
                        )
                    )
            calls.append(("submit_candidates", {"candidate_ids": list(task.candidate_ids)}))
            return ResponsesGateway(
                task.output / "gateway",
                model=config.proposer.model,
                settings=config.proposer.settings,
                binding=proposal_binding(task, config.proposer),
                upstream_base_url="https://fixture.invalid/v1",
                client=client,
                allowed_function_names=set(TOOL_NAMES),
            )

        proposer = ResponsesCodingProposer(
            config.proposer, config.workspace, gateway_factory=gateway
        )
        original = proposer.propose

        def propose(task):
            result = original(task)
            if tampered:
                path = task.output / "gateway/gateway.json"
                record = read(path)
                record["binding"]["execution_id"] = "f" * 32
                write_json(path, record)
            return result

        proposer.propose = propose
        executor = Executor()
        search.step(executor=executor)
        if tampered:
            with pytest.raises(ValueError, match="gateway evidence failed"):
                search.step(executor=executor, proposer=proposer)
            assert set(search.status()["candidates"]) == {"baseline"}
        else:
            assert search.step(executor=executor, proposer=proposer)["status"] == "proposed"
            evidence = search.status()["proposals"]["iteration-0001"]["gateway_usage"]
            assert evidence["status"] == "verified_complete"
            assert evidence["total_tokens"] == 550
            assert evidence["upstream_transport"] == "caller-supplied"
        assert len(seen) == 5 and len(executor.calls) == 1
        assert proposer.close()["quiescent"] is True


def test_candidate_preparation_recovery_preserves_partial_files_without_execution(search):
    executor, proposer = Executor(), Proposer(search)
    search.step(executor=executor)
    search.step(executor=executor, proposer=proposer)
    journal = read(search.root / "journal.json")
    identity = "candidate-0001-01"
    journal["candidates"][identity]["status"] = "preparing"
    write_json(search.root / "journal.json", journal)
    with pytest.raises(ValueError, match="unresolved"):
        search.step(executor=executor, proposer=proposer)
    assert (
        search.recover_candidate(identity, reason="Fixture interrupted preparation")["status"]
        == "invalid"
    )
    assert len(executor.calls) == 1


def test_admission_reservation_failure_leaves_no_unjournaled_directory(search, monkeypatch):
    executor, proposer = Executor(), Proposer(search)
    search.step(executor=executor)
    original = search._save
    interrupted = False

    def fail_before_reservation(journal):
        nonlocal interrupted
        if not interrupted and "candidate-0001-01" in journal["candidates"]:
            interrupted = True
            raise OSError("Fixture journal write failed before reservation")
        original(journal)

    monkeypatch.setattr(search, "_save", fail_before_reservation)
    with pytest.raises(OSError, match="before reservation"):
        search.step(executor=executor, proposer=proposer)
    assert not (search.root / "candidate-attempts/candidate-0001-01").exists()
    reopened = SearchController(search.root)
    assert reopened.step(executor=executor, proposer=proposer)["status"] == "proposed"
    assert len(proposer.calls) == 1 and len(executor.calls) == 1


def test_candidate_recovery_commit_failure_does_not_mutate_frozen_attempt(search, monkeypatch):
    executor, proposer = Executor(), Proposer(search)
    search.step(executor=executor)
    search.step(executor=executor, proposer=proposer)
    identity = "candidate-0001-01"
    journal = read(search.root / "journal.json")
    journal["candidates"][identity]["status"] = "preparing"
    original_files = journal["candidates"][identity]["attempt_files"]
    write_json(search.root / "journal.json", journal)
    original = search._save

    def fail_commit(updated):
        if updated["candidates"][identity].get("recovery") == "no_execution_replay":
            raise OSError("Fixture recovery commit interruption")
        original(updated)

    monkeypatch.setattr(search, "_save", fail_commit)
    with pytest.raises(OSError, match="recovery commit"):
        search.recover_candidate(identity, reason="Stopped preparation")
    reopened = SearchController(search.root)
    result = reopened.recover_candidate(identity, reason="Stopped preparation")
    assert result["status"] == "invalid" and result["attempt_files"] == original_files
    assert reopened.recover_candidate(identity, reason="Stopped preparation") == result
    assert len(executor.calls) == 1 and len(proposer.calls) == 1


def test_prepare_rejects_heldout_or_unreviewed_model_inputs(search, tmp_path):
    plan = read(search.root / "search.json")
    package = tmp_path / "changed-benchmark"
    shutil.copytree(search.root / "comparisons/baseline/benchmark", package)
    manifest = read(package / "manifest.json")
    manifest["split"] = "heldout"
    write_json(package / "manifest.json", manifest)
    (tmp_path / "new-source").mkdir()
    kwargs = dict(
        development_manifest=package / "manifest.json",
        baseline=bundle(tmp_path / "new-source"),
        instructions="Fixture",
        config=SearchConfig.model_validate(plan["config"]),
    )
    with pytest.raises(ValueError, match="development cases only"):
        SearchController.create(tmp_path / "new", tmp_path / "new-feedback", **kwargs)
    manifest["split"] = "development"
    write_json(package / "manifest.json", manifest)
    kwargs["config"] = kwargs["config"].model_copy(
        update={"comparison": ComparisonConfig(execution="model")}
    )
    with pytest.raises(ValueError, match="reviewed development"):
        SearchController.create(tmp_path / "new", tmp_path / "new-feedback", **kwargs)
    assert not (tmp_path / "new").exists()
