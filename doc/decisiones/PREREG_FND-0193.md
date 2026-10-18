# PREREG — FND-0193: does P3 scoring + regime model add value over simple baselines?

- **Approved: owner directive 2026-10-18** (decisions taken through AskUserQuestion the same day: honest reset of the 2017-2026 window, universe-proxy baselines, simplify-and-freeze on a non-win, 25 bp for every strategy, window extension audit, trial-ledger penalty ×2, drawdown circuit breaker).
- **Commit rule:** this file and `LEDGER_FND-0193.csv/.json` are committed locally BEFORE any baseline, ablation or walk-forward number is computed. The commit hash (tag `prereg-fnd0193`) is the timestamp. Any later change is a dated amendment at the end of the file, never an edit.
- **Code under test:** `origin/master` b148ba2. Frozen hashes (SHA-256): `proyecto3/config/scorer_params.yaml` = `1641de8e5fe570183d861b8e56359e7c6c1d3937011bffdab2dfdd1f587a84b1`; `shared/regime_taxonomy.py` (REGIME_WEIGHTS) = `f246aa28a87c0474cd14b8b061537bf49f71dc6f8a619b21d497a868b5bd6cbf`.
- **Only numbers seen before this file:** the ones already on the backlog (FND-0193/0206/0217 logs) and `out/reports/pit_backtest/20261017_151019/{summary.txt,series_stats.json,hysteresis_experiment.csv}` (model 0.5498 / pool 0.7099 Sharpe; hysteresis 10 % 0.678). No baseline other than B-POOL has ever been computed.

## 1. Data, window, conventions
- **Primary window:** 2005-01-31 .. last PIT month of the saved run (continuity with all earlier evidence). Evaluation series = chained 1-month net returns, as in `pit_backtest.series_stats`.
- **Cost:** 25 bp per unit of turnover (`Σ|Δw|`), the convention of `assemble_table`, applied to EVERY strategy including every baseline; 0 and 50 bp are sensitivities.
- **Sharpe:** annualised, in excess of the monthly deposit-rate cash return (`cash_ret_1m`).
- **Extended window:** a PIT run with `--start 2001-03-31` (owner-run). Rule, fixed now: the extended start is the first month where the risk-free rate and the lagged IPC are observable (`pit_metrics.first_observable_date`), the regime is classifiable, and every sub-portfolio has ≥ 30 eligible funds in `universe_by_date`. Months of the extended window before 2005-01 form the sub-period "early". 1999 is not feasible (macro series start 2000-01, NAV 1999-12-31; `assert_series_cover` refuses earlier dates).
- **Reproduction gate (before anything else):** `load_saved_run('20261017_151019')` must reproduce `series_stats.json` (model Sharpe 0.5497736777725755, pool 0.7099471890218418) to 1e-6. On failure the study stops and the owner runs a fresh reference; no baseline is computed on a non-reproduced base.
- **Known baseline-friendly bias, stated up front:** `scores_long` keeps one share class per family chosen by the model's own `score_final` (family dedup). Baselines inherit that choice. This favours baselines slightly and makes the test stricter for the model.

## 2. Strategies
**Model under test (M):** the live construction (Layer 1-3, regime weights, constraints) with hysteresis band 10 % (the owner-confirmed go-live band). M0 = same with band 0, reported only.

**Primary baselines (decide the verdict, Holm family of 4):**
| Id | Definition |
|---|---|
| B-POOL | per sub-portfolio, equal weight over the funds with `eligible == True`; sub-portfolios blended with the date's regime weights (`pool_benchmark` rule), now with 25 bp on its own turnover |
| B-STATIC | B-POOL with a constant sub-portfolio mix = unweighted mean of the 7 `REGIME_WEIGHTS` rows = Defensiva 0.464286 / Equilibrada 0.364286 / Dinámica 0.171429 (no regime, no look-ahead) |
| B-LOWVOL | per sub-portfolio, the 10 eligible funds with the lowest trailing-36-month annualised volatility computed from monthly NAV returns up to t (≥ 24 observations required), equal weight, regime weights, hysteresis 10 % as in M |
| B-6040 | 60 % equal weight over eligible `Renta Variable` funds + 40 % equal weight over eligible `Renta Fija Flexible` + `Renta Fija Corto Plazo` funds, sub-portfolio membership ignored, static, monthly rebalanced |

