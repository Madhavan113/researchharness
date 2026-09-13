from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest

from research_harness import registry
from research_harness.backend import Backend, BackendSettings
from research_harness.discovery_models import Candidate, DataNeed, ProposalDraft
from research_harness.services.research import ResearchService
from research_harness.store import MIGRATIONS, SCHEMA_VERSION, SqliteDialect, Store
from research_harness.util import canonical_json, digest, timestamp


@pytest.fixture
def service_backend(store, tmp_path, clock):
    if store.mode == "sqlite":
        return Backend.local(tmp_path / "backend", clock=clock)
    return Backend(
        BackendSettings(
            local_root=tmp_path / "backend",
            database_url=store.dialect.url,
            schema=store.dialect.schema,
            blob_bucket="injected-test-blobs",
            blob_access_key="injected",
            blob_secret_key="injected",
        ),
        blobs=store.blobs,
        clock=clock,
    )


def make_service(tmp_path, backend, client=None, *, name="discovery", limits=None):
    service = ResearchService(
        tmp_path / name, backend=backend, http_client=client, public_only=False
    )
    service.begin("Collect official policy evidence", model="fixture-model", limits=limits)
    return service


def search(service, source):
    return service.record_search(
        {
            "type": "web_search_call",
            "status": "completed",
            "action": {"query": "official policy data", "sources": [{"url": source.url}]},
        },
        provider="fixture-native",
        operation_id="search-1",
    )


def proposal(source, probe_id):
    return ProposalDraft(
        name="policy-monitor",
        title="Policy monitor",
        research_question="Collect official policy evidence",
        needs=[DataNeed(id="policy", description="Official policy changes", required=True)],
        candidates=[
            Candidate(
                name="Official data",
                purpose="Monitor official policy",
                covers=["policy"],
                status="ready",
                evidence_urls=[source.url],
                source=source,
                probe_id=probe_id,
                freshness_assessment="Hourly",
                historical_coverage="Current response only",
                access_notes="Public fixture",
                limitations=["Sample only"],
            )
        ],
        open_questions=[],
    )


