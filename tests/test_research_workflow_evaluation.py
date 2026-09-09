from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest

from research_harness import registry
from research_harness.backend import Backend, BackendSettings
from research_harness.config import Pagination
from research_harness.evaluation.workflow import capture_snapshot, evaluate_workflow
from research_harness.services.jobs import JobService
from research_harness.services.research import ResearchService
from research_harness.util import canonical_json, timestamp


@pytest.fixture
def workflow_backend(store, tmp_path, clock):
    if store.mode == "sqlite":
        return Backend.local(tmp_path / "backend", clock=clock)
    return Backend(
        BackendSettings(
            local_root=tmp_path / "backend",
            database_url=store.dialect.url,
            schema=store.dialect.schema,
            blob_bucket="injected",
            blob_access_key="injected",
            blob_secret_key="injected",
        ),
        blobs=store.blobs,
        clock=clock,
    )


def prepare(tmp_path, backend, spec, name="case"):
    service = ResearchService(tmp_path / name, backend=backend, public_only=False)
    ids = service.begin(f"Independently collect policy notices for {name}", model="fixture")
    with backend.open_registry() as store:
        version = registry.register_pipeline(store, ids["question_id"], spec)
    return service, JobService(service, auto_launch=False), version["id"], ids["question_id"]


def snapshot(backend, question, pipeline):
    return capture_snapshot(backend, question_id=question, pipeline_version_id=pipeline)


def collect(jobs, pipeline, item, operation_id="collect", **kwargs):
    job = jobs.start_collection(pipeline, operation_id=operation_id)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        return jobs.run_job(job["id"], client=client, **kwargs)


def expected(spec, pipeline, job, *, status="succeeded", sources=None, **kwargs):
    return {
        "pipeline_version_id": pipeline,
        "pipeline_fingerprint": spec.fingerprint(),
        "collections": [
            {
                "job_id": job["id"],
                "operation_id": job["operation_id"],
                "run_id": job["run_id"],
                "status": status,
                "sources": sources or {spec.sources[0].id: {"status": status, "observations": 1}},
                **kwargs,
            }
        ],
    }


def failed(report):
    return {check["check"] for check in report["checks"] if check["status"] == "failed"}


def unknown(report):
    return {check["check"] for check in report["checks"] if check["status"] == "unknown"}


def export_expectation(jobs, job, *, count):
    manifest = json.loads(jobs.read_export(job["id"], artifact="manifest")["content"])
    return {
        "job_id": job["id"],
        "operation_id": job["operation_id"],
        "as_of": job["request"]["as_of"],
        "kind": job["request"]["kind"],
        "record_count": count,
        "returned_manifest": manifest,
    }


def test_success_and_replay_are_measured_separately(
    tmp_path, workflow_backend, spec, item, monkeypatch
):
    service, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = collect(jobs, pipeline, item)
    expectation = expected(spec, pipeline, job)
    before = snapshot(workflow_backend, question, pipeline)
    report = evaluate_workflow(before, expectation)
    assert report["status"] == "incomplete" and not failed(report)
    assert unknown(report) == {"replay.identity_and_publication"}
    reopened = ResearchService(tmp_path / "ignored", backend=workflow_backend, public_only=False)
    reopened.resume(service.discovery_id)
    restarted = JobService(reopened, auto_launch=False)
    assert restarted.start_collection(pipeline, operation_id="collect")["id"] == job["id"]
    assert restarted.run_job(job["id"])["run_id"] == job["run_id"]
    expectation["replay_job_ids"] = [job["id"]]
    # An evaluation must never accidentally call recovery-aware service readers.
    monkeypatch.setattr(JobService, "get_job", lambda *_: pytest.fail("evaluation mutated jobs"))
    after = snapshot(workflow_backend, question, pipeline)
    report = evaluate_workflow(after, expectation, before=before)
    assert report["status"] == "passed", report
    assert after.fingerprint() == snapshot(workflow_backend, question, pipeline).fingerprint()
    assert "quality" not in report and "total_tokens" not in report


