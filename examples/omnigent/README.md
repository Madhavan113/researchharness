# Omnigent integration examples

The [integration guide](../../docs/omnigent-integration.md) describes the authored research bundle and normal server/runner launch. Its process fixture saves a validated pipeline, runs real collection/export workers, reads the result, and reopens the same case and jobs:

~~~sh
uv run --extra mcp python examples/omnigent/normal_runtime_fixture.py --out /tmp/researchharness-normal-fixture --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python
~~~

This uses a local model SSE fixture and local source HTTP server. Add `--interactive` for a browser walkthrough using the documented host control files. Model quality, live pricing, and live provider compatibility remain separate checks. [Saved normal-runtime evidence](evidence/normal-runtime-2026-09-08/acceptance.json) distinguishes these boundaries.

For controlled comparisons, `--controlled-gateway` verifies exact forwarded generation controls and a function allowlist through the normal runner. `--gateway-limit-check` verifies that a third provider request is denied after a two-request allowance. These use the explicit host adapter documented in the integration guide; the unchanged native runner does not enforce authored output-token or provider-round limits reliably. `--shared-settings` records those native differences without the adapter.

## Earlier adapter compatibility fixture

The milestone 0 compatibility fixture proves the pinned runtime can carry research-style tool schemas, evidence envelopes, and event streams. It exercises the adapter directly; the normal runtime example above uses the production server and runner path.

Run commands from the Research Harness checkout. Keep Omnigent in its own environment:

~~~sh
sh examples/omnigent/setup.sh /tmp/researchharness-omnigent-be042b39
/tmp/researchharness-omnigent-be042b39/.venv/bin/python examples/omnigent/compatibility.py --out /tmp/researchharness-m0-new-run
~~~

Use a new output directory each time. The setup script installs Omnigent commit `be042b390e293a8d586cbb7e403a2ce0ce38fc62` with its frozen upstream lock and Python 3.13. It uses `uv==0.11.8` through `uvx`; it does not replace the system `uv` or change Research Harness dependencies.

The compatibility script loads the [authored agent](compatibility-agent/config.yaml), writes its MCP configuration with absolute executable paths, starts the real MCP subprocess through Omnigent's runner manager, and runs the real OpenAI Agents SDK executor. Only the model HTTP transport is replaced with deterministic local responses. It never uses a provider key or contacts a model endpoint.

Ten assertions cover JSON transport, malformed input, instruction delivery, tool arguments and results, usage aggregation, updated instructions, warm history, fresh-executor replay, and cancellation. Two assertions explicitly document limitations at this pin: structured-only MCP content is lost, and cached input token detail is lost in the adapter's usage conversion. Passing means the observed contract matches the recorded pin; it does not mean these upstream limitations are fixed.

The artifact directory contains tool schemas, raw model request/response fixtures, Omnigent events, actual subprocess call receipts, checks, version metadata, source hashes, and the generated runnable agent bundle. [Saved evidence](evidence/2026-09-08/metadata.json) and the [compatibility report](../../docs/omnigent-compatibility.md) explain the September 8 run.

The normal-runtime archive retains three empty captured streams: `omnigent/runner.log` and the collection/export jobs' `worker.log` files. The budget-runtime archive retains one empty `omnigent/runner.log`. Their zero-byte contents match the original artifact indexes; they mean those streams emitted no output, not that the operations succeeded. The [normal acceptance record](evidence/normal-runtime-2026-09-08/acceptance.json) records collection/export and reopening, while the [budget acceptance record](evidence/budget-runtime-2026-09-08/acceptance.json) records denial of the next request. The original logs and hashes are preserved.

## Offline provider schema preflight

Check the direct Responses request, structured proposal output, and all fourteen MCP input schemas without a provider key or network request:

~~~sh
uv run --locked --extra mcp python examples/omnigent/provider_schema_preflight.py \
  --out /tmp/researchharness-provider-schema-new-run \
  --omnigent-python /tmp/researchharness-omnigent-be042b39/.venv/bin/python
~~~

Use a new output directory. The optional `--omnigent-python` argument uses the separate pinned environment installed above to check all fourteen tools with the actual Agents SDK's strict converter. Without it, the direct SDK and in-memory MCP checks still run. The command does not launch Omnigent or contact a model endpoint.

The script captures `openai-sdk-request.json`, `wire-response.json`, `parsed-proposal.json`, `mcp-tools.json`, optional `agents-sdk-tools.json`, and a hashed `report.json`. It checks closed objects, required properties, absence of schema default annotations, valid nullable inputs and restoration of host defaults by the real SDK parser. The authored proposal is schema evidence only, with no verified source or saved research pipeline. See the [service contract](../../docs/research-service.md#provider-schemas-and-host-defaults).

These checks establish serialization and conversion behavior, not live API acceptance. The separate converter check does not establish which strict flag the native Omnigent runner selects; the required runtime tests independently inspect the actual emitted schemas through that runner. Model quality and live compatibility remain outstanding.

## Search provider preflight

This separate command makes real network requests to Keenable's public keyless MCP endpoint. It records the negotiated server/protocol and tool schemas; it does not query search by default or test OpenAI schema acceptance:

~~~sh
/tmp/researchharness-omnigent-be042b39/.venv/bin/python examples/omnigent/search_preflight.py --out /tmp/researchharness-search-schema.json
~~~

Add `--query 'a public research query'` to record one real search with at most two results. The script preserves the whole returned MCP payload. Provider search results are discovery evidence, not proof that a connector works or that a page was independently fetched.

The observed endpoint returns search results as a single text content block containing `Title:`, `URL:`, `Acquired:`, and `Snippets:` fields. It supplies no output schema. The service adapter must extract result URLs conservatively from that stored response and preserve the original payload.

## Remaining live checks

The model setting is explicitly `gpt-5.4-mini` with `openai-agents`; provider access to that model has not been verified. Establish the provider configuration and spend budget in the shared tracker before a model-backed run. Once configured, the generated bundle can be used with the actual CLI:

~~~sh
/tmp/researchharness-omnigent-be042b39/.venv/bin/python -m omnigent run /tmp/researchharness-m0-new-run/agent --harness openai-agents --model gpt-5.4-mini --debug-events
~~~

This command is documented from the installed CLI help, not executed in the earlier adapter check. The generated bundle's source paths must remain available. The newer normal-runtime fixture verifies server startup, a saved proposal, durable collection/export, and restart. A real model call, interactive browser behavior, and live cost-policy enforcement require their own evidence; consult the integration guide and shared tracker for current status.
