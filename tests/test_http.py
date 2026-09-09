from __future__ import annotations

import socket

import httpx
import pytest

from research_harness.config import HttpSettings
from research_harness.http import Fetcher, FetchError, validate_public_url


def fetcher(store, spec, source, client, clock, **kwargs):
    run = store.start_run(spec)
    source_run = store.start_source(run, source)
    return Fetcher(
        store,
        spec.name,
        source_run,
        source.id,
        HttpSettings(min_interval_seconds=0, **kwargs),
        client=client,
        clock=clock,
        public_only=False,
    )


def test_429_retries_with_server_delay_and_saves_both_responses(store, spec, source, clock):
    calls = []
    sleeps = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"retry-after": "2"}, text="slow down")
            if len(calls) == 1
            else httpx.Response(200, json={"items": []})
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        worker = fetcher(store, spec, source, client, clock)
        worker.sleep = sleeps.append
        capture = worker.fetch(source.url)
    assert capture.status_code == 200
    assert sleeps == [2]
    assert store.count("captures") == 2


def test_long_retry_after_defers_instead_of_retrying_too_soon(store, spec, source, clock):
    calls = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (
                calls.append(request) or httpx.Response(429, headers={"retry-after": "3600"})
            )
        )
    ) as client:
        worker = fetcher(store, spec, source, client, clock)
        with pytest.raises(FetchError, match="defer"):
            worker.fetch(source.url)
    assert len(calls) == 1


def test_timeout_retries_are_bounded_and_audited(store, spec, source, clock):
    def handler(request):
        raise httpx.ReadTimeout("test timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        worker = fetcher(store, spec, source, client, clock, max_attempts=2)
        worker.sleep = lambda _: None
        with pytest.raises(FetchError, match="2 attempts"):
            worker.fetch(source.url)
    assert store.count("captures") == 2


def test_size_limit_saves_truncated_evidence_without_accepting_it(store, spec, source, clock):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 3000))
    ) as client:
        worker = fetcher(store, spec, source, client, clock, max_response_bytes=1024)
        with pytest.raises(FetchError, match="limit"):
            worker.fetch(source.url)
    row = store.query_one("SELECT * FROM captures")
    assert len(store.read_blob(row["body_hash"])) == 1024
    assert row["error"]
    assert '"truncated":true' in row["context_json"]


def test_304_without_body_is_a_failure(store, spec, source, clock):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(304))) as client:
        worker = fetcher(store, spec, source, client, clock)
        with pytest.raises(FetchError, match="304"):
            worker.fetch(source.url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/data",
        "http://127.0.0.1/data",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "file:///etc/passwd",
        "https://public.example:8443/",
        "https://public.example/?api_key=secret",
    ],
)
def test_source_inspection_rejects_local_and_credential_urls(url, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_public_hostname_with_private_dns_is_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.2.3.4", 443))],
    )
    with pytest.raises(ValueError, match="nonpublic"):
        validate_public_url("https://public.example/path")


def test_redirect_is_validated_before_second_request(store, spec, source, clock, monkeypatch):
    calls = []

    def guard(url):
        if "169.254" in url:
            raise ValueError("nonpublic")

    monkeypatch.setattr("research_harness.http.validate_public_url", guard)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (
                calls.append(request)
                or httpx.Response(302, headers={"location": "http://169.254.169.254/"})
            )
        )
    ) as client:
        worker = fetcher(store, spec, source, client, clock)
        worker.public_only = True
        with pytest.raises(FetchError):
            worker.fetch(source.url)
    assert len(calls) == 1


def test_reused_body_integrity_is_checked(store, spec, source, clock, tamper):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"original", headers={"etag": "v1"})
        )
    ) as client:
        worker = fetcher(store, spec, source, client, clock)
        first = worker.fetch(source.url)
    tamper(store, first.body_hash, b"changed")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(304))) as client:
        worker = fetcher(store, spec, source, client, clock)
        with pytest.raises(RuntimeError, match="integrity"):
            worker.fetch(source.url)
