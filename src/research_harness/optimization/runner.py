"""Budgeted search and private final execution using the existing real runtimes.

Preparation is offline. Authorization records describe an external decision;
they never grant permission. The shared .pilots.json registry retains pilots
and searches across revisions. Source fixtures remain the controlled treatment.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import model_validator

from research_harness.config import StrictModel
from research_harness.evaluation.benchmark import RunControls, gateway_binding_for_case, local_path
from research_harness.evaluation.budget import AuthorizationRecord, BudgetLedger, RateCard
from research_harness.evaluation.controller import (
    ARMS,
    ExecutionArtifacts,
    _journal,
)
from research_harness.evaluation.controller import (
    _load as load_comparison,
)
from research_harness.evaluation.dispatch_budget import DispatchBudget, DispatchPolicy
from research_harness.evaluation.pilot import (
    BudgetedRuntimeExecutor,
    PilotConfig,
    _check_comparison_ledger_evidence,
    _check_ledger_witness,
    _check_registered_pilots,
    _ledger,
)
from research_harness.execution import GatewayBinding
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.optimization.archive import (
    ArchiveConfig,
    _absolute,
    _inventory,
    _read,
    _relative,
    _save,
)
from research_harness.optimization.controller import SearchConfig, SearchController
from research_harness.optimization.proposer import (
    TOOL_NAMES,
    ProposalError,
    ResponsesCodingProposer,
    proposal_binding,
)
from research_harness.strategies.config import StrategyBundle
from research_harness.util import canonical_json, digest, timestamp


class SearchRunConfig(StrictModel):
    schema_version: Literal[1] = 1
    policy: DispatchPolicy
    authorization: AuthorizationRecord
    ceiling_usd: str
    rates: RateCard
    search: SearchConfig

    @model_validator(mode="after")
    def coherent(self):
        self.pilot_config()
        if self.search.proposer.model != self.policy.model:
            raise ValueError("Research and proposer must use the same priced model snapshot")
        if self.search.proposer.settings.service_tier != "default":
            raise ValueError("Budgeted proposals require an explicit default service tier")
        if self.search.proposer.budget_control is not None:
            raise ValueError("Search preparation derives proposer budget controls from its ledger")
        return self

    def pilot_config(self) -> PilotConfig:
        return PilotConfig(
            purpose="baseline",
            policy=self.policy,
            authorization=self.authorization,
            ceiling_usd=self.ceiling_usd,
            rates=self.rates,
            comparison=self.search.comparison,
        )


def _bound_config(config: SearchRunConfig, ledger: BudgetLedger) -> SearchConfig:
    def control(settings):
        return DispatchBudget.configuration_metadata(
            ledger, policy=config.policy, settings=settings, authorization=config.authorization
        )

    data = config.search.model_dump(mode="json")
    data["comparison"]["budget_control"] = control(config.search.comparison.settings)
    data["proposer"]["budget_control"] = control(config.search.proposer.settings)
    return SearchConfig.model_validate(data)


def prepare_search_run(
    development_manifest: Path,
    output: Path,
    *,
    feedback: Path,
    ledger_path: Path,
    baseline: StrategyBundle,
    instructions: str,
    config: SearchRunConfig,
) -> Path:
    config = SearchRunConfig.model_validate(config.model_dump(mode="json"))
    output, feedback, ledger_path = map(_absolute, (output, feedback, ledger_path))
    if output.exists() or feedback.exists():
        raise ValueError("Choose new search and feedback directories")
    if any(ledger_path.is_relative_to(path) for path in (output, feedback)):
        raise ValueError("The shared ledger must stay outside search and feedback directories")
    registry_path = Path(str(ledger_path) + ".pilots.json")
    if registry_path.exists() != ledger_path.is_file():
        raise ValueError("Shared ledger and registry must both exist; never reset spent funds")
    ledger = _ledger(ledger_path, config, create=True)
    with ledger.lock:
        registry = (
            _check_registered_pilots(ledger)
            if registry_path.exists()
            else {"schema_version": 1, "pilots": {}}
        )
        if not registry_path.exists():
            # A failed offline preparation must not strand a new ledger without
            # its registry or invite recreating the ledger on the next attempt.
            _save(registry_path, registry)
        SearchController.create(
            output,
            feedback,
            development_manifest=development_manifest,
            baseline=baseline,
            instructions=instructions,
            config=_bound_config(config, ledger),
        )
        _save(output / "budget-initial.json", ledger.snapshot())
        manifest = {
            "schema_version": 1,
            "configuration": config.model_dump(mode="json"),
            "ledger_path": str(ledger_path),
            "search_sha256": digest((output / "search.json").read_bytes()),
            "initial_ledger_sha256": digest((output / "budget-initial.json").read_bytes()),
        }
        _save(output / "budget-run.json", manifest)
        registry.setdefault("searches", {})[str(output)] = digest(canonical_json(manifest))
        _save(registry_path, registry)
    return output / "budget-run.json"


def _configuration(output, ledger, expected_hash):
    manifest = _read(output / "budget-run.json")
    if (
        manifest.get("schema_version") != 1
        or digest(canonical_json(manifest)) != expected_hash
        or Path(manifest["ledger_path"]) != ledger.path
        or digest((output / "search.json").read_bytes()) != manifest["search_sha256"]
        or digest((output / "budget-initial.json").read_bytes())
        != manifest["initial_ledger_sha256"]
    ):
        raise ValueError("Registered search, ledger reference or initial witness changed")
    config = SearchRunConfig.model_validate(manifest["configuration"])
    plan = _read(output / "search.json")
    if plan["config"] != _bound_config(config, ledger).model_dump(mode="json"):
        raise ValueError("Search budget controls differ from its registered configuration")
    journal = _read(output / "journal.json")
    if journal.get("search_sha256") != digest(canonical_json(plan)):
        raise ValueError("Registered search journal differs from its frozen plan")
    return config, journal


def _comparisons(output, journal):
    for candidate in journal["candidates"].values():
        if "comparison" in candidate:
            path = output / _relative(candidate["comparison"])
            if (path / "comparison.json").is_file():
                yield path
            elif candidate["status"] != "preparing":
                raise ValueError("Registered candidate comparison is missing")
    final = _read(output / "archive/journal.json").get("final")
    if final is not None:
        if journal["phase"] not in {"final_started", "finalized"} or not journal.get(
            "proposer_revocation", {}
        ).get("quiescent"):
            raise ValueError("Private final access requires frozen search and proposer revocation")
        private = Path(final["private_dir"])
        if final["status"] != "started":
            sealed = _read(private / "final-archive.json")
            files = _inventory(
                private,
                ArchiveConfig.model_validate(_read(output / "search.json")["archive_config"]),
            )
            files.pop("final-archive.json", None)
            if (
                files != sealed["files"]
                or digest(canonical_json(sealed)) != final["archive_sha256"]
            ):
                raise ValueError("Registered private final artifacts changed")
        plan_path = private / "final-plan.json"
        if plan_path.is_file():
            plan = _read(plan_path)
            for candidate_id in plan["candidate_ids"]:
                path = private / "evaluations" / _relative(candidate_id)
                if (path / "comparison.json").is_file():
                    yield path


def _budget(config, ledger, binding, *, proposer=False):
    settings = config.search.proposer.settings if proposer else config.search.comparison.settings
    return DispatchBudget(
        ledger,
        policy=config.policy,
        settings=settings,
        authorization=config.authorization,
        binding=binding,
    )


def _gateways(output, config, journal, ledger):
    for proposal in journal["proposals"].values():
        binding = GatewayBinding.model_validate(proposal["binding"])
        archive = output / _relative(proposal["output"]) / "gateway/archive.json"
        if archive.is_file():
            start = output / "budget/proposers" / proposal["execution_id"] / "start.json"
            if not start.is_file() or _read(start).get("binding") != binding.model_dump(
                mode="json"
            ):
                raise ValueError("Proposer budget witness is missing or has a foreign binding")
            yield archive, _budget(config, ledger, binding, proposer=True)
    for comparison in _comparisons(output, journal):
        plan, benchmark = load_comparison(comparison, check_source=False)
        for arm in ARMS:
            for case_id, entry in _journal(comparison, arm, plan)["cases"].items():
                archive = comparison / arm / "cases" / case_id / "gateway/archive.json"
                if archive.is_file():
                    if not entry.get("execution_id"):
                        raise ValueError("Provider archive exists without its reserved execution")
                    binding = gateway_binding_for_case(
                        benchmark.cases[case_id],
                        RunControls.model_validate(plan["controls"][arm]),
                        entry["execution_id"],
                    )
                    yield archive, _budget(config, ledger, binding)


def _check_search_ledger_evidence(output, ledger, expected_hash, current):
    """Called under the shared ledger lock, including from older pilot revisions."""
    config, journal = _configuration(output, ledger, expected_hash)
    _check_ledger_witness(_read(output / "budget-initial.json"), current)
    for path in (output / "budget").rglob("*.json"):
        record = _read(path)
        if "ledger" in record:
            _check_ledger_witness(record["ledger"], current)
    bound = _bound_config(config, ledger)
    for comparison in _comparisons(output, journal):
        plan, _ = load_comparison(comparison, check_source=False)
        if plan["config"] != bound.comparison.model_dump(mode="json"):
            raise ValueError("Registered search comparison changed its frozen controls")
        _check_comparison_ledger_evidence(comparison, config.pilot_config(), ledger, plan, current)
    for archive, budget in _gateways(output, config, journal, ledger):
        consistency = budget.archive_consistency(archive, snapshot=current)
        if consistency["status"] == "inconsistent":
            raise ValueError(
                "Search budget archive/ledger consistency failed: "
                + "; ".join(consistency["errors"])
            )


class BudgetedSearchRun:
    def __init__(self, output: Path):
        self.root = _absolute(output)
        manifest = _read(self.root / "budget-run.json")
        self._config_json = SearchRunConfig.model_validate(
            manifest["configuration"]
        ).model_dump_json()
        self.ledger = _ledger(Path(manifest["ledger_path"]), self.config)
        self._check()
        self.controller = SearchController(self.root)

    @property
    def config(self):
        return SearchRunConfig.model_validate_json(self._config_json)

    def _check(self):
        registry = _check_registered_pilots(self.ledger)
        if str(self.root) not in registry.get("searches", {}):
            raise ValueError("Search is missing from the shared budget registry")

    def _execution(self, api_key, research_handler, proposer_handler=None, *, needs_proposer=True):
        self._check()
        config = self.config
        if config.policy.mode == "fixture":
            if (
                api_key is not None
                or research_handler is None
                or (needs_proposer and proposer_handler is None)
            ):
                raise ValueError(
                    "Fixture execution requires explicit HTTP handlers and no provider key"
                )
        else:
            if not api_key or research_handler is not None or proposer_handler is not None:
                raise ValueError("Model execution requires a provider key and no fixture handlers")
            current = self.ledger.snapshot()["authorization"]
            if (
                current != config.authorization.model_dump(mode="json")
                or current["status"] != "approved"
            ):
                raise ValueError(
                    "Model execution requires the current matching external spending approval"
                )

    def _executor(self, omnigent_python, api_key, handler):
        runtime = BudgetedRuntimeExecutor(
            self.config.pilot_config(),
            self.ledger,
            omnigent_python=omnigent_python,
            api_key=api_key,
            fixture_handler=handler,
        )

        def execute(task):
            self._check()
            return runtime(task)

        return execute

    def _proposer(self, api_key=None, handler=None):
        return _BudgetedProposer(self, api_key=api_key, handler=handler)

    def run(
        self, *, omnigent_python: Path, api_key=None, research_handler=None, proposer_handler=None
    ):
        if self.controller.status()["phase"] != "search":
            return self.status()
        self._execution(api_key, research_handler, proposer_handler)
        proposer = self._proposer(api_key, proposer_handler)
        try:
            self.controller.run(
                executor=self._executor(omnigent_python, api_key, research_handler),
                proposer=proposer,
            )
        finally:
            proposer.close()
        return self.status()

    def final(
        self,
        *,
        heldout_manifest: Path,
        output: Path,
        omnigent_python: Path,
        api_key=None,
        research_handler=None,
        operation_id="final",
    ):
        if self.ledger.path.is_relative_to(_absolute(output)):
            raise ValueError("The shared ledger must stay outside private final artifacts")
        phase = self.controller.status()["phase"]
        if phase == "selected":
            self._execution(api_key, research_handler, needs_proposer=False)
            executor = self._executor(omnigent_python, api_key, research_handler)
        else:

            def executor(task):
                raise ValueError("Final recovery or retry cannot dispatch provider work")

        result = self.controller.final(
            heldout_manifest=heldout_manifest,
            output=output,
            executor=executor,
            operation_id=operation_id,
        )
        self._check()
        return result

    def reconcile(self):
        """Reconcile surviving sealed archives without changing execution artifacts."""
        with self.controller.lock:
            self._check()
            _, journal, _, _ = self.controller._load()
            reports = []
            for archive, budget in _gateways(self.root, self.config, journal, self.ledger):
                reports.append(
                    {"archive": str(archive), "result": budget.reconcile_archive(archive)}
                )
            record = {
                "observed_at": timestamp(),
                "reports": reports,
                "ledger": self.ledger.snapshot(),
            }
            _save(self.root / "budget/reconciliations" / (uuid4().hex + ".json"), record)
            return record

    def recover_proposal(self, iteration: int, *, reason: str):
        proposer = self._proposer()
        try:
            result = self.controller.recover_proposal(iteration, proposer=proposer, reason=reason)
        finally:
            proposer.close()
        self.reconcile()
        return result

    def recover_case(self, candidate_id: str, case_id: str, *, reason: str):
        with self.controller.lock:
            _, journal, config, _ = self.controller._load()
            comparison = self.root / journal["candidates"][candidate_id]["comparison"]
            result = self.controller.recover_case(
                candidate_id,
                case_id,
                reason=reason,
                artifact_loader=lambda: _surviving_artifacts(
                    comparison / config.arm / "cases" / case_id, config.arm
                ),
            )
        self.reconcile()
        return result

    def recover_final(self, *, reason: str, operation_id="final"):
        result = self.controller.recover_final(reason=reason, operation_id=operation_id)
        self.reconcile()
        return result

    def status(self):
        state = self.controller.status()
        with self.ledger.lock:
            self._check()
            snapshot = self.ledger.snapshot()
        return {
            "search": state,
            "budget": snapshot["accounting"],
            "authorization": snapshot["authorization"],
            "cost_semantics": "Supplied token-rate accounting; fixture amounts are synthetic, not paid spending",
        }


class _BudgetedProposer(ResponsesCodingProposer):
    def __init__(self, run: BudgetedSearchRun, *, api_key=None, handler=None):
        self.run, self.api_key, self.handler = run, api_key, handler
        self.clients = []
        config = _bound_config(run.config, run.ledger)
        super().__init__(config.proposer, config.workspace, gateway_factory=self._gateway)

    def _gateway(self, task):
        self.run._check()
        client = None
        if self.handler is not None:
            client = httpx.Client(
                transport=httpx.MockTransport(lambda request: self.handler(task, request))
            )
            self.clients.append(client)
        binding = proposal_binding(task, self.config)
        return ResponsesGateway(
            task.output / "gateway",
            model=self.config.model,
            settings=self.config.settings,
            upstream_base_url=self.run.config.policy.upstream_base_url,
            upstream_api_key=self.api_key,
            client=client,
            binding=binding,
            allowed_function_names=set(TOOL_NAMES),
            dispatch_budget=_budget(self.run.config, self.run.ledger, binding, proposer=True),
        )

    def propose(self, task):
        self.run._check()
        receipt = self.run.root / "budget/proposers" / task.execution_id
        if receipt.exists():
            raise ValueError("Budgeted proposal attempt already exists; recover without replay")
        _save(
            receipt / "start.json",
            {
                "binding": proposal_binding(task, self.config).model_dump(mode="json"),
                "ledger": self.run.ledger.snapshot(),
                "observed_at": timestamp(),
            },
        )
        error = None
        try:
            return super().propose(task)
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                archive = task.output / "gateway/archive.json"
                result = (
                    _budget(
                        self.run.config,
                        self.run.ledger,
                        proposal_binding(task, self.config),
                        proposer=True,
                    ).reconcile_archive(archive)
                    if archive.is_file()
                    else {"status": "unsealed", "reservations": "held"}
                )
                _save(
                    receipt / "checkpoint.json",
                    {
                        "reconciliation": result,
                        "ledger": self.run.ledger.snapshot(),
                        "observed_at": timestamp(),
                    },
                )
                if result["status"] == "inconsistent":
                    raise ValueError("Proposer archive and shared ledger disagree")
            except BaseException as exc:
                if error is not None:
                    error.add_note(
                        f"Budget reconciliation also failed: {type(exc).__name__}: {exc}"
                    )
                else:
                    raise ProposalError(str(exc), task.output) from exc

    def close(self):
        proof = super().close()
        for client in self.clients:
            client.close()
        self.clients.clear()
        return proof


def _surviving_artifacts(case_root, arm):
    manifest = case_root / "budget-execution.json"
    if manifest.is_file():
        data = _read(manifest)
        return ExecutionArtifacts(
            research=local_path(case_root, data["research"]),
            runtime_usage=local_path(case_root, data["runtime_usage"])
            if data.get("runtime_usage")
            else None,
            gateway_usage=local_path(case_root, data["gateway_usage"])
            if data.get("gateway_usage")
            else None,
            metadata=data["metadata"],
            attachments=tuple([manifest, *(local_path(case_root, p) for p in data["attachments"])]),
        )
    archive = case_root / "gateway/archive.json"
    return ExecutionArtifacts(
        research=case_root / ("research" if arm == "direct" else "runtime/research"),
        gateway_usage=archive if archive.is_file() else None,
        attachments=tuple(
            path
            for name in ("gateway", "strategy", "budget-start.json", "budget-checkpoint.json")
            if (path := case_root / name).exists()
        ),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("development_manifest", type=Path)
    for name in ("config", "baseline", "instructions", "ledger", "out", "feedback"):
        prepare.add_argument("--" + name, type=Path, required=True)
    for command in (
        "status",
        "run",
        "final",
        "reconcile",
        "recover-proposal",
        "recover-case",
        "recover-final",
    ):
        command_parser = sub.add_parser(command)
        command_parser.add_argument("output", type=Path)
        if command in {"run", "final"}:
            command_parser.add_argument("--omnigent-python", type=Path, required=True)
        if command == "final":
            command_parser.add_argument("--heldout", type=Path, required=True)
            command_parser.add_argument("--private-out", type=Path, required=True)
        if command.startswith("recover-"):
            command_parser.add_argument("--reason", required=True)
        if command == "recover-proposal":
            command_parser.add_argument("--iteration", type=int, required=True)
        if command == "recover-case":
            command_parser.add_argument("--candidate", required=True)
            command_parser.add_argument("--case", required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = str(
            prepare_search_run(
                args.development_manifest,
                args.out,
                feedback=args.feedback,
                ledger_path=args.ledger,
                baseline=StrategyBundle.load(args.baseline),
                instructions=args.instructions.read_text(),
                config=SearchRunConfig.model_validate(_read(args.config)),
            )
        )
    else:
        run = BudgetedSearchRun(args.output)
        if args.command == "run":
            result = run.run(
                omnigent_python=args.omnigent_python, api_key=os.environ.get("OPENAI_API_KEY")
            )
        elif args.command == "final":
            result = run.final(
                heldout_manifest=args.heldout,
                output=args.private_out,
                omnigent_python=args.omnigent_python,
                api_key=os.environ.get("OPENAI_API_KEY"),
            )
        elif args.command == "recover-proposal":
            result = run.recover_proposal(args.iteration, reason=args.reason)
        elif args.command == "recover-case":
            result = run.recover_case(args.candidate, args.case, reason=args.reason)
        elif args.command == "recover-final":
            result = run.recover_final(reason=args.reason)
        else:
            result = getattr(run, args.command)()
    print(canonical_json(result))


if __name__ == "__main__":
    main()
