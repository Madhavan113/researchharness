# Durable research service and MCP tools

The direct discovery CLI and the MCP server use the same research service for source inspection, probes, evidence lookup, and proposal submission. Discovery state lives in the configured registry, so restarting a process does not discard its proof records or operation limits.

The Omnigent bundle now drives discovery, collection, export, and reopening through its normal server and runner. Recorded acceptance, including the [browser walkthrough](omnigent-ui-acceptance.md), uses synthetic model responses and a local HTTP feed. Live model quality is tracked separately in the [shared goal](goals/omnigent-integration.md). See the [runtime compatibility report](omnigent-compatibility.md) for the pinned runtime contracts.

## Run the local MCP server

Install the optional MCP dependencies and give each research case its own output directory:

~~~sh
uv sync --extra mcp
uv run --extra mcp rh mcp serve \
  --out artifacts/omnigent/policy-case \
  --model gpt-5.4-mini \
  --session-id local-policy-case
~~~

This command serves MCP over stdio; an MCP client supplies the tool requests. Stdout contains protocol messages. It does not start a model or an Omnigent UI. Configure the runtime to launch it using absolute executable and output paths.

The first begin_research call creates the question, discovery, and context.json. Launching the same command again resumes that context. Alternatively, pass --discovery with the service-issued id. The stored model and runtime binding must match explicitly supplied settings on reconnect. Use a new directory for a new discovery; a different brief cannot silently replace the old question.

Omitting `--model` uses `gpt-5.4-mini` for a new case and the saved model when resuming. Omitted session settings also inherit the saved binding. Explicit conflicting model, adapter or session settings are rejected; reconnecting does not replace the recorded configuration.

Host configuration selects the backend using the existing [backend settings](backend.md). The local pilot binds each server process to one discovery. Tools do not accept an arbitrary discovery id, model setting, usage claim, or agent-written list of observed URLs. This process boundary is local context binding; authenticated remote workspace access has not been implemented.

## Tool sequence and results

| Tool | Purpose |
| --- | --- |
| begin_research | Bind the server to a brief and return saved case ids |
| get_research_context | Retrieve the case, schemas, receipts, operations, pipelines, jobs, and remaining limits |
| search_sources | Execute the configured search provider and save its actual response |
| inspect_source | Capture a URL and inspect its response shape or text |
| probe_source | Test the exact SourceSpec and retain a sampled compatibility report |
| get_evidence | Read a bounded section of stored evidence without fetching again |
| submit_proposal | Validate every citation and ready-source proof, then save proposal/pipeline ids |
| list_pipelines | List candidate versions for the bound question |
| start_collection | Queue a collection of a saved pipeline version and return its durable job id |
| get_job / list_jobs | Reconcile and inspect jobs for the bound question |
| cancel_job | Request cancellation while preserving actual published data |
| export_observations | Queue an export with an explicit publication cutoff |
| read_export | Read bounded observations or manifest content from a completed export |

Search, inspection, probe, proposal, collection, and export tools require an operation_id. Reuse it for an identical retry; use a new id for a changed request or a deliberate fresh observation. The result envelope contains operation_id, status, data, evidence_refs, error, and remaining. The error object includes code, message, and retryable. Failed probes preserve their report and evidence references. A successful lookup of a failed case or job still returns a successful lookup envelope with its actual status in data.

Error codes come from exception types and host-assigned domain codes. Source URLs and provider messages cannot impersonate budget exhaustion, context conflicts or writer contention. Actual `WriterBusy` errors produce retryable `operation_busy`; replaying a recorded terminal failure produces nonretryable `operation_failed` and does not dispatch again.

`remaining` reads the bound discovery's limits and operation counts directly from the registry, without loading receipts or opening pipeline stores. Each envelope reads current counts, including operations admitted by another resumed host. An unbound case reports `null`. A failed budget read instead reports `budget_state_unavailable`, sets the envelope status to `error`, and retains any completed result and evidence references. If an operation also failed, its original error code/message remain and `error.remaining_error` describes the budget failure. Automatic retry is disabled until the backend is repaired; an identical completed-operation replay then returns its saved receipt without repeating work.

