from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import Field, model_validator

from research_harness.config import StrictModel
from research_harness.util import canonical_json, digest

NORMALIZER_VERSION = "1"


def decimal_string(value: Any, *, unit_interval: bool = False, positive: bool = False) -> str:
    if isinstance(value, bool):
        raise ValueError("A boolean is not a numeric value")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal: {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("A numeric value must be finite and nonnegative")
    if unit_interval and result > 1:
        raise ValueError("A binary contract price must be between 0 and 1")
    if positive and result <= 0:
        raise ValueError("A resting order must have positive size")
    return format(result.normalize(), "f")


class Outcome(StrictModel):
    label: str = Field(min_length=1)
    token_id: str | None = None


class Market(StrictModel):
    venue: Literal["polymarket", "kalshi"]
    contract_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    rules: str = Field(min_length=1)
    event_id: str | None = None
    slug: str | None = None
    resolution_source: str | None = None
    status: Literal["open", "closed", "resolved", "inactive", "unknown"]
    provider_status: dict[str, Any]
    starts_at: str | None = None
    closes_at: str | None = None
    outcomes: list[Outcome] = Field(min_length=2)
    fees_enabled: bool | None = None
    fee_details: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_outcomes(self) -> Market:
        if len({outcome.label for outcome in self.outcomes}) != len(self.outcomes):
            raise ValueError("Outcome labels must be unique")
        return self


class Quote(StrictModel):
    price: str
    size: str

    @model_validator(mode="after")
    def valid_numbers(self) -> Quote:
        self.price = decimal_string(self.price, unit_interval=True)
        self.size = decimal_string(self.size, positive=True)
        return self


class OrderBook(StrictModel):
    venue: Literal["polymarket", "kalshi"]
    market_key: str
    outcome: str
    token_id: str | None = None
    venue_timestamp: str | None = None
    availability: Literal["available", "unavailable"] = "available"
    bids: list[Quote]
    asks: list[Quote]
    depth_limit: int | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered(self) -> OrderBook:
        self.bids.sort(key=lambda quote: Decimal(quote.price), reverse=True)
        self.asks.sort(key=lambda quote: Decimal(quote.price))
        if self.bids and self.asks and Decimal(self.bids[0].price) > Decimal(self.asks[0].price):
            raise ValueError("Crossed order book: best bid exceeds best ask")
        return self


@dataclass(frozen=True)
class Record:
    kind: str
    key: str
    data: dict[str, Any]
    published_at: str | None = None

    @property
    def content_hash(self) -> str:
        return digest(canonical_json({"data": self.data, "published_at": self.published_at}))


@dataclass(frozen=True)
class Issue:
    location: str
    error: str
    candidate: Any = None


@dataclass
class Parsed:
    records: list[Record] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


@dataclass(frozen=True)
class Capture:
    id: str
    source_id: str
    url: str
    observed_at: str
    status_code: int | None
    body_hash: str | None
    headers: dict[str, str]
    context: dict[str, Any]
    error: str | None = None
    reused_capture_id: str | None = None
