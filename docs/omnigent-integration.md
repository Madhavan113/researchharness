# Local Omnigent research harness

The authored [research agent](../agents/research/config.yaml) runs through Omnigent's normal server, runner, policy engine, and OpenAI Agents adapter. Research Harness exposes its evidence, proposal, collection, and export services through stdio MCP. The integration controller uses subprocesses and ordinary HTTP APIs; it does not inject an executor callback.

The September 8 acceptance run used a local synthetic model endpoint and a real local HTTP feed. It saved a validated pipeline, collected and exported one observation with detached workers, read its provenance, restarted the server and runner, and recovered the same case and jobs. This establishes integration behavior. It does not measure live-model research quality. The earlier [compatibility report](omnigent-compatibility.md) records adapter contracts and limitations.

## Setup and launch

Run these commands from a Research Harness source checkout. Keep that checkout and its environment at stable paths: generated MCP configurations contain absolute executable and manifest paths.

```sh
uv sync --extra mcp
sh examples/omnigent/setup.sh /tmp/researchharness-omnigent-be042b39
```

Omnigent is pinned to `be042b390e293a8d586cbb7e403a2ce0ce38fc62` in its own environment, using its frozen lock, Python 3.13, and `uv==0.11.8`. Startup verifies the commit and rejects tracked source modifications. Research Harness remains in its own environment.

Write the brief to a file, then prepare a fresh case. Preparation creates files and makes no model request:

```sh
uv run --extra mcp python -m research_harness.integrations.omnigent prepare \
  --out ./runs/policy-case \
  --brief ./brief.md \
  --model gpt-5.4-mini \
  --max-spend-usd 1
```

Before a live run, record the provider configuration and run budget in the [shared tracker](goals/omnigent-integration.md). The chosen model must be available to that provider. The adapter reads `OPENAI_API_KEY` and optionally `OPENAI_BASE_URL` from its process environment. Credentials are not written into the authored bundle. This project has not yet established live access to the selected model.

```sh
uv run --extra mcp python -m research_harness.integrations.omnigent serve \
  --case ./runs/policy-case/case.json \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python
```

The command prints a loopback URL and session id. Open that URL, select the case, and send the brief. The foreground process keeps the normal Omnigent server, dedicated runner, and event recorder available. `--message 'your brief'` sends an ordinary user turn through the controller. Ctrl-C exports the available trace and stops the owned server and runner processes. Start the same command to reopen the same case. A port occupied by another process causes startup to fail for inspection.

The generated bundle declares 32 executor iterations, but this pinned normal runner does not enforce that value as a provider-request limit; measured comparisons use the host gateway below. The bundle also declares Omnigent's cost policy with `expensive_models: []`, which blocks all models once the observed cost limit is reached. In the [normal-runner spending fixture](../examples/omnigent/evidence/budget-runtime-2026-09-08/acceptance.json), a $0.000000001 cap allowed the first seven-response turn to finish; the catalog priced that turn at $0.00084. The next ordinary request was denied and the provider request count stayed at seven. This verifies blocking between turns, including the overshoot of an in-flight turn. Missing auxiliary usage or pricing still limits accounting. This setting is not a provider-enforced spending ceiling. See the pinned [cost-policy implementation](https://github.com/omnigent-ai/omnigent/blob/be042b390e293a8d586cbb7e403a2ce0ce38fc62/omnigent/policies/builtins/cost.py).

## Cases, tools, and durable work

Each prepared case has its own bundle, backend root, server database, runner, research directory, and host-issued session binding. The MCP process requires the normal runner's `OMNIGENT_RUNNER_PRIMARY_SESSION_ID` and `RUNNER_SERVER_URL`. The manifest binds once to both values and rejects reuse from another conversation. Prepare a new case for a different brief; resume the existing case for follow-ups. A runtime lock prevents two controllers from starting the same case concurrently.

The MCP command and arguments are literal absolute paths; only supported MCP environment entries are expanded by Omnigent. The generated command calls `research_harness.integrations.omnigent mcp --case …`. This launcher restores the saved discovery and records the model, adapter, source pin, instruction hash, complete authored-bundle hash, config hash, case id, server URL, and session id in the domain context. Startup and MCP resume reject a bundle that differs from its frozen manifest. A trusted preparation hook can refresh a bundle only before its first session binding; it is not an agent tool. These hashes cover authored files, not the entire Python environment. The launcher never derives the research identity from a model-supplied argument. Shared backend settings supplied through `RH_*` still apply; the case has its own local artifact root.

The agent uses these domain tools:

