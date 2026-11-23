# P3-10F: feasibility of marginal-CDaR admission (pillar C.2 of the drawdown-management RFC): result

Written 2026-11-23 after the run `out/reports/p3_cdar_feasibility/20261123_224439` (git-ignored artifacts: `REPORT.md`, `verdict.json`, `f1_census_*.csv`, `f1b_observations_*.csv`). Preregistration `doc/decisiones/PREREG_P3_10_FEAS.md` (committed `3cd54bf` before any outcome existed), code `2553f94` (`proyecto3/src/pit_cdar.py`, 38 tests, 43 mutations killed). Sealed run `20261119_202509` (9 artifacts hash-verified), P3-7 frozen snapshot, no database, no production change.

## Verdict: **STOP at F1b** (primary cell: beta cap 0.3, stress threshold 3 %)
Ex-ante convexity is **not demonstrated** to predict later stress behaviour at the preregistered level. F2 (the hindsight ceiling) was therefore not run, by rule: it is not evaluated on a signal that fails its own predictive gate. Nothing is adopted.

## What was checked first
- The seal verified; the rescore of the 262 sealed dates reproduced the sealed `score_base` (coverage 98.7 %, max difference 0.000000) before anything was classified; in every row whose sealed first failing reason was the drawdown filter, the independently evaluated drawdown filter also fails (100 %).
- The three-pool ceiling at -15 % reproduced: 4.1356 % (the 4.14 % of `P3_L3_MANDATE_AND_FEASIBILITY.md`); `optimise_mix` equals `best_static_mix` on it.
- The design count on the forward-return panel equals the one in the preregistration: 69 stress months, 10 episodes with the gap rule.

## F1 census (primary cell)
Median per month over the 202 evaluation months (2010-01 .. 2026-10): 408 funds excluded by the drawdown filter only (set a), 422 by it and/or a short gate (set b); 11 convex candidates; the Convex column is active (at least 3 candidates, at least 6 stress months in the window) in 79.2 % of the months; empty in 20 of 202. **F1 passes.** The candidates sit in the defensive sub-portfolio: median per sub-portfolio 9.5 in Defensiva, 1 in Equilibrada, 0 in Dinamica. By cell of the F1b observations: Renta Fija Corto Plazo 103, Renta Fija Flexible 96, Mixtos 93, Monetario 90, Renta Variable 2.

## F1b predictive gate (candidates against matched excluded non-candidates, mean excess return over the stress months of the next 12 months)
| Cell (beta cap, stress threshold) | Mean | One-sided 95 % bound | Observations / episodes | Episodes positive | Result |
|---|---|---|---|---|---|
| **0.3, 3 % (primary)** | **+0.73 pp** | **-0.18 pp** | 384 / 8 | 6 of 8 | **STOP** |
| 0.5, 3 % | +0.78 pp | +0.03 pp | 430 / 8 | 6 of 8 | passes (not decisive) |
| 0.3, 5 % | -0.09 pp | -1.92 pp | 256 / 4 | 3 of 4 | STOP |
| 0.5, 5 % | +0.02 pp | -1.67 pp | 262 / 4 | 3 of 4 | STOP |

Dropped for lack of a control: 0 % in the primary cell (every pairing found inside its cell). By episode (primary cell, first stress month ahead): 2011 +3.35 pp (39 observations), 2015-16 -0.20 (13), 2018 +3.38 (27), 2020 +0.48 (80), **2022-23 +0.80 (105)**, 2023-10 +1.32 (24), **2025 -2.11 (56)**, 2026-03 +0.65 (40). The 2007-09 crisis has no forward observation: the first evaluation date (2010-01) is the first with a 60-month pool history, so 2008 only trains the betas of 2010-2013.

## Reading
- **The sign is mostly right and the evidence is thin.** Candidates beat their controls by about 0.7 pp per stress month on average and in 6 of 8 episodes, but the one-sided bound is 0.18 pp below zero. With 8 independent episodes the test could not have been strong; the preregistration said so in advance (a gate, not an adoption test).
- **One neighbouring cell passes by 0.03 pp.** That is the signature of a borderline result, not of a hidden positive one. By the preregistered rule the primary cell decides and the other cells cannot turn a STOP into a PASS; this report does not use it to reopen the question. A larger beta cap is not a refinement to adopt after the fact (R8).
- **The unlocked set is not the one the RFC describes.** The candidates are Monetario, short-term and flexible fixed-income funds excluded by Defensiva's -20 % since-inception limit (96 % in Defensiva). Trend-following, real-asset and equity-diversifier funds are essentially absent (Dinamica 0, Equilibrada 1 as medians). In this data, admitting funds by marginal CDaR amounts to relaxing the defensive drawdown filter for bond-type funds, whose "convexity" in a flight-to-quality crisis is the behaviour that failed in 2025 and was weak in 2015-16.
- **Pillars A and B were not carried** (time to recovery was retired neutral in FND-0193 and L3 was inert; the short-gate softening depended on C.2 passing). Pillar C.1 is lever L1, closed in P3-5A.

## Limits (as preregistered)
Survivor-only panel (FND-0436): the excluded funds that died are missing, so the true signal is no better than this. Pool-level stress defined on the funds the model itself found eligible. Eight forward episodes. A three-pool frontier that is hindsight and gross. The four-cell grid and every threshold were fixed before the run; none was tuned.

## Decision (the preregistered table, row "F1b fails")
Record "no ex-ante signal demonstrated"; **C.2 closed; no study; F2 not run; nothing adopted**. Levers L1-L6 stay OFF; the AC forward shadow was not read. What could change this verdict is more forward episodes (the shadow accumulates them) or fund-level data on dead funds (FND-0436), not a different threshold on the same history.
