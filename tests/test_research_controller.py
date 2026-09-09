from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from filelock import FileLock, Timeout

from research_harness.backend import Backend
from research_harness.discovery_models import Candidate, DataNeed, ProposalDraft
from research_harness.evaluation import controller
from research_harness.evaluation.benchmark import load_benchmark
from research_harness.evaluation.controller import (
    ARMS,
    ComparisonConfig,
    ExecutionArtifacts,
    finalize_comparison,
    prepare_comparison,
    record_interruption,
    run_case,
)
from research_harness.evaluation.fixtures import FixtureProvider
from research_harness.execution import DiscoverySettings
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.services.research import ResearchService
from research_harness.util import canonical_json, digest, write_json

DEVELOPMENT = Path(__file__).resolve().parents[1] / "examples/evaluation/development"


@pytest.fixture
def prepared(tmp_path):
    package = tmp_path / "input"
    shutil.copytree(DEVELOPMENT, package)
    manifest = json.loads((package / "manifest.json").read_bytes())
    manifest["cases"] = [
        entry
        for entry in manifest["cases"]
        if Path(entry["path"]).stem in {"filing-stable-accession", "tariff-pagination"}
    ]
    write_json(package / "manifest.json", manifest)
    out = tmp_path / "comparison"
    prepare_comparison(
        package / "manifest.json",
        out,
        instructions="Same semantic instructions.\n",
        config=ComparisonConfig(settings=DiscoverySettings(max_searches=1, max_probes=2)),
    )
    return out


def fixture_executor(task):
    """Infrastructure fixture with real service artifacts; never a model score."""
    benchmark = load_benchmark(task.fixture_path.parents[1] / "manifest.json")
    case = benchmark.cases[task.case_id]
    fixture = json.loads(task.fixture_path.read_bytes())
    responses = {response["url"]: response for response in fixture["responses"]}

    def handler(request):
        response = responses[str(request.url)]
        body = response["body"]
        return httpx.Response(
            response.get("status", 200),
            headers=response.get("headers", {}),
            content=body if isinstance(body, str) else canonical_json(body),
        )

    output = task.output / "research"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = ResearchService(
            output,
            backend=Backend.local(task.output / "backend"),
            http_client=client,
            public_only=False,
        )
        service.begin(
            task.brief,
            model=task.config.model,
            limits=task.config.settings.limits(),
            deadline_seconds=task.config.settings.deadline_seconds,
        )
        service.search(
            task.brief, provider=FixtureProvider(fixture["search_results"]), operation_id="search"
        )
        source = case.sources[0]
        probe = service.probe(source, operation_id="probe")
        service.submit_proposal(
            ProposalDraft(
                name=case.id,
                title=case.title,
                research_question=task.brief,
                needs=[DataNeed(id="research", description=task.brief, required=True)],
                candidates=[
                    Candidate(
                        name=source.name,
                        purpose="Test infrastructure",
                        covers=["research"],
                        status="ready",
                        evidence_urls=[source.url],
                        source=source,
                        probe_id=probe["probe_id"],
                        freshness_assessment="Fixture only",
                        historical_coverage="Fixture sample",
                        access_notes="Synthetic fixture",
                        limitations=["Software check, not model performance"],
                    )
                ],
                open_questions=[],
            ),
            operation_id="submit",
        )
    return ExecutionArtifacts(research=output, metadata={"execution": "test-service-fixture"})


def gateway_fixture(task, *, unknown=False, wrong_binding=False, relaxed_limits=False):
    response = {
        "id": "response-fixture",
        "object": "response",
        "status": "completed",
        "model": task.config.model,
        "output": [],
    }
    if not unknown:
        response["usage"] = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    binding = task.gateway_binding
    assert binding.case_id == task.case_id and binding.phase == "discovery"
    if wrong_binding:
        binding = binding.model_copy(update={"execution_id": "f" * 32})
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        with ResponsesGateway(
            task.output / "gateway",
            model=task.config.model,
            settings=task.config.settings.model_copy(
                update={"max_rounds": 30, "deadline_seconds": 86400}
            )
            if relaxed_limits
            else task.config.settings,
            upstream_base_url="https://provider.example/v1",
            client=client,
            binding=binding,
        ) as gateway:
            result = httpx.post(
                gateway.base_url + "/responses",
                headers={"Authorization": "Bearer " + gateway.api_key},
                json={"model": task.config.model, "input": task.brief},
            )
            assert result.status_code == 200
    return gateway.output / "archive.json"


