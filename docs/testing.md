# Test configurations

The suite uses authored model/source fixtures and never needs provider credentials. Passing these checks verifies software behavior; reviewed benchmarks and live model evaluation remain separate milestones in the [shared tracker](goals/omnigent-integration.md).

## Ordinary local checks

~~~sh
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
~~~

The default pytest options include `-ra`, so the final summary lists skip reasons. Install the MCP extra to include protocol and process-integration checks: `uv run --locked --extra mcp pytest`. Without it, MCP-dependent tests report skipped modules or cases. Docker/Omnigent runtime tests also report skips when their environment variables are unset; a green ordinary run does not prove those checks ran. The session header identifies optional or required runtime mode.

## Required runtime checks

Install Omnigent in a separate checkout using the existing pinned setup script, and pull the pinned strategy image before running the suite:

~~~sh
sh examples/omnigent/setup.sh /tmp/researchharness-omnigent-be042b39
docker pull python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

RH_TEST_REQUIRE_RUNTIME=1 \
RH_TEST_STRATEGY_IMAGE=python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 \
RH_TEST_OMNIGENT_PYTHON=/tmp/researchharness-omnigent-be042b39/.venv/bin/python \
uv run --locked --extra mcp pytest --tb=short
~~~

Keep the virtual-environment executable path itself; resolving its symlink to the base Python interpreter loses the separate environment. Docker must be running, and the Omnigent checkout must match `be042b390e293a8d586cbb7e403a2ce0ce38fc62` without tracked edits. Tests verify those actual runtime controls; setting variables alone does not establish compatibility.

`RH_TEST_REQUIRE_RUNTIME=1` first requires both runtime variables, an executable Python path and the MCP dependency. Missing configuration or an invalid flag value fails before test execution. During collection and every execution phase, any skip becomes a failure with the original reason retained. This also catches new skip sites and module-level `importorskip` calls. Strict mode applies to all selected checks; selecting only a subset still proves only that subset. Leave the flag unset, or set it to `0`, for ordinary optional-runtime runs.

The [pytest-contract regressions](../tests/test_pytest_runtime_contract.py) launch isolated child suites against the actual repository configuration. Their small dependency stubs test configuration reporting and failure behavior only; the real Docker/Omnigent cases provide runtime evidence.

## Stable test inputs and guard coverage

Controller, pilot, private-final and leakage tests use the four-case
[lifecycle snapshot](../tests/fixtures/research-lifecycle/README.md). Its original
case/source hashes are retained. Editing the development benchmark therefore
does not silently change lifecycle expectations. Archive tests construct two
minimal cases themselves; benchmark and workbook-fixture tests still read the
current development package because they validate that package.

Shared authored observation-runner evidence lives in `tests/conftest.py`; it
never executes candidate Python. Most search-controller tests use a small
implementation freeze for speed. Dedicated regressions use the real fingerprint
scanner and verify that changed working or frozen implementation bytes prevent
execution, including files beyond the fixture's configuration module.

The gateway's late-chunk deadline test uses an injected clock and controlled
timers with a real HTTP connection. It captures a chunk before the absolute
deadline, then fires that deadline while the provider is silent and checks that
the connection is interrupted and partial evidence retained. Real-time waits
guard test deadlocks; they are not narrow assertions about scheduler timing.

Docker guard tests exercise wall-clock, output and memory failure separately.
The wall-clock case retains its one-second limit. Output/memory cases keep the
production default ten-second watchdog so it does not mask their intended
guard; both retain the 1,024-byte capture limit and 64-MB memory ceiling. They
assert bounded captures, removal/recovery, output truncation or the OOM exit
status as applicable. No production limits are changed. A Docker setup timeout
still fails verification and must be reported.

## Artifact verification performance fixture

The [verification fixture](../examples/evaluation/verification_cache_fixture.py)
measures eight repeated workspace listings over sixteen authored 2-MiB feedback
files and their private readable copy. It waits for metadata to settle, warms
verification, counts full artifact read opens and records implementation hashes.
Closing the workspace still performs fresh verification and removes the copy.

~~~sh
uv run --locked --extra mcp python examples/evaluation/verification_cache_fixture.py \
  --out /tmp/research-verification-fixture
~~~

Use a new output directory for each run. The September 12
[before/after reports](../examples/evaluation/evidence/verification-cache-2026-09-12/README.md)
record 256 full artifact opens (512 MiB) before memoization and zero afterward
for the measured repeated listings. Metadata scans remain. This warm filesystem
fixture does not measure live model quality, model cost or disk throughput.

The [memo tests](../tests/test_verification.py) and session/workspace/controller/
budget integration regressions check restored-mtime edits, replacement, directory
membership, links, changed controls, current ledger comparisons and missing
registered runs. Their eligibility clock is advanced to avoid sleeps; real
filesystem metadata and domain validators still run. Wall-clock speed is not a
test assertion. Required Docker/Omnigent checks remain separate from this fixture.

## Evidence export and retention checks

The [portable evidence tests](../tests/test_portable_evidence.py) exercise exact
original/exported hashes, source-bound transformation verification, configured
identity in binary files, JUnit hostname removal, escaped JSON/XML rendering,
links, nested archives, source changes and incomplete outputs. Rendering creates
inspection templates only. The [asset tests](../tests/test_evidence_assets.py)
verify deterministic packaging, every archived member and the Git byte policy.
Both use authored fixtures without provider access. The
[159-file historical archive check](../examples/evaluation/evidence/portable-review-2026-09-12/README.md)
separately verifies the tools against retained runtime evidence; it does not
establish a new model result or remote asset availability.

## Hosted CI

The [Tests workflow](../.github/workflows/tests.yml) runs on pushes and pull requests and supports manual dispatch. Both jobs use Ubuntu 24.04, Python 3.13.12, uv 0.11.8 and the locked MCP extra:

- **Ordinary tests and lint** runs Ruff and the suite with visible optional-runtime skips. It also enforces the [evidence retention policy](evidence-retention.md): new checkpoint Git byte limits and exact historical compressed-archive hashes.
- **Required Docker and Omnigent runtime tests** installs the frozen separate Omnigent environment, pulls the digest-pinned image and runs the whole suite with skips forbidden.

Checkout/setup actions are pinned to commit hashes, and the workflow uses read-only repository permissions without model secrets. A setup failure fails the runtime job; it cannot downgrade to an ordinary run. GitHub retains the command output with each run. JUnit reports are written to the runner's temporary directory and are not committed or published as runtime archives. CI results and exact local verification counts are recorded in the tracker when a checkpoint is completed.

Postgres/MinIO verification remains separately configured through `RH_TEST_DATABASE_URL` and `RH_TEST_BLOB_*`, as documented in [backend acceptance](backend.md#local-shared-storage-acceptance). The required runtime job exercises local SQLite/file storage; it does not claim shared-storage or real-provider acceptance.
