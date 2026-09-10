"""Conservative rate-based planning and durable request reservations.

Rates, token bounds and authorization records are supplied by the caller. This
module neither verifies user approval nor grants permission to dispatch work.
Its bounds cover the supplied token rates, not invoices, taxes or other fees.
The dispatcher must enforce the reserved input/output token bounds and
verify usage evidence independently before settling an operation.
"""

from __future__ import annotations

import argparse
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


class ProviderAccountingConfirmation(StrictModel):
    """An operator's reviewed transcription of retained external evidence.

    This validates consistency, not the provider's authenticity or the identity
    of the reviewer. Never derive it from absent usage or an agent assertion.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")
    provider_request_id: str = Field(pattern=r"^[!-~]{1,200}$")
    confirmed_cost_usd: Decimal
    currency: Literal["usd"]
    final_charge_confirmed: StrictBool
    reference: str = Field(min_length=1, max_length=2000)
    reviewed_by: str = Field(min_length=1, max_length=200)
    provider_evidence: str = Field(min_length=1, max_length=65536)
    provider_evidence_sha256: str = Field(pattern=SHA256)

    @field_validator("confirmed_cost_usd", mode="before")
    @classmethod
    def exact_cost(cls, value):
        return _money(value)

    @model_validator(mode="after")
    def reviewed_confirmation(self):
        if (
            not self.final_charge_confirmed
            or not self.reference.strip()
            or not self.reviewed_by.strip()
            or not self.provider_evidence.strip()
            or self.provider_request_id not in self.provider_evidence
            or digest(self.provider_evidence.encode("utf-8")) != self.provider_evidence_sha256
        ):
            raise ValueError("Accounting requires a reviewed, retained final-charge confirmation")
        return self

    def cost_nanodollars(self) -> int:
        value = Fraction(self.confirmed_cost_usd) * NANODOLLARS
        return (value.numerator + value.denominator - 1) // value.denominator


class ProviderAccountingSettlement(StrictModel):
    """Separate accounting evidence; it deliberately has no token counters."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")
    kind: Literal["operator_reviewed_provider_accounting_v1"]
    operation_id: str = Field(min_length=1, max_length=200)
    request_sha256: str = Field(pattern=SHA256)
    evidence_sha256: str = Field(pattern=SHA256)
    http_status: StrictInt = Field(ge=400, le=599)
    confirmation: ProviderAccountingConfirmation


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
    settlement: _Settlement | ProviderAccountingSettlement | None = None
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


