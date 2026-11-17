# PREREG - FND-0429 / WP5: does the champion's scoring layer add net value over the equal-weight pool?

**STATUS: SIGNED OFF 2026-11-17.** The owner confirmed the three locked values of section 0 in the working session; every other [ADOPTED] rule below was a proposal of the draft that the owner did not override. Committed BEFORE any WP5.1 / WP5.2 / M1 / M2 number exists (the PIT run `20261117_201724` only regenerated the PIT scores). Drafted 2026-11-17 from `doc/especificacion/PLAN_SELECCION_CALIDAD.md` (WP5), `doc/decisiones/PREREG_FND-0193.md` (format and inference precedent) and the PIT run `out/reports/pit_backtest/20261117_201724`.
Tags: **[LOCKED]** a value the owner confirmed by name; **[ADOPTED]** a rule of the draft that stands because the owner did not override it; **[F]** a fact read from the repo or the run; **[DERIVED]** arithmetic on a stated assumption, not a measurement; **[GAP]** something the plan leaves undefined. Every value without a tag below is quoted from the plan or the run. This page is not amended: a later change is a new dated page that cites it.

---
## 0. DECISION BLOCK - the three values the owner locked (2026-11-17, "values confirmed": as proposed)

| # | Parameter | Plan default | Proposal | What was checked before the lock | LOCK |
|---|---|---|---|---|---|
| 1 | **Significance level alpha** (CONTINUE confidence; also the maximum PASS rate of the permuted-label control) | one-sided 95 % (parameter #8, class P) = **alpha 0.05 one-sided** [ADOPTED] | **0.05 one-sided, no multiplicity correction** (one primary test: M2 at 12 months). Sensitivity only, never decisive: 0.10 and 0.025. | The history is already mined: the FND-0193 ledger counted N_scan = 437 trials (upper bound) [F, PREREG_FND-0193 section 3] and the plan counts at least 27 PIT run folders and tickets as a lower bound (23 timestamped PIT run folders on disk today + FND-0193 / 0303 / 0315 / 0318) [F, folder count]. Disclosed, not corrected: a correction would make the test impossible to pass by construction. | **0.05 one-sided [LOCKED]** |
| 2 | **Annual attrition rate** for the PIVOT survivorship bound | **5 % a year** (parameter #10, class P) | **base 5 % a year, KEPT AS AN ASSUMPTION** [ADOPTED]. **Sensitivity bounds 2.5 % and 10 % a year** (half and double). A PIVOT or CONTINUE verdict must hold at the three values; if it flips between 2.5 % and 10 %, the verdict is INCONCLUSIVE. | **Assumption, not measured** (the plan says so). The data cannot supply it today: of the 822 funds with `In_Current_Universe=0` and NAV, only about 10 are true deaths and 812 still publish NAV in 2026, so the universe history is nearly survivor-only [F, plan context]. The WP3.3 measurement replaces it when it exists; until then the verdict is conditional on the three bounds. | **5 % a year, bounds 2.5 % and 10 % [LOCKED]; remains an assumption** |
| 3 | **`MIN_MEANINGFUL_DELTA_SHARPE`** | **0.10** (parameter #15, class P) | **0.10, locked before WP5.2 runs** [ADOPTED]. It must not be tuned after seeing the feasibility result. | Evidence: the gap between B-POOL and the champion with band 0.10 is 0.75 - 0.655 = **0.095** in the 2026-11-17 run, identical to 2026-11-14 [F]. **Caution:** that 0.75 is the FROZEN-COST (frictionless) pool, the 0.655 is net of 25 bp [F, `pit_backtest.py:300`], so part of the gap is accounting, see section 5. Detectability [DERIVED]: if the plan's 90 % two-sided half-width of a portfolio Sharpe difference (+-0.058) applies, SE is about 0.035, and the minimum detectable difference at one-sided 5 % is about 0.058 at 50 % power, 0.088 at 80 % power, 0.103 at 90 % power. So 0.10 is testable at 80 % power and borderline at 90 %: section 7's power target matters. WP5.2 stops the test as untestable if the minimum detectable value exceeds this number. | **0.10 [LOCKED]** |

**P3 refactor:** stays paused behind FND-0429 until the WP5 verdict is recorded. Nothing in this page changes the production approach.

---
## 1. Question and exact comparison
**Question.** With hysteresis applied where a ranking exists, does the champion's scoring layer (`score_final` of the PIT scores) select funds that beat their peers at selection level (M2) and does the resulting portfolio beat the equal-weight pool at portfolio level (M1)? Decides whether the challenger project continues (plan WP5, outcomes CONTINUE / CONSTRUCTION-LOSS / PIVOT / INCONCLUSIVE).

**Champion arm (model under test).** The live construction (Layer 1-3, regime weights, constraints), weight method `equal` (`PORTFOLIO_WEIGHT_METHOD`) [F], hysteresis band **0.10** (the owner-confirmed value; flag `PORTFOLIO_HYSTERESIS_ENABLED` stays OFF in production, the PIT applies the band through `--hysteresis-band` / `hysteresis_experiment`) [F].
**Pool arm (B-POOL).** Equal weight over the eligible pool of every sub-portfolio, blended with the regime weights (`pit_baselines.pool_weights`) [F].

## 2. Data, cutoff and run identity (recorded, per plan 5.0)
- PIT run `20261117_201724`: git `2da6c32` (+ uncommitted files of another session: `SKILL.md`, `docs/`; no code of this study), 3,750 funds, `--current-universe-only`, 2005-01-31 to **2026-10-31** (262 month-ends), regenerated scores in `scores_long.parquet` [F]. Identical in every figure to run `20261114_152205` (risk, peers and scoring recomputed, not cached) [F].
- Eligibility: wrong-doc exclusion ON; stale NAV 60 days and NAV continuity (min coverage 0.8, max gap run 6 months) modelled; accepted audit residuals NOT modelled (look-ahead) [F, manifest].
- The run manifest does not record the weight method [F, plan]. **[ADOPTED]** the study uses the production method `equal` for both arms and the mini-prereg records it explicitly.
- **Forward holdout is not used here:** only months up to 2026-10-31 are in the sample; the blind months start 2026-11 and belong to FND-0299 [F].

## 3. Units and peers (plan 5.3)
- One class per `fund_family_id` (the cheapest accessible) [plan].
- Peers: `Fund_Nature` x ex-ante volatility tercile (from NAV as of the formation date) x FX-exposure class (`shared/fx_exposure.py`). A cell needs at least **15 families**, else it collapses to `Fund_Nature` x FX, then to `Fund_Nature` (parameter #7) [plan].
- Scores: the PIT `score_final` of `eligible` funds per (`as_of`, `subportfolio`) [plan].

## 4. Metrics (plan 5.4)
- **M2, selection (primary).** Per (`as_of`, `subportfolio`): the champion's **top quintile** by `score_final` against the **peer median** on the outcome *forward return divided by the fund's trailing 36-month volatility at formation*. **12 months is primary**; 36 months is reported and must be sign-consistent. Secondary outcome: forward Sortino with the existing downside-deviation floor. Drawdown condition: the top quintile's max drawdown must be no worse than the peer median's by more than **2 pp** (parameter #9) [plan].
- **M1, portfolio.** Net Sharpe of the champion minus B-POOL, with its paired 90 % interval [plan].
- **Sensitivity.** Repeat M2 with peers that avoid `Fund_Nature` (ex-ante volatility and FX classes only), because the nature classifier uses realized volatility over the full history; if the verdict changes the result is INCONCLUSIVE [plan].

## 5. Pool-arm parity (WP5.4 "hysteresis ON for both arms") - [ADOPTED]
**Facts [F].** (a) The B-POOL has no ranking, so it has no incumbents and an incumbent bonus has nothing to act on: hysteresis cannot apply to it in the sense it applies to the champion (`rank_weights` takes `hysteresis_band`, `pool_weights` does not). (b) The `bench_*` column of the main PIT table and the "pool benchmark" line of `summary.txt` (Sharpe 0.75, max DD -11.9 %) are the **frictionless** pool (`pit_backtest.py:300`). (c) A baseline run through `pit_baselines` goes through the same return and cost layer as the model (25 bp on the real turnover of its own weights), so a net B-POOL is obtainable.
**Parity rule proposed.**
1. **Headline M1** compares the champion at band 0.10, net of 25 bp on its real turnover, against the **net B-POOL** (25 bp on the pool's own real turnover: membership and regime-weight changes), both on the same dates, the same eligibility and the same cash leg.
2. **Reported beside it, never decisive:** the champion with no band; the frictionless pool (the 0.75 in the plan); the champion at bands 0.05 and 0.20.
3. "Hysteresis ON for both arms" is satisfied as: the champion with the band, the pool with no ranking so no band, and the cost layer identical. The mini-prereg states that this is the meaning, so nobody reads the 0.75 as like-for-like.
**[DERIVED] consequence to know before you lock `MIN_MEANINGFUL_DELTA_SHARPE`:** the 0.095 gap measured on the frictionless pool probably overstates the selection gap by the pool's own cost (not measured here; WP5.2 measures it). It does not change the proposal to lock 0.10, it changes how the number is read.

## 6. Inference [ADOPTED, following PREREG_FND-0193 section 4]
- Stationary block bootstrap clustered by date and family; mean block **6** (sensitivity 3 and 12); **5,000 resamples** for M2, **2,000** for M1 (`backtest_inference.paired_sharpe_difference`, as FND-0193); seed **12345**; M1 interval 90 % two-sided; M2 decision on the **one-sided lower bound at alpha** (decision block, parameter 1).
- Survivorship bound for PIVOT: the plan fixes the rate and "worst-decile paths" but **[GAP]** not the loss given death. **[ADOPTED]** a fund that disappears from the data is treated as if it had exited at its last NAV followed by the worst-decile 12-month forward return of its own peer cell; 1,000 attrition paths per rate; the bound is the 10th-percentile path. The owner confirms or replaces this rule when signing.

## 7. Outcome rules, controls and feasibility
**Outcomes (plan 5.5, restated; alpha, attrition and the delta come from the decision block).**
- **CONTINUE:** the one-sided lower bound of the 12-month M2 is above zero AND the 2 pp drawdown condition holds. If M1 (net, section 5) is at most zero the verdict adds **CONSTRUCTION-LOSS**: the construction study runs first and WP6 does not start until its result is recorded (it needs no vendor).
- **PIVOT:** the 12-month M2 point estimate is at most zero AND the survivorship bound cannot flip its sign at the three attrition rates, OR the champion's max drawdown is worse than B-POOL's by more than 2 pp.
- **INCONCLUSIVE:** anything else. No pivot, no vendor contact; return to 5.2.
**Controls (plan 5.1, run after the sign-off).** Permuted-label runs: the PASS rate must be at most alpha. Planted alpha in a random 10 % of funds on the real data layout: the detection rate must meet its target. **[ADOPTED]** planted size = the minimum detectable selection spread from 5.2; detection target **80 %**; 200 permutations. A failure blocks the test.
**Feasibility (plan 5.2, run after the sign-off).** Report independent periods per horizon, the minimum detectable delta-Sharpe and the minimum detectable selection spread, at **80 % power** [ADOPTED; the plan does not state a power]. If the minimum detectable delta-Sharpe exceeds `MIN_MEANINGFUL_DELTA_SHARPE`, stop: the question is untestable. [F] The history supports about 7 independent 36-month windows and about 21 twelve-month windows, and regimes have 21-65 months each (plan context).

## 8. Trial counter (plan 5.0), recorded 2026-11-17
Lower bound **27** = 23 timestamped PIT run folders on disk + FND-0193 / 0303 / 0315 / 0318 [F, folder count]. Upper bound by the ledger (`scripts/audit/p3/pit_trial_ledger.py`, run 2026-11-17, output kept outside the repo): **N_scan_upper = 622** (it was 437 at the FND-0193 freeze), 2 x N_scan = 1,244, 41 distinct strategy configurations, 23 runs with manifest + 32 log-only runs (55 run logs), 874 ledger rows, 584 Sharpe values, variance of annualised trial Sharpes 0.0281 [F]. Both numbers are disclosed and NOT used to correct alpha (section 0, parameter 1).

## 9. Parameter register for this study (class S structural, P policy, C operational)
| # | Constant | Value | Class | Status |
|---|---|---|---|---|
| 1 | alpha (one-sided) | 0.05 | P | decision block, your lock |
| 2 | attrition rate, base and bounds | 5 % (2.5 % / 10 %) | P | decision block, your lock; assumption, not measured |
| 3 | `MIN_MEANINGFUL_DELTA_SHARPE` | 0.10 | P | decision block, your lock |
| 4 | minimum families per peer cell | 15 | P | plan, [ADOPTED] |
| 5 | drawdown margin | 2 pp | P | plan, [ADOPTED] |
| 6 | power target of 5.2 | 80 % | P | [ADOPTED], plan silent |
| 7 | planted-alpha detection target | 80 % | P | [ADOPTED], plan silent |
| 8 | bootstrap: block, resamples, seed | 6, 5,000 (M1: 2,000), 12345 | C | [ADOPTED], FND-0193 precedent |
| 9 | champion hysteresis band | 0.10 | P | owner-confirmed 2026-11-15 |
| 10 | cost convention | 25 bp per side, entry once | P | existing PIT convention |

## 10. What this document does NOT decide
Enabling `PORTFOLIO_HYSTERESIS_ENABLED`, any change of the band, `ROTATION_COST_GATE_ENABLED`, the vendor decision (WP6), the P3 refactor scope, and anything about the forward holdout (FND-0299).

## 11. Sign-off
Values locked by the owner on 2026-11-17 ("values confirmed", as proposed): alpha = 0.05 one-sided; attrition base 5 % a year with bounds 2.5 % / 10 %; `MIN_MEANINGFUL_DELTA_SHARPE` = 0.10. No override of any other proposal, which are therefore [ADOPTED].
Cleared to: commit this page, then run WP5.1 (controls) and WP5.2 (feasibility) only. M1 / M2 and the verdict need a further order. The P3 refactor stays paused behind FND-0429.
