# Shared backend: registry and storage

The harness has two storage modes selected by environment variables. Nothing else changes: the
same commands, connectors, publication rules, and exports work in both.

| Mode | Metadata | Raw response bodies | Registry | When |
| --- | --- | --- | --- | --- |
| Local (default) | SQLite file per data directory | `raw/` directory beside it | SQLite under `RH_LOCAL_ROOT` (default `.researchharness/registry`) | One machine, tests, offline replay |
| Shared | One Postgres schema for every pipeline | S3-compatible bucket, keys by SHA-256 | Same Postgres schema | Several agents or machines, durable history |

Supabase provides both halves of the shared mode: a Postgres database and Storage buckets with an
S3-compatible endpoint. Any other Postgres and S3 endpoint works the same way.

## Why a registry

Discovery produces one proposal per run, and alternative definitions for the same question pile
up across runs, models, and hand edits. The registry keeps every candidate with its origin and
history so agents can compare and choose:

| Table | Holds |
| --- | --- |
| `questions` | The research brief with a stable id and title; trimmed brief text is hashed to deduplicate repeated registration |
| `discoveries` | Each agent run for a question: model, status, output directory, token usage, error |
| `proposals` | The structured proposal and rendered Markdown a discovery produced, plus uncovered needs |
| `pipeline_versions` | Every candidate pipeline definition: full spec, fingerprint, `proposed`/`adopted`/`superseded`/`retired`, origin (`discovery` or `manual`), label, notes |
| `pipeline_events` | Status transitions with timestamps and notes |

A definition is identified by its fingerprint (SHA-256 of the canonical JSON), so registering the
same definition twice for one question returns the existing entry. Adopting a definition marks the
question's previously adopted one `superseded`. Registered runs link to definitions through the
question namespace, pipeline name, and configuration hash. Shared storage can join these directly;
the research context also reads each local pipeline store to report its latest run.

Discovery registers the question and its run before spending any model tokens, records the
proposal and compiled pipeline on completion, and marks the run failed on error. The ids appear in
`proposal.json` under `registry` and in the trace.

```sh
uv run rh questions add --brief examples/trade-policy.brief.md
uv run rh questions list
uv run rh questions show QUESTION_ID
uv run rh pipelines register examples/trade-policy.pipeline.json --question QUESTION_ID --label hand-made
uv run rh pipelines list --question QUESTION_ID
uv run rh pipelines adopt PIPELINE_ID --note "Best coverage of official notices"
uv run rh pipelines history PIPELINE_ID
uv run rh run PIPELINE_ID
uv run rh pipelines export PIPELINE_ID --out artifacts/adopted.pipeline.json
```

Ids may be abbreviated to any unique prefix of at least six characters. `run`, `status`, `export`,
`replay`, and `validate` accept a registered pipeline id in place of a file path.

Registered collections use `RH_LOCAL_ROOT/pipelines/QUESTION_ID/PIPELINE_NAME` locally, and
`QUESTION_ID/PIPELINE_NAME` as the internal dataset key in Postgres. Identical names in different
questions therefore do not mix observations, checkpoints, caches, or writer locks. The saved
configuration and its fingerprint retain the original pipeline name. File-based CLI workflows
continue using the configuration's data directory and unscoped name.

Older registered collections used only the pipeline name. Opening a registry does not guess
which question owns those rows. `validate`, `status`, and export results/manifests report detected
legacy data; normal registered reads use the new namespace. To inspect or export historical data,
use `rh status PIPELINE_ID --legacy-unscoped`, `rh export PIPELINE_ID --legacy-unscoped --out FILE`,
or the same flag with `replay`. This compatibility path does not collect new unscoped data and
cannot read modern namespaced runs through a legacy run-id lookup. Historical ownership may be
ambiguous when several questions used the same name; automatic migration is deliberately absent.

## Configuration

| Variable | Meaning |
| --- | --- |
| `RH_DATABASE_URL` | `postgresql://...` connection URL. Setting it selects the shared mode. |
| `RH_DATABASE_SCHEMA` | Postgres schema, default `research_harness`. Created if missing. |
| `RH_BLOB_BUCKET` | Bucket for raw response bodies. Required in shared mode. |
| `RH_BLOB_ENDPOINT` | S3-compatible endpoint URL. Omit only for AWS S3 itself. |
| `RH_BLOB_ACCESS_KEY`, `RH_BLOB_SECRET_KEY` | S3 credentials. |
| `RH_BLOB_REGION` | Signing region, default `us-east-1`. Supabase requires the project's region. |
| `RH_BLOB_PREFIX` | Key prefix inside the bucket, default `raw`. |
| `RH_LOCAL_ROOT` | Local mode root for the registry and for pipelines run by id, default `.researchharness`. |

`rh backend info` prints the effective configuration with secrets masked. `rh backend init`
connects, creates or upgrades the schema, and verifies the bucket, creating it when the endpoint
allows. `rh backend check` also reports registry counts and round-trips one small blob.

