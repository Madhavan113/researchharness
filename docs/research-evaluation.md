# Independent research discovery evaluation

The maintained evaluator grades saved Research Harness source proposals against requirements supplied by a reviewer. It does not use the agent's own needs, coverage mappings, or claim that discovery is complete to determine coverage.

This is the evaluation infrastructure for [milestone 5](omnigent-integration-plan.md). It includes independent source scoring, controlled comparison manifests, 20 authored development cases, and a separate authoritative workflow checker. Verification uses synthetic fixtures and the real services/collection engine with mocked HTTP. Independent human review, held-out briefs, live model comparisons, combined workflow comparisons, and the strategy optimization runner remain separate work. The tests do not establish a model performance improvement.

## Run

From the repository root:

~~~sh
uv run python -m research_harness.evaluation /path/to/requirements.json /path/to/discovery-a /path/to/discovery-b
~~~

Each discovery directory is a completed, frozen run from the trusted application or evaluation controller. The evaluator reads these artifacts without modifying them:

| File | Use |
| --- | --- |
| proposal.json | Saved candidates, exact source configurations, and reported model token usage; required |
| trace.jsonl | Native search results, inspection/probe results, and service receipt events; required |
| probes.json | Successful sampled probes and configuration fingerprints; necessary for ready candidates |
| pipeline.json | Compiled pipeline checked against all ready source configurations; necessary when any source is ready |
| receipts.json | Optional persisted service receipts, including search results and discovery ownership |
| runtime usage export | Optional host-owned discovery-only usage JSON and its immutable hashed event snapshot; supplied explicitly or at omnigent/runtime-usage.discovery.json beneath the artifact directory |
| gateway archive | Optional host-bound archive.json and its complete request/response inventory; supplied through the controlled comparison or score API |

The command prints a JSON object containing results and the quality/token Pareto frontier. Unreadable or malformed inputs cause exit code 2. Inconsistent evidence produces a result with status=invalid and quality=0; consumers must inspect this status rather than treating a successful command invocation as a passing research run.

Python callers can use the same API:

~~~python
from pathlib import Path
from research_harness.evaluation import frontier, score

result = score(Path("artifacts/discovery-a"), specification)
eligible_results = frontier([result])
~~~

## Specify expected coverage independently

Write a specification for one brief before inspecting candidate performance. Each requirement accepts one or more independently reviewed source predicates:

~~~json
{
  "requirements": [
    {
      "id": "official-policy",
      "weight": 2,
      "any_of": [
        {
          "url": "https://agency.example/notices.json",
          "connector": "json",
          "items_pointer": "/items",
          "id_pointer": "/id"
        }
      ]
    }
  ]
}
~~~

This URL is illustrative. Use actual reviewed sources for a benchmark, and include valid alternatives so the evaluator does not penalize good sources merely because a reviewer omitted them.

Requirement ids must be unique, and weights must be finite and positive. Each source alternative requires an exact HTTP(S) URL and a connector. Objects match a subset of fields. Evaluator version 2 treats `required_pointers` as an unordered required subset: a predicate of `["/form", "/filed_at"]` accepts either order and additional required pointers. Its values must be a list of JSON-pointer strings. Other lists, including ordered query parameters, and scalar values still match exactly, including their types. Use published_pointer, required_pointers, or pagination predicates when those configuration details determine relevance. Unknown top-level SourceSpec field names are rejected to catch misspelled requirements.

An independent requirement may additionally allow an evidenced gap. For example, `{"id":"history","any_of":[],"gap_any_of":[{"url":"https://agency.example/history.json","status":"needs_access","status_code":401}]}` specifies an inaccessible historical archive without pretending it is a usable source. `any_of` may be empty only when there is at least one valid gap alternative. A fulfilled source alternative always takes precedence over gap credit for the same requirement.

