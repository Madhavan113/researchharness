from __future__ import annotations

import contextlib
import gzip
import json
import shutil
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from research_harness.evaluation.budget import BudgetLedger, RateCard
from research_harness.evaluation.dispatch_budget import DispatchBudget, DispatchPolicy
from research_harness.evaluation.gateway_usage import verify_gateway_usage
from research_harness.execution import DiscoverySettings, GatewayBinding
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.util import digest, write_json

MODEL = "gpt-5.4-mini"
BINDING = GatewayBinding(
    execution_id="a" * 32,
    case_id="independent-usage-fixture",
    runtime="direct-controlled-gateway",
    task_sha256="b" * 64,
)
SETTINGS = DiscoverySettings()
BUDGET_SETTINGS = DiscoverySettings(service_tier="default")


def provider_response(identity="resp-1", *, count=1, status="completed", cache=True):
    usage = {"input_tokens": 100 * count, "output_tokens": 10 * count, "total_tokens": 110 * count}
    if cache:
        usage["input_tokens_details"] = {"cached_tokens": 20 * count}
    return {"id": identity, "model": MODEL, "status": status, "output": [], "usage": usage}


def streamed_response(response, *, compressed=False):
    raw = (
        f"event: response.{response['status']}\ndata: "
        + json.dumps({"type": f"response.{response['status']}", "response": response})
        + "\n\ndata: [DONE]\n\n"
    ).encode()
    headers = {"Content-Type": "text/event-stream"}
    if compressed:
        raw = gzip.compress(raw, mtime=0)
        headers["Content-Encoding"] = "gzip"

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield raw[:23]
            yield raw[23:]

    return httpx.Response(200, headers=headers, stream=Stream())


def post(proxy, payload=None):
    with contextlib.suppress(httpx.HTTPError):
        return httpx.post(
            proxy.base_url + "/responses",
            json=payload or {"model": MODEL, "input": "Fixture task"},
            headers={"Authorization": "Bearer " + proxy.api_key},
            timeout=3,
        )


def make_archive(path, steps, *, payloads=None, settings=SETTINGS, binding=BINDING):
    def upstream(request):
        step = steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        with ResponsesGateway(
            path,
            model=MODEL,
            settings=settings,
            upstream_base_url="https://synthetic-provider.example/v1",
            client=client,
            binding=binding,
            allowed_function_names={"search_sources"},
        ) as proxy:
            for payload in payloads or [None] * len(steps):
                post(proxy, payload)
    return path


@pytest.fixture(scope="module")
def complete_archive(tmp_path_factory):
    return make_archive(
        tmp_path_factory.mktemp("gateway-usage") / "complete",
        [
            httpx.Response(200, json=provider_response()),
            streamed_response(provider_response("resp-2", count=2), compressed=True),
        ],
    )


@pytest.fixture
def archive(tmp_path, complete_archive):
    return Path(shutil.copytree(complete_archive, tmp_path / "gateway"))


def verify(path, binding=BINDING, **kwargs):
    return verify_gateway_usage(
        path,
        binding,
        expected_model=kwargs.pop("expected_model", MODEL),
        expected_model_settings=kwargs.pop("expected_model_settings", SETTINGS.model_settings()),
        **kwargs,
    )


def load(path):
    return json.loads(path.read_text())


def reindex(path):
    write_json(
        path / "archive.json",
        {
            "schema_version": 1,
            "files": {
                file.relative_to(path).as_posix(): digest(file.read_bytes())
                for file in path.rglob("*")
                if file.is_file() and file.name != "archive.json"
            },
        },
    )


def update_record(path, update, *, number=1):
    report = load(path / "report.json")
    record = report["requests"][number - 1]
    update(record)
    write_json(path / record["id"] / "record.json", record)
    write_json(path / "report.json", report)
    reindex(path)


