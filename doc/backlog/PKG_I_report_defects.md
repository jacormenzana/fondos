# Package I - Report return defects (selection quality, package 1)

Owner order and decisions 2026-11-15: freeze lifted for packages I and J; letters I / J / K; coverage rule A (span, 35 days); first-batch constants ratified (35 days, YTD base 5 business days, 5 bp, 3 working days, restore batch size from the rehearsal). One page, rewritten in place. Plan: `doc/especificacion/PLAN_SELECCION_CALIDAD.md` (work packages WP0 to WP2). No P1 / P2 / P3 process is run by this package without the owner's order for that run. Exit metric: the report reconciles to NAV, zero truncated windows in `fund_metrics`, `ytd` present, one full receipt for the delivery. Time box: 4 to 6 sessions (estimate) plus the owner-run recompute.

## 1. Decision needed
**One owner event:** review the selection diff of the rebuilt DRAFT `cartera_shock_energetico_202611_b20261116230614` against PRODUCTION (3 funds out: DE000A0X7541, LU0572586674, LU1670606927; 3 in: LU1006075656, LU1670606760, LU1670725776; traded notional 0.06, one-way 3 %). It is the last open item of FND-0427; FND-0426 and FND-0428 are verified and close with the commit that carries this page. WP2 (3.09 % vs 4.02 %) still needs his reference source for the 4.02 %. Promotion of the DRAFT stays his separate ruling.

## 2. State (verified 2026-11-17, read-only, live data)
- **Delivered:** the slice is committed (`02c885e`, `00ed6ec`, `c926984`, `209af91`) and `b42ee0f` (2026-11-16, owner order) switched `ROLLING_WINDOW_COVERAGE_ENABLED` and `YTD_METRIC_ENABLED` ON with `CALC_VERSION` 20261116; the recompute ran and the 2026-11-16 P2P3 cycle finished with every step RC=0. One full receipt covers `b42ee0f` (7,408 passed).
- **FND-0426 (ytd):** horizon `ytd` has 7,386 `return_cum` rows (was 0). IE00008TZ2E8: stored 0.029823 = `ytd_return()` = a manual recomputation (base 2025-12-31 NAV, end 2026-11-12). The POST audit `post_20261116_232554_p2` is FULL / PASS_WITH_WARNINGS. VERIFIED.
- **FND-0427 (rolling windows):** over the full population, 0 stored `rolling_1y` .. `rolling_10y` rows violate the span or continuity rule (stored funds 3,688 / 3,659 / 3,612 / 3,446 / 2,549); the audit's ROLLING_COVERAGE rule raised no finding. Covered by the rule but not stored: 18 / 18 / 14 / 14 / 5 funds (INVALID_NAV or no metric rows; one fund, IE00BJ7BP819, has metrics and no `rolling_10y`: unexplained, not a violation). Verified except the diff review (section 1).
- **FND-0428 (report):** the regenerated DRAFT report has 31 scoped columns; the excess equals nominal 1Y minus nominal objective in 30 of 30 rows; the NAV reconciliation over the 30 DRAFT funds plus the 40-ISIN sample (69 funds, 201 claims) has 0 mismatches at 5 bp. VERIFIED.
- **Probe defect fixed on the way:** the `calc_share` closing condition counted only the exact `CALC_VERSION` (11.6 %); family-versioned stamps `<CALC>.<epoch>` are on the same version (99.5 %). `rfd_matrix_probe.py` now counts both, with a test.
- **Rollback tooling stays available:** `scripts/mig/fondos/p2_rollback.py` (export, verify, restore, rehearse; restore batch 50 funds); the export must be taken again before any new recompute.
- **Known gap:** sheets 0, 2, 3, 4 and 5 of the report are not inventoried (WP1.0b covers `1_Cartera` only); `export_metrics.py` does not yet use `metric_labels`. About 458 funds are still on the previous version until the next cycle.

