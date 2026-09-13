# Strategy optimization: implementation status

Isolated strategies, observation/context projection, independent development archival, a bounded coding proposer and the search/private-final controllers are implemented. The maintained seed now enables context selection and host-enforced finalization after a verified stop decision; the [current handoff](goals/omnigent-integration.md#september-10-2026--rw-1-strategy-control-ownership-and-decision) records verification. A shared ledger covers research, proposer and private final requests. Earlier complete fixtures verify three iterations with two candidates each through the actual coding proposer, Docker and normal Omnigent/MCP runtime, followed by isolated final evaluation. Reviewed benchmarks and measured model search/final evaluation remain unfinished; the accepted goal includes those runs.

The [budgeted runtime checkpoint](../examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/verification.json) records 1,004 passing tests with zero skips. Its accepted runtime run reserved and settled all 125 synthetic requests, with 13,750 synthetic tokens and 157 actual Docker executions. A [fresh-process audit](../examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/independent-audit.json) matched all seventeen gateway archives to the ledger. The [6,479-file archive](../examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/index.json) retains complete development/proposer evidence and fixture accounting witnesses; private final contents remain local.

The earlier [combined runtime checkpoint](../examples/evaluation/evidence/combined-strategy-search-2026-09-09/verification.json) records six passing helper tests, 125 synthetic model requests and 157 actual Docker executions. Its [6,380-file archive](../examples/evaluation/evidence/combined-strategy-search-2026-09-09/index.json) preserves complete development feedback, exact proposer input snapshots, closed attempts, interface checks and reproduction source. The one-case final package and execution contents remain private. That checkpoint's production source was unchanged from the preceding orchestration checkpoint.

The [orchestration checkpoint](../examples/evaluation/evidence/search-orchestration-2026-09-09/verification.json) separately verifies 985 unique tests with zero skips: 982 passed initially, and three runtime tests passed after restoring the missing pinned Omnigent environment. Both reports are retained. Its [227-file archive](../examples/evaluation/evidence/search-orchestration-2026-09-09/index.json) includes 105 source/test/configuration hashes, complete actual coding-proposer artifacts and coordinator fixture metadata. The proposer fixture records eight requests, actual generated-program execution and 880 verified synthetic tokens.

The [context/evaluation checkpoint](../examples/evaluation/evidence/strategy-context-evaluation-2026-09-09/verification.json) retains 97 source/test/configuration hashes and the [876-test report](../examples/evaluation/evidence/strategy-context-evaluation-2026-09-09/pytest.xml). All tests passed with zero skips, including actual Omnigent and Docker checks. Its [725-file archive](../examples/evaluation/evidence/strategy-context-evaluation-2026-09-09/index.json) preserves context and development evidence plus reproduction source. The earlier [787-test observation checkpoint](../examples/evaluation/evidence/checkpoint-2026-09-09/verification.json) and [foundation verification](../examples/evaluation/evidence/strategy-foundation-2026-09-08/verification.json) remain separate immutable records. These are software/fixture checks, not a measurement of research quality.

Meta-Harness lets a coding proposer inspect prior candidate code, scores and full available execution traces through a filesystem, then propose executable changes. This implementation keeps that feedback channel and selects only on development results. Test feedback stays outside search. Its bounded Responses proposer is a domain adaptation of the paper's coding-agent setup. [Paper, section 3](https://arxiv.org/html/2603.28052v1).

## Execute a candidate in isolation

[DockerStrategyRunner](../src/research_harness/strategies/sandbox.py) copies one Python file, an explicit JSON event and a standalone worker into a new attempt directory. It never imports candidate code in the host process. The candidate implements `apply(event) -> dict`; its result remains untrusted input for a host projection validator. Code can implement ranking, formatting and state-update algorithms with the Python standard library. Current request events and returned state are supplied explicitly; this executor does not itself manage a conversational strategy state or call research/model tools.

The [21-test isolation acceptance](../examples/evaluation/evidence/strategy-isolation-2026-09-08/acceptance.json) exercised ten actual containers, including credential/file/network isolation, invalid output, resource limits and host interruption. Its [75-file archive](../examples/evaluation/evidence/strategy-isolation-2026-09-08/index.json) preserves exact code, inputs, outputs and effective settings.

The [authored example](../examples/strategies/source_order.py) ranks two supplied source objects, renders observations and returns stop advice. It is a fixture, not an optimized strategy. The [runtime configuration](../examples/strategies/sandbox.proposed.json) pins the official Python image used in local acceptance; the image reported Python 3.13.15 on Linux arm64. Docker must already have that digest. The runner never pulls an image or falls back to host execution.

~~~sh
uv run python -m research_harness.strategies.sandbox \
  --config examples/strategies/sandbox.proposed.json \
  execute examples/strategies/source_order.py \
  --input examples/strategies/source_order.input.json \
  --out /tmp/research-strategy-example
~~~

Execution uses a nonroot user, no network, a read-only root/input mount, no added capabilities or devices, no new privileges, default seccomp, and bounded CPU, memory, process count, scratch space and output. The host checks effective Docker settings before starting the candidate. No backend, evaluator, held-out package, provider credentials or Docker socket is mounted. The host and Docker daemon remain trusted infrastructure; this is not a claim about resistance to kernel vulnerabilities. [Docker execution controls](https://docs.docker.com/engine/containers/run/).

Each attempt retains exact source, worker and input bytes, stdout/stderr, decision, effective container settings, errors, timing and file hashes. Output beyond the configured limit terminates the candidate and is explicitly marked truncated; oversized input/source fails before execution. Failed and interrupted output directories cannot be reused. Recovery takes the execution lock and removes only the recorded, matching container; it never reruns code. A create request whose acknowledgement was lost can remain uncertain even when an immediate inspection finds no container. The report preserves that uncertainty for a later cleanup inspection.

~~~sh
uv run python -m research_harness.strategies.sandbox \
  --config examples/strategies/sandbox.proposed.json \
  recover /tmp/research-strategy-example
~~~

## What the Omnigent hook actually supports

The [normal-runner fixture](../examples/evaluation/native_strategy_hook_fixture.py) verifies that a native TOOL_RESULT policy can replace a serialized research observation before the SDK receives it. Reversing two source results, formatting a title and adding stop advice changed the next model request; the stored search receipt remained byte-for-byte equivalent in its original result data.

The [archived hook acceptance](../examples/evaluation/evidence/native-strategy-hooks-2026-09-08/acceptance.json) and [41-file artifact index](../examples/evaluation/evidence/native-strategy-hooks-2026-09-08/index.json) preserve the complete available requests, original receipt, projected observation, authored policy and runtime exports.

The native LLM_REQUEST policy received only prompt/user previews and ran once across three provider calls. Its replacement data was ignored. It does not establish a full-context rewrite or a per-round stopping hook. Observation projection also cannot remove older observations already retained in SDK history. Stop advice in that historical native-hook fixture is advisory; the shared host controls below enforce finalization when explicitly enabled.

~~~sh
uv run --extra mcp python examples/evaluation/native_strategy_hook_fixture.py \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python \
  --out /tmp/research-native-hook-fixture
~~~

The shared integration below applies validated structured-observation projection to direct and MCP results before their differing serializations. The separate controlled gateway now selects complete conversation groups while retaining fixed model, tool and budget controls.

## Shared observation strategies

[StrategyBundle](../src/research_harness/strategies/config.py) validates a bounded manifest, source digest and sandbox limits, then freezes exact source bytes without importing them. [StrategySession](../src/research_harness/strategies/session.py) wraps completed search, inspection and probe operations in the direct dispatcher and MCP adapter. The host assigns stable references to supplied results, executes the candidate with explicit state, and validates its decision before serializing the model observation. Candidate code has no service or provider access. Exact-operation retries reuse the recorded projection; changed inputs or candidate identity are rejected.

The [observation contract](../src/research_harness/strategies/projection.py) enforces these rules:

- Ranking and selection may refer only to result identities supplied in that event. Duplicate or invented references fail validation. The original receipt, operation status, errors, evidence references and remaining budgets remain authoritative.
- Rendered text is candidate-generated interpretation, with bounded size and explicit references to its source results. It cannot create observed URLs, passing probes, saved proposal ids or collection outcomes. The existing service still validates proposals against its original receipts.
- With `finalize_on_stop: true`, a verified stop recommendation ends new discovery operations and enters the finalization phase below. A stopped attempt without a saved valid proposal remains incomplete in independent evaluation. Omitted or false controls retain the legacy advisory behavior.

Each event retains the candidate hash, exact input, isolated execution artifacts, validated decision and state transition. File hashes and state chains are checked on reuse. Invalid decisions and exhausted strategy budgets fail the session and prevent further model work or proposal submission. A durable pending event survives interruption; explicit recovery validates completed output or records failure and cleans up the recorded container, without rerunning candidate code. Stored research receipts remain authoritative.

The [maintained example](../examples/strategies/research_strategy.py) and [manifest](../examples/strategies/research_strategy.json) enable observations, context selection and `finalize_on_stop`. The authored policy reorders/renders supplied results, keeps required context groups and recommends stopping on an empty result set; it is not an optimized policy. Supply `--strategy examples/strategies/research_strategy.json` to `rh discover` with `--search-provider mcp`, to `rh mcp serve`, or to the Omnigent `prepare` command. Prepared cases freeze code outside the agent/plugin directory and persist its identity across restarts. Direct and standalone MCP output directories contain a `strategy/` session; prepared Omnigent cases use the case's `strategy/` directory by default. MCP alone controls observations and source admission; full Omnigent request projection requires the bound controlled gateway described below.

~~~sh
uv run rh strategy status --out /path/to/case/strategy
uv run rh strategy recover --out /path/to/case/strategy

uv run --extra mcp python examples/evaluation/strategy_observation_fixture.py \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python \
  --out /tmp/research-strategy-observation-fixture
~~~

The [acceptance record](../examples/evaluation/evidence/strategy-observation-2026-09-09/acceptance.json) verifies matching direct/MCP projection through the actual normal Omnigent server and runner, two actual strategy containers, four synthetic model requests, unchanged receipts and exact retries with one execution per path. A host-import guard remained untouched. The [120-file archive](../examples/evaluation/evidence/strategy-observation-2026-09-09/index.json) retains the exact fixture and available execution evidence. This acceptance covers observation projection; it does not measure proposal quality or optimization.

Strategy readiness and a domain operation now share one session lock through proposal commit. A concurrent failed strategy cannot pass a separate readiness check and commit afterward. Every prepared case binds a fresh session identity as well as the code digest. Missing or replaced state is rejected without executing a cached observation again. Older strategy-bound cases from the earlier checkpoint lack this identity and cannot resume with the new loader: restore the corresponding code/state version or prepare a new case. Baseline cases without strategies remain compatible.

## Controlled context selection

[Context projection](../src/research_harness/strategies/context.py) groups the actual Responses input on the host. A candidate returns `{"keep_group_ids": [...]}` using existing identities in their original order. The host retains every user/system/developer message and all items since the latest user message, including reasoning items and complete tool exchanges. Only older completed model/tool interactions may be omitted. This is a conservative harness policy informed by the provider's [reasoning-context guidance](https://developers.openai.com/api/docs/guides/reasoning#keeping-reasoning-items-in-context), not a claim that every retained item is a JSON-schema requirement.

Direct discovery applies enabled context decisions before the SDK request. For controlled comparisons, it delegates request projection to the host [ResponsesGateway](../src/research_harness/integrations/model_gateway.py), avoiding duplicate candidate execution. Omnigent request projection requires that gateway to receive the same bound `StrategySession` as MCP; the native hook still does not rewrite full requests. Each controlled request archives the original and projected bytes, group decision and isolated strategy event. Independent gateway verification checks the projection and fixed controls against that proof. Spending reservations use the final projected bytes; deadline checks and shutdown wait for strategy work before sealing the archive.

The actual normal Omnigent follow-up fixture made five synthetic provider requests and five Docker executions. Initial research retained its full active turn. After a real second user message, history changed from 7 to 2 items, then 9 to 4; both user messages survived. The provider fixture reported 550 independently verified synthetic tokens. A single-brief run may have no removable history under this policy, so these counts establish integration behavior rather than quality or cost improvement.

The [acceptance record](../examples/evaluation/evidence/strategy-context-evaluation-2026-09-09/acceptance.json) also covers the controlled direct and Omnigent comparison: 4/7 synthetic requests, 7/10 isolated strategy executions and 440/770 verified synthetic tokens respectively. Both independently graded fixture proposals scored 1.0, and the archive retained the execution files without upgrading either run to verified model evidence. Reproduce both boundaries with the already-installed pinned image and separate Omnigent environment:

~~~sh
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
uv run --extra mcp pytest \
  tests/test_strategy_context.py::test_actual_normal_runner_followup_prunes_only_the_previous_completed_interaction \
  tests/test_comparison_runtime.py::test_controlled_strategy_uses_real_docker_and_both_runtime_paths -q
~~~

Comparison and pilot execution now freeze the candidate bundle and reserve one session identity for each case before dispatch. Both direct and Omnigent paths share that session with their gateway and record its artifacts on success, failure and interruption. A strategy comparison can vary instructions and code while retaining the remaining controls. Candidate limits, sandbox settings and the complete execution inventory stay bound to the run.

## Finalization after a verified stop

The frozen `finalize_on_stop` manifest control makes `stop_recommended: true` an execution decision. The host derives it from the first verified completed observation event for the discovery, so later candidate state cannot undo it and restart/replay preserves it. Direct and MCP admission refuse new searches, inspections and probes before service budget consumption. Completed operations can replay their original receipts; changing the arguments under a completed operation id still fails the service's replay checks.

Direct requests then expose no discovery tools and request the structured proposal. Controlled Omnigent requests retain only existing evidence/context reads, case resumption, pipeline listing and proposal submission. A fixed host instruction explains finalization; candidates cannot rewrite that instruction. Submission still checks the original receipts and can repair a rejected draft within the original model-round, deadline, strategy and spending limits. Stopping grants no extra calls and cannot turn unsupported or unverified sources into ready sources. If no valid proposal is saved before the limits expire, the attempt fails evaluation.

The gateway copies the isolated stopping event and binds it to an actual incoming function-tool output before reserving or dispatching a provider request. It supports the recorded direct JSON and pinned Omnigent MCP serializations. Independent usage verification reconstructs finalization from that proof and checks the forwarded tools/instructions; missing, changed or mismatched proof invalidates the control evidence. An in-flight request admitted before a concurrent stop may finish, but source admission still refuses new discovery once the stop is recorded.

Older manifests omit `finalize_on_stop` and retain their canonical hashes and advisory behavior. New searches using the maintained seed freeze context selection and finalization as enabled for every candidate; candidate code controls the selected context groups, source ranking/rendering and stop decision, and its instructions file remains editable. The host request mechanics and limits remain fixed. Per-turn replacement of host instructions is not part of the accepted candidate interface.

The [stopping tests](../tests/test_strategy_stopping.py) exercise durable decisions, malformed output, receipt replay, source admission, SDK/MCP proposal completion, fixed limits and altered gateway proofs. The [runtime comparison](../tests/test_comparison_runtime.py) exercises both enabled and legacy controls with actual Docker and pinned Omnigent. These use synthetic provider responses; they do not establish optimization gains or live provider compatibility.

## Development archive and final evaluation

[OptimizationArchive](../src/research_harness/optimization/archive.py) keeps frozen backend/evaluator/benchmark inputs and its operation journal in host-only state. A separate feedback directory contains candidate bundles, complete dedicated development artifacts and independent per-case evidence. Source trees and artifact trees are copied and hashed; symlinks, hardlinks, path escapes, extra files and changed bytes are rejected. Size limits fail an archive operation rather than silently summarize away artifacts.

A trusted evaluator callback receives the frozen artifact copy and returns evidence bound to the candidate, artifact inventory, controls, evaluator and development benchmark. Every case and failure must be represented. A callback identity is not authenticated by a supplied fingerprint: the controller must instantiate the actual independent evaluator. Candidate-provided quality claims are not an evaluator.

[export_development and ResearchDevelopmentEvaluator](../src/research_harness/optimization/evaluator.py) connect completed comparison arms to the archive. Export copies every controller-recorded execution file unchanged, including available raw requests, strategy attempts, receipts, runtime traces and failure artifacts. It excludes the comparison's private benchmark and implementation trees. Historical exports validate the frozen implementation and do not require the current worktree to match or rerun providers. The trusted executor remains responsible for the recorded attachment inventory.

The evaluator independently regrades saved proposals against frozen development requirements, verifies gateway usage and strategy state without executing candidate code, and retains failed cases with zero quality and any known usage. Synthetic and unknown-transport evidence cannot establish a measured model baseline. Model eligibility additionally requires standard host HTTP dispatch, matching approved production budget controls, positive observed responses and complete settlement evidence. These are trusted-host provenance checks, not authentication against a malicious host or fresh spending permission. The [bridge tests](../tests/test_optimization_evaluator.py) cover private inputs, forged score claims, failures and altered bindings; the [two-runtime acceptance](../tests/test_comparison_runtime.py) also archives actual strategy executions.

Model-mode admission requires reviewed development cases, explicit model/provider/budget/sandbox controls and a verified measured baseline before further candidates. Fixture mode tests this lifecycle without making model-quality claims. Selection computes the quality/token Pareto frontier among candidates with positive macro quality and no failed development cases; unknown objectives and unverified model executions also stay excluded. `candidate_ids` contains this eligible frontier. The journal retains all rankable scores in `ranked`, the unfiltered diagnostic frontier in `raw_candidate_ids`, and `failed_development_cases` or `zero_quality` exclusion reasons. Ineligible candidates cannot dominate eligible ones. The selection intent closes search and freezes the decision in the same journal write. Retrying that operation only completes the recorded decision.

Publishing candidate feedback first records an intent binding the exact host inventory and evaluator outcome. Files are written in the recorded `publication_staging_dir` outside the feedback tree, then atomically moved into place; a partial write never becomes proposer feedback. `recover_publication(operation_id=...)` completes the authorized file copy and journal commit without invoking the evaluator again. Pending publication temporarily prevents exposing the feedback path; unknown or changed published files remain integrity failures. Interrupted evaluator work has a separate `recover_development` path that preserves available files and records missing evidence.

Beginning a final attempt records its binding and phase before any private output work. Failed or interrupted preparation can be sealed with explicit missing-directory/selection evidence; a completed final status requires those preparation files. A durable seal intent lets a retry finish unchanged metadata writes. Final artifacts stay in the separate private directory; no operation reopens search or replays evaluation automatically.

The host must enforce the directory boundary when launching the proposer and terminate/revoke that proposer before final evaluation. The archive API alone does not isolate an already running process. The search controller and workspace below enforce that boundary for the supplied coding proposer; the trusted caller remains responsible for external executor ownership.

## Coding proposer and search controller

[ProposalWorkspace](../src/research_harness/optimization/workspace.py) exposes only an inventoried development feedback tree and a bounded writable workspace. Its `list_files` and `read_file` tools support pagination over complete files, including binary artifacts. `write_file` changes workspace files only. `run_python` executes generated code in the pinned Docker sandbox with read-only feedback and a bounded temporary workspace. It publishes file changes only after successful execution and confirmed cleanup; failed or interrupted programs retain their evidence and cannot silently resume. It never mounts evaluator code, held-out inputs or host credentials.

Ordinary tool errors leave the proposer able to repair its next call. Before writing, the host rejects case or Unicode-normalization aliases, conflicting file/directory paths and nonprintable path components. The check covers empty directories left by an earlier failed write as well as the planned file inventory. Lone surrogate arguments are retained as JSON escapes in the request evidence and rejected through the normal recorded tool-error path. They consume a workspace call without changing files or launching Python. Valid follow-up edits and explicit candidate submission remain available.

Docker creation failures require evidence that creation never began. The host recognizes a missing pinned image with pulling forbidden, and a CLI unknown-flag rejection naming an actual host-supplied flag. It retains the exit status, diagnostic and arguments in `creation_rejection`; these failures and earlier preflight failures stay `not_created` across recovery. This distinction follows the [Docker CLI's create path](https://github.com/docker/cli/blob/master/cli/command/container/create.go) and [Moby's image lookup before container allocation](https://github.com/moby/moby/blob/master/daemon/create.go), and is exercised with authored subprocess stubs and the actual CLI. An arbitrary nonzero exit, a proxy/transport error, a timeout or a lost acknowledgement cannot establish non-creation. Those still require ownership-checked cleanup, and uncertain cleanup stops proposer dispatch. Older archives retain their recorded semantics and bytes.

[ResponsesCodingProposer](../src/research_harness/optimization/proposer.py) uses those four tools plus explicit `submit_candidates`. The host binds each attempt to its full feedback inventory, fixed prompt/tool schemas, requested candidate IDs, model/settings and budget metadata. The SDK retains original model outputs and tool results across requests and disables automatic retries. Submission closes access before exposing immutable candidate snapshots. Failed gateway/workspace cleanup prevents a quiescence claim; explicit recovery cleans owned resources without replaying model or candidate work. A missing attempt directory alone is not proof that an interrupted attempt never executed.

The proposer requires the returned `response.model` to equal its configured model identifier exactly. A provider alias that resolves to a differently named snapshot is rejected; the raw response is retained before that check. Before a live run, verify the provider's returned identifier and use that exact identity consistently in the proposer, frozen comparison settings and rate card. The example model setting is a proposed configuration, not evidence that a provider accepts it or returns the same name. The host does not silently normalize aliases or change the priced model binding.

[SearchController](../src/research_harness/optimization/controller.py) freezes the baseline, development package, implementation, strategy interface and limits. It evaluates the baseline through the independent archive bridge before proposing candidates. Its default is three iterations with two candidates each; every later snapshot includes previous candidate code, complete available development traces, proposal attempts and failures. The proposer chooses which files to inspect. Generated Python is parsed on the host and its public interface is exercised only inside Docker. The controller checks the reserved proposer binding and independently verifies gateway evidence before admission.

Selection requires every proposal to be completed or explicitly recovered, every completed proposal to be admitted, and every reserved candidate to have a terminal outcome. If a crash leaves a completed proposal awaiting admission, resume `step(...)` or `run(...)` to admit its saved files without another proposer call. Direct `select(...)` refuses that incomplete state before revoking the proposer or freezing selection. Selection replay and `final(...)` also reject older saved selections with unresolved proposals or missing candidate slots, before entering private evaluation; they do not repair or unfreeze those records.

The host supplies the trusted case executor and configured proposer to `run(executor=..., proposer=...)`. `step(...)` advances one candidate or iteration. The `prepare` and `status` CLI commands are read-only with respect to provider dispatch:

~~~sh
uv run python -m research_harness.optimization.controller prepare \
  /path/to/development/manifest.json \
  --baseline /path/to/strategy.json --instructions /path/to/instructions.md \
  --config /path/to/search-config.json \
  --out /path/to/new-search --feedback /path/to/new-feedback
uv run python -m research_harness.optimization.controller status /path/to/new-search
~~~

The config follows `SearchConfig`, including explicit workspace sandbox limits. Model-mode search requires independently reviewed development cases, frozen proposer budget controls and a verified model baseline. The fixture label records evidence provenance; it does not make an arbitrary caller-supplied executor offline. The checked-in tests supply synthetic transports and make no paid requests.

### Development leakage audit and split checks

New searches freeze a host-only `leakage-audit-inputs.json` with the development task ids, five-word brief phrases, and URLs/hosts from the public source fixtures (`search_results` and `responses`). Expected source configurations, requirement predicates, review notes and evaluator code are excluded. The plan binds this catalog's hash. No held-out package is read to prepare or run the audit.

The baseline and each bounded candidate admission get a `candidate-audits/<id>.json` report, bound to the exact Python/instruction bytes and catalog. The search journal records the report path/hash and `leak_suspect` flag; subsequent proposer snapshots contain the reports and original candidate artifacts, but never the full catalog. The audit uses case-insensitive literal URL, host and case-id matches with token boundaries, plus five-word brief sequences after case/punctuation/whitespace normalization. It records at most 200 unique file/term findings while retaining the full count and an explicit truncation flag. Candidate code is never executed by the scanner. Syntactically invalid submissions can still have an audit report.

Matches are advisory and do not change admission, scores or selection. Review the report alongside the hashed `strategy.py` and `instructions.md` to distinguish reusable logic from task-specific hardcoding. A reviewer should record the candidate hashes, findings considered, rationale and remaining concerns with the experiment's review evidence before making measured claims. `no_match` means only that this literal heuristic found nothing: encoded/generated strings, shorter phrases and semantic copying can evade it. Generic instructions can also match legitimate shared brief phrases. This supports, but does not perform, the [paper's manual inspection and regex audit](https://arxiv.org/html/2603.28052v1#S4.SS3). Legacy searches without the frozen catalog retain their historical records; absence of a report is unaudited, not a clean result.

Private final split validation still runs only after selection and proposer revocation. It compares registrable domains using the Public Suffix List snapshot in pinned `tldextract==5.3.2`, with HTTP fetching and user caches disabled. Private suffixes distinguish unrelated hosting tenants (for example, `a.github.io` and `b.github.io`), while sibling subdomains of one tenant overlap. Host comparison normalizes IDNA, case, trailing dots and IP literals; unknown suffixes use the implicit last-two-label rule, including synthetic `.example` domains. Source choices, gap alternatives and public fixture URLs all count. Existing id/topic/family/brief/fixture checks remain, and overlap errors name only the dimension, never private values. Shared domains can be conservatively rejected even when paths describe different datasets; human review must also consider common ownership across different domains. Domain grouping alone does not establish independence.

New synthetic final packages use a different registrable domain. Historical evidence is unchanged, and older packages that relied on sibling hosts must be regrouped before a new final evaluation. Keep any private regrouping and final reports outside proposer access; do not restart search using information learned from private split failures.

Selection closes the proposer and freezes the eligible development Pareto frontier. `final(heldout_manifest=..., output=..., executor=..., operation_id=...)` then invokes the [host-only final evaluator](../src/research_harness/optimization/final.py), which reads the private package only after revocation. It requires disjoint development/final cases and compares the selected candidates with the original baseline under the same controls. Before starting a new final attempt, the archive checks development eligibility again for every selected candidate and the required baseline. An ineligible baseline or legacy selection stops before private-directory creation, held-out loading or provider dispatch; the frozen selection is not rewritten. Completed historical final attempts remain readable and retry without execution. Final failures and unknown usage remain visible, final files never enter development feedback, and repeated completed operations do not dispatch again. Recovery methods preserve partial artifacts and require explicit resolution of uncertain work; opening a controller never resumes execution.

The [controller tests](../tests/test_optimization_controller.py) exercise complete search/final coordination with authored executors and a substituted strategy runner. The [proposer tests](../tests/test_optimization_proposer.py) and [workspace tests](../tests/test_optimization_workspace.py) separately cover real gateway threads, generated-program execution and process-death cleanup. The [final evaluator tests](../tests/test_optimization_final.py) cover private splits, independent scores, missing evidence and terminal retry behavior. The combined acceptance below connects the actual runtime components.

## Complete runtime search fixture

The [combined fixture](../examples/evaluation/strategy_search_fixture.py) connects the actual coding proposer, Docker strategies, normal Omnigent/MCP executor, independent development evaluator and private final controller. Its [authored response/source policies](../examples/evaluation/search_runtime_fixtures.py) supply synthetic model responses and use actual returned probe/registry receipts. They do not read independent evaluator predicates. The proposer obtains feedback paths from workspace tool results, reads a complete recorded raw response, writes two candidate programs, tests them in Docker and explicitly submits them.

Use the installed pinned image and separate Omnigent environment, and choose a new output directory:

~~~sh
uv run --extra mcp python examples/evaluation/strategy_search_fixture.py \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python \
  --image python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
  --out /tmp/research-combined-search-fixture
~~~

The fixture evaluates the baseline and six candidates over three iterations, then freezes development selection and permanently revokes the proposer. It creates the disjoint authored final package only after revocation. Final evaluation compares the selected candidates with the original baseline. The output retains complete controller and runtime artifacts, `runtime-progress.json`, and `acceptance.json` after successful completion. The synthetic policies use fixed token counts; the fixture cannot establish quality or efficiency improvements.

The September 9 [acceptance record](../examples/evaluation/evidence/combined-strategy-search-2026-09-09/acceptance.json) passed seven development and seven final evaluations. Three proposer attempts each made nine requests and ran a generated check in Docker. Each research execution made seven requests and ten strategy executions; interface validation added fourteen executions. Totals are 125 requests, 13,750 synthetic tokens and 157 actual Docker executions. Every development candidate tied at quality 1.0 and 770 synthetic tokens, so all seven candidates, including the baseline, remained on the development frontier. Repeated final/controller operations executed no additional research or proposer calls. A [fresh-process audit](../examples/evaluation/evidence/combined-strategy-search-2026-09-09/independent-audit.json) verified task bindings, sealed proposal inventories, raw proposer usage and strategy execution records. The agent completed source review; root completed the artifact audit after the agent's capacity interruption.

Keep `private-heldout-package/` and `private-final/` outside all future proposer inputs. An interrupted output directory remains evidence and cannot be reused by the example command; inspect its durable controller status and use explicit recovery for uncertain work. The private ten-case benchmark draft described in the shared tracker is separate from this one-case runtime acceptance package and still requires human review.

The combined-search and budgeted-search files named `independent-audit.json` are fresh-process checks performed within this project by the same author, reusing production verifiers. They are not external third-party reviews. Their historical filenames and archived bytes remain unchanged; fresh-process verification and separate source review do not replace independent human benchmark review or measured model evaluation.

## Run search with the shared model budget

[BudgetedSearchRun](../src/research_harness/optimization/runner.py) connects the existing search controller, coding proposer and real research executor to one retained ledger. Research and proposer requests use the same explicitly priced model snapshot and default service tier. The [proposed configuration](../examples/evaluation/search-run.proposed.json) carries the existing draft $10 ceiling and September 8 rate card; it grants no spending approval. Review the actual provider terms and configuration before recording the pending spending decision.

Preparation is offline and requires new search/feedback directories. Model-mode preparation requires reviewed development cases. The ledger must be outside those directories, and the same ledger and `.pilots.json` registry must be retained across comparison/search revisions:

~~~sh
uv run --extra mcp python -m research_harness.optimization.runner prepare \
  /path/to/reviewed-development/manifest.json \
  --baseline examples/strategies/research_strategy.json \
  --instructions agents/comparison/instructions.md \
  --config examples/evaluation/search-run.proposed.json \
  --ledger /path/to/shared-model-budget.json \
  --out /path/to/strategy-search \
  --feedback /path/to/development-feedback

uv run python -m research_harness.optimization.runner status /path/to/strategy-search
~~~

The registry retains both pilot and search preparations. Before further execution it checks all registered runs, their original budget witnesses, recorded gateway artifacts and sealed final evidence against the shared ledger. A new directory cannot replenish spent funds. Missing registered state or contradictory spending fails verification. These local witnesses detect loss or rollback against retained evidence; they are not an external accounting authority.

After provider access, the actual external spending authorization and the required review are recorded, `run` executes the measured baseline and the configured search. It retains each proposer attempt's budget witness and reconciliation outside its immutable output and outside proposer feedback. Unknown provider outcomes keep conservative holds. Exhausted or interrupted proposals require explicit recovery and never automatically replay. CLI execution reads `OPENAI_API_KEY`; fixture execution is available only through explicitly supplied offline HTTP handlers.

~~~sh
uv run --extra mcp python -m research_harness.optimization.runner run \
  /path/to/strategy-search \
  --omnigent-python /path/to/pinned-omnigent/.venv/bin/python

uv run --extra mcp python -m research_harness.optimization.runner final \
  /path/to/strategy-search \
  --heldout /path/to/private-reviewed-heldout/manifest.json \
  --private-out /path/to/private-final \
  --omnigent-python /path/to/pinned-omnigent/.venv/bin/python
~~~

The final command first requires frozen selection and permanent proposer revocation. It then reads the reviewed held-out package and evaluates the original baseline and selected candidates under the same shared budget. Completed retries return the recorded result without credentials or provider dispatch. Private packages, final artifacts and host budget records must never be added to future search feedback.

After establishing that an interrupted runtime has stopped, use `recover-proposal <search> --iteration <n> --reason '<observation>'`, `recover-case <search> --candidate <id> --case <id> --reason '<observation>'`, or `recover-final <search> --reason '<observation>'` with the same module. Recovery acquires the relevant execution lock and retains surviving evidence; it does not rerun the model or candidate. `reconcile <search>` settles only independently verified sealed archives, preserves unknown holds and writes a separate host receipt without altering sealed proposer/final artifacts. Missing or contradictory evidence stays unresolved.

The [budgeted runtime fixture](../examples/evaluation/budgeted_strategy_search_fixture.py) supplies authored responses while exercising these same APIs. Its provider handler checks that every request already has a matching durable dispatch reservation. It then checks independent settlement, private final isolation and completed retries. The [focused tests](../tests/test_optimization_runner.py) also cover ledger rollback across older pilots/searches, exhaustion, unknown responses, interruption and authorization gates. The accepted runtime run passed all of those checks. Its first attempt completed the search and final execution, then failed because the example verifier treated an event list as a dictionary; after correcting that check, the full example passed in a fresh fixture directory. Both runs made only synthetic requests, and the original diagnostic/source remain retained. These checks do not establish model improvement.

## Remaining work

Review the twenty development cases and separate ten-case private held-out draft. The draft passed twenty offline service/evaluator checks and remains authored. Run live compatibility and a measured baseline only after provider access and the pending spending decision are recorded. Then run model search, freeze selection, revoke proposer access and run the isolated final evaluation. Budgeted runtime acceptance establishes software behavior and provides no measured optimization result.

The [shared goal](goals/omnigent-integration.md) retains the full completion criteria and current evidence.