| Workflow | Tools |
| --- | --- |
| Context and discovery | `get_research_context`, `begin_research`, `search_sources`, `inspect_source`, `probe_source`, `get_evidence` |
| Proposal | `submit_proposal`, `list_pipelines` |
| Durable jobs | `start_collection`, `get_job`, `list_jobs`, `cancel_job` |
| Exports | `export_observations`, `read_export` |

Omnigent prefixes these names with `research__`. Tool results include JSON text because this pinned runner drops MCP `structuredContent` when no text content accompanies it. Its normal bridge can render a parsed result as a Python representation in the next model request; original JSON text remains in runtime events. The fixture verifies both paths with actual returned ids.

A useful follow-up is: “Collect the saved pipeline, export observations available as of the current time, and show the export's provenance.” Workers persist independently of the chat process. The agent polls the returned job id and reads saved observations and their manifest. After reopening, it reads context, pipelines, and jobs from the backend. Stopping a chat is not proof that a collection was cancelled; cancellation uses the job tool and retains already published snapshots. See the [service guide](research-service.md) for evidence and retry semantics.

## Traces and usage

The case's `omnigent/` directory contains the live `events.jsonl`, an exported `session.json`, runtime metadata, and a manifest with artifact hashes. The event recorder also captures UI-originated turns. Each export freezes event bytes under `events/<sha256>.jsonl`, so later heartbeats cannot invalidate an exported usage reference. Hidden model reasoning is unavailable and is not reconstructed.

Host-owned usage exports use schema version 1:

```json
{
  "schema_version": 1,
  "question_id": "saved-question-id",
  "discovery_id": "saved-discovery-id",
  "phase": "discovery",
  "source_event_file": "events/<sha256>.jsonl",
  "source_event_sha256": "<sha256>",
  "responses": {
    "host-response-id": {
      "model": "gpt-5.4-mini",
      "input_tokens": 700,
      "output_tokens": 70,
      "status": "completed"
    }
  },
  "totals": {"input_tokens": 700, "output_tokens": 70, "total_tokens": 770},
  "complete": false,
  "auxiliary_usage_unknown": true,
  "interrupted": false
}
```

The source path is relative to the usage file. Response ids identify Omnigent's aggregate turns, not individual provider calls. Counts are deduplicated from terminal events; the model comes from `response.usage.model`. Missing counters remain null. At the tested pin, auxiliary compaction usage is unavailable, so `complete` remains false and totals are lower bounds. Cached-token detail also has an upstream conversion limitation; exact costs remain unknown.

`runtime-usage.json` has phase `workflow` and covers every observed turn. Only a trusted controller can mark a turn with `LocalOmnigent.send(..., phase="discovery")` or `phase="followup"`; these produce `runtime-usage.discovery.json` and `runtime-usage.followup.json`. Ordinary UI turns and the CLI's `--message` are workflow turns. Phase assignments are stored in `response-phases.json` and stamped into immutable event wrappers as `{event, phase, data}` before writing; model or MCP arguments cannot choose an evaluation phase. The evaluator accepts an explicit discovery usage path, checks its source hash, source phase and counters, excludes follow-ups, and preserves unknown complete usage. Untagged historical event files cannot establish a verified discovery total. Proposal usage is not rewritten or resubmitted to carry host measurements.

## Reproduce the fixture

```sh
uv run --extra mcp python examples/omnigent/normal_runtime_fixture.py \
  --out /tmp/researchharness-normal-fixture \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python

RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
  uv run --extra mcp pytest tests/test_omnigent_integration.py -q

uv run --extra mcp python examples/omnigent/normal_runtime_fixture.py \
  --out /tmp/researchharness-budget-fixture \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python \
  --budget-check
```

Use a new output directory. The script overrides model access with a dummy key and loopback OpenAI-compatible SSE endpoint, clears inherited `RH_*` backend settings, and substitutes a local search fixture. The fixture-only MCP launcher explicitly allows the loopback source; detached workers perform real HTTP requests. Production launchers retain public-source restrictions.

The checked-in [acceptance record](../examples/omnigent/evidence/normal-runtime-2026-09-08/acceptance.json) and [artifact index](../examples/omnigent/evidence/normal-runtime-2026-09-08/artifact-index.json) describe the September 8 run. It used 19 synthetic model responses, three source HTTP requests, one published observation, and two recovered jobs. Its discovery turn recorded 770 tokens; these values describe fixture plumbing, not a research benchmark. The evaluator verified the bound discovery export and retained a 770-token lower bound with unknown total usage.

For a browser walkthrough, add `--interactive`. The script writes `runtime-ready.json` with the URL and session id, then serves the same fixture until an output-directory `stop` file appears. Set `control.json` to `{"mode":"collection"}` before the collection message and `{"mode":"reopen"}` before a later reopen message. Creating `restart` exports the trace and restarts the actual server and runner; `runtime-ready.json` increments its generation. Creating `export` snapshots the current trace. These are fixture-only host controls. UI turns remain workflow usage.

