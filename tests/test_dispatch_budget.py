from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import httpx
import pytest
from openai import OpenAI

from research_harness.evaluation.budget import (
    AuthorizationRecord,
    BudgetExceeded,
    BudgetLedger,
    CancellationProof,
    RateCard,
)
from research_harness.evaluation.dispatch_budget import (
    STANDARD_MODEL,
    DispatchBudget,
    DispatchPolicy,
)
from research_harness.execution import DiscoverySettings, GatewayBinding
from research_harness.integrations.model_gateway import ResponsesGateway
from research_harness.util import digest

MODEL = "fixture-snapshot-2026-09-08"
SETTINGS = DiscoverySettings(max_output_tokens=10, service_tier="default")
BINDING = GatewayBinding(
    execution_id="a" * 32, case_id="budget-fixture", runtime="direct", task_sha256="b" * 64
)
APPROVAL = AuthorizationRecord(status="approved", reference="external-approval-fixture-only")


def rate_card(*, standard=False, **overrides):
    return RateCard.model_validate(
        {
            "model": STANDARD_MODEL if standard else MODEL,
            "snapshot": STANDARD_MODEL if standard else MODEL,
            "input_usd_per_million": "0.75" if standard else "1",
            "output_usd_per_million": "4.50" if standard else "2",
            "max_input_tokens_per_request": 400_000 if standard else 100,
            "price_source_url": "https://prices.fixture.example/model",
            "price_as_of": "2026-09-08",
            **overrides,
        }
    )


def make_budget(
    path,
    *,
    standard=False,
    rates=None,
    settings=SETTINGS,
    binding=BINDING,
    ceiling="1",
    authorization=None,
):
    ledger = BudgetLedger(
        path / "budget.json",
        rates=rates or rate_card(standard=standard),
        ceiling_usd=ceiling,
        authorization=authorization,
    )
    return DispatchBudget(
        ledger,
        policy=DispatchPolicy(
            mode="openai-standard" if standard else "fixture",
            upstream_base_url="https://api.openai.com/v1"
            if standard
            else "https://fixture.invalid/v1",
            model=STANDARD_MODEL if standard else MODEL,
        ),
        binding=binding,
        settings=settings,
    )


def payload(**overrides):
    return {
        "model": MODEL,
        "input": "Find the authored source",
        **SETTINGS.model_settings(),
        **overrides,
    }


def provider_response(*, status="completed", **overrides):
    return {
        "id": "response-fixture-1",
        "object": "response",
        "model": MODEL,
        "status": status,
        "service_tier": "default",
        "output": [],
        "usage": {
            "input_tokens": 5,
            "output_tokens": 2,
            "total_tokens": 7,
            "input_tokens_details": {"cached_tokens": 1},
        },
        **overrides,
    }


def make_archive(path, budget, *, response=None, failure=None, before_build=None, request_count=1):
    dispatched = []

    def upstream(request):
        dispatched.append(request)
        if failure is not None:
            raise failure
        return httpx.Response(
            200,
            json=response
            if response is not None
            else provider_response(id=f"response-fixture-{len(dispatched)}"),
        )

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        if before_build is not None:
            client.build_request = before_build
        with ResponsesGateway(
            path,
            model=MODEL,
            settings=budget.settings,
            binding=budget.binding,
            upstream_base_url=budget.policy.upstream_base_url,
            client=client,
            dispatch_budget=budget,
        ) as proxy:
            for _ in range(request_count):
                result = httpx.post(
                    proxy.base_url + "/responses",
                    json=payload(),
                    headers={"Authorization": "Bearer " + proxy.api_key},
                    timeout=5,
                )
    return path / "archive.json", result, dispatched


