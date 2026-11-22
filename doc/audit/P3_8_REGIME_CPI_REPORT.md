# P3-8: results of the preregistered study of the regime classifier's CPI input (read 2026-11-22)

Preregistration: `doc/decisiones/PREREG_P3_8_CPI_REGIME_INPUT.md` (committed before any code or number). Code: `scripts/audit/p3/p3_p38_regime_study.py`, `proyecto3/src/pit_p38_regime.py`. Run: `out/reports/p3_p38_regime/20261122_p38/` (git-ignored; `REPORT.md`, `trials_table.csv`). In-sample history, the baseline model at the stated fees; nothing in production was touched, and the IPC + M3 target was not recomputed.

## What the owner must decide
**Nothing in this evidence justifies changing the classifier's CPI input; I recommend leaving it as it is.** The three alternatives move the net return by at most 0.04 pp a year and the maximum drawdown by at most 0.25 pp, because the CPI input changes the regime label in only 12-18 of 321 months and the change in mean sub-portfolio weights is below 0.3 pp. If you still want a more timely input on a priori grounds, the only alternative that passes the preregistered no-harm rule is **R2 (the same average, lag 1 month)**, and the release calendars confirm a 1-month lag has no look-ahead. It would be a production change, so it stops here for your review.

## Results (full sample, 261 monthly returns; paired against R0 = today's input, which reproduced the sealed run exactly)
| Arm | CPI input | Months whose label changes | Net, stated fees | Δ net (pp a year) | Lower bound at the Holm level (pp) | Max drawdown, stated fees | Δ drawdown (pp) | Classification |
|---|---|---|---|---|---|---|---|---|
| R0 (today) | average of ES and EA, lag 2 | - | 0.08 % | - | - | -26.38 % | - | reference |
| R1 | **maximum**, lag 2 | 15 | 0.06 % | -0.03 | **-0.21** | -26.34 % | +0.04 | **HARMFUL** (rule) |
| R2 | average, **lag 1** | 12 | 0.12 % | +0.04 | -0.04 | -26.29 % | +0.10 | **NEUTRAL** (acceptable) |
| R3 | **maximum**, **lag 1** | 18 | 0.11 % | +0.03 | **-0.11** | -26.13 % | +0.25 | **HARMFUL** (rule) |

At the flat 25 bp cross-check every arm is identical to R0 to within 0.04 pp a year and 0.00 pp of drawdown. The drawdown condition (no more than 0.5 pp deeper) holds for all three at both cost regimes; the labels come from the net-return bound alone. No arm reaches BENEFICIAL (+0.5 pp a year of net return or 1.0 pp of drawdown).

**How to read HARMFUL.** It is the preregistered label, applied as written, but it does not mean the arm hurts: the point estimates are -0.03 pp (R1) and +0.03 pp (R3). The paired difference is nonzero in only 15 and 18 months, so its bootstrap standard error (0.08 and 0.06 pp) is large against the 0.10 pp margin, and the minimum detectable effect (0.25 and 0.19 pp) exceeds that margin. R1 and R3 simply cannot show non-inferiority on this history; R3 misses the bound by 0.01 pp. The study states this limit in advance (preregistration section 3): it can show "no harm" for R2 and "cannot tell" for the other two, never a material gain.

## Why nothing moves
- The maximum differs from the average by 0.40 pp on average over 2005-2026 and crosses the 3 % / 4 % thresholds in more months (35 % against 25 %, and 15 % against 10 %), but a label only changes when a higher-priority regime does not already apply.
- **The latest months do not change under any arm**: June-July 2026 are Expansion and August-November 2026 Shock_Energetico (the oil trigger outranks inflation). Inflation running at 5 % in Spain is therefore not what is holding the portfolio's regime today.
- Regime shares move by at most 4 points (Contraction 42.4 % to 38.6 %, Stagflation 8.4 % to 12.1 % under R1), and the mean Defensive / Balanced / Dynamic weights by 0.0-0.2 pp.

## Integrity and what went wrong
The four arms differ only in the regime history; all four re-ran the whole PIT chain on one frozen snapshot of the histories (`04aa54682f9b`), and every score row is identical to the sealed run (`score_final` equal, no swap), so the regime changes the blend and never a score. R0 passed the identity gate (gross difference 0.0). Defects found and fixed: my first counterfactual of the labels, run before the preregistration, returned "no change" because the classifier caches its first result (caught by forcing CPI to 10 %, which changed 182 months; the study script clears the cache and fails if an override changes no label); and the first tests of the CPI input were too weak (constant growth hid the lag and the aggregator), caught by a mutation check and strengthened.

## Limits
In-sample history; the sparse difference series gives low power by construction; the lag-1 conclusion rests on the release calendars read through search summaries of the INE and Eurostat pages (preregistration section 2); thresholds were not recalibrated (out of scope); a Spain-only input and lag 0 were not tested.
