# Research Harness with Omnigent: implementation plan

Accepted plan: September 8, 2026. Scope: build an interactive research agent on the existing Research Harness backend, using Omnigent for execution and the interface, followed by independently evaluated strategy optimization. Offline follow-ups from the September 10 review are in progress. Measured evaluation still requires independent benchmark review, provider access and the pending spending decision. The local workflow has fixture and browser acceptance, while live evaluation and optimization remain outstanding; the full scope and acceptance criteria are unchanged.

Track milestone status, ownership, validation evidence, and agent handoffs in the [shared goal](goals/omnigent-integration.md). All development agents should follow the repository's [coordination instructions](../AGENTS.md).

Implementation update: the [shared research service and fourteen MCP tools](research-service.md) now cover discovery, jobs, exports, and recovery. The normal Omnigent server/runner and [browser workflow](omnigent-ui-acceptance.md) have acceptance evidence with synthetic model responses and real local collection workers. The [compatibility spike](omnigent-compatibility.md), budget-policy fixture, [independent evaluator with twenty development cases](research-evaluation.md), and [controlled runtime fixture comparison](controlled-comparison.md) have landed. Live model testing, independent human review, matched comparisons with real model responses, and optimization remain outstanding.

The first deliverable is one complete workflow: a user supplies a research brief, the agent discovers and tests sources, the backend produces a validated pipeline proposal, and the user can request collection, inspect the evidence, and return to the case later.

Use source discovery as the initial task because its tools, storage, and validation already exist. Company analysis, forecasting, workbook editing, and specialist agents can build on this foundation after the first workflow works.

## 1. Architecture and ownership

Omnigent owns the conversational agent loop. Research Harness exposes individual domain operations through MCP. The existing direct discovery CLI remains a baseline and calls the same extracted service.

~~~mermaid
flowchart TD
    U[User in Omnigent UI] --> A[Research agent]
    A --> M[Research MCP adapter]
    M --> S[Research service]
    S --> Q[Search provider]
    S --> C[Source inspection and connector probes]
    S --> V[Proposal validation and pipeline registry]
    S --> J[Collection and export jobs]
    C --> D[Evidence and metadata storage]
    V --> D
    J --> D
    D --> E[Independent evaluation]
    E -. later .-> O[Meta-Harness optimizer]
    O -. candidate strategy .-> A
~~~

| Component | Responsibility | Implementation decision |
| --- | --- | --- |
| Omnigent | Conversation, agent execution, streaming, session UI, runtime policies | Use an authored agent bundle and supported extension interfaces |
| Research service | Source inspection, probes, verified proposals, pipeline operations | Extract reusable operations from the existing discovery class |
| Research storage | Questions, evidence, proposals, pipeline versions, operation results | Extend the existing SQLite/Postgres and raw-file/S3 backend |
| MCP adapter | Typed tool requests, execution context, structured results | Thin wrapper around the service; local stdio first |
| Search provider adapter | Execute searches and record what the provider returned | One provider initially, with a stable receipt format |
| Evaluator | Measure independently specified coverage, correctness, cost, and usefulness | Separate from the agent and editable strategy |
| Meta-Harness optimizer | Propose and compare strategy changes | Add after the integration has a measured baseline |

