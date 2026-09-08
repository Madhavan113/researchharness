# What a fundamental analyst needs from a research harness

Research and product synthesis, September 6, 2026.

**Recommendation:** design around maintaining coverage of companies the analyst already knows. The first product should help an analyst prepare for an event, reconcile what changed, review the implications for their existing model, and explain the decision to their portfolio manager. Company initiation remains a useful workflow and a technical benchmark.

The initial research-pack-and-DCF design covers only part of the job. The broader requirement is to preserve a defensible connection between the investment question, expectations, new evidence, forecasts, and decisions over time.

This is desk research, not completed customer discovery. The working audience is a fundamental public-equity analyst at a hedge fund. The exact sector, holding period, fund structure, current software, data entitlements, and purchasing authority remain unconfirmed. Feature priorities below are product judgments to test with the intended users.

## What the evidence changes

CFA Institute's analyst-skills material includes choosing material research factors, differentiating a forecast from consensus, identifying catalysts, communicating a stock call, and reviewing previous theses. That is a substantially broader assignment than collecting company information. [CFA analyst skills](https://www.cfainstitute.org/programs/cfa-program/candidate-resources/practical-skills-modules/analyst-skills).

Fund descriptions also show that investment research is divided among people with different responsibilities. Viking describes sector analysts, research specialists, portfolio managers, and a CIO responsible for overall exposures. Point72 describes supply-chain investigations and alternative-data research supporting investment teams. [Viking public equity](https://vikingglobal.com/public-equity/), [Point72 Market Intelligence](https://point72.com/market-intelligence/).

The competitive research changes the differentiation argument. AlphaSense advertises event monitoring and model revisions; Rogo advertises configurable workflows and Excel integration; research-management products already address institutional memory. We should test specific failures in a team's current workflow before treating those capabilities as an unmet market need. See the [market landscape](market-landscape.md).

## Choose the user more precisely

These are useful segmentation hypotheses, not claims that every fund in a category behaves alike. Long/short investing can also have a long horizon.

| Context | Likely emphasis to investigate | Product implication |
| --- | --- | --- |
| Earnings-sensitive long/short team | Estimate revisions, event timing, relative value, portfolio and short constraints | Fast comparisons against the saved model and pre-event expectations |
| Concentrated investor with a longer horizon | Business durability, reinvestment opportunities, management execution, downside resilience | Longitudinal evidence, operating scenarios, and capital-allocation history |
| Sector specialist | Industry-specific drivers and information sources | A sector-specific model and research vocabulary |
| Analyst starting coverage | Learning the business, forming the initial debate, building history | Initiation pack and a traceable starting model |
| Analyst maintaining established coverage | Changes since the last decision and repeated model updates | Preserve the existing workbook, assumptions, and research history |

Dodge & Cox provides one concrete longer-horizon example: its published process combines continuing diligence, forecasts, downside analysis, and team review. That supports offering different research cadences rather than imposing a universal earnings-trading workflow. [Dodge & Cox investment process](https://www.dodgeandcox.com/financial-professional/ie/en/our-approach/our-philosophy-and-process.html).

The analyst is the daily user. A PM or research lead may sponsor adoption; technology, operations, and the owner of data contracts can determine whether the product can be used. These buyer roles need to be established during discovery.

## The additional jobs to support

The artifacts in this table are proposed product outputs. They are not findings that analysts have requested these exact interfaces.

| Analyst's question | Required capability | Concrete output |
| --- | --- | --- |
| Which name or question deserves my next hour? | Coverage triage using the mandate, upcoming events, unresolved material questions, and available research | A ranked research queue with an explanation for each item |
| What are the few debates that determine this investment? | Identify competing explanations and what could discriminate between them | A short debate list with evidence on each side |
| Where is our forecast different from expectations? | Align the analyst model, broker consensus, company guidance, and prior snapshots | A driver-by-driver expectations table |
| What happened relative to what we expected? | Compare a release against frozen estimates and guidance | An earnings scorecard and a list of unresolved changes |
| What should change in my model? | Map new facts to existing model inputs and assumptions | A reviewable workbook change proposal with a numerical impact |
| Are reported improvements economically durable? | Examine earnings quality, cash conversion, financing, and recurring adjustments | An exception list with source evidence and follow-up work |
| What should I ask management or an expert? | Use open questions and previous conversations to prepare focused research | A call brief, question list, and evidence gaps |
| Does management allocate capital and execute well? | Compare commitments, incentives, investments, financing, and outcomes over time | A dated capital-allocation and execution record |
| What does a competitor or supplier's news mean for us? | Track supported relationships between companies and operating drivers | A cross-company implication to review |
| What could make the market reassess the company, and when? | Track catalysts, prerequisites, expectations, and uncertainty around dates | A catalyst calendar linked to thesis tests |
| Is this idea useful in our actual portfolio? | Bring in relevant position, exposure, liquidity, and short-borrow context | A portfolio-context panel for the PM |
| How do I explain the change quickly? | Build a concise argument from the reviewed evidence and model | A PM update with the change, consequences, and decision needed |
| What have we learned from previous calls? | Preserve dated forecasts and decisions, then compare with outcomes | A forecast and thesis review without rewriting prior beliefs |

## Requirements that materially change the product

### 1. Expectations need their own data model

Keep the analyst's forecast, broker consensus, company guidance, and assumptions inferred from a valuation exercise as separate records. Each needs a timestamp, period, metric definition, currency, source, and accounting basis. Consensus also needs its contributing population and dispersion when available. A post-release consensus update must not replace the pre-release comparison.

Visible Alpha already provides granular broker estimates, revision analysis, and line-item alerts. Our proposed value is in reconciling the relevant expectations to the analyst's particular drivers and decisions; the data itself is an integration requirement. [Visible Alpha Insights](https://www.spglobal.com/market-intelligence/en/solutions/products/visible-alpha-insights).

A reverse valuation can show assumptions consistent with a price conditional on the other inputs. It cannot establish a unique set of beliefs held by investors. Informal expectations mentioned in calls should retain their attribution and cannot silently become consensus.

### 2. The operating model needs more attention than the DCF format

The analyst may need quarterly forecasts, segment models, price/volume/mix bridges, customer cohorts, working capital, dilution, debt maturities, and several valuation methods. The appropriate scope depends on the business and the assignment. CFA's forecasting material explicitly connects model choice and horizon to industry, strategy, and information availability. [Company forecasting](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/company-analysis-forecasting).

For example, software research can involve recurring revenue and retention, while a bank model needs funding and credit metrics. Even similarly named metrics require checking each issuer's definition. [Software KPIs](https://www.spglobal.com/market-intelligence/en/news-insights/resources/kpi-guides/application-software), [Bank KPIs](https://www.spglobal.com/market-intelligence/en/news-insights/resources/kpi-guides/bank).

The first workbook integration should support one actual model layout. Distinguish a newly reported actual from a proposed forecast revision. Preserve formulas, annotations, provider links, and analyst overrides. A reporting-segment change should create a mapping exception with an explicit historical recast proposal.

Use DCF, comparable valuation, sum-of-the-parts, or other methods according to the analyst's process. A generated spreadsheet is useful only if the analyst can continue working in it.

### 3. Primary research is a workflow of its own

Help the analyst choose what to investigate and prepare for conversations. Citadel's published discussion emphasizes direct interaction, understanding incentives, and repeatedly reassessing an investment as circumstances change. [Citadel on fundamental investing](https://www.citadel.com/careers/career-perspectives/staying-grounded-judgment-and-perspective-in-fundamental-investing/).

A useful call brief links each question to a disputed assumption. After a conversation, capture the date, source's role, what they actually observed, geographic and customer scope, remaining uncertainty, and which assumptions deserve review. Separate quotations from paraphrases. Several notes that repeat one original claim do not constitute independent confirmation.

The management investigation should examine acquisitions, buybacks and dilution, investment choices, compensation incentives, guidance, and subsequent outcomes. Account for business conditions when interpreting results. The product should present the evidence and relevant alternatives rather than assign an opaque management-quality score.

### 4. Monitoring must explain why a development matters

An event is useful when it changes a question, estimate, scenario, or decision. Every alert should identify the new evidence, the affected company or relationship, the relevant assumption, and the next review step. Analysts should be able to dismiss or defer an alert and explain why.

A peer's weak result might reflect a shared end market, its own execution, or a different customer mix. Store the proposed transmission mechanism and counterevidence before treating the observation as a change to the target company's forecast. Include uncertainty and duplicate-source detection in the review.

A catalyst record needs an event or date range, confirmed versus estimated timing, an expected observable outcome, and the thesis condition it tests. A scheduled date alone does not explain why an investment could be repriced.

### 5. Financial quality is an investigation, not an automatic accusation

CFA's reporting-quality material covers revenue and expense recognition, cash-flow classifications, balance-sheet completeness, accounting-policy comparisons, and differences between earnings and cash generation. [Evaluating financial reports](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/evaluating-quality-financial-reports).

Proposed checks should produce specific review items: recurring exclusions from adjusted earnings, unusual receivable movements, changes in capitalized costs, acquisition effects, financing requirements, and unexplained changes in disclosed metrics. Link the observation to historical and peer context. An unusual ratio is a reason to investigate; it does not establish misconduct.

### 6. Research must connect to the PM's decision

The output should make the forecast difference, payoff scenarios, timing, counterarguments, and evidence still needed easy to review. Portfolio context can explain whether several holdings share a customer, industry driver, or risk exposure. Position data and portfolio analytics should come from the team's existing systems and retain their timestamps.

Short ideas need additional context. Borrow cost and availability can change; indicative availability is not a guaranteed locate. Treat these as sourced constraints on a scenario, with actual portfolio decisions owned by the fund. [IBKR short availability](https://www.interactivebrokers.com/en/trading/short-securities-availability.php).

Public ownership filings cannot substitute for current portfolio data. Form 13F is a delayed disclosure and excludes short positions. [SEC Form 13F FAQ](https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f).

### 7. Preserve learning without manufacturing certainty

Save the forecast, reasoning, confidence expressed by the analyst, expected timing, and evidence available at the decision. Later, compare the forecast with the actual result and document what was learned. Keep research accuracy distinct from returns and from decisions about entry, sizing, and exit.

Historical comparisons can also challenge overly optimistic projections, provided the reference group is relevant and its limitations are visible. Morgan Stanley's base-rate discussion provides one example of using historical experience when assessing forecasts. [Bayes and base rates](https://www.morganstanley.com/im/en-us/individual-investor/insights/consilient-observer/bayes-and-base-rates.html).

## Product priorities

Priority reflects the proposed first workflow, integration dependencies, and ability to evaluate quality. It is not a demand score measured from customers.

| Priority | Scope | Reason |
| --- | --- | --- |
| First pilot | Saved thesis and questions; dated model and expectations; event comparison; source inspection; workbook changes; PM update | A coherent assignment with concrete inputs and reviewable outputs |
| Expand after the first pilot | Call preparation and evidence capture; management history; accounting exceptions; related-company monitoring | Adds research depth using the same case and evidence records |
| Add when data and demand justify it | Broad idea screening; alternative-data analysis; portfolio context; detailed short constraints; decision analytics | Requires more integrations, different evaluation, and often additional owners |

Foundational requirements apply immediately: source lineage, definitions and periods, explicit missing data, persistent analyst edits, source access boundaries, and a record of what changed. These features are necessary for usability; their presence alone does not establish differentiation.

## The first product bet to test

**An event-review workflow for a sector analyst maintaining an existing model.** Before earnings, capture the debates and expectations. After the release, compare the results, prepare targeted questions, and propose model and thesis changes. Between reports, bring relevant developments back to those same questions.

| Alternative | When it may be preferable | Main uncertainty |
| --- | --- | --- |
| Event review and model maintenance | The team repeats this work and current tools leave substantial reconciliation or review | Whether we can improve on its existing data and Excel tools |
| Call preparation and research memory | The analyst's distinctive work is in conversations and internal notes | Whether input capture is reliable and the suggested questions add value |
| Accounting and disclosure investigations | The user frequently investigates changes that standardized data misses | Whether the checks are accurate enough to avoid an expensive false-positive workload |
| Full company initiation | The user frequently starts coverage or has limited existing infrastructure | Whether a broad pack materially accelerates actual decisions |

The proposed event workflow is already competitively contested. A plausible entry point is one team's sector logic, workbook conventions, and source mix, with strong evidence that it saves review effort. A successful internal tool for the user's friends is a valuable outcome even if a standalone software business is not yet justified.

## Questions that would change the recommendation

- Which work is repeated most often, and how long does it take after using the team's current tools?
- Which mistakes are hardest to find and most costly to correct?
- Does the PM primarily ask about earnings revisions, longer-term business durability, catalysts, or portfolio fit?
- Which workbook and data sources can the team actually use with an external system?
- Which useful information exists only in calls, chats, email, or individual memory?
- Will the same workflow help the next analyst without extensive custom work?
- Who can approve a pilot and who would pay if it succeeds?

The next discovery step is to observe one completed assignment and one live review with the intended users. The [pilot plan](analyst-pilot.md) defines what to collect and measure; the [workflow examples](product-workflows.md) make the proposal concrete enough to critique.
