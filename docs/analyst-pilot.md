# Analyst discovery and pilot plan

Proposed September 6, 2026. No interviews or pilot runs have yet been conducted. The purpose is to identify a repeated analyst problem and test a concrete improvement against the team's current tools.

## Start with the intended users

Begin with the user's analyst friends, ideally two people who can show how they work, plus a PM or research lead who reviews their output. Establish the fund's investing style, sector coverage, holding period, size of coverage universe, and split between initiating ideas and maintaining positions.

Ask who owns the model, who makes portfolio decisions, which software and data services are already used, and who can authorize a pilot or purchase. The users and buyer may be different people. For a broader product, later test the same assignment with a second team rather than extrapolating from a single relationship.

## Observe a completed assignment

Use a recent earnings update, new idea, management meeting, or change in thesis that the analyst is permitted to discuss. Reconstruct the work using its real artifacts. A public-source example is sufficient to start.

| Discovery prompt | What to observe |
| --- | --- |
| Walk through the last time new information changed your forecast | Trigger, sources, actual edits, and the decision the work supported |
| Show the model and notes immediately before and after that event | How expectations, rationale, and changes are currently recorded |
| Which part took longest even with your existing tools? | A specific bottleneck, its frequency, and its workaround |
| What did the PM ask you to defend or investigate further? | Missing context and the actual standard for a useful output |
| Show a recent data or modeling error and how you discovered it | Error types, detection effort, and consequences |
| What information was only in your calls, inbox, or memory? | Whether valuable context can be captured without extra clerical work |
| What makes you ignore an alert? | Materiality, duplication, source confidence, and alert fatigue |
| Which recent tool did you try and then stop using? | Adoption friction and whether our proposal repeats it |
| What could prevent the team from using or buying this? | Source access, deployment, budget, procurement, and support constraints |

Observe current behavior before asking for reactions to the proposed workflows. Record observations separately from interpretations and feature requests. Avoid treating polite interest in an AI demo as evidence of repeated demand.

## Select one assignment

The current product hypothesis is event review against an existing model. Change it if observed behavior indicates a stronger starting point.

| Observed gap | Candidate pilot |
| --- | --- |
| Release, consensus, and existing model require repeated reconciliation | Earnings comparison and model-change review |
| Segment/KPI changes make history hard to compare | Disclosure-change investigation and explicit recast proposal |
| Prior calls and unanswered questions are difficult to recover | Call preparation and research-memory workflow |
| Important peer or supplier developments are missed | Monitoring linked to specific thesis assumptions |
| Frequent unfamiliar-company work dominates | Initiation pack with operating assumptions and valuation |

If the current stack already solves the observed task effectively, investigate the next bottleneck. Include a configured workflow on an existing platform as a legitimate alternative to a new standalone product.

## Proposed pilot design

Use one sector, one actual workbook layout, and a small selected coverage set. The following test size is an initial feasibility exercise, not a statistically representative study.

1. Select three historical events: an ordinary update, a material change or conflicting signal, and a reporting-definition or missing-data problem.
2. Freeze the assignment, baseline workbook, thesis, expectations, and information cutoff for each case. Define required output and material errors before inspecting results.
3. Have the analysts evaluate the work produced using their best current workflow and the proposed harness. Counterbalance order where practical and record the familiarity advantage of reviewing the same case twice.
4. Test the resulting workflow on two new events prospectively when appropriate sources and users are available.
5. Observe the review, corrections, workbook use, and PM discussion. Record whether the analyst chooses the workflow again without prompting.

Historical replays are useful for testing temporal source filtering, mechanics, and review usability. They cannot establish predictive investment skill: the model may have learned later outcomes. Prospective evaluation is required for claims about better forecasts or earlier insight.

## Measures and acceptance

| Measure | Definition |
| --- | --- |
| Total analyst effort | Preparation + data export + review + corrections + repeated work |
| Time to accepted output | Elapsed time until the analyst would use the result, with preliminary and final stages distinguished |
| Material factual quality | Numerical and interpretive errors that would change the forecast, thesis, or next action |
| Source support | Whether each material claim is actually supported by its linked evidence and time context |
| Model integrity | Correct calculations, persistent analyst edits, and no unintended changes to formulas or structure |
| Monitoring usefulness | Analyst-accepted review items, duplicate/irrelevant alerts, and known material events missed |
| Research usefulness | Open questions resolved, useful new questions, and consequences for the analyst's work |
| Setup and support | Initial mapping time, per-case manual preparation, and work required for a second analyst |
| Incremental cost | Model/tool usage, additional data access, and ongoing operating effort |
| Repeat use | Whether the analyst elects to use it for the next comparable assignment |

Agree on a material time-saving target and error rubric with the users after measuring the baseline and before judging the pilot. A faster first draft that takes longer to verify is not a win. A prettier report without a useful change in the assignment is not enough.

Mechanical acceptance requirements are explicit: no silent workbook overwrites; material changes have evidence or an identified assumption owner; unlike periods and definitions are not silently compared; failed calculations and missing inputs remain visible; a PM brief matches the reviewed workbook version.

Proceed when repeated assignments require less total work, the analyst trusts the inspection process enough to reuse the outputs, and the integration effort is sustainable. Investigate or narrow the design when the analyst must reconstruct the answer manually, essential data cannot be supplied, or each additional model requires extensive bespoke work.

## What to retain from the pilot

Keep a case record containing the assignment, baseline, source manifest, outputs, workbook changes, analyst corrections, elapsed effort, and final disposition. Capture the actual reason for acceptance or rejection in the analyst's words.

The pilot should end with one selected workflow, a defined user and sector, explicit source requirements, a measured baseline, a list of consequential failure cases, and a reasoned build/configure/defer decision. Investment returns are not the success metric for this initial product test.