Omnigent supports agent definitions and MCP integrations, including a research example using search and page-reading tools. These are the intended integration points. [Agent specification](https://github.com/omnigent-ai/omnigent/blob/main/docs/AGENT_YAML_SPEC.md), [research example](https://github.com/omnigent-ai/omnigent/tree/main/examples/deep-research).

Run Omnigent in a separate environment from the research package. Its Python and SDK dependency constraints differ from the current project. MCP provides a process boundary without forcing both dependency trees into one installation.

The pilot is local and single-user. Its service context still binds each operation to the correct question and discovery. Shared deployment will add authenticated user/workspace scope before exposing the service remotely.

## 2. What the first user experience should do

Use the existing trade-policy brief as the initial walkthrough:

1. The user asks for sources to monitor U.S.–China export policy and related prediction-market contracts.
2. The application registers the brief, a discovery id, the selected strategy version, and explicit model settings.
3. The agent searches, inspects actual responses, and probes candidate connector configurations.
4. The agent submits a structured proposal. The backend checks the source evidence and returns either validation feedback or saved proposal/pipeline ids.
5. The UI presents the proposed sources, unresolved needs, sample-validation limits, and links to saved evidence.
6. When the user's request includes collection, the agent starts a bounded collection job and reports its result. A discovery-only request ends with the proposal.
7. A later message such as “what changed since the last collection?” retrieves the stored case and run records. It does not depend on the old conversation still fitting in model context.

A follow-up that changes the brief creates a new version or a related question with an explicit link. Reopening the same question preserves its previous proposals and collections.

## 3. Service and tool contracts

All fourteen tools below are implemented as described in the service guide, including collection jobs and exports. Each call uses a service-issued discovery context. The transport binds that context to the session; the agent cannot establish ownership by supplying an arbitrary user id.

| Tool | Main input | Result and behavior |
| --- | --- | --- |
| begin_research | Brief, optional existing question id | Question id, discovery id, fixed scope, remaining operation limits |
| get_research_context | No arguments; host-bound context | Connector capabilities, schemas, prior proposals, saved evidence references, remaining work |
| search_sources | Query and bounded filters | Ranked results plus a stored search receipt |
| inspect_source | URL | Content type, shape or excerpt, capture id, timestamps |
| probe_source | Exact SourceSpec | Probe id, source fingerprint, sample records, errors and limitations |
| get_evidence | Evidence id and bounded range | Stored content and provenance; large bodies can be read in pages |
| submit_proposal | ProposalDraft, operation id | Validation errors or saved proposal/pipeline ids |
| list_pipelines | Bound question context | Candidate definitions, selection status, latest run references |
| start_collection | Pipeline version id, operation id | Durable job id; runs the existing collection engine |
| get_job / cancel_job | Job id | Progress, terminal status, artifacts; cooperative cancellation |
| list_jobs | Bound question context | Jobs created by this question's discoveries |
| export_observations | Pipeline version, explicit cutoff, optional kind, operation id | Durable export job reference |
| read_export | Job id, artifact and bounded range | Verified observations or manifest content |

The MCP server writes protocol messages to stdout and diagnostic logs to stderr. Tool results use a common envelope: operation id, status, structured data, evidence references, error code, retryability, and remaining limits.

**Search evidence needs an explicit bridge.** The original discovery loop recognized native web-search events and accumulated observed URLs in memory. The extracted service now persists that evidence trail. Route the pilot's searches through search_sources, which calls the configured provider and stores its actual response. Inspecting or probing a URL records separate evidence of access.

Do not accept an agent-written list of “observed URLs” as proof. A search result establishes discovery; a page capture establishes access; a successful probe establishes sampled structural compatibility. The compiler loads receipts and probes from storage when checking a submitted proposal.

For the first provider, use a configurable MCP search endpoint behind this wrapper, with Keenable as the initial integration candidate from Omnigent's example. Confirm its current schema and availability during setup, and record fixtures for tests. Only search queries and filters need to reach that provider. [Example provider configuration](https://github.com/omnigent-ai/omnigent/blob/main/examples/deep-research/tools/mcp/keenable.yaml).

## 4. State, evidence, and execution

Reuse question, discovery, proposal, pipeline-version, collection-run, and raw-capture records. Add the minimum records needed for external runtimes:

| Record | Required information |
| --- | --- |
| Runtime binding | Omnigent session id, question/discovery ids, runtime version, adapter, model, strategy hash |
| Discovery receipt | Operation type, discovery id, provider/request identity, source fingerprint where relevant, artifact reference, time, result |
| Operation ledger | Idempotency key, request hash, state, result reference, job id, timestamps |
| Trace manifest | Source/configuration hashes, event stream references, usage, completion/failure status |

Numbered migration 3 adds durable discovery state; migration 4 adds durable jobs for SQLite and Postgres. Probe receipts, observed sources, operation budgets, and job identities survive process restart. The Omnigent bundle records session binding, source/configuration digests, and hashed event exports. Registered datasets use question namespaces so identical pipeline names cannot mix different questions' observations.

An operation id identifies one intended action. Retrying the same id and payload returns its stored result; reusing the id with different arguments fails. Intentionally fetching fresh data uses a new operation id. Source fingerprints alone must not deduplicate separate observations across time.

Collection jobs use durable status and the existing pipeline writer locks. An interrupted or uncertain operation is reconciled against its underlying collection run before another execution starts. Cancelling stops further work and records interruption; any already published source snapshots retain their real publication status.

The domain store remains authoritative for research state. Omnigent conversation history is useful context, and session reconnection is not proof that a collection completed.

## 5. Implementation milestones

Estimates assume one engineer familiar with the code, existing provider access, and a local pilot. Plan roughly two to three engineering weeks through milestone 5. Model-backed runs have a separate, as-yet-unspecified spend budget.

| Milestone | Work | Completion check | Estimate |
| --- | --- | --- | --- |
| 0. Compatibility spike | Pin Omnigent, select adapter/model, register a small agent, call a fixture MCP tool, inspect events | Instructions and tool arguments arrive intact; structured results, interruption, and usage behavior are recorded | 0.5–1 day |
| 1. Extract research service | Separate schemas and source/proposal operations from the model loop; persist discovery state; add migrations | Existing CLI behavior passes its tests, and a restarted service can validate a previously probed source | 2–3 days |
| 2. Add research MCP tools | Implement typed wrappers, search receipts, context binding, evidence reads, and operation limits | A protocol client completes discovery against recorded fixtures and rejects mismatched proof/configuration pairs | 1–2 days |
| 3. Connect the Omnigent agent | Add the bundle, instructions, session binding, and trace export | A brief produces a saved validated proposal through the real Omnigent/MCP path | 1–2 days |
| 4. Finish the research workflow | Add collection jobs, cancellation, exports, saved-case lookup, and recovery | The user collects, disconnects, reconnects, and inspects the same run without duplicated publication | 2–3 days |
| 5. Evaluate the pilot | Build reviewed briefs and expected outcomes; run baseline and integration comparisons; fix failures | Evidence correctness, source usefulness, cost, and recovery are measured on held-out cases | 2–3 days |
| 6. Add strategy optimization | Connect candidate execution, full search history, selection, and final test evaluation | Reproducible comparison of a baseline and code-generated strategies with test feedback excluded from search | Additional 3–5 days |

### Milestone 0: pin the runtime before building around it

Use the inspected Omnigent commit be042b390e293a8d586cbb7e403a2ce0ce38fc62 as the starting reference; select and lock the tested release or commit during the spike.

Proposed first executor: openai-agents with the research project's existing gpt-5.4-mini model. This preserves the underlying model for an eventual controlled comparison. It remains valid for authored YAML agents in the inspected registry, although it is omitted from the generic runtime picker. Confirm the registered research agent launches in the UI. A failed preflight should produce a deliberate adapter/model decision rather than a silent fallback. [Registry](https://github.com/omnigent-ai/omnigent/blob/main/omnigent/harness_plugins.py).

Measure how the selected adapter delivers instructions, restores history, reports usage, and emits tool events. Save available request inputs and runtime metadata; do not claim access to hidden model reasoning or unexposed internal prompts. Omnigent explicitly represents adapter differences in its capability model. [Capabilities](https://github.com/omnigent-ai/omnigent/blob/main/omnigent/harness_capabilities.py).

### Milestones 1–2: establish the domain boundary

Move ProposalDraft and its related models into a provider-neutral module. Extract inspection, probing, evidence lookup, proposal compilation, and persistence into a service. Keep the existing direct model loop as one client of that service.

Omnigent's research agent should call these individual operations. Wrapping the entire existing discovery agent in one tool would leave the important decisions and traces inside a second model loop.

Add an optional MCP dependency to the research package and a proposed CLI entrypoint, rh mcp serve. Start with stdio launched by the Omnigent bundle. Bind it to a local workspace/run context and reconstruct that binding on restart.

### Milestones 3–4: make the workflow usable

Build a research agent bundle with task instructions, an explicit model, and the MCP configuration. Start with the research tool set. Return short observations plus evidence ids, allowing the agent to retrieve more detail when useful.

A proposal is complete only when submit_proposal returns saved domain ids. Persist validation failures and let the agent repair them. A conversational claim of completion cannot create a pipeline.

Use Omnigent's existing chat and artifact surfaces for the pilot. Render a source table, evidence references, coverage gaps, and collection status. Add a custom research dashboard only if the pilot exposes a concrete UI limitation.

Proposed initial limits per discovery are 8 searches, 16 inspections, 12 probes, and a 10-minute deadline. Enforce operation counts in the service and the deadline in the controller. Record retries separately so transport and model retries cannot silently multiply the workload.

Log model tokens, wall time, and search-provider usage separately. Configure spending controls against the pinned runtime and validate their actual behavior. The existing Omnigent cost policy can be configured with different blocking/downgrade behavior, and accounting checks do not guarantee zero overshoot within an in-flight turn. [Cost policy](https://github.com/omnigent-ai/omnigent/blob/main/omnigent/policies/builtins/cost.py).

### Milestone 5: evaluate what the user receives

Start with 20 reviewed search/development briefs and 10 held-out briefs. Group similar topics and source families to reduce leakage; include unsupported sources, irrelevant but parseable endpoints, pagination, and timestamp mistakes.

The [maintained independent artifact evaluator](research-evaluation.md) adapts the earlier research-discovery prototype into this package. Its accepted-source rules are a useful starting point, but reviewers must account for valid alternative sources. Retain qualitative review for relevance, stated limitations, and useful unanswered questions.

Keep three measurements separate:

- **Domain correctness:** matching probes, citations backed by recorded evidence, complete valid ingestion, correct time cutoffs.
- **Research usefulness:** externally defined requirement coverage, relevance, historical/freshness suitability, and honest gaps.
- **Efficiency:** model tokens, provider cost, elapsed time, and repeated work.

Run the direct baseline and Omnigent path with the same model, search provider, fixtures, and budgets before attributing differences to integration. The direct CLI supports the shared provider with --search-provider mcp, plus explicit --settings and --instructions files; native search remains a separate configuration. The [controlled comparison controller](controlled-comparison.md) freezes shared semantic instructions, generation settings, fixtures, implementation bytes, and failure records. Runtime-specific prompt assembly and tool schemas remain part of the treatment.

The pinned Omnigent normal runner does not forward its authored output-token or round limits. Controlled comparisons therefore use a host Responses gateway for both arms. It applies the output/reasoning settings and per-case call/deadline limits before forwarding, retains incoming and forwarded requests, restricts function tools to discovery, and rejects auxiliary compaction requests locally. The pinned factory also omits the authored SDK retry policy. The controlled gateway sends non-retryable responses and rejects SDK retry attempts before reservation or dispatch, retaining the observed retry headers for independent verification. Its provider transport uses zero retries; every other admitted request consumes its own round and reservation. This is a controlled adapter variant, distinct from the native Omnigent configuration. Request limits and Omnigent's between-turn cost policy do not constitute an exact dollar cap; provider access and a spending budget remain prerequisites for paid comparison.

The gateway now seals an execution-bound archive, and an independent verifier supplies complete tokens or explicit lower bounds from raw provider responses, including failed cases. A [draft pilot budget](pilot-budget.md) and durable reservation ledger are implemented. The [budgeted pilot runner](../src/research_harness/evaluation/pilot.py) now connects reservations to dispatch and enforces the priced snapshot/endpoint/tier and token bounds. It preserves unknown holds and checks spending against archived provider evidence and ledger witnesses. Recording external spending approval and configuring provider access remain prerequisites for paid execution. The draft's $10 ceiling is a proposal, not an approved or guaranteed full-benchmark budget.

The ten-case test set is an initial acceptance check. It is too small to justify broad performance claims; larger or repeated evaluations should follow the observed variability.

### Milestone 6: optimize the research strategy

Implementation update: the [strategy integration](strategy-optimization.md) provides isolated Python execution, durable observation sessions shared by direct discovery and Omnigent/MCP, controlled-gateway context selection, and a development archive with a durable private-final phase. Context selection preserves every user message and the entire active turn; the actual normal runner verifies removal of older completed interactions after a real user follow-up. Comparison cases freeze code, limits and session identity, and the independent development bridge copies complete recorded execution artifacts into feedback while keeping benchmark predicates and evaluator source private. The bounded Responses coding proposer, filesystem workspace, three-iteration/two-candidate search controller and private final evaluator are now implemented. The [combined runtime acceptance](../examples/evaluation/evidence/combined-strategy-search-2026-09-09/acceptance.json) verifies all three iterations, seven development evaluations and seven isolated final evaluations through actual Omnigent/MCP and Docker. Selection and permanent proposer revocation precede final package creation; completed final retries dispatch no further work. All responses are synthetic, so these results do not satisfy the measured-baseline gate for model-mode search. A separate ten-case private held-out draft has passed twenty offline service/evaluator checks; all cases remain authored pending human review.

The [budgeted search runner](strategy-optimization.md#run-search-with-the-shared-model-budget) now wires the actual proposer, research executor and private final evaluator to the pilot's retained ledger and registry. Its [runtime acceptance](../examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/acceptance.json) verifies 125 synthetic requests reserved before dispatch and settled from raw evidence, with no remaining holds. That September 9 checkpoint passed 1,004 tests. Source/model responses remain authored fixtures; the September 10 review follow-ups and measured baseline/optimization remain outstanding.

Freeze the backend, evaluator, task split, model, and tool budgets. Candidate changes live in a versioned Python strategy module for context selection, source ranking, observation formatting, and stopping recommendations, plus its agent instructions.

Verify the necessary execution hook in the selected adapter before promising deeper changes to its internal orchestration. A prompt-only experiment should be reported as prompt search.

Evaluate the baseline first, then start with 3 iterations and 2 candidates per iteration. Keep source snapshots, complete available search traces, scores, model configuration, errors, and usage for every candidate. Let the proposer choose which prior artifacts to inspect.

Select on development performance with quality/cost tradeoffs among candidates with positive macro quality and no failed development cases. Retain the raw frontier, scores and exclusion reasons for inspection. The final phase checks the same eligibility rule for the selected candidates and the required original baseline before any private evaluation begins, including for older frozen selections. Freeze selection and disable further search for that run before evaluating held-out tasks, including when final evaluation is interrupted. The coding proposer reads the development feedback workspace. Generated strategy code executes separately on supplied events and returns validated ranking, formatting, context-selection and stopping decisions; the host retains research-tool access and fixed limits. Held-out data and authoritative evaluators stay outside proposer and candidate access. This applies the paper's search procedure to the domain after the integration is measurable. [Meta-Harness](https://arxiv.org/html/2603.28052v1).

## 6. Proposed repository changes

All paths below are proposed additions or refactors inside researchharness:

~~~text
src/research_harness/
  discovery.py                    existing direct model loop, using the shared service
  discovery_models.py             provider-neutral proposal schemas
  services/research.py             inspection, probes, proposals, case lookup
  services/search.py               provider wrapper and search receipts
  services/jobs.py                 collection/export execution and recovery
  mcp/server.py                    tool definitions and transport
  integrations/omnigent.py         session binding and runtime event adapter
  evaluation/                     benchmark loading, scoring, run comparison
  strategies/                     isolated candidate execution and projection contracts
  optimization/                   development archive, selection and private-final lifecycle

agents/research/
  config.yaml                     authored Omnigent agent
  instructions.md                 research procedure and output expectations
  tools/mcp/research.yaml          local research MCP server

tests/
  test_research_service.py
  test_research_mcp.py
  test_omnigent_integration.py
  test_research_recovery.py
  test_research_evaluation.py

examples/omnigent/                 one brief, expected walkthrough, local setup
~~~

Extend the existing store/registry and migrations rather than introducing another authoritative research database. Omnigent's own session database remains separate.

## 7. Acceptance and later expansion

The first release is ready for a pilot when the trade-policy walkthrough works through the UI, a restart preserves its evidence and operation history, and malformed proposals cannot bypass validation.

Required checks include changed configuration after probing, an unrelated run's probe id, unobserved citations, duplicate tool delivery, interruption during collection, provider failure, operation-budget exhaustion, and concurrent writers. Run the current core suite plus service/MCP integration tests. The [local Postgres/MinIO acceptance](backend.md#local-shared-storage-acceptance) now exercises shared-storage tests and the durable research/job/export workflow. Remote deployments still need their own environment-specific acceptance.

Human verification should include following source references, inspecting an unresolved requirement, asking for a collection, reopening the case, and checking that exported observations trace back to the saved run. Test a second adapter only after this workflow passes on the first.

After the local pilot, the next increments are authenticated HTTP MCP with shared storage, repeat collection and change summaries, then specialist workflows. Multi-user access needs workspace ownership and checks on every domain lookup; an Omnigent login alone does not add those checks to the research backend.

Open choices for implementation are the representative briefs, provider credentials, live-run spending limit, and the eventual shared deployment environment. The defaults above are sufficient to begin service extraction and fixture-based integration work.
