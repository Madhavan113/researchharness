# Research experiments

The experiment commands prepare inputs, check a benchmark and retain operator
curation decisions. They do not run a research agent or accept a finding.
See the [active goal](goals/reproducible-research.md).

## Prepare the example

Use Python 3.11+ for Research Harness, a separate Python 3.12 environment for
Harbor 0.23.0, and a running local Linux Docker daemon. No model key is needed.
The [example plan](../examples/experiments/meta-harness/plan.md) explains the
question, proposed baseline, infrastructure, task selection and adaptations.
Read it and the task's build/test scripts before executing them.

```sh
uv sync --locked
uv venv --python 3.12 .researchharness/runtimes/harbor
uv pip install --python .researchharness/runtimes/harbor/bin/python \
  -r examples/experiments/harbor-requirements.txt

git clone --filter=blob:none --sparse \
  https://github.com/harbor-framework/terminal-bench-2.git \
  .researchharness/terminal-bench-2
git -C .researchharness/terminal-bench-2 sparse-checkout set cancel-async-tasks
git -C .researchharness/terminal-bench-2 checkout 2fd12b88aafdd04a52c298e3940bcb189f9766d6

uv run rh experiment prepare examples/experiments/meta-harness/experiment.json \
  --checkout .researchharness/terminal-bench-2 \
  --out .researchharness/experiments/prepared
```

Preparation verifies the Git origin/revision and unchanged task files. It copies
the plan, upstream task and license, explicit overlays and effective task into
`inputs/`, then writes `prepared.json` with file hashes and resource settings.
Inspect `inputs/plan.md`, `inputs/upstream/`, `inputs/overlays/` and
`inputs/tasks/` to review exactly what would run. Preparation executes no task code.

## Check the evaluator

```sh
uv run rh experiment check .researchharness/experiments/prepared \
  --harbor .researchharness/runtimes/harbor/bin/harbor \
  --out .researchharness/experiments/check-1

uv run rh experiment status .researchharness/experiments/check-1
```

Use a new output directory for each check. Harbor runs its known reference
solution (`oracle`) and a no-op (`nop`) against the same task, one trial at a time.
The example expects rewards 1 and 0 respectively. Both must finish without an
infrastructure exception, use separate verifier containers, and retain matching
raw reward files. A successful process exit alone does not pass the check.

`check.json` records progress, commands and the result matrix. `harbor.log` and
`jobs/controls/` retain raw build, execution and verifier evidence. `files.json`
records terminal output hashes; `status` detects missing/changed artifacts and
never reruns work. An interrupted controller's `running` receipt does not prove
liveness. Inspect its recorded process and Docker resources before acting; there
is no automatic replay or durable recovery implementation yet.

## Curate the benchmark package

Inspect the question, plan, task resources and verified control results together:

```sh
uv run rh experiment review .researchharness/experiments/prepared \
  --check .researchharness/experiments/check-1 \
  --store .researchharness/operator/curation.sqlite3
```

This is read-only, even when the curator database does not exist. It returns
`pending_review`, the complete plan, control results, a `subject_sha256` binding
the input contents and check artifacts, and the current review `head` (`none`
initially). Read the underlying scripts and logs too; passing controls cannot
establish whether a research question or benchmark is useful.

After reviewing, a local operator can record a decision with the exact subject
and head from that inspection. Replace the placeholders yourself:

```sh
uv run rh experiment review .researchharness/experiments/prepared \
  --check .researchharness/experiments/check-1 \
  --store .researchharness/operator/curation.sqlite3 \
  --decision accept --subject REVIEWED_SUBJECT_SHA256 --after REVIEWED_HEAD \
  --reason 'Why this question, task selection and evaluator are suitable'

uv run rh experiment reviews meta-harness-coding-pilot \
  --store .researchharness/operator/curation.sqlite3
```

