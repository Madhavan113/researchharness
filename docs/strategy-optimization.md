# Strategy optimization: implementation status

The strategy foundation and shared observation projection are implemented; model-backed search and held-out evaluation are still pending. The accepted goal includes those runs. The current components are an isolated Python executor, durable strategy sessions shared by direct discovery and Omnigent/MCP, and an immutable development archive with selection/finalization rules. Context selection and the end-to-end optimization controller remain unfinished.

The [September 9 checkpoint validation](../examples/evaluation/evidence/checkpoint-2026-09-09/verification.json) retains source hashes and the [787-test report](../examples/evaluation/evidence/checkpoint-2026-09-09/pytest.xml). All tests passed with zero skips, including the actual Omnigent runtime and Docker checks. The earlier [foundation verification](../examples/evaluation/evidence/strategy-foundation-2026-09-08/verification.json) remains a separate immutable checkpoint. These are software/fixture checks, not a measurement of research quality.

Meta-Harness lets a coding proposer inspect prior candidate code, scores and full available execution traces through a filesystem, then propose executable changes. Our planned search keeps that feedback channel and selects only on development results. Test feedback stays outside search. [Paper, section 3](https://arxiv.org/html/2603.28052v1).

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

The native LLM_REQUEST policy received only prompt/user previews and ran once across three provider calls. Its replacement data was ignored. It does not establish a full-context rewrite or a per-round stopping hook. Observation projection also cannot remove older observations already retained in SDK history. Stop advice is advisory.

~~~sh
uv run --extra mcp python examples/evaluation/native_strategy_hook_fixture.py \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python \
  --out /tmp/research-native-hook-fixture
~~~

The shared integration below now applies validated structured-observation projection to direct and MCP results before their differing serializations. Full conversation selection still requires a separate host gateway projection that preserves complete function-call/result groups and immutable model/tool/budget controls.

## Shared observation strategies

[StrategyBundle](../src/research_harness/strategies/config.py) validates a bounded manifest, source digest and sandbox limits, then freezes exact source bytes without importing them. [StrategySession](../src/research_harness/strategies/session.py) wraps completed search, inspection and probe operations in the direct dispatcher and MCP adapter. The host assigns stable references to supplied results, executes the candidate with explicit state, and validates its decision before serializing the model observation. Candidate code has no service or provider access. Exact-operation retries reuse the recorded projection; changed inputs or candidate identity are rejected.

The [observation contract](../src/research_harness/strategies/projection.py) enforces these rules:

- Ranking and selection may refer only to result identities supplied in that event. Duplicate or invented references fail validation. The original receipt, operation status, errors, evidence references and remaining budgets remain authoritative.
- Rendered text is candidate-generated interpretation, with bounded size and explicit references to its source results. It cannot create observed URLs, passing probes, saved proposal ids or collection outcomes. The existing service still validates proposals against its original receipts.
- Stop recommendations remain advice until the controller has a defined terminal outcome. A stopped attempt without a saved valid proposal must remain incomplete in independent evaluation.

Each event retains the candidate hash, exact input, isolated execution artifacts, validated decision and state transition. File hashes and state chains are checked on reuse. Invalid decisions and exhausted strategy budgets fail the session and prevent further model work or proposal submission. A durable pending event survives interruption; explicit recovery validates completed output or records failure and cleans up the recorded container, without rerunning candidate code. Stored research receipts remain authoritative.

The [authored observation example](../examples/strategies/research_strategy.py) and [manifest](../examples/strategies/research_strategy.json) enable observations and leave context selection disabled. Supply `--strategy examples/strategies/research_strategy.json` to `rh discover` with `--search-provider mcp`, to `rh mcp serve`, or to the Omnigent `prepare` command. Prepared cases freeze code outside the agent/plugin directory and persist its identity across restarts. Direct and standalone MCP output directories contain a `strategy/` session; prepared Omnigent cases use the case's `strategy/` directory by default.

~~~sh
uv run rh strategy status --out /path/to/case/strategy
uv run rh strategy recover --out /path/to/case/strategy

uv run --extra mcp python examples/evaluation/strategy_observation_fixture.py \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python \
  --out /tmp/research-strategy-observation-fixture
~~~

The [acceptance record](../examples/evaluation/evidence/strategy-observation-2026-09-09/acceptance.json) verifies matching direct/MCP projection through the actual normal Omnigent server and runner, two actual strategy containers, four synthetic model requests, unchanged receipts and exact retries with one execution per path. A host-import guard remained untouched. The [120-file archive](../examples/evaluation/evidence/strategy-observation-2026-09-09/index.json) retains the exact fixture and available execution evidence. This acceptance covers observation projection; it does not measure proposal quality or optimization.

Context selection remains the next boundary: candidates should return identities of host-built conversation groups, with current instructions, user tasks, complete function-call/result relationships and provider-required reasoning items preserved. The controlled gateway must archive original and projected requests while retaining fixed model, tool and spending controls. That gateway integration and independent verification are not implemented in this checkpoint; keep `context` disabled.

## Development archive and final evaluation

[OptimizationArchive](../src/research_harness/optimization/archive.py) keeps frozen backend/evaluator/benchmark inputs and its operation journal in host-only state. A separate feedback directory contains candidate bundles, complete dedicated development artifacts and independent per-case evidence. Source trees and artifact trees are copied and hashed; symlinks, hardlinks, path escapes, extra files and changed bytes are rejected. Size limits fail an archive operation rather than silently summarize away artifacts.

A trusted evaluator callback receives the frozen artifact copy and returns evidence bound to the candidate, artifact inventory, controls, evaluator and development benchmark. Every case and failure must be represented. A callback identity is not authenticated by a supplied fingerprint: the future controller must instantiate the actual independent evaluator. Candidate-provided quality claims are not an evaluator.

Model-mode admission requires reviewed development cases, explicit model/provider/budget/sandbox controls and a verified measured baseline before further candidates. Fixture mode tests this lifecycle without making model-quality claims. Selection computes the quality/token Pareto frontier from development evidence; unknown objectives and unverified model executions stay excluded. The selection intent closes search and freezes the frontier in the same journal write. Retrying that operation only completes the recorded decision.

Publishing candidate feedback first records an intent binding the exact host inventory and evaluator outcome. Files are written in the recorded `publication_staging_dir` outside the feedback tree, then atomically moved into place; a partial write never becomes proposer feedback. `recover_publication(operation_id=...)` completes the authorized file copy and journal commit without invoking the evaluator again. Pending publication temporarily prevents exposing the feedback path; unknown or changed published files remain integrity failures. Interrupted evaluator work has a separate `recover_development` path that preserves available files and records missing evidence.

Beginning a final attempt records its binding and phase before any private output work. Failed or interrupted preparation can be sealed with explicit missing-directory/selection evidence; a completed final status requires those preparation files. A durable seal intent lets a retry finish unchanged metadata writes. Final artifacts stay in the separate private directory; no operation reopens search or replays evaluation automatically.

The host must enforce the directory boundary when launching the proposer and terminate/revoke that proposer before final evaluation. The archive API alone does not isolate an already running process. The held-out dataset and final evaluator are still to be connected to this lifecycle.

## Remaining work

Implement protocol-safe context selection, then bind strategy hashes and execution limits into controlled evaluation. Add the bounded coding proposer and the planned three iterations with two candidates each, preserving all available development artifacts. Prepare the reviewed development and isolated held-out packages. Run the real baseline and search only after provider access and the pending spending decision are recorded; then freeze selection and run the isolated final evaluation.

The [shared goal](goals/omnigent-integration.md) retains the full completion criteria and current evidence.
