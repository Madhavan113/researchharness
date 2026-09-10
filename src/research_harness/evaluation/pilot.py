"""Run a frozen discovery comparison with one shared, durable request budget.

Preparing a pilot is offline. Model execution requires explicitly recorded
external authorization and an environment key; this module never grants that
authorization. Fixture callers must supply a deterministic HTTP response policy.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import httpx
from pydantic import model_validator

from research_harness.config import StrictModel
from research_harness.evaluation.benchmark import (
    RunControls,
    gateway_binding_for_case,
    load_benchmark,
    local_path,
)
from research_harness.evaluation.budget import AuthorizationRecord, BudgetLedger, RateCard
from research_harness.evaluation.controller import (
    ARMS,
    CaseTask,
    ComparisonConfig,
    ExecutionArtifacts,
    finalize_comparison,
    prepare_comparison,
    record_interruption,
    run_case,
)
from research_harness.evaluation.dispatch_budget import DispatchBudget, DispatchPolicy
from research_harness.evaluation.runtime_executor import (
    RuntimeExecutionError,
    RuntimeExecutor,
    task_strategy_session,
)
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.strategies.config import StrategyBundle
from research_harness.util import canonical_json, digest, timestamp, write_json

DISCOVERY_FUNCTIONS = {
    "begin_research",
    "get_research_context",
    "search_sources",
    "inspect_source",
    "probe_source",
    "submit_proposal",
    "get_evidence",
    "list_pipelines",
}


class PilotConfig(StrictModel):
    schema_version: Literal[1] = 1
    purpose: Literal["compatibility", "baseline"] = "compatibility"
    policy: DispatchPolicy
    authorization: AuthorizationRecord
    ceiling_usd: str
    rates: RateCard
    comparison: ComparisonConfig

    @model_validator(mode="after")
    def coherent(self):
        try:
            ceiling = Decimal(self.ceiling_usd)
        except InvalidOperation as exc:
            raise ValueError("Invalid decimal ceiling") from exc
        if not ceiling.is_finite() or ceiling < Decimal("0.000000001"):
            raise ValueError(
                "Pilot ceiling must be a positive decimal string of at least one nanodollar"
            )
        if self.comparison.budget_control is not None:
            raise ValueError("Pilot preparation derives budget controls from its ledger")
        if self.comparison.model != self.policy.model:
            raise ValueError("Pilot and comparison must use the same model snapshot")
        expected_execution = "fixture" if self.policy.mode == "fixture" else "model"
        if self.comparison.execution != expected_execution:
            raise ValueError("Pilot execution label conflicts with its provider policy")
        if self.comparison.settings.service_tier != "default":
            raise ValueError("Budgeted pilots require an explicit default service tier")
        return self


def _ledger(path: Path, config: PilotConfig, *, create: bool = False) -> BudgetLedger:
    if not create and not path.is_file():
        raise ValueError("Shared budget ledger is missing; never recreate spent funds")
    return BudgetLedger(
        path,
        rates=config.rates,
        ceiling_usd=config.ceiling_usd,
        authorization=config.authorization if create else None,
    )


def prepare_pilot(
    benchmark_path: Path,
    output: Path,
    *,
    ledger_path: Path,
    instructions: str,
    config: PilotConfig,
    strategy: StrategyBundle | None = None,
) -> Path:
    """Freeze a pilot without dispatching; reuse this ledger across source revisions."""
    config = PilotConfig.model_validate(config)
    output, ledger_path = Path(output).resolve(), Path(ledger_path).resolve()
    if output.exists():
        raise ValueError("Choose a new pilot output directory")
    if ledger_path.is_relative_to(output):
        raise ValueError("The shared ledger must live outside the prepared comparison")
    load_benchmark(benchmark_path)
    registry_path = Path(str(ledger_path) + ".pilots.json")
    if registry_path.exists() and not ledger_path.is_file():
        raise ValueError("Registered shared ledger is missing; never recreate spent funds")
    if ledger_path.exists() and not registry_path.is_file():
        raise ValueError("Existing shared budget ledger has no pilot registry; do not reset it")
    ledger = _ledger(ledger_path, config, create=True)
    with ledger.lock:
        registry = (
            _check_registered_pilots(ledger)
            if registry_path.is_file()
            else {"schema_version": 1, "pilots": {}}
        )
        if not registry_path.exists():
            # Offline preparation may fail after the ledger is created. Retain
            # an empty registry so a new attempt can reuse that exact ledger.
            write_json(registry_path, registry)
            descriptor = os.open(ledger_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        control = DispatchBudget.configuration_metadata(
            ledger,
            policy=config.policy,
            settings=config.comparison.settings,
            authorization=config.authorization,
        )
        prepare_comparison(
            benchmark_path,
            output,
            instructions=instructions,
            config=config.comparison.model_copy(update={"budget_control": control}),
            strategy=strategy,
        )
        witness = output / "budget-initial.json"
        write_json(witness, ledger.snapshot())
        manifest = {
            "schema_version": 1,
            "configuration": config.model_dump(mode="json"),
            "ledger_path": str(ledger_path),
            "comparison_sha256": digest((output / "comparison.json").read_bytes()),
            "initial_ledger_sha256": digest(witness.read_bytes()),
        }
        write_json(output / "pilot.json", manifest)
        for arm in ARMS:
            path = output / arm / "journal.json"
            journal = json.loads(path.read_bytes())
            journal["pilot_sha256"] = digest(canonical_json(manifest))
            write_json(path, journal)
        registry["pilots"][str(output)] = digest(canonical_json(manifest))
        write_json(registry_path, registry)
        descriptor = os.open(ledger_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return output / "pilot.json"


def _load_pilot(output: Path) -> tuple[PilotConfig, BudgetLedger, dict]:
    manifest = json.loads((output / "pilot.json").read_bytes())
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported pilot manifest")
    if digest((output / "comparison.json").read_bytes()) != manifest["comparison_sha256"]:
        raise ValueError("Frozen pilot comparison changed")
    for arm in ARMS:
        journal = json.loads((output / arm / "journal.json").read_bytes())
        if journal.get("pilot_sha256") != digest(canonical_json(manifest)):
            raise ValueError("Pilot configuration or shared ledger reference changed")
    config = PilotConfig.model_validate(manifest["configuration"])
    ledger = _ledger(Path(manifest["ledger_path"]), config)
    plan = json.loads((output / "comparison.json").read_bytes())
    expected = DispatchBudget.configuration_metadata(
        ledger,
        policy=config.policy,
        settings=config.comparison.settings,
        authorization=config.authorization,
    )
    if plan["config"].get("budget_control") != expected or any(
        plan["controls"][arm].get("budget_control") != expected for arm in ARMS
    ):
        raise ValueError("Frozen pilot budget controls changed")
    _check_registered_pilots(ledger, required=output)
    return config, ledger, plan


def _check_registered_pilots(ledger: BudgetLedger, *, required: Path | None = None) -> dict:
    """Check every revision against one locked snapshot, including older preparations."""
    registry_path = Path(str(ledger.path) + ".pilots.json")
    with ledger.lock:
        if not registry_path.is_file():
            raise ValueError("Shared budget pilot registry is missing")
        registry = json.loads(registry_path.read_bytes())
        if registry.get("schema_version") != 1 or not isinstance(registry.get("pilots"), dict):
            raise ValueError("Invalid shared budget pilot registry")
        if required is not None and str(required) not in registry["pilots"]:
            raise ValueError("Prepared pilot is missing from the shared budget registry")
        current = ledger.snapshot()
        for location, expected_hash in registry["pilots"].items():
            output = Path(location)
            manifest = json.loads((output / "pilot.json").read_bytes())
            if (
                digest(canonical_json(manifest)) != expected_hash
                or Path(manifest["ledger_path"]) != ledger.path
                or digest((output / "comparison.json").read_bytes())
                != manifest["comparison_sha256"]
            ):
                raise ValueError("Registered pilot or ledger reference changed")
            for arm in ARMS:
                journal = json.loads((output / arm / "journal.json").read_bytes())
                if journal.get("pilot_sha256") != expected_hash:
                    raise ValueError("Registered pilot journal changed")
            config = PilotConfig.model_validate(manifest["configuration"])
            plan = json.loads((output / "comparison.json").read_bytes())
            _check_ledger_evidence(output, config, ledger, plan, manifest, current)
        searches = registry.get("searches", {})
        if not isinstance(searches, dict):
            raise ValueError("Invalid shared budget search registry")
        if searches:
            from research_harness.optimization.runner import _check_search_ledger_evidence

            for location, expected_hash in searches.items():
                _check_search_ledger_evidence(Path(location), ledger, expected_hash, current)
        return registry


def _check_ledger_witness(before: dict, current: dict) -> None:
    """Detect loss/regression of host-observed state, not adversarial file forgery."""
    for key in ("rates", "rates_sha256", "ceiling_usd", "ceiling_nanodollars"):
        if before[key] != current[key]:
            raise ValueError("Shared budget ledger differs from its recorded witness")
    history = before["authorization_history"]
    if current["authorization_history"][: len(history)] != history:
        raise ValueError("Shared budget ledger authorization history was rolled back")
    for key, row in before["reservations"].items():
        now = current["reservations"].get(key)
        if now is None:
            raise ValueError(f"Shared budget ledger lost a witnessed operation: {key}")
        for field in (
            "operation_id",
            "request_sha256",
            "max_output_tokens",
            "reserved_nanodollars",
            "created_at",
        ):
            if now[field] != row[field]:
                raise ValueError(f"Shared budget ledger changed a witnessed operation: {key}")
        if row["status"] in {"settled", "released"} and now != row:
            raise ValueError(f"Shared budget ledger changed a terminal operation: {key}")
        if row["dispatched_at"] is not None and now["dispatched_at"] != row["dispatched_at"]:
            raise ValueError(f"Shared budget ledger lost a dispatch marker: {key}")
        if row["status"] == "held" and now["status"] in {"reserved", "dispatched"}:
            raise ValueError(f"Shared budget ledger rolled back an unresolved hold: {key}")
        if now["hold_reasons"][: len(row["hold_reasons"])] != row["hold_reasons"]:
            raise ValueError(f"Shared budget ledger lost recorded hold reasons: {key}")


def _check_ledger_evidence(output, config, ledger, plan, manifest, current):
    witness = output / "budget-initial.json"
    if digest(witness.read_bytes()) != manifest["initial_ledger_sha256"]:
        raise ValueError("Frozen initial budget witness changed")
    _check_ledger_witness(json.loads(witness.read_bytes()), current)
    _check_comparison_ledger_evidence(output, config, ledger, plan, current)


def _check_comparison_ledger_evidence(output, config, ledger, plan, current):
    """Share case evidence checks with registered search and private-final runs."""
    benchmark = load_benchmark(output / "benchmark/manifest.json")
    for arm in ARMS:
        journal = json.loads((output / arm / "journal.json").read_bytes())
        for case_id, entry in journal["cases"].items():
            case_root = output / arm / "cases" / case_id
            for name, expected_hash in entry.get("artifact_hashes", {}).items():
                if name.startswith("gateway/"):
                    path = local_path(case_root, name)
                    if not path.is_file() or digest(path.read_bytes()) != expected_hash:
                        raise ValueError("Recorded gateway budget evidence changed or is missing")
            for name in ("budget-start.json", "budget-checkpoint.json"):
                path = case_root / name
                expected_hash = entry.get("artifact_hashes", {}).get(name)
                if expected_hash and (
                    not path.is_file() or digest(path.read_bytes()) != expected_hash
                ):
                    raise ValueError("Recorded case budget witness changed")
                if path.is_file():
                    _check_ledger_witness(json.loads(path.read_bytes())["ledger"], current)
            archive = case_root / "gateway/archive.json"
            if archive.is_file() and entry.get("execution_id"):
                budget = DispatchBudget(
                    ledger,
                    policy=config.policy,
                    settings=config.comparison.settings,
                    authorization=config.authorization,
                    binding=gateway_binding_for_case(
                        benchmark.cases[case_id],
                        RunControls.model_validate(plan["controls"][arm]),
                        entry["execution_id"],
                    ),
                )
                consistency = budget.archive_consistency(archive, snapshot=current)
                if consistency["status"] == "inconsistent":
                    raise ValueError(
                        "Budget archive/ledger consistency failed: "
                        + "; ".join(consistency["errors"])
                    )


def _artifact_manifest(task: CaseTask, artifacts: ExecutionArtifacts) -> Path:
    def relative(path):
        return str(path.resolve().relative_to(task.output.resolve())) if path is not None else None

    path = task.output / "budget-execution.json"
    write_json(
        path,
        {
            "research": relative(artifacts.research),
            "runtime_usage": relative(artifacts.runtime_usage),
            "gateway_usage": relative(artifacts.gateway_usage),
            "attachments": [relative(item) for item in artifacts.attachments],
            "metadata": artifacts.metadata,
        },
    )
    return path


class BudgetedRuntimeExecutor:
    def __init__(
        self,
        config: PilotConfig,
        ledger: BudgetLedger,
        *,
        omnigent_python: Path,
        api_key: str | None = None,
        fixture_handler: Callable[[CaseTask, httpx.Request], httpx.Response] | None = None,
    ):
        self.config, self.ledger = config, ledger
        self.omnigent_python = Path(omnigent_python).expanduser().absolute()
        self.api_key, self.fixture_handler = api_key, fixture_handler
        if config.policy.mode == "fixture":
            if fixture_handler is None or api_key is not None:
                raise ValueError(
                    "Fixture execution requires an explicit response policy and no provider key"
                )
        elif fixture_handler is not None or not api_key:
            raise ValueError(
                "Live execution requires an environment provider key and no fixture handler"
            )
        elif config.authorization.status != "approved":
            raise ValueError("Live execution requires a recorded external spending approval")

    def __call__(self, task: CaseTask) -> ExecutionArtifacts:
        if task.gateway_binding is None:
            raise ValueError("Budgeted execution requires a controller binding")
        budget = DispatchBudget(
            self.ledger,
            policy=self.config.policy,
            binding=task.gateway_binding,
            settings=task.config.settings,
            authorization=self.config.authorization,
        )
        if budget.metadata() != task.config.budget_control:
            raise ValueError("Executor budget differs from the frozen comparison")
        allowed = (
            {"search_sources", "inspect_url", "probe_source"}
            if task.arm == "direct"
            else {f"research__{name}" for name in DISCOVERY_FUNCTIONS}
        )
        result = None
        error = None
        gateway = None
        reconciliation = None
        host_errors = []
        starting_budget = task.output / "budget-start.json"
        write_json(
            starting_budget,
            {"observed_at": timestamp(), "ledger": self.ledger.snapshot()},
        )
        with contextlib.ExitStack() as stack:
            client = (
                stack.enter_context(
                    httpx.Client(
                        transport=httpx.MockTransport(
                            lambda request: self.fixture_handler(task, request)
                        )
                    )
                )
                if self.fixture_handler
                else None
            )
            try:
                strategy = task_strategy_session(task)
                gateway = ResponsesGateway(
                    task.output / "gateway",
                    model=task.config.model,
                    settings=task.config.settings,
                    upstream_base_url=self.config.policy.upstream_base_url,
                    upstream_api_key=self.api_key,
                    client=client,
                    allowed_function_names=allowed,
                    binding=task.gateway_binding,
                    dispatch_budget=budget,
                    **({"strategy": strategy} if strategy is not None else {}),
                )
                with gateway:
                    runtime = RuntimeExecutor(
                        base_url=gateway.base_url,
                        api_key=gateway.api_key,
                        omnigent_python=self.omnigent_python,
                        max_spend_usd=min(float(self.config.ceiling_usd), 100.0),
                    )
                    try:
                        result = runtime(task)
                    except BaseException as exc:
                        # Capture partial runtime evidence before context-manager
                        # shutdown can raise a separate archive/transport error.
                        error = exc
                        result = getattr(exc, "artifacts", result)
            except BaseException as exc:
                error = exc if error is None or not isinstance(exc, Exception) else error
                result = getattr(exc, "artifacts", result)
                host_errors.append(f"Gateway: {type(exc).__name__}: {exc}")
            finally:
                if gateway is not None:
                    try:
                        gateway.close()
                    except BaseException as exc:
                        error = exc if error is None or not isinstance(exc, Exception) else error
                        host_errors.append(f"Gateway shutdown: {type(exc).__name__}: {exc}")
            if gateway is not None and (gateway.output / "archive.json").is_file():
                reconciliation = task.output / "budget-reconciliation.json"
                try:
                    report = budget.reconcile_archive(gateway.output / "archive.json")
                    if report.get("status") == "inconsistent" and error is None:
                        error = ValueError("Provider archive and shared budget ledger disagree")
                except BaseException as exc:
                    report = {
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "unverified_reservations": "held",
                    }
                    error = exc if error is None or not isinstance(exc, Exception) else error
                try:
                    write_json(reconciliation, report)
                except BaseException as exc:
                    error = exc if error is None or not isinstance(exc, Exception) else error
                    host_errors.append(f"Budget report: {type(exc).__name__}: {exc}")
        if not isinstance(result, ExecutionArtifacts):
            research = task.output / ("research" if task.arm == "direct" else "runtime/research")
            result = ExecutionArtifacts(research=research)
        checkpoint = task.output / "budget-checkpoint.json"
        try:
            write_json(checkpoint, {"observed_at": timestamp(), "ledger": self.ledger.snapshot()})
        except BaseException as exc:
            error = exc if error is None or not isinstance(exc, Exception) else error
            host_errors.append(f"Budget checkpoint: {type(exc).__name__}: {exc}")
        additions = tuple(
            path
            for path in (
                gateway.output if gateway else None,
                reconciliation,
                starting_budget,
                checkpoint,
                task.output / "strategy",
                task.output / "runtime" / "code-strategy",
            )
            if path is not None and path.exists()
        )
        result = replace(
            result,
            metadata={**result.metadata, "budget_host_errors": host_errors},
            attachments=(*result.attachments, *additions),
            gateway_usage=gateway.output / "archive.json"
            if gateway is not None and (gateway.output / "archive.json").is_file()
            else None,
        )
        record = task.output / "budget-execution.json"
        try:
            _artifact_manifest(task, result)
        except BaseException as exc:
            error = exc if error is None or not isinstance(exc, Exception) else error
            host_errors.append(f"Artifact manifest: {type(exc).__name__}: {exc}")
        if record.is_file():
            result = replace(result, attachments=(*result.attachments, record))
        if error is not None:
            if isinstance(error, Exception):
                raise RuntimeExecutionError(str(error), artifacts=result) from error
            error.artifacts = result
            raise error
        return result


def execute_pilot_case(
    output: Path,
    arm: str,
    case_id: str,
    *,
    omnigent_python: Path,
    fixture_handler: Callable[[CaseTask, httpx.Request], httpx.Response] | None = None,
) -> dict:
    output = Path(output).resolve()
    config, ledger, _ = _load_pilot(output)
    if arm not in ARMS:
        raise ValueError("Unknown comparison arm")
    journal = json.loads((output / arm / "journal.json").read_bytes())
    if case_id not in journal["cases"]:
        raise ValueError("Unknown comparison case")
    if journal["cases"][case_id]["status"] in {"completed", "failed"}:
        return journal["cases"][case_id]
    if config.policy.mode == "openai-standard":
        current_authorization = ledger.snapshot()["authorization"]
        if current_authorization[
            "status"
        ] != "approved" or current_authorization != config.authorization.model_dump(mode="json"):
            raise ValueError(
                "Live execution requires the current matching external spending approval"
            )
    if config.policy.mode == "openai-standard" and config.purpose == "baseline":
        benchmark = load_benchmark(output / "benchmark/manifest.json")
        if any(case.review_status != "reviewed" for case in benchmark.cases.values()):
            raise ValueError("A measured baseline requires reviewed development cases")
    executor = BudgetedRuntimeExecutor(
        config,
        ledger,
        omnigent_python=omnigent_python,
        api_key=os.environ.get("OPENAI_API_KEY")
        if config.policy.mode == "openai-standard"
        else None,
        fixture_handler=fixture_handler,
    )
    return run_case(output, arm, case_id, executor)


def recover_pilot_case(output: Path, arm: str, case_id: str, *, reason: str) -> dict:
    """Attach stopped-case evidence and reconcile; never replay a model request."""
    output = Path(output).resolve()
    config, ledger, plan = _load_pilot(output)
    if arm not in ARMS or case_id not in plan["case_ids"]:
        raise ValueError("Unknown pilot arm or case")
    case_root = output / arm / "cases" / case_id
    journal = json.loads((output / arm / "journal.json").read_bytes())
    entry = journal["cases"][case_id]
    if entry["status"] not in {"running", "interrupted"}:
        raise ValueError("Only unresolved executions can be recovered")

    def load_artifacts():
        archive = case_root / "gateway/archive.json"
        attachments = [
            path
            for name in ("budget-start.json", "budget-checkpoint.json")
            if (path := case_root / name).is_file()
        ]
        attachments.extend(
            path
            for path in (case_root / "strategy", case_root / "runtime" / "code-strategy")
            if path.exists()
        )
        if archive.is_file():
            case = load_benchmark(output / "benchmark/manifest.json").cases[case_id]
            budget = DispatchBudget(
                ledger,
                policy=config.policy,
                settings=config.comparison.settings,
                authorization=config.authorization,
                binding=gateway_binding_for_case(
                    case, RunControls.model_validate(plan["controls"][arm]), entry["execution_id"]
                ),
            )
            reconciliation = case_root / "budget-reconciliation.json"
            write_json(reconciliation, budget.reconcile_archive(archive))
            attachments.extend([archive.parent, reconciliation])
        manifest = case_root / "budget-execution.json"
        if manifest.is_file():
            raw = json.loads(manifest.read_bytes())
            artifacts = ExecutionArtifacts(
                research=local_path(case_root, raw["research"]),
                runtime_usage=local_path(case_root, raw["runtime_usage"])
                if raw.get("runtime_usage")
                else None,
                gateway_usage=archive if archive.is_file() else None,
                attachments=tuple(
                    {
                        manifest,
                        *attachments,
                        *(local_path(case_root, value) for value in raw["attachments"]),
                    }
                ),
                metadata=raw["metadata"],
            )
        else:
            artifacts = ExecutionArtifacts(
                research=case_root / ("research" if arm == "direct" else "runtime/research"),
                gateway_usage=archive if archive.is_file() else None,
                attachments=tuple(attachments),
            )
        return artifacts

    return record_interruption(output, arm, case_id, reason=reason, artifact_loader=load_artifacts)


def finalize_pilot(output: Path) -> dict:
    output = Path(output).resolve()
    config, ledger, _ = _load_pilot(output)
    report = finalize_comparison(output)
    identities = set()
    for arm in ARMS:
        journal = json.loads((output / arm / "journal.json").read_bytes())
        identities.update(entry.get("execution_id") for entry in journal["cases"].values())
    with ledger.lock:
        _check_registered_pilots(ledger, required=output)
        snapshot = ledger.snapshot()
        selected = [
            row
            for key, row in snapshot["reservations"].items()
            if key.split("/", 1)[0] in identities
        ]
        report["pilot_budget"] = {
            "purpose": config.purpose,
            "execution": config.comparison.execution,
            "cost_semantics": "Supplied token-rate accounting, not a provider invoice; fixture amounts are synthetic",
            "rates": config.rates.model_dump(mode="json"),
            "accounting_observed_at": timestamp(),
            "comparison_settlement_complete": all(
                (result["score"].get("gateway_usage") or {}).get("budget_settlement_complete")
                is True
                for arm in report["arms"]
                for result in arm["results"]
            )
            and all(row["status"] in {"settled", "released"} for row in selected),
            "comparison_settled_nanodollars": sum(
                row["charged_nanodollars"] for row in selected if row["status"] == "settled"
            ),
            "comparison_held_nanodollars": sum(
                row["charged_nanodollars"]
                for row in selected
                if row["status"] not in {"settled", "released"}
            ),
            "shared_ledger_accounting": snapshot["accounting"],
            "authorization": snapshot["authorization"],
        }
        write_json(output / "pilot-report.json", report)
    return report


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("benchmark", type=Path)
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--instructions", type=Path, required=True)
    prepare.add_argument("--ledger", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    prepare.add_argument("--strategy", type=Path, help="Manifest of the isolated code strategy")
    run = commands.add_parser("run-case")
    run.add_argument("output", type=Path)
    run.add_argument("arm", choices=ARMS)
    run.add_argument("case_id")
    run.add_argument("--omnigent-python", type=Path, required=True)
    recover = commands.add_parser("recover")
    recover.add_argument("output", type=Path)
    recover.add_argument("arm", choices=ARMS)
    recover.add_argument("case_id")
    recover.add_argument("--reason", required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        print(
            prepare_pilot(
                args.benchmark,
                args.out,
                ledger_path=args.ledger,
                instructions=args.instructions.read_text(),
                config=PilotConfig.model_validate_json(args.config.read_bytes()),
                strategy=StrategyBundle.load(args.strategy) if args.strategy else None,
            )
        )
    elif args.command == "run-case":
        print(
            json.dumps(
                execute_pilot_case(
                    args.output, args.arm, args.case_id, omnigent_python=args.omnigent_python
                ),
                indent=2,
            )
        )
    elif args.command == "recover":
        print(
            json.dumps(
                recover_pilot_case(args.output, args.arm, args.case_id, reason=args.reason),
                indent=2,
            )
        )
    else:
        print(json.dumps(finalize_pilot(args.output), indent=2))


if __name__ == "__main__":
    main()
