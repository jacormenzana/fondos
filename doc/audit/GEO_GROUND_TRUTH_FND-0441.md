# FND-0441 Step 1: what the documents say about Geography (ground truth), 2026-11-25

Read-only. Samples sealed in commit `b41d8a4` before any document was read: `doc/audit/geo_ground_truth/` (holdout 150, **not read**; development 100; KIID-only 80). Tool: `scripts/audit/p1/geo_ground_truth.py`.

## Result
**Most funds that are `Global` by default do state a mandate in their document; the extractor misses it. The stop rule (stated share below 25 %) does not trigger.**

### Funds labelled `Global` by default (population 692; sample 100)
| Group | n | Global | Region | Emerging | Not stated | Any mandate stated (95 % interval) |
|---|---|---|---|---|---|---|
| All | 100 | 44 % | 9 % | 6 % | 41 % | **59 %** (49-68) |
| Spanish documents | 81 | 46 % | 7 % | 2 % | 44 % | 56 % (45-66) |
| English documents | 19 | 37 % | 16 % | 21 % | 26 % | 74 % (51-88) |
| Bonds and money market | 44 | 23 % | 11 % | 5 % | 61 % | 39 % (26-53) |
| Equity, mixed, other | 56 | 61 % | 7 % | 7 % | 25 % | 75 % (62-84) |

Applied to the 692: about **300 are global** and now unrecognised, about **60 have a region** (6 Europe, 2 North America, 1 Japan in the sample) and about **40 are emerging-market funds** recorded as `Global/Mixed`; about **285 state nothing**. These are estimates with the intervals above, not counts.

### Funds labelled `Global` on KIID text only (population 483; sample 80)
| Group | n | Confirmed by the document (95 % interval) | Not stated |
|---|---|---|---|
| All | 80 | **84 %** (74-90) | 13 |
| Spanish | 76 | 83 % (73-90) | 13 |
| English | 4 | 100 % (51-100) | 0 |
| Bonds and money market | 22 | **64 %** (43-80) | 8 |

So roughly one in six of these `Global` values rests on no mandate, and one in three among bonds and money market. One cause seen: "calcular la exposición **global**" is the risk-measurement method (VaR), not a geography. "May invest in emerging markets" and an index trademark notice are others.

## What the extractor misses (observed in the development sample only)
- Global: "podrá invertir en todo el mundo", "de emisores de cualquier lugar del mundo", "companies located around the world", "invierte a escala mundial / global", "invests globally", "sin limitación geográfica", "mercados desarrollados y emergentes", "países OCDE y emergentes", "emisores nacionales y extranjeros".
- Region: "bonos europeos", "emisores alemanes", "deuda pública estadounidense", "domicilio o actividad en Japón", "bonds issued by European companies".
- Emerging: "principalmente en un mercado emergente", "companies based in emerging market countries".

## Labelling rules used (revise them if you disagree)
A currency of denomination is not a region. A benchmark or performance objective is not a mandate. Developed plus emerging, OECD plus emerging, "domestic and foreign", and "US and non-US issuers" count as global. "May invest in emerging markets" is not a mandate. German issuers count as Europe.

## Limits
- The labels are the assistant's, read from the sentences about investing (not the whole document): "not stated" may hide a statement elsewhere, so the stated share is more likely too low than too high.
- In the KIID-only file, the quote of a `global` row is extracted by the tool (marked `[auto]`); twelve were checked. The other labels carry the sentence read.
- The 15 documents displayed while analysing the problem are not in the holdout.
- The intervals are wide per language; English KIID-only (n = 4) says almost nothing.

## Spot check (ten rows, fixed order)
| ISIN | Label | Evidence |
|---|---|---|
| LU0827880930 | global | "invests globally in equity securities" |
| LU2415445837 | not stated | corporate and government bonds, no region |
| LU1295552464 | global | "companies located around the world" |
| IE00BYP0Y993 | global | US and non-US issuers |
| LU1679113404 | global | "invierte a escala global" |
| LU0415415636 | not stated | time deposits and money market |
| DE0009848077 | global | "acciones de emisores internacionales" |
| LU0992631217 | not stated | debt denominated in euros or other currencies |
| DE000DWS1UL0 | not stated | covered bonds denominated in euros |
| LU1663932728 | not stated | a list of asset types, no region |

## Decision for the owner (Step 1 is a stop point)
Go to Step 2 (extraction fix from these sentences, then the sealed holdout judges it) or stop here and only record the source of each value (Step 3).

