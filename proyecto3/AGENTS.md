# proyecto3 — P3 scoring and portfolio (area guide)

Loaded by Claude Code the first time a file under `proyecto3/` is read (`proyecto3/CLAUDE.md` imports this file); any other agent reads it before changing code here. Cross-project rules (design principles P#1-P#11, restrictions R-1..R-8, backlog, live-store safety) are in the root `AGENTS.md` and apply here.

## P3 — Scoring & Portfolio

### Module map

<!-- AUTO:BEGIN p3-module-map -->
| Module | Folder |
|--------|--------|
| `backtest_inference.py` | `proyecto3/src` |
| `backtesting.py` | `proyecto3/src` |
| `data_freshness.py` | `proyecto3/src` |
| `fund_scorer.py` | `proyecto3/src` |
| `monthly_report.py` | `proyecto3/src` |
| `pit_backtest.py` | `proyecto3/src` |
| `pit_baselines.py` | `proyecto3/src` |
| `pit_cache.py` | `proyecto3/src` |
| `pit_candidates.py` | `proyecto3/src` |
| `pit_crisis_variants.py` | `proyecto3/src` |
| `pit_inputs.py` | `proyecto3/src` |
| `pit_macro.py` | `proyecto3/src` |
| `pit_metrics.py` | `proyecto3/src` |
| `pit_oos.py` | `proyecto3/src` |
| `pit_peer_metrics.py` | `proyecto3/src` |
| `pit_run.py` | `proyecto3/src` |
| `pit_short_horizon.py` | `proyecto3/src` |
| `pit_value.py` | `proyecto3/src` |
| `portfolio_builder.py` | `proyecto3/src` |
| `portfolio_engine.py` | `proyecto3/src` |
| `regime_classifier.py` | `proyecto3/src` |
| `scenario_lifecycle.py` | `proyecto3/src` |
| `score_candidates.py` | `proyecto3/src` |
| `scorer_config.py` | `proyecto3/src` |
<!-- AUTO:END p3-module-map -->

### Regime classification (7 regimes, priority order)

| Regime | Trigger |
|--------|---------|
| `Crisis_Financiera` | HY spread > 600bps AND VIX YoY > 30% |
| `Shock_Energetico` | WTI oil YoY > 25% |
| `Estanflacion` | Weak/negative growth + IPC > 4% |
| `Contraccion` | Recession + low/negative inflation |
| `Recalentamiento_Tardio` | Rates rising, IPC high, CLI still positive |
| `Recalentamiento` | Strong growth, IPC high and rising |
| `Expansion` | Normal growth, moderate rates, IPC < 3% |

### Sub-portfolio weights by regime

| Regime | Defensiva | Equilibrada | Dinámica |
|--------|-----------|-------------|----------|
| Crisis_Financiera | 70% | 25% | 5% |
| Shock_Energetico | 55% | 35% | 10% |
| Estanflacion | 50% | 35% | 15% |
| Contraccion | 60% | 35% | 5% |
| Recalentamiento_Tardio | 40% | 40% | 20% |
| Recalentamiento | 30% | 40% | 30% |
| Expansion | 20% | 45% | 35% |

### Scoring (fund_scorer.py)

**Layer 1 – Hard filters (auto-exclude):** max_drawdown above sub-portfolio limit; return_ann_real below limit; SRRI_nav > 5 in Defensiva; Credit_Quality = High Yield in Defensiva.

**Layer 2 – Base score weights (vary by sub-portfolio):**

| Metric | Defensiva | Equilibrada | Dinámica |
|--------|-----------|-------------|----------|
| return_ann_real | 20% | 25% | 30% |
| sharpe | 25% | 20% | 15% |
| max_drawdown | 30% | 20% | 15% |
| alpha_persistence | 15% | 15% | 15% |
| capture_ratio | 5% | 10% | 15% |
| momentum_rank | 5% | 10% | 10% |

**Layer 3 – Structural multipliers: NEUTRAL since the FND-0193 SIMPLIFY verdict (2026-10-19).** Every `MULT_*` in `proyecto3/config/scorer_params.yaml` is 1.0, so the final score is the base score: removing the layer changed the out-of-sample Sharpe by −0.0003 (90 % CI −0.058..+0.058) with the same drawdown (`doc/decisiones/SIMPLIFY_RUNBOOK_FND-0193.md`; the previous values — FX ×0.80, alpha ×1.15, empirical regime-return and crisis-stress multipliers — are in the file's previous commit; the code paths go after two stable cycles). Do not re-enable a multiplier without new out-of-sample evidence (FND-0299 checkpoints). The macro multipliers (beta_oil, beta_rate_eu, macro_r2, crisis beta_spread_hy / beta_vix) were **retired 2026-10-04** (FND-0225): the oil / VIX / HY legs never fired in production (threshold scale and sign), and the full-universe PIT backtests showed no robust benefit beyond one episode (2008-09). The scorer no longer reads the macro betas; P2 still computes them. Do not reintroduce without out-of-sample validation (FND-0193).

### Portfolio construction constraints

Max 10 funds/sub-portfolio · max 20% per fund · max 2 funds per manager (a count, global across sub-portfolios — there is no weight cap per manager) · min 3% per fund · max 5 same Fund_Nature per sub-portfolio.  
Weight method: `equal` (FND-0193 SIMPLIFY, 2026-10-19: score weighting did not earn its place out of sample; the score still ranks and selects, hysteresis included; `portfolio_builder.WEIGHT_METHOD`). Sub-portfolio and master weights sum to exactly 1.0000 (largest-remainder rounding, never a weight past
its bound); the currency budget is a construction constraint too (see "Scenario lifecycle & currency exposure").

### Monthly report (monthly_report.py)

`generate_report(conn)` (default `out/reports/p3/`, `shared.config.REPORT_DIRS["p3"]`; P1 exports go to `out/reports/p1/`, P2 metrics to `out/reports/p2/`) → Excel with sheets: `0_Portada`, `1_Cartera`, `2_Regimen`, `3_Backtesting`, `4_Rotacion`, `5_Macro_Indicadores`. `4_Rotacion` only shows the rotation plan the builder persisted in the scenario (needs `ROTATION_COST_GATE_ENABLED` and a previous portfolio); otherwise it says so.

### Scenario lifecycle & currency exposure (`scenario_lifecycle.py`, `shared/fx_exposure.py`)

A portfolio scenario is `DRAFT` → `PRODUCTION` → `ARCHIVED` (`portfolio_scenarios.status`; one PRODUCTION row, DB-enforced). The build
(`p3_build_portfolio.py`) only writes a DRAFT, under a unique id `cartera_<regime>_<YYYYMM>_b<YYYYMMDDHHMMSS>`, and never replaces anything that is not
a DRAFT (`--replace-draft` replaces a DRAFT). Promotion is an owner command, `scripts\launch\p3_promote_scenario.py <id>`, gated on: DRAFT with weights
summing to 1, no stale input in the P3 freshness gate, net non-EUR exposure ≤ `MAX_NON_EUR_UNHEDGED_WEIGHT` (30%, `shared/fx_exposure.py`: a foreign
class counts as exposed whatever it hedges inside; a EUR class only if unhedged with non-EUR assets) and the **data gate** (FND-0251): the evidence run (FND-0261,
`AUDIT_COMPLETENESS_CONTRACT_ENABLED` ON: the latest `control.audit_run` header of a FULL-scope, not INCOMPLETE `p2_metrics` audit; a sample or INCOMPLETE run is
never evidence, an INCOMPLETE run exits 2; contract and header: `doc/reglas/AUDITORIA_ESTADISTICA.md` §6.4) must be newer than the DRAFT build and have no open *hard defect* (`HARD_INVARIANT`, `BLOCK2`, `TIMESERIES_DUPLICATE`:
`shared/statistical_audit/gating.py`, the same predicate as the audit's `--mode check`) on a fund of the scenario; a hard defect outside it needs an
active accepted residual (`accept_audit_residual.py`, AP + expiry: a GRANT then a separate `--approve`, recorded in the append-only
`control.audit_exception_event`; with `AUDIT_EXCEPTION_EVENTS_ENABLED` ON an unreadable or inconsistent history refuses as `[GATE:EXCEPTIONS]`, FND-0263,
`doc/reglas/GOBIERNO_CONTROLES.md` §5), and an accepted residual never covers a selected fund. Statistical anomalies
(`OUTLIER_MAD_Z`) on the scenario's funds are listed and never block. A fund already in PRODUCTION that carries a `DD_CAT_P03` / `VOL_CAT_P97`
ALARM in `fund_metric_alerts` may not gain weight in the DRAFT (`[GATE:ALARM_WATCH]`, `ALARM_WATCH_GATE_ENABLED`, FND-0289; same reasoned override). The exposure limit is a **hard gate with no override** (the mandate changes in
`shared/config.py` by a reviewed commit, not at promotion time); only the data gates on the scenario's own funds take a written reason **and the owning
ticket** (`--accept-alarms "motivo" --ap FND-nnnn`), logged in `ingestion_log` as `[OVERRIDE:<gate>]`; every refusal carries a stable `[GATE:<X>]`
code and residuals about to lapse are warned `AUDIT_RESIDUAL_EXPIRY_WARN_DAYS` ahead. The build-time counterpart is
`FUND_DATA_ELIGIBILITY_ENABLED` (**ON since 2026-10-11**; `fund_scorer.load_eligibility_inputs` / `check_hard_filters`): a fund whose monthly NAV is older than
`P3_FUND_NAV_MAX_AGE_DAYS`, that carries an active accepted residual, or whose own monthly NAV span is too sparse (coverage < `P3_FUND_NAV_MIN_COVERAGE` or a run of
`P3_FUND_NAV_MAX_GAP_RUN_MONTHS` missing months: P2 annualises by rows, not dates, FND-0253) is ineligible, so the builder never selects what the gate would refuse; read
`scripts/audit/eligibility_switch_diff.py` (read-only ON/OFF eligible-set diff) shows what it changes; the PIT backtest applies the same rule (FND-0254): a fund's stale-NAV drop uses the same `P3_FUND_NAV_MAX_AGE_DAYS` and the NAV-continuity test is evaluated from the rows observable at each date (`shared/nav_continuity.py`, one predicate for both), except accepted audit residuals, which are today's governance decisions and would be look-ahead. The build declares its structural cash
(`cash_weight` in the scenario notes: a sub-portfolio short of eligible non-exposed funds holds the residue as cash, FND-0187, and prints which one
and why); the gate requires weights + declared cash = 1 with cash ≤ `MAX_PORTFOLIO_CASH_WEIGHT` (10%, also a hard gate), and a refused promotion
writes one `P3_SCENARIO_PROMOTION_BLOCKED` row to `ingestion_log`. The report and the rotation plan read PRODUCTION; with
none (or before the owner applies `scripts/ops/migrate_portfolio_scenario_status.py`) the report fails closed (RC 3) unless `--scenario ID` or `--draft`
(the newest DRAFT) is given, in which case it is marked NO PRODUCCION and written under its own file name in the `borradores\` subfolder of the
output directory, never next to the official report. The cycle launchers (`P2P3_runCycle.bat`,
`P1P2P3_runCycle.bat`) report the DRAFT they just built with `--draft`; the official report is the owner's `P3_generateReport.bat` after promotion. Cover sheet
shows the scenario and its currency exposure. The limit is also a **construction constraint** (`FX_EXPOSURE_CONSTRAINT_ENABLED`,
`portfolio_engine.select_within_exposure`): each sub-portfolio drops its lowest-scored exposed fund and re-selects until its exposed weight is within
the budget, so a DRAFT is promotable by construction (a residue the non-exposed pool cannot absorb is held as cash). It is the safety net under the
regime-evidence FX neutralisation, which stays dormant until its PIT validation. The legacy backtester and the PIT backtest build under the same
constraint (`score_candidates.exposure_constrained`; flags in `PitInputs.fx_exposed`, loaded by `pit_inputs.load_fx_exposed`, deliberately not a column
of `attrs`, which feeds the PIT score cache keys), so a PIT run evaluates the model production runs.

**Valid-document eligibility (FND-0323 D1, `P3_WRONG_DOC_EXCLUSION_ENABLED`, **ON since 2026-10-25**):** an ISIN whose `KIID_Status` is `WRONG_DOC` has no valid document, so with the switch ON it is
ineligible in P3 (`exclusion_reason` `wrong_doc: ...`, score 0) -- P3 eligibility only: P1 already drops it from every block and P2 keeps computing its metrics. One predicate and
one reason text for both paths (`shared/doc_eligibility.py`); live it is a `doc_status_reason` column added by `fund_scorer.load_fund_metrics_for_scoring` and read first by
`check_hard_filters`; the PIT backtest gets the same column from `pit_inputs.load_attributes` (it is part of the score cache key because scores depend on it) and applies today's
status at every date (`KIID_Status` has no history; the run manifest says so). Validate with `scripts/audit/wrong_doc_switch_diff.py` (read-only, 40-ISIN sample, live and PIT).

### Data-freshness gate (`data_freshness.py`)

`scripts/launch/p3_build_portfolio.py` checks input freshness **before** scoring or persisting and
exits `P3_EXIT_STALE_INPUTS` (2) if it fails: the 10th percentile of each active fund's newest
monthly NAV, the last valid date of each regime input, the newest harvest, and that at least
`P3_MIN_UNIFORM_METRICS_SHARE` of the active funds have their metrics on ONE `CALC_VERSION` (a partial
refresh after a version bump would mix incomparable metrics). The check is universe-level (a few frozen
funds do not block a run); limits live in `shared/config.py::P3_FRESHNESS_MAX_AGE_DAYS`.
`--allow-stale` overrides (and is audited: `P3_STALE_BYPASS` rows in `ingestion_log` before anything is scored; see `NORMAS_IMPLEMENTACION.md` §5d); `--dry-run` only warns.
A further universe check, `family_refresh_pending`, is STALE while any active fund whose `Fund_Nature` the family builder rewrote still awaits `run_block.py --family-nature-refresh`.

### DB tables used by P3

| Table | Contents |
|-------|----------|
| `fund_scores` | Per-fund scores by sub-portfolio and regime |
| `portfolio_scenarios` | Built portfolios per scenario |
| `portfolio_weights` | Fund weights within each scenario |

### Launcher commands

**P3 monthly report** (the PRODUCTION scenario; `--scenario ID` for another one, marked NO PRODUCCION):
```batch
scripts\launch\P3_generateReport.bat
```

**P3 point-in-time backtest (read-only on the DB; full universe is owner-run, ~20-25 min cold / <1 min warm cache):**
```batch
C:\data\envs\des\python.exe -X utf8 scripts\launch\p3_pit_backtest.py --dry-run
C:\data\envs\des\python.exe -X utf8 scripts\launch\p3_pit_backtest.py --sample-per-nature 4
C:\data\envs\des\python.exe -X utf8 scripts\launch\p3_pit_backtest.py
```
Artifacts per run in `out\reports\pit_backtest\<STAMP>\` (inside the project; `--out-dir` overrides) (manifest, timings, summary, monthly table,
cost sensitivity 0/25/50 bp, hysteresis/turnover experiment, series stats, universe/short-gate coverage, all PIT scores); log in
`proyecto3/log/log_P3_pitBacktest_<STAMP>.log`; parquet cache in `proyecto3/cache/pit/` (git-ignored, `--no-cache` to bypass).
