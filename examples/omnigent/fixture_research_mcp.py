"""Actual research MCP service with source/search I/O confined to offline fixtures.

Only the normal-runtime acceptance script launches this server. Production
bundles launch research_harness.integrations.omnigent mcp instead.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_harness.execution import DiscoverySettings
from research_harness.integrations.omnigent import bound_service
from research_harness.mcp.server import create_server


class FixtureSearch:
    name = "normal-omnigent-offline-fixture"

    def __init__(self, source: dict):
        self.source = source

    def search(self, query, filters):
        result = {"url": self.source["url"], "title": "Fixture policy feed"}
        return {
            "results": [result],
            "provider_response": {"structuredContent": {"results": [result]}, "isError": False},
            "provider_arguments": {"query": query, "filters": filters},
            "provider_usage": {"fixture_calls": 1},
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    service, runtime = bound_service(args.case)

    # Host-only fixture setting also reaches the real detached collection worker.
    # Production bundles retain the normal public-source restrictions.
    service.public_only = False
    controls = (
        DiscoverySettings.model_validate(runtime["discovery_settings"])
        if runtime.get("discovery_settings") is not None
        else None
    )
    create_server(
        service,
        search_provider=FixtureSearch(fixture["source"]),
        runtime=runtime,
        **(
            {
                "research_limits": controls.limits(),
                "research_deadline_seconds": controls.deadline_seconds,
            }
            if controls is not None
            else {}
        ),
    ).run(transport="stdio")


if __name__ == "__main__":
    main()