def test_policy_and_metadata_are_immutable_and_shared_across_cases(tmp_path):
    budget = make_budget(tmp_path / "first")
    other = make_budget(
        tmp_path / "second",
        binding=BINDING.model_copy(
            update={"execution_id": "c" * 32, "case_id": "other", "runtime": "omnigent"}
        ),
    )
    assert budget.metadata() == other.metadata()
    metadata = budget.metadata()
    assert metadata["authorization_is_dispatch_permission"] is False
    assert metadata["input_bound"] == {
        "method": "fixture_host_bound",
        "max_input_tokens": 100,
        "locally_tokenized": False,
    }
    assert metadata["rates_sha256"] == budget.ledger.rates.fingerprint()
    assert str(tmp_path) not in json.dumps(metadata)
    metadata["authorization"]["status"] = "approved"
    assert budget.metadata()["authorization"]["status"] == "draft"
    with pytest.raises(ValueError, match="frozen"):
        budget.policy.model = "changed"
    settings = budget.settings
    settings.max_output_tokens = 100
    assert budget.settings.max_output_tokens == 10


@pytest.mark.parametrize(
    "mode,endpoint,model",
    [
        ("fixture", "https://fixture.invalid/v1/", MODEL),
        ("fixture", "https://api.openai.com/v1", MODEL),
        ("openai-standard", "https://api.openai.com/v1?extra=1", STANDARD_MODEL),
        ("openai-standard", "https://api.openai.com/v1", "gpt-5.4-mini"),
        ("openai-standard", "http://api.openai.com/v1", STANDARD_MODEL),
    ],
)
def test_policy_rejects_unreviewed_endpoint_or_alias(mode, endpoint, model):
    with pytest.raises(ValueError):
        DispatchPolicy(mode=mode, upstream_base_url=endpoint, model=model)


@pytest.mark.parametrize(
    "rate_updates",
    [
        {"max_input_tokens_per_request": 399_999},
        {"max_input_tokens_per_request": 400_001},
        {"input_usd_per_million": "0.749999999"},
        {"output_usd_per_million": "4.499999999"},
        {"model": "gpt-5.4-mini"},
        {"snapshot": "other-snapshot"},
    ],
)
def test_standard_rate_contract_rejects_under_reservation(tmp_path, rate_updates):
    with pytest.raises(ValueError):
        make_budget(tmp_path, standard=True, rates=rate_card(standard=True, **rate_updates))


def test_standard_draft_metadata_allows_conservative_reviewed_rates(tmp_path):
    budget = make_budget(
        tmp_path,
        standard=True,
        rates=rate_card(standard=True, input_usd_per_million="1", output_usd_per_million="5"),
    )
    assert budget.metadata()["input_bound"]["method"] == "provider_context_limit"
    assert budget.metadata()["input_bound"]["max_input_tokens"] == 400_000
    assert budget.metadata()["authorization"] == {"status": "draft", "reference": None}
    with pytest.raises(ValueError, match="external approval"):
        budget.reserve("request-0001", digest("one"))
    assert budget.ledger.snapshot()["reservations"] == {}


def test_explicit_tier_and_exact_fixture_snapshot_are_required(tmp_path):
    with pytest.raises(ValueError, match="explicit default"):
        make_budget(tmp_path / "tier", settings=DiscoverySettings())
    with pytest.raises(ValueError, match="match exactly"):
        make_budget(tmp_path / "alias", rates=rate_card(snapshot="different"))


def test_authorization_changes_require_new_binding_and_revocation_blocks_dispatch(tmp_path):
    draft = make_budget(tmp_path, standard=True)
    draft.ledger.record_authorization(APPROVAL)
    with pytest.raises(ValueError, match="changed"):
        draft.reserve("request-0001", digest("one"))
    approved = DispatchBudget(draft.ledger, policy=draft.policy, binding=BINDING, settings=SETTINGS)
    row = approved.reserve("request-0001", digest("one"))
    assert row["status"] == "reserved"
    approved.ledger.record_authorization(AuthorizationRecord(reference="revoked-fixture"))
    with pytest.raises(ValueError, match="external approval"):
        approved.mark_dispatched(row["operation_id"])
    assert approved.ledger.snapshot()["reservations"][row["operation_id"]]["dispatched_at"] is None
    historical = DispatchBudget(
        approved.ledger,
        policy=approved.policy,
        binding=BINDING,
        settings=SETTINGS,
        authorization=APPROVAL,
    )
    assert historical.metadata() == approved.metadata()
    with pytest.raises(ValueError, match="external approval"):
        historical.reserve("request-0002", digest("two"))
    with pytest.raises(ValueError, match="ledger history"):
        DispatchBudget.configuration_metadata(
            approved.ledger,
            policy=approved.policy,
            settings=SETTINGS,
            authorization=AuthorizationRecord(status="approved", reference="never-recorded"),
        )