## Supabase setup

1. Create a project. In **Project Settings → Database**, copy the **Session pooler** connection
   string (port 5432). It is IPv4-compatible and supports the session-level advisory locks the
   writer uses; the transaction pooler on port 6543 does not. Put it in `RH_DATABASE_URL`.
2. In **Storage**, create a **private** bucket, for example `research-harness`. Put its name in
   `RH_BLOB_BUCKET`.
3. In **Project Settings → Storage → S3 Connection**, enable S3 access and create an access key.
   Set `RH_BLOB_ENDPOINT` to the shown endpoint (`https://PROJECT_REF.storage.supabase.co/storage/v1/s3`
   or the `.supabase.co/storage/v1/s3` form), `RH_BLOB_REGION` to the shown region, and the key
   pair in `RH_BLOB_ACCESS_KEY` and `RH_BLOB_SECRET_KEY`.
4. Run `uv run rh backend init`, then `uv run rh backend check`.

Tables live in the `research_harness` schema, not `public`, so Supabase's auto-generated REST API
does not expose them. Keep the connection string and S3 key out of the repository.

## Concurrency and integrity

- Each pipeline has a writer lock: an OS file lock per data directory in local mode, a Postgres
  session advisory lock keyed by the question-scoped pipeline name in shared mode. Agents can collect different
  pipelines at the same time; a second writer for the same pipeline fails immediately instead of
  waiting. Acquiring the exclusive lock marks that pipeline's abandoned `running` rows interrupted.
- Probes and inspections take a shared lock, so several discoveries can run concurrently, and
  they never publish records or advance checkpoints.
- Publication of a source snapshot is one transaction in both modes.
- Bodies are content-addressed. Uploads skip bodies already present; reads verify the digest.
- Timestamps are stored as UTC ISO-8601 text with microseconds in both databases, so cutoff
  comparisons behave identically.

## Testing

The suite runs against SQLite by default. Set `RH_TEST_DATABASE_URL` to run every store test
against Postgres as well. Each test creates and drops its own throwaway schema, so the Supabase
project itself is a fine target; the `research_harness` schema is never touched. Add
`RH_TEST_BLOB_BUCKET`, `RH_TEST_BLOB_ENDPOINT`, `RH_TEST_BLOB_ACCESS_KEY`, and
`RH_TEST_BLOB_SECRET_KEY` to store test bodies in the bucket under a per-test prefix that is
deleted afterwards:

```sh
RH_TEST_DATABASE_URL="$RH_DATABASE_URL" RH_TEST_BLOB_BUCKET="$RH_BLOB_BUCKET" \
RH_TEST_BLOB_ENDPOINT="$RH_BLOB_ENDPOINT" RH_TEST_BLOB_ACCESS_KEY="$RH_BLOB_ACCESS_KEY" \
RH_TEST_BLOB_SECRET_KEY="$RH_BLOB_SECRET_KEY" uv run pytest
```

## Schema changes

Schema version 2 adds the registry tables. Version 3 adds discovery contexts, an operation ledger,
and evidence receipts for the [shared research service](research-service.md). Version 4 adds
durable collection/export jobs, their preallocated collection run ids, and reconciliation state.
Numbered migrations
upgrade existing local databases in place on open; anything newer than the running code is
refused. Postgres records applied versions in `schema_migrations`. Future changes should add a
numbered migration rather than editing earlier definitions, and keep the SQLite and Postgres
shapes identical.

Limits that remain: no retention policy and no background scheduler.

## Local shared-storage acceptance

The [reproducible fixture](../examples/evaluation/shared_storage_fixture.py) uses already-present, digest-pinned PostgreSQL 17.11 and MinIO images, task-owned containers, loopback-only ports, temporary credentials and disposable schemas/buckets. It leaves unrelated Docker services untouched and removes its containers and volumes after saving evidence.

~~~sh
uv run --extra mcp python examples/evaluation/shared_storage_fixture.py \
  --out /tmp/research-shared-storage
~~~

The [September 8 acceptance](../examples/evaluation/evidence/shared-storage-2026-09-08/acceptance.json) records 125 passing selected tests, including 49 Postgres/S3 parameterizations. The workflow also used actual Backend clients and detached collection/export workers, restarted both storage services, reopened receipts/proposals through fresh clients, recovered an export from S3 after deleting the local file, checked exact-operation retries without more fetching, replayed without publication, and verified writer exclusion and question-scoped access. Final workflow acceptance reused the preceding passing test-suite evidence explicitly; the corrected workflow itself was rerun.

The [21-file archive/index](../examples/evaluation/evidence/shared-storage-2026-09-08/index.json) preserves the fixture program, full selected test output, research/job evidence and recovered export. Credentials and database volumes are excluded. This establishes local shared-storage behavior, not remote service configuration or a multi-user deployment.
