# Omnigent compatibility evidence

September 8, 2026. Owner: `/root/omnigent_spike`. Milestone 0 has a reproducible offline runtime check; full server/UI and live-model acceptance remain open. See the [shared goal](goals/omnigent-integration.md) and [example setup](../examples/omnigent/README.md).

## Tested runtime

The prior review checkout had disappeared. A fresh source checkout at `/tmp/researchharness-omnigent-be042b39` was installed without modifying upstream source. The setup script checks an existing checkout's revision and refuses tracked modifications.

| Component | Tested value |
| --- | --- |
| Omnigent commit | `be042b390e293a8d586cbb7e403a2ce0ce38fc62` |
| Omnigent / client / UI SDK | `0.13.0.dev0` |
| Python | `3.13.12`, macOS arm64 |
| Installation | `uv==0.11.8`, upstream frozen lock, no development group |
| OpenAI Python SDK | `2.41.0` |
| OpenAI Agents SDK | `0.13.6` |
| MCP SDK | `1.28.1` |
| HTTPX | `0.28.1` |
| Authored adapter and model | `openai-agents`, `gpt-5.4-mini` |

The host's `uv==0.10.4` did not satisfy the upstream `>=0.11.8` requirement. The setup uses `uvx --from uv==0.11.8`, leaving the host installation intact. The separate environment also avoids forcing Research Harness's newer OpenAI SDK into Omnigent's `<2.45` constraint. Full versions and source/lock hashes are in [metadata.json](../examples/omnigent/evidence/2026-09-08/metadata.json).

## What ran

The authored YAML was parsed by Omnigent. Its runner MCP manager launched the fixture server as a real stdio subprocess, negotiated MCP, discovered schemas, and called its tools. The real `OpenAIAgentsSDKExecutor` used the real Agents and OpenAI SDKs; an injected HTTP transport supplied deterministic Responses API SSE messages. The resulting tool calls went through the real manager and subprocess, and the SDK's second model request contained the actual returned receipt. No SDK class or runner method was mocked.

The check verified these behaviors:

- The instruction marker, pinned model, nested source arguments, Unicode, quotes, and literal environment-expression text arrive intact.
- Tool request/completion events preserve the call id and JSON receipt; malformed MCP arguments return an actionable error.
- Two model responses aggregate input/output/total usage; context usage reflects the final response rather than the sum.
- Updated instructions reach the next request, and a warm executor retains prior conversation/tool history.
- A fresh executor replays the supplied user/assistant history. This establishes the adapter's replay behavior; durable research recovery still depends on the domain service and stored case context.
- Interrupting an observed live fixture stream stops the SDK run and closes that stream. It emits no completed-turn usage. Unknown interrupted usage must remain unknown in evaluations.

The checked-in [events](../examples/omnigent/evidence/2026-09-08/events.json), [model requests](../examples/omnigent/evidence/2026-09-08/model-requests.json), [model responses](../examples/omnigent/evidence/2026-09-08/model-responses.json), [tool schemas](../examples/omnigent/evidence/2026-09-08/tool-schemas.json), and [subprocess receipts](../examples/omnigent/evidence/2026-09-08/mcp-calls.jsonl) contain the available evidence. The raw model responses and their token counts are synthetic fixtures; they do not measure model quality, latency, or real billing.

## Integration contracts and discovered limits

