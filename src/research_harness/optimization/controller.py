"""Durable Meta-Harness search over code, with private final evaluation.

The host owns this controller and its executors. A proposer receives only an
inventoried development feedback snapshot and a new bounded workspace. Opening
a controller never starts a model, resumes uncertain work, or reads heldout data.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

from filelock import FileLock
from pydantic import ConfigDict, Field, StrictInt

from research_harness.config import StrictModel
from research_harness.evaluation.benchmark import load_benchmark
from research_harness.evaluation.controller import (
    CaseTask,
    ComparisonConfig,
    ExecutionArtifacts,
    prepare_comparison,
    record_interruption,
    run_case,
)
from research_harness.evaluation.gateway_usage import verify_gateway_usage
from research_harness.execution import GatewayBinding
from research_harness.optimization.archive import (
    ArchiveConfig,
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
from research_harness.optimization.evaluator import (
    ResearchDevelopmentEvaluator,
    _model_evidence,
    export_development,
)
from research_harness.optimization.leakage import audit_candidate, audit_inputs
from research_harness.optimization.proposer import ProposalTask, ProposerConfig, proposal_binding
from research_harness.optimization.workspace import WorkspaceConfig
from research_harness.strategies.config import StrategyBundle
from research_harness.strategies.context import project_context
from research_harness.strategies.session import StrategySession
from research_harness.util import canonical_json, digest, timestamp

TERMINAL_CANDIDATES = {"evaluated", "evaluation_failed", "invalid", "proposal_failed"}


def _record_audit(root: Path, identity: str, inputs: dict, raw: bytes, instructions: bytes) -> dict:
    report = audit_candidate(inputs, source=raw, instructions=instructions)
    relative = f"candidate-audits/{identity}.json"
    _save(root / relative, report)
    return {
        "leak_suspect": report["status"] == "leak_suspect",
        "leakage_audit": relative,
        "leakage_audit_sha256": digest((root / relative).read_bytes()),
    }


class SearchConfig(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
    arm: Literal["direct", "omnigent"] = "omnigent"
    comparison: ComparisonConfig = Field(default_factory=ComparisonConfig)
    proposer: ProposerConfig = Field(default_factory=ProposerConfig)
    workspace: WorkspaceConfig
    iterations: StrictInt = Field(default=3, ge=1, le=100)
    candidates_per_iteration: StrictInt = Field(default=2, ge=1, le=10)


def _contract(bundle: StrategyBundle) -> dict:
    """Public strategy interface, without independent research predicates."""
    return {
        "schema_version": 1,
        "entrypoint": "def apply(event): return {'decision': {...}, 'state': {...}}",
        "event": "JSON object with kind, payload, and prior state; no model or research-tool access",
        "observations": {
            "enabled": bundle.config.observations,
            "kind": "observation",
            "payload": "tool, authoritative observation, items [{id,value}]",
            "decision": "Optional order of supplied item IDs; rendered [{text,references}]; replace_body; stop_recommended; stop_reason",
            "constraints": "Only supplied IDs may be selected or referenced. Rendering is interpretation; original receipts remain authoritative. When finalize_on_stop is enabled, a verified stop prevents further discovery and requests a validated proposal from existing evidence.",
            "finalize_on_stop": bundle.config.finalize_on_stop,
        },
        "context": {
            "enabled": bundle.config.context,
            "kind": "context",
            "payload": "groups [{id,start,items,required}], required_group_ids, input_type and policy",
            "decision": "keep_group_ids in original order, including every required group",
            "constraints": "All user/system/developer items and the entire active turn are required; only older completed model/tool interactions may be removed.",
        },
        "frozen_limits": bundle.config.model_dump(mode="json", exclude={"source", "source_sha256"}),
        "files": "For each requested ID write candidates/<id>/strategy.py and instructions.md in the writable workspace. The host supplies the frozen manifest.",
    }


def _validate_interface(bundle: StrategyBundle, output: Path) -> None:
    """Exercise the public interface in Docker; never import candidate code here."""
    ast.parse(bundle.source.read_bytes())
    session = StrategySession(output, bundle)
    if bundle.config.observations:
        session.project(
            "search_sources",
            {
                "operation_id": "interface-search",
                "discovery_id": "public-interface",
                "receipt_id": "public-interface-receipt",
                "results": [{"url": "https://interface.example/notice", "title": "Notice"}],
            },
        )
    if bundle.config.context:
        project_context(
            session,
            {
                "input": [
                    {"role": "user", "content": "Public interface example"},
                    {"role": "assistant", "content": "Completed example"},
                    {"role": "user", "content": "Continue the public example"},
                ]
            },
            operation_id="interface-context",
        )
    session.assert_ready()


class SearchController:
    def __init__(self, root: Path):
        self.root = _absolute(root)
        self.lock = FileLock(str(self.root) + ".lock", timeout=0)
        with self.lock:
            self._load()

    @classmethod
    def create(
        cls,
        root: Path,
        feedback: Path,
        *,
        development_manifest: Path,
        baseline: StrategyBundle,
        instructions: str,
        config: SearchConfig,
    ) -> SearchController:
        """Freeze the real baseline and controls; preparation makes no model calls."""
        root, feedback = _absolute(root), _absolute(feedback)
        _disjoint(root, feedback)
        config = SearchConfig.model_validate(config)
        benchmark = load_benchmark(development_manifest)
        if benchmark.manifest.split != "development":
            raise ValueError("Search accepts development cases only")
        if config.comparison.execution == "model" and any(
            case.review_status != "reviewed" for case in benchmark.cases.values()
        ):
            raise ValueError("Model search requires independently reviewed development cases")
        if config.comparison.execution == "model" and config.proposer.budget_control is None:
            raise ValueError("Model search requires frozen proposer budget controls")
        if not instructions.strip():
            raise ValueError("Baseline instructions must not be empty")
        leakage_inputs = audit_inputs(benchmark)
        baseline = StrategyBundle.load(baseline.manifest_path)
        _disjoint(root, baseline.manifest_path.parent)
        _disjoint(root, _absolute(development_manifest).parent)
        if root.exists() or feedback.exists():
            raise ValueError("Search and feedback directories must be new")
        root.mkdir(parents=True)
        frozen = baseline.freeze(root / "seed")
        _write_file(root / "seed/instructions.md", instructions.encode())
        _save(root / "leakage-audit-inputs.json", leakage_inputs)
        baseline_audit = _record_audit(
            root, "baseline", leakage_inputs, frozen.source.read_bytes(), instructions.encode()
        )
        comparison = root / "comparisons/baseline"
        prepare_comparison(
            development_manifest,
            comparison,
            instructions=instructions,
            config=config.comparison,
            strategy=frozen,
        )
        prepared = _read(comparison / "comparison.json")
        fixed = {
            key: value
            for key, value in prepared["controls"][config.arm].items()
            if key not in {"strategy_sha256", "code_strategy_sha256"}
        }
        fixed["sandbox"] = frozen.config.sandbox.model_dump(mode="json")
        fixed["strategy_limits"] = frozen.config.model_dump(
            mode="json", exclude={"source", "source_sha256", "sandbox"}
        )
        archive_config = ArchiveConfig(
            run_id=config.run_id, execution=config.comparison.execution, fixed_controls=fixed
        )
        OptimizationArchive.create(
            root / "archive",
            feedback,
            config=archive_config,
            frozen_inputs={
                "development_manifest": comparison / "benchmark/manifest.json",
                "backend": comparison / "implementation",
                "evaluator": Path(__file__).with_name("evaluator.py"),
            },
        )
        plan = {
            "schema_version": 1,
            "created_at": timestamp(),
            "config": config.model_dump(mode="json"),
            "archive_config": archive_config.model_dump(mode="json"),
            "benchmark_sha256": benchmark.sha256,
            "leakage_audit_inputs_sha256": digest(
                (root / "leakage-audit-inputs.json").read_bytes()
            ),
            "seed_files": _inventory(root / "seed", archive_config),
            "strategy_contract": _contract(frozen),
        }
        _save(root / "search.json", plan)
        _save(
            root / "journal.json",
            {
                "schema_version": 1,
                "search_sha256": digest(canonical_json(plan)),
                "phase": "search",
                "proposals": {},
                "candidates": {
                    "baseline": {
                        **baseline_audit,
                        "status": "prepared",
                        "source": "seed",
                        "manifest": "seed/strategy.json",
                        "instructions": "seed/instructions.md",
                        "comparison": "comparisons/baseline",
                    }
                },
            },
        )
        return cls(root)

    @property
    def archive(self) -> OptimizationArchive:
        return OptimizationArchive(self.root / "archive")

    def _load(self) -> tuple[dict, dict, SearchConfig, ArchiveConfig]:
        plan, journal = _read(self.root / "search.json"), _read(self.root / "journal.json")
        config = SearchConfig.model_validate(plan["config"])
        limits = ArchiveConfig.model_validate(plan["archive_config"])
        if journal.get("schema_version") != 1 or journal.get("search_sha256") != digest(
            canonical_json(plan)
        ):
            raise ValueError("Search journal differs from its frozen plan")
        archived = _read(self.root / "archive/archive.json")
        if (
            archived["config"] != limits.model_dump(mode="json")
            or archived["benchmark_sha256"] != plan["benchmark_sha256"]
            or _inventory(self.root / "seed", limits) != plan["seed_files"]
        ):
            raise ValueError("Frozen search inputs changed")
        for proposal in journal["proposals"].values():
            for path_key, files_key in (("feedback", "feedback_files"), ("output", "files")):
                if (
                    files_key in proposal
                    and _inventory(self.root / _relative(proposal[path_key]), limits)
                    != proposal[files_key]
                ):
                    raise ValueError("Immutable proposal input or output changed")
        if (
            "leakage_audit_inputs_sha256" in plan
            and digest((self.root / "leakage-audit-inputs.json").read_bytes())
            != plan["leakage_audit_inputs_sha256"]
        ):
            raise ValueError("Frozen leakage audit inputs changed")
        for candidate in journal["candidates"].values():
            if (
                "leakage_audit" in candidate
                and digest((self.root / _relative(candidate["leakage_audit"])).read_bytes())
                != candidate["leakage_audit_sha256"]
            ):
                raise ValueError("Candidate leakage audit changed")
            if (
                "attempt_files" in candidate
                and _inventory(self.root / _relative(candidate["attempt"]), limits)
                != candidate["attempt_files"]
            ):
                raise ValueError("Candidate preparation or validation artifacts changed")
            if (
                "recovery_sha256" in candidate
                and digest((self.root / _relative(candidate["recovery_file"])).read_bytes())
                != candidate["recovery_sha256"]
            ):
                raise ValueError("Candidate recovery receipt changed")
        self.archive.status()
        return plan, journal, config, limits

    def _save(self, journal: dict) -> None:
        _save(self.root / "journal.json", journal)

    def status(self) -> dict:
        with self.lock:
            _, journal, _, _ = self._load()
            return {**journal, "archive": self.archive.status()}

    def _baseline_ready(self, limits: ArchiveConfig) -> bool:
        state = self.archive.status()
        return OptimizationArchive._baseline_ready(state["candidates"].get("baseline"), limits)

    def _evaluate(self, candidate_id: str, journal: dict, config: SearchConfig, limits, executor):
        candidate = journal["candidates"][candidate_id]
        if candidate["status"] in {"validating", "preparing"}:
            raise ValueError("Candidate preparation is unresolved; recover it before continuing")
        bundle = StrategyBundle.load(self.root / candidate["manifest"])
        if not candidate.get("interface_validated"):
            candidate["status"] = "validating"
            self._save(journal)
            try:
                _validate_interface(bundle, self.root / "interfaces" / candidate_id)
            except Exception as exc:
                candidate.update(status="invalid", error=f"{type(exc).__name__}: {exc}")
                self._save(journal)
                return {"candidate_id": candidate_id, "status": "invalid"}
            candidate.update(status="prepared", interface_validated=True)
            self._save(journal)
        instructions = (self.root / candidate["instructions"]).read_text()
        if "comparison" not in candidate:
            candidate.update(status="preparing", comparison=f"comparisons/{candidate_id}")
            self._save(journal)
            prepare_comparison(
                self.root / "comparisons/baseline/benchmark/manifest.json",
                self.root / candidate["comparison"],
                instructions=instructions,
                config=config.comparison,
                strategy=bundle,
            )
            candidate["status"] = "prepared"
            self._save(journal)
        registered = self.archive.register_candidate(
            candidate_id,
            self.root / candidate["source"],
            instructions=instructions,
            manifest={"schema_version": 1, "strategy_manifest": "strategy.json"},
            operation_id=f"register:{candidate_id}",
        )
        candidate.update(status="evaluating", candidate_sha256=registered["candidate_sha256"])
        self._save(journal)
        comparison = self.root / candidate["comparison"]
        for case_id in _read(comparison / "comparison.json")["case_ids"]:
            run_case(comparison, config.arm, case_id, executor)
        if "export" not in candidate:
            # A failed copy can be repeated into a fresh directory without any
            # provider dispatch. Preserve every older partial export on disk.
            target = f"exports/{candidate_id}-{uuid4().hex}"
            candidate.setdefault("export_attempts", []).append(target)
            self._save(journal)
            export_development(comparison, config.arm, self.root / target)
            candidate["export"] = target
            self._save(journal)
        result = self.archive.record_development(
            candidate_id,
            self.root / candidate["export"],
            evaluator=ResearchDevelopmentEvaluator(limits.fixed_controls),
            operation_id=f"evaluate:{candidate_id}",
        )
        candidate["status"] = result["status"]
        self._save(journal)
        return result

    def _snapshot(self, iteration: int, journal: dict, limits: ArchiveConfig) -> Path:
        source = self.archive.feedback_path()
        target = self.root / "proposal-inputs" / f"iteration-{iteration:04d}-{uuid4().hex}"
        _copy(source, target, _inventory(source, limits), limits)
        for identity, proposal in journal["proposals"].items():
            if proposal.get("closed") is not True or proposal.get("quiescent") is not True:
                raise ValueError("Close every prior proposer before exposing another snapshot")
            _copy(
                self.root / proposal["output"],
                target / "proposal-attempts" / identity,
                proposal["files"],
                limits,
            )
        for identity, candidate in journal["candidates"].items():
            if "leakage_audit" in candidate:
                relative = _relative(candidate["leakage_audit"])
                _write_file(target / relative, (self.root / relative).read_bytes())
            if "attempt_files" in candidate:
                _copy(
                    self.root / candidate["attempt"],
                    target / "candidate-attempts" / identity,
                    candidate["attempt_files"],
                    limits,
                )
            if "recovery_sha256" in candidate:
                _write_file(
                    target / "recovery-receipts" / f"{identity}.json",
                    (self.root / candidate["recovery_file"]).read_bytes(),
                )
            interface = self.root / "interfaces" / identity
            if interface.exists():
                _copy(
                    interface,
                    target / "interface-attempts" / identity,
                    _inventory(interface, limits),
                    limits,
                )
        return target

    def _task(self, proposal: dict, plan: dict) -> ProposalTask:
        return ProposalTask(
            candidate_ids=tuple(proposal["candidate_ids"]),
            iteration=proposal["iteration"],
            feedback_dir=self.root / proposal["feedback"],
            output=self.root / proposal["output"],
            instructions=(
                "Improve this research harness through executable Python strategies and agent instructions. "
                "Explore prior candidate code, independent development scores and complete available raw traces "
                "in the supplied filesystem. Choose which experience to inspect and how to derive new algorithms. "
                "Aim to improve source-selection quality and token efficiency under the frozen controls. "
                "Write every requested candidate and use the explicit submission tool. All available prior "
                "development files may be read; fixture evidence describes software behavior, not model quality."
            ),
            strategy_contract=plan["strategy_contract"],
            execution_id=proposal["execution_id"],
        )

    @staticmethod
    def _check_proposer(proposer, config):
        if proposer is None:
            raise ValueError("Supply the configured coding proposer for the next iteration")
        if proposer.config.model_dump(mode="json") != config.proposer.model_dump(
            mode="json"
        ) or proposer.workspace_config.model_dump(mode="json") != config.workspace.model_dump(
            mode="json"
        ):
            raise ValueError("Proposer settings differ from frozen search controls")

    def _reserved_task(self, proposal, plan, config):
        task = self._task(proposal, plan)
        binding = GatewayBinding.model_validate(proposal["binding"])
        if proposal_binding(task, config.proposer) != binding:
            raise ValueError("Proposer task differs from its reserved binding")
        return task, binding

    def _proposal_evidence(self, task, binding, config, metadata):
        if _read(task.output / "task.json").get("binding") != binding.model_dump(mode="json"):
            raise ValueError("Proposer task artifact differs from its reserved binding")
        access = task.output / "gateway-access.json"
        if not access.exists():
            if config.comparison.execution != "fixture" or metadata.get("fixture") is not True:
                raise ValueError("Proposer gateway evidence is required")
            return None
        gateway = _absolute(task.output / _relative(_read(access)["directory"]))
        usage = verify_gateway_usage(
            gateway,
            binding,
            expected_model=config.proposer.model,
            expected_model_settings=config.proposer.settings.model_settings(),
            expected_budgets=config.proposer.settings.budgets(),
            expected_budget_control=config.proposer.budget_control,
        )
        if usage["status"] == "invalid":
            raise ValueError("Proposer gateway evidence failed independent verification")
        if config.comparison.execution == "model" and not _model_evidence(
            SimpleNamespace(execution="model", budget_control=config.proposer.budget_control),
            usage,
        ):
            raise ValueError("Model proposer requires verified production dispatch and settlement")
        return usage

    def _propose(self, iteration, plan, journal, config, limits, proposer):
        self._check_proposer(proposer, config)
        identity = f"iteration-{iteration:04d}"
        if identity in journal["proposals"]:
            raise ValueError("Proposal attempt is unresolved; do not dispatch it again")
        feedback = self._snapshot(iteration, journal, limits)
        ids = [
            f"candidate-{iteration:04d}-{i:02d}"
            for i in range(1, config.candidates_per_iteration + 1)
        ]
        proposal = {
            "iteration": iteration,
            "candidate_ids": ids,
            "status": "running",
            "execution_id": uuid4().hex,
            "feedback": feedback.relative_to(self.root).as_posix(),
            "feedback_files": _inventory(feedback, limits),
            "output": f"proposals/{identity}",
            "closed": False,
            "quiescent": False,
        }
        task = self._task(proposal, plan)
        binding = proposal_binding(task, config.proposer)
        proposal["binding"] = binding.model_dump(mode="json")
        journal["proposals"][identity] = proposal
        self._save(journal)
        result = None
        try:
            result = proposer.propose(task)
            if not result.closed or not result.quiescent:
                raise ValueError("Proposer returned without closing its execution capabilities")
            if set(result.candidates) != set(ids):
                raise ValueError("Proposer must account for exactly the requested candidate slots")
            if _absolute(result.artifacts) != self.root / proposal["output"]:
                raise ValueError("Proposer returned foreign artifacts")
            proposal["gateway_usage"] = self._proposal_evidence(
                task, binding, config, result.metadata
            )
            proposal.update(status="completed", closed=True, quiescent=True)
        except BaseException as exc:
            proposal.update(
                status="unresolved",
                error=f"{type(exc).__name__}: {exc}",
            )
            self._save(journal)
            # Explicit recovery records closure and preserves unknown usage. A
            # new iteration can follow recovery; this execution is never replayed.
            raise
        if _inventory(feedback, limits) != proposal["feedback_files"]:
            proposal.update(status="unresolved", error="Proposer feedback changed")
            self._save(journal)
            raise ValueError("Proposer changed the development snapshot")
        proposal["files"] = _inventory(self.root / proposal["output"], limits)
        proposal["metadata"] = result.metadata
        # Store the completed proposal and every slot before candidate admission.
        proposal["candidate_files"] = {
            identity: {
                "source": _absolute(files.source)
                .relative_to(self.root / proposal["output"])
                .as_posix(),
                "instructions": _absolute(files.instructions)
                .relative_to(self.root / proposal["output"])
                .as_posix(),
            }
            for identity, files in result.candidates.items()
        }
        self._save(journal)
        return self._admit(proposal, journal, limits)

    def _admit(self, proposal: dict, journal: dict, limits: ArchiveConfig) -> dict:
        for identity in proposal["candidate_ids"]:
            if identity in journal["candidates"]:
                continue
            attempt = self.root / "candidate-attempts" / identity
            candidate = {
                "status": "preparing",
                "attempt": attempt.relative_to(self.root).as_posix(),
            }
            journal["candidates"][identity] = candidate
            self._save(journal)
            attempt.mkdir(parents=True, exist_ok=False)
            try:
                supplied = proposal["candidate_files"][identity]
                root = self.root / proposal["output"]
                raw = _absolute(root / _relative(supplied["source"])).read_bytes()
                instructions = _absolute(root / _relative(supplied["instructions"])).read_bytes()
                seed = StrategyBundle.load(self.root / "seed/strategy.json")
                if len(raw) > seed.config.sandbox.max_source_bytes:
                    raise ValueError("Candidate source exceeds frozen limit")
                if not instructions.decode().strip() or len(instructions) > 1024 * 1024:
                    raise ValueError("Candidate instructions must be nonempty bounded UTF-8")
                source = attempt / "source"
                _write_file(source / "strategy.py", raw)
                _write_file(attempt / "instructions.md", instructions)
                manifest = seed.config.model_dump(mode="json")
                manifest.update(source="strategy.py", source_sha256=digest(raw))
                _save(source / "strategy.json", manifest)
                StrategyBundle.load(source / "strategy.json")
                if "leakage_audit_inputs_sha256" in _read(self.root / "search.json"):
                    candidate.update(
                        _record_audit(
                            self.root,
                            identity,
                            _read(self.root / "leakage-audit-inputs.json"),
                            raw,
                            instructions,
                        )
                    )
                ast.parse(raw)
                candidate.update(
                    status="prepared",
                    source=source.relative_to(self.root).as_posix(),
                    manifest=(source / "strategy.json").relative_to(self.root).as_posix(),
                    instructions=(attempt / "instructions.md").relative_to(self.root).as_posix(),
                )
            except Exception as exc:
                candidate.update(status="invalid", error=f"{type(exc).__name__}: {exc}")
            _save(attempt / "admission.json", {"candidate_id": identity, **candidate})
            candidate["attempt_files"] = _inventory(attempt, limits)
            self._save(journal)
        proposal["admitted"] = True
        self._save(journal)
        return {
            "iteration": proposal["iteration"],
            "status": "proposed",
            "candidate_ids": proposal["candidate_ids"],
        }

    def step(self, *, executor: Callable[[CaseTask], ExecutionArtifacts], proposer=None) -> dict:
        """Advance one durable candidate/iteration; never replay uncertain work."""
        with self.lock:
            plan, journal, config, limits = self._load()
            if journal["phase"] != "search":
                return {"status": journal["phase"], "selection": self.archive.status()["selection"]}
            for proposal in journal["proposals"].values():
                if proposal["status"] in {"running", "unresolved"}:
                    raise ValueError("Proposal attempt requires explicit recovery")
                if proposal["status"] == "completed" and not proposal.get("admitted"):
                    return self._admit(proposal, journal, limits)
            for identity, candidate in journal["candidates"].items():
                if candidate["status"] not in TERMINAL_CANDIDATES:
                    return self._evaluate(identity, journal, config, limits, executor)
            if not self._baseline_ready(limits):
                raise ValueError(
                    "Independently measured baseline is not eligible; search cannot proceed"
                )
            iteration = len(journal["proposals"]) + 1
            if iteration <= config.iterations:
                return self._propose(iteration, plan, journal, config, limits, proposer)
            return self._select(journal, proposer=proposer)

    def run(self, *, executor, proposer) -> dict:
        while True:
            result = self.step(executor=executor, proposer=proposer)
            if result.get("status") == "selected":
                return self.status()
            if result.get("status") in {"finalized", "final_started"}:
                return self.status()

    def _select(self, journal: dict, *, proposer=None) -> dict:
        if journal["phase"] != "search":
            return {"status": journal["phase"], "selection": self.archive.status()["selection"]}
        self._quiescent(journal)
        if any(c["status"] not in TERMINAL_CANDIDATES for c in journal["candidates"].values()):
            raise ValueError("Resolve every candidate before selection")
        if "proposer_revocation" not in journal:
            if proposer is None:
                raise ValueError("Close the configured proposer before freezing selection")
            proof = proposer.close()
            if proof.get("closed") is not True or proof.get("quiescent") is not True:
                raise ValueError("Proposer capability revocation is incomplete")
            journal["proposer_revocation"] = proof
            self._save(journal)
        result = self.archive.select(operation_id="select-search")
        journal.update(phase="selected", selected_at=timestamp(), selection=result)
        self._save(journal)
        return {"status": "selected", "selection": result}

    @staticmethod
    def _quiescent(journal):
        if any(
            p.get("closed") is not True or p.get("quiescent") is not True
            for p in journal["proposals"].values()
        ):
            raise ValueError("Revoke all proposer execution capabilities before final selection")

    def select(self, *, proposer=None) -> dict:
        with self.lock:
            _, journal, config, _ = self._load()
            if journal["phase"] == "search" and len(journal["proposals"]) != config.iterations:
                raise ValueError("Complete the configured search iterations before selection")
            return self._select(journal, proposer=proposer)

    def recover_proposal(self, iteration: int, *, proposer, reason: str) -> dict:
        """Close an abandoned proposer, retain artifacts, and consume its slots."""
        if not reason.strip():
            raise ValueError("Record the observed interruption reason")
        with self.lock:
            plan, journal, config, limits = self._load()
            proposal = journal["proposals"][f"iteration-{iteration:04d}"]
            if proposal["status"] not in {"running", "unresolved"}:
                return proposal
            self._check_proposer(proposer, config)
            task, binding = self._reserved_task(proposal, plan, config)
            proof = proposer.recover(task, reason=reason)
            if proof.get("binding") != binding.model_dump(mode="json"):
                raise ValueError("Proposer recovery differs from its reserved binding")
            if proof.get("closed") is not True or proof.get("quiescent") is not True:
                raise ValueError("Recovery has not closed all proposer capabilities")
            proposal.update(
                status="failed",
                closed=True,
                quiescent=True,
                recovery=proof,
                files=_inventory(self.root / proposal["output"], limits),
            )
            for identity in proposal["candidate_ids"]:
                journal["candidates"].setdefault(
                    identity, {"status": "proposal_failed", "error": reason}
                )
            self._save(journal)
            return proposal

    def recover_case(
        self, candidate_id: str, case_id: str, *, reason: str, artifacts=None, artifact_loader=None
    ) -> dict:
        with self.lock:
            _, journal, config, _ = self._load()
            candidate = journal["candidates"][candidate_id]
            return record_interruption(
                self.root / candidate["comparison"],
                config.arm,
                case_id,
                reason=reason,
                artifacts=artifacts,
                artifact_loader=artifact_loader,
            )

    def recover_candidate(self, candidate_id: str, *, reason: str) -> dict:
        """Close an interrupted preparation/interface attempt without execution."""
        if not reason.strip():
            raise ValueError("Record the observed interruption reason")
        with self.lock:
            _, journal, _, limits = self._load()
            candidate = journal["candidates"][candidate_id]
            if candidate.get("recovery") == "no_execution_replay":
                if candidate["error"] != reason:
                    raise ValueError("Recovery reason differs from its recorded receipt")
                return candidate
            if candidate["status"] not in {"preparing", "validating"}:
                raise ValueError("Candidate is not in an interrupted preparation phase")
            intent = {"reason": reason, "file": f"candidate-recoveries/{candidate_id}.json"}
            if candidate.get("recovery_intent", intent) != intent:
                raise ValueError("Recovery request differs from its recorded intent")
            candidate["recovery_intent"] = intent
            self._save(journal)
            interface = self.root / "interfaces" / candidate_id
            if (interface / "session.json").exists():
                StrategySession.open(interface).recover()
            receipt = self.root / intent["file"]
            value = {"reason": reason, "reexecuted": False}
            if receipt.exists():
                if _read(receipt) != value:
                    raise ValueError("Candidate recovery receipt changed")
            else:
                _save(receipt, value)
            candidate.update(status="invalid", error=reason, recovery="no_execution_replay")
            candidate.update(
                recovery_file=intent["file"], recovery_sha256=digest(receipt.read_bytes())
            )
            if "attempt" in candidate:
                attempt = self.root / candidate["attempt"]
                if attempt.is_dir():
                    candidate["attempt_files"] = _inventory(attempt, limits)
            self._save(journal)
            return candidate

    def final(
        self, *, heldout_manifest: Path, output: Path, executor, operation_id="final"
    ) -> dict:
        """First heldout read is inside the private evaluator, after revocation."""
        from research_harness.optimization.final import evaluate_final

        with self.lock:
            _, journal, config, _ = self._load()
            self._quiescent(journal)
            if journal.get("proposer_revocation", {}).get("closed") is not True:
                raise ValueError("Final evaluation requires permanent proposer revocation")
            if journal["phase"] not in {"selected", "final_started", "finalized"}:
                raise ValueError("Finish search and freeze selection before final evaluation")
            if journal["phase"] == "selected":
                self.archive.validate_final_selection()
            terminal = journal["phase"] == "finalized"
            if not terminal:
                journal["phase"] = "final_started"
                self._save(journal)
            result = evaluate_final(
                self.archive,
                heldout_manifest=heldout_manifest,
                output=output,
                comparison_config=config.comparison,
                arm=config.arm,
                executor=executor,
                operation_id=operation_id,
            )
            if not terminal:
                journal.update(phase="finalized", final=result)
                self._save(journal)
            return result

    def recover_final(self, *, reason: str, operation_id="final") -> dict:
        from research_harness.optimization.final import recover_final

        with self.lock:
            _, journal, _, _ = self._load()
            self._quiescent(journal)
            result = recover_final(self.archive, operation_id=operation_id, reason=reason)
            journal.update(phase="finalized", final=result)
            self._save(journal)
            return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("output", type=Path)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("development_manifest", type=Path)
    prepare.add_argument("--baseline", type=Path, required=True)
    prepare.add_argument("--instructions", type=Path, required=True)
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    prepare.add_argument("--feedback", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "status":
        result = SearchController(args.output).status()
    else:
        result = SearchController.create(
            args.out,
            args.feedback,
            development_manifest=args.development_manifest,
            baseline=StrategyBundle.load(args.baseline),
            instructions=args.instructions.read_text(),
            config=SearchConfig.model_validate(_read(args.config)),
        ).status()
    print(canonical_json(result))


if __name__ == "__main__":
    main()
