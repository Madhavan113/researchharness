# Documentation

Start with the [project README](../README.md) for an introduction and a runnable
example. This index groups the detailed guides by what you want to do.

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

- [Development evaluation](research-evaluation.md): case requirements and scoring.
- [Controlled comparison](controlled-comparison.md): compare direct and Omnigent execution.
- [Strategy optimization](strategy-optimization.md): candidate code, development
  selection and isolated final evaluation.
- [Run budgets](pilot-budget.md): provider configuration, reservations and accounting.

These guides cover experimental infrastructure. Scripted tests do not establish
live model quality or optimization gains; those measurements remain unfinished.

## Understand the project direction

- [Analyst workflow examples](product-workflows.md), [analyst needs](analyst-needs.md)
  and [financial research design](research-design.md).
- [Event and prediction-market research](prediction-markets.md).
- [Research sources](research-sources.md), [market landscape](market-landscape.md)
  and [analyst pilot](analyst-pilot.md).

These are design research and proposals, not implemented end-to-end workflows.
They describe possible applications of the shared data and execution foundation.

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