def test_reservation_retries_are_stable_and_dispatch_is_one_shot(tmp_path):
    budget = make_budget(tmp_path)
    row = budget.reserve("request-0001", digest("one"))
    assert row["operation_id"] == BINDING.execution_id + "/request-0001"
    assert row["reserved_nanodollars"] == 120_000
    assert budget.reserve("request-0001", digest("one")) == row
    with pytest.raises(ValueError, match="different arguments"):
        budget.reserve("request-0001", digest("changed"))
    dispatched = budget.mark_dispatched(row["operation_id"])
    assert budget.reserve("request-0001", digest("one")) == dispatched
    with pytest.raises(ValueError):
        budget.mark_dispatched(row["operation_id"])
    with pytest.raises(ValueError, match="different execution"):
        budget.mark_dispatched("c" * 32 + "/request-0001")
    with pytest.raises(ValueError, match="stable gateway"):
        budget.reserve("../request-0001", digest("bad"))
    mismatched = DispatchBudget(
        budget.ledger,
        policy=budget.policy,
        binding=BINDING,
        settings=DiscoverySettings(max_output_tokens=11, service_tier="default"),
    )
    with pytest.raises(ValueError):
        mismatched.reserve("request-0001", digest("one"))
    with pytest.raises(ValueError, match="settings"):
        mismatched.mark_dispatched(row["operation_id"])


def test_concurrent_executions_share_the_durable_ceiling(tmp_path):
    first = make_budget(tmp_path, ceiling="0.000120")
    second = make_budget(
        tmp_path, ceiling="0.000120", binding=BINDING.model_copy(update={"execution_id": "c" * 32})
    )
    barrier = Barrier(2)

    def reserve(budget):
        barrier.wait()
        try:
            return budget.reserve("request-0001", digest("same-body"))["status"]
        except BudgetExceeded:
            return "exhausted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, [first, second])) == ["exhausted", "reserved"]
    reopened = make_budget(tmp_path, ceiling="0.000120")
    assert reopened.ledger.snapshot()["accounting"]["committed_nanodollars"] == 120_000


def test_actual_sdk_http_history_preserves_text_functions_and_encrypted_reasoning(tmp_path):
    budget = make_budget(tmp_path)
    requests = []
    output = [
        {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [{"type": "summary_text", "text": "Plan"}],
            "encrypted_content": "recorded-provider-ciphertext",
            "content": [{"type": "reasoning_text", "text": "Available"}],
        },
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "search_sources",
            "arguments": "{}",
        },
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "Searching", "annotations": []}],
        },
    ]

    def upstream(request):
        body = json.loads(request.content)
        budget.validate_request(body)
        requests.append(body)
        return httpx.Response(200, json=provider_response(output=output))

    with httpx.Client(transport=httpx.MockTransport(upstream)) as http_client:
        with OpenAI(
            api_key="fixture-only",
            base_url="https://fixture.invalid/v1",
            http_client=http_client,
            max_retries=0,
        ) as client:
            first = client.responses.create(**payload())
            history = [{"role": "user", "content": "Find the source"}]
            history.extend(item.model_dump(mode="json") for item in first.output)
            history.append(
                {"type": "function_call_output", "call_id": "call_1", "output": "Recorded receipt"}
            )
            client.responses.create(
                **payload(
                    input=history,
                    include=["reasoning.encrypted_content"],
                    tools=[
                        {
                            "type": "function",
                            "name": "search_sources",
                            "parameters": {"type": "object"},
                        }
                    ],
                )
            )
    assert len(requests) == 2
    assert requests[1]["input"][1]["encrypted_content"] == "recorded-provider-ciphertext"
    assert "previous_response_id" not in requests[1]


