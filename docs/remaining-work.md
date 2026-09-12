# Remaining work after the PR #1 checkpoint

Prioritized follow-up list from the September 10, 2026 review of HEAD `4f49495`. Read [checkpoint scope](checkpoint-scope.md) first. Each item names the files, the confirmed failure, a fix direction and the acceptance check that closes it. Line numbers refer to HEAD `4f49495` and will drift; search for the quoted identifiers.

How to work this list:

- Take items in priority order unless the tracker records a different owner. Record ownership in the [tracker](goals/omnigent-integration.md) before starting, per AGENTS.md.
- Every fix ships with a regression test, a doc update where a claim changes, and a tracker handoff entry. Keep production changes separate from evidence regeneration.
- Do not start P0 items marked "decision required" without the recorded decision.
- Run `uv run ruff check src tests`, `uv run ruff format --check src tests` and the relevant tests. Run the Docker-gated tests when Docker is available and say which configuration you ran.

Status legend: `open`, `in_progress`, `done`, `decision`. Confidence: `confirmed` means reproduced or traced end to end during review; `plausible` means strong reading without reproduction.

## P0: before any budgeted or measured run

### RW-1 · Decide and implement the searchable strategy surface · `done` · confirmed

Files: `src/research_harness/optimization/controller.py` (`_admit`, the `_contract()` string near line 82 and admission near lines 523–539), `src/research_harness/strategies/projection.py` (lines 98–107), `examples/strategies/research_strategy.json`, `docs/omnigent-integration-plan.md` Milestone 6, `docs/strategy-optimization.md`.

Problem at review: candidates could only edit `instructions.md`, reorder or re-render `search_sources` results, and set an advisory `stop_recommended` flag. `_admit` copied `seed.config`, freezing context selection at the documented baseline's `context: false`. The plan promised "ranking, formatting, context-selection and stopping decisions" and the paper searches whole harness programs.

Alternatives considered by the owner:

- **Widen the surface.** Let candidates enable and configure context selection (ship a baseline manifest with `context: true` or allow the candidate manifest to toggle it), consume stop advice in the direct and gateway loops as a real stopping rule with a fixed-limit fallback, and consider exposing per-turn instruction assembly to the candidate. Each addition needs a projection rule, a test that invalid decisions fail closed, and a docs update.
- **Re-scope the claim.** Keep the current surface and rewrite plan Milestone 6, README and the PR description to say the search covers instruction text and observation rendering. The plan already says a prompt-only experiment must be reported as prompt search.

Acceptance: plan, strategy guide and contract string agree; a test proves each advertised decision type changes host behavior.

