# Research Harness

Tools for finding data sources, collecting them repeatedly, and keeping research
inputs traceable.

For example: start with a question about trade policy, find relevant government
feeds and market data, test whether those sources can be collected, then save a
repeatable pipeline. Every exported record links back to the response it came from.

**Status:** early-stage Python CLI and agent integration. Data collection works
today. The broader financial, event and coding/ML research workflows are being
built around it.

## What you can do today

- **Find sources:** give an agent a research brief and get a source assessment,
  coverage gaps, and a pipeline definition when supported sources are found.
- **Collect data:** use RSS/Atom feeds, JSON APIs, HTML pages, and Polymarket or
  Kalshi market data. Save original responses, timestamps and revisions.
- **Export and inspect:** write JSONL datasets, trace records to their sources,
  and reprocess saved responses without fetching them again.
- **Connect agents:** use the CLI directly or let Omnigent call the same research
  and ingestion operations through MCP.

Live model quality and strategy-optimization gains have not been measured yet.
The integration tests use scripted model responses.

## Try it without an API key

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
This example fetches a public government RSS feed; no model or paid service is used.

```sh
git clone https://github.com/Madhavan113/researchharness.git
cd researchharness
uv sync --locked

uv run rh validate examples/quickstart.pipeline.json
uv run rh run examples/quickstart.pipeline.json
uv run rh export examples/quickstart.pipeline.json --out artifacts/quickstart.jsonl
```

Open `artifacts/quickstart.jsonl` for the collected records and
`artifacts/quickstart.jsonl.manifest.json` for the record count, checksum and source
health. Local history and raw responses are saved under `.researchharness/quickstart/`.
The example collects the OFAC site feed, which includes sanctions-program pages;
it is not a complete list of new sanctions actions. Results depend on the live feed.

Edit the [example configuration](examples/quickstart.pipeline.json) to use your
own sources. See the [pipeline guide](docs/data-pipeline.md) for connector formats,
repeated collection, exports and recovery. Scheduling is currently external;
`rh run --due` checks which sources are ready to collect when invoked.

## Start from a research question

Set `OPENAI_API_KEY` in your shell, then run the included brief:

```sh
uv run rh discover \
  --brief examples/trade-policy.brief.md \
  --out artifacts/trade-policy
```

This calls a model and incurs provider charges. It writes a readable `proposal.md`,
source evidence and, when supported sources pass their checks, `pipeline.json`.
Collection is a separate step; review the proposal before running that pipeline.
See [discovery options](docs/data-pipeline.md#discovery-and-proposal-generation)
for model settings and limits. For agent-driven use, follow the
[Omnigent setup guide](examples/omnigent/README.md).

## Where this is going

The shared foundation supports investigations and optional data ingestion.
Planned workflows have different outputs:

| Workflow | Intended outputs |
| --- | --- |
| Financial research | Analyst work, diligence memos, financial models and DCF analysis |
| Event research | Dated evidence, event timelines, competing scenarios and assessments |
| Coding and ML research | Changes grounded in repository/design constraints, reproducible experiments and baseline comparisons |

These are planned capabilities, not finished workflows. General investigations
remain part of the scope; a task does not have to produce a pipeline.

## Documentation and downloads

Use the [documentation index](docs/README.md) for storage, agent integration,
strategy experiments, testing and contributor guides.

Install the current CLI from this repository. The existing
[GitHub release](https://github.com/Madhavan113/researchharness/releases/tag/evidence-historical-review-all-2026-09-12)
is an archive of test evidence for reviewers, not an application installer or a
benchmark result.