@pytest.mark.parametrize(
    "update",
    [
        {"model": "alias"},
        {"max_output_tokens": 11},
        {"max_output_tokens": True},
        {"reasoning": {"effort": "high"}},
        {"service_tier": "priority"},
        {"previous_response_id": "resp-remote"},
        {"conversation": "conversation-remote"},
        {"background": True},
        {"parallel_tool_calls": True},
        {"max_tool_calls": 1},
        {"input": [{"type": "item_reference", "id": "remote"}]},
        {
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": "https://remote.invalid/image"}
                    ],
                }
            ]
        },
        {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "remote"}]}]},
        {"input": [{"type": "web_search_call", "id": "native"}]},
        {"tools": [{"type": "web_search"}]},
        {"tools": [{"type": "mcp", "server_url": "https://remote.invalid"}]},
        {
            "tools": [
                {
                    "type": "function",
                    "name": "test",
                    "parameters": {"$ref": "https://remote.invalid/schema"},
                }
            ]
        },
        {"input": [{"role": [], "content": "malformed"}]},
        {"input": [{"role": "assistant", "content": "text", "status": {}}]},
        {"input": [{"type": "reasoning", "id": "rs", "summary": [{"type": [], "text": "bad"}]}]},
        {"tool_choice": []},
        {"tool_choice": {"type": "function", "name": []}},
        {"text": {"format": {"type": []}}},
        {"include": [{}]},
        {"metadata": {"bad": float("nan")}},
    ],
)
def test_request_validation_rejects_unpriced_paths_and_malformed_json(tmp_path, update):
    budget = make_budget(tmp_path)
    with pytest.raises(ValueError):
        budget.validate_request(payload(**update))
    assert budget.ledger.snapshot()["reservations"] == {}


@pytest.mark.parametrize("status", ["completed", "failed", "incomplete"])
def test_raw_verified_usage_settles_once_even_for_failed_model_responses(tmp_path, status):
    budget = make_budget(tmp_path)
    archive, _, sent = make_archive(
        tmp_path / "gateway", budget, response=provider_response(status=status)
    )
    assert len(sent) == 1
    report = budget.reconcile_archive(archive)
    assert report["archive_valid"], report["errors"]
    assert set(report["operations"].values()) == {"settled"}
    assert report["accounting"]["settled_nanodollars"] == 9_000
    assert budget.reconcile_archive(archive) == report
    row = next(iter(budget.ledger.snapshot()["reservations"].values()))
    assert row["settlement"]["verified"] and row["settlement"]["completed"]
    assert row["settlement"]["evidence_sha256"] != digest(
        json.dumps(provider_response(status=status))
    )


@pytest.mark.parametrize("missing", ["usage", "service_tier"])
def test_missing_raw_usage_or_tier_holds_full_reservation(tmp_path, missing):
    budget = make_budget(tmp_path)
    response = provider_response()
    del response[missing]
    archive, _, _ = make_archive(tmp_path / "gateway", budget, response=response)
    result = budget.reconcile_archive(archive)
    assert result["archive_valid"], result["errors"]
    assert set(result["operations"].values()) == {"held"}
    assert result["accounting"]["committed_nanodollars"] == 120_000
    reopened = make_budget(tmp_path)
    assert reopened.reconcile_archive(archive) == result


