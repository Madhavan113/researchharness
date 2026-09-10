# Checkpoint scope: PR #1

Branch `checkpoint/omnigent-research-harness`, commits `ee2148b..4f49495`, reviewed September 10, 2026. This document records what the checkpoint delivers, what it deliberately does not, how its evidence can be reproduced, and which invariants a continuing agent must preserve. The prioritized follow-up list is in [remaining work](remaining-work.md). The shared goal, milestone status and handoff history stay in the [tracker](goals/omnigent-integration.md).

## What this checkpoint is

A software checkpoint for two things:

1. An interactive research harness in which Omnigent (pinned commit `be042b39`, separate venv) owns the conversational loop and UI, and Research Harness owns evidence, proposal validation, collection jobs and durable state. Fourteen MCP tools and the direct `rh discover` CLI share one `ResearchService`.
2. A domain adaptation of the Meta-Harness search procedure ([paper](https://arxiv.org/html/2603.28052v1)): a bounded coding proposer with filesystem access to prior candidate code, development scores and execution traces; candidates executed in a digest-pinned Docker sandbox; development-only Pareto selection; permanent proposer revocation; private held-out final evaluation against the original baseline; one shared spending ledger across research, proposer and final requests.

All model responses in every archived run are authored fixtures. Nothing in this checkpoint measures model quality, optimization gain or real cost.

## Delivered and independently verified

An eight-area read-only review (paper fidelity, sandbox isolation, budget ledger, durable state, MCP/Omnigent/CLI, evaluation benchmark, tests and evidence claims, repository hygiene) confirmed the following by code trace, probe scripts or test execution:

- **Storage and jobs.** Dialect abstraction, savepoint transactions, per-pipeline writer locks with abandoned-run sweeps, atomic per-source publication, content-addressed blobs and the operation ledger preserve `main`'s collection guarantees. Interrupted or cancelled collection never publishes a partial source; completed runs are reconciled without rerun (SIGKILL, death-before-commit and mid-pagination cancel tests pass).
- **Proposal validation.** Probe ids are scoped to the discovery, changed connector configuration fails the fingerprint check, failed probes never count as verified samples, citations must be in the observed URL set from stored receipts, and limits are enforced in the service under the operation lock for both CLI and MCP paths.
- **MCP surface.** Fourteen typed tools reject unexpected arguments, accept no discovery id, model, usage or claimed results, and replay completed operations without network. The Omnigent adapter binds a case once to a session/server pair, fingerprints the bundle on every read, keeps candidate code outside the bundle, uses list-argument subprocesses only and verifies the commit pin. The public-only HTTP guard resolves DNS and checks every hop.
- **Sandbox.** Every documented Docker control is assembled and re-verified from `docker inspect` between create and start: no network, read-only rootfs, uid 65534, all capabilities dropped, no new privileges, pids/memory/CPU/ulimit bounds, noexec tmpfs, no log driver, `--pull=never`, digest match, environment equal to the image's, no Docker socket, host fallback refused. Candidate bytes are never imported on the host. Path tools reject absolute, `..`, `//`, backslash and NUL shapes. Truncated output always fails.
- **Ledger.** Reservations are written and fsynced before HTTP dispatch; costs use exact integer nanodollar arithmetic with round-up; settlement re-parses the sealed raw archive; re-settlement is idempotent; the ceiling boundary is correct; a new run directory cannot replenish spent funds against retained witnesses.
- **Search isolation.** Selection reads only development evidence. Held-out content, evaluator predicates, evaluator source and final-phase artifacts never enter the feedback tree. Revocation is journaled before selection and required by `final()`. The baseline is evaluated first, seeded into history and always included in the final comparison.
- **Tests and evidence.** 552 test functions, none without an assertion, none reaching the network. All eight acceptance checks from the plan (changed config after probing, foreign probe id, unobserved citation, duplicate delivery, interruption, provider failure, budget exhaustion, concurrent writers) have tests. All 115 recorded source hashes match HEAD, all 6,479 indexed files match the budgeted archive, all eleven evidence archives verify, and no secrets or held-out contents are committed.

## Not delivered

- No measured baseline, no live provider call, no real spending, no reviewed benchmark cases and no optimization result. M0, M5 and M6 remain blocked on provider access, the spending decision and human case review.
- The searchable strategy surface is narrower than the plan's Milestone 6 wording. Candidates can edit the instructions file, reorder or re-render search results and set an advisory stop flag. No host code consumes the stop flag, and context selection is frozen from the seed manifest, which the documented baseline disables. See remaining work item RW-1.
- The twenty-case development benchmark has never been used in a search run and cannot currently rank candidates. All archived search runs used a one-case synthetic package whose requirement equals what the scripted model always proposes. See RW-2.
- No CI configuration exists. The runtime-gated tests are opt-in.
- Private held-out packages, private final contents and native runtime credentials are outside the repository by design.

## Reproducing the evidence

This section records the original PR #1 configuration and counts. Later changes to skip reporting and required CI/runtime checks are documented in [test configurations](testing.md); use the shared tracker for current checkpoint results.

Environment requirements for the full configuration: Docker running with the pinned image already present (`python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285`, a multi-arch index), the pinned Omnigent venv, and the `mcp` extra.

```sh
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/path/to/pinned-omnigent/.venv/bin/python \
uv run --extra mcp pytest -ra
```

Results by environment:

| Environment | Result |
| --- | --- |
| Full configuration, recorded September 9 | 1,004 passed, 0 skipped |
| No Docker, env vars unset, `--extra mcp` installed | 978 passed, 26 skipped, 0 failed |
| Additionally without `--extra mcp` | 31 more MCP tests skip |

`pyproject.toml` sets `addopts = "-q"`, so a plain `uv run pytest` prints no skip summary. Treat "zero skips" as a property of the recorded command, not of the suite.

Ruff lint and format pass. Postgres SQL was reviewed for portability but not executed in the review environment.

## Invariants to preserve

From AGENTS.md and confirmed by the review; a change that weakens any of these needs a plan update first.

- Research Harness is authoritative for evidence, proposal validation, pipelines and collection state. Agent assertions are never proof. Candidate rendering may reorder and re-render supplied results but may not mint URLs, passing probes, proposal ids or collection outcomes.
- Direct CLI and MCP go through the same service with the same limits and receipts.
- Immutable captures, publication cutoffs, writer locking and retry/recovery semantics stay as they are. Never reuse a failed or interrupted output directory.
- Held-out data, evaluator predicates, evaluator source, private final artifacts and host budget records never enter proposer feedback. Revoke the proposer before reading a held-out package.
- Spending reservations precede dispatch. Unknown outcomes hold. Completed operations never dispatch again. The ledger and `.pilots.json` registry are retained across revisions.
- Candidate code runs only in the pinned sandbox. Never import, `exec` or `eval` candidate bytes on the host.
- Fixture evidence never satisfies a measured-model gate.

## Known limitations accepted at merge

Merging this checkpoint accepts these as open items, tracked in [remaining work](remaining-work.md):

- Narrow search surface (RW-1) and a non-discriminating benchmark (RW-2), so a live search today would measure instruction wording against a flat scorer.
- Zero-quality candidates can reach the paid final phase (RW-3).
- Provider error responses permanently lock their worst-case reservation (RW-4).
- Committed evidence contains the operator's home directory path and hostname; the repository is private, so this is a reproducibility and hygiene issue rather than a disclosure, but it must be fixed before any public release (RW-5).
- About 23 MB of compressed archives per checkpoint with no LFS or retention policy (RW-6).
- Runtime test skips are silent and unenforced (RW-7).

## Review provenance

Review date September 10, 2026, on HEAD `4f49495`, read-only, no repository edits. Each area reviewer traced code paths end to end and reproduced findings with throwaway scripts under `/tmp` where possible; findings are marked confirmed or plausible in the remaining-work list. Not verified in the review environment: Docker-gated and Omnigent-gated tests, Postgres execution, any live provider behavior.