Decision and implementation September 10: `/root` widened the surface on `feat/strategy-control-surface`. The maintained baseline enables context projection and the frozen `finalize_on_stop` control. Verified stop decisions end new source discovery while permitting evidence reads and validated proposal submission within existing limits. Legacy manifests retain their declared observation-only/advisory behavior and canonical hashes. Candidate instructions remain editable; per-turn host instructions remain fixed. Actual Docker, direct SDK and pinned Omnigent/MCP tests verify finalization and context selection changing forwarded requests. All 64 affected checks pass after fixing the sole failure in the initial full-suite run; see the [handoff](goals/omnigent-integration.md#september-10-2026--rw-1-strategy-control-ownership-and-decision) and [strategy guide](strategy-optimization.md#finalization-after-a-verified-stop). These are software fixtures, not measured search results.

### RW-2 · Make the development benchmark discriminate · `done` · confirmed

Files: `src/research_harness/evaluation/discovery.py` (`matches` at lines 23–29, `score` from line 141, requirement loop near lines 285–304), `examples/evaluation/development/build_cases.py` (fixture generation near lines 86–97, unsupported cases near lines 146–170, 226–259, 276–295, 594–617), `examples/evaluation/search_runtime_fixtures.py` (`_source` near line 30), `docs/research-evaluation.md`.

Problem, three parts:

1. `matches` requires exact list equality including order. `required_pointers` of `["/filed_at", "/form"]` against a predicate `["/form", "/filed_at"]` scores zero, and a stricter superset also scores zero. Contradicts the doc's "valid alternatives" rule.
2. Four cases (401 archive, xlsx and PDF with `connector: "json"`, no fixture response) encode requirements no proposal can satisfy, and `score` never consults `fixture_plan.unsupported`, so the honest "needs access/connector" answer scores like the wrong answer. Running `python -m research_harness.evaluation.fixtures` over all twenty cases: answer-key macro 0.858 versus first-listed 0.233, with seven of twenty cases tied. Three further cases are single-source trivial.
3. Every fixture's `search_results` equals the case's `sources` plus `unsupported` URLs, so one search returns the answer set. The benchmark measures configuration discrimination, not discovery under noise. The one-case synthetic package used in all archived search runs has a requirement built from the same `_source(url)` the scripted model always proposes, which guarantees the recorded seven-way tie at 1.0.

Fix direction: set or subset semantics for pointer lists with per-field rules documented; a scored "gap reported correctly" signal for unsupported-source cases; distractor results in fixtures; retire or rewrite degenerate cases; state in docs that no search run has used the twenty-case set.

Acceptance: a probe over the twenty cases shows the answer key strictly beats first-listed on every case; a regression test covers pointer reordering and supersets; docs updated.

Completed September 10 by `/root` in [PR #5](https://github.com/Madhavan113/researchharness/pull/5). Evaluator version 2 accepts required-pointer reordering/supersets and scores independently specified, scoped capture-backed gaps separately from fulfilled data requirements. Unsupported/access cases now have actual document/error responses and no impossible JSON placeholders. Every case has search noise; the naive policy no longer receives the authored gap choices. Both legitimate vendor alternatives still receive full credit. The revised comparison has twenty strict per-case wins and forty valid artifacts: macro quality 0.9495238095238095 versus 0.08333333333333333. The full required Docker/Omnigent suite passes 1,189 tests with zero skips or failures. Archive lifecycle tests now use stable independent fixtures, and the final-phase regression explicitly selects its intended wrong-identity source. The [evaluation guide](research-evaluation.md) documents the scoring convention and that no search run has used the twenty-case package. Human review and measured evaluation remain outstanding; old runtime archives are unchanged. See the [tracker handoff](goals/omnigent-integration.md#september-10-2026--rw-2-evaluator-and-benchmark-handoff) for exact verification and hashes.

### RW-3 · Exclude zero-quality and failed candidates from the final phase · `done` · confirmed

Files: `src/research_harness/optimization/archive.py` (frontier loop near lines 941–952), `src/research_harness/optimization/final.py`, `tests/test_optimization_archive.py`.

Problem: strict Pareto dominance keeps any candidate that is cheapest regardless of quality. Reproduced: baseline at quality 1.0 and 100 tokens plus a candidate that fails every case at quality 0.0 and 2 tokens yields a frontier of both, and `final()` then runs the crasher on every reviewed held-out case under the shared ledger.

Fix direction: exclude `quality == 0` or `failed_cases > 0` from the final set and record them as `excluded` with a reason, or require a minimum quality relative to the baseline. Keep the raw frontier in the journal for inspection.

Acceptance: the reproduction above selects only the baseline; a test asserts the exclusion reason.

Completed September 10 by `/root` on `fix/final-candidate-eligibility`: final selection now requires positive macro quality and no failed cases. The raw frontier and all rankable scores remain available with explicit exclusion reasons. Both the search controller and archive check the selected set and required baseline before beginning private evaluation, including for legacy selections. Rejection leaves the controller journal unchanged and does not read held-out inputs or dispatch work. The full configured suite passes 1,012 tests with zero skips; the focused archive/final/controller group passes 138 tests. See the tracker for commands and the retained local reports.

### RW-4 · Release reservations for provider error responses and denied admissions · `done` · confirmed

Files: `src/research_harness/evaluation/dispatch_budget.py` (reconcile loop near lines 590–628), `src/research_harness/evaluation/gateway_usage.py` (`_observation` near line 225), `src/research_harness/integrations/model_gateway.py` (admission near lines 577–604), `src/research_harness/evaluation/pilot.py` (`prepare_pilot` lines 127–163), `src/research_harness/evaluation/budget.py` (`settle` near line 465), `docs/pilot-budget.md`.

Problem, four parts:

1. An HTTP error body (429, 5xx) has no terminal `status`, so the verifier classifies it `nonterminal_response`, the outcome is `unknown`, and `reconcile_archive` holds the full worst-case reservation indefinitely. `cancel_before_dispatch` rejects dispatched rows and nothing records a provider-confirmed no-charge outcome. Reproduced with a mock 429: archive `consistent`, row `held`, second reconcile unchanged. The review also identified automatic SDK retries as a possible multiplier of held reservations; verify the effective retry count through the pinned Omnigent executor before changing it. Missing usage does not establish that zero tokens were consumed or that no charge occurred.
2. When admission is denied after `ledger.reserve` succeeds (deadline, closed gateway, concurrent policy violation), the record has no `budget_operation_id`, so reconcile cannot take the not-dispatched release branch. Reproduced: zero upstream dispatches, row `held`.
3. `prepare_pilot` creates the ledger before writing `.pilots.json`; any failure in between leaves a ledger every later prepare refuses, and the docs forbid deleting it. `prepare_search_run` already guards this.
4. `settle` accepts rows never marked dispatched; only `DispatchBudget` guards it.

Fix direction, corrected September 10 to preserve the accepted "unknown outcomes hold" invariant: retain error-response reservations until sufficient provider accounting evidence can resolve them; do not infer zero cost from a missing usage field. Retain and bind the raw error body to any future recovery proof. Write `budget_operation_id` before the post-reserve denial check; pin `max_retries=0` in the Omnigent arm or explicitly account for every retry; persist the pilot registry before comparison preparation without resetting retained funds; add a `dispatched_at` invariant to `settle`; add a CLI for `record_authorization` since the docs present it as the workflow.

Acceptance: 429/500 tests retain holds when cost is unknown and permit resolution only with sufficient bound accounting evidence. Denied-after-reserve requests release only from a valid archive proving no dispatch; invalid policy evidence retains holds. Failed comparison preparation permits another attempt with the same ledger and registry. Settlement without a durable dispatch marker is rejected. Verify the effective Omnigent retry behavior and authorization-recording CLI. Document release timing as reconciliation after archive seal.

Partial checkpoint, September 10 by `/root`, published in [PR #3](https://github.com/Madhavan113/researchharness/pull/3), stacked on PR #2: implemented acknowledged reservation binding before later denial, verification of prepared but unadmitted requests, the dispatch-marker settlement invariant and an empty registry before comparison preparation. Seven new regression cases cover deadline/closing/policy denials, reserved/held settlement rejection, a lost dispatch marker and failed comparison preparation. The full configured suite passes 1,019 tests with zero skips; Ruff lint and formatting pass. The budget guide now states the release timing. Provider-error accounting, effective retry behavior and the authorization CLI remain open; failure during initial ledger/registry persistence remains fail-closed. RW-4 is not complete. See the tracker for checkpoint validation and publication.

Continuation in PR #3: gateway retry enforcement and the authorization-recording CLI are implemented and verified with 1,045 configured tests, zero skips. The pinned factory does not pass authored retry settings. New gateway archives record the controlled policy and SDK retry headers; retries are rejected before reservation/dispatch, including after a lost response. Error responses advise the SDK not to retry. Both actual runtimes dispatch once for 429 and 500 fixtures and retain one unresolved hold. The CLI records external authorization metadata in an existing ledger without changing balances or creating a missing ledger. Provider-error accounting resolution and the initial ledger/registry persistence boundary remain outstanding; RW-4 stays in_progress.

Completed September 10 by `/root` in the next PR #3 checkpoint: durable initialization intents now let pilot and search preparation recover interrupted ledger/registry creation only from the exact retained empty state. Write failures, process death, concurrent initialization, changed/spent state and mismatched configuration are covered. Existing missing state without an intent remains refused. The host-only accounting CLI records operator-reviewed final-charge confirmation for a verified sealed error, with request/operation/evidence bindings, retained source text/hash, reviewer and reference. It preserves unknown token usage, rejects duplicate/conflicting or excessive charges and never replays provider work. Authenticating the external provider confirmation remains the operator's responsibility; an HTTP error alone never proves zero cost. Pilot/search witnesses continue to validate these terminal records, and provider confirmations stay outside feedback. The full configured suite passes **1,114 tests, zero skips, zero failures**, including 69 additional regression cases since the prior checkpoint. The [budget guide](pilot-budget.md) documents both recovery paths and reporting semantics; the tracker records the exact command and report hash. This completes RW-4 software acceptance; other P0 follow-ups and the external measured-run prerequisites remain open.

## P1: before the next checkpoint

### RW-5 · Strip operator paths and hostnames from archived evidence · `open` · confirmed

Files: the evidence writers used by `examples/evaluation/*_fixture.py` and `examples/omnigent/*.py`; `examples/omnigent/evidence/*/agent/tools/mcp/research.yaml`, `examples/omnigent/evidence/*/metadata.json`, `examples/evaluation/evidence/search-orchestration-2026-09-09/pytest.xml`, and 74 files inside eight `artifacts.tar.gz` archives, including `verification-tools/audit-budgeted-search.py` in the budgeted archive, which hard-codes the repository path.

Problem: `/Users/<user>/...` paths and a JUnit `hostname` attribute are committed. The repository is private, so this is not a disclosure today, but the archived agent bundles and the audit script run only on one machine, and a public release would leak the operator's identity.

Fix direction: relativize paths at archive time (the writer knows the repository root and the output root), strip or normalize the JUnit `hostname` attribute, and make the audit script take the repository path as an argument. Do not rewrite history; regenerate archives at the next checkpoint and note the change in the evidence README.

Acceptance: `git grep -l "$HOME"` over the evidence directories and a grep over freshly extracted archives return nothing.

### RW-6 · Adopt an artifact retention policy · `open` · confirmed

Files: `examples/evaluation/evidence/*/artifacts.tar.gz`, `examples/omnigent/evidence/*/model-requests.json.gz`, `.gitattributes`, evidence READMEs.

Problem: this checkpoint adds about 23 MB of compressed files; branch blobs total 30 MB against 0.3 MB on `main`; the two large archives share few content hashes so git cannot delta them; eleven checkpoints landed in two days.

Fix direction: keep `index.json`, `acceptance.json`, `verification.json` and the README in git (hash-anchored, sufficient for independent verification); move `artifacts.tar.gz` to GitHub release assets or LFS; document the download step and the recorded SHA-256 in each evidence README; drop `checkpoint-source/` from archives once the commit hash is recorded.

Acceptance: a new checkpoint adds under 1 MB to the repository and its README says where the archive lives and how to verify it.

### RW-7 · Make runtime test skips visible and enforceable · `done` · confirmed

Files: `pyproject.toml` (`addopts`), `tests/conftest.py`, ten skip sites (for example `tests/test_strategy_sandbox.py:28`, `tests/test_omnigent_integration.py:138`, `tests/test_comparison_runtime.py:647`), new `.github/workflows/`.

Problem: `addopts = "-q"` plus the documented `uv run pytest` prints no skip summary, so the 26 Docker/Omnigent tests and the 31 MCP tests skip silently when their gates are unset. No CI enforces the recorded 1,004-test configuration.

Fix direction: change `addopts` to `-ra`; document both runtime env vars in `conftest.py` alongside the Postgres ones; add an opt-in `RH_TEST_REQUIRE_RUNTIME=1` that turns gated skips into failures; add a CI workflow running the ungated suite on every push and the full configuration where a Docker runner is available.

Acceptance: a plain `uv run pytest` shows the skip reasons; CI is green on the ungated suite.

Completed September 10 by `/root` in [PR #4](https://github.com/Madhavan113/researchharness/pull/4), commits `442ad31` and `b8eb2c3`. Default `-ra` reporting exposes skipped checks; required mode validates configuration/dependency availability and fails on any collection/setup/call/teardown skip. Thirteen subprocess regressions cover that contract. The hosted ordinary job exposed a gateway response-completion race, fixed with connection-close framing and two deterministic JSON/SSE regressions. On the corrected source, [both CI jobs pass](https://github.com/Madhavan113/researchharness/actions/runs/34539869346): ordinary checks report 1,101 passes and 28 explicit runtime skips; required Docker/Omnigent checks report 1,129 passes and zero skips. The local required suite also passes 1,129 tests. Ruff, actionlint and local documentation links pass. [Test configurations](testing.md) documents reproduction and the separate shared-storage/live-provider limits. No paid model calls or new runtime archives were included.

### RW-8 · Stop ordinary errors from losing a whole proposer attempt · `done` · confirmed

Files: `src/research_harness/optimization/workspace.py` (`write_file` near lines 607–616, argument encoding near lines 500–501, `_relative` near lines 157–170), `src/research_harness/strategies/sandbox.py` (`created = True` before `docker create` at lines 319–320), `src/research_harness/optimization/proposer.py` (attempt abort near lines 521–525).

Problem: `write_file` mutates the workspace before checking the planned inventory, so on a case-insensitive filesystem a second write differing only in case strands the workspace as `uncertain` and the attempt can never submit. A lone surrogate in tool arguments raises `UnicodeEncodeError` out of `call()`. Any `docker create` failure, including a definitive non-zero exit, is recorded as `absent_at_check_creation_unconfirmed`, which never becomes quiescent, so the attempt is permanently lost.

Fix direction: stage-and-swap writes as `_publish` does, or reject case-fold collisions in `_check_planned`; reject non-printable and surrogate code points in `_relative`; distinguish proven pre-creation rejection from a lost acknowledgement. A nonzero exit without a container id is not sufficient proof by itself: the CLI also uses nonzero exits for transport/proxy failures.

Acceptance: unit tests for case collision, surrogate arguments and a failing `docker` stub, each ending with a recoverable workspace.

Completed September 10 by `/root`, branch `fix/proposer-workspace-recovery`, based on PR #6. Reproduced both a case-alias write overwriting the original on the local filesystem and an unpaired surrogate escaping the tool handler. Implemented alias/type checks before writes, including empty directories; recorded, repairable Unicode errors; and durable recognition of the CLI's missing-image/unknown-flag rejections. All other unacknowledged create failures retain conservative cleanup. The [strategy guide](strategy-optimization.md#coding-proposer-and-search-controller) records the upstream evidence for that distinction. The full required suite passes 1,238 tests with zero skips, including actual proposer error repair/submission, subprocess failure stubs and actual Docker rejection/recovery; the shared tracker records the exact command and report hash.

### RW-9 · Close MCP and CLI parity gaps · `done` · confirmed

Files: `src/research_harness/mcp/server.py`, `src/research_harness/cli.py`, `src/research_harness/services/research.py`, `src/research_harness/services/jobs.py` and `src/research_harness/services/errors.py`.

Problem: MCP validates `SearchFilters` after `_operate` inserts the running row, so an invalid request consumes search budget while the CLI consumes nothing. `_failure` classifies by substring, so a source hostname containing "writer" is reported as retryable `operation_busy`, and replaying the same operation id repeats the misclassification. The serve command's model default conflicts with any case begun under another model. Three tools marked `readOnlyHint` write job status through reconciliation. Every envelope's `remaining` opens a registry connection plus one store per pipeline, and `_remaining` swallows all exceptions.

Fix: validate filters before `_operate` without changing stored request hashes; classify by exception type (`WriterBusy` and host-assigned research errors); default `--model` to `None` and inherit the saved binding; mark reconciliation as mutating. Read remaining budgets directly from the registry on each response, preserving freshness across resumed hosts, and report backend failures explicitly instead of caching or hiding them.

Acceptance: tests for invalid filters not consuming budget, a 404 from a "writer" hostname classified non-retryable, and resume without `--model`.

Completed September 10 by `/root`, branch `fix/mcp-cli-parity`, [PR #8](https://github.com/Madhavan113/researchharness/pull/8), based on PR #7. The full required suite passes 1,264 tests with zero skips, including 26 added cases for service/direct/MCP admission, failure classification and replay, actual writer locks, fresh budgets and failure reporting, and custom-model CLI stdio resumption. Reconciliation annotations and frozen binding rejection are also verified. The [service guide](research-service.md) documents the behavior and the shared tracker records the command and report hash. The original documentation-only checkpoint has been followed by the implemented fixes.

### RW-10 · Preflight strict provider schemas · `done` · confirmed

Files: `src/research_harness/integrations/provider_schema.py`, direct discovery, MCP server, schema/SDK/protocol/runtime tests, and `examples/omnigent/provider_schema_preflight.py`. The existing `search_preflight.py` checks Keenable's MCP endpoint, not OpenAI schema acceptance.

Problem: the installed SDK strips only `None` defaults, leaving non-null `default` annotations in generated search and nested proposal schemas. This was confirmed in the actual SDK. Live API rejection has not been reproduced; numeric/array bounds have documented support and should be preserved independently of default handling.

Fix direction: make tool-schema fields nullable and apply defaults host-side; run the preflight once provider access exists and record the result in the tracker.

Acceptance: a recorded live preflight or a unit test asserting the generated strict schema contains no `default` keys.

Completed September 12 by `/root` in [PR #11](https://github.com/Madhavan113/researchharness/pull/11), branch `fix/strict-provider-schemas`, based on PR #10, using the unit-test acceptance option. A shared adapter emits default-free, closed, required schemas while host validation restores nullable defaults and preserves existing probe identity and operation replay. The offline preflight checks actual SDK serialization/parsing, three direct function schemas, one proposal schema, fourteen MCP input schemas and fourteen strict Agents SDK conversions. All new schema/SDK/protocol tests and the actual controlled Docker/Omnigent comparison pass. The full required run records 1,331 passes and four runtime timeout failures; a focused rerun passes the context/rejection checks but retains two one-second runaway-test timeouts. These are recorded in the [tracker handoff](goals/omnigent-integration.md#september-10-2026--rw-10-strict-provider-schema-ownership), with no relaxed limits or assertions. Live API acceptance remains a separate M0 gate, with provider access and spending decisions still pending.

### RW-11 · Storage parity fixes · `done` · confirmed

Files: store, registry, backend, blobs, CLI and research/job services; package pins; backend/service/job tests and a frozen original SQL schema; `docs/backend.md`.

Problem: databases created on `main` never receive the `runs_pipeline` index because it is bundled into migration 1. Inspection runs under the shared writer lock yet advances a `source_state` checkpoint, contradicting the backend doc. `rh pipelines list` reports `latest_run: null` in local mode because runs live in per-pipeline SQLite files. SQLite locks ignore `scope` and `shared` and are not re-entrant in-process. Registry inserts do not catch UNIQUE violations. `S3Blobs.put` does not verify an existing object's bytes. Workers inherit the entire parent environment including provider keys.

Fix: forward migration 5 repairs old and already-upgraded databases; inspections use `finish_probe` without checkpoint updates; CLI summaries load local pipeline runs by question. Scoped shared/exclusive locks retain the legacy global guard and safe reentry. Registration uses conflict-aware inserts with nested rollback; SQLite begins outer write transactions before reads. S3 verifies existing bodies and uses conditional uploads. Workers receive only selected runtime/storage environment settings. Enforce SQLite 3.35 and Boto3 1.35.2 minimums.

Acceptance: a migration test from `main`'s schema; a test that inspection leaves `source_state` untouched; a CLI test for `latest_run`.

Completed September 10 by `/root`, branch `fix/storage-parity`, based on PR #8. The required Docker/Omnigent suite passes 1,285 tests with zero skips. Separate disposable PostgreSQL/MinIO acceptance passes 166 tests (59 Postgres/S3) and the detached workflow/restart/export checks, including native conditional-upload corruption rejection. The [backend guide](backend.md) documents the contracts; the shared tracker records exact commands, report hashes and cleanup evidence. These are local fixture/runtime checks, not remote deployment or measured-model results.

### RW-12 · Tighten split checks and add a leakage audit · `done` · confirmed

Files: benchmark split validation, optimization controller and new `optimization/leakage.py`; dependency pins, focused benchmark/controller/final/audit tests, synthetic runtime helpers and strategy/evaluation documentation.

Problem: disjointness is checked by case id, topic group, free-text family label, brief hash, fixture hash and exact hostname, so a held-out case on a sibling subdomain passes. Admission is `ast.parse` plus size only; the paper's regex and manual audit for task-specific string leakage into evolved harnesses has no equivalent.

Fix: compare canonical registrable domains with the pinned offline PSL snapshot, including private hosting boundaries, gap alternatives and public fixture URLs. Freeze a development-only audit catalog from public task ids/briefs and source-fixture URLs; scan baseline/candidate code and instructions, bind bounded reports to exact inputs/bytes, and record advisory `leak_suspect` flags in the journal. Reports enter subsequent proposer snapshots; the full catalog, expected answers and evaluator predicates do not. Manual review remains separate, and a no-match result is not proof against overfitting.

Acceptance: tests for a sibling-subdomain overlap and for a candidate containing a dev-case URL.

Completed September 10 by `/root`, branch `fix/split-leakage-audit`, based on PR #9. Final required Docker/Omnigent tests pass **1,315 cases with zero skips**, including 30 added cases and stronger existing assertions. The complete synthetic runtime workflow passes three proposer iterations, seven development and seven isolated final evaluations; all seven audit reports match their files/catalog and subsequent snapshots contain 1/3/5 reports without the full catalog. A fresh-process check reproduces every audit and verifies finalized state. Historical archives and the private held-out draft remain unchanged. Exact commands and evidence hashes are in the [shared tracker](goals/omnigent-integration.md#september-10-2026--rw-12-split-isolation-and-leakage-audit-ownership).

## P2: quality and efficiency

### RW-13 · Remove repeated hashing on hot paths · `open` · plausible

`strategies/session.py` (lines 149–187) re-hashes every completed event on every `apply`, `assert_ready`, `guard` and `status`. `optimization/workspace.py` (lines 382–394) re-hashes the whole feedback tree on every tool call. `optimization/controller.py` (lines 247–268) re-hashes all inventories on every open, so `status` is O(archive bytes). `evaluation/pilot.py` (lines 199–236) re-verifies every registered run's archives on every case start and proposer attempt, and a moved run directory raises a raw `FileNotFoundError`. Cache by inventory digest and mtime; convert the missing-directory case into a ledger error.

### RW-14 · Test suite maintenance · `done` · confirmed

`tests/test_model_gateway.py:381–406` asserts `0.85 <= elapsed < 1.5` around a 0.8 s sleep and 1.0 s deadline; widen margins or use a fake clock. `fake_runner` is imported across test modules from `test_strategy_session.py`; move it to `conftest.py`. The `search` fixture in `tests/test_optimization_controller.py` replaces `source_fingerprints`, so no controller-level test exercises the real implementation-freeze check. Tests read the twenty committed cases as inputs, so editing cases under RW-2 changes test behavior; snapshot the inputs the tests need.

Completed September 12 by `/root` on `test/reproducible-runtime-checks`, based on PR #11. The late-chunk deadline test uses a controlled clock/timer and a real HTTP connection. Shared observation-runner evidence moved to `conftest.py`; two new controller cases exercise the real fingerprint scanner against altered working/frozen implementation bytes before execution. Six lifecycle/leakage modules use four frozen cases with original content hashes; benchmark/workbook tests still inspect the actual development package. Sandbox tests now exercise each guard independently: the deadline case remains one second, while output/memory cases use the production default ten-second watchdog with unchanged 1,024-byte/64-MB caps and additional truncation/OOM assertions. The full required suite passes **1,337 tests with zero skips**; all three real-Docker guard checks also pass separately. The [testing guide](testing.md#stable-test-inputs-and-guard-coverage) and [tracker handoff](goals/omnigent-integration.md#september-12-2026--rw-14-test-maintenance-ownership) record the contracts and exact verification. No production limit changed.

RW-14 is published in [PR #12](https://github.com/Madhavan113/researchharness/pull/12), stacked on PR #11. Its [hosted run 34719113011](https://github.com/Madhavan113/researchharness/actions/runs/34719113011) passed both ordinary tests/lint and the required Docker/Omnigent job at head `9230c91`. The remaining small items below are separate work.

### RW-15 · Small items · `in_progress`

- [x] Declare directly imported `anyio` under the `mcp` extra; all 60 locked package versions are preserved.
- [x] Explain the four zero-byte archived logs in the Omnigent example guide; preserve their original bytes and hashes.
- [x] Document exact proposer `response.model` identity and rejection of aliases that resolve to a different name; no silent normalization of priced model bindings.
- [x] Guard selection, saved-selection replay and finalization against completed but unadmitted proposals, unresolved proposals and missing/nonterminal reserved candidates. Recovery retains every slot without redispatching the proposer.
- [x] Record the built-in SDK policies in new comparison-plan limitations: direct disables retries and uses `min(90, deadline_seconds)`; pinned Omnigent configures seven retries and 120 seconds. The gateway rejects retry dispatches and applies shared bounds; runtime behavior is unchanged.
- [x] Replace stale service verification counts and the outdated claim that Postgres/S3 execution was unavailable with current tracker/testing references.
- [x] Explicitly label the historical `independent-audit.json` results as same-project, same-author fresh-process checks using production verifiers, not external third-party review.
- [ ] Consider proposer exploration tools/limits after measured runs exist. Current defaults remain 16 rounds, 64 workspace calls, 48 KB per read and 16 KB stdout; the paper reports a median of 82 files read with grep.
- [x] Give copied feedback a private `0700` host parent before writing bytes, with a readable child mounted read-only into Docker. Recorded-layout and ownership checks preserve legacy recovery and safe cleanup after interruption; original sources and unrelated paths are preserved.

The bounded admission/compatibility checkpoint was completed September 12 by `/root` and published in [PR #13](https://github.com/Madhavan113/researchharness/pull/13), branch `fix/selection-admission-checks`, based on PR #12. Four regressions reproduce the original omission/replay gap; the final required Docker/Omnigent suite passes **1,341 tests with zero failures/errors/skips**. Lint, formatting, locked versions, local links and the diff pass. Exact commands, report hashes and remaining dependencies are in the [tracker handoff](goals/omnigent-integration.md#september-12-2026--rw-15-admission-and-compatibility-handoff). The feedback-permissions item was completed in the subsequent checkpoint below; exploration is the only unchecked task above.

The private-feedback checkpoint was completed September 12 by `/root` and published in [PR #14](https://github.com/Madhavan113/researchharness/pull/14), branch `fix/private-proposer-feedback`, based on PR #13. The final required Docker/Omnigent suite passes **1,352 tests with zero failures/errors/skips**, including all 66 workspace cases. The [private-feedback handoff](goals/omnigent-integration.md#september-12-2026--rw-15-private-feedback-handoff) records exact commands, hashes, the corrected legacy-fixture setup and the remaining external dependencies.

## Blocked on external decisions

Unchanged from the tracker: provider access, the spending decision, human review of the twenty development cases and the ten private held-out drafts, and the measured baseline. Do not start model-mode search until RW-1 through RW-4 are done and these are recorded.

## Suggested order for a continuing agent

1. Check PR #13 and #14 required CI results. PR #13 ordinary tests/lint passed; its required runtime job is still active. The final private-feedback required local suite passes.
2. RW-5 and RW-6: agree on artifact retention and path/hostname handling before the next evidence regeneration or public release.
3. RW-13 and RW-15: remaining independent offline efficiency and maintenance work.
4. Resolve the external decisions above, then perform live compatibility and the measured baseline before model-mode search. RW-10's offline acceptance does not replace that live compatibility gate.
