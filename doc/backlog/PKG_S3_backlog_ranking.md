# Backlog ranking for FND-0425 (rewritten 2026-11-17) - ONE page, rewritten in place

Read-only analysis (skill `backlog-priorizer`). The analysis moved nothing; the closures and the ordering below are the owner's rulings. Facts are marked **[F]** (read from the backlog, the code or the database), inferences **[I]**, hypotheses **[H]**.

## 1. Owner ordering (ruling 2026-11-17)
1. **FND-0427 and FND-0428 (HIGH) first:** metric and report correctness take precedence over any refactoring.
2. **FND-0426 (MEDIUM) after the two HIGH fixes:** the `ytd` horizon is restored once core metric and report integrity is settled.
3. **FND-0429 (MEDIUM) is a GATE before the P3 refactor:** validate whether the scoring layer beats a baseline before executing "P3 first". The refactor scope follows the verdict.
4. Everything else keeps the order of section 3.

## 2. Executive verdict
- **Risk: MEDIUM.** The two HIGH items are about published numbers (rolling windows the NAV history does not cover; an excess return that mixes a real and a nominal figure), not about live selection failures **[F]**.
- **45 open items [F]**: 30 DEFERRED with review dates, 6 IN_PROGRESS, 6 OPEN, 2 BLOCKED, 1 TODO (read at the time of writing; another session moves tickets of Package I meanwhile). The 9 closures of 2026-11-15 (0354, 0390, 0364, 0388, 0267, 0395, 0396 and the four of 17:14: 0033, 0035, 0180, 0206) are reflected.
- **Package I code is already delivered by another session [F]** (commits `02c885e`, `c926984`, `209af91`, `b42ee0f`: rolling-window coverage rule, YTD metric, scoped report headers; `CALC_VERSION` 20261116, recomputed). 0426, 0427 and 0428 are still OPEN: what is left for them is **verification against the live data and the closing probes**, not new construction.
- **Decision required:** confirm the verification work on 0427 / 0428 / 0426 (section 5, step 1-2) and run the full-universe PIT that opens the FND-0429 gate (owner-run, about 20-25 minutes cold).

## 3. Evidence coverage
| Source | Scope | Retrieved | Limitations | Completeness |
|---|---|---|---|---|
| `gestion.backlog` | all non-CLOSED, 2026-11-17 | 45 of 45 | none | complete |
| ticket logs | last non-system log of the candidates | partial by design | older history not read | partial |
| confirmed links | between open items | 8 CONFIRMED, 3 PROPOSED | the 3 PROPOSED not read | complete for CONFIRMED |
| `fund_metric_alerts` | 2026-07-31 .. 2026-11-15, aggregates | 18,376 rows, 3,706 funds | no ticket reference column: no alert-to-ticket correlation | complete (aggregates), no causal reading |
| `fund_metrics` horizons | `ytd` and `rolling_*` return_ann, today | counts only | counts, not a value audit | indicative |

## 4. Prioritised clusters
| Rank | Cluster | Priority | Items | Evidence | Confidence |
|---|---|---|---|---|---|
| 1 | **Published-number correctness (Package I)**: windows beyond the NAV history, real vs nominal excess in the monthly report | **High** | **0427, 0428** | 0427: rolling coverage is now close to the NAV history (see section 6) **[F]**; 0428: report fix committed (`02c885e`), not verified by this analysis | Medium |
| 2 | **YTD horizon restored** | Medium | **0426** | horizon `ytd` now has 7,386 rows (`return_cum`, `20261116.20261019`); it had 0 **[F]** | Medium |
| 3 | **Gate: does scoring beat a baseline** | Medium (gate) | **0429** | parked by its own text until Package I recomputed P2 (done 11-16 for 3,699 funds, 458 still on the old version) and the owner re-runs the full-universe PIT **[F]** | High |
| 4 | **P3 selection and risk policy** (to execute after the gate) | High (decision) | 0158, 0257, 0222, 0304, 0299, 0306 | the shadow wrote 2 rows, ON arm equals PRODUCTION, no ruling basis yet **[F]** | High |
| 5 | **Delivery cost** (full receipts of 506-602 s, selector in SHADOW) | Medium | 0385, 0387, 0400 | none (not a data issue) | High |
| 6 | **Manual and unscheduled operations (D-0)** | Medium | 0100, 0248, 0326, 0090, 0239 | 0078 DEPENDS_ON 0100 **[F]** | Medium |
| 7 | **Cost and policy attribute residuals** | Low | 0384, 0410-0414, 0416-0418, 0421, 0424, 0281, 0282, 0415 | 295 ALARM rows have no value: all from the 2026-08-14 batch, where every row has none; the 2026-11-15 batch has none missing; the table is keyed on (isin, metric, window) and rows the new run does not rewrite stay **[F]** (whether a consumer reads them as current was NOT checked) | Medium |
| 8 | **Classification semantics** | Low | 0397, 0398, 0402, 0324, 0325, 0185 | 0397: IE00008TZ2E8 held at 5.5 % in PRODUCTION, owner NO GO unchanged **[F]** | Medium |
| 9 | **Data provenance and coverage** | Low | 0167, 0182, 0209, 0078, 0389 | none | Low |
| 10 | **Blocked by population** | Low | 0029 | 5 WRONG_DOC funds, out of scope by standing rule | High |
| - | Unclustered | - | 0425 (this task) | - | - |

