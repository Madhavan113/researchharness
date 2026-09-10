from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from research_harness.config import PipelineSpec, SourceSpec
from research_harness.connectors import SourceContractError, load_json, normalize
from research_harness.http import Fetcher, OperationCancelled
from research_harness.records import NORMALIZER_VERSION, Capture, Issue, Record
from research_harness.store import Store
from research_harness.util import (
    canonical_json,
    digest,
    json_pointer,
    parse_timestamp,
    timestamp,
    utcnow,
    write_json,
)


def source_url(source: SourceSpec) -> str:
    return str(
        httpx.URL(source.url).copy_merge_params(
            [(parameter.name, parameter.value) for parameter in source.parameters]
        )
    )


class Collector:
    def __init__(self, source: SourceSpec, fetcher: Fetcher, store: Store):
        self.source = source
        self.fetcher = fetcher
        self.store = store
        self.records: list[tuple[Record, Capture]] = []
        self.issues: list[tuple[str | None, Issue]] = []
        self.captures: list[Capture] = []
        self.seen: dict[tuple[str, str], str] = {}
        self.pages = 0

    def ingest(self, capture: Capture) -> list[Record]:
        self.captures.append(capture)
        if capture.body_hash is None:
            raise SourceContractError("Successful capture has no body")
        try:
            parsed = normalize(self.source, capture, self.store.read_blob(capture.body_hash))
        except SourceContractError as exc:
            self.issues.append((capture.id, Issue("response", str(exc))))
            raise
        self.issues.extend((capture.id, issue) for issue in parsed.issues)
        accepted = []
        for record in parsed.records:
            identity = (record.kind, record.key)
            prior = self.seen.get(identity)
            if prior and prior != record.content_hash:
                self.issues.append(
                    (
                        capture.id,
                        Issue(
                            record.key, "Conflicting duplicate identity within one source snapshot"
                        ),
                    )
                )
                continue
            if prior:
                continue
            self.seen[identity] = record.content_hash
            self.records.append((record, capture))
            accepted.append(record)
            if len(self.records) > self.source.max_records:
                raise SourceContractError("Record limit exceeded; snapshot is incomplete")
        return accepted

    def collect_books(self, record: Record) -> None:
        market = record.data
        if market["status"] != "open":
            return
        if market["venue"] == "polymarket":
            for outcome in market["outcomes"]:
                token = outcome.get("token_id")
                if not token or not token.isdecimal():
                    self.issues.append(
                        (None, Issue(record.key, "Open market is missing a valid CLOB token id"))
                    )
                    continue
                url = str(
                    httpx.URL("https://clob.polymarket.com/book").copy_merge_params(
                        {"token_id": token}
                    )
                )
                capture = self.fetcher.fetch(
                    url,
                    conditional=False,
                    allow_404=True,
                    context={
                        "role": "orderbook",
                        "venue": "polymarket",
                        "market_key": record.key,
                        "outcome": outcome["label"],
                        "token_id": token,
                    },
                )
                self.ingest(capture)
        else:
            parts = urlsplit(self.source.url)
            url = f"{parts.scheme}://{parts.netloc}/trade-api/v2/markets/{quote(market['contract_id'], safe='')}/orderbook?depth=100"
            self.ingest(
                self.fetcher.fetch(
                    url,
                    conditional=False,
                    allow_404=True,
                    context={
                        "role": "orderbook",
                        "venue": "kalshi",
                        "market_key": record.key,
                        "depth_limit": 100,
                    },
                )
            )

    def collect(self, *, sample_only: bool = False) -> dict[str, Any]:
        initial = source_url(self.source)
        current = initial
        visited: set[str] = set()
        while True:
            if current in visited:
                raise SourceContractError(
                    "Pagination repeated a URL or cursor; refusing an incomplete snapshot"
                )
            visited.add(current)
            capture = self.fetcher.fetch(current)
            self.pages += 1
            records = self.ingest(capture)
            if self.source.include_orderbooks and not sample_only:
                for record in records:
                    if record.kind == "market":
                        self.collect_books(record)
            if self.source.pagination.mode == "none":
                if self.source.connector in {"json", "polymarket", "kalshi"}:
                    payload = load_json(self.store.read_blob(capture.body_hash or ""))
                    for pointer in [
                        "/next",
                        "/next_page_url",
                        "/next_cursor",
                        "/cursor",
                        "/links/next",
                        "/pagination/next",
                    ]:
                        try:
                            advertised = json_pointer(payload, pointer)
                        except (KeyError, IndexError, ValueError):
                            continue
                        if advertised:
                            raise SourceContractError(
                                f"Response advertises continuation at {pointer}; configure pagination before collecting"
                            )
                break
            payload = load_json(self.store.read_blob(capture.body_hash or ""))
            try:
                next_value = json_pointer(payload, self.source.pagination.next_pointer or "")
            except (KeyError, ValueError, IndexError) as exc:
                if self.source.pagination.stop_when_missing:
                    break
                raise SourceContractError(
                    "Pagination field is missing; completeness cannot be established"
                ) from exc
            if next_value is None or next_value == "":
                break
            if not isinstance(next_value, str):
                raise SourceContractError("Pagination token or next URL must be a string")
            if sample_only:
                break
            if self.pages >= self.source.max_pages:
                raise SourceContractError(
                    "Page limit reached with more pages remaining; snapshot is incomplete"
                )
            if self.source.pagination.mode == "cursor":
                current = str(
                    httpx.URL(initial).copy_merge_params(
                        {self.source.pagination.cursor_parameter or "cursor": next_value}
                    )
                )
            else:
                current = urljoin(capture.url, next_value)
                if urlsplit(current).netloc != urlsplit(initial).netloc:
                    raise SourceContractError(
                        "Pagination changed origin; define the other endpoint as a separate source"
                    )
        primary_count = sum(record.kind != "orderbook" for record, _ in self.records)
        if primary_count < self.source.min_records:
            self.issues.append(
                (
                    None,
                    Issue(
                        "source",
                        f"Expected at least {self.source.min_records} records; found {primary_count}",
                    ),
                )
            )
        return {
            "last_completed_observation": self.captures[-1].observed_at if self.captures else None,
            "pages": self.pages,
            "pagination_complete": not sample_only,
            "poll_strategy": "restart enumeration; conditional fetches and version deduplication",
        }


