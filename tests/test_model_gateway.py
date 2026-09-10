from __future__ import annotations

import contextlib
import gzip
import json
import socket
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx
import pytest
from openai import APIStatusError, OpenAI

from research_harness.execution import DiscoverySettings, GatewayBinding
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.util import digest

MODEL = "gpt-5.4-mini"


def response(*, usage=True):
    result = {
        "id": "response-1",
        "model": MODEL,
        "object": "response",
        "status": "completed",
        "output": [],
    }
    if usage:
        result["usage"] = {
            "input_tokens": 100,
            "output_tokens": 10,
            "total_tokens": 110,
            "input_tokens_details": {"cached_tokens": 20},
        }
    return result


def post(gateway, payload=None, path="/responses", key=None):
    return httpx.post(
        gateway.base_url + path,
        json=payload if payload is not None else {"model": MODEL, "input": "Research"},
        headers={"Authorization": "Bearer " + (key if key is not None else gateway.api_key)},
        timeout=5,
    )


def gateway(tmp_path, handler, **kwargs):
    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer upstream-secret"}
    )
    return ResponsesGateway(
        tmp_path / "gateway",
        model=MODEL,
        settings=kwargs.pop("settings", DiscoverySettings()),
        upstream_base_url="https://provider.example/v1",
        client=client,
        **kwargs,
    )


@pytest.mark.parametrize("encoding", ["json", "sse"])
def test_http_body_completion_waits_for_captured_usage_before_immediate_close(
    tmp_path, monkeypatch, encoding
):
    from research_harness.integrations import model_gateway

    capture_started, finish_capture = threading.Event(), threading.Event()
    original = model_gateway._response_usage

    def pause_capture(*args):
        capture_started.set()
        assert finish_capture.wait(5), "Test did not release capture"
        return original(*args)

    if encoding == "json":
        content = json.dumps(response()).encode()
        content_type = "application/json"
    else:
        content = (
            "event: response.completed\ndata: "
            + json.dumps({"type": "response.completed", "response": response()})
            + "\n\ndata: [DONE]\n\n"
        ).encode()
        content_type = "text/event-stream"
    monkeypatch.setattr(model_gateway, "_response_usage", pause_capture)
    proxy = gateway(
        tmp_path,
        lambda request: httpx.Response(
            200, content=content, headers={"content-type": content_type}
        ),
    ).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(post, proxy)
            try:
                assert capture_started.wait(2)
                # Content-Length must not expose HTTP body completion while
                # final capture is still pending on the gateway handler.
                with pytest.raises(TimeoutError):
                    pending.result(timeout=0.1)
            finally:
                finish_capture.set()
            assert pending.result(timeout=5).content == content
            proxy.close()
        record = json.loads((proxy.output / "request-0001/record.json").read_bytes())
        assert record["outcome"] == "completed"
        assert proxy.report()["complete"] is True
        assert (record["usage"]["input_tokens"], record["usage"]["output_tokens"]) == (100, 10)
        assert_archive(proxy.output)
    finally:
        finish_capture.set()
        proxy.close()


@pytest.mark.parametrize("failure", [429, 500, "transport"])
def test_sdk_does_not_retry_gateway_or_provider_errors(tmp_path, failure):
    received = []

    def upstream(request):
        received.append(request)
        if failure == "transport":
            raise httpx.ReadError("Authored upstream connection failure")
        return httpx.Response(
            failure,
            json={"error": {"message": "Authored provider error", "type": "fixture_error"}},
            headers={"x-should-retry": "true"},
        )

    with gateway(tmp_path, upstream) as proxy:
        with OpenAI(base_url=proxy.base_url, api_key=proxy.api_key, max_retries=2) as sdk:
            with pytest.raises(APIStatusError) as raised:
                sdk.responses.create(model=MODEL, input="Research")
        assert raised.value.status_code == (502 if failure == "transport" else failure)
        assert raised.value.response.headers["x-should-retry"] == "false"
        assert len(received) == proxy.report()["upstream_requests"] == 1
        assert len(proxy.report()["requests"]) == 1


@pytest.mark.parametrize("retry_headers", [["1"], ["7"], ["-1"], [""], ["0, 1"], ["0", "0"]])
def test_sdk_retry_headers_are_rejected_before_admission(tmp_path, retry_headers):
    with gateway(tmp_path, lambda request: pytest.fail("Retry reached provider")) as proxy:
        result = httpx.post(
            proxy.base_url + "/responses",
            json={"model": MODEL, "input": "Research"},
            headers=[("Authorization", "Bearer " + proxy.api_key)]
            + [("x-stainless-retry-count", value) for value in retry_headers],
        )
        assert result.status_code == 400
        assert result.json()["error"]["code"] == "automatic_retry_rejected"
        report = proxy.report()
        assert report["upstream_requests"] == report["reserved_attempts"] == 0
        assert report["requests"][0]["sdk_retry_headers"] == retry_headers


