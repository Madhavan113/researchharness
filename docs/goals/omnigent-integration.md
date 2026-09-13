# Shared goal: Omnigent research harness

Goal id: `omnigent-research-harness`

Status: in_progress; local software and artifact delivery verified; live provider acceptance and measured evaluation still require independent benchmark review, provider access and an approved spending budget

Accepted: September 8, 2026. Last updated: September 13, 2026

Current priority: the user has accepted the
[reproducible AI research goal](reproducible-research.md), beginning with a bounded
Meta-Harness pilot on coding tasks. Use that tracker for new experiment work.
This earlier ingestion track retains its unfinished measurements and historical
statuses; it is deferred as the default work queue, not marked complete.

This technical track integrates an interactive source-discovery workflow using
Omnigent for agent execution and the pilot interface, with Research Harness
providing verified evidence, pipeline proposals, collection and durable state.
After measuring the integration, evaluate the Meta-Harness paper's strategy
optimization process independently.

September 13 scope correction: the broader product supports investigation and
automated ingestion. This tracker does not define every research task as a
pipeline proposal or require Omnigent's native UI. General task/artifact contracts
and other workflow areas need their own work and acceptance. The measured
optimization phase retains its completion gates but is not a prerequisite for
that product work. Historical evidence and unfinished evaluations are unchanged.

The [accepted implementation plan](../omnigent-integration-plan.md) defines the design and detailed acceptance criteria. [AGENTS.md](../../AGENTS.md) defines how agents coordinate. This file is the shared progress record; update it as work happens.

## Scope and completion

Milestones 0–5 deliver the local, single-user pilot. Milestone 6 delivers the subsequent paper-based optimization phase. Completing the documentation or the pilot alone does not complete the whole implementation goal.

The goal is complete when there is recorded evidence for all of the following:

- A research brief produces a saved, validated proposal through the actual Omnigent agent and Research Harness MCP tools.
- Citations and connector configurations are backed by stored evidence and matching probes; invalid submissions receive actionable errors.
- Requested collection and export work through the agent. Reopening a case recovers its evidence, pipeline, and jobs; retries and interruption do not duplicate publication.
- The existing direct CLI remains functional and uses the same domain service. Storage changes preserve the SQLite/Postgres contract and existing collection guarantees.
- Independent reviewed cases measure correctness, usefulness, cost, and recovery. A controlled comparison records the model, search provider, fixtures, budgets, and limitations for each path.
- Strategy optimization records the baseline, candidate code, complete available traces, scores, and usage. Selection uses development results; held-out evaluation remains isolated from search and candidate access.
- Reproducible setup instructions, an example walkthrough, and validation evidence are available in this repository.

Authenticated shared deployment, product interfaces, analytical workflows and
specialist agents are outside this technical track. Their priority is governed by
current product work, not the milestone sequence here. Multiple development agents
may collaborate when assigned; a fixed product agent team is not implied.

## Milestones and ownership

Use `queued`, `in_progress`, `blocked`, or `done`. Replace an owner only after an explicit handoff or after establishing that the previous task is no longer running. Record a bounded subtask below when work is smaller than a milestone.

| Id | Deliverable | Status | Owner | Dependencies / completion evidence |
| --- | --- | --- | --- | --- |
| G0 | Accepted plan, shared tracker, agent instructions | done | `/root`, goal-documentation session | Four Markdown files checked; 20 local links resolve; diff check passes |
| M0 | Pinned Omnigent runtime and compatibility spike | blocked | `/root` (spike handed off) | Pinned runtime, normal server/UI, and between-turn budget policy verified with fixtures; live acceptance awaits provider access and the pending spending decision |
| M1 | Reusable research service and durable discovery state | done | `/root` | CLI parity, restart evidence/budgets, migration, atomic rollback, and concurrent ownership verified locally |
| M2 | Typed MCP tools, search receipts, context binding, limits | done | `/root` and `/root/evaluation` | Fourteen tools after M4; actual protocol and CLI stdio discovery/job/reconnect checks pass |
| M3 | Omnigent research agent bundle and runtime binding | done | `/root/omnigent_spike`, browser verification `/root` | Normal server/runner/MCP and browser chat save validated proposal/pipeline ids; synthetic model HTTP, frozen authored bundle |
| M4 | Collection jobs, exports, case lookup, recovery | done | `/root` | Detached workers, cancellation, process termination, scoped data, exports, and full server/browser restart verified locally; local Postgres/MinIO tests and detached workflow/restart/export acceptance now verified |
| M5 | Independent pilot evaluation and baseline comparison | blocked | `/root`, bounded agent work handed off | Twenty authored cases now discriminate in the offline policy comparison; independent evaluators, frozen controller and bound gateway usage verified through actual runtimes; budgeted dispatch and independent settlement verified through both actual runtimes; awaits human review, provider access and the pending spending decision |
| M6 | Meta-Harness strategy optimization and isolated final evaluation | in_progress | `/root`; measured validation next | Strategy execution, coding proposer, shared budget, development selection and isolated final evaluation are fixture-verified. The pre-measure software follow-ups are published in PRs #2–#19; the required core suite passes 1,463 tests with zero skips locally and in PR #18 CI. RW-5/RW-6 are complete: the full historical review release is published and all 15,397 downloaded members verify. Real-model baseline/search/final still require reviewed cases, provider access, spending approval and live compatibility. Exploration-limit changes remain conditional on measured evidence |

M0 and M1 can proceed independently against the agreed tool/service boundary. Evaluation case design can also proceed independently. Agree on ownership of shared schemas, CLI wiring, dependencies, migrations, and this tracker before concurrent edits.

## Current evidence and limits

