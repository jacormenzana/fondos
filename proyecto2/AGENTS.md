# proyecto2 — P2 quantitative metrics (area guide)

Loaded by Claude Code the first time a file under `proyecto2/` is read (`proyecto2/CLAUDE.md` imports this file); any other agent reads it before changing code here. Cross-project rules (design principles P#1-P#11, restrictions R-1..R-8, backlog, live-store safety) are in the root `AGENTS.md` and apply here.

## P2 — Quantitative Metrics Pipeline

**Medallion layering:** DB tables follow a Bronze (raw) → Silver (normalized) → Gold (indicators) structure. See `doc/reglas/SCHEMA_REFERENCE.md` §Medallion Architecture for the full table mapping. Gold rows carry `algorithm_version` and `batch_id` audit columns (v26) linking each row to the exact `CALC_VERSION` and pipeline run that computed it.

### Module map

<!-- AUTO:BEGIN p2-module-map -->
```
proyecto2/
  src/
    analysis/
      export_metrics.py
    calculations/
      capture_ratios.py
      consistency.py
      currency_factor.py
      deflation.py
      drawdown.py
      m2_global_builder.py
      macro_sensitivity.py
      momentum.py
      peer_panel.py
      persistence.py
      regime_returns.py
      returns.py
      risk_metrics.py
      rolling_stats.py
      short_horizon.py
      srri.py
    discovery/
      macro_discovery.py
      nav_discovery.py
      source_chain.py
    pipeline/
      run_pipeline.py
    readers/
      db_readers.py
    reports/
      rolling_dashboard.py
    utils/
      family_versions.py
      fingerprint.py
      logger.py
      time_windows.py
      validators.py
    writers/
      metrics_writer.py
  tests/
    analysis/
      test_export_metrics.py
    calculations/
      test_alert_engine_fix_20260913.py
      test_annualization_by_dates_fnd0253.py
      test_annualization_interval_fnd0240.py
      test_calculations_pg.py
      test_consistency.py
      test_deflation.py
      test_deflation_month_align_fnd0241.py
      test_drawdown.py
      test_eur_view_coupling_fnd0243.py
      test_fx_eur_view_fnd0235.py
      test_m2_global_builder_pg.py
      test_macro_sensitivity.py
      test_momentum_units_fnd0302.py
      test_p2_bundle_flags.py
      test_peer_panel_fnd0255_pg.py
      test_period_key_invariance_pg.py
      test_regime_returns.py
      test_returns.py
      test_risk_metrics.py
      test_rolling_stats.py
      test_short_horizon.py
      test_sortino_downside_bound.py
      test_sortino_min_downside_count_fnd0242.py
      test_srri.py
    discovery/
      test_ecb_fx_series_fnd0243.py
      test_eurostat.py
      test_fred_es.py
      test_fred_es2.py
      test_historia.py
      test_macro_discovery_loaders_20260927.py
      test_macro_discovery_pg.py
      test_nav_currency_no_default_fnd0243.py
      test_nav_daily_quality_gate_fnd0177.py
      test_nav_discover_delta_fnd0296.py
      test_nav_discovery_cli_isin_split_20260927.py
      test_nav_discovery_date_types_pg.py
      test_nav_discovery_freeze_20260915.py
      test_nav_discovery_splice_20260915.py
      test_nav_discovery_writers_pg.py
      test_nav_monthly_write_20260913.py
      test_nav_source_unavailable_rc.py
      test_nav_token_renewal_fnd0295.py
      test_source_chain_fnd0256.py
      test_sovereign_yield_series_fnd0294.py
    pipeline/
      test_family_versioning_fnd0236_pg.py
      test_family_versioning_wiring_fnd0236.py
      test_metric_state_scoped_run_fnd0144.py
      test_min_nav_rows_clear_fnd0168.py
      test_nav_data_freshness_delta_20260927.py
      test_refresh_matviews_and_update_isin.py
      test_rolling_prune_wiring_fnd0301.py
      test_run_pipeline_writes_pg.py
    readers/
      test_db_readers_misc_pg.py
      test_eur_nav_readers_fnd0243_pg.py
      test_preflight.py
      test_preflight_pg.py
      test_reliability_signals.py
      test_reliability_signals_pg.py
    reports/
      test_rolling_dashboard.py
      test_rolling_dashboard_pg.py
    utils/
      test_family_versions.py
      test_fingerprint.py
      test_logger_structured.py
    writers/
      test_metrics_writer_pg.py
```
<!-- AUTO:END p2-module-map -->

### DB tables used by P2

| Table | Key | Contents |
|-------|-----|----------|
| `fund_nav_monthly` | `(ISIN, Date)` | Monthly NAV series (source: Morningstar) |
| `nav_sources` | `ISIN` | Morningstar ms_id + date range + status |
| `series_macro` | `(date, indicator, geography)` | All macro time series |
| `fund_metrics` | `(ISIN, metric, horizon, real_flag)` | All calculated metrics |
| `fund_nav_daily` | `(ISIN, Date)` | Daily NAV series — short-horizon source |
| `fund_metric_timeseries` | `(ISIN, metric, date, ...)` | Rolling metric series (~16.2M rows; v27, `value_nominal`/`value_real`/`has_real` — no more `real_flag` key; `gold.v_fund_metric_timeseries_long` reconstructs the long shape) |
| `fund_metric_alerts` | `(ISIN, alert_type, ...)` | Rolling-signal alerts |
| `fund_metric_state` | `ISIN` | Per-fund calc fingerprint/state (cache control) |
| `p2_pipeline_log` | `id` | Per-run traceability |

### Metric horizons

`since_inception` (always), `crisis_windows` (per `CRISIS_WINDOWS` in config), rolling windows (per `ROLLING_WINDOWS` in config).  
`real_flag=0` → nominal; `real_flag=1` → deflated by IPC.

### Annualisation convention (FND-0240, FND-0253)

`returns.annualized_return` / `rolling_stats` historically took the horizon from the NUMBER OF NAV ROWS, so a monthly series with missing
months was treated as consecutive (return_ann inflated: LU1663960000 stored 20.9% vs 6.5% by dates). `ANNUALIZATION_BY_DATES_ENABLED`
(**ON since 2026-10-11**, `CALC_VERSION` 20261011) annualises by the first..last calendar month through the same `shared/annualization.years_spanned` (a dense series is
bit-identical), weights a k-month return as k months in the vol (`kweighted_std`), uses one-month returns only for consistency / regime
statistics, and is mirrored by `statistical_audit/timeseries.py` and the PIT metrics. It was flipped together with the `CALC_VERSION` bump and
the recompute of the `risk`, `rolling`, `regime`, `persistence` families (`utils/family_versions.FLAG_FAMILIES`); undoing it needs another
recompute. The build-time continuity guard (`FUND_DATA_ELIGIBILITY_ENABLED`, `P3_FUND_NAV_MIN_COVERAGE`) stays as defence in depth.

### Momentum and rolling-window conventions (FND-0302, FND-0301)

`momentum_1y` / `momentum_3y` = the fund's ANNUALISED return over `returns.trailing_window(nav, N months)` (the one definition of a rolling window,
shared with the rolling horizons) minus the mean of its peers' stored `rolling_Ny` `return_ann`: same window, same units. A fund's rolling time series
is REPLACED on every run (`metrics_writer.prune_timeseries` before `write_timeseries`), so a date the run no longer computes disappears.

