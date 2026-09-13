# Documentation

Start with the [project README](../README.md) for an introduction and a runnable
example. This index groups the detailed guides by what you want to do.

## Code map

Most runtime code is under `src/research_harness/`:

| Area | Responsibility |
| --- | --- |
| [cli.py](../src/research_harness/cli.py) | Routes `rh discover`, `run`, `export` and other terminal commands. |
| [discovery.py](../src/research_harness/discovery.py) | Runs the direct model/tool loop for discovering sources. |
| [services/](../src/research_harness/services) | Saves discovery state, checks source evidence and proposals, and manages durable collection/export jobs. |
| [engine.py](../src/research_harness/engine.py) and [connectors.py](../src/research_harness/connectors.py) | Fetch and parse sources, collect complete snapshots, replay captures and export data. |
| [config.py](../src/research_harness/config.py) and [records.py](../src/research_harness/records.py) | Define validated pipeline/source configurations and data records. |
| [store.py](../src/research_harness/store.py), [backend.py](../src/research_harness/backend.py) and [blobs.py](../src/research_harness/blobs.py) | Store metadata, record history and original responses locally or in Postgres/S3. |
| [mcp/](../src/research_harness/mcp) and [integrations/](../src/research_harness/integrations) | Expose operations as agent tools and connect the separately installed Omnigent runtime. |
| [experiments/](../src/research_harness/experiments) | Snapshot benchmark packages, retain curator decisions, delegate work through Omnigent into Docker and collect independent scores and execution evidence. |
| [strategies/](../src/research_harness/strategies), [evaluation/](../src/research_harness/evaluation) and [optimization/](../src/research_harness/optimization) | Run configurable agent strategies, grade experiments and propose/test strategy changes. This experimental track is separate from ordinary ingestion. |

`agents/` contains agent instructions, `examples/` contains configurations and
walkthroughs, and `tests/` checks the implementation. `docs/archive/` contains
historical research outputs. A saved JSON result is not a feature or an add-on.

The existing research service is specialized in source discovery and ingestion.
The separate experiment path currently supports single-step Harbor CPU tasks;
broader investigation, planning and experiment search remain unfinished.
Polymarket/Kalshi support currently lives in the built-in configuration, connector,
record and collection code; there is no optional plugin loader yet.

## Collect and use data

- [Data pipelines](data-pipeline.md): discover sources, configure connectors,
  collect data, export records and replay saved responses.
- [Storage and registry](backend.md): local SQLite, shared Postgres/S3, and saved
  questions and pipeline versions.
- [Research service](research-service.md): domain operations and MCP tool contracts.

## Connect an agent

- [Omnigent walkthrough](../examples/omnigent/README.md): install the pinned
  runtime and try the integration with scripted model responses.
- [Integration guide](omnigent-integration.md): how runtime sessions bind to
  research operations.
- [Browser acceptance](omnigent-ui-acceptance.md): what the existing UI walkthrough
  demonstrated and which parts used synthetic responses.

## Evaluate research strategies

- [Research experiments](experiments.md): prepare a proposed experiment, run real
  Harbor benchmark controls, retain curator decisions and test delegated execution.
- [Development evaluation](research-evaluation.md): case requirements and scoring.
- [Controlled comparison](controlled-comparison.md): compare direct and Omnigent execution.
- [Strategy optimization](strategy-optimization.md): candidate code, development
  selection and isolated final evaluation.
- [Run budgets](pilot-budget.md): provider configuration, reservations and accounting.

These guides cover experimental infrastructure. Scripted tests do not establish
live model quality or optimization gains; those measurements remain unfinished.

## Understand the project direction

- [Current goal](goals/reproducible-research.md): one reproducible AI research
  experiment, starting with a bounded Meta-Harness pilot. Includes completion
  criteria, the next task and shared ownership.

Earlier domain proposals are deferred:

- [Analyst workflow examples](product-workflows.md), [analyst needs](analyst-needs.md)
  and [financial research design](research-design.md).
- [Archived prediction-market exploration](prediction-markets.md), September 2026.
- [Research sources](research-sources.md), [market landscape](market-landscape.md)
  and [analyst pilot](analyst-pilot.md).

These earlier documents describe possible applications, not the active roadmap
or implemented end-to-end workflows.

## Contribute or inspect the evidence

- [Agent/contributor instructions](../AGENTS.md) and [test setup](testing.md).
- [Integration plan](omnigent-integration-plan.md), [work tracker](goals/omnigent-integration.md)
  and [checkpoint follow-ups](remaining-work.md). These are internal development
  records rather than an onboarding sequence.
- [Historical checkpoint review](checkpoint-scope.md): the state at PR #1;
  later fixes are recorded in the tracker.
- [Evidence retention](evidence-retention.md) and [portable review copies](portable-evidence.md).
- [September 2026 archive guide](../examples/evaluation/evidence/historical-review-all-2026-09-12/README.md):
  download instructions, file inventory, checksums and reproduction details for
  the test-evidence archive on GitHub Releases.

The evidence archive is for reviewing past test runs. To run the current CLI,
install from the repository using the project README.
