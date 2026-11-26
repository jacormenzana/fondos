# PREREG P3-7: the cost study, judged at the fees the owner pays (the KIID-stated fees)

**Status: PREREGISTERED 2026-11-21 on the owner's sign-off of D1-D6 (section 10), committed BEFORE any code or number of this study.** Written the same day from the results of P3-5A (`doc/audit/P3_5A_TRIALS_REPORT.md`) and the decisions applied after them. Ticket: FND-0438. Section 11 records the three precisions added when the owner's conditions on D2 and D4 were checked; they change no value of the draft he reviewed. The page keeps its draft wording where it was not changed; "the owner's fees" means the stated fees (D1).

## 0. Decisions of 2026-11-21 that this study applies (from the results of P3-5A)
L1 (peer-relative filters) and L3 (recovery modulation) are CLOSED: no evidence (L1 deepens the 2008 drawdown and its return gain is not distinguishable from zero; L3 is in recovery on 1.1 % of the dates). The code stays in the repository, OFF. L2 (overextension) is PARKED inside P3-6 (FND-0437), not studied on its own. Amendment 2 of P3-5A (the ceiling at the flat 25 bp) is NOT executed: it could not change any verdict, since condition 4 fails for every trial. The defect it would have patched, a risk KPI that was undefined at the stated fees, is fixed here, before any number is read (section 5).

