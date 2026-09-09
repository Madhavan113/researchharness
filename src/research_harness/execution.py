"""Explicit settings shared by the direct and Omnigent comparison paths."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictInt

from research_harness.config import StrictModel


class DiscoverySettings(StrictModel):
    max_rounds: StrictInt = Field(default=16, ge=1, le=30)
    max_output_tokens: StrictInt = Field(default=6000, ge=1, le=128000)
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] = "none"
    service_tier: Literal["default"] | None = None
    max_searches: StrictInt = Field(default=8, ge=0, le=100)
    max_inspections: StrictInt = Field(default=16, ge=0, le=100)
    max_probes: StrictInt = Field(default=12, ge=0, le=100)
    deadline_seconds: StrictInt = Field(default=600, ge=1, le=86400)

    def limits(self) -> dict[str, int]:
        return {
            "search": self.max_searches,
            "inspection": self.max_inspections,
            "probe": self.max_probes,
        }

    def budgets(self) -> dict[str, int]:
        return {
            **self.limits(),
            "deadline_seconds": self.deadline_seconds,
            "model_rounds": self.max_rounds,
            "max_output_tokens": self.max_output_tokens,
        }

    def model_settings(self) -> dict:
        settings = {
            "max_output_tokens": self.max_output_tokens,
            "reasoning": {"effort": self.reasoning_effort},
        }
        if self.service_tier is not None:
            settings["service_tier"] = self.service_tier
        return settings


class GatewayBinding(StrictModel):
    """Host-owned execution identity set before the first provider request."""

    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str = Field(min_length=1, max_length=100)
    runtime: str = Field(min_length=1, max_length=100)
    phase: Literal["discovery", "workflow", "followup"] = "discovery"
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