def test_exact_forwarding_controls_and_function_allowlist_are_recorded(tmp_path):
    received = []

    def upstream(request):
        received.append(request)
        return httpx.Response(200, json=response())

    with gateway(tmp_path, upstream, allowed_function_names={"research__search_sources"}) as proxy:
        incoming = {
            "model": MODEL,
            "input": "Research",
            "max_output_tokens": 99,
            "reasoning": {"effort": "high"},
            "parallel_tool_calls": False,
            "tools": [
                {
                    "type": "function",
                    "name": "research__search_sources",
                    "parameters": {"type": "object"},
                },
                {
                    "type": "function",
                    "name": "sys_scheduled_task_create",
                    "parameters": {"type": "object"},
                },
            ],
        }
        result = post(proxy, incoming)
        assert result.json() == response()
        forwarded = json.loads(received[0].content)
        assert forwarded["max_output_tokens"] == 6000
        assert forwarded["reasoning"] == {"effort": "none"}
        assert "parallel_tool_calls" not in forwarded
        assert [tool["name"] for tool in forwarded["tools"]] == ["research__search_sources"]
        assert received[0].headers["Authorization"] == "Bearer upstream-secret"
        assert proxy.api_key != "upstream-secret"
        report = proxy.report()
        assert report["complete"] and report["upstream_requests"] == 1
        assert report["totals"] == {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
        assert report["requests"][0]["usage"]["provider_usage"]["input_tokens_details"] == {
            "cached_tokens": 20
        }
    record = json.loads((tmp_path / "gateway/request-0001/record.json").read_text())
    assert record["stripped_function_names"] == ["sys_scheduled_task_create"]
    assert json.loads((tmp_path / "gateway/request-0001/incoming.json").read_text()) == incoming
    assert record["forwarded_sha256"] == digest(
        (tmp_path / "gateway/request-0001/forwarded.json").read_bytes()
    )
    assert all(
        "upstream-secret" not in path.read_text() and proxy.api_key not in path.read_text()
        for path in (tmp_path / "gateway").rglob("*.json")
    )


def test_sse_bytes_and_terminal_provider_usage_are_preserved(tmp_path):
    raw = (
        "event: response.completed\ndata: "
        + json.dumps({"type": "response.completed", "response": response()})
        + "\n\ndata: [DONE]\n\n"
    ).encode()

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield raw[:31]
            yield raw[31:]

    with gateway(
        tmp_path,
        lambda request: httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=Stream()
        ),
    ) as proxy:
        result = post(proxy, {"model": MODEL, "input": "Research", "stream": True})
        assert result.content == raw
        assert proxy.report()["complete"]
    assert (tmp_path / "gateway/request-0001/response.body").read_bytes() == raw


def test_round_limit_is_atomic_across_concurrent_requests(tmp_path):
    received = []

    def upstream(request):
        received.append(request)
        return httpx.Response(200, json=response())

    with gateway(tmp_path, upstream, settings=DiscoverySettings(max_rounds=1)) as proxy:
        with ThreadPoolExecutor(max_workers=3) as executor:
            statuses = sorted(executor.map(lambda _: post(proxy).status_code, range(3)))
        assert statuses == [200, 429, 429]
        assert len(received) == proxy.report()["upstream_requests"] == 1
        assert {denial["reason"] for denial in proxy.report()["denials"]} == {
            "model_rounds_exhausted"
        }


def test_deadline_starts_on_first_accepted_request_and_stops_later_requests(tmp_path):
    now = [10.0]
    received = []

    def upstream(request):
        received.append(request)
        return httpx.Response(200, json=response())

    with gateway(
        tmp_path, upstream, settings=DiscoverySettings(deadline_seconds=2), clock=lambda: now[0]
    ) as proxy:
        now[0] = 100.0
        assert proxy.report()["started_at"] is None
        assert post(proxy).status_code == 200
        now[0] += 2
        result = post(proxy)
        assert result.status_code == 429 and result.json()["error"]["code"] == "deadline_exhausted"
        assert len(received) == 1


