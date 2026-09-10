"""Local archive/lifecycle fixtures; these do not measure model performance."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from research_harness.evaluation.benchmark import load_benchmark
from research_harness.execution import DiscoverySettings
from research_harness.optimization import archive as archive_module
from research_harness.optimization.archive import (
    ArchiveConfig,
    CaseEvidence,
    DevelopmentEvidence,
    EvaluationInput,
    OptimizationArchive,
)
from research_harness.strategies.sandbox import SandboxConfig
from research_harness.util import digest, write_json

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def prepare(tmp_path, *, execution="fixture", reviewed=None, **controls):
    benchmark = tmp_path / "benchmark"
    benchmark.mkdir()
    # Stable lifecycle inputs: benchmark design changes must not silently change
    # this synthetic exact-answer callback or its intended two-case outcomes.
    manifest = {
        "schema_version": 1,
        "id": "archive-lifecycle-fixture",
        "split": "development",
        "description": "Synthetic archive lifecycle only",
        "cases": [],
    }
    reviewed = execution == "model" if reviewed is None else reviewed
    for identity in ("archive-alpha", "archive-beta"):
        url = f"https://{identity}.fixture.invalid/records.json"
        fixture_path = f"fixtures/{identity}.json"
        write_json(
            benchmark / fixture_path,
            {
                "search_results": [{"url": url}],
                "responses": [{"url": url, "body": {"items": [{"id": "synthetic-1"}]}}],
            },
        )
        case = {
            "id": identity,
            "title": f"{identity} lifecycle task",
            "brief": f"Find the synthetic {identity} records.",
            "topic_group": identity,
            "source_families": [identity],
            "tags": ["archive-lifecycle"],
            "review_status": "reviewed" if reviewed else "authored",
            "review_notes": "Synthetic review-gate fixture only; no claim of actual review.",
            "specification": {
                "requirements": [{"id": "records", "any_of": [{"url": url, "connector": "json"}]}]
            },
            "fixtures": {
                "path": fixture_path,
                "sha256": digest((benchmark / fixture_path).read_bytes()),
            },
            "sources": [
                {
                    "id": "records",
                    "name": "Synthetic records",
                    "url": url,
                    "connector": "json",
                    "id_pointer": "/id",
                    "items_pointer": "/items",
                }
            ],
            "fixture_plan": {"source_ids": ["records"]},
            "manual_checks": ["Archive lifecycle fixture, not research quality."],
        }
        path = f"cases/{identity}.json"
        write_json(benchmark / path, case)
        manifest["cases"].append({"path": path, "sha256": digest((benchmark / path).read_bytes())})
    write_json(benchmark / "manifest.json", manifest)
    # The source of this fixture evaluator stays host-side, as do independent
    # predicates. Neither is a candidate-authored score file.
    inputs = {
        "development_manifest": benchmark / "manifest.json",
        "evaluator": Path(__file__),
        "backend": ROOT / "src/research_harness/config.py",
    }
    config = ArchiveConfig(
        run_id="archive-contract-fixture",
        execution=execution,
        fixed_controls={
            "runtime": "archive-test-runtime",
            "model": "synthetic-model",
            "model_settings": DiscoverySettings().model_settings(),
            "provider": "fixture",
            "provider_settings": {"execution": "fixture"},
            "budgets": DiscoverySettings().budgets(),
            "sandbox": SandboxConfig(image="python@sha256:" + "b" * 64).model_dump(mode="json"),
            **controls,
        },
    )
    archive = OptimizationArchive.create(
        tmp_path / "host",
        tmp_path / "feedback",
        config=config,
        frozen_inputs=inputs,
    )
    return archive, inputs, config


def source(tmp_path, name="baseline"):
    root = tmp_path / f"source-{name}"
    root.mkdir()
    (root / "strategy.py").write_text("def apply(event):\n    return {}\n")
    (root / "helper.txt").write_bytes(b"preserve exact bytes\x00\xff\n")
    return root


def register(archive, tmp_path, name="baseline", **kwargs):
    path = source(tmp_path, name)
    result = archive.register_candidate(
        name,
        path,
        instructions="Keep evidence ids intact.\n",
        manifest={
            "schema_version": 1,
            "entrypoint": "strategy.py:apply",
            "protocol": "research-strategy-v1",
            "source_sha256": digest((path / "strategy.py").read_bytes()),
        },
        operation_id=f"register-{name}",
        **kwargs,
    )
    return result, path


def artifacts(archive, tmp_path, name="baseline", *, correct=2, tokens=10, failed=False):
    directory = tmp_path / f"execution-{name}"
    directory.mkdir()
    benchmark = load_benchmark(archive.root / "frozen/development_manifest/manifest.json")
    for index, case in enumerate(benchmark.cases.values()):
        expected = case.specification["requirements"][0]["any_of"][0]["url"]
        write_json(
            directory / case.id / "response.json",
            {
                "output": expected if index < correct else "https://irrelevant.example/",
                "usage": tokens,
                "failure": "worker interrupted" if failed else None,
                "candidate_claimed_quality": 100.0,
            },
        )
    (directory / "trace.jsonl").write_bytes(b'{"event":"complete available trace"}\n')
    (directory / "request.bin").write_bytes(b"\x00\xff original request bytes")
    (directory / "stderr.log").write_text("diagnostic output\n")
    return directory


def fixture_evaluator(context: EvaluationInput) -> DevelopmentEvidence:
    benchmark = load_benchmark(context.benchmark_path)
    cases = []
    for case_id in context.case_ids:
        relative = f"{case_id}/response.json"
        response = read(context.artifacts_dir / relative)
        expected = benchmark.cases[case_id].specification["requirements"][0]["any_of"][0]["url"]
        failure = response["failure"]
        cases.append(
            CaseEvidence(
                case_id=case_id,
                status="execution_failed" if failure else "ok",
                quality=0.0 if failure else float(response["output"] == expected),
                total_tokens=response["usage"],
                evidence_files=[relative],
                errors=[failure] if failure else [],
            )
        )
    return context.evidence(
        cases=cases,
        limitations=[
            "Synthetic archive evaluator for lifecycle tests, not research correctness or model quality."
        ],
    )


def evaluated(archive, tmp_path, name="baseline", *, evaluator=fixture_evaluator, **kwargs):
    registered, path = register(archive, tmp_path, name)
    directory = artifacts(archive, tmp_path, name, **kwargs)
    result = archive.record_development(
        name,
        directory,
        evaluator=evaluator,
        operation_id=f"evaluate-{name}",
    )
    return result, registered, path, directory


def test_archive_keeps_exact_files_instructions_and_host_only_inputs(tmp_path):
    archive, inputs, _ = prepare(tmp_path)
    result, registered, code, output = evaluated(archive, tmp_path)
    assert result["status"] == "evaluated"
    feedback = archive.feedback_path()
    bundle = feedback / "candidates/baseline/bundle"
    assert (bundle / "source/helper.txt").read_bytes() == (code / "helper.txt").read_bytes()
    assert (bundle / "instructions.md").read_text() == "Keep evidence ids intact.\n"
    assert read(bundle / "manifest.json")["source_sha256"] == digest(
        (code / "strategy.py").read_bytes()
    )
    frozen = feedback / "candidates/baseline/development/artifacts"
    assert {
        path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()
    } == {path.relative_to(frozen).as_posix() for path in frozen.rglob("*") if path.is_file()}
    for original in output.rglob("*"):
        if original.is_file():
            assert original.read_bytes() == (frozen / original.relative_to(output)).read_bytes()
    evidence = read(feedback / "candidates/baseline/development/evidence.json")
    assert evidence["candidate_sha256"] == registered["candidate_sha256"]
    assert archive.status()["candidates"]["baseline"]["summary"]["quality"] == 1.0
    assert not (feedback / "frozen").exists()
    assert all(path.name != "test_optimization_archive.py" for path in feedback.rglob("*"))
    assert (
        inputs["development_manifest"].read_bytes()
        == (archive.root / "frozen/development_manifest/manifest.json").read_bytes()
    )
    # Source inputs can subsequently change; archived bytes remain independent.
    (code / "strategy.py").write_text("raise SystemExit('changed source')\n")
    (output / "trace.jsonl").write_text("changed source trace")
    assert archive.status()["candidates"]["baseline"]["status"] == "evaluated"
    assert (frozen / "trace.jsonl").read_bytes() != (output / "trace.jsonl").read_bytes()


def test_operation_replay_returns_prior_result_without_evaluator_call(tmp_path):
    archive, _, _ = prepare(tmp_path)
    result, _, _, output = evaluated(archive, tmp_path)

    def forbidden(_):
        raise AssertionError("must not evaluate again")

    repeated = archive.record_development(
        "baseline", output, evaluator=forbidden, operation_id="evaluate-baseline"
    )
    assert repeated == result
    (output / "new.log").write_text("different request")
    with pytest.raises(ValueError, match="reused"):
        archive.record_development(
            "baseline", output, evaluator=forbidden, operation_id="evaluate-baseline"
        )


def test_candidate_python_is_archived_without_host_import_or_execution(tmp_path):
    archive, _, _ = prepare(tmp_path)
    path = source(tmp_path)
    marker = tmp_path / "must-not-be-created"
    program = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('untrusted code')\n"
    (path / "strategy.py").write_text(program)
    kwargs = {"instructions": "", "manifest": {}, "operation_id": "register"}
    saved = archive.register_candidate("baseline", path, **kwargs)
    assert archive.register_candidate("baseline", path, **kwargs) == saved
    assert not marker.exists()
    assert (
        archive.feedback_path() / "candidates/baseline/bundle/source/strategy.py"
    ).read_text() == program
    with pytest.raises(ValueError, match="reused"):
        archive.register_candidate("baseline", path, **{**kwargs, "instructions": "changed"})


def test_development_artifact_symlink_fails_before_evaluator_or_feedback(tmp_path):
    archive, _, _ = prepare(tmp_path)
    register(archive, tmp_path)
    output = artifacts(archive, tmp_path)
    private = tmp_path / "private-heldout.json"
    private.write_text("PRIVATE")
    (output / "extra-results.json").symlink_to(private)

    def forbidden(_):
        raise AssertionError("unsafe input must never reach evaluator")

    with pytest.raises(ValueError, match="without links"):
        archive.record_development("baseline", output, evaluator=forbidden, operation_id="evaluate")
    assert not (archive.feedback_path() / "candidates/baseline/development").exists()


def test_baseline_gate_does_not_admit_candidate_or_consume_operation(tmp_path):
    archive, _, _ = prepare(tmp_path)
    with pytest.raises(ValueError, match="baseline"):
        register(archive, tmp_path, "premature")
    assert archive.status()["operations"] == {}
    register(archive, tmp_path)
    with pytest.raises(ValueError, match="baseline"):
        register(archive, tmp_path, "still-premature")
    assert set(archive.status()["candidates"]) == {"baseline"}


@pytest.mark.parametrize("verified", [False, True])
def test_model_archive_requires_host_verified_measured_baseline(tmp_path, verified):
    archive, _, _ = prepare(tmp_path, execution="model")

    def check(context):
        result = fixture_evaluator(context)
        # This flag tests the callback trust contract only; synthetic results do
        # not establish that a real baseline was measured.
        return result.model_copy(update={"execution_verified": verified})

    evaluated(archive, tmp_path, evaluator=check)
    if verified:
        register(archive, tmp_path, "next")
    else:
        with pytest.raises(ValueError, match="baseline"):
            register(archive, tmp_path, "next")


def test_model_archive_rejects_unreviewed_cases(tmp_path):
    with pytest.raises(ValueError, match="reviewed"):
        prepare(tmp_path, execution="model", reviewed=False)
    assert not (tmp_path / "host").exists()


def test_unverified_model_candidate_cannot_enter_selected_frontier(tmp_path):
    archive, _, _ = prepare(tmp_path, execution="model")

    def verified(context):
        return fixture_evaluator(context).model_copy(update={"execution_verified": True})

    evaluated(archive, tmp_path, evaluator=verified, correct=1, tokens=100)
    evaluated(archive, tmp_path, "unverified-better", correct=2, tokens=1)
    result = archive.select(operation_id="select")
    assert result["candidate_ids"] == ["baseline"]
    assert result["excluded"] == {"unverified-better": "unverified_model_execution"}
    assert "unverified-better" not in result["ranked"]


@pytest.mark.parametrize(
    "missing",
    ["runtime", "model", "model_settings", "provider", "provider_settings", "budgets", "sandbox"],
)
def test_model_archive_cannot_omit_frozen_controls(tmp_path, missing):
    _, _, config = prepare(tmp_path)
    controls = dict(config.fixed_controls)
    del controls[missing]
    with pytest.raises(ValueError, match="explicit"):
        ArchiveConfig(run_id="bad", execution="model", fixed_controls=controls)


@pytest.mark.parametrize(
    "mutation",
    [
        "search",
        "zero_deadline",
        "negative",
        "boolean",
        "model_mismatch",
        "image",
        "sandbox_default",
    ],
)
def test_model_archive_rejects_incomplete_or_inconsistent_budgets(tmp_path, mutation):
    _, _, config = prepare(tmp_path)
    controls = config.model_dump(mode="json")["fixed_controls"]
    if mutation == "search":
        del controls["budgets"]["search"]
    elif mutation == "zero_deadline":
        controls["budgets"]["deadline_seconds"] = 0
    elif mutation == "negative":
        controls["budgets"]["search"] = -1
    elif mutation == "boolean":
        controls["budgets"]["search"] = True
    elif mutation == "model_mismatch":
        controls["model_settings"]["max_output_tokens"] = 99
    elif mutation == "image":
        controls["sandbox"]["image"] = "python:latest"
    else:
        del controls["sandbox"]["timeout_seconds"]
    with pytest.raises(ValueError):
        ArchiveConfig(run_id="bad", execution="model", fixed_controls=controls)


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_id", "foreign"),
        ("candidate_sha256", "0" * 64),
        ("artifacts_sha256", "0" * 64),
        ("controls_sha256", "0" * 64),
        ("evaluator_sha256", "0" * 64),
        ("benchmark_sha256", "0" * 64),
        ("execution", "model"),
        ("split", "heldout"),
    ],
)
def test_rejects_foreign_evidence_binding_and_preserves_all_artifacts(tmp_path, field, value):
    archive, _, _ = prepare(tmp_path)

    def bad(context):
        return fixture_evaluator(context).model_copy(update={field: value})

    result, _, _, output = evaluated(archive, tmp_path, evaluator=bad)
    assert result["status"] == "evaluation_failed"
    target = archive.feedback_path() / "candidates/baseline/development"
    assert (target / "artifacts/trace.jsonl").read_bytes() == (output / "trace.jsonl").read_bytes()
    assert (target / "evaluation-error.json").is_file()
    with pytest.raises(ValueError, match="baseline"):
        archive.select(operation_id="select")


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "foreign", "path_escape", "unarchived", "aggregate"]
)
def test_full_independent_case_evidence_is_required(tmp_path, mutation):
    archive, _, _ = prepare(tmp_path)

    def bad(context):
        result = fixture_evaluator(context)
        if mutation == "aggregate":
            return {"score": 1.0, "total_tokens": 0}
        cases = result.cases
        if mutation == "missing":
            cases = cases[:1]
        elif mutation == "duplicate":
            cases = [cases[0], cases[0]]
        elif mutation == "foreign":
            cases[0] = cases[0].model_copy(update={"case_id": "foreign"})
        else:
            cases[0] = cases[0].model_copy(
                update={
                    "evidence_files": [
                        "../private.json" if mutation == "path_escape" else "missing.json"
                    ]
                }
            )
        return result.model_copy(update={"cases": cases})

    result, _, _, _ = evaluated(archive, tmp_path, evaluator=bad)
    assert result["status"] == "evaluation_failed"


def test_failed_and_unknown_candidates_remain_visible_on_selection(tmp_path):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path, correct=1, tokens=20)
    evaluated(archive, tmp_path, "accurate", correct=2, tokens=30)
    evaluated(archive, tmp_path, "cheaper", correct=1, tokens=10)
    evaluated(archive, tmp_path, "tied", correct=1, tokens=10)
    evaluated(archive, tmp_path, "unknown-cost", correct=2, tokens=None)
    evaluated(archive, tmp_path, "failed", failed=True, tokens=40)

    def crash(_):
        raise RuntimeError("independent evaluator crashed")

    evaluated(archive, tmp_path, "bad-evaluator", evaluator=crash)
    feedback = archive.feedback_path()
    assert (feedback / "candidates/failed/development/artifacts/stderr.log").is_file()
    assert (feedback / "candidates/bad-evaluator/development/evaluation-error.json").is_file()
    selection = archive.select(operation_id="freeze-selection")
    assert selection["candidate_ids"] == ["accurate", "cheaper", "tied"]
    assert selection["excluded"] == {
        "unknown-cost": "unknown_objective",
        "bad-evaluator": "evaluation_failed",
        "failed": "failed_development_cases",
    }
    assert selection["ranked"]["failed"]["quality"] == 0.0
    assert selection["ranked"]["failed"]["failed_cases"] == 2
    assert selection["execution"] == "fixture"
    assert archive.select(operation_id="freeze-selection") == selection
    with pytest.raises(ValueError, match="permanently closed"):
        archive.feedback_path()


@pytest.mark.parametrize(
    ("failed", "reason"), [(True, "failed_development_cases"), (False, "zero_quality")]
)
def test_cheap_useless_candidate_is_diagnostic_only(tmp_path, failed, reason):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path, tokens=50)
    evaluated(archive, tmp_path, "cheap", correct=0, failed=failed, tokens=1)

    selection = archive.select(operation_id="select")
    assert selection["candidate_ids"] == ["baseline"]
    assert selection["raw_candidate_ids"] == ["baseline", "cheap"]
    assert selection["excluded"] == {"cheap": reason}
    assert selection["ranked"]["cheap"]["quality"] == 0.0
    assert selection["ranked"]["cheap"]["total_tokens"] == 2
    assert OptimizationArchive(archive.root).select(operation_id="select") == selection


def test_partially_failed_candidate_cannot_dominate_eligible_candidates(tmp_path):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path, correct=1, tokens=50)
    evaluated(archive, tmp_path, "eligible", correct=1, tokens=20)

    def partial_failure(context):
        result = fixture_evaluator(context)
        cases = list(result.cases)
        cases[1] = cases[1].model_copy(
            update={"status": "execution_failed", "quality": 0.0, "errors": ["worker failed"]}
        )
        return result.model_copy(update={"cases": cases})

    evaluated(archive, tmp_path, "partial", correct=1, tokens=1, evaluator=partial_failure)
    selection = archive.select(operation_id="select")
    assert selection["raw_candidate_ids"] == ["partial"]
    assert selection["candidate_ids"] == ["eligible"]
    assert selection["excluded"] == {"partial": "failed_development_cases"}
    assert selection["ranked"]["partial"]["quality"] == 0.5


@pytest.mark.parametrize("failed", [True, False])
def test_ineligible_baseline_prevents_private_final_preparation(tmp_path, failed):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path, correct=0, failed=failed)
    evaluated(archive, tmp_path, "eligible")
    archive.select(operation_id="select")
    private = tmp_path / "private-final"
    with pytest.raises(ValueError, match="baseline.*ineligible"):
        archive.begin_final(private, operation_id="final")
    assert not private.exists()
    assert archive.status()["phase"] == "selected"
    assert archive.status()["final"] is None


def test_unresolved_evaluator_is_not_replayed_and_can_be_explicitly_archived(tmp_path):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path)
    register(archive, tmp_path, "interrupted")
    output = artifacts(archive, tmp_path, "interrupted")
    calls = []

    def stop(context):
        calls.append(context)
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        archive.record_development(
            "interrupted", output, evaluator=stop, operation_id="interrupted-eval"
        )
    reopened = OptimizationArchive(archive.root)
    assert len(calls) == 1
    with pytest.raises(ValueError, match="unresolved"):
        reopened.record_development(
            "interrupted", output, evaluator=stop, operation_id="interrupted-eval"
        )
    with pytest.raises(ValueError, match="pending"):
        reopened.select(operation_id="select")
    recovered = reopened.recover_development(
        "interrupted", operation_id="interrupted-eval", reason="host observed worker exit"
    )
    assert recovered["status"] == "evaluation_failed"
    assert len(calls) == 1
    target = reopened.feedback_path() / "candidates/interrupted/development"
    assert (target / "artifacts/request.bin").read_bytes() == (output / "request.bin").read_bytes()
    assert read(target / "evaluation-error.json")["missing_artifacts"] == []
    assert reopened.select(operation_id="select")["excluded"]["interrupted"] == "evaluation_failed"


@pytest.mark.parametrize("kind", ["register", "development"])
@pytest.mark.parametrize("recovery", ["same_operation", "explicit"])
def test_feedback_published_before_journal_failure_is_recovered_without_callback_replay(
    tmp_path, monkeypatch, kind, recovery
):
    archive, _, _ = prepare(tmp_path)
    code = source(tmp_path)
    register_kwargs = {"instructions": "stable", "manifest": {}, "operation_id": "register"}
    if kind == "development":
        archive.register_candidate("baseline", code, **register_kwargs)
    output = artifacts(archive, tmp_path)
    operation_id = "register" if kind == "register" else "evaluate"
    calls = []

    def evaluate(context):
        calls.append(context)
        return fixture_evaluator(context)

    def perform(instance):
        if kind == "register":
            return instance.register_candidate("baseline", code, **register_kwargs)
        return instance.record_development(
            "baseline", output, evaluator=evaluate, operation_id=operation_id
        )

    original = archive_module._save

    def fail(path, value):
        if (
            path == archive.root / "journal.json"
            and value.get("operations", {}).get(operation_id, {}).get("status") == "completed"
        ):
            raise OSError("publication journal failed")
        original(path, value)

    monkeypatch.setattr(archive_module, "_save", fail)
    with pytest.raises(OSError, match="publication journal failed"):
        perform(archive)
    monkeypatch.setattr(archive_module, "_save", original)
    reopened = OptimizationArchive(archive.root)
    operation = reopened.status()["operations"][operation_id]
    assert operation["status"] == "pending"
    expected = operation["publication"]["result"]
    with pytest.raises(ValueError, match="pending publication"):
        reopened.feedback_path()
    if recovery == "explicit":
        # Recovery depends only on the committed host copy, not mutable original
        # source paths or another call to the independently evaluated callback.
        shutil.rmtree(code)
        shutil.rmtree(output)
        result = reopened.recover_publication(operation_id=operation_id)
    else:
        result = perform(reopened)
    assert result == expected
    assert len(calls) == (1 if kind == "development" else 0)
    assert reopened.status()["operations"][operation_id]["status"] == "completed"
    assert reopened.feedback_path().is_dir()


def test_partial_pending_feedback_copy_is_completed_from_frozen_host_files(tmp_path, monkeypatch):
    archive, _, _ = prepare(tmp_path)
    register(archive, tmp_path)
    output = artifacts(archive, tmp_path)
    target = tmp_path / "feedback/candidates/baseline/development"
    original = archive_module.os.replace
    copied = []

    def fail(source, destination):
        original(source, destination)
        if Path(destination).is_relative_to(target):
            copied.append(destination)
            raise OSError("copy interrupted after a complete file")

    monkeypatch.setattr(archive_module.os, "replace", fail)
    with pytest.raises(OSError, match="copy interrupted"):
        archive.record_development(
            "baseline", output, evaluator=fixture_evaluator, operation_id="evaluate"
        )
    assert len(copied) == 1
    monkeypatch.setattr(archive_module.os, "replace", original)
    reopened = OptimizationArchive(archive.root)
    assert (
        reopened.recover_development(
            "baseline", operation_id="evaluate", reason="host writer stopped"
        )["status"]
        == "evaluated"
    )
    assert (target / "artifacts/trace.jsonl").read_bytes() == (output / "trace.jsonl").read_bytes()
    assert (target / "evidence.json").is_file()


def test_partial_file_write_stays_outside_feedback_and_recovers_without_callback(
    tmp_path, monkeypatch
):
    archive, _, _ = prepare(tmp_path)
    register(archive, tmp_path)
    output = artifacts(archive, tmp_path)
    staging = Path(read(archive.root / "archive.json")["publication_staging_dir"])
    original = archive_module._write_file
    calls = []

    def evaluate(context):
        calls.append(context)
        return fixture_evaluator(context)

    def partial(path, raw):
        if path.is_relative_to(staging):
            original(path, raw[:3])
            raise OSError("disk full after prefix write")
        original(path, raw)

    monkeypatch.setattr(archive_module, "_write_file", partial)
    with pytest.raises(OSError, match="prefix write"):
        archive.record_development("baseline", output, evaluator=evaluate, operation_id="evaluate")
    assert len(calls) == 1
    assert len(list(staging.rglob("*.part"))) == 1
    feedback = tmp_path / "feedback"
    assert not list(feedback.rglob("*.part"))
    assert not list((feedback / "candidates/baseline/development").rglob("*"))
    monkeypatch.setattr(archive_module, "_write_file", original)
    reopened = OptimizationArchive(archive.root)
    assert reopened.recover_publication(operation_id="evaluate")["status"] == "evaluated"
    assert len(calls) == 1
    assert not list(staging.rglob("*.part"))
    copied = reopened.feedback_path() / "candidates/baseline/development/artifacts"
    for original_file in output.rglob("*"):
        if original_file.is_file():
            assert (
                copied / original_file.relative_to(output)
            ).read_bytes() == original_file.read_bytes()


@pytest.mark.parametrize("tamper", ["extra_file", "changed_feedback", "changed_host"])
def test_pending_publication_does_not_authorize_unexpected_or_changed_files(
    tmp_path, monkeypatch, tamper
):
    archive, _, _ = prepare(tmp_path)
    register(archive, tmp_path)
    output = artifacts(archive, tmp_path)
    original = archive_module._save

    def fail(path, value):
        if (
            path == archive.root / "journal.json"
            and value.get("operations", {}).get("evaluate", {}).get("status") == "completed"
        ):
            raise OSError("journal failed")
        original(path, value)

    monkeypatch.setattr(archive_module, "_save", fail)
    with pytest.raises(OSError):
        archive.record_development(
            "baseline", output, evaluator=fixture_evaluator, operation_id="evaluate"
        )
    monkeypatch.setattr(archive_module, "_save", original)
    target = (
        tmp_path
        / ("host" if tamper == "changed_host" else "feedback")
        / "candidates/baseline/development"
    )
    (target / ("unexpected.txt" if tamper == "extra_file" else "artifacts/trace.jsonl")).write_text(
        "tampered"
    )
    with pytest.raises(ValueError, match="unexpected|changed"):
        OptimizationArchive(archive.root)


@pytest.mark.parametrize("boundary", ["intent", "completion"])
def test_selection_intent_closes_search_and_resumes_only_its_fixed_decision(
    tmp_path, monkeypatch, boundary
):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path)
    original = archive_module._save

    def fail(path, value):
        if path == archive.root / "journal.json":
            if boundary == "intent" and value["phase"] == "selecting":
                original(path, value)
                raise OSError("selection intent fsync failed")
            if boundary == "completion" and value["phase"] == "selected":
                raise OSError("selection commit failed")
        original(path, value)

    monkeypatch.setattr(archive_module, "_save", fail)
    with pytest.raises(OSError, match="selection"):
        archive.select(operation_id="select")
    monkeypatch.setattr(archive_module, "_save", original)
    reopened = OptimizationArchive(archive.root)
    state = reopened.status()
    assert state["phase"] == "selecting"
    assert state["operations"]["select"]["status"] == "pending"
    with pytest.raises(ValueError, match="permanently closed"):
        reopened.feedback_path()
    with pytest.raises(ValueError, match="permanently closed"):
        register(reopened, tmp_path, "too-late")
    with pytest.raises(ValueError, match="permanently closed"):
        reopened.select(operation_id="different-selection")
    assert reopened.select(operation_id="select") == state["selection"]
    assert reopened.status()["phase"] == "selected"


@pytest.mark.parametrize(
    "location",
    [
        "host/candidates/baseline/bundle/source/strategy.py",
        "host/candidates/baseline/development/artifacts/trace.jsonl",
        "host/frozen/backend/config.py",
        "feedback/candidates/baseline/bundle/source/strategy.py",
        "feedback/candidates/baseline/development/evidence.json",
        "feedback/unexpected-private-data.txt",
    ],
)
def test_detects_archived_artifact_mutation_before_reuse(tmp_path, location):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path)
    (tmp_path / location).write_text("mutated")
    with pytest.raises(ValueError, match="changed|Unexpected"):
        OptimizationArchive(archive.root)


@pytest.mark.parametrize("link_kind", ["file", "directory", "root", "hardlink"])
def test_candidate_links_cannot_escape_source_root(tmp_path, link_kind):
    archive, _, _ = prepare(tmp_path)
    path = source(tmp_path)
    private = tmp_path / "private"
    private.mkdir()
    secret = private / "heldout.txt"
    secret.write_text("PRIVATE HELDOUT CONTENT")
    if link_kind == "file":
        (path / "leak.txt").symlink_to(secret)
    elif link_kind == "directory":
        (path / "leak").symlink_to(private, target_is_directory=True)
    elif link_kind == "hardlink":
        (path / "leak.txt").hardlink_to(secret)
    else:
        linked = tmp_path / "linked-source"
        linked.symlink_to(path, target_is_directory=True)
        path = linked
    with pytest.raises(ValueError, match="links|Symlink"):
        archive.register_candidate(
            "baseline", path, instructions="", manifest={}, operation_id="register"
        )
    assert not list((tmp_path / "feedback").rglob("*.txt"))


@pytest.mark.parametrize(
    "candidate_id", ["../escape", "nested/id", ".", "..", "/absolute", "bad\\id"]
)
def test_candidate_ids_cannot_escape_archive(tmp_path, candidate_id):
    archive, _, _ = prepare(tmp_path)
    with pytest.raises(ValueError, match="candidate id"):
        archive.register_candidate(
            candidate_id, source(tmp_path), instructions="", manifest={}, operation_id="register"
        )


def test_artifact_limit_fails_without_silently_dropping_logs(tmp_path):
    archive, inputs, config = prepare(tmp_path)
    other = OptimizationArchive.create(
        tmp_path / "small-host",
        tmp_path / "small-feedback",
        config=config.model_copy(update={"max_bytes": 100_000}),
        frozen_inputs=inputs,
    )
    register(other, tmp_path)
    output = artifacts(other, tmp_path)
    (output / "huge.log").write_bytes(b"x" * 100_001)
    with pytest.raises(ValueError, match="limits"):
        other.record_development(
            "baseline", output, evaluator=fixture_evaluator, operation_id="evaluate"
        )
    assert other.status()["candidates"]["baseline"]["status"] == "registered"
    assert not (other.feedback_path() / "candidates/baseline/development").exists()


@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
def test_private_final_results_never_enter_feedback_and_search_stays_closed(tmp_path, status):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path)
    feedback = archive.feedback_path()
    before = {
        path.relative_to(feedback): path.read_bytes()
        for path in feedback.rglob("*")
        if path.is_file()
    }
    archive.select(operation_id="select")
    private = tmp_path / "private-final"
    started = archive.begin_final(private, operation_id="heldout-once")
    (private / "heldout-results.json").write_text('"PRIVATE FINAL RESULTS"')
    sealed = archive.finish_final(operation_id="heldout-once", status=status)
    assert sealed["status"] == status
    reopened = OptimizationArchive(archive.root)
    assert reopened.begin_final(private, operation_id="heldout-once")["status"] == status
    assert reopened.finish_final(operation_id="heldout-once", status=status) == sealed
    assert started["operation_id"] == sealed["operation_id"]
    after = {
        path.relative_to(feedback): path.read_bytes()
        for path in feedback.rglob("*")
        if path.is_file()
    }
    assert before == after
    with pytest.raises(ValueError, match="permanently closed"):
        reopened.feedback_path()
    with pytest.raises(ValueError, match="permanently closed"):
        register(reopened, tmp_path, "after-final")
    with pytest.raises(ValueError, match="already bound"):
        reopened.begin_final(tmp_path / "another-final", operation_id="new-attempt")
    (private / "heldout-results.json").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        reopened.finish_final(operation_id="heldout-once", status=status)


def test_final_directory_creation_failure_cannot_reopen_search_or_replay(tmp_path, monkeypatch):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path)
    archive.select(operation_id="select")
    private = tmp_path / "private-final"
    original = Path.mkdir
    attempts = []

    def fail(path, *args, **kwargs):
        if path == private:
            attempts.append(path)
            raise OSError("disk unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        archive.begin_final(private, operation_id="final")
    reopened = OptimizationArchive(archive.root)
    assert reopened.status()["phase"] == "final_started"
    assert reopened.begin_final(private, operation_id="final")["status"] == "started"
    assert len(attempts) == 1
    with pytest.raises(ValueError, match="permanently closed"):
        reopened.feedback_path()


@pytest.mark.parametrize("failure", ["mkdir", "selection", "begin_commit"])
@pytest.mark.parametrize("terminal", ["failed", "interrupted"])
def test_failed_final_preparation_can_close_with_explicit_missing_evidence(
    tmp_path, monkeypatch, failure, terminal
):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path)
    feedback = archive.feedback_path()
    before = {
        path.relative_to(feedback): path.read_bytes()
        for path in feedback.rglob("*")
        if path.is_file()
    }
    archive.select(operation_id="select")
    private = tmp_path / "private-final"
    original_mkdir, original_save = Path.mkdir, archive_module._save

    def fail_mkdir(path, *args, **kwargs):
        if failure == "mkdir" and path == private:
            raise OSError("initial final directory failed")
        return original_mkdir(path, *args, **kwargs)

    def fail_save(path, value):
        if failure == "selection" and path == private / "selection.json":
            raise OSError("initial selection write failed")
        if (
            failure == "begin_commit"
            and path == archive.root / "journal.json"
            and value.get("operations", {}).get("final", {}).get("status") == "completed"
        ):
            raise OSError("initial final commit failed")
        original_save(path, value)

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    monkeypatch.setattr(archive_module, "_save", fail_save)
    with pytest.raises(OSError, match="initial"):
        archive.begin_final(private, operation_id="final")
    monkeypatch.setattr(Path, "mkdir", original_mkdir)
    monkeypatch.setattr(archive_module, "_save", original_save)
    reopened = OptimizationArchive(archive.root)
    assert reopened.status()["phase"] == "final_started"
    reopened.begin_final(private, operation_id="final")
    with pytest.raises(ValueError, match="original preparation"):
        reopened.finish_final(operation_id="final", status="completed")
    result = reopened.finish_final(operation_id="final", status=terminal)
    sealed = read(private / "final-archive.json")
    assert sealed["files"] == {}
    assert sealed["missing_evidence"] == (
        ["selection.json"] if failure == "selection" else ["private_directory", "selection.json"]
    )
    assert result["status"] == terminal
    assert reopened.finish_final(operation_id="final", status=terminal) == result
    assert {
        path.relative_to(feedback): path.read_bytes()
        for path in feedback.rglob("*")
        if path.is_file()
    } == before
    assert set(path.name for path in private.iterdir()) == {"final-archive.json"}
    with pytest.raises(ValueError, match="permanently closed"):
        reopened.feedback_path()


def test_missing_preparation_seal_intent_survives_another_journal_write_failure(
    tmp_path, monkeypatch
):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path)
    archive.select(operation_id="select")
    private = tmp_path / "private-final"
    original = Path.mkdir

    def fail_mkdir(path, *args, **kwargs):
        if path == private:
            raise OSError("directory unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    with pytest.raises(OSError):
        archive.begin_final(private, operation_id="final")
    monkeypatch.setattr(Path, "mkdir", original)
    original_save = archive_module._save

    def fail_journal(path, value):
        if path == archive.root / "journal.json" and value["phase"] == "final_interrupted":
            raise OSError("terminal commit failed")
        original_save(path, value)

    monkeypatch.setattr(archive_module, "_save", fail_journal)
    with pytest.raises(OSError, match="terminal commit"):
        archive.finish_final(operation_id="final", status="interrupted")
    sealed = (private / "final-archive.json").read_bytes()
    monkeypatch.setattr(archive_module, "_save", original_save)
    reopened = OptimizationArchive(archive.root)
    assert (
        reopened.finish_final(operation_id="final", status="interrupted")["status"] == "interrupted"
    )
    assert (private / "final-archive.json").read_bytes() == sealed
    assert read(private / "final-archive.json")["missing_evidence"] == [
        "private_directory",
        "selection.json",
    ]


@pytest.mark.parametrize("tamper", [False, True])
def test_final_archive_written_before_journal_failure_is_reconciled_without_reexecution(
    tmp_path, monkeypatch, tamper
):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path)
    archive.select(operation_id="select")
    private = tmp_path / "private-final"
    archive.begin_final(private, operation_id="final")
    (private / "results.json").write_text('"PRIVATE RESULTS"')
    original = archive_module._save
    writes = []

    def fail_journal(path, value):
        writes.append(path)
        if path == archive.root / "journal.json" and value["phase"] == "final_completed":
            raise OSError("journal fsync failed")
        original(path, value)

    monkeypatch.setattr(archive_module, "_save", fail_journal)
    with pytest.raises(OSError, match="journal fsync failed"):
        archive.finish_final(operation_id="final", status="completed")
    assert (private / "final-archive.json").is_file()
    assert read(archive.root / "journal.json")["phase"] == "final_started"
    archived_bytes = (private / "final-archive.json").read_bytes()
    monkeypatch.setattr(archive_module, "_save", original)
    reopened = OptimizationArchive(archive.root)
    if tamper:
        (private / "results.json").write_text("changed private evidence")
        with pytest.raises(ValueError, match="does not match"):
            reopened.finish_final(operation_id="final", status="completed")
    else:
        assert (
            reopened.finish_final(operation_id="final", status="completed")["status"] == "completed"
        )
        assert (private / "final-archive.json").read_bytes() == archived_bytes
    assert writes.count(private / "final-archive.json") == 1
    with pytest.raises(ValueError, match="permanently closed"):
        reopened.feedback_path()


@pytest.mark.parametrize("relative", ["feedback/private", "host/private", "feedback", "host"])
def test_final_directory_cannot_be_inside_search_or_host_archive(tmp_path, relative):
    archive, _, _ = prepare(tmp_path)
    evaluated(archive, tmp_path)
    archive.select(operation_id="select")
    with pytest.raises(ValueError, match="separate"):
        archive.begin_final(tmp_path / relative, operation_id="final")


def test_heldout_manifest_cannot_be_created_as_search_archive(tmp_path):
    _, inputs, config = prepare(tmp_path)
    manifest = read(inputs["development_manifest"])
    manifest["split"] = "heldout"
    write_json(inputs["development_manifest"], manifest)
    with pytest.raises(ValueError, match="development"):
        OptimizationArchive.create(
            tmp_path / "other-host",
            tmp_path / "other-feedback",
            config=config,
            frozen_inputs=inputs,
        )