def test_real_gateway_json_and_gzip_sse_are_verified_without_proposal(archive):
    result = verify(archive / "archive.json")
    assert result["status"] == "verified_complete", result["errors"]
    assert result["total_tokens"] == result["completed_token_lower_bound"] == 330
    assert result["input_tokens"] == 300 and result["output_tokens"] == 30
    assert result["cached_input_tokens"] == result["cached_input_token_lower_bound"] == 60
    assert result["dispatched_requests"] == result["reserved_attempts"] == 2
    assert result["returned_models"] == [MODEL]
    assert result["response_count"] == 2 and result["cost_usd"] is None
    assert set(result["files"]) == {"archive.json", *load(archive / "archive.json")["files"]}
    assert verify(archive)["total_tokens"] == 330
    assert result["upstream_transport"] == "caller-supplied"


def test_legacy_gateway_transport_provenance_is_unknown(archive):
    metadata = load(archive / "gateway.json")
    metadata.pop("upstream_transport")
    write_json(archive / "gateway.json", metadata)
    reindex(archive)
    result = verify(archive)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["upstream_transport"] is None


def test_unknown_gateway_transport_provenance_is_invalid(archive):
    metadata = load(archive / "gateway.json")
    metadata["upstream_transport"] = "claimed-live"
    write_json(archive / "gateway.json", metadata)
    reindex(archive)
    assert verify(archive)["status"] == "invalid"


def test_gateway_constructed_standard_transport_is_recorded_without_dispatch(tmp_path):
    output = tmp_path / "gateway"
    with ResponsesGateway(
        output,
        model=MODEL,
        settings=SETTINGS,
        upstream_base_url="https://api.openai.com/v1",
        binding=BINDING,
    ):
        pass
    result = verify(output)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["upstream_transport"] == "httpx-default"
    assert result["dispatched_requests"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_id", "c" * 32),
        ("case_id", "another-case"),
        ("runtime", "omnigent-controlled-gateway"),
        ("phase", "followup"),
        ("task_sha256", "d" * 64),
    ],
)
def test_all_host_binding_fields_are_independent_requirements(archive, field, value):
    result = verify(archive, BINDING.model_copy(update={field: value}))
    assert result["status"] == "invalid" and result["total_tokens"] is None
    assert "binding mismatch" in result["errors"][0]


@pytest.mark.parametrize("change", ["unbound", "open", "wrong_model", "settings"])
def test_closed_binding_and_model_controls_are_required_even_after_reindex(archive, change):
    file = archive / ("report.json" if change == "open" else "gateway.json")
    data = load(file)
    if change == "unbound":
        data["binding"] = None
    elif change == "open":
        data["closed"] = False
    elif change == "wrong_model":
        data["model"] = "another-model"
    else:
        data["discovery_settings"]["max_output_tokens"] = 12
    write_json(file, data)
    reindex(archive)
    assert verify(archive)["status"] == "invalid"


@pytest.mark.parametrize(
    "field,value", [("max_rounds", 30), ("deadline_seconds", 3600), ("max_searches", 20)]
)
def test_host_budget_expectations_reject_relaxed_archived_limits(archive, field, value):
    gateway = load(archive / "gateway.json")
    gateway["discovery_settings"][field] = value
    write_json(archive / "gateway.json", gateway)
    report = load(archive / "report.json")
    if field in report:
        report[field] = value
    write_json(archive / "report.json", report)
    reindex(archive)
    result = verify(archive, expected_budgets=SETTINGS.budgets())
    assert result["status"] == "invalid"
    assert "budgets mismatch" in result["errors"][0]


def test_exact_host_budget_expectations_are_verified(archive):
    assert verify(archive, expected_budgets=SETTINGS.budgets())["status"] == "verified_complete"


