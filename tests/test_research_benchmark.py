from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from research_harness.backend import Backend
from research_harness.discovery_models import ProposalDraft
from research_harness.evaluation import frontier, score
from research_harness.evaluation.benchmark import (
    CaseRun,
    RunControls,
    compare_runs,
    gateway_binding_for_case,
    load_benchmark,
    validate_splits,
)
from research_harness.evaluation.fixtures import (
    FixtureProvider,
    backend_fingerprint,
    run_fixture_arm,
)
from research_harness.services.research import ResearchService
from research_harness.util import canonical_json, digest, write_json

DEVELOPMENT = Path(__file__).resolve().parents[1] / "examples/evaluation/development/manifest.json"


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    output = tmp_path_factory.mktemp("benchmark-fixtures")
    benchmark = load_benchmark(DEVELOPMENT)
    backend_hash = backend_fingerprint()
    paths = [
        run_fixture_arm(benchmark, output / policy, policy=policy, backend_sha256=backend_hash)
        for policy in ("authored-selection", "first-listed-source")
    ]
    return paths


def changed_run(path, name, change):
    value = json.loads(path.read_bytes())
    change(value)
    changed = path.parent / f"{name}.json"
    write_json(changed, value)
    return changed


def test_development_suite_has_twenty_authored_diverse_cases_and_candidate_safe_tasks():
    benchmark = load_benchmark(DEVELOPMENT)
    assert len(benchmark.cases) == 20
    assert all(case.review_status == "authored" for case in benchmark.cases.values())
    tags = {tag for case in benchmark.cases.values() for tag in case.tags}
    assert {
        "unsupported",
        "irrelevant-parseable",
        "pagination",
        "timestamps",
        "historical-gap",
        "valid-alternatives",
        "stable-identity",
        "multiple-needs",
    } <= tags
    for case in benchmark.cases.values():
        assert case.task() == {"id": case.id, "brief": case.brief}
        assert case.manual_checks
        fixture = json.loads((DEVELOPMENT.parent / case.fixtures.path).read_bytes())
        answer_urls = {source.url for source in case.sources} | {
            choice.url for choice in case.fixture_plan.unsupported
        }
        assert {result["url"] for result in fixture["search_results"]} - answer_urls


def test_offline_comparison_uses_real_service_artifacts_without_claiming_model_scores(generated):
    report = compare_runs(DEVELOPMENT, generated, axis="strategy")
    assert report["review_status_counts"] == {"authored": 20, "reviewed": 0}
    assert report["fixed_controls"]["execution"] == "fixture"
    selected, first = report["arms"]
    assert selected["summary"]["correctness"]["valid"] == 20
    assert first["summary"]["correctness"]["valid"] == 20
    assert (
        selected["summary"]["usefulness"]["macro_quality"]
        > first["summary"]["usefulness"]["macro_quality"]
    )
    scores = {result["case_id"]: result["score"] for result in first["results"]}
    assert scores["tariff-pagination"]["status"] == "ok"
    assert scores["tariff-pagination"]["quality"] == 0
    assert scores["customs-relevance"]["quality"] == 0
    assert scores["vendor-valid-alternatives"]["quality"] == 0
    assert scores["license-history-access"]["quality"] == 0
    assert 0 < scores["sanctions-current-history"]["quality"] < 1
    for result in selected["results"]:
        assert result["score"]["quality"] > scores[result["case_id"]]["quality"], result["case_id"]
    assert selected["summary"]["usefulness"]["macro_quality"] == pytest.approx(0.9495238095238095)
    assert first["summary"]["usefulness"]["macro_quality"] == pytest.approx(1 / 12)
    assert (
        selected["summary"]["usefulness"]["macro_requirement_recall"]
        < selected["summary"]["usefulness"]["macro_research_recall"]
    )
    for arm in report["arms"]:
        efficiency = arm["summary"]["efficiency"]
        assert efficiency["model_tokens"]["total"] is None
        assert efficiency["model_tokens"]["unknown_runs"] == 20
        assert efficiency["provider_cost_usd"]["total"] is None
        assert efficiency["elapsed_seconds"]["known_runs"] == 20
        assert "recovery" in arm["summary"]["correctness"]["unmeasured"]
    saved = json.loads((generated[0].parent / "cases/export-notices/proposal.json").read_bytes())
    assert saved["registry"]["proposal_id"] and saved["registry"]["pipeline_version_id"]
    assert saved["usage"] == {}
    assert report["arms"][0]["results"][0]["score"]["artifact_sha256"]["receipts.json"]


