# T0 baseline of P3 (P3-0 item 7), read-only, 2026-11-18

Purpose: every later claim of improvement is measured against these numbers. Sources are read from stored artifacts and one read-only query; nothing was recomputed or run.

## 1. Point-in-time (PIT) run `out/reports/pit_backtest/20261117_201724`
- Scope: full universe, 3,750 funds with NAV, 262 monthly dates 2005-01-31 to 2026-10-31, **champion at hysteresis band 0**, 25 bp per side, `--current-universe-only` (a survivor-only universe, see the fidelity register), git `2da6c32 (+uncommitted changes)`, P2 bundle flags all ON. Elapsed 471 s (scoring 270 s).
- Chained 1-month series (net of the rotation actually made each month), 261 months:

| | Annual return | Volatility | Sharpe | Max drawdown |
|---|---|---|---|---|
| Champion, band 0, net | 3.37 % | 4.63 % | 0.56 | −14.6 % |
| Pool benchmark, frictionless | 4.08 % | 4.36 % | 0.75 | −11.9 % |

- Hysteresis experiment (same run, `hysteresis_experiment.csv`):

| Band | Mean monthly turnover | Annual cost drag | Annual return | Sharpe | Max drawdown |
|---|---|---|---|---|---|
| 0 | 31.9 % | 0.96 % | 3.37 % | 0.56 | −14.6 % |
| 0.05 | 19.8 % | 0.59 % | 3.68 % | 0.64 | −13.4 % |
| 0.10 | 16.6 % | 0.50 % | 3.68 % | 0.65 | −12.5 % |
| 0.20 | 13.1 % | 0.39 % | 3.61 % | 0.65 | −12.8 % |
| 0.40 | 10.2 % | 0.31 % | 3.42 % | 0.66 | −12.3 % |

- Median funds held 30, mean cash weight 0.01 %.

## 2. Mandate-level metrics (rule R6), from `pit_backtest_table.csv`, 12-month windows, 250 windows
| | Median excess over IPC+M3 | 25th percentile | Mean | Share of windows above target |
|---|---|---|---|---|
| Champion (band 0, net of entry cost once per window) | −1.83 pp | −5.48 pp | −3.31 pp | 36.8 % |
| Pool (frictionless) | −2.08 pp | −6.05 pp | −3.53 pp | 32.4 % |

The median 12-month target is 5.92 %. **Neither the champion nor the pool meets the target on this history**, and the aspiration (target + 2 pp) is met in 23 % of 12-month windows by the champion (`summary.txt`). Cost sensitivity of the champion's 12-month window (`cost_sensitivity.csv`): mean net return 4.01 % at 0 bp, 3.75 % at 25 bp, 3.49 % at 50 bp; share above target 38.8 %, 36.8 %, 34.4 %. The 36-60 month rolling-window version of these metrics is not in the stored artifacts and is computed in P3-1.4.

## 3. Derived reading (arithmetic on the numbers above; not a measured result)
The champion's net return plus its annual cost drag is 3.37 % + 0.96 % ≈ 4.33 % gross, above the frictionless pool's 4.08 %. Backing the risk-free rate out of the stored Sharpe values (about 0.77 %) gives a champion gross Sharpe of about 0.77 against the pool's 0.75. If that holds, most of the −0.189 Sharpe gap that FND-0429 read as "construction loss" is the 25 bp cost charged to the champion and not to the pool, not a gross shortfall. This is exactly what P3-1.4 (a costed, mandate-matched pool and the random-selection benchmarks) is designed to test, and it points to the C1 branch (b), the rotation-cost drag. It does not pre-decide C1.

## 4. PRODUCTION scenario now (live database, read-only)
- `cartera_shock_energetico_202611_b20261115113822`, status PRODUCTION, regime Shock_Energetico.
- 30 funds, 10 per sub-portfolio; weights Defensiva 0.55, Equilibrada 0.35, Dinamica 0.10 (sum 1.00).
- By nature (funds, weight): Monetario 5, 0.275; Renta Variable 10, 0.225; Renta Fija Corto Plazo 4, 0.220; Mixtos 7, 0.195; Renta Fija Flexible 1, 0.055; Alternativo 3, 0.030.
- 28 managers, at most 2 funds per manager.
- Not captured here (needs the P3-1.6 `selection_health` block): rank turnover between builds, NaN-weight share, eligible-set change, hold and suspect counts.

## 5. Reading conditions
- The PIT figures describe a history, not a forecast; the universe is survivor-only and the run carries uncommitted changes (git field above).
- Stored metrics in the live database are mostly on `20261116.20261019` while the code is `20261116` (pre-flight 2026-11-18): a recompute is pending or running, so the live selection may shift after it.
