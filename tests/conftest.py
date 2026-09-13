from __future__ import annotations

import importlib.util
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

# Runtime fixtures need RH_TEST_STRATEGY_IMAGE (a locally pulled digest-pinned Python image)
# and RH_TEST_OMNIGENT_PYTHON (the pinned checkout's separate .venv/bin/python), plus the mcp
# extra. RH_TEST_REQUIRE_RUNTIME=1 requires this configuration and rejects every skip;
# ordinary runs retain visible skip reasons. See docs/testing.md for local and CI commands.
REQUIRE_RUNTIME = pytest.StashKey[bool]()


def pytest_configure(config):
    value = os.environ.get("RH_TEST_REQUIRE_RUNTIME", "0")
    if value not in {"0", "1"}:
        raise pytest.UsageError("RH_TEST_REQUIRE_RUNTIME must be 0 or 1 (or unset)")
    config.stash[REQUIRE_RUNTIME] = value == "1"
    if value != "1":
        return
    missing = [
        name
        for name in ("RH_TEST_STRATEGY_IMAGE", "RH_TEST_OMNIGENT_PYTHON")
        if not os.environ.get(name, "").strip()
    ]
    if importlib.util.find_spec("mcp") is None:
        missing.append("MCP dependency (install with uv sync --locked --extra mcp)")
    if missing:
        raise pytest.UsageError("Required runtime configuration is missing: " + ", ".join(missing))
    python = Path(os.environ["RH_TEST_OMNIGENT_PYTHON"]).expanduser()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise pytest.UsageError("RH_TEST_OMNIGENT_PYTHON must name an executable Python file")


def pytest_report_header(config):
    if config.stash.get(REQUIRE_RUNTIME, False):
        return "Runtime checks: required; all skips fail (RH_TEST_REQUIRE_RUNTIME=1)"
    return "Runtime checks: optional; skip reasons are reported (set RH_TEST_REQUIRE_RUNTIME=1 to require them)"


def _require_executed(config, report):
    if config.stash.get(REQUIRE_RUNTIME, False) and report.skipped:
        report.outcome = "failed"
        report.longrepr = (
            f"{report.nodeid}: RH_TEST_REQUIRE_RUNTIME=1 forbids skipped checks.\n"
            f"Original skip: {report.longrepr}"
        )
        # A skipped xfail must not retain a status that masks the strict failure.
        if hasattr(report, "wasxfail"):
            del report.wasxfail


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_make_collect_report(collector):
    report = yield
    _require_executed(collector.config, report)
    return report


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    report = yield
    _require_executed(item.config, report)
    return report


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


@pytest.fixture
def fake_runner(monkeypatch):
    """Authored evidence for host journal tests; never executes candidate Python."""

    import json

    import research_harness.strategies.session as session_module
    from research_harness.util import canonical_json, digest, write_json

    class FakeRunner:
        calls = 0
        invalid = False

        def __init__(self, config):
            self.config = config

        def execute(self, source, event, output):
            type(self).calls += 1
            inputs = output / "input"
            inputs.mkdir(parents=True)
            (inputs / "strategy.py").write_bytes(source.read_bytes())
            (inputs / "request.json").write_text(canonical_json(event))
            (inputs / "worker.py").write_text("# authored fixture worker evidence\n")
            decision = {"order": [item["id"] for item in reversed(event["payload"]["items"])]}
            if self.invalid:
                decision["order"] = ["invented-result"]
            raw = {"decision": decision, "state": {"calls": event["state"].get("calls", 0) + 1}}
            write_json(output / "decision.json", raw)
            (output / "stdout.txt").write_text(canonical_json(raw))
            (output / "stderr.txt").write_text("")
            write_json(
                output / "execution.json",
                {
                    "status": "completed",
                    "cleanup": "removed",
                    "configuration": self.config.model_dump(mode="json"),
                    "source_sha256": digest(source.read_bytes()),
                    "request_sha256": digest(canonical_json(event)),
                    "output_truncated": False,
                    "output_collection_complete": True,
                    "artifact_hashes": {
                        p.relative_to(output).as_posix(): digest(p.read_bytes())
                        for p in sorted(output.rglob("*"))
                        if p.is_file()
                    },
                },
            )
            return raw

        def recover(self, output):
            return json.loads((output / "execution.json").read_bytes())

    monkeypatch.setattr(session_module, "DockerStrategyRunner", FakeRunner)
    return FakeRunner
