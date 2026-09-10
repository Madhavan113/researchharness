"""Search providers return observations; the research service owns their receipts."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import Any, Protocol

import httpx
from pydantic import Field

from research_harness.config import StrictModel
from research_harness.util import http_url


class SearchProvider(Protocol):
    @property
    def name(self) -> str: ...

    def search(self, query: str, filters: dict[str, Any]) -> dict[str, Any]: ...


class SearchProviderError(ValueError):
    def __init__(self, message: str, provider_response: dict[str, Any]):
        super().__init__(message)
        self.provider_response = provider_response


class SearchFilters(StrictModel):
    site: str | None = Field(default=None, max_length=253)
    acquired_after: str | None = None
    acquired_before: str | None = None
    published_after: str | None = None
    published_before: str | None = None
    max_results: int = Field(default=6, ge=1, le=10)
    snippet_max_length: int = Field(default=1000, ge=180, le=5000)


def parse_search_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse the observed Keenable format without treating URLs in snippets as result headers."""
    if response.get("isError"):
        raise ValueError("Search provider returned a tool error")
    structured = response.get("structuredContent")
    if structured is not None:
        if not isinstance(structured, dict) or not isinstance(structured.get("results"), list):
            raise ValueError("Unsupported structured search response; update the provider adapter")
        results = []
        for item in structured["results"]:
            if not isinstance(item, dict):
                raise ValueError("Malformed search result")
            results.append({**item, "url": http_url(item["url"])})
        return results
    blocks = response.get("content", [])
    if any(block.get("type") != "text" for block in blocks):
        raise ValueError("Unsupported search content; expected text results")
    text = "\n\n".join(block["text"] for block in blocks).strip()
    if not text or text.lower() in {"no results", "no results found", "no results found."}:
        return []
    results = []
    for block in re.split(r"\n\s*---\s*\n", text):
        match = re.fullmatch(
            r"Title: ([^\n]*)\nURL: ([^\n]+)\nAcquired: ([^\n]*)\nSnippets:\n(.*)",
            block.strip(),
            re.DOTALL,
        )
        if not match:
            raise ValueError("Unsupported search response format; raw response requires review")
        results.append(
            {
                "title": match[1],
                "url": http_url(match[2]),
                "acquired_at": match[3],
                "snippet": match[4],
            }
        )
    return results


class McpSearchProvider:
    """Bounded Keenable-compatible Streamable HTTP MCP search."""

    def __init__(
        self, endpoint: str = "https://api.keenable.ai/mcp", *, timeout_seconds: float = 45
    ):
        self.endpoint = http_url(endpoint)
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("Search timeout must be between 1 and 120 seconds")
        self.timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return f"{self.endpoint}#search_web_pages"

    def search(self, query: str, filters: dict[str, Any]) -> dict[str, Any]:
        arguments = {
            "query": query,
            "mode": "realtime",
            **SearchFilters.model_validate(filters).model_dump(exclude_none=True),
        }
        return asyncio.run(self._search(arguments))

    async def _search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise RuntimeError("Install the MCP extra: uv sync --extra mcp") from exc
        async with asyncio.timeout(self.timeout_seconds):
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                async with streamable_http_client(self.endpoint, http_client=client) as (
                    read,
                    write,
                    _,
                ):
                    async with ClientSession(
                        read, write, read_timeout_seconds=timedelta(seconds=self.timeout_seconds)
                    ) as session:
                        initialized = await session.initialize()
                        result = await session.call_tool("search_web_pages", arguments)
                        raw = result.model_dump(mode="json", exclude_none=True)
        try:
            results = parse_search_response(raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise SearchProviderError(str(exc), raw) from exc
        return {
            "results": results,
            "provider_response": raw,
            "provider_server": initialized.serverInfo.model_dump(mode="json"),
            "provider_arguments": arguments,
            "provider_usage": None,
        }