def test_interrupted_dispatch_retains_full_amount(tmp_path):
    budget = make_budget(tmp_path)
    archive, result, sent = make_archive(
        tmp_path / "gateway", budget, failure=httpx.ReadError("fixture interruption")
    )
    assert result.status_code >= 400 and len(sent) == 1
    report = budget.reconcile_archive(archive)
    assert report["archive_valid"], report["errors"]
    assert report["accounting"]["held_nanodollars"] == 120_000


def test_verified_failure_before_dispatch_can_release_without_replay(tmp_path):
    budget = make_budget(tmp_path)

    def fail_before_mark(*args, **kwargs):
        raise ValueError("fixture build failure before every dispatch path")

    archive, _, sent = make_archive(tmp_path / "gateway", budget, before_build=fail_before_mark)
    assert not sent
    report = budget.reconcile_archive(archive)
    assert report["archive_valid"], report["errors"]
    assert set(report["operations"].values()) == {"released"}
    assert report["accounting"]["committed_nanodollars"] == 0
    assert budget.reconcile_archive(archive) == report
    row = next(iter(budget.ledger.snapshot()["reservations"].values()))
    assert row["cancellation"]["all_dispatch_paths_verified"] is True
    assert budget.reserve("request-0001", row["request_sha256"])["status"] == "released"
    with pytest.raises(ValueError):
        budget.mark_dispatched(row["operation_id"])


def test_pending_dispatch_marker_holds_even_when_no_http_dispatch_was_observed(
    tmp_path, monkeypatch
):
    budget = make_budget(tmp_path)
    original = budget.mark_dispatched

    def mark_then_fail(operation_id):
        original(operation_id)
        raise OSError("fixture lost acknowledgment after durable dispatch mark")

    monkeypatch.setattr(budget, "mark_dispatched", mark_then_fail)
    archive, _, sent = make_archive(tmp_path / "gateway", budget)
    assert not sent
    report = budget.reconcile_archive(archive)
    assert report["archive_valid"], report["errors"]
    assert report["accounting"]["held_nanodollars"] == 120_000


def test_unsealed_or_tampered_archive_never_releases_or_settles(tmp_path):
    budget = make_budget(tmp_path)
    archive, _, _ = make_archive(tmp_path / "gateway", budget)
    (archive.parent / "request-0001/response.body").write_bytes(b"{}")
    result = budget.reconcile_archive(archive)
    assert not result["archive_valid"] and result["errors"]
    assert result["accounting"]["held_nanodollars"] == 120_000
    archive.unlink()
    result = budget.reconcile_archive(archive)
    assert not result["archive_valid"]
    assert result["accounting"]["held_nanodollars"] == 120_000


def test_late_reservations_absent_from_archive_are_held_and_other_executions_are_scoped(tmp_path):
    budget = make_budget(tmp_path)
    archive, _, _ = make_archive(tmp_path / "gateway", budget)
    late = budget.reserve("request-0002", digest("reservation returned after seal"))
    other = DispatchBudget(
        budget.ledger,
        policy=budget.policy,
        binding=BINDING.model_copy(update={"execution_id": "c" * 32}),
        settings=SETTINGS,
    )
    foreign = other.reserve("request-0001", digest("other execution"))
    result = budget.reconcile_archive(archive)
    assert result["operations"][late["operation_id"]] == "held"
    assert foreign["operation_id"] not in result["operations"]
    assert budget.ledger.snapshot()["reservations"][foreign["operation_id"]]["status"] == "reserved"
    assert result["accounting"]["committed_nanodollars"] == 249_000


def test_request_hash_mismatch_cannot_settle_a_different_ledger_reservation(tmp_path):
    original = make_budget(tmp_path / "original")
    archive, _, _ = make_archive(tmp_path / "gateway", original)
    independent = make_budget(tmp_path / "different-ledger")
    independent.reserve("request-0001", digest("different request bytes"))
    result = independent.reconcile_archive(archive)
    assert result["archive_valid"]
    assert result["accounting"]["held_nanodollars"] == 120_000