def test_fixed_cutoff_reconstructs_original_records_and_preserves_config(
    tmp_path, workflow_backend, spec, item, clock
):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    first = collect(jobs, pipeline, item)
    clock.advance(60)
    cutoff = timestamp(clock())
    exported = jobs.export_observations(pipeline, operation_id="export", as_of=cutoff)
    clock.advance(60)
    collect(jobs, pipeline, {**item, "title": "Late revision"}, "later")
    # A new registry configuration must not silently replace the requested version.
    changed = spec.model_copy(update={"description": "A later version"})
    with workflow_backend.open_registry() as store:
        registry.register_pipeline(store, question, changed)
    exported = jobs.run_job(exported["id"])
    expectation = expected(spec, pipeline, first)
    expectation["exports"] = [export_expectation(jobs, exported, count=1)]
    report = evaluate_workflow(snapshot(workflow_backend, question, pipeline), expectation)
    assert not failed(report), report
    assert unknown(report) == {"replay.identity_and_publication"}
    expectation["pipeline_fingerprint"] = changed.fingerprint()
    assert "pipeline.original_configuration" in failed(
        evaluate_workflow(snapshot(workflow_backend, question, pipeline), expectation)
    )


def test_availability_cutoff_excludes_earlier_capture_not_yet_published(
    tmp_path, workflow_backend, spec, item, clock
):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = jobs.start_collection(pipeline, operation_id="collect")
    cutoff = timestamp(clock())

    def handler(_):
        clock.advance(30)
        return httpx.Response(200, json={"items": [item]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        job = jobs.run_job(job["id"], client=client)
    exported = jobs.export_observations(pipeline, operation_id="export", as_of=cutoff)
    exported = jobs.run_job(exported["id"])
    expectation = expected(spec, pipeline, job)
    expectation["exports"] = [export_expectation(jobs, exported, count=0)]
    result = evaluate_workflow(snapshot(workflow_backend, question, pipeline), expectation)
    assert not failed(result), result
    # Historical source publication time is not availability before collection.
    assert expectation["exports"][0]["returned_manifest"]["record_count"] == 0


def test_export_delivery_is_unknown_without_returned_manifest(
    tmp_path, workflow_backend, spec, item, clock
):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = collect(jobs, pipeline, item)
    exported = jobs.export_observations(pipeline, operation_id="export", as_of=timestamp(clock()))
    exported = jobs.run_job(exported["id"])
    expectation = expected(spec, pipeline, job)
    expectation["exports"] = [export_expectation(jobs, exported, count=1)]
    expectation["exports"][0].pop("returned_manifest")
    result = evaluate_workflow(snapshot(workflow_backend, question, pipeline), expectation)
    assert f"export.{exported['id']}.returned_manifest" in unknown(result)
    expectation["exports"][0]["returned_manifest"] = {"record_count": 999}
    result = evaluate_workflow(snapshot(workflow_backend, question, pipeline), expectation)
    assert f"export.{exported['id']}.returned_manifest" in failed(result)


@pytest.mark.parametrize("corruption", ["capture", "lineage", "configuration", "job_claim"])
def test_database_success_cannot_hide_corrupt_publication(
    tmp_path, workflow_backend, spec, item, tamper, corruption
):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = collect(jobs, pipeline, item)
    with workflow_backend.pipeline_store(spec, None, question_id=question) as store:
        if corruption == "capture":
            capture = store.query_one("SELECT * FROM captures")
            tamper(store, capture["body_hash"], b"replaced capture")
        else:
            with store.transaction():
                if corruption == "lineage":
                    store.execute("UPDATE observations SET available_at='2020-01-01T00:00:00Z'")
                elif corruption == "configuration":
                    store.execute("UPDATE source_runs SET config_hash=?", ("0" * 64,))
                else:
                    store.execute("UPDATE source_runs SET status='failed'")
    report = evaluate_workflow(
        snapshot(workflow_backend, question, pipeline), expected(spec, pipeline, job)
    )
    assert report["status"] == "failed" and "publication.lineage" in failed(report)


def test_archived_export_hash_checked_even_when_files_exist(
    tmp_path, workflow_backend, spec, item, clock, tamper
):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = collect(jobs, pipeline, item)
    exported = jobs.export_observations(pipeline, operation_id="export", as_of=timestamp(clock()))
    exported = jobs.run_job(exported["id"])
    expectation = expected(spec, pipeline, job)
    expectation["exports"] = [export_expectation(jobs, exported, count=1)]
    ref = next(r for r in exported["result"]["artifact_refs"] if r["kind"] == "observations")
    with workflow_backend.open_registry() as store:
        tamper(store, ref["sha256"], b'{"claimed":"success"}\n')
    result = evaluate_workflow(snapshot(workflow_backend, question, pipeline), expectation)
    assert "capture.integrity" in failed(result)
    assert f"export.{exported['id']}.archived_export" in failed(result)


def test_cancel_before_start_neither_creates_store_nor_claims_work_done(
    tmp_path, workflow_backend, spec
):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = jobs.start_collection(pipeline, operation_id="collect")
    jobs.cancel_job(job["id"])
    expectation = expected(
        spec,
        pipeline,
        job,
        status="cancelled",
        run_exists=False,
        sources={spec.sources[0].id: {"status": "not_started", "observations": 0}},
    )
    existing = set(workflow_backend.settings.local_root.rglob("harness.sqlite3"))
    report = evaluate_workflow(snapshot(workflow_backend, question, pipeline), expectation)
    assert not failed(report), report
    if workflow_backend.mode == "local":
        assert set(workflow_backend.settings.local_root.rglob("harness.sqlite3")) == existing
    expectation["collections"][0]["status"] = "succeeded"
    assert f"collection.{job['id']}.terminal_status" in failed(
        evaluate_workflow(snapshot(workflow_backend, question, pipeline), expectation)
    )


@pytest.mark.parametrize("interrupt", [False, True])
def test_partial_cancellation_and_interruption_preserve_published_sources_on_replay(
    tmp_path, workflow_backend, spec, source, item, interrupt
):
    second = source.model_copy(update={"id": "second", "url": "https://source.example/second"})
    spec = spec.model_copy(update={"sources": [source, second]})
    service, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = jobs.start_collection(pipeline, operation_id="collect")

    def stop(_):
        if interrupt:
            raise SystemExit("worker stopped after first committed source")
        jobs.cancel_job(job["id"])

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        if interrupt:
            with pytest.raises(SystemExit):
                jobs.run_job(job["id"], client=client, on_progress=stop)
        else:
            jobs.run_job(job["id"], client=client, on_progress=stop)
    expectation = expected(
        spec,
        pipeline,
        job,
        status="interrupted" if interrupt else "cancelled",
        sources={
            source.id: {"status": "succeeded", "observations": 1},
            second.id: {"status": "not_started", "observations": 0},
        },
    )
    if interrupt:
        pending = snapshot(workflow_backend, question, pipeline)
        report = evaluate_workflow(pending, expectation)
        assert f"collection.{job['id']}.terminal_status" in unknown(report)
        assert pending.fingerprint() == snapshot(workflow_backend, question, pipeline).fingerprint()
        # Recovery belongs to the application, never the evaluator.
        jobs.get_job(job["id"])
    before = snapshot(workflow_backend, question, pipeline)
    reopened = ResearchService(tmp_path / "reopened", backend=workflow_backend, public_only=False)
    reopened.resume(service.discovery_id)
    restarted = JobService(reopened, auto_launch=False)
    assert restarted.start_collection(pipeline, operation_id="collect")["id"] == job["id"]
    restarted.run_job(job["id"])
    expectation["replay_job_ids"] = [job["id"]]
    report = evaluate_workflow(
        snapshot(workflow_backend, question, pipeline), expectation, before=before
    )
    assert report["status"] == "passed", report
    expectation["collections"][0]["sources"][source.id]["observations"] = 0
    assert f"collection.{job['id']}.{source.id}.publication" in failed(
        evaluate_workflow(
            snapshot(workflow_backend, question, pipeline), expectation, before=before
        )
    )


def test_pending_does_not_pass_a_claim_of_completion(tmp_path, workflow_backend, spec):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = jobs.start_collection(pipeline, operation_id="collect")
    before = snapshot(workflow_backend, question, pipeline)
    result = evaluate_workflow(before, expected(spec, pipeline, job))
    assert f"collection.{job['id']}.terminal_status" in unknown(result)
    assert before.fingerprint() == snapshot(workflow_backend, question, pipeline).fingerprint()


def test_snapshot_does_not_create_an_absent_dataset(tmp_path, workflow_backend, spec):
    _, _, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    value = snapshot(workflow_backend, question, pipeline)
    assert not value.tables["runs"]
    if workflow_backend.mode == "local":
        assert not (workflow_backend.settings.local_root / "pipelines").exists()


def test_cancelled_pagination_keeps_captures_without_partial_publication(
    tmp_path, workflow_backend, spec, source, item
):
    source = source.model_copy(
        update={"pagination": Pagination(mode="next_url", next_pointer="/next")}
    )
    spec = spec.model_copy(update={"sources": [source]})
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = jobs.start_collection(pipeline, operation_id="collect")
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"items": [item], "next": "/page2"})
        jobs.cancel_job(job["id"])
        return httpx.Response(200, json={"items": [{**item, "id": "second"}], "next": None})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs.run_job(job["id"], client=client)
    value = snapshot(workflow_backend, question, pipeline)
    report = evaluate_workflow(
        value,
        expected(
            spec,
            pipeline,
            job,
            status="cancelled",
            sources={source.id: {"status": "cancelled", "observations": 0}},
        ),
    )
    assert not failed(report), report
    assert len(value.tables["captures"]) == 2 and not value.tables["observations"]


