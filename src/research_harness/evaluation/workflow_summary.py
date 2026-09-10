"""Aggregate trusted workflow reports without dropping missing or invalid cases."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_harness.util import canonical_json, digest

CheckStatus = Literal["passed", "failed", "unknown"]
ReportStatus = Literal["passed", "failed", "incomplete"]
CASE_STATUSES = ("passed", "failed", "incomplete", "missing", "invalid")
DIMENSIONS = ("snapshot", "pipeline", "publication", "capture", "collection", "export", "replay")
SHA256 = r"^[0-9a-f]{64}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Check(_Strict):
    check: str = Field(min_length=1)
    status: CheckStatus
    detail: Any = None


class _Counts(_Strict):
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    unknown: int = Field(ge=0)


class _Report(_Strict):
    schema_version: int = Field(ge=1, le=1)
    measurement: Literal["workflow_correctness"]
    status: ReportStatus
    question_id: str = Field(min_length=1)
    pipeline_version_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=SHA256)
    before_snapshot_sha256: str | None = Field(pattern=SHA256)
    expectation_sha256: str = Field(pattern=SHA256)
    counts: _Counts
    checks: list[_Check] = Field(min_length=1)
    unmeasured: list[str]

    @model_validator(mode="after")
    def consistent(self):
        names = [check.check for check in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("Workflow check names must be unique")
        baseline = {
            "snapshot.stable_registry",
            "pipeline.original_configuration",
            "publication.lineage",
            "capture.integrity",
        }
        if not baseline.issubset(names) or not any(
            name.startswith(("collection.", "export.")) for name in names
        ):
            raise ValueError("Workflow report lacks required evaluator checks")
        actual = {
            status: sum(check.status == status for check in self.checks)
            for status in ("passed", "failed", "unknown")
        }
        if actual != self.counts.model_dump():
            raise ValueError("Workflow counts do not match individual checks")
        derived = "failed" if actual["failed"] else "incomplete" if actual["unknown"] else "passed"
        if self.status != derived:
            raise ValueError("Workflow status contradicts individual checks")
        if len(self.unmeasured) != len(set(self.unmeasured)) or any(
            not item.strip() for item in self.unmeasured
        ):
            raise ValueError("Unmeasured dimensions must be unique nonempty strings")
        return self


def _check_counts(checks: list[_Check]) -> dict[str, int]:
    return {
        status: sum(check.status == status for check in checks)
        for status in ("passed", "failed", "unknown")
    }


def _unwrap(case_id: str, value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    if "case_id" in value and value["case_id"] != case_id:
        raise ValueError("Workflow report case binding conflicts with its mapping key")
    if "report" in value:
        value = value["report"]
    if isinstance(value, Mapping):
        if "case_id" in value and value["case_id"] != case_id:
            raise ValueError("Workflow report case binding conflicts with its mapping key")
        value = {key: item for key, item in value.items() if key != "case_id"}
    return value


def aggregate_workflows(
    expected_case_ids: Iterable[str], reports_by_case: Mapping[str, Any]
) -> dict[str, Any]:
    """Summarize every expected case, including failed executions with no report.

    Values are evaluate_workflow reports, None, or wrappers containing ``report``
    and an optional matching ``case_id``. Wrapper metadata is not evidence and is
    not scored. Unknown/duplicate/foreign case bindings reject the aggregation;
    malformed report contents become invalid cases in the full denominator.

    This verifies report structure and internal consistency, not provenance or
    whether the controller actually executed every requested workflow step.
    """
    if isinstance(expected_case_ids, (str, bytes)):
        raise ValueError("Expected a full iterable of unique case ids")
    expected = list(expected_case_ids)
    if not expected or any(type(case_id) is not str or not case_id.strip() for case_id in expected):
        raise ValueError("Expected a nonempty case set with nonempty string ids")
    if len(expected) != len(set(expected)):
        raise ValueError("Expected workflow case ids must be unique")
    if not isinstance(reports_by_case, Mapping):
        raise ValueError("Workflow reports must be a mapping keyed by case id")
    supplied = list(reports_by_case.items())
    keys = [key for key, _ in supplied]
    if any(type(key) is not str for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("Workflow report mapping keys must be unique string case ids")
    if not set(keys).issubset(expected):
        raise ValueError("Workflow reports contain cases outside the expected case set")
    reports = {case_id: _unwrap(case_id, value) for case_id, value in supplied}
    results = []
    parsed: dict[str, _Report] = {}
    for case_id in expected:
        value = reports.get(case_id)
        if value is None:
            results.append({"case_id": case_id, "status": "missing", "report_sha256": None})
            continue
        try:
            report_hash = digest(canonical_json(value))
            report = _Report.model_validate(value)
        except (TypeError, ValueError):
            results.append(
                {
                    "case_id": case_id,
                    "status": "invalid",
                    "report_sha256": None,
                    "error": "Malformed or internally inconsistent workflow report",
                }
            )
            continue
        parsed[case_id] = report
        results.append(
            {
                "case_id": case_id,
                "status": report.status,
                "report_sha256": report_hash,
                "snapshot_sha256": report.snapshot_sha256,
                "expectation_sha256": report.expectation_sha256,
                "counts": report.counts.model_dump(),
                "unmeasured": report.unmeasured,
            }
        )
    counts = {status: sum(row["status"] == status for row in results) for status in CASE_STATUSES}
    dimensions = set(DIMENSIONS)
    dimensions.update(
        check.check.split(".", 1)[0] for report in parsed.values() for check in report.checks
    )
    dimension_results = {}
    for dimension in sorted(dimensions):
        dimension_counts = {
            key: 0 for key in ("passed", "failed", "unknown", "not_measured", "missing", "invalid")
        }
        known_checks = []
        for result in results:
            case_id = result["case_id"]
            if case_id not in parsed:
                dimension_counts[result["status"]] += 1
                continue
            checks = [
                check
                for check in parsed[case_id].checks
                if check.check.split(".", 1)[0] == dimension
            ]
            known_checks.extend(checks)
            outcomes = {check.status for check in checks}
            status = (
                "failed"
                if "failed" in outcomes
                else "unknown"
                if "unknown" in outcomes
                else "passed"
                if checks
                else "not_measured"
            )
            dimension_counts[status] += 1
        dimension_results[dimension] = {
            "case_counts": dimension_counts,
            "recorded_check_counts": _check_counts(known_checks),
        }
    return {
        "schema_version": 1,
        "measurement": "workflow_correctness_summary",
        "scope": "recorded_checks_from_trusted_workflow_evaluations",
        "status": "failed"
        if counts["failed"]
        else "passed"
        if counts["passed"] == len(expected)
        else "incomplete",
        "cases": len(expected),
        "case_counts": counts,
        "valid_reports": len(parsed),
        "unavailable_reports": counts["missing"] + counts["invalid"],
        "recorded_check_counts": _check_counts(
            [check for report in parsed.values() for check in report.checks]
        ),
        "dimensions": dimension_results,
        "unmeasured": sorted({item for report in parsed.values() for item in report.unmeasured}),
        "results": results,
        "limitations": [
            "Every expected case remains in the denominator, including executions with no workflow report.",
            "A passed case means its recorded checks passed; absent workflow dimensions are not measured.",
            "Missing, invalid, and unknown evidence cannot establish workflow correctness.",
            "Report validation does not authenticate execution, case ownership, or artifact provenance.",
            "No research usefulness, model quality, or cost score is inferred.",
        ],
    }