Gap predicates require an exact URL, candidate status and HTTP status. Access gaps allow 401/403; unavailable-source gaps allow 404/410. Connector gaps require HTTP 200 and `content_type` equal to `application/pdf`, `application/vnd.ms-excel` or `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`. They describe currently unsupported document extraction; other formats need a reviewed extension to this contract. Independent predicates and weights remain host-only evaluation inputs, separate from the authored fixture selection and candidate-facing brief.

## What the score proves

Ready configurations are parsed through the current SourceSpec model and matched against its canonical fingerprint. This normalizes omitted defaults exactly as the application does. A fingerprint must reference a uniquely identified successful probe with a positive integer record count. The compiled pipeline must pass PipelineSpec validation and contain exactly the same normalized ready sources. Disabled sources, changed configurations, missing probes, duplicate identifiers, and inconsistent pipelines make the result invalid.

Every candidate citation, including citations on unsupported sources, must appear in stored observations. Supported observations are completed native web_search_call results, inspection captures, probe captures or successful probe reports, and service search/inspection/probe receipts. Search receipts establish observation from their results[].url fields. Agent-written proposal.observed_urls, assistant citation annotations, arbitrary tool names, and a failed request's configured URL alone do not establish observation.

The service format is supported both in receipts.json as records with kind, discovery_id, and payload, and in trace.jsonl as service_receipt events with kind, discovery_id, and result. When proposal.registry.discovery_id exists, these receipts must match it. Explicitly mismatched probe discovery ids are also rejected. Older unscoped probe files remain supported as trusted exports; the evaluator cannot reconstruct missing ownership metadata.

Gap credit has a stricter evidence requirement: a nonempty discovery id in the saved proposal must match a service inspection or probe receipt. Captures must retain their id, exact URL, HTTP status and body SHA-256. Connector gaps also require the inspection's content type, normalized for case and optional MIME parameters. Search results and unscoped legacy reports can establish observation but cannot establish a blocker. Conflicting exports for the same capture invalidate the result; contradictory or incomplete observations at the gap URL prevent gap credit. Repeated receipt/trace copies are deduplicated. This checks trusted runner exports, not the authenticity of arbitrary files or long-term availability; content-type metadata does not prove semantic extraction correctness.

| Measurement | Meaning |
| --- | --- |
| status / errors | Whether artifact evidence and ready configurations satisfy these consistency checks |
| requirement_recall | Weight of independently covered requirements divided by all requirement weight |
| source_precision | Relevant verified sources divided by all ready sources |
| gap_recall | Weight of otherwise unfulfilled requirements with a correctly evidenced gap divided by all requirement weight |
| research_recall | Data requirement recall plus half of gap recall |
| research_precision | Relevant verified sources plus distinct relevant gap claims divided by all ready sources and nonready claims |
| quality | Harmonic mean of research recall and research precision; zero for any invalid result |
| total_tokens | Reported input plus output tokens, or null when either count is missing or invalid |
| completed_token_lower_bound | Verified completed-response usage, retained separately when auxiliary/compaction usage or interruption prevents a complete total |
| coverage | Each independent requirement and the source ids that satisfy it |
| gap_coverage | Each requirement's evidenced-gap status and indexes into the nonready claim list |

Recall and precision remain diagnostic when a result is invalid; quality is the selection gate. A correctly evidenced gap earns half the recall credit of a fulfilled requirement, with `gap_credit=0.5` recorded in the result. A pure gap can therefore earn quality 2/3 while its data requirement recall remains zero. Repeating the same URL/status claim never increases credit and lowers research precision. A claim with no independently permitted, matching blocker earns no gap credit. The half-credit choice is an authored scoring convention requiring human review before measured evaluation.

The frontier maximizes quality and minimizes known model tokens. It retains ties and tradeoffs, excludes dominated results, and excludes invalid results or unknown costs. It rejects results carrying different specification hashes or evaluator versions; an absent evaluator version is treated as legacy version 1. Existing archived scores remain historical. Re-evaluate both arms under the same version and specification for a new comparison. Compare runs for the same brief with the same model, search provider, fixtures, and budgets; this function does not verify those experiment controls. Tokens alone do not measure money, provider/search usage, wall time, or recovery cost.