@pytest.mark.parametrize("source_id", ["vendor-advisories", "vendor-feed"])
def test_both_legitimate_vendor_alternatives_still_earn_full_credit(tmp_path, source_id):
    benchmark = load_benchmark(DEVELOPMENT)
    case = benchmark.cases["vendor-valid-alternatives"]
    case.fixture_plan.source_ids = [source_id]
    benchmark = replace(benchmark, cases={case.id: case})
    manifest = run_fixture_arm(benchmark, tmp_path / source_id, policy="authored-selection")
    result = score(manifest.parent / "cases" / case.id, case.specification)
    assert result["status"] == "ok"
    assert result["quality"] == 1


@pytest.mark.parametrize(
    "case_id",
    [
        "license-history-access",
        "sanctions-current-history",
        "rates-workbook-unsupported",
        "legislation-unsupported-votes",
    ],
)
def test_authored_gap_credit_comes_from_scoped_service_captures(generated, case_id):
    benchmark = load_benchmark(DEVELOPMENT)
    authored = generated[0].parent / "cases" / case_id
    result = score(authored, benchmark.cases[case_id].specification)
    assert result["status"] == "ok"
    assert result["gap_recall"] > 0
    assert result["requirement_recall"] < result["research_recall"] < 1
    receipt_rows = json.loads((authored / "receipts.json").read_bytes())
    evidence_urls = {
        row["payload"]["url"] for row in receipt_rows if row["kind"] == "inspection"
    } | {
        capture["url"]
        for row in receipt_rows
        if row["kind"] == "probe"
        for capture in row["payload"]["captures"]
    }
    assert {
        choice.url for choice in benchmark.cases[case_id].fixture_plan.unsupported
    } <= evidence_urls
    naive = json.loads((generated[1].parent / "cases" / case_id / "proposal.json").read_bytes())
    assert all(candidate["status"] == "ready" for candidate in naive["proposal"]["candidates"])


@pytest.mark.parametrize(
    "control,value",
    [
        ("model", "different-model"),
        ("provider", "different-provider"),
        ("model_settings", {"temperature": 1}),
        ("provider_settings", {"different_index": True}),
        ("budgets", {"search": 7, "inspection": 16, "probe": 12, "deadline_seconds": 600}),
        ("backend_sha256", "a" * 64),
        ("execution", "model"),
        ("runtime", "other-runtime"),
    ],
)
def test_strategy_comparison_rejects_changed_controls(generated, control, value):
    altered = changed_run(
        generated[1],
        f"changed-{control}",
        lambda manifest: manifest["controls"].update({control: value}),
    )
    with pytest.raises(ValueError, match="Uncontrolled comparison"):
        compare_runs(DEVELOPMENT, [generated[0], altered], axis="strategy")


def test_runtime_comparison_requires_identical_strategy_and_accepts_only_runtime_difference(
    generated,
):
    with pytest.raises(ValueError, match="only runtime may differ"):
        compare_runs(DEVELOPMENT, generated)
    original = json.loads(generated[0].read_bytes())
    changed = changed_run(
        generated[0],
        "fixture-runtime-contract",
        lambda manifest: manifest.update(
            name="fixture-runtime-contract",
            controls={**manifest["controls"], "runtime": "fixture-protocol-transport"},
        ),
    )
    report = compare_runs(DEVELOPMENT, [generated[0], changed])
    assert report["comparison_axis"] == "runtime"
    assert report["fixed_controls"]["strategy_sha256"] == original["controls"]["strategy_sha256"]


def test_optional_code_strategy_preserves_legacy_control_and_case_identity(generated):
    raw = json.loads(generated[0].read_bytes())["controls"]
    controls = RunControls.model_validate({**raw, "code_strategy_sha256": None})
    assert "code_strategy_sha256" not in controls.model_dump(mode="json")
    case = next(iter(load_benchmark(DEVELOPMENT).cases.values()))
    legacy = dict(raw)
    if legacy.get("budget_control") is None:
        legacy.pop("budget_control", None)
    expected = digest(
        canonical_json(
            {"task": case.task(), "fixture_sha256": case.fixtures.sha256, "controls": legacy}
        )
    )
    binding = gateway_binding_for_case(case, controls, "a" * 32)
    assert binding.task_sha256 == expected
    changed = controls.model_copy(update={"code_strategy_sha256": "b" * 64})
    assert gateway_binding_for_case(case, changed, "a" * 32).task_sha256 != expected