def test_restart_preserves_evidence_budgets_and_idempotent_submission(
    tmp_path, service_backend, source, item
):
    fetched = []

    def handler(request):
        fetched.append(str(request.url))
        return httpx.Response(200, json={"items": [item]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = make_service(tmp_path, service_backend, client, limits={"probe": 1})
        search_receipt = search(service, source)
        inspected = service.inspect(source.url, operation_id="inspect-1")
        proof = service.probe(source, operation_id="probe-1")
        resumed = ResearchService(
            tmp_path / "wrong-output",
            backend=service_backend,
            http_client=client,
            public_only=False,
        )
        resumed.resume(service.discovery_id)
        assert resumed.output == service.output
        context = resumed.get_context()
        assert context["probes"][proof["probe_id"]] == proof
        assert context["observed_urls"] == [source.url]
        assert context["remaining"] == {"search": 7, "inspection": 15, "probe": 0}
        assert resumed.probe(source, operation_id="probe-1") == proof
        assert len(fetched) == 2
        with pytest.raises(ValueError, match="Probe budget exhausted"):
            resumed.probe(source, operation_id="probe-2")
        text = resumed.get_evidence(inspected["receipt_id"], limit=12)
        assert text["next_offset"] == 12
        continuation = resumed.get_evidence(inspected["receipt_id"], offset=12)
        assert json.loads(text["content"] + continuation["content"]) == {"items": [item]}
        assert resumed.get_evidence(search_receipt["receipt_id"])["kind"] == "search"
        answer = proposal(source, proof["probe_id"])
        result = resumed.submit_proposal(answer, operation_id="proposal-1")
        (service.output / "proposal.json").unlink()
        assert resumed.submit_proposal(answer, operation_id="proposal-1") == result
        assert (service.output / "proposal.json").is_file()
        with service_backend.open_registry() as store:
            assert store.count("proposals") == store.count("pipeline_versions") == 1
            saved = json.loads(
                store.query_one("SELECT proposal_json FROM proposals")["proposal_json"]
            )
            assert saved["registry"] == result["registry"]
        assert len(json.loads((service.output / "receipts.json").read_text())) == 3
        with service.evidence_store() as store:
            assert store.count("observations") == 0  # Discovery must never publish collection data.


def test_operation_id_rejects_changed_payload_and_new_ids_fetch_fresh_data(
    tmp_path, service_backend, source, item
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"items": [item]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = make_service(tmp_path, service_backend, client)
        first = service.probe(source, operation_id="same")
        changed = source.model_copy(update={"items_pointer": "/elsewhere"})
        with pytest.raises(ValueError, match="different arguments"):
            service.probe(changed, operation_id="same")
        with pytest.raises(ValueError, match="different arguments"):
            service.inspect(source.url, operation_id="same")
        second = service.probe(source, operation_id="fresh")
        assert first["probe_id"] != second["probe_id"] and len(calls) == 2


def test_other_discovery_probe_and_receipt_cannot_prove_this_proposal(
    tmp_path, service_backend, source, item
):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        first = make_service(tmp_path, service_backend, client, name="first")
        proof = first.probe(source)
        other = make_service(tmp_path, service_backend, client, name="other")
        search(other, source)
        with pytest.raises(ValueError, match="successful probe"):
            other.submit_proposal(proposal(source, proof["probe_id"]), operation_id="bad")
        with pytest.raises(ValueError, match="does not belong"):
            other.get_evidence(proof["receipt_id"])
        assert other.get_context()["status"] == "running"
        assert other.get_context()["operations"][-1]["status"] in {"completed", "failed"}
        with service_backend.open_registry() as store:
            assert store.count("proposals") == store.count("pipeline_versions") == 0


def test_changed_configuration_and_mixed_unobserved_citations_are_rejected(
    tmp_path, service_backend, source, item
):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        service = make_service(tmp_path, service_backend, client)
        search(service, source)
        proof = service.probe(source)
        changed = source.model_copy(update={"poll_interval_seconds": 7200})
        with pytest.raises(ValueError, match="changed after probing"):
            service.submit_proposal(proposal(changed, proof["probe_id"]))
        answer = proposal(source, proof["probe_id"])
        answer.candidates[0].evidence_urls.append("https://invented.example/fake")
        with pytest.raises(ValueError, match="actually observed"):
            service.submit_proposal(answer)


def test_failed_probes_count_against_durable_budget(tmp_path, service_backend, source):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": []}))
    ) as client:
        service = make_service(tmp_path, service_backend, client, limits={"probe": 1})
        proof = service.probe(source, operation_id="empty")
        assert proof["status"] == "failed" and proof["record_count"] == 0
        resumed = ResearchService(tmp_path, backend=service_backend)
        resumed.resume(service.discovery_id)
        assert resumed.probe(source, operation_id="empty") == proof
        with pytest.raises(ValueError, match="Probe budget exhausted"):
            resumed.probe(source)


def test_failed_inspection_is_cached_and_does_not_repeat_network(tmp_path, service_backend, source):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = make_service(tmp_path, service_backend, client, limits={"inspection": 1})
        with pytest.raises(Exception, match="404"):
            service.inspect(source.url, operation_id="missing")
        with pytest.raises(ValueError, match="404"):
            service.inspect(source.url, operation_id="missing")
        assert len(calls) == 1 and service.get_context()["remaining"]["inspection"] == 0
        assert service.get_context()["observed_urls"] == []


@pytest.mark.parametrize("seeded", [False, True])
@pytest.mark.parametrize("status", [200, 404])
def test_inspection_never_creates_or_changes_collection_checkpoint(
    tmp_path, service_backend, source, seeded, status
):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json={"items": []}))
    ) as client:
        service = make_service(tmp_path, service_backend, client)
        with service.evidence_store() as evidence:
            if seeded:
                with evidence.transaction():
                    evidence.execute(
                        "INSERT INTO source_state (pipeline,source_id,config_hash,last_success_at,checkpoint_json) VALUES (?,?,?,?,?)",
                        (
                            "discovery-inspection",
                            "inspection",
                            "saved-config",
                            timestamp(),
                            '{"cursor":"saved"}',
                        ),
                    )
            before = evidence.state("discovery-inspection", "inspection")
        if status == 404:
            with pytest.raises(Exception, match="404"):
                service.inspect(source.url, operation_id="inspection")
        else:
            assert service.inspect(source.url, operation_id="inspection")["status_code"] == 200
        with service.evidence_store() as evidence:
            assert evidence.state("discovery-inspection", "inspection") == before
            assert evidence.count("versions") == evidence.count("observations") == 0
            assert evidence.count("captures") == 1
            source_run = evidence.query_one("SELECT status FROM source_runs")
            assert source_run["status"] == ("probed" if status == 200 else "probe_failed")


def test_saved_operations_remain_readable_after_deadline_but_new_work_stops(
    tmp_path, service_backend, source, item, clock
):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        service = make_service(tmp_path, service_backend, client)
        proof = service.probe(source, operation_id="before")
        clock.advance(601)
        assert service.probe(source, operation_id="before") == proof
        assert service.get_evidence(proof["receipt_id"])["content"]
        with pytest.raises(ValueError, match="deadline exhausted"):
            service.probe(source, operation_id="after")


