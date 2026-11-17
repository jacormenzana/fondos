# RESULT - FND-0429 / WP5.2 feasibility (2026-11-17) - ONE page

Pre-registration: `doc/decisiones/PREREG_FND-0429_WP5.md` (signed off 2026-11-17). PIT run: `out/reports/pit_backtest/20261117_201724` (git 2da6c32, 3,750 funds, 2005-01-31 to 2026-10-31). Harness: `proyecto3/src/pit_selection_test.py`, `scripts/audit/p3/p3_pit_selection_test.py` (13 tests). Output of the official pass: `out/reports/wp5/20261117_204737/wp5_checks.json` (not in git). The harness is BLIND: it never computed a point estimate of the real scores, so there is no M1 / M2 verdict here.

## 1. Result
| Quantity | Value | Reading |
|---|---|---|
| Formation months with a 12-month outcome | 251 (about 20.9 independent 12-month windows) | 36-month: 288 months, about 8 windows |
| Unit-months / families / valid outcomes | 208,142 / 1,204 / 84.5 % | one class per family, the cheapest |
| Peer cells used | level 1 (nature x tercile x FX) 82.3 %, level 2 12.8 %, level 3 4.9 % | |
| SE of the real-score top-quintile mean (M2) | date block 0.0366, family cluster 0.0280, used 0.0366 | |
| **Minimum detectable selection spread** (alpha 0.05, power 80 %) | **0.091** outcome units (12-month return per unit of trailing volatility) | no minimum meaningful selection spread is locked |
| **Minimum detectable Sharpe difference** (M1) | **0.233** (block 3: 0.220; block 12: 0.231); 90 % interval half-width 0.154 over 261 months | against the locked `MIN_MEANINGFUL_DELTA_SHARPE` **0.10** |

**Outcome under the pre-registered rule (plan 5.2): the minimum detectable Sharpe difference exceeds 0.10, so the question is UNTESTABLE on this history at the portfolio level. The harness stopped; WP5.1 controls were NOT run.** Nothing here is a PIVOT or a CONTINUE.

## 2. Correction of the draft
The decision block of the draft derived a minimum detectable difference of about 0.088 at 80 % power from the plan's +-0.058 half-width, and concluded that 0.10 was testable. That half-width is the one measured for the FND-0193 comparisons of near-identical portfolios (the champion with and without a layer). The champion against the pool is a wider paired difference: measured half-width +-0.154. The owner locked 0.10 reading that derivation; the measurement overturns it.

## 3. Caveats (read before relying on the number)
- The M1 series are the PIT table's: the champion at band 0 net of 25 bp (`net_turnover_1m`) against the FRICTIONLESS pool (`bench_1m`). The prereg's comparison is the champion at band 0.10 against the NET pool; neither is stored. The margin (0.233 against 0.10, a factor of 2.3) makes a flip unlikely; a re-run with `--hysteresis-band 0.10` and a pool through the cost layer would remove the proxy.
- The independent-window counts (about 21 twelve-month, about 8 thirty-six-month) match the plan's context.
- The M2 minimum (0.091) is in Sharpe-like outcome units and has no locked threshold to be compared with.

## 4. Options for the owner (none taken)
- **A. Accept "untestable on the history".** FND-0429 cannot be passed by the PIT alone; the blind months from 2026-11 (FND-0299, first checkpoint 2028-10) are the only way to resolve it. The P3 refactor would then be scoped so that it does not depend on the scoring verdict.
- **B. Move the primary test to selection level (M2)** with a locked minimum meaningful selection spread, as a dated amendment written before any M2 estimate exists. The harness already measures that spread's detectability (0.091).
- **C. Run the WP5.1 controls as a diagnostic** (about 2 minutes) to validate the M2 machinery without a verdict.
- **D. Remove the proxy** (PIT re-run at band 0.10, pool through the cost layer, about 10 minutes) before deciding.

The P3 refactor stays paused behind FND-0429 until the owner chooses.
