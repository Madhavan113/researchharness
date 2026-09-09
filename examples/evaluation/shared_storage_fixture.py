"""Exercise disposable loopback Postgres/MinIO and the real shared-backend workflow.

Uses only already-present, digest-pinned images; never pulls images or uses cloud
services. Run with ``uv run --extra mcp python examples/evaluation/shared_storage_fixture.py
--out /tmp/rh-shared-storage-acceptance``. Credentials exist only in private
temporary files/process environments and are redacted from saved diagnostics.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg

from research_harness import registry
from research_harness.backend import Backend, BackendSettings
from research_harness.config import SourceSpec
from research_harness.discovery_models import Candidate, DataNeed, ProposalDraft
from research_harness.engine import replay_run
from research_harness.services.jobs import TERMINAL, JobService
from research_harness.services.research import ResearchService
from research_harness.store import SCHEMA_VERSION, WriterBusy
from research_harness.util import digest, timestamp, utcnow, write_json

POSTGRES = "postgres@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73"
MINIO = "minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
TESTS = [
    "tests/test_backend.py",
    "tests/test_pipeline.py",
    "tests/test_http.py",
    "tests/test_research_service.py",
    "tests/test_research_search.py",
    "tests/test_research_jobs.py",
]


class Services:
    def __init__(self, output: Path):
        self.output = output
        self.identity = "rh-shared-" + uuid4().hex[:12]
        self.password = secrets.token_urlsafe(32)
        self.access_key = secrets.token_hex(12)
        self.secret_key = secrets.token_urlsafe(32)
        self.names: list[str] = []
        self.commands: list[list[str]] = []
        self.containers: list[dict] = []
        self.settings: BackendSettings | None = None

    def redact(self, value: str) -> str:
        for secret in (self.password, self.access_key, self.secret_key):
            value = value.replace(secret, "<redacted>")
        return value

    def docker(self, *arguments: str) -> str:
        command = ["docker", *arguments]
        recorded = list(command)
        if "--env-file" in recorded:
            recorded[recorded.index("--env-file") + 1] = "<private-temporary-env-file>"
        self.commands.append(recorded)
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(self.redact(result.stderr.strip()))
        return result.stdout.strip()

    def start(self):
        for image in (POSTGRES, MINIO):
            self.docker("image", "inspect", image, "--format", "{{.Id}}")
        with tempfile.TemporaryDirectory(prefix=self.identity + "-") as private:
            configurations = [
                (
                    "postgres",
                    POSTGRES,
                    5432,
                    "/var/lib/postgresql/data",
                    {
                        "POSTGRES_USER": "rh_fixture",
                        "POSTGRES_DB": "rh_fixture",
                        "POSTGRES_PASSWORD": self.password,
                    },
                    [],
                ),
                (
                    "minio",
                    MINIO,
                    9000,
                    "/data",
                    {
                        "MINIO_ROOT_USER": self.access_key,
                        "MINIO_ROOT_PASSWORD": self.secret_key,
                        "MINIO_BROWSER": "off",
                    },
                    ["server", "/data", "--address", ":9000"],
                ),
            ]
            ports = {}
            for role, image, port, mount, environment, arguments in configurations:
                path = Path(private) / (role + ".env")
                path.write_text("".join(f"{key}={value}\n" for key, value in environment.items()))
                path.chmod(0o600)
                name = self.identity + "-" + role
                self.names.append(name)
                # Docker may choose a different dynamic published port after a
                # restart. Reserve a currently free explicit loopback port so
                # the backend configuration remains unchanged across restarts.
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    host_port = listener.getsockname()[1]
                container = self.docker(
                    "run",
                    "-d",
                    "--pull=never",
                    "--name",
                    name,
                    "--label",
                    "research-harness.fixture=" + self.identity,
                    "--env-file",
                    str(path),
                    "--publish",
                    f"127.0.0.1:{host_port}:{port}",
                    "--mount",
                    f"type=volume,destination={mount}",
                    image,
                    *arguments,
                )
                bindings = json.loads(
                    self.docker("inspect", name, "--format", "{{json .NetworkSettings.Ports}}")
                )
                published = bindings[f"{port}/tcp"]
                assert len(published) == 1 and published[0]["HostIp"] == "127.0.0.1"
                ports[role] = int(published[0]["HostPort"])
                self.containers.append(
                    {"name": name, "id": container, "image": image, "ports": bindings}
                )
        self.settings = BackendSettings(
            database_url=f"postgresql://rh_fixture:{self.password}@127.0.0.1:{ports['postgres']}/rh_fixture",
            schema="acceptance_" + uuid4().hex[:12],
            blob_bucket="rh-shared-fixture",
            blob_endpoint=f"http://127.0.0.1:{ports['minio']}",
            blob_access_key=self.access_key,
            blob_secret_key=self.secret_key,
            blob_prefix="workflow/" + self.identity,
            local_root=self.output / "backend",
        )
        self.wait_ready()
        return Backend(self.settings)

    def wait_ready(self):
        until = time.monotonic() + 45
        while True:
            try:
                with psycopg.connect(self.settings.database_url, connect_timeout=1) as connection:
                    connection.execute("SELECT 1").fetchone()
                with httpx.Client(trust_env=False, timeout=1) as client:
                    client.get(
                        self.settings.blob_endpoint + "/minio/health/ready"
                    ).raise_for_status()
                return
            except (psycopg.Error, httpx.HTTPError):
                if time.monotonic() >= until:
                    raise TimeoutError("Task-owned storage services did not become ready") from None
                time.sleep(0.2)

    def restart(self):
        for name in self.names:
            self.docker("restart", name)
        self.wait_ready()

    def close(self) -> list[dict]:
        results = []
        for name in reversed(self.names):
            try:
                self.docker("rm", "-f", "-v", name)
                results.append({"name": name, "removed": True})
            except Exception as exc:
                results.append({"name": name, "removed": False, "error": self.redact(str(exc))})
        return results

    def test_environment(self) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("RH_", "AWS_", "OPENAI_"))
        }
        settings = self.settings
        environment.update(
            RH_TEST_DATABASE_URL=settings.database_url,
            RH_TEST_BLOB_BUCKET=settings.blob_bucket,
            RH_TEST_BLOB_ENDPOINT=settings.blob_endpoint,
            RH_TEST_BLOB_ACCESS_KEY=settings.blob_access_key,
            RH_TEST_BLOB_SECRET_KEY=settings.blob_secret_key,
            RH_TEST_BLOB_REGION=settings.blob_region,
        )
        return environment


def store_tests(services: Services) -> dict:
    command = [
        sys.executable,
        "-m",
        "pytest",
        *TESTS,
        "-ra",
        "--junitxml",
        str(services.output / "pytest.xml"),
    ]
    print("Running SQLite and Postgres/S3 store, service and job tests", flush=True)
    result = subprocess.run(
        command, capture_output=True, text=True, env=services.test_environment(), timeout=300
    )
    (services.output / "pytest.log").write_text(services.redact(result.stdout + result.stderr))
    root = ET.parse(services.output / "pytest.xml").getroot()
    cases = root.findall(".//testcase")
    return {
        "command": ["uv", "run", "--extra", "mcp", "python", "-m", "pytest", *TESTS, "-ra"],
        "environment": [key for key in services.test_environment() if key.startswith("RH_TEST_")],
        "exit_code": result.returncode,
        "cases": len(cases),
        "postgres_cases": sum("postgres" in case.attrib["name"] for case in cases),
        "failures": len(root.findall(".//failure")),
        "errors": len(root.findall(".//error")),
        "skipped": len(root.findall(".//skipped")),
    }


def reuse_store_tests(services: Services, previous: Path) -> dict:
    """Carry forward an explicitly selected completed suite; do not rerun it."""
    previous = previous.resolve()
    report = json.loads((previous / "acceptance.json").read_bytes())
    inventory = json.loads((previous / "files.json").read_bytes())
    tests = report["tests"]
    if tests["exit_code"] != 0 or tests["failures"] or tests["errors"]:
        raise ValueError("Prior shared-storage tests did not pass")
    if {item["image"] for item in report["containers"]} != {POSTGRES, MINIO}:
        raise ValueError("Prior tests used different service images")
    for name in ("acceptance.json", "pytest.xml", "pytest.log"):
        if digest((previous / name).read_bytes()) != inventory[name]:
            raise ValueError(f"Prior test evidence changed: {name}")
    for name in ("pytest.xml", "pytest.log"):
        shutil.copyfile(previous / name, services.output / name)
    print("Reusing explicitly selected passing store tests; running corrected workflow", flush=True)
    return {
        **tests,
        "reused_from": str(previous),
        "executed_in_this_attempt": False,
        "prior_acceptance_sha256": inventory["acceptance.json"],
    }


def wait_job(jobs: JobService, identity: str) -> dict:
    until = time.monotonic() + 30
    while True:
        result = jobs.get_job(identity)
        if result["status"] in TERMINAL:
            assert result["status"] == "succeeded", result
            return result
        if time.monotonic() >= until:
            raise TimeoutError(f"Fixture job {identity} did not finish")
        time.sleep(0.1)


def workflow(services: Services, backend: Backend) -> dict:
    print("Running real shared-backend discovery, detached jobs and restart workflow", flush=True)
    state = {"title": "Before publication cutoff", "requests": []}

    class SourceHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/policy.json"
            state["requests"].append(self.path)
            body = json.dumps(
                {
                    "items": [
                        {
                            "id": "policy-1",
                            "title": state["title"],
                            "published_at": "2026-09-01T00:00:00Z",
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_arguments):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = SourceSpec(
            id="policy",
            name="Authored local policy",
            connector="json",
            url=f"http://127.0.0.1:{server.server_port}/policy.json",
            items_pointer="/items",
            id_pointer="/id",
            published_pointer="/published_at",
            required_pointers=["/title"],
        )
        research = ResearchService(services.output / "research", backend=backend, public_only=False)
        ids = research.begin(
            "Collect the authored policy fixture", model="no-model-storage-fixture"
        )
        search = research.record_search(
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {"query": "authored policy", "sources": [{"url": source.url}]},
            },
            provider="authored-storage-fixture",
            operation_id="search-1",
        )
        inspected = research.inspect(source.url, operation_id="inspect-1")
        probe = research.probe(source, operation_id="probe-1")
        draft = ProposalDraft(
            name="shared-policy",
            title="Shared storage policy",
            research_question="Collect the authored policy fixture",
            needs=[DataNeed(id="policy", description="Official policy", required=True)],
            candidates=[
                Candidate(
                    name="Authored policy",
                    purpose="Test shared storage",
                    covers=["policy"],
                    status="ready",
                    evidence_urls=[source.url],
                    source=source,
                    probe_id=probe["probe_id"],
                    freshness_assessment="Fixture",
                    historical_coverage="One authored record",
                    access_notes="Loopback fixture only",
                    limitations=["No live source or model quality claim"],
                )
            ],
            open_questions=[],
        )
        proposal = research.submit_proposal(draft, operation_id="proposal-1")
        pipeline_id = proposal["registry"]["pipeline_version_id"]
        jobs = JobService(research)
        first = wait_job(jobs, jobs.start_collection(pipeline_id, operation_id="collect-1")["id"])
        cutoff = timestamp(utcnow())
        export = JobService(research, auto_launch=False).export_observations(
            pipeline_id, operation_id="export-1", as_of=cutoff
        )
        state["title"] = "After publication cutoff"
        second = wait_job(jobs, jobs.start_collection(pipeline_id, operation_id="collect-2")["id"])
        exported = wait_job(
            jobs, jobs.export_observations(pipeline_id, operation_id="export-1", as_of=cutoff)["id"]
        )
        assert exported["id"] == export["id"]
        assert first["worker_pid"] and second["worker_pid"] and exported["worker_pid"]
        original = jobs.read_export(export["id"])
        row = json.loads(original["content"])
        assert row["data"]["title"] == "Before publication cutoff"
        with backend.open_registry() as store:
            _, spec = registry.pipeline_spec(store, pipeline_id)
        with backend.pipeline_store(spec, None, question_id=ids["question_id"]) as store:
            before_replay = store.count("observations")
            replay = replay_run(store, first["run_id"])
            assert store.count("observations") == before_replay == 2
            assert replay["network_requests"] == 0 and replay["published"] is False
            assert not replay["issues"] and len(replay["records"]) == 1
            assert replay["records"][0]["data"]["title"] == "Before publication cutoff"
        for key in ("output", "manifest"):
            local = Path(exported["result"][key]).resolve()
            assert local.is_relative_to(services.output)
            local.unlink()
        (research.output / "proposal.json").unlink()
        assert len(state["requests"]) == 4
        services.restart()
        resumed_backend = Backend(
            replace(services.settings, local_root=services.output / "other-host-root")
        )
        resumed = ResearchService(
            services.output / "ignored-output", backend=resumed_backend, public_only=False
        )
        resumed.resume(research.discovery_id)
        assert resumed.output == research.output
        assert resumed.get_context()["probes"][probe["probe_id"]] == probe
        assert resumed.probe(source, operation_id="probe-1") == probe
        assert resumed.submit_proposal(draft, operation_id="proposal-1") == proposal
        resumed_jobs = JobService(resumed)
        assert (
            resumed_jobs.start_collection(pipeline_id, operation_id="collect-1")["id"]
            == first["id"]
        )
        recovered = resumed_jobs.read_export(export["id"])
        assert recovered == original
        assert len(state["requests"]) == 4
        for receipt in (search, inspected, probe):
            assert resumed.get_evidence(receipt["receipt_id"])["content"]
        with resumed_backend.pipeline_store(spec, None, question_id=ids["question_id"]) as one:
            with resumed_backend.pipeline_store(spec, None, question_id=ids["question_id"]) as two:
                with one.writer(spec.name):
                    try:
                        with two.writer(spec.name):
                            raise AssertionError("Concurrent writer unexpectedly admitted")
                    except WriterBusy:
                        pass
            assert one.run_details(first["run_id"])["status"] == "succeeded"
        foreign = ResearchService(
            services.output / "foreign", backend=resumed_backend, public_only=False
        )
        foreign_ids = foreign.begin(
            "A different research question", model="no-model-storage-fixture"
        )
        try:
            JobService(foreign).get_job(first["id"])
            raise AssertionError("Foreign question could access original job")
        except ValueError as exc:
            assert "does not belong" in str(exc)
        with resumed_backend.pipeline_store(
            spec, None, question_id=foreign_ids["question_id"]
        ) as store:
            assert store.as_of(spec.name, timestamp(utcnow())) == []
            assert store.recent_runs(spec.name) == []
            assert store.state(spec.name, source.id) is None
            try:
                store.run_details(first["run_id"])
                raise AssertionError("Foreign dataset could read original run")
            except ValueError as exc:
                assert "does not belong" in str(exc)
        with resumed_backend.open_registry() as store:
            assert store.scalar("SELECT MAX(version) FROM schema_migrations") == SCHEMA_VERSION
        (services.output / "recovered-export.jsonl").write_text(recovered["content"])
        report = {
            "status": "passed",
            "mode": resumed_backend.mode,
            "schema_version": SCHEMA_VERSION,
            "backend_services_restarted": True,
            "fresh_backend_client": True,
            "question_id": ids["question_id"],
            "discovery_id": research.discovery_id,
            "pipeline_version_id": pipeline_id,
            "source_requests": len(state["requests"]),
            "collection_job_ids": [first["id"], second["id"]],
            "export_job_id": export["id"],
            "detached_worker_pids": [
                first["worker_pid"],
                second["worker_pid"],
                exported["worker_pid"],
            ],
            "export_sha256": recovered["sha256"],
            "replay": replay,
            "remaining_operation_budgets": resumed.get_context()["remaining"],
            "checks": [
                "search/inspection/probe receipts",
                "atomic proposal registration",
                "detached collection and export workers",
                "queued export cutoff",
                "fresh clients after storage restart",
                "export read from S3 after local deletion",
                "same-operation retry without fetching",
                "replay without publication",
                "Postgres writer exclusion",
                "question namespace isolation",
            ],
        }
        write_json(services.output / "workflow.json", report)
        return report
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--prior-tests",
        type=Path,
        help="Reuse passing suite evidence from a prior attempt with these images",
    )
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    services = Services(output)
    report = {
        "schema_version": 1,
        "started_at": timestamp(utcnow()),
        "status": "failed",
        "paid_calls": 0,
        "fixture_sha256": digest(Path(__file__).read_bytes()),
    }
    try:
        backend = services.start()
        backend.ensure_bucket()
        report["versions"] = {
            "postgres": services.docker("exec", services.names[0], "postgres", "--version"),
            "minio": services.docker("exec", services.names[1], "minio", "--version"),
        }
        report["tests"] = (
            reuse_store_tests(services, args.prior_tests)
            if args.prior_tests
            else store_tests(services)
        )
        report["workflow"] = workflow(services, backend)
        report["status"] = "passed" if report["tests"]["exit_code"] == 0 else "failed"
    except BaseException as exc:
        report["error"] = services.redact(f"{type(exc).__name__}: {exc}")
        (output / "error.log").write_text(services.redact(traceback.format_exc()))
    finally:
        report["containers"] = services.containers
        report["cleanup"] = services.close()
        report["commands"] = services.commands
        report["finished_at"] = timestamp(utcnow())
        if any(not item["removed"] for item in report["cleanup"]):
            report["status"] = "failed"
        for path in output.rglob("*"):
            if path.is_file():
                try:
                    content = path.read_text()
                except UnicodeError:
                    continue
                redacted = services.redact(content)
                if redacted != content:
                    path.write_text(redacted)
        write_json(output / "acceptance.json", report)
        write_json(
            output / "files.json",
            {
                str(path.relative_to(output)): digest(path.read_bytes())
                for path in output.rglob("*")
                if path.is_file() and path.name != "files.json"
            },
        )
    print(
        json.dumps(
            {"status": report["status"], "evidence": str(output), "error": report.get("error")}
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