def test_preparation_freezes_bytes_controls_and_candidate_task(prepared):
    plan = json.loads((prepared / "comparison.json").read_bytes())
    controls = [plan["controls"][arm] for arm in ARMS]
    assert controls[0]["runtime"] != controls[1]["runtime"]
    assert {k: v for k, v in controls[0].items() if k != "runtime"} == {
        k: v for k, v in controls[1].items() if k != "runtime"
    }
    assert controls[0]["model_settings"] == {
        "max_output_tokens": 6000,
        "reasoning": {"effort": "none"},
    }
    assert controls[0]["strategy_sha256"] == digest(b"Same semantic instructions.\n")
    assert controls[0]["budgets"]["search"] == 1
    assert plan["sources"]["src/research_harness/services/research.py"]
    assert (prepared / "implementation/src/research_harness/evaluation/controller.py").is_file()
    seen = []

    def capture(task):
        seen.append(task)
        raise RuntimeError("Fixture stop before provider")

    run_case(prepared, "direct", "filing-stable-accession", capture)
    task = seen[0]
    assert not hasattr(task, "specification") and not hasattr(task, "fixture_plan")
    assert set(json.loads((task.output / "task.json").read_bytes())) == {"id", "brief"}


def test_completed_and_failed_cases_remain_in_both_arms(prepared):
    calls = []

    def execute(task):
        calls.append((task.arm, task.case_id))
        if task.arm == "omnigent" and task.case_id == "tariff-pagination":
            (task.output / "partial.txt").write_text("Provider request may have been billed")
            raise RuntimeError("Synthetic provider failure")
        return fixture_executor(task)

    for arm in ARMS:
        for case_id in ("filing-stable-accession", "tariff-pagination"):
            entry = run_case(prepared, arm, case_id, execute)
            assert run_case(prepared, arm, case_id, execute) == entry
    assert len(calls) == 4
    report = finalize_comparison(prepared)
    assert report["fixed_controls"]["execution"] == "fixture"
    assert all(arm["summary"]["cases"] == 2 for arm in report["arms"])
    assert report["arms"][1]["summary"]["correctness"]["execution_failed"] == 1
    assert report["arms"][1]["summary"]["efficiency"]["model_tokens"]["total"] is None
    assert all(value["case_counts"]["missing"] == 2 for value in report["workflow"].values())
    question_ids = []
    for arm in ARMS:
        saved = json.loads(
            (prepared / arm / "cases/filing-stable-accession/research/proposal.json").read_bytes()
        )
        question_ids.append(saved["registry"]["question_id"])
    assert question_ids[0] != question_ids[1]
    assert (prepared / "omnigent/cases/tariff-pagination/partial.txt").is_file()


def test_interruption_requires_explicit_resolution_without_replay(prepared):
    calls = []

    def crash(task):
        calls.append(task.case_id)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_case(prepared, "direct", "filing-stable-accession", crash)
    with pytest.raises(ValueError, match="unresolved"):
        run_case(prepared, "direct", "filing-stable-accession", crash)
    entry = record_interruption(
        prepared,
        "direct",
        "filing-stable-accession",
        reason="Inspected stopped process and incomplete trace",
    )
    assert entry["run"]["failed_run_tokens"] is None
    assert run_case(prepared, "direct", "filing-stable-accession", crash) == entry
    assert len(calls) == 1
    with pytest.raises(ValueError, match="Only unresolved"):
        record_interruption(prepared, "direct", "tariff-pagination", reason="Not started")


def test_active_controller_cannot_be_replayed_or_marked_interrupted(prepared):
    with FileLock(str(prepared / "direct/controller.lock")):
        with pytest.raises(Timeout):
            run_case(prepared, "direct", "filing-stable-accession", fixture_executor)
        with pytest.raises(Timeout):
            record_interruption(
                prepared, "direct", "filing-stable-accession", reason="Still active"
            )


