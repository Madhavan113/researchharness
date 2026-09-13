# Goal: reproducible AI research

Accepted September 13, 2026. Status: in_progress. Owner: `/root`.

The active thread goal now explicitly includes human-guided curation,
coding and computer-use investigation, ideation/planning, and infrastructure
planning for a research idea. The pilot below is the first increment toward that
full workflow, not a replacement for it. Agents propose directions, evidence,
experiment plans and resources; people curate the benchmark and accepted findings.

**Take one AI research question through a runnable baseline, experiments and
independent evaluation. Deliver code and a conclusion another person can check
by rerunning the experiment. A negative result is a valid outcome.**

## First pilot

Build a bounded [Meta-Harness](https://arxiv.org/html/2603.28052v1#S3)
reproduction on real coding tasks. A proposer reads previous candidate programs,
scores and available execution traces, edits the task-specific agent program,
and delegates candidate execution through Omnigent. Candidates can change prompting,
retrieval, state and orchestration within the task interface. The evaluator and
resource controls remain outside the candidate's control. Keep the underlying task
model fixed during a comparison.

Record the chosen baseline, tasks, models, budgets and every departure from the
paper. A small working pilot does not reproduce the published performance claims.
The paper's TerminalBench experiment reuses its search tasks for final evaluation;
do not describe that protocol as held-out testing. Additional transfer tests need
their own untouched tasks and an explicitly different protocol.

## Done means

- The question, baseline, evaluation criteria, data/tasks and resource limits are
  versioned before comparing candidates. Known-working solutions pass and
  known-bad solutions fail the evaluator.
- Omnigent delegates real execution into isolated workspaces. Every attempt has
  recorded inputs, source revision, dependencies, commands, model settings,
  applicable seeds, outputs and usage; failures and interruptions remain visible.
- The worker cannot edit the authoritative evaluator or result records. Preserve
  the full attempt history and distinguish environment failures from task results.
  Observability and isolation reduce reward-hacking risk; they do not prove its absence.
- An independent run checks the selected result in a fresh environment. Report
  variability and limitations, link conclusions to artifacts, and provide the
  exact reproduction commands. Fixtures establish plumbing, not model performance.
- Retain reusable code with its evidence and limits. Keep paper claims, local
  observations and tested methods distinct; reuse does not imply untested transfer
  or a change to the model's weights.

## Small steps

| Step | Deliverable | Status | Owner |
| --- | --- | --- | --- |
| E1 | One versioned experiment package and a runnable baseline/evaluator | in_progress | `/root` |
| E2 | Omnigent delegation, isolated attempts and durable execution artifacts | queued | unassigned |
| E3 | Candidate search, independent rerun and a reproducible finding | queued | unassigned |

After the pilot, connect investigation (including computer-use evidence),
ideation and infrastructure proposals to the same human curation and experiment
records. These capabilities remain required and unimplemented; completing E1–E3
alone does not establish the full active thread goal.

The [experiment guide](../experiments.md) now supplies a proposed task, setup/run
commands and verified positive/negative benchmark controls. Next: connect human
curator decisions and the planned agent baseline to execution through Omnigent.
The research baseline itself remains unmeasured. Record provider access and a
spending budget before any paid model run, using existing controls where applicable.

## Keep the scope small

Reuse Omnigent for orchestration and existing ingestion/evidence components where
they fit. Defer finance/event products, a general software factory, and elaborate
memory infrastructure. Keep existing ingestion usable. Private application code
and design stay in their repository.

This is the current technical priority. The earlier
[ingestion integration track](omnigent-integration.md) retains its historical
evidence and unfinished measurements; those are not automatically the next tasks.
The app goal controller now reports the user's research-workflow goal active.
The earlier failed registration is historical; no old evaluation was marked complete.

## Current ownership

September 13, `/root`, E1 in progress on `feat/curated-experiments`: implement one
reviewable experiment package and benchmark validation using Harbor's existing
task/runtime format. Scope: `src/research_harness/experiments/`, CLI integration,
focused tests, an example package, experiment docs and this tracker. Inspect and
pin the runtime; retain actual positive/negative-control results without claiming
agent performance or human review. Preserve existing ingestion/optimization and
private UI code. Acceptance: executable benchmark check, observable artifacts,
honest curation state, meaningful failure-path tests and setup commands. Subsequent
agent execution, computer-use investigation and human acceptance remain explicit
follow-up work, not simulated completion.

Checkpoint: `rh experiment prepare/check/status` now snapshots the Git-pinned
task, plan, overlays and effective environment; runs Harbor 0.23.0 reference/no-op
controls; and preserves raw outputs and verified terminal inventories. The proposed
async-cancellation task is human-authored upstream. Actual local Docker verification
passes all six assertions for the reference and fails five for no-op, with rewards
1 and 0 and no runtime exceptions. Both verifiers are separate containers. Three
earlier setup failures are retained; the [guide](../experiments.md) gives commands,
hashes, infrastructure limits and the precise scope of that evidence.

Validation: 20 new experiment tests; the complete ordinary MCP-enabled suite passes
1,451 tests with 32 optional runtime skips in 261 seconds. Ruff check and formatting
pass. Those skips are not runtime acceptance; the new Harbor checks were executed
separately, while the older required Docker/Omnigent suite is left to hosted CI.
Logs remain under `.researchharness/experiments/`; source and setup are reviewable
without committing another large evidence archive. No paid model calls were made.

E1 stays in progress: the proposed Terminus-2/model baseline needs integration and
curation. Authenticated curator decisions, Omnigent delegation, computer-use
investigation, agent planning and infrastructure-planning tools remain required.
The plan currently records those choices for human review; it does not implement
agents that make or execute them. The full goal remains active.

Earlier checkpoint, before implementation: September 13, `/root`, goal
documentation complete. Files: this goal,
`AGENTS.md`, `README.md`, `docs/README.md` and the earlier tracker. All 341 local
Markdown targets in the 29 root/documentation files resolve; `git diff --check`
and a documentation-only scope check pass. The next implementation task is E1;
no experiment implementation or measured result is claimed. New tasks must record
their owner, files and completion evidence here before work begins.
