"""Export development execution evidence and grade it with the host evaluator.

The comparison directory contains private benchmark predicates and evaluator
source. Never give that directory to a proposer. Export only the complete
controller-recorded execution inventory; no trace summarization is performed.
The trusted host controls which executor may supply that inventory.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from filelock import FileLock

from research_harness.evaluation.benchmark import (
    CaseRun,
    RunManifest,
    gateway_binding_for_case,
    load_benchmark,
    local_path,
)
from research_harness.evaluation.controller import (
    ARMS,
    ExecutionArtifacts,
    _artifact_hashes,
    _journal,
    _load,
)
from research_harness.evaluation.discovery import score
from research_harness.evaluation.gateway_usage import verify_gateway_usage
from research_harness.execution import DiscoverySettings
from research_harness.optimization.archive import (
    CaseEvidence,
    DevelopmentEvidence,
    EvaluationInput,
    _absolute,
    _disjoint,
    _read,
    _relative,
    _save,
    _write_file,
)
from research_harness.strategies.config import StrategyBundle
from research_harness.strategies.session import verify_session
from research_harness.util import canonical_json, digest


def _model_evidence(controls, usage: dict | None) -> bool:
    """Token completeness alone cannot turn a synthetic run into a live baseline."""
    budget = controls.budget_control
    if controls.execution != "model" or budget is None or usage is None:
        return False
    policy, approval = budget.get("policy", {}), budget.get("authorization", {})
    return (
        policy.get("mode") == "openai-standard"
        and policy.get("upstream_base_url") == "https://api.openai.com/v1"
        and policy.get("service_tier") == "default"
        and approval.get("status") == "approved"
        and isinstance(approval.get("reference"), str)
        and bool(approval["reference"].strip())
        and usage.get("status") == "verified_complete"
        and usage.get("upstream_transport") == "httpx-default"
        and usage.get("budget_control_verified") is True
        and usage.get("budget_settlement_complete") is True
        and usage.get("budget_control") == budget
        and type(usage.get("dispatched_requests")) is int
        and usage["dispatched_requests"] > 0
        and type(usage.get("response_count")) is int
        and usage["response_count"] > 0
    )


def _case_files(root: Path, entry: dict, run: CaseRun) -> dict[str, str]:
    for name in ("artifacts", "runtime_usage", "gateway_usage"):
        expected = (
            (entry.get("research") if entry.get("research_present") else None)
            if name == "artifacts"
            else entry.get(name)
        )
        relative = getattr(run, name)
        if relative != (f"cases/{run.case_id}/{expected}" if expected else None):
            raise ValueError("Case artifact references differ from the host journal")
    if "artifact_hashes" not in entry:
        if run.status == "completed":
            raise ValueError("A completed case requires its full artifact inventory")
        return {}

    def optional(name):
        return local_path(root, entry[name]) if entry.get(name) else None

    result = ExecutionArtifacts(
        research=local_path(root, entry["research"]),
        runtime_usage=optional("runtime_usage"),
        gateway_usage=optional("gateway_usage"),
        workflow_report=optional("workflow_report"),
        attachments=tuple(local_path(root, p) for p in entry.get("attachments", [])),
    )
    actual = _artifact_hashes(root, result, require_proposal=run.status == "completed")
    if actual != entry["artifact_hashes"]:
        raise ValueError("Development artifact inventory or bytes changed")
    return actual


def export_development(comparison: Path, arm: str, output: Path) -> Path:
    """Copy one terminal arm into a new development-only execution package.

    This never launches an executor or needs the other comparison arm to run.
    Partial export directories are not reusable; another export may copy the
    same completed execution to a fresh directory without replaying providers.
    """
    if arm not in ARMS:
        raise ValueError("Unknown comparison arm")
    comparison, output = _absolute(comparison), _absolute(output)
    _disjoint(comparison, output)
    plan, benchmark = _load(comparison, check_source=False)
    with FileLock(str(comparison / arm / "controller.lock"), timeout=0):
        journal = _journal(comparison, arm, plan)
        selected, runs, records = {}, [], {}
        for case_id in plan["case_ids"]:
            entry = journal["cases"][case_id]
            if entry["status"] not in {"completed", "failed"}:
                raise ValueError("Finish or resolve every development case before export")
            run = CaseRun.model_validate(entry["run"])
            if (
                run.case_id != case_id
                or run.status != entry["status"]
                or run.execution_id != entry.get("execution_id")
            ):
                raise ValueError("Case result differs from its host execution binding")
            root = comparison / arm / "cases" / case_id
            for relative, sha256 in _case_files(root, entry, run).items():
                selected[f"cases/{case_id}/{_relative(relative)}"] = sha256
            records[case_id] = entry
            runs.append(run)
        manifest = RunManifest(
            name=arm,
            benchmark_sha256=benchmark.sha256,
            controls=plan["controls"][arm],
            runs=runs,
        )
        output.mkdir(parents=True, exist_ok=False)
        for relative, sha256 in selected.items():
            raw = _absolute(comparison / arm / relative).read_bytes()
            if digest(raw) != sha256:
                raise ValueError("Execution changed during development export")
            _write_file(output / relative, raw)
        _save(output / "run.json", manifest.model_dump(mode="json"))
        for case_id, record in records.items():
            _save(output / "case-records" / f"{case_id}.json", record)
        # Private benchmark/source files are not part of selected, even though
        # their identities stay in the fixed controls and host archive.
        _save(
            output / "development.json",
            {
                "schema_version": 1,
                "split": "development",
                "arm": arm,
                "benchmark_sha256": benchmark.sha256,
                "execution_files": selected,
                "limitations": [
                    "Host-recorded execution artifacts only; benchmark predicates and evaluator source remain private.",
                    "Hashes prove consistency, not authenticity against a malicious host executor.",
                ],
            },
        )
        for case_id, entry in records.items():
            _case_files(
                comparison / arm / "cases" / case_id,
                entry,
                CaseRun.model_validate(entry["run"]),
            )
    return output / "run.json"


class ResearchDevelopmentEvaluator:
    """Read-only independent archive callback for exported controlled runs.

    ``fixed_controls`` must be the archive's full fixed control mapping. Only
    instruction and code identities vary; sandbox and strategy limits do not.
    No candidate score, model-authored coverage claim or report.json is trusted.
    """

    def __init__(self, fixed_controls: dict):
        self.fixed_controls = deepcopy(fixed_controls)

    def __call__(self, context: EvaluationInput) -> DevelopmentEvidence:
        if digest(canonical_json(self.fixed_controls)) != context.binding["controls_sha256"]:
            raise ValueError("Evaluator controls differ from the frozen archive")
        root = context.artifacts_dir
        exported = _read(root / "development.json")
        manifest = RunManifest.model_validate(_read(root / "run.json"))
        benchmark = load_benchmark(context.benchmark_path)
        controls = manifest.controls
        if (
            exported.get("schema_version") != 1
            or exported.get("split") != "development"
            or benchmark.manifest.split != "development"
            or exported.get("arm") != manifest.name
            or exported.get("benchmark_sha256") != benchmark.sha256
            or manifest.benchmark_sha256 != benchmark.sha256
            or benchmark.sha256 != context.binding["benchmark_sha256"]
            or controls.execution != context.binding["execution"]
            or tuple(benchmark.cases) != context.case_ids
            or controls.fixture_sha256 != benchmark.fixture_sha256
        ):
            raise ValueError("Development package is not bound to this benchmark/execution")
        fixed = controls.model_dump(
            mode="json", exclude={"strategy_sha256", "code_strategy_sha256"}
        )
        expected = {
            k: v for k, v in self.fixed_controls.items() if k not in {"sandbox", "strategy_limits"}
        }
        if fixed != expected:
            raise ValueError("Development execution changed frozen controls")
        candidate = _read(context.candidate_dir / "manifest.json")
        if (
            set(candidate) != {"schema_version", "strategy_manifest"}
            or candidate["schema_version"] != 1
        ):
            raise ValueError("Expected the research development candidate manifest")
        if (
            digest((context.candidate_dir / "instructions.md").read_bytes())
            != controls.strategy_sha256
        ):
            raise ValueError("Execution instructions differ from the registered candidate")
        strategy = None
        if candidate["strategy_manifest"] is not None:
            strategy = StrategyBundle.load(
                context.candidate_dir / "source" / _relative(candidate["strategy_manifest"])
            )
            if strategy.sha256 != controls.code_strategy_sha256:
                raise ValueError("Execution code differs from the registered candidate")
            limits = strategy.config.model_dump(
                mode="json", exclude={"source", "source_sha256", "sandbox"}
            )
            if strategy.config.sandbox.model_dump(mode="json") != self.fixed_controls.get(
                "sandbox"
            ) or limits != self.fixed_controls.get("strategy_limits"):
                raise ValueError("Candidate changed frozen sandbox or strategy limits")
        elif controls.code_strategy_sha256 is not None:
            raise ValueError("A code-strategy execution requires its registered source")
        ids = [run.case_id for run in manifest.runs]
        if len(ids) != len(set(ids)) or set(ids) != set(context.case_ids):
            raise ValueError("Every development case must be retained exactly once")
        for relative, sha256 in exported["execution_files"].items():
            if context.inventory.get(_relative(relative)) != sha256:
                raise ValueError("Exported execution bytes differ from the archive inventory")
        cases, verified = [], []
        for run in manifest.runs:
            case = benchmark.cases[run.case_id]
            case_root = root / "cases" / run.case_id
            record_name = f"case-records/{run.case_id}.json"
            entry = _read(root / record_name)
            if entry.get("run") != run.model_dump(mode="json"):
                raise ValueError("Exported run differs from its case journal")
            files = _case_files(case_root, entry, run)
            references = ["development.json", "run.json", record_name] + [
                f"cases/{case.id}/{name}" for name in sorted(files)
            ]
            binding = (
                gateway_binding_for_case(case, controls, run.execution_id)
                if run.execution_id
                else None
            )
            usage = None
            if run.gateway_usage and binding:
                usage = verify_gateway_usage(
                    local_path(root, run.gateway_usage),
                    binding,
                    expected_model=controls.model,
                    expected_model_settings=controls.model_settings,
                    expected_budgets=controls.budgets,
                    expected_budget_control=controls.budget_control,
                    expected_strategy_sha256=controls.code_strategy_sha256,
                )
            case_verified = _model_evidence(controls, usage)
            execution_path = case_root / "execution.json"
            if execution_path.is_file():
                execution = _read(execution_path)
                settings = DiscoverySettings.model_validate(execution.get("execution_settings"))
                case_verified = case_verified and (
                    execution.get("arm") == manifest.name
                    and execution.get("model") == controls.model
                    and execution.get("instructions_sha256") == controls.strategy_sha256
                    and execution.get("code_strategy_sha256") == controls.code_strategy_sha256
                    and execution.get("gateway_binding")
                    == (binding.model_dump(mode="json") if binding else None)
                    and settings.model_settings() == controls.model_settings
                    and settings.budgets() == controls.budgets
                    and execution.get("status") == run.status
                )
            else:
                case_verified = False
            status, quality, errors = "execution_failed", 0.0, [run.error or "Execution failed"]
            measured = None
            try:
                if strategy is not None:
                    state = verify_session(
                        case_root / "strategy",
                        strategy,
                        expected_session_id=entry.get("strategy_session_id"),
                    )
                    if run.status == "completed" and (
                        state["status"] != "ready"
                        or any(event["status"] != "completed" for event in state["events"])
                    ):
                        raise ValueError(
                            "A failed/pending strategy cannot be a completed research run"
                        )
                if run.status == "completed":
                    measured = score(
                        local_path(root, run.artifacts),
                        case.specification,
                        runtime_usage=local_path(root, run.runtime_usage)
                        if run.runtime_usage
                        else None,
                    )
                    if measured["reported_model"] != controls.model or set(
                        measured["reported_search_providers"]
                    ) - {controls.provider}:
                        raise ValueError(
                            "Observed model or search provider differs from frozen controls"
                        )
                    status, quality, errors = (
                        measured["status"],
                        measured["quality"],
                        measured["errors"],
                    )
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                status, quality, errors = "artifact_error", 0.0, [f"{type(exc).__name__}: {exc}"]
                case_verified = False
            # Synthetic fixture totals can exercise the archive lifecycle. Live
            # totals require independently checked gateway evidence, including failures.
            tokens = (
                usage["total_tokens"]
                if usage
                else (
                    measured["total_tokens"]
                    if measured and controls.execution == "fixture"
                    else None
                )
            )
            lower = usage["completed_token_lower_bound"] if usage else tokens
            cases.append(
                CaseEvidence(
                    case_id=case.id,
                    status=status,
                    quality=float(quality),
                    total_tokens=tokens,
                    completed_token_lower_bound=lower,
                    evidence_files=references,
                    errors=errors,
                )
            )
            verified.append(case_verified)
        return context.evidence(
            cases=cases,
            execution_verified=all(verified),
            limitations=[
                "Sampled source-selection quality is independently recomputed from frozen development requirements; it is not live-source freshness or human usefulness review.",
                "Fixture scores and tokens test software behavior; only verified provider archives can establish model execution usage.",
                "Live baseline eligibility additionally requires standard host HTTP dispatch, matching approved production budget controls, positive observed responses and complete budget evidence. These are trusted-host provenance records, not authentication against a malicious host or renewed spending permission.",
                "The host must keep evaluator inputs private and isolate/revoke the proposer before final evaluation.",
            ],
        )
