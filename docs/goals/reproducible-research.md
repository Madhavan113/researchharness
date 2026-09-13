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
| E2 | Omnigent delegation, isolated attempts and durable execution artifacts | in_progress | `/root` |
| E3 | Candidate search, independent rerun and a reproducible finding | queued | unassigned |

After the pilot, connect investigation (including computer-use evidence),
ideation and infrastructure proposals to the same human curation and experiment
records. These capabilities remain required and unimplemented; completing E1–E3
alone does not establish the full active thread goal.

The [experiment guide](../experiments.md) supplies a proposed task, benchmark
controls, operator review and a real Omnigent/Docker execution fixture. Delegation
now connects to the existing model budget and independent verifier. The authored
fixture is not the planned Terminus-2 baseline or an editable agent program.
Next: implement the full candidate-program interface and a runnable research
baseline. Record provider access and a spending budget before any paid model run.

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

September 13, `/root`, E2 execution integration in progress on
`feat/experiment-execution`. Intended files: `experiments/` runtime/service code,
CLI, focused/runtime tests, experiment documentation and this tracker. Connect the
curation prerequisite to real Omnigent worker execution in a Harbor task environment;
retain attempts and link worker actions to independent verifier results. Use
scripted model responses to establish the runtime boundary before any paid run.
Do not route experiments through a fabricated source-discovery case, execute
candidate code on the host, expose curator writes to workers or claim model gains.
Preserve the current ingestion path and Claude's private UI work.

Implementation checkpoint: the trusted `experiments.execution.run` API rechecks
curation, binds a fixed model/budget to exact inputs, snapshots the harness source,
and starts real supervisor/worker sessions through the pinned Omnigent runtime.
Worker code runs through a container command endpoint; raw commands, submitted
artifacts, runtime identities/conversations, dependencies, actual Docker settings,
verifier output and model usage are retained. `rh experiment status` verifies the
exported inventory. Task reward 0 remains a valid completed negative result.

Runtime inspection found that Harbor 0.23.0 mounts verifier output in its task
container even with separate grading. `ExperimentDocker` removes all host mounts
and uses Docker `network_mode: none` for both roles. Image builds remain networked.
This runtime restriction is an explicit departure from the original check's public
networking and is recorded in the example plan and each execution. Omnigent also
auto-exposes management/browser tools: an inherited session policy restricts calls
to the execution interface, with deliberate denied calls in both parent and child
fixtures. Its inline format preserves the concurrent-worker limit; the native
directory parser at this pin drops it, while inline nested MCP servers are dropped.
The fixed connector function avoids both parser limitations.

The integration fixture and required Docker/Omnigent CI cover a scripted working
solution and an attempted forged reward, using a separate synthetic curator log and
zero-cost mock model transport. These establish execution and selected isolation
properties, not model quality, a human decision or a paper reproduction. Failed
setup attempts remain under `.researchharness/experiments/omnigent-fixture-*`.
Final local runtime controls are `omnigent-fixture-8` (solution, 6/6 assertions,
reward 1) and `omnigent-fixture-9` (forged reward, 1/6 assertions, reward 0).
Both retain seven scripted Responses calls, real parent/child sessions, zero-cost
reconciled fixture accounting, actual Docker `network_mode: none`/empty mounts and
verified terminal inventories. The [guide](../experiments.md#delegated-execution-controls)
records exact trial IDs and inventory hashes. Earlier attempts are preserved.
The final focused command passes 26 tests:
`RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --locked --extra mcp pytest -q tests/test_experiment_bundle.py tests/test_experiment_execution.py tests/test_experiment_workspace.py`.
Logs are `.researchharness/experiments/pytest-execution-focused-3.txt` and
`pytest-execution-runtime.txt`. Full-suite results and publication are recorded at
handoff; no paid provider calls have been made.

Remaining scope: a normal research run entry point, editable full agent programs,
the curated/measured baseline, candidate search, independent reruns and accepted
findings. Computer-use investigation, ideation and infrastructure proposals still
need to connect to the same curated records. This fixed coding fixture does not
replace those requirements. E1/E2 and the full goal remain in progress.

September 13, `/root`, E1 local curator decision step complete on
`feat/experiment-curation`. Scope: `experiments/curation.py`, benchmark evidence
validation, CLI, focused tests and experiment/goal documentation. Add an operator
review history bound to exact prepared inputs and check evidence, with withdrawal
and stale-review protection. Acceptance: a human can inspect and record a decision;
the service rejects missing, stale, withdrawn or altered evidence. Local OS account
attribution is not proof a human was present. This step does not authorize spending,
accept findings or claim that Omnigent execution is integrated.

Implementation checkpoint: `rh experiment review/reviews/withdraw` and
`CuratorStore.require_accepted()` now retain decisions in an operator SQLite log,
revalidate the raw controls and prevent stale or withdrawn acceptances from being
reused. Sixteen new curation test cases pass; the combined experiment/CLI tests pass
43 tests, including recovery after interrupted database initialization. Read-only
inspection of the actual `async-v4` Harbor evidence passes and leaves its state
pending, without creating a curator database or making a decision. The full
ordinary MCP-enabled run passed 1,466 tests with 32 optional runtime skips in
244.59 seconds. That run started before the final initialization guard; the final
43-test focused run covers that fix. Ruff lint/formatting, documentation links
(345 targets in 31 files), evidence-retention policy and `git diff --check` pass.
Logs are retained in `.researchharness/experiments/pytest-curation-*.txt`.

Next: use this prerequisite in a trusted Omnigent execution adapter with isolated
worker tools, a fixed task model, enforced run limits and retained attempt/session
identities. The existing `LocalOmnigent` wrapper is source-discovery-specific;
experiments must not be represented as fake discovery cases. Network curator
authentication, paid-run configuration, independent reruns and the full
investigation/planning workflow remain unfinished. E1 and the full goal stay active.

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
curation. Authenticated network curator decisions, Omnigent delegation, computer-use
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