## 3. WP1.0a - consumers of the rows the coverage rule removes (read-only, 2026-11-15)
| Reader | What it reads | On a missing value | Action |
|---|---|---|---|
| Scorer, `fund_scorer.py:299-304, 616-633, 721-729` | `*_pctile_cat` and slopes at `rolling_3y` | NaN-safe guards; percentile signals OFF (`ROLLING_PCTILE_P3_ENABLED = False`); slope multipliers neutral (1.0) | none; no effect on the score |
| Scorer `momentum_rank`, `fund_scorer.py:265` | since-inception percentile of peers | unaffected by windows | none |
| `momentum.py:124-139` | own span check (`covers`, no tolerance) and peers' `rolling_Ny` returns (`load_category_returns`) | skips the window | call `window_coverage_ok` (35-day tolerance: slightly more lenient); the peer mean stops including fake windows, so `momentum_1y` / `momentum_3y` shift a little |
| PIT `pit_metrics.py:272-306` | its own trailing windows | a partial window is already NaN | WP1 brings the live metrics in line with the PIT (parity) |
| `db_readers.py:525-536` category snapshot | latest timeseries point per (isin, metric, window) | a fund without a point leaves that cross-section | expected: percentiles, z-scores and alerts use fewer young funds |
| `data_suspect.py` slope panel (FND-0401) | `sharpe_slope`, `return_ann_slope` at `rolling_3y` | `adequate` by `ROLLING_SLOPE_MIN_OBS = 12` | one definition: the window span is the coverage rule, the slope floor stays a minimum of points of the rolling series; a slope therefore needs about 36 + 12 months |
| Matviews `mv_fmts_peer_stats`, `mv_fmts_latest`, `mv_fund_coverage` | timeseries aggregates | counts and `first_date` change | refreshed at the end of each P2 run; note for the BI users and `pg_reconcile.py` expectations |
| `rolling_dashboard.py`, `export_metrics.py:1394-1396` | fixed window lists (`_TENDENCIA_WINDOWS` = `rolling_3y`, `rolling_5y`) | blank / the fund leaves the trend sheet | expected; `ytd` is not listed, nothing breaks |
| Statistical audit, `SCALAR_EQUALS_TIMESERIES`, catalogs, canaries | scalar vs timeseries per window | must stay equal | register `ROLLING_COVERAGE` with a canary fixture; catalog `return_cum` / `ytd` |

**Correction to the plan's premise:** the champion's score is not changed by the coverage rule (momentum is since-inception, the percentile bonus is off, the slope multipliers are neutral). The selection diff after the recompute is still run, because other commits since the last calculation can change selections.

## 4. WP1.0b - report provenance, sheet `1_Cartera` (first pass; sheets 0, 2, 3, 4 and 5 still to inventory)
| Column | Source | Gap |
|---|---|---|
| Retorno real % | `return_ann`, `since_inception`, `real_flag=1` | unscoped: annualized, IPC-deflated, since inception |
| Exceso obj % | column above minus the nominal IPC+M3 objective | real minus nominal |
| Max DD % | `max_dd`, `since_inception`, nominal | horizon not stated |
| Divisa contrib % | `fx_contribution_ann`, `since_inception`, nominal | annualized, horizon not stated |
| Beta Oil / Rate EU / Rate US, Macro R2, Alpha Persist., SRRI Calc | `since_inception`, nominal | horizon not stated |
| DD Corto 6M %, Vol Adj 3M %, Liq Flag 6M | short-horizon `d1` metrics | scoped by their label |
| Score | `fund_scores.score_total`, version `v1`, regime of the scenario | regime and version not stated |
| Obj IPC+M3 % | nominal annual objective | basis not stated |

All the `1_Cartera` gaps above are closed in the code (headers from `shared/metric_labels.py`, excess nominal-versus-nominal, objective labelled nominal).

## 5. Decisions pending
(1) The review of the selection diff (FND-0427). (2) The 4.02 % reference for WP2. (3) Later: the owner's ruling on promotion of the rebuilt DRAFT, after the FND-0429 gate (his full-universe PIT re-run).
