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

## Hosted CI

The [Tests workflow](../.github/workflows/tests.yml) runs on pushes and pull requests and supports manual dispatch. Both jobs use Ubuntu 24.04, Python 3.13.12, uv 0.11.8 and the locked MCP extra:

- **Ordinary tests and lint** runs Ruff and the suite with visible optional-runtime skips.
- **Required Docker and Omnigent runtime tests** installs the frozen separate Omnigent environment, pulls the digest-pinned image and runs the whole suite with skips forbidden.

Checkout/setup actions are pinned to commit hashes, and the workflow uses read-only repository permissions without model secrets. A setup failure fails the runtime job; it cannot downgrade to an ordinary run. GitHub retains the command output with each run. JUnit reports are written to the runner's temporary directory and are not committed or published as runtime archives. CI results and exact local verification counts are recorded in the tracker when a checkpoint is completed.

Postgres/MinIO verification remains separately configured through `RH_TEST_DATABASE_URL` and `RH_TEST_BLOB_*`, as documented in [backend acceptance](backend.md#local-shared-storage-acceptance). The required runtime job exercises local SQLite/file storage; it does not claim shared-storage or real-provider acceptance.