def test_self_consistent_but_wrong_export_records_fail_independent_selector(
    tmp_path, workflow_backend, spec, item, clock
):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = collect(jobs, pipeline, item)
    exported = jobs.export_observations(pipeline, operation_id="export", as_of=timestamp(clock()))
    exported = jobs.run_job(exported["id"])
    expectation = expected(spec, pipeline, job)
    export_request = export_expectation(jobs, exported, count=1)
    expectation["exports"] = [export_request]
    # Emulate an exporter that silently omitted the only record, but hashed the
    # wrong bytes consistently. Valid artifact hashes alone cannot establish correctness.
    with workflow_backend.open_registry() as store:
        result = exported["result"]
        manifest = {**export_request["returned_manifest"], "record_count": 0}
        manifest["data_sha256"] = store.put_blob(b"")
        manifest_hash = store.put_blob(canonical_json(manifest).encode())
        result.update(manifest)
        result["artifact_refs"] = [
            {"kind": "manifest", "sha256": manifest_hash},
            {"kind": "observations", "sha256": manifest["data_sha256"], "bytes": 0},
        ]
        with store.transaction():
            store.execute(
                "UPDATE research_jobs SET result_json=? WHERE id=?",
                (canonical_json(result), exported["id"]),
            )
    export_request["returned_manifest"] = manifest
    report = evaluate_workflow(snapshot(workflow_backend, question, pipeline), expectation)
    assert f"export.{exported['id']}.fixed_cutoff_records" in failed(report)
    assert "capture.integrity" not in failed(report)