@pytest.mark.parametrize("change_instructions", [False, True])
def test_code_strategy_changes_are_allowed_only_on_strategy_axis(generated, change_instructions):
    def change(value):
        value["name"] = "code-candidate"
        value["controls"]["code_strategy_sha256"] = "c" * 64
        if change_instructions:
            value["controls"]["strategy_sha256"] = "d" * 64

    candidate = changed_run(generated[0], f"code-axis-{change_instructions}", change)
    with pytest.raises(ValueError, match="only runtime"):
        compare_runs(DEVELOPMENT, [generated[0], candidate], axis="runtime")
    report = compare_runs(DEVELOPMENT, [generated[0], candidate], axis="strategy")
    assert report["arms"][1]["controls"]["code_strategy_sha256"] == "c" * 64
    assert "strategy_sha256" not in report["fixed_controls"]
    assert "code_strategy_sha256" not in report["fixed_controls"]
    assert report["paired_differences"][0]["macro_quality_difference"] == 0


@pytest.mark.parametrize("field", ["model", "provider"])
def test_even_matching_declared_controls_cannot_contradict_saved_metadata(generated, field):
    changed = [
        changed_run(
            path,
            f"both-fake-{field}",
            lambda manifest: manifest["controls"].update({field: "fake-declaration"}),
        )
        for path in generated
    ]
    with pytest.raises(ValueError, match=f"Run {field} control conflicts"):
        compare_runs(DEVELOPMENT, changed, axis="strategy")


def artifact_arm_with_optional_search(generated, tmp_path, *, provider_name=None):
    copied = tmp_path / "comparison-arm"
    shutil.copytree(generated[0].parent, copied)
    case = load_benchmark(DEVELOPMENT).cases["export-notices"]
    fixtures = json.loads((DEVELOPMENT.parent / case.fixtures.path).read_bytes())
    responses = {entry["url"]: entry for entry in fixtures["responses"]}
    saved = json.loads((copied / "cases/export-notices/proposal.json").read_bytes())
    draft = ProposalDraft.model_validate(saved["proposal"])

    def handler(request):
        response = responses[str(request.url)]
        return httpx.Response(response["status"], json=response["body"])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = ResearchService(
            copied / "cases/replacement",
            backend=Backend.local(tmp_path / "replacement-backend"),
            http_client=client,
            public_only=False,
        )
        binding = service.begin(case.brief, model=saved["model"])
        if provider_name is not None:
            provider = FixtureProvider(fixtures["search_results"])
            provider.name = provider_name
            service.search(case.brief, provider=provider, operation_id="search")
        # A known endpoint can be probed and cited without conducting a search.
        for candidate in draft.candidates:
            proof = service.probe(candidate.source, operation_id="probe")
            candidate.probe_id = proof["probe_id"]
        if provider_name is not None:
            service.submit_proposal(draft, operation_id="submit")
        else:
            # Authored saved-artifact fixture: current service submission requires
            # a search. The independent evaluator also supports probe-only exports;
            # real probe evidence remains intact, with no invented registry saves.
            saved.update(
                proposal=draft.model_dump(mode="json"),
                registry={key: binding[key] for key in ("question_id", "discovery_id")},
                web_search_calls=0,
                successful_searches=0,
            )
            write_json(service.output / "proposal.json", saved)
            shutil.copyfile(
                copied / "cases/export-notices/pipeline.json", service.output / "pipeline.json"
            )
    value = json.loads((copied / "run.json").read_bytes())
    next(run for run in value["runs"] if run["case_id"] == case.id)["artifacts"] = (
        "cases/replacement"
    )
    write_json(copied / "run.json", value)
    return copied / "run.json"


