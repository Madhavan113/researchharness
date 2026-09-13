# Archived prediction-market research notes

Historical exploration, not a standalone application or the current product roadmap. The accompanying JSON is a saved observation snapshot, not executable harness code.

Research date: September 7, 2026. This is a product proposal supported by public documentation and a small set of direct API observations. No customer interviews, forecasting benchmark, or trading trial has been conducted.

## Recommendation

Test a research workspace for trade and export-policy contracts: purchase announcements, sanctions, export restrictions, and trade agreements. The working interpretation of the user's reference to sales includes aircraft, agricultural commodities, and arms sales; the intended specialty remains unconfirmed.

The core job is to estimate whether a particular contract will resolve Yes by its deadline, explain the evidence behind that estimate, and determine whether available prices and quantities make the disagreement useful. The hypothesis is that a specialist can benefit from connecting institutional processes, original-language sources, and exact contract terms. This research does not establish a forecasting or trading advantage.

Start with a market inventory and one source-to-research-card workflow. Select a narrow policy family after checking recurring listings, source access, and capacity at users' intended position sizes. An arms-sales-only product needs that validation: the limited keyword search conducted here found a historical Taiwan arms-sales-halt contract, but did not establish an adequate active universe.

## What exists, and what the contract actually asks

| Verified example | Status observed | Product lesson |
| --- | --- | --- |
| Chinese Boeing purchase announcement associated with the May 2026 summit | Historical; API marked closed and resolved | A definitive government announcement counted even if the purchase or delivery occurred later. Negotiations alone did not count. The named primary evidence was Chinese government information. |
| New U.S. sanctions against China through September 2026 | Active and accepting orders | The rules distinguish new measures or expanded scope from routine additions of entities under existing rules. An authorizing act can count before its effective date. |
| U.S.–Cuba trade agreement before January 2027 | Active | A preliminary framework can qualify; Cuba must also acknowledge the agreement. A statement about starting talks is insufficient. |

