# Budgeted model pilot

The [proposal](../examples/evaluation/pilot-budget.proposed.json) records a **draft $10 ceiling**, the explicit `gpt-5.4-mini-2026-03-17` snapshot, and matched settings for both runtimes. The pending provider/budget decision has not been answered. The host gateway now reserves funds before dispatch, using one durable ledger across both arms and prepared revisions. This draft does not authorize spending.

## Reproduce the plan offline

~~~sh
uv run python examples/evaluation/plan_pilot_budget.py \
  --out /tmp/research-pilot-budget.json
~~~

The command validates the proposed configuration and makes no model or source requests. The checked-in [generated plan](../examples/evaluation/pilot-budget.plan.json) includes the proposal and rate-card hashes. The comparison subsection is a proposed configuration for the budgeted pilot; it has not replaced the default model or authorized a live comparison.

As checked on September 8, 2026, OpenAI lists a 400,000-token context window for this model, with standard text prices of $0.75 per million input tokens and $4.50 per million output tokens. The plan reserves the full context as input, assumes no cache discount, and adds the configured 6,000-token output allowance. Regional processing has a 10% uplift and is outside this proposal. [Model specification and pricing](https://developers.openai.com/api/docs/models/gpt-5.4-mini).

| Scope | Maximum requests | Conservative token-rate reservation |
| --- | ---: | ---: |
| One request | 1 | $0.327 |
| One case in one runtime | 16 | $5.232 |
| One matched pair | 32 | $10.464 |
| Twenty cases in both runtimes | 640 | $209.28 |

These bounds come from the supplied rates and maximum tokens, not observed model costs. A $10 ceiling cannot guarantee completing even one pair at the full worst-case reservation. Unused reservations from verified smaller responses are released during reconciliation after the gateway archive is sealed; an exhausted ceiling must stop dispatch and preserve unfinished cases as failures or interruptions. Changing round limits to fit a budget would require a fresh, matched comparison configuration.

The proposed endpoint is `https://api.openai.com/v1` with `service_tier="default"`. The API documents that explicit default uses standard pricing, while an omitted tier uses project settings; the response reports the tier actually used. The budgeted gateway applies this tier explicitly and rejects conflicting requests. Independently verified returned model/tier and token bounds determine whether a reservation can settle. [Responses API reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create).

## Implemented ledger contract

[BudgetLedger](../src/research_harness/evaluation/budget.py) supplies exact decimal rates, conservative integer rounding, a locked atomic journal, and immutable ceiling/rate-card configuration. A successful reservation is persisted before its caller may dispatch. Reusing an operation ID returns its prior reservation; it never grants a retry. A dispatch marker is one-shot.

Unknown or interrupted outcomes hold the entire reservation. Settlement requires explicit completed, independently verified token counts and an evidence hash, within the reserved input/output bounds. Repeated settlement must match the original counts and evidence exactly. Pre-dispatch cancellation requires matching request/evidence hashes and an explicit host assertion that every dispatch path was checked. Caller-reported authorization metadata neither verifies approval nor sends a request.

The ledger's tests exercise concurrent processes, process crashes, restart/retry conflicts, competing settlements, rounding, and failed persistence. The budgeted gateway integrates these reservations with every provider attempt, including retries. Each request must have a durable, one-shot dispatch marker before HTTP dispatch. This policy applies to the budgeted pilot; other CLI and native Omnigent entry points do not share its ledger automatically. Supplied token-rate accounting does not establish an exact provider invoice, taxes, unmodelled fees, or price adjustments.

## Prepare and operate a pilot

[The pilot runner](../src/research_harness/evaluation/pilot.py) freezes the comparison and [proposed configuration](../examples/evaluation/pilot-config.proposed.json). Preparation makes no provider requests. Keep the ledger outside any prepared comparison and reuse it for source/configuration revisions so a new comparison does not replenish funds.

~~~sh
uv run --extra mcp python -m research_harness.evaluation.pilot prepare \
  examples/evaluation/development/manifest.json \
  --config examples/evaluation/pilot-config.proposed.json \
  --instructions agents/comparison/instructions.md \
  --ledger /tmp/research-model-budget.json \
  --out /tmp/research-model-pilot
~~~

The checked-in authorization is draft. Live execution requires the actual external spending decision recorded in configuration and the ledger, plus OPENAI_API_KEY configured in the environment. Record a changed decision with `python -m research_harness.evaluation.budget record-authorization <shared-ledger.json> --authorization <authorization.json>`, then prepare a new matching pilot; editing an existing frozen manifest is refused. The authorization file contains an `AuthorizationRecord`: `status` is `draft` or `approved`, and approval requires a nonempty `reference` to the actual external decision. The CLI calls `BudgetLedger.record_authorization`, preserves rates, ceiling, reservations and charges, and refuses to create a missing ledger. Identical updates are idempotent; recording draft status revokes the recorded approval. The reference records a real decision; writing an approved value cannot grant permission. Revocation prevents a pending case from starting, and the gateway rechecks current approval immediately before dispatch. Recovery remains available under the original recorded authorization.

Once those prerequisites are satisfied, run one case with `python -m research_harness.evaluation.pilot run-case <pilot> <direct|omnigent> <case-id> --omnigent-python <pinned-venv-python>`. Both arms use the frozen model/settings and source fixtures. Purpose compatibility permits authored cases while making no baseline claim; purpose baseline requires reviewed development cases before provider dispatch. The normal Omnigent runner also receives a frozen discovery tool policy; permitted tools must pass dispatch checks as well as request advertisement.

The production dispatch policy accepts the explicit priced snapshot, default tier, standard endpoint, and text/function requests. It reserves the provider's full 400,000-token context limit plus the configured output allowance; it does not claim to count local prompt tokens. Remote conversation/reference items and media or provider-native tools are rejected because they defeat the local record or priced contract. Returned policy violations stop subsequent admissions. Missing usage, interrupted streams and uncertain persistence keep conservative holds. Shutdown deadlines do not wait indefinitely for a blocked ledger write, and late state cannot rewrite a sealed provider archive.

If admission is denied after a reservation is acknowledged, the gateway retains the operation id and prepared request hash. Reconciliation can release that reservation when the sealed archive verifies that dispatch never began. A gateway-wide policy violation still invalidates that evidence and retains the hold. Settlement requires a durable dispatch marker both when written and when the ledger is reopened. HTTP 429/5xx responses without usage remain unknown; the error status alone does not establish zero cost. Provider-error accounting resolution remains tracked under [RW-4](remaining-work.md).

The controlled gateway sends `x-should-retry: false` on relayed responses and its own errors. Before reserving or forwarding it also rejects SDK retry headers other than an absent header or a single `x-stainless-retry-count: 0`. This prevents automatic replay even when a response was lost before the SDK received the no-retry header. The gateway's upstream HTTP transport already uses zero retries. The pinned Omnigent factory does not forward authored retry settings, so enforcement is at the controlled gateway; the native runtime is unchanged. The SDK header is a trusted-runtime diagnostic, not an authenticated logical-call identity: a caller omitting it still consumes a fresh round and reservation. New archives retain the observed headers and declare `reject_automatic_retries_v1`; the independent verifier rejects contradictory retry/dispatch records. Legacy archives remain readable without claiming this policy.

Pilot preparation persists an empty registry before preparing the comparison, so failure in comparison preparation allows another attempt in a new directory using the same ledger. Keep both the ledger and registry. Failure while initially persisting the registry itself still requires investigation; missing retained budget state must never be recreated as fresh funds.

The ledger’s .pilots.json registry records every prepared comparison; retain that registry and the registered directories alongside the ledger. Initial snapshots and case checkpoints preserve observed operations and authorization history. Before preparing or executing another case, and again for the exact final reporting snapshot, the pilot checks all registered comparisons under the shared ledger lock. Witnessed spending must remain present, and independently verified archived requests must match ledger rows and settlement evidence. Earlier prepared revisions therefore also see later spending. This detects ordinary rollback or accidental replacement against available host witnesses; the local files are not a signed, external accounting authority.

The [budgeted strategy runner](strategy-optimization.md#run-search-with-the-shared-model-budget) now registers search preparations in the same registry and uses that ledger for research, coding proposals and private final evaluation. Pilot and search checks both validate the retained search evidence, so an older pilot cannot ignore later recorded search spending. Keep private final directories local and outside proposer feedback. The new [draft search configuration](../examples/evaluation/search-run.proposed.json) preserves the pending authorization status; it does not approve a model run.

If a process stops with an unresolved case, inspect and stop its runtime before `python -m research_harness.evaluation.pilot recover <pilot> <arm> <case-id> --reason '<observed interruption>'`. Recovery takes the controller lock, attaches surviving provider/research evidence and reconciles the sealed archive; it never replays model work. Finalization uses `python -m research_harness.evaluation.pilot finalize <pilot>`. Its pilot-report.json distinguishes per-comparison settled/held amounts, settlement completeness and the observed balance of the shared ledger. Every attempted case remains in the denominator. A total budget can exhaust before both arms finish; the starting balance and failures remain visible rather than becoming a quality claim.

The [budgeted runtime fixture](../examples/evaluation/budgeted_runtime_fixture.py) exercises both actual adapters using synthetic HTTP responses. The [archived acceptance](../examples/evaluation/evidence/budgeted-runtime-2026-09-08/acceptance.json) verifies eleven reserved-and-settled requests, valid proposals in both arms and zero remaining holds. Fixture mode requires an explicit MockTransport and fixture.invalid endpoint; it cannot silently become a live provider run. Fixture reservations and prices are synthetic accounting, not money spent.

Provider access and the pending spending decision remain prerequisites for a live compatibility run. Human case review and a measured baseline are still required before running the [strategy optimization phase](omnigent-integration-plan.md). No fixture counters establish provider cost or research performance.
