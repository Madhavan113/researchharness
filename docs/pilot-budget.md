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

Unknown or interrupted outcomes hold the entire reservation. Token-based settlement requires explicit completed, independently verified token counts and an evidence hash, within the reserved input/output bounds. A separate operator-reviewed accounting path can resolve a sealed provider error using retained final-charge confirmation, as described below; its token usage remains unknown. Repeated settlement must match the original evidence exactly. Pre-dispatch cancellation requires matching request/evidence hashes and an explicit host assertion that every dispatch path was checked. Caller-reported authorization metadata neither verifies approval nor sends a request.

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

If admission is denied after a reservation is acknowledged, the gateway retains the operation id and prepared request hash. Reconciliation can release that reservation when the sealed archive verifies that dispatch never began. A gateway-wide policy violation still invalidates that evidence and retains the hold. Settlement requires a durable dispatch marker both when written and when the ledger is reopened. HTTP 429/5xx responses without usage remain unknown; the error status alone does not establish zero cost.

The controlled gateway sends `x-should-retry: false` on relayed responses and its own errors. Before reserving or forwarding it also rejects SDK retry headers other than an absent header or a single `x-stainless-retry-count: 0`. This prevents automatic replay even when a response was lost before the SDK received the no-retry header. The gateway's upstream HTTP transport already uses zero retries. The pinned Omnigent factory does not forward authored retry settings, so enforcement is at the controlled gateway; the native runtime is unchanged. The SDK header is a trusted-runtime diagnostic, not an authenticated logical-call identity: a caller omitting it still consumes a fresh round and reservation. New archives retain the observed headers and declare `reject_automatic_retries_v1`; the independent verifier rejects contradictory retry/dispatch records. Legacy archives remain readable without claiming this policy.

Pilot and search preparation persist `<ledger>.initializing.json` with the exact empty ledger and its hash before creating the ledger/registry pair. Under the shared lock, a retry completes that pair only if all surviving files still match the retained initial state. The intent is removed and the directory synced before run preparation begins. This covers interrupted writes and process death during initial creation; retry preparation in a new output directory with the same configuration and ledger path. Keep the intent if failure leaves it behind. Changed balances, authorization or registered runs are refused. An established ledger or registry missing without this intent must never be recreated as fresh funds.

The ledger’s .pilots.json registry records every prepared comparison; retain that registry and the registered directories alongside the ledger. Initial snapshots and case checkpoints preserve observed operations and authorization history. Before preparing or executing another case, and again for the exact final reporting snapshot, the pilot checks all registered comparisons under the shared ledger lock. Witnessed spending must remain present, and independently verified archived requests must match ledger rows and settlement evidence. Earlier prepared revisions therefore also see later spending. This detects ordinary rollback or accidental replacement against available host witnesses; the local files are not a signed, external accounting authority.

