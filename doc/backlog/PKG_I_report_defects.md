# Package I - Report return defects (selection quality, package 1)

Owner order and decisions 2026-11-15: freeze lifted for packages I and J; letters I / J / K; coverage rule A (span, 35 days); first-batch constants ratified (35 days, YTD base 5 business days, 5 bp, 3 working days, restore batch size from the rehearsal). One page, rewritten in place. Plan: `doc/especificacion/PLAN_SELECCION_CALIDAD.md` (work packages WP0 to WP2). No P1 / P2 / P3 process is run by this package without the owner's order for that run. Exit metric: the report reconciles to NAV, zero truncated windows in `fund_metrics`, `ytd` present, one full receipt for the delivery. Time box: 4 to 6 sessions (estimate) plus the owner-run recompute.

## 1. Decision needed
Two owner events, in this order: (a) "commit the slice" (nothing is committed yet; every new switch ships OFF except the report gate); (b) the order to run the P2 sample run and then the recompute. WP2 (the 3.09% vs 4.02% reconciliation) needs the owner's reference source for the 4.02% figure.

## 2. State
- **Tickets (closing conditions defined):** FND-0426 `ytd` metric missing; FND-0427 rolling windows written without a coverage check; FND-0428 report excess mixes real and nominal, unscoped labels, no reconciliation to NAV. Cluster ticket for package K: FND-0429 (parked).
- **Built, uncommitted, tests green (2026-11-15):** WP1.1 `window_coverage_ok` / `window_span_ok` and its three call sites (rolling loop, `rolling_stats`, `momentum`) behind `ROLLING_WINDOW_COVERAGE_ENABLED` (OFF); WP1.2 `ytd_return` and `_write_ytd` behind `YTD_METRIC_ENABLED` (OFF); both in `P2_BUNDLE_FLAGS` / `FLAG_FAMILIES`. WP1.3 `shared/metric_labels.py`. WP1.4 `shared/report_reconciliation.py` (independent, pinned to P2 on 4 conventions x 3 series; live viability: 544 claims on 272 funds, 0 mismatches over 5 bp) and the gate `REPORT_RECONCILIATION_ENABLED` (ON, read-only). Sheet `1_Cartera` rebuilt: 31 columns, scoped headers, nominal 1Y and YTD, window span, history, as-of date, stale flag, excess = nominal 1Y - nominal objective, legend footer; built in memory against the live DB, gate passed on all 30 funds of the production scenario (YTD shows N/D until the recompute). WP1.5 audit rule `ROLLING_COVERAGE` (HARD_INVARIANT, applied only while the coverage switch is ON) with its canary, registered in `completeness.CHECKS`; `return_cum` registered in the metric catalog. Policy scanners and the related suites pass; AGENTS sentinels regenerated.
- **Sample run done (owner order 2026-11-15, `scripts/launch/p2_sample_run.py`):** ON run removed `rolling_10y` for 21 funds, `rolling_5y` for 11 and added 76 `ytd` rows; the OFF run restored the sample; the drift on momentum, betas and freshness is identical with the switches OFF, so it is not caused by them.
- **Rollback rehearsed (2026-11-15, `scripts/mig/fondos/p2_rollback.py`):** WAL archive healthy (477 archived, 0 failed, newest base backup 2026-11-15 04:30, 812 GB free); full export `out/pkg_i/rollback_20261115` (1,608,384 + 15,861,710 rows, 378 MB, manifest with per-group hashes; `verify` of live against it: 0 groups differ); restore rehearsal on a throwaway postgres:17: every trial reproduced the export; **restore batch = 50 funds** (longest transaction 10.5 s; 150 funds took 15.7 s); about 10 GB of WAL and 10 minutes for a whole-universe restore. The export must be taken again right before the recompute.
- **Not done (needs the owner's order):** flipping the two P2 switches with the `CALC_VERSION` bump; the standard cycle with PRE / POST audits; the FULL audit on the sample (needs the switch ON); one full receipt for the delivery; the selection diff of the rebuilt DRAFT.
- **Known gap:** sheets 0, 2, 3, 4 and 5 of the report are not inventoried (WP1.0b first pass covers `1_Cartera` only); `export_metrics.py` does not yet use `metric_labels`.

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
(1) Commit the slice. (2) The order to run the P2 sample run and the recompute. (3) The 4.02% reference for WP2. (4) Later: the owner's ruling on promotion of the rebuilt DRAFT.