def test_explicit_begin_and_auxiliary_rejection_never_spend_upstream(tmp_path):
    now = [0.0]
    with gateway(
        tmp_path,
        lambda request: pytest.fail("Unexpected upstream request"),
        settings=DiscoverySettings(deadline_seconds=1),
        clock=lambda: now[0],
    ) as proxy:
        assert post(proxy, {"model": "gpt-4.1"}, path="/responses/compact").status_code == 501
        assert proxy.report()["started_at"] is None
        proxy.begin()
        now[0] = 1.0
        assert post(proxy).status_code == 429
        assert proxy.report()["upstream_requests"] == 0
        assert proxy.report()["complete"] and not proxy.report()["auxiliary_usage_unknown"]


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"model": "another-model"}, "model_mismatch"),
        ({"model": MODEL, "tools": [{"type": "web_search"}]}, "provider_native_tools_forbidden"),
        ({"model": MODEL, "background": True}, "background_responses_unsupported"),
        ({"model": MODEL, "background": 1}, "background_responses_unsupported"),
        ({"model": MODEL, "background": 0}, "background_responses_unsupported"),
        ({"model": MODEL, "temperature": 0.3}, "unsupported_request_fields"),
        ({"model": MODEL, "top_p": 0.4}, "unsupported_request_fields"),
        ({"model": MODEL, "service_tier": "priority"}, "undeclared_service_tier"),
        (
            {"model": MODEL, "context_management": [{"type": "compaction"}]},
            "unsupported_request_fields",
        ),
        ({"model": MODEL, "prompt": {"id": "prompt-1"}}, "unsupported_request_fields"),
        ({"model": MODEL, "previous_response_id": "response-1"}, "unsupported_request_fields"),
        ({"model": MODEL, "conversation": "conversation-1"}, "unsupported_request_fields"),
        ({"model": MODEL, "text": {"verbosity": "high"}}, "unsupported_text_controls"),
        (
            {"model": MODEL, "input": [{"type": "item_reference", "id": "item-1"}]},
            "provider_context_reference_forbidden",
        ),
        (
            {"model": MODEL, "tool_choice": {"type": "function", "name": "forbidden"}},
            "tool_choice_forbidden",
        ),
    ],
)
def test_uncontrolled_requests_are_rejected_before_forwarding(tmp_path, payload, reason):
    with gateway(
        tmp_path,
        lambda request: pytest.fail("Unexpected upstream request"),
        allowed_function_names={"search"},
    ) as proxy:
        result = post(proxy, payload)
        assert result.status_code == 400
        assert result.json()["error"]["code"] == reason
        assert proxy.report()["upstream_requests"] == 0


def test_missing_usage_stays_a_lower_bound_and_gateway_key_is_required(tmp_path):
    with gateway(
        tmp_path, lambda request: httpx.Response(200, json=response(usage=False))
    ) as proxy:
        assert post(proxy, key="wrong").status_code == 401
        assert proxy.report()["upstream_requests"] == 0
        assert post(proxy).status_code == 200
        report = proxy.report()
        assert not report["complete"] and report["totals"]["total_tokens"] == 0


def test_deadline_interrupts_stream_and_retains_unknown_usage(tmp_path):
    now = [0.0]

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"event: response.created\ndata: {}\n\n"
            now[0] = 3.0
            yield b"data: [DONE]\n\n"

    with gateway(
        tmp_path,
        lambda request: httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=Stream()
        ),
        settings=DiscoverySettings(deadline_seconds=2),
        clock=lambda: now[0],
    ) as proxy:
        result = post(proxy)
        assert b"[DONE]" not in result.content
        report = proxy.report()
        assert report["interrupted"] and not report["complete"]
        assert report["upstream_requests"] == 1


@pytest.mark.parametrize("status", ["in_progress", "queued", None])
def test_nonterminal_json_usage_is_unknown(tmp_path, status):
    result = response()
    result["status"] = status
    with gateway(tmp_path, lambda request: httpx.Response(200, json=result)) as proxy:
        assert post(proxy).status_code == 200
        assert not proxy.report()["complete"]
        assert proxy.report()["totals"]["total_tokens"] == 0


@pytest.mark.parametrize("total", [999, True, -1])
def test_inconsistent_total_tokens_is_unknown(tmp_path, total):
    result = response()
    result["usage"]["total_tokens"] = total
    with gateway(tmp_path, lambda request: httpx.Response(200, json=result)) as proxy:
        assert post(proxy).status_code == 200
        assert not proxy.report()["complete"]
        assert proxy.report()["totals"]["total_tokens"] == 0


@pytest.mark.parametrize("status", ["failed", "incomplete"])
def test_terminal_unsuccessful_response_can_have_complete_usage(tmp_path, status):
    result = response()
    result["status"] = status
    with gateway(tmp_path, lambda request: httpx.Response(200, json=result)) as proxy:
        assert post(proxy).status_code == 200
        assert proxy.report()["complete"]
        assert proxy.report()["totals"]["total_tokens"] == 110


def test_already_consumed_encoded_upstream_response_is_rejected(tmp_path):
    raw = gzip.compress(json.dumps(response()).encode())
    with gateway(
        tmp_path,
        lambda request: httpx.Response(
            200, content=raw, headers={"Content-Encoding": "gzip", "Content-Length": str(len(raw))}
        ),
    ) as proxy:
        assert post(proxy).status_code == 502
        report = proxy.report()
        assert not report["complete"] and report["interrupted"]
        assert report["requests"][0]["reason"] == "preconsumed_encoded_response_unsupported"


