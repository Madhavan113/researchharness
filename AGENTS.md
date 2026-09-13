# Research Harness: shared agent instructions

## Shared goal

Read the [current research goal and work tracker](docs/goals/reproducible-research.md)
first: one AI research question, a runnable baseline, delegated experiments,
independent evaluation and a reproducible conclusion. Start with the bounded
Meta-Harness pilot. Prefer the smallest working experiment over a general framework.

For existing ingestion/integration work, also read its
[tracker](docs/goals/omnigent-integration.md), [plan](docs/omnigent-integration-plan.md),
[historical checkpoint](docs/checkpoint-scope.md) and [remaining work](docs/remaining-work.md).
Those records retain unfinished measurements; they do not override the current
priority. Update the relevant tracker when taking or finishing work.

Follow the current user's task and constraints. The shared goal supplies context; it does not expand an unrelated request or override later user instructions. Record accepted scope changes in the tracker and update the plan when the design changes.

## Coordination and handoff

- Inspect the working tree before editing. Preserve existing user and agent changes; do not reset, overwrite, or claim them as your work.
- Before starting a milestone or bounded task, record its owner, status, and intended files in the tracker. Use the actual agent/thread identifier when available, otherwise a descriptive session label.
- With concurrent agents, assign one owner for each task and coordinate edits to shared files. Give delegated work explicit file boundaries, dependencies, and a completion check. A tracker entry is coordination metadata, not a filesystem lock.
- Re-read the relevant tracker entry before updating it. Keep updates local to your task so another agent's progress is preserved.
- At handoff, record changed files, validation commands and results, unresolved decisions, and the next concrete action. Link artifacts where practical. Distinguish source review, fixture tests, and live runtime verification.
- Mark a milestone complete only when its acceptance criteria have evidence. Report blocked dependencies precisely and continue independent work where possible.
- Keep the README and plan's implementation-status statements consistent with the tracker as milestones land.

These instructions coordinate agents using this repository checkout. Other checkouts need the same goal and instruction files plus their latest updates; the documents do not automatically synchronize separate conversations or running agents.

## Implementation constraints

- Keep Research Harness authoritative for evidence, proposal validation, pipelines, and collection state. Omnigent supplies the conversational execution loop and runtime session state; its native UI is the integration pilot's interface, not a requirement for every product workflow.
- Treat the existing discovery agent and ProposalDraft contract as ingestion-specific. Do not require every research task to produce a pipeline or infer task completion from a runtime session. A general task/artifact abstraction still needs implementation; do not describe it as already shipped.
- Keep private application code, detailed product plans and design assets out of this public repository. Product scope corrections do not erase historical runtime evidence or complete unfinished evaluation gates.
- Keep the direct discovery CLI working through the same domain service as the MCP path. Use a separate environment for Omnigent.
- Load source evidence from stored search, inspection, and probe receipts. Agent assertions are not proof of observation or successful validation.
- Preserve immutable captures, publication cutoffs, writer locking, and retry/recovery semantics. Enforce operation limits in the service.
- Keep evaluation requirements independent of generated proposals. Exclude held-out data and evaluator internals from optimization candidates and search feedback.
- For experiments, keep authoritative scoring and result records outside candidate write access. Retain failed attempts, verify evaluator behavior against broken solutions, and link conclusions and reusable methods to reproducible evidence.
- Use offline fixtures for work that does not require a live provider. Before model-backed comparisons, establish and record the provider configuration and run budget described in the plan.

## Verification

Run checks appropriate to the change. The core commands are `uv run pytest`, `uv run ruff check src tests`, and `uv run ruff format --check src tests`. Service, MCP, migration, and recovery changes also need their milestone-specific acceptance checks. Record unavailable checks and pre-existing failures accurately.

For documentation-only work, check links, consistency, and the diff; do not claim runtime behavior was tested.
