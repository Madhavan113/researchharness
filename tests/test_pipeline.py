from __future__ import annotations

import json

import httpx
import pytest

from research_harness.config import Pagination
from research_harness.engine import export_dataset, probe_source, replay_run, run_pipeline
from research_harness.util import timestamp


def test_idempotent_versions_and_conditional_reobservation(spec, store, clock, item):
    calls = []

    def handler(request):
        calls.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json={"items": [item]}, headers={"etag": '"v1"'})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = run_pipeline(spec, store, client=client, public_only=False, clock=clock)
        first_time = timestamp(clock())
        clock.advance()
        second = run_pipeline(spec, store, client=client, public_only=False, clock=clock)
    assert first["status"] == second["status"] == "succeeded"
    assert first["sources"][0]["new_versions"] == 1
    assert second["sources"][0]["new_versions"] == 0
    assert store.db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
    assert len(list((store.root / "raw").rglob("?" * 64))) == 1
    record = store.as_of(spec.name, timestamp(clock()))[0]
    assert record["first_observed_at"] == first_time
    assert record["observed_at"] == timestamp(clock())
    captures = store.captures_for_run(second["run_id"])
    assert captures[0].reused_capture_id is not None
    assert len(calls) == 2


def test_asof_selects_revisions_and_reversions_without_publication_leakage(
    spec, store, clock, item, tmp_path
):
    value = item.copy()
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [value]}))
    ) as client:
        run_pipeline(spec, store, client=client, public_only=False, clock=clock)
        first = timestamp(clock())
        clock.advance()
        value["title"] = "Revised rule"
        run_pipeline(spec, store, client=client, public_only=False, clock=clock)
        second = timestamp(clock())
        clock.advance()
        value["title"] = item["title"]
        run_pipeline(spec, store, client=client, public_only=False, clock=clock)
    assert store.as_of(spec.name, "2025-01-01T00:00:00.000000Z") == []
    assert store.as_of(spec.name, first)[0]["data"]["title"] == item["title"]
    assert store.as_of(spec.name, second)[0]["data"]["title"] == "Revised rule"
    assert store.as_of(spec.name, timestamp(clock()))[0]["data"]["title"] == item["title"]
    assert store.db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 2
    output = tmp_path / "records.jsonl"
    export = export_dataset(spec, store, clock(), output)
    again = export_dataset(spec, store, clock(), tmp_path / "again.jsonl")
    assert export["data_sha256"] == again["data_sha256"]
    assert json.loads(output.read_text())["published_at"].startswith("2020")


def test_later_page_failure_does_not_publish_or_advance_state(spec, store, clock, item):
    source = spec.sources[0].model_copy(
        update={
            "pagination": Pagination(mode="cursor", next_pointer="/next", cursor_parameter="cursor")
        }
    )
    spec = spec.model_copy(update={"sources": [source]})
    broken = False

    def handler(request):
        if request.url.params.get("cursor"):
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "items": [{**item, "title": "Changed" if broken else item["title"]}],
                "next": "page2" if broken else None,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        run_pipeline(spec, store, client=client, public_only=False, clock=clock)
        state = store.state(spec.name, source.id)
        clock.advance()
        broken = True
        failed = run_pipeline(spec, store, client=client, public_only=False, clock=clock)
    assert failed["status"] == "failed"
    assert store.state(spec.name, source.id) == state
    assert store.as_of(spec.name, timestamp(clock()))[0]["data"]["title"] == item["title"]
    assert len(store.captures_for_run(failed["run_id"])) == 2


def test_malformed_record_quarantines_and_preserves_raw_for_replay(spec, store, clock, item):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"items": [item, {"id": "bad"}]})
        )
    ) as client:
        result = run_pipeline(spec, store, client=client, public_only=False, clock=clock)
    assert result["sources"][0]["status"] == "degraded"
    assert store.as_of(spec.name, timestamp(clock())) == []
    assert len(store.quarantine_for_run(result["run_id"])) == 1
    replay = replay_run(store, result["run_id"])
    assert len(replay["records"]) == 1
    assert len(replay["issues"]) == 1
    assert replay["network_requests"] == 0
    assert store.state(spec.name, "policy") is None


@pytest.mark.parametrize("mode", ["loop", "limit", "missing", "cross_origin"])
def test_incomplete_or_unsafe_pagination_is_not_published(mode, spec, store, clock, item):
    pagination = (
        Pagination(mode="next_url", next_pointer="/next")
        if mode == "cross_origin"
        else Pagination(mode="cursor", next_pointer="/next", cursor_parameter="cursor")
    )
    source = spec.sources[0].model_copy(
        update={"pagination": pagination, "max_pages": 1 if mode == "limit" else 5}
    )
    spec = spec.model_copy(update={"sources": [source]})
    response = {"items": [item]}
    if mode != "missing":
        response["next"] = "https://other.example/data" if mode == "cross_origin" else "repeated"
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        result = run_pipeline(spec, store, client=client, public_only=False, clock=clock)
    assert result["status"] == "failed"
    assert store.as_of(spec.name, timestamp(clock())) == []
    assert store.state(spec.name, source.id) is None


