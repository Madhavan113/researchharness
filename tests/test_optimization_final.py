"""Private lifecycle fixtures; no live provider or human-review claims."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from filelock import FileLock, Timeout
from test_research_controller import fixture_executor, gateway_fixture

from research_harness.evaluation.benchmark import BenchmarkCase, BenchmarkManifest, FileRef
from research_harness.evaluation.controller import ComparisonConfig, prepare_comparison
from research_harness.evaluation.runtime_executor import RuntimeExecutionError
from research_harness.optimization import archive as archive_module
from research_harness.optimization import final
from research_harness.optimization.archive import ArchiveConfig, CaseEvidence, OptimizationArchive
from research_harness.strategies.config import StrategyBundle
from research_harness.strategies.sandbox import SandboxConfig
from research_harness.util import digest, write_json

ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT = ROOT / "examples/evaluation/development"
CANARY = "PRIVATE-HELDOUT-CANARY-DO-NOT-PUBLISH"
IMAGE = "python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285"


def read(path):
    return json.loads(path.read_bytes())


def inventory(path):
    return {
        str(item.relative_to(path)): digest(item.read_bytes())
        for item in path.rglob("*")
        if item.is_file()
    }


def package(path, cases, *, split, reviewed=False):
    references = []
    for name in cases:
        case = BenchmarkCase.model_validate(read(DEVELOPMENT / "cases" / f"{name}.json"))
        value = case.model_dump(mode="json")
        value["review_status"] = "reviewed" if reviewed else "authored"
        value["review_notes"] = (
            "Synthetic declaration exercises review gate only; no person reviewed this fixture"
        )
        if split == "heldout":
            value["brief"] += f" [{CANARY}]"
            value["manual_checks"].append(CANARY)
        fixture = path / case.fixtures.path
        fixture.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEVELOPMENT / case.fixtures.path, fixture)
        target = path / "cases" / f"{name}.json"
        write_json(target, value)
        references.append(FileRef(path=f"cases/{name}.json", sha256=digest(target.read_bytes())))
    manifest = BenchmarkManifest(
        id=f"private-final-{split}",
        split=split,
        description="Synthetic lifecycle fixture",
        cases=references,
    )
    write_json(path / "manifest.json", manifest.model_dump(mode="json"))
    return path / "manifest.json"


def build(
    tmp_path,
    *,
    execution="fixture",
    heldout_reviewed=False,
    code=False,
    changed_limits=False,
    candidate_failure=False,
):
    development = package(
        tmp_path / "development",
        ["export-notices"],
        split="development",
        reviewed=execution == "model",
    )
    heldout = package(
        tmp_path / "heldout",
        ["filing-stable-accession", "weather-alert-feed"],
        split="heldout",
        reviewed=heldout_reviewed,
    )
    config = ComparisonConfig(execution=execution)
    comparison = tmp_path / "development-controls"
    prepare_comparison(development, comparison, instructions="Baseline instructions", config=config)
    controls = read(comparison / "comparison.json")["controls"]["direct"]
    fixed = {
        k: v for k, v in controls.items() if k not in {"strategy_sha256", "code_strategy_sha256"}
    }
    if execution == "model" or code:
        fixed["sandbox"] = SandboxConfig(image=IMAGE).model_dump(mode="json")
    sources = {}
    for identity in ("baseline", "candidate"):
        source = tmp_path / f"source-{identity}"
        source.mkdir()
        if code:
            program = f"# {identity}\ndef apply(event):\n    return {{'decision': {{}}, 'state': event['state']}}\n"
            (source / "strategy.py").write_text(program)
            write_json(
                source / "strategy.json",
                {
                    "schema_version": 1,
                    "source": "strategy.py",
                    "source_sha256": digest(program),
                    "sandbox": fixed["sandbox"],
                    "max_events": 2 if identity == "candidate" and changed_limits else 128,
                },
            )
            strategy = StrategyBundle.load(source / "strategy.json")
            if identity == "baseline":
                fixed["strategy_limits"] = strategy.config.model_dump(
                    mode="json", exclude={"source", "source_sha256", "sandbox"}
                )
        else:
            (source / "inert.py").write_text(
                "raise RuntimeError('Registered source must not be imported by the host')\n"
            )
        sources[identity] = source
    archive = OptimizationArchive.create(
        tmp_path / "state",
        tmp_path / "feedback",
        config=ArchiveConfig(
            run_id="private-final-fixture", execution=execution, fixed_controls=fixed
        ),
        frozen_inputs={
            "development_manifest": development,
            "evaluator": final.__file__,
            "backend": ROOT / "src/research_harness/config.py",
        },
    )
    outcomes = (
        (("baseline", 1.0, 100), ("candidate", 0.0, 2))
        if candidate_failure
        else (("baseline", 0.5, 20), ("candidate", 1.0, 10))
    )
    for candidate, quality, tokens in outcomes:
        source = sources[candidate]
        archive.register_candidate(
            candidate,
            source,
            instructions=f"{candidate} frozen instructions",
            manifest={"schema_version": 1, "strategy_manifest": "strategy.json" if code else None},
            operation_id=f"register-{candidate}",
        )
        artifacts = tmp_path / f"development-{candidate}"
        write_json(artifacts / "fixture.json", {"scope": "synthetic evaluator lifecycle evidence"})

        failed = candidate_failure and candidate == "candidate"

        def evaluate(context, quality=quality, tokens=tokens, failed=failed):
            return context.evidence(
                cases=[
                    CaseEvidence(
                        case_id=case_id,
                        status="execution_failed" if failed else "ok",
                        quality=quality,
                        total_tokens=tokens,
                        evidence_files=["fixture.json"],
                        errors=["development worker failed"] if failed else [],
                    )
                    for case_id in context.case_ids
                ],
                execution_verified=execution
                == "model",  # Host metadata fixture for the selection gate only.
                limitations=["Synthetic test of archive binding; not real model performance"],
            )

        archive.record_development(
            candidate, artifacts, evaluator=evaluate, operation_id=f"evaluate-{candidate}"
        )
    assert archive.select(operation_id="select")["candidate_ids"] == [
        "baseline" if candidate_failure else "candidate"
    ]
    return archive, config, heldout


@pytest.fixture
def prepared(tmp_path):
    return build(tmp_path)


def run(archive, config, heldout, output, executor, *, op="final"):
    return final.evaluate_final(
        archive,
        heldout_manifest=heldout,
        output=output,
        comparison_config=config,
        arm="direct",
        executor=executor,
        operation_id=op,
    )


def synthetic_executor(calls, *, fail_case=None, unknown_case=None, interrupt_at=None):
    def execute(task):
        calls.append(task)
        assert CANARY in task.brief
        if len(calls) == interrupt_at:
            (task.output / "partial.log").write_text("Available evidence before host interruption")
            raise KeyboardInterrupt
        result = fixture_executor(
            task,
            source_id="row-number-id" if task.case_id == "filing-stable-accession" else None,
        )
        proposal = read(result.research / "proposal.json")
        proposal["usage"] = (
            {} if task.case_id == unknown_case else {"input_tokens": 10, "output_tokens": 1}
        )
        write_json(result.research / "proposal.json", proposal)
        write_json(result.research / "agent-score.json", {"quality": 999, "total_tokens": 0})
        if task.case_id == fail_case:
            raise RuntimeExecutionError("Synthetic final task failure", artifacts=result)
        return result

    return execute


def test_private_final_compares_original_baseline_and_frozen_selection(
    prepared, tmp_path, monkeypatch
):
    archive, config, heldout = prepared
    feedback = tmp_path / "feedback"
    before = inventory(feedback)
    original_load = final.load_benchmark

    def guarded(path):
        if path == heldout:
            assert archive.status()["phase"] == "final_started"
        return original_load(path)

    monkeypatch.setattr(final, "load_benchmark", guarded)
    calls = []
    output = tmp_path / "private-final"
    report = run(archive, config, heldout, output, synthetic_executor(calls))
    assert report["status"] == "completed" and report["split"] == "heldout"
    assert report["candidate_ids"] == ["baseline", "candidate"]
    assert len(calls) == 4
    assert {task.instructions for task in calls} == {
        "baseline frozen instructions",
        "candidate frozen instructions",
    }
    for candidate in report["candidates"]:
        assert candidate["summary"]["case_count"] == 2
        assert candidate["summary"]["macro_quality"] == 0.5
        # The explicitly selected row-number source uses the wrong identity field;
        # independent held-out predicates correctly give it zero usefulness.
        assert candidate["cases"][0]["quality"] == 0.0
        assert candidate["summary"]["total_tokens"] == 22
        assert candidate["summary"]["execution_verified"] is False
        assert all(case["quality"] != 999 for case in candidate["cases"])
    assert inventory(feedback) == before
    assert all(
        CANARY.encode() not in path.read_bytes() for path in feedback.rglob("*") if path.is_file()
    )
    assert archive.status()["phase"] == "final_completed"
    with pytest.raises(ValueError, match="closed"):
        archive.feedback_path()
    # Repeated delivery returns sealed evidence without touching the changed original heldout package.
    heldout.write_text("changed after freezing")
    assert (
        run(archive, config, heldout, output, lambda _: pytest.fail("Repeated provider execution"))
        == report
    )
    assert archive.status()["selection"]["candidate_ids"] == ["candidate"]


def test_final_never_executes_failed_development_candidate(tmp_path):
    archive, config, heldout = build(tmp_path, candidate_failure=True)
    calls = []
    report = run(archive, config, heldout, tmp_path / "private-final", synthetic_executor(calls))
    assert report["candidate_ids"] == ["baseline"]
    assert len(calls) == 2
    assert {task.instructions for task in calls} == {"baseline frozen instructions"}
    assert archive.status()["selection"]["excluded"] == {"candidate": "failed_development_cases"}


def test_old_selection_cannot_dispatch_an_ineligible_candidate(tmp_path):
    archive, config, heldout = build(tmp_path, candidate_failure=True)
    # Reproduce a pre-policy selection. The recorded development evidence stays
    # unchanged, while the old Pareto rule includes the cheap failed candidate.
    journal = read(archive.root / "journal.json")
    selection = journal["selection"]
    selection["candidate_ids"] = ["baseline", "candidate"]
    selection["excluded"] = {}
    selection.pop("raw_candidate_ids", None)
    selection.pop("eligibility_policy", None)
    journal["operations"]["select"]["result"] = selection
    write_json(archive.root / "journal.json", journal)
    heldout.unlink()  # The guard must run before private benchmark loading.
    output = tmp_path / "private-final"
    with pytest.raises(ValueError, match="candidate.*ineligible.*failed_development_cases"):
        run(archive, config, heldout, output, lambda _: pytest.fail("Provider dispatched"))
    assert not output.exists()
    assert archive.status()["phase"] == "selected"
    assert archive.status()["final"] is None


def test_failed_and_unknown_cases_remain_in_final_denominator(prepared, tmp_path):
    archive, config, heldout = prepared
    report = run(
        archive,
        config,
        heldout,
        tmp_path / "private-final",
        synthetic_executor(
            [], fail_case="filing-stable-accession", unknown_case="weather-alert-feed"
        ),
    )
    for candidate in report["candidates"]:
        assert candidate["summary"]["case_count"] == 2
        assert candidate["summary"]["failed_cases"] == 1
        assert candidate["summary"]["macro_quality"] == 0.5
        assert candidate["summary"]["total_tokens"] is None
        assert candidate["summary"]["unknown_token_cases"] == 2
        failed = candidate["cases"][0]
        assert failed["status"] == "execution_failed"
        assert "research/proposal.json" in failed["evidence_files"]


def test_failed_final_preserves_independently_verified_gateway_usage(prepared, tmp_path):
    archive, config, heldout = prepared

    def execute(task):
        result = fixture_executor(task)
        result = replace(result, gateway_usage=gateway_fixture(task))
        raise RuntimeExecutionError("Failed after observed provider usage", artifacts=result)

    report = run(archive, config, heldout, tmp_path / "private-final", execute)
    for candidate in report["candidates"]:
        assert candidate["summary"]["failed_cases"] == 2
        assert candidate["summary"]["total_tokens"] == 220
        assert candidate["summary"]["execution_verified"] is False
        assert all(
            case["gateway_usage"]["status"] == "verified_complete" for case in candidate["cases"]
        )


def test_final_code_uses_frozen_selected_bytes_and_matching_limits(tmp_path):
    archive, config, heldout = build(tmp_path, code=True)
    calls = []
    report = run(archive, config, heldout, tmp_path / "private-final", synthetic_executor(calls))
    for task in calls:
        assert task.strategy_path.is_relative_to(tmp_path / "private-final")
        bundle = StrategyBundle.load(task.strategy_path)
        assert task.code_strategy_sha256 == bundle.sha256
        assert task.strategy_session_id
    assert len({task.code_strategy_sha256 for task in calls}) == 2
    assert report["candidate_ids"] == ["baseline", "candidate"]


def test_changed_candidate_strategy_limits_reject_before_any_final_execution(tmp_path):
    archive, config, heldout = build(tmp_path, code=True, changed_limits=True)
    with pytest.raises(ValueError, match="sandbox or strategy limits"):
        run(
            archive,
            config,
            heldout,
            tmp_path / "private-final",
            lambda _: pytest.fail("Changed limits reached provider"),
        )
    report = final.recover_final(
        archive, operation_id="final", reason="Rejected candidate configuration"
    )
    assert all(item["summary"]["case_count"] == 2 for item in report["candidates"])
    assert all(item["summary"]["unmeasured_cases"] == 2 for item in report["candidates"])


def test_preparation_interruption_retains_the_full_planned_case_count(
    prepared, tmp_path, monkeypatch
):
    archive, config, heldout = prepared
    original = final.prepare_comparison
    calls = []

    def prepare(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    monkeypatch.setattr(final, "prepare_comparison", prepare)
    with pytest.raises(KeyboardInterrupt):
        run(
            archive,
            config,
            heldout,
            tmp_path / "private-final",
            lambda _: pytest.fail("Preparation did not finish"),
        )
    report = final.recover_final(
        archive, operation_id="final", reason="Stopped before any case began"
    )
    assert len(report["candidates"]) == 2
    assert all(
        item["summary"]["case_count"] == item["summary"]["unmeasured_cases"] == 2
        for item in report["candidates"]
    )


def test_recovery_refuses_a_case_whose_controller_lock_is_still_owned(prepared, tmp_path):
    archive, config, heldout = prepared
    output = tmp_path / "private-final"
    with pytest.raises(KeyboardInterrupt):
        run(archive, config, heldout, output, synthetic_executor([], interrupt_at=1))
    lock = output / "evaluations/baseline/direct/controller.lock"
    with FileLock(str(lock)):
        with pytest.raises(Timeout):
            final.recover_final(
                archive, operation_id="final", reason="Another executor still owns its case"
            )
    assert archive.status()["phase"] == "final_started"


def test_interrupted_attempt_requires_explicit_no_replay_recovery(prepared, tmp_path):
    archive, config, heldout = prepared
    output, calls = tmp_path / "private-final", []
    before = inventory(tmp_path / "feedback")
    with pytest.raises(KeyboardInterrupt):
        run(archive, config, heldout, output, synthetic_executor(calls, interrupt_at=2))
    assert len(calls) == 2 and archive.status()["phase"] == "final_started"
    with pytest.raises(ValueError, match="unresolved"):
        run(archive, config, heldout, output, lambda _: pytest.fail("Repeated model call"))
    recovered = final.recover_final(
        archive, operation_id="final", reason="Host stopped and quiesced the executor"
    )
    assert recovered["status"] == "interrupted"
    assert all(item["summary"]["case_count"] == 2 for item in recovered["candidates"])
    assert recovered["candidates"][0]["summary"]["unmeasured_cases"] == 1
    assert recovered["candidates"][1]["summary"]["unmeasured_cases"] == 2
    assert recovered["candidates"][0]["cases"][0]["quality"] == 0.0
    assert recovered["candidates"][0]["summary"]["macro_quality"] is None
    assert any(
        name.endswith("partial.log") for name in read(output / "final-archive.json")["files"]
    )
    assert final.recover_final(archive, operation_id="final", reason="Repeat") == recovered
    assert inventory(tmp_path / "feedback") == before
    assert archive.status()["phase"] == "final_interrupted"


def test_interrupted_case_keeps_verified_usage_without_claiming_research_completion(
    prepared, tmp_path
):
    archive, config, heldout = prepared

    def interrupted(task):
        artifacts = replace(fixture_executor(task), gateway_usage=gateway_fixture(task))
        error = KeyboardInterrupt()
        error.artifacts = artifacts
        raise error

    with pytest.raises(KeyboardInterrupt):
        run(archive, config, heldout, tmp_path / "private-final", interrupted)
    report = final.recover_final(
        archive, operation_id="final", reason="Host stopped after the gateway sealed"
    )
    baseline = report["candidates"][0]
    assert baseline["cases"][0]["status"] == "unmeasured"
    assert baseline["cases"][0]["quality"] is None
    assert baseline["cases"][0]["total_tokens"] == 110
    assert baseline["summary"]["known_token_subtotal"] == 110
    assert baseline["summary"]["total_tokens"] is None
    assert baseline["summary"]["macro_quality"] is None


@pytest.mark.parametrize("path_kind", ["output-in-feedback", "linked-fixture"])
def test_unsafe_private_paths_fail_before_case_execution(prepared, tmp_path, path_kind):
    archive, config, heldout = prepared
    output = tmp_path / "private-final"
    if path_kind == "output-in-feedback":
        output = tmp_path / "feedback" / "private-final"
    else:
        reference = read(heldout)["cases"][0]
        case = read(heldout.parent / reference["path"])
        fixture = heldout.parent / case["fixtures"]["path"]
        target = tmp_path / "linked-source.json"
        fixture.rename(target)
        fixture.symlink_to(target)
    with pytest.raises(ValueError):
        run(
            archive,
            config,
            heldout,
            output,
            lambda _: pytest.fail("Unsafe final path reached executor"),
        )
    assert not (tmp_path / "feedback/private-final").exists()


@pytest.mark.parametrize("change", ["model", "budget", "runtime", "split", "overlap"])
def test_final_rejects_unmatched_controls_or_leaking_splits_before_provider(
    prepared, tmp_path, change
):
    archive, config, heldout = prepared
    arm = "direct"
    if change == "model":
        config = config.model_copy(update={"model": "foreign-model"})
    elif change == "budget":
        config = config.model_copy(
            update={"settings": config.settings.model_copy(update={"max_rounds": 100})}
        )
    elif change == "runtime":
        arm = "omnigent"
    elif change == "split":
        value = read(heldout)
        value["split"] = "development"
        write_json(heldout, value)
    else:
        heldout = package(tmp_path / "overlap", ["export-notices"], split="heldout")
    with pytest.raises(ValueError):
        final.evaluate_final(
            archive,
            heldout_manifest=heldout,
            output=tmp_path / "private-final",
            comparison_config=config,
            arm=arm,
            executor=lambda _: pytest.fail("Invalid final reached provider"),
            operation_id="final",
        )
    assert archive.status()["phase"] == "final_started"
    assert (
        final.recover_final(archive, operation_id="final", reason="Rejected preparation")["status"]
        == "interrupted"
    )


def test_model_mode_requires_reviewed_heldout_before_any_dispatch(tmp_path):
    archive, config, heldout = build(tmp_path, execution="model")
    with pytest.raises(ValueError, match="reviewed held-out"):
        run(
            archive,
            config,
            heldout,
            tmp_path / "private-final",
            lambda _: pytest.fail("Unreviewed final dispatched"),
        )


def test_model_labeled_synthetic_final_does_not_claim_verified_execution(tmp_path):
    archive, config, heldout = build(tmp_path, execution="model", heldout_reviewed=True)
    report = run(archive, config, heldout, tmp_path / "private-final", synthetic_executor([]))
    assert all(item["summary"]["execution_verified"] is False for item in report["candidates"])
    assert all(item["summary"]["total_tokens"] is None for item in report["candidates"])


@pytest.mark.parametrize("failure", ["mkdir", "selection-write", "report-write", "seal-write"])
def test_setup_and_seal_failures_are_recoverable_without_reexecution(
    prepared, tmp_path, monkeypatch, failure
):
    archive, config, heldout = prepared
    output, calls = tmp_path / "private-final", []
    original_mkdir, original_save, original_final_save = (
        Path.mkdir,
        archive_module._save,
        final._save,
    )

    def mkdir(path, *args, **kwargs):
        if failure == "mkdir" and path == output:
            raise OSError("Synthetic private directory failure")
        return original_mkdir(path, *args, **kwargs)

    def save(path, value):
        if (failure == "selection-write" and path == output / "selection.json") or (
            failure == "seal-write" and path == output / "final-archive.json"
        ):
            raise OSError("Synthetic archive write failure")
        return original_save(path, value)

    def save_final(path, value):
        if failure == "report-write" and path == output / "report.json":
            raise OSError("Synthetic report write failure")
        return original_final_save(path, value)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(archive_module, "_save", save)
    monkeypatch.setattr(final, "_save", save_final)
    with pytest.raises(OSError):
        run(archive, config, heldout, output, synthetic_executor(calls))
    count = len(calls)
    monkeypatch.undo()
    result = final.recover_final(
        archive, operation_id="final", reason="Host recovered the stopped attempt"
    )
    assert len(calls) == count
    assert result["status"] == ("completed" if failure == "seal-write" else "interrupted")
    sealed = read(output / "final-archive.json")
    if failure in {"mkdir", "selection-write"}:
        assert "selection.json" in sealed["missing_evidence"]
    assert archive.status()["phase"] in {"final_interrupted", "final_completed"}


def test_final_lock_excludes_simultaneous_execution_or_recovery(prepared, tmp_path):
    archive, config, heldout = prepared
    with FileLock(str(archive.root) + ".final.lock"):
        with pytest.raises(Timeout):
            run(archive, config, heldout, tmp_path / "private-final", synthetic_executor([]))
        with pytest.raises(Timeout):
            final.recover_final(archive, operation_id="final", reason="Concurrent recovery")
    assert archive.status()["phase"] == "selected"


@pytest.mark.parametrize(
    "change", ["report", "new-file", "different-operation", "different-config"]
)
def test_terminal_final_rejects_artifact_or_request_changes_without_replay(
    prepared, tmp_path, change
):
    archive, config, heldout = prepared
    output = tmp_path / "private-final"
    run(archive, config, heldout, output, synthetic_executor([]))
    op = "final"
    if change == "report":
        write_json(output / "report.json", {"quality": 999})
    elif change == "new-file":
        (output / "unrecorded.txt").write_text("late mutation")
    elif change == "different-operation":
        op = "another-final"
    else:
        config = config.model_copy(update={"model": "another-model"})
    with pytest.raises(ValueError):
        run(
            archive,
            config,
            heldout,
            output,
            lambda _: pytest.fail("Repeated final execution"),
            op=op,
        )