def test_raw_gzip_stream_retains_encoded_bytes_headers_and_usage(tmp_path):
    raw = gzip.compress(json.dumps(response()).encode())

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield raw[:17]
            yield raw[17:]

    with gateway(
        tmp_path,
        lambda request: httpx.Response(
            200,
            stream=Stream(),
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(raw))},
        ),
    ) as proxy:
        result = post(proxy)
        assert result.json() == response()
        assert result.headers["content-encoding"] == "gzip"
        assert proxy.report()["complete"]
    assert (tmp_path / "gateway/request-0001/response.body").read_bytes() == raw


@contextlib.contextmanager
def live_upstream(serve):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            with contextlib.suppress(OSError):
                serve(self)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def file_snapshot(path):
    return {
        str(file.relative_to(path)): file.read_bytes() for file in path.rglob("*") if file.is_file()
    }


def assert_archive(path):
    manifest = json.loads((path / "archive.json").read_text())
    assert manifest["schema_version"] == 1
    expected = {
        name: digest(raw) for name, raw in file_snapshot(path).items() if name != "archive.json"
    }
    assert manifest["files"] == expected
    assert "gateway.json" in expected and "report.json" in expected
    assert json.loads((path / "report.json").read_text())["closed"]
    return manifest


def test_absolute_deadline_interrupts_silence_after_a_late_chunk(tmp_path):
    release = threading.Event()

    def serve(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        time.sleep(0.8)
        handler.wfile.write(b"event: response.created\ndata: {}\n\n")
        handler.wfile.flush()
        release.wait(5)

    try:
        with live_upstream(serve) as upstream:
            with ResponsesGateway(
                tmp_path / "gateway",
                model=MODEL,
                settings=DiscoverySettings(deadline_seconds=1),
                upstream_base_url=upstream,
            ) as proxy:
                started = time.monotonic()
                with contextlib.suppress(httpx.HTTPError):
                    post(proxy)
                elapsed = time.monotonic() - started
                assert 0.85 <= elapsed < 1.5
                report = proxy.report()
                assert report["interrupted"] and not report["complete"]
                assert report["requests"][0]["reason"] == "deadline_exhausted"
    finally:
        release.set()


def test_close_seals_header_waiting_request_before_provider_finishes(tmp_path):
    waiting = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def serve(handler):
        waiting.set()
        release.wait(5)
        try:
            raw = json.dumps(response()).encode()
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)
        finally:
            finished.set()

    try:
        with live_upstream(serve) as upstream:
            # The supplied client remains owned by the caller during shutdown.
            with httpx.Client() as client:
                proxy = ResponsesGateway(
                    tmp_path / "gateway",
                    model=MODEL,
                    settings=DiscoverySettings(),
                    upstream_base_url=upstream,
                    client=client,
                ).start()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(post, proxy)
                    assert waiting.wait(2)
                    proxy.close()
                    with pytest.raises(httpx.HTTPError):
                        future.result(timeout=1)
                    assert not client.is_closed
                    report = proxy.report()
                    assert report["closed"] and report["interrupted"] and not report["complete"]
                    assert report["requests"][0]["reason"] == "gateway_closed"
                    frozen = file_snapshot(proxy.output)
                    release.set()
                    assert finished.wait(2)
                    deadline = time.monotonic() + 2
                    while proxy._active_attempts and time.monotonic() < deadline:
                        time.sleep(0.01)
                    assert not proxy._active_attempts
                    assert file_snapshot(proxy.output) == frozen
                    assert proxy.report() == report
    finally:
        release.set()


def test_close_seals_authenticated_request_still_reading_body(tmp_path):
    proxy = gateway(tmp_path, lambda request: pytest.fail("Unexpected upstream call")).start()
    url = urlsplit(proxy.base_url)
    connection = socket.create_connection((url.hostname, url.port), timeout=2)
    try:
        connection.sendall(
            (
                "POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n"
                f"Authorization: Bearer {proxy.api_key}\r\nContent-Length: 100\r\n\r\n{{"
            ).encode()
        )
        deadline = time.monotonic() + 2
        while not proxy._handlers and time.monotonic() < deadline:
            time.sleep(0.01)
        assert proxy._handlers
        proxy.close()
        frozen = file_snapshot(proxy.output)
        with contextlib.suppress(OSError):
            connection.sendall(b" " * 99)
        while proxy._handlers and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not proxy._handlers
        assert proxy.report()["upstream_requests"] == 0
        assert file_snapshot(proxy.output) == frozen
    finally:
        connection.close()
        proxy.close()


