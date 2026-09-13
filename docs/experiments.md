# Research experiments

Prepare a question and benchmark, review it, delegate work into Docker and inspect
the independent score. CLI commands cover preparation, checks, curation and status;
a trusted Python API connects execution to Omnigent and the existing model budget.
The end-to-end examples use scripted responses. No research baseline or finding
has been measured or accepted.
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
decision before the Omnigent execution adapter starts. It also requires an explicit
`DispatchBudget` bound to this experiment's ID and input fingerprint. Withdrawal
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
findings. Authenticated network curation, computer-use evidence, experiment planning,
full agent-program search and measured model-backed execution remain follow-up work.

The example preserves upstream test assertions and adds an explicit verifier
image, artifact transfer and pinned dependency setup. The original benchmark
check uses upstream public networking. Delegated execution additionally overrides
runtime networking to Docker's `none` mode in both roles, recorded with each run;
the images contain the required dependencies. This avoids Harbor's nftables-based
egress sidecar, which the local Docker host could not run. Image builds still use
networking. See the [plan](../examples/experiments/meta-harness/plan.md).

The first execution adapter supports single-step local CPU tasks with binary
`reward.txt` output. Other metrics, GPU jobs and candidate-program search need
further integration.

## Try delegated execution without a model key

After installing Harbor and checking out the task as above, install the pinned
Omnigent environment separately:

```sh
sh examples/omnigent/setup.sh "$PWD/.researchharness/runtimes/omnigent"

uv run --locked --extra mcp python examples/experiments/omnigent_runtime_fixture.py \
  --checkout .researchharness/terminal-bench-2 \
  --out .researchharness/experiments/delegation-1 \
  --harbor .researchharness/runtimes/harbor/bin/harbor \
  --omnigent-python .researchharness/runtimes/omnigent/.venv/bin/python

uv run --locked --extra mcp python examples/experiments/omnigent_runtime_fixture.py \
  --fixture-inputs .researchharness/experiments/delegation-1 \
  --out .researchharness/experiments/forged-reward-1 --forged-reward \
  --harbor .researchharness/runtimes/harbor/bin/harbor \
  --omnigent-python .researchharness/runtimes/omnigent/.venv/bin/python

uv run rh experiment status .researchharness/experiments/delegation-1/execution
uv run rh experiment status .researchharness/experiments/forged-reward-1/execution
```

Use new output directories for every attempt. The first script prepares a clearly
labelled test package, checks reference/no-op solutions, and records a synthetic
decision in its own test-operator database. It does not accept the proposed research
pilot on a person's behalf. Model responses come from `httpx.MockTransport`;
Omnigent, its supervisor/worker sessions, Docker commands and Harbor's verifier
are real. The first worker writes a known solution (expected reward 1); the second
writes fake rewards without solving the task (expected independent reward 0).
Both deliberately attempt forbidden tools in the parent and child sessions.

`completed` means the execution protocol finished with verified evidence. A
completed task can score **0**. A session ending or a process exiting successfully
does not establish a passing benchmark, an accepted finding or model improvement.

For integration code, use
[`experiments.execution.run`](../src/research_harness/experiments/execution.py)
with prepared inputs, check artifacts, a `CuratorStore`, an explicitly configured
`DispatchBudget`, separate runtime paths and a fresh output directory. The budget
binding uses the experiment ID, prepared input SHA, phase `workflow` and runtime
`omnigent-experiment`. It reuses the [existing provider controls](pilot-budget.md);
provider access, current pricing and spending authorization must be established
separately. No provider is chosen implicitly. The reviewed provider policy does
not yet cover Astra/max. There is no general `rh experiment run` command yet.

## What a run retains and restricts

