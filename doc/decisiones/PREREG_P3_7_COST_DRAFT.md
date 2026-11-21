# PREREG P3-7 (DRAFT): the cost study, judged at the fees the owner actually pays

**Status: DRAFT for the owner's review. This is NOT a preregistration**: it becomes one only when committed as `doc/decisiones/PREREG_P3_7.md` after his sign-off of section 10. No code is written for it before that (the order of Package L: prereg first). Written 2026-11-21 from the results of P3-5A (`doc/audit/P3_5A_TRIALS_REPORT.md`) and the decisions applied after them. Ticket: see the PKG_L page.

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

## 10. What the owner must decide before this can be committed
- **D1 (CLOSED 2026-11-21):** he pays the commissions reflected in the KIID, i.e. the stated fees; primary regime = stated fees fund by fund (section 3).
- **D2:** the comparator of Q2: both the persistent and the redraw pools must be beaten (recommended), or one of them.
- **D3:** the changed drawdown condition (section 5, item 1), or another way to handle a baseline that fails the cap under the stress.
- **D4:** whether this study folds in the P3-2' re-sync of R8 and so replaces Amendment 5's separate re-run, or runs next to it.
- **D5:** the eight trials and H = 7 months, or a smaller design (for example only A1, A2, C and ABC).
- **D6:** that L2 stays parked in P3-6 and L1 / L3 stay closed, as recorded.