@pytest.mark.parametrize(
    "kind", ["hash", "missing", "unindexed", "indexed_foreign", "symlink", "traversal"]
)
def test_archive_hashes_paths_and_full_inventory_are_required(archive, tmp_path, kind):
    body = archive / "request-0001/response.body"
    if kind == "hash":
        body.write_bytes(body.read_bytes() + b" ")
    elif kind == "missing":
        body.unlink()
    elif kind in {"unindexed", "indexed_foreign"}:
        (archive / "foreign.json").write_text("{}")
        if kind == "indexed_foreign":
            reindex(archive)
    elif kind == "symlink":
        outside = tmp_path / "outside.body"
        outside.write_bytes(body.read_bytes())
        body.unlink()
        body.symlink_to(outside)
    else:
        index = load(archive / "archive.json")
        index["files"]["../outside.json"] = "a" * 64
        write_json(archive / "archive.json", index)
    result = verify(archive)
    assert result["status"] == "invalid" and result["total_tokens"] is None


def test_summary_inflation_does_not_override_hashed_raw_usage(archive):
    def inflate(record):
        record["usage"]["input_tokens"] += 1000
        record["usage"]["provider_usage"]["input_tokens"] += 1000
        record["usage"]["provider_usage"]["total_tokens"] += 1000

    update_record(archive, inflate)
    report = load(archive / "report.json")
    report["totals"]["input_tokens"] += 1000
    report["totals"]["total_tokens"] += 1000
    write_json(archive / "report.json", report)
    reindex(archive)
    result = verify(archive)
    assert result["status"] == "invalid"
    assert "raw provider response" in result["errors"][0]


@pytest.mark.parametrize(
    "field,value",
    [
        ("reserved_attempts", 3),
        ("upstream_requests", 0),
        ("complete", False),
        ("interrupted", True),
        ("totals", {"input_tokens": 300, "output_tokens": 30, "total_tokens": 999}),
    ],
)
def test_report_counters_are_recomputed(archive, field, value):
    report = load(archive / "report.json")
    report[field] = value
    write_json(archive / "report.json", report)
    reindex(archive)
    assert verify(archive)["status"] == "invalid"


@pytest.mark.parametrize(
    "field,value",
    [
        ("dispatch_started", False),
        ("upstream_request_number", 2),
        ("reserved_attempt_number", True),
        ("response_sha256", "0" * 64),
        ("id", "request-0002"),
    ],
)
def test_records_cannot_reassign_or_duplicate_request_identity(archive, field, value):
    update_record(archive, lambda record: record.update({field: value}))
    assert verify(archive)["status"] == "invalid"


def test_record_file_must_equal_report_request(archive):
    file = archive / "request-0001/record.json"
    record = load(file)
    record["reason"] = "invented reason"
    write_json(file, record)
    reindex(archive)
    assert verify(archive)["status"] == "invalid"


@pytest.mark.parametrize(
    "extra",
    [{"temperature": 0.7}, {"previous_response_id": "foreign"}, {"parallel_tool_calls": False}],
)
def test_forwarded_controls_cannot_be_added_with_consistent_hashes(archive, extra):
    file = archive / "request-0001/forwarded.json"
    payload = load(file)
    payload.update(extra)
    write_json(file, payload)
    update_record(archive, lambda record: record.update(forwarded_sha256=digest(file.read_bytes())))
    assert verify(archive)["status"] == "invalid"


@pytest.mark.parametrize("conflict", [False, True])
def test_duplicate_provider_ids_never_inflate_usage(tmp_path, conflict):
    archive = make_archive(
        tmp_path / "gateway",
        [
            httpx.Response(200, json=provider_response()),
            httpx.Response(200, json=provider_response(count=2 if conflict else 1)),
        ],
    )
    result = verify(archive)
    assert result["status"] == "invalid" and result["completed_token_lower_bound"] is None
    assert "Duplicate provider response id" in result["errors"][0]