**MCP results need JSON text content.** Omnigent's manager converts MCP results to a string by joining content blocks. It ignores `structuredContent`. The fixture confirmed a structured-only result becomes `(empty response)`. Return an identical JSON envelope in `TextContent` and `structuredContent`, or use FastMCP's typed return handling, which mirrors the JSON into text. The backend envelope remains authoritative for operation state. [Pinned MCP formatter](https://github.com/omnigent-ai/omnigent/blob/be042b390e293a8d586cbb7e403a2ce0ce38fc62/omnigent/tools/mcp.py).

**Tool names and configuration are explicit.** MCP tool names appear as `server__tool`, so a server named `research` exposes `research__begin_research`, etc. Bundled `tools/mcp/*.yaml` files are discovered automatically. Stdio configuration uses `name`, `transport: stdio`, `command`, `args`, optional `env`, and `timeout`; command and args are literal, while env values expand environment references. Generate absolute launch paths or use a dedicated launcher. The old MCP `sandbox` key is rejected at this pin. [Pinned parser](https://github.com/omnigent-ai/omnigent/blob/be042b390e293a8d586cbb7e403a2ce0ce38fc62/omnigent/spec/parser.py).

**Cached-token pricing data is lost.** Each fixture response declared 100 input tokens, including 20 cached, and 10 output tokens. After two responses the runtime returned 200 input / 20 output / 220 total / 110 context, with no cached-token field. The pinned Agents SDK uses `usage.input_tokens_details`; Omnigent reads `usage.prompt_tokens_details`. Preserve provider usage separately where accessible, or record the cache discount as unknown. Do not treat this aggregate as exact billed cost. The compatibility check records this defect without changing upstream code. [Pinned usage conversion](https://github.com/omnigent-ai/omnigent/blob/be042b390e293a8d586cbb7e403a2ce0ce38fc62/omnigent/inner/openai_agents_sdk_executor.py).

**Schema references need provider verification.** The fixture's typed nested source produces `$defs`/`$ref`. The local SDK passes it through and Omnigent warns that provider handling can differ. A model transport fixture cannot prove the live provider accepts that schema. Flatten nonrecursive references at the MCP boundary if live preflight rejects them; do not weaken backend validation.

The intended production extension remains an authored YAML agent with explicit model and MCP tools. The compatibility script's direct executor callback injection is a test seam, not the production integration. The production runner/server must supply its normal tool bridge, session binding, policy handling, and event persistence.

## Search endpoint observed

A real keyless MCP initialization and tool listing succeeded against `https://api.keenable.ai/mcp`: server `keenable-mcp-server==0.2.1`, protocol `2025-11-25`. The reproducible preflight and one bounded public search are saved as [schema](../examples/omnigent/evidence/2026-09-08/keenable-schema.json) and [search receipt](../examples/omnigent/evidence/2026-09-08/keenable-search.json). No credentials were read; the search requested two realtime results from a public government site.

- `search_web_pages` requires `query`; optional fields are `site`, acquired/published date filters, `query_time`, `snippet_max_length` (180–10000), `max_results` (1–50), and `mode` (`realtime` or `pro`).
- `fetch_page_content` requires `url`; optional fields include `max_chars`, `live`, and `prompt`. The provider describes `prompt` as LLM extraction, so omit it when retaining page content as evidence.
- Both tools omit an output schema. The observed search returns one text block with labeled result records; `structuredContent` is null. Only URLs extracted from actual stored provider records should become discovery evidence. Search snippets do not establish successful connector probes.

This confirms present reachability and response shape. It does not establish a service-level guarantee, future availability, source completeness, or comparison fairness with the existing native-search baseline.

## Commands and remaining acceptance

~~~sh
sh examples/omnigent/setup.sh /tmp/researchharness-omnigent-be042b39
/tmp/researchharness-omnigent-be042b39/.venv/bin/python examples/omnigent/compatibility.py --out /tmp/researchharness-m0-verified
/tmp/researchharness-omnigent-be042b39/.venv/bin/python examples/omnigent/search_preflight.py --out /tmp/researchharness-m0-keenable-verified.json
.venv/bin/ruff check examples/omnigent
.venv/bin/ruff format --check examples/omnigent
~~~

The setup check passed with 91 installed packages. The final offline run passed ten contract assertions. The preflight confirmed the two expected search tools. The first fixture run exposed the cache-detail loss; the final check deliberately asserts and reports the observed pinned behavior. FastMCP emitted a Pydantic lifespan forward-reference warning at startup, but the MCP protocol and shutdown completed successfully.

Still required for full M0: establish provider settings and spend budget, launch the registered authored agent through the actual Omnigent server/UI, call the fixture with a real model, inspect interruption and usage in that path, and exercise the configured spending policy. Then M3 must replace the fixture with the real Research Harness MCP tools and obtain saved proposal/pipeline ids. These checks are not marked complete by the offline run.
