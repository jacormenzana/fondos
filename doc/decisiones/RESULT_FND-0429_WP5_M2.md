# RESULT - FND-0429 / WP5 M2 verdict (2026-11-17) - ONE page

Rule: `PREREG_FND-0429_WP5.md` (signed off 2026-11-17) + `PREREG_FND-0429_WP5_AMENDMENT1.md` (committed in `f075534`, before any estimate existed). Harness: `proyecto3/src/pit_selection_test.py`, commit `83b296a` (committed before the run). PIT run `out/reports/pit_backtest/20261117_201724`; output `out/reports/wp5/20261117_213920/wp5_m2_verdict.{json,md}` (not in git); seed 12345; alpha 0.05 one-sided; run once.

## 1. Verdict by the signed rule: **CONTINUE**
| Run | Estimate | SE date / family | One-sided lower bound | Reading |
|---|---|---|---|---|
| M2, 12 months, signed peers (PRIMARY) | +0.0726 | 0.0366 / 0.0280 | **+0.0124** | above zero |
| M2, 36 months (sign check) | +0.5435 | 0.0886 / 0.1040 | +0.3724 | same sign |
| M2, 12 months, nature-free peers (sensitivity) | +0.1234 | 0.0422 / 0.0287 | +0.0540 | same reading |
Drawdown condition: the top quintile's forward 12-month max drawdown minus its peer median is -0.72 pp over 37,546 unit-months (limit -2 pp): met. Champion portfolio drawdown (band 0.10) -12.52 % against the pool's -11.88 %: worse by 0.64 pp (PIVOT margin 2 pp): not triggered. All consistency checks hold, so the outcome is CONTINUE.

## 2. What the number does NOT say (read before using it)
- **Thin and small.** The one-sided lower bound is +0.012, barely above zero, and the point estimate (0.073) is BELOW the locked `MIN_MEANINGFUL_SELECTION_SPREAD` of 0.10: statistically positive, smaller than the spread the owner called meaningful. The 0.10 only governed testability; it is not part of the outcome rule.
- **Not robust to survivorship.** Under the attrition adjustment (loss given death = the cell's worst-decile gap; base 5 % a year, bounds 2.5 % / 10 %) the adjusted estimate is +0.047 / +0.022 / **-0.029** and every adjusted lower bound is negative (-0.011 / -0.035 / -0.083); the sign flips at 10 %. The rule uses the adjustment only for PIVOT (not for CONTINUE), and the rate is an assumption, not a measurement; the method here is the pooled expectation, not the 1,000 simulated paths.
- **Nature tilt.** The nature-free estimate (0.123) is larger than the signed one (0.073): part of the apparent selection skill is the choice of natures, not the choice of funds inside a nature.
- **History only.** 251 formation months (about 21 independent 12-month windows), a nearly survivor-only universe (84.5 % of unit-months have an outcome), and a test set the project already mined (trial ledger upper bound 622). The blind months from 2026-11 (FND-0299) are the only clean data.

## 3. Portfolio level (M1): descriptive only, and it points the other way
Champion at band 0.10: Sharpe 0.655; pool (frictionless): 0.750. Paired difference of the champion at band 0 (net) against the frictionless pool: -0.189, 90 % interval -0.351 .. -0.042, 261 months. The interval is entirely below zero, so this is not a "cannot tell" case: on these proxies the champion's net Sharpe is BELOW the pool's. Proxies: the pool is frictionless and the band differs; the net pool would narrow the gap by an unmeasured amount. By the plan's own wording this reading is **CONSTRUCTION-LOSS: scoring shows (weak) selection value while portfolio construction gives it back.** Amendment 1 made that flag descriptive; the plan's consequence for it was a champion-side construction study first (remove or relax one construction element at a time on the PIT harness: fund and manager caps, nature caps, regime weights, FX budget, weight method, and report which closes the gap to B-POOL), needing no vendor.

## 4. Not computed in this pass
The forward Sortino secondary outcome (secondary by the plan); the 1,000 simulated attrition paths; M1 with the band 0.10 series and the pool through the cost layer.

## 5. For the owner
The gate of FND-0429 ("validate whether scoring beats a baseline before executing P3 first") is answered: **at the selection level yes, weakly; at the portfolio level, on proxies, no.** The P3 refactor scope is the owner's decision; the evidence points at portfolio construction, not at the scoring layer, as where value leaks. The P3 refactor stays paused until the owner decides.