def test_same_pipeline_name_other_question_cannot_supply_publication(
    tmp_path, workflow_backend, spec, item
):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = collect(jobs, pipeline, item)
    _, other_jobs, other_pipeline, other_question = prepare(
        tmp_path, workflow_backend, spec, name="unrelated"
    )
    assert question != other_question
    with pytest.raises(ValueError, match="unavailable"):
        snapshot(workflow_backend, other_question, pipeline)
    own = other_jobs.start_collection(other_pipeline, operation_id="collect")
    own_expectation = expected(spec, other_pipeline, own)
    own_expectation["collections"][0]["job_id"] = job["id"]
    result = evaluate_workflow(
        snapshot(workflow_backend, other_question, other_pipeline), own_expectation
    )
    assert f"collection.{job['id']}.exists_in_scope" in failed(result)


@pytest.mark.parametrize("changed", ["operation_id", "run_id", "due_only"])
def test_independent_request_cannot_be_replaced_by_actual_job_values(
    tmp_path, workflow_backend, spec, item, changed
):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = collect(jobs, pipeline, item)
    expectation = expected(spec, pipeline, job)
    expectation["collections"][0][changed] = True if changed == "due_only" else "another-id"
    result = evaluate_workflow(snapshot(workflow_backend, question, pipeline), expectation)
    assert result["status"] == "failed"


def test_replay_rejects_changed_publication_and_unknown_before(
    tmp_path, workflow_backend, spec, item
):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = collect(jobs, pipeline, item)
    before = snapshot(workflow_backend, question, pipeline)
    expectation = expected(spec, pipeline, job)
    expectation["replay_job_ids"] = [job["id"]]
    result = evaluate_workflow(before, expectation)
    assert "replay.identity_and_publication" in unknown(result)
    altered = deepcopy(before)
    altered.tables["observations"][0]["id"] += 1
    result = evaluate_workflow(altered, expectation, before=before)
    assert f"replay.{job['id']}.publication" in failed(result)


def test_wrong_saved_operation_result_is_detected(tmp_path, workflow_backend, spec, item):
    _, jobs, pipeline, question = prepare(tmp_path, workflow_backend, spec)
    job = collect(jobs, pipeline, item)
    with workflow_backend.open_registry() as store:
        with store.transaction():
            store.execute(
                "UPDATE discovery_operations SET result_json=? WHERE operation_id='collect'",
                (canonical_json({"job_id": "another-job"}),),
            )
    report = evaluate_workflow(
        snapshot(workflow_backend, question, pipeline), expected(spec, pipeline, job)
    )
    assert f"collection.{job['id']}.request_identity" in failed(report)