Sources: [Boeing announcement contract](https://polymarket.com/event/trump-xi-summit-what-will-china-announce-by-may-22?marketSlug=will-china-announce-a-boeing-aircraft-purchase-by-may-22-385&outcomeIndex=0), [China sanctions contract](https://polymarket.com/event/us-imposes-new-sanctions-on-china-by-september-30), and [Cuba contract API](https://external-api.kalshi.com/trade-api/v2/markets/KXTRADEDEALCUBA-27-B270101). Dates, identifiers, and numerical observations are recorded in the [discovery snapshot](archive/prediction-markets/2026-09-07-discovery.json).

These examples make source selection consequential. Shipment data may help assess demand, but it cannot substitute for a required purchase announcement. A sanctions-related headline may be relevant background without satisfying that contract's trigger. The research card must identify the actor, action, object, threshold, time window, exclusions, and resolution source separately.

The arms-sales workflow needs its own stages: public announcement, congressional notification, agreement, and delivery are separate observations. DSCA's notices describe possible sales, and its current page directs future notification posts to the State Department. A connector built only around the old DSCA feed could miss new publications. The destination State page could not be retrieved with the research browser during this pass, so direct connector access still needs verification. [DSCA publication notice and examples](https://www.dsca.mil/press-media/major-arms-sales).

## Capacity is part of the research question

Direct order-book observations retrieved around **19:09 UTC on September 7, 2026**:

| Contract | Best Yes bid | Best Yes ask | Shares offered at best Yes ask | Purchase cost at that level, before fees |
| --- | ---: | ---: | ---: | ---: |
| China sanctions | $0.07 | $0.08 | 5 | $0.40 |
| Cuba trade agreement | $0.11 | $0.15 | 103.43 | $15.5145 |

These are displayed quantities at a particular instant, not promised fills. There were additional offers at higher prices. The Cuba ask is derived from the complementary No bid, consistent with Kalshi's order-book representation. Sources and extracted levels are preserved in the [snapshot](archive/prediction-markets/2026-09-07-discovery.json); see the [Polymarket order-book documentation](https://docs.polymarket.com/api-reference/market-data/get-order-book) and [Kalshi order-book documentation](https://docs.kalshi.com/api-reference/market/get-market-orderbook).

This small sample does not characterize all geopolitical markets. It does show why headline probability and lifetime volume are inadequate measures of capacity. A user buying more than the best offer's quantity faces different prices. For hedge-fund users, feasible position size and research cost could determine whether this is a useful trading product. A geopolitical monitoring tool for their equity or macro portfolios is a separate customer hypothesis.

## First product workflow

1. **Choose a contract.** Screen current status, precise terms, ambiguity, deadline, source coverage, spread, and cumulative depth at the user's intended size. Save the rules and the quote snapshot together.
2. **Build an evidence map.** Identify the institutions and people who can cause the qualifying action, necessary procedural steps, scheduled decisions, and evidence that would support or weaken each scenario.
3. **Monitor the relevant sources.** Preserve original text, source links, publication time, first observed time, retrieval time, revisions, and translation. Connect repeated reporting to its original source so syndicated stories do not look like independent confirmation.
4. **Update the research card.** Explain what changed, which contract clause it affects, and the strongest contrary interpretation. Keep observed facts, interpretations, and probability judgments separate. An update may leave the probability unchanged.
5. **Record a decision.** Show the analyst's probability estimate and plausible range, current executable-side prices, depth, applicable fees, next catalyst, and reasons to wait or abstain. Keep each revision for later review.

For a conventional binary contract paying $1 for Yes and $0 for No, an illustrative hold-to-settlement expected profit per share is `p - average purchase price - fees per share`. The probability must refer to the contract's resolution, and the purchase price must account for quantity. Funding costs, settlement timing, and any venue-specific alternative settlement terms require separate treatment. Displayed price is a comparison point, not a demonstrated true probability. Polymarket documents a proposal and dispute process, so the harness must track settlement status as well as the underlying event. [Resolution documentation](https://docs.polymarket.com/concepts/resolution).

## Ingestion priorities and agent responsibilities

| Layer | First sources or work | Purpose |
| --- | --- | --- |
| Contract and market data | Venue market/event APIs, rules, status, fees, order books | Establish the actual question and the prices available for a specified quantity. |
| Official policy evidence | [OFAC recent actions](https://ofac.treasury.gov/recent-actions), [BIS Federal Register notices](https://media.bis.gov/regulations/federal-register-notices), [MOFCOM announcements](https://english.mofcom.gov.cn/Policies/AnnouncementsOrders/) | Distinguish statements, formal measures, amendments, and effective dates. |
| Sales evidence | [USDA export sales reporting](https://www.fas.usda.gov/programs/export-sales-reporting-program), relevant government or company purchase announcements | Match the reported sale, commitment, or shipment to the contract's required event. USDA publishes daily and weekly information with defined reporting requirements. |
| Arms-sales evidence | State notification destination linked by DSCA, relevant legislative and purchaser announcements | Track the specified administrative stage. |
| Additional evidence | Selected local reporting and specialist sources; subsequently evaluate licensed trade or shipping data | Test a specific unresolved question and measure whether the source improves the workflow. |

The proposed agents have bounded jobs: interpret contract terms; research institutional steps and relevant historical cases; monitor and translate evidence; develop scenarios and challenge the forecast; and explain changes to the analyst. Deterministic code handles collection, timestamps, deduplication, arithmetic, and order-book calculations. Agents share an evidence ledger; multiple agents repeating the same source do not supply independent probability estimates.

The existing company harness's persistent case, evidence links, and decision history transfer well. The principal model becomes an event tree with explicit assumptions and conditional probabilities. Spreadsheet output can support scenarios and payout calculations when users need it.

Observed integration issues should become acceptance checks:

- The historical Boeing contract still had `active: true` alongside `closed: true` and `acceptingOrders: false`; one flag cannot establish tradability.
- Its metadata end date differed from the deadline stated in the rule text. Preserve both, flag the conflict, and require review before scheduling a cutoff.
- The sampled Kalshi response reported zero in its generic liquidity field while its order book contained quotes. Use actual levels for capacity calculations.
- The China-sanctions response had fees enabled. Retrieve the applicable fee parameters rather than assuming this category is free. [Polymarket fee documentation](https://docs.polymarket.com/trading/fees).

Venue choice also depends on the intended users. The global Polymarket page displayed a U.S. trading restriction and directed users to its separate U.S. service. Listing availability cannot be assumed identical. The research observations here used public read access. [Observed market page](https://polymarket.com/event/us-imposes-new-sanctions-on-china-by-september-30).

## Proposed pilot and decisions it must resolve

First ask three intended users to show a recent geopolitical research decision, explain how much they would realistically deploy, and identify the sources and steps that consumed time. Determine whether they need market selection, faster evidence review, better forecasting, or portfolio monitoring.

Inventory a candidate policy family across accessible venues. Select five to ten related contracts if enough meet user-defined clarity and depth requirements; this is a pilot target, not an observed count. Build one read-only connector workflow and evaluate it alongside the users' existing process for an initial two-week usability trial, continuing outcome tracking until the contracts settle.

Measure source coverage and alert precision, time saved after checking and correcting the output, missed qualifying events, and unsupported conclusions. Record forecasts and market benchmarks at the same predeclared times. On resolved cases, compare Brier scores and calibration; report the sample size and group correlated contracts from the same underlying event. A small trial can validate usability but will not establish durable forecasting skill.

Evaluate any simulated trading results separately, using observed depth, current fees, timing, and explicit fill assumptions. Freeze historical inputs before replay; historical tests using a modern language model cannot establish absence of prior knowledge. Prospective forecasts are needed to test predictive value.

Proceed with a trading-research product only if recurring markets, usable sources, and capacity support the intended customer's workflow. The immediate implementation candidate is **contract ingestion plus a source-linked research card for one trade-policy market**, followed by a dated record of changes.
