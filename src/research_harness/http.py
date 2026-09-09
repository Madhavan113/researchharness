from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit
from uuid import uuid4

import httpx

from research_harness.config import HttpSettings
from research_harness.records import Capture
from research_harness.store import Store
from research_harness.util import http_url, timestamp, utcnow


class FetchError(RuntimeError):
    pass


class OperationCancelled(RuntimeError):
    pass


def validate_public_url(url: str) -> None:
    """Reject local destinations before requests and again on every redirect."""
    http_url(url)
    parts = urlsplit(url)
    if parts.port not in {None, 80, 443}:
        raise ValueError("Only public HTTP/HTTPS ports are supported")
    host = parts.hostname or ""
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Local destinations are not research sources")
    for key, _value in parse_qsl(parts.query):
        if key.lower().replace("-", "_") in {
            "api_key",
            "apikey",
            "access_token",
            "token",
            "secret",
            "password",
            "authorization",
        }:
            raise ValueError("Source URLs must not contain credentials")
    addresses = socket.getaddrinfo(
        host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM
    )
    if not addresses:
        raise ValueError("Source hostname did not resolve")
    if any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
        raise ValueError("Source hostname resolves to a nonpublic address")


def retry_delay(value: str | None, attempt: int, now: datetime) -> float:
    if value:
        try:
            return max(0, float(value))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return max(0, (parsed - now).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return 0.5 * (2 ** (attempt - 1))


class Fetcher:
    def __init__(
        self,
        store: Store,
        pipeline: str,
        source_run_id: str,
        source_id: str,
        settings: HttpSettings,
        *,
        client: httpx.Client,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] = time.sleep,
        public_only: bool = True,
        check_cancelled: Callable[[], None] | None = None,
    ):
        self.store = store
        self.pipeline = pipeline
        self.source_run_id = source_run_id
        self.source_id = source_id
        self.settings = settings
        self.client = client
        self.clock = clock
        self.sleep = sleep
        self.public_only = public_only
        self.check_cancelled = check_cancelled
        self.requests = 0
        self.last_request = 0.0

    def _check_cancelled(self) -> None:
        if self.check_cancelled:
            self.check_cancelled()

    def _sleep(self, seconds: float) -> None:
        if self.check_cancelled is None:
            self.sleep(seconds)
            return
        while seconds > 0:
            self._check_cancelled()
            interval = min(seconds, 0.25)
            self.sleep(interval)
            seconds -= interval
        self._check_cancelled()

    def _capture(
        self,
        url: str,
        context: dict[str, Any],
        status: int | None,
        headers: dict[str, str],
        body: bytes | None,
        error: str | None,
        reused: Capture | None = None,
    ) -> Capture:
        capture = Capture(
            id=uuid4().hex,
            source_id=self.source_id,
            url=url,
            observed_at=timestamp(self.clock()),
            status_code=status,
            body_hash=reused.body_hash
            if reused
            else self.store.put_blob(body)
            if body is not None
            else None,
            headers=headers,
            context=context,
            error=error,
            reused_capture_id=reused.id if reused else None,
        )
        self.store.save_capture(self.source_run_id, capture)
        return capture

    def fetch(
        self,
        url: str,
        *,
        context: dict[str, Any] | None = None,
        allow_404: bool = False,
        conditional: bool = True,
    ) -> Capture:
        context = context or {"role": "primary"}
        current = url
        redirects = 0
        attempt = 0
        while True:
            self._check_cancelled()
            if self.requests >= self.settings.max_requests_per_source:
                raise FetchError("Source request budget exhausted; snapshot is incomplete")
            try:
                if self.public_only:
                    validate_public_url(current)
                else:
                    http_url(current)
            except (ValueError, OSError) as exc:
                self._capture(current, context, None, {}, None, f"URL validation: {exc}")
                raise FetchError(f"URL validation failed: {exc}") from exc
            cached = (
                self.store.cached_capture(self.pipeline, self.source_id, current)
                if conditional
                else None
            )
            headers = {
                "User-Agent": "ResearchHarness/0.1 (+public source research)",
                "Accept": "application/json, application/rss+xml, application/atom+xml, text/html, */*",
            }
            if cached:
                if cached.headers.get("etag"):
                    headers["If-None-Match"] = cached.headers["etag"]
                elif cached.headers.get("last-modified"):
                    headers["If-Modified-Since"] = cached.headers["last-modified"]
            elapsed = time.monotonic() - self.last_request
            if elapsed < self.settings.min_interval_seconds:
                self._sleep(self.settings.min_interval_seconds - elapsed)
            self.last_request = time.monotonic()
            self.requests += 1
            attempt += 1
            status: int | None = None
            response_headers: dict[str, str] = {}
            try:
                with self.client.stream(
                    "GET",
                    current,
                    headers=headers,
                    follow_redirects=False,
                    timeout=self.settings.timeout_seconds,
                ) as response:
                    status = response.status_code
                    response_headers = {
                        key: response.headers[key]
                        for key in [
                            "etag",
                            "last-modified",
                            "content-type",
                            "date",
                            "retry-after",
                            "location",
                        ]
                        if key in response.headers
                    }
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        try:
                            self._check_cancelled()
                        except OperationCancelled as exc:
                            self._capture(
                                current,
                                {**context, "interrupted": True},
                                status,
                                response_headers,
                                bytes(body),
                                str(exc),
                            )
                            raise
                        remaining = self.settings.max_response_bytes - len(body)
                        body.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            self._capture(
                                current,
                                {**context, "truncated": True},
                                status,
                                response_headers,
                                bytes(body),
                                "Response body exceeds configured limit",
                            )
                            raise FetchError("Response body exceeds configured limit")
                payload = bytes(body)
            except httpx.TransportError as exc:
                self._capture(
                    current,
                    context,
                    status,
                    response_headers,
                    None,
                    type(exc).__name__ + ": " + str(exc),
                )
                if attempt >= self.settings.max_attempts:
                    raise FetchError(
                        f"Request failed after {attempt} attempts: {type(exc).__name__}"
                    ) from exc
                self._sleep(
                    min(
                        retry_delay(None, attempt, self.clock()),
                        self.settings.max_retry_delay_seconds,
                    )
                )
                continue
            if status in {301, 302, 303, 307, 308}:
                self._capture(
                    current,
                    {**context, "role": "redirect"},
                    status,
                    response_headers,
                    payload,
                    None,
                )
                redirects += 1
                if redirects > 5 or not response_headers.get("location"):
                    raise FetchError("Invalid or excessive redirects")
                current = urljoin(current, response_headers["location"])
                attempt = 0
                continue
            if status == 304:
                if not cached or not cached.body_hash:
                    self._capture(
                        current,
                        context,
                        status,
                        response_headers,
                        payload,
                        "304 without cached body",
                    )
                    raise FetchError("304 without a stored response body")
                self.store.read_blob(cached.body_hash)
                return self._capture(
                    current,
                    context,
                    status,
                    {**cached.headers, **response_headers},
                    None,
                    None,
                    cached,
                )
            if status == 200 or (status == 404 and allow_404):
                return self._capture(current, context, status, response_headers, payload, None)
            self._capture(current, context, status, response_headers, payload, f"HTTP {status}")
            if status not in {429, 500, 502, 503, 504} or attempt >= self.settings.max_attempts:
                raise FetchError(f"HTTP {status} from {urlsplit(current).hostname}")
            delay = retry_delay(response_headers.get("retry-after"), attempt, self.clock())
            if delay > self.settings.max_retry_delay_seconds:
                raise FetchError(f"Server requested retry after {delay:g}s; defer to a later run")
            self._sleep(delay)
