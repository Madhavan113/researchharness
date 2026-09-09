# Controlled runtime comparison

The M5 controller freezes a development benchmark and runs each case once per runtime, with isolated backend state. It preserves completed, failed, and interrupted executions and independently verifies gateway token evidence. The current acceptance uses deterministic model responses through the actual direct SDK and normal Omnigent server/runner. The [budgeted pilot](pilot-budget.md) connects a shared durable ledger to provider dispatch and independent settlement. Live model comparison, spending authorization and case review remain open.

## Controls and runtime differences

[DiscoverySettings](../src/research_harness/execution.py) supplies shared operation limits, request limits, output tokens, and reasoning effort. The [shared instructions](../agents/comparison/instructions.md) contain the same semantic policy in both arms. The direct CLI also accepts --settings JSON_FILE and --instructions MARKDOWN_FILE; existing direct defaults remain available when these are omitted.

The pinned Omnigent normal runner forwards reasoning effort, but drops authored output-token and iteration limits. This was verified against actual fixture HTTP requests. Its normal tools also include host functions beyond the research domain. The [Responses gateway](../src/research_harness/integrations/model_gateway.py) is an explicit host adapter used for both comparison arms:

- It applies the same model, output-token limit and reasoning effort; parallel_tool_calls is omitted in both forwarded requests, leaving that behavior to the provider.
- It limits every forwarded HTTP attempt, including failed attempts and retries. A monotonic deadline starts on the first accepted request, or an explicit host begin call. Source-operation limits and their begin-relative deadline remain enforced by ResearchService.
- It exposes only each runtime's discovery functions. Native provider tools are rejected, and unsupported auxiliary endpoints such as /responses/compact are rejected without an upstream request.
- It records incoming and forwarded request bodies separately, raw JSON/SSE responses, hashes, control changes, denials, and observed usage. The runner receives a temporary local key; the upstream key stays in the host process.
- It records an immutable host execution/case/runtime/phase/task binding before requests and publishes archive.json only after successful shutdown seals all writers. The evaluator independently checks the raw provider evidence against that binding, generation settings and all frozen budgets.

The controlled Omnigent bundle also enforces its discovery allowlist at tool dispatch; stripping request advertisement alone does not prevent the SDK from calling registered functions. Normal workflow bundles keep their collection/export tools. The controlled runtime names identify this adapter. Prompt assembly, function schemas, proposal delivery, streaming and error handling still differ. A common instruction hash does not mean the full assembled model prompt is identical. Limits bound requests; they are not an exact provider billing cap. A verified gateway archive can establish complete provider tokens while Omnigent's narrower turn export remains a lower bound. The report retains both evidence scopes. An invalid selected gateway archive keeps the total unknown; it cannot fall back to a smaller proposal or runtime counter.

## Prepare and inspect a run

Preparation makes no model or source requests:

~~~sh
uv run --extra mcp python -m research_harness.evaluation.controller prepare \
  examples/evaluation/development/manifest.json \
  --instructions agents/comparison/instructions.md \
  --out artifacts/comparison-prepared
~~~

An optional --config JSON_FILE accepts ComparisonConfig, including execution, model, and DiscoverySettings. The default is fixture execution. Source access uses the benchmark's authored search results and exact HTTP response bytes, with no external-source fallback. Collection/export tools are unavailable in this discovery comparison.

The prepared directory contains the benchmark and fixture copies, instructions, implementation and dependency snapshots, comparison.json, and one journal per arm. Each arm includes every case, including failures. Source, template, fixture or instruction changes require a newly prepared run. The public task copy contains only id and brief; evaluation predicates are not passed to the agent. This trusted controller directory is not an M6 candidate sandbox.

The [RuntimeExecutor](../src/research_harness/evaluation/runtime_executor.py) uses the supplied loopback model gateway and shared source recordings. It starts the actual Discovery or LocalOmnigent path, sends one discovery task, preserves partial evidence and runtime exports on failure, and closes owned processes. Call it through run_case; the controller does not silently create paid-provider access or authorize spending. The pending provider/budget decision must be resolved before paid execution.

## Offline acceptance

Use the separate pinned Omnigent environment from the [integration guide](omnigent-integration.md):

~~~sh
uv run --extra mcp python examples/evaluation/controlled_runtime_fixture.py \
  --omnigent-python /path/to/omnigent/.venv/bin/python \
  --out /tmp/research-controlled-fixture
~~~

This command creates one authored acceptance case, runs both actual runtimes through local gateways, and evaluates their saved artifacts independently. All model and source responses are fixtures. Complete available research artifacts, runtime events, request/response archives, journals, and report.json remain under the output directory. acceptance.json records the controls, outcomes and limitations. The fixture does not measure model quality or live provider cost.