- GitHub reports the repository public as of September 12. Twenty-five original files are now referenced at their pinned Git revision and restored outside the checkout with exact hash checks. Historical originals and Git history still contain operator paths. The portable exporter creates labelled derived review copies. The complete historical-baseline release is now public with user approval; all 15,397 downloaded members verify. Original Git history and exact runtime proofs remain unchanged.
- The repository already contains the direct discovery CLI, connector probes, proposal compilation, collection/export/replay, and local/shared backend work. Inspect and preserve the existing working-tree changes before building on them.
- The tested Omnigent source pin is `be042b390e293a8d586cbb7e403a2ce0ce38fc62`, installed in a separate environment. Normal server, runner, MCP, browser, restart, and spending-policy fixtures have run. The current turn can exceed the configured budget threshold; the next turn is blocked. This is not an exact provider billing cap.
- The independent evaluator is maintained in this package under `evaluation/`, with twenty authored development cases and verified Omnigent usage-file support. It derives source usefulness from independent requirements. Authored cases are not human-reviewed cases, and fixtures do not establish model performance.
- The [browser acceptance record](../omnigent-ui-acceptance.md) verifies chat-driven discovery, collection/export, source provenance inspection, and reopening the same case after a full process restart. Model responses are synthetic. That native runtime's auxiliary usage remains incomplete; live model quality, paid comparisons, and strategy optimization remain unverified.
- The [controlled comparison fixture](../controlled-comparison.md) passes through both actual runtime paths with shared instructions, settings, source fixtures, and per-request limits. Native Omnigent drops authored output-token/round limits; the explicitly named controlled variant uses a host gateway. Independent raw-archive verification now establishes all 440 direct and 770 Omnigent synthetic provider tokens while retaining the narrower runtime evidence separately. No real-model or billing conclusion follows from these fixtures.
- The [budgeted pilot](../pilot-budget.md) and [search runner](../strategy-optimization.md#run-search-with-the-shared-model-budget) enforce shared reservations before provider dispatch and verify the priced model/tier. Settlement uses independently verified usage or, for a sealed provider error, separately recorded operator-reviewed final-charge confirmation; the latter leaves token usage unknown. Other unknown outcomes retain holds. Durable initialization intents recover only unchanged initial ledger/registry state. The registry checks pilots and searches across prepared revisions, including coding proposals and private final execution. The proposed $10 ceiling remains draft and does not authorize spending or guarantee completing the full benchmark.
- The [strategy integration](../strategy-optimization.md) provides isolated candidate execution, shared direct/MCP observation sessions, controlled-gateway context selection, bound code/session evaluation and independently graded development artifacts. The [combined runtime acceptance](../../examples/evaluation/evidence/combined-strategy-search-2026-09-09/acceptance.json) now verifies three proposer iterations, seven development evaluations and seven isolated final evaluations through actual Omnigent/MCP and Docker. Its 125 requests and 13,750 tokens are synthetic. Local Postgres/MinIO acceptance separately passed 125 selected tests and a detached workflow/restart/export check on September 8. Reviewed cases, a measured baseline and real optimization/final evaluation remain unfinished.
- No OpenAI API key or project .env was configured in the current process when checked. A provider/budget clarification is pending; no paid calls have been made. Twenty development cases and a separate ten-case private held-out draft remain authored. The private draft passed twenty offline service/evaluator checks and disjointness validation; human review is still required. Local shared-storage acceptance has passed with Postgres/MinIO.

## Next tasks

The [remaining work](../remaining-work.md) list records completed pre-measure software follow-ups and artifact delivery. Its only conditional follow-up is reconsidering proposer exploration limits after measured runs provide evidence. The remaining critical path **for this integration/evaluation track** is live compatibility, independent case review, the measured baseline and then optimization/final evaluation. Broader product work proceeds under its own scope; these are not automatic next tasks for every agent:

1. Resolve provider access and the pending spending decision before a live compatibility case. The budgeted dispatcher and pilot runner are implemented and verified with actual runtime fixtures; the checked-in configuration remains draft. Recheck the priced snapshot/endpoint/tier/rates when recording the live configuration, and reuse the shared ledger and registered pilot directories across revisions.
2. Finish M0's live provider/model checks once access and the pending budget choice are recorded. Validate live nested tool-schema acceptance and freeze the actual provider/model configuration before running a measured baseline.
3. Review the twenty development cases and separate ten-case private held-out draft, then run controlled direct-versus-Omnigent comparisons. Shared instructions, settings, fixture providers, failure records, and actual runtime executors are implemented. Fixture policies do not establish model performance.
4. Use the implemented budgeted search runner for measured M6 optimization only after the baseline is measured and controls are frozen. Complete budgeted runtime acceptance is archived. Keep held-out contents/evaluators outside search access and revoke proposer access before final evaluation.

The local integration uses `openai-agents` and the existing `gpt-5.4-mini` model setting. Its protocol/runtime behavior is fixture-verified; live provider access remains unverified. Record any deliberate change to the adapter, provider, or model so evaluation comparisons remain interpretable.

## Handoff format

For each active task, add a short entry with:

- Task id, owner, date, status, and milestone.
- Scope and files owned or changed.
- Acceptance evidence: exact commands, results, and artifact paths; identify fixture versus live checks.
- Remaining work, dependencies or decisions, and the next action.

Retain completed handoffs so another agent can distinguish implemented behavior from planned work. Avoid copying secrets, raw credentials, or held-out task contents into this shared tracker.

## Activity and handoffs

### September 13, 2026 — Adopt the reproducible research goal

Owner: `/root`; status: documentation complete; branch `docs/reproducible-research-goal`.
The user accepted a simpler goal: carry one AI research question through a
baseline, delegated experiments, independent evaluation and a reproducible
conclusion. Record the new active technical goal in
`docs/goals/reproducible-research.md`; align `AGENTS.md`, `README.md`, the
documentation index and this track's priority notice. Preserve runtime code,
frozen experiments and historical completion evidence. Acceptance: consistent
scope/status, valid Markdown links, clean diff and a reviewable checkpoint.
The existing app goal is paused and unfinished; `create_goal` rejected replacing
it. Repository documentation must not claim automatic goal activation.

Handoff: the new goal has three implementation steps, with the baseline/evaluator
next. Both README entry points and shared agent instructions use it; old domain
proposals are deferred. All 341 local Markdown targets in 29 root/documentation
files resolve, the diff passes whitespace checks, and only Markdown files changed.
Runtime tests were not rerun for this documentation-only task. Existing source,
fixtures and historical evidence are unchanged.

### September 13, 2026 — Archive exploratory market snapshot and explain code layout

Owner: `/root`; status: done; branch `docs/research-foundation-scope`, PR #20.
The user identified `research/prediction-markets/` as an unnecessary top-level
directory while asking for a codebase explanation. It contains one dated JSON
research result, referenced only by two documentation links. Move that file
unchanged to `docs/archive/prediction-markets/`, update those links and label the
notes historical. Add a source map to `docs/README.md`. Preserve the working
market connectors and their tests; an optional connector add-on is a design
direction, not an implemented plugin system. Acceptance: identical snapshot
bytes, no runtime references, valid documentation links and clean diff.

Handoff: Git records a 100% content-preserving move into the documentation archive.
The two note links now resolve there and the note title explicitly says archived.
The documentation index explains the executable modules, examples, tests and
research artifacts, including the current lack of a general task layer or plugin
loader. Snapshot SHA-256 is unchanged; all 402 local Markdown targets resolve
across 60 documents, and `git diff --check HEAD` passes. No runtime code, agent
instructions or tests changed. Connector extraction remains a separate proposed
refactor; this cleanup does not remove working market ingestion support.

### September 13, 2026 — README and release presentation cleanup

Owner: `/root`; status: implemented, release presentation published;
branch `docs/research-foundation-scope`, review in PR #20.
The user asked to fix the public README and releases after feedback that both
were hard to understand. Scope: rewrite `README.md` for new readers, add a small
documentation index and a single-source quickstart configuration, and edit the
existing GitHub release title/notes/classification and asset display labels.
Store the release copy in `docs/releases/evidence-archive-2026-09-12.md` and
align the repository's short description with the plain-language introduction.
Keep release tags, downloadable filenames, bytes and historical receipts intact.
No product binary or measured result is being released. Update the existing PR
against current `main`; preserve all runtime code and other agents' work.

Acceptance: run the documented key-free quickstart in isolated local storage,
check documentation links and diff, verify release metadata and unchanged asset
hashes/IDs, and keep the README's current/planned distinction explicit.

Handoff: the README is 553 words, down from 1,280 on `main`; it now starts with
the purpose, available capabilities and a key-free example. Internal evaluation
and contributor material is linked through `docs/README.md`. `uv sync --locked`
and the documented validate/run/export commands pass with isolated local storage
and no model credentials. The live OFAC feed yielded 10 records; the JSONL count
and SHA-256 match its companion manifest. Raw responses and verification logs
remain in ignored local storage; this is a collection check, not a model test.
All 387 local Markdown targets resolve across 60 documents; diff checks pass.

The GitHub release is now titled "Historical test evidence (September 2026)",
marked as a prerelease, and no longer marked Latest. Its notes describe the
audience and downloads in plain language; both assets have readable display
labels. API verification confirms the original release ID, tag, target, publish
date, asset IDs, filenames, download URLs, sizes and digests are unchanged.
Historical publication receipts retain the original metadata. The repository
description now matches the README's purpose. The README/configuration changes
are delivered through PR #20 against `main`; no PR merge is performed here.

### September 13, 2026 — Shared foundation scope clarification

Owner: `/root`; status: implemented and published for review; branch `docs/research-foundation-scope`,
separate worktree `researchharness-scope-cleanup`. The user clarified that the
product is a broad investigation and automated data-ingestion harness. This
tracker covers the existing ingestion integration and measured optimization
track; its proposal workflow is not the required outcome of every investigation.

Bounded task: clarify that boundary in `README.md`, `AGENTS.md`, this tracker,
`docs/omnigent-integration-plan.md` and `docs/remaining-work.md`. Preserve all
runtime code, frozen agent instructions, evaluation criteria and historical
evidence. Private application design and detailed product plans stay outside
this public repository. Acceptance: local Markdown links, consistent scope and
`git diff --check`; no runtime or model-performance claim from documentation.

Handoff: the five owned documents now distinguish the shared research/ingestion
foundation from the existing source-discovery and measured-evaluation track.
Checkpoint `2eb5898` is published in
[PR #20](https://github.com/Madhavan113/researchharness/pull/20), stacked on PR #19;
hosted-check status is recorded on the PR. No merge is implied.
All 386 local Markdown targets resolve across 58 documents; `git diff --check`
passes. The diff is limited to those five files. Runtime code, frozen bundles,
evaluation criteria, evidence and release assets are unchanged. The full runtime
suite was not rerun locally for this documentation-only change. Product work
can proceed independently; live compatibility and measured evaluation still need
their previously recorded external decisions and acceptance.

### September 12, 2026 — Complete review release publication ownership

Owner: `/root`; publication and download verification done on
`docs/complete-historical-reviews`, continuing PR #19 from head
`48a3564555987ef0205f12f1c75e3f38875db616`. The user explicitly answered
"publish it" after the request to publish the complete 24.4 MB archive and
604 KB inventory. This authorizes the two recorded assets under
`evidence-historical-review-all-2026-09-12`; it does not authorize provider spending,
benchmark sign-off or merging PRs. The repository had no existing release or tag
with that name when checked. PR #19's exact-head hosted tests remain active.

Scope: recheck the prepared files against the committed index, publish the release
at the metadata checkpoint, download the exact two assets into a new private
directory and verify every member. Only then mark remote availability verified,
record the receipt, close RW-5/RW-6 delivery, and update the shared status/PR.
Preserve the immutable tag, original evidence, private inputs and all model gates.

Publication handoff: the [complete review release](https://github.com/Madhavan113/researchharness/releases/tag/evidence-historical-review-all-2026-09-12)
was published at **2026-09-12 23:32:45 UTC**, release id `387735562`, at the
immutable metadata tag commit `48a3564555987ef0205f12f1c75e3f38875db616`.
The two uploaded assets match the committed SHA-256 values and lengths exactly:
archive id `560138798` (24,371,182 bytes) and inventory id `560138799` (603,906
bytes). The release is public, published, and is not marked as the latest product
release. Its retained tag also keeps the original `b58bf44` baseline reachable.

Both files were downloaded from the exact tag into the new private directory
`/tmp/rh-rw5-complete-release-download-20260912`. The independent download check
verifies **15,397 members / 140,767,556 expanded bytes** against the committed
index. The [publication receipt](../../examples/evaluation/evidence/historical-review-all-2026-09-12/publication.json)
records the API response, user authorization, commands and verification report
hash. The current index now records `availability: download_verified`; earlier
preparation reports and asset bytes remain unchanged.

RW-5 and RW-6 are complete for their content/delivery acceptance. The complete
review includes every baseline file and nested snapshot, all known identity
checks passed before publication, and remote availability is now demonstrated.
Exact historical originals still retain their original paths and remain the
source for runtime proofs; derived copies cannot relocate a ledger or supply
omitted private audit inputs. The publication does not establish model quality.

Changed files: the release index and new receipt, evidence README/guides, main
README, accepted-plan status and shared trackers. No production code, tests,
benchmarks, model settings, budgets or dependency pins changed. Final metadata
validation confirms the original prepared index differs only in availability,
the receipt/report/index hashes agree, and all 386 local Markdown targets resolve.
The retention guard passes for 21 checkpoints, 4,392,463 current evidence bytes
and all 25 historical references; this new checkpoint occupies 24,183 bytes in
Git. `git diff --check` passes. Next: finish
PR #19's hosted checks, then resolve independent case review, provider access
and spending authorization before live compatibility and measured evaluation.
M1–M4 are complete; M0's live acceptance, M5's reviewed comparison and M6's
measured optimization/final results remain unfinished. The full goal stays active.

### September 12, 2026 — Complete historical review coverage ownership

Owner: `/root`; complete baseline preparation, verification and metadata publication
done in [PR #19](https://github.com/Madhavan113/researchharness/pull/19), branch
`docs/complete-historical-reviews`, based on
PR #18 head `8fab9199eeb0b9e03fd99643e896f8b93f14ad8a`. The previous goal turn
made progress: all 25 original files restored exactly, the 1,463-test required
suite passed, and three checkpoint commits plus PR #18 were published.
PR #18 run `34725093128` now passes both jobs at exact head `8fab919`: 1,463
required tests with zero skips in 420.07 seconds, and 1,431 ordinary tests with
32 expected optional-runtime skips in 295.66 seconds. The older run is cancelled.
The successful runtime log is `/tmp/rh-pr18-runtime-ci-20260912.log`, SHA-256
`e69c76e49536a5ea6fb624629578193eda76944ff5859c1a1aa4a9b94cb4a640`.

RW-5 still covers only one representative derived archive. The baseline contains
11 regular-member tar archives and two gzip JSON request captures. The largest
member is below 1 MB; a complete review is within the existing tooling limits.
Scope: verify every selected original from the pinned Git baseline and its
original member index, prepare private review inputs with explicit container/member
provenance, export and re-verify every file with the existing portable tools,
and prepare a complete external asset package. Keep original bytes, indices,
ledgers, frozen proofs and private final data unchanged. Do not execute archived
code or upload any release assets. Human/provider/budget decisions stay pending.

Intended repository edits are small evidence indices/summaries and review guides,
plus this tracker and remaining-work status. Full original/review trees and
compressed assets remain in private local directories outside Git. Acceptance:
all original files and expanded archive members accounted for, source-bound
transform verification, no configured operator identity or JUnit hostname in
new review files, archive member verification and a fresh extraction scan, with
all limitations recorded. A derived copy cannot reproduce an audit that requires
omitted private inputs or resume an original budgeted run.


Local handoff: [recipe commit `c5efe82`](https://github.com/Madhavan113/researchharness/commit/c5efe824742bd6f9550b4e07d021d2831a005656)
prepares all **150 Git files / 27,209,764 original bytes** from baseline `b58bf44`.
The first export correctly refused 544 nested gzip snapshots; that private
partial output remains at `/tmp/rh-rw5-all-review-20260912`. The completed recipe
uses a fresh tree, decodes all 31 distinct nested payloads at each of their 544
recorded locations, and retains directory metadata and original container/member
hashes. No archive is executed, omitted or rewritten. A separate byte comparison
checks every Git original, all 14,576 outer tar members, all nested containers
and all 15,395 resulting leaf files against the private prepared tree.

The [complete review checkpoint](../../examples/evaluation/evidence/historical-review-all-2026-09-12/README.md)
records exact commands, file counts and report hashes. Private inputs are at
`/tmp/rh-rw5-all-originals-recursive-20260912`; the complete derived review is at
`/tmp/rh-rw5-all-review-recursive-20260912`. Source-bound verification passes
15,396 files including provenance: **124 changed, 15,272 byte-identical**.
All forty binary files remain intact. The review manifest SHA-256 is
`fd9946eb4819ccd5f200ce9b42a0097feb900b5b771579d6e3f2a8b5bb2a3255`;
the original-input provenance SHA-256 is
`1e4d3d884f712b1ae2b8ea1740c029ba727749bd8a579c020db4a79750b321e8`.

The complete package at `/tmp/rh-rw5-all-package-20260912` has **15,397 members /
140,767,556 expanded bytes**. Archive: 24,371,182 bytes, SHA-256
`68f005af1e2107dbcc1ee3a7666cf01262ed82451c7dd29be8970617fee040f0`.
Inventory: 603,906 bytes, SHA-256
`1a541a21ee8d86bc6d55229410850ea9342ca7e34cb57675590af99ea6011c3a`.
A separate CLI verification passes every member. A fresh private extraction at
`/tmp/rh-rw5-all-extracted-review-20260912` rechecks every file hash, normalized
tar host metadata, zero configured identity matches and all nine JUnit reports
with no hostname. This covers the full historical baseline; it is not a general
secrets detector, third-party review, a relocated ledger or a new model result.

The reproducible input recipe and small index/acceptance/verification summaries
are committed; complete payloads remain outside Git. The intended release tag is
`evidence-historical-review-all-2026-09-12`, still `availability: prepared`.
RW-5 local content checks are complete; RW-5/RW-6 delivery remains open pending
public release approval and a verified download. Commits `c5efe82` (recipe) and
`1638d23` (evidence) are pushed; [PR #19](https://github.com/Madhavan113/researchharness/pull/19)
is open, stacked on #18. Next: inspect its hosted checks and await the publication
decision for the complete package. Do not infer consent
from elapsed time or the prior authorization to commit/open PRs.

Provider prerequisites were rechecked: no configured `OPENAI_API_KEY` or project
`.env`, budget authorization still `draft` with no reference, and all twenty
development cases remain `authored`. Private held-out packages were not opened or edited.
The broader goal remains active; reviewed cases, provider access, spending
approval, live compatibility and measured baseline/search/final are unfinished.

Final local checks: Ruff lint/format pass for 117 files including the recipe;
381 local Markdown targets resolve, current evidence has zero operator-home
matches, and `git diff --check` passes. Retention validates 21 checkpoints and
all 25 historical references. The new checkpoint totals 21,463 bytes. Production
source, tests and dependency pins are unchanged from PR #18; no additional
local runtime suite is claimed for this evidence-preparation checkpoint.

### September 12, 2026 — Historical evidence reference migration ownership

Owner: `/root`; bounded implementation, local verification and publication done
in [PR #18](https://github.com/Madhavan113/researchharness/pull/18), branch
`feat/historical-evidence-references`, based on
PR #17 diagnostic head `d938385d797dd1e07a8827ba91e83a3f0292fdbb`. The preceding
turn made progress by publishing diagnostics and passing 1,446 required tests.
Hosted run `34724115100` now passes both jobs at that exact head: 1,446
required tests with zero skips and 1,414 ordinary tests with 32 expected optional
runtime skips. The earlier timeout did not recur; its cause remains unconfirmed.

A read-only comparison verifies that all 13 legacy compressed assets plus 12
path-bearing or JUnit-hostname-bearing files exactly match existing commit
`b58bf44a84407d1069039499411e53aaaede5968`: **25 files / 22,872,690 bytes**.
Candidate records are retained locally in
`/tmp/rh-legacy-git-reference-candidates-20260912.json`. The repository's Git
object database already contains those bytes (30.29 MiB total observed); no
history rewrite or additional public archive upload is needed to reference them.

Scope: extend the retention policy with explicit, hash/size-bound historical
Git references and a private, exclusive-create restore command; verify original
objects before removing their duplicate current-checkout copies; update affected
links and reproduction instructions; preserve every historical byte and hash.
The existing guard only checks listed files and can miss staged archive deletions;
close that gap by requiring each legacy original either present or covered by a
verified reference. CI must fetch the referenced history explicitly. This reduces
current-checkout exposure/size, not Git history size or historical public exposure.
RW-5 remains open until its full acceptance is supported; this migration must
not relabel original captures as sanitized or claim a new runtime execution.

Intended files: evidence retention helper/tests and policy, CI checkout settings,
25 verified file removals, evidence guides/links and trackers. Acceptance: all
25 originals restored outside Git with exact bytes, absent/corrupt/mismatched
objects refused, unrecorded removals and reintroduced archived copies refused,
no overwrites or executable restored files, current evidence identity scan,
focused tool tests, retention checks and documentation links. Public release
approval, benchmark review, provider access and spending decisions remain pending.

Local handoff: implementation commit `fccd48bb5e8ea5b384fcaf5e4e932351d421b25e` adds
verified historical restoration and retention guards. Schema 2 preserves the
original baseline commit, all 13 compressed hashes and the 18 original size
records. Both CI checkouts fetch full history. All 25 originals were restored
into `/tmp/rh-legacy-originals-20260912` before the selected `git rm`; a separate
comparison checked each Git blob, current file, restored file, recorded length
and SHA-256. The private `0700` directory and `0600` nonexecutable files pass.
The current checkout loses 22,872,690 original bytes; Git history is unchanged.

The [checkpoint](../../examples/evaluation/evidence/historical-references-2026-09-12/README.md)
contains only small acceptance/verification metadata. Fifteen checkpoint READMEs
now describe original retrieval, and nine former local links resolve to their
pinned original Git blobs. The current evidence scan found no checked operator
home/hostname matches and no nonempty JUnit hostname attributes in 144 files
(before the new verification summary). The representative 159-file portable
review copy separately re-verifies all original transforms, with three changed
files. Restored originals remain unsanitized; RW-5 stays `in_progress`.

Validation: the asset/portable group passes **83 tests** in 4.24 seconds. JUnit
`/tmp/rh-historical-references-tools-final-20260912.xml`, SHA-256
`c47ac2aba939a0da741120cb269890aba2c3f32ed9b993fec4ba489e5ab33c49`.
The first focused attempt failed only because a fault-injection test tried to
modify its own read-only Git fixture object; the fixture now changes that
object's mode before corrupting it. It did not modify this repository's objects.

~~~sh
RH_TEST_REQUIRE_RUNTIME=1 \
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
uv run --locked --extra mcp pytest --tb=short \
  --basetemp=/tmp/rh-historical-references-required-20260912 \
  --junitxml=/tmp/rh-historical-references-required-20260912.xml
~~~

The full required suite passes **1,463 tests, zero failures/errors/skips**, in
304.23 seconds; JUnit SHA-256
`e1766515f6d4f17612d0c66baaaf31a3336e1e2e460e2dc66922bb74298b2f35`.
Production source, tests and policy were stable throughout that run. Ruff lint
and formatting pass for 116 source/test files. The retention guard verifies
20 checkpoints and all 25 historical references; final documentation links
and staged changes are checked before publication.

Publication: commits `fccd48b` (implementation) and `9c4f8ad` (migration/evidence)
are pushed, and [PR #18](https://github.com/Madhavan113/researchharness/pull/18)
is open, stacked on #17. The final link check resolves 372 local Markdown targets
and nine pinned original Git blobs. The new checkpoint is 5,198 bytes; all
evidence checkpoints total 4,368,280 bytes in the current tree. Hosted checks
are pending. Next: inspect those checks and retain any failure evidence. The
pinned baseline must remain available in retained Git history.
Public release assets remain local pending the existing publication question;
human benchmark review, provider access, spending approval and measured
baseline/search/final are still pending. The overall goal remains active.

### September 12, 2026 — Runtime collection timeout investigation

Owner: `/root`; `in_progress`, continuing PR #17 on `feat/portable-evidence-export`
from head `49c0ecdbf4830dcfb8c301281d196e94c0d15b54` (initial investigation branch
`fix/runtime-fixture-diagnostics`; no separate implementation commits). The prior turn was a
verified wait. PR #16 exact-head run `34722233873` attempt 2 passed both hosted
jobs (1,412 required tests, zero skips). PR #17 run `34723146902` passed ordinary
checks but failed one required test: the normal server/runner collection/export
follow-up timed out after 90 seconds; 1,443 other required tests passed.
The console trace identifies an unresolved turn, not its cause. The public CI
log does not retain the fixture's model errors, session events or job state.

Scope: reproduce the failing path with retained offline artifacts, inspect
model/tool/worker/session evidence, and fix a demonstrated cause or the missing
diagnostics needed to establish it. Do not simply relax the timeout or replay an
unresolved turn. Intended files: normal runtime fixture, its focused tests and
diagnostic retention if needed, testing guide and this tracker. Retain the failed
CI result; it is not superseded by a passing local rerun. All provider/model
responses remain authored fixtures; no live model or spending gate changes.

Diagnostic checkpoint: the exact failed test passed locally in **8.49 seconds**
with the original limits; report `/tmp/rh-runtime-timeout-repro-20260912.xml`,
SHA-256 `962d67a3810839ef1d752cd418de07a2c929da6dc1028aebf6978c57950d2576`.
The original hosted failure log remains at
`/tmp/rh-pr17-runtime-failed-20260912.log`, SHA-256
`58e35c62d18d146517322e363909ff7f5a5c243f0054b9a74da660ae32951d83`.
Its cause is not established, and no production runtime limit, retry policy or
assertion was relaxed.

The fixture now writes a bounded `fixture-state.json` from its finalizer with
phase/stage, request/response counts, the last action, observed job states and
model errors. Failed subprocess exits include up to 16 KiB of that report in the
test assertion. Complete original captures stay separate; the summary is a last
observation and cannot prove an unresolved job stopped. Two added tests verify
payload exclusion/error bounds and preservation of both diagnostics and the
original timeout without a fabricated acceptance record. They pass in 0.70
seconds; report `/tmp/rh-runtime-diagnostics-tests-20260912.xml`, SHA-256
`14501596527287edba3482941717ebed75d69719770f3f01d2d5bb6806fa1a73`.

~~~sh
RH_TEST_REQUIRE_RUNTIME=1 \
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
uv run --locked --extra mcp pytest --tb=short \
  --basetemp=/tmp/rh-runtime-diagnostics-required-20260912 \
  --junitxml=/tmp/rh-runtime-diagnostics-required-20260912.xml
~~~

The fresh full suite passes **1,446 tests, zero failures/errors/skips**, in
300.90 seconds. JUnit SHA-256:
`b4b77715ee4979ae37d341f263207dbdc07bbcaac88d236b3bd9b2abcbdbe6de`.
The actual workflow's new report records 19 model requests/responses, zero model
errors, both jobs succeeded and a completed final response in the reopened case;
its SHA-256 is
`5ee5bc04f5d96750ba89bbe03373fb23039b59912e8f2b9509791aa687149e23`.
Lint/format checks pass for 117 files (including the fixture), all 109 local
Markdown targets in changed documentation resolve, and the retention guard
verifies 19 checkpoints with all 13 historical compressed hashes intact.

Hosted follow-up: [run 34724115100](https://github.com/Madhavan113/researchharness/actions/runs/34724115100)
passes both jobs at `d938385d797dd1e07a8827ba91e83a3f0292fdbb`: 1,446 required
tests with zero skips in 447.62 seconds, and 1,414 ordinary tests with 32 expected
optional-runtime skips. The diagnostic checkpoint is complete; use its retained
state to investigate a recurrence. The original timeout cause remains unconfirmed,
and a passing rerun does not demonstrate a fix for it. Public release publication, human
benchmark review, provider access and spending approval remain unanswered; no
live model requests or release uploads occurred. The overall goal remains active.

### September 12, 2026 — Historical audit portability follow-up

Owner: `/root`; bounded documentation/evidence check completed, continuing
PR #17. The preceding turn made progress by publishing the portable exporter,
its verified checkpoint and PR. Current-head PR #17 run `34723007267` and PR #16
run `34722233873` attempt 2 are confirmed live; no restart is requested.

The archived budgeted-search audit depends on more than a hard-coded repository
path: it opens the original private final executions and retained ledger, checks
the executed source version, and writes into the original run. Replacing two
paths would not make it a valid audit of the published subset. Scope: document
that boundary and provide/test a portable, read-only member-integrity command
against the complete historical public archive. Keep originals unchanged; do
not claim that member hashes reproduce the private-final or runtime audit.
Files: portable evidence guide, historical checkpoint README and shared tracker.
Publication of the prepared derived release is awaiting a separate user answer;
provider/budget and benchmark-review decisions remain pending.

The documented command was executed verbatim from the guide in a separate
Python process against the original budgeted-search index/archive. It verifies
all **6,479 files / 49,698,731 expanded bytes** without extraction. The compressed
SHA-256 remains `7af0293e450f0279139c56ff0dd4f35a0996dfd5bafe1d5f0dbe3514713a90f3`.
The local result `/tmp/rh-historical-member-verification-20260912.json` records
the original index hash and exact documented script hash; its SHA-256 is
`9c9d5b32a3b6bbd3186f98204dfd70ed0de0e1c24d5396a483f365968a5c9def`.
All 84 local Markdown targets in the changed files resolve, the diff check
passes, and the retention guard still verifies all 13 original compressed
hashes across 19 checkpoints. This is a documentation and integrity check;
production code/tests are unchanged and no runtime suite was rerun. The full
private-final/runtime audit has not been rerun or claimed reproduced.

### September 12, 2026 — RW-5 portable review export ownership

Owner: `/root`; bounded RW-5 export work `in_progress` on `feat/portable-evidence-export`, based on PR #16 head `e03c498930906b6efa48812168e02780a78a78da`. The preceding goal turn made progress: it implemented retention tools/CI, passed 1,412 required tests and published PR #16. Its hosted run `34722228062` is confirmed queued at this task's initial poll and is not restarted.

Source inspection confirms five loose files and 74 files across eight compressed archives contain the operator's home path. Most are MCP launch configurations bound into original bundle/runtime hashes; blindly rewriting them would invalidate that evidence. Scope: create explicitly derived portable review exports with named path placeholders, normalized JUnit host metadata and an original/exported hash manifest; preserve every original byte and runtime/ledger binding; reject residual known identity in unsupported binary data rather than corrupting it. Verify a representative complete reviewed archive and its original hashes, package the result with the adopted retention tools, and record the limits of derived evidence. Intended files: export/verification helpers and tests, a portable evidence guide and reproduction templates, small acceptance/index records and trackers. Historical originals remain available and unchanged. This work does not declare the whole RW-5 item complete while historical identity remains in the current tree, nor establish model performance or spending authorization.

### September 12, 2026 — RW-5 portable review export handoff

Owner: `/root`; bounded export/verification checkpoint implemented on
`feat/portable-evidence-export`, based on PR #16. Implementation revision:
`cf21dc26c01805001cf1f2687e1f3a3117003613`. Added the portable export, verification
and local template-rendering CLI plus 32 regression cases. The
[guide](../portable-evidence.md) distinguishes derived copies from authoritative
runtime evidence, private bindings from public placeholder names, and local
preparation from asset availability. The original run, ledgers, archive hashes
and private held-out draft remain unchanged.

Validation:

~~~sh
RH_TEST_REQUIRE_RUNTIME=1 \
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
uv run --locked --extra mcp pytest --tb=short \
  --basetemp=/tmp/rh-rw5-required-20260912 \
  --junitxml=/tmp/rh-rw5-required-20260912.xml
~~~

The full required suite passes **1,444 tests, zero skips, in 299.35 seconds**.
Its JUnit SHA-256 is
`7884112f8fe93e06089f6e5c94edf649edf3a15878ef9e2c7e2fcab7061e7121`.
The focused command `uv run --locked --extra mcp pytest
tests/test_portable_evidence.py tests/test_evidence_assets.py --tb=short
--junitxml=/tmp/rh-rw5-tools-final-20260912.xml` passes all 66 cases, zero skips;
report SHA-256
`ed40c0307ad275e3ac95076c35147281aff8c81ab6afdb7e4a9ee902d9e935b7`.
Ruff lint and format checks pass for 116 files. All 153 local Markdown targets
resolve after correcting one historical-index link; `git diff --check` passes.
The retention guard verifies 19 checkpoints and all 13 original compressed
hashes. The new evidence directory contains 9,446 bytes in Git, below the
1,000,000-byte limit; complete prepared assets remain outside Git.

The [representative checkpoint](../../examples/evaluation/evidence/portable-review-2026-09-12/README.md)
first verifies every original hash in the historical 159-file controlled-runtime
archive. Export changes three path-bearing files and preserves 156 byte-for-byte.
A separate CLI process verifies every source hash and transformation with private
bindings. The manifest contains no binding values. A second process verifies
the complete prepared package: 160 members including the manifest, 1,991,647
expanded bytes. Asset hashes, intended URLs, source references and test reports
are in the small committed index/acceptance/verification records. Local package:
`/tmp/rh-rw5-package-20260912`; source and review copies:
`/tmp/rh-rw5-original-20260912` and `/tmp/rh-rw5-review-20260912`. The private
binding file stays outside Git. No release was created or uploaded.

GitHub reports the repository public (`gh repo view --json nameWithOwner,isPrivate`),
so the earlier private-repository assumption has been corrected. Historical
operator paths remain in Git; these derived copies do not satisfy RW-5's original
repository-wide removal check. RW-5 and RW-6 remain `in_progress`; the first
actual reviewed asset upload/download is still outstanding. No provider or paid
model calls occurred. Independent benchmark review, provider access, spending
approval and measured baseline/search/final remain pending; the shared goal is
active. Next actions: inspect this checkpoint's hosted checks, decide the next
publication step for the prepared public-repository assets, and address the
remaining original-path acceptance without invalidating runtime evidence.

PR #16 CI observation: the first current-head runs `34722228062` and
`34722233873` were cancelled while older implementation-head run `34722208937`
remained active. After confirming every earlier branch run terminal and that
the implementation-head run succeeded, `/root` reran `34722233873` once.
Attempt 2 targets exact head `e03c498930906b6efa48812168e02780a78a78da`; its
last observation during this handoff is `in_progress`, not a success claim.

Publication: implementation commit `cf21dc26c01805001cf1f2687e1f3a3117003613` and evidence/documentation commit `86eed6b97750168e961a2ef6f067ce75050821d6` are pushed to `origin/feat/portable-evidence-export`. [PR #17](https://github.com/Madhavan113/researchharness/pull/17) is open and ready for review, stacked on `feat/evidence-retention`. All preceding checkpoint commits are already on origin. Hosted checks remain pending and no merge or release publication occurred.

### September 12, 2026 — RW-6 artifact retention ownership

Owner: `/root`; bounded RW-6 tooling/policy work `done` on `feat/evidence-retention`, based on PR #15 head `b58bf44a84407d1069039499411e53aaaede5968`. RW-6 as a whole remains in progress until a reviewed archive is uploaded and downloaded successfully. The previous goal turn made progress: it completed RW-13, passed 1,378 required tests and published PR #15. Hosted run `34721663969` was queued at this task's initial poll; it subsequently passed both jobs without being restarted.

Scope: define GitHub release assets as the storage for future complete checkpoint archives, retain small hash-bound metadata and reproduction instructions in Git, implement deterministic archive preparation and independent file/hash verification, and enforce a sub-1-MB Git footprint for each new evidence checkpoint in CI. Preserve original evidence bytes and historical checkpoint hashes; do not migrate or delete historical archives, upload unreviewed host/private data, or label prepared assets as published. Intended files: evidence packaging/policy helpers and focused tests, a legacy checkpoint inventory, ignore rules/CI, artifact documentation and trackers. Acceptance: exact archive/index verification, traversal/link/duplicate/tamper rejection, deterministic output, source-commit binding without redundant checkpoint-source copies, explicit asset download/hash instructions, and CI refusal of oversized or unregistered new archive blobs. RW-5 identity/path cleanup remains separate; no provider/model calls or real spending are involved.

### September 12, 2026 — RW-6 artifact retention tooling handoff

Owner: `/root`; the [retention tools](../../src/research_harness/evaluation/evidence_assets.py), [policy and commands](../evidence-retention.md), [legacy inventory](../../examples/evaluation/evidence-policy.json), [34 new tests](../../tests/test_evidence_assets.py), ignore rules and ordinary CI guard are complete locally. New checkpoint directories must stay below 1,000,000 Git bytes. Future archives and complete compressed inventories use GitHub release assets; small indices bind compressed hashes, every member, the declared source commit and intended locations. Preparation verifies that the commit exists locally, preserves input bytes, normalizes tar metadata and publishes its completion index only after verification. It never uploads, extracts or executes artifacts. The declared source revision still needs comparison with observed runtime fingerprints before claiming execution provenance. This tooling checkpoint is published in [PR #16](https://github.com/Madhavan113/researchharness/pull/16), stacked on PR #15.

The guard checks all 18 existing checkpoints (27,209,764 bytes) and pins the exact hashes of all 13 historical compressed files. It rejects new compressed files even if renamed as text or force-added past ignore rules, and refuses oversized new checkpoints. The policy explicitly retains the older root-level workflow report. No historical archive, report or raw hash changed; no large runtime evidence was added to Git. The checkpoint changes tooling, tests, policy/ignore configuration, workflow, README and documentation.

Final required verification:

~~~sh
RH_TEST_REQUIRE_RUNTIME=1 \
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
uv run --locked --extra mcp pytest --tb=short \
  --basetemp=/tmp/rh-rw6-required-20260912 \
  --junitxml=/tmp/rh-rw6-required-20260912.xml
~~~

Result: **1,412 passed, zero failures/errors/skips**, 297.85 seconds. Report `/tmp/rh-rw6-required-20260912.xml` SHA-256 `d4e6307fefd6c8f9854f14fcfc7ffe61525716ab48098557193d0561109708d3`; console log has the same stem. The final focused command `uv run --locked --extra mcp pytest tests/test_evidence_assets.py --tb=short --junitxml=/tmp/rh-rw6-tools-final-20260912.xml` passed all 34 cases in 1.15 seconds; report SHA-256 `21b2f70f1f649decf5e597d49306df59d375060ab96166193696e84b4dd06b3b`. Tests cover deterministic byte preservation, changed compressed/member hashes, traversal/links/duplicates/missing files, bounds, failed preparation, source-commit lookup and real CLI/policy behavior. The first focused run had one incorrect expected byte count (295 instead of the authored 297), corrected before the successful runs; its original XML remains `/tmp/rh-rw6-tools-20260912.xml`, SHA-256 `80f780e72f524cdc8f6353d616201bb510a2d292d4ee54b4a8f3e9adb265cf28`.

Standalone CLI acceptance packaged the already committed RW-13 performance-report directory using source commit `b58bf44a84407d1069039499411e53aaaede5968`, repository `Madhavan113/researchharness` and proposed tag `evidence-verification-cache-2026-09-12`. Output `/tmp/rh-rw6-package-20260912` has private `0700` permissions. A separate verifier process confirmed all three original files and 3,707 expanded bytes; result `/tmp/rh-rw6-package-verification-20260912.json`. Prepared archive: 1,681 bytes, SHA-256 `fec5a86d573aa13f5727d9bb939de8f9562fe662bed0854d1107c24e0f374fa8`; compressed inventory: 271 bytes, SHA-256 `9da1d1cdfcf4a879823ba32cabd983da801b742c000b88f07d380c8ce73f6390`; index: 870 bytes, SHA-256 `72c14d4108383ca47cafb796ce144c3342e6770efe6774267bc94e0fcd1144c5`. Availability remains `prepared`: no release, upload or download occurred. These are tooling fixtures, not a new research run or published runtime evidence.

`uv run --locked ruff check src tests` and `uv run --locked ruff format --check src tests` pass (114 files). The documented policy command passes against this checkout; all 132 checked local Markdown targets resolve and `git diff --check` passes. All local execution handles are terminal and Docker reports no running containers. PR #15's hosted ordinary and required-runtime checks pass and its description now records that result. No model-provider calls or spending were involved.

Publication: implementation/verification commit `74bc17ab55cf26eb829253738a22488d19107aa7` is pushed to `origin/feat/evidence-retention`. PR #16 is open and ready for review with base `perf/artifact-verification-cache`; its hosted checks are pending, not claimed passed. Next action: check PR #16 CI, then complete RW-5 export hygiene and RW-6's first reviewed asset upload/download round trip. The tools preserve raw bytes and therefore do not remove embedded operator paths or private content. Independent benchmark review, provider access, the pending spending decision and measured baseline/search/final remain outstanding; the shared goal remains active.

### September 12, 2026 — RW-13 verification efficiency ownership

Owner: `/root`; RW-13 `done` on `perf/artifact-verification-cache`, based on PR #14 head `4730b582c07c4e8f01857e57ec3bcc18403efc04`. The previous goal turn made progress: private feedback copies and legacy/interrupted cleanup were verified by 1,352 required tests and published in PR #14; PR #13 hosted checks also passed. PR #14 run `34720652114` was active at this task's initial poll and subsequently passed both jobs, as recorded in its handoff below.

Scope: measure and reduce repeated immutable-artifact verification in strategy sessions, proposer workspaces, search/archive controllers and registered shared-budget runs, with bounded process-local memoization. Recheck directory membership and file identity/permissions/size/mtime/ctime before reuse, conservatively bypass recent or unsupported metadata, retain fresh public audits and live ledger comparisons, and report missing/moved registered evidence as a budget-ledger error before new work. Intended files: a shared verification helper; strategy/session, workspace/archive/controller and budget/pilot/runner integration; targeted integrity/recovery tests; an offline performance fixture; strategy/budget/testing guides and trackers. Acceptance: before/after artifact-read measurements; repeated calls avoid full verification of unchanged settled artifacts; same-size restored-mtime edits, replacements, links, additions/removals, changed controls and ledger rollback still fail or reverify; cache results cannot be mutated by callers; memory is bounded; required real-runtime checks remain green. Model budgets, authorization, historical artifacts and benchmark contents are unchanged. No live model calls are part of this work.

### September 12, 2026 — RW-13 verification efficiency handoff

Owner: `/root`; RW-13 is complete and published in [PR #15](https://github.com/Madhavan113/researchharness/pull/15), branch `perf/artifact-verification-cache`, stacked on PR #14. Completed strategy events, proposer feedback/workspace inventories, search/archive artifacts and registered-run gateway proofs now reuse successful verification while complete membership, POSIX metadata and controls match. Each memo bounds retained built-in data to 32 MiB and 4,096 entries; recent or unsupported metadata and oversized scans fall back to the original verifier. Returned data cannot mutate retained proofs, and a process restart starts cold. Journals, expected inventories, frozen host implementation, workspace parent privacy and live ledger comparisons remain fresh; public audits, workspace closure and budget reconciliation still read the underlying evidence. Missing/moved registered evidence raises `LedgerEvidenceError` without changing the ledger or starting work.

Changed files: [shared memo](../../src/research_harness/verification.py); session, workspace/archive/controller and budget/pilot/runner integrations; six existing test modules plus shared fixtures and [memo regressions](../../tests/test_verification.py); [performance fixture](../../examples/evaluation/verification_cache_fixture.py), its small reports, strategy/budget/testing guides and trackers. Twenty-six new cases cover reuse, restored-mtime edits, replacement, membership changes, links, changed controls, current ledger consistency, bounded retention, concurrent clearing and moved-run recovery. Existing real implementation-freeze and isolated-runtime tests remain enabled.

Final required verification:

~~~sh
RH_TEST_REQUIRE_RUNTIME=1 \
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
uv run --locked --extra mcp pytest --tb=short \
  --basetemp=/tmp/rh-rw13-required-20260912 \
  --junitxml=/tmp/rh-rw13-required-20260912.xml
~~~

Result: **1,378 passed, zero failures/errors/skips**, 297.69 seconds. Report `/tmp/rh-rw13-required-20260912.xml` SHA-256 `e3501c81647e805ad7be76f9a78a80d4f0fbe2241fdd5038ec52f400ae4bc85b`; console log has the same stem. The preceding focused command selected `test_verification`, `test_strategy_session`, `test_optimization_workspace`, `test_optimization_controller`, `test_optimization_archive`, `test_dispatch_budget`, `test_research_pilot` and `test_optimization_runner` with `uv run --locked --extra mcp pytest --tb=short`: **323 passed, 10 expected runtime skips**, 80.43 seconds. Its report `/tmp/rh-rw13-focused-20260912.xml` SHA-256 is `f684b38dfa3a72b5e43ddc40c50e0f8227d77749d15259901fed9970a7de19b6`; the final required suite ran those skipped cases successfully.

The [before/after fixture reports](../../examples/evaluation/evidence/verification-cache-2026-09-12/README.md) bind the exact source and fixture hashes. Eight warmed listings over sixteen 2-MiB files and their readable copy went from **256 full artifact opens / 512 MiB to zero**. Observed elapsed time was 0.219779 versus 0.012375 seconds; metadata scans remain, and this single warm filesystem measurement is not a model-efficiency or disk-throughput claim. Both runs completed fresh closure and removed the private copy, with zero Docker executions or model requests. Commands used `uv run --locked --extra mcp python examples/evaluation/verification_cache_fixture.py --out /tmp/rh-rw13-verification-before-20260912` and the same command with `--out /tmp/rh-rw13-verification-final-20260912`. Only 3,707 bytes of report/README evidence enter Git; generated binary trees and host-specific workspace records stay outside it.

Ruff lint and format pass for `src tests examples/evaluation/verification_cache_fixture.py` (113 files). All 166 local Markdown file targets checked before publication resolve; report/source hashes and `git diff --check` pass. All local test handles are terminal and Docker reports no running containers. PR #14's hosted checks pass and its description now records the result. No paid/live model calls, model-limit changes, historical artifact rewrites or private held-out edits occurred.

Publication: implementation/verification commit `94281c05dcf34385c5241d12489296fa0756e23a` is pushed to `origin/perf/artifact-verification-cache`; PR #15 is open and ready for review with base `fix/private-proposer-feedback`. Earlier checkpoint commits are also on origin. Its [hosted run `34721663969`](https://github.com/Madhavan113/researchharness/actions/runs/34721663969) subsequently passed both ordinary tests/lint and required Docker/Omnigent checks at head `b58bf44a84407d1069039499411e53aaaede5968`; the PR description records the result. Next action: continue artifact handling. Artifact handling (RW-5/RW-6), conditional exploration-limit review (RW-15), independent benchmark review, provider access, the pending spending decision and measured baseline/search/final remain outstanding. This software checkpoint does not complete the shared implementation goal.

### September 12, 2026 — RW-15 private feedback ownership

Owner: `/root`; bounded RW-15 feedback-permission work `done` on `fix/private-proposer-feedback`, based on PR #13 head `dfb14ca10d333ebeeca465565789e44533820d60`. The previous goal turn made progress: it fixed admission/selection recovery, passed all 1,341 required tests and published PR #13. Its latest hosted run `34720051522` is confirmed pending at this task's first poll; an earlier push run is still active, so neither is restarted merely because the latest checks have not begun.

Scope: copied feedback gets a private host parent from initial creation while the child bind mount remains readable by the unprivileged sandbox. Record the new layout durably, preserve recovery of legacy flat copies, reject unexpected ownership/layout changes and remove only the recorded owned paths after execution quiescence. Intended files: `optimization/workspace.py`, workspace/recovery/runtime tests, strategy documentation and shared trackers. Acceptance: private permissions before the first copied byte; real Docker reads complete feedback; interrupted parent/view/copy/removal recovery without replay; original feedback and foreign files preserved; legacy recovery still works. No changes to model/workspace budgets, benchmark content, historical evidence or spending authorization. The remaining exploration-limit item stays conditional on measured-run evidence.

### September 12, 2026 — RW-15 private feedback handoff

Owner: `/root`; the copied-feedback permissions item is complete and published in [PR #14](https://github.com/Madhavan113/researchharness/pull/14), branch `fix/private-proposer-feedback`, stacked on PR #13. New copies have a `0700` host parent before the first copied byte, with a readable child mounted read-only into Docker. The recorded layout distinguishes new copies from legacy flat copies. Tools check parent permissions and ownership; cleanup rejects symlinked parents, unexpected siblings and unknown layouts, and removes only the recorded child and empty parent after execution quiescence. Interrupted parent/view creation, copying and either deletion boundary recover without replay. Original source bytes/permissions and directly mounted source trees are preserved.

Changed files: [workspace implementation](../../src/research_harness/optimization/workspace.py), [workspace tests](../../tests/test_optimization_workspace.py), [strategy guide](../strategy-optimization.md), this tracker and remaining work. Eleven new regression cases cover the privacy/recovery boundary. Existing actual-Docker checks now compare complete binary feedback and verify private-parent removal; the hard-exit fixture forces an owned copy and confirms cleanup after terminating the proposer process. Model settings, execution budgets, dependency versions and historical artifacts are unchanged.

Final required verification:

~~~sh
RH_TEST_REQUIRE_RUNTIME=1 \
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
uv run --locked --extra mcp pytest --tb=short \
  --basetemp=/tmp/rh-rw15-private-required-20260912 \
  --junitxml=/tmp/rh-rw15-private-required-20260912.xml
~~~

Result: **1,352 passed, zero failures/errors/skips**, 318.57 seconds, including all 66 workspace cases, actual Docker and pinned Omnigent. Report `/tmp/rh-rw15-private-required-20260912.xml` SHA-256 `85ac5c43b907d997a42d1cb4f45b235ca39eb70d8b6168dfbcbbbe92abfd6894`; console log has the same stem. The original implementation failed the privacy-before-copy regression under an explicitly shared temporary root (`/tmp/rh-rw15-private-before-v2-20260912.xml`, SHA-256 `661ea6c706ea01ce9c1cb2a58b7badde82e38c67fb80321315f3f0e6e82f6eb8`). The initial workspace run had 65 passes and one macOS read-only-directory rename failure in legacy-fixture setup; the fixture now enables the move and restores the original legacy permissions. Its targeted test and the final full suite pass, and the failed setup's recorded copy was recovered and removed. The original report remains `/tmp/rh-rw15-private-workspace-20260912.xml`, SHA-256 `1b896f85a850a1495b5993570787daa994e443349db1b9cd97192a521c2c0116`.

Ruff lint/format pass for `src tests` (110 files); local Markdown targets and the diff are checked before publication. All local execution handles are terminal and Docker reports no running containers. PR #13's latest [hosted run `34720051522`](https://github.com/Madhavan113/researchharness/actions/runs/34720051522) subsequently completed both ordinary tests/lint and the required Docker/Omnigent job successfully at head `dfb14ca10d333ebeeca465565789e44533820d60`; its PR description records the result. The run was never restarted merely because it was pending or slow. No live model-provider or paid calls were made.

Publication: implementation/verification commit `551f58f3c5548464c0e3e8089a15ec55b35ced54` is pushed to `origin/fix/private-proposer-feedback`; PR #14 is open and ready for review with base `fix/selection-admission-checks`. Its [hosted run `34720652114`](https://github.com/Madhavan113/researchharness/actions/runs/34720652114) subsequently passed both ordinary tests/lint and the required Docker/Omnigent job at head `4730b582c07c4e8f01857e57ec3bcc18403efc04`; the PR description records that result. Next action: complete RW-13 and the artifact-handling follow-ups. RW-15 remains open only for considering exploration tools/limits after measured runs exist. Independent benchmark review, provider access, the spending decision and measured baseline/search/final remain outstanding; this permissions checkpoint does not complete the full goal.

### September 12, 2026 — RW-15 admission and compatibility ownership

Owner: `/root`; bounded RW-15 admission/compatibility work complete on `fix/selection-admission-checks`, based on PR #12, head `9230c91`. The previous goal turn made progress: RW-14 was completed, verified by 1,337 required tests and published; PR #11's hosted checks also passed. PR #12 run `34719113011` was pending at this task's initial poll; it subsequently completed both ordinary tests/lint and the required Docker/Omnigent job successfully at head `9230c915333c4f5e31b0b64048d722fc981b92ec`. Its PR description now records that hosted result.

Scope: prevent selection/finalization from omitting a completed but unadmitted proposal after a crash; record the built-in runtime's SDK-policy differences in frozen comparison limitations; declare the directly imported AnyIO dependency; document exact proposer model identity, empty archived logs and fresh-process audit provenance; replace stale current verification claims. Intended files: optimization controller and recovery tests, comparison plan/tests, package metadata/lock, strategy/research/Omnigent guides and shared trackers. Acceptance: reproduce the last-iteration admission crash, reject selection before revocation or private reads, resume admission without another proposer dispatch and retain every candidate; reject malformed older selection records; preserve locked package versions and runtime/budget controls. Temporary feedback permissions and exploration limits remain separately tracked RW-15 work, so this bounded checkpoint alone will not close the whole item. No live provider calls or spending decisions are part of this work.

### September 12, 2026 — RW-15 admission and compatibility handoff

Owner: `/root`; the bounded checkpoint is complete and published in [PR #13](https://github.com/Madhavan113/researchharness/pull/13), stacked on PR #12. The overall RW-15 item remains `in_progress`. Selection now checks completed/admitted proposals, explicit failed-proposal recovery, every reserved candidate slot and terminal candidate outcomes before revocation or freezing. The same guard applies when `select`, `step` or `run` reopens a saved selection and before `final` enters private evaluation. Refusal leaves the journal and proposer unchanged; resuming interrupted admission uses retained files without another proposer dispatch. Older incomplete selections are rejected, not rewritten or unfrozen. Final-recovery accounting remains available.

Changed implementation: [search controller](../../src/research_harness/optimization/controller.py), [comparison plan](../../src/research_harness/evaluation/controller.py), their controller tests, `pyproject.toml` and `uv.lock`. New plans disclose direct `max_retries=0` / `min(90, deadline_seconds)` versus the pinned Omnigent `RetryPolicy` of seven retries / 120 seconds, together with the gateway's retry rejection and shared bounds. Runtime behavior is unchanged. AnyIO is declared directly under the MCP extra; `uv lock --offline` preserves all 60 locked package names/versions. The strategy, comparison, service and Omnigent guides document exact response-model identity, the four hash-verified empty log streams, same-project fresh-process audit provenance and current verification evidence. Historical archives remain unchanged.

Final required verification:

~~~sh
RH_TEST_REQUIRE_RUNTIME=1 \
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
uv run --locked --extra mcp pytest --tb=short \
  --basetemp=/tmp/rh-rw15-required-20260912 \
  --junitxml=/tmp/rh-rw15-required-20260912.xml
~~~

Result: **1,341 passed, zero failures/errors/skips**, 593.70 seconds, using actual Docker and the pinned separate Omnigent environment with authored model/source responses. Report `/tmp/rh-rw15-required-20260912.xml` SHA-256: `27da70ffdb0d5223684b56b0937b63096fafcc42ab49ad00e33a35ccb970d038`; console log has the same stem. The original implementation failed all four admission/older-selection regressions (`/tmp/rh-rw15-admission-before-20260912.xml`, SHA-256 `21ca156095f3ca58006a4884e1147cb897191d3d5cc22a44832985b911deb690`). The initial guard passed the 28-test controller module; the full run above additionally verifies `run` replay and the final dependency/plan changes. Ruff check and format check pass for `src tests` (110 files); local Markdown targets and `git diff --check` pass. All local execution handles are terminal, and Docker reports no running containers.

Remaining dependencies: feedback-copy permissions still need a private host parent that preserves sandbox readability and durable cleanup/recovery; exploration tooling/limits need measured-run evidence before changing the design. RW-5/6 artifact handling, RW-13 repeated hashing, independent benchmark review, provider access and the pending spending decision remain open. No paid or live model calls were made. Publication: implementation/verification commit `cb63dfc525f40f6c8fadae75962a148b776bcef3` is pushed to `origin/fix/selection-admission-checks`; PR #13 is open and ready for review with base `test/reproducible-runtime-checks`. All earlier checkpoint commits are also on origin. PR #12 hosted validation passed; the new checkpoint's hosted checks are pending and are not claimed as passed. Next action: check PR #13 CI, then continue the remaining offline review work.

### September 12, 2026 — RW-14 test maintenance ownership

Owner: `/root`; RW-14 done in [PR #12](https://github.com/Madhavan113/researchharness/pull/12), branch `test/reproducible-runtime-checks`, based on PR #11, head `cdc596c`. The previous goal turn made progress: it completed and published the RW-10 schema checkpoint and retained the full local test evidence. PR #11 run `34718217512` was pending at this task's first poll; its later successful completion is recorded below.

Scope: make deadline and sandbox guard tests distinguish the controls they exercise, consolidate the shared fake runner, add controller coverage of the actual implementation-freeze scanner, and audit lifecycle tests for accidental dependence on editable development cases. The two repeated local runaway-test failures reached the test's one-second wall-clock limit before its expected output/memory errors; production limits and cleanup guarantees remain intact. Intended files: `tests/conftest.py`, affected gateway/strategy/controller/archive test modules and test fixtures as needed, the testing guide and shared trackers. No production limit will be relaxed. Acceptance: independently exercise wall-clock, output and memory failures with cleanup assertions; remove brittle elapsed-time windows; use the real scanner in a controller freeze regression; keep lifecycle fixtures stable when benchmark cases change. Record both targeted results and the still-pending hosted checkpoint result.

Progress: the shared observation fake runner is in `conftest.py`, with seven cross-module fixture imports removed. Six lifecycle/leakage modules now use a four-case snapshot with eight original content hashes and recorded provenance; the snapshot is 15,516 bytes including its README. Benchmark/workbook tests still validate the editable benchmark. Two new controller tests invoke the real scanner and reject changed working/frozen MCP implementation bytes before execution. The live HTTP deadline test uses a controlled clock/timer, preserves a late partial chunk and interrupts the silent connection at the original deadline. Docker tests separately exercise the one-second watchdog, 1,024-byte output cap and 64-MB memory ceiling; output/memory tests use the existing default ten-second watchdog and additionally require truncation/OOM evidence. Production source is unchanged.

Targeted verification: the deadline and two scanner checks passed (`3 passed in 3.88s`), report `/tmp/rh-rw14-targeted-v2-20260912.xml`, SHA-256 `1de15e961ecbbf17bef4ea4549c89550792ada81abf042f4a831ebfff3337ac8`. Required real-Docker guard checks passed (`3 passed in 27.37s`, zero skips), report `/tmp/rh-rw14-sandbox-20260912.xml`, SHA-256 `7b8edcbc8500a673cd35fc5702c764582136e085cc54371eaf1a140f9ccc2243`. Both jobs in [PR #11's hosted run](https://github.com/Madhavan113/researchharness/actions/runs/34718217512) also passed, including its complete required Docker/Omnigent suite. Its original local timeout reports remain historical evidence; the PR description now records the hosted result.

Full verification on the final test changes:

~~~sh
RH_TEST_REQUIRE_RUNTIME=1 \
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
uv run --locked --extra mcp pytest --tb=short \
  --basetemp=/tmp/rh-rw14-required-20260912 \
  --junitxml=/tmp/rh-rw14-required-20260912.xml
~~~

Result: **1,337 passed, zero failures/errors/skips**, 794.90 seconds. JUnit SHA-256: `19d9bc99cd510901aaf1e17b699ec7d0db369ecc709c0e81c25ce4e8cc537c7e`; console output: `/tmp/rh-rw14-required-20260912.log`. The full run includes actual Docker/Omnigent comparisons, normal-runner follow-up, every sandbox guard, snapshot-backed lifecycle tests and runtime skip enforcement. No owned running containers remain. `uv run --locked --extra mcp ruff check src tests`, `ruff format --check src tests` and `git diff --check` pass.

Handoff: changed shared/test-specific fixtures, gateway/sandbox/controller coverage, the four-case snapshot with its provenance README, the testing guide and shared trackers. Production source, the authored twenty-case development package and historical evidence archives are unchanged. All 80 local Markdown link targets resolve. Implementation commit `9261de5` is pushed and PR #12 is open for review. Next action: inspect its required CI; continue RW-5/6 artifact hygiene/retention, RW-13 hashing and RW-15 remaining correctness/maintenance work. The goal stays active: human benchmark review, provider access, spending decisions and measured baseline/search/final remain unfinished.

### September 10, 2026 — RW-10 strict provider schema ownership

Owner: `/root`; RW-10 done in [PR #11](https://github.com/Madhavan113/researchharness/pull/11), branch `fix/strict-provider-schemas`, based on [PR #10](https://github.com/Madhavan113/researchharness/pull/10), head `0d70ff9`. It satisfies the review item's offline acceptance option; live provider acceptance remains in M0. The previous turn made progress: RW-12 was implemented, passed 1,315 required tests and complete synthetic search/final acceptance, then was committed/pushed and published ready for review. Both jobs in [PR #10's pull-request run](https://github.com/Madhavan113/researchharness/actions/runs/34560118417) have since passed; its earlier push run was superseded.

Scope: remove default annotations from model-facing strict schemas, represent defaulted inputs as nullable and apply domain defaults at the host boundary; preserve domain validation, bounds and operation-replay behavior. Intended files: a shared provider-schema adapter, direct discovery, MCP server, focused schema/SDK/protocol tests, an offline preflight artifact helper if useful, and research/Omnigent/status documentation. Inspect the actual SDK and normal Omnigent tool conversion; validate generated search/proposal and MCP input schemas without claiming live provider acceptance. The existing `search_preflight.py` targets Keenable MCP metadata/query compatibility and cannot prove OpenAI schema acceptance. Acceptance includes default-free strict emitted schemas, all properties required with appropriate nullability, valid host defaults, unchanged explicit values and meaningful invalid-input errors through actual SDK/MCP requests. OpenAI documentation skill applied; current official sources support the numeric/array bounds, so retain them. No live model calls or spending records are authorized by this task; pending provider/budget and independent-review gates remain unchanged.

September 12 resumption: source changes persisted, but earlier temporary test reports and the separate Omnigent checkout did not. Restored the clean pinned checkout using `sh examples/omnigent/setup.sh /tmp/researchharness-omnigent-be042b39` and confirmed the Docker image digest. Fresh required-suite and offline preflight evidence replace reliance on the missing temporary reports. The user has authorized committing/pushing all checkpoints and opening PRs; earlier checkpoints are published in PRs #2–#10. This task finishes and publishes RW-10, without changing the pending live-run decisions.

Implementation: the shared provider adapter removes only schema `default` annotations, requires every named property, closes objects and makes defaulted nonnullable inputs nullable. Normalization against the original domain schema restores defaults before validation, including nested source/proposal models, while preserving explicit values and meaningful nulls. Direct search, `source_json` probes, structured output and all fourteen MCP tools use it. MCP advertises typed search filters without coercing legacy explicit values before operation hashing. New tests cover actual SDK serialization/parsing, nullable defaults, bounds, required-null rejection before admission, unchanged probe/proposal identity and receipt replay without extra source/provider calls. The actual normal Omnigent/Docker comparison also checks emitted schemas. Configuration models, dependency pins and historical archives are unchanged.

Fresh offline preflight: `uv run --locked --extra mcp python examples/omnigent/provider_schema_preflight.py --out /tmp/rh-rw10-schema-preflight-final-20260912 --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python` passed with OpenAI SDK 2.54.0, Pydantic 2.13.5 and Agents SDK 0.13.6. It captured three direct function schemas, one proposal schema, fourteen MCP input schemas and fourteen successful strict conversions. All five artifact hashes and the unchanged helper were independently rechecked. Report SHA-256: `ca0b008a3d57f28a2baa5a3dc88b2f14a14f916e3e6a07361a045a4283969e93`; helper SHA-256: `c75886e0cf594cd536ce9ef4f13acb28cf7851a5b1953acf9f2d36c7c15b74ea`. Provider requests: zero. The fixtures establish serialization, conversion and default restoration; they do not establish live API acceptance or native Omnigent's strict-flag selection.

Required full run on September 12:

~~~sh
RH_TEST_REQUIRE_RUNTIME=1 \
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
uv run --locked --extra mcp pytest --tb=short \
  --basetemp=/tmp/rh-rw10-required-final-20260912 \
  --junitxml=/tmp/rh-rw10-required-final-20260912.xml
~~~

Result: **1,331 passed, four failed, zero skipped**, 991.40 seconds. JUnit SHA-256: `7e264a6a3d1a71409cc9174d788275a8693f363a02e02d2a66e983ff63ff2b42`; console output: `/tmp/rh-rw10-required-final-20260912.log`. All new schema/SDK/protocol cases and the controlled Docker/both-runtime comparison passed. The failures were the normal-runner context follow-up, Docker create-rejection check, and two runaway-candidate cases. Captured execution records show a sandbox wall-clock timeout and `docker info` exceeding 15 seconds; the runaway cases reached their one-second wall-clock limit before the expected output/memory-specific failures. The suite continued and no running containers remained afterward. No runtime limit or assertion was relaxed. A targeted recheck covers those failures plus the third runaway case in a fresh directory; retain both reports rather than describing the original command as green.

Recheck, using the same three `RH_TEST_*` variables above:

~~~sh
uv run --locked --extra mcp pytest \
  tests/test_strategy_context.py::test_actual_normal_runner_followup_prunes_only_the_previous_completed_interaction \
  tests/test_strategy_sandbox.py::test_actual_cli_create_rejection_stays_quiescent_after_recovery \
  tests/test_strategy_sandbox.py::test_actual_runaway_candidates_are_bounded_and_removed \
  --tb=short --basetemp=/tmp/rh-rw10-runtime-recheck-20260912 \
  --junitxml=/tmp/rh-rw10-runtime-recheck-20260912.xml
~~~

Result: **three passed, two failed, zero skipped**, 81.61 seconds. The normal-runner context follow-up, create-rejection check and intentional wall-clock case passed. The output/memory cases again reached their one-second wall-clock limit before their more specific expected errors. Their test and sandbox implementation are unchanged by this checkpoint; no assertion or runtime limit was relaxed. JUnit SHA-256: `125e0bd19c9d084ae9207a6083e74af28057f9a48bff28e1658de090c1b85b75`; console output: `/tmp/rh-rw10-runtime-recheck-20260912.log`. Required CI must establish the hosted result; this is not a fully green local suite.

Handoff: changed the shared provider adapter, direct discovery and MCP server; five test modules; the offline preflight helper; README, research service/example guides, accepted plan and shared trackers. `uv run --locked --extra mcp ruff check src tests examples/omnigent/provider_schema_preflight.py` and the corresponding `ruff format --check` pass for 110 files; all 135 local Markdown link targets and `git diff --check` pass. RW-10 is closed on its explicit offline acceptance branch. Implementation commit `7642fd6` is pushed and PR #11 is open for review with the local failures disclosed. Next action: inspect required CI, retaining the local timeout evidence. Remaining artifact hygiene/retention and efficiency/test-maintenance work is tracked in RW-5/6/13/14/15. The shared goal stays active; human case review, provider access, spending decisions and measured baseline/search/final remain unfinished.

### September 10, 2026 — RW-12 split isolation and leakage audit ownership

Owner: `/root`; RW-12 done on `fix/split-leakage-audit`, based on [PR #9](https://github.com/Madhavan113/researchharness/pull/9), head `2daff67`. The previous turn verified that every checkpoint was pushed and polled the still-running hosted jobs; it was a verified wait, with no implementation changes. Both jobs were still running at this task's initial poll; their successful completion is recorded below.

Scope: compare source registrable domains using a reproducible offline suffix list, and audit candidate source/instructions for development task strings at admission. Intended files: benchmark split validation, optimization controller and a bounded audit helper, focused benchmark/controller/audit tests, dependency metadata if needed, strategy guide and goal/plan/remaining-work documentation. Record advisory `leak_suspect` findings without automatic rejection. Do not consult held-out data or evaluator predicates for candidate feedback; preserve revocation before private split validation. Acceptance includes sibling-subdomain overlap, domain edge cases, a candidate containing a development URL, durable findings across restart/feedback, and no held-out/evaluator information in audit output. Work uses synthetic/offline fixtures; measured runs and pending external decisions remain separate.

[PR #9's hosted run](https://github.com/Madhavan113/researchharness/actions/runs/34558785893) has now completed successfully: both ordinary tests/lint and required Docker/Omnigent checks passed. Implementation scope also includes the synthetic final-package constructors and combined runtime acceptance helper, because the former sibling-domain fixtures correctly fail the stronger check. Historical archives and the private held-out draft remain untouched.

Design: source-domain checks include source choices, gap alternatives and public fixture URLs; IDNA/IP normalization, private PSL boundaries and the unknown-suffix last-two-label rule run without HTTP or user caches. `tldextract==5.3.2` pins the suffix snapshot (SHA-256 `b69315c085d53972724b8f2df111ffc329b0c84fe0a47d62c8c91655cc774a38`); four packages are added and no existing locked version changed. New searches freeze a host-only catalog from development task ids/briefs and public fixture URLs, never expected configurations or scoring rules. Baseline/candidate audits record bounded advisory matches, exact file/catalog hashes and a journal `leak_suspect` flag. Reports enter later proposer snapshots; the full catalog stays private. Manual review remains separate, and no-match does not establish absence of overfitting. Old search records remain readable without fabricated audit results.

Handoff: changed benchmark validation, the search controller and the new audit helper; package pins; four test modules; two synthetic runtime helpers; README, strategy/evaluation guides, plan and shared trackers. Tests establish PSL wildcard/exception/private boundaries, IDNA/IP aliases, sibling overlap across source/gap/fixture inputs, private-value-free split errors, exclusion of evaluator fields, bounded findings, unchanged eligibility for suspicious candidates, preserved reports across restart/feedback and rejection of modified audit files/catalogs. Syntactically invalid submissions retain their reports. The final-phase test rejects sibling-domain overlap before provider dispatch after search has closed.

Final validation: `RH_TEST_REQUIRE_RUNTIME=1 RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --locked --extra mcp pytest --tb=short --basetemp=/tmp/rh-rw12-required-final-20260910 --junitxml=/tmp/rh-rw12-required-final-20260910.xml` passes **1,315 tests, zero failures/errors/skips**, in 279.28 seconds. JUnit SHA-256: `57937ae618b75c437a4d1cd77ef5feadb8830013a97f98cc17cdb0e28d03e6d0`. Ruff lint/format passes for source/tests and both changed helpers (108 files); local documentation links and `git diff --check` pass. Earlier focused validation passed 138 tests after correcting a new fixture's `http_status` key to the schema's `status_code`. An initial full run passed 1,313 tests, then code review found an IDNA-mapped terminal-dot alias on IP literals; its normalization fix and two added cases passed 21 targeted domain checks and the final full run above. No earlier output directory was reused.

Runtime validation: `uv run --locked --extra mcp python examples/evaluation/strategy_search_fixture.py --out /tmp/rh-rw12-search-runtime-20260910 --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python --image python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285` passes the actual coding proposer, Docker and Omnigent/MCP workflow: three iterations, seven development and seven private final evaluations, 125 synthetic requests, seven bound audit reports and 1/3/5 reports in successive proposer snapshots. Selection/revocation precede private package creation; final retries execute nothing; final contents and the full audit catalog remain outside feedback. Acceptance SHA-256: `3130f3773e284908e26683c18d88a4357f609e7a4aaab1b9a0f3b227e57089d3`. A fresh process reopens the finalized controller, verifies its immutable inventories, recomputes all seven reports (all `no_match` for this authored fixture), and checks snapshot boundaries. Its `rw12-post-run-verification.json` SHA-256 is `27cf7e17c2db2f6a33ebc03a1fe94ef8a9a2baa627ca2f0b25517ece70919f95`, under the same output directory (real path `/private/tmp/rh-rw12-search-runtime-20260910`). The first inspection used macOS's `/tmp` symlink and was correctly refused before reading controller state; using the canonical directory passed. Both processes completed; no Docker containers remain. These are synthetic software checks, not a human review or measured optimization result.

Publication target: a ready PR stacked on #9. Next: inspect its hosted checks, then prepare RW-10's strict-schema fixes offline; live preflight still needs the pending provider/budget decisions. The broader goal remains active: remaining review work, independent human case review, provider access, the spending decision and measured baseline/search/final remain pending. No paid model calls, authorization records, historical-archive regeneration or private held-out draft changes were made.

### September 10, 2026 — RW-11 storage parity ownership

Owner: `/root`; RW-11 done on `fix/storage-parity`, based on [PR #8](https://github.com/Madhavan113/researchharness/pull/8), head `6b1add3`. The previous turn made progress: RW-9 was implemented, passed 1,264 required tests with zero skips, and was published ready for review. [Its hosted run](https://github.com/Madhavan113/researchharness/actions/runs/34556290612) is now complete with both ordinary and required-runtime jobs passing; the superseded push run is cancelled.

Scope: verify and repair migration/index upgrades, inspection checkpoint isolation, local CLI pipeline run lookup, SQLite lock scope/shared/reentrant behavior, concurrent registry registration, S3 content integrity on deduplication and worker environment isolation. Intended files: store, registry, backend, blobs, research/job services, CLI, focused backend/service/registry/job/CLI tests, backend/service documentation and shared trackers. Use a forward migration for already-upgraded databases if needed; changing an old migration alone cannot repair them. Acceptance includes old-schema upgrades with data preserved, inspections leaving collection state unchanged, real lock/concurrency checks, local run visibility, corrupt blob rejection and detached workers retaining only necessary runtime/storage configuration. Work remains offline with local fixtures; provider/spending/human-review gates remain pending.

Lock design: hold the legacy `writer.lock` in shared mode, then acquire a shared/exclusive lock for the question-scoped pipeline. This preserves exclusion against older exclusive writers while permitting unrelated pipelines and concurrent readers. Handle nested ownership in `Store.writer` so reentry does not sweep its own active runs; reject shared-to-exclusive upgrades until the shared scope exits. Added Portalocker 4.3.0 (including its Windows shared-lock extra) through `pyproject.toml` and `uv.lock`; its typed contention errors preserve RW-9's distinction from backend failures. Registry registration uses `ON CONFLICT` for the intended unique keys while unrelated integrity errors still surface. SQLite reserves outer write transactions before reading; nested savepoints preserve rollback behavior.

Handoff: migration 5 restores the missing index without editing applied migrations; tests use the [original version-1 schema](../../tests/fixtures/sqlite-v1-ee2148b.sql), extracted without executing code from commit `ee2148b`, and exercise already-upgraded local/shared databases. Inspections finish through the probe path and leave collection checkpoints unchanged. Backend summaries now supply local pipeline runs to both CLI listings and research context. S3 deduplication verifies existing bytes and conditional uploads verify the winning object after a race. Worker environments contain only selected OS/network/storage settings, excluding model keys and Python path overrides. Changed seven production modules, three test modules, the frozen SQL fixture, package metadata/lockfile and backend/status documentation. Boto3's minimum is now 1.35.2: the upstream Botocore models for 1.35.0/1.35.1 lack `IfNoneMatch`, while 1.35.2 includes it. The locked/tested Boto3 remains 1.43.89; no existing package version changed.

Full validation: `RH_TEST_REQUIRE_RUNTIME=1 RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --locked --extra mcp pytest --tb=short --basetemp=/tmp/rh-rw11-required-full-20260910 --junitxml=/tmp/rh-rw11-required-full-20260910.xml` passes **1,285 tests, zero failures/errors/skips**, in 268.14 seconds. JUnit SHA-256: `1c72cf241a244815c19421ab3c1cbbf309781f08f914f10e0b9f86150b9572ee`. Includes 21 added cases plus stronger existing S3 assertions, actual child-process locks/termination, legacy-writer exclusion, reader concurrency/reentry, registration races and rollback, checkpoint isolation, local CLI run lookup and subprocess environment checks. Earlier focused runs passed 47 and 89 checks. Ruff lint/format (104 files), documentation links and `git diff --check` pass.

Shared validation: `uv run --locked --extra mcp python examples/evaluation/shared_storage_fixture.py --out /tmp/rh-rw11-shared-final-20260910` passes **166 tests, including 59 Postgres/S3 cases, zero failures/errors/skips**. The actual workflow passes schema-5 discovery/proposal registration, detached collection/export, storage restart with fresh clients, S3 export recovery after local deletion, exact-operation retry, replay without publication, writer exclusion and question isolation. Native S3 tests reject corrupted objects even after a forced stale HEAD, exercising MinIO's conditional PUT behavior. Both task-owned containers were removed; paid calls are zero. Acceptance SHA-256: `763bc0e1870abf248fd674cf94b6f8f5412f1970a8be4c406614aecc8e95f17b`; shared JUnit SHA-256: `b4383532d49361bd48667f836c845c3ce2abb23d331d25d0ec36cc8015a1175c`. Reports are in the named output directory. Historical committed archives were not regenerated.

Publication target: a ready PR stacked on #8. Next: inspect that head's hosted checks, then address RW-12's split/leakage audit or RW-10's offline schema preparation. The broader goal remains active: remaining review work, independent human case review, provider access, the spending decision and measured baseline/search/final are still pending. No paid model calls, authorization records or private held-out changes were made.

### September 10, 2026 — RW-9 MCP/CLI parity ownership

Owner: `/root`; RW-9 done on `fix/mcp-cli-parity`, based on [PR #7](https://github.com/Madhavan113/researchharness/pull/7), head `1c40f36`. Ownership began after RW-8 was implemented, verified with 1,238 required tests and published. [PR #7's hosted run](https://github.com/Madhavan113/researchharness/actions/runs/34554980401) is complete: both ordinary tests/lint and required Docker/Omnigent runtime tests passed. The cancelled push run was superseded by this successful pull-request run.

Scope: validate search arguments before admission, classify writer contention by type, preserve the stored model when a CLI resume omits `--model`, correct annotations for tools that reconcile jobs, and remove full pipeline/context scans from envelope budget reporting. Intended files: MCP server, research/search service boundaries, CLI entry point, focused protocol/service/CLI tests and service/goal documentation. Acceptance includes identical invalid-search budget behavior across both paths, nonretryable source failures even when a URL contains `writer`, real writer-contention handling, saved-model resumption, current lightweight budget reads and correct reconciliation hints. Model access, spending approval, human case review and measured runs remain separate pending gates.

Checkpoint handoff: the user requested committing and publishing all checkpoints. After fetching `origin`, all existing implementation branches matched their remote checkpoints and PRs #2–#7 remained open in dependency order. Published the remaining tracker and `docs/remaining-work.md` changes in commit `2157b70` and opened [PR #8](https://github.com/Madhavan113/researchharness/pull/8) in draft, stacked on #7. That initial documentation checkpoint recorded ownership and investigation; the implementation is recorded below.

Checkpoint validation: `git diff --check` passes; a Python local-file link check resolves all 68 relative file links in the two changed documents. Reviewed the diff and kept RW-9 marked in progress. No runtime tests were rerun for this documentation-only checkpoint; the preceding implementation's local test evidence is recorded under RW-8 below.

Implementation resumed by `/root` on the same branch. The previous turn made progress by committing and publishing the remaining checkpoint as PR #8 and recording PR #7's completed hosted checks. RW-9's intended files also included shared service error types and the job service's ownership/idempotency errors. Chose a fresh registry-only budget read rather than a cross-process cache because other resumed hosts can consume operations independently. Both jobs in [the initial documentation checkpoint's hosted run](https://github.com/Madhavan113/researchharness/actions/runs/34555567258) passed at head `0d0371b`; the implementation requires its own hosted run after publication.

Handoff: search filters validate before service admission while existing request shapes/hashes remain compatible. Added typed research errors and used `WriterBusy` itself for contention classification; source text cannot impersonate domain failures, and recorded terminal failures replay without a retryable classification. CLI resume inherits the saved model and runtime binding when settings are omitted. The three job-reconciling tools advertise their mutations. Envelope budgets use current registry counts without full context/pipeline scans; backend failures remain explicit, preserve completed receipts and primary errors, and disable automatic retry until repaired. Changed the CLI, MCP server, research/job services, new `services/errors.py`, two research test modules and three documentation files. The service guide describes the error and resume behavior.

Validation: `RH_TEST_REQUIRE_RUNTIME=1 RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --locked --extra mcp pytest --tb=short --basetemp=/tmp/rh-rw9-required-full-20260910 --junitxml=/tmp/rh-rw9-required-full-20260910.xml` passes **1,264 tests, zero failures/errors/skips**, in 272.77 seconds. JUnit SHA-256: `ec5811a4aa769c90566c3eb597eed2512bb15a6dd40436c184bf877092fcbda3`. Includes 26 added cases: invalid-filter budget/operation-id parity through actual MCP and direct handlers, legacy hashes/replays, a mocked 404 from a `writer` host, actual lock contention/retry, spoofed provider messages, cross-host counts, explicit budget-read failures with receipt recovery, and actual CLI stdio resumption under a saved custom model. The focused six-module suite passed 126 tests; one subsequently tightened assertion initially expected the probe-specific error code for an invalid proposal and was corrected to the existing `invalid_argument` behavior before the full passing run. Ruff lint/format (104 files), 76 local documentation file links and `git diff --check` pass. Runtime checks use the pinned Docker/Omnigent environments and authored model responses; no live model or billing result is claimed.

Publication target: update PR #8 with this implementation and make it ready for review. Next: inspect the updated head's hosted checks, then take RW-11's storage parity fixes. The broader goal stays active; remaining software review work, independent human case review, provider access, the spending decision and measured baseline/search/final remain pending. No paid model calls, authorization records or private held-out changes were made.

### September 10, 2026 — RW-8 proposer recovery ownership

Owner: `/root`; RW-8 done on `fix/proposer-workspace-recovery`, based on [PR #6](https://github.com/Madhavan113/researchharness/pull/6), head `ef8b7fe`. The previous goal turn made progress: RW-1's strategy controls were implemented, verified and published, and all checkpoint branches are pushed. [PR #6's hosted CI](https://github.com/Madhavan113/researchharness/actions/runs/34544483696) is now complete: required Docker/Omnigent tests pass 1,214 checks with zero skips in 396.94 seconds; ordinary tests pass 1,184 with 30 explicitly reported runtime skips in 309.14 seconds. Its superseded push run is cancelled, not a test failure.

Scope: reproduce and fix ordinary proposer workspace failures involving path aliases/case collisions, unencodable tool arguments and definitively unsuccessful Docker creation. Intended files: `optimization/workspace.py`, relevant sandbox/cleanup code if evidence supports a safe distinction, focused workspace/proposer/sandbox tests, the strategy guide and shared tracker. Acceptance: malformed tool calls leave unchanged usable workspace state, valid follow-up editing/submission succeeds, and failures before container creation become quiescent without weakening lost-acknowledgement recovery. No model spending, private held-out changes or evidence archive regeneration is needed.

Handoff: reproduced the local case-insensitive overwrite and escaping Unicode error, then added planned path and existing-directory checks before mutation. Case/normalization aliases and file/directory conflicts now fail as ordinary tool errors; escaped unpaired surrogates retain request evidence and consume a call without executing code. Actual proposer fixtures repair both errors and submit valid candidates. Docker rejection handling is deliberately evidence-based: the exact missing pinned-image diagnostic with `--pull=never`, or an unknown host-supplied flag rejected by CLI parsing, establishes non-creation and is retained in the report. A generic nonzero exit or proxy error does not. Recovery preserves known `not_created` outcomes while unknown acknowledgements still cannot claim quiescence. Changed two production modules, three test modules and the strategy/status documentation; historical evidence archives and private inputs are unchanged.

Validation: `RH_TEST_REQUIRE_RUNTIME=1 RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --locked --extra mcp pytest --tb=short --basetemp=/tmp/rh-rw8-required-full-20260910 --junitxml=/tmp/rh-rw8-required-full-20260910.xml` passes **1,238 tests, zero failures/errors/skips**, in 266.09 seconds. JUnit SHA-256: `3f178f35e6a805bdcd349c039e72b02d0f0ba5db95ad82d0315d29130b352d6f`. This includes 24 added regressions, the actual pinned Docker/Omnigent checks and the actual CLI rejection test. Ruff lint/format, local documentation links and `git diff --check` pass. All model responses and command-failure stubs are authored fixtures.

Next: publish the complete checkpoint in a PR stacked on #6, inspect its hosted checks, then take RW-9's MCP/CLI parity gaps. The broader goal remains active; independent human case review, provider access, the spending decision and measured baseline/search/final are still pending. No paid calls or new authorization records were made.

### September 10, 2026 — RW-1 strategy-control ownership and decision

Owner: `/root`; RW-1 done on `feat/strategy-control-surface`, based on PR #5. The preceding goal turn made progress: RW-2 was implemented, verified with 1,189 required tests and published as `ca0030a`; [both hosted jobs passed](https://github.com/Madhavan113/researchharness/actions/runs/34542759570) for that implementation. RW-1 decision: widen the searchable behavior under the original full goal. Enable context selection and `finalize_on_stop` in the maintained seed. Derive finalization from verified completed observation decisions, keep it persistent for the discovery, reject new exploration operations before service admission, and preserve receipt replays. Direct requests then request a structured proposal without discovery tools; the controlled Omnigent gateway retains evidence/proposal tools and records independently verifiable stopping evidence. Fixed round/deadline/spending limits and proposal validation remain authoritative. Legacy manifests/archives retain their explicit older behavior and digests.

Intended files: strategy configuration/session/projection and request-control helpers; direct discovery, MCP admission, controlled gateway and independent usage verification; baseline source/manifest and proposer contract; focused strategy/direct/MCP/gateway/runtime tests; strategy guide, accepted plan and shared tracker. Acceptance includes a useful saved proposal after a stop, denied further discovery without budget consumption, durable replay/restart, invalid decisions failing closed, verified gateway accounting and candidate context selection changing an actual forwarded request. Context preserves every protected message and the current reasoning/tool chain. Single-brief histories may have no removable groups; do not claim otherwise. Candidate instruction text remains editable; per-request finalization guidance is host-owned. No paid calls or held-out edits are authorized.

Handoff: implemented the configuration/session controls, new `strategies/stopping.py`, direct and MCP admission, gateway projection and independent proof validation, maintained seed and proposer contract, with tests in `test_strategy_stopping.py`, `test_strategy_context.py` and `test_comparison_runtime.py`. Updated README, strategy guide, plan and remaining-work status. The runtime check exposed pinned Omnigent's `str(dict)` serialization of MCP outputs; a literal-only parser now supports that envelope as well as direct JSON and MCP text blocks, without executing expressions. Actual isolated tests verify the maintained seed removes only older completed interactions, both runtimes save a valid proposal after stopping, and the independently verified gateway archives retain the stopping event. No immutable historical evidence archives or private held-out packages changed.

Validation used the [required configuration](../testing.md): `RH_TEST_REQUIRE_RUNTIME=1`, the pinned `python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285` image, and `RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python`. The initial `uv run --locked --extra mcp pytest --tb=short --basetemp=/tmp/rh-rw1-required-full-20260910 --junitxml=/tmp/rh-rw1-required-full-20260910.xml` completed with 1,208 passed, one failed, zero skipped in 261.06 seconds; that sole failure was the serialization mismatch. Its JUnit SHA-256 is `db05f4434f5b5e479fb34bd0ebba548d36a2504374d792a0e7ac1f4dd80dd04e`. After the fix and additional regressions, `uv run --locked --extra mcp pytest tests/test_strategy_stopping.py tests/test_strategy_context.py tests/test_comparison_runtime.py::test_controlled_strategy_uses_real_docker_and_both_runtime_paths --tb=short --basetemp=/tmp/rh-rw1-required-recheck-20260910 --junitxml=/tmp/rh-rw1-required-recheck-20260910.xml` passed all 64 checks with zero skips in 48.21 seconds. That JUnit SHA-256 is `bb70dbba905779bdda3da489b11115ca4f7b47624f794d2b647f4ee350e473b2`. These are a full run followed by affected-check reruns, not a claim of a second full-suite run on the final source. Ruff lint/format and documentation-link/diff checks pass.

Publication scope: commit the complete RW-1 checkpoint and open a PR stacked on [PR #5](https://github.com/Madhavan113/researchharness/pull/5), as requested. Earlier checkpoint branches are already pushed and PRs #2–#5 remain open. Next: inspect the new PR's hosted full-suite results, then continue the remaining software follow-ups. Human benchmark review, provider access, the spending decision and measured baseline/search/final remain pending. No paid calls were made; the shared goal remains active.

### September 10, 2026 — RW-2 evaluator and benchmark handoff

Owner: `/root`; done for RW-2 on `fix/benchmark-discrimination` / [PR #5](https://github.com/Madhavan113/researchharness/pull/5), stacked on PR #4. Changed evaluator predicates and scoped gap evidence, separate data/research metrics, evaluator-version frontier checks, fixture response decoding and authored policy execution, the builder and forty-one generated JSON inputs, focused tests, and evaluation/plan/status documentation. No runtime archives or private held-out inputs changed.

The `research-discovery-development-v2` manifest has SHA-256 `95be76617d447022d5f59917e3d5b364eca2e58122d7504b145cf9f42c6ebcac`. Running `uv run --locked --extra mcp python -m research_harness.evaluation.fixtures examples/evaluation/development/manifest.json /tmp/rh-rw2-final-development-20260910` produces twenty strict authored-policy wins, forty valid artifacts and macro quality 0.9495238095238095 versus 0.08333333333333333. Authored data recall remains 0.8416666666666666; research recall is 0.9208333333333334. Local `comparison.json` SHA-256: `11366c8e7951f6351e1f929aed6a23328490f66c674f8354f6ca69ce511c1a44`. Re-evaluation with the final source reproduces the entire stored comparison exactly. A clean temporary builder run reproduces all forty-one JSON files byte for byte. These are software-policy diagnostics, not model performance or reviewed benchmark results; diagnostic outputs remain local and use fresh output directories.

Validation: **1,189 tests pass, zero failures/errors/skips**, in 244.17 seconds. Exact command: `RH_TEST_REQUIRE_RUNTIME=1 RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --locked --extra mcp pytest --tb=short --basetemp=/tmp/rh-rw2-final-required-full-20260910 --junitxml=/tmp/rh-rw2-final-required-full-20260910.xml`. JUnit SHA-256: `930f52c7cd7c58f89742da40dbb39f8a5fe53dedb2ad7152d32835a88ba85592`. The first full run exposed archive tests assuming the first two development cases had source alternatives, and a final test relying on source order. Stable archive lifecycle inputs and an explicit wrong-identity selection preserve those tests' original assertions; all 156 archive/final/controller checks then passed, followed by the clean full rerun. All sixty added regressions are included. Ruff check/format (including the builder), local documentation links and `git diff --check` pass.

Next: record and implement RW-1's strategy-surface decision under the accepted full goal, then continue the remaining software follow-ups. Human benchmark review, provider access, the spending decision and measured baseline/search/final are still pending; no paid calls were made. The goal remains active. The prior documentation-only PR head `e080370` passed [both hosted CI jobs](https://github.com/Madhavan113/researchharness/actions/runs/34541316713); hosted checks for this implementation follow publication and are not included in the local test claim above.

### September 10, 2026 — RW-2 evaluator implementation ownership

Owner: `/root`; in_progress in [draft PR #5](https://github.com/Madhavan113/researchharness/pull/5). The preceding turn made progress by publishing the investigation checkpoint and confirming all local commits reached GitHub. The accepted plan now records the version-2 scoring design: field-specific pointer subsets, independently specified and scoped capture-backed gaps, separate data/research metrics, half recall credit for evidenced unresolved needs, duplicate/contradiction protection and evaluator-version separation. Intended files and acceptance remain as recorded below. Implement the evaluator and adversarial regressions first, then revise the twenty-case fixtures and rerun their comparison. Existing archives and private held-out data remain unchanged; no measured or paid run is authorized.

### September 10, 2026 — RW-2 investigation checkpoint and handoff

Owner: `/root`; RW-2 remains in_progress on `fix/benchmark-discrimination`, based on [PR #4](https://github.com/Madhavan113/researchharness/pull/4) while it remains open. This checkpoint changes only this tracker and remaining-work status. Evaluator, schema, fixture and test changes are still pending. RW-7 is complete: its final documentation-head [CI run](https://github.com/Madhavan113/researchharness/actions/runs/34540617833) also passed both jobs on `f51a1ff87b2eab295dcf3223dcf0727966444232`.

Investigation reproduced the existing benchmark's limitation with `uv run --locked --extra mcp python -m research_harness.evaluation.fixtures examples/evaluation/development/manifest.json /tmp/rh-rw2-before-20260910`. Both policies completed twenty offline fixture cases. Authored-selection macro quality is 0.8583333333333334 versus first-listed-source 0.23333333333333334: thirteen strict wins and seven ties. Tied cases are `export-notices`, `license-history-access`, `sanctions-current-history`, `rates-workbook-unsupported`, `vendor-valid-alternatives`, `weather-alert-feed` and `legislation-unsupported-votes`. These are fixture-policy scores, not a model or optimization result. The local diagnostic `comparison.json` has SHA-256 `9fa91c71e6ed3866885033c656ee1096534c75528ec9bc93bd41a523cfaa2464`; benchmark manifest SHA-256 is `61c840a81101600e2847ced9cf21a68c3bab3163c13ae8b6d8b0b71d46aaea06`. The diagnostic output is not committed; use a fresh output directory to reproduce the comparison.

Scope for continued work: documented field-specific source-predicate semantics, independently evidenced credit for correctly reported unsupported/access gaps, discriminating twenty-case development fixtures with distractors, and a reproducible answer-key-versus-naive comparison. Intended files: evaluator, benchmark schema, authored fixture executor/builder and generated development package; focused evaluator/benchmark tests and existing fixtures as necessary; evaluation guide, accepted plan, remaining work and this tracker. Acceptance: valid pointer reordering/supersets pass, false or unsupported gap claims receive no credit, and the answer-key policy strictly exceeds first-listed selection in every development case.

Next action: record the scoring design before implementation, keeping fulfilled data requirements distinct from correctly evidenced gaps; then add regressions and revise the fixtures. The gap-credit formula remains undecided. Preserve valid alternative sources, held-out isolation, trustworthy receipts and human-review gates. Keep existing runtime archives historical. Validation for this documentation checkpoint: all 63 local Markdown links and the new handoff anchor resolve; `git diff --check` passes. The diagnostic above exercises existing behavior and does not validate a fix. No paid calls or private held-out edits are authorized by this subtask. The shared implementation goal remains active.

### September 10, 2026 — RW-7 verified CI and gateway-capture handoff

Owner: `/root`; done for RW-7 on `ci/required-runtime-checks` / [PR #4](https://github.com/Madhavan113/researchharness/pull/4), stacked on PR #3. Implementation commits `442ad31` and `b8eb2c3` are published. Changed files: pytest defaults and shared hooks, thirteen subprocess contract cases, two deterministic gateway framing regressions, the gateway's relayed-header selection, GitHub Actions, testing guide, README, checkpoint scope, accepted plan, remaining work and this tracker. The earlier CI failure is retained below; its complete-evidence assertion was preserved and the underlying response-completion race was fixed.

The [corrected hosted run](https://github.com/Madhavan113/researchharness/actions/runs/34539869346) is terminal and successful on `b8eb2c3f0eac1f07eb5435579650bbaab238846f`: the ordinary Ubuntu job passes **1,101 tests with 28 reported runtime skips** in 253.52 seconds, plus Ruff; the required Docker/Omnigent job passes **1,129 tests with zero skips or failures** in 346.88 seconds. Both use the frozen MCP dependencies, Python 3.13.12 and uv 0.11.8. The existing setup script installed the unchanged pinned Omnigent checkout, and Docker pulled the pinned image. Setup and tests run without provider credentials. Superseded push runs were cancelled by workflow concurrency, not retried as failed evidence.

Local corrected-source verification passes **1,129 tests, zero skips, zero failures**, in 244.42 seconds (JUnit 244.384). Exact command: `RH_TEST_REQUIRE_RUNTIME=1 RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --locked --extra mcp pytest --tb=short --basetemp=/tmp/rh-rw7-framing-required-full-20260910 --junitxml=/tmp/rh-rw7-framing-required-full-20260910.xml`. Report SHA-256: `fcdddcd1ef104fff79445bf24da252ce779fbeeaff1e4909d1a662c54cdb6cf8`. The original required-run report hash is `111e45b344335bb58055f358d2d5f8136ed44d54260fb5e30e9cdb981e7066f2`. The two framing reproductions failed against the old gateway and pass against the fix in 1.34 seconds. The final full run includes every regression. `uv run --locked --extra mcp ruff check src tests` and `uv run --locked --extra mcp ruff format --check src tests` pass across 100 files; `go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12 .github/workflows/tests.yml`, local Markdown links and `git diff --check` pass. Reports remain local or in hosted command logs; no new runtime archive was committed.

Next: address RW-2 benchmark/scoring discrimination and RW-1's remaining strategy surface under the accepted full scope. Other offline review items remain open. Provider access, the spending decision, human benchmark review and measured baseline/search/final are still pending, so the broader shared goal stays active. This documentation handoff records the verified source commit; it does not alter tested code or workflow configuration.

### September 10, 2026 — RW-7 test visibility and CI ownership

Owner: `/root`; in_progress on `ci/required-runtime-checks`, based on the published RW-4 checkpoint while PR #3 is open. The previous goal turn made progress: initialization/accounting recovery was verified with 1,114 passing tests and published as `2d525f1`; RW-4 software acceptance is complete. Scope: visible default skip reporting, strict runtime configuration and skip failures, regression coverage for the pytest contract, and GitHub Actions for ordinary and fully configured synthetic checks. Intended files: `pyproject.toml`, `tests/conftest.py`, a bounded pytest-contract test module, `.github/workflows/`, testing documentation, README, remaining-work list and this tracker. Acceptance: ordinary execution displays skip reasons; strict runs reject missing runtime/dependency configuration and unexpected skips; configured local checks pass, and the hosted workflow is observed to completion. Provider/model calls remain synthetic; pending external model access, budget and benchmark review are unchanged.

Local verification before publication: **1,127 passed, zero skips, zero failures**, in 242.99 seconds. Exact command: `RH_TEST_REQUIRE_RUNTIME=1 RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --locked --extra mcp pytest --tb=short --basetemp=/tmp/rh-rw7-required-full-20260910 --junitxml=/tmp/rh-rw7-required-full-20260910.xml`. The 13 subprocess regressions also passed separately in 1.83 seconds. An actual optional Docker test with both runtime variables unset showed its skip reason; the same invocation with strict mode exited 4 before execution and named the missing configuration. Ruff passes across 100 Python files, actionlint v1.7.12 accepts the workflow, all 113 local links in six changed documents resolve, and `git diff --check` passes. Hosted CI remains to be observed after publication; RW-7 stays in_progress until that evidence is available.

CI follow-up scope: [PR #4](https://github.com/Madhavan113/researchharness/pull/4) is published at `442ad31`. Hosted ordinary tests reported one failure, 1,098 passes and 28 visible runtime skips: a completed response was archived as a lower bound in the failed-case usage regression. `/root` is reproducing the response-completion/shutdown boundary and owns the necessary gateway framing fix plus deterministic gateway/controller regression coverage. Do not weaken the complete-evidence assertion or hide the Linux failure. Preserve bounded shutdown and conservative incomplete-stream accounting. The required runtime job is still being observed; the shared goal and RW-7 remain in_progress.

The first [hosted PR run](https://github.com/Madhavan113/researchharness/actions/runs/34538909364) is now terminal: required runtime checks passed all 1,127 tests in 362.44 seconds; the ordinary job failed as recorded above. Deterministic JSON/SSE reproductions paused final usage capture after the body was relayed and showed that forwarded Content-Length allowed the HTTP caller to return before capture finished. The gateway now leaves downstream body framing to its existing connection-close behavior, so HTTP body completion follows final capture. Streaming bytes, bounded shutdown, incomplete-outcome holds and archive verification remain intact. Both new regression cases pass after the fix; complete local and hosted checks are being repeated on the corrected source before RW-7 acceptance.

### September 10, 2026 — RW-4 initialization and accounting handoff

Owner: `/root`; done for RW-4 software acceptance on `fix/budget-reservation-recovery`, included in [PR #3](https://github.com/Madhavan113/researchharness/pull/3), stacked on PR #2. Scope: complete the retained initialization/accounting implementation, verify the checkpoint and publish it under the user's checkpoint/PR authorization. Changed files: budget, dispatch-budget and independent gateway-usage verification; pilot and search runner; new initialization and provider-accounting tests; pilot/search regressions; budget guide, accepted plan, remaining-work list and this tracker. Earlier checkpoint commits remain on their published branches.

Initialization now retains an exact empty-state intent before creating the shared ledger/registry pair. Recovery covers write failure and process death, preserves the original ledger and authorization timestamp, and refuses changed/spent state or an established missing file without retained initialization evidence. Operator-reviewed provider accounting requires a sealed, independently verified error request and retained external final-charge confirmation bound to its request, operation and evidence hash. It checks consistency, not the authenticity of the provider or reviewer. Unknown tokens remain unknown; unsupported errors keep their full holds. Charges exceeding reservations, duplicate provider-request confirmations and conflicting terminal records are rejected. The CLI and subsequent reconciliation never replay provider work. Original archives and legacy terminal-row formats remain intact; pilot/search witness checks accept valid accounting updates and keep the confirmations outside feedback.

Verification: **1,114 tests passed, zero skips, zero failures**, in 240.79 seconds (JUnit 240.781). Exact command: `RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --extra mcp pytest -ra --tb=short --basetemp=/tmp/rh-rw4-accounting-full-20260910 --junitxml=/tmp/rh-rw4-accounting-full-20260910.xml`. Report SHA-256: `9b81e1643b534d20f7d0f31ffed612301c0f7b18f4995080921e038f58ce8317`. The pinned Docker image and clean Omnigent checkout were available. This adds 69 regression cases since the 1,045-test checkpoint. The accounting group separately passed 42 cases in 16.48 seconds, the initialization/pilot/search group passed 63 in 27.85 seconds, and the additional search accounting test passed in 1.37 seconds. Initial focused failures identified two test assumptions (finalizing unfinished cases and supplying both fixture handlers); both were corrected before the complete run. Ruff lint and formatting pass across 99 Python files, local links in all four changed documents resolve, and `git diff --check` passes. All provider responses and confirmations were authored fixtures; no paid calls occurred and no new runtime archive is committed.

Next: continue the other September 10 follow-ups, starting with RW-7 test visibility and then the remaining P0 strategy-surface/benchmark work. RW-4 is complete as a software change; real provider confirmations, access, the pending spending decision, human benchmark review and measured baseline/search/final remain external or unperformed work. The shared goal stays active, and this checkpoint does not claim measured model performance.

### September 10, 2026 — RW-4 initialization and accounting ownership

Publication continuation: `/root` is validating the retained initialization/accounting changes and adding regression coverage before committing them to the existing PR #3. This bounded checkpoint does not include other remaining-work items or paid evaluation.

Owner: `/root`; in_progress on `fix/budget-reservation-recovery` / PR #3. The previous goal turn made progress: commit `68ad052` added verified retry enforcement and the authorization CLI; all 1,045 configured tests passed and the PR was updated. Scope: recover interrupted initial ledger/registry creation from a durable intent shared by pilot and search preparation, and resolve dispatched provider-error holds only from sufficient retained accounting evidence. Intended files: budget, pilot, search runner, dispatch/usage verification as needed, focused failure/recovery tests, budget guide, plan, remaining work and this tracker. Initialization recovery must compare exact retained empty-state evidence and refuse changed or spent state, never recreate missing established funds. Existing ledger and terminal-witness formats remain compatible. Provider access, spending approval and human benchmark review are still pending; this work uses offline fixtures.

### September 10, 2026 — RW-4 retry and authorization handoff

Owner: `/root`; done for retry enforcement and the authorization CLI on `fix/budget-reservation-recovery` / [PR #3](https://github.com/Madhavan113/researchharness/pull/3); RW-4 remains in_progress. The previous turn made progress: all checkpoint commits were pushed, the configured suite passed 1,019 tests and PR #3 was opened. Changed files: gateway, independent gateway verifier and budget module; gateway, dispatch, verifier and actual-runtime regression tests; new budget CLI tests; budget guide, plan, remaining work and this tracker. The original scope and all measured-evaluation requirements remain unchanged.

The pinned Omnigent factory does not forward the authored retry policy. The controlled gateway now rejects SDK retry headers before reservation or dispatch and sends `x-should-retry: false` for provider responses and its own errors. New archives retain headers under `reject_automatic_retries_v1`, and the verifier checks that retries were never admitted. Legacy archives remain readable without claiming this enforcement. The installed SDK reproduced three provider calls for each 429, 500 and transport error before the fix; a lost-response reproduction dispatched twice. The new tests reduce those to one provider dispatch and reject the SDK's retry even when the first response was lost before receipt. Every other admitted request still needs its own round and reservation; the diagnostic header is not a cryptographic logical-call identity.

The budget CLI records caller-supplied external authorization or revocation, retains the ceiling/rates/charges/holds and avoids duplicate history entries. `BudgetLedger.open_existing` refuses to recreate a missing ledger, including when the file disappears between its initial read and locked initialization. It does not grant spending permission. All authorization records used by tests are fixtures.

Verification: **1,045 tests passed, zero skips, zero failures** in 225.97 seconds (JUnit 225.947). Exact command: `RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --extra mcp pytest -ra --tb=short --basetemp=/tmp/rh-rw4-retry-auth-full-20260910 --junitxml=/tmp/rh-rw4-retry-auth-full-20260910.xml`. Report SHA-256: `e90c1a5aab00768387787bf1febb8269a7f31e6d69b88bfe9e62093faec118ea`. Docker and the clean pinned Omnigent checkout were available. The four direct/actual-Omnigent 429/500 tests also passed separately in 10.58 seconds, each with one dispatch and one unresolved reservation. The focused gateway/dispatch/verifier group passed 233 tests before the additional archive/CLI/runtime coverage. Ruff lint and formatting pass across 97 Python files; all 87 local Markdown file links in the four changed documents resolve; `git diff --check` passes. Reports stay local and no new runtime archive is published. All model responses were synthetic; no paid calls occurred.

Next: implement provider-error accounting resolution using retained provider confirmation bound to the sealed error request; preserve unknown token usage rather than inventing zero tokens. Preserve historical terminal-witness equality when extending settlement evidence. Also recover the initial ledger/registry persistence boundary using retained initialization evidence, without resetting existing balances. These are remaining software tasks; provider access, the spending decision and human benchmark review remain pending external prerequisites. The shared goal stays active.

### September 10, 2026 — publish the current budget recovery checkpoint

Owner: `/root`; done for checkpoint verification and publication on `fix/budget-reservation-recovery`; RW-4 remains in_progress. The user requested committing and pushing all checkpoints and opening a PR. PR #1 is already merged and RW-3 is published in [PR #2](https://github.com/Madhavan113/researchharness/pull/2). Implementation commit `c3d2573` is pushed and [PR #3](https://github.com/Madhavan113/researchharness/pull/3) is open against `fix/final-candidate-eligibility`, so PR #2 precedes it. All earlier local checkpoint commits were already present on origin. Neither open PR was merged as part of this publication. The broader shared goal remains active.

Changed files: `evaluation/budget.py`, `evaluation/gateway_usage.py`, `evaluation/pilot.py`, `integrations/model_gateway.py`, three budget/pilot regression test files, the budget guide, remaining-work list and this tracker. The checkpoint preserves acknowledged reservation bindings when later admission is denied, releases verified unsent requests, writes an empty pilot registry before comparison preparation and requires durable dispatch markers before settlement, including when reopening a ledger. Invalid archives and uncertain outcomes retain holds. An acknowledgment arriving after archive sealing cannot rewrite the archive.

Verification: **1,019 tests passed, zero skips, zero failures**, including all seven new regression cases, in 231.09 seconds (JUnit 231.077). Exact command: `RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --extra mcp pytest -ra --basetemp=/tmp/rh-rw4-checkpoint-full-20260910 --junitxml=/tmp/rh-rw4-checkpoint-full-20260910.xml`. Docker and the clean pinned Omnigent checkout were available. The local JUnit report has SHA-256 `c7d2fe45577fd19069ca3fdeade7b43e973a4c6cfe8d3b8b003ffc2558920022`; no new runtime archive or private benchmark content is included in the checkpoint. `uv run ruff check src tests` and `uv run ruff format --check src tests` pass across 96 files. All 71 local Markdown file links in the three changed documents resolve; `git diff --check` passes. These are offline software checks with synthetic model responses.

Next: continue RW-4 with evidence-backed provider-error resolution, explicit Omnigent retry behavior and an authorization-recording CLI. The initial ledger/registry write boundary also still fails closed if registry persistence itself fails; the new recovery test covers failure in subsequent comparison preparation. HTTP 429/5xx without usage is insufficient proof of zero cost. Provider access, spending approval and human benchmark review remain pending; no paid execution is part of this checkpoint.

### September 10, 2026 — RW-4 budget recovery ownership

Owner: `/root`; in_progress on `fix/budget-reservation-recovery`, based on the published RW-3 branch while PR #2 is open. The previous turn made progress: RW-3 was implemented, verified with 1,012 tests and published. Scope: bind acknowledged reservations before any later admission denial; preserve recoverable pilot preparation; require dispatch before settlement; account for provider error responses without inferring zero usage from missing evidence; make retry policy explicit and provide an authorization-recording CLI. Intended files: budget, dispatch, gateway-usage, pilot and runtime-executor modules; model gateway and Omnigent runner bridge if needed; their tests; budget/strategy guides, plan, remaining work and this tracker. Acceptance: reproduce each failure offline, verify settlement/release only from sufficient evidence, retain unknown holds, then run relevant tests and the configured suite. Provider access, spending approval and human benchmark review remain pending; no paid execution is part of this task.

### September 10, 2026 — RW-3 final selection handoff

Owner: `/root`; done for RW-3 implementation, verification and publication. The resumed goal had new actionable evidence from commit `54c8b56`; the previous blocked handoff is superseded for offline software work. PR #1 is merged at `37b2277`; implementation commit `0889a7a` is pushed on `fix/final-candidate-eligibility` and included in [PR #2](https://github.com/Madhavan113/researchharness/pull/2). Changed files: `optimization/archive.py`, `optimization/controller.py`, archive/final/controller regression tests, the strategy guide, remaining-work list, README, plan and this tracker. No benchmark or historical evidence archive changed.

Final selection now uses a Pareto frontier restricted to positive macro quality and no failed development cases. It retains `ranked`, the diagnostic `raw_candidate_ids` and explicit exclusion reasons. A partially failed candidate cannot dominate an eligible one. Both the archive and coordinator validate the selected set and required baseline before beginning private evaluation. Ineligible legacy selections are refused without rewriting the selection; completed final retries keep their existing behavior. An ineligible baseline leaves the coordinator journal unchanged, creates no private output and dispatches no provider work.

Eight regression cases cover the cheap all-failed candidate (quality 0 at two tokens versus a quality-1 baseline at 100), zero quality without execution errors, partial failures, both kinds of ineligible baseline, actual final executor call selection, a legacy selection and coordinator rejection state. The final full suite passed **1,012 tests, zero skips, zero failures** in 229.63 seconds. Command: `RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --extra mcp pytest -ra --basetemp=/tmp/rh-rw3-final-full-20260910 --junitxml=/tmp/rh-rw3-final-full-20260910.xml`. Docker and the pinned Omnigent environment were available. The focused command `uv run --extra mcp pytest -ra tests/test_optimization_archive.py tests/test_optimization_final.py tests/test_optimization_controller.py --basetemp=/tmp/rh-rw3-final-focused-20260910 --junitxml=/tmp/rh-rw3-final-focused-20260910.xml` passed **138 tests** in 21.06 seconds. Both reports remain local; no new runtime archive was generated.

`uv run ruff check src tests` and `uv run ruff format --check src tests` pass across 96 Python files. All 151 local Markdown links in the five changed documents resolve; `git diff --check` passes. These are offline software checks with synthetic responses, not model-quality measurements. No paid calls occurred.

Next: RW-4 reservation/reconciliation fixes, then the remaining prioritized review items. The provider/budget and human-review questions remain pending; all twenty development cases and ten private held-out cases remain authored. The full goal stays active with its original scope. PR #2 is open for review; it has not been merged.

### September 10, 2026 — independent code review and continuation handoff

Owner: Claude Code review session (one lead, eight area reviewers); done for documentation. Changed files: new [checkpoint scope](../checkpoint-scope.md) and [remaining work](../remaining-work.md); README, AGENTS.md and this tracker link to them. No production, test, benchmark or evidence files changed.

The read-only review covered paper fidelity, sandbox isolation, the budget ledger, durable state, the MCP/Omnigent/CLI surface, the evaluation benchmark, test quality with evidence claims, and repository hygiene on HEAD `4f49495`. Storage, sandboxing, accounting and the tool surface held under adversarial reading; all recorded evidence hashes and counts reproduce; no secrets or held-out contents are committed. Six items are recorded as accepted limitations at merge: the searchable strategy surface is narrower than the plan's Milestone 6 wording (RW-1); the twenty-case benchmark cannot rank candidates and has never been used in a search run (RW-2); strict Pareto selection admits zero-quality candidates into the paid final phase (RW-3); provider error responses permanently lock their worst-case reservation (RW-4); archived evidence embeds operator machine paths and a hostname (RW-5); and each checkpoint adds tens of megabytes of archives without a retention policy (RW-6). The remaining-work list holds the full prioritized set with file anchors, fix directions and acceptance checks.

Validation: `uv run ruff check src tests` and `uv run ruff format --check src tests` pass. The full suite in the review environment, without Docker and with `RH_TEST_STRATEGY_IMAGE`/`RH_TEST_OMNIGENT_PYTHON` unset, reports 978 passed, 26 skipped, 0 failed; the recorded 1,004/0 result requires the full configuration. Local Markdown links in the changed documents resolve and `git diff --check` passes. Runtime-gated tests, Postgres and live providers were not exercised.

Next: merge the checkpoint, then work [remaining work](../remaining-work.md) in its suggested order, starting with RW-3 and RW-4. RW-1 needs an owner decision before implementation. Milestone statuses are unchanged.

### September 9, 2026 — measured evaluation dependency audit

Owner: `/root`; blocked on external prerequisites, with documentation handoff complete. Changed files: this tracker, README and the accepted plan; draft PR #1 carries the same status. No production, benchmark or private-package files changed.

The publication verification turn, subsequent readiness check and this revalidation all retained the same missing inputs. The previous turn was no progress, not a wait on a live execution. Current read-only checks confirm no `OPENAI_API_KEY` in the process, no project `.env`, and `authorization.status=draft` with no reference in both proposed budget configurations. Parsing the actual manifest-referenced case files confirms twenty authored development cases and ten authored private held-out cases, with zero reviewed cases. The private human-review checklist is present. The provider/budget question remains pending; the preceding turn also requested a human reviewer. No new paid or synthetic executions were started.

The accepted plan requires reviewed cases and matched measured comparisons. Current `execute_pilot_case` and the budgeted search runner require matching external spending approval; the measured baseline also requires reviewed cases. The published 1,004-test result and complete synthetic runtime acceptance establish software behavior, not those missing model measurements. No remaining independent implementation task was identified that would satisfy these external prerequisites. M0/M5/M6 and the shared goal are blocked, not complete; the original scope and all acceptance criteria remain intact.

Resume after provider access and a spending decision are recorded, and independent review of the development/private held-out packages is available. Recheck current model pricing and controls, run live compatibility, freeze and measure the matched baseline, then run budgeted strategy search and isolated final evaluation with the retained shared ledger. Keep the private checklist and benchmark contents local.

Validation: all 92 local Markdown links in the three changed documents resolve, and `git diff --check` passes. Runtime tests were not rerun for this documentation-only handoff. Both prior review agents are terminal with model-capacity errors; no running agent or execution is being awaited. Publication target: the existing `checkpoint/omnigent-research-harness` branch and [draft PR #1](https://github.com/Madhavan113/researchharness/pull/1).

### September 9, 2026 — budgeted search runtime ownership

Owner: `/root`; done for budgeted software wiring, verification, archival and checkpoint publication. Commit `f8af2f8` is pushed to `checkpoint/omnigent-research-harness`; draft PR #1 was updated and reported the matching head. The preceding goal turn made progress: combined runtime acceptance and all checkpoints were published through `04a1121`. Both previous agents have terminal capacity errors; root owned this continuation and its fresh-process artifact audit. Changed files: new `optimization/runner.py`, `tests/test_optimization_runner.py`, budgeted runtime example and draft configuration; the shared registry/evidence checks in `evaluation/pilot.py`; recovery artifact-loader support in `optimization/controller.py`; README, plan, budget/strategy guides and this tracker.

The new runner reuses `BudgetedRuntimeExecutor` and `DispatchBudget`. Its CLI prepares, executes and inspects search, runs private final evaluation, reconciles sealed evidence and recovers stopped attempts without replay. Research, coding proposals and final requests share one retained ledger across prepared revisions. Older pilot/search preparations check later search spending; missing recorded gateway evidence fails verification. Proposer budget receipts stay outside immutable attempt outputs and outside feedback. Reviewed model-mode cases and current matching external authorization remain required before live execution.

The [verification record](../../examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/verification.json) records **1,004 tests passed, zero skips**, with JUnit time 232.788 seconds (console 232.82). Exact command: `RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --extra mcp pytest -ra --basetemp=/tmp/rh-budgeted-search-suite-20260909 --junitxml=/tmp/rh-budgeted-search-suite-20260909.xml`. The focused runner/pilot/controller group separately passed 54 tests. Coverage includes registration, rollback across older preparations, exhaustion before dispatch, retained unknown holds, locked recovery, immutable retries and authorization gates. Final Ruff lint/format checks pass across 110 Python files.

The [accepted runtime run](../../examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/acceptance.json), retained at `/tmp/rh-budgeted-strategy-search-20260909-retry/`, completed three coding iterations, seven development evaluations and seven private final evaluations through actual Omnigent/MCP and Docker. All **125 synthetic requests** had matching durable dispatch reservations and settled from independently verified raw evidence, with zero remaining holds. The fixture reported 13,750 synthetic tokens and 15,000,000 settled nanodollars under its synthetic rate card; no real money was spent. Selection and permanent proposer revocation preceded final package creation. Completed final/search retries dispatched no further work. The [fresh-process audit](../../examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/independent-audit.json) verified seventeen gateway archives and 157 Docker executions: 140 research strategies, fourteen interface checks and three generated programs. No strategy containers remained afterward.

The first runtime attempt at `/tmp/rh-budgeted-strategy-search-20260909/` also reached `finalized` and settled all 125 requests, but its post-run example verifier called `.values()` on the event list. The example check was corrected and the complete command reran successfully in a new fixture directory. Production/test bytes did not change after the passing suite. The first diagnostic and executed source are retained; the accepted run's exact source is also retained separately from a subsequent formatting-only change, whose Python AST matches. The two runtime attempts used separate fixture ledgers and made 250 synthetic requests in total; neither is a measured model run.

The [6,479-file archive](../../examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/index.json) preserves complete development feedback, exact proposer snapshots, closed attempts, Docker checks, fixture budget witnesses and source. Its size is 9,688,901 bytes; SHA-256 is `7af0293e450f0279139c56ff0dd4f35a0996dfd5bafe1d5f0dbe3514713a90f3`. All payload hashes and all ten earlier archives/8,097 payloads were verified. The checkpoint retains 115 source/test/example hashes. Private final execution/package contents, the representative ten-case draft and native runtime credentials stay local. Reviewer source, host metadata and budget records must never become proposer feedback.

Next: obtain independent review of the twenty development cases and ten private held-out drafts, resolve provider access and the already-pending spending decision, and perform live compatibility plus measured baseline/search/final evaluation. The broader goal remains active; this checkpoint completes the budgeted software wiring and fixture acceptance, not M0/M5/M6's measured requirements. Publication is complete, all runtime processes used for this acceptance have exited, and no paid calls occurred.

### September 9, 2026 — combined runtime checkpoint publication

Owner: `/root`; done for checkpoint publication. Commit `43fea13` is pushed on `checkpoint/omnigent-research-harness` and included in [draft PR #1](https://github.com/Madhavan113/researchharness/pull/1); GitHub reported the matching head after publication. The user requested all checkpoints committed and a GitHub PR. Changed files are `examples/evaluation/strategy_search_fixture.py`, `examples/evaluation/search_runtime_fixtures.py`, `tests/test_search_runtime_fixtures.py`, the new immutable evidence directory, README, plan, strategy guide and this tracker. Production implementation bytes remain unchanged from `238e99d`.

The complete original run at `/tmp/rh-combined-strategy-search-20260909/` exited successfully. It executed three actual coding-proposer attempts, six generated candidates plus the baseline, seven development evaluations and seven private final evaluations through normal Omnigent/MCP. Every proposer inspected complete recorded development output, wrote executable candidates, tested them in Docker and submitted them. Selection and permanent proposer revocation preceded creation of the final package. Completed final retries and reopening the search triggered no new execution. All fourteen independent fixture scores were 1.0; all seven development candidates tied at 770 synthetic tokens. Totals were 125 synthetic requests, 13,750 synthetic tokens and 157 actual Docker executions. No model-quality or cost-improvement result follows.

The [verification record](../../examples/evaluation/evidence/combined-strategy-search-2026-09-09/verification.json) retains the exact combined runtime command and six passing helper tests: `uv run --extra mcp pytest -ra tests/test_search_runtime_fixtures.py --basetemp=/tmp/rh-combined-search-checkpoint-tests-20260909 --junitxml=/tmp/rh-combined-search-checkpoint-tests-20260909.xml`. Ruff lint and formatting passed across 106 Python files. The [fresh-process audit](../../examples/evaluation/evidence/combined-strategy-search-2026-09-09/independent-audit.json) verifies exact task bindings, all three sealed proposal inventories, independently verified proposer usage and all strategy sessions. The separate agent source audit found no gap; root completed the artifact audit after the agent's capacity interruption. No strategy containers remained after completion. The earlier 985-test result remains its own dated record; the full suite was not rerun for these example/test additions.

The [6,380-file archive](../../examples/evaluation/evidence/combined-strategy-search-2026-09-09/index.json) retains complete development feedback, exact proposer input snapshots, closed proposer attempts, interface checks and reproduction source. Its size is 9,349,237 bytes; SHA-256 is `8a878106a8be6db524654dddc6fa023eaf48d6ff2ffca212b46f82893f25b661`. All payload hashes were verified, along with all nine earlier archives and 1,717 payloads. Private final packages/executions, the representative held-out draft and native runtime credentials are excluded. Reviewer source and host metadata must never become proposer feedback.

Root also completed the separate private ten-case draft under ignored `.researchharness/private-benchmarks/heldout-draft-20260909/`. Its `HUMAN_REVIEW_PRIVATE.md` and `latest-validation.json` remain local. The validation ran twenty actual service/evaluator executions using offline fixtures, matched all authored expectations and validated disjointness against twenty development cases. All ten cases remain `authored`, with zero human-reviewed cases. The report is retained privately at `validation/ee5715f5f2534a78969cbb905db9e9e1/report.json`; its SHA-256 is `2add65445edb8c2d1a97ec702cea947d611a214d2c6d6a1ac93c284ed13a6147`. No private task contents were added to Git or search feedback.

Publication is complete; the PR description now records combined runtime acceptance, retained checkpoints and measured-evaluation limits. The broader goal remains active: human review, provider access and the pending spending decision, reusable model-run wiring, live compatibility and measured baseline/search/final evaluation remain open. Next implementation action is the reusable model-run wiring with shared budget controls. No paid calls occurred.

### September 9, 2026 — combined runtime search ownership

The previous goal turn made progress: checkpoint `238e99d` is committed and pushed to draft PR #1 with 985 unique tests verified and dated evidence. This continuation starts from a clean branch; the pinned Omnigent environment and Docker daemon are available. Root is implementing the complete three-iteration/two-candidate search and private final acceptance through actual runtime components, using synthetic provider responses.

- `/root`: new `examples/evaluation/strategy_search_fixture.py`, combined acceptance and its tests, integration fixes if evidence requires them, documentation and immutable evidence. Run the actual coding proposer and Docker programs, then normal Omnigent/MCP with code strategies and independent development evaluation. Freeze selection/revoke the proposer before creating or reading the private authored final package.
- `/root/boundary_review`: new `examples/evaluation/search_runtime_fixtures.py` and `tests/test_search_runtime_fixtures.py` only. Provide disjoint authored development/final source packages and a synthetic research-response policy that uses actual returned receipts and only the public brief/source URL. Keep human review status authored and avoid mutable global prompts.

Existing production source remains frozen unless a concrete integration defect requires a coordinated fix. Combined software acceptance does not establish model quality, approve spending or complete the live evaluation milestones.

The fixture-helper assignment is handed off: six focused tests passed through the actual in-memory MCP/service, independent scoring and disjoint split validation. While root runs combined acceptance, `/root/boundary_review` now owns a separate ten-case authored held-out draft under local ignored `.researchharness/private-benchmarks/heldout-draft-20260909/`, including validation and a human review checklist in that private directory. This is the plan's representative held-out package, separate from the one-case runtime smoke fixture; retain `authored` status, cover varied source/coverage/failure requirements, validate disjointness against the twenty development cases and keep all contents outside search feedback and Git publication. This does not substitute agent review for independent human review.

`/root/search_acceptance_review` owns a bounded read-only audit of the combined example and actual artifacts: full development/proposer evidence, actual Docker execution and mounts, revocation before final package access, immutable selection and explicit fixture provenance. It must not edit source or restart the root-owned run. Root owns any fixes and the running process handle.

Both follow-up agents subsequently stopped with the model service's capacity error (confirmed terminal agent statuses). The read-only source audit had found no gap in export, exact binding, Docker mount verification or revocation sequencing, but its artifact audit was unfinished. The private benchmark assignment left a partial build script. Root takes ownership of the remaining artifact audit and private draft, preserving those files; the actual combined runtime process continues on its original handle.

### September 9, 2026 — orchestration checkpoint and GitHub publication

Owners: `/root` for controller integration, recovery fixes, documentation, verification and publication; `/root/boundary_review` for independent shutdown review and proposer fixes. Earlier workspace/proposer/final-evaluator assignments are handed off. The user requested every checkpoint committed and a PR; this checkpoint updates [draft PR #1](https://github.com/Madhavan113/researchharness/pull/1) on `checkpoint/omnigent-research-harness`. The overall implementation goal remains active.

Changed files include the new `optimization/workspace.py`, `proposer.py`, `controller.py` and `final.py`, four focused test modules, the explicit held-out comparison option and proposer gateway phase. The search controller freezes exact proposer tasks before dispatch, independently verifies gateway artifacts, retains every candidate slot and prior development attempt, and revokes proposer access before private final evaluation. Review fixes preserve admission reservations, immutable recovery receipts and completed final state. The proposer now retains unresolved client/gateway/workspace ownership across replacement objects and requires actual cleanup before reporting quiescence. Its budget metadata is frozen and checked before dispatch and usage verification. Missing artifacts and permanently failed cleanup remain unresolved.

The [verification record](../../examples/evaluation/evidence/search-orchestration-2026-09-09/verification.json) records **985 unique tests verified, zero skips and no remaining failures**, with 105 source/test/configuration hashes. Initial command: `RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --extra mcp pytest -ra --basetemp=/tmp/rh-search-controller-suite-20260909 --junitxml=/tmp/rh-search-controller-suite-20260909.xml`. The [initial report](https://github.com/Madhavan113/researchharness/blob/b58bf44a84407d1069039499411e53aaaede5968/examples/evaluation/evidence/search-orchestration-2026-09-09/pytest.xml) records 982 passed and three failures in 181.94 seconds because the pinned Omnigent checkout had disappeared from `/tmp`. `sh examples/omnigent/setup.sh /tmp/researchharness-omnigent-be042b39` restored the exact pin and frozen environment. All three affected checks then passed in 67.35 seconds; the [retry report](https://github.com/Madhavan113/researchharness/blob/b58bf44a84407d1069039499411e53aaaede5968/examples/evaluation/evidence/search-orchestration-2026-09-09/runtime-retry.xml) and exact selectors are retained. No implementation changes were needed for those environment failures. Ruff lint/format checks pass across 103 Python files; all eight previous evidence archives and 1,490 payload hashes remain unchanged.

The [acceptance record](../../examples/evaluation/evidence/search-orchestration-2026-09-09/acceptance.json) distinguishes the coordinator fixtures from actual components. The actual coding proposer makes eight synthetic requests/eight tool calls, executes its generated check in Docker, submits a candidate and closes access; independent usage is 880 synthetic tokens. Coordinator fixtures evaluate the baseline plus six candidates, preserve prior complete development traces, freeze selection and exercise private final evaluation with authored executors and a substituted strategy runner. Separate actual Omnigent/Docker checks pass, but combined actual-runtime search is still unverified. No paid calls occurred.

The [227-file archive](../../examples/evaluation/evidence/search-orchestration-2026-09-09/index.json) retains the current reproduction source, complete actual coding-proposer artifacts and authored coordinator metadata. Its size is 462,466 bytes and SHA-256 is `59c867821b5ff5ef447ca8300bd015508e68d3496416c5990923576882834c5f`; every payload was verified. Private final execution/package directories and runtime credentials are excluded. The reproduction source is for reviewers and must never be mounted as proposer feedback.

Next action: run and archive the complete three-by-two search through the actual coding proposer, isolated strategies and normal Omnigent executor, then the isolated authored final fixture. Reviewed development/final cases, live provider access and the pending spending decision, measured baseline/comparison and model optimization remain open. This checkpoint completes the requested publication work; it does not complete M0/M5/M6.

### September 9, 2026 — coding proposer and search/final controller ownership

The preceding goal turn was progress: checkpoint `f83da05` was validated, committed and pushed to draft PR #1. This continuation starts from its clean worktree and implements the remaining M6 orchestration without authorizing paid requests.

The subsequent interrupted turn also made progress: all four orchestration modules and focused tests landed; independent review found four controller recovery/state issues, three of which were fixed before interruption. On resumption the old test handle was absent and no sub-agents remained live. Root takes integration ownership of the handed-off modules, fixes the remaining final-retry regression and pins the exact proposer task binding. The user now requests all checkpoints committed and published through GitHub. Root owns checkpoint validation, documentation, commit and PR update; complete combined search/runtime acceptance remains separately identified if not verified before this checkpoint. No paid work is resumed or authorized by this continuation.

`/root/boundary_review` completed the read-only workspace/proposer boundary review: 48 fixture tests and, after restoring the existing Docker Desktop daemon, ten actual runtime tests passed. The reviewer reproduced a gateway cleanup failure that let both proposer shutdown and same-process recovery overstate quiescence. The assignment now owns fixes in `optimization/proposer.py` and `tests/test_optimization_proposer.py` only, including frozen proposer budget metadata. Root owns the controller, its tests and all documentation; source must be stable before combined verification.

- `/root`: `optimization/controller.py`, search-controller tests, shared integration interfaces and `execution.py` phase binding, end-to-end acceptance, documentation and publication. Preserve immutable prior evidence. The controller will evaluate the baseline, run the configured three iterations/two candidates, preserve proposal attempts and complete development artifacts, then close search and revoke proposer access before private final evaluation.
- `/root/service_review`: new `optimization/workspace.py` and focused tests for the proposer filesystem/read-write-execute boundary. Expose only the complete development feedback tree and a new writable workspace; execute generated programs only in a restricted Docker container. Record and close every owned execution.
- `/root/omnigent_spike`: new `optimization/proposer.py` and focused tests for a bounded Responses coding-agent loop over the workspace tools. Keep original model/tool traces, return explicit candidate files and close workspace/gateway access. No candidate or proposer access to benchmark predicates, evaluator code, held-out files or host credentials.
- `/root/evaluation`: new `optimization/final.py` and focused tests for private evaluation of the frozen selection and baseline on a disjoint held-out package. Reuse the controlled case executor and independent scoring; retain failed/interrupted artifacts and never publish held-out feedback or restart search. Root owns proposer revocation and orchestration before invoking this host-only evaluator.

Agree interfaces before dependent integration; each agent owns only the listed new files. Existing archive, gateway, benchmark and strategy modules stay frozen unless a concrete dependency is coordinated with root. Human review, live provider access/budget approval and measured optimization remain separate outstanding requirements.

### September 9, 2026 — context, independent evaluator and PR checkpoint

Owners: `/root` for integration, review fixes, archive bridge, acceptance and publication; `/root/omnigent_spike` for context/gateway verification; `/root/evaluation` for controlled runtime bindings; `/root/service_review` for independent review. These bounded assignments are complete. The checkpoint is published through [draft PR #1](https://github.com/Madhavan113/researchharness/pull/1) on `checkpoint/omnigent-research-harness`; the shared implementation goal remains active.

Changed source includes `strategies/context.py` and `session.py`, discovery/CLI/MCP and Omnigent session loading, the controlled model gateway, benchmark/controller/runtime/pilot integration, gateway usage verification, and the new `optimization/evaluator.py`. Focused tests cover protocol grouping, budget/deadline/shutdown ordering, strategy failure versus proposal commit, missing/replaced storage, evaluation bindings, preserved failures and private evaluator inputs. README, the plan and strategy guide now describe the implemented boundary and remaining work.

The [combined verification](../../examples/evaluation/evidence/strategy-context-evaluation-2026-09-09/verification.json) and [JUnit report](https://github.com/Madhavan113/researchharness/blob/b58bf44a84407d1069039499411e53aaaede5968/examples/evaluation/evidence/strategy-context-evaluation-2026-09-09/pytest.xml) record **876 passed, zero skips**, in 183.42 seconds. Command: `RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --extra mcp pytest -ra --basetemp=/tmp/rh-context-evaluation-suite-20260909 --junitxml=/tmp/rh-context-evaluation-suite-20260909.xml`. Ruff lint and format checks passed across 95 Python files. The record retains 97 source/test/configuration hashes; prior checkpoint hashes remain unchanged.

The [acceptance](../../examples/evaluation/evidence/strategy-context-evaluation-2026-09-09/acceptance.json) verifies actual normal Omnigent follow-up context with five requests, five Docker executions and 550 synthetic tokens: the active turn remains intact, and a real second user message permits older completed history to shrink from 7 to 2 and then 9 to 4 items. Both user messages remain. The controlled direct/Omnigent runs produce independently valid fixture proposals, 440/770 synthetic tokens and 7/10 strategy executions. Both complete execution packages pass the independent archive bridge with model verification explicitly false. Single-brief conversations may have no removable groups; these fixtures establish software behavior, not model quality or cost improvement.

The [portable evidence index](../../examples/evaluation/evidence/strategy-context-evaluation-2026-09-09/index.json) covers 725 files and a 1,015,519-byte archive, SHA-256 `fd8ee82667a7fc65a92f754d2a41380ecee569f341e118bab1ba8327866e3cad`. Every archived file hash was checked. Complete recorded development artifacts and safe context proof are retained; benchmark/evaluator inputs remain outside proposer feedback. The two included Research Harness SQLite snapshots contain fixture evidence. Omnigent account/session databases and credentials are excluded. The separate reproduction-source tree is for reviewers and must not be mounted as proposer feedback.

Compatibility: old strategy-bound cases without the new session identity fail closed under the new loader. Restore the matching historical code/state or prepare a new case; missing state cannot be silently recreated and exact observations cannot be reexecuted as a resume. Cases without code strategies remain compatible.

Next action: implement a bounded coding proposer and orchestrate the existing controlled executor and independent archive bridge, then isolate final evaluation after selection and proposer shutdown. Human-reviewed development cases, held-out briefs, live provider access and the pending spending decision, a measured baseline and actual search/final evaluation remain open. No paid calls or held-out evaluation occurred. This is a completed implementation checkpoint, not completion of M0/M5/M6.

### September 9, 2026 — context and evaluation integration ownership

The previous goal turn was progress: all accumulated checkpoints were committed, pushed and published in draft PR #1; the clean branch starts at `8d40098`. This continuation resumes the implementation goal without authorizing provider spending or declaring the remaining milestones complete.

- `/root`: coordinated integration, acceptance fixtures, new `optimization/evaluator.py` development-export/evaluator bridge and tests, session/transport fixes from independent review, and documentation/handoff. Preserve the existing draft PR and prior immutable evidence.
- `/root/omnigent_spike`: `strategies/context.py`, controlled Responses gateway projection, independent gateway usage verification and focused tests. Candidate decisions select host-built protocol groups; fixed instructions, user tasks, tool relationships and provider/model/budget controls remain authoritative.
- `/root/evaluation`: optional code-strategy freezing and identity in comparison controls, runtime tasks/executors and pilot session wiring, with related tests. Include complete strategy execution artifacts on success and failure; preserve baseline runs.
- `/root/service_review`: read-only independent review of the published strategy session, projection, recovery and transport boundaries. Root owns fixes after concrete findings.

The bounded agent assignments are handed off and source is frozen. `/root/evaluation` passed 140 focused tests plus the actual Docker/two-runtime comparison. `/root/omnigent_spike` passed 191 focused tests with zero skips, including actual Omnigent and Docker. `/root/service_review` independently verified the proposal-commit and missing/replaced-session fixes with 27 tests and reviewed the read-only evaluator/export boundary. Root additionally extended the actual comparison test to independently grade and archive both arms, which passed in 43.78 seconds. Combined checkpoint validation and evidence publication are recorded in the preceding handoff. Human-reviewed briefs, live provider/budget configuration, measured baseline, proposer search and isolated held-out evaluation remain open.

### September 9, 2026 — observation integration and GitHub checkpoint

Owner: `/root`. Status: implementation and acceptance complete for observation projection; all accumulated checkpoints were committed as `d2e83ca` and pushed on `checkpoint/omnigent-research-harness` at the user's request. [Draft PR #1](https://github.com/Madhavan113/researchharness/pull/1) targets `main` and records the verified scope and remaining work. Existing agent implementation assignments were explicitly frozen for publication. The pending context/gateway and controlled-evaluation assignments have not landed; the shared implementation goal remains in progress.

New `strategies/config.py`, `session.py` and `projection.py` validate and freeze candidate code, maintain durable state/event journals, execute only through the isolated runner and project supplied source identities into observations. Direct discovery and the MCP adapter share this contract. Invalid decisions, altered artifacts and exhausted strategy budgets fail the session; exact retries return the recorded decision, and explicit recovery never reexecutes candidate code. Candidate rendering is labelled interpretation and cannot replace authoritative receipts, probe outcomes or saved proposal ids.

The CLI adds `--strategy` to discovery and MCP serving, plus `rh strategy status` and `recover`. Omnigent preparation freezes code outside its agent/plugin tree, binds the code digest to the case and restores that binding through the normal and fixture MCP launchers. Changed files include `discovery.py`, `cli.py`, `mcp/server.py`, `integrations/omnigent.py`, `evaluation/fixture_transport.py`, six focused strategy test files, the authored strategy example, the acceptance fixture and the README/plan/strategy guide. No context-selection or proposer support is claimed.

The [observation acceptance](../../examples/evaluation/evidence/strategy-observation-2026-09-09/acceptance.json) passed through the actual normal Omnigent server/runner/MCP and the direct dispatcher using two real Docker containers and four synthetic model requests. Both paths produce matching projection, retain original receipts and reuse exact retries with one candidate execution each. A host-import guard remained untouched. Command: `uv run --extra mcp python examples/evaluation/strategy_observation_fixture.py --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python --out /tmp/rh-strategy-observation-20260909`. The [portable archive](../../examples/evaluation/evidence/strategy-observation-2026-09-09/index.json) contains 120 files, 160811 compressed bytes, SHA-256 `2bc96e5afc24856a5a1e7dfe9efb20d6e2f2bfa251961244e6afe46c08b538aa`; extraction and every indexed hash were verified. This fixture tests projection and retry behavior, not complete proposal quality or optimization.

Final validation: `RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --extra mcp pytest -ra --basetemp=/tmp/rh-pr-checkpoint-suite-20260909 --junitxml=/tmp/rh-pr-checkpoint-suite-20260909.xml` passed **787 tests, zero skips**, in 121.87 seconds. The [verification record](../../examples/evaluation/evidence/checkpoint-2026-09-09/verification.json) retains 99 source/test/configuration hashes and the [JUnit report](https://github.com/Madhavan113/researchharness/blob/b58bf44a84407d1069039499411e53aaaede5968/examples/evaluation/evidence/checkpoint-2026-09-09/pytest.xml). An import-order-only Ruff fix was followed by all 11 session tests passing with real Docker enabled. Ruff check and format check passed across 90 Python files. Documentation checks resolved 169 local links across 33 Markdown files, with no links depending on sibling checkouts, trailing whitespace or diff errors. All 99 checkpoint hashes matched; no task-owned strategy or shared-storage containers remained. Older evidence remains unchanged and refers to its own dated checkpoint.

Next action after publication: resume protocol-safe context selection and controlled code-strategy evaluation bindings, then connect the coding proposer and archive evaluator. Live provider access and the spending decision, human review of development cases, held-out briefs, a measured baseline and actual optimization/final evaluation remain open. No paid calls occurred. The PR is a reviewable checkpoint of the implementation so far, not completion of M0/M5/M6.

### September 8, 2026 — strategy workflow integration ownership

The previous turn was progress: shared-storage acceptance, isolated Python execution, immutable archive recovery, native hook proof and 739 passing tests landed. This turn connects those foundations to the actual direct/MCP and controlled-evaluation paths using fixtures. No provider spending or real optimization search is authorized by this implementation work.

- `/root`: `strategies/config.py`, `strategies/session.py`, `strategies/projection.py`, direct discovery and MCP invocation hooks, focused config/projection/session/workflow tests, combined acceptance and documentation. Own the durable strategy event/state contract and preserve authoritative service payloads and receipts. On September 9 root took the missing config dependency back while the assigned agent was still pending initialization; the agent was explicitly notified not to edit those files.
- `/root/service_review`: read-only review of root's config/session/projection and transport bindings after handoff. The pending implementation assignment was superseded on September 9: root now owns CLI, Omnigent bundle and fixture-transport loading/freezing as well, so useful integration work can continue while the agent awaits initialization. The agent was explicitly told not to edit these files.
- `/root/omnigent_spike`: `strategies/context.py`, controlled Responses gateway context projection, independent gateway-usage verification updates and related tests. Preserve complete protocol groups and fixed provider/model/tool/budget controls; coordinate the session interface before integration.
- `/root/evaluation`: comparison-controller, benchmark controls and runtime-executor strategy freezing/binding, optional pilot gateway-session wiring and related focused tests. Keep candidate hashes/limits and complete strategy execution artifacts in controlled evaluation; do not edit the archive, gateway or service hooks. Benchmark ownership was explicitly expanded for the optional code-strategy hash and strategy-axis comparison.

The proposer and archive evaluator bridge follow these execution bindings. Human-reviewed cases, live provider acceptance, a measured baseline and isolated final evaluation remain completion requirements. Candidate observation summaries remain explicitly generated interpretation; only original service evidence can establish discovery, passing probes or saved domain ids.

### September 8, 2026 — shared-storage and strategy foundation handoff

Owners: `/root` for the isolated executor, reproducible hook fixture, combined acceptance and documentation; `/root/evaluation` for the optimization archive; `/root/service_review` for shared-storage acceptance and independent archive review; `/root/omnigent_spike` for native-hook verification and executor review. Bounded assignments are complete and source is frozen. This goal turn made concrete progress; M0/M5 live acceptance and the complete M6 controller/search/final evaluation remain outstanding.

The [strategy guide](../strategy-optimization.md) describes the new `strategies/` executor and `optimization/` archive, their trust boundaries, commands and next integration contract. Candidate Python executes only in a digest-pinned Docker container with bounded resources and explicit JSON inputs. Ten actual-container cases verify ranking/rendering, inaccessible host credentials/private files, denied network access and host writes, invalid output, resource overruns and host interruption. Execution artifacts and cleanup/uncertainty records survive failures; recovery never reruns candidate code.

The development archive retains complete dedicated candidate/artifact trees and host-evaluated per-case evidence. Model-mode admission requires frozen controls, reviewed development cases and a verified measured baseline. Independent review found and fixed unverified candidates entering selection, feedback-copy/journal recovery gaps, selection reservation deadlock, and unsealable interrupted final preparation. Feedback files now publish atomically from journal-bound staging outside the proposer directory. Recovery completes the recorded copy/commit without invoking evaluation. Selection closes search and freezes its result in one durable intent; interrupted final preparation seals explicit missing evidence. The trusted host still must isolate/revoke the proposer and connect actual evaluators.

The reproducible native-hook fixture passes through the pinned normal Omnigent server and runner with three synthetic model requests. TOOL_RESULT replacement changes source order, rendered observations and stop advice while preserving stored receipts. LLM_REQUEST replacement is ignored and runs once across those requests; it does not provide full-history selection. The accepted plan now explicitly separates the proposer feedback mount, isolated candidate execution and host-owned research tools. Integrating matched structured projection into the direct dispatcher and MCP invoke path is the next concrete implementation task.

Portable evidence was extracted and its indexed hashes verified:

- [Shared storage](../../examples/evaluation/evidence/shared-storage-2026-09-08/index.json): 21 files, archive SHA-256 `344894a6c5d90571f44b7a97ce1ade859864c9b8fe756c6fec125834a29df44b`. The [fixture](../../examples/evaluation/shared_storage_fixture.py) verifies Postgres/MinIO clients, detached collection/export, both services restarting, fresh-client recovery, S3 export reads after local deletion, exact-operation retries, replay, writer exclusion and question scoping. The accepted workflow explicitly reuses its preceding 125-test/49-Postgres-parameterization suite evidence; task containers/volumes were removed.
- [Native hooks](../../examples/evaluation/evidence/native-strategy-hooks-2026-09-08/index.json): 41 files, archive SHA-256 `91ec05b5770f2b02a6ff0dfd7330c552d298d6c22bc8c9dddd19b7ba3c24c5f6`. Exact fixture/bundle code, available model requests, original/projected observations and runtime exports are retained without runtime credentials or operational databases.
- [Strategy isolation](../../examples/evaluation/evidence/strategy-isolation-2026-09-08/index.json): 75 files, archive SHA-256 `c3d61b2a66c173503b69fad13a477f181366c4b01f430b79f8b33788eba41d6b`. Twenty-one tests include ten actual containers; the archive retains exact code, inputs, decisions, failures and effective container settings.

Final combined validation: `RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --extra mcp pytest -ra --basetemp=/tmp/rh-strategy-foundation-suite-20260908-reviewed --junitxml=/tmp/rh-strategy-foundation-suite-20260908-reviewed.xml` passed **739 tests, zero skips**, in 119.53 seconds. The [verification record](../../examples/evaluation/evidence/strategy-foundation-2026-09-08/verification.json) and [JUnit report](https://github.com/Madhavan113/researchharness/blob/b58bf44a84407d1069039499411e53aaaede5968/examples/evaluation/evidence/strategy-foundation-2026-09-08/pytest.xml) retain results and tested code hashes. `uv run ruff check src tests agents/research/policies examples/evaluation examples/strategies` and the corresponding `ruff format --check` passed across 80 Python files. The archive's focused suite passed 85 tests; the independent reviewer passed 21 targeted regressions and an ephemeral hard-exit check with one evaluator invocation and exact-byte recovery. That independent inline check retained no script/raw artifacts and is identified as such in the verification record.

Documentation checks passed: 157 local file links across 23 Markdown files, no trailing whitespace, and `git diff --check`. All 83 recorded code/configuration hashes still matched the tested files, and the retained JUnit hash matched. No task-owned strategy or shared-storage containers remained after validation.

No provider key, custom base URL or project `.env` was present at the final check. No paid calls, actual optimization search or held-out evaluation occurred. The pending provider/spending decision and human case review are unchanged. Continue with projection/context/proposer/evaluator integration using fixtures; retain the measured-baseline gate for actual search. Shared remote deployment and multi-user acceptance are separate from the completed local storage fixture.

### September 8, 2026 — shared-storage and strategy foundation ownership

The previous turn was progress: budgeted dispatch, cross-revision accounting witnesses, normal-runner tool enforcement, 631 passing tests and portable actual-runtime fixture evidence landed. Provider access/spending authorization and human case review remain pending; this turn proceeds with independent implementation and local acceptance work. Infrastructure work below does not waive the measured-baseline gate for optimization/search or claim M6 complete.

- `/root`: candidate Python strategy contract and isolated local execution under new strategies/ modules, corresponding tests, architecture decisions and docs/tracker. No paid calls or actual search/selection from fixture quality.
- `/root/service_review`: local Postgres/S3-compatible acceptance using task-owned containers and existing shared-backend tests, plus a new examples/evaluation/shared_storage_fixture.py or focused shared workflow test if needed. No existing production-source edits without a reported bug/ownership handoff; preserve other containers and user backend changes.
- `/root/omnigent_spike`: read-only inspection of verified code strategy hook options in the pinned normal runner and current gateway/MCP/direct paths; deliver a concrete supported boundary with fixture evidence. No source edits until the interface is agreed.
- `/root/evaluation`: new optimization/archive.py and its focused tests for immutable candidate code/trace artifacts and durable development-selection/final-evaluation separation. Root owns the execution contract; coordinate its interface before integrating. No existing evaluator/controller edits and no held-out task contents in search-visible artifacts.

### September 8, 2026 — budgeted dispatch and controlled tools handoff

Owners: `/root` for the pilot runner, shared settings/controller wiring, ledger witnesses/registry, combined acceptance and docs; `/root/service_review` for the dispatch budget adapter and read-only integration review; `/root/omnigent_spike` for gateway reservations and the controlled Omnigent tool policy; `/root/evaluation` for independent request settlement evidence and read-only pilot review. All bounded assignments are complete and source is frozen. This goal turn made concrete progress; the overall M0–M6 goal remains active. Fixture success does not complete M0/M5 live acceptance or M6.

The [budgeted pilot](../pilot-budget.md) now prepares a frozen comparison, executes one case, recovers an unresolved stopped case without replay, and finalizes independently evaluated artifacts plus budget accounting. Both arms share one durable ledger across retries and prepared revisions. Production policy pins the priced snapshot, standard endpoint and default tier; text/function requests reserve the full provider context bound plus configured output tokens before every possible HTTP dispatch. One-shot durable dispatch markers precede HTTP. Only independently verified sealed response evidence settles/cancels reservations; interrupted or unknown outcomes remain fully held. Returned model/tier/bound violations stop subsequent admissions. Ledger waits cannot extend gateway shutdown indefinitely or rewrite a sealed archive. These are supplied-rate controls, not an exact invoice guarantee.

Review exposed and fixed unadvertised SDK tool execution, lost archive references after report-write failures, revocation starting/consuming pending cases, missing ledger-operation coverage, and rollback/concurrency gaps across prepared revisions and final reporting. The shared .pilots.json registry preserves every prepared pilot's manifest identity; checkpoints and raw archives are checked together under the ledger lock, including the exact final accounting snapshot. Missing ledgers or registries are not silently recreated. Keep all registered pilot directories as local evidence. Available host witnesses detect ordinary accidental restore/replacement; local files are not a signed external accounting authority.

Controlled Omnigent cases now freeze an eight-tool discovery allowlist and a Python TOOL_CALL policy loaded through the pinned runtime's normal policy_modules configuration. The normal server checks it before builtin, plugin and MCP dispatch. A synthetic startup event is separately constrained to the case/harness. Default workflow bundles retain collection/export behavior. The separate actual normal-runner regression returned unadvertised sys_session_rename and research__list_jobs calls; both were denied, the title remained unchanged, and allowed begin/context calls succeeded. No upstream source patch was required. Evidence: `/tmp/rh-controlled-tool-policy-20260908-v2/test_normal_runner_denies_unad0`; reproduce with `RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --extra mcp pytest tests/test_omnigent_integration.py::test_normal_runner_denies_unadvertised_registered_tools_before_execution`.

Combined acceptance: `uv run --extra mcp python examples/evaluation/budgeted_runtime_fixture.py --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python --out /tmp/rh-budgeted-runtime-20260908`. Both actual adapters saved independently valid proposals. Every one of **4 direct + 7 Omnigent** HTTP fixture dispatches had a durable reservation/marker first. Independent tokens remain **440 / 770**; **11 operations** settled, **0 holds** remain, and **1,320,000 nanodollars** are synthetic supplied-rate accounting. Eight auxiliary compaction attempts were denied locally. This discovery fixture establishes neither live model quality nor actual provider cost.

Portable [acceptance](../../examples/evaluation/evidence/budgeted-runtime-2026-09-08/acceptance.json), [pilot report](../../examples/evaluation/evidence/budgeted-runtime-2026-09-08/report.json), and [archive/index](../../examples/evaluation/evidence/budgeted-runtime-2026-09-08/index.json) contain **186 files**; archive SHA-256: `371f506478dd19ed1e0a37a607b2ce6b6ced0ea413e9ef2bebd13d041f0e6f82`. All file hashes and frozen source bytes were verified. An extracted copy independently reproduced valid proposals, complete token totals, all required operations and zero holds using the extracted ledger explicitly. The archive includes the initial witness, per-case checkpoints, shared ledger/registry and authored tool policy; it excludes runtime credentials and operational databases. Prior evidence packages remain unchanged.

Final validation: `uv run --extra mcp pytest -ra` passed **631 tests**, with **2 optional normal-runtime tests skipped**; the new policy regression and combined actual-runtime acceptance ran separately above. Focused coverage includes **22 pilot**, **67 dispatch-adapter**, **67 gateway** and **83 independent verifier** tests. The pilot reviewer separately ran the five cross-revision/final-snapshot/concurrency/registry regressions successfully. Ruff and format checks cover source, tests, the standalone policy and three evaluation programs (**69 files**). The offline budget plan regenerated with explicit default tier. All **131 local links across 23 Markdown files** resolve; tracked and owned-file whitespace checks pass. No task-owned server/runner/fixture process remains. Provider key/base URL, shared test database URL and project .env remain unconfigured when rechecked.

Changed files: execution/discovery settings; evaluation/pilot.py, dispatch_budget.py, gateway_usage.py, benchmark/controller/runtime_executor wiring; integrations/model_gateway.py and omnigent.py; new authored discovery policy; corresponding tests; proposed pilot configuration/planner, combined fixture, evidence archive and linked guides. No paid calls, commits, deployments or shared-service mutations occurred. Existing user changes are preserved. The draft spending question remains unanswered; configure provider access and record the actual decision before live compatibility execution. Human development-case review, held-out briefs, real-model baseline, shared Postgres/S3 acceptance and M6 strategy optimization remain outstanding.

### September 8, 2026 — dispatch budget implementation ownership

The preceding goal turn was progress: bound raw-response verification, a durable budget ledger, both actual runtime fixtures, and 487 passing tests landed. The current worktree and goal remain authoritative; paid-provider authorization is still pending.

- `/root`: shared service-tier setting and direct propagation; evaluation/pilot.py orchestration/CLI and tests; combined budgeted runtime acceptance, proposed configuration and docs/tracker. Owns execution.py, discovery.py and their existing tests, controller integration if needed, plus the new pilot entry point and fixture.
- `/root/service_review`: new evaluation/dispatch_budget.py and tests/test_dispatch_budget.py, implementing the immutable provider policy and ledger adapter. No model gateway, raw verifier or pilot edits.
- `/root/omnigent_spike`: integrations/model_gateway.py and tests/test_model_gateway.py for reserve-before-dispatch wiring, pinned tier/endpoint controls and budget failures. No ledger/verifier/pilot edits.
- `/root/evaluation`: evaluation/gateway_usage.py and tests/test_gateway_usage_evaluation.py for independently verified request-level settlement evidence and budget/tier metadata. No gateway, ledger or pilot edits.

Review follow-ups retain these owners: `/root/service_review` owns missing-operation consistency checks in dispatch_budget.py and its tests; `/root/omnigent_spike` owns a controlled discovery tool allowlist at Omnigent execution/registration in integrations/omnigent.py, its authored policy template, and tests/test_omnigent_integration.py. `/root` owns pilot ledger witnesses/preflight and runtime_executor.py wiring. Completed verifier source remains frozen. These are bounded fixes from read-only integration review.

Settlement will use sealed independent evidence; interrupted or unverifiable dispatches retain full reservations. The controlled pilot must preserve all failures and refuse automatic replay of unresolved model turns. Fixture mode must remain explicit and no paid calls will occur while the provider/budget decision is unanswered.

### September 8, 2026 — verified provider usage and budget handoff

Owners: `/root` for scoring/controller/recovery integration, shared schema, combined acceptance, proposed budget and docs; `/root/evaluation` for the independent gateway verifier; `/root/omnigent_spike` for the bound gateway archive and read-only integration review; `/root/service_review` for the separate budget ledger. All bounded source/test assignments are complete and frozen. This goal turn made concrete progress; the overall goal remains active and M6 retains its measured-baseline dependency.

Each controlled execution now receives a host-generated identity before provider work. Gateway metadata copies its case/runtime/phase/task binding immutably and publishes a complete hash inventory only after successful shutdown. The independent verifier re-reads raw JSON/SSE/gzip responses, checks the archive/report/records, provider IDs, model, generation controls and all frozen budgets, and distinguishes complete tokens, observed lower bounds and invalid evidence. Cache counters are reported only when observed. Failed tasks, missing proposals and explicitly recovered interruptions can retain valid gateway usage; bad selected gateway evidence cannot fall back to smaller runtime/proposal totals. Legacy manifests retain their prior accounting behavior.

Read-only review found and resolved three issues: standalone discovery scoring accepted workflow-phase evidence, actual gateway request/deadline limits were not checked against frozen controls, and controller-side proposal validation could discard an already returned provider archive. Regression coverage now exercises each issue. record-interruption accepts explicit recovered artifacts through its API, or --gateway-usage through the CLI, without replaying any model turn.

The separate [budget ledger and proposal](../pilot-budget.md) use exact rates, locked durable reservations, one-shot dispatch markers, strict evidence-backed settlement/cancellation, and full holds for unknown outcomes. The ledger does not yet intercept gateway requests. The [offline plan](../../examples/evaluation/pilot-budget.plan.json) calculates a $0.327 maximum request reservation, $10.464 per pair at 16 rounds, and $209.28 for twenty pairs under its explicit rate assumptions. The $10 ceiling and model snapshot remain draft inputs to the pending spending decision; no paid calls occurred.

Combined acceptance: `uv run --extra mcp python examples/evaluation/controlled_runtime_fixture.py --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python --out /tmp/rh-controlled-provider-usage-20260908`. Both actual runtimes saved independently valid proposals. Four direct and seven Omnigent fixture requests supplied complete independently verified totals of **440** and **770** tokens; eight auxiliary compaction attempts were rejected locally. Source/model responses were fixtures, workflow checks remain unmeasured in this discovery comparison, and dollar totals remain unknown.

The [acceptance record](../../examples/evaluation/evidence/controlled-provider-usage-2026-09-08/acceptance.json), [comparison report](../../examples/evaluation/evidence/controlled-provider-usage-2026-09-08/report.json), and [163-file archive/index](../../examples/evaluation/evidence/controlled-provider-usage-2026-09-08/index.json) preserve frozen inputs/implementation, authored runtime bundle, fixture programs, research artifacts, available events and sealed gateway archives. Archive SHA-256: `d31c5efd3586c40a3a8d35b08da449ebf074a24545e20f86f2228e453dd377ba`. All archived hashes were verified, the implementation matches the working source at acceptance, and an extracted package independently reproduced both token totals and valid proposals. Live runtime credentials and operational databases are excluded. Earlier evidence packages remain unchanged.

Final source validation: `uv run --extra mcp pytest -ra` passed **487 tests**, with **1 optional process test skipped**; actual normal-runtime acceptance ran separately above. `uv run --extra mcp ruff check src tests examples/evaluation/controlled_runtime_fixture.py examples/evaluation/plan_pilot_budget.py` and the matching format check passed for **63 files**. Bounded agent checks include **41 gateway**, **59 independent gateway-verifier** and **29 budget-ledger** tests. The offline budget plan regenerated successfully; **114 local links across 20 Markdown files** and whitespace checks passed. No commits, deployments, paid calls or shared-service mutations were made; pre-existing user changes remain preserved.

Changed files: execution.py; evaluation/benchmark.py, controller.py, discovery.py, runtime_executor.py and new gateway_usage.py/budget.py; integrations/model_gateway.py; related tests; combined fixture and new offline budget proposal/planner; README and the linked evaluation/comparison/plan/budget guides. Next: implement and fixture-test dispatch reservations and exact priced-provider controls, then resolve provider access/budget and human review before the live baseline. Shared Postgres/S3 acceptance and M6 remain outstanding.

### September 8, 2026 — provider usage and budget implementation ownership

The previous goal turn was progress: both actual runtime paths passed a frozen fixture comparison, its 159-file artifact archive was verified, and 384 tests passed. Current work begins from that authoritative worktree; the overall goal remains active.

- `/root`: shared gateway-binding schema, gateway-usage integration in discovery scoring/benchmark/controller/runtime execution, concrete pilot configuration and shared documentation/tracker. Owns edits to execution.py, evaluation/discovery.py, benchmark.py, controller.py, runtime_executor.py and the combined fixture, plus their existing tests.
- `/root/evaluation`: independent raw gateway-usage verifier in evaluation/gateway_usage.py and tests/test_gateway_usage_evaluation.py. No gateway implementation or shared evaluator/controller edits.
- `/root/omnigent_spike`: immutable host case/phase binding and a sealed archive index in integrations/model_gateway.py, plus tests/test_model_gateway.py. No scoring/controller edits.
- `/root/service_review`: a separate conservative budget-reservation ledger and planning model in evaluation/budget.py and tests/test_research_budget.py. Pricing and spending approval remain explicit inputs; no paid calls or gateway/controller edits.

Raw provider responses, not model-written usage claims or summary counters alone, must establish token evidence. Failed and interrupted cases remain in the denominator. The provider/budget question is still unanswered; no elapsed time or new fixture evidence authorizes paid execution. M6 retains its measured-baseline dependency.

### September 8, 2026 — controlled runtime comparison handoff

Owners: `/root` for the controller, MCP host budgets, shared settings/instructions, combined acceptance and documentation; `/root/service_review` for direct settings and actual runtime executors; `/root/omnigent_spike` for frozen Omnigent settings and the model gateway; `/root/evaluation` for independent summaries/provider-evidence handling and read-only review. All bounded source/test assignments are complete and frozen. This goal turn made progress; the whole integration/optimization goal remains active.

The controller now copies and hashes development cases, fixtures, semantic instructions, Python source, agent templates and dependency files. It isolates each arm/case backend, reserves executions durably before side effects, preserves partial failures, refuses automatic replay of unresolved model turns, and rechecks complete artifact inventories and case bindings before scoring. Missing workflow reports and absent provider observations remain explicitly unknown. Source/evaluator changes require a newly prepared comparison.

Actual normal-runner HTTP exposed dropped output-token and iteration controls, additional host tools and auxiliary gpt-4.1 compaction requests. The controlled variant routes both runtimes through a host Responses gateway that applies generation controls, restricts advertised functions to discovery, rejects unsupported provider work before dispatch, enforces request/deadline limits and seals artifacts at shutdown. It records reserved attempts separately from calls entering client.send. Review regressions cover deadline overruns, pre-dispatch interruption, late shutdown writes, raw gzip handling, undeclared controls and invalid usage counters. This gateway is neither an exact dollar cap nor an M6 OS sandbox.

Combined acceptance command: `uv run --extra mcp python examples/evaluation/controlled_runtime_fixture.py --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python --out /tmp/rh-controlled-comparison-20260908-verified`. Both actual paths saved independently valid proposals. The gateways dispatched 4 direct and 7 Omnigent requests; 8 auxiliary compaction attempts were rejected locally. All model/source responses were fixtures, no paid calls occurred, and workflow dimensions remain unmeasured in this discovery comparison. Direct usage is 440 synthetic tokens; the selected Omnigent runtime artifact establishes a 770-token lower bound, while the gateway separately observes all 770 fixture provider tokens. Dollar totals remain unknown.

The [acceptance record](../../examples/evaluation/evidence/controlled-runtime-2026-09-08/acceptance.json), [comparison report](../../examples/evaluation/evidence/controlled-runtime-2026-09-08/report.json), and [159-file artifact archive/index](../../examples/evaluation/evidence/controlled-runtime-2026-09-08/index.json) preserve the inputs, implementation, evidence and available traces. Archive SHA-256: `30049e5f7b3cb3b902ae3ffd9fb6fc94c1b0e20575a8766e35e22df62652e153`. Runtime credentials and mutable backend state are excluded. An earlier failed acceptance attempt is retained at `/tmp/rh-controlled-comparison-20260908-final`; fixing its native-only request option and virtual-environment launcher created a fresh comparison rather than rewriting failures.

Final validation: `uv run --extra mcp pytest` passed **384 tests**, with **1 optional runtime test skipped**; the actual normal-runtime controller fixture above ran separately. `uv run --extra mcp ruff check src tests` and `uv run --extra mcp ruff format --check src tests` passed for **57 files**. Focused checks include 39 direct-discovery tests, 36 gateway tests, 16 controller tests, 15 runtime-executor tests, 32 workflow-summary tests and 48 benchmark tests. Archived hashes and documentation links were checked. No commits, deployments or shared-service changes were made; pre-existing user work remains preserved.

Files: execution.py, discovery/CLI settings, MCP server host budgets, evaluation/controller.py, runtime_executor.py, fixture_transport.py, workflow_summary.py and provider-evidence checks, integrations/model_gateway.py and Omnigent case settings, related tests, agents/comparison/instructions.md, examples/evaluation/controlled_runtime_fixture.py, and linked guides. Next: verified gateway-usage integration and a concrete paid-run budget/access record, followed by reviewed cases and a measured baseline. M6 remains gated on that baseline; no milestone is marked complete from fixture model scores.

### September 8, 2026 — controlled comparison implementation ownership

The previous goal turn was progress: M3/M4 acceptance and the independent workflow evaluator landed, with 260 tests passing. Current work begins from that worktree; earlier dirty user files remain preserved.

- `/root`: shared execution settings, matched comparison controller and its CLI/tests, MCP host budget wiring, shared instructions, combined controlled-runtime fixture, documentation and this tracker.
- `/root/service_review`: direct Discovery support for explicit shared instructions/settings and provider controls, its tests, and only discover-related CLI options/invocation. After that handoff, owns evaluation/runtime_executor.py, evaluation/fixture_transport.py and tests/test_comparison_runtime.py. Defaults retain the existing direct path.
- `/root/omnigent_spike`: prepare_case support for the same explicit instructions/settings, normal-runner request verification, integrations/model_gateway.py, gateway/integration tests and corresponding examples. No edits to shared service/MCP/controller files.
- `/root/evaluation`: workflow report aggregation and its tests, benchmark provider-evidence handling and its tests, evaluation documentation, plus read-only controller/gateway review. No edits to controller, runtime executor or gateway files.

Shared semantic instructions and generation/budget settings are fixed across arms. Different prompt assembly, tool schemas, proposal delivery, streaming, and compaction are runtime behavior and must remain visible in traces and interpretation. Fixture execution will validate the controller; live model comparison still awaits provider access and the unanswered budget question. M6 keeps its measured-baseline dependency.

### September 8, 2026 — normal workflow, recovery, and browser acceptance

Owners: `/root` for M4 and browser verification, `/root/omnigent_spike` for M3 and the pinned runtime, `/root/service_review` for independent M4 review and direct-provider parity, `/root/evaluation` for M5 infrastructure. M3 and M4 are complete for the local fixture pilot; the overall goal remains active.

M3 now creates a case-specific authored bundle and launches the real pinned Omnigent server and dedicated runner. The MCP process binds to the host session and server, and startup verifies the source pin and frozen authored-file/configuration/instruction hashes. Runtime exports preserve raw available events with host-owned phase tags and immutable source hashes. [Normal-runtime evidence](../../examples/omnigent/evidence/normal-runtime-2026-09-08/acceptance.json) records a saved proposal, a collected observation, an export read through the agent, and recovery of the same session and both jobs after all runtime processes restarted. Model responses and search were fixtures; source HTTP and detached workers were real local processes.

M4 adds schema migration 4, durable collection/export jobs, detached workers, cooperative cancellation, read_export, and question-scoped datasets. Real worker termination, partial-source cancellation, completed-run recovery, and operation-id retries were checked. Independent review found and resolved cross-question data contamination, the wrong export-recovery directory, stale state read before acquiring a job lock, and an export-file error reclassifying committed work. Legacy registered data remains explicitly readable without bypassing modern scope checks; detected legacy data is included in CLI status and export manifests. The focused backend/job/MCP suite passed 34 tests.

The [browser acceptance report](../omnigent-ui-acceptance.md) records actual chat submission, proposal ids, requested collection/export, inspection of source provenance, and reopening after the full server/runner restart. Before and after reopening, storage retained the same two succeeded jobs and exactly one run, source run, capture, observation, and version. The optional upstream GitHub-resource lookup returned 404; research operations succeeded. Full local browser artifacts are under output/playwright; the report includes portable screenshots and a machine-readable acceptance summary.

The [budget-policy fixture](../../examples/omnigent/evidence/budget-runtime-2026-09-08/acceptance.json) exposed and fixed tiny numeric budgets being read as YAML strings. It then verified that a seven-response turn can exceed the configured threshold, with the next request denied and no cheaper-model fallback. This is between-turn blocking, not a strict billing ceiling. Unknown auxiliary usage and cached-token details remain explicit. No paid calls were made.

M5 has twenty authored development cases, independent source predicates, controlled run manifests, split/hash validation, forty real-service fixture artifact sets, and a host usage-file verifier. Selected usage must match discovery-tagged source events; untagged legacy exports and relabelled follow-ups do not supply verified tokens. Completed usage stays a lower bound while auxiliary calls are unknown. Direct discovery can now use the same provider via --search-provider mcp, with native search preserved as the default; its bounded handoff passed 57 targeted tests, including 22 discovery tests. Provider parity does not establish prompt or runtime comparability.

The independent workflow checker now reads authoritative SQLite/Postgres records without opening a Store, migrating, reconciling, or publishing. It verifies expected requests, actual source outcomes, scoped data, lineage/capture hashes, and export selection at the cutoff. Before/after snapshots can measure identity and unchanged publication around a controller-observed replay. Both saved normal-runtime cases produced 23 passed checks, zero failed, and one explicitly unknown replay check because no before snapshot was available. The [archived workflow report](../../examples/evaluation/evidence/workflow-2026-09-08.json) records those fixture-only results; no repeated collection was triggered by evaluation. The checker has 22 tests, including cancellation, pagination, corruption, and a self-consistent but incorrect export. The combined evaluator/benchmark/workflow subset passed 110 tests.

Final combined validation: `uv run --extra mcp pytest` passed **260 tests**, with **one optional Omnigent process test skipped** because its opt-in environment variable was unset. The current standalone normal-runtime and budget-process fixture commands passed separately in the pinned environment. `uv run --extra mcp ruff check src tests` passed, and `uv run --extra mcp ruff format --check src tests` passed for **47 files**. Documentation links, whitespace, and archived artifact hashes were checked. These are local SQLite and fixture results; no Postgres/S3 or paid-provider acceptance is implied.

All three bounded agent assignments have handed off and their source/test files are frozen after this combined check. Ownership in the table records responsibility, not a claim that another agent is still running. A subsequent goal turn should claim the matched model-run controller before editing, then reuse these acceptance checks without rerunning unchanged experiments.

Changed files include integrations/omnigent.py, agents/research/, examples/omnigent/, services/jobs.py, store/backend/engine/http/CLI/MCP modules, evaluation/, matching tests, the service/backend/integration/browser/evaluation guides, README, and this tracker. Existing user work is preserved; no commit, deployment, or shared-service mutation was performed. Postgres/S3 runtime tests remain unavailable without configured test services.

Next: finish the matched model-run controller and record live provider access and a budget before a paid pilot. Human review, held-out cases, controlled model comparison, complete cost evidence, and M6 remain outstanding. The asynchronous provider/budget question has not been answered; elapsed time does not authorize spending.

### September 8, 2026 — workflow and job implementation ownership

The previous goal turn completed verified service/MCP work and was progress. Current task owners:

- `/root/omnigent_spike`: M3 agent bundle, `integrations/omnigent.py`, tests and scripts specific to normal Omnigent runner/server integration, and integration documentation. No edits to shared dependencies, CLI, store, service, MCP server, or this tracker without coordination.
- `/root`: M4 durable jobs, worker lifecycle, collection/recovery changes, store migration, MCP job wrappers, CLI wiring, shared documentation, and this tracker.
- `/root/evaluation`: bounded M5 development benchmark infrastructure under `evaluation/`, matching tests, development fixtures/cases, and evaluation documentation. Do not expose held-out cases or claim model quality from fixtures. Runtime comparisons remain pending.

The existing dirty worktree and completed agent handoffs were rechecked before assigning work. No active work was reassigned.

Additional bounded M5 ownership: after completing the M4 review, `/root/service_review` owns opt-in direct discovery through the same MCP search-provider wrapper. Files: `discovery.py`, `tests/test_discovery.py`, and only the discover parser/invocation in `cli.py`. Native search remains the default. Verify real SDK HTTP fixtures, service receipts, and budgets; do not treat matching providers alone as a controlled runtime comparison. Root retains other CLI changes and shared documentation.

After handing off the benchmark and usage parser, `/root/evaluation` owns a separate workflow evaluator under `evaluation/workflow.py`, its tests, and evaluation documentation. It checks collection/export/recovery against authoritative backend records and keeps these measurements separate from discovery usefulness. No changes to job execution, storage, or shared CLI files. `/root` owns the Playwright browser acceptance run and its artifacts under `output/playwright/`.

### September 8, 2026 — service, MCP, and evaluation implementation handoff

Owner: `/root`, with `/root/omnigent_spike`, `/root/evaluation`, and read-only reviewer `/root/service_review`. The previous documentation turn made progress; this implementation turn also changes and verifies the codebase. The overall goal remains active.

M1 is complete for the local pilot: provider-neutral models and proposal compilation are extracted; the direct CLI uses ResearchService; schema migration 3 stores contexts, operations, and receipts. Restart tests recover matching probes and remaining budgets. Exact retries reuse results, changed payloads are rejected, unrelated discovery proofs cannot be submitted, and proposal/pipeline/ledger saves are atomic. Scoped service locks allow independent local sessions. Review findings around empty ids, native page evidence, search-budget exposure, failed-search repair, and local lock scope were fixed and regression-tested.

M2 is complete for discovery: the optional MCP extra, `rh mcp serve`, eight typed tools, bound context checks, bounded evidence reads, real search-provider wrapper, text/structured result parity, and protocol rejection paths are implemented. CLI stdio startup and reconnect were tested through the actual MCP client. The captured Keenable response is retained as a parser fixture. Job tools remain M4.

M0's [compatibility spike](../omnigent-compatibility.md) passed ten checks using the real pinned Omnigent executor, SDK, MCP manager, and subprocess transport with synthetic model HTTP responses. The source pin and evidence are saved under `examples/omnigent/`. The full server/UI, live model, and spending-policy checks remain open. No paid model calls were made.

M5's [independent evaluator](../research-evaluation.md) is now maintained in `src/research_harness/evaluation/` with a module CLI and 42 tests. It reads the service's receipts, validates current schemas and fingerprints, rejects foreign discovery evidence, scores independent requirements, and preserves unknown usage semantics. Reviewed briefs, matched real runtime comparisons, collection/recovery metrics, and M6 optimization remain outstanding.

Files: `discovery.py`, `discovery_models.py`, `services/`, `mcp/`, `evaluation/`, `store.py`, `cli.py`, optional dependency/lock updates, corresponding tests, `examples/omnigent/`, and linked documentation. Prior backend, ingestion, and unrelated test changes are preserved.

Validation: `uv run --extra mcp pytest` passed **153 tests**; `uv run --extra mcp ruff check src tests` and `uv run --extra mcp ruff format --check src tests` passed. `uv run --extra mcp rh mcp serve --help` exposes the expected launch/resume options. The compatibility command and its artifact evidence are documented in the linked report. Postgres/S3 execution was unavailable (Docker daemon not running and no configured test backend); migration SQL was reviewed and SQLite migration/recovery was executed. This limitation remains visible for shared-storage acceptance.

Next action: M3's normal Omnigent agent/server wiring, followed by durable collection jobs. Do not mark the full goal complete from fixture discovery alone. Service setup and exact current limits are documented in [the service guide](../research-service.md).

### September 8, 2026 — implementation ownership

The prior goal turn completed G0 documentation and validation; it made concrete progress. The goal runner is active again.

- `/root`: M1. Owns `src/research_harness/discovery.py`, the new discovery models/service modules, registry/store changes, and service tests. Also owns shared dependency files, CLI wiring, README, and this tracker; coordinate requests for changes to those files.
- `/root/omnigent_spike`: M0. Owns `examples/omnigent/` and a compatibility report under `docs/`. May install the pinned runtime in a separate local environment and create fixture-based runtime checks. Record live versus mocked evidence and remaining UI/provider verification. Do not edit domain code or shared dependency files.
- `/root/evaluation`: bounded M5 subtask. Owns `src/research_harness/evaluation/`, `tests/test_research_evaluation.py`, and evaluation documentation. Migrate and validate the existing independent evaluator; do not edit discovery, storage, dependency files, or the goal tracker. Final benchmark design and runtime comparisons remain pending.

Agents report results to `/root` for integration and tracker updates. Existing working-tree changes remain preserved.

M1 review subtask: `/root/service_review` owns a read-only review of service persistence, retry/concurrency behavior, migrations, and compatibility with the direct CLI. Report findings to `/root`; do not edit application or tracker files.

M2 ownership: `/root` owns `services/search.py`, service search dispatch, dependencies, and CLI wiring. After completing evaluator migration, `/root/evaluation` owns `mcp/` and `tests/test_research_mcp.py` for typed wrappers and actual protocol tests. Each server process remains bound to one discovery context; collection job tools follow in M4.

### September 8, 2026 — G0: establish the shared goal

Owner: `/root`, conversation `01a07f23-24df-7502-9834-a7ece2020bcc`. Status: done.

The user accepted documenting the Omnigent integration as a shared goal across development agents. The implementation objective was registered in the current conversation. This repository tracker provides durable project context independently of the conversation's goal-runner state.

Files in scope: `AGENTS.md`, `README.md`, `docs/omnigent-integration-plan.md`, and this tracker. Existing application, backend, dependency, and test changes belong to prior work and are being preserved.

Validation: an inline Python documentation check passed for all four files, including 20 local links, balanced code fences, whitespace, and accepted/queued status consistency. `git diff --check -- AGENTS.md README.md docs/omnigent-integration-plan.md docs/goals/omnigent-integration.md` passed. Untracked Markdown files were included in the Python check. No runtime tests were needed or run for these documentation changes.

Next action: claim and start M0 or M1 using the boundaries above. All implementation milestones remain queued. The registered conversation goal was paused by the interruption; its runner state does not change the accepted repository objective or automatically assign work to other agents.
