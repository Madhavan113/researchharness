from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from research_harness.util import canonical_json, digest, http_url


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Parameter(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=10_000)


class Pagination(StrictModel):
    mode: Literal["none", "cursor", "next_url"] = "none"
    next_pointer: str | None = None
    cursor_parameter: str | None = None
    stop_when_missing: bool = Field(
        default=False,
        description="Enable only when the source's terminal-page convention omits the continuation field",
    )

    @model_validator(mode="after")
    def coherent(self) -> Pagination:
        if self.mode != "none" and not self.next_pointer:
            raise ValueError("Pagination requires next_pointer")
        if self.mode == "cursor" and not self.cursor_parameter:
            raise ValueError("Cursor pagination requires cursor_parameter")
        if self.next_pointer and not self.next_pointer.startswith("/"):
            raise ValueError("next_pointer must be a JSON pointer")
        return self


class SourceSpec(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=200)
    connector: Literal["polymarket", "kalshi", "rss", "json", "html"]
    url: str
    parameters: list[Parameter] = Field(default_factory=list)
    pagination: Pagination = Field(default_factory=Pagination)
    items_pointer: str = ""
    id_pointer: str | None = None
    published_pointer: str | None = None
    required_pointers: list[str] = Field(default_factory=list)
    include_orderbooks: bool = False
    poll_interval_seconds: int = Field(default=900, ge=60, le=2_592_000)
    max_pages: int = Field(default=10, ge=1, le=100)
    max_records: int = Field(default=5000, ge=1, le=100_000)
    min_records: int = Field(default=1, ge=0)
    enabled: bool = True

    _url = field_validator("url")(http_url)

    @model_validator(mode="after")
    def coherent(self) -> SourceSpec:
        if self.min_records > self.max_records:
            raise ValueError("min_records exceeds max_records")
        if self.connector == "json" and self.id_pointer is None:
            raise ValueError("JSON sources require id_pointer for stable record identity")
        for pointer in [
            self.items_pointer,
            self.id_pointer,
            self.published_pointer,
            *self.required_pointers,
        ]:
            if pointer is not None and pointer != "" and not pointer.startswith("/"):
                raise ValueError(f"Invalid JSON pointer: {pointer}")
        host = urlsplit(self.url).hostname
        if self.connector == "polymarket" and host != "gamma-api.polymarket.com":
            raise ValueError("Polymarket metadata must come from gamma-api.polymarket.com")
        if self.connector == "polymarket":
            path = urlsplit(self.url).path.rstrip("/")
            params = dict(parse_qsl(urlsplit(self.url).query))
            params.update((parameter.name, parameter.value) for parameter in self.parameters)
            if path in {"/markets", "/events"} and not params.get("slug"):
                raise ValueError(
                    "Use a scoped slug query, or /markets/keyset with cursor pagination; unpaginated catalogs are incomplete"
                )
            if path == "/markets/keyset" and (
                self.pagination.mode != "cursor"
                or self.pagination.next_pointer != "/next_cursor"
                or self.pagination.cursor_parameter != "after_cursor"
            ):
                raise ValueError(
                    "Polymarket keyset pagination requires /next_cursor and after_cursor"
                )
        if self.connector == "kalshi" and host not in {
            "external-api.kalshi.com",
            "api.elections.kalshi.com",
        }:
            raise ValueError("Kalshi metadata must come from an official Kalshi API host")
        if self.connector in {"rss", "html"} and self.pagination.mode != "none":
            raise ValueError("Feed and HTML connectors capture one document per run")
        if self.include_orderbooks and self.connector not in {"polymarket", "kalshi"}:
            raise ValueError("Order books require a market connector")
        if self.connector == "kalshi" and self.pagination.mode != "cursor":
            raise ValueError("Kalshi sources require cursor pagination with /cursor")
        if self.connector == "kalshi" and (
            self.pagination.next_pointer != "/cursor"
            or self.pagination.cursor_parameter != "cursor"
        ):
            raise ValueError("Use /cursor and the cursor query parameter for Kalshi")
        for parameter in self.parameters:
            if re.search(
                r"(^|[_-])(key|token|secret|password|authorization)($|[_-])", parameter.name, re.I
            ):
                raise ValueError(
                    "This version supports public sources; do not put credentials in parameters"
                )
        return self

    def fingerprint(self) -> str:
        return digest(canonical_json(self.model_dump(mode="json")))


class HttpSettings(StrictModel):
    timeout_seconds: float = Field(default=20, gt=0, le=120)
    max_attempts: int = Field(default=3, ge=1, le=6)
    min_interval_seconds: float = Field(default=0.25, ge=0, le=30)
    max_retry_delay_seconds: float = Field(default=30, ge=0, le=60)
    max_response_bytes: int = Field(default=5_000_000, ge=1024, le=50_000_000)
    max_requests_per_source: int = Field(default=100, ge=1, le=1000)


class PipelineSpec(StrictModel):
    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    description: str
    data_dir: str = ".researchharness/data"
    http: HttpSettings = Field(default_factory=HttpSettings)
    sources: list[SourceSpec] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_sources(self) -> PipelineSpec:
        ids = [source.id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("Source ids must be unique within a pipeline")
        return self

    def fingerprint(self) -> str:
        return digest(canonical_json(self.model_dump(mode="json")))


def load_pipeline(path: Path) -> PipelineSpec:
    return PipelineSpec.model_validate(json.loads(path.read_text()))


def data_path(spec: PipelineSpec, config_path: Path) -> Path:
    path = Path(spec.data_dir).expanduser()
    return path.resolve() if path.is_absolute() else (config_path.parent / path).resolve()