## 1. Question
The finding of P3-5A that matters is not a lever: at the stated fees the baseline nets **0.08 % a year**, the costed matched pool **1.54 %**, and the baseline's own drawdown at those fees is -26.4 % (against -14.6 % at the flat 25 bp). The selection rotates about 32 % of the portfolio a month and pays entry and exit fees on each rotation. Two questions, in this order:
- **Q1 (primary).** Which combination of the cost levers (hysteresis, fee-aware class choice, a fee penalty in the ranking) raises the net return at the owner's real fees, within the 15 % drawdown mandate?
- **Q2 (the plan's option "retire ranking complexity", C2).** Does the ranking beat a matched pool once both are run at the SAME turnover and the same costs? At 32 % against 15 % turnover the comparison of P3-5A was unfair to the ranking; this asks it fairly.

## 2. What is already known, and how much it can be trusted (in-sample, today's fees, survivors only)
- Stated fees, hysteresis grid (cost check 2026-11-19): net return 0.08 % (band 0), 1.67 % (0.05), 1.92 % (0.10), 2.34 % (0.20), 2.44 % (0.40); the drawdown at those fees -26.4 % at band 0 and -14.8 % to -15.7 % at the others; turnover 32 % a month at band 0, 14.8 % at 0.10, 11.3 % at 0.20.
- L4 (fee penalty in the ranking) at the REALISED holding (about 7 months): +2.4 pp a year, 2.5 % net, turnover 27.8 %, the champion beats the pool (informative runs of P3-5A, outside its verdict). At the preregistered H of 36 / 24 months: +0.70 / +1.13 pp.
- The fee-aware class choice (`P3_FEE_AWARE_CLASS_ENABLED`, built and OFF, session 1) has never been run in the PIT, which carries no one-off fees in its frames.
- The effects here (about 2 pp) are well above the minimum detectable effect of the history (0.6-1.2 pp), unlike the effects of P3-5A: this study CAN be tested on the history, for what an in-sample test is worth.

## 3. The dependency that only the owner can resolve (D1)
**ANSWERED 2026-11-21 (owner: "I pay commissions reflected in KIID").** The fees he pays are the ones the KIID states, which are the stated fees of `fund_master` / `fund_cost_schedule` the model already uses. The primary regime of every number below is therefore the **stated fees, fund by fund**; the flat 25 / 50 / 100 bp stay as cross-checks. The paragraph below is kept as written before the answer. Residual caveats, to test rather than ask: a KIID entry/exit fee is a maximum (a platform may charge less, never more is assumed), and funds whose fees are missing keep the rule of the draft (an unknown fee never wins a tie).

Every cost number rests on one unverified assumption: that the stated fee of a fund is what the owner pays (PKG_L section 1, 2026-11-19). If his platform lets him buy a zero-entry-fee class, or rebates the entry fee, the cost problem shrinks to the exit fee (zero for 94 % of the funds) and a spread, and the study changes from "fight the fees" to "the rotation is almost free". **The study does not start before D1 is answered.** Needed from him: the platform or platforms, the entry / exit / custody / transaction charges he actually faces, and whether zero-entry classes are available for the funds the model selects. The primary regime of every number below is whatever he states; the stated fees and the flat 25 / 50 / 100 bp are reported next to it.

## 4. Design
**Levers (each OFF unless in the trial).** H = hysteresis band on the incumbents (`PORTFOLIO_HYSTERESIS_BAND`, built and preregistered in Amendment 5 of `PREREG_FND-0193`), values {0.10, 0.20}; C = the fee-aware class choice, tolerance 0.02 and the rule of session 1; P = L4, the fee penalty `return_ann_real - (entry + exit) / H` with H fixed at **0.58 years (7 months, the baseline's realised mean holding of 6.9 months rounded)**. H = 7 months is a value chosen after seeing P3-5A (provenance table); the C4 rule of P3-5A, which excluded it as a near-veto, is replaced by the direct evidence of the informative runs.
**Trials (8; the baseline is re-run in the same build).** A1 = H 0.10; A2 = H 0.20; B = C; C = P; AB = H 0.10 + C; AC = H 0.10 + P; ABC = H 0.10 + C + P; D = H 0.20 + P. Values less intrusive first; every run is built from scratch; the DB-derived inputs (IPC, fees, geography) are frozen in a snapshot BEFORE the first run (the lesson of the integrity check of P3-5A).
**Comparators for Q2 (not trials, not in the Holm family).** The matched pool at the SAME band as each champion arm: persistent random selection from the eligible set through the production engine with the same incumbents rule (`pit_benchmarks.random_selection_weights(persistent=True)` with the band), 20 draws, costed like the champion. Known bias, stated in advance: persistence makes such a pool hold survivors of past performance (an age effect of -1.16 pp a year in P3-5A's D5), which flatters the ranking; the redraw each month (R2) has the opposite bias (higher turnover). Q2 is read against BOTH and a verdict of "the ranking earns its place" requires beating both (D2).

## 5. Metrics and the adoption rule (carried from P3-5A with the defects fixed ex ante)
Primary regime: the owner's fees (D1); cross-checks: the stated fees, the flat 25 bp. Conditions, all at the primary regime unless said:
1. **Drawdown (changed, D3).** In P3-5A the baseline itself failed the 15 % cap under the tail stress (-15.9 %), so the condition as signed could not be passed by a variant that only matched the baseline. Proposed: the unstressed full-sample drawdown of the master portfolio at the flat 25 bp is at most 15.0 % AND the stressed worst window (the primary stress of P3-5A, unchanged) is no deeper than the baseline's by more than 0.5 pp; the same in each informative episode window. The drawdown at the primary regime is reported next to it.
2. **Improvement.** A lower bound above 0 (Holm over the 8 trials: level 0.05 / 8 = 0.00625) AND a point estimate of at least 0.5 pp a year on the net return at the primary regime, paired monthly difference against the baseline, the stationary bootstrap of P3-5A. No ceiling KPI: it was undefined at the stated fees and is not a gate here; it is reported only where the baseline's own is defined.
3. **No collateral damage** (unchanged): non-crisis degradation at most 0.35 pp a year, the share of 36-month windows beating IPC+M3 not lower than the baseline's.
4. **The matched pool at the same turnover is beaten** (the comparators of section 4, both).
A trial is a forward-shadow CANDIDATE only if all four hold (R7); history never adopts.

## 6. Contamination, bias and the confirmation
- **Everything above was read in P3-5A**, including the informative L4 runs and the hysteresis grid of the cost check: the values of H (0.10, 0.20, 7 months) are chosen after seeing them (provenance table, section 9).
- **There is no independent historical confirmation left**: the locked episodes were opened and read by P3-5A, so they are spent. The confirmation of this study is the FORWARD SHADOW (the `P3_HYSTERESIS_SHADOW` stream of Amendment 5 and the shadow rows of the levers), which is slow (about one independent month per row). The historical runs are a design and a safety test only.
- Fees and attributes are today's values (a look-ahead); the panel holds no dead funds (FND-0436: it flatters any lever that keeps funds longer).
- Reporting order: the design window (2008), the full sample, the confirmation episodes, then the adoption table (they are still read in that order, for the record).

## 7. Relation to the other studies
- Amendment 5 of `PREREG_FND-0193` (P3-2') fixes the criteria for the hysteresis band and cadence on their own and its shadow stream keeps running; this study tests hysteresis only as one factor next to the other cost levers and does not replace Amendment 5's decision rule (D4: whether the P3-2' re-sync of R8 is folded into this study).
- P3-6 (FND-0437, geographic caps, construction layer) is independent and starts later; L2 is parked there.
- Ranking-layer levers other than the fee penalty are out of scope.

## 8. Engineering prerequisites (only after sign-off; tests first; nothing is built now)
Fees in the PIT frame for C (the PIT carries them only for L4 today); a per-trial switch for C (it reads a global switch today); the hysteresis band as a trial parameter of the runner (`prepare_pit_backtest(constraints=..., use_incumbents=True)`); the pool comparators with the band; the frozen input snapshot; the stated-fee, owner-fee and flat regimes in one report; the baseline of this study re-run and compared with the sealed run before anything else.

## 9. Provenance (value / source / chosen before or after seeing P3-5A and the cost check)
| Parameter | Value | Source | Before or after |
|---|---|---|---|
| Hysteresis band | 0.10, 0.20 | the grid of Amendment 5 (owner), values selected from the cost check | after |
| Class-choice tolerance | 0.02 | external review, session 1 | after the diagnostic |
| H of the fee penalty | 7 months (0.58 years) | the baseline's realised holding, informative runs of P3-5A | after |
| Holm family | 8 trials (0.00625) | the owner's rule of P3-5A applied to this study | before |
| Materiality floor, degradation, drawdown cap, bootstrap | 0.5 pp, 0.35 pp, 15 %, block 6 / 4,000 resamples | P3-5A, unchanged | before this study |
| Drawdown condition vs the baseline | 0.5 pp | the assistant, after finding that the baseline fails the signed condition | after |
| Comparator draws | 20 | the assistant | before |

## 10. The owner's decisions (all closed 2026-11-21)
- **D1:** he pays the commissions reflected in the KIID, i.e. the stated fees; primary regime = stated fees fund by fund (section 3).
- **D2:** BOTH the persistent and the redraw matched pools must be beaten, at the same costs (precision in section 11, item 1).
- **D3:** the drawdown condition is relative: the stressed worst window no deeper than the baseline's by more than 0.5 pp (section 5, item 1); the absolute 15 % under stress stays reported, not gating, because the baseline itself is at -15.9 %.
- **D4:** this study folds in the P3-2' re-sync of R8, on condition that it consumes no degree of freedom outside the counted N (checked, section 11, item 2).
- **D5:** the eight trials and H = 7 months, approved; the exact breakdown is validated in section 11, item 3.
- **D6:** L2 stays parked in P3-6 (FND-0437) and L1 / L3 stay closed, as recorded.

## 11. Precisions written when the owner's conditions were checked (2026-11-21, before any code; no value of sections 4-9 changes)
1. **How each pool is costed (D2).** Each arm and each comparator pays the stated entry and exit fee of EACH fund it buys or sells, on its OWN turnover, through the same engine; nobody is given the champion's turnover or cost. The turnover of every arm and pool is reported next to its net return. The persistent pool runs at the band of its champion arm (so its turnover is close by construction); the redraw pool is the opposite bias and is expected to turn over more: that is why a verdict requires beating BOTH, and the report states which of the two the champion beat. Condition 4 therefore reads net return after costs, never gross.
2. **P3-2' is folded in with no new degree of freedom (D4).** (a) Amendment 5 states its re-sync clause for "after any adopted P3-5a lever"; none was adopted, so the clause is not triggered and folding is a convenience, not an obligation. (b) The bands of this study (0.10, 0.20) are two of the four of Amendment 5's frozen grid, at the monthly cadence, which is the grid's own baseline cadence: no new band or cadence value is created. (c) The cadences (3, 6, 12 months, phase-averaged) are NOT tested here, so Amendment 5's cadence question stays open in its own shadow stream. This study is not "the ONE re-run" for the cadence. (d) The values of Amendment 5 (turnover 30 %, cost drag 0.50 pp, tolerance 0.10 pp, drawdown tolerance 1.0 pp, the 25 / 100 bp checks) are NOT re-synchronised: the runs A1 and A2 are ALSO read against Amendment 5's decision rules exactly as written, as a second, separate verdict on the band alone (a band is its own question there; the 0.5 pp of section 5 applies to the combined candidates of this study only). The two verdicts are reported side by side; if a candidate of this study contains a band that Amendment 5's rules reject, the case goes to the owner and no rule is changed (the same way Amendment 5 sends its age finding to the owner). (e) N: the 8 trials are added to the current count of the ledger scan by this dated page, conservatively (A1 and A2 repeat two cells already counted by Amendment 5), for the deflated Sharpe ratio and the probability of backtest overfitting; the Holm family of the adoption test stays 8 (0.00625), because Amendment 5's rules are deterministic arithmetic and use no significance level. `LEDGER_FND-0193.json` stays locked.
3. **The eight trials, validated (D5).** One factor each, bands first: A1 (band 0.10), A2 (band 0.20), B (fee-aware class choice), C (fee penalty, H = 7 months). Combinations: AB, AC, ABC, all at band 0.10, and D (band 0.20 + penalty). That is 4 single + 3 at band 0.10 + 1 at band 0.20 = 8, no trial is duplicated. The design is deliberately NOT the full 2 x 2 x 2 factorial: band 0.20 is never combined with the class choice (the pair AB at 0.20, and the triple at 0.20, were not run for economy; the Holm family counts exactly the 8 above and no unlisted run may enter the adoption table). The re-run baseline (band 0, no lever) is the reference of every difference and is not a trial.

## 12. Log of what was read and what went wrong (2026-11-21 / 22, written after the runs; nothing here changes a value of sections 4-11)
- **The snapshot** (id `42d92e50d483...`, IPC 322 rows against the 321 of P3-5A) was frozen before the first run; the three inputs came only from it.
- **The identity gate failed once on my own check, not on the reproduction**: it required a forward return on the last date, which the sealed table does not have either (NaN in both). Every reproduction check had passed (score_base difference 0.0, eligible set, gross 0.0, turnover 7.8e-16). The check was corrected test-first to "the same months in both" and the gate re-evaluated from the saved baseline; the baseline was later rebuilt on the final code and passed again.
- **First build, arm B was a no-op** (found by an integrity check, before any performance number was read): the engine-level class choice had nothing to choose because the PIT scorer already keeps one class per family. B equalled the baseline and AB equalled A1 exactly (an equality test of the series, no returns printed). The choice was moved into the scorer's own family de-duplication (per-trial parameter), the first build was set aside in `superseded_first_build/` (never read for performance), and ALL arms and the baseline were rebuilt on the final code (commit of the fix before the rebuild).
- **The adoption table was read once, after every arm and pool of the final build finished**; then, as descriptive context outside every rule, the gross annual return of each arm, of the pools and of the IPC+M3 target.
- **Smoke runs on a sample** (4 funds per nature) exercised the plumbing and found two trailing-NaN defects (pool statistics, Amendment 5 verdict), fixed test-first; no sample number was used.

## 13. Amendment 1 (2026-11-22): how the rows of the AC forward shadow are read. Written and committed BEFORE any row of `P3_FEE_PENALTY_SHADOW` exists or is read; no value of sections 4-12 changes.
- **Scope.** The stream `P3_FEE_PENALTY_SHADOW` (`proyecto3/src/fee_penalty_shadow.py`, commit `5966367`) records, once per real build, the AC arm (band 0.10 + fee penalty, H 0.58 years) next to the live arm (`off`, the DRAFT) in two streams (`one_step` from PRODUCTION, `chain` from its own previous row). Three series are compared, all rebuilt from stored rows: **L** = the live arm (consecutive DRAFTs, monthly rebalance = the baseline of the PIT), **B** = the band-only chain (`P3_HYSTERESIS_SHADOW`, Amendment 5 of PREREG_FND-0193), **AC** = this chain. The owner's decision of 2026-11-22: AC is the candidate, D is rejected, arm B (class choice) is retired.
- **What has been read (disclosure, R9).** The PIT results of AC (section 12 and `doc/audit/P3_7_TRIALS_REPORT.md`; in-sample, survivors only, today's fees, the arm chosen after reading them). For this amendment only the STANDARD ERRORS of the paired monthly net-return differences were computed (`pit_p35a_prep.paired_se_annual_pp`, which never returns a point estimate); the point estimate of AC minus A1 was not read. Of this stream: nothing (no row exists). Of the hysteresis stream: as disclosed in Amendment 5.
- **Feasibility (R1; stated fees, paired stationary bootstrap, 261 months, scaled by sqrt(261 / T), one-sided level 0.05, power 0.80).**

| Comparison | SE of the whole history (pp a year) | MDE at 12 / 24 / 36 months (pp a year) | Historical gain it must detect |
|---|---|---|---|
| AC against L (the baseline) | 0.355 | 4.12 / 2.91 / 2.38 | +2.96, mostly fee arithmetic |
| AC against B (A1) | 0.257 | 2.98 / 2.11 / 1.72 | about +1.2 |

  Consequence: the forward RETURN stream can show AC against L only from about 36 months, and can NOT separate AC from B inside the FND-0299 horizon (MDE 1.72 against about 1.2). Therefore AC against B is judged on the deterministic COST stream (turnover and the stated fees paid, arithmetic on stored weights), and the return comparison with B is reported, never gating. The 12-month reading of return is descriptive (MDE 4.12): it can only trigger an alarm, never a pass.
- **What counts.** One row per calendar month: the LAST `SHADOW` row of the month (rebuilds in the same month are not independent and are excluded from every statistic). A month with no `SHADOW` row (an `ERROR` row, no build) is a gap, reported; the first row after a gap is excluded from the chain statistics (its incumbents come from PRODUCTION). More than 1 gap in the 6 months of a review postpones it by one month. Turnover is RECOMPUTED by the reader between consecutive counted rows of each series (never taken from the stored `traded_notional`, which a same-month rebuild distorts). Cost at the primary regime = per fund, entry fee on the notional bought and exit fee on the notional sold, with the stated fees of `fund_master` frozen in a hash-verified snapshot at each review (the lesson of P3-7). A row of month m earns the return of month m+1 (month-end NAV to month-end NAV, the PIT convention), so a month is read only after the next one closes.
- **Before the first review only integrity is displayed** (first live row, Step 2 of the roadmap): status `SHADOW`, scenario id, band, H, funds per sub-portfolio, weights summing to 1 per series, `calc_versions`, `n_candidates`, `n_fee_unknown`. Turnover, cost and selection-difference fields are not displayed before the first review.
- **Parameters.** Values marked A5 are the owner's working values of 2026-11-21 (Amendment 5 of PREREG_FND-0193, reused unchanged); values marked N are the assistant's proposal, committed on the owner's order of 2026-11-22. One dated re-sync of any value is allowed before the first review, never after it (R8).

| Name | Value | Used in |
|---|---|---|
| `turnover_reduction_min` (A5) | 30 % | R6 rule 1 |
| `cost_drag_reduction_min` (A5) | 0.50 pp a year | R6 rule 2 |
| `ac_vs_band_cost_saving_min` (N) | 0.50 pp a year (the same materiality as the line above) | R6 rule 3 |
| `net_return_tolerance` (A5) / `max_dd_tolerance` (A5) / `mandate_dd_cap` (A5) | 0.10 pp a year / 1.0 pp worse / 15.0 % absolute (if L itself is deeper: "not worse") | R24, R36 |
| `revert_return_gap` (A5) / `revert_dd_gap` (A5) | 0.50 pp a year / 2.0 pp deeper | alarm at R12, revert once adopted |
| review months (N) | 6, 12, then 24 and 36 (the FND-0299 checkpoints, 2028-10 and 2029-10) | all |
| `dd_informative_min` (N) | the drawdown guard counts only if L's own forward drawdown in the window is at least 3.0 % | R12, R24, R36 |

- **Reviews (deterministic; the report states each rule PASS / FAIL / NOT INFORMATIVE and never a blend).**
  - **R6 (6 counted months, about 2027-05 if builds run monthly): cost rules, at the primary regime.** (1) AC's turnover is at least `turnover_reduction_min` below L's; (2) AC's cost drag is at least `cost_drag_reduction_min` below L's; (3) AC's cost drag is at least `ac_vs_band_cost_saving_min` below B's. For reference the PIT values are turnover 13.4 against 32.2 % a month (L) and 14.8 % (B); cost drag 1.00 pp against 4.61 pp (L) and 2.23 pp (B). All three PASS = "AC cost-confirmed".
  - **R12 (12 months): alarm only.** The forward net return of AC below L's by more than `revert_return_gap`, or its drawdown deeper by more than `revert_dd_gap`, raises an ALARM to the owner (descriptive: MDE 4.12 pp); no alarm is not a pass.
  - **R24 and R36 (paired bootstrap of `pit_oos`, the FND-0299 checkpoints): AC against L only.** Non-inferiority of the forward net return at the stated fees (lower bound of AC minus L above minus `net_return_tolerance`) AND the drawdown guard (no worse than L's by more than `max_dd_tolerance` and within `mandate_dd_cap`; NOT INFORMATIVE under `dd_informative_min` is carried to the next review, never a pass). The superiority of AC over L and any return comparison with B are reported, not gating.
- **Consequence.** No review adopts anything (R7). AC is proposed for adoption only if R6 passed at its last review and R36 passed on both tests; the owner's order then switches `PORTFOLIO_HYSTERESIS_ENABLED` and `P3_L4_FEE_PENALTY_ENABLED` with `P3_L4_H_YEARS` = 0.58 (the shadow's own H is a separate constant; both ship OFF until then, R2). If R6 rule 3 fails but rules 1 and 2 pass, the band alone (B) is the candidate and the penalty is dropped. The revert trigger of Amendment 5 applies to whatever is adopted. The reader of these rows is a read-only script built AFTER the first live row has been verified (tests first); it is not part of this commit.
- **Not decided here:** the gross-return gap (separate studies, one layer at a time), the mandate parameters, and any change of the band or of H.

## 14. Amendment 2 (2026-11-26): adoption route of AC and the unknown-fee rule of every cost reader
Written and committed **before the first review** and before any turnover, cost or selection-difference field of `P3_FEE_PENALTY_SHADOW` or `P3_HYSTERESIS_SHADOW` is read (R8, R9). The owner's rulings of 2026-11-26 (`doc/decisiones/DECISION_P3_7_AC_ADOPTION_ROUTE.md`) are the authority. This amendment changes a RULE of section 13, not one of its values, so it does not use the one dated re-sync of values that section 13 allows. Every value of sections 4-13 stays as it is unless it is named below.

- **What has been read (disclosure, R9).**
  - The P3-7 PIT report and adoption table: in-sample, already disclosed in sections 12-13.
  - The read-only fee-coverage check of the live candidate pool, 2026-11-26, from the metrics frame and `fund_master`, no shadow field. Of the 942 candidate funds, 202 lack an exact fee on at least one side; 180 of those state a ceiling and 22 state nothing.
  - From the shadow rows: only the integrity fields that section 13 allows (`n_candidates`, `n_fee_unknown`).
- **1. Adoption route (replaces the first sentence of section 13, "Consequence").** The fee penalty is treated as a structural rule (`PKG_L` R7). AC is proposed for adoption at the R6 review if ALL of the following hold:
  - (a) R6 rules 1-3 PASS at the primary regime, with the fill of item 2;
  - (b) the coverage condition of item 3 holds;
  - (c) on the PIT cost grid, rule 3 of Amendment 5 of `PREREG_FND-0193` applied to AC against the baseline holds on the P3-7 build: at 25 bp per side the net return is no lower than the baseline's minus `net_return_tolerance_25bp` (0.15 pp a year), and at 100 bp it is higher. The 25 bp half is already on record (`adoption_table.csv` of the build `20261121_p37`): AC nets 3.71 % a year against the baseline's 3.69 % (paired monthly mean -0.01 pp, lower bound -0.76 pp). The 100 bp half is computed after this commit from the stored series;
  - (d) the owner's order.

  The order switches `PORTFOLIO_HYSTERESIS_ENABLED` and `P3_L4_FEE_PENALTY_ENABLED`, with `P3_L4_H_YEARS` = 0.58. The fallback of section 13 is unchanged: if R6 rule 3 fails but rules 1 and 2 pass, the band alone (B) is the candidate and the penalty is dropped. The band alone keeps its own route: Amendment 5 at its 6-row review (owner ruling 2026-11-26, which decouples it from the FND-0299 holdout).
- **After adoption.**
  - R12 stays an alarm.
  - R24 and R36 (the same tests, AC against L, the same parameters) become checks after adoption: a FAIL on either test means REVERT.
  - The revert trigger of Amendment 5 also applies, with AC as the adopted arm and L as the baseline arm: `revert_window` 12, `revert_return_gap` 0.50 pp a year, `revert_dd_gap` 2.0 pp, `revert_turnover_ratio` 0.5.
  - A REVERT verdict is reported by the reader, and the owner's order switches both flags OFF (a one-off authority event, D-0).
  - **Engineering prerequisite of the adoption (built test-first before the order, shipped with it):** once AC is live, L (band 0, no penalty) keeps being recorded as a shadow arm, so that R24, R36 and the revert trigger still have their baseline.
- **2. Fill of an unknown fee in EVERY cost reader of this stream (R6, R12, R24, R36 and the revert trigger).** Per fund and per side (entry, exit), the first of:
  1. the exact value (`Entry_Fee_Pct` / `Exit_Fee_Pct`, ratios);
  2. the declared ceiling (`Entry_Fee_Pct_Max` / `Exit_Fee_Pct_Max`, percent divided by 100), consistent with D1: the KIID maximum is what the owner pays;
  3. the 90th percentile of the exact values of that side among the funds of the same `Fund_Nature` in the review's frozen fee snapshot; if a nature has no exact value on that side, the 90th percentile over all natures.

  The fill is applied identically to L, B and AC. The verdicts use it. Next to it the reader reports an optimistic variant, with a fund without any figure costed at 0; that variant is never gating. The stored `rotation_cost_estimate` of both shadows (which costs an unknown entry at 0) is not used by any rule. The fill of the ranking penalty (`ranking_levers.l4_penalty`, the median of the nature cell) is NOT changed: it defines the AC arm, and changing it would break the stream.
- **3. Coverage condition (gating at R6).** In the review window, at least `fee_coverage_min` = **95 %** of AC's bought notional must be in funds with an exact value or a declared ceiling on BOTH sides. Bought notional is the sum of the positive weight changes between consecutive counted rows, recomputed by the reader as in section 13. Below the threshold, R6 is NOT INFORMATIVE and carried to the next review, never PASS. The same share is reported for L and B, and the exit coverage of sold notional for all three; these are not gating. Derivation (the assistant's; value set by the owner 2026-11-26): AC buys about 1.6 times capital a year at its PIT turnover (13.4 % a month). A share u of bought notional costed at 0 while paying 5 % (the p90 of the exact entry fees) biases the cost by u × 8.0 pp a year; u ≤ 5 % keeps it at most 0.40 pp, below the 0.50 pp materiality of R6 rules 2 and 3.
- **New parameters** (added to the table of section 13).

| Name | Value | Used in |
|---|---|---|
| `fee_fill` | exact → declared ceiling → p90 of the nature (frozen snapshot), symmetric over L, B, AC | every cost reader of this stream |
| `fee_coverage_min` | 95 % of AC's bought notional, both sides, exact or ceiling | R6 (NOT INFORMATIVE below) |

- **Not changed:** the reader is still built only after the first live row has been verified (tests first); the 12-month alarm; the review months; every value of the parameter table of section 13. The one-off validation of the PIT cost under this fill (a rebuild of the stored-weight series of L, A1 and AC, ordered 2026-11-26 as a document check) runs AFTER this commit and cannot change any rule of it. Its result is recorded in the decision note.