Reports include the canonical specification SHA-256 and the hashes of the exact artifact bytes parsed, including receipts.json when present. Hashes support reproduction and change detection. The report does not authenticate file provenance: the evaluation controller must prevent candidate code from rewriting authoritative artifacts.

Omnigent saves a proposal before its final turn usage arrives. That proposal can therefore have empty usage even after the turn completes. Supply its separate host export to the single-case CLI with --runtime-usage /case/omnigent/runtime-usage.discovery.json, or call score(artifacts, specification, runtime_usage=usage_path). The CLI flag accepts exactly one artifact directory. A benchmark CaseRun can name the same file through its runtime_usage reference; it cannot override completed token counts numerically.

The version-1 usage export binds a question/discovery id, phase, selected response ids, totals, and completeness flags to an immutable content-hashed source event file. The evaluator requires matching domain ids and phase=discovery, checks each selected response against the actual SSE usage.model and counters, deduplicates repeated response events, and rejects conflicting counts or hashes. Each selected terminal event must also have phase=discovery in its archived wrapper. Missing phase tags and selected followup responses make usage unverified and leave its token objective unknown. The controller writes these phase tags while recording events; they are not part of the provider's original SSE payload. Hashes verify consistency with that controller-owned record, not protection against a controller or candidate able to rewrite both files.

The pinned Omnigent export marks complete=false and auxiliary_usage_unknown=true: compaction/auxiliary calls are not fully exposed. Its completed-response lower bound is useful, but total_tokens remains null and the run stays outside the quality/token frontier. An interrupted run also cannot establish a complete total. Only an explicitly complete export with all auxiliary usage accounted for, consistent events, and no interruption can supply the total token objective. Invalid usage leaves source quality unchanged and records the verification errors while keeping cost unknown.

The controlled comparison can instead select the [gateway verifier](../src/research_harness/evaluation/gateway_usage.py). It independently parses raw JSON/SSE/gzip responses, checks the sealed archive's full inventory and hashes, and reconciles provider IDs, counters, controls and dispatch records. The expected execution ID, case, runtime, discovery phase and task/control hash come from the host; generation settings and all operation/request/deadline budgets must also match the frozen comparison. Duplicate or conflicting provider response IDs invalidate the archive. Hash consistency is not a signature or an OS isolation boundary.

A valid complete gateway archive supplies total_tokens, even if the research task failed after receiving its responses. A dispatched call with missing or interrupted usage leaves the total unknown and retains the observed completed subtotal. A sealed archive showing no dispatched calls can establish zero provider tokens. Missing cache details remain unknown. The report retains the narrower runtime export separately, but an explicitly supplied invalid gateway archive never falls back to proposal or runtime counters. Discovery scoring rejects workflow/follow-up phase bindings. Standalone callers pass gateway_usage, gateway_binding, expected_model_settings and expected_budgets to score; the controlled benchmark supplies these automatically.

## Authored development benchmark

The [development manifest](../examples/evaluation/development/manifest.json) pins 20 case files and their synthetic HTTP fixtures by SHA-256. Each case contains a brief, source-family/topic grouping, independent accepted-source predicates, manual review questions, and an explicitly authored fixture selection. Every case is marked authored; none is represented as independently human-reviewed. Endpoints under fixture.example and the records they return are illustrative.

The cases cover export-control notices and inaccessible history; tariff, procurement, outage, and port pagination; parseable but irrelevant customs, energy, and recall feeds; publication versus retrieval/amendment time; stable procurement/filing identities; acceptable vendor-source alternatives; contract metadata plus settlement wording; and unsupported spreadsheets/PDFs. The version-2 package replaces impossible future-JSON placeholders with independent gap predicates and actual captured 401/403 responses or PDF/workbook documents. Every case includes a parseable search distractor outside its source answer set. Formerly trivial cases now include plausible wrong timestamps, current summaries or unrelated publications, while both legitimate vendor alternatives remain acceptable.

Validate the suite and run a complete offline software comparison:

