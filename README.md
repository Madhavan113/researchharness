# Research Harness

A Python harness that turns a research question into a proposed data pipeline. An agent discovers sources, inspects their responses, tests connector configurations, and produces a source assessment plus a runnable definition. The collectors preserve original responses, revisions, and timestamps for later analytical models.

**Status:** working local CLI prototype. Public-source ingestion has been exercised against Polymarket, Kalshi, and OFAC. The agent's tool loop is tested through the actual OpenAI SDK with mocked model responses. Live model-driven discovery requires an `OPENAI_API_KEY`; forecasting models, a web UI, and a background scheduler are future work.

## Start with a research question

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). The lockfile pins dependencies.

```sh
uv sync
```

Set `OPENAI_API_KEY` in your shell, then run:

```sh
uv run rh discover \
  --brief examples/trade-policy.brief.md \
  --out artifacts/trade-policy
```

The example brief asks for sources useful to U.S.–China trade and export-policy research. You can also pass a question directly. `--model` or `OPENAI_MODEL` selects the discovery model; the default is `gpt-5.4-mini`. The `.env.example` file documents variables and is not automatically loaded.

Discovery writes:

- `proposal.md` and `proposal.json`: source assessments, citations, coverage, and open questions.
- `probes.json` and `evidence/`: actual source responses and structural probe results.
- `pipeline.json`: executable definitions backed by matching successful probes, when any are available.
- `trace.jsonl`: tool activity, model response metadata, validation feedback, and token usage.

If a source requires an unsupported connector or credentials, the proposal records that requirement. A source sample does not establish complete history or sustained availability.

## Collect and export

```sh
uv run rh validate artifacts/trade-policy/pipeline.json
uv run rh run artifacts/trade-policy/pipeline.json
uv run rh status artifacts/trade-policy/pipeline.json
uv run rh export artifacts/trade-policy/pipeline.json \
  --out artifacts/trade-policy/observations.jsonl
```

Exports include lineage and a companion manifest with a data checksum and source health. `--as-of` selects the latest records actually observed **and published by the pipeline** before a timezone-qualified cutoff. An old source publication date never backdates collection.

For a live collection example that needs no model key:

```sh
uv run rh run examples/trade-policy.pipeline.json
```

This hand-configured example uses three public sources identified during the product research. Its Polymarket contract is dated September 2026; use discovery or edit the watchlist for later research.

## Reliability and extension

- Immutable response bodies addressed by SHA-256, with HTTP attempts and source provenance in SQLite.
- Conditional requests, bounded retries, explicit pagination, and polling intervals respected by `rh run --due`.
- Atomic publication per source. Failed pages, conflicting record identities, and quarantined records cannot advance its state or publish a partial snapshot.
- Version deduplication with separate observations, so unchanged data still has a fresh collection history.
- Offline replay from captured bodies, without HTTP requests or changes to published data.
- Polymarket and Kalshi metadata/books, RSS/Atom, configurable JSON endpoints, and HTML page text.

Read the [pipeline guide](docs/data-pipeline.md) for commands, schemas, guarantees, and limits. `rh catalog` lists connector capabilities; `rh schema source` exposes the configuration contract an agent uses.

```sh
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
```

## Product research

The earlier company-analysis and prediction-market proposals provide context for the data layer:

- [Geopolitical prediction markets](docs/prediction-markets.md): contract research, evidence monitoring, market capacity, and a pilot proposal.
- [Analyst needs and product priorities](docs/analyst-needs.md): the broader job, user segments, and proposed first workflow.
- [Market landscape](docs/market-landscape.md): documented competitors, integration choices, and differentiation hypotheses.
- [Workflow examples](docs/product-workflows.md): event review, related-company monitoring, call preparation, and a fictional worked example.
- [Analyst pilot plan](docs/analyst-pilot.md): interviews, benchmark assignments, measurements, and decision criteria.
- [Research sources](docs/research-sources.md): evidence and its limits.
- [Harness architecture](docs/research-design.md): shared records, agent responsibilities, financial checks, and execution design.