class _Initialization(StrictModel):
    schema_version: Literal[1]
    ledger_path: str
    ledger_sha256: str = Field(pattern=SHA256)
    ledger: _Journal


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _persist(path: Path, data: Any) -> None:
    write_json(path, data)
    # atomic_write fsyncs the file; persist the renamed directory entry too.
    _sync_directory(path.parent)


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
        create: bool = True,
    ):
        self._configure(path, rates, ceiling_usd)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            if self.path.exists():
                self._check_authorization(self._load(), authorization)
            else:
                if not create:
                    raise ValueError(
                        "Existing budget ledger is missing; never recreate spent funds"
                    )
                self._write(self._initial_state(authorization))

    def _configure(self, path: Path, rates: RateCard, ceiling_usd: Decimal | str | int) -> None:
        self.path = Path(path).resolve()
        self.rates = RateCard.model_validate(rates)
        self.ceiling_usd = _money(ceiling_usd)
        self.ceiling_nanodollars = _ceiling_nanodollars(self.ceiling_usd)
        self.lock = FileLock(str(self.path) + ".lock", timeout=30)

    def _initial_state(self, authorization: AuthorizationRecord | None) -> _Journal:
        return _Journal(
            rates=self.rates,
            rates_sha256=self.rates.fingerprint(),
            ceiling_usd=self.ceiling_usd,
            ceiling_nanodollars=self.ceiling_nanodollars,
            authorization_history=[
                _AuthorizationEvent(at=timestamp(), record=authorization or AuthorizationRecord())
            ],
        )

    @staticmethod
    def _check_authorization(state: _Journal, authorization: AuthorizationRecord | None) -> None:
        if authorization is not None and authorization != state.authorization_history[-1].record:
            raise ValueError("Authorization metadata changed; record it explicitly")

    @classmethod
    def prepare_registered(
        cls,
        path: Path,
        *,
        rates: RateCard,
        ceiling_usd: Decimal | str | int,
        authorization: AuthorizationRecord | None = None,
    ) -> BudgetLedger:
        """Complete a recorded initial ledger/registry pair before preparing any run.

        The intent contains the exact empty ledger, never a replacement for an
        established balance. Callers still verify all registered run evidence.
        """
        book = cls.__new__(cls)
        book._configure(path, rates, ceiling_usd)
        book.path.parent.mkdir(parents=True, exist_ok=True)
        registry = Path(str(book.path) + ".pilots.json")
        intent_path = Path(str(book.path) + ".initializing.json")
        empty_registry = {"schema_version": 1, "pilots": {}}
        with book.lock:
            if registry.is_symlink() or intent_path.is_symlink():
                raise ValueError("Budget registry and initialization intent cannot be symlinks")
            if not intent_path.exists():
                if book.path.exists() or registry.exists():
                    if not book.path.is_file():
                        raise ValueError(
                            "Registered shared ledger is missing; never recreate spent funds"
                        )
                    if not registry.is_file():
                        raise ValueError(
                            "Existing shared budget ledger has no pilot registry; do not reset it"
                        )
                    book._check_authorization(book._load(), authorization)
                    # A prior cleanup may have removed the intent and then
                    # failed its directory fsync. Make the surviving pair
                    # durable before admitting preparation on this path.
                    _sync_directory(book.path.parent)
                    return book
                initial = book._initial_state(authorization)
                _persist(
                    intent_path,
                    _Initialization(
                        schema_version=1,
                        ledger_path=str(book.path),
                        ledger=initial,
                        ledger_sha256=digest(canonical_json(initial.model_dump(mode="json"))),
                    ).model_dump(mode="json"),
                )
            raw_intent = json.loads(intent_path.read_bytes())
            if (
                not isinstance(raw_intent, dict)
                or type(raw_intent.get("schema_version")) is not int
            ):
                raise ValueError("Invalid initialization schema version")
            intent = _Initialization.model_validate(raw_intent)
            initial = intent.ledger
            book._validate(initial)
            book._check_authorization(initial, authorization)
            if (
                intent.ledger_path != str(book.path)
                or initial.reservations
                or len(initial.authorization_history) != 1
                or digest(canonical_json(raw_intent["ledger"])) != intent.ledger_sha256
                or digest(canonical_json(initial.model_dump(mode="json"))) != intent.ledger_sha256
            ):
                raise ValueError("Invalid empty-ledger initialization evidence")
            # Validate both surviving files before creating either missing file.
            # Changed authorization, any spending or registered run ends the
            # right to recover from this empty-state intent.
            if (
                book.path.exists()
                and digest(canonical_json(json.loads(book.path.read_bytes())))
                != intent.ledger_sha256
            ):
                raise ValueError("Ledger changed since initialization; preserve its existing state")
            if registry.exists() and canonical_json(
                json.loads(registry.read_bytes())
            ) != canonical_json(empty_registry):
                raise ValueError(
                    "Registry changed since initialization; preserve its existing state"
                )
            if not book.path.exists():
                book._write(initial)
            if not registry.exists():
                _persist(registry, empty_registry)
            intent_path.unlink()
            _sync_directory(book.path.parent)
        return book

    @classmethod
    def open_existing(cls, path: Path) -> BudgetLedger:
        """Read retained terms and validate under lock without ever creating a ledger."""
        path = Path(path).resolve()
        state = _Journal.model_validate_json(path.read_bytes())
        # The constructor re-reads and validates under its lock. A file lost or
        # replaced between these reads cannot become a new spending balance.
        return cls(path, rates=state.rates, ceiling_usd=state.ceiling_usd, create=False)

    def _validate(self, state: _Journal) -> None:
        if (
            state.rates != self.rates
            or state.rates_sha256 != self.rates.fingerprint()
            or state.ceiling_usd != self.ceiling_usd
            or state.ceiling_nanodollars != self.ceiling_nanodollars
        ):
            raise ValueError("Budget ceiling or immutable rate card differs from the journal")
        accounted_provider_ids = set()
        for key, row in state.reservations.items():
            expected = self.rates.cost_nanodollars(
                self.rates.max_input_tokens_per_request, row.max_output_tokens
            )
            if key != row.operation_id or row.reserved_nanodollars != expected:
                raise ValueError("Invalid reservation identity or reserved amount")
            if row.status == "settled":
                usage = row.settlement
                if usage is None or row.dispatched_at is None or row.cancellation is not None:
                    raise ValueError("Invalid settled usage or token bound")
                if isinstance(usage, ProviderAccountingSettlement):
                    provider_id = usage.confirmation.provider_request_id
                    if (
                        usage.operation_id != row.operation_id
                        or usage.request_sha256 != row.request_sha256
                        or provider_id in accounted_provider_ids
                    ):
                        raise ValueError("Invalid or duplicate provider accounting binding")
                    accounted_provider_ids.add(provider_id)
                    charge = usage.confirmation.cost_nanodollars()
                else:
                    if (
                        usage.input_tokens > self.rates.max_input_tokens_per_request
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
        _persist(self.path, state.model_dump(mode="json"))

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

    def record_provider_accounting(
        self, operation_id: str, *, proof: ProviderAccountingSettlement
    ) -> dict[str, Any]:
        """Retain a host-verified binding and operator-reviewed external charge.

        DispatchBudget must independently check the sealed error request first.
        This primitive, like settle(), does not authenticate provider evidence.
        """
        proof = ProviderAccountingSettlement.model_validate(proof)
        with self.lock:
            state = self._load()
            row = self._row(state, operation_id)
            if row.status == "settled":
                if row.settlement != proof:
                    raise ValueError("Operation already settled with different accounting evidence")
                return row.model_dump(mode="json")
            if row.status == "released" or row.dispatched_at is None:
                raise ValueError("Provider accounting requires an existing durable dispatch")
            if proof.operation_id != operation_id or proof.request_sha256 != row.request_sha256:
                raise ValueError("Provider accounting belongs to another operation or request")
            charge = proof.confirmation.cost_nanodollars()
            if charge > row.reserved_nanodollars:
                raise ValueError("Confirmed charge exceeds the reservation; retain the hold")
            row.settlement = proof
            row.charged_nanodollars = charge
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Manage retained model-budget authorization metadata"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    authorization = commands.add_parser(
        "record-authorization",
        help="Record an external decision without granting spending permission",
    )
    authorization.add_argument("ledger", type=Path, help="Existing shared budget ledger")
    authorization.add_argument(
        "--authorization",
        type=Path,
        required=True,
        help="JSON AuthorizationRecord containing status and an external decision reference",
    )
    args = parser.parse_args(argv)
    try:
        record = AuthorizationRecord.model_validate_json(args.authorization.read_bytes())
        ledger = BudgetLedger.open_existing(args.ledger)
        with ledger.lock:
            ledger.record_authorization(record)
            snapshot = ledger.snapshot()
            print(
                json.dumps(
                    {
                        key: snapshot[key]
                        for key in (
                            "authorization",
                            "authorization_is_dispatch_permission",
                            "accounting",
                        )
                    },
                    indent=2,
                )
            )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
