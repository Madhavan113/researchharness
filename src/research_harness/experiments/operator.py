"""Local operator entry points; reuse the existing curation, budget and runtime."""

from pathlib import Path
from uuid import uuid4

import httpx

from research_harness.evaluation.budget import AuthorizationRecord, BudgetLedger, RateCard
from research_harness.evaluation.dispatch_budget import DispatchBudget, DispatchPolicy
from research_harness.execution import DiscoverySettings, GatewayBinding
from research_harness.experiments import execution
from research_harness.experiments.curation import CuratorStore
from research_harness.experiments.program import read_program
from research_harness.util import digest


def prepare_budget(rates: Path, output: Path, ceiling: str, authorization: Path | None) -> dict:
    record = (
        AuthorizationRecord.model_validate_json(authorization.read_bytes())
        if authorization
        else None
    )
    ledger = BudgetLedger.prepare_registered(
        output,
        rates=RateCard.model_validate_json(rates.read_bytes()),
        ceiling_usd=ceiling,
    )
    if record is not None:
        ledger.record_authorization(record)
    snapshot = ledger.snapshot()
    return {
        "ledger": str(ledger.path),
        "rates": snapshot["rates"],
        "ceiling_usd": snapshot["ceiling_usd"],
        "authorization": snapshot["authorization"],
        "authorization_is_dispatch_permission": False,
        "accounting": snapshot["accounting"],
    }


def run(
    prepared: Path,
    check: Path,
    store: Path,
    ledger_path: Path,
    settings_path: Path,
    candidate: Path,
    *,
    provider: str,
    output: Path,
    harbor: Path,
    omnigent_python: Path,
    timeout: int = 1800,
    dry_run: bool = False,
    provider_api_key: str | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """Inspect or launch an explicitly configured run, never creating its budget.

    Fixture injection is a trusted test API; the CLI offers no fixture provider.
    A saved authorization reference is operator metadata, not proof of permission.
    """
    curator = CuratorStore(store)
    review = curator.inspect(prepared, check)
    ledger = BudgetLedger.open_existing(ledger_path)
    settings = DiscoverySettings.model_validate_json(settings_path.read_bytes())
    policy = DispatchPolicy(
        mode=provider,
        upstream_base_url=(
            "https://fixture.invalid/v1" if provider == "fixture" else "https://api.openai.com/v1"
        ),
        model=ledger.rates.model,
    )
    budget = DispatchBudget(
        ledger,
        policy=policy,
        settings=settings,
        binding=GatewayBinding(
            execution_id=uuid4().hex,
            case_id=review["experiment_id"],
            runtime="omnigent-experiment",
            phase="workflow",
            task_sha256=review["input_sha256"],
        ),
    )
    source = read_program(candidate)
    snapshot = ledger.snapshot()
    reservation = ledger.rates.cost_nanodollars(
        ledger.rates.max_input_tokens_per_request, settings.max_output_tokens
    )
    prerequisites = {
        "benchmark_accepted": review["curation_status"] == "accepted",
        "spending_approval_recorded": (
            provider == "fixture" or snapshot["authorization"]["status"] == "approved"
        ),
        "can_reserve_one_request": snapshot["accounting"]["remaining_nanodollars"] >= reservation,
    }
    if dry_run:
        return {
            "kind": "experiment_run_plan",
            "status": "planned",
            "execution_started": False,
            "experiment_id": review["experiment_id"],
            "input_sha256": review["input_sha256"],
            "review_head": review["head"],
            "candidate_sha256": digest(source),
            "output": str(output.resolve()),
            "harbor": str(harbor.absolute()),
            "omnigent_python": str(omnigent_python.absolute()),
            "timeout_seconds": timeout,
            "settings": settings.model_dump(mode="json"),
            "budget": budget.metadata(),
            "accounting": snapshot["accounting"],
            "reservation_per_request_nanodollars": reservation,
            "prerequisites": prerequisites,
            "runtime_checked": False,
        }
    missing = [name for name, satisfied in prerequisites.items() if not satisfied]
    if missing:
        raise ValueError("Run prerequisites not met: " + ", ".join(missing))
    if provider == "openai-standard":
        if not provider_api_key or not provider_api_key.strip():
            raise ValueError("Set OPENAI_API_KEY before starting the explicitly configured run")
        if client is not None:
            raise ValueError("OpenAI dispatch cannot use an injected client")
    elif (
        provider_api_key is not None
        or type(client) is not httpx.Client
        or not isinstance(
            client._transport_for_url(httpx.URL(policy.upstream_base_url)), httpx.MockTransport
        )
    ):
        raise ValueError("Fixture dispatch requires an explicit MockTransport and no provider key")
    return execution.run(
        prepared,
        check,
        curator,
        budget,
        output=output,
        harbor=harbor,
        omnigent_python=omnigent_python,
        timeout=timeout,
        provider_api_key=provider_api_key,
        client=client,
        candidate=candidate,
    )
