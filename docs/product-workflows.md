# Proposed analyst workflows

Product examples dated September 6, 2026. These are designs for review, not implemented features or accounts of customer interviews. The numerical example below is entirely fictional.

## Workflow A: Prepare for earnings and review what changed

**Trigger:** a company under coverage has an upcoming release, or has just reported.

**Inputs:** the current workbook and thesis; open research questions; prior company guidance; dated consensus if available; recent company and relevant peer disclosures. The analyst selects the comparison baseline. Every imported record retains its source and information-availability timestamp.

| Stage | Harness work | Analyst-facing artifact |
| --- | --- | --- |
| Before the event | Identify the key unresolved debates and the results that would inform them; preserve forecasts and source versions | An expectations sheet, questions for the call, and a saved baseline |
| Release arrives | Extract reported figures, check definitions and periods, and compare actuals and guidance against the baseline | A preliminary scorecard, source links, and exceptions |
| Call or transcript arrives | Connect answers and new qualifications to the original questions | Answered, partly answered, and open questions with speaker attribution |
| Model review | Map supported changes to the existing model; distinguish actuals from forecast proposals; calculate scenarios | A change list with prior/new values, formulas, rationale, and impact |
| PM review | Summarize what changed, what remains uncertain, and what decision is needed | A concise PM brief using the reviewed model version |
| Filing and later evidence arrive | Reconcile details, update incomplete claims, and preserve the earlier event record | A new version with a visible explanation of changes |

A release-only brief should show that the call or filing has not yet been incorporated. A later source can supersede an earlier observation without erasing the initial brief. If a company changes the definition of a KPI, stop the affected comparison and propose a mapping or recast instead of treating unlike numbers as a trend.

An analyst can accept a reported actual while retaining their own forecast. Proposed assumptions stay separate until the analyst accepts or edits them. If a model is not recalculated or a required input is missing, dependent valuation outputs remain incomplete.

### Fictional example: Harbor Software

All numbers below are synthetic and illustrate product behavior. Currency is USD millions. The example assumes the same revenue accounting basis and fiscal periods across the compared inputs, and an unchanged issuer definition for retention. No security price or investment recommendation is implied.

The current thesis is that improving customer retention will sustain growth. The analyst's pre-event model contains a full-year revenue forecast of $1,100 million. The company reports the following:

| Metric | Analyst's saved estimate | Pre-release consensus | Company report / guidance |
| --- | ---: | ---: | ---: |
| Second-quarter revenue | 245 | 240 | 250 actual |
| Net revenue retention | 118% | Unavailable | 112% actual |
| Full-year revenue | 1,100 | Unavailable | New guidance: 1,010–1,050 |

The prior full-year company guidance was 1,050–1,100. Its midpoint moves from 1,075 to 1,030, a decrease of approximately 4.2%. Reported second-quarter revenue is approximately 2.0% above the analyst estimate and 4.2% above pre-release consensus. The analyst's full-year forecast is 50 above the new guidance's upper bound.

The useful artifact should surface the tension: the reported quarter beat the saved estimates, while the full-year outlook and retention do not support an uncomplicated growth-improvement narrative. This is a question for investigation, not sufficient evidence on its own to reject the thesis.

Proposed work for the analyst:

- Inspect the release passages underlying revenue, guidance, and retention.
- Identify whether the guidance change reflects churn, expansion, sales timing, currency, or a definition change.
- Prepare questions about cohorts, renewals, and the outlook for the remaining quarters.
- Put reported revenue into the actuals review queue while preserving the old forecast snapshot.
- Run a clearly labeled management-guidance scenario; do not relabel its midpoint as the analyst's accepted forecast.
- Review how the scenario affects the forecast and valuation before changing the base case.

A sample PM update could read:

> Harbor reported Q2 revenue above our saved estimate, but cut the midpoint of its full-year guidance by 4.2%. Retention was below our modeled assumption. Our full-year forecast remains above the new guidance range. The retention thesis needs review; the cause of the guidance reduction is still unresolved. The attached scenario is exploratory, and the base-case forecast has not yet been revised.

The sample should carry the release-only status, source list, saved estimate timestamp, and model version. In the real product, the numerical claims would link to their actual evidence and formulas.

## Workflow B: Review a development elsewhere in the coverage universe

**Trigger:** a peer, customer, supplier, or relevant regulator publishes new information.

Start with a documented relationship to a company under coverage and a research question the development might affect. Record whether the relationship is disclosed, analyst-supplied, or inferred. Identify the potentially affected revenue or cost driver and the period in which the effect could occur.

The analyst sees the source, explanation of relevance, alternative explanations, and any proposed scenario. For example, weak results from one supplier may concern a different geography or customer mix; the analyst should be able to reject that connection without repeatedly seeing the same alert.

Useful output is a short review item: development, affected thesis condition, relevant model input, what would confirm the connection, and next action. Ranking should reflect the user's coverage priorities and stated materiality thresholds. Track dismissed alerts and important events the system missed.

Begin with a small user-selected set of related companies. Broad ecosystem coverage adds entity-resolution and maintenance work that must be visible in the pilot's cost.

## Workflow C: Prepare a management or expert conversation

**Trigger:** the analyst has a scheduled call or an unanswered research question.

Bring together the current thesis, relevant assumptions, prior call notes, public statements, and sources the analyst is entitled to use. List what the intended contact is well placed to observe. Organize questions by the decisions their answers could inform.

For each proposed question, show the open issue, relevant existing evidence, what has already been asked, and what a useful answer could clarify. Avoid embedding the preferred answer in the question. Keep the distinction between an observation, a source's opinion, and an analyst inference.

After the conversation, draft a record of answers and uncertainty with dates and attribution. Link it to the relevant assumptions and draft follow-up tasks. The analyst reviews the interpretation. The proposed initial product prepares material and captures supplied notes; contacting people and arranging calls remain separate user-directed actions.

Success is observable: fewer repeated questions, faster preparation, better coverage of the analyst's important unknowns, and an evidence trail that remains useful during the next review.

## Workflow D: Challenge a thesis and learn from the outcome

**Trigger:** a pitch, a material new development, a scheduled review, or a completed investment.

The challenger receives the same source boundary and information cutoff as the original work. It identifies which assumptions drive the result, seeks contrary evidence, and distinguishes a missing source from a weak inference or an actual contradiction. It can conclude that a concern is unsupported.

The analyst records their disposition of each material challenge, including any change in forecast or planned research. Store dated expectations and the future observable outcome that would test them.

When that outcome arrives, compare the forecast with what happened. Record changed definitions and events that made the comparison inappropriate. Attribute research misses separately from valuation changes, position sizing, trading decisions, and market moves. A historical replay cannot establish forecast skill because the language model may already know later outcomes; prospective cases are needed to evaluate that claim.

## Proposed product surfaces

| Surface | What the analyst should accomplish |
| --- | --- |
| Coverage inbox | Choose which event or unresolved question to review, with its relevance explained |
| Company case | Inspect the thesis, expectations, catalysts, prior decisions, and open questions |
| Evidence view | Read the exact supporting passage or table alongside a claim and its conflicts |
| Model review | Compare proposed changes with the current workbook and inspect recalculated consequences |
| PM brief | Review a short argument tied to the accepted sources and model version |

The underlying agents handle bounded assignments. A user should be able to direct work through the question they need answered without understanding the agent architecture.