def test_deadline_during_request_construction_prevents_late_dispatch(tmp_path):
    received = []

    class SlowBuildClient(httpx.Client):
        def build_request(self, *args, **kwargs):
            time.sleep(1.2)
            return super().build_request(*args, **kwargs)

    with SlowBuildClient(
        transport=httpx.MockTransport(
            lambda request: received.append(request) or httpx.Response(200, json=response())
        )
    ) as client:
        with ResponsesGateway(
            tmp_path / "gateway",
            model=MODEL,
            settings=DiscoverySettings(deadline_seconds=1),
            upstream_base_url="https://provider.example/v1",
            client=client,
        ) as proxy:
            with pytest.raises(httpx.HTTPError):
                post(proxy)
            deadline = time.monotonic() + 2
            while proxy._active_attempts and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not proxy._active_attempts
            assert received == []
            report = proxy.report()
            assert report["requests"][0]["reason"] == "deadline_exhausted"
            assert not report["requests"][0]["dispatch_started"]
            assert report["reserved_attempts"] == 1 and report["upstream_requests"] == 0
            assert report["complete"] and report["interrupted"]


def test_binding_is_recorded_before_start_and_copies_are_immutable(tmp_path):
    binding = GatewayBinding(
        execution_id="1" * 32,
        case_id="case-1",
        runtime="direct-controlled-gateway",
        phase="discovery",
        task_sha256="2" * 64,
    )
    expected = binding.model_dump(mode="json")
    proxy = gateway(tmp_path, lambda request: httpx.Response(200, json=response()), binding=binding)
    metadata = proxy.output / "gateway.json"
    assert json.loads(metadata.read_text())["binding"] == expected
    before = metadata.read_bytes()
    binding.case_id = "changed-original"
    proxy.binding.runtime = "changed-returned-copy"
    assert proxy.binding.model_dump(mode="json") == expected
    with pytest.raises(AttributeError):
        proxy.binding = None
    with proxy:
        assert post(proxy).status_code == 200
        assert not (proxy.output / "archive.json").exists()
    assert metadata.read_bytes() == before
    assert_archive(proxy.output)


def test_archive_includes_actual_success_and_denial_files_and_close_is_idempotent(tmp_path):
    with gateway(tmp_path, lambda request: httpx.Response(200, json=response())) as proxy:
        assert post(proxy).status_code == 200
        assert post(proxy, path="/responses/compact").status_code == 501
    manifest = assert_archive(proxy.output)
    assert json.loads((proxy.output / "gateway.json").read_text())["binding"] is None
    assert "request-0001/forwarded.json" in manifest["files"]
    assert "request-0001/response.body" in manifest["files"]
    assert "request-0002/incoming.json" in manifest["files"]
    assert "request-0002/record.json" in manifest["files"]
    assert "request-0002/forwarded.json" not in manifest["files"]
    record = json.loads((proxy.output / "request-0001/record.json").read_text())
    assert record["response_content_type"] == "application/json"
    assert record["response_content_encoding"] is None
    frozen = file_snapshot(proxy.output)
    proxy.close()
    assert file_snapshot(proxy.output) == frozen


def test_concurrent_close_waits_for_sealed_inventory_and_late_handler_cannot_change_it(tmp_path):
    waiting = threading.Event()
    release = threading.Event()

    def upstream(request):
        waiting.set()
        release.wait(5)
        return httpx.Response(200, json=response())

    proxy = gateway(tmp_path, upstream).start()
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            response_future = executor.submit(post, proxy)
            assert waiting.wait(2)

            def close_and_inventory():
                proxy.close()
                return assert_archive(proxy.output)

            closes = [executor.submit(close_and_inventory) for _ in range(2)]
            manifests = [future.result(timeout=2) for future in closes]
            assert manifests[0] == manifests[1]
            with pytest.raises(httpx.HTTPError):
                response_future.result(timeout=1)
            assert proxy.report()["requests"][0]["outcome"] == "interrupted"
            assert not proxy.report()["complete"]
            frozen = file_snapshot(proxy.output)
            release.set()
            deadline = time.monotonic() + 2
            while proxy._active_attempts and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not proxy._active_attempts
            assert file_snapshot(proxy.output) == frozen
    finally:
        release.set()
        proxy.close()


@pytest.mark.parametrize("failed_file", ["report.json", ".archive.json.tmp"])
def test_shutdown_write_failure_cannot_publish_an_archive(tmp_path, monkeypatch, failed_file):
    import research_harness.integrations.model_gateway as module

    proxy = gateway(tmp_path, lambda request: httpx.Response(200, json=response())).start()
    assert post(proxy).status_code == 200
    original = module.write_json

    def fail_write(path, value):
        if path.name == failed_file:
            path.write_text("partial failed write")
            raise OSError("fixture disk write failure")
        return original(path, value)

    monkeypatch.setattr(module, "write_json", fail_write)
    with pytest.raises(OSError, match="fixture disk write failure"):
        proxy.close()
    assert proxy._sealed
    assert not (proxy.output / "archive.json").exists()
    assert not (proxy.output / ".archive.json.tmp").exists()
    frozen = file_snapshot(proxy.output)
    with pytest.raises(RuntimeError, match="did not produce an archive"):
        proxy.close()
    assert proxy._record("/v1/responses", b"{}") is None
    assert file_snapshot(proxy.output) == frozen