@pytest.mark.parametrize(
    "kind", ["missing_usage", "nonterminal", "bad_total", "transport_failure", "truncated_json"]
)
def test_partial_dispatches_preserve_only_previous_completed_usage(tmp_path, kind):
    data = provider_response("resp-unknown")
    if kind == "missing_usage":
        data.pop("usage")
    elif kind == "nonterminal":
        data["status"] = "in_progress"
    elif kind == "bad_total":
        data["usage"]["total_tokens"] = 999
    second = httpx.Response(200, json=data)
    if kind == "transport_failure":
        second = httpx.ConnectError("Authored transport failure")
    elif kind == "truncated_json":
        second = httpx.Response(200, content=b'{"id":')
    archive = make_archive(
        tmp_path / "gateway", [httpx.Response(200, json=provider_response()), second]
    )
    result = verify(archive)
    assert result["status"] == "verified_lower_bound", result["errors"]
    assert result["total_tokens"] is None and result["completed_token_lower_bound"] == 110
    assert result["input_tokens"] == 100 and result["output_tokens"] == 10
    assert result["cached_input_tokens"] is None and result["cached_input_token_lower_bound"] == 20
    assert result["dispatched_requests"] == result["reserved_attempts"] == 2
    assert len(result["unknown_requests"]) == 1


@pytest.mark.parametrize("status", ["failed", "incomplete"])
def test_unsuccessful_terminal_response_can_have_complete_accounting(tmp_path, status):
    archive = make_archive(
        tmp_path / "gateway", [streamed_response(provider_response(status=status))]
    )
    result = verify(archive)
    assert result["status"] == "verified_complete" and result["total_tokens"] == 110
    assert result["responses"][0]["status"] == status


@pytest.mark.parametrize("denied", [False, True])
def test_zero_dispatched_calls_establish_zero_without_successful_proposal(tmp_path, denied):
    archive = make_archive(
        tmp_path / "gateway", [], payloads=[{"model": "wrong-model"}] if denied else None
    )
    result = verify(archive)
    assert result["status"] == "verified_complete" and result["total_tokens"] == 0
    assert result["reserved_attempts"] == result["dispatched_requests"] == 0
    assert result["response_count"] == 0 and result["returned_models"] == []


def test_interrupted_reserved_attempt_with_no_dispatch_has_known_zero(tmp_path):
    now = [0.0]

    class DelayedClient(httpx.Client):
        def build_request(self, *args, **kwargs):
            request = super().build_request(*args, **kwargs)
            now[0] = 2.0
            return request

    path = tmp_path / "gateway"
    with DelayedClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected dispatch"))
    ) as client:
        with ResponsesGateway(
            path,
            model=MODEL,
            settings=DiscoverySettings(deadline_seconds=1),
            upstream_base_url="https://synthetic-provider.example/v1",
            client=client,
            binding=BINDING,
            clock=lambda: now[0],
        ) as proxy:
            post(proxy)
    result = verify(path)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["total_tokens"] == 0 and result["interrupted"]
    assert result["reserved_attempts"] == 1 and result["dispatched_requests"] == 0


def test_missing_cache_details_do_not_invent_a_discount(tmp_path):
    archive = make_archive(
        tmp_path / "gateway", [httpx.Response(200, json=provider_response(cache=False))]
    )
    result = verify(archive)
    assert result["status"] == "verified_complete" and result["total_tokens"] == 110
    assert result["cached_input_tokens"] is None and result["cached_input_token_lower_bound"] == 0


def test_explicitly_unknown_cache_details_preserve_complete_token_accounting(tmp_path):
    response = provider_response()
    response["usage"]["input_tokens_details"] = None
    archive = make_archive(tmp_path / "gateway", [httpx.Response(200, json=response)])
    result = verify(archive)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["total_tokens"] == 110 and result["cached_input_tokens"] is None


def test_impossible_cache_discount_is_invalid(tmp_path):
    response = provider_response()
    response["usage"]["input_tokens_details"]["cached_tokens"] = 101
    archive = make_archive(tmp_path / "gateway", [httpx.Response(200, json=response)])
    assert verify(archive)["status"] == "invalid"


