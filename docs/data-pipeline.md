# Source discovery and data pipelines

The first implementation starts with a research brief. The agent identifies data requirements, searches for sources, inspects actual responses, and proposes connector configurations. It uses the same normalizers for sample probes that the collection runner uses for ingestion.

## Discovery and proposal generation

Run `uv run rh discover --brief examples/trade-policy.brief.md --out artifacts/trade-policy` after setting `OPENAI_API_KEY`. A question can replace `--brief`, for example:

```sh
uv run rh discover "Find public sources for monitoring U.S.–China export policy and related prediction-market contracts" --out artifacts/export-policy
```

The agent uses the OpenAI Responses API with web search and two local tools: `inspect_url` and `probe_source`. Each can make bounded public GET requests. The model receives connector capabilities and the complete source schema. It can inspect data, revise a configuration, and try another probe when validation fails. The model has no shell or arbitrary code execution tool.

The final structured proposal separates required data needs, candidate sources, evidence URLs, collection configuration, freshness assessments, historical limits, access requirements, and open questions. The compiler independently checks that every ready source references a successful probe for the exact same configuration. Unknown proof ids, changed configurations, and citations with no observed URL are rejected and returned as repair feedback. Coverage gaps are calculated from verified sources, not accepted from a model's completion claim.

A successful sample produces `verified_sample`, which means the first response could be parsed into records. It does not verify all pages, all future updates, full history, or research relevance. Full collection separately checks pagination and record validity. A source that cannot yet be collected remains in the assessment; it is excluded from the executable configuration.

Discovery defaults to 16 model rounds, up to 12 source probes and 16 URL inspections. Each model response is capped at 6,000 output tokens and six hosted tool calls. `--max-rounds` changes the round budget. Actual token usage is recorded; these are operation limits, not a guaranteed dollar budget. Search can consult multiple pages per call.

The output directory contains the proposal, a supported-source pipeline when possible, probe reports, evidence storage, and an activity trace. The question, the discovery run, the proposal, and the compiled pipeline are also recorded in the registry (`registry` in `proposal.json`), so later discoveries for the same brief accumulate as comparable alternatives. Probe reports expose sample titles, declared publication dates, and content scope so the agent can assess relevance as well as parseability. An exhausted or failed discovery preserves `failure.json` and its evidence. Use a fresh output directory for a new discovery. A completed proposal is never silently overwritten.