def run_pipeline(
    spec: PipelineSpec,
    store: Store,
    *,
    client: httpx.Client | None = None,
    only_source: str | None = None,
    due_only: bool = False,
    public_only: bool = True,
    clock: Callable[[], datetime] = utcnow,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    run_id: str | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if only_source and not any(source.id == only_source for source in spec.sources):
        raise ValueError(f"Unknown source: {only_source}")
    owned_client = client is None
    client = client or httpx.Client()
    try:
        with store.writer(spec.name):
            run_id = store.start_run(spec, run_id=run_id)
            summaries = []
            try:
                for source in spec.sources:
                    if check_cancelled:
                        check_cancelled()
                    if not source.enabled or (only_source and source.id != only_source):
                        continue
                    state = store.state(spec.name, source.id)
                    if (
                        due_only
                        and state
                        and state["config_hash"] == source.fingerprint()
                        and (clock() - parse_timestamp(state["last_success_at"])).total_seconds()
                        < source.poll_interval_seconds
                    ):
                        summaries.append({"source_id": source.id, "status": "not_due"})
                        continue
                    source_run_id = store.start_source(run_id, source)
                    fetcher = Fetcher(
                        store,
                        spec.name,
                        source_run_id,
                        source.id,
                        spec.http,
                        client=client,
                        clock=clock,
                        public_only=public_only,
                        check_cancelled=check_cancelled,
                    )
                    collector = Collector(source, fetcher, store)
                    error = None
                    checkpoint = None
                    cancelled = None
                    try:
                        checkpoint = collector.collect()
                        if check_cancelled:
                            check_cancelled()
                    except OperationCancelled as exc:
                        cancelled = exc
                        error = str(exc)
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                    summary = store.finish_source(
                        spec.name,
                        source_run_id,
                        source,
                        collector.records,
                        collector.issues,
                        error=error,
                        checkpoint=checkpoint,
                        failure_status="cancelled" if cancelled else "failed",
                    )
                    summary.update({"requests": fetcher.requests, "pages": collector.pages})
                    summaries.append(summary)
                    if on_progress:
                        on_progress(summary)
                    if cancelled:
                        raise cancelled
                status = (
                    "succeeded"
                    if all(summary["status"] in {"succeeded", "not_due"} for summary in summaries)
                    else "failed"
                )
                store.finish_run(run_id, status)
                return {
                    "run_id": run_id,
                    "pipeline": spec.name,
                    "status": status,
                    "sources": summaries,
                }
            except OperationCancelled as exc:
                store.finish_run(run_id, "cancelled", str(exc))
                return {
                    "run_id": run_id,
                    "pipeline": spec.name,
                    "status": "cancelled",
                    "sources": summaries,
                    "error": str(exc),
                }
            except BaseException as exc:
                with store.transaction():
                    store.execute(
                        "UPDATE source_runs SET status='interrupted', finished_at=?, error=? WHERE run_id=? AND status='running'",
                        (timestamp(clock()), type(exc).__name__, run_id),
                    )
                store.finish_run(run_id, "interrupted", type(exc).__name__)
                raise
    finally:
        if owned_client:
            client.close()


def probe_source(
    source: SourceSpec,
    store: Store,
    *,
    client: httpx.Client | None = None,
    public_only: bool = True,
    clock: Callable[[], datetime] = utcnow,
) -> dict[str, Any]:
    """Validate one captured page. A sample probe is never published as a complete dataset."""
    spec = PipelineSpec(
        name="source-probes", description="Bounded discovery probes", sources=[source]
    )
    owned_client = client is None
    client = client or httpx.Client()
    try:
        with store.writer(spec.name, shared=True):
            run_id = store.start_run(spec)
            source_run_id = store.start_source(run_id, source)
            fetcher = Fetcher(
                store,
                spec.name,
                source_run_id,
                source.id,
                spec.http,
                client=client,
                public_only=public_only,
                clock=clock,
            )
            collector = Collector(source, fetcher, store)
            error = None
            try:
                collector.collect(sample_only=True)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            # Preserve captures/quarantine; probes do not advance ingestion state or publish records.
            store.finish_probe(source_run_id, collector.issues, error)
            passed = not error and not collector.issues and bool(collector.records)
            store.finish_run(run_id, "probed" if passed else "probe_failed", error)
            captures = store.captures_for_run(run_id)
            return {
                "probe_id": run_id,
                "source_id": source.id,
                "source_fingerprint": source.fingerprint(),
                "status": "verified_sample" if passed else "failed",
                "url": source_url(source),
                "observed_at": timestamp(clock()),
                "record_count": len(collector.records),
                "record_kinds": sorted({record.kind for record, _ in collector.records}),
                "publication_profile": {
                    "records_with_declared_timestamp": sum(
                        record.published_at is not None for record, _ in collector.records
                    ),
                    "oldest_declared_timestamp": min(
                        (
                            record.published_at
                            for record, _ in collector.records
                            if record.published_at
                        ),
                        default=None,
                    ),
                    "newest_declared_timestamp": max(
                        (
                            record.published_at
                            for record, _ in collector.records
                            if record.published_at
                        ),
                        default=None,
                    ),
                },
                "sample_fields": sorted(
                    {key for record, _ in collector.records[:3] for key in record.data}
                ),
                "samples": [
                    {
                        "kind": record.kind,
                        "key": record.key,
                        "title": record.data.get("title"),
                        "published_at": record.published_at,
                        "content_scope": record.data.get("content_scope"),
                        "data_preview": canonical_json(record.data)[:2500],
                    }
                    for record, _ in collector.records[:2]
                ],
                "issues": [issue.error for _, issue in collector.issues],
                "error": error,
                "captures": [
                    {
                        "capture_id": capture.id,
                        "url": capture.url,
                        "body_sha256": capture.body_hash,
                        "status_code": capture.status_code,
                    }
                    for capture in captures
                ],
                "limits": [
                    "One-page structural sample; pagination completeness, historical coverage, and sustained availability are not established.",
                    "Order-book follow-up requests are tested during pipeline execution.",
                ],
            }
    finally:
        if owned_client:
            client.close()


def replay_run(store: Store, run_id: str) -> dict[str, Any]:
    details = store.run_details(run_id)
    spec = PipelineSpec.model_validate(details["config"])
    sources = {source.id: source for source in spec.sources}
    records = []
    issues = []
    for capture in store.captures_for_run(run_id):
        if (
            capture.error
            or not capture.body_hash
            or capture.context.get("role") not in {"primary", "orderbook"}
        ):
            continue
        if capture.status_code not in {200, 304, 404}:
            continue
        try:
            parsed = normalize(
                sources[capture.source_id], capture, store.read_blob(capture.body_hash)
            )
            records.extend(
                {
                    "source_id": capture.source_id,
                    "kind": record.kind,
                    "key": record.key,
                    "data": record.data,
                    "published_at": record.published_at,
                    "content_hash": record.content_hash,
                    "observed_at": capture.observed_at,
                    "capture_id": capture.id,
                }
                for record in parsed.records
            )
            issues.extend(
                {"capture_id": capture.id, "location": issue.location, "error": issue.error}
                for issue in parsed.issues
            )
        except (SourceContractError, RuntimeError, OSError) as exc:
            issues.append({"capture_id": capture.id, "error": str(exc)})
    return {
        "run_id": run_id,
        "normalizer_version": NORMALIZER_VERSION,
        "replayed_at": timestamp(),
        "network_requests": 0,
        "published": False,
        "records": records,
        "issues": issues,
    }


def export_dataset(
    spec: PipelineSpec, store: Store, cutoff: datetime, output: Path, kind: str | None = None
) -> dict[str, Any]:
    cutoff_text = timestamp(cutoff)
    source_ids = {source.id for source in spec.sources}
    records = [
        record
        for record in store.as_of(spec.name, cutoff_text, kind)
        if record["source_id"] in source_ids
    ]
    content = "".join(canonical_json(record) + "\n" for record in records)
    from research_harness.util import atomic_write

    atomic_write(output, content)
    health = store.health(spec, cutoff_text)
    for source in health:
        last_success = source["last_success_at"]
        source["stale"] = (
            last_success is None
            or (cutoff - parse_timestamp(last_success)).total_seconds()
            > source["poll_interval_seconds"]
        )
    manifest = {
        "pipeline": spec.name,
        "dataset_scope": store.dataset_scope,
        "config_fingerprint": spec.fingerprint(),
        "as_of": cutoff_text,
        "record_count": len(records),
        "kind_filter": kind,
        "data_sha256": digest(content),
        "normalizer_version": NORMALIZER_VERSION,
        "sources": health,
        "selection": "Latest successfully published observation per source/kind/key available by the cutoff, limited to source ids in this configuration. Use a saved historical configuration to reproduce its source universe. Absence from a later response is not interpreted as deletion.",
    }
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    write_json(manifest_path, manifest)
    return {"output": str(output), "manifest": str(manifest_path), **manifest}
