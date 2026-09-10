from __future__ import annotations

import json
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import httpx
import pytest

from research_harness import registry
from research_harness.backend import Backend, BackendSettings
from research_harness.services.jobs import TERMINAL, JobService
from research_harness.services.research import ResearchService
from research_harness.store import WriterBusy
from research_harness.util import timestamp


@pytest.fixture
def job_backend(store, tmp_path, clock):
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


def prepare(
    tmp_path, backend, spec, *, name="case", brief="Collect policy data", auto_launch=False
):
    service = ResearchService(tmp_path / name, backend=backend, public_only=False)
    ids = service.begin(brief, model="fixture")
    with backend.open_registry() as store:
        version = registry.register_pipeline(store, ids["question_id"], spec)
    return service, JobService(service, auto_launch=auto_launch), version["id"]


def dataset(backend, service, spec):
    return backend.pipeline_store(spec, None, question_id=service.get_context()["question_id"])


def test_job_restart_retry_and_original_configuration(tmp_path, job_backend, spec, item):
    service, jobs, pipeline = prepare(tmp_path, job_backend, spec)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"items": [item]})

    queued = jobs.start_collection(pipeline, operation_id="collect")
    assert queued["status"] == "queued" and queued["run_id"]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = jobs.run_job(queued["id"], client=client)
        assert result["status"] == "succeeded"
        assert result["run_id"] == queued["run_id"]
        resumed = ResearchService(tmp_path / "ignored", backend=job_backend, public_only=False)
        resumed.resume(service.discovery_id)
        reopened = JobService(resumed, auto_launch=False)
        assert reopened.get_job(queued["id"])["collection"]["status"] == "succeeded"
        assert reopened.start_collection(pipeline, operation_id="collect")["id"] == queued["id"]
        assert reopened.run_job(queued["id"], client=client)["status"] == "succeeded"
        assert len(calls) == 1
    with dataset(job_backend, service, spec) as store:
        details = store.run_details(queued["run_id"])
        assert details["config_hash"] == spec.fingerprint()
        assert details["config"] == spec.model_dump(mode="json")
        assert store.count("observations") == 1
    with pytest.raises(ValueError, match="different arguments"):
        jobs.start_collection(pipeline, operation_id="collect", due_only=True)
    with pytest.raises(ValueError, match="different arguments"):
        service.inspect(spec.sources[0].url, operation_id="collect")


def test_export_preserves_cutoff_and_reads_archived_blobs(tmp_path, job_backend, spec, item, clock):
    service, jobs, pipeline = prepare(tmp_path, job_backend, spec)
    current = dict(item)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [current]}))
    ) as client:
        first = jobs.start_collection(pipeline, operation_id="first")
        jobs.run_job(first["id"], client=client)
        clock.advance(60)
        export = jobs.export_observations(pipeline, operation_id="export", as_of=timestamp(clock()))
        clock.advance(60)
        current["title"] = "Changed after cutoff"
        second = jobs.start_collection(pipeline, operation_id="second")
        jobs.run_job(second["id"], client=client)
        result = jobs.run_job(export["id"])
    assert result["status"] == "succeeded" and result["result"]["record_count"] == 1
    body = json.loads(jobs.read_export(export["id"])["content"])
    assert body["data"]["title"] == item["title"]
    assert body["lineage"]["source_url"] == spec.sources[0].url
    manifest = json.loads(jobs.read_export(export["id"], artifact="manifest")["content"])
    assert manifest["data_sha256"] == jobs.read_export(export["id"])["sha256"]
    assert (
        jobs.export_observations(pipeline, operation_id="export", as_of=export["request"]["as_of"])[
            "id"
        ]
        == export["id"]
    )
    with pytest.raises(ValueError, match="future"):
        jobs.export_observations(pipeline, operation_id="future", as_of="2099-01-01T00:00:00Z")


def test_cancel_before_start_never_creates_collection_run(tmp_path, job_backend, spec):
    service, jobs, pipeline = prepare(tmp_path, job_backend, spec)
    job = jobs.start_collection(pipeline, operation_id="collect")
    assert jobs.cancel_job(job["id"])["status"] == "cancelled"
    assert jobs.run_job(job["id"])["status"] == "cancelled"
    with dataset(job_backend, service, spec) as store:
        assert store.count("runs") == 0