~~~sh
uv run python -m research_harness.evaluation.benchmark check examples/evaluation/development/manifest.json
uv run python -m research_harness.evaluation.fixtures examples/evaluation/development/manifest.json artifacts/evaluation/demo-001
~~~

Choose a new output directory. The fixture runner preserves existing runs and denies HTTP requests absent from the authored fixture map. It executes actual ResearchService search, inspection, probe, and submission operations for two deterministic policies: authored-selection and first-listed-source. Only the authored policy receives the explicit gap selection, and it must inspect or probe those URLs to retain blocker evidence. The naive policy takes the first listed source configuration. It does not perform search ranking; the distractors are available to future discovery runs. It writes 40 case artifact directories, two run.json manifests, and comparison.json. The second policy demonstrates why sample compatibility alone cannot establish relevance or complete source coverage. These policies are software fixtures; neither runs a model or the Omnigent agent. Both the domain fixture runner and subprocess source transport decode binary documents from a strict `body_base64` field, mutually exclusive with the existing text/JSON `body` field.

Model tokens and dollar costs remain unknown for this demonstration. Elapsed seconds measure only the executed fixture workflow. Some correctly documented unsupported/history gaps yield less than full source coverage even for authored-selection; the fixture is not designed to make every result perfect.

The revised twenty-case comparison gives authored-selection macro quality 0.9495238095238095 versus first-listed-source 0.08333333333333333, with twenty strict per-case wins and forty valid runs. Authored data requirement recall remains 0.8416666666666666; its research recall is 0.9208333333333334. These numbers establish fixture discrimination under the revised contract, not model improvement. No search run has yet used the twenty-case package. Archived searches used a one-case scripted runtime fixture whose perfect ties verify orchestration only; those archives are unchanged and cannot support a research-quality claim.

[build_cases.py](../examples/evaluation/development/build_cases.py) reproduces the authored case/fixture files. Editing and rebuilding cases changes the pinned benchmark revision and invalidates old run manifests for comparisons with that revision.

## Controlled comparisons

The comparison module aggregates all briefs in one manifest:

~~~sh
uv run python -m research_harness.evaluation.benchmark compare examples/evaluation/development/manifest.json /path/to/direct/run.json /path/to/omnigent/run.json --axis runtime --out artifacts/evaluation/comparison.json
~~~

The runtime comparison requires identical instruction and optional code-strategy hashes. For an explicitly declared strategy experiment, use --axis strategy; then instructions and code may differ while the runtime stays fixed. Execution type, model and settings, provider and settings, operation/deadline budgets, fixture package, and backend hash must match in either mode. The optimization archive additionally freezes sandbox and strategy limits. It rejects different benchmark revisions and missing or duplicated cases. Every case must appear in every arm, including failed executions; dropping a difficult case cannot improve the denominator.

RunControls, CaseRun, and RunManifest in [benchmark.py](../src/research_harness/evaluation/benchmark.py) define the strict manifest schemas. Their main fields are:

| Field | Requirement |
| --- | --- |
| name / benchmark_sha256 | Unique arm name and the hash returned by benchmark check |
| controls.execution / runtime | fixture or model, plus the actual execution path identifier |
| controls.model / model_settings | Explicit model and all behavior-relevant model settings |
| controls.provider / provider_settings | Search provider identity and settings, including any index/time filters |
| controls.budgets | search, inspection, probe, and deadline_seconds; record any additional controller limits too |
| controls.fixture_sha256 | Combined fixture hash returned by benchmark check |
| controls.strategy_sha256 / code_strategy_sha256 / backend_sha256 | Instruction, optional executable strategy bundle, and domain implementation hashes recorded by the controller; absent code identity retains legacy baseline bindings |
| runs | One CaseRun per case id; completed runs name an artifact directory, failed runs record a failure reason |
| per-run usage | Optional elapsed_seconds, model_cost_usd, provider_cost_usd; unknown values are null; runtime_usage or gateway_usage can reference verified host artifacts; gateway_usage requires execution_id |