def test_interrupted_capture_cannot_be_silently_removed_and_reindexed(tmp_path):
    archive = make_archive(tmp_path / "gateway", [httpx.ConnectError("Authored failure")])
    (archive / "request-0001/response.body").unlink()
    update_record(archive, lambda record: record.pop("response_sha256"))
    result = verify(archive)
    assert result["status"] == "invalid"
    assert "terminal capture" in result["errors"][0]


def test_invalid_compressed_stream_stays_unknown_without_crashing(tmp_path):
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\xff\xff"

    response = httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=Stream())
    archive = make_archive(tmp_path / "gateway", [response])
    result = verify(archive)
    assert result["status"] == "verified_lower_bound", result["errors"]
    assert result["total_tokens"] is None and result["completed_token_lower_bound"] == 0


def test_multiple_terminal_events_in_one_stream_are_invalid(tmp_path):
    raw = b"".join(
        (
            "data: "
            + json.dumps({"type": "response.completed", "response": provider_response(identity)})
            + "\n\n"
        ).encode()
        for identity in ("resp-1", "resp-2")
    )

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield raw

    archive = make_archive(
        tmp_path / "gateway",
        [httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Stream())],
    )
    result = verify(archive)
    assert result["status"] == "invalid" and result["total_tokens"] is None


def test_sse_event_and_terminal_status_must_agree(tmp_path):
    raw = (
        "event: response.failed\ndata: "
        + json.dumps({"type": "response.completed", "response": provider_response()})
        + "\n\n"
    ).encode()

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield raw

    archive = make_archive(
        tmp_path / "gateway",
        [httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Stream())],
    )
    assert verify(archive)["status"] == "invalid"


def test_returned_model_must_match_external_expectation(tmp_path):
    response = provider_response()
    response["model"] = "foreign-model"
    archive = make_archive(tmp_path / "gateway", [httpx.Response(200, json=response)])
    result = verify(archive)
    assert result["status"] == "invalid" and result["returned_models"] == ["foreign-model"]


@pytest.mark.parametrize("model", [MODEL, "foreign-model"])
def test_partial_sse_retains_observed_model_without_inventing_tokens(tmp_path, model):
    raw = (
        "data: "
        + json.dumps(
            {
                "type": "response.created",
                "response": {"id": "resp-partial", "model": model, "status": "in_progress"},
            }
        )
        + "\n\n"
    ).encode()

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield raw

    archive = make_archive(
        tmp_path / "gateway",
        [httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Stream())],
    )
    result = verify(archive)
    assert result["returned_models"] == [model]
    assert result["total_tokens"] is None
    assert result["status"] == ("verified_lower_bound" if model == MODEL else "invalid")


@pytest.mark.parametrize("file", ["archive.json", "report.json"])
def test_duplicate_json_keys_are_not_silently_accepted(archive, file):
    path = archive / file
    raw = path.read_bytes()
    path.write_bytes(b'{"schema_version":1,' + raw[1:])
    if file != "archive.json":
        reindex(archive)
    assert verify(archive)["status"] == "invalid"


def make_budget_archive(
    path, steps, *, settings=BUDGET_SETTINGS, wrapper=None, clock=None, configure_client=None
):
    rates = RateCard(
        model=MODEL,
        snapshot=MODEL,
        input_usd_per_million="1",
        output_usd_per_million="2",
        max_input_tokens_per_request=1000,
        price_source_url="https://fixture.invalid/pricing",
        price_as_of="2026-09-08",
    )
    budget = DispatchBudget(
        BudgetLedger(path.parent / "ledger.json", rates=rates, ceiling_usd="1"),
        policy=DispatchPolicy(
            mode="fixture", upstream_base_url="https://fixture.invalid/v1", model=MODEL
        ),
        binding=BINDING,
        settings=settings,
    )

    def upstream(request):
        assert steps, "Unexpected provider dispatch"
        step = steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        if configure_client is not None:
            configure_client(client)
        with ResponsesGateway(
            path,
            model=MODEL,
            settings=settings,
            binding=BINDING,
            upstream_base_url="https://fixture.invalid/v1",
            client=client,
            dispatch_budget=wrapper(budget) if wrapper else budget,
            **({"clock": clock} if clock else {}),
        ) as proxy:
            for _ in range(max(1, len(steps))):
                post(proxy)
    return path, budget.metadata()