def test_historical_authorization_can_reconcile_after_revocation(tmp_path):
    budget = make_budget(tmp_path, authorization=APPROVAL)
    archive, _, _ = make_archive(tmp_path / "gateway", budget)
    budget.ledger.record_authorization(AuthorizationRecord(reference="revoked"))
    reopened = BudgetLedger(budget.ledger.path, rates=budget.ledger.rates, ceiling_usd=Decimal("1"))
    recovery = DispatchBudget(
        reopened,
        policy=budget.policy,
        binding=budget.binding,
        settings=budget.settings,
        authorization=APPROVAL,
    )
    assert recovery.metadata() == budget.metadata()
    report = recovery.reconcile_archive(archive)
    assert report["archive_valid"]
    assert report["accounting"]["settled_nanodollars"] == 9_000


def test_archive_consistency_is_read_only_and_honors_explicit_snapshot(tmp_path):
    budget = make_budget(tmp_path)
    initial = budget.ledger.snapshot()
    archive, _, _ = make_archive(tmp_path / "gateway", budget)
    before = budget.ledger.path.read_bytes()
    report = budget.archive_consistency(archive)
    assert report == {
        "status": "consistent",
        "errors": [],
        "required_operation_ids": [BINDING.execution_id + "/request-0001"],
    }
    assert budget.ledger.path.read_bytes() == before
    stale = budget.archive_consistency(archive, snapshot=initial)
    assert stale["status"] == "inconsistent" and "missing" in stale["errors"][0]
    assert budget.ledger.path.read_bytes() == before
    budget.reconcile_archive(archive)
    settled = budget.ledger.path.read_bytes()
    assert budget.archive_consistency(archive) == report
    assert budget.ledger.path.read_bytes() == settled


@pytest.mark.parametrize("before_dispatch", [False, True])
def test_empty_rollback_ledger_cannot_reconcile_known_archived_reservations(
    tmp_path, before_dispatch
):
    budget = make_budget(tmp_path)
    initial = budget.ledger.path.read_bytes()

    def fail_before_mark(*args, **kwargs):
        raise ValueError("fixture before dispatch")

    archive, _, _ = make_archive(
        tmp_path / "gateway",
        budget,
        before_build=fail_before_mark if before_dispatch else None,
    )
    budget.ledger.path.write_bytes(initial)
    report = budget.reconcile_archive(archive)
    assert report["archive_valid"] and report["status"] == "inconsistent"
    assert report["required_operation_ids"] == [BINDING.execution_id + "/request-0001"]
    assert any("missing from ledger" in error for error in report["errors"])
    assert report["operations"] == {}
    assert budget.ledger.path.read_bytes() == initial


def test_missing_operation_blocks_settling_other_known_operations(tmp_path):
    budget = make_budget(tmp_path)
    archive, _, _ = make_archive(tmp_path / "gateway", budget, request_count=2)
    journal = json.loads(budget.ledger.path.read_bytes())
    missing = BINDING.execution_id + "/request-0002"
    del journal["reservations"][missing]
    budget.ledger.path.write_text(json.dumps(journal))
    report = budget.reconcile_archive(archive)
    assert report["status"] == "inconsistent"
    assert missing in report["required_operation_ids"]
    assert set(report["operations"].values()) == {"held"}
    assert report["accounting"]["held_nanodollars"] == 120_000
    assert report["accounting"]["settled_nanodollars"] == 0


@pytest.mark.parametrize("late_row", [False, True])
def test_unresolved_reservation_ack_does_not_require_a_ledger_row(tmp_path, monkeypatch, late_row):
    budget = make_budget(tmp_path)
    original = budget.reserve

    def uncertain_reservation(request_id, request_sha256):
        if late_row:
            original(request_id, request_sha256)
        raise OSError("fixture acknowledgment unavailable")

    monkeypatch.setattr(budget, "reserve", uncertain_reservation)
    archive, _, sent = make_archive(tmp_path / "gateway", budget)
    assert not sent
    report = budget.archive_consistency(archive)
    assert report == {"status": "consistent", "errors": [], "required_operation_ids": []}
    reconciled = budget.reconcile_archive(archive)
    assert reconciled["status"] == "reconciled"
    assert reconciled["accounting"]["held_nanodollars"] == (120_000 if late_row else 0)