Artifact directories and usage references are relative to the run manifest. All manifest paths, including symlinks, must stay within their own package. Gateway token evidence takes precedence when explicitly supplied; otherwise legacy proposal/runtime usage behavior remains available. failed_run_tokens is available only for failed runs without a gateway reference. Failed runs and unreadable proposals can still retain independently verified gateway usage. The comparison also checks reported models against proposal metadata and observed provider identities against saved search receipts. Any observed provider that conflicts with the configured provider rejects the comparison. Controls remain trusted host records: identical metadata alone does not authenticate execution or prove access isolation.

Each case's control_evidence separates configured controls from observed artifact metadata. A completed artifact with no observed search provider remains in the comparison with search_provider.status=unverified, observed=[], and reason=no_observed_search_provider. This does not claim that a search occurred or that none was attempted. When execution fails or artifacts cannot be read, observed=null and reason=no_artifact_evidence distinguish the absence of artifact evidence. Matching provider receipts produce status=matched. The current service requires a search receipt before submission; the empty-provider regression also covers the independent evaluator's authored probe-only artifact contract.

Reports keep artifact consistency, independent source usefulness, and efficiency separate. They distinguish invalid artifacts, unreadable artifacts, and failed execution. All contribute zero aggregate quality and coverage; the single-case diagnostic scores remain available. Macro averages give each brief equal weight; requirement weights apply within a brief. Known token/cost subtotals and completed-response lower bounds are shown alongside unknown-run counts; lower bounds never substitute for a complete total. The overall total stays null whenever any run is unknown. Collection completion, publication cutoffs, recovery, and qualitative relevance remain explicitly unmeasured by this source-selection comparison.

Trusted controller API:

~~~python
from research_harness.evaluation.benchmark import (
    compare_runs, load_benchmark, validate_splits,
)

development = load_benchmark(development_manifest)
task_input = development.cases[case_id].task()  # id and brief only
report = compare_runs(development_manifest, run_manifests, axis="runtime")
split_counts = validate_splits(development, load_benchmark(private_heldout_manifest))
~~~

