from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from research_harness.evaluation.workflow_summary import aggregate_workflows

EVIDENCE = Path(__file__).parents[1] / "examples/evaluation/evidence/workflow-2026-09-08.json"


@pytest.fixture
def report():
    # Actual read-only evaluator output from the synthetic normal runtime, not a
    # status-only success claim. Mutations below exercise the report contract.
    return deepcopy(next(iter(json.loads(EVIDENCE.read_bytes()).values()))["report"])


def recount(report):
    report["counts"] = {
        status: sum(check["status"] == status for check in report["checks"])
        for status in ("passed", "failed", "unknown")
    }
    report["status"] = (
        "failed"
        if report["counts"]["failed"]
        else "incomplete"
        if report["counts"]["unknown"]
        else "passed"
    )
    return report


def passing(report):
    report = deepcopy(report)
    for check in report["checks"]:
        check["status"] = "passed"
    report["before_snapshot_sha256"] = "1" * 64
    return recount(report)


def test_mixed_outcomes_retain_every_case_including_failed_executions(report):
    failed = deepcopy(report)
    next(check for check in failed["checks"] if check["check"].endswith("terminal_status"))[
        "status"
    ] = "failed"
    invalid = deepcopy(report)
    invalid["status"] = "passed"
    result = aggregate_workflows(
        ["passed", "failed", "incomplete", "missing", "absent", "invalid"],
        {
            "passed": passing(report),
            "failed": {"case_id": "failed", "report": recount(failed)},
            "incomplete": report,
            "missing": {"case_id": "missing", "report": None, "error": "Model execution failed"},
            "invalid": invalid,
        },
    )
    assert result["cases"] == 6 and result["status"] == "failed"
    assert result["case_counts"] == {
        "passed": 1,
        "failed": 1,
        "incomplete": 1,
        "missing": 2,
        "invalid": 1,
    }
    assert result["valid_reports"] == result["unavailable_reports"] == 3
    assert sum(result["case_counts"].values()) == result["cases"]
    assert result["recorded_check_counts"] == {"passed": 69, "failed": 1, "unknown": 2}
    for dimension in result["dimensions"].values():
        assert sum(dimension["case_counts"].values()) == 6
        assert dimension["case_counts"]["missing"] == 2
        assert dimension["case_counts"]["invalid"] == 1
    assert result["dimensions"]["replay"]["case_counts"]["unknown"] == 2
    assert result["dimensions"]["collection"]["case_counts"]["failed"] == 1
    assert result["unmeasured"] == [
        "model_cost",
        "scientific_usefulness",
        "source_content_entailment",
    ]
    assert "quality" not in result and "total_tokens" not in result


def test_all_missing_is_incomplete_and_does_not_turn_zero_checks_into_success():
    result = aggregate_workflows(["a", "b", "c"], {"b": None})
    assert result["cases"] == result["case_counts"]["missing"] == 3
    assert result["status"] == "incomplete" and result["valid_reports"] == 0
    assert result["recorded_check_counts"] == {"passed": 0, "failed": 0, "unknown": 0}
    assert all(value["case_counts"]["missing"] == 3 for value in result["dimensions"].values())


def test_real_archived_reports_keep_replay_unknown():
    archived = json.loads(EVIDENCE.read_bytes())
    reports = {f"case-{index}": value["report"] for index, value in enumerate(archived.values())}
    result = aggregate_workflows(reports.keys(), reports)
    assert result["status"] == "incomplete"
    assert result["case_counts"] == {
        "passed": 0,
        "failed": 0,
        "incomplete": 2,
        "missing": 0,
        "invalid": 0,
    }
    assert result["recorded_check_counts"] == {"passed": 46, "failed": 0, "unknown": 2}
    assert result["dimensions"]["replay"]["case_counts"]["unknown"] == 2


@pytest.mark.parametrize(
    "expected, reports",
    [
        ([], {}),
        (["a", "a"], {}),
        ("case-id", {}),
        ([""], {}),
        ([1], {}),
        (["a"], []),
        (["a"], {"foreign": None}),
        (["a"], {1: None}),
    ],
)
def test_case_set_contract_rejects_empty_duplicate_or_foreign_cases(expected, reports):
    with pytest.raises(ValueError):
        aggregate_workflows(expected, reports)