### Idempotency & caching

Runs are idempotent via an input fingerprint: `utils/fingerprint.py::compute_input_hash()` (SHA-1 over NAV
last-date/rows/value + IPC coverage + `METRIC_VERSION` + `CALC_VERSION`) is stored in `fund_metric_state`.
Unchanged inputs → 100% cache-hit, 0 recomputed. **Bump `CALC_VERSION` (`run_pipeline.py`, currently
`"20261011"`) to force a full recompute** of all ISINs (e.g. after changing calculation logic). Enabling a P2 bundle flag
also forces it (the fingerprint includes the flags), but bump `CALC_VERSION` with it anyway: `algorithm_version` is stamped from
`CALC_VERSION` alone, so only a bump lets the beta gate and the P3 `metrics_calc_version` gate tell recomputed rows from old ones.
`CALC_VERSION` is the *global* epoch. Per-metric-family versions (FND-0236, `proyecto2/src/utils/family_versions.py`, behind
`FAMILY_VERSIONING_ENABLED`, off until the owner applies `scripts/ops/migrate_fund_metric_family_state.py`) let a change that
touches one of the nine families recompute only that family: add it to `FAMILY_CALC_OVERRIDES` instead of bumping `CALC_VERSION`.

### Macro factors (OLS model — machine-verified)

