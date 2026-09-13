"""Software fixtures for the host-only bridge; no human/model quality claim."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_research_controller import fixture_executor

from research_harness.evaluation import controller
from research_harness.evaluation.controller import ComparisonConfig, prepare_comparison, run_case
from research_harness.evaluation.runtime_executor import RuntimeExecutionError
from research_harness.optimization.archive import ArchiveConfig, OptimizationArchive
from research_harness.optimization.evaluator import (
    ResearchDevelopmentEvaluator,
    _model_evidence,
    export_development,
)
from research_harness.util import digest, write_json

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = b"host-only-independent-evaluator-canary"


@pytest.fixture
def prepared(tmp_path):
    original = Path(__file__).resolve().parent / "fixtures/research-lifecycle"
    package = tmp_path / "benchmark-input"
    shutil.copytree(original, package)
    manifest = read(package / "manifest.json")
    manifest["cases"] = [
        ref
        for ref in manifest["cases"]
        if Path(ref["path"]).stem in {"export-notices", "weather-alert-feed"}
    ]
    write_json(package / "manifest.json", manifest)
    output = tmp_path / "comparison"
    prepare_comparison(
        package / "manifest.json",
        output,
        instructions="Shared software-fixture instructions.\n",
        config=ComparisonConfig(execution="fixture"),
    )
    return output


def read(path):
    return json.loads(path.read_bytes())


def complete(output, *, fail=None):
    def execute(task):
        result = fixture_executor(task)
        proposal = read(result.research / "proposal.json")
        # Explicit synthetic accounting, never a verified provider measurement.
        proposal["usage"] = {"input_tokens": 10, "output_tokens": 1}
        write_json(result.research / "proposal.json", proposal)
        write_json(result.research / "candidate-score.json", {"quality": 999, "total_tokens": 0})
        if task.case_id == fail:
            raise RuntimeExecutionError("Synthetic fixture outage", artifacts=result)
        return result

    for case_id in read(output / "comparison.json")["case_ids"]:
        run_case(output, "direct", case_id, execute)


def archive_for(output, tmp_path):
    controls = read(output / "comparison.json")["controls"]["direct"]
    fixed = {
        k: v for k, v in controls.items() if k not in {"strategy_sha256", "code_strategy_sha256"}
    }
    archive = OptimizationArchive.create(
        tmp_path / "host-archive",
        tmp_path / "feedback",
        config=ArchiveConfig(
            run_id="independent-bridge-fixture", execution="fixture", fixed_controls=fixed
        ),
        frozen_inputs={
            "development_manifest": output / "benchmark/manifest.json",
            "backend": ROOT / "src/research_harness/config.py",
            "evaluator": ROOT / "src/research_harness/optimization/evaluator.py",
        },
    )
    source = tmp_path / "candidate"
    source.mkdir()
    (source / "unused.py").write_text(
        "raise RuntimeError('Candidate source must never be host imported')\n"
    )
    archive.register_candidate(
        "baseline",
        source,
        instructions=(output / "instructions.md").read_text(),
        manifest={"schema_version": 1, "strategy_manifest": None},
        operation_id="register-baseline",
    )
    return archive, fixed


def evaluate(archive, fixed, exported):
    return archive.record_development(
        "baseline",
        exported,
        evaluator=ResearchDevelopmentEvaluator(fixed),
        operation_id="evaluate-baseline",
    )


def test_complete_export_is_independently_regraded_and_private_inputs_stay_out_of_feedback(
    prepared, tmp_path
):
    complete(prepared)
    (prepared / "benchmark/private-checks.txt").write_bytes(PRIVATE)
    (prepared / "implementation/private-evaluator.py").write_bytes(PRIVATE)
    exported = tmp_path / "development"
    export_development(prepared, "direct", exported)
    assert not (exported / "benchmark").exists()
    assert not (exported / "implementation").exists()
    assert not (exported / "omnigent").exists()
    archive, fixed = archive_for(prepared, tmp_path)
    assert evaluate(archive, fixed, exported)["status"] == "evaluated"
    evidence = read(archive.root / "candidates/baseline/development/evidence.json")
    assert evidence["execution_verified"] is False
    assert len(evidence["cases"]) == 2
    assert all(case["status"] == "ok" and case["quality"] == 1.0 for case in evidence["cases"])
    assert sum(case["total_tokens"] for case in evidence["cases"]) == 22
    preserved = archive.feedback_path() / "candidates/baseline/development/artifacts"
    for relative, sha256 in read(exported / "development.json")["execution_files"].items():
        assert digest((preserved / relative).read_bytes()) == sha256
    assert len(list(preserved.rglob("candidate-score.json"))) == 2
    assert all(
        PRIVATE not in path.read_bytes()
        for path in archive.feedback_path().rglob("*")
        if path.is_file()
    )
    assert archive.select(operation_id="select")["candidate_ids"] == ["baseline"]


def test_failure_remains_in_denominator_and_retains_partial_execution(prepared, tmp_path):
    failed = read(prepared / "comparison.json")["case_ids"][0]
    complete(prepared, fail=failed)
    exported = tmp_path / "development"
    export_development(prepared, "direct", exported)
    archive, fixed = archive_for(prepared, tmp_path)
    assert evaluate(archive, fixed, exported)["status"] == "evaluated"
    evidence = read(archive.root / "candidates/baseline/development/evidence.json")
    cases = {case["case_id"]: case for case in evidence["cases"]}
    assert len(cases) == 2
    assert cases[failed]["status"] == "execution_failed" and cases[failed]["quality"] == 0.0
    assert cases[failed]["total_tokens"] is None
    assert "Synthetic fixture outage" in cases[failed]["errors"][0]
    assert any(name.endswith("proposal.json") for name in cases[failed]["evidence_files"])
    with pytest.raises(ValueError, match="baseline"):
        archive.select(operation_id="select")


def test_unresolved_runs_cannot_be_exported(prepared, tmp_path):
    with pytest.raises(ValueError, match="Finish or resolve"):
        export_development(prepared, "direct", tmp_path / "development")
    assert not (tmp_path / "development").exists()


def test_changed_historical_worktree_does_not_require_reexecution(prepared, tmp_path, monkeypatch):
    complete(prepared)
    monkeypatch.setattr(controller, "source_fingerprints", lambda: {"later-change.py": "0" * 64})
    assert export_development(prepared, "direct", tmp_path / "development").is_file()


@pytest.mark.parametrize("change", ["artifact", "path", "case"])
def test_tampered_execution_cannot_enter_feedback(prepared, tmp_path, change):
    complete(prepared)
    journal_path = prepared / "direct/journal.json"
    journal = read(journal_path)
    identity, entry = next(iter(journal["cases"].items()))
    if change == "artifact":
        proposal = prepared / "direct" / entry["run"]["artifacts"] / "proposal.json"
        proposal.write_text("{}")
    elif change == "path":
        entry["run"]["artifacts"] = "../benchmark"
    else:
        entry["run"]["case_id"] = "foreign-case"
    write_json(journal_path, journal)
    with pytest.raises(ValueError):
        export_development(prepared, "direct", tmp_path / "development")


@pytest.mark.parametrize("change", ["instructions", "model", "benchmark", "missing-case"])
def test_archive_evaluator_rejects_foreign_candidate_controls_or_case_set(
    prepared, tmp_path, change
):
    complete(prepared)
    exported = tmp_path / "development"
    export_development(prepared, "direct", exported)
    manifest = read(exported / "run.json")
    if change == "instructions":
        manifest["controls"]["strategy_sha256"] = "0" * 64
    elif change == "model":
        manifest["controls"]["model"] = "foreign-model"
    elif change == "benchmark":
        manifest["benchmark_sha256"] = "0" * 64
    else:
        manifest["runs"] = manifest["runs"][:1]
    write_json(exported / "run.json", manifest)
    archive, fixed = archive_for(prepared, tmp_path)
    assert evaluate(archive, fixed, exported)["status"] == "evaluation_failed"
    assert (archive.root / "candidates/baseline/development/evaluation-error.json").exists()


def test_synthetic_complete_tokens_cannot_become_a_live_model_baseline():
    policy = {
        "mode": "openai-standard",
        "upstream_base_url": "https://api.openai.com/v1",
        "service_tier": "default",
    }
    budget = {
        "policy": policy,
        "authorization": {"status": "approved", "reference": "Synthetic test of gate only"},
    }
    controls = SimpleNamespace(execution="model", budget_control=budget)
    usage = {
        "status": "verified_complete",
        "upstream_transport": "caller-supplied",
        "budget_control_verified": True,
        "budget_settlement_complete": True,
        "budget_control": budget,
        "dispatched_requests": 1,
        "response_count": 1,
    }
    assert not _model_evidence(controls, usage)
    usage["upstream_transport"] = "httpx-default"
    assert _model_evidence(controls, usage)  # Pure metadata gate, not a live call.
    for key, value in [
        ("budget_settlement_complete", False),
        ("dispatched_requests", 0),
        ("upstream_transport", None),
        ("budget_control_verified", False),
    ]:
        assert not _model_evidence(controls, {**usage, key: value})
    policy["mode"] = "fixture"
    assert not _model_evidence(controls, usage)
    policy["mode"] = "openai-standard"
    budget["authorization"]["status"] = "draft"
    assert not _model_evidence(controls, usage)
