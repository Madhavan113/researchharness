"""Bounded, recorded Responses gateway for controlled discovery comparisons.

Both execution paths can use this host adapter. It leaves the pinned runtime
unchanged and records its incoming request separately from the controlled one.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import secrets
import socket
import threading
import time
import zlib
from collections.abc import Callable
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from research_harness.evaluation.budget import BudgetExceeded
from research_harness.execution import DiscoverySettings, GatewayBinding
from research_harness.util import canonical_json, digest, timestamp, utcnow, write_json

if TYPE_CHECKING:
    from research_harness.evaluation.dispatch_budget import DispatchBudget

MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
SUPPORTED_REQUEST_FIELDS = frozenset(
    {
        "model",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "text",
        "stream",
        "include",
        "max_output_tokens",
        "reasoning",
        "parallel_tool_calls",
        "store",
        "metadata",
        "background",
        "service_tier",
    }
)


def _response_usage(body: bytes, content_type: str, encoding: str | None) -> dict | None:
    if encoding == "gzip":
        body = gzip.decompress(body)
    elif encoding not in {None, "identity"}:
        return None
    responses = []
    if "text/event-stream" in content_type:
        data: list[str] = []
        for line in body.decode().splitlines() + [""]:
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line and data:
                payload = "\n".join(data)
                data = []
                if payload == "[DONE]":
                    continue
                event = json.loads(payload)
                if event.get("type") in {
                    "response.completed",
                    "response.incomplete",
                    "response.failed",
                }:
                    responses.append(event.get("response") or {})
    else:
        responses.append(json.loads(body))
    if len(responses) != 1:
        return None
    response = responses[0]
    if (
        not isinstance(response, dict)
        or response.get("status") not in {"completed", "incomplete", "failed"}
        or not isinstance(response.get("id"), str)
        or not response["id"]
    ):
        return None
    usage = response.get("usage") or {}
    if not isinstance(usage, dict):
        return None
    if any(
        type(usage.get(key)) is not int or usage[key] < 0
        for key in ("input_tokens", "output_tokens")
    ):
        return None
    if "total_tokens" in usage and (
        type(usage["total_tokens"]) is not int
        or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]
    ):
        return None
    return {
        "response_id": response.get("id"),
        "model": response.get("model"),
        "status": response.get("status"),
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "provider_usage": usage,
    }


class _BudgetResponseObserver:
    """Observe explicit provider fields promptly; never settle token charges."""

    def __init__(self, content_type: str, encoding: str | None, observe: Callable[[dict], None]):
        self.sse = "text/event-stream" in content_type.lower()
        self.enabled = encoding in {None, "identity", "gzip"}
        self.decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
        self.buffer = bytearray()
        self.data: list[bytes] = []
        self.decoded_bytes = 0
        self.observe = observe

    def _payload(self, raw: bytes) -> None:
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            return
        if isinstance(value, dict):
            response = value.get("response") if self.sse else value
            if isinstance(response, dict):
                self.observe(response)

    def feed(self, raw: bytes) -> None:
        if not self.enabled:
            return
        try:
            decoded = (
                self.decoder.decompress(raw, MAX_RESPONSE_BYTES + 1 - self.decoded_bytes)
                if self.decoder is not None
                else raw
            )
        except zlib.error:
            self.enabled = False
            return
        self.decoded_bytes += len(decoded)
        if self.decoded_bytes > MAX_RESPONSE_BYTES:
            self.enabled = False
            return
        self.buffer.extend(decoded)
        if self.sse:
            while b"\n" in self.buffer:
                raw_line, _, remainder = self.buffer.partition(b"\n")
                self.buffer = bytearray(remainder)
                line = raw_line.rstrip(b"\r")
                if line.startswith(b"data:"):
                    self.data.append(line[5:].lstrip())
                elif not line and self.data:
                    self._payload(b"\n".join(self.data))
                    self.data.clear()

    def finish(self) -> None:
        if not self.enabled:
            return
        if self.sse:
            if self.buffer.startswith(b"data:"):
                self.data.append(bytes(self.buffer[5:]).lstrip())
            if self.data:
                self._payload(b"\n".join(self.data))
        else:
            self._payload(bytes(self.buffer))


class ResponsesGateway:
    """Enforce model calls before forwarding; this is not a dollar-spend cap.

    ``base_url`` and ``api_key`` are local runner credentials. The upstream
    client/key stay with the host. The monotonic deadline begins on the first
    accepted request, or an explicit ``begin()``. Each admitted forwarding attempt
    reserves one round; ``upstream_requests`` counts dispatches separately.
    """

    def __init__(
        self,
        output: Path,
        *,
        model: str,
        settings: DiscoverySettings,
        upstream_base_url: str,
        upstream_api_key: str | None = None,
        client: httpx.Client | None = None,
        allowed_function_names: set[str] | None = None,
        binding: GatewayBinding | None = None,
        dispatch_budget: DispatchBudget | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not model.strip():
            raise ValueError("Gateway model must be nonempty")
        parts = urlsplit(upstream_base_url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Invalid upstream base URL")
        self._binding_json = (
            canonical_json(GatewayBinding.model_validate(binding).model_dump(mode="json"))
            if binding is not None
            else None
        )
        self.model = model
        self.settings = DiscoverySettings.model_validate(settings.model_dump(mode="json"))
        self.upstream_base_url = upstream_base_url.rstrip("/")
        self._dispatch_budget = dispatch_budget
        self._budget_violation: str | None = None
        if dispatch_budget is not None:
            policy = dispatch_budget.policy
            if (
                policy.model != self.model
                or policy.upstream_base_url != self.upstream_base_url
                or dispatch_budget.binding != self.binding
                or dispatch_budget.settings != self.settings
                or policy.service_tier != "default"
                or self.settings.service_tier != "default"
            ):
                raise ValueError("Dispatch budget does not match gateway identity and settings")
            if policy.mode == "openai-standard":
                if client is not None:
                    raise ValueError("OpenAI budget mode cannot use an injected HTTP client")
            elif policy.mode == "fixture":
                if (
                    self.upstream_base_url != "https://fixture.invalid/v1"
                    or type(client) is not httpx.Client
                    or not isinstance(
                        client._transport_for_url(httpx.URL(self.upstream_base_url)),
                        httpx.MockTransport,
                    )
                ):
                    raise ValueError(
                        "Fixture budget mode requires an explicit MockTransport client"
                    )
            else:
                raise ValueError("Unsupported dispatch budget mode")
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self._upstream_api_key = upstream_api_key
        self.client = client or httpx.Client(
            follow_redirects=False,
            trust_env=False,
            transport=httpx.HTTPTransport(retries=0, trust_env=False),
        )
        self._owns_client = client is None
        self.allowed_function_names = (
            frozenset(allowed_function_names) if allowed_function_names is not None else None
        )
        self.api_key = secrets.token_urlsafe(32)
        self.base_url: str | None = None
        self._clock = clock
        self._lock = threading.RLock()
        self._admission_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._close_finished = False
        self._close_error: BaseException | None = None
        self._started: float | None = None
        self._started_at: str | None = None
        self._records: list[dict[str, Any]] = []
        self._reserved_attempts = 0
        self._upstream_requests = 0
        self._active_attempts: dict[str, dict] = {}
        self._handlers: set[BaseHTTPRequestHandler] = set()
        self._closing = threading.Event()
        self._sealed = False
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        write_json(
            self.output / "gateway.json",
            {
                "schema_version": 1,
                "adapter": "controlled-responses-gateway",
                "binding": json.loads(self._binding_json)
                if self._binding_json is not None
                else None,
                "model": model,
                "discovery_settings": self.settings.model_dump(mode="json"),
                "allowed_function_names": sorted(self.allowed_function_names)
                if self.allowed_function_names is not None
                else None,
                "auxiliary_endpoint_policy": "reject_without_forwarding",
                "parallel_tool_calls": "omitted",
                "deadline_origin": "first_accepted_request_or_explicit_begin",
                "dollar_cap_enforced": False,
                "budget_control": dispatch_budget.metadata()
                if dispatch_budget is not None
                else None,
                "supported_request_fields": sorted(SUPPORTED_REQUEST_FIELDS),
                "shutdown_policy": "seal_interrupted_records_before_returning",
            },
        )

    @property
    def binding(self) -> GatewayBinding | None:
        """Return a copy; caller mutation cannot change the recorded identity."""
        return (
            GatewayBinding.model_validate_json(self._binding_json)
            if self._binding_json is not None
            else None
        )

    def begin(self) -> None:
        with self._lock:
            if self._closing.is_set():
                raise RuntimeError("Gateway closed")
            if self._started is None:
                self._started = self._clock()
                self._started_at = timestamp(utcnow())

    def _record(self, path: str, incoming: bytes) -> dict | None:
        with self._lock:
            if self._closing.is_set():
                return None
            record = {
                "id": f"request-{len(self._records) + 1:04d}",
                "path": path,
                "received_at": timestamp(utcnow()),
                "incoming_sha256": digest(incoming),
                "forwarded": False,
                "dispatch_started": False,
                "outcome": "received",
                "usage": None,
            }
            self._records.append(record)
            directory = self.output / record["id"]
            directory.mkdir()
            (directory / "incoming.json").write_bytes(incoming)
            self._save_record(record)
            return record

    def _save_record(self, record: dict) -> None:
        write_json(self.output / record["id"] / "record.json", record)

    def _deny(self, record: dict, reason: str) -> None:
        with self._lock:
            if self._sealed or record["outcome"] != "received":
                return
            record.update(outcome="denied", reason=reason, finished_at=timestamp(utcnow()))
            self._save_record(record)
            self._save_report()

    def _prepare(
        self, record: dict, incoming: dict, handler: BaseHTTPRequestHandler | None = None
    ) -> tuple[dict | None, str | None]:
        if incoming.get("model") != self.model:
            return None, "model_mismatch"
        if set(incoming) - SUPPORTED_REQUEST_FIELDS:
            return None, "unsupported_request_fields"
        if "service_tier" in incoming and self.settings.service_tier is None:
            return None, "undeclared_service_tier"
        if "background" in incoming and incoming["background"] is not False:
            return None, "background_responses_unsupported"
        if "text" in incoming and (
            not isinstance(incoming["text"], dict) or set(incoming["text"]) - {"format"}
        ):
            return None, "unsupported_text_controls"
        # Provider-side references can inherit unrecorded context or instructions.
        items = incoming.get("input", [])
        if isinstance(items, list) and any(
            isinstance(item, dict) and item.get("type") == "item_reference" for item in items
        ):
            return None, "provider_context_reference_forbidden"
        tools = incoming.get("tools", [])
        if not isinstance(tools, list) or any(
            not isinstance(tool, dict) or tool.get("type") != "function" for tool in tools
        ):
            return None, "provider_native_tools_forbidden"
        forwarded = deepcopy(incoming)
        stripped = []
        if self.allowed_function_names is not None:
            forwarded["tools"] = []
            for tool in tools:
                if tool.get("name") in self.allowed_function_names:
                    forwarded["tools"].append(tool)
                else:
                    stripped.append(tool.get("name"))
            choice = forwarded.get("tool_choice")
            if isinstance(choice, dict) and choice.get("name") not in self.allowed_function_names:
                return None, "tool_choice_forbidden"
        forwarded.update(self.settings.model_settings())
        forwarded.pop("parallel_tool_calls", None)
        with self._lock:
            if self._closing.is_set():
                return None, "gateway_closed"
            record["stripped_function_names"] = stripped
            record["control_changes"] = {
                key: {"incoming": incoming.get(key), "forwarded": forwarded.get(key)}
                for key in ("max_output_tokens", "reasoning", "parallel_tool_calls", "service_tier")
                if incoming.get(key) != forwarded.get(key)
                or (key in incoming) != (key in forwarded)
            }
            self.begin()
            if self._clock() - self._started >= self.settings.deadline_seconds:
                return None, "deadline_exhausted"
            if self._reserved_attempts >= self.settings.max_rounds:
                return None, "model_rounds_exhausted"
        raw = canonical_json(forwarded).encode()
        if self._dispatch_budget is not None:
            return self._budget_admission(record, forwarded, raw, handler)
        with self._lock:
            reason = self._admission_denial(record)
            if reason is not None:
                return None, reason
            self._reserve_round(record, raw)
        return forwarded, None

    def _admission_denial(self, record: dict) -> str | None:
        if self._closing.is_set() or record["outcome"] != "received":
            return record.get("reason", "gateway_closed")
        if self._remaining() <= 0:
            return "deadline_exhausted"
        if self._budget_violation is not None:
            return "budget_policy_violation"
        if self._reserved_attempts >= self.settings.max_rounds:
            return "model_rounds_exhausted"
        return None

    def _reserve_round(self, record: dict, raw: bytes) -> None:
        with self._lock:
            self._reserved_attempts += 1
            record.update(
                outcome="in_flight",
                reserved_attempt_number=self._reserved_attempts,
                forwarded_sha256=digest(raw),
            )
            (self.output / record["id"] / "forwarded.json").write_bytes(raw)
            self._save_record(record)

    def _budget_admission(
        self, record: dict, forwarded: dict, raw: bytes, handler: BaseHTTPRequestHandler | None
    ) -> tuple[dict | None, str | None]:
        attempt = None
        if handler is not None:
            attempt = {"handler": handler, "response": None, "body": bytearray()}
            timer = threading.Timer(self._remaining(), self._expire, args=(record, attempt))
            timer.daemon = True
            attempt["timer"] = timer
            with self._lock:
                if reason := self._admission_denial(record):
                    return None, reason
                self._active_attempts[record["id"]] = attempt
                timer.start()
        try:
            # Serialize admission without blocking shutdown's artifact writer
            # lock while the durable ledger waits on its own process lock.
            with self._admission_lock:
                with self._lock:
                    if reason := self._admission_denial(record):
                        return None, reason
                try:
                    self._dispatch_budget.validate_request(forwarded)
                except Exception:
                    return None, "budget_request_rejected"
                with self._lock:
                    if reason := self._admission_denial(record):
                        return None, reason
                    record["budget_reservation_pending"] = True
                    self._save_record(record)
                try:
                    reservation = self._dispatch_budget.reserve(record["id"], digest(raw))
                except BudgetExceeded:
                    return None, "budget_exhausted"
                except ValueError:
                    return None, "budget_reservation_rejected"
                except Exception:
                    return None, "budget_reservation_failed"
                with self._lock:
                    if reason := self._admission_denial(record):
                        return None, reason
                    if (
                        reservation.get("status") != "reserved"
                        or reservation.get("operation_id")
                        != f"{self.binding.execution_id}/{record['id']}"
                        or reservation.get("request_sha256") != digest(raw)
                        or type(reservation.get("reserved_nanodollars")) is not int
                        or reservation["reserved_nanodollars"] < 0
                    ):
                        return None, "budget_reservation_rejected"
                    record.update(
                        budget_reservation_pending=False,
                        budget_operation_id=reservation["operation_id"],
                        budget_reserved_nanodollars=reservation["reserved_nanodollars"],
                    )
                    self._reserve_round(record, raw)
                return forwarded, None
        finally:
            if attempt is not None:
                attempt["timer"].cancel()
                with self._lock:
                    self._active_attempts.pop(record["id"], None)

    def _remaining(self) -> float:
        with self._lock:
            return max(0.0, self.settings.deadline_seconds - (self._clock() - self._started))

    @staticmethod
    def _abort_connection(handler: BaseHTTPRequestHandler) -> None:
        handler.close_connection = True
        with contextlib.suppress(OSError):
            handler.connection.shutdown(socket.SHUT_RDWR)

    @staticmethod
    def _abort_response(response: httpx.Response | None) -> None:
        if response is None:
            return
        # Interrupt HTTP/1 reads on this response's socket. HTTP/2 may share its
        # socket with other callers, so leave that transport to Response.close.
        if response.http_version != "HTTP/2":
            stream = response.extensions.get("network_stream")
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.get_extra_info("socket").shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(Exception):
            response.close()

    def _finish(
        self, record: dict, body: bytearray, outcome: str, *, reason=None, usage=None
    ) -> None:
        """The first terminal writer wins; sealed artifacts never change."""
        with self._lock:
            if self._sealed or record["outcome"] not in {"received", "in_flight"}:
                return
            raw = bytes(body)
            (self.output / record["id"] / "response.body").write_bytes(raw)
            record.update(
                outcome=outcome,
                usage=usage,
                response_sha256=digest(raw),
                finished_at=timestamp(utcnow()),
            )
            if reason is not None:
                record["reason"] = reason
            self._save_record(record)
            self._save_report()

    def _expire(self, record: dict, attempt: dict) -> None:
        with self._lock:
            if self._sealed or record["outcome"] not in {"received", "in_flight"}:
                return
            if record["outcome"] == "received":
                self._deny(record, "deadline_exhausted")
            else:
                self._finish(record, attempt["body"], "interrupted", reason="deadline_exhausted")
        self._abort_connection(attempt["handler"])
        self._abort_response(attempt["response"])

    def _mark_budget_dispatch(self, record: dict, attempt: dict) -> bool:
        with self._lock:
            if self._closing.is_set() or record["outcome"] != "in_flight":
                return False
            if self._remaining() <= 0:
                self._expire(record, attempt)
                return False
            if self._budget_violation is not None:
                self._finish(
                    record, attempt["body"], "interrupted", reason="budget_policy_violation"
                )
                self._error(attempt["handler"], 400, "budget_policy_violation")
                return False
            record["budget_dispatch_pending"] = True
            self._save_record(record)
        try:
            dispatched = self._dispatch_budget.mark_dispatched(record["budget_operation_id"])
            if (
                dispatched.get("status") != "dispatched"
                or dispatched.get("operation_id") != record["budget_operation_id"]
                or dispatched.get("request_sha256") != record["forwarded_sha256"]
            ):
                raise ValueError("Ledger dispatch marker mismatch")
        except Exception:
            self._finish(record, attempt["body"], "interrupted", reason="budget_dispatch_failed")
            self._error(attempt["handler"], 400, "budget_dispatch_failed")
            return False
        with self._lock:
            if self._closing.is_set() or record["outcome"] != "in_flight":
                return False
            if self._remaining() <= 0:
                self._expire(record, attempt)
                return False
            if self._budget_violation is not None:
                self._finish(
                    record, attempt["body"], "interrupted", reason="budget_policy_violation"
                )
                self._error(attempt["handler"], 400, "budget_policy_violation")
                return False
            record.update(budget_dispatch_pending=False, budget_dispatch_marked=True)
            self._save_record(record)
        return True

    def _observe_budget_response(self, record: dict, response: dict) -> None:
        reason = None
        if isinstance(response.get("model"), str) and response["model"] != self.model:
            reason = "returned_model_mismatch"
        elif (
            isinstance(response.get("service_tier"), str) and response["service_tier"] != "default"
        ):
            reason = "returned_service_tier_mismatch"
        usage = response.get("usage")
        if reason is None and isinstance(usage, dict):
            bounds = {
                "input_tokens": self._dispatch_budget.ledger.rates.max_input_tokens_per_request,
                "output_tokens": self.settings.max_output_tokens,
            }
            if any(
                type(usage.get(key)) is int and usage[key] > bound for key, bound in bounds.items()
            ):
                reason = "returned_usage_exceeds_reservation"
        if reason is not None:
            with self._lock:
                if self._sealed or record["outcome"] != "in_flight":
                    return
                if self._budget_violation is None:
                    self._budget_violation = reason
                record["budget_policy_violation"] = reason
                self._save_record(record)
                self._save_report()

    def _forward(self, handler: BaseHTTPRequestHandler, record: dict, payload: dict) -> None:
        response_body = bytearray()
        headers_sent = False
        response = None
        attempt = {"handler": handler, "response": None, "body": response_body}
        timer = threading.Timer(self._remaining(), self._expire, args=(record, attempt))
        timer.daemon = True
        attempt["timer"] = timer
        with self._lock:
            if self._closing.is_set() or record["outcome"] != "in_flight":
                self._abort_connection(handler)
                return
            self._active_attempts[record["id"]] = attempt
            timer.start()
        try:
            headers = {"Accept-Encoding": "identity", "Content-Type": "application/json"}
            if self._upstream_api_key is not None:
                headers["Authorization"] = f"Bearer {self._upstream_api_key}"
            remaining = max(0.001, self._remaining())
            request = self.client.build_request(
                "POST",
                self.upstream_base_url + "/responses",
                headers=headers,
                content=canonical_json(payload).encode(),
                timeout=remaining,
            )
            if self._dispatch_budget is not None and not self._mark_budget_dispatch(
                record, attempt
            ):
                return
            with self._lock:
                if self._closing.is_set() or record["outcome"] != "in_flight":
                    return
                if self._remaining() <= 0:
                    self._expire(record, attempt)
                    return
                if self._budget_violation is not None:
                    self._finish(
                        record, response_body, "interrupted", reason="budget_policy_violation"
                    )
                    self._error(handler, 400, "budget_policy_violation")
                    return
                self._upstream_requests += 1
                record.update(
                    forwarded=True,
                    dispatch_started=True,
                    upstream_request_number=self._upstream_requests,
                )
                self._save_record(record)
            response = self.client.send(request, stream=True, follow_redirects=False)
            with self._lock:
                attempt["response"] = response
                if self._closing.is_set() or record["outcome"] != "in_flight":
                    return
                record["upstream_status"] = response.status_code
                record["upstream_request_id"] = response.headers.get("x-request-id")
                record["response_content_type"] = response.headers.get("content-type", "")
                record["response_content_encoding"] = response.headers.get("content-encoding")
            if response.is_stream_consumed and response.headers.get("content-encoding") not in {
                None,
                "identity",
            }:
                # HTTPX decoded this injected response already. Relaying it under
                # the original headers would corrupt bytes and the capture hash.
                raise ValueError("preconsumed_encoded_response_unsupported")
            handler.send_response(response.status_code)
            for name in ("content-type", "content-encoding", "content-length", "x-request-id"):
                if name in response.headers:
                    handler.send_header(name, response.headers[name])
            handler.send_header("Connection", "close")
            handler.end_headers()
            headers_sent = True
            observer = (
                _BudgetResponseObserver(
                    response.headers.get("content-type", ""),
                    response.headers.get("content-encoding"),
                    lambda value: self._observe_budget_response(record, value),
                )
                if self._dispatch_budget is not None
                else None
            )
            chunks = [response.content] if response.is_stream_consumed else response.iter_raw()
            for chunk in chunks:
                with self._lock:
                    if self._closing.is_set() or record["outcome"] != "in_flight":
                        return
                    if self._remaining() <= 0:
                        self._expire(record, attempt)
                        return
                    if len(response_body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ValueError("Response exceeded the gateway capture limit")
                    response_body.extend(chunk)
                if observer is not None:
                    observer.feed(chunk)
                handler.wfile.write(chunk)
                handler.wfile.flush()
            if observer is not None:
                observer.finish()
            observation = _response_usage(
                bytes(response_body),
                response.headers.get("content-type", ""),
                response.headers.get("content-encoding"),
            )
            if self._remaining() <= 0:
                self._expire(record, attempt)
            else:
                self._finish(record, response_body, "completed", usage=observation)
        except (BrokenPipeError, ConnectionResetError):
            self._finish(record, response_body, "interrupted", reason="downstream_disconnected")
        except Exception as exc:
            reason = (
                "preconsumed_encoded_response_unsupported"
                if str(exc) == "preconsumed_encoded_response_unsupported"
                else type(exc).__name__
            )
            self._finish(record, response_body, "interrupted", reason=reason)
            if not headers_sent:
                self._error(handler, 502, "upstream_request_failed")
        finally:
            timer.cancel()
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()
            with self._lock:
                self._active_attempts.pop(record["id"], None)
            handler.close_connection = True

    @staticmethod
    def _error(handler: BaseHTTPRequestHandler, status: int, reason: str) -> None:
        body = canonical_json(
            {"error": {"type": "gateway_error", "code": reason, "message": reason}}
        ).encode()
        with contextlib.suppress(OSError):
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(body)
        handler.close_connection = True

    def start(self):
        if self._server is not None or self._closing.is_set():
            raise ValueError("Gateway already started or closed")
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                with gateway._lock:
                    gateway._handlers.add(self)
                    if gateway._closing.is_set():
                        gateway._abort_connection(self)

            def finish(self):
                try:
                    super().finish()
                finally:
                    with gateway._lock:
                        gateway._handlers.discard(self)

            def log_message(self, *args):
                pass

            def do_POST(self):
                if not secrets.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + gateway.api_key
                ):
                    gateway._error(self, 401, "invalid_gateway_key")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    length = -1
                if not 0 <= length <= MAX_REQUEST_BYTES:
                    gateway._error(self, 413, "request_size_invalid")
                    return
                try:
                    incoming = self.rfile.read(length)
                except OSError:
                    return
                record = gateway._record(self.path, incoming)
                if record is None:
                    gateway._error(self, 503, "gateway_closed")
                    return
                if self.path != "/v1/responses":
                    gateway._deny(record, "unsupported_auxiliary_endpoint")
                    gateway._error(self, 501, "unsupported_auxiliary_endpoint")
                    return
                try:
                    payload = json.loads(incoming)
                    if not isinstance(payload, dict):
                        raise ValueError("Expected JSON object")
                except (ValueError, UnicodeDecodeError):
                    gateway._deny(record, "invalid_json_request")
                    gateway._error(self, 400, "invalid_json_request")
                    return
                forwarded, reason = gateway._prepare(record, payload, self)
                if reason:
                    gateway._deny(record, reason)
                    gateway._error(
                        self,
                        429
                        if reason.endswith("exhausted") and not reason.startswith("budget_")
                        else 400,
                        reason,
                    )
                    return
                gateway._forward(self, record, forwarded)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def report(self) -> dict:
        with self._lock:
            forwarded = [record for record in self._records if record["forwarded"]]
            known = [record["usage"] for record in forwarded if record.get("usage")]
            complete = all(
                record["outcome"] == "completed"
                and record.get("usage")
                and record["usage"]["model"] == self.model
                for record in forwarded
            )
            input_tokens = sum(usage["input_tokens"] for usage in known)
            output_tokens = sum(usage["output_tokens"] for usage in known)
            return {
                "schema_version": 1,
                "adapter": "controlled-responses-gateway",
                "model": self.model,
                "started_at": self._started_at,
                "closed": self._sealed,
                "upstream_requests": self._upstream_requests,
                "reserved_attempts": self._reserved_attempts,
                "budget_policy_violation": self._budget_violation,
                "max_rounds": self.settings.max_rounds,
                "deadline_seconds": self.settings.deadline_seconds,
                "complete": complete,
                "auxiliary_usage_unknown": False,
                "interrupted": any(record["outcome"] == "interrupted" for record in self._records),
                "totals": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
                "cost_usd": None,
                "denials": [
                    {"id": record["id"], "reason": record["reason"]}
                    for record in self._records
                    if record["outcome"] == "denied"
                ],
                "requests": deepcopy(self._records),
            }

    def _save_report(self) -> None:
        write_json(self.output / "report.json", self.report())

    def _publish_archive(self) -> None:
        """Publish the inventory only after every artifact writer is sealed."""
        if not self._sealed:
            raise RuntimeError("Gateway artifacts have not been sealed")
        paths = [self.output / "gateway.json", self.output / "report.json"]
        for directory in sorted(self.output.glob("request-*")):
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("Invalid gateway request directory")
            for path in sorted(directory.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Gateway archives cannot contain symlinks")
                if path.is_file():
                    paths.append(path)
        files = {}
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise ValueError("Invalid gateway archive file")
            files[path.relative_to(self.output).as_posix()] = digest(path.read_bytes())
        temporary = self.output / ".archive.json.tmp"
        try:
            write_json(temporary, {"schema_version": 1, "files": files})
            temporary.replace(self.output / "archive.json")
        finally:
            temporary.unlink(missing_ok=True)

    def _seal_records(self) -> None:
        with self._lock:
            try:
                for record in self._records:
                    if record["outcome"] in {"received", "in_flight"}:
                        attempt = self._active_attempts.get(record["id"])
                        self._finish(
                            record,
                            attempt["body"] if attempt else bytearray(),
                            "interrupted" if record["outcome"] == "in_flight" else "denied",
                            reason="gateway_closed",
                        )
            finally:
                # Seal even if a disk write failed. A failed close must neither
                # publish an inventory nor leave late handler writes enabled.
                self._sealed = True
            self._save_report()

    def _stop_transport(self) -> None:
        with self._lock:
            attempts = list(self._active_attempts.values())
            handlers = list(self._handlers)
        for handler in handlers:
            self._abort_connection(handler)
        for attempt in attempts:
            attempt["timer"].cancel()
            self._abort_response(attempt["response"])
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._owns_client:
            self.client.close()

    def close(self) -> None:
        # Concurrent callers wait for the same complete archive or failure;
        # seeing a sealed report alone is not successful shutdown publication.
        with self._close_lock:
            if self._close_finished:
                if self._close_error is not None:
                    raise RuntimeError(
                        "Gateway shutdown did not produce an archive"
                    ) from self._close_error
                return
            self._closing.set()
            error: BaseException | None = None
            try:
                self._seal_records()
            except BaseException as exc:
                error = exc
            try:
                self._stop_transport()
            except BaseException as exc:
                error = error or exc
            if error is None:
                try:
                    self._publish_archive()
                except BaseException as exc:
                    error = exc
            self._close_error = error
            self._close_finished = True
            if error is not None:
                raise error

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