@pytest.mark.parametrize("incoming_tier", [None, "priority", "default"])
def test_host_service_tier_is_explicit_and_recorded(tmp_path, incoming_tier):
    received = []

    def upstream(request):
        received.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    settings = DiscoverySettings(service_tier="default")
    with gateway(tmp_path, upstream, settings=settings) as proxy:
        payload = {"model": MODEL, "input": "Fixture"}
        if incoming_tier is not None:
            payload["service_tier"] = incoming_tier
        assert post(proxy, payload).status_code == 200
        assert received[0]["service_tier"] == "default"
        changes = proxy.report()["requests"][0]["control_changes"]
        if incoming_tier == "default":
            assert "service_tier" not in changes
        else:
            assert changes["service_tier"] == {"incoming": incoming_tier, "forwarded": "default"}
    assert_archive(proxy.output)


def dispatch_budget(tmp_path, *, settings=None, ceiling="1", mode="fixture"):
    from research_harness.evaluation.budget import BudgetLedger, RateCard
    from research_harness.evaluation.dispatch_budget import (
        STANDARD_MODEL,
        DispatchBudget,
        DispatchPolicy,
    )

    settings = settings or DiscoverySettings(service_tier="default")
    model = MODEL if mode == "fixture" else STANDARD_MODEL
    ledger = BudgetLedger(
        tmp_path / "ledger.json",
        rates=RateCard(
            model=model,
            snapshot=model,
            input_usd_per_million="1",
            output_usd_per_million="5",
            max_input_tokens_per_request=1000 if mode == "fixture" else 400000,
            price_source_url="https://fixture.invalid/prices",
            price_as_of="2026-09-08",
        ),
        ceiling_usd=ceiling,
    )
    return DispatchBudget(
        ledger,
        policy=DispatchPolicy(
            mode=mode,
            model=model,
            upstream_base_url="https://fixture.invalid/v1"
            if mode == "fixture"
            else "https://api.openai.com/v1",
        ),
        binding=GatewayBinding(
            execution_id="b" * 32, case_id="budget-case", runtime="fixture", task_sha256="c" * 64
        ),
        settings=settings,
    )


def budget_gateway(tmp_path, budget, handler, **kwargs):
    return ResponsesGateway(
        tmp_path / "gateway",
        model=budget.policy.model,
        settings=budget.settings,
        binding=budget.binding,
        dispatch_budget=budget,
        upstream_base_url=budget.policy.upstream_base_url,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def test_budget_reservation_and_dispatch_marker_precede_provider_call(tmp_path):
    budget = dispatch_budget(tmp_path)
    seen = []

    def upstream(request):
        record = json.loads((tmp_path / "gateway/request-0001/record.json").read_text())
        row = budget.ledger.snapshot()["reservations"][record["budget_operation_id"]]
        assert row["status"] == "dispatched"
        assert record["dispatch_started"] and record["budget_dispatch_marked"]
        assert not record["budget_reservation_pending"] and not record["budget_dispatch_pending"]
        assert row["request_sha256"] == record["forwarded_sha256"] == digest(request.content)
        seen.append(request)
        return httpx.Response(200, json={**response(), "service_tier": "default"})

    with budget_gateway(tmp_path, budget, upstream) as proxy:
        assert post(proxy).status_code == 200
        assert len(seen) == 1
    metadata = json.loads((proxy.output / "gateway.json").read_text())
    assert metadata["budget_control"] == budget.metadata()
    assert metadata["dollar_cap_enforced"] is False
    assert {row["status"] for row in budget.ledger.snapshot()["reservations"].values()} == {
        "dispatched"
    }
    assert_archive(proxy.output)


def test_exhausted_budget_denies_before_round_or_provider_reservation(tmp_path):
    budget = dispatch_budget(tmp_path, ceiling="0")
    with budget_gateway(
        tmp_path, budget, lambda request: pytest.fail("Unexpected dispatch")
    ) as proxy:
        result = post(proxy)
        assert result.status_code == 400 and result.json()["error"]["code"] == "budget_exhausted"
        assert proxy.report()["reserved_attempts"] == proxy.report()["upstream_requests"] == 0
        assert not budget.ledger.snapshot()["reservations"]
    assert_archive(proxy.output)


def test_already_dispatched_ledger_operation_cannot_be_reused(tmp_path):
    from research_harness.util import canonical_json

    budget = dispatch_budget(tmp_path)
    payload = {"model": MODEL, "input": "Research", **budget.settings.model_settings()}
    row = budget.reserve("request-0001", digest(canonical_json(payload).encode()))
    budget.mark_dispatched(row["operation_id"])
    with budget_gateway(
        tmp_path, budget, lambda request: pytest.fail("Unexpected redispatch")
    ) as proxy:
        result = post(proxy)
        assert result.status_code == 400
        assert result.json()["error"]["code"] == "budget_reservation_rejected"
        assert proxy.report()["upstream_requests"] == proxy.report()["reserved_attempts"] == 0
        assert (
            budget.ledger.snapshot()["reservations"][row["operation_id"]]["status"] == "dispatched"
        )


def test_unbudgeted_media_is_denied_before_any_ledger_change(tmp_path):
    budget = dispatch_budget(tmp_path)
    with budget_gateway(
        tmp_path, budget, lambda request: pytest.fail("Unexpected dispatch")
    ) as proxy:
        result = post(
            proxy,
            {
                "model": MODEL,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_image", "image_url": "https://fixture.invalid/image"}
                        ],
                    }
                ],
            },
        )
        assert result.json()["error"]["code"] == "budget_request_rejected"
        assert not budget.ledger.snapshot()["reservations"]
        assert proxy.report()["reserved_attempts"] == 0


