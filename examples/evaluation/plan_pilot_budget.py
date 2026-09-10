"""Calculate a proposed token-rate budget offline; never create a provider client."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from research_harness.config import StrictModel
from research_harness.evaluation.budget import AuthorizationRecord, RateCard, model_budget_plan
from research_harness.evaluation.controller import ComparisonConfig
from research_harness.util import digest, write_json


class PilotBudgetProposal(StrictModel):
    schema_version: Literal[1] = 1
    authorization: AuthorizationRecord
    proposed_ceiling_usd: str
    upstream_base_url: Literal["https://api.openai.com/v1"]
    proposed_service_tier: Literal["default"]
    comparison: ComparisonConfig
    case_count: StrictInt = Field(ge=1)
    rates: RateCard

    @model_validator(mode="after")
    def draft_only(self):
        if self.authorization.status != "draft":
            raise ValueError("This file is a budget proposal, not a spending authorization")
        ceiling = Decimal(self.proposed_ceiling_usd)
        if not ceiling.is_finite() or ceiling <= 0:
            raise ValueError("Proposed ceiling must be a finite positive decimal string")
        if self.comparison.model != self.rates.snapshot or self.rates.model != self.rates.snapshot:
            raise ValueError("The proposed run and rate card must name the same model snapshot")
        if self.comparison.execution != "model":
            raise ValueError("This estimates a proposed model run; fixtures have no provider bill")
        return self


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--proposal", type=Path, default=Path(__file__).with_name("pilot-budget.proposed.json")
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    raw = args.proposal.read_bytes()
    proposal = PilotBudgetProposal.model_validate_json(raw)
    report = model_budget_plan(proposal.comparison.settings, proposal.case_count, proposal.rates)
    report.update(
        proposal_sha256=digest(raw),
        authorization_record=proposal.authorization.model_dump(mode="json"),
        proposed_ceiling_usd=proposal.proposed_ceiling_usd,
        upstream_base_url=proposal.upstream_base_url,
        proposed_service_tier=proposal.proposed_service_tier,
        dispatcher_budget_enforcement="available through the budgeted pilot; draft is not dispatch permission",
        paid_calls=False,
        assumptions=[
            "Standard token prices at the documented date; no cached-input discount assumed",
            "Full model context reserved as input plus the configured output cap",
            "No native provider tools, regional endpoint, priority tier, or external source fees",
            "Settlement may reduce held funds only with independently verified terminal usage",
            "This is worst-case rate-based planning, not an expected invoice or approval",
        ],
    )
    if args.out:
        write_json(args.out, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
