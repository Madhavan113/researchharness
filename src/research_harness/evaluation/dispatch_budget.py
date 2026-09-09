"""Conservative dispatch controls and sealed-evidence budget reconciliation.

This adapter performs no network work and does not establish user permission.
Production callers must separately verify authorization and current applicable
prices. The standard policy reserves the pinned model's full declared context
window; it does not infer token counts from characters or encrypted reasoning.
"""

from __future__ import annotations

import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from research_harness.config import StrictModel
from research_harness.evaluation.budget import AuthorizationRecord, BudgetLedger, CancellationProof
from research_harness.execution import DiscoverySettings, GatewayBinding
from research_harness.util import canonical_json

STANDARD_MODEL = "gpt-5.4-mini-2026-03-17"
STANDARD_CONTEXT = 400_000
STANDARD_MAX_OUTPUT = 128_000
# Official standard-price minimums reviewed September 8, 2026. These do not
# prove that prices, regional charges or account terms cannot change later.
STANDARD_INPUT_FLOOR = Decimal("0.75")
STANDARD_OUTPUT_FLOOR = Decimal("4.50")
REQUEST_ID = re.compile(r"^request-[0-9]{4,150}$")
REQUEST_FIELDS = {
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
    "store",
    "metadata",
    "background",
    "service_tier",
}