@pytest.mark.parametrize("operation", ["reserve", "mark_dispatched"])
def test_uncertain_budget_write_holds_funds_and_never_dispatches(tmp_path, monkeypatch, operation):
    budget = dispatch_budget(tmp_path)
    original = getattr(budget, operation)

    def uncertain(*args):
        original(*args)
        raise OSError("fixture sensitive error must not enter archive")

    monkeypatch.setattr(budget, operation, uncertain)
    with budget_gateway(
        tmp_path, budget, lambda request: pytest.fail("Unexpected dispatch")
    ) as proxy:
        result = post(proxy)
        assert result.status_code == 400
        assert proxy.report()["upstream_requests"] == 0
        rows = list(budget.ledger.snapshot()["reservations"].values())
        assert len(rows) == 1 and rows[0]["charged_nanodollars"] == rows[0]["reserved_nanodollars"]
        assert rows[0]["status"] in {"reserved", "dispatched"}
    assert all(
        b"fixture sensitive error" not in raw for raw in file_snapshot(proxy.output).values()
    )
    assert_archive(proxy.output)


@pytest.mark.parametrize("operation", ["reserve", "mark_dispatched"])
@pytest.mark.parametrize("stop", ["deadline", "close"])
def test_blocked_budget_operation_cannot_dispatch_or_write_after_sealing(
    tmp_path, monkeypatch, operation, stop
):
    budget = dispatch_budget(
        tmp_path, settings=DiscoverySettings(service_tier="default", deadline_seconds=1)
    )
    original = getattr(budget, operation)
    entered = threading.Event()
    release = threading.Event()

    def blocked(*args):
        entered.set()
        release.wait(5)
        return original(*args)

    monkeypatch.setattr(budget, operation, blocked)
    proxy = budget_gateway(
        tmp_path, budget, lambda request: pytest.fail("Unexpected dispatch")
    ).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(post, proxy)
            assert entered.wait(2)
            if stop == "close":
                proxy.close()
            with pytest.raises(httpx.HTTPError):
                future.result(timeout=1.5)
            proxy.close()
            frozen = file_snapshot(proxy.output)
            assert proxy.report()["upstream_requests"] == 0
            assert_archive(proxy.output)
            release.set()
            deadline = time.monotonic() + 2
            while proxy._active_attempts and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not proxy._active_attempts
            assert file_snapshot(proxy.output) == frozen
            rows = list(budget.ledger.snapshot()["reservations"].values())
            assert (
                len(rows) == 1 and rows[0]["charged_nanodollars"] == rows[0]["reserved_nanodollars"]
            )
    finally:
        release.set()
        proxy.close()


@pytest.mark.parametrize("changed", ["model", "settings", "binding", "upstream_base_url"])
def test_budget_identity_and_settings_must_match_gateway(tmp_path, changed):
    budget = dispatch_budget(tmp_path)
    arguments = dict(
        model=budget.policy.model,
        settings=budget.settings,
        binding=budget.binding,
        upstream_base_url=budget.policy.upstream_base_url,
    )
    arguments[changed] = {
        "model": "foreign-model",
        "settings": DiscoverySettings(service_tier="default", max_rounds=1),
        "binding": budget.binding.model_copy(update={"execution_id": "e" * 32}),
        "upstream_base_url": "https://fixture.invalid/v2",
    }[changed]
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected dispatch"))
    ) as client:
        with pytest.raises(ValueError, match="does not match"):
            ResponsesGateway(
                tmp_path / "gateway", dispatch_budget=budget, client=client, **arguments
            )


