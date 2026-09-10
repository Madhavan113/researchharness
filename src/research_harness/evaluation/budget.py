"""Conservative rate-based planning and durable request reservations.

Rates, token bounds and authorization records are supplied by the caller. This
module neither verifies user approval nor grants permission to dispatch work.
Its bounds cover the supplied token rates, not invoices, taxes or other fees.
The future dispatcher must enforce the reserved input/output token bounds and
verify usage evidence independently before settling an operation.
"""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock
from pydantic import ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

from research_harness.config import StrictModel
from research_harness.execution import DiscoverySettings
from research_harness.util import canonical_json, digest, http_url, timestamp, write_json

NANODOLLARS = 1_000_000_000
TOKENS_PER_MILLION = 1_000_000
SHA256 = r"^[0-9a-f]{64}$"


def _money(value: Decimal | str | int) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise ValueError("Money must be an exact Decimal, decimal string, or integer; not float")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal money amount") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Money must be finite and nonnegative")
    # Canonicalize without Decimal.normalize(), which uses ambient precision.
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return Decimal(text) if amount else Decimal(0)


def _usd(nanodollars: int) -> str:
    whole, remainder = divmod(nanodollars, NANODOLLARS)
    return f"{whole}.{remainder:09d}"


def _ceiling_nanodollars(value: Decimal | str | int) -> int:
    exact = Fraction(_money(value)) * NANODOLLARS
    return exact.numerator // exact.denominator