**Secondary baselines (reported, never decisive):** B-MOM (top 10 by trailing-12-month return per sub-portfolio, equal weight, regime weights, hysteresis 10 %); B-CHEAP (10 lowest `Ongoing_Charge` per nature; uses today's costs = look-ahead); CASH (deposit rate); IPC+M3 hurdle (`RegimeClassifier.absolute_target_annual`).

## 3. Evaluation set
- **E1** full-history comparison M vs each primary baseline; sub-periods 2005-2010, 2011-2016, 2017-2021, 2022-2026 (+ "early" when the extended run exists); leave-one-episode-out removing 2005-11..2007-08, 2008, 2020, 2022 (calendar years in full, except the first which is the stated range); regime-conditional table.
- **E2** walk-forward of the only fitted choice (the hysteresis band): each January from 2010, choose the band over {0, 0.05, 0.10, 0.20, 0.40} on the expanding window since 2005-01 ending 1 month before (embargo) with `pit_oos.select_cell` (tolerance 0.02), hold it for the next 12 months. Result = one OOS series 2010-01..end.
- **E3** deflated Sharpe of M (full-history net monthly returns in excess of cash) with N = `N_scan` and N = `2 × N_scan` from the ledger; variance of trial Sharpes = `var_annualised_sharpe_across_trials` of the ledger divided by 12 (per-period units).
- **E4** probability of backtest overfitting: CSCV, S = 16 equal blocks of the common history, over the regenerable configurations (hysteresis × weight-band × cadence × trigger grid of the model, the 4 primary and the secondary baselines, ablations A1-A4). Reported with the matrix size.
- **E5** forward holdout: months from 2026-11 on are the only truly blind data; checkpoints at 2028-10 (24 m) and 2029-10 (36 m) re-run the frozen code. Recorded on the ticket.
- **Trial ledger:** `scripts/audit/pit_trial_ledger.py` over both artifact roots + all PIT run logs. Result at freeze: **N_scan = 437** (upper bound), **2 × N_scan = 874**, 41 distinct strategy configurations, 12 runs with artifacts + 12 log-only runs, variance of annualised trial Sharpes 0.030465 over 529 values. Not enumerable and therefore covered only by the ×2 factor: the 2026-09 hand optimisation of scorer and regime weights, FND-0224/0225 exploratory variants beyond the saved CSVs, attribution analyses.

## 4. Inference
- Paired stationary bootstrap (`backtest_inference.paired_sharpe_difference`): mean block 6 (sensitivity 3 and 12), 2,000 resamples, seed 12345, 90 % two-sided interval.
- **Holm** across the 4 primary baselines at one-sided α = 0.05: p_i = share of resamples with Sharpe(M) − Sharpe(B_i) ≤ 0; "M beats all primary baselines" means every p_i passes the Holm step-down.
- Bootstrap drawdown probability: stationary bootstrap of the net monthly series (mean block 6, 5,000 resamples, seed 12345), P(maximum drawdown over a 36-month path < −15 %).

## 5. Decision rules
**KEEP** — all of:
1. M beats all 4 primary baselines in E1 on the primary window (Holm).
2. The sign of Sharpe(M) − Sharpe(B_i) is positive in ≥ 3 of the 4 main sub-periods, positive in the "early" sub-period when it exists, and positive in every leave-one-episode-out (point estimates, vs each primary baseline).
3. E2 walk-forward series: Sharpe above each primary baseline over its span (point estimate).
4. DSR ≥ 0.90 at N = 2 × N_scan.
5. PBO ≤ 0.30.
6. **Drawdown circuit breaker:** maximum drawdown ≤ 15 % in every evaluation (full history, E2 series, extended window), and the bootstrap probability in §4 no higher than B-POOL's.

**SIMPLIFY (simplify-and-freeze)** — when KEEP fails and no primary baseline beats M with a CI excluding 0:
- Keep the simplest configuration that is statistically indistinguishable from M and satisfies the drawdown breaker; ablations A1-A4 decide which layers survive: a layer is kept only if removing it lowers Sharpe with the 90 % CI excluding 0, or worsens max drawdown by more than 2 pp.
- Then freeze P3 machinery: FND-0160, FND-0161, FND-0162, FND-0163 → CLOSED/REJECTED with the evidence log; FND-0222 reduced to the data-freshness triggers; FND-0258 decided after the per-multiplier ablation A5 (owner-run).

**REPLACE proposal** — a primary baseline beats M with a CI excluding 0 (Holm): a ticket proposes production construction by that rule; the owner decides.

**Mandate:** if the drawdown breaker leaves no configuration with max drawdown ≤ 15 %, the FND-0223 waiver is required before any go-live (FND-0158 stays blocked).

## 6. Ablations
A1 no Layer-3 multipliers (`score_base` in place of `score_final`); A2 equal weight among the selected funds; A3 static regime mix (B-STATIC mix); A4 = A1+A2+A3; A5 one multiplier at a time (owner-run PIT run with the per-multiplier detail saved). Hard filters and construction constraints are not ablated.

## 7. Reporting
Every result carries the bias block: universe composition and retired share, survivorship A/B (same-commit `--current-universe-only` run, owner-run), static-attribute look-ahead (FND-0198), P2 defects replicated by the PIT, 2-month publication lags, flat-bp costs with ongoing charges assumed inside the Morningstar total-return NAV, B-CHEAP look-ahead, accepted audit residuals not modelled, cash = ECB deposit rate, family-dedup bias (§1).

## 8. Amendments
**Amendment 1 — 2026-10-18, implementation details fixed while writing the code, before any full-universe baseline, ablation or walk-forward number existed** (only a 32-fund smoke run, whose numbers are discarded, was seen). None changes a rule of §5.
- Reproduction gate (§1): applied to the run's own `series_stats.json`, evaluated from the run's input snapshot (`inputs/`). Run 20261017_151019 failed it (a regime label moved after the run, Sharpe 0.5528 vs 0.5498) and is replaced by fresh runs R1 (2005+), R2 (2001-03+), R3 (`--current-universe-only`).
- B-POOL / B-STATIC: weights go on ALL eligible funds; a fund without a forward return keeps its weight in cash (point-in-time correct, as the model). The legacy `pool_benchmark` renormalises over funds that have a return; the unit test pins equality when every fund has one.
- B-LOWVOL / B-MOM / B-CHEAP: rank score = percentile rank in (0, 1] (1 = best); an incumbent (last month's holding of that sub-portfolio) gets `score × (1 + band)` as in the model; a fund chosen in an earlier sub-portfolio that month is skipped; B-LOWVOL needs ≥ 24 monthly returns in the 36-month window; B-CHEAP ranks within each sub-portfolio (not per nature) on today's `Ongoing_Charge`.
- Ablations: all seven non-empty combinations of {noMult (A1), eqW (A2), staticMix (A3)} at band 10 %; A4 is the three together; the "simplest configuration statistically indistinguishable" is read from these. A5 families by multiplier-key prefix: alpha, fx, regime (return/sharpe/sortino/maxdd/coverage damp), crisis, slope, roll; both "drop one family" and "keep only one family".
- Layer rule (§5) as coded: the layer earns its place when Sharpe(model) − Sharpe(variant) has a 90 % interval above 0, or the variant's maximum drawdown is more than 2 pp deeper.
- E2 as coded: the design window of year Y ends 30 November of Y−1 (one-month embargo); the stitched WEIGHTS go through the cost layer, so a band switch pays its turnover; before 2010 band 0 is used and not reported.
- E4 matrix as coded: hysteresis {0, 5, 10, 20 %} × weight band {0, 1, 2, 3 %}; cadence {1, 2, 3, 4, 6, 12} × hysteresis {0, 10 %} (phase-averaged); the baselines; the seven ablation combinations; the A5 variants. S = 16, excess over cash.
- Holm p-values: p(M not better) = share of bootstrap resamples with diff ≤ 0; p(B not better) = share with diff ≥ 0 (REPLACE test).