`get_research_context`, `get_job` and `list_jobs` can persist reconciled job status, so their MCP annotations set `readOnlyHint=false`, `idempotentHint=true` and `openWorldHint=false`. Evidence and artifact reads remain read-only. These annotations describe effects; they do not authorize an action.

FastMCP returns matching JSON text and structured content, because the tested Omnigent formatter reads the text representation. Unexpected tool arguments are rejected. The integration stays on the MCP Python SDK 1.x line with a less-than-2 constraint; Omnigent uses its independently locked environment. [Official SDK version guidance](https://github.com/modelcontextprotocol/python-sdk).

## Provider schemas and host defaults

Direct discovery and the fourteen MCP tools share a provider-schema adapter. Model-facing object schemas are closed, list every property as required, and contain no `default` annotations. Previously optional, nonnullable inputs accept `null` to request their existing host defaults. For example, `max_results: null` uses six results and a source's `poll_interval_seconds: null` uses 900 seconds. Fields that already accepted null keep that meaning; required values remain required. This follows the documented [strict function schema contract](https://developers.openai.com/api/docs/guides/function-calling#strict-mode).

Normalization happens before domain validation. It preserves explicit values, meaningful nulls, numeric/array bounds, and rejection of unknown fields. Domain configuration schemas and saved proposals keep their existing defaults. Older clients can still omit optional fields; clients following the advertised strict schemas supply them, using null where appropriate. The same source normalization applies to direct discovery's `source_json` probe input and its structured proposal output, as well as MCP probes and submissions, so equivalent configurations retain their probe identity.

Search advertises the closed `SearchFilters` schema while retaining the original explicit filter values for invocation. It removes only newly nullable defaults before service admission, preserving existing operation hashes for omitted defaults, explicit nulls and legacy accepted values. Completed requests still replay without another provider call. The [offline provider schema preflight](../examples/omnigent/README.md#offline-provider-schema-preflight) records actual SDK serialization and MCP/Agents conversion checks. Live provider acceptance remains a separate milestone 0 check.

## Evidence, retries, and limits

Schema migration 3 adds discovery_contexts, discovery_operations, and discovery_receipts to the existing SQLite/Postgres schema. The operation ledger stores the request hash, attempt status, and result. Receipts belong to one discovery and link back to the operation that produced them. Proposal, pipeline registration, and completed submission result commit atomically, including nested registry calls.

The initial limits are 8 search attempts, 16 inspections, 12 probes, and a ten-minute deadline. Failed attempts consume their operation budget; replaying a completed or failed operation does not make another request. Completed receipts remain readable after the deadline. Native direct-CLI search availability and its per-response call limit are reduced before the next model request; failed native searches are recorded and can be repaired using remaining budget or previous evidence.

Search queries and filters are validated before an operation is admitted in both the direct and MCP paths. Invalid filters consume neither budget nor the operation id, so corrected arguments can use that id. Once admitted, a provider failure consumes an attempt. Validation preserves the stored request shape and hash, including omitted filter defaults, so previously completed requests still replay.

An interrupted operation is not automatically rerun with the same id. The service acquires the discovery's exclusive lock before reconciling an abandoned operation; an active owner keeps the retry from executing. Independent local discoveries use separate service locks. Existing collection writer locks and publication behavior remain in the ingestion layer.

Proposals must cite only observed URLs, and each ready source must reference a successful matching probe from the same discovery. A changed connector configuration requires another probe. A search result establishes discovery, a capture establishes access, and a probe establishes sampled structural compatibility. None establishes complete historical coverage or semantic relevance by itself.

The service exports receipts.json, probes.json, proposal.json, proposal.md, pipeline.json when available, and trace.jsonl. The registry and ledger are authoritative; proposal exports can be regenerated after a failed file write. Direct model traces also record the available instructions, input, tool definitions, response schema, outputs, and reported usage. The [independent evaluator](research-evaluation.md) reads these artifacts.

## Search provider

The default wrapper calls Keenable's public MCP search_web_pages tool. --search-endpoint can select a compatible endpoint. The wrapper forces realtime search, limits results and snippet length, and accepts only declared filters. It preserves the original MCP response and parses the observed Title/URL/Acquired/Snippets result records. URLs appearing only inside snippets do not become result metadata. Unrecognized or error responses fail validation and are retained in the operation result and trace.

The recorded [provider fixture](../examples/omnigent/evidence/2026-09-08/keenable-search.json) came from a bounded public search. A provider text format without an output schema remains a compatibility dependency; unexpected changes should produce a visible adapter error. Search-provider usage is unknown unless supplied by the provider. The direct CLI can now select this wrapper with --search-provider mcp and an optional --search-endpoint. It uses the same service ledger and filters, disables native search in that mode, and records provider configuration. Native search remains the default; provider parity alone does not control the full runtime experiment.

## Collection, export, and recovery

Schema migration 4 adds research_jobs. A job records its owning question and discovery, pipeline version, request hash, deadline, worker state, and result. It shares the discovery operation-id namespace, so a collection cannot reuse an inspection id. A question may have at most four queued or running jobs. Each accepted collection gets its run id before a worker starts.

Workers run in detached local processes and survive the MCP client disconnecting. They use the existing collection engine and pipeline writer lock. Poll get_job or list_jobs for actual progress. A job has a ten-minute deadline by default; cancellation is cooperative during fetches, retries, and between sources. A source publishes only after its whole validated snapshot completes. Earlier successfully published sources remain available if another source is cancelled or interrupted.

Reconciliation checks the underlying run after acquiring the job lock. An active worker keeps ownership; a stopped worker's published run determines the final status. A completed run is not collected again just because the worker stopped before updating the job. A queued job that never began can be launched by repeating start_collection with the original operation id. Interrupted collections retain their terminal result; an intentional new collection needs a new id. This local worker lifecycle does not include a background scheduler.

export_observations requires an explicit, non-future as_of timestamp. The export selects published observations available by that cutoff, retains lineage, and archives the JSONL and manifest as hashed blobs. read_export checks those blobs and limits each read to 50,000 characters. Reopening the question exposes its previous jobs, including jobs created by an earlier discovery. Recovery uses the originating discovery's artifact directory.

Registered datasets are scoped by question id and pipeline name. Identically named pipelines in different questions have independent observations, checkpoints, locks, and run histories. Pipeline definitions and fingerprints remain unchanged. Pre-namespace registered data is reported in context and by the CLI; it is available through the explicit compatibility path described in the [backend guide](backend.md).

## Verification and remaining work

~~~sh
uv run --locked --extra mcp pytest
uv run --locked --extra mcp ruff check src tests
uv run --locked --extra mcp ruff format --check src tests
~~~

Use [test configurations](testing.md) for the required Docker/Omnigent environment and skip policy, and the [shared tracker](goals/omnigent-integration.md) for current checkpoint commands, counts and results. The September 8 normal server/runner and budget fixtures saved a proposal, collected/exported data through real workers, restarted and reopened the same case with synthetic model responses. The workflow evaluator checks persisted collection/export state without performing recovery writes; [browser acceptance](omnigent-ui-acceptance.md) records the separate UI walkthrough.

The RW-11 checkpoint subsequently verified local Postgres and MinIO execution, including backend parity tests and a detached collection/restart/export workflow; its commands and evidence are recorded in the shared tracker. These checks require their own backend services and do not run merely because the MCP extra is installed. Fixture results do not establish real-model quality, live provider billing or measured optimization improvement. Omnigent's native budget fixture verifies blocking the next turn after its threshold; the running turn can exceed that threshold.