def tier_response(identity="resp-1", *, tier="default", count=1, status="completed"):
    response = provider_response(identity, count=count, status=status)
    if tier is not None:
        response["service_tier"] = tier
    return response


def verify_budget(path, control, **kwargs):
    return verify(
        path,
        expected_model_settings=kwargs.pop(
            "expected_model_settings", BUDGET_SETTINGS.model_settings()
        ),
        expected_budgets=kwargs.pop("expected_budgets", BUDGET_SETTINGS.budgets()),
        expected_budget_control=control,
        **kwargs,
    )


@pytest.fixture(scope="module")
def complete_budget_archive(tmp_path_factory):
    return make_budget_archive(
        tmp_path_factory.mktemp("budget-usage") / "gateway",
        [
            httpx.Response(200, json=tier_response()),
            streamed_response(tier_response("resp-2", count=2), compressed=True),
        ],
    )


@pytest.fixture
def budget_archive(tmp_path, complete_budget_archive):
    path, control = complete_budget_archive
    return Path(shutil.copytree(path, tmp_path / "gateway")), deepcopy(control)


def test_request_settlement_evidence_binds_raw_files_and_budget_reservation(
    budget_archive, tmp_path
):
    path, control = budget_archive
    result = verify_budget(path, control)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["budget_control_verified"] and result["budget_settlement_complete"]
    assert result["total_tokens"] == 330
    requests = result["verified_requests"]
    assert len(requests) == 2 and requests[0]["evidence_sha256"] != requests[1]["evidence_sha256"]
    for number, request in enumerate(requests, 1):
        assert request["request_id"] == f"request-{number:04d}"
        assert request["budget_operation_id"] == BINDING.execution_id + "/" + request["request_id"]
        assert (
            request["request_sha256"]
            == request["forwarded_sha256"]
            == digest((path / request["request_id"] / "forwarded.json").read_bytes())
        )
        assert request["incoming_sha256"] == digest(
            (path / request["request_id"] / "incoming.json").read_bytes()
        )
        assert request["budget_reserved_nanodollars"] == 13_000_000
        assert request["dispatched"] and request["budget_dispatch_marked"]
        assert request["status"] == "complete" and request["issues"] == []
        assert request["response_service_tier"] == "default"
        assert request["usage"]["input_tokens"] == 100 * number
    copy = Path(shutil.copytree(path, tmp_path / "copy"))
    assert verify_budget(copy, control)["verified_requests"] == requests


def test_budget_metadata_must_match_external_controls(budget_archive):
    path, control = budget_archive
    changed = deepcopy(control)
    changed["ceiling_nanodollars"] += 1
    result = verify_budget(path, changed)
    assert result["status"] == "invalid" and result["verified_requests"] == []
    assert result["budget_control"] is None and not result["budget_control_verified"]


def test_unmatched_budget_metadata_does_not_create_settlement_assurance(budget_archive):
    path, _ = budget_archive
    result = verify_budget(path, None)
    assert result["status"] == "verified_complete" and result["total_tokens"] == 330
    assert not result["budget_control_verified"] and not result["budget_settlement_complete"]
    assert all(item["status"] == "unknown" for item in result["verified_requests"])