The split checker rejects repeated case ids, normalized identical briefs, topic groups, source-family labels, registrable source domains, and identical fixture content across development and held-out manifests. Domain checks cover source choices, gap alternatives and public fixture URLs using a pinned offline suffix snapshot, IDNA normalization and private-hosting boundaries; sibling subdomains overlap. The [strategy guide](strategy-optimization.md#development-leakage-audit-and-split-checks) records the exact rules and advisory candidate audit. The checker reports counts or the conflicting dimension without listing private test cases. Semantic similarity and shared ownership across different domains still need review. During optimization, run this private check only after permanently revoking the proposer. Held-out task contents are not shipped in the development package, and the public fixture runner refuses held-out manifests.

## Remaining evaluation workflow

The source score grades sampled source selection. A passing probe does not prove complete pagination, publication-time correctness, freshness, source-content entailment, licensing, research usefulness, or a successful collection/export/recovery workflow. The separate workflow checker below measures authoritative collection/export state; source completeness and qualitative review must still accompany the pilot comparison.

The next integration work is to independently review and improve the authored development cases, curate the private held-out set, record actual model/provider/budget settings, run the direct and Omnigent paths under matched conditions, and report source scores alongside workflow correctness and recovery. The comparison infrastructure does not invoke a proposer or freeze optimizer selection by itself. No live baseline-versus-Omnigent benchmark has been completed by the fixture runner.

Keep held-out requirements and evaluator internals outside the generated strategy's accessible workspace. A trusted controller supplies each brief and bounded research tools, evaluates exported artifacts, and releases development feedback only. Final selection must be frozen before held-out evaluation begins. A separate directory by itself does not enforce that boundary; the optimization runner must provide the access isolation specified in the accepted plan. No held-out cases are embedded in this package or its synthetic tests.

The migration is self-contained in this package and no longer requires the sibling Meta-Harness checkout. Validation:

~~~sh
uv run pytest tests/test_research_evaluation.py tests/test_research_benchmark.py tests/test_research_workflow_evaluation.py tests/test_research_workflow_summary.py
uv run ruff check src/research_harness/evaluation tests/test_research_evaluation.py tests/test_research_benchmark.py tests/test_research_workflow_evaluation.py tests/test_research_workflow_summary.py
uv run ruff format --check src/research_harness/evaluation tests/test_research_evaluation.py tests/test_research_benchmark.py tests/test_research_workflow_evaluation.py tests/test_research_workflow_summary.py
~~~

## Authoritative workflow evaluation

[workflow.py](../src/research_harness/evaluation/workflow.py) evaluates independently requested collection/export outcomes against the registry, scoped dataset rows, and immutable captures/export blobs. Its report is separate from discovery quality, the Pareto frontier, and the controlled comparison aggregates. A chat statement or a job's success flag cannot establish publication or correct export contents.

The trusted controller supplies a canonical question binding and the original pipeline version/fingerprint. Capture snapshots around an actual isolated replay or reopen operation:

~~~python
from research_harness.evaluation.workflow import capture_snapshot, evaluate_workflow

before = capture_snapshot(
    backend, question_id=question_id, pipeline_version_id=pipeline_version_id,
)
# The application/controller performs its replay or reopen here.
after = capture_snapshot(
    backend, question_id=question_id, pipeline_version_id=pipeline_version_id,
)
report = evaluate_workflow(after, independent_expectation, before=before)
~~~

WorkflowExpectation accepts collections, exports, and optional replay_job_ids. Write the desired outcomes before executing the workflow; bind returned identifiers to those requests in the trusted controller. CollectionExpectation requires a job/operation id, expected terminal status, and an explicit SourceOutcome for every configured source. Use not_started with zero observations for sources skipped after cancellation, and succeeded with the expected publication count for earlier sources that must survive. Optional run_id pins the originally reserved identity; run_exists=false represents cancellation before any collection run starts. source_id and due_only pin collection filters.

ExportExpectation requires a job/operation id and explicit as_of cutoff, with optional kind filter and expected record count. Supply returned_manifest as the exact manifest observed by the controller at the delivery boundary. Otherwise artifact correctness can be checked, but successful delivery remains unknown. The checker reads archived blobs, so a missing local export file does not silently substitute a newly generated export.

| Check | Evidence and interpretation |
| --- | --- |
| Request/configuration identity | Scoped pipeline version, original fingerprint, persisted operation hash/arguments, reserved run id, run configuration, and per-source fingerprints agree |
| Collection publication | Actual terminal run/source statuses and observation counts match independent expectations; failed/cancelled/interrupted sources publish no partial records |
| Observation lineage | Published observations belong to successful source runs and the bound dataset; version/content hashes, capture ownership/body hashes, configuration lineage, observed time, and publication availability agree |
| Fixed-cutoff export | A Python selector reconstructs latest eligible observations per source/kind/key using both observed_at and available_at; compares records, lineage, source universe, manifest, health metadata, cutoff, kind, and hashes |
| Replay/reopen | Before/after operation and reserved run identities remain stable, terminal results remain stable, and scoped runs/captures/publication remain unchanged; requires an isolated controller-observed replay interval |

Export source ids come from the requested saved configuration. An eligible historical observation can originate from an earlier configuration of the same source id; its lineage is checked against its own collection configuration. Publication dates in source content do not make a record available before its capture and successful source publication. The checker does not call Store.as_of or Store.health to verify their own output.

Every check is passed, failed, or unknown. The overall report is failed if any check fails, incomplete if any requested evidence remains unknown, and passed otherwise. A single terminal snapshot does not prove replay or reopen behavior: it records that dimension as unknown. A snapshot taken before terminal completion also cannot establish unchanged terminal publication across replay. These statuses do not imply scientific usefulness, source-content entailment, complete pagination in an arbitrary live source, or known model cost.

Snapshot reads use SQLite mode=ro/query_only or a PostgreSQL read-only transaction. They never initialize an absent dataset, migrate a schema, acquire a writer, invoke JobService.get_job, reconcile interruption, launch a worker, or publish. SQLite may maintain its normal WAL bookkeeping files while reading; dataset/registry records and blobs remain unchanged. Local registry and dataset files have separate read transactions, so use quiescent jobs for acceptance. A registry change observed during capture marks the snapshot unstable. Store these private snapshots and expectations outside candidate access; this API is not exposed as an agent tool. PostgreSQL uses the same optional test-backend fixture contract; only SQLite execution is verified in the local handoff.

The workflow tests execute real services/jobs with mocked HTTP and controllable clocks. They cover cancellation before collection, interrupted/cancelled collection after an earlier source publishes, cancelled pagination with retained captures but no partial publication, reopen/replay, immutable-blob corruption, foreign-question state, changed requests/configurations, publication cutoffs, and a wrong export whose internally consistent hashes still fail independent record selection. No paid provider calls or scientific performance claims are involved. Workflow report aggregation into matched direct/Omnigent comparisons remains controller integration work.

The [September 8 workflow report](../examples/evaluation/evidence/workflow-2026-09-08.json) records read-only checks of two existing synthetic normal-Omnigent runtime runs. Each has 23 passed checks, zero failures, and one unknown: no before/replay snapshot was available to independently measure replay in that check. Authoritative snapshot fingerprints remained unchanged across evaluation. Identifiers, cutoff, configuration, and the delivered manifest came from trusted fixture orchestration; this is software compatibility evidence, not an independently reviewed scientific benchmark. The referenced temporary backend databases are local execution artifacts; the report alone does not reproduce those databases.

## Workflow summaries across cases

[workflow_summary.py](../src/research_harness/evaluation/workflow_summary.py) aggregates those workflow reports for a full expected case set. It remains separate from discovery usefulness and efficiency. Supply the benchmark's complete case list, including failed executions, rather than deriving the denominator from whichever reports were saved:

~~~python
from research_harness.evaluation.workflow_summary import aggregate_workflows

summary = aggregate_workflows(
    benchmark.cases.keys(),
    reports_by_case,  # case id -> evaluate_workflow report or None
)
~~~

Values can also be wrappers containing report and an optional case_id. An embedded case_id must match its mapping key, including inside the report; foreign or duplicate case bindings reject the aggregation. Wrapper metadata, such as a controller's execution error, is not evidence of workflow correctness. The execution journal should retain those errors separately.

| Per-case status | Meaning |
| --- | --- |
| passed | Every recorded check passed; workflow dimensions absent from the report are still not measured |
| failed | A valid evaluator report contains at least one failed check |
| incomplete | A valid evaluator report has unknown checks and no failed checks |
| missing | No report was supplied, including executions that failed before workflow evaluation |
| invalid | Report schema, hashes, check identifiers, counts, or claimed status are inconsistent |

The helper validates the full version-1 evaluator report, requires its baseline and job checks, rejects repeated check names, and recomputes counters and status. A status-only success claim cannot establish correctness. All expected cases remain in case_counts and the cases denominator; missing or invalid reports do not contribute invented check outcomes. valid_reports counts structurally valid reports, including failed and incomplete ones.

Dimension summaries retain passed, failed, unknown, not_measured, missing, and invalid case counts for collection, export, replay, and the other recorded check families. recorded_check_counts describes only the available valid reports; it is a diagnostic count, not a quality score or an average that gives more weight to cases with more checks. Individual valid reports retain a canonical report-content hash and their snapshot/expectation hashes. These hashes verify consistent records, not execution or provenance.

The summary is failed when any valid case has a failed check, incomplete when missing/invalid/unknown reports prevent a complete result, and passed only when every expected case's recorded checks passed. Its scope field and per-dimension not_measured counts remain relevant even for a passed summary. The archived fixture pair correctly summarizes as two incomplete cases, 46 passed checks, and two unknown replay checks. It does not establish replay correctness, source usefulness, model quality, or cost.