@pytest.mark.parametrize("location", ["wrapper", "report", "nested", "repeated"])
def test_foreign_and_reused_case_bindings_are_rejected(report, location):
    values = {"a": report}
    if location == "wrapper":
        values["a"] = {"case_id": "b", "report": report}
    elif location == "report":
        values["a"] = {**report, "case_id": "b"}
    elif location == "nested":
        values["a"] = {"case_id": "a", "report": {**report, "case_id": "b"}}
    else:
        values["a"] = {"case_id": "a", "report": report}
        values["b"] = {"case_id": "a", "report": report}
    with pytest.raises(ValueError, match="binding"):
        aggregate_workflows(["a", "b"], values)


def test_custom_mapping_cannot_deliver_same_case_twice(report):
    class Repeated(dict):
        def items(self):
            return [("a", report), ("a", report)]

    with pytest.raises(ValueError, match="unique"):
        aggregate_workflows(["a"], Repeated())


@pytest.mark.parametrize(
    "mutation",
    [
        "status",
        "count",
        "bool_count",
        "bool_version",
        "future_schema",
        "duplicate_check",
        "empty_checks",
        "nonfinite_detail",
        "wrong_hash",
        "wrong_measurement",
        "unknown_status",
        "missing_required_check",
        "empty_unmeasured",
        "status_only",
    ],
)
def test_malformed_reports_stay_in_denominator_as_invalid(report, mutation):
    if mutation == "status":
        report["status"] = "passed"
    elif mutation == "count":
        report["counts"]["passed"] += 1
    elif mutation == "bool_count":
        report["counts"]["unknown"] = True
    elif mutation == "bool_version":
        report["schema_version"] = True
    elif mutation == "future_schema":
        report["schema_version"] = 2
    elif mutation == "duplicate_check":
        report["checks"].append(deepcopy(report["checks"][0]))
        recount(report)
    elif mutation == "empty_checks":
        report["checks"] = []
        recount(report)
    elif mutation == "nonfinite_detail":
        report["checks"][0]["detail"] = float("nan")
    elif mutation == "wrong_hash":
        report["snapshot_sha256"] = "not-a-hash"
    elif mutation == "wrong_measurement":
        report["measurement"] = "discovery_quality"
    elif mutation == "unknown_status":
        report["checks"][0]["status"] = "skipped"
    elif mutation == "missing_required_check":
        report["checks"] = [c for c in report["checks"] if c["check"] != "publication.lineage"]
        recount(report)
    elif mutation == "empty_unmeasured":
        report["unmeasured"] = [""]
    else:
        report = {"status": "passed"}
    result = aggregate_workflows(["case"], {"case": report})
    assert result["status"] == "incomplete"
    assert result["cases"] == result["case_counts"]["invalid"] == 1
    assert result["case_counts"]["failed"] == 0
    assert result["recorded_check_counts"] == {"passed": 0, "failed": 0, "unknown": 0}


def test_absent_dimensions_are_not_measured_even_when_recorded_checks_pass(report):
    report = passing(report)
    report["checks"] = [
        check for check in report["checks"] if not check["check"].startswith("export.")
    ]
    result = aggregate_workflows(["case"], {"case": recount(report)})
    assert result["case_counts"]["passed"] == 1
    assert result["dimensions"]["export"]["case_counts"]["not_measured"] == 1
    assert result["dimensions"]["export"]["case_counts"]["passed"] == 0
    assert result["scope"] == "recorded_checks_from_trusted_workflow_evaluations"


def test_summary_is_stable_preserves_expected_order_and_does_not_mutate_inputs(report):
    original = deepcopy(report)
    first = aggregate_workflows(["a", "b"], {"b": report, "a": {**report, "case_id": "a"}})
    second = aggregate_workflows(["a", "b"], {"a": report, "b": {"case_id": "b", "report": report}})
    assert first == second
    assert [value["case_id"] for value in first["results"]] == ["a", "b"]
    assert all(len(value["report_sha256"]) == 64 for value in first["results"])
    assert report == original
