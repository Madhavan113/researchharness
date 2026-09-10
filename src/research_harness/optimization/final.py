"""Host-only held-out evaluation after development selection and proposer shutdown.

The caller must revoke proposer/workspace access and stop all its processes before
invocation. Archive phase guards alone are not process or filesystem isolation.
Only the supplied trusted case executor may run research; candidate code remains
inside its normal strategy sandbox. Recovery requires stopped case executors and
never invokes them, reopens search, or publishes private results to feedback.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from research_harness.evaluation.benchmark import (
    CaseRun,
    RunControls,
    gateway_binding_for_case,
    load_benchmark,
    local_path,
    validate_splits,
)
from research_harness.evaluation.controller import (
    ARMS,
    CaseTask,
    ComparisonConfig,
    ExecutionArtifacts,
    _journal,
    _load,
    prepare_comparison,
    run_case,
)
from research_harness.evaluation.discovery import score
from research_harness.evaluation.gateway_usage import verify_gateway_usage
from research_harness.execution import DiscoverySettings
from research_harness.optimization.archive import (
    OptimizationArchive,
    _absolute,
    _copy,
    _disjoint,
    _inventory,
    _read,
    _relative,
    _save,
    _write_file,
)
from research_harness.optimization.evaluator import _case_files, _model_evidence
from research_harness.strategies.config import StrategyBundle
from research_harness.strategies.session import verify_session
from research_harness.util import canonical_json, digest


def _request(heldout: Path, output: Path, config: ComparisonConfig, arm: str, op: str) -> dict:
    return {
        "schema_version": 1,
        "operation_id": op,
        "heldout_manifest": str(Path(heldout).absolute()),
        "output": str(output),
        "comparison_config": config.model_dump(mode="json"),
        "arm": arm,
    }


def _terminal(archive: OptimizationArchive, operation_id: str) -> dict:
    final = archive.status()["final"]
    archive.finish_final(operation_id=operation_id, status=final["status"])
    root = Path(final["private_dir"])
    name = "interruption-report.json" if final["status"] == "interrupted" else "report.json"
    return (
        _read(root / name)
        if (root / name).is_file()
        else {
            "schema_version": 1,
            "split": "heldout",
            "status": final["status"],
            "operation_id": operation_id,
            "report_available": False,
        }
    )


def _strategy(bundle_dir: Path, fixed: dict) -> StrategyBundle | None:
    manifest = _read(bundle_dir / "manifest.json")
    if set(manifest) != {"schema_version", "strategy_manifest"} or manifest["schema_version"] != 1:
        raise ValueError("Expected the registered research candidate manifest")
    if manifest["strategy_manifest"] is None:
        return None
    strategy = StrategyBundle.load(bundle_dir / "source" / _relative(manifest["strategy_manifest"]))
    if strategy.config.sandbox.model_dump(mode="json") != fixed.get(
        "sandbox"
    ) or strategy.config.model_dump(
        mode="json", exclude={"source", "source_sha256", "sandbox"}
    ) != fixed.get("strategy_limits"):
        raise ValueError("Final candidate changed frozen sandbox or strategy limits")
    return strategy


def _check_controls(actual: RunControls, fixed: dict) -> None:
    allowed = {"fixture_sha256", "strategy_sha256", "code_strategy_sha256"}
    expected = {k: v for k, v in fixed.items() if k not in allowed | {"sandbox", "strategy_limits"}}
    if actual.model_dump(mode="json", exclude=allowed) != expected:
        raise ValueError("Final execution changed frozen development controls")


def _freeze_heldout(path: Path, destination: Path, archive: OptimizationArchive, config):
    heldout = load_benchmark(path)
    plan, _, _ = archive._load()
    development = load_benchmark(archive.root / _relative(plan["benchmark_path"]))
    validate_splits(development, heldout)
    if config.execution == "model" and any(
        case.review_status != "reviewed" for case in heldout.cases.values()
    ):
        raise ValueError("Measured final evaluation requires independently reviewed held-out cases")
    files = {path.name: digest(path.read_bytes())}
    for ref, case in zip(heldout.manifest.cases, heldout.cases.values(), strict=True):
        for relative, expected in (
            (ref.path, ref.sha256),
            (case.fixtures.path, case.fixtures.sha256),
        ):
            files[_relative(relative)] = expected
    destination.mkdir(parents=True, exist_ok=False)
    for relative, expected in files.items():
        source = _absolute(path.parent / relative)
        if not source.is_relative_to(path.parent) or source.stat().st_nlink != 1:
            raise ValueError("Held-out files must be regular unlinked package files")
        raw = source.read_bytes()
        if digest(raw) != expected:
            raise ValueError("Held-out inputs changed during freezing")
        _write_file(destination / relative, raw)
    if _inventory(destination, config) != dict(sorted(files.items())):
        raise ValueError("Private held-out copy is incomplete")
    frozen = load_benchmark(destination / path.name)
    if frozen.sha256 != heldout.sha256:
        raise ValueError("Held-out manifest changed during freezing")
    return frozen


def _score_case(root: Path, case, controls: RunControls, entry: dict, strategy) -> dict:
    outcome = {
        "case_id": case.id,
        "status": "unmeasured",
        "quality": None,
        "total_tokens": None,
        "completed_token_lower_bound": None,
        "execution_verified": False,
        "errors": [],
    }
    if entry["status"] not in {"completed", "failed"}:
        outcome["errors"] = ["Final attempt interrupted before a terminal case result was recorded"]
        # A stopped controller can leave a sealed gateway even when no terminal
        # task result was committed. Its exact host binding permits usage
        # verification, without establishing completed research or quality.
        if entry.get("execution_id"):
            gateway = (
                local_path(root, entry["gateway_usage"])
                if entry.get("gateway_usage")
                else root / "gateway/archive.json"
            )
            if gateway.is_file():
                usage = verify_gateway_usage(
                    gateway,
                    gateway_binding_for_case(case, controls, entry["execution_id"]),
                    expected_model=controls.model,
                    expected_model_settings=controls.model_settings,
                    expected_budgets=controls.budgets,
                    expected_budget_control=controls.budget_control,
                    expected_strategy_sha256=controls.code_strategy_sha256,
                )
                outcome.update(
                    gateway_usage=usage,
                    total_tokens=usage["total_tokens"],
                    completed_token_lower_bound=usage["completed_token_lower_bound"],
                )
        return outcome
    run = CaseRun.model_validate(entry["run"])
    if (
        run.case_id != case.id
        or run.status != entry["status"]
        or run.execution_id != entry.get("execution_id")
    ):
        raise ValueError("Private case result differs from its recorded execution identity")
    files = _case_files(root, entry, run)
    outcome["evidence_files"] = sorted(files)
    binding = (
        gateway_binding_for_case(case, controls, run.execution_id) if run.execution_id else None
    )
    usage = None
    if run.gateway_usage and binding:
        # Run references are relative to the arm, whose cases directory contains root.
        usage = verify_gateway_usage(
            local_path(root.parents[1], run.gateway_usage),
            binding,
            expected_model=controls.model,
            expected_model_settings=controls.model_settings,
            expected_budgets=controls.budgets,
            expected_budget_control=controls.budget_control,
            expected_strategy_sha256=controls.code_strategy_sha256,
        )
        outcome["gateway_usage"] = usage
    verified = _model_evidence(controls, usage)
    execution = _read(root / "execution.json") if (root / "execution.json").is_file() else {}
    if execution:
        settings = DiscoverySettings.model_validate(execution.get("execution_settings"))
        verified = verified and (
            execution.get("arm") == root.parents[1].name
            and execution.get("model") == controls.model
            and execution.get("instructions_sha256") == controls.strategy_sha256
            and execution.get("code_strategy_sha256") == controls.code_strategy_sha256
            and execution.get("gateway_binding")
            == (binding.model_dump(mode="json") if binding else None)
            and execution.get("status") == run.status
            and settings.model_settings() == controls.model_settings
            and settings.budgets() == controls.budgets
        )
    else:
        verified = False
    measured = None
    try:
        if strategy is not None:
            state = verify_session(
                root / "strategy", strategy, expected_session_id=entry.get("strategy_session_id")
            )
            if run.status == "completed" and (
                state["status"] != "ready"
                or any(event["status"] != "completed" for event in state["events"])
            ):
                raise ValueError("A failed/pending strategy cannot complete final research")
        if run.status == "completed":
            measured = score(
                local_path(root.parents[1], run.artifacts),
                case.specification,
                runtime_usage=local_path(root.parents[1], run.runtime_usage)
                if run.runtime_usage
                else None,
            )
            if measured["reported_model"] != controls.model or set(
                measured["reported_search_providers"]
            ) - {controls.provider}:
                raise ValueError("Final observed model/provider conflicts with fixed controls")
            outcome.update(
                status=measured["status"], quality=measured["quality"], errors=measured["errors"]
            )
        else:
            outcome.update(
                status="execution_failed", quality=0.0, errors=[run.error or "Execution failed"]
            )
    except (ValueError, OSError, TypeError, KeyError, RuntimeError) as exc:
        outcome.update(
            status="artifact_error", quality=0.0, errors=[f"{type(exc).__name__}: {exc}"]
        )
        verified = False
    tokens = (
        usage["total_tokens"]
        if usage
        else (measured["total_tokens"] if measured and controls.execution == "fixture" else None)
    )
    outcome.update(
        total_tokens=tokens,
        completed_token_lower_bound=usage["completed_token_lower_bound"] if usage else tokens,
        execution_verified=verified,
    )
    return outcome


def _candidate_cases(output, candidate_id, plan, archived, archive_config, benchmark, interrupted):
    comparison = output / "evaluations" / candidate_id
    if not (comparison / plan["arm"] / "journal.json").is_file():
        raise FileNotFoundError("Candidate preparation did not finish")
    candidate = archived["candidates"][candidate_id]["bundle"]
    if (
        candidate["sha256"] != plan["candidate_sha256"][candidate_id]
        or _inventory(output / "bundles" / candidate_id, archive_config) != candidate["files"]
    ):
        raise ValueError("Private selected candidate changed")
    comparison = output / "evaluations" / candidate_id
    prepared, current = _load(comparison, check_source=False)
    if current.sha256 != benchmark.sha256:
        raise ValueError("Final candidate benchmark changed")
    controls = RunControls.model_validate(prepared["controls"][plan["arm"]])
    _check_controls(controls, plan["fixed_controls"])
    strategy = _strategy(output / "bundles" / candidate_id, plan["fixed_controls"])
    if controls.code_strategy_sha256 != (
        strategy.sha256 if strategy else None
    ) or controls.strategy_sha256 != digest(
        (output / "bundles" / candidate_id / "instructions.md").read_bytes()
    ):
        raise ValueError("Private execution differs from selected code or instructions")
    with FileLock(str(comparison / plan["arm"] / "controller.lock"), timeout=0):
        journal = _journal(comparison, plan["arm"], prepared)
        if not interrupted and any(
            entry["status"] not in {"completed", "failed"} for entry in journal["cases"].values()
        ):
            raise ValueError("Final report requires every case result")
        cases = [
            _score_case(
                comparison / plan["arm"] / "cases" / case.id,
                case,
                controls,
                journal["cases"][case.id],
                strategy,
            )
            for case in benchmark.cases.values()
        ]
    return cases


def _report(
    archive: OptimizationArchive, output: Path, *, interrupted: bool, reason: str | None = None
) -> dict:
    plan = _read(output / "final-plan.json")
    _, archived, archive_config = archive._load()
    expected_ids = list(
        dict.fromkeys(
            [archive_config.baseline_candidate_id, *archived["selection"]["candidate_ids"]]
        )
    )
    if (
        plan["candidate_ids"] != expected_ids
        or plan["selection_sha256"] != archived["final"]["selection_sha256"]
        or plan["fixed_controls"] != archive_config.fixed_controls
        or plan["execution"] != archive_config.execution
    ):
        raise ValueError("Private evaluation differs from the frozen development selection")
    benchmark = load_benchmark(output / _relative(plan["heldout_manifest"]))
    candidates = []
    for candidate_id in plan["candidate_ids"]:
        try:
            cases = _candidate_cases(
                output, candidate_id, plan, archived, archive_config, benchmark, interrupted
            )
        except FileNotFoundError:
            if not interrupted:
                raise
            cases = [
                {
                    "case_id": case.id,
                    "status": "unmeasured",
                    "quality": None,
                    "total_tokens": None,
                    "completed_token_lower_bound": None,
                    "execution_verified": False,
                    "errors": [
                        "Private candidate preparation was interrupted; no execution outcome is asserted"
                    ],
                }
                for case in benchmark.cases.values()
            ]
        quality = [case["quality"] for case in cases]
        tokens = [case["total_tokens"] for case in cases]
        candidates.append(
            {
                "candidate_id": candidate_id,
                "candidate_sha256": plan["candidate_sha256"][candidate_id],
                "baseline": candidate_id == plan["baseline_candidate_id"],
                "cases": cases,
                "summary": {
                    "case_count": len(cases),
                    "unmeasured_cases": quality.count(None),
                    "failed_cases": sum(
                        case["status"] in {"execution_failed", "invalid", "artifact_error"}
                        for case in cases
                    ),
                    "macro_quality": sum(quality) / len(quality) if None not in quality else None,
                    "total_tokens": sum(tokens) if None not in tokens else None,
                    "known_token_subtotal": sum(value for value in tokens if value is not None),
                    "unknown_token_cases": tokens.count(None),
                    "execution_verified": all(case["execution_verified"] for case in cases),
                },
            }
        )
    baseline = candidates[0]["summary"]["macro_quality"]
    return {
        "schema_version": 1,
        "split": "heldout",
        "status": "interrupted" if interrupted else "completed",
        "operation_id": plan["operation_id"],
        "execution": plan["execution"],
        "arm": plan["arm"],
        "selection_sha256": plan["selection_sha256"],
        "benchmark_sha256": benchmark.sha256,
        "candidate_ids": plan["candidate_ids"],
        "candidates": candidates,
        "interruption_reason": reason,
        "paired_differences": [
            {
                "baseline": plan["baseline_candidate_id"],
                "candidate": item["candidate_id"],
                "macro_quality_difference": item["summary"]["macro_quality"] - baseline
                if item["summary"]["macro_quality"] is not None and baseline is not None
                else None,
            }
            for item in candidates[1:]
        ],
        "limitations": [
            "The selection was fixed on development evidence; held-out scores cannot reopen search or choose another candidate.",
            "Fixture runs establish software behavior, not model performance. Missing usage and interrupted cases remain explicit.",
            "Host-only artifacts are private only if the caller revoked proposer access and quiesced all processes; archive state is not process isolation.",
            "Provider provenance relies on trusted host archives. Predicate scores are not human usefulness or live-source freshness judgments.",
        ],
    }


def evaluate_final(
    archive: OptimizationArchive,
    *,
    heldout_manifest: Path,
    output: Path,
    comparison_config: ComparisonConfig,
    arm: str,
    executor: Callable[[CaseTask], ExecutionArtifacts],
    operation_id: str,
) -> dict[str, Any]:
    """Run one already-selected private final attempt; never resume provider work."""
    if arm not in ARMS:
        raise ValueError("Unknown final runtime arm")
    config = ComparisonConfig.model_validate(comparison_config)
    output = _absolute(output)
    request = _request(heldout_manifest, output, config, arm, operation_id)
    with FileLock(str(archive.root) + ".final.lock", timeout=0):
        prior = archive.status()["final"]
        if prior is not None:
            if prior["operation_id"] != operation_id or prior["private_dir"] != str(output):
                raise ValueError("Final attempt is already bound")
            if (output / "request.json").is_file() and _read(output / "request.json") != request:
                raise ValueError("Final invocation differs from the recorded request")
            if prior["status"] != "started":
                return _terminal(archive, operation_id)
            raise ValueError("Final attempt is unresolved; recover it without reexecuting cases")
        # Durable closure precedes private directory creation, held-out loading,
        # candidate preparation, evaluator work and any provider invocation.
        archive.begin_final(output, operation_id=operation_id)
        try:
            _save(output / "request.json", request)
            source = _absolute(heldout_manifest)
            archive_plan, journal, archive_config = archive._load()
            for private in (archive.root, output, Path(archive_plan["feedback_dir"])):
                _disjoint(source.parent, private)
            if config.execution != archive_config.execution:
                raise ValueError("Final execution mode differs from development")
            heldout = _freeze_heldout(source, output / "heldout", archive, archive_config)
            ids = list(
                dict.fromkeys(
                    [archive_config.baseline_candidate_id, *journal["selection"]["candidate_ids"]]
                )
            )
            plan = {
                "schema_version": 1,
                "operation_id": operation_id,
                "arm": arm,
                "execution": config.execution,
                "baseline_candidate_id": archive_config.baseline_candidate_id,
                "candidate_ids": ids,
                "candidate_sha256": {
                    identity: journal["candidates"][identity]["bundle"]["sha256"]
                    for identity in ids
                },
                "heldout_manifest": str(heldout.path.relative_to(output)),
                "selection_sha256": digest(canonical_json(journal["selection"])),
                "fixed_controls": archive_config.fixed_controls,
            }
            _save(output / "final-plan.json", plan)
            for candidate_id in ids:
                candidate = journal["candidates"][candidate_id]
                destination = output / "bundles" / candidate_id
                _copy(
                    archive.root / "candidates" / candidate_id / "bundle",
                    destination,
                    candidate["bundle"]["files"],
                    archive_config,
                )
                strategy = _strategy(destination, archive_config.fixed_controls)
                comparison = output / "evaluations" / candidate_id
                prepare_comparison(
                    heldout.path,
                    comparison,
                    instructions=(destination / "instructions.md").read_text(encoding="utf-8"),
                    config=config,
                    strategy=strategy,
                    expected_split="heldout",
                )
                prepared, _ = _load(comparison)
                _check_controls(
                    RunControls.model_validate(prepared["controls"][arm]),
                    archive_config.fixed_controls,
                )
            for candidate_id in ids:
                for case_id in heldout.cases:
                    run_case(output / "evaluations" / candidate_id, arm, case_id, executor)
            result = _report(archive, output, interrupted=False)
            _save(output / "report.json", result)
            archive.finish_final(operation_id=operation_id, status="completed")
            return result
        except BaseException as exc:
            try:
                if not archive.status()["final"].get("seal_intent"):
                    _save(
                        output / "failure.json",
                        {"operation_id": operation_id, "error": f"{type(exc).__name__}: {exc}"},
                    )
            except OSError:
                pass
            raise


def recover_final(
    archive: OptimizationArchive, *, operation_id: str, reason: str
) -> dict[str, Any]:
    """Seal a quiescent interrupted attempt and its available evidence, without replay."""
    if not reason.strip():
        raise ValueError("Record the reason for sealing the interrupted attempt")
    with FileLock(str(archive.root) + ".final.lock", timeout=0):
        final = archive.status()["final"]
        if final is None or final["operation_id"] != operation_id:
            raise ValueError("Unknown final attempt")
        if final["status"] != "started":
            return _terminal(archive, operation_id)
        if final.get("seal_intent"):
            archive.finish_final(operation_id=operation_id, status=final["seal_intent"]["status"])
            return _terminal(archive, operation_id)
        output = Path(final["private_dir"])
        if (output / "final-plan.json").is_file():
            try:
                result = _report(archive, output, interrupted=True, reason=reason)
            except Timeout:
                raise
            except (ValueError, OSError, TypeError, KeyError, RuntimeError) as exc:
                result = {
                    "schema_version": 1,
                    "split": "heldout",
                    "status": "interrupted",
                    "operation_id": operation_id,
                    "interruption_reason": reason,
                    "report_available": False,
                    "validation_error": f"{type(exc).__name__}: {exc}",
                    "limitation": "Available private files are retained, but no complete quality report can be verified.",
                }
        else:
            result = {
                "schema_version": 1,
                "split": "heldout",
                "status": "interrupted",
                "operation_id": operation_id,
                "interruption_reason": reason,
                "report_available": False,
                "limitation": "Preparation did not finish; no complete case inventory or quality measurement is asserted.",
            }
        # Do not create absent original setup evidence: archive sealing records it.
        if output.is_dir():
            _save(output / "interruption-report.json", result)
        archive.finish_final(operation_id=operation_id, status="interrupted")
        return result