def test_cancellation_preserves_published_source_and_stops_next(
    tmp_path, job_backend, spec, source, item
):
    second = source.model_copy(update={"id": "second", "url": "https://source.example/second"})
    spec = spec.model_copy(update={"sources": [source, second]})
    service, jobs, pipeline = prepare(tmp_path, job_backend, spec)
    job = jobs.start_collection(pipeline, operation_id="collect")
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={"items": [item]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = jobs.run_job(
            job["id"], client=client, on_progress=lambda _: jobs.cancel_job(job["id"])
        )
    assert result["status"] == "cancelled" and requests == [source.url]
    with dataset(job_backend, service, spec) as store:
        details = store.run_details(job["run_id"])
        assert details["status"] == "cancelled"
        assert details["sources"][0]["status"] == "succeeded"
        assert store.count("observations") == 1
        assert store.state(spec.name, second.id) is None
    assert jobs.get_job(job["id"])["collection"]["sources"][0]["record_count"] == 1


def test_cancel_during_pagination_does_not_publish_partial_source(
    tmp_path, job_backend, spec, source, item
):
    from research_harness.config import Pagination

    source = source.model_copy(
        update={
            "pagination": Pagination(mode="next_url", next_pointer="/next"),
        }
    )
    spec = spec.model_copy(update={"sources": [source]})
    service, jobs, pipeline = prepare(tmp_path, job_backend, spec)
    job = jobs.start_collection(pipeline, operation_id="collect")
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"items": [item], "next": "/page2"})
        jobs.cancel_job(job["id"])
        return httpx.Response(200, json={"items": [{**item, "id": "second"}], "next": None})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = jobs.run_job(job["id"], client=client)
    assert result["status"] == "cancelled"
    with dataset(job_backend, service, spec) as store:
        assert store.count("observations") == 0
        assert store.state(spec.name, source.id) is None
        assert store.run_details(job["run_id"])["sources"][0]["status"] == "cancelled"
        assert len(store.captures_for_run(job["run_id"])) == 2


def test_worker_interruption_reconciles_published_data_without_rerun(
    tmp_path, job_backend, spec, source, item
):
    second = source.model_copy(update={"id": "second", "url": "https://source.example/second"})
    spec = spec.model_copy(update={"sources": [source, second]})
    service, jobs, pipeline = prepare(tmp_path, job_backend, spec)
    job = jobs.start_collection(pipeline, operation_id="collect")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"items": [item]})

    def crash(_):
        raise SystemExit("worker exited after first publication")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SystemExit):
            jobs.run_job(job["id"], client=client, on_progress=crash)
        assert jobs.get_job(job["id"])["status"] == "interrupted"
        assert jobs.start_collection(pipeline, operation_id="collect")["status"] == "interrupted"
        assert jobs.run_job(job["id"], client=client)["status"] == "interrupted"
    assert len(calls) == 1
    with dataset(job_backend, service, spec) as store:
        assert store.count("observations") == 1
        assert store.count("runs") == 1


def test_successful_run_survives_death_before_job_commit(
    tmp_path, job_backend, spec, item, monkeypatch
):
    service, jobs, pipeline = prepare(tmp_path, job_backend, spec)
    job = jobs.start_collection(pipeline, operation_id="collect")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        with monkeypatch.context() as patch:

            def crash(*_args, **_kwargs):
                raise SystemExit("before job commit")

            patch.setattr(jobs, "_finish", crash)
            with pytest.raises(SystemExit):
                jobs.run_job(job["id"], client=client)
        recovered = jobs.get_job(job["id"])
    assert recovered["status"] == "succeeded" and recovered["result"]["recovered"]
    with dataset(job_backend, service, spec) as store:
        assert store.count("runs") == store.count("observations") == 1


def test_followup_discovery_recovers_original_export_files(
    tmp_path, job_backend, spec, item, clock, monkeypatch
):
    service, jobs, pipeline = prepare(tmp_path, job_backend, spec)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [item]}))
    ) as client:
        job = jobs.start_collection(pipeline, operation_id="collect")
        jobs.run_job(job["id"], client=client)
    clock.advance()
    export = jobs.export_observations(pipeline, operation_id="export", as_of=timestamp(clock()))
    with monkeypatch.context() as patch:

        def crash(*_args, **_kwargs):
            raise SystemExit("after export")

        patch.setattr(jobs, "_finish", crash)
        with pytest.raises(SystemExit):
            jobs.run_job(export["id"])
    followup, later_jobs, _ = prepare(tmp_path, job_backend, spec, name="followup")
    assert followup.discovery_id != service.discovery_id
    recovered = later_jobs.get_job(export["id"])
    assert recovered["status"] == "succeeded" and recovered["result"]["recovered"]
    assert str(service.output) in recovered["result"]["output"]
    assert json.loads(later_jobs.read_export(export["id"])["content"])["key"] == item["id"]