def test_late_integrity_failure_cannot_leak_earlier_settlement_entries(budget_archive):
    path, control = budget_archive
    report = load(path / "report.json")
    report["totals"]["total_tokens"] += 1
    write_json(path / "report.json", report)
    reindex(path)
    result = verify_budget(path, control)
    assert result["status"] == "invalid" and result["verified_requests"] == []
    assert result["total_tokens"] is None and result["responses"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("budget_reserved_nanodollars", 1),
        ("budget_reserved_nanodollars", True),
        ("budget_operation_id", "foreign/request-0001"),
        ("budget_dispatch_pending", True),
        ("budget_dispatch_marked", False),
    ],
)
def test_budget_request_markers_and_amounts_are_independently_checked(budget_archive, field, value):
    path, control = budget_archive
    update_record(path, lambda record: record.update({field: value}))
    result = verify_budget(path, control)
    assert result["status"] == "invalid" and result["verified_requests"] == []


@pytest.mark.parametrize("tier", [None, 7])
def test_missing_or_invalid_returned_tier_keeps_known_tokens_but_holds_budget(tmp_path, tier):
    path, control = make_budget_archive(
        tmp_path / "gateway", [httpx.Response(200, json=tier_response(tier=tier))]
    )
    result = verify_budget(path, control)
    assert result["status"] == "verified_complete" and result["total_tokens"] == 110
    request = result["verified_requests"][0]
    assert request["status"] == "unknown" and request["response_service_tier"] is None
    assert request["usage"]["input_tokens"] == 100 and not result["budget_settlement_complete"]


def test_record_only_tier_claim_cannot_supply_missing_raw_tier(tmp_path):
    path, control = make_budget_archive(
        tmp_path / "gateway", [httpx.Response(200, json=tier_response(tier=None))]
    )
    update_record(path, lambda record: record.update(response_service_tier="default"))
    request = verify_budget(path, control)["verified_requests"][0]
    assert request["status"] == "unknown" and request["response_service_tier"] is None


@pytest.mark.parametrize("partial", [False, True])
def test_foreign_tier_in_complete_or_partial_raw_response_invalidates_settlement(tmp_path, partial):
    response = tier_response(tier="priority")
    if partial:
        raw = (
            "data: " + json.dumps({"type": "response.created", "response": response}) + "\n\n"
        ).encode()

        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                yield raw

        step = httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Stream())
    else:
        step = httpx.Response(200, json=response)
    path, control = make_budget_archive(tmp_path / "gateway", [step])
    result = verify_budget(path, control)
    assert result["status"] == "invalid" and result["verified_requests"] == []
    assert "service tier" in result["errors"][0]


@pytest.mark.parametrize("field,amount", [("input_tokens", 1001), ("output_tokens", 6001)])
def test_provider_usage_outside_reserved_bounds_cannot_settle(tmp_path, field, amount):
    response = tier_response()
    response["usage"][field] = amount
    response["usage"]["total_tokens"] = (
        response["usage"]["input_tokens"] + response["usage"]["output_tokens"]
    )
    path, control = make_budget_archive(tmp_path / "gateway", [httpx.Response(200, json=response)])
    result = verify_budget(path, control)
    assert result["status"] == "invalid" and result["verified_requests"] == []
    assert "reserved token bounds" in result["errors"][0]


@pytest.mark.parametrize("status", ["failed", "incomplete"])
def test_terminal_task_failure_can_still_establish_request_settlement(tmp_path, status):
    path, control = make_budget_archive(
        tmp_path / "gateway", [streamed_response(tier_response(status=status))]
    )
    result = verify_budget(path, control)
    assert result["verified_requests"][0]["status"] == "complete"
    assert result["verified_requests"][0]["usage"]["status"] == status