def test_completed_no_search_proposal_stays_in_comparison_with_unverified_provider(
    generated, tmp_path
):
    altered = artifact_arm_with_optional_search(generated, tmp_path)
    report = compare_runs(DEVELOPMENT, [altered, generated[1]], axis="strategy")
    arm = report["arms"][0]
    result = next(result for result in arm["results"] if result["case_id"] == "export-notices")
    assert arm["summary"]["cases"] == arm["summary"]["correctness"]["valid"] == 20
    assert result["run"]["status"] == "completed" and result["score"]["status"] == "ok"
    assert result["score"]["reported_search_providers"] == []
    assert result["control_evidence"]["search_provider"] == {
        "configured": FixtureProvider.name,
        "observed": [],
        "status": "unverified",
        "reason": "no_observed_search_provider",
    }
    observed = next(result for result in arm["results"] if result["case_id"] != "export-notices")
    assert observed["control_evidence"]["search_provider"] == {
        "configured": FixtureProvider.name,
        "observed": [FixtureProvider.name],
        "status": "matched",
        "reason": None,
    }
    assert result["control_evidence"]["model"]["status"] == "matched"
    assert result["score"]["total_tokens"] is None


def test_actual_foreign_provider_receipt_still_rejects_comparison(generated, tmp_path):
    altered = artifact_arm_with_optional_search(
        generated, tmp_path, provider_name="foreign-provider"
    )
    with pytest.raises(ValueError, match="Run provider control conflicts"):
        compare_runs(DEVELOPMENT, [altered, generated[1]], axis="strategy")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "revision", "fixtures"])
def test_case_omission_duplicate_or_revision_mismatch_invalidates_comparison(generated, mutation):
    def change(manifest):
        if mutation == "missing":
            manifest["runs"].pop()
        elif mutation == "duplicate":
            manifest["runs"].append(manifest["runs"][0])
        elif mutation == "revision":
            manifest["benchmark_sha256"] = "b" * 64
        else:
            manifest["controls"]["fixture_sha256"] = "c" * 64

    altered = changed_run(generated[1], f"invalid-{mutation}", change)
    with pytest.raises(ValueError):
        compare_runs(DEVELOPMENT, [generated[0], altered], axis="strategy")


def test_failures_stay_in_denominator_and_partial_usage_stays_unknown(generated):
    original = compare_runs(DEVELOPMENT, generated, axis="strategy")

    def fail_first(manifest):
        manifest["runs"][0].update(
            status="failed",
            error="Fixture controller failure",
            failed_run_tokens=15,
            model_cost_usd=0.03,
        )

    altered = changed_run(generated[0], "with-failure", fail_first)
    report = compare_runs(DEVELOPMENT, [altered, generated[1]], axis="strategy")
    summary = report["arms"][0]["summary"]
    assert summary["cases"] == 20 and summary["correctness"]["execution_failed"] == 1
    assert summary["usefulness"]["macro_quality"] == pytest.approx(
        original["arms"][0]["summary"]["usefulness"]["macro_quality"] - 1 / 20
    )
    assert summary["efficiency"]["model_tokens"] == {
        "known_runs": 1,
        "unknown_runs": 19,
        "known_subtotal": 15,
        "total": None,
    }
    assert summary["efficiency"]["model_cost_usd"]["total"] is None
    assert summary["efficiency"]["model_cost_usd"]["known_subtotal"] == 0.03
    evidence = report["arms"][0]["results"][0]["control_evidence"]
    assert evidence["search_provider"]["status"] == "unverified"
    assert evidence["search_provider"]["observed"] is None
    assert evidence["search_provider"]["reason"] == "no_artifact_evidence"
    assert evidence["model"]["status"] == "unverified"


def test_invalid_artifacts_count_separately_and_get_no_aggregate_coverage(generated, tmp_path):
    copied = tmp_path / "copied"
    shutil.copytree(generated[0].parent, copied)
    pipeline_path = copied / "cases/export-notices/pipeline.json"
    pipeline = json.loads(pipeline_path.read_bytes())
    pipeline["sources"][0]["items_pointer"] = "/unprobed"
    write_json(pipeline_path, pipeline)
    report = compare_runs(DEVELOPMENT, [copied / "run.json", generated[1]], axis="strategy")
    summary = report["arms"][0]["summary"]
    assert summary["correctness"]["invalid"] == 1
    assert summary["correctness"]["valid"] == 19
    assert report["arms"][0]["results"][0]["score"]["quality"] == 0