def test_one_failed_source_does_not_block_other_sources(spec, store, clock, item):
    broken = spec.sources[0].model_copy(
        update={"id": "broken", "url": "https://source.example/broken"}
    )
    spec = spec.model_copy(update={"sources": [broken, spec.sources[0]]})
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(404)
                if request.url.path == "/broken"
                else httpx.Response(200, json={"items": [item]})
            )
        )
    ) as client:
        result = run_pipeline(spec, store, client=client, public_only=False, clock=clock)
    assert result["status"] == "failed"
    assert len(store.as_of(spec.name, timestamp(clock()))) == 1
    assert result["sources"][1]["status"] == "succeeded"


def test_due_only_skips_without_network_and_config_change_forces_run(spec, store, clock, item):
    calls = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200, json={"items": [item]})
        )
    ) as client:
        run_pipeline(spec, store, client=client, public_only=False, clock=clock)
        skipped = run_pipeline(
            spec, store, client=client, public_only=False, clock=clock, due_only=True
        )
        updated = spec.model_copy(
            update={"sources": [spec.sources[0].model_copy(update={"name": "Changed"})]}
        )
        run_pipeline(updated, store, client=client, public_only=False, clock=clock, due_only=True)
    assert skipped["sources"][0]["status"] == "not_due"
    assert len(calls) == 2


def test_sample_probe_never_publishes_or_advances_ingestion_state(source, store, clock, item):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        report = probe_source(source, store, client=client, public_only=False, clock=clock)
    assert report["status"] == "verified_sample"
    assert report["source_fingerprint"] == source.fingerprint()
    assert store.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert store.state("source-probes", source.id) is None


def test_blob_tampering_is_detected_during_replay(spec, store, clock, item):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        run = run_pipeline(spec, store, client=client, public_only=False, clock=clock)
    hashed = store.captures_for_run(run["run_id"])[0].body_hash
    (store.root / "raw" / hashed[:2] / hashed).write_bytes(b"tampered")
    replay = replay_run(store, run["run_id"])
    assert replay["issues"]
    assert not replay["records"]


def test_advertised_pagination_cannot_be_silently_ignored(spec, store, clock, item):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"items": [item], "next_page_url": "https://source.example/page2"}
            )
        )
    ) as client:
        result = run_pipeline(spec, store, client=client, public_only=False, clock=clock)
    assert result["status"] == "failed"
    assert "configure pagination" in result["sources"][0]["error"]
    assert store.as_of(spec.name, timestamp(clock())) == []


def test_declared_terminal_page_convention_allows_missing_next(spec, store, clock, item):
    source = spec.sources[0].model_copy(
        update={
            "pagination": Pagination(
                mode="next_url", next_pointer="/next_page_url", stop_when_missing=True
            )
        }
    )
    spec = spec.model_copy(update={"sources": [source]})
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        result = run_pipeline(spec, store, client=client, public_only=False, clock=clock)
    assert result["status"] == "succeeded"


def test_collection_time_is_not_backdated_before_atomic_publication(spec, store, clock, item):
    source = spec.sources[0].model_copy(
        update={
            "pagination": Pagination(mode="cursor", next_pointer="/next", cursor_parameter="cursor")
        }
    )
    spec = spec.model_copy(update={"sources": [source]})
    first_capture_time = timestamp(clock())

    def handler(request):
        if request.url.params.get("cursor"):
            clock.advance()
            return httpx.Response(200, json={"items": [], "next": None})
        return httpx.Response(200, json={"items": [item], "next": "second"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        run_pipeline(spec, store, client=client, public_only=False, clock=clock)
    assert store.as_of(spec.name, first_capture_time) == []
    current = store.as_of(spec.name, timestamp(clock()))[0]
    assert current["observed_at"] == first_capture_time
    assert current["available_at"] == timestamp(clock())
    assert current["lineage"]["source_config_hash"] == source.fingerprint()


def test_export_uses_the_declared_source_universe(spec, store, clock, item, tmp_path):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        run_pipeline(spec, store, client=client, public_only=False, clock=clock)
    renamed = spec.model_copy(
        update={"sources": [spec.sources[0].model_copy(update={"id": "different-dataset"})]}
    )
    result = export_dataset(renamed, store, clock(), tmp_path / "new-scope.jsonl")
    assert result["record_count"] == 0
    assert len(store.as_of(spec.name, timestamp(clock()))) == 1