The [budgeted strategy runner](strategy-optimization.md#run-search-with-the-shared-model-budget) now registers search preparations in the same registry and uses that ledger for research, coding proposals and private final evaluation. Pilot and search checks both validate the retained search evidence, so an older pilot cannot ignore later recorded search spending. Keep private final directories local and outside proposer feedback. The new [draft search configuration](../examples/evaluation/search-run.proposed.json) preserves the pending authorization status; it does not approve a model run.

If a process stops with an unresolved case, inspect and stop its runtime before `python -m research_harness.evaluation.pilot recover <pilot> <arm> <case-id> --reason '<observed interruption>'`. Recovery takes the controller lock, attaches surviving provider/research evidence and reconciles the sealed archive; it never replays model work. Finalization uses `python -m research_harness.evaluation.pilot finalize <pilot>`. Its pilot-report.json distinguishes per-comparison settled/held amounts, settlement completeness and the observed balance of the shared ledger. `comparison_accounting_complete` also accepts operator-reviewed final charges, with their operation ids listed separately; `comparison_settlement_complete` still requires complete verified usage evidence. Every attempted case remains in the denominator. A total budget can exhaust before both arms finish; the starting balance and failures remain visible rather than becoming a quality claim.

The [budgeted runtime fixture](../examples/evaluation/budgeted_runtime_fixture.py) exercises both actual adapters using synthetic HTTP responses. The [archived acceptance](../examples/evaluation/evidence/budgeted-runtime-2026-09-08/acceptance.json) verifies eleven reserved-and-settled requests, valid proposals in both arms and zero remaining holds. Fixture mode requires an explicit MockTransport and fixture.invalid endpoint; it cannot silently become a live provider run. Fixture reservations and prices are synthetic accounting, not money spent.

Provider access and the pending spending decision remain prerequisites for a live compatibility run. Human case review and a measured baseline are still required before running the [strategy optimization phase](omnigent-integration-plan.md). No fixture counters establish provider cost or research performance.

## Checking registered run evidence

Registered pilot/search runs must retain their evidence at the recorded paths.
Missing or moved registered files raise `LedgerEvidenceError` before further
budgeted work. Restore the original files at those paths, then retry the check;
do not remove registry entries or reset the ledger to bypass it. This error does
not discard charges or release reservations.

Repeated checks reuse successful gateway proofs and artifact hashes only while
their complete membership, POSIX metadata and verification controls match the
[bounded memo contract](strategy-optimization.md#reusing-verified-artifacts).
Current expected hashes, manifests, journals, budget witnesses and the live
ledger are still checked on every operation. A reused raw proof is compared
against the current ledger snapshot, so it cannot hide ledger rollback or a
changed reservation. Independent gateway audits, reconciliation and reviewed
provider-accounting verification retain their fresh full verification paths.

## Record reviewed accounting for a provider error

This offline host command records a separately reviewed provider confirmation for one sealed HTTP error. It makes no provider request, cannot reopen a completed operation for dispatch and does not grant spending permission. The software verifies the local evidence and ledger binding; the operator is responsible for authenticating the external confirmation and its final charge. An error response, absent usage, an agent assertion or an aggregate cost report is insufficient. OpenAI's [Costs API](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs) returns time-bucket aggregates, so those totals alone cannot establish one failed request's charge.

Retain an operator-authored JSON file with these fields from `ProviderAccountingConfirmation`:

- `provider_request_id`: the captured server request id, matching the provider confirmation exactly.
- `confirmed_cost_usd`: the final charge as an exact decimal string; zero requires explicit no-charge confirmation.
- `currency`: `"usd"`; `final_charge_confirmed`: `true` only after that final charge is confirmed.
- `reference` and `reviewed_by`: the external confirmation reference and the operator who reviewed it.
- `provider_evidence`: the retained confirmation text, including the provider request id; `provider_evidence_sha256`: the SHA-256 of those exact UTF-8 text bytes.

~~~sh
python -m research_harness.evaluation.dispatch_budget record-provider-accounting \
  /path/to/shared-ledger.json \
  /path/to/case/gateway/archive.json request-0001 \
  --confirmation /path/to/reviewed-confirmation.json
~~~

The archive must independently verify a completed capture of a JSON HTTP 400–599 error with a nonempty error message, a unique captured server request id and an acknowledged, durable dispatch binding. Partial responses, missing or ambiguous ids, contradictory response fields and invalid archives retain their holds. The command binds the confirmation to the exact request, sealed evidence hash and ledger operation, rounds the charge up to nanodollars and rejects charges exceeding the reservation. Duplicate provider-request confirmations across operations and conflicting replays are refused. Exact replay returns the original terminal record.

Successful accounting settles the reservation at that confirmed charge while keeping token usage unknown. Run normal pilot finalization or search reconciliation afterward to refresh accounting views; the original archive and case witnesses are preserved. The retained confirmation belongs with the host ledger, outside research tools, candidate access, optimization feedback and Git. No provider confirmation has been recorded for real spending in the checked-in evidence; all regression confirmations are authored fixtures.
