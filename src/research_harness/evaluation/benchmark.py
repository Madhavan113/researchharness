"""Validate benchmark inputs and compare controlled research discovery experiments."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, StrictInt, model_serializer, model_validator

from research_harness.config import SourceSpec, StrictModel
from research_harness.evaluation.discovery import score, validate_requirements
from research_harness.evaluation.gateway_usage import verify_gateway_usage
from research_harness.execution import GatewayBinding
from research_harness.util import canonical_json, digest, write_json

SHA256 = r"^[0-9a-f]{64}$"


class FileRef(StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256)


class UnsupportedChoice(StrictModel):
    name: str
    url: str
    status: Literal["needs_connector", "needs_access", "unavailable"]
    limitation: str


class FixturePlan(StrictModel):
    source_ids: list[str]
    unsupported: list[UnsupportedChoice] = Field(default_factory=list)


class BenchmarkCase(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    title: str = Field(min_length=1)
    brief: str = Field(min_length=1)
    topic_group: str = Field(min_length=1)
    source_families: list[str] = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    review_status: Literal["authored", "reviewed"] = "authored"
    review_notes: str = Field(min_length=1)
    specification: dict[str, Any]
    fixtures: FileRef
    sources: list[SourceSpec]
    fixture_plan: FixturePlan
    manual_checks: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent(self):
        validate_requirements(self.specification.get("requirements"))
        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Case source ids must be unique")
        if not set(self.fixture_plan.source_ids) <= set(source_ids):
            raise ValueError("Fixture plan references an unknown source")
        if len(self.fixture_plan.source_ids) != len(set(self.fixture_plan.source_ids)):
            raise ValueError("Fixture plan source ids must be unique")
        if not self.fixture_plan.source_ids and not self.fixture_plan.unsupported:
            raise ValueError("Fixture plan needs a source choice or an explicit unsupported source")
        if len(self.source_families) != len(set(self.source_families)):
            raise ValueError("Source families must be unique within a case")
        return self

    def task(self) -> dict[str, str]:
        """Return only candidate-facing task input, without evaluation predicates."""
        return {"id": self.id, "brief": self.brief}


class BenchmarkManifest(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1)
    split: Literal["development", "heldout"]
    description: str = Field(min_length=1)
    cases: list[FileRef] = Field(min_length=1)


@dataclass(frozen=True)
class LoadedBenchmark:
    path: Path
    manifest: BenchmarkManifest
    cases: dict[str, BenchmarkCase]
    sha256: str
    fixture_sha256: str


def local_path(root: Path, relative: str) -> Path:
    """Manifest file references cannot escape their own artifact package."""
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("Manifest paths must stay within their package directory")
    return path


def _checked_file(root: Path, reference: FileRef) -> bytes:
    raw = local_path(root, reference.path).read_bytes()
    if digest(raw) != reference.sha256:
        raise ValueError(f"Manifest file hash mismatch: {reference.path}")
    return raw


def load_benchmark(path: Path) -> LoadedBenchmark:
    path = Path(path).resolve()
    manifest = BenchmarkManifest.model_validate_json(path.read_bytes())
    cases: dict[str, BenchmarkCase] = {}
    fixture_hashes = {}
    for reference in manifest.cases:
        case = BenchmarkCase.model_validate_json(_checked_file(path.parent, reference))
        if case.id in cases:
            raise ValueError("Benchmark case ids must be unique")
        _checked_file(path.parent, case.fixtures)
        fixture_hashes[case.id] = case.fixtures.sha256
        cases[case.id] = case
    return LoadedBenchmark(
        path=path,
        manifest=manifest,
        cases=cases,
        sha256=digest(canonical_json(manifest.model_dump(mode="json"))),
        fixture_sha256=digest(canonical_json(fixture_hashes)),
    )


def _source_hosts(benchmark: LoadedBenchmark) -> set[str | None]:
    urls = set()
    for case in benchmark.cases.values():
        urls.update(source.url for source in case.sources)
        urls.update(choice.url for choice in case.fixture_plan.unsupported)
        urls.update(
            alternative["url"]
            for requirement in case.specification["requirements"]
            for alternative in requirement["any_of"]
        )
    return {urlsplit(url).hostname for url in urls}


def validate_splits(development: LoadedBenchmark, heldout: LoadedBenchmark) -> dict[str, int]:
    """Check declared grouping without returning private held-out case contents."""
    if development.manifest.split != "development" or heldout.manifest.split != "heldout":
        raise ValueError("Expected distinct development and heldout manifests")
    dimensions = {
        "case id": lambda benchmark: set(benchmark.cases),
        "topic group": lambda benchmark: {case.topic_group for case in benchmark.cases.values()},
        "source family": lambda benchmark: {
            family for case in benchmark.cases.values() for family in case.source_families
        },
        "brief": lambda benchmark: {
            digest(" ".join(case.brief.lower().split())) for case in benchmark.cases.values()
        },
        "fixture": lambda benchmark: {case.fixtures.sha256 for case in benchmark.cases.values()},
        "source host": _source_hosts,
    }
    for dimension, values in dimensions.items():
        if values(development) & values(heldout):
            raise ValueError(f"Development/heldout {dimension} overlap; regroup before evaluation")
    return {"development_cases": len(development.cases), "heldout_cases": len(heldout.cases)}


class RunControls(StrictModel):
    execution: Literal["fixture", "model"]
    runtime: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_settings: dict[str, Any]
    budget_control: dict[str, Any] | None = None
    provider: str = Field(min_length=1)
    provider_settings: dict[str, Any]
    budgets: dict[str, StrictInt]
    fixture_sha256: str = Field(pattern=SHA256)
    strategy_sha256: str = Field(pattern=SHA256)
    code_strategy_sha256: str | None = Field(default=None, pattern=SHA256)
    backend_sha256: str = Field(pattern=SHA256)

    @model_serializer(mode="wrap")
    def serialize_controls(self, handler):
        result = handler(self)
        # Legacy task identities and manifests did not include a code strategy.
        if self.code_strategy_sha256 is None:
            result.pop("code_strategy_sha256", None)
        return result

    @model_validator(mode="after")
    def positive_budgets(self):
        if not {"search", "inspection", "probe", "deadline_seconds"} <= set(self.budgets):
            raise ValueError("Record search, inspection, probe, and deadline_seconds budgets")
        if (
            any(value < 0 for value in self.budgets.values())
            or self.budgets["deadline_seconds"] == 0
        ):
            raise ValueError("Budgets must be nonnegative with a positive deadline")
        return self


class CaseRun(StrictModel):
    case_id: str
    status: Literal["completed", "failed"]
    artifacts: str | None = None
    runtime_usage: str | None = None
    gateway_usage: str | None = None
    execution_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    error: str | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    model_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    provider_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    failed_run_tokens: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def coherent(self):
        if self.gateway_usage is not None and self.execution_id is None:
            raise ValueError("Gateway usage requires the host execution id")
        if self.gateway_usage is not None and self.failed_run_tokens is not None:
            raise ValueError("Gateway usage replaces manually reported failure token totals")
        if self.status == "completed" and not self.artifacts:
            raise ValueError("Completed runs need an artifact directory")
        if self.status == "failed" and not self.error:
            raise ValueError("Failed runs need a recorded failure reason")
        if self.status == "completed" and self.failed_run_tokens is not None:
            raise ValueError("Completed run tokens come from authoritative proposal artifacts")
        return self


def gateway_binding_for_case(
    case: BenchmarkCase, controls: RunControls, execution_id: str
) -> GatewayBinding:
    """Bind model traffic to the public task and frozen controls, without scorer predicates."""
    frozen_controls = controls.model_dump(mode="json")
    if controls.budget_control is None:
        # Preserve bindings written before dispatch-budget controls existed.
        frozen_controls.pop("budget_control")
    return GatewayBinding(
        execution_id=execution_id,
        case_id=case.id,
        runtime=controls.runtime,
        phase="discovery",
        task_sha256=digest(
            canonical_json(
                {
                    "task": case.task(),
                    "fixture_sha256": case.fixtures.sha256,
                    "controls": frozen_controls,
                }
            )
        ),
    )


class RunManifest(StrictModel):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1)
    benchmark_sha256: str = Field(pattern=SHA256)
    controls: RunControls
    runs: list[CaseRun] = Field(min_length=1)


def _totals(values: list[int | float | None]) -> dict[str, Any]:
    known = [value for value in values if value is not None]
    return {
        "known_runs": len(known),
        "unknown_runs": len(values) - len(known),
        "known_subtotal": sum(known),
        "total": sum(known) if len(known) == len(values) else None,
    }


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(results)
    return {
        "cases": count,
        "correctness": {
            "scope": "artifact_consistency_and_sampled_source_evidence",
            "valid": sum(result["score"]["status"] == "ok" for result in results),
            "invalid": sum(result["score"]["status"] == "invalid" for result in results),
            "execution_failed": sum(
                result["score"]["status"] == "execution_failed" for result in results
            ),
            "artifact_error": sum(
                result["score"]["status"] == "artifact_error" for result in results
            ),
            "unmeasured": [
                "complete_collection",
                "publication_cutoffs",
                "recovery",
                "citation_entailment",
            ],
        },
        "usefulness": {
            "scope": "independent_source_and_gap_predicates",
            "macro_quality": sum(result["score"]["quality"] for result in results) / count,
            "macro_requirement_recall": sum(
                result["score"]["requirement_recall"]
                for result in results
                if result["score"]["status"] == "ok"
            )
            / count,
            "macro_source_precision": sum(
                result["score"]["source_precision"]
                for result in results
                if result["score"]["status"] == "ok"
            )
            / count,
            **{
                f"macro_{metric}": sum(
                    result["score"][metric]
                    for result in results
                    if result["score"]["status"] == "ok"
                )
                / count
                for metric in ("research_recall", "research_precision", "gap_recall")
            },
            "qualitative_review": "not_measured",
        },
        "efficiency": {
            "model_tokens": _totals([result["score"]["total_tokens"] for result in results]),
            "completed_token_lower_bounds": _totals(
                [result["score"].get("completed_token_lower_bound") for result in results]
            ),
            "elapsed_seconds": _totals([result["run"]["elapsed_seconds"] for result in results]),
            "model_cost_usd": _totals([result["run"]["model_cost_usd"] for result in results]),
            "provider_cost_usd": _totals(
                [result["run"]["provider_cost_usd"] for result in results]
            ),
        },
    }


def compare_runs(
    benchmark_path: Path,
    run_paths: list[Path],
    *,
    axis: Literal["runtime", "strategy"] = "runtime",
) -> dict[str, Any]:
    """Compare complete case sets under matched controls; failures receive zero quality."""
    if axis not in {"runtime", "strategy"}:
        raise ValueError("Comparison axis must be runtime or strategy")
    if len(run_paths) < 2:
        raise ValueError("A controlled comparison needs at least two run manifests")
    benchmark = load_benchmark(benchmark_path)
    manifests = []
    for path in run_paths:
        raw = Path(path).read_bytes()
        manifests.append((Path(path).resolve(), RunManifest.model_validate_json(raw), digest(raw)))
    names = [manifest.name for _, manifest, _ in manifests]
    if len(names) != len(set(names)):
        raise ValueError("Comparison arm names must be unique")
    varied_fields = (
        {"runtime"} if axis == "runtime" else {"strategy_sha256", "code_strategy_sha256"}
    )
    fixed = manifests[0][1].controls.model_dump(exclude=varied_fields)
    for _, manifest, _ in manifests:
        if manifest.benchmark_sha256 != benchmark.sha256:
            raise ValueError("Run manifest was produced for a different benchmark revision")
        if manifest.controls.fixture_sha256 != benchmark.fixture_sha256:
            raise ValueError("Run fixture hash does not match the benchmark package")
        if canonical_json(manifest.controls.model_dump(exclude=varied_fields)) != canonical_json(
            fixed
        ):
            varied = "runtime" if axis == "runtime" else "instruction and code strategy hashes"
            raise ValueError(f"Uncontrolled comparison: only {varied} may differ")
        ids = [run.case_id for run in manifest.runs]
        if len(ids) != len(set(ids)) or set(ids) != set(benchmark.cases):
            raise ValueError(
                "Every arm must include every benchmark case exactly once, including failures"
            )
    arms = []
    for path, manifest, manifest_hash in manifests:
        results = []
        for run in manifest.runs:
            case = benchmark.cases[run.case_id]
            gateway_path = local_path(path.parent, run.gateway_usage) if run.gateway_usage else None
            binding = (
                gateway_binding_for_case(case, manifest.controls, run.execution_id)
                if gateway_path
                else None
            )
            failed = {
                "quality": 0.0,
                "requirement_recall": 0.0,
                "source_precision": 0.0,
                "total_tokens": run.failed_run_tokens,
            }
            if run.status == "failed":
                result = {**failed, "status": "execution_failed", "errors": [run.error]}
            else:
                try:
                    artifact_path = local_path(path.parent, run.artifacts or "")
                    usage_path = (
                        local_path(path.parent, run.runtime_usage)
                        if run.runtime_usage is not None
                        else None
                    )
                    result = score(
                        artifact_path,
                        case.specification,
                        runtime_usage=usage_path,
                        gateway_usage=gateway_path,
                        gateway_binding=binding,
                        expected_model_settings=manifest.controls.model_settings,
                        expected_budgets=manifest.controls.budgets,
                        expected_budget_control=manifest.controls.budget_control,
                        expected_strategy_sha256=manifest.controls.code_strategy_sha256,
                    )
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    result = {
                        **failed,
                        "status": "artifact_error",
                        "errors": [f"{type(exc).__name__}: {exc}"],
                    }
                else:
                    if result["reported_model"] != manifest.controls.model:
                        raise ValueError("Run model control conflicts with saved proposal metadata")
                    observed_providers = set(result["reported_search_providers"])
                    if observed_providers and observed_providers != {manifest.controls.provider}:
                        raise ValueError(
                            "Run provider control conflicts with saved search receipts"
                        )
            if gateway_path is not None and "gateway_usage" not in result:
                # Failed or unreadable proposals can still have independently
                # observed provider usage. Never drop those calls from cost data.
                evidence = verify_gateway_usage(
                    gateway_path,
                    binding,
                    expected_model=manifest.controls.model,
                    expected_model_settings=manifest.controls.model_settings,
                    expected_budgets=manifest.controls.budgets,
                    expected_budget_control=manifest.controls.budget_control,
                    **(
                        {"expected_strategy_sha256": manifest.controls.code_strategy_sha256}
                        if manifest.controls.code_strategy_sha256 is not None
                        else {}
                    ),
                )
                result.update(
                    gateway_usage=evidence,
                    total_tokens=evidence["total_tokens"],
                    completed_token_lower_bound=evidence["completed_token_lower_bound"],
                    token_evidence_source="gateway",
                )
            observed_providers = result.get("reported_search_providers")
            observed_model = result.get("reported_model")
            results.append(
                {
                    "case_id": case.id,
                    "run": run.model_dump(mode="json"),
                    "score": result,
                    "control_evidence": {
                        "scope": "observed_artifact_metadata",
                        "model": {
                            "configured": manifest.controls.model,
                            "observed": observed_model,
                            "status": "matched" if observed_model is not None else "unverified",
                        },
                        "search_provider": {
                            "configured": manifest.controls.provider,
                            "observed": observed_providers,
                            "status": "matched" if observed_providers else "unverified",
                            "reason": None
                            if observed_providers
                            else "no_observed_search_provider"
                            if observed_providers == []
                            else "no_artifact_evidence",
                        },
                    },
                }
            )
        arms.append(
            {
                "name": manifest.name,
                "manifest_sha256": manifest_hash,
                "controls": manifest.controls.model_dump(mode="json"),
                "summary": _aggregate(results),
                "results": results,
            }
        )
    reference = arms[0]
    return {
        "schema_version": 1,
        "benchmark_id": benchmark.manifest.id,
        "benchmark_sha256": benchmark.sha256,
        "split": benchmark.manifest.split,
        "review_status_counts": {
            status: sum(case.review_status == status for case in benchmark.cases.values())
            for status in ("authored", "reviewed")
        },
        "comparison_axis": axis,
        "fixed_controls": fixed,
        "arms": arms,
        "paired_differences": [
            {
                "reference": reference["name"],
                "candidate": arm["name"],
                "macro_quality_difference": arm["summary"]["usefulness"]["macro_quality"]
                - reference["summary"]["usefulness"]["macro_quality"],
            }
            for arm in arms[1:]
        ],
        "limitations": [
            "Fixture execution measures software behavior, not model performance.",
            "Controls are recorded by the trusted runner; matching metadata does not authenticate execution.",
            "No observed search provider leaves provider use unverified; the configured provider is not evidence that a search occurred.",
            "Source predicates require qualitative review and cannot prove complete collection or recovery.",
            "No confidence interval or broad scientific conclusion is inferred from this case set.",
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    check = subcommands.add_parser("check", help="Validate hashes and optional split separation")
    check.add_argument("benchmark", type=Path)
    check.add_argument(
        "--heldout", type=Path, help="Trusted controller's private held-out manifest"
    )
    compare = subcommands.add_parser("compare", help="Score saved artifacts under matched controls")
    compare.add_argument("benchmark", type=Path)
    compare.add_argument("runs", type=Path, nargs="+")
    compare.add_argument("--axis", choices=("runtime", "strategy"), default="runtime")
    compare.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            benchmark = load_benchmark(args.benchmark)
            result = {
                "benchmark_id": benchmark.manifest.id,
                "cases": len(benchmark.cases),
                "sha256": benchmark.sha256,
                "fixture_sha256": benchmark.fixture_sha256,
            }
            if args.heldout:
                result["split_check"] = validate_splits(benchmark, load_benchmark(args.heldout))
        else:
            result = compare_runs(args.benchmark, args.runs, axis=args.axis)
            if args.out:
                write_json(args.out, result)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"Benchmark failed: {exc}\n")


if __name__ == "__main__":
    main()
