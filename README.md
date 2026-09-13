# Research Harness

Tools for running traceable research experiments and collecting the data they use.
The experiment path connects a reviewed question and benchmark to an Omnigent
worker in Docker, then retains its code, actions and independently computed score.
The ingestion path discovers sources and collects them repeatedly, preserving
the original responses behind each exported record.

**Status:** early-stage Python CLI and agent integration. Data collection works
today. Experiment delegation works with scripted model responses; a measured
research baseline and automated candidate search remain unfinished. See the
[current goal](docs/goals/reproducible-research.md).

## What you can do today

- **Find sources:** give an agent a research brief and get a source assessment,
  coverage gaps, and a pipeline definition when supported sources are found.
- **Collect data:** use RSS/Atom feeds, JSON APIs, HTML pages, and Polymarket or
  Kalshi market data. Save original responses, timestamps and revisions.
- **Export and inspect:** write JSONL datasets, trace records to their sources,
  and reprocess saved responses without fetching them again.
- **Connect agents:** use the CLI directly or let Omnigent call the same research
  and ingestion operations through MCP.
- **Curate an experiment:** snapshot a plan and task environment, run reference/no-op
  benchmark controls, and record an operator's review decision. See the
  [experiment guide](docs/experiments.md).
- **Run an agent program:** preview a reviewed experiment's model settings and
  budget, then explicitly launch its program through Omnigent in Docker. Paid runs
  require provider access and a recorded spending decision.
- **Test experiment execution:** run the included Omnigent/Docker integration
  fixture with an editable Python agent program, inspect worker actions and verify
  that a forged reward cannot alter the independent score. This uses authored
  responses, not a live model.

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

The [goal](docs/goals/reproducible-research.md) is to turn one AI research question
into a runnable baseline, experiments delegated through Omnigent, and an
independently checked result. Start with a bounded Meta-Harness reproduction;
preserve code, execution evidence and failed attempts so someone else can rerun it.

Finance/event products and a general software factory are deferred. Existing
ingestion remains useful for supplying research data; an experiment does not have
to produce a pipeline. The current strategy optimizer is a constrained
source-discovery adaptation tested with fixtures, not a reproduction of the
paper's measured results.

## Documentation and downloads

Use the [documentation index](docs/README.md) for storage, agent integration,
strategy experiments, testing and contributor guides.

Install the current CLI from this repository. The existing
[GitHub release](https://github.com/Madhavan113/researchharness/releases/tag/evidence-historical-review-all-2026-09-12)
is an archive of test evidence for reviewers, not an application installer or a
benchmark result.