def test_questions_with_same_pipeline_name_have_isolated_datasets(
    tmp_path, job_backend, spec, source, item, clock
):
    service_a, jobs_a, pipeline_a = prepare(
        tmp_path, job_backend, spec, name="a", brief="Question A"
    )
    second = source.model_copy(update={"url": "https://other.example/data"})
    spec_b = spec.model_copy(update={"sources": [second]})
    service_b, jobs_b, pipeline_b = prepare(
        tmp_path, job_backend, spec_b, name="b", brief="Question B"
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"items": [{**item, "id": request.url.host, "title": request.url.host}]}
            )
        )
    ) as client:
        first = jobs_a.start_collection(pipeline_a, operation_id="collect")
        jobs_a.run_job(first["id"], client=client)
        second_job = jobs_b.start_collection(pipeline_b, operation_id="collect")
        jobs_b.run_job(second_job["id"], client=client)
    clock.advance()
    export = jobs_b.export_observations(pipeline_b, operation_id="export", as_of=timestamp(clock()))
    jobs_b.run_job(export["id"])
    records = [
        json.loads(line) for line in jobs_b.read_export(export["id"])["content"].splitlines()
    ]
    assert [record["key"] for record in records] == ["other.example"]
    with pytest.raises(ValueError, match="does not belong"):
        jobs_b.get_job(first["id"])
    with pytest.raises(ValueError, match="does not belong"):
        jobs_b.start_collection(pipeline_a, operation_id="foreign")
    with dataset(job_backend, service_a, spec) as first_store:
        assert first_store.health(spec, timestamp(clock()))[0]["last_success_at"]
    assert service_a.get_context()["question_id"] != service_b.get_context()["question_id"]


def test_active_worker_is_not_reconciled_or_executed_again(tmp_path, job_backend, spec, item):
    entered, release = Event(), Event()
    service, jobs, pipeline = prepare(tmp_path, job_backend, spec)
    job = jobs.start_collection(pipeline, operation_id="collect")
    calls = []

    def handler(request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json={"items": [item]})

    other = JobService(service, auto_launch=False)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(jobs.run_job, job["id"], client=client)
            try:
                assert entered.wait(5)
                assert other.get_job(job["id"])["worker_active"] is True
                with pytest.raises(WriterBusy):
                    other.run_job(job["id"], client=client)
            finally:
                release.set()
            assert future.result(timeout=5)["status"] == "succeeded"
    assert len(calls) == 1


@contextmanager
def source_server(item, *, entered=None, release=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if entered:
                entered.set()
            if release:
                release.wait(10)
            body = json.dumps({"items": [item]}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/data"
    finally:
        if release:
            release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def wait_for_job(jobs, job_id):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        result = jobs.get_job(job_id)
        if result["status"] in TERMINAL:
            return result
        time.sleep(0.05)
    raise AssertionError(f"Job did not complete: {result}")


def test_detached_process_survives_client_reopen(tmp_path, spec, source, item):
    backend = Backend.local(tmp_path / "backend")
    with source_server(item) as url:
        spec = spec.model_copy(update={"sources": [source.model_copy(update={"url": url})]})
        service, jobs, pipeline = prepare(tmp_path, backend, spec, auto_launch=True)
        job = jobs.start_collection(pipeline, operation_id="collect")
        reopened = ResearchService(tmp_path, backend=backend, public_only=False)
        reopened.resume(service.discovery_id)
        result = wait_for_job(JobService(reopened), job["id"])
    assert result["status"] == "succeeded" and result["worker_pid"] != os.getpid()
    assert result["collection"]["sources"][0]["record_count"] == 1


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="POSIX worker-death recovery")
def test_killed_worker_is_reconciled_without_duplicate_publication(tmp_path, spec, source, item):
    backend = Backend.local(tmp_path / "backend")
    entered, release = Event(), Event()
    with source_server(item, entered=entered, release=release) as url:
        spec = spec.model_copy(update={"sources": [source.model_copy(update={"url": url})]})
        service, jobs, pipeline = prepare(tmp_path, backend, spec, auto_launch=True)
        job = jobs.start_collection(pipeline, operation_id="collect")
        assert entered.wait(8)
        current = jobs.get_job(job["id"])
        assert current["worker_active"] and current["worker_pid"] != os.getpid()
        os.kill(current["worker_pid"], signal.SIGKILL)
        result = wait_for_job(jobs, job["id"])
        release.set()
    assert result["status"] == "interrupted"
    assert jobs.start_collection(pipeline, operation_id="collect")["status"] == "interrupted"
    with dataset(backend, service, spec) as store:
        assert store.count("runs") == 1 and store.count("observations") == 0
