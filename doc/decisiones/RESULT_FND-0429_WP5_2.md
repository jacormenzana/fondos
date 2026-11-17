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

**Outcome under the pre-registered rule (plan 5.2): the minimum detectable Sharpe difference exceeds 0.10, so the question is UNTESTABLE on this history at the PORTFOLIO level. The harness stopped. Amendment 1 (section 4) then moved the decisive test to the selection level, where it is testable.** Nothing here is a PIVOT or a CONTINUE.

## 2. Correction of the draft
The decision block of the draft derived a minimum detectable difference of about 0.088 at 80 % power from the plan's +-0.058 half-width, and concluded that 0.10 was testable. That half-width is the one measured for the FND-0193 comparisons of near-identical portfolios (the champion with and without a layer). The champion against the pool is a wider paired difference: measured half-width +-0.154. The owner locked 0.10 reading that derivation; the measurement overturns it.

## 3. Caveats (read before relying on the number)
- The M1 series are the PIT table's: the champion at band 0 net of 25 bp (`net_turnover_1m`) against the FRICTIONLESS pool (`bench_1m`). The prereg's comparison is the champion at band 0.10 against the NET pool; neither is stored. The margin (0.233 against 0.10, a factor of 2.3) makes a flip unlikely; a re-run with `--hysteresis-band 0.10` and a pool through the cost layer would remove the proxy.
- The independent-window counts (about 21 twelve-month, about 8 thirty-six-month) match the plan's context.
- The M2 minimum (0.091) is in Sharpe-like outcome units and has no locked threshold to be compared with.

## 4. WP5.1 controls (diagnostic) and the owner's decision
- **Controls run as a DIAGNOSTIC** (`--diagnostic`, 2026-11-17, `out/reports/wp5/20261117_210318`, 1,000 permutations, 500 plants, seed 12345): permuted labels 3 of 1,000 PASS (rate 0.003, Wilson 95 % upper limit 0.009, rule rate <= 0.05 MET: the test is conservative, undersized, because the family-cluster bootstrap includes variation between families that random picks lack); planted alpha 497 of 500 detected at an outcome shift 0.182 (0.994, target 0.80 MET; it overstates power for the real, persistent scores).
- **Option B taken (owner, 2026-11-17):** Amendment 1 (`doc/decisiones/PREREG_FND-0429_WP5_AMENDMENT1.md`) moves the stop rule to the selection level with `MIN_MEANINGFUL_SELECTION_SPREAD` = 0.10 [LOCKED]; the minimum detectable spread 0.091 is inside it, so the question is TESTABLE; M1 stays reported and never decisive; this diagnostic run is adopted as the WP5.1 result.
- Options A (accept untestable) and D (remove the M1 proxy) were not taken.

The P3 refactor stays paused behind FND-0429 until the M2 verdict is recorded.
