from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from research_harness.blobs import LocalBlobs, S3Blobs
from research_harness.config import HttpSettings, PipelineSpec, SourceSpec
from research_harness.store import PostgresDialect, Store

# Set RH_TEST_DATABASE_URL (and optionally RH_TEST_BLOB_*) to run every store test against
# Postgres and an S3-compatible bucket as well as SQLite, each in a throwaway schema and key
# prefix; see docs/backend.md.
BACKENDS = ["sqlite"] + (["postgres"] if os.environ.get("RH_TEST_DATABASE_URL") else [])


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


def _test_blobs(tmp_path: Path, namespace: str):
    if not os.environ.get("RH_TEST_BLOB_BUCKET"):
        return LocalBlobs(tmp_path / "raw"), None
    from research_harness.backend import BackendSettings, s3_client

    settings = BackendSettings(
        database_url=os.environ["RH_TEST_DATABASE_URL"],
        blob_bucket=os.environ["RH_TEST_BLOB_BUCKET"],
        blob_endpoint=os.environ.get("RH_TEST_BLOB_ENDPOINT"),
        blob_access_key=os.environ["RH_TEST_BLOB_ACCESS_KEY"],
        blob_secret_key=os.environ["RH_TEST_BLOB_SECRET_KEY"],
        blob_region=os.environ.get("RH_TEST_BLOB_REGION", "us-east-1"),
    )
    client = s3_client(settings)
    return S3Blobs(client, settings.blob_bucket or "", prefix=f"tests/{namespace}"), client


@pytest.fixture(params=BACKENDS)
def store(request, tmp_path: Path, clock: Clock):
    if request.param == "sqlite":
        with Store(tmp_path / "data", clock=clock) as value:
            yield value
        return
    schema = f"test_{uuid4().hex[:12]}"
    blobs, client = _test_blobs(tmp_path, schema)
    dialect = PostgresDialect(os.environ["RH_TEST_DATABASE_URL"], schema)
    with Store(dialect=dialect, blobs=blobs, clock=clock) as value:
        try:
            yield value
        finally:
            value.execute(f'DROP SCHEMA "{schema}" CASCADE')
            if client is not None and isinstance(blobs, S3Blobs):
                keys = [
                    {"Key": item["Key"]}
                    for page in client.get_paginator("list_objects_v2").paginate(
                        Bucket=blobs.bucket, Prefix=f"{blobs.prefix}/"
                    )
                    for item in page.get("Contents") or []
                ]
                if keys:
                    client.delete_objects(Bucket=blobs.bucket, Delete={"Objects": keys})


@pytest.fixture
def tamper():
    """Overwrite a stored body in place, whichever blob backend is active."""

    def apply(store: Store, hashed: str, content: bytes) -> None:
        blobs = store.blobs
        if isinstance(blobs, LocalBlobs):
            blobs.path(hashed).write_bytes(content)
        else:
            blobs.client.put_object(Bucket=blobs.bucket, Key=blobs.key(hashed), Body=content)

    return apply


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