| Evidence | Location inside an execution directory |
| --- | --- |
| Curated input/review binding, model settings, limits, score and usage | `execution.json`, `review.json` |
| Frozen harness Python source, available project lockfiles and hashes | `source/`, `source-sha256.json` |
| Actual Python/package versions for controller, Harbor and Omnigent | `dependencies.json`, per-trial dependency records |
| Supervisor/worker IDs, exported conversation items, events and tool policy | `controller/<trial-id>/runtime/` |
| Container command inputs, timestamps, exit codes and bounded output | `controller/<trial-id>/commands/` |
| Actual image/container IDs, mounts, privilege, CPU and memory settings | `controller/environment-*.json` |
| Submitted artifacts, raw verifier results and build/runtime logs | `jobs/experiment/`, `harbor.log` |
| Model request/response records, usage and budget reconciliation | `gateway/` |

`files.json` seals exported evidence; status detects changes without replaying a
run. Source imports do not write bytecode into the frozen snapshot. Failed attempts
remain separate. Runtime databases/configuration and the private loopback connection
file are retained locally but excluded from exported evidence. Hosted runtime CI
also runs both fixtures and keeps exported diagnostics for 14 days.

The adapter removes **all host mounts** from both task and verifier containers.
Harbor 0.23.0's default Docker environment mounts verifier output in the task
container even with separate grading, so a separate verifier alone is insufficient.
Only configured task artifacts are transferred into a fresh verifier. Controller
records, curation and model credentials stay outside the candidate environment.
Docker inspection checks actual mounts and privileges; the example applies one CPU
and 1 GiB RAM. Disk space is declared by the task but is not an enforced quota.
Runtime networking is forced to `none` in both containers, removing access to
host services and external networks; this pilot cannot run network-dependent tasks.
This restriction applies to task execution, not to trusted image builds or the
host-side model gateway.

Omnigent's root session has a tool policy installed before its first message,
inherited by the worker. It permits delegation, session inspection, inbox reads
and the fixed container-command connector; other automatically exposed management
and browser tools are denied. The pinned inline format preserves a limit of one
concurrent worker session. The bridge serializes commands, limits their number and
duration, and records receipts before dispatch. Reusing a request ID returns the
receipt; it never silently reruns a command. Retained stdout/stderr is capped at
64 KiB each and explicitly marked if truncated; Harbor still buffers command output
before that cap, so this is not a host-memory limit.

These controls assume a trusted local operator, task definition and runtime.
They do not establish a hostile multi-tenant service, eliminate every reward-hacking
strategy or prove human presence. Abrupt termination may leave Docker/runtime
resources; a `running` receipt is not a liveness check. Inspect the recorded IDs
before taking recovery action. There is no automatic replay.

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

### Delegated execution controls

The final local Docker/Omnigent controls use runtime networking `none`, no host
mounts, one CPU and 1 GiB RAM in both roles. Each used seven scripted model
responses with reconciled zero-cost fixture accounting. Parent and child histories
contain explicit policy denials for the forbidden calls.

| Local attempt | Control | Passed / failed tests | Independent reward |
| --- | --- | --- | --- |
| `omnigent-fixture-8` | Worker writes the scripted solution | 6 / 0 | 1 |
| `omnigent-fixture-9` | Worker writes fake rewards without solving the task | 1 / 5 | 0 |

Both attempts have `status: completed`, no runtime exceptions, retained child
sessions and intact exported inventories. Their trial IDs are respectively
`e989566b-d9a3-41aa-aa66-3600b650cdfd` and
`7a252e61-07dd-4603-a9b9-1bd352830d2e`. The SHA-256 of `execution/files.json` is
`586490f28c2a8859c4b5f84f380e970422b3e3a98c3764414afccef72d8b5f22`
and
`0c053b558c5b02b9ea80b2cbef90fea2cd645de00361ed2269c745a9267250dc`.

Local evidence lives under `.researchharness/experiments/`, with checked fixture
inputs in `omnigent-fixture-1/`. Earlier attempts remain there too: MCP/executor
setup failures (1–3), a native bundle that lost its worker-session limit (4),
source verification rejecting generated bytecode (5), and tool-policy checks
before networking was disabled (6–7). They are development evidence, not failed
or successful research hypotheses. Reproduce the current controls using the
commands above; these local paths are not downloadable GitHub assets.