def test_production_budget_disallows_injected_client_and_disables_retries_and_environment(
    tmp_path, monkeypatch
):
    budget = dispatch_budget(tmp_path, mode="openai-standard")
    arguments = dict(
        model=budget.policy.model,
        settings=budget.settings,
        binding=budget.binding,
        upstream_base_url=budget.policy.upstream_base_url,
        dispatch_budget=budget,
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected dispatch"))
    ) as client:
        with pytest.raises(ValueError, match="injected HTTP client"):
            ResponsesGateway(tmp_path / "rejected", client=client, **arguments)
    original = httpx.HTTPTransport
    transport_options = []

    def transport(**kwargs):
        transport_options.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(httpx, "HTTPTransport", transport)
    with ResponsesGateway(tmp_path / "gateway", **arguments) as proxy:
        assert proxy.client._trust_env is False
        assert transport_options == [{"retries": 0, "trust_env": False}]
        assert proxy.report()["upstream_requests"] == 0


def test_fixture_budget_requires_mock_transport(tmp_path):
    budget = dispatch_budget(tmp_path)
    arguments = dict(
        model=budget.policy.model,
        settings=budget.settings,
        binding=budget.binding,
        upstream_base_url=budget.policy.upstream_base_url,
        dispatch_budget=budget,
    )
    with pytest.raises(ValueError, match="MockTransport"):
        ResponsesGateway(tmp_path / "rejected", **arguments)
    with httpx.Client(trust_env=False) as client:
        with pytest.raises(ValueError, match="MockTransport"):
            ResponsesGateway(tmp_path / "rejected", client=client, **arguments)


@pytest.mark.parametrize("violation", ["model", "service_tier", "input_tokens", "output_tokens"])
def test_definite_provider_policy_violation_latches_further_dispatch_off(tmp_path, violation):
    budget = dispatch_budget(tmp_path)
    terminal = {**response(), "service_tier": "default"}
    if violation in {"model", "service_tier"}:
        terminal[violation] = "foreign"
    else:
        terminal["usage"][violation] = 1001 if violation == "input_tokens" else 6001
        terminal["usage"]["total_tokens"] = (
            terminal["usage"]["input_tokens"] + terminal["usage"]["output_tokens"]
        )
    with budget_gateway(
        tmp_path, budget, lambda request: httpx.Response(200, json=terminal)
    ) as proxy:
        assert post(proxy).status_code == 200
        result = post(proxy)
        assert (
            result.status_code == 400
            and result.json()["error"]["code"] == "budget_policy_violation"
        )
        assert proxy.report()["upstream_requests"] == proxy.report()["reserved_attempts"] == 1
        assert proxy.report()["budget_policy_violation"] is not None
        assert len(budget.ledger.snapshot()["reservations"]) == 1


def test_missing_usage_keeps_funds_held_without_inventing_a_policy_mismatch(tmp_path):
    budget = dispatch_budget(tmp_path)
    with budget_gateway(
        tmp_path, budget, lambda request: httpx.Response(200, json=response(usage=False))
    ) as proxy:
        assert post(proxy).status_code == post(proxy).status_code == 200
        assert proxy.report()["budget_policy_violation"] is None
        assert not proxy.report()["complete"] and proxy.report()["upstream_requests"] == 2
    assert all(
        row["status"] == "dispatched" for row in budget.ledger.snapshot()["reservations"].values()
    )


@pytest.mark.parametrize("encoding", [None, "gzip"])
def test_response_created_policy_mismatch_blocks_next_call_before_stream_finishes(
    tmp_path, encoding
):
    budget = dispatch_budget(tmp_path)
    release = threading.Event()
    created = json.dumps(
        {
            "type": "response.created",
            "response": {"id": "response-1", "model": MODEL, "service_tier": "priority"},
        }
    )

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            first = f"data: {created}\n\n".encode()
            compressor = zlib.compressobj(wbits=16 + zlib.MAX_WBITS) if encoding == "gzip" else None
            yield (
                compressor.compress(first) + compressor.flush(zlib.Z_SYNC_FLUSH)
                if compressor
                else first
            )
            release.wait(5)
            final = b"data: [DONE]\n\n"
            yield compressor.compress(final) + compressor.flush() if compressor else final

    headers = {"Content-Type": "text/event-stream"}
    if encoding:
        headers["Content-Encoding"] = encoding
    proxy = budget_gateway(
        tmp_path,
        budget,
        lambda request: httpx.Response(200, headers=headers, stream=Stream()),
    ).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(post, proxy)
            deadline = time.monotonic() + 2
            while proxy.report()["budget_policy_violation"] is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert proxy.report()["budget_policy_violation"] == "returned_service_tier_mismatch"
            assert not first.done()
            assert post(proxy).json()["error"]["code"] == "budget_policy_violation"
            release.set()
            assert first.result(timeout=2).status_code == 200
            assert proxy.report()["upstream_requests"] == 1
    finally:
        release.set()
        proxy.close()