Use `--decision reject` to retain a rejection and its reason. Acceptance requires
matching, intact check artifacts and a recomputed positive/negative result matrix;
a success flag alone is insufficient. Both decisions require the inspected subject
and current head, so an edited package or a concurrent review forces another
inspection. Each experiment has one current decision; reviewing a new version
supersedes the previous decision while retaining its history. It never restores
an older acceptance merely because someone selects the older files again.

To withdraw an acceptance, use its review ID. Withdrawal still works if the
original evidence files are missing or damaged:

```sh
uv run rh experiment withdraw meta-harness-coding-pilot \
  --store .researchharness/operator/curation.sqlite3 \
  --after CURRENT_REVIEW_ID --reason 'Why this acceptance is withdrawn'
```

The SQLite log must stay outside prepared/check directories and future worker
mounts. The local OS account is the current trust boundary: the log records the
process UID where available, not a caller-supplied reviewer identity. It cannot
distinguish a person from automation with that same account. A networked curator
interface still needs authenticated principals and access controls; do not expose
these write methods as agent tools. A host writer can alter the database or hashes.

Benchmark acceptance has a narrow scope: it does **not** authorize model spending,
launch an experiment, accept a finding or change the proposal's saved metadata.
`CuratorStore.require_accepted()` revalidates inputs, raw results and the latest
decision for a future dispatcher. That prerequisite is implemented and tested;
the Omnigent dispatcher and execution budget gate are not connected yet. Withdrawal
does not cancel a running process. The CLI reports successful inspection/recording
with exit code 0 even for pending/rejected packages; dispatchers must use the
service prerequisite rather than interpreting that exit code as acceptance.

## What this establishes

This is benchmark validation on a real task, not a measured agent baseline,
paper reproduction result or proof against reward hacking. Input/output hashes
detect accidental edits; a person with host write access can replace the hashes.
These are local operator commands for inspected task packages, not an authorization
boundary for hostile task authors. Agent tools must not gain the same access.

Prepared/check receipts remain `pending_human_review` when checks pass; only the
separate curator log records an operator's decision. Humans still choose the
direction, benchmark, evaluation criteria, environment, model/budget and accepted
findings. Authenticated network curation, Omnigent delegation, computer-use evidence,
experiment planning tools and model-backed execution remain follow-up work.

The example preserves upstream test assertions and adds an explicit verifier
image, artifact transfer and pinned dependency setup. Networking uses the upstream
task's public mode; the local Docker host lacks the kernel support required by
Harbor's network-blocking implementation. Resource settings and this limitation
are visible in the plan. Do not describe the environment as network-isolated.

The first checker supports single-step local CPU tasks with binary `reward.txt`
output. Other metrics, GPU jobs and agent execution need further integration.

## Local verification, September 13, 2026

The actual Harbor 0.23.0/Docker run completed both controls in separate verifier
containers. The original six test assertions ran in each trial:

| Control | Passed tests | Failed tests | Skipped | Reward |
| --- | --- | --- | --- | --- |
| Reference solution (`oracle`) | 6 | 0 | 0 | 1 |
| No-op (`nop`) | 1 | 5 | 0 | 0 |

The check's input fingerprint is
`805685a345441fc8121b824634bcd93c887d895096b040094696fb5db6840e69`.
Local raw evidence is retained under
`.researchharness/experiments/checks/async-v4/`; its `check.json` SHA-256 is
`d6b0e9613d261c9738f30f3ce91ea97d4a3a93590af348c019437dd5e0b0ceee`.
`rh experiment status` verified the terminal artifact inventory without rerunning
the task. These local files are not published runtime assets; use the commands
above to generate your own run.

Earlier failed attempts are retained alongside this run: unavailable network
blocking, a missing separate-verifier image, and test files not copied into that
image. The successful overlay fixes setup while preserving upstream test
assertions. No model, research baseline, human acceptance or search improvement
was measured. E1 remains in progress until the curated baseline is runnable.