def _tokens(value: int, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(f"{name} must be a {'positive' if positive else 'nonnegative'} integer")
    return value


class RateCard(StrictModel):
    """Immutable caller-supplied token prices for one explicit model snapshot.

    ``model`` is the controller's requested identifier, which may be an alias;
    ``snapshot`` is the exact version the supplied rates describe. A future
    dispatcher must pin or verify that resolution rather than assume an alias
    still refers to this snapshot. This planner does not resolve model aliases.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str = Field(min_length=1, max_length=200)
    snapshot: str = Field(min_length=1, max_length=200)
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    max_input_tokens_per_request: StrictInt = Field(ge=1)
    price_source_url: str
    price_as_of: date

    @field_validator("input_usd_per_million", "output_usd_per_million", mode="before")
    @classmethod
    def exact_rates(cls, value):
        return _money(value)

    @field_validator("price_source_url")
    @classmethod
    def source_url(cls, value):
        return http_url(value)

    @field_validator("model", "snapshot")
    @classmethod
    def nonempty_identity(cls, value):
        if not value.strip():
            raise ValueError("Model and snapshot must be nonempty")
        return value

    def fingerprint(self) -> str:
        return digest(canonical_json(self.model_dump(mode="json")))

    def cost_nanodollars(self, input_tokens: int, output_tokens: int) -> int:
        """Round the combined exact token cost up, independently of Decimal context."""
        _tokens(input_tokens, "input_tokens")
        _tokens(output_tokens, "output_tokens")
        exact = (
            Fraction(self.input_usd_per_million) * input_tokens
            + Fraction(self.output_usd_per_million) * output_tokens
        ) * Fraction(NANODOLLARS, TOKENS_PER_MILLION)
        return (exact.numerator + exact.denominator - 1) // exact.denominator


class AuthorizationRecord(StrictModel):
    """Caller-reported metadata, never independently verified spending permission."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["draft", "approved"] = "draft"
    reference: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def referenced_approval(self):
        if self.status == "approved" and not (self.reference or "").strip():
            raise ValueError("Caller-reported approval must reference its external authorization")
        return self


class CancellationProof(StrictModel):
    """Host evidence that every dispatch path was checked and none dispatched."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    request_sha256: str = Field(pattern=SHA256)
    evidence_sha256: str = Field(pattern=SHA256)
    no_dispatch: StrictBool
    all_dispatch_paths_verified: StrictBool
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def complete_proof(self):
        if not self.no_dispatch or not self.all_dispatch_paths_verified or not self.reason.strip():
            raise ValueError(
                "Cancellation requires complete evidence that no request was dispatched"
            )
        return self


class _Settlement(StrictModel):
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    evidence_sha256: str = Field(pattern=SHA256)
    verified: StrictBool
    completed: StrictBool

    @model_validator(mode="after")
    def complete_usage(self):
        if not self.verified or not self.completed:
            raise ValueError(
                "Only completed, independently verified usage can settle a reservation"
            )
        return self


class _Reservation(StrictModel):
    operation_id: str = Field(min_length=1, max_length=200)
    request_sha256: str = Field(pattern=SHA256)
    max_output_tokens: StrictInt = Field(ge=1)
    reserved_nanodollars: StrictInt = Field(ge=0)
    charged_nanodollars: StrictInt = Field(ge=0)
    status: Literal["reserved", "dispatched", "held", "settled", "released"] = "reserved"
    created_at: str
    dispatched_at: str | None = None
    finished_at: str | None = None
    hold_reasons: list[str] = Field(default_factory=list)
    settlement: _Settlement | None = None
    cancellation: CancellationProof | None = None


class _AuthorizationEvent(StrictModel):
    at: str
    record: AuthorizationRecord


class _Journal(StrictModel):
    schema_version: Literal[1] = 1
    rates: RateCard
    rates_sha256: str = Field(pattern=SHA256)
    ceiling_usd: Decimal
    ceiling_nanodollars: StrictInt = Field(ge=0)
    authorization_history: list[_AuthorizationEvent] = Field(min_length=1)
    reservations: dict[str, _Reservation] = Field(default_factory=dict)

    @field_validator("ceiling_usd", mode="before")
    @classmethod
    def exact_ceiling(cls, value):
        return _money(value)


def model_budget_plan(
    settings: DiscoverySettings, case_count: int, rates: RateCard
) -> dict[str, Any]:
    """Worst-case token-rate reservation for a request, one arm, a pair and all cases.

    Every attempt must consume a model round, including retries. Input token
    limits come from the rate card and require enforcement by the dispatcher.
    """
    _tokens(case_count, "case_count", positive=True)
    cost = rates.cost_nanodollars(rates.max_input_tokens_per_request, settings.max_output_tokens)

    def amount(requests: int) -> dict[str, int | str]:
        return {"requests": requests, "nanodollars": cost * requests, "usd": _usd(cost * requests)}

    return {
        "schema_version": 1,
        "rates": rates.model_dump(mode="json"),
        "rates_sha256": rates.fingerprint(),
        "settings": settings.model_dump(mode="json"),
        "case_count": case_count,
        "arms_per_pair": 2,
        "per_request": amount(1),
        "per_case": amount(settings.max_rounds),
        "per_pair": amount(settings.max_rounds * 2),
        "full_benchmark": amount(settings.max_rounds * 2 * case_count),
        "authorization": "not established by this plan",
        "scope": "supplied token rates only; excludes unmodelled fees, taxes and price changes",
    }


class BudgetExceeded(ValueError):
    pass


class BudgetLedger:
    """A local, locked reservation journal; it never sends or authorizes requests.

    Reserve before any possible network dispatch. Call mark_dispatched once
    before dispatch; its durable marker cannot be reused as permission to retry.
    An identical reserve retry returns the existing state, never a fresh grant.
    Unknown outcomes remain fully charged until verified settlement or complete
    evidence of pre-dispatch cancellation. Caller authorization is metadata only.
    """

    def __init__(
        self,
        path: Path,
        *,
        rates: RateCard,
        ceiling_usd: Decimal | str | int,
        authorization: AuthorizationRecord | None = None,
    ):
        self.path = Path(path).resolve()
        self.rates = RateCard.model_validate(rates)
        self.ceiling_usd = _money(ceiling_usd)
        self.ceiling_nanodollars = _ceiling_nanodollars(self.ceiling_usd)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.path) + ".lock", timeout=30)
        with self.lock:
            if self.path.exists():
                state = self._load()
                if (
                    authorization is not None
                    and authorization != state.authorization_history[-1].record
                ):
                    raise ValueError("Authorization metadata changed; record it explicitly")
            else:
                self._write(
                    _Journal(
                        rates=self.rates,
                        rates_sha256=self.rates.fingerprint(),
                        ceiling_usd=self.ceiling_usd,
                        ceiling_nanodollars=self.ceiling_nanodollars,
                        authorization_history=[
                            _AuthorizationEvent(
                                at=timestamp(), record=authorization or AuthorizationRecord()
                            )
                        ],
                    )
                )

    def _validate(self, state: _Journal) -> None:
        if (
            state.rates != self.rates
            or state.rates_sha256 != self.rates.fingerprint()
            or state.ceiling_usd != self.ceiling_usd
            or state.ceiling_nanodollars != self.ceiling_nanodollars
        ):
            raise ValueError("Budget ceiling or immutable rate card differs from the journal")
        for key, row in state.reservations.items():
            expected = self.rates.cost_nanodollars(
                self.rates.max_input_tokens_per_request, row.max_output_tokens
            )
            if key != row.operation_id or row.reserved_nanodollars != expected:
                raise ValueError("Invalid reservation identity or reserved amount")
            if row.status == "settled":
                usage = row.settlement
                if (
                    usage is None
                    or row.dispatched_at is None
                    or row.cancellation is not None
                    or usage.input_tokens > self.rates.max_input_tokens_per_request
                    or usage.output_tokens > row.max_output_tokens
                ):
                    raise ValueError("Invalid settled usage or token bound")
                charge = self.rates.cost_nanodollars(usage.input_tokens, usage.output_tokens)
            elif row.status == "released":
                if (
                    row.cancellation is None
                    or row.cancellation.request_sha256 != row.request_sha256
                    or row.dispatched_at is not None
                    or row.settlement is not None
                ):
                    raise ValueError("Invalid pre-dispatch cancellation")
                charge = 0
            else:
                if row.settlement is not None or row.cancellation is not None:
                    raise ValueError("Unresolved reservations cannot contain terminal evidence")
                if row.status == "dispatched" and row.dispatched_at is None:
                    raise ValueError("Missing durable dispatch marker")
                charge = expected
            if row.charged_nanodollars != charge or charge > expected:
                raise ValueError("Invalid charged amount")
        if (
            sum(row.charged_nanodollars for row in state.reservations.values())
            > self.ceiling_nanodollars
        ):
            raise ValueError("Budget journal exceeds its declared ceiling")

    def _load(self) -> _Journal:
        state = _Journal.model_validate(json.loads(self.path.read_bytes()))
        self._validate(state)
        return state

    def _write(self, state: _Journal) -> None:
        self._validate(state)
        write_json(self.path, state.model_dump(mode="json"))
        # atomic_write fsyncs the new file; persist the renamed directory entry
        # too, so a successful reservation is durable before dispatch proceeds.
        descriptor = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _row(state: _Journal, operation_id: str) -> _Reservation:
        if operation_id not in state.reservations:
            raise ValueError("Unknown budget operation")
        return state.reservations[operation_id]

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            state = self._load()
            settled = sum(
                row.charged_nanodollars
                for row in state.reservations.values()
                if row.status == "settled"
            )
            held = sum(
                row.charged_nanodollars
                for row in state.reservations.values()
                if row.status not in {"settled", "released"}
            )
            return {
                **state.model_dump(mode="json"),
                "authorization": state.authorization_history[-1].record.model_dump(mode="json"),
                "authorization_is_dispatch_permission": False,
                "accounting": {
                    "settled_nanodollars": settled,
                    "held_nanodollars": held,
                    "committed_nanodollars": settled + held,
                    "remaining_nanodollars": self.ceiling_nanodollars - settled - held,
                    "committed_usd": _usd(settled + held),
                    "remaining_usd": _usd(self.ceiling_nanodollars - settled - held),
                },
            }

    def record_authorization(self, record: AuthorizationRecord) -> dict[str, Any]:
        """Record an external caller assertion without treating it as verified approval."""
        record = AuthorizationRecord.model_validate(record)
        with self.lock:
            state = self._load()
            if state.authorization_history[-1].record != record:
                state.authorization_history.append(
                    _AuthorizationEvent(at=timestamp(), record=record)
                )
                self._write(state)
            return record.model_dump(mode="json")

    def reserve(
        self, operation_id: str, *, request_sha256: str, max_output_tokens: int
    ) -> dict[str, Any]:
        _tokens(max_output_tokens, "max_output_tokens", positive=True)
        amount = self.rates.cost_nanodollars(
            self.rates.max_input_tokens_per_request, max_output_tokens
        )
        new = _Reservation(
            operation_id=operation_id,
            request_sha256=request_sha256,
            max_output_tokens=max_output_tokens,
            reserved_nanodollars=amount,
            charged_nanodollars=amount,
            created_at=timestamp(),
        )
        if not operation_id.strip():
            raise ValueError("Operation id must not be blank")
        with self.lock:
            state = self._load()
            if operation_id in state.reservations:
                row = state.reservations[operation_id]
                if (
                    row.request_sha256 != request_sha256
                    or row.max_output_tokens != max_output_tokens
                ):
                    raise ValueError(
                        "Budget operation id was already used with different arguments"
                    )
                return row.model_dump(mode="json")
            charged = sum(row.charged_nanodollars for row in state.reservations.values())
            if charged + amount > self.ceiling_nanodollars:
                raise BudgetExceeded("Insufficient budget for the request's worst-case reservation")
            state.reservations[operation_id] = new
            self._write(state)
            return new.model_dump(mode="json")

    def mark_dispatched(self, operation_id: str) -> dict[str, Any]:
        with self.lock:
            state = self._load()
            row = self._row(state, operation_id)
            if row.status != "reserved" or row.dispatched_at is not None:
                raise ValueError(
                    "Operation cannot be dispatched again or from an unresolved outcome"
                )
            row.status, row.dispatched_at = "dispatched", timestamp()
            self._write(state)
            return row.model_dump(mode="json")

    def hold(self, operation_id: str, *, reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Unknown or interrupted usage needs a recorded reason")
        with self.lock:
            state = self._load()
            row = self._row(state, operation_id)
            if row.status in {"settled", "released"}:
                raise ValueError("A terminal budget operation cannot be held again")
            if row.status != "held" or not row.hold_reasons or row.hold_reasons[-1] != reason:
                row.status = "held"
                row.hold_reasons.append(reason)
                self._write(state)
            return row.model_dump(mode="json")

    def settle(
        self,
        operation_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        evidence_sha256: str,
        verified: bool,
        completed: bool,
    ) -> dict[str, Any]:
        usage = _Settlement(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            evidence_sha256=evidence_sha256,
            verified=verified,
            completed=completed,
        )
        with self.lock:
            state = self._load()
            row = self._row(state, operation_id)
            if row.status == "settled":
                if row.settlement != usage:
                    raise ValueError(
                        "Operation was already settled with different usage or evidence"
                    )
                return row.model_dump(mode="json")
            if row.status == "released":
                raise ValueError("A released operation cannot be settled")
            if row.dispatched_at is None:
                raise ValueError("Settlement requires a durable dispatch marker")
            if (
                input_tokens > self.rates.max_input_tokens_per_request
                or output_tokens > row.max_output_tokens
            ):
                raise ValueError(
                    "Verified usage exceeds the reserved token bound; keep the reservation held"
                )
            row.settlement = usage
            row.charged_nanodollars = self.rates.cost_nanodollars(input_tokens, output_tokens)
            row.status, row.finished_at = "settled", timestamp()
            self._write(state)
            return row.model_dump(mode="json")

    def cancel_before_dispatch(
        self, operation_id: str, *, proof: CancellationProof
    ) -> dict[str, Any]:
        proof = CancellationProof.model_validate(proof)
        with self.lock:
            state = self._load()
            row = self._row(state, operation_id)
            if row.status == "released":
                if row.cancellation != proof:
                    raise ValueError(
                        "Operation was already released with different cancellation evidence"
                    )
                return row.model_dump(mode="json")
            if row.status not in {"reserved", "held"} or row.dispatched_at is not None:
                raise ValueError("A dispatched or settled operation cannot be released as unsent")
            if proof.request_sha256 != row.request_sha256:
                raise ValueError("Cancellation proof belongs to a different request")
            row.status, row.finished_at = "released", timestamp()
            row.cancellation = proof
            row.charged_nanodollars = 0
            self._write(state)
            return row.model_dump(mode="json")