The [browser acceptance report](omnigent-ui-acceptance.md) separately verifies discovery, collection, export, and reopening through the actual interface. Live model access, exact provider billing, nested-schema acceptance by a live provider, and model-quality comparisons remain unverified.

## Controlled comparison adapter

`prepare_case(..., settings=DiscoverySettings(...), instructions=shared_text)` accepts the same explicit settings and semantic instructions as the direct path. Instruction bytes are preserved exactly. Settings are saved in the manifest and frozen `agent/research-settings.json`; runtime context also records the requested model settings and research budgets. MCP receives the explicit search/inspection/probe limits and begin-relative research deadline. Omitting these arguments preserves the earlier pilot defaults.

Requested settings alone are insufficient at the tested pin. Actual normal-runner HTTP fixtures established these differences:

| Control | Unchanged normal runner | Controlled gateway |
| --- | --- | --- |
| Reasoning effort `none` | Forwarded | Forwarded explicitly |
| Output-token limit 6000 | Omitted from provider requests | Set to 6000 |
| Two provider requests | Seven discovery requests completed despite authored limits | Third upstream request denied; partial case retained |
| Parallel tool calls | Field omitted | Field omitted for both comparison paths |
| Tool surface | Research tools plus Omnigent builtins | Caller-supplied function allowlist |
| Auxiliary compaction | Requested `gpt-4.1` | Rejected locally with no upstream call |

The host [Responses gateway](../src/research_harness/integrations/model_gateway.py) therefore forms an explicit comparison adapter. Both runtimes must use it for the controlled comparison; its results describe that configuration. It checks the exact model, fixes generation controls, strips function schemas outside the caller's allowlist, rejects provider-native tools, and rejects auxiliary endpoints such as `/responses/compact`. Undeclared generation fields, server-side compaction, inherited provider prompts/context, and background execution are rejected before forwarding. It preserves incoming and forwarded JSON separately, records their hashes and changes, and relays provider JSON/SSE. Request headers and provider keys are not archived. The runner receives a new gateway-local key; the real provider client and key stay with the host. Restricting forwarded function schemas does not provide an OS sandbox or a complete runtime authorization boundary; candidate isolation remains separate M6 work.

Each admitted forwarding attempt reserves a round, including failed attempts. Admission and the round counter are atomic. The report separates `reserved_attempts` from `upstream_requests`: an interruption during local request construction consumes a reservation but starts no provider call. The monotonic deadline begins with the first accepted provider request or an explicit `begin()`, so server startup does not consume differing runtime latency. A watchdog closes the downstream connection at the absolute deadline even while waiting for headers or another stream chunk. Shutdown records interrupted attempts, seals every artifact writer, and closes downstream connections before returning. Late upstream completion cannot change the frozen report; this does not promise cancellation of work already accepted by a provider. Captured provider usage includes cached-token details; only terminal responses with consistent counters establish complete accounting. Missing usage or interrupted streams leave a lower bound. `report().complete` concerns requests forwarded through this gateway and does not establish an exact dollar bill. Bind a gateway report to the controller's case and phase before using it for evaluation.

Raw compressed streams retain their bytes and encoding headers. A supplied test client that has already decoded an encoded response is rejected because its original wire bytes are no longer available.

```python
with ResponsesGateway(
    output / "gateway",
    model=model,
    settings=settings,
    upstream_base_url=provider_url,
    client=host_provider_client,
    allowed_function_names=allowed_names,
) as gateway:
    # Both controlled runtimes use these local credentials.
    runtime_env = {
        "OPENAI_BASE_URL": gateway.base_url,
        "OPENAI_API_KEY": gateway.api_key,
    }
```

The normal-runtime fixture can exercise this adapter without paid calls:

```sh
uv run --extra mcp python examples/omnigent/normal_runtime_fixture.py \
  --out /tmp/researchharness-controlled-gateway \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python \
  --controlled-gateway

uv run --extra mcp python examples/omnigent/normal_runtime_fixture.py \
  --out /tmp/researchharness-controlled-limit \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python \
  --gateway-limit-check
```

The first check saves a pipeline and completes a follow-up after the unchanged runner's auxiliary compaction request is rejected. The second expects a failed partial session after exactly two upstream requests; SDK retries receive local denials. `--shared-settings` separately records the unchanged runner's requested-versus-observed controls, and `--settings path.json` supplies a specific `DiscoverySettings` object. Matching semantic instructions and explicit controls does not make full requests identical: prompt assembly, schema names, proposal delivery, history, streaming, and the disclosed compaction policy remain runtime differences.
