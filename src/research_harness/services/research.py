"""Durable research operations, independent of the model or conversation runtime."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from uuid import uuid4

import httpx

from research_harness import registry
from research_harness.backend import Backend
from research_harness.config import PipelineSpec, SourceSpec
from research_harness.connectors import CONNECTOR_CATALOG, PageText, load_json
from research_harness.discovery_models import ProposalDraft
from research_harness.engine import probe_source
from research_harness.http import Fetcher
from research_harness.services.proposals import compile_proposal, render_proposal
from research_harness.services.search import SearchProvider, SearchProviderError
from research_harness.store import Store
from research_harness.util import (
    atomic_write,
    canonical_json,
    digest,
    error_message,
    http_url,
    parse_timestamp,
    timestamp,
    write_json,
)

DEFAULT_LIMITS = {"search": 8, "inspection": 16, "probe": 12}


class ResearchService:
    """One bound discovery; callers resume using its service-issued discovery id."""

    def __init__(
        self,
        output: Path,
        *,
        backend: Backend | None = None,
        http_client: httpx.Client | None = None,
        public_only: bool = True,
        event: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.output = output.resolve()
        self.backend = backend or Backend.from_env()
        self.http_client = http_client
        self.public_only = public_only
        self.event = event or self._event
        self.discovery_id: str | None = None

    def _event(self, event: dict[str, Any]) -> None:
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / "trace.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(canonical_json({"at": timestamp(self.backend.clock()), **event}) + "\n")

    def begin(
        self,
        brief: str,
        *,
        model: str,
        question_id: str | None = None,
        limits: dict[str, int] | None = None,
        deadline_seconds: int = 600,
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        if self.discovery_id is not None:
            raise ValueError("This service is already bound to a discovery")
        chosen = {**DEFAULT_LIMITS, **(limits or {})}
        if set(chosen) != set(DEFAULT_LIMITS) or any(
            type(value) is not int or value < 0 for value in chosen.values()
        ):
            raise ValueError("Discovery limits must be nonnegative search/inspection/probe counts")
        if not 1 <= deadline_seconds <= 86_400:
            raise ValueError("Discovery deadline must be between 1 and 86400 seconds")
        self.output.mkdir(parents=True, exist_ok=True)
        with self.backend.open_registry() as store:
            with store.operation_lock("discovery-start"), store.transaction():
                if question_id:
                    question = registry.get_question(store, question_id)
                    if question["brief"] != brief.strip():
                        raise ValueError(
                            "Changed brief: begin a new question to preserve the original"
                        )
                else:
                    question = registry.register_question(store, brief)
                existing = store.query_one(
                    "SELECT id FROM discoveries WHERE output_ref=?", (str(self.output),)
                )
                if existing:
                    raise ValueError(
                        "This output directory belongs to a discovery; resume it or choose a new directory"
                    )
                discovery_id = registry.start_discovery(
                    store, question["id"], model=model, output_ref=str(self.output)
                )
                store.execute(
                    "INSERT INTO discovery_contexts (discovery_id, limits_json, runtime_json, deadline_at) VALUES (?, ?, ?, ?)",
                    (
                        discovery_id,
                        canonical_json(chosen),
                        canonical_json(runtime or {}),
                        timestamp(store.clock() + timedelta(seconds=deadline_seconds)),
                    ),
                )
        self.discovery_id = discovery_id
        ids = {"question_id": question["id"], "discovery_id": discovery_id}
        write_json(self.output / "context.json", ids)
        return ids

    def resume(self, discovery_id: str) -> dict[str, str]:
        with self.backend.open_registry() as store:
            row = self._load(store, discovery_id)
        if self.discovery_id and self.discovery_id != discovery_id:
            raise ValueError("This service is already bound to another discovery")
        self.discovery_id = discovery_id
        self.output = Path(row["output_ref"])
        return {"question_id": row["question_id"], "discovery_id": discovery_id}

    def _load(self, store: Store, discovery_id: str | None = None) -> dict[str, Any]:
        bound = discovery_id or self.discovery_id
        if not bound:
            raise ValueError("Begin or resume a research discovery first")
        row = store.query_one(
            """SELECT d.*, c.limits_json, c.runtime_json, c.deadline_at
               FROM discoveries d JOIN discovery_contexts c ON c.discovery_id=d.id
               WHERE d.id=?""",
            (bound,),
        )
        if not row:
            raise ValueError("Unknown discovery context")
        return row

    def evidence_store(self) -> Store:
        return self.backend.open_store(self.output / "evidence")

    def _receipts(self, store: Store) -> list[dict[str, Any]]:
        return [
            {
                **{k: v for k, v in row.items() if k != "payload_json"},
                "payload": json.loads(row["payload_json"]),
            }
            for row in store.query(
                "SELECT * FROM discovery_receipts WHERE discovery_id=? ORDER BY created_at, id",
                (self.discovery_id,),
            )
        ]

    @staticmethod
    def _evidence_state(
        receipts: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        probes: dict[str, dict[str, Any]] = {}
        observed: set[str] = set()
        for receipt in receipts:
            payload = receipt["payload"]
            if receipt["kind"] == "search":
                observed.update(result["url"] for result in payload["results"])
            elif receipt["kind"] == "inspection":
                observed.update([payload["requested_url"], payload["url"]])
            elif receipt["kind"] == "probe":
                probes[payload["probe_id"]] = payload
                for capture in payload["captures"]:
                    if capture.get("status_code") is not None:
                        observed.add(capture["url"])
        return probes, observed

    def get_context(self) -> dict[str, Any]:
        with self.backend.open_registry() as store:
            context = self._load(store)
            receipts = self._receipts(store)
            counts = {
                row["kind"]: int(row["n"])
                for row in store.query(
                    "SELECT kind, COUNT(*) AS n FROM discovery_operations WHERE discovery_id=? GROUP BY kind",
                    (self.discovery_id,),
                )
            }
            question = registry.get_question(store, context["question_id"])
            pipelines = registry.list_pipelines(store, question_id=question["id"])
            operations = [
                {
                    **{k: v for k, v in row.items() if k != "result_json"},
                    "result": json.loads(row["result_json"]) if row["result_json"] else None,
                }
                for row in store.query(
                    """SELECT operation_id, kind, status, created_at, finished_at, result_json
                       FROM discovery_operations WHERE discovery_id=? ORDER BY created_at, operation_id""",
                    (self.discovery_id,),
                )
            ]
        limits = json.loads(context["limits_json"])
        for pipeline in pipelines:
            with self.backend.open_registry() as store:
                _, spec = registry.pipeline_spec(store, pipeline["id"])
            pipeline["legacy_unscoped_data"] = self.backend.legacy_pipeline_data(spec)
            with self.backend.pipeline_store(spec, None, question_id=question["id"]) as data:
                pipeline["latest_run"] = data.latest_run(spec.name, spec.fingerprint())
        probes, observed = self._evidence_state(receipts)
        return {
            "question_id": question["id"],
            "discovery_id": self.discovery_id,
            "brief": question["brief"],
            "status": context["status"],
            "model": context["model"],
            "runtime": json.loads(context["runtime_json"]),
            "deadline_at": context["deadline_at"],
            "limits": limits,
            "counts": counts,
            "remaining": {
                kind: max(0, limit - counts.get(kind, 0)) for kind, limit in limits.items()
            },
            "receipts": receipts,
            "operations": operations,
            "probes": probes,
            "observed_urls": sorted(observed),
            "pipelines": pipelines,
            "connector_catalog": CONNECTOR_CATALOG,
            "source_schema": SourceSpec.model_json_schema(),
            "proposal_schema": ProposalDraft.model_json_schema(),
        }

    def _export_evidence(self, store: Store) -> None:
        receipts = self._receipts(store)
        probes, _ = self._evidence_state(receipts)
        write_json(self.output / "receipts.json", receipts)
        write_json(self.output / "probes.json", list(probes.values()))

    def _operate(
        self,
        kind: str,
        request: dict[str, Any],
        action: Callable[[Store], dict[str, Any]],
        *,
        operation_id: str | None,
        evidence: bool = True,
        atomic_action: bool = False,
    ) -> dict[str, Any]:
        if operation_id is None:
            operation_id = uuid4().hex
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:
            raise ValueError("Operation id must contain between 1 and 200 characters")
        request_json = canonical_json(request)
        request_hash = digest(canonical_json({"kind": kind, "request": request}))
        with self.backend.open_registry() as store:
            context = self._load(store)
            # The lock outlives network work; a retry cannot race an active operation.
            with store.operation_lock(f"discovery:{self.discovery_id}"):
                existing = store.query_one(
                    "SELECT * FROM discovery_operations WHERE discovery_id=? AND operation_id=?",
                    (self.discovery_id, operation_id),
                )
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise ValueError("Operation id was already used with different arguments")
                    if existing["status"] == "completed":
                        self._export_evidence(store)
                        return json.loads(existing["result_json"])
                    if existing["result_json"]:
                        raise ValueError(json.loads(existing["result_json"])["error"])
                    # Acquiring the exclusive lock proves that no service owner is still executing.
                    error = (
                        "Previous operation was interrupted; use a new id for a fresh observation"
                    )
                    with store.transaction():
                        store.execute(
                            "UPDATE discovery_operations SET status='interrupted', result_json=?, finished_at=? WHERE discovery_id=? AND operation_id=?",
                            (
                                canonical_json({"status": "error", "error": error}),
                                timestamp(store.clock()),
                                self.discovery_id,
                                operation_id,
                            ),
                        )
                    raise ValueError(error)
                context = self._load(store)
                if context["status"] != "running":
                    raise ValueError(
                        "Discovery is finished; begin a new discovery for further research"
                    )
                if store.clock() >= parse_timestamp(context["deadline_at"]):
                    raise ValueError("Discovery deadline exhausted")
                limits = json.loads(context["limits_json"])
                count = int(
                    store.scalar(
                        "SELECT COUNT(*) AS n FROM discovery_operations WHERE discovery_id=? AND kind=?",
                        (self.discovery_id, kind),
                    )
                )
                if kind in limits and count >= limits[kind]:
                    raise ValueError(f"{kind.capitalize()} budget exhausted")
                with store.transaction():
                    store.execute(
                        """INSERT INTO discovery_operations
                           (discovery_id, operation_id, kind, request_hash, request_json, status, created_at)
                           VALUES (?, ?, ?, ?, ?, 'running', ?)""",
                        (
                            self.discovery_id,
                            operation_id,
                            kind,
                            request_hash,
                            request_json,
                            timestamp(store.clock()),
                        ),
                    )
                try:
                    if atomic_action:
                        with store.transaction():
                            result = action(store)
                            result = self._complete(store, kind, operation_id, result, evidence)
                    else:
                        result = action(store)
                        with store.transaction():
                            result = self._complete(store, kind, operation_id, result, evidence)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {error_message(exc)}"
                    failure = {"status": "error", "error": error}
                    if isinstance(exc, SearchProviderError):
                        failure["provider_response"] = exc.provider_response
                    with store.transaction():
                        store.execute(
                            "UPDATE discovery_operations SET status='failed', result_json=?, finished_at=? WHERE discovery_id=? AND operation_id=?",
                            (
                                canonical_json(failure),
                                timestamp(store.clock()),
                                self.discovery_id,
                                operation_id,
                            ),
                        )
                    self.event(
                        {
                            "event": "service_operation_failed",
                            "kind": kind,
                            "operation_id": operation_id,
                            **failure,
                        }
                    )
                    raise
                self._export_evidence(store)
                if evidence:
                    self.event(
                        {
                            "event": "service_receipt",
                            "kind": kind,
                            "receipt_id": result["receipt_id"],
                            "discovery_id": self.discovery_id,
                            "result": result,
                        }
                    )
                return result

    def _complete(
        self, store: Store, kind: str, operation_id: str, result: dict[str, Any], evidence: bool
    ) -> dict[str, Any]:
        result = {**result, "operation_id": operation_id, "discovery_id": self.discovery_id}
        if evidence:
            result["receipt_id"] = uuid4().hex
            store.execute(
                "INSERT INTO discovery_receipts (id, discovery_id, operation_id, kind, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    result["receipt_id"],
                    self.discovery_id,
                    operation_id,
                    kind,
                    canonical_json(result),
                    timestamp(store.clock()),
                ),
            )
        store.execute(
            "UPDATE discovery_operations SET status='completed', result_json=?, finished_at=? WHERE discovery_id=? AND operation_id=?",
            (canonical_json(result), timestamp(store.clock()), self.discovery_id, operation_id),
        )
        return result

    def search(
        self,
        query: str,
        *,
        provider: SearchProvider,
        filters: dict[str, Any] | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000:
            raise ValueError("Search query must contain between 1 and 2000 characters")
        request = {"provider": provider.name, "query": query.strip(), "filters": filters or {}}

        def execute(_store: Store) -> dict[str, Any]:
            response = provider.search(request["query"], request["filters"])
            if not isinstance(response.get("results"), list):
                raise ValueError("Search provider must return a results list")
            results = []
            for result in response["results"]:
                if not isinstance(result, dict):
                    raise ValueError("Search provider returned a malformed result")
                results.append({**result, "url": http_url(result["url"])})
            return {**response, **request, "results": results}

        return self._operate("search", request, execute, operation_id=operation_id)

    def record_search(
        self, response: dict[str, Any], *, provider: str, operation_id: str | None = None
    ) -> dict[str, Any]:
        """Record native provider output supplied by trusted runtime code, never by an agent tool."""
        action = response.get("action") or {}
        results = []
        sources = list(action.get("sources") or [])
        if action.get("type") in {"open_page", "find_in_page"} and action.get("url"):
            sources.append({"url": action["url"]})
        for source in sources:
            try:
                url = http_url(source["url"])
            except (KeyError, TypeError, ValueError):
                continue
            results.append({"url": url, "title": source.get("title")})

        def observe(_store: Store) -> dict[str, Any]:
            if response.get("status") != "completed":
                raise SearchProviderError(
                    f"Native search did not complete: {response.get('status')}", response
                )
            return {
                "provider": provider,
                "query": action.get("query"),
                "results": results,
                "provider_response": response,
            }

        return self._operate(
            "search",
            {"provider": provider, "response": response},
            observe,
            operation_id=operation_id,
        )

    def probe(self, source: SourceSpec, *, operation_id: str | None = None) -> dict[str, Any]:
        def execute(_store: Store) -> dict[str, Any]:
            with self.evidence_store() as evidence:
                return probe_source(
                    source,
                    evidence,
                    client=self.http_client,
                    public_only=self.public_only,
                    clock=self.backend.clock,
                )

        return self._operate(
            "probe",
            {"source": source.model_dump(mode="json")},
            execute,
            operation_id=operation_id,
        )

    def inspect(self, url: str, *, operation_id: str | None = None) -> dict[str, Any]:
        http_url(url)
        return self._operate(
            "inspection",
            {"url": url},
            lambda _store: self._inspect(url),
            operation_id=operation_id,
        )

    def _inspect(self, url: str) -> dict[str, Any]:
        source = SourceSpec(id="inspection", name="Discovery inspection", connector="html", url=url)
        spec = PipelineSpec(
            name="discovery-inspection", description="Public source inspection", sources=[source]
        )
        owned = self.http_client is None
        client = self.http_client or httpx.Client()
        try:
            with self.evidence_store() as store, store.writer(spec.name, shared=True):
                run_id = store.start_run(spec)
                source_run_id = store.start_source(run_id, source)
                try:
                    capture = Fetcher(
                        store,
                        spec.name,
                        source_run_id,
                        source.id,
                        spec.http,
                        client=client,
                        public_only=self.public_only,
                        clock=self.backend.clock,
                    ).fetch(url, context={"role": "inspection"})
                    body = store.read_blob(capture.body_hash or "")
                    content_type = capture.headers.get("content-type", "")
                    report: dict[str, Any] = {
                        "capture_id": capture.id,
                        "requested_url": url,
                        "url": capture.url,
                        "status_code": capture.status_code,
                        "observed_at": capture.observed_at,
                        "body_sha256": capture.body_hash,
                        "content_type": content_type,
                    }
                    if "json" in content_type or body.lstrip().startswith((b"{", b"[")):
                        parsed = load_json(body)
                        report["top_level_type"] = type(parsed).__name__
                        report["top_level_keys"] = (
                            sorted(parsed) if isinstance(parsed, dict) else []
                        )
                        report["preview"] = canonical_json(parsed)[:6500]
                    else:
                        page = PageText()
                        page.feed(body.decode("utf-8", "replace"))
                        report["title"] = " ".join(page.title)
                        report["preview"] = page.text[:6500]
                        report["links"] = [
                            {**link, "href": urljoin(capture.url, link["href"])}
                            for link in page.links[:50]
                        ]
                    store.finish_source(
                        spec.name, source_run_id, source, [], [], checkpoint={"inspection": True}
                    )
                    store.finish_run(run_id, "inspected")
                    return report
                except Exception as exc:
                    store.finish_source(
                        spec.name,
                        source_run_id,
                        source,
                        [],
                        [],
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    store.finish_run(run_id, "failed", str(exc))
                    raise
        finally:
            if owned:
                client.close()

    def get_evidence(
        self, receipt_id: str, *, capture_id: str | None = None, offset: int = 0, limit: int = 8000
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 50_000:
            raise ValueError("Evidence range must have offset >= 0 and limit between 1 and 50000")
        with self.backend.open_registry() as store:
            self._load(store)
            receipt = next((r for r in self._receipts(store) if r["id"] == receipt_id), None)
        if receipt is None:
            raise ValueError("Evidence does not belong to this discovery")
        payload = receipt["payload"]
        captures = payload.get("captures") or ([payload] if "capture_id" in payload else [])
        capture = (
            next((c for c in captures if c["capture_id"] == capture_id), None)
            if capture_id
            else (captures[0] if captures else None)
        )
        if capture_id and capture is None:
            raise ValueError("Capture does not belong to this evidence receipt")
        hashed = capture.get("body_sha256") if capture else None
        if hashed:
            with self.evidence_store() as store:
                content = store.read_blob(hashed).decode("utf-8", "replace")
        else:
            content = canonical_json(payload)
        return {
            "receipt_id": receipt_id,
            "discovery_id": self.discovery_id,
            "kind": receipt["kind"],
            "capture_id": capture["capture_id"] if capture else None,
            "body_sha256": hashed,
            "content": content[offset : offset + limit],
            "offset": offset,
            "next_offset": offset + limit if offset + limit < len(content) else None,
            "total_characters": len(content),
        }

    def submit_proposal(
        self,
        draft: ProposalDraft,
        *,
        operation_id: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        def submit(store: Store) -> dict[str, Any]:
            context = self._load(store)
            receipts = self._receipts(store)
            searches = sum(r["kind"] == "search" for r in receipts)
            if not searches:
                raise ValueError("Use web_search to discover current sources before submitting")
            search_attempts = int(
                store.scalar(
                    "SELECT COUNT(*) FROM discovery_operations WHERE discovery_id=? AND kind='search'",
                    (self.discovery_id,),
                )
            )
            probes, observed = self._evidence_state(receipts)
            pipeline, gaps = compile_proposal(draft, probes, observed)
            status = (
                "proposed"
                if pipeline and not gaps
                else ("proposed_with_gaps" if pipeline else "research_only")
            )
            result = {
                "status": status,
                "created_at": timestamp(store.clock()),
                "model": context["model"],
                "usage": usage or {},
                "web_search_calls": search_attempts,
                "successful_searches": searches,
                "proposal": draft.model_dump(mode="json"),
                "uncovered_required_needs": gaps,
                "verified_source_count": len(pipeline.sources) if pipeline else 0,
                "observed_urls": sorted(observed),
            }
            markdown = render_proposal(draft, gaps)
            ids = {"question_id": context["question_id"], "discovery_id": self.discovery_id}
            ids["proposal_id"] = registry.record_proposal(
                store,
                discovery_id=context["id"],
                question_id=context["question_id"],
                result=result,
                proposal_md=markdown,
            )
            if pipeline is not None:
                version = registry.register_pipeline(
                    store,
                    context["question_id"],
                    pipeline,
                    proposal_id=ids["proposal_id"],
                    origin="discovery",
                    label=f"Discovery with {context['model']}",
                )
                ids["pipeline_version_id"] = version["id"]
            result["registry"] = ids
            store.execute(
                "UPDATE proposals SET proposal_json=? WHERE id=?",
                (canonical_json(result), ids["proposal_id"]),
            )
            registry.finish_discovery(store, context["id"], status=status, usage=usage)
            return result

        result = self._operate(
            "proposal",
            {"draft": draft.model_dump(mode="json"), "usage": usage or {}},
            submit,
            operation_id=operation_id,
            evidence=False,
            atomic_action=True,
        )
        # Files are recoverable exports; the registry and ledger committed together above.
        self.export_proposal(result["registry"]["proposal_id"])
        return result

    def export_proposal(self, proposal_id: str) -> None:
        with self.backend.open_registry() as store:
            self._load(store)
            row = store.query_one(
                "SELECT * FROM proposals WHERE id=? AND discovery_id=?",
                (proposal_id, self.discovery_id),
            )
            if not row:
                raise ValueError("Proposal does not belong to this discovery")
            result = json.loads(row["proposal_json"])
            pipeline_id = result["registry"].get("pipeline_version_id")
            pipeline = registry.get_pipeline(store, pipeline_id) if pipeline_id else None
        write_json(self.output / "proposal.json", result)
        atomic_write(self.output / "proposal.md", row["proposal_md"])
        if pipeline:
            write_json(self.output / "pipeline.json", pipeline["spec"])

    def finish_failure(self, exc: BaseException, usage: dict[str, Any]) -> None:
        if self.discovery_id:
            with self.backend.open_registry() as store:
                with store.operation_lock(f"discovery:{self.discovery_id}"):
                    if self._load(store)["status"] == "running":
                        registry.finish_discovery(
                            store,
                            self.discovery_id,
                            status="failed",
                            usage=usage,
                            error=f"{type(exc).__name__}: {exc}",
                        )
