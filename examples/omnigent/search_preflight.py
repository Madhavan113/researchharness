"""Record the live public search tool schema and one optional bounded query.

This is separate from the offline compatibility check. It makes network requests
to the public keyless Keenable endpoint and does not read credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ENDPOINT = "https://api.keenable.ai/mcp"


async def run(out: Path, query: str | None) -> None:
    async with asyncio.timeout(30):
        async with streamablehttp_client(
            ENDPOINT, headers={"X-Keenable-Title": "Research Harness compatibility preflight"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                info = await session.initialize()
                tools = await session.list_tools()
                evidence = {
                    "observed_at": datetime.now(UTC).isoformat(),
                    "endpoint": ENDPOINT,
                    "server": info.model_dump(mode="json"),
                    "tools": tools.model_dump(mode="json"),
                }
                names = {tool.name for tool in tools.tools}
                assert {"search_web_pages", "fetch_page_content"} <= names
                if query:
                    arguments = {"query": query, "max_results": 2, "mode": "realtime"}
                    result = await session.call_tool("search_web_pages", arguments)
                    evidence["search"] = {
                        "tool": "search_web_pages",
                        "arguments": arguments,
                        "response": result.model_dump(mode="json"),
                    }
                    if result.isError:
                        raise RuntimeError(
                            "Search provider returned an error; no successful receipt recorded"
                        )
    out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": "passed", "schema": str(out), "query_executed": bool(query)}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--query", help="Optional public query, limited to two realtime results")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output already exists; choose a new evidence path")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(args.out, args.query))


if __name__ == "__main__":
    main()
