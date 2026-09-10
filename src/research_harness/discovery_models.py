"""Provider-neutral research requirements and source proposal contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from research_harness.config import SourceSpec, StrictModel
from research_harness.util import http_url


class DataNeed(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    description: str
    required: bool


class Candidate(StrictModel):
    name: str
    purpose: str
    covers: list[str]
    status: Literal["ready", "needs_connector", "needs_access", "unavailable"]
    evidence_urls: list[str]
    source: SourceSpec | None
    probe_id: str | None
    freshness_assessment: str
    historical_coverage: str
    access_notes: str
    limitations: list[str]

    @field_validator("evidence_urls")
    @classmethod
    def valid_urls(cls, values: list[str]) -> list[str]:
        return [http_url(value) for value in values]


class ProposalDraft(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    title: str
    research_question: str
    needs: list[DataNeed] = Field(min_length=1, max_length=20)
    candidates: list[Candidate] = Field(min_length=1, max_length=12)
    open_questions: list[str]
