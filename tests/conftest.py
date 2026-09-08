from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research_harness.config import HttpSettings, PipelineSpec, SourceSpec
from research_harness.store import Store


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float = 60) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock):
    with Store(tmp_path / "data", clock=clock) as value:
        yield value


@pytest.fixture
def source() -> SourceSpec:
    return SourceSpec(
        id="policy",
        name="Policy data",
        connector="json",
        url="https://source.example/data",
        items_pointer="/items",
        id_pointer="/id",
        published_pointer="/published_at",
        required_pointers=["/title"],
    )


@pytest.fixture
def spec(source: SourceSpec) -> PipelineSpec:
    return PipelineSpec(
        name="test-pipeline",
        description="Synthetic test sources",
        sources=[source],
        http=HttpSettings(max_attempts=1, min_interval_seconds=0),
    )


@pytest.fixture
def item() -> dict:
    return {
        "id": "policy-1",
        "title": "A proposed trade rule",
        "published_at": "2020-01-01T00:00:00Z",
    }


@pytest.fixture
def market() -> dict:
    return {
        "id": "123",
        "question": "Will an agreement be announced?",
        "description": "An official announcement must occur before the specified deadline.",
        "slug": "test-agreement",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "endDate": "2026-10-01T00:00:00Z",
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["111", "222"]',
        "feesEnabled": True,
    }


@pytest.fixture
def rss() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Official policy feed</title>
<item><guid>policy-123</guid><title>Trade rule issued</title><link>https://source.example/policy/123</link>
<pubDate>Fri, 04 Sep 2026 12:30:00 GMT</pubDate><description><![CDATA[<p>An <strong>official</strong> update.</p>]]></description>
</item></channel></rss>"""