Implementation uses [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search), [function calling](https://developers.openai.com/api/docs/guides/function-calling), and [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs). The configured default [GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini) supports these capabilities; access depends on the API account. Live model discovery has not been exercised in this workspace because no API key is configured.

## Source definitions

`rh schema source` and `rh schema pipeline` print the schemas. JSON configuration rejects unknown fields. Source ids must be unique and stable within a pipeline; use a new id when changing the meaning or identity domain of a dataset.

| Connector | Input and output |
| --- | --- |
| `polymarket` | A scoped Gamma market/event query or market keyset pagination. Normalizes individual contracts, preserving exact rules, dates, flags, outcomes, and available fee metadata. Optional books use outcome token ids. |
| `kalshi` | The public markets list with explicit cursor pagination. Retains primary and secondary rules. Optional books use dollar and fixed-point fields and derive asks from complementary bids. |
| `rss` | RSS 2.0 or Atom entries, identified by GUID/id or link. Retains the feed's content and declared dates. Article links are not automatically fetched. |
| `json` | An array selected by `items_pointer`, an `id_pointer`, optional `published_pointer`, and required non-null values selected by `required_pointers`. Original record fields remain available. |
| `html` | One HTML page with extracted text and any declared publication metadata. This is page text, which may include navigation. It does not execute JavaScript or parse PDFs. |

Example JSON endpoint configuration, using an illustrative hostname:

```json
{
  "id": "official-notices",
  "name": "Official notices",
  "connector": "json",
  "url": "https://data.example.org/notices",
  "items_pointer": "/results",
  "id_pointer": "/document_number",
  "published_pointer": "/published_at",
  "required_pointers": ["/title", "/document_number"],
  "pagination": {
    "mode": "next_url",
    "next_pointer": "/next_page_url",
    "stop_when_missing": false
  },
  "poll_interval_seconds": 3600,
  "max_pages": 10,
  "min_records": 1
}
```

Pointers use RFC 6901 syntax, including escaping `/` as `~1` and `~` as `~0`. Cursor pagination requires a continuation pointer and query parameter. Missing continuation fields fail by default. Set `stop_when_missing` only when omission is the documented terminal-page convention. Common advertised continuation fields cannot be ignored by selecting unpaginated collection. Hitting a page, record, or request limit with unfinished work fails the source.

Market adapters use [Polymarket's market API](https://docs.polymarket.com/api-reference/markets/list-markets), [keyset pagination](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination), [Polymarket order books](https://docs.polymarket.com/api-reference/market-data/get-order-book), [Kalshi markets](https://docs.kalshi.com/api-reference/market/get-markets), and [Kalshi order books](https://docs.kalshi.com/api-reference/market/get-market-orderbook). Catalog partitions and historical endpoints are not automatically backfilled. The provided [OFAC source](../examples/ofac.source.json) was directly verified against the [official site RSS feed](https://ofac.treasury.gov/rss.xml). Its sample includes sanctions-program pages; it is not established as a complete feed of newly issued actions. The separate [recent-actions listing](https://ofac.treasury.gov/recent-actions) is a candidate for dedicated coverage. This distinction demonstrates why structural probes and coverage assessments are separate.

## Collection and publication

`rh run pipeline.json` executes enabled sources once. `--source ID` selects one source. `--due` skips a source until its interval has elapsed since its last successful run, unless its configuration changed. No schedule is installed; an operator can invoke the command from an existing scheduler.

The runner saves each HTTP attempt before parsing. Bodies are stored by content hash; identical responses reuse the same file. Successful conditional requests retain a reference to the earlier body and create a new observation. Retries honor usable `Retry-After` values; an excessive delay defers the work to a later run. Redirects, response size, requests, and attempts are bounded.

All pages for a source are collected and normalized before any of its records become visible to exports. A failed request, malformed record, conflicting duplicate identity, or insufficient record count prevents publication and state advancement for that source. Valid sources in the same run can succeed independently. Quarantined records and response evidence remain inspectable. CLI failure status is nonzero if any selected source fails or is degraded.

Runs restart enumeration at the first page, using conditional requests and record-version deduplication. Checkpoints describe completed polls; this version does not resume halfway through an incomplete cursor sequence. This avoids continuing a cursor against a changed dataset. The local writer lock prevents competing ingestion runs in the same data directory, and abandoned run rows are marked interrupted on the next writer acquisition.

Every JSONL record includes a stable source/key, content and version hashes, normalizer version, capture id, source-run id, source configuration hash, URL, and body hash. Contract prices and quantities use decimal strings; order-book levels are sorted and validated. An empty or unavailable book supplies no implied executable price. Generic JSON numbers retain their source representation in the raw body; downstream decimal semantics need a domain-specific mapping.

## Time, revisions, and exports

| Field | Meaning |
| --- | --- |
| `published_at` | A parseable, timezone-qualified publication timestamp supplied by the source. Date-only or unknown values remain in source data without inventing an exact instant. |
| `first_observed_at` | Earliest successfully published observation of that source/key available within the export cutoff. |
| `observed_at` | When the HTTP response supporting this record was captured. |
| `available_at` | When the complete source snapshot was published locally. |

Repeated observations do not duplicate an unchanged version. A change and later reversion remain distinguishable through the observation history. A document published in 2020 but first collected today is unavailable to a 2020 export.

```sh
uv run rh export examples/trade-policy.pipeline.json --out artifacts/observations.jsonl
uv run rh export examples/trade-policy.pipeline.json --as-of 2026-09-07T20:00:00Z --kind market --out artifacts/markets-at-cutoff.jsonl
```

The export selects the latest successful observation of each source/kind/key for which both observation and local availability are at or before the cutoff. It writes a companion `.manifest.json` containing the cutoff, record count, data checksum, source status, collection freshness, and configuration-match information. Fresh collection does not prove fresh underlying publications. Consumers should check the manifest before using the dataset.

These are last-known records, selected from the source ids in the supplied configuration. Disappearance from a feed or filtered query is not inferred to be deletion or market resolution. Disabling a source stops collection without erasing its history. Retired source ids remain in storage but are excluded when absent from the export configuration. Each run preserves its configuration; use that saved configuration for a historical source universe. A time cutoff alone does not eliminate universe-selection bias. There are no inferred cross-source joins, entity matches, probabilities, or translations in this version.

## Inspection and offline replay

```sh
uv run rh status examples/trade-policy.pipeline.json
uv run rh status examples/trade-policy.pipeline.json --run-id RUN_ID
uv run rh replay examples/trade-policy.pipeline.json --run-id RUN_ID --out artifacts/replay.json
uv run rh probe examples/ofac.source.json --data-dir artifacts/probes
```

Replay reads saved response bodies, verifies their hashes, and runs the current normalizer without HTTP requests, publication, or checkpoint changes. It can reveal valid records and failures from an unsuccessful run. It does not turn a partial run into a published snapshot. Increment `NORMALIZER_VERSION` when shipping changed normalization semantics; retain the corresponding code and lockfile for reproducible research.

Storage is local SQLite plus a raw-body directory by default, or a shared Postgres schema plus an S3-compatible bucket when `RH_DATABASE_URL` is set; see [backend.md](backend.md) for the registry of questions and candidate pipelines, configuration, and concurrency rules. Back up a local database and its raw files together using a consistent SQLite backup or while the runner is stopped. Authenticated/vendor connectors, retention management, full historical backfills, and production scheduling remain separate work. Public URL and redirect checks reduce accidental access to local services; a hosted deployment also needs enforced network egress controls.

## Verification

The test suite exercises the actual OpenAI SDK against mocked Responses API output, including source probing and proposal compilation. Deterministic tests cover retries, conditional requests, malformed records, decimal books, pagination failures, publication timing, revisions, replay integrity, URL checks, and configuration validation. A separate live smoke run collected from the three sources in the hand-configured example. Model discovery quality and long-term source reliability still require live evaluation.