@pytest.mark.parametrize("kind", ["case", "fixture"])
def test_manifest_detects_modified_case_or_http_fixture_bytes(tmp_path, kind):
    root = tmp_path / "package"
    shutil.copytree(DEVELOPMENT.parent, root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    reference = manifest["cases"][0]
    case = json.loads((root / reference["path"]).read_bytes())
    path = root / (reference["path"] if kind == "case" else case["fixtures"]["path"])
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_benchmark(root / "manifest.json")


def heldout_manifest(tmp_path, mutate=None):
    development = load_benchmark(DEVELOPMENT)
    original = next(iter(development.cases.values()))
    case = original.model_dump(mode="json")
    case.update(
        id="private-contract",
        topic_group="private-topic",
        source_families=["private-family"],
        brief="An unrelated private test brief.",
    )
    for alternative in case["specification"]["requirements"][0]["any_of"]:
        alternative["url"] = "https://private-source.fixture.example/data"
    for source in case["sources"]:
        source["url"] = "https://private-source.fixture.example/data"
    fixture = {"synthetic_test_only": "private fixture placeholder"}
    if mutate:
        mutate(case, fixture, original)
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture_path = tmp_path / "fixture.json"
    write_json(fixture_path, fixture)
    if case["fixtures"]["sha256"] == original.fixtures.sha256:
        case["fixtures"] = {"path": "fixture.json", "sha256": digest(fixture_path.read_bytes())}
    case_path = tmp_path / "case.json"
    write_json(case_path, case)
    manifest_path = tmp_path / "manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": 1,
            "id": "private-test-metadata",
            "split": "heldout",
            "description": "Temporary unit-test metadata, not actual held-out tasks",
            "cases": [{"path": "case.json", "sha256": digest(case_path.read_bytes())}],
        },
    )
    return load_benchmark(manifest_path)


def test_private_split_check_returns_counts_without_exposing_task_contents(tmp_path):
    heldout = heldout_manifest(tmp_path)
    assert validate_splits(load_benchmark(DEVELOPMENT), heldout) == {
        "development_cases": 20,
        "heldout_cases": 1,
    }
    with pytest.raises(ValueError, match="only accepts development"):
        run_fixture_arm(heldout, tmp_path / "forbidden-output", policy="authored-selection")
    assert not (tmp_path / "forbidden-output").exists()


@pytest.mark.parametrize(
    "dimension", ["case id", "topic group", "source family", "brief", "source host"]
)
def test_split_check_rejects_leaking_group_dimensions_without_listing_private_values(
    tmp_path, dimension
):
    def change(case, _fixture, original):
        if dimension == "case id":
            case["id"] = original.id
        elif dimension == "topic group":
            case["topic_group"] = original.topic_group
        elif dimension == "source family":
            case["source_families"] = original.source_families
        elif dimension == "brief":
            case["brief"] = "  " + original.brief.upper() + "  "
        else:
            case["specification"] = copy.deepcopy(original.specification)

    heldout = heldout_manifest(tmp_path, change)
    with pytest.raises(ValueError, match=dimension) as error:
        validate_splits(load_benchmark(DEVELOPMENT), heldout)
    assert "private-contract" not in str(error.value)


