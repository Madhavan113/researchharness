# Goal: reproducible AI research

Accepted September 13, 2026. Status: in_progress. Owner: `/root`.

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
| E1 | One versioned experiment package and a runnable baseline/evaluator | queued | unassigned |
| E2 | Omnigent delegation, isolated attempts and durable execution artifacts | queued | unassigned |
| E3 | Candidate search, independent rerun and a reproducible finding | queued | unassigned |

Next: select one baseline and a small task set, write their setup/run/score
commands, and prove the evaluator distinguishes working and broken solutions.
This defines the first execution contract; avoid designing a universal task
framework before that example works. Record provider access and a spending budget
before any paid model run, using the existing controls where applicable.

## Keep the scope small

Reuse Omnigent for orchestration and existing ingestion/evidence components where
they fit. Defer finance/event products, a general software factory, and elaborate
memory infrastructure. Keep existing ingestion usable. Private application code
and design stay in their repository.

This is the current technical priority. The earlier
[ingestion integration track](omnigent-integration.md) retains its historical
evidence and unfinished measurements; those are not automatically the next tasks.
This file records the accepted goal, not successful activation of the app's goal
controller, which still holds the older paused, unfinished goal.

## Current ownership

September 13, `/root`: goal documentation complete. Files: this goal,
`AGENTS.md`, `README.md`, `docs/README.md` and the earlier tracker. All 341 local
Markdown targets in the 29 root/documentation files resolve; `git diff --check`
and a documentation-only scope check pass. The next implementation task is E1;
no experiment implementation or measured result is claimed. New tasks must record
their owner, files and completion evidence here before work begins.