@pytest.mark.parametrize("stage", ["reserve", "mark_dispatched"])
def test_pending_ledger_operation_is_not_release_evidence(tmp_path, stage):
    class UncertainBudget:
        def __init__(self, budget):
            self.budget = budget

        def __getattr__(self, name):
            if name == stage:

                def uncertain(*args, **kwargs):
                    getattr(self.budget, name)(*args, **kwargs)
                    raise RuntimeError("Authored failure after durable ledger write")

                return uncertain
            return getattr(self.budget, name)

    path, control = make_budget_archive(tmp_path / "gateway", [], wrapper=UncertainBudget)
    result = verify_budget(path, control)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["total_tokens"] == 0 and result["dispatched_requests"] == 0
    request = result["verified_requests"][0]
    assert request["status"] == "unknown" and not request["dispatched"]
    assert request[
        "budget_reservation_pending" if stage == "reserve" else "budget_dispatch_pending"
    ]
    assert not result["budget_settlement_complete"]


def test_verified_pre_dispatch_deadline_can_supply_no_dispatch_proof(tmp_path):
    now = [0.0]

    def configure_client(client):
        original = client.build_request

        def delayed_request(*args, **kwargs):
            request = original(*args, **kwargs)
            now[0] = 2.0
            return request

        client.build_request = delayed_request

    settings = BUDGET_SETTINGS.model_copy(update={"deadline_seconds": 1})
    path, control = make_budget_archive(
        tmp_path / "gateway",
        [],
        settings=settings,
        clock=lambda: now[0],
        configure_client=configure_client,
    )
    result = verify_budget(path, control, expected_budgets=settings.budgets())
    assert result["status"] == "verified_complete", result["errors"]
    request = result["verified_requests"][0]
    assert request["status"] == "not_dispatched" and not request["dispatched"]
    assert request["budget_operation_id"] and request["request_sha256"]
    assert not request["budget_dispatch_marked"] and not request["budget_dispatch_pending"]
    assert result["total_tokens"] == 0


def test_explicit_service_tier_is_reconstructed_for_unbudgeted_archive(tmp_path):
    path = make_archive(
        tmp_path / "gateway",
        [httpx.Response(200, json=tier_response())],
        settings=BUDGET_SETTINGS,
        payloads=[{"model": MODEL, "input": "Fixture", "service_tier": "priority"}],
    )
    result = verify(path, expected_model_settings=BUDGET_SETTINGS.model_settings())
    assert result["status"] == "verified_complete", result["errors"]
    request = result["verified_requests"][0]
    assert request["status"] == "complete" and request["response_service_tier"] == "default"
    assert request["budget_operation_id"] is None and result["budget_settlement_complete"] is None


def test_early_response_metadata_can_establish_same_response_tier(tmp_path):
    early = {
        "id": "resp-1",
        "model": MODEL,
        "service_tier": "default",
        "status": "in_progress",
        "usage": None,
    }
    raw = b"".join(
        ("data: " + json.dumps({"type": kind, "response": response}) + "\n\n").encode()
        for kind, response in [
            ("response.created", early),
            ("response.completed", tier_response(tier=None)),
        ]
    )

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield raw

    path, control = make_budget_archive(
        tmp_path / "gateway",
        [httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Stream())],
    )
    result = verify_budget(path, control)
    assert result["status"] == "verified_complete", result["errors"]
    assert result["verified_requests"][0]["status"] == "complete"
    assert result["verified_requests"][0]["response_service_tier"] == "default"


def test_partial_usage_outside_budget_bounds_cannot_supply_settlement(tmp_path):
    response = tier_response()
    response["status"] = "in_progress"
    response["usage"] = {"input_tokens": 1001, "output_tokens": 0, "total_tokens": 1001}
    raw = (
        "data: " + json.dumps({"type": "response.created", "response": response}) + "\n\n"
    ).encode()

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield raw

    path, control = make_budget_archive(
        tmp_path / "gateway",
        [httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Stream())],
    )
    result = verify_budget(path, control)
    assert result["status"] == "invalid" and result["verified_requests"] == []
    assert "reserved token bounds" in result["errors"][0]