<!-- AUTO:BEGIN macro-factors -->
| Factor key | Metric |
|------------|--------|
| `d_rate_eu` | `beta_rate_eu` |
| `m3_yoy` | `beta_m3_yoy` |
| `ipc_yoy_es` | `beta_ipc_es` |
| `ipc_yoy_eu` | `beta_ipc_eu` |
| `ipc_yoy_us` | `beta_ipc_us` |
| `ipc_yoy_jp` | `beta_ipc_jp` |
| `ipc_yoy_cn` | `beta_ipc_cn` |
| `d_rate_us` | `beta_rate_us` |
| `d_rate_jp` | `beta_rate_jp` |
| `d_rate_cn` | `beta_rate_cn` |
| `oil_yoy` | `beta_oil` |
| `copper_yoy` | `beta_copper` |
| `cli_yoy_eu` | `beta_cli_eu` |
| `cli_yoy_us` | `beta_cli_us` |
| `dxy_yoy` | `beta_dxy` |
| `gold_yoy` | `beta_gold` |
| `spread_hy` | `beta_spread_hy` |
| `spread_ig` | `beta_spread_ig` |
| `vix_yoy` | `beta_vix` |
| `term_spread` | `beta_term_spread` |
| `eur_jpy_yoy` | `beta_eur_jpy` |
| `eur_gbp_yoy` | `beta_eur_gbp` |
| `eur_cny_yoy` | `beta_eur_cny` |
<!-- AUTO:END macro-factors -->

2 additional derived metrics (computed at runtime, not in `_FACTOR_TO_METRIC`): `energy_sensitivity_pct`, `hy_spread_sensitivity_pct`.  
VIF filter applied; factors with VIF > 10 excluded.

### Data discovery (run before pipeline)

#### Registered macro data sources (machine-verified)

<!-- AUTO:BEGIN data-sources -->
| Source | Loader |
|--------|--------|
| `ine` | `load_ine_ipc` |
| `bce` | `load_bce_series` |
| `fred` | `load_fred_series` |
| `eurostat` | `load_eurostat_series` |
<!-- AUTO:END data-sources -->

#### Commands

```batch
# NAV discovery (verify fund exists in Morningstar)
python -m proyecto2.src.discovery.nav_discovery --mode discover

# NAV load (historical, one-time)
python -m proyecto2.src.discovery.nav_discovery --mode load --desde 2000-01-01

# NAV update (monthly)
python -m proyecto2.src.discovery.nav_discovery --mode update

# Macro data (one source or all)
python -m proyecto2.src.discovery.macro_discovery --source eurostat
python -m proyecto2.src.discovery.macro_discovery --source all
```

### Launcher commands

**P2 data discovery (run before pipeline — macro + NAV):**
```batch
scripts\launch\P2_discoverLoadMetrics.bat
```
Runs macro discovery (BCE, FRED, Eurostat) → NAV discover → NAV load in sequence.
Log: `proyecto2/log/log_P2_discoverMetrics_YYYYMMDD_HHMMSS.log`.

Individual steps (debug / single-source):
```batch
python -X utf8 -m proyecto2.src.discovery.nav_discovery --mode discover
python -X utf8 -m proyecto2.src.discovery.nav_discovery --mode load --desde 2000-01-01
python -X utf8 -m proyecto2.src.discovery.nav_discovery --mode update
python -X utf8 -m proyecto2.src.discovery.macro_discovery --source bce
python -X utf8 -m proyecto2.src.discovery.macro_discovery --source fred
python -X utf8 -m proyecto2.src.discovery.macro_discovery --source eurostat
python -X utf8 -m proyecto2.src.discovery.macro_discovery --source all
```

**P2 full pipeline (two-phase: pipeline → export_metrics on RC=0):**
```batch
scripts\launch\P2_calculateIndicators.bat
```
Or standalone pipeline only (debug/single ISIN):
```batch
cd C:\desarrollo\fondos
python -X utf8 -m proyecto2.src.pipeline.run_pipeline
```

**P2 single ISIN (debug):**
```batch
python -X utf8 -m proyecto2.src.pipeline.run_pipeline --isin LU1234567890 --dry-run
```