@pytest.mark.parametrize(
    "target",
    [
        "instructions.md",
        "benchmark/fixtures/filing-stable-accession.json",
        "implementation/src/research_harness/config.py",
    ],
)
def test_frozen_inputs_cannot_change_before_execution(prepared, target):
    path = prepared / target
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="changed|hash mismatch"):
        run_case(prepared, "direct", "filing-stable-accession", fixture_executor)


def test_current_source_change_requires_new_experiment(prepared, monkeypatch):
    real = controller.source_fingerprints()
    monkeypatch.setattr(controller, "source_fingerprints", lambda: {**real, "changed.py": "a" * 64})
    with pytest.raises(ValueError, match="Implementation changed"):
        run_case(prepared, "direct", "filing-stable-accession", fixture_executor)


def test_result_outside_case_is_failed_and_cannot_be_reused(prepared, tmp_path):
    outsider = tmp_path / "foreign"
    outsider.mkdir()
    write_json(outsider / "proposal.json", {})
    entry = run_case(
        prepared,
        "direct",
        "filing-stable-accession",
        lambda task: ExecutionArtifacts(research=outsider),
    )
    assert entry["status"] == "failed"
    assert "inside their case" in entry["run"]["error"]


def test_finalization_refuses_pending_cases_and_modified_saved_artifacts(prepared):
    with pytest.raises(ValueError, match="every case"):
        finalize_comparison(prepared)
    for arm in ARMS:
        for case_id in ("filing-stable-accession", "tariff-pagination"):
            run_case(prepared, arm, case_id, fixture_executor)
    path = prepared / "direct/cases/filing-stable-accession/research/proposal.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="artifacts changed"):
        finalize_comparison(prepared)


def test_preparation_rejects_heldout_without_creating_output(tmp_path):
    package = tmp_path / "heldout"
    shutil.copytree(DEVELOPMENT, package)
    manifest = json.loads((package / "manifest.json").read_bytes())
    manifest["split"] = "heldout"
    write_json(package / "manifest.json", manifest)
    with pytest.raises(ValueError, match="development cases only"):
        prepare_comparison(package / "manifest.json", tmp_path / "run", instructions="Policy")
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("change", ["add-scoring-input", "foreign-case-reference"])
def test_finalization_checks_complete_inventory_and_case_binding(prepared, change):
    for arm in ARMS:
        for case_id in ("filing-stable-accession", "tariff-pagination"):
            run_case(prepared, arm, case_id, fixture_executor)
    if change == "add-scoring-input":
        write_json(
            prepared
            / "direct/cases/filing-stable-accession/research/omnigent/runtime-usage.discovery.json",
            {},
        )
    else:
        path = prepared / "direct/journal.json"
        journal = json.loads(path.read_bytes())
        journal["cases"]["filing-stable-accession"]["run"]["artifacts"] = (
            "cases/tariff-pagination/research"
        )
        write_json(path, journal)
    with pytest.raises(ValueError, match="artifacts changed|inside their case"):
        finalize_comparison(prepared)


def test_directory_creation_failure_is_durably_resolvable(prepared, monkeypatch):
    original = Path.mkdir
    target = prepared / "direct/cases/filing-stable-accession"

    def crash(path, *args, **kwargs):
        if path == target:
            raise KeyboardInterrupt
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", crash)
    with pytest.raises(KeyboardInterrupt):
        run_case(prepared, "direct", "filing-stable-accession", fixture_executor)
    entry = record_interruption(
        prepared,
        "direct",
        "filing-stable-accession",
        reason="Controller stopped after reservation, before execution",
    )
    assert entry["status"] == "failed"
    assert not target.exists()


def test_failed_executor_retains_partial_artifacts_with_unknown_total(prepared):
    class PartialError(RuntimeError):
        pass

    def fail(task):
        research = task.output / "research"
        research.mkdir()
        write_json(research / "partial.json", {"input_tokens_lower_bound": 12})
        error = PartialError("Stream interrupted")
        error.artifacts = ExecutionArtifacts(research=research, metadata={"usage_complete": False})
        raise error

    entry = run_case(prepared, "direct", "filing-stable-accession", fail)
    assert entry["run"]["artifacts"] == "cases/filing-stable-accession/research"
    assert entry["run"]["failed_run_tokens"] is None
    assert entry["artifact_hashes"]["research/partial.json"]
    assert entry["metadata"]["usage_complete"] is False


