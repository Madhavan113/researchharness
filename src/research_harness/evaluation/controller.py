"""Durable, frozen comparison runs owned by the evaluator, outside agent context.

Executors receive only a brief and source fixtures, never scoring predicates.
An interrupted execution is not automatically retried: its provider work may
already have happened. Terminal failures remain in every comparison denominator.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from filelock import FileLock
from pydantic import Field

from research_harness.config import StrictModel
from research_harness.evaluation.benchmark import (
    CaseRun,
    RunControls,
    RunManifest,
    compare_runs,
    gateway_binding_for_case,
    load_benchmark,
    local_path,
)
from research_harness.evaluation.fixtures import FixtureProvider
from research_harness.evaluation.workflow_summary import aggregate_workflows
from research_harness.execution import DiscoverySettings, GatewayBinding
from research_harness.strategies.config import StrategyBundle
from research_harness.strategies.session import StrategySession
from research_harness.util import canonical_json, digest, timestamp, write_json

ARMS = ("direct", "omnigent")


class ComparisonConfig(StrictModel):
    execution: Literal["fixture", "model"] = "fixture"
    model: str = Field(default="gpt-5.4-mini", min_length=1)
    settings: DiscoverySettings = Field(default_factory=DiscoverySettings)
    budget_control: dict[str, Any] | None = None
    # Source access is fixed to the benchmark's authored HTTP/search recordings.
    source_mode: Literal["authored-fixtures"] = "authored-fixtures"


@dataclass(frozen=True)
class CaseTask:
    case_id: str
    brief: str
    arm: str
    output: Path
    fixture_path: Path
    instructions: str
    config: ComparisonConfig
    gateway_binding: GatewayBinding | None = None
    strategy_path: Path | None = None
    code_strategy_sha256: str | None = None
    strategy_session_id: str | None = None


@dataclass(frozen=True)
class ExecutionArtifacts:
    research: Path
    runtime_usage: Path | None = None
    workflow_report: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: tuple[Path, ...] = ()
    gateway_usage: Path | None = None


def source_fingerprints() -> dict[str, str]:
    """Freeze working source bytes, including uncommitted files and dependencies."""
    package = Path(__file__).resolve().parents[1]
    repository = package.parents[1]
    files = {
        f"src/research_harness/{p.relative_to(package)}": digest(p.read_bytes())
        for p in package.rglob("*.py")
    }
    for name in ("pyproject.toml", "uv.lock"):
        path = repository / name
        if path.is_file():
            files[name] = digest(path.read_bytes())
    template = repository / "agents" / "research"
    for path in template.rglob("*"):
        if path.is_file():
            files[str(path.relative_to(repository))] = digest(path.read_bytes())
    return dict(sorted(files.items()))


def _read(path: Path) -> dict:
    return json.loads(path.read_bytes())


def prepare_comparison(
    benchmark_path: Path,
    output: Path,
    *,
    instructions: str,
    config: ComparisonConfig | None = None,
    strategy: StrategyBundle | None = None,
    expected_split: Literal["development", "heldout"] = "development",
) -> Path:
    """Freeze an explicitly chosen split without starting providers or runtimes.

    The public prepare command stays development-only. Private final evaluation
    explicitly requests heldout after freezing selection and revoking search.
    """
    benchmark = load_benchmark(benchmark_path)
    if expected_split not in {"development", "heldout"}:
        raise ValueError("Unsupported comparison split")
    if benchmark.manifest.split != expected_split:
        raise ValueError(f"This comparison controller accepts {expected_split} cases only")
    if not instructions.strip():
        raise ValueError("Shared instructions must not be empty")
    config = ComparisonConfig.model_validate(config or ComparisonConfig())
    if (
        strategy is not None
        and StrategyBundle.load(strategy.manifest_path).sha256 != strategy.sha256
    ):
        raise ValueError("Code strategy changed before comparison preparation")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    package = output / "benchmark"
    package.mkdir()
    for reference in benchmark.manifest.cases:
        target = local_path(package, reference.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path(benchmark.path.parent, reference.path), target)
    for case in benchmark.cases.values():
        target = local_path(package, case.fixtures.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path(benchmark.path.parent, case.fixtures.path), target)
    write_json(package / "manifest.json", benchmark.manifest.model_dump(mode="json"))
    # Revalidate copied bytes; a changing source package cannot silently enter a run.
    frozen = load_benchmark(package / "manifest.json")
    if frozen.sha256 != benchmark.sha256 or frozen.fixture_sha256 != benchmark.fixture_sha256:
        raise ValueError("Benchmark changed while preparing the comparison")
    (output / "instructions.md").write_bytes(instructions.encode("utf-8"))
    frozen_strategy = strategy.freeze(output / "code-strategy") if strategy is not None else None
    sources = source_fingerprints()
    repository = Path(__file__).resolve().parents[3]
    for relative, expected in sources.items():
        target = local_path(output / "implementation", relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / relative, target)
        if digest(target.read_bytes()) != expected:
            raise ValueError("Implementation changed while preparing the comparison")
    common = dict(
        execution=config.execution,
        model=config.model,
        model_settings=config.settings.model_settings(),
        budget_control=config.budget_control,
        provider=FixtureProvider.name,
        provider_settings={"mode": config.source_mode, "external_source_network": False},
        budgets=config.settings.budgets(),
        fixture_sha256=frozen.fixture_sha256,
        strategy_sha256=digest(instructions),
        code_strategy_sha256=frozen_strategy.sha256 if frozen_strategy is not None else None,
        backend_sha256=digest(canonical_json(sources)),
    )
    plan = {
        "schema_version": 1,
        "created_at": timestamp(),
        "benchmark_sha256": frozen.sha256,
        "config": config.model_dump(mode="json"),
        "sources": sources,
        "case_ids": list(frozen.cases),
        "controls": {
            arm: RunControls(runtime=f"{arm}-controlled-responses", **common).model_dump(
                mode="json"
            )
            for arm in ARMS
        },
        "omitted_model_settings": ["parallel_tool_calls"]
        + (["service_tier"] if config.settings.service_tier is None else []),
        "limitations": [
            "Shared semantic instructions are identical; assembled prompts and tool schemas differ.",
            "Model requests require a host gateway; native Omnigent drops some authored limits.",
            "Built-in SDK policies differ: direct max_retries=0, "
            "timeout=min(90, deadline_seconds); pinned Omnigent RetryPolicy "
            "max_retries=7, timeout=120 seconds. Client error timing can differ.",
            "The host gateway rejects SDK retries before provider dispatch "
            "and enforces shared request and deadline limits.",
            "Source fixtures measure behavior on authored data, not live-source freshness.",
            "Generation and operation limits are not an exact provider billing cap.",
            "This directory is trusted controller state, not an M6 candidate sandbox.",
        ],
    }
    if frozen_strategy is not None:
        plan["code_strategy"] = {
            "manifest_path": str(frozen_strategy.manifest_path.relative_to(output)),
            "sha256": frozen_strategy.sha256,
            "config": frozen_strategy.config.model_dump(mode="json"),
        }
    write_json(output / "comparison.json", plan)
    for arm in ARMS:
        write_json(
            output / arm / "journal.json",
            {
                "schema_version": 1,
                "comparison_sha256": digest(canonical_json(plan)),
                "arm": arm,
                "cases": {case_id: {"status": "pending"} for case_id in frozen.cases},
            },
        )
    return output / "comparison.json"


def _load(output: Path, *, check_source: bool = True) -> tuple[dict, Any]:
    output = Path(output).resolve()
    plan = _read(output / "comparison.json")
    if plan.get("schema_version") != 1:
        raise ValueError("Unsupported comparison schema")
    ComparisonConfig.model_validate(plan["config"])
    benchmark = load_benchmark(output / "benchmark" / "manifest.json")
    if plan["benchmark_sha256"] != benchmark.sha256 or plan["case_ids"] != list(benchmark.cases):
        raise ValueError("Frozen benchmark differs from the comparison")
    if check_source and plan["sources"] != source_fingerprints():
        raise ValueError("Implementation changed since preparation; prepare a new comparison")
    for relative, expected in plan["sources"].items():
        if digest(local_path(output / "implementation", relative).read_bytes()) != expected:
            raise ValueError("Frozen implementation bytes changed")
    code = plan.get("code_strategy")
    frozen_strategy = None
    if code is not None:
        if code["manifest_path"] != "code-strategy/strategy.json":
            raise ValueError("Frozen code strategy manifest binding changed")
        frozen_strategy = StrategyBundle.load(local_path(output, code["manifest_path"]))
        if (
            frozen_strategy.sha256 != code["sha256"]
            or frozen_strategy.config.model_dump(mode="json") != code["config"]
        ):
            raise ValueError("Frozen code strategy identity or limits changed")
    for arm in ARMS:
        controls = RunControls.model_validate(plan["controls"][arm])
        if (
            controls.fixture_sha256 != benchmark.fixture_sha256
            or controls.strategy_sha256 != digest((output / "instructions.md").read_bytes())
            or controls.code_strategy_sha256
            != (frozen_strategy.sha256 if frozen_strategy else None)
        ):
            raise ValueError("Frozen instructions, code strategy or fixtures changed")
    return plan, benchmark


def _journal(output: Path, arm: str, plan: dict) -> dict:
    if arm not in ARMS:
        raise ValueError("Unknown comparison arm")
    journal = _read(output / arm / "journal.json")
    if journal["arm"] != arm or journal["comparison_sha256"] != digest(canonical_json(plan)):
        raise ValueError("Journal belongs to a different comparison")
    if set(journal["cases"]) != set(plan["case_ids"]):
        raise ValueError("Journal must retain every case")
    return journal


def _within(root: Path, path: Path) -> str:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Executor artifacts must stay inside their case directory")
    return str(path.relative_to(root.resolve()))


def _artifact_hashes(
    case_root: Path, result: ExecutionArtifacts, *, require_proposal: bool = True
) -> dict[str, str]:
    files = []
    _within(case_root, result.research)
    if require_proposal and (
        not result.research.is_dir() or not (result.research / "proposal.json").is_file()
    ):
        raise ValueError("Execution did not produce a saved proposal")
    files.extend(path for path in result.research.rglob("*") if path.is_file())
    if result.runtime_usage is not None:
        _within(case_root, result.runtime_usage)
        files.append(result.runtime_usage)
        usage = _read(result.runtime_usage)
        if usage.get("source_event_file"):
            files.append(local_path(result.runtime_usage.parent, usage["source_event_file"]))
    if result.workflow_report is not None:
        files.append(result.workflow_report)
    if result.gateway_usage is not None:
        _within(case_root, result.gateway_usage)
        files.append(result.gateway_usage)
        archive = _read(result.gateway_usage)
        files.extend(
            local_path(result.gateway_usage.parent, name) for name in archive.get("files", {})
        )
    for path in result.attachments:
        _within(case_root, path)
        files.extend(
            item for item in path.rglob("*") if item.is_file()
        ) if path.is_dir() else files.append(path)
    return {_within(case_root, path): digest(path.read_bytes()) for path in files}


def _save_artifact_refs(
    case_root: Path, entry: dict, result: ExecutionArtifacts, *, complete: bool
) -> None:
    hashes = _artifact_hashes(case_root, result, require_proposal=complete)
    canonical_json(result.metadata)
    entry.update(
        artifact_hashes=hashes,
        metadata=result.metadata,
        research=_within(case_root, result.research),
        research_present=result.research.is_dir(),
        runtime_usage=_within(case_root, result.runtime_usage) if result.runtime_usage else None,
        gateway_usage=_within(case_root, result.gateway_usage) if result.gateway_usage else None,
        workflow_report=_within(case_root, result.workflow_report)
        if result.workflow_report
        else None,
        attachments=[_within(case_root, path) for path in result.attachments],
    )


def _retain_strategy(case_root: Path, result: ExecutionArtifacts) -> ExecutionArtifacts:
    paths = [case_root / "strategy", case_root / "runtime" / "code-strategy"]
    attachments = tuple(
        dict.fromkeys((*result.attachments, *(path for path in paths if path.exists())))
    )
    return replace(result, attachments=attachments)


def _check_strategy_session(
    case_root: Path, expected: str | None, expected_session_id: str | None
) -> None:
    if expected is None:
        return
    session = StrategySession.open(case_root / "strategy")
    if session.bundle.sha256 != expected or session.session_id != expected_session_id:
        raise ValueError("Case strategy session differs from frozen comparison controls")
    session.assert_ready()


def run_case(
    output: Path,
    arm: str,
    case_id: str,
    executor: Callable[[CaseTask], ExecutionArtifacts],
) -> dict:
    """Run a pending case once. Return prior terminals; refuse unresolved replays.

    The caller owns provider access and spending authorization. Executions must
    pass through the same request gate; fixture callbacks are explicitly labelled.
    """
    output = Path(output).resolve()
    plan, benchmark = _load(output)
    if arm not in ARMS or case_id not in benchmark.cases:
        raise ValueError("Unknown comparison arm or case")
    arm_root = output / arm
    # One arm lock covers journal updates and the provider operation, preventing
    # concurrent retries without keeping a database transaction over network I/O.
    with FileLock(str(arm_root / "controller.lock"), timeout=0):
        journal = _journal(output, arm, plan)
        entry = journal["cases"][case_id]
        if entry["status"] in {"completed", "failed"}:
            return entry
        if entry["status"] != "pending":
            raise ValueError(
                "Case is unresolved; inspect its artifacts and record interruption before continuing"
            )
        case = benchmark.cases[case_id]
        case_root = arm_root / "cases" / case_id
        execution_id = uuid4().hex
        binding = gateway_binding_for_case(
            case, RunControls.model_validate(plan["controls"][arm]), execution_id
        )
        entry.update(status="running", started_at=timestamp(), execution_id=execution_id)
        write_json(arm_root / "journal.json", journal)
        case_root.mkdir(parents=True, exist_ok=False)
        task = CaseTask(
            case_id=case_id,
            brief=case.brief,
            arm=arm,
            output=case_root,
            fixture_path=local_path(output / "benchmark", case.fixtures.path),
            instructions=(output / "instructions.md").read_text(encoding="utf-8"),
            config=ComparisonConfig.model_validate(plan["config"]),
            gateway_binding=binding,
            strategy_path=local_path(output, plan["code_strategy"]["manifest_path"])
            if plan.get("code_strategy")
            else None,
            code_strategy_sha256=plan.get("code_strategy", {}).get("sha256"),
        )
        # The public task copy omits evaluator requirements, correct source
        # choices and review notes. Each case/arm has a separate backend root.
        write_json(case_root / "task.json", {"id": case_id, "brief": case.brief})
        started = perf_counter()
        result = None
        try:
            if task.strategy_path is not None:
                session = StrategySession(
                    case_root / "strategy", StrategyBundle.load(task.strategy_path)
                )
                entry["strategy_session_id"] = session.session_id
                task = replace(task, strategy_session_id=session.session_id)
                write_json(arm_root / "journal.json", journal)
            result = _retain_strategy(case_root, executor(task))
            _check_strategy_session(case_root, task.code_strategy_sha256, task.strategy_session_id)
            _save_artifact_refs(case_root, entry, result, complete=True)
            run = CaseRun(
                case_id=case_id,
                status="completed",
                execution_id=execution_id,
                artifacts=_within(arm_root, result.research),
                runtime_usage=_within(arm_root, result.runtime_usage)
                if result.runtime_usage
                else None,
                elapsed_seconds=perf_counter() - started,
                gateway_usage=_within(arm_root, result.gateway_usage)
                if result.gateway_usage
                else None,
            )
        except Exception as exc:
            # Validation can fail after the executor returned provider evidence.
            # Preserve that result as partial just as an executor-raised error.
            partial = getattr(exc, "artifacts", result)
            if not isinstance(partial, ExecutionArtifacts) and (case_root / "strategy").exists():
                partial = ExecutionArtifacts(
                    research=case_root / ("research" if arm == "direct" else "runtime/research")
                )
            if isinstance(partial, ExecutionArtifacts):
                partial = _retain_strategy(case_root, partial)
                try:
                    _save_artifact_refs(case_root, entry, partial, complete=False)
                except Exception as artifact_error:
                    entry["artifact_error"] = f"{type(artifact_error).__name__}: {artifact_error}"
                    partial = None
            run = CaseRun(
                case_id=case_id,
                status="failed",
                execution_id=execution_id,
                error=f"{type(exc).__name__}: {exc}",
                artifacts=_within(arm_root, partial.research)
                if isinstance(partial, ExecutionArtifacts) and partial.research.is_dir()
                else None,
                runtime_usage=_within(arm_root, partial.runtime_usage)
                if isinstance(partial, ExecutionArtifacts) and partial.runtime_usage
                else None,
                elapsed_seconds=perf_counter() - started,
                gateway_usage=_within(arm_root, partial.gateway_usage)
                if isinstance(partial, ExecutionArtifacts) and partial.gateway_usage
                else None,
            )
        except BaseException as exc:
            partial = getattr(exc, "artifacts", result)
            if not isinstance(partial, ExecutionArtifacts) and (case_root / "strategy").exists():
                partial = ExecutionArtifacts(
                    research=case_root / ("research" if arm == "direct" else "runtime/research")
                )
            if isinstance(partial, ExecutionArtifacts):
                try:
                    _save_artifact_refs(
                        case_root, entry, _retain_strategy(case_root, partial), complete=False
                    )
                except Exception as artifact_error:
                    entry["artifact_error"] = f"{type(artifact_error).__name__}: {artifact_error}"
            entry.update(status="interrupted", observed_at=timestamp())
            write_json(arm_root / "journal.json", journal)
            raise
        entry.update(status=run.status, finished_at=timestamp(), run=run.model_dump(mode="json"))
        write_json(arm_root / "journal.json", journal)
        return entry


def record_interruption(
    output: Path,
    arm: str,
    case_id: str,
    *,
    reason: str,
    artifacts: ExecutionArtifacts | None = None,
    artifact_loader: Callable[[], ExecutionArtifacts] | None = None,
) -> dict:
    """Record an abandoned execution as failed without making another provider call."""
    if not reason.strip():
        raise ValueError("Record the observed interruption reason")
    if artifacts is not None and artifact_loader is not None:
        raise ValueError("Supply recovered artifacts or a loader, not both")
    output = Path(output).resolve()
    plan, _ = _load(output, check_source=False)
    if arm not in ARMS or case_id not in plan["case_ids"]:
        raise ValueError("Unknown comparison arm or case")
    with FileLock(str(output / arm / "controller.lock"), timeout=0):
        journal = _journal(output, arm, plan)
        entry = journal["cases"][case_id]
        if entry["status"] not in {"running", "interrupted"}:
            raise ValueError("Only unresolved executions can be recorded as interrupted")
        if artifact_loader is not None:
            # Recovery may reconcile host accounting files. Do that only after
            # acquiring the lock that excludes a still-running executor.
            artifacts = artifact_loader()
        arm_root = output / arm
        case_root = arm_root / "cases" / case_id
        if artifacts is None and (case_root / "strategy").exists():
            artifacts = ExecutionArtifacts(
                research=case_root / ("research" if arm == "direct" else "runtime/research")
            )
        if artifacts is not None:
            artifacts = _retain_strategy(case_root, artifacts)
            # Explicitly supplied recovery evidence is frozen without replaying
            # the execution. The evaluator still checks its original binding.
            _save_artifact_refs(arm_root / "cases" / case_id, entry, artifacts, complete=False)
        run = CaseRun(
            case_id=case_id,
            status="failed",
            error=f"Interrupted: {reason}",
            execution_id=entry.get("execution_id"),
            artifacts=_within(arm_root, artifacts.research)
            if artifacts is not None and artifacts.research.is_dir()
            else None,
            runtime_usage=_within(arm_root, artifacts.runtime_usage)
            if artifacts is not None and artifacts.runtime_usage
            else None,
            gateway_usage=_within(arm_root, artifacts.gateway_usage)
            if artifacts is not None and artifacts.gateway_usage
            else None,
        )
        entry.update(status="failed", finished_at=timestamp(), run=run.model_dump(mode="json"))
        write_json(output / arm / "journal.json", journal)
        return entry


def finalize_comparison(output: Path) -> dict:
    """Verify frozen artifacts, retain failures, and score both complete arms."""
    output = Path(output).resolve()
    plan, benchmark = _load(output)
    manifests = []
    workflows = {}
    for arm in ARMS:
        with FileLock(str(output / arm / "controller.lock"), timeout=0):
            journal = _journal(output, arm, plan)
            runs, reports = [], {}
            for case_id in plan["case_ids"]:
                entry = journal["cases"][case_id]
                if entry["status"] not in {"completed", "failed"}:
                    raise ValueError("Finish or resolve every case before final comparison")
                run = CaseRun.model_validate(entry["run"])
                if (
                    run.case_id != case_id
                    or run.status != entry["status"]
                    or run.execution_id != entry.get("execution_id")
                ):
                    raise ValueError("Journal result has a foreign case or status")
                case_root = output / arm / "cases" / case_id
                if run.status == "completed":
                    _check_strategy_session(
                        case_root,
                        plan.get("code_strategy", {}).get("sha256"),
                        entry.get("strategy_session_id"),
                    )
                for name in ("artifacts", "runtime_usage", "gateway_usage"):
                    relative = getattr(run, name)
                    expected = (
                        (entry.get("research") if entry.get("research_present") else None)
                        if name == "artifacts"
                        else entry.get(name)
                    )
                    if (relative is None) != (expected is None):
                        raise ValueError(
                            "Journal artifact reference differs from its frozen case binding"
                        )
                    if relative is not None:
                        selected = local_path(output / arm, relative)
                        bound = _within(case_root, selected)
                        if bound != expected:
                            raise ValueError(
                                "Journal artifact reference differs from its frozen case binding"
                            )
                if run.status == "completed" or "artifact_hashes" in entry:
                    result = ExecutionArtifacts(
                        research=local_path(case_root, entry["research"]),
                        runtime_usage=local_path(case_root, entry["runtime_usage"])
                        if entry.get("runtime_usage")
                        else None,
                        gateway_usage=local_path(case_root, entry["gateway_usage"])
                        if entry.get("gateway_usage")
                        else None,
                        workflow_report=local_path(case_root, entry["workflow_report"])
                        if entry.get("workflow_report")
                        else None,
                        attachments=tuple(
                            local_path(case_root, relative)
                            for relative in entry.get("attachments", [])
                        ),
                    )
                    result = _retain_strategy(case_root, result)
                    try:
                        current = _artifact_hashes(
                            case_root, result, require_proposal=run.status == "completed"
                        )
                        if current != entry["artifact_hashes"]:
                            raise ValueError("File inventory or bytes differ")
                    except (OSError, ValueError) as exc:
                        raise ValueError(
                            "Saved execution artifacts changed after completion"
                        ) from exc
                if entry.get("workflow_report"):
                    reports[case_id] = {
                        "case_id": case_id,
                        "report": _read(local_path(case_root, entry["workflow_report"])),
                    }
                runs.append(run)
            manifest = RunManifest(
                name=arm,
                benchmark_sha256=benchmark.sha256,
                controls=RunControls.model_validate(plan["controls"][arm]),
                runs=runs,
            )
            path = output / arm / "run.json"
            write_json(path, manifest.model_dump(mode="json"))
            manifests.append(path)
            workflows[arm] = aggregate_workflows(plan["case_ids"], reports)
    report = compare_runs(output / "benchmark" / "manifest.json", manifests, axis="runtime")
    report["workflow"] = workflows
    report["controller_limitations"] = plan["limitations"]
    write_json(output / "report.json", report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("benchmark", type=Path)
    prepare.add_argument("--out", required=True, type=Path)
    prepare.add_argument("--instructions", required=True, type=Path)
    prepare.add_argument("--config", type=Path)
    prepare.add_argument("--strategy", type=Path, help="Manifest of the isolated code strategy")
    finalize = commands.add_parser("finalize")
    finalize.add_argument("output", type=Path)
    interrupt = commands.add_parser("record-interruption")
    interrupt.add_argument("output", type=Path)
    interrupt.add_argument("arm", choices=ARMS)
    interrupt.add_argument("case_id")
    interrupt.add_argument("--reason", required=True)
    interrupt.add_argument("--gateway-usage", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        path = prepare_comparison(
            args.benchmark,
            args.out,
            instructions=args.instructions.read_text(encoding="utf-8"),
            config=ComparisonConfig.model_validate_json(args.config.read_bytes())
            if args.config
            else None,
            strategy=StrategyBundle.load(args.strategy) if args.strategy else None,
        )
        print(path)
    elif args.command == "finalize":
        print(json.dumps(finalize_comparison(args.output), indent=2))
    else:
        print(
            json.dumps(
                record_interruption(
                    args.output,
                    args.arm,
                    args.case_id,
                    reason=args.reason,
                    artifacts=ExecutionArtifacts(
                        research=args.output / args.arm / "cases" / args.case_id / "research",
                        gateway_usage=args.gateway_usage,
                    )
                    if args.gateway_usage
                    else None,
                ),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