def test_case_references_cannot_escape_package_even_through_symlinks(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (package / "linked.json").symlink_to(outside)
    write_json(
        package / "manifest.json",
        {
            "schema_version": 1,
            "id": "bad-path",
            "split": "development",
            "description": "Invalid fixture",
            "cases": [{"path": "linked.json", "sha256": digest(outside.read_bytes())}],
        },
    )
    with pytest.raises(ValueError, match="within their package"):
        load_benchmark(package / "manifest.json")


def test_split_check_also_detects_identical_fixture_content(tmp_path):
    heldout = heldout_manifest(tmp_path)
    development = load_benchmark(DEVELOPMENT)
    original = next(iter(development.cases.values()))
    raw = (DEVELOPMENT.parent / original.fixtures.path).read_bytes()
    (tmp_path / "fixture.json").write_bytes(raw)
    case = json.loads((tmp_path / "case.json").read_bytes())
    case["fixtures"]["sha256"] = digest(raw)
    write_json(tmp_path / "case.json", case)
    manifest = json.loads(heldout.path.read_bytes())
    manifest["cases"][0]["sha256"] = digest((tmp_path / "case.json").read_bytes())
    write_json(heldout.path, manifest)
    with pytest.raises(ValueError, match="fixture overlap"):
        validate_splits(development, load_benchmark(heldout.path))


def test_cost_and_budget_types_cannot_turn_boolean_unknowns_into_zero(generated):
    with pytest.raises(ValueError):
        CaseRun(case_id="case", status="failed", error="failure", model_cost_usd=False)
    controls = json.loads(generated[0].read_bytes())["controls"]
    controls["budgets"]["search"] = False
    with pytest.raises(ValueError):
        RunControls.model_validate(controls)


def test_benchmark_cli_checks_and_writes_comparison_report(generated, tmp_path):
    checked = subprocess.run(
        [sys.executable, "-m", "research_harness.evaluation.benchmark", "check", str(DEVELOPMENT)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(checked.stdout)["cases"] == 20
    output = tmp_path / "comparison.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "research_harness.evaluation.benchmark",
            "compare",
            str(DEVELOPMENT),
            *map(str, generated),
            "--axis",
            "strategy",
            "--out",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout) == json.loads(output.read_bytes())


def runtime_export(output, proposal, *, mutate=None):
    response = {
        "id": "discovery-response",
        "status": "completed",
        "model": "research-agent-bundle",
        "usage": {
            "model": proposal["model"],
            "input_tokens": 700,
            "output_tokens": 70,
            "total_tokens": 770,
            "cost_usd": None,
        },
    }
    event = {
        "event": "response.completed",
        "phase": "discovery",
        "data": {"type": "response.completed", "sequence_number": 1, "response": response},
    }
    followup = copy.deepcopy(event)
    followup["phase"] = "followup"
    followup["data"]["response"].update(
        id="followup-response",
        usage={
            "model": proposal["model"],
            "input_tokens": 200,
            "output_tokens": 20,
            "total_tokens": 220,
        },
    )
    events = [event, copy.deepcopy(event), followup]
    usage = {
        "schema_version": 1,
        "discovery_id": proposal["registry"]["discovery_id"],
        "question_id": proposal["registry"]["question_id"],
        "phase": "discovery",
        "source_event_file": "events/immutable.jsonl",
        "source_event_sha256": "0" * 64,
        "responses": {
            "discovery-response": {
                "model": proposal["model"],
                "input_tokens": 700,
                "output_tokens": 70,
                "status": "completed",
            }
        },
        "totals": {"input_tokens": 700, "output_tokens": 70, "total_tokens": 770},
        "complete": False,
        "auxiliary_usage_unknown": True,
        "interrupted": False,
    }
    if mutate:
        mutate(usage, events)
    output.mkdir(parents=True, exist_ok=True)
    event_path = output / "events/immutable.jsonl"
    event_path.parent.mkdir()
    raw = "".join(json.dumps(value) + "\n" for value in events).encode()
    event_path.write_bytes(raw)
    usage["source_event_sha256"] = digest(raw)
    path = output / "runtime-usage.discovery.json"
    write_json(path, usage)
    return path


def test_runtime_discovery_usage_excludes_followups_deduplicates_events_and_stays_a_lower_bound(
    generated, tmp_path
):
    artifacts = generated[0].parent / "cases/export-notices"
    proposal = json.loads((artifacts / "proposal.json").read_bytes())
    usage = runtime_export(tmp_path / "usage", proposal)
    case = load_benchmark(DEVELOPMENT).cases["export-notices"]
    result = score(artifacts, case.specification, runtime_usage=usage)
    assert result["status"] == "ok" and result["quality"] == 1
    assert result["runtime_usage"]["status"] == "verified_lower_bound"
    assert result["runtime_usage"]["response_count"] == 1
    assert result["completed_token_lower_bound"] == 770
    assert result["total_tokens"] is None and frontier([result]) == []
    assert result["artifact_sha256"]["runtime_usage"] == digest(usage.read_bytes())
    assert result["artifact_sha256"]["runtime_usage_events"] == digest(
        (usage.parent / "events/immutable.jsonl").read_bytes()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "discovery",
        "question",
        "phase",
        "totals",
        "model",
        "conflicting-duplicate",
        "path",
        "false-completeness",
        "phase-event",
        "untagged-event",
        "followup-relabelled-as-discovery",
    ],
)
def test_runtime_usage_mismatch_preserves_source_quality_but_cannot_establish_cost(
    generated, tmp_path, mutation
):
    artifacts = generated[0].parent / "cases/export-notices"
    proposal = json.loads((artifacts / "proposal.json").read_bytes())

    def change(usage, events):
        if mutation in {"discovery", "question"}:
            usage[f"{mutation}_id"] = "other"
        elif mutation == "phase":
            usage["phase"] = "workflow"
        elif mutation == "totals":
            usage["totals"]["total_tokens"] = 1
        elif mutation == "model":
            usage["responses"]["discovery-response"]["model"] = "different"
        elif mutation == "conflicting-duplicate":
            events[1]["data"]["response"]["usage"]["input_tokens"] = 701
        elif mutation == "path":
            usage["source_event_file"] = "../../outside.jsonl"
        elif mutation == "false-completeness":
            usage["complete"] = True
        elif mutation == "phase-event":
            events[0]["phase"] = "followup"
        elif mutation == "untagged-event":
            events[0].pop("phase")
        else:
            response = events[2]["data"]["response"]
            usage["responses"] = {
                response["id"]: {
                    "model": proposal["model"],
                    "input_tokens": 200,
                    "output_tokens": 20,
                    "status": "completed",
                }
            }
            usage["totals"] = {"input_tokens": 200, "output_tokens": 20, "total_tokens": 220}

    usage = runtime_export(tmp_path / "usage", proposal, mutate=change)
    result = score(
        artifacts,
        load_benchmark(DEVELOPMENT).cases["export-notices"].specification,
        runtime_usage=usage,
    )
    assert result["quality"] == 1
    assert result["runtime_usage"]["status"] == "invalid"
    assert result["runtime_usage"]["errors"]
    assert result["total_tokens"] is None
    if mutation in {"phase-event", "untagged-event", "followup-relabelled-as-discovery"}:
        assert result["completed_token_lower_bound"] is None


def test_changed_source_snapshot_and_duplicate_json_ids_are_rejected(generated, tmp_path):
    artifacts = generated[0].parent / "cases/export-notices"
    proposal = json.loads((artifacts / "proposal.json").read_bytes())
    specification = load_benchmark(DEVELOPMENT).cases["export-notices"].specification
    usage = runtime_export(tmp_path / "usage", proposal)
    event_path = usage.parent / "events/immutable.jsonl"
    event_path.write_bytes(event_path.read_bytes() + b"\n")
    result = score(artifacts, specification, runtime_usage=usage)
    assert "hash mismatch" in result["runtime_usage"]["errors"][0]
    raw = usage.read_text()
    usage.write_text(
        raw.replace('"phase": "discovery"', '"phase": "discovery", "phase": "discovery"')
    )
    result = score(artifacts, specification, runtime_usage=usage)
    assert "Duplicate key" in result["runtime_usage"]["errors"][0]


def test_explicit_complete_usage_requires_known_auxiliary_usage_and_no_interruption(
    generated, tmp_path
):
    artifacts = generated[0].parent / "cases/export-notices"
    proposal = json.loads((artifacts / "proposal.json").read_bytes())

    def complete(usage, _events):
        usage.update(complete=True, auxiliary_usage_unknown=False)

    usage = runtime_export(tmp_path / "usage", proposal, mutate=complete)
    specification = load_benchmark(DEVELOPMENT).cases["export-notices"].specification
    result = score(artifacts, specification, runtime_usage=usage)
    assert result["runtime_usage"]["status"] == "verified_complete"
    assert result["total_tokens"] == 770
    value = json.loads(usage.read_bytes())
    value.update(complete=False, interrupted=True)
    write_json(usage, value)
    result = score(artifacts, specification, runtime_usage=usage)
    assert result["completed_token_lower_bound"] == 770
    assert result["total_tokens"] is None


def test_benchmark_consumes_runtime_file_reference_without_numeric_override(generated):
    manifest = json.loads(generated[0].read_bytes())
    artifacts = generated[0].parent / manifest["runs"][0]["artifacts"]
    proposal = json.loads((artifacts / "proposal.json").read_bytes())
    usage = runtime_export(generated[0].parent / "runtime-usage-benchmark", proposal)

    def reference(value):
        value["runs"][0]["runtime_usage"] = str(usage.relative_to(generated[0].parent))

    modified = changed_run(generated[0], "with-runtime-usage", reference)
    report = compare_runs(DEVELOPMENT, [modified, generated[1]], axis="strategy")
    efficiency = report["arms"][0]["summary"]["efficiency"]
    assert efficiency["model_tokens"]["total"] is None
    assert efficiency["completed_token_lower_bounds"]["known_subtotal"] == 770
