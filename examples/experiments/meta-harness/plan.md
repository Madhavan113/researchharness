# Proposed Meta-Harness coding experiment

Status: proposed; awaiting human curation. This is a benchmark setup pilot.

Question: can search over a full agent program improve coding-task completion
under a fixed model and budget? The hypothesis is that access to previous code,
scores and execution traces helps identify useful changes. The outcome is unknown.

## Investigation and sources

- [Meta-Harness, section 3](https://arxiv.org/html/2603.28052v1#S3): the method to
  implement. A paper's reported results are not results of this repository.
- [Original coding experiment](https://arxiv.org/html/2603.28052v1#S4.SS3): uses
  TerminalBench-2 and repeats search/evaluation on the same task set. A separate
  transfer test would be an additional experiment, not that published protocol.
- [Terminal-Bench task source](https://github.com/harbor-framework/terminal-bench-2/tree/2fd12b88aafdd04a52c298e3940bcb189f9766d6/cancel-async-tasks):
  Alex Shaw's async-concurrency/cancellation task, under the repository's Apache-2.0
  license. Original instructions, solution and test assertions are retained.

The selected task checks concurrent execution, the concurrency limit and cleanup
when cancellation happens with queued work. It is one proposed environment for
integration testing, not a representative research benchmark. No model has been
evaluated here, and no performance claim follows from reference/no-op controls.
Computer-use investigation and attachment of browser evidence remain future work.

## Baseline and evaluation proposal

Use Harbor's Terminus-2 as the initial agent baseline. A human must choose the
model, resource budget, additional tasks and comparison protocol before measured
execution. Keep the task model fixed while the proposer changes the agent program.
Record all attempts and costs; use independent reruns and describe uncertainty.

The current executable check runs only Harbor's `oracle` and `nop` controls.
The reference solution must receive reward 1, and no-op must receive reward 0.
Both must reach the verifier without an environment/runtime exception. A green
check establishes only that these controls separate on this setup.

## Infrastructure proposal and explicit adaptations

- Local Linux Docker; one concurrent trial, one CPU, 1 GiB RAM, no GPU.
- Harbor 0.23.0 runs separately under Python 3.12. The task uses a pinned Python
  3.13 image digest and installs pinned pytest/CTRf package versions during build.
  Transitive dependencies are resolved during build; retain the build logs.
- Runtime networking uses Harbor's `public` mode, as in the upstream task. Harbor's
  network blocking requires nftables kernel support unavailable on the current
  Docker host; the first failed attempt is retained. Network access needs curator
  review before agent runs. No provider credentials or model calls are used by
  the two controls. Image/dependency installation also requires network access.
- The verifier uses a fresh container and receives only `/app/run.py` from the
  candidate environment. The initial file is an empty stub so no-op reaches tests.
- The overlay changes the environment and test launcher to use preinstalled
  dependencies. It leaves upstream test assertions and reference solution intact.
  These changes differ from the paper's environment and require review.
- Omnigent delegation, editable agent programs, model-budget enforcement for those
  programs and interruption/recovery of their experiments are not implemented here.

## Human curation before research execution

- [ ] Is this direction worth investigating, and what result would be useful?
- [ ] Do the tasks and tests capture the intended behavior? Are negative controls
      sufficient, and which adversarial cases or metric loopholes are missing?
- [ ] Are the environment adaptations acceptable for the chosen experiment?
- [ ] Which model, budget and search/final task protocol should be frozen?
- [ ] After execution, does the evidence support accepting or rejecting the finding?

Preparation and automated checks never tick these boxes or mark the proposal
human-reviewed. Local hashes detect accidental changes, not an authenticated
human decision. Future agent tools must not expose curator-only decisions or
authoritative evaluator/result writes.