def test_proposal_and_pipeline_rollback_together_and_can_be_repaired(
    tmp_path, service_backend, source, item, monkeypatch
):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        service = make_service(tmp_path, service_backend, client)
        search(service, source)
        proof = service.probe(source)
        answer = proposal(source, proof["probe_id"])
        real = registry.register_pipeline

        def fail_after_save(*args, **kwargs):
            real(*args, **kwargs)
            raise RuntimeError("simulated save failure")

        with monkeypatch.context() as patch:
            patch.setattr(registry, "register_pipeline", fail_after_save)
            with pytest.raises(RuntimeError, match="simulated save failure"):
                service.submit_proposal(answer, operation_id="failed-save")
        with service_backend.open_registry() as store:
            assert store.count("proposals") == store.count("pipeline_versions") == 0
            assert store.count("pipeline_events") == 0
        assert service.get_context()["status"] == "running"
        result = service.submit_proposal(answer, operation_id="repair-save")
        assert result["status"] == "proposed"


def test_interrupted_operation_is_not_reexecuted_under_same_id(tmp_path, service_backend, source):
    service = make_service(tmp_path, service_backend)
    request = {"url": source.url}
    with service_backend.open_registry() as store, store.transaction():
        store.execute(
            """INSERT INTO discovery_operations
               (discovery_id, operation_id, kind, request_hash, request_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'running', ?)""",
            (
                service.discovery_id,
                "interrupted",
                "inspection",
                digest(canonical_json({"kind": "inspection", "request": request})),
                canonical_json(request),
                timestamp(store.clock()),
            ),
        )
    with pytest.raises(ValueError, match="interrupted"):
        service.inspect(source.url, operation_id="interrupted")
    assert service.get_context()["operations"][0]["status"] == "interrupted"


def test_concurrent_duplicate_cannot_execute_while_first_owner_is_running(
    tmp_path, service_backend, source, item
):
    entered, release = Event(), Event()
    calls = []

    def handler(request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json={"items": [item]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = make_service(tmp_path, service_backend, client)
        second = ResearchService(tmp_path, backend=service_backend, http_client=client)
        second.resume(first.discovery_id)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(first.probe, source, operation_id="concurrent")
            try:
                assert entered.wait(5)
                with pytest.raises(RuntimeError, match="writer"):
                    second.probe(source, operation_id="concurrent")
            finally:
                release.set()
            result = future.result(timeout=5)
        assert second.probe(source, operation_id="concurrent") == result
        assert len(calls) == 1


def test_existing_output_and_changed_brief_do_not_overwrite_case(tmp_path, service_backend):
    service = make_service(tmp_path, service_backend)
    another = ResearchService(service.output, backend=service_backend)
    with pytest.raises(ValueError, match="output directory belongs"):
        another.begin("Different brief", model="fixture")
    with service_backend.open_registry() as store:
        assert store.count("questions") == 1
    third = ResearchService(tmp_path / "third", backend=service_backend)
    with pytest.raises(ValueError, match="Changed brief"):
        third.begin(
            "Different brief", model="fixture", question_id=service.get_context()["question_id"]
        )


def test_unrelated_discovery_can_work_while_another_waits_for_http(
    tmp_path, service_backend, source, item
):
    entered, release = Event(), Event()

    def slow(_request):
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json={"items": [item]})

    with (
        httpx.Client(transport=httpx.MockTransport(slow)) as first_http,
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
        ) as second_http,
    ):
        first = make_service(tmp_path, service_backend, first_http, name="slow")
        second = make_service(tmp_path, service_backend, second_http, name="independent")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(first.probe, source)
            try:
                assert entered.wait(5)
                assert second.inspect(source.url)["status_code"] == 200
            finally:
                release.set()
            assert future.result(timeout=5)["status"] == "verified_sample"


def test_sqlite_v2_migration_preserves_registry_and_is_repeatable(tmp_path):
    dialect = SqliteDialect(tmp_path)
    db = sqlite3.connect(dialect.location)
    for version in (1, 2):
        for statement in MIGRATIONS[version]:
            db.execute(dialect.render(statement))
    db.execute("PRAGMA user_version=2")
    db.execute(
        "INSERT INTO questions (id, brief, brief_hash, title, created_at) VALUES (?, ?, ?, ?, ?)",
        ("abcdef123456", "Existing brief", digest("Existing brief"), "Existing", timestamp()),
    )
    db.commit()
    db.close()
    for _ in range(2):
        with Store(tmp_path) as store:
            assert store.describe()["schema_version"] == SCHEMA_VERSION
            assert registry.get_question(store, "abcdef123456")["brief"] == "Existing brief"
            assert store.count("discovery_receipts") == 0


def test_nested_registry_transactions_roll_back_with_outer_transaction(store):
    with pytest.raises(RuntimeError, match="rollback"):
        with store.transaction():
            registry.register_question(store, "Must roll back")
            raise RuntimeError("rollback")
    assert store.count("questions") == 0