The [September 8 provider-usage acceptance](../examples/evaluation/evidence/controlled-provider-usage-2026-09-08/acceptance.json) contains one independently valid proposal per arm. The gateways dispatched four direct and seven Omnigent requests; eight auxiliary compaction attempts were rejected locally. [The report](../examples/evaluation/evidence/controlled-provider-usage-2026-09-08/report.json) verifies complete totals of 440 direct and 770 Omnigent synthetic tokens from the raw gateway archives. Workflow checks remain unmeasured and dollar totals unknown. Those fixture counters are not model-efficiency results.

The [artifact archive](../examples/evaluation/evidence/controlled-provider-usage-2026-09-08/artifacts.tar.gz) preserves 163 selected files: frozen inputs and implementation, the authored agent bundle, fixture programs, research evidence, available runtime exports and sealed gateway archives. [The index](../examples/evaluation/evidence/controlled-provider-usage-2026-09-08/index.json) records every file hash and the archive hash. The extracted package was independently re-evaluated with the same valid artifacts and token totals. Runtime credentials and operational databases are excluded; this is an evaluation artifact package, not a restorable Omnigent server backup. The [earlier runtime-only report](../examples/evaluation/evidence/controlled-runtime-2026-09-08/report.json) remains unchanged as historical evidence of the narrower accounting scope.


## Budgeted runtime acceptance

~~~sh
uv run --extra mcp python examples/evaluation/budgeted_runtime_fixture.py \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python \
  --out /tmp/research-budgeted-fixture
~~~

The [September 8 budgeted acceptance](../examples/evaluation/evidence/budgeted-runtime-2026-09-08/acceptance.json) saved valid proposals through both actual runtimes. Every one of the four direct and seven Omnigent HTTP fixture requests had a durable reservation and dispatch marker first. Independent raw-response verification established 440 and 770 synthetic tokens; eleven operations settled at the supplied rates, with zero remaining holds. The eight auxiliary compaction attempts were rejected locally. The 1,320,000 nanodollars in the [pilot report](../examples/evaluation/evidence/budgeted-runtime-2026-09-08/report.json) are synthetic rate accounting, not a model-efficiency result or real spending.

The [186-file archive and hash index](../examples/evaluation/evidence/budgeted-runtime-2026-09-08/index.json) preserve the frozen implementation/inputs, three fixture programs, authored policy, research/runtime exports, raw provider archives, ledger, pilot registry and checkpoints. Extraction and independent re-evaluation reproduced both valid proposals, the token totals and all eleven ledger operations. Runtime credentials and operational databases are excluded. Absolute runtime/ledger paths are provenance; portable verification explicitly selects the extracted ledger and relative run artifacts. The archive is an evaluation package, not a restorable server backup.

A separate normal-runner regression returned calls to registered but unadvertised sys_session_rename and research__list_jobs. The policy denied both, the session title remained unchanged, and allowed begin/context calls succeeded. Reproduce it with `RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python uv run --extra mcp pytest tests/test_omnigent_integration.py::test_normal_runner_denies_unadvertised_registered_tools_before_execution`. This uses fixture responses and no paid provider.

## Recovery and scoring

A case is durably reserved before execution starts. Repeating a completed or failed case returns its existing journal entry. Running or interrupted cases require inspection; the controller never automatically replays a possibly billed model turn. After confirming that the owned process has stopped, record an abandoned case without another model call:

~~~sh
uv run python -m research_harness.evaluation.controller record-interruption \
  artifacts/comparison-prepared direct CASE_ID \
  --reason 'Inspected stopped process and incomplete provider trace'
~~~

Failed cases retain available partial artifacts and unknown usage rather than inventing zero cost.

If the stopped execution left a sealed gateway archive, add --gateway-usage /absolute/path/to/that/case/gateway/archive.json to record-interruption. The controller freezes this explicit recovery evidence without replaying the model turn; the evaluator still requires its original execution binding. A task failure or missing proposal does not discard valid provider accounting. An incomplete dispatched response leaves the total unknown and retains the independently observed subtotal.

Finalization requires every case to be terminal and revalidates the full artifact inventory, file hashes and case bindings:

~~~sh
uv run python -m research_harness.evaluation.controller finalize \
  artifacts/comparison-prepared
~~~

The report separates discovery correctness/source usefulness from workflow checks. Missing workflow reports stay missing in the full case denominator. [Independent evaluation](research-evaluation.md) describes the scoring predicates, usage checks and qualitative-review limits. A fixture report does not satisfy M5's reviewed live-model baseline requirement or start M6 optimization.
