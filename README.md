# Research Harness

A Python harness that turns a research question into a proposed data pipeline. An agent discovers sources, inspects their responses, tests connector configurations, and produces a source assessment plus a runnable definition. The collectors preserve original responses, revisions, and timestamps for later analytical models.

**Status:** working local CLI and Omnigent integration. Public-source ingestion has been exercised against Polymarket, Kalshi, and OFAC. The Omnigent browser workflow has saved proposals, collected/exported data, and reopened the same case after restarting, using synthetic model responses and real local workers. A controlled fixture comparison now exercises both actual runtimes through shared request controls. Live model quality and comparisons using real model responses remain unverified. Model-driven discovery requires an `OPENAI_API_KEY`; forecasting and a background scheduler remain future work.

**Shared development goal:** build the Omnigent integration, then add independently evaluated strategy optimization. See the [goal and milestone tracker](docs/goals/omnigent-integration.md), [accepted implementation plan](docs/omnigent-integration-plan.md), and [agent coordination instructions](AGENTS.md). Offline review follow-ups are in progress; measured evaluation still requires independent benchmark review, provider access and the pending spending decision. The tracker records ownership and completion evidence. The [checkpoint scope](docs/checkpoint-scope.md) records what PR #1 delivers, its boundaries and how to reproduce its evidence; [remaining work](docs/remaining-work.md) lists reviewed follow-up items in priority order.

The [durable research service and fourteen local MCP tools](docs/research-service.md) share validation with the direct CLI. Follow the [Omnigent setup and fixture walkthrough](examples/omnigent/README.md), inspect the [browser acceptance evidence](docs/omnigent-ui-acceptance.md), or run the [independent development evaluation](docs/research-evaluation.md). The [controlled comparison runner](docs/controlled-comparison.md) freezes shared settings, inputs and failure records for both runtimes and independently verifies gateway usage. The [budgeted pilot](docs/pilot-budget.md) adds shared reservations before provider dispatch, evidence-backed settlement and interruption recovery; its proposed spending decision remains pending. Local Postgres/MinIO acceptance now covers restart, detached jobs and scoped exports. The [strategy integration](docs/strategy-optimization.md) adds isolated Python execution, observation/context selection, verified-stop finalization, complete development archives, a bounded coding proposer, a durable search controller, advisory development-string audits and private final evaluation with registrable-domain split checks. A [complete runtime fixture](examples/evaluation/evidence/combined-strategy-search-2026-09-09/acceptance.json) now verifies three search iterations and isolated final evaluation through the actual coding proposer, Docker and normal Omnigent runtime. Responses are synthetic; reviewed cases and measured model optimization remain unfinished.

The [budgeted strategy runner](docs/strategy-optimization.md#run-search-with-the-shared-model-budget) now shares the pilot ledger across research, coding proposals and private final evaluation. Its [acceptance record](examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/acceptance.json) verifies 125 reserved and settled synthetic requests through actual Omnigent/Docker execution. The September 9 checkpoint records 1,004 passing tests; current follow-up verification is recorded in the tracker. Human review and measured model evaluation remain outstanding.

Direct discovery and MCP now emit strict provider schemas with host-owned defaults. The [offline schema preflight](examples/omnigent/README.md#offline-provider-schema-preflight) captures SDK serialization and conversion without a provider call; live acceptance remains outstanding.

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

Native OpenAI search is the default. To use the same search-provider wrapper as Omnigent, install the optional MCP extra and add `--search-provider mcp`:

~~~sh
uv run --extra mcp rh discover \
  --brief examples/trade-policy.brief.md \
  --out artifacts/direct-mcp \
  --search-provider mcp
~~~

`--search-endpoint` can select a compatible MCP endpoint. `--settings` and `--instructions` bind explicit execution settings and a shared instruction file. Use the controlled comparison runner to preserve these controls and failed cases across both runtimes.

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

## Registry and shared backend

Every research question, discovery run, proposal, and candidate pipeline definition is recorded in a registry, so alternatives for the same question can be listed, compared, adopted, or retired. Discovery registers automatically; hand-written definitions are added with `rh pipelines register`. The registry and all collected data live either in local SQLite (the default) or in a shared Postgres database plus an S3-compatible bucket, selected by `RH_DATABASE_URL` and `RH_BLOB_*`. Supabase provides both.

```sh
uv run rh backend init
uv run rh questions list
uv run rh pipelines list --question QUESTION_ID
uv run rh pipelines adopt PIPELINE_ID --note "Best coverage"
uv run rh run PIPELINE_ID
```

Pipeline ids work wherever a pipeline file is accepted. See [docs/backend.md](docs/backend.md) for setup, schema, and concurrency details.

## Reliability and extension

- Immutable response bodies addressed by SHA-256, with HTTP attempts and source provenance in SQLite or Postgres.
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

Pytest lists skip reasons by default. Follow the [test configurations](docs/testing.md) to include MCP, Docker and the pinned Omnigent runtime; `RH_TEST_REQUIRE_RUNTIME=1` makes missing configuration and skipped checks fail. The [CI workflow](.github/workflows/tests.yml) runs ordinary checks and the required runtime configuration on pushes and pull requests. New checkpoint archives follow the [evidence retention policy](docs/evidence-retention.md), with small hash-bound summaries in Git and complete archives in release assets.

The [portable evidence guide](docs/portable-evidence.md) explains derived review
copies with path placeholders and original/exported hashes. Historical originals
remain unchanged; prepared release assets are not yet published.

## Product research

The earlier company-analysis and prediction-market proposals provide context for the data layer:

- [Geopolitical prediction markets](docs/prediction-markets.md): contract research, evidence monitoring, market capacity, and a pilot proposal.
- [Analyst needs and product priorities](docs/analyst-needs.md): the broader job, user segments, and proposed first workflow.
- [Market landscape](docs/market-landscape.md): documented competitors, integration choices, and differentiation hypotheses.
- [Workflow examples](docs/product-workflows.md): event review, related-company monitoring, call preparation, and a fictional worked example.
- [Analyst pilot plan](docs/analyst-pilot.md): interviews, benchmark assignments, measurements, and decision criteria.
- [Research sources](docs/research-sources.md): evidence and its limits.
- [Harness architecture](docs/research-design.md): shared records, agent responsibilities, financial checks, and execution design.
