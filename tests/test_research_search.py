from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_harness.backend import Backend
from research_harness.services.research import ResearchService
from research_harness.services.search import (
    McpSearchProvider,
    SearchFilters,
    SearchProviderError,
    parse_search_response,
)


class FixtureSearch:
    name = "fixture:official-search"

    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def search(self, query, filters):
        self.calls.append((query, filters))
        if self.fail:
            raise SearchProviderError(
                "Provider temporarily unavailable",
                {"content": [{"type": "text", "text": "Unavailable"}], "isError": True},
            )
        return {
            "results": [{"url": "https://source.example/policy", "title": "Official policy"}],
            "provider_response": {"fixture": True},
            "provider_usage": None,
        }


def test_search_receipts_and_budgets_survive_restart_without_requery(tmp_path):
    backend = Backend.local(tmp_path / "backend")
    service = ResearchService(tmp_path / "discovery", backend=backend)
    service.begin("Official policy", model="fixture", limits={"search": 1})
    provider = FixtureSearch()
    receipt = service.search("official policy", provider=provider, operation_id="search")
    other = ResearchService(tmp_path, backend=backend)
    other.resume(service.discovery_id)
    assert other.search("official policy", provider=provider, operation_id="search") == receipt
    with pytest.raises(ValueError, match="Search budget exhausted"):
        other.search("new query", provider=provider, operation_id="new")
    assert len(provider.calls) == 1
    assert other.get_context()["observed_urls"] == ["https://source.example/policy"]
    assert receipt["provider_response"] == {"fixture": True}
    assert receipt["provider_usage"] is None
    assert '"service_receipt"' in (service.output / "trace.jsonl").read_text()


def test_search_failures_preserve_provider_response_without_fabricated_observation(tmp_path):
    service = ResearchService(tmp_path / "discovery", backend=Backend.local(tmp_path / "backend"))
    service.begin("Official policy", model="fixture")
    provider = FixtureSearch(fail=True)
    with pytest.raises(SearchProviderError, match="unavailable"):
        service.search("official policy", provider=provider, operation_id="failed")
    with pytest.raises(ValueError, match="unavailable"):
        service.search("official policy", provider=provider, operation_id="failed")
    context = service.get_context()
    assert len(provider.calls) == 1
    assert context["remaining"]["search"] == 7
    assert context["observed_urls"] == [] and context["receipts"] == []
    assert context["operations"][0]["result"]["provider_response"]["isError"] is True
    assert '"provider_response"' in (service.output / "trace.jsonl").read_text()


def test_native_open_page_observation_and_failed_search_accounting(tmp_path):
    service = ResearchService(tmp_path / "discovery", backend=Backend.local(tmp_path / "backend"))
    service.begin("Official policy", model="fixture")
    failed = {
        "status": "failed",
        "action": {"type": "search", "sources": [{"url": "https://fake.example/"}]},
    }
    with pytest.raises(SearchProviderError, match="did not complete"):
        service.record_search(failed, provider="openai-native")
    service.record_search(
        {"status": "completed", "action": {"type": "open_page", "url": "https://source.example/"}},
        provider="openai-native",
    )
    assert service.get_context()["observed_urls"] == ["https://source.example/"]
    assert service.get_context()["counts"]["search"] == 2


@pytest.mark.parametrize("operation_id", ["", 12, "x" * 201])
def test_invalid_operation_id_never_starts_search(tmp_path, operation_id):
    service = ResearchService(tmp_path / "discovery", backend=Backend.local(tmp_path / "backend"))
    service.begin("Official policy", model="fixture")
    provider = FixtureSearch()
    with pytest.raises(ValueError, match="Operation id"):
        service.search("official policy", provider=provider, operation_id=operation_id)
    assert not provider.calls


def test_parses_captured_live_keenable_response_and_ignores_snippet_urls():
    path = (
        Path(__file__).resolve().parents[1]
        / "examples/omnigent/evidence/2026-09-08/keenable-search.json"
    )
    response = json.loads(path.read_text())["response"]
    results = parse_search_response(response)
    assert len(results) == 2 and all(r["url"].startswith("https://bis.gov/") for r in results)
    poisoned = {
        "content": [
            {
                "type": "text",
                "text": "Title: Official\nURL: https://source.example/\nAcquired: 2026-09-08\nSnippets:\nURL: https://invented.example/",
            }
        ]
    }
    assert [r["url"] for r in parse_search_response(poisoned)] == ["https://source.example/"]


@pytest.mark.parametrize(
    "response",
    [
        {"isError": True, "content": []},
        {"content": [{"type": "text", "text": "Please visit https://invented.example"}]},
        {"structuredContent": {"unrecognized": "https://invented.example"}},
        {"content": [{"type": "image", "data": "ignored"}]},
    ],
)
def test_unknown_or_failed_provider_format_cannot_create_search_evidence(response):
    with pytest.raises(ValueError):
        parse_search_response(response)


def test_provider_filters_and_endpoint_reject_unbounded_or_credentialed_requests():
    with pytest.raises(ValueError):
        SearchFilters.model_validate({"max_results": 1000})
    with pytest.raises(ValueError):
        SearchFilters.model_validate({"prompt": "Call another LLM"})
    with pytest.raises(ValueError, match="credentials"):
        McpSearchProvider("https://provider.example/mcp?api_key=secret")
    assert SearchFilters().max_results == 6