## 5. Roadmap
| Sequence | Priority | Action | Items | Dependencies | Exit criteria | Residual risk |
|---|---|---|---|---|---|---|
| 1 | **High** | **Verify the Package I fixes on the live data, then close with evidence**: rolling windows only where the NAV history covers them; the report's excess uses like-for-like returns and says what each column is | 0427, 0428 | the code and recompute already done by the other session | per ticket: a read-only check of the stored rows (and the report) against the ticket's own figures, a closing condition that passes, then the `Closes:` route | 458 funds still on the old `CALC_VERSION` until the next cycle |
| 2 | Medium | **Close the YTD item after step 1**: check `ytd` for the incident fund (NAV-derived YTD 3.09 % per the ticket) and for the NAV-short funds | 0426 | step 1 | the stored `ytd` value equals the NAV-derived one for the named fund, no value where there is no base | none known |
| 3 | Medium | **Gate**: owner re-runs the full-universe PIT, then the selection-quality question is answered with hysteresis ON in both arms (champion vs equal-weight pool) | 0429 | steps 1-2 (the recompute), the owner's PIT run | a written verdict: the scoring layer adds net value or does not, at selection and portfolio level | the PIT window is the test set already used (blind months start 2026-11) |
| 4 | High (decision) | **P3 refactor, scope set by the gate verdict**; keep the `shadow_after_build` call; absorb 0158, 0257, 0222, 0304 into one ticket | cluster 4 | step 3, owner scope | one refactor ticket replaces four; shadow rows still written | scope creep |
| 5 | Medium | Earn the selector's narrower receipt, then close | 0385, 0387 (0400 cascade stays) | a bounded change | one measured receipt, then close | full receipts stay the default until then |
| 6 | Medium | Schedule the daily NAV load and harvest when the owner lifts "not now" | 0100, then 0078 | owner | task registered, one clean run | operator cost until then |
| 7 | Low | Keep dated deferrals: 2026-11-29 (0411, 0415, 0248), 2026-12-15 (Package H residuals) | clusters 7-9 | review dates | decide each on its date | none |

## 6. Live checks behind steps 1-2 (2026-11-17, counts only)
- `ytd`: 7,386 `return_cum` rows, all on `20261116.20261019`.
- Rolling windows (nominal `return_ann`): 10y 2,549 funds against 2,571 funds with at least 120 monthly NAV rows; 5y 3,446 against 3,465 with at least 60; 1y 3,688, 2y 3,659, 3y 3,612. The small gaps are not explained here and belong to the verification of 0427.

## 7. Decisions and unresolved questions
1. **Confirm steps 1-2** as verification and closure work (the build is done), not as new construction.
2. **FND-0429 gate:** the full-universe PIT re-run is yours; the verdict decides the scope of the P3 refactor **[I]** (if scoring adds no net value, the refactor is about simplification rather than extension).
3. **FND-0078:** confirm or drop (draft spec, DEPENDS_ON 0100).
4. **Not checked:** whether any consumer (the ALARM_WATCH gate, dashboards) reads the stale 2026-08-14 alert rows as current (cluster 7).
5. **Not touched:** 0281 and 0282 (CRITICAL residuals accepted to 2027-01-15), 0299 (tracker to 2028-09-01).
