# Policy feed

Find a public feed of official policy changes with stable ids and publication timestamps.

## Data requirements

- **policy** (required): Official policy changes

## Source assessment

### Policy feed

Status: **ready**. Covers: policy.

Monitor official changes

Freshness: Current sample

History: No historical claim

Access: Offline fixture

- [Source evidence 1](http://127.0.0.1:57378/policy.json)

Probe: `de20e554dd424845a5bbe5d17498a44a`. This is a sampled compatibility check.

- Fixture does not measure live source quality

## Coverage gaps

Every required need has a source that passed a sample probe. Semantic completeness still needs review.

## Execution

`pipeline.json`, when present, contains only sources backed by matching successful probes. Run it with `rh run pipeline.json`. Polling intervals are configuration; no background schedule is installed. The full ingestion run validates pagination and publishes each source atomically.