@pytest.mark.parametrize(
    "mode",
    [
        "completed",
        "failed",
        "interrupted",
        "no-proposal",
        "unknown",
        "wrong-binding",
        "relaxed-limits",
    ],
)
def test_gateway_usage_survives_case_failure_and_requires_matching_evidence(prepared, mode):
    class PartialError(RuntimeError):
        pass

    recovered = {}

    def execute(task):
        archive = gateway_fixture(
            task,
            unknown=mode == "unknown",
            wrong_binding=mode == "wrong-binding",
            relaxed_limits=mode == "relaxed-limits",
        )
        artifacts = replace(fixture_executor(task), gateway_usage=archive)
        recovered[task.arm] = artifacts
        if mode == "failed":
            error = PartialError("Provider work completed; task failed afterwards")
            error.artifacts = artifacts
            raise error
        if mode == "interrupted":
            raise KeyboardInterrupt
        if mode == "no-proposal":
            (artifacts.research / "proposal.json").unlink()
        return artifacts

    ids = []
    for arm in ARMS:
        if mode == "interrupted":
            with pytest.raises(KeyboardInterrupt):
                run_case(prepared, arm, "filing-stable-accession", execute)
            entry = record_interruption(
                prepared,
                arm,
                "filing-stable-accession",
                reason="Host stopped after provider response; gateway has closed",
                artifacts=recovered[arm],
            )
        else:
            entry = run_case(prepared, arm, "filing-stable-accession", execute)
        ids.append(entry["execution_id"])
        assert entry["artifact_hashes"]["gateway/archive.json"]
        assert entry["artifact_hashes"]["gateway/request-0001/response.body"]
        run_case(prepared, arm, "tariff-pagination", fixture_executor)
    assert len(set(ids)) == 2
    report = finalize_comparison(prepared)
    for arm in report["arms"]:
        result = next(
            value["score"]
            for value in arm["results"]
            if value["case_id"] == "filing-stable-accession"
        )
        assert result["token_evidence_source"] == "gateway"
        if mode in {"failed", "interrupted", "no-proposal"}:
            assert result["status"] == "execution_failed" and result["quality"] == 0
        else:
            assert result["status"] == "ok"
        if mode in {"wrong-binding", "relaxed-limits"}:
            assert result["gateway_usage"]["status"] == "invalid"
            assert result["total_tokens"] is None
            assert result["completed_token_lower_bound"] is None
        elif mode == "unknown":
            assert result["gateway_usage"]["status"] == "verified_lower_bound"
            assert result["total_tokens"] is None
            assert result["completed_token_lower_bound"] == 0
        else:
            assert result["gateway_usage"]["status"] == "verified_complete"
            assert result["total_tokens"] == 110
            assert arm["summary"]["efficiency"]["model_tokens"]["known_subtotal"] == 110


def test_gateway_artifacts_are_frozen_before_comparison(prepared):
    def execute(task):
        return replace(fixture_executor(task), gateway_usage=gateway_fixture(task))

    for arm in ARMS:
        for case_id in ("filing-stable-accession", "tariff-pagination"):
            run_case(prepared, arm, case_id, execute)
    response = prepared / "direct/cases/filing-stable-accession/gateway/request-0001/response.body"
    response.write_bytes(response.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="artifacts changed"):
        finalize_comparison(prepared)


def test_finalization_cannot_drop_recorded_usage_reference(prepared):
    def with_usage(task):
        result = fixture_executor(task)
        usage = task.output / "usage.json"
        write_json(usage, {"note": "Malformed usage remains explicitly invalid to evaluator"})
        return ExecutionArtifacts(research=result.research, runtime_usage=usage)

    for arm in ARMS:
        for case_id in ("filing-stable-accession", "tariff-pagination"):
            run_case(prepared, arm, case_id, with_usage)
    path = prepared / "direct/journal.json"
    journal = json.loads(path.read_bytes())
    journal["cases"]["filing-stable-accession"]["run"]["runtime_usage"] = None
    write_json(path, journal)
    with pytest.raises(ValueError, match="frozen case binding"):
        finalize_comparison(prepared)