def test_archived_dispatch_marker_cannot_disappear_from_ledger(tmp_path):
    budget = make_budget(tmp_path)
    archive, _, _ = make_archive(tmp_path / "gateway", budget)
    journal = json.loads(budget.ledger.path.read_bytes())
    row = next(iter(journal["reservations"].values()))
    row.update(status="reserved", dispatched_at=None)
    budget.ledger.path.write_text(json.dumps(journal))
    report = budget.reconcile_archive(archive)
    assert report["status"] == "inconsistent"
    assert any("dispatch marker" in error for error in report["errors"])
    assert report["accounting"]["held_nanodollars"] == 120_000


@pytest.mark.parametrize("wrong_tokens", [False, True])
def test_terminal_settlement_must_match_independent_usage_and_evidence(tmp_path, wrong_tokens):
    budget = make_budget(tmp_path)
    archive, _, _ = make_archive(tmp_path / "gateway", budget)
    operation_id = BINDING.execution_id + "/request-0001"
    budget.ledger.settle(
        operation_id,
        input_tokens=1 if wrong_tokens else 5,
        output_tokens=2,
        evidence_sha256=digest("different evidence"),
        verified=True,
        completed=True,
    )
    before = budget.ledger.path.read_bytes()
    report = budget.archive_consistency(archive)
    assert report["status"] == "inconsistent"
    assert any("settlement differs" in error for error in report["errors"])
    assert budget.reconcile_archive(archive)["status"] == "inconsistent"
    assert budget.ledger.path.read_bytes() == before


def test_terminal_release_requires_matching_independent_no_dispatch_proof(tmp_path):
    budget = make_budget(tmp_path)

    def fail_before_mark(*args, **kwargs):
        raise ValueError("fixture before dispatch")

    archive, _, _ = make_archive(tmp_path / "gateway", budget, before_build=fail_before_mark)
    row = next(iter(budget.ledger.snapshot()["reservations"].values()))
    budget.ledger.cancel_before_dispatch(
        row["operation_id"],
        proof=CancellationProof(
            request_sha256=row["request_sha256"],
            evidence_sha256=digest("different proof"),
            no_dispatch=True,
            all_dispatch_paths_verified=True,
            reason="Different host proof",
        ),
    )
    before = budget.ledger.path.read_bytes()
    report = budget.reconcile_archive(archive)
    assert report["status"] == "inconsistent"
    assert any("no-dispatch proof" in error for error in report["errors"])
    assert budget.ledger.path.read_bytes() == before


def test_terminal_operation_without_archived_reservation_is_inconsistent(tmp_path):
    budget = make_budget(tmp_path)
    archive, _, _ = make_archive(tmp_path / "gateway", budget)
    extra = budget.reserve("request-0002", digest("unarchived request"))
    budget.ledger.settle(
        extra["operation_id"],
        input_tokens=1,
        output_tokens=1,
        evidence_sha256=digest("unbound evidence"),
        verified=True,
        completed=True,
    )
    report = budget.reconcile_archive(archive)
    assert report["status"] == "inconsistent"
    assert any("no verified archived reservation" in error for error in report["errors"])
    assert report["operations"][extra["operation_id"]] == "settled"
    assert report["accounting"]["held_nanodollars"] == 120_000


def test_missing_or_unverifiable_archive_is_explicitly_unverified(tmp_path):
    budget = make_budget(tmp_path)
    before = budget.ledger.path.read_bytes()
    report = budget.archive_consistency(tmp_path / "missing/archive.json")
    assert report["status"] == "unverified" and report["errors"]
    assert report["required_operation_ids"] == []
    assert budget.ledger.path.read_bytes() == before
