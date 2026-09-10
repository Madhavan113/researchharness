"""Discovery source I/O confined to the benchmark's authored source bytes.

The subprocess receives only a bound runtime case and source/search recordings.
It does not load case specifications, source selections, or evaluator predicates.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from research_harness.evaluation.fixtures import FixtureProvider
from research_harness.execution import DiscoverySettings
from research_harness.integrations.omnigent import (
    PRIMARY_SESSION_ENV,
    bound_service,
    case_strategy_session,
)
from research_harness.services.jobs import JobService
from research_harness.util import canonical_json, digest


class FixtureSources:
    """One source recording, shared by the direct and MCP comparison adapters."""

    def __init__(self, path: Path):
        self.raw = path.read_bytes()
        self.sha256 = digest(self.raw)
        fixture = json.loads(self.raw)
        if not isinstance(fixture, dict) or not isinstance(fixture.get("search_results"), list):
            raise ValueError("Fixture requires authored search_results")
        if not isinstance(fixture.get("responses"), list):
            raise ValueError("Fixture requires authored responses")
        self.provider = FixtureProvider(fixture["search_results"])
        self.responses = {}
        for response in fixture["responses"]:
            url = str(httpx.URL(response["url"]))
            if url in self.responses:
                raise ValueError("Fixture response URLs must be unique")
            body = response["body"]
            self.responses[url] = (
                response.get("status", 200),
                response.get("headers", {}),
                (body if isinstance(body, str) else canonical_json(body)).encode("utf-8"),
            )

    def handle(self, request: httpx.Request) -> httpx.Response:
        response = self.responses.get(str(request.url))
        if request.method != "GET" or response is None:
            raise RuntimeError("No authored fixture for requested URL; network access is disabled")
        status, headers, body = response
        return httpx.Response(status, headers=headers, content=body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle), trust_env=False)


class DiscoveryOnlyJobs(JobService):
    """Keep unexpected workflow calls from spawning workers outside fixture I/O."""

    def start_collection(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("Collection is unavailable in a discovery-only comparison")

    def export_observations(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("Export is unavailable in a discovery-only comparison")


@contextmanager
def fixture_server(case_path: Path, fixture_path: Path, *, env: dict[str, str] | None = None):
    from research_harness.mcp.server import create_server

    fixtures = FixtureSources(fixture_path)
    host = os.environ if env is None else env
    # Only the case's backend root is authoritative. Parent RH_* credentials
    # must not select a shared database or blob store for this fixture run.
    identity = {key: host[key] for key in (PRIMARY_SESSION_ENV, "RUNNER_SERVER_URL") if key in host}
    service, runtime = bound_service(case_path, env=identity)
    if runtime.get("discovery_settings") is None:
        raise ValueError("Fixture comparisons require explicit shared discovery settings")
    controls = DiscoverySettings.model_validate(runtime["discovery_settings"])
    runtime.update(
        search={"mode": "wrapped", "provider": fixtures.provider.name},
        fixture_sha256=fixtures.sha256,
        source_mode="authored-fixtures",
        external_source_network=False,
        discovery_only=True,
        instructions_sha256=runtime["strategy_sha256"],
    )
    with fixtures.client() as client:
        service.http_client = client
        service.public_only = False  # MockTransport performs no DNS or external I/O.
        yield create_server(
            service,
            search_provider=fixtures.provider,
            runtime=runtime,
            jobs=DiscoveryOnlyJobs(service, auto_launch=False),
            research_limits=controls.limits(),
            research_deadline_seconds=controls.deadline_seconds,
            strategy=case_strategy_session(case_path),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    with fixture_server(args.case, args.fixture) as server:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