## Step 2: extraction fix judged on the sealed holdout (2026-11-25)

The owner ordered Step 2, approved the labelling rules and the twelve spot-check rows, and kept database writes frozen. Patterns frozen in commit `0a16ab5` (`GEO-PATTERNS-FROZEN`) before any holdout text was read; the code has not changed since. The 150 holdout funds were labelled without running the patterns on them (`geo_ground_truth/labels_holdout.csv`; evidence extracted by the tool, marked `[auto]`).

| Sample | Values written | Right | Precision (one-sided 95 % bound) | Wrong named regions | Share of stated mandates found |
|---|---|---|---|---|---|
| Development (training, not evidence) | 55 | 55 | 100 % | 0 | 93 % |
| KIID-only (partly independent) | 51 | 48 | 94 % (86 %) | 0 | 72 % |
| **Sealed holdout** | **56** | **55** | **98.2 % (92.4 %)** | **0** | **61 %** |

- **Acceptance:** no wrong named region (met) and precision of written values at least 95 % (met).
- **Owner ruling (2026-11-26): the single disputed value counts as correct.** `LU0794123256` reads "se puede invertir en todo el mundo, lo que incluye los mercados emergentes": global under the approved rules; the label was a labelling miss and is corrected. **56 of 56 right (100 %)**. One-sided 95 % bound: 95.4 % (Wilson), 94.8 % (exact binomial), so with n = 56 "at least 95 % with 95 % confidence" holds under the first method and falls just short under the stricter one. The table above shows the figures as first measured.
- **Recall fell from 93 % to 61 %** between development and holdout: the patterns fit the development wording. Missed mandates stay `Global` by default; nothing wrong is written by missing them.
- **Disclosed deviations from "development sample only":** the negation guard ("Asia (sin Japón)") came from the replay (which excluded the holdout), the "U.S. dollars" split from a test, and some negative test sentences from the KIID-only sample.
- **Not fixed:** the current extractor's own false `Global` values (13 of 80 KIID-only) come from benchmark names (`msci world`, `bloomberg global aggregate`) and from worldwide phrases outside a mandate, not from "exposición global" as the Step 1 text above says. That statement was wrong.

### What switching it ON would change (read-only replay, current universe, 2,943 funds)
- 29 stored `Geography` values, all from `Global`: 19 to Europe, 9 to North America, 1 to Japan. No stored named region changes.
- 23 funds marked `Development_Status = Emerging` (`Geography` stays `Global`).
- The fallback recognises a mandate in 309 funds; for most of them the value stays `Global`, now with a sentence behind it. That matters only once the source is recorded (Step 3).

`GEO_MANDATE_FALLBACK_ENABLED` ships OFF. No database value has changed.

## Delivery (2026-11-26, on the owner's order)

- **Switch** `GEO_MANDATE_FALLBACK_ENABLED` ON. P1 pass on the 52 affected funds, family builder and refresh. Full `fund_master` diff: only those 52 funds; `Geography` and nothing else changed on 29 (19 Europe, 9 North America, 1 Japan, all from `Global`, identical to the replay) with their `Investment_Universe`; `Development_Status` on 52 (29 to Developed, 23 to Emerging); no named region changed; no `GEOGRAPHY_UNIVERSE_WARNING`.
- **Column** `fund_master.Geography_Source` added by the owner; backfill wrote 3,715 rows, that column only; a second run changes 0. Rows with a `Geography` and no source: 0.

| Source (all funds, retired included) | `Global` | Named region |
|---|---|---|
| kiid | 1,377 | 745 |
| name | 167 | 591 |
| mandate | 299 | 40 |
| index | 0 | 17 |
| default | 467 | 0 |
| unreproduced | 8 | 51 |
| wrong_doc | 4 | 1 |

(6 funds have no `Geography`.)

- **Level of the default `Global` at delivery**, by nature (the closing condition): Renta Fija Corto Plazo 128, Renta Fija Flexible 111, Mixtos 91, Renta Variable 70, Monetario 37, Alternativo 30.
- **Monthly report:** a `default` or `wrong_doc` value prints "not determined" (9 of the 30 rows of the PRODUCTION portfolio on this date).
- **Left open:** the 51 unreproduced named regions come from older rules and are kept; the current extractor's own false `Global` values are not fixed; blanking the defaults stays deferred.