class DispatchPolicy(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["fixture", "openai-standard"]
    upstream_base_url: str
    model: str = Field(min_length=1, max_length=200)
    service_tier: Literal["default"] = "default"

    @model_validator(mode="after")
    def pinned_endpoint(self):
        expected = (
            "https://fixture.invalid/v1" if self.mode == "fixture" else "https://api.openai.com/v1"
        )
        if self.upstream_base_url != expected:
            raise ValueError(f"{self.mode} requires the exact upstream endpoint {expected}")
        if self.mode == "openai-standard" and self.model != STANDARD_MODEL:
            raise ValueError("Standard dispatch requires the explicitly reviewed model snapshot")
        return self


def _object(value: Any, allowed: set[str], required: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or set(value) - allowed or not (required or set()) <= set(value):
        raise ValueError("Unsupported or incomplete text/function request structure")
    return value


def _string(value: Any, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")


def _schema(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                not isinstance(item, str) or not item.startswith("#")
            ):
                raise ValueError("Remote schema references are not supported")
            _schema(item)
    elif isinstance(value, list):
        for item in value:
            _schema(item)


def _text_part(part: Any, *, reasoning: bool = False) -> None:
    if not isinstance(part, dict):
        raise ValueError("Message content must contain explicit text parts")
    kind = part.get("type")
    if reasoning:
        _object(part, {"type", "text"}, {"type", "text"})
        if kind not in ("summary_text", "reasoning_text"):
            raise ValueError("Reasoning content must be recorded text")
        _string(part["text"], "Reasoning text")
    elif kind == "input_text":
        _object(part, {"type", "text"}, {"type", "text"})
        _string(part["text"], "Input text")
    elif kind == "output_text":
        _object(part, {"type", "text", "annotations", "logprobs"}, {"type", "text"})
        _string(part["text"], "Output text")
        for name in ("annotations", "logprobs"):
            if name == "logprobs" and part.get(name) is None:
                continue
            if name in part and not isinstance(part[name], list):
                raise ValueError(f"Output {name} must be an explicit list")
    elif kind == "refusal":
        _object(part, {"type", "refusal"}, {"type", "refusal"})
        _string(part["refusal"], "Refusal")
    else:
        raise ValueError("Media inputs and remote content references are not budgeted")


def _content(value: Any) -> None:
    if isinstance(value, str):
        return
    if not isinstance(value, list):
        raise ValueError("Message or tool content must be text or explicit text parts")
    for part in value:
        _text_part(part)


def _input_item(item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError("Request input must contain explicit recorded items")
    kind = item.get("type", "message" if "role" in item else None)
    if kind == "message":
        _object(item, {"type", "role", "content", "id", "status", "phase"}, {"role", "content"})
        if item["role"] not in ("user", "assistant", "system", "developer"):
            raise ValueError("Unsupported message role")
        _content(item["content"])
        if item.get("phase") not in (None, "commentary", "final"):
            raise ValueError("Unsupported assistant message phase")
    elif kind == "function_call":
        _object(
            item,
            {"type", "id", "call_id", "name", "arguments", "status", "namespace", "caller"},
            {"type", "call_id", "name", "arguments"},
        )
        for name in ("call_id", "name", "arguments"):
            _string(item[name], name)
        if item.get("caller") is not None:
            raise ValueError("Only ordinary recorded function calls are supported")
        if item.get("namespace") is not None:
            _string(item["namespace"], "Function namespace")
    elif kind == "function_call_output":
        _object(item, {"type", "id", "call_id", "output", "status"}, {"type", "call_id", "output"})
        _string(item["call_id"], "Function call id")
        _content(item["output"])
    elif kind == "reasoning":
        _object(
            item,
            {"type", "id", "summary", "content", "encrypted_content", "status"},
            {"type", "id", "summary"},
        )
        _string(item["id"], "Reasoning id")
        if not isinstance(item["summary"], list):
            raise ValueError("Reasoning summary must be an explicit list")
        for part in item["summary"]:
            _text_part(part, reasoning=True)
        if item.get("content") is not None:
            if not isinstance(item["content"], list):
                raise ValueError("Reasoning content must be an explicit list")
            for part in item["content"]:
                _text_part(part, reasoning=True)
        if item.get("encrypted_content") is not None:
            _string(item["encrypted_content"], "Recorded encrypted reasoning")
    else:
        raise ValueError("Remote context references, media and native tool items are not budgeted")
    if item.get("id") is not None:
        _string(item["id"], "Recorded item id")
    if item.get("status") not in (None, "in_progress", "completed", "incomplete"):
        raise ValueError("Unsupported recorded item status")


class DispatchBudget:
    @staticmethod
    def configuration_metadata(
        ledger: BudgetLedger,
        *,
        policy: DispatchPolicy,
        settings: DiscoverySettings,
        authorization: AuthorizationRecord | None = None,
    ) -> dict[str, Any]:
        """Describe controls, optionally capturing a recorded historical authorization.

        Historical records support read-only recovery after revocation. They do
        not override the current-authorization check before new dispatch.
        """
        policy = DispatchPolicy.model_validate(policy.model_dump(mode="json"))
        settings = DiscoverySettings.model_validate(settings.model_dump(mode="json"))
        snapshot = ledger.snapshot()
        captured_authorization = snapshot["authorization"]
        if authorization is not None:
            captured_authorization = AuthorizationRecord.model_validate(authorization).model_dump(
                mode="json"
            )
            if not any(
                event["record"] == captured_authorization
                for event in snapshot["authorization_history"]
            ):
                raise ValueError("Captured authorization must exist in the ledger history")
        rates = ledger.rates
        if settings.service_tier != "default" or policy.service_tier != "default":
            raise ValueError("Budgeted dispatch requires an explicit default service tier")
        if policy.model != rates.model or rates.model != rates.snapshot:
            raise ValueError("Policy model, rate model and rate snapshot must match exactly")
        if policy.mode == "openai-standard":
            if rates.max_input_tokens_per_request != STANDARD_CONTEXT:
                raise ValueError("Standard dispatch must reserve the full 400000-token input bound")
            if settings.max_output_tokens > STANDARD_MAX_OUTPUT:
                raise ValueError("Standard output token limit exceeds the reviewed model bound")
            if (
                rates.input_usd_per_million < STANDARD_INPUT_FLOOR
                or rates.output_usd_per_million < STANDARD_OUTPUT_FLOOR
            ):
                raise ValueError("Rate card is below the September 8, 2026 standard-price minimums")
        return {
            "schema_version": 1,
            "policy": policy.model_dump(mode="json"),
            "rates": rates.model_dump(mode="json"),
            "rates_sha256": rates.fingerprint(),
            "ceiling_nanodollars": snapshot["ceiling_nanodollars"],
            "authorization": captured_authorization,
            "authorization_is_dispatch_permission": False,
            "input_bound": {
                "method": "provider_context_limit"
                if policy.mode == "openai-standard"
                else "fixture_host_bound",
                "max_input_tokens": rates.max_input_tokens_per_request,
                "locally_tokenized": False,
            },
        }

    def __init__(
        self,
        ledger: BudgetLedger,
        *,
        policy: DispatchPolicy,
        binding: GatewayBinding,
        settings: DiscoverySettings,
        authorization: AuthorizationRecord | None = None,
    ):
        self.ledger = ledger
        self._policy_json = canonical_json(
            DispatchPolicy.model_validate(policy).model_dump(mode="json")
        )
        self._binding_json = canonical_json(
            GatewayBinding.model_validate(binding).model_dump(mode="json")
        )
        self._settings_json = canonical_json(
            DiscoverySettings.model_validate(settings).model_dump(mode="json")
        )
        self._metadata = self.configuration_metadata(
            ledger, policy=self.policy, settings=self.settings, authorization=authorization
        )

    @property
    def policy(self) -> DispatchPolicy:
        return DispatchPolicy.model_validate_json(self._policy_json)

    @property
    def binding(self) -> GatewayBinding:
        return GatewayBinding.model_validate_json(self._binding_json)

    @property
    def settings(self) -> DiscoverySettings:
        return DiscoverySettings.model_validate_json(self._settings_json)

    def metadata(self) -> dict[str, Any]:
        return deepcopy(self._metadata)

    def _configuration_matches(self, state: dict) -> None:
        if (
            state["rates_sha256"] != self._metadata["rates_sha256"]
            or state["ceiling_nanodollars"] != self._metadata["ceiling_nanodollars"]
        ):
            raise ValueError("Budget configuration changed after binding this dispatch adapter")

    def _authorization(self, state: dict) -> None:
        self._configuration_matches(state)
        if self.policy.mode == "openai-standard":
            authorization = state["authorization"]
            if (
                authorization["status"] != "approved"
                or not (authorization.get("reference") or "").strip()
            ):
                raise ValueError("Standard dispatch requires caller-reported external approval")
            if authorization != self._metadata["authorization"]:
                raise ValueError("Authorization metadata changed; construct a newly bound adapter")

    def validate_request(self, payload: dict[str, Any]) -> None:
        _object(
            payload, REQUEST_FIELDS, {"model", "max_output_tokens", "reasoning", "service_tier"}
        )
        try:
            canonical_json(payload)  # Reject non-JSON and non-finite controls.
        except (TypeError, ValueError) as exc:
            raise ValueError("Request must be finite JSON data") from exc
        settings = self.settings
        if payload["model"] != self.policy.model:
            raise ValueError("Request model differs from the pinned rate snapshot")
        for key, expected in settings.model_settings().items():
            if canonical_json(payload.get(key)) != canonical_json(expected):
                raise ValueError(f"Request {key} differs from the budgeted settings")
        if payload.get("background", False) is not False:
            raise ValueError("Background provider work is not budgeted")
        for key in ("store", "stream"):
            if key in payload and type(payload[key]) is not bool:
                raise ValueError(f"Request {key} must be boolean")
        if "instructions" in payload:
            _string(payload["instructions"], "Instructions")
        inputs = payload.get("input", [])
        if isinstance(inputs, str):
            pass
        elif isinstance(inputs, list):
            for item in inputs:
                _input_item(item)
        else:
            raise ValueError("Request input must be explicit text or recorded items")
        tools = payload.get("tools", [])
        if not isinstance(tools, list):
            raise ValueError("Tools must be an explicit function list")
        names = set()
        for tool in tools:
            _object(
                tool,
                {"type", "name", "description", "parameters", "strict"},
                {"type", "name", "parameters"},
            )
            if tool["type"] != "function" or not isinstance(tool["parameters"], dict):
                raise ValueError("Only ordinary function tools are budgeted")
            _string(tool["name"], "Function name")
            if not tool["name"] or tool["name"] in names:
                raise ValueError("Function names must be nonempty and unique")
            names.add(tool["name"])
            if "description" in tool:
                _string(tool["description"], "Function description")
            if "strict" in tool and type(tool["strict"]) is not bool:
                raise ValueError("Function strict flag must be boolean")
            _schema(tool["parameters"])
        choice = payload.get("tool_choice", "auto")
        if isinstance(choice, dict):
            _object(choice, {"type", "name"}, {"type", "name"})
            if (
                choice["type"] != "function"
                or not isinstance(choice["name"], str)
                or choice["name"] not in names
            ):
                raise ValueError("Tool choice must select an advertised function")
        elif choice not in ("auto", "none", "required"):
            raise ValueError("Unsupported tool choice")
        if "text" in payload:
            text = _object(payload["text"], {"format"})
            if "format" in text:
                format_ = _object(
                    text["format"], {"type", "name", "schema", "description", "strict"}, {"type"}
                )
                if format_["type"] not in ("text", "json_object", "json_schema"):
                    raise ValueError("Unsupported text response format")
                _schema(format_)
        include = payload.get("include", [])
        if not isinstance(include, list) or any(
            item not in ("reasoning.encrypted_content", "message.output_text.logprobs")
            for item in include
        ):
            raise ValueError("Unsupported provider expansion")

    def _operation_id(self, request_id: str) -> str:
        if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
            raise ValueError("Expected a stable gateway request-NNNN id")
        return f"{self.binding.execution_id}/{request_id}"

    def reserve(self, request_id: str, request_sha256: str) -> dict[str, Any]:
        operation_id = self._operation_id(request_id)
        with self.ledger.lock:
            self._authorization(self.ledger.snapshot())
            return self.ledger.reserve(
                operation_id,
                request_sha256=request_sha256,
                max_output_tokens=self.settings.max_output_tokens,
            )

    def mark_dispatched(self, operation_id: str) -> dict[str, Any]:
        prefix = self.binding.execution_id + "/"
        if (
            not isinstance(operation_id, str)
            or not operation_id.startswith(prefix)
            or self._operation_id(operation_id[len(prefix) :]) != operation_id
        ):
            raise ValueError("Budget operation belongs to a different execution")
        with self.ledger.lock:
            snapshot = self.ledger.snapshot()
            self._authorization(snapshot)
            row = snapshot["reservations"].get(operation_id)
            if row is None or row["max_output_tokens"] != self.settings.max_output_tokens:
                raise ValueError("Budget reservation does not match the dispatch settings")
            return self.ledger.mark_dispatched(operation_id)

    def _verify_archive(self, archive_path: Path) -> dict[str, Any]:
        from research_harness.evaluation.gateway_usage import verify_gateway_usage

        return verify_gateway_usage(
            archive_path,
            self.binding,
            expected_model=self.policy.model,
            expected_model_settings=self.settings.model_settings(),
            expected_budgets=self.settings.budgets(),
            expected_budget_control=self.metadata(),
        )

    def _archive_consistency(self, verified: dict, snapshot: dict) -> dict[str, Any]:
        errors = list(verified["errors"])
        required = []
        if (
            verified["status"] not in {"verified_complete", "verified_lower_bound"}
            or not verified["budget_control_verified"]
        ):
            return {
                "status": "unverified",
                "errors": errors or ["Archive budget evidence is not independently verified"],
                "required_operation_ids": required,
            }
        try:
            self._configuration_matches(snapshot)
            rows = snapshot["reservations"]
            if not isinstance(rows, dict):
                raise ValueError("Budget reservations must be a mapping")
        except (KeyError, TypeError, ValueError) as exc:
            return {
                "status": "inconsistent",
                "errors": [f"Ledger snapshot does not match archive controls: {exc}"],
                "required_operation_ids": required,
            }

        for request in verified["verified_requests"]:
            operation_id = request["budget_operation_id"]
            if operation_id is None:
                # A pending reservation may not have reached the ledger when the
                # archive sealed. Its absence is not evidence of a reset. Any
                # late row remains fully held during reconciliation below.
                if not (
                    request["dispatched"]
                    or request["budget_dispatch_pending"]
                    or request["budget_dispatch_marked"]
                ):
                    continue
                operation_id = self._operation_id(request["request_id"])
                errors.append(f"{operation_id}: archived dispatch lacks its budget reservation")
            required.append(operation_id)
            row = rows.get(operation_id)
            if not isinstance(row, dict):
                errors.append(
                    f"{operation_id}: required archived reservation is missing from ledger"
                )
                continue
            expected = {
                "operation_id": operation_id,
                "request_sha256": request["request_sha256"],
                "reserved_nanodollars": request["budget_reserved_nanodollars"],
                "max_output_tokens": self.settings.max_output_tokens,
            }
            mismatches = [name for name, value in expected.items() if row.get(name) != value]
            if mismatches:
                errors.append(
                    f"{operation_id}: ledger reservation differs in {', '.join(mismatches)}"
                )
                continue
            if (request["dispatched"] or request["budget_dispatch_marked"]) and row.get(
                "dispatched_at"
            ) is None:
                errors.append(f"{operation_id}: ledger lost its archived dispatch marker")

            if row.get("status") == "settled":
                usage = request.get("usage")
                if request["status"] != "complete" or not usage:
                    errors.append(
                        f"{operation_id}: ledger settlement lacks complete archived usage"
                    )
                    continue
                settlement = {
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                    "evidence_sha256": request["evidence_sha256"],
                    "verified": True,
                    "completed": True,
                }
                cost = self.ledger.rates.cost_nanodollars(
                    usage["input_tokens"], usage["output_tokens"]
                )
                if row.get("settlement") != settlement or row.get("charged_nanodollars") != cost:
                    errors.append(
                        f"{operation_id}: ledger settlement differs from verified usage or evidence"
                    )
            elif row.get("status") == "released":
                proof = row.get("cancellation") or {}
                if (
                    request["status"] != "not_dispatched"
                    or request["dispatched"]
                    or request["budget_dispatch_marked"]
                    or request["budget_dispatch_pending"]
                    or request["budget_reservation_pending"]
                    or row.get("dispatched_at") is not None
                    or proof.get("request_sha256") != request["request_sha256"]
                    or proof.get("evidence_sha256") != request["evidence_sha256"]
                    or proof.get("no_dispatch") is not True
                    or proof.get("all_dispatch_paths_verified") is not True
                    or row.get("charged_nanodollars") != 0
                ):
                    errors.append(
                        f"{operation_id}: ledger release lacks matching no-dispatch proof"
                    )

        prefix = self.binding.execution_id + "/"
        for operation_id, row in rows.items():
            if (
                operation_id.startswith(prefix)
                and operation_id not in required
                and row["status"] in {"settled", "released"}
            ):
                errors.append(
                    f"{operation_id}: terminal ledger operation has no verified archived reservation"
                )
        return {
            "status": "inconsistent" if errors else "consistent",
            "errors": errors,
            "required_operation_ids": sorted(required),
        }

    def archive_consistency(
        self, archive_path: Path, *, snapshot: dict | None = None
    ) -> dict[str, Any]:
        """Read-only cross-check of sealed evidence against a validated ledger snapshot.

        ``consistent`` means no recorded reservation or terminal proof conflicts
        with this snapshot. It does not mean all requests have known usage, nor
        does it establish that a snapshot remains current after this call.
        """
        verified = self._verify_archive(archive_path)
        return self._archive_consistency(
            verified, self.ledger.snapshot() if snapshot is None else snapshot
        )

    def reconcile_archive(self, archive_path: Path) -> dict[str, Any]:
        """Settle only independently verified sealed evidence; never replay work."""
        verified = self._verify_archive(archive_path)
        # The verifier exposes request-level proof only after the complete archive
        # passes its integrity and binding checks. Summary counters are never proof.
        archive_valid = verified["status"] in {"verified_complete", "verified_lower_bound"}
        requests = (
            verified["verified_requests"]
            if archive_valid and verified["budget_control_verified"] is True
            else []
        )
        evidence = {item["request_id"]: item for item in requests}
        outcomes = {}
        with self.ledger.lock:
            snapshot = self.ledger.snapshot()
            self._configuration_matches(snapshot)
            consistency = self._archive_consistency(verified, snapshot)
            if consistency["status"] != "consistent":
                # Missing or conflicting rows can indicate a rolled-back ledger.
                # Never free additional funds while that contradiction exists.
                evidence = {}
            prefix = self.binding.execution_id + "/"
            for operation_id, row in snapshot["reservations"].items():
                if not operation_id.startswith(prefix):
                    continue
                if row["status"] in {"settled", "released"}:
                    outcomes[operation_id] = row["status"]
                    continue
                request = evidence.get(operation_id[len(prefix) :])
                reason = "Sealed archive has no independently valid proof for this reservation"
                matches = bool(
                    request
                    and request.get("budget_operation_id") == operation_id
                    and request.get("request_sha256") == row["request_sha256"]
                    and request.get("budget_reserved_nanodollars") == row["reserved_nanodollars"]
                    and row["max_output_tokens"] == self.settings.max_output_tokens
                    and request.get("budget_reservation_pending") is False
                    and request.get("budget_dispatch_pending") is False
                    and not request.get("issues")
                )
                if matches and request["status"] == "complete" and request["dispatched"] is True:
                    usage = request.get("usage")
                    if (
                        usage
                        and usage.get("model") == self.policy.model
                        and request.get("response_service_tier") == "default"
                    ):
                        outcomes[operation_id] = self.ledger.settle(
                            operation_id,
                            input_tokens=usage["input_tokens"],
                            output_tokens=usage["output_tokens"],
                            evidence_sha256=request["evidence_sha256"],
                            verified=True,
                            completed=True,
                        )["status"]
                        continue
                    reason = "Verified request lacks a complete priced model/tier observation"
                elif (
                    matches
                    and request["status"] == "not_dispatched"
                    and request["dispatched"] is False
                    and request.get("budget_dispatch_marked") is False
                    and row["dispatched_at"] is None
                ):
                    outcomes[operation_id] = self.ledger.cancel_before_dispatch(
                        operation_id,
                        proof=CancellationProof(
                            request_sha256=row["request_sha256"],
                            evidence_sha256=request["evidence_sha256"],
                            no_dispatch=True,
                            all_dispatch_paths_verified=True,
                            reason="Independently verified sealed archive proves no dispatch",
                        ),
                    )["status"]
                    continue
                elif matches:
                    reason = "Provider outcome or budget dispatch state remains unknown"
                if row["status"] not in {"settled", "released"}:
                    outcomes[operation_id] = self.ledger.hold(operation_id, reason=reason)["status"]
                else:
                    outcomes[operation_id] = row["status"]
        return {
            "schema_version": 1,
            "status": "reconciled"
            if consistency["status"] == "consistent"
            else consistency["status"],
            "archive_valid": archive_valid,
            "errors": consistency["errors"],
            "required_operation_ids": consistency["required_operation_ids"],
            "operations": outcomes,
            "accounting": self.ledger.snapshot()["accounting"],
        }
