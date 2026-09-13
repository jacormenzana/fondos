# AGENTS.md — Single Source of Truth (índice + orientación)

**This is the authoritative entry point for any agent (human or LLM) working in this repository.**
Read this file first. It carries the full project orientation, and it is the **index** to the
canonical specification documents in `doc/reglas/` (see table below). Those documents are canonical
for their respective domains; this file must not duplicate their content — it points to them.

| Domain doc (`doc/reglas/`) | Answers | Canonical for |
|---|---|---|
| `PRINCIPIOS_DISENO.md` | *why* | The 11 non-negotiable design principles (P#1–P#11) |
| `RESTRICCIONES_ARQUITECTURA.md` | *what not to do* | Code restrictions R-1..R-8 + pre-commit checklist |
| `NORMAS_IMPLEMENTACION.md` | *how to build/test/log* | Runtime standards, DQ `check_code`, logging v2, regressions |
| `MODELO_SEMANTICO.md` | *what values mean* | Attribute semantics + SC-A1..SC-F4 consistency rules |
| `SCHEMA_REFERENCE.md` | *where data lives* | Full DB table/column reference (P1/P2/P3) |
| `PROVENANCE_DATOS_MACRO.md` | *where macro data comes from* | Macro-series sourcing, data-source provenance, API keys, refresh cadence |
| `P4_BI_CHARTER.md` | *how BI sync works* | P4 Postgres/Superset charter (ETL, ports, DDL, datasets) |
| `AUDITORIA_ESTADISTICA.md` | *how statistical distribution audits work* | 7-block model evaluation, indicator catalog, generic function/config catalog for the cost-attribute and P2-metrics statistical audits |

| Backlog registers (`doc/backlog/`) | Answers | Canonical for |
|---|---|---|
| **Live HTML artifact** | *consolidated master view* | All-domain incident backlog (always-current, v8 2026-08-13): `https://claude.ai/code/artifact/24eb50ad-1274-4149-bae6-6cda184f1a0e` |

**Workflow before touching code:** read this file → read the relevant domain doc(s) above →
verify your change against `RESTRICCIONES_ARQUITECTURA.md` (R-1..R-8) and the pre-commit checklist.
If a request conflicts with a principle or restriction, stop and report.

---

## Context

~3,200 European investment funds. Goal: capital preservation relative to IPC+M3 (~6–7% annual, max drawdown 15%, 3–5 year horizon).  
Stack: Python 3.13, SQLite, Windows 10, Conda env `des`.  
<!-- AUTO:BEGIN schema-version -->
DB: `db/fondos.sqlite` (schema v26). Master list: `c:\data\fondos\in\GestoresDeFondosv1.xlsx`.
<!-- AUTO:END schema-version -->

---

## Architecture

```
P1  Ingestion + classification     → ACTIVE
P2  Quantitative metrics           → ACTIVE
P3  Regime-aware scoring + portfolio → ACTIVE (modules exist, production use evolving)
P4  Analytics/BI sync (SQLite → Postgres + Superset) → ACTIVE (rolling-signal visualization)

Flow: P1 → P2 → P3 → P4  (unidirectional)
```

**Bounded exception — P1 ← P2 feedback (2026-07-17, accepted):** P1's evidence classifier
(`resolve_nature_evidence`) reads `fund_metrics.srri_nav` (a P2 realized-volatility output) to **veto/
arbitrate** `Fund_Nature` — never to derive it (see `PRINCIPIOS_DISENO.md` §P#6 scope). This is a
deliberate, bounded feedback, not a cycle to "fix": it **degrades gracefully** (no NAV → `srri_nav`
NULL → ex-ante-only classification, no error) and **converges** (classification moves toward realized
behaviour as P2 coverage grows). Ordering: run P2 before the P1 nature-first pass for freshest data.

---

## P1 — Classification Pipeline

### Execution order per fund

| Step | Module | What it does |
|------|--------|--------------|
| 1 | `proyecto1/core/io.py` | Fetch KIID (cache-first; HTTP only on FORCE_REFRESH) |
| 2 | `proyecto1/core/kiid_parser.py` | `parse_kiid_generic()` → SRRI, costs, dates, language |
| 3 | `proyecto1/blocks/<block>.py` | Specialized classifier → `classification` dict |
| 4 | `proyecto1/core/fund_characterizer.py` | Fill missing v3 attributes |
| 5 | `proyecto1/core/pipeline.py` | Orchestrate + INTER rules + defaults |
| 6 | `proyecto1/core/sqlite_writer.py` | Idempotent UPSERT with COALESCE |
| 7 | `proyecto1/core/fund_family_builder.py` | Run once after all blocks; group share classes into families |

### Classification blocks (sequential, mutually exclusive)

`monetarios → rf_corto → rf_flexible → renta_variable → mixtos → alternativos → restantes`

Each block exposes `classify_fund(name, kiid_text, ...)` and `get_universe_isins(df_master)`.  
`restantes` is residual: takes all unclassified ISINs; exposes `get_universe_isins(df_master, conn)`.

> **Paradigm note (current — `--nature-first --master-db`):** Block execution order is NOT the
> classification authority. The `--nature-first` dispatch routes each ISIN directly to its block
> based on Fund_Nature already persisted in the DB (via `resolve_nature_evidence()`). The
> sequential order above is the fallback dispatch chain for ISINs with no DB record — it does not
> determine nature for established funds. Single source of classification truth: `classify_utils.py`
> logic + the block's `classify_fund()` output.

### KIID_Status state machine

| Status | Meaning |
|--------|---------|
| `CACHED` / `OK` | Text in DB → no HTTP, returned from DB in < 1s |
| `FORCE_REFRESH` | Re-download on next cycle |
| `WRONG_DOC` | PDF mismatch; fund excluded from all blocks |
| `NOT_FOUND` | URL unreachable |

HTTP policy: 3 retries (1s/2s/4s backoff), timeout 15s. 429 does NOT retry.  
`scripts/launch/mark_stale.py` marks max 50 funds/cycle as FORCE_REFRESH (age > 180 days).

### Fund discovery & KIID lifecycle (`proyecto1/harvest/`) — new paradigm

> **Paradigm note:** ISIN discovery and lifecycle management is now driven by the **Deutsche Bank
> live XML catalogue** (`catalogo.xml`), not by `GestoresDeFondosv1.xlsx`. The Excel is still
> supported via `--master` mode but is no longer authoritative for which funds exist or are retired.
> `--master-db` loads the pipeline universe from `db_document_catalogue` (latest harvest snapshot).

| Script | Purpose |
|--------|---------|
| `p1_db_harvest.py` | Phases 0–3: probe page, fetch `catalogo.xml` → raw JSONL + `db_document_catalogue`, codSus discovery report |
| `p1_kiid_sync.py` | Phases 4–5: KIID delta vs local PDFs, net-new downloads, retire-orphans, `kiid_lifecycle` |

**5-phase flow (run in order):**
1. `p1_db_harvest.py --harvest` — fetch XML → upsert into `db_document_catalogue` with `harvest_ts`
2. `p1_db_harvest.py --report-codsus` — **quality gate**: flags new funds (in harvest, not in `fund_master`) and retirement candidates (in `fund_master`, not in harvest); review before proceeding
3. `p1_kiid_sync.py` (dry-run) — delta: `missing` = to download; `orphans` = local PDFs not in latest harvest
4. `p1_kiid_sync.py --sync` — download net-new KIIDs (hrefs captured verbatim, §6.1 — never URL-built)
5. `p1_kiid_sync.py --retire-orphans` — archive orphan PDFs to `kiid_retired/YYYYMMDD/`; record in `kiid_lifecycle`

**Storage:**
Active: `C:\data\fondos\kiid\{ISIN}.pdf` · Retired: `C:\data\fondos\kiid_retired\YYYYMMDD\{ISIN}.pdf`
PDFs and `Raw_KIID_Text` (in `fund_kiid_metadata`) are **never deleted**.

`kiid_lifecycle` table — one row per lifecycle period per ISIN:
- `status = 'commercializing'` (active) or `'retired'`; `retire_dir` = YYYYMMDD archive subdirectory

**"Orphan" (precise):** a local PDF in `kiid/` absent from the latest harvest's `db_document_catalogue` — fund was removed from the live XML catalogue. Not a fund that was "never registered".

**Retirement cause:** fund disappears from Deutsche Bank's `catalogo.xml` → absent from latest `harvest_ts` in `db_document_catalogue` → Phase 3 report flags it → `--retire-orphans` archives it.

**Pipeline impact of retired ISINs:** `--master-db` / `--nature-first` load universe from `db_document_catalogue` MAX `harvest_ts` — retired ISINs have 0 rows there and **cannot be reached by the classifier**. Direct SQL is the correct mechanism for `fund_master` attribute updates on retired ISINs (NOT a P#7 violation — P#7 covers active, classifier-reachable funds only).

**Diagnosing a fund absent from the pipeline:** query `kiid_lifecycle WHERE isin=?` first.
- `status='retired'` → retired KIID; `In_Current_Universe=0`; update via SQL.
- No rows → check `db_document_catalogue`; may never have been in the XML catalogue.

### Key support modules

<!-- AUTO:BEGIN kill-switches-line -->
- `shared/config.py` — all constants: `DB_PATH`, `SCHEMA_VERSION` (`"v26"`), `DOMAIN_VALUES`, `ATTRIBUTE_CATALOG`, kill-switches (`PRIIPS_COST_EXTRACTION_ENABLED`, `SHORT_HORIZON_SCORING_ENABLED`, `ROLLING_STATS_ENABLED`, `ROLLING_PCTILE_P3_ENABLED`, `BENCHMARK_DECOMP_ENABLED`, `BENCHMARK_ROLE_ENABLED`, `INTER18_RECONCILIATION_ENABLED`, `DLA2_ARBITRATION_ENABLED`)
<!-- AUTO:END kill-switches-line -->
- `shared/schema_checks.py` — `assert_schema_alignment()` validates DB columns at startup
- `proyecto1/core/classify_utils.py` — **single source of truth** for all categorical normalization maps (EN→ES for Sector_Focus, Type, Family). Import from here; never duplicate elsewhere (P#11 / R-1).
- `proyecto1/core/cost_arbitration.py` — dual-path cost arbitration (PRIIPs vs UCITS)
- `proyecto1/core/priips_cost_extractor.py` + `ucits_cost_extractor.py` — cost extraction

### All P1 modules (machine-verified)

<!-- AUTO:BEGIN p1-module-map -->
| Module | Folder |
|--------|--------|
| `benchmark_normalizer.py` | `proyecto1/core` |
| `classify_utils.py` | `proyecto1/core` |
| `cost_arbitration.py` | `proyecto1/core` |
| `cost_cross_validator.py` | `proyecto1/core` |
| `cost_format_router.py` | `proyecto1/core` |
| `cost_format_signals.py` | `proyecto1/core` |
| `cost_pct_anchored.py` | `proyecto1/core` |
| `cost_scale.py` | `proyecto1/core` |
| `cost_table_parser.py` | `proyecto1/core` |
| `dla_extractor.py` | `proyecto1/core` |
| `dla_table_serializer.py` | `proyecto1/core` |
| `fund_characterizer.py` | `proyecto1/core` |
| `fund_family_builder.py` | `proyecto1/core` |
| `io.py` | `proyecto1/core` |
| `kiid_parser.py` | `proyecto1/core` |
| `mark_stale.py` | `proyecto1/core` |
| `normalize_db_casing_v20.py` | `proyecto1/core` |
| `PATCHES_pipeline.py` | `proyecto1/core` |
| `pipeline.py` | `proyecto1/core` |
| `priips_cost_extractor.py` | `proyecto1/core` |
| `sqlite_writer.py` | `proyecto1/core` |
| `srri_text.py` | `proyecto1/core` |
| `srri_v4_geometric.py` | `proyecto1/core` |
| `srri_v5_geometric.py` | `proyecto1/core` |
| `ucits_cost_extractor.py` | `proyecto1/core` |
| `alternativos.py` | `proyecto1/blocks` |
| `mixtos.py` | `proyecto1/blocks` |
| `monetarios.py` | `proyecto1/blocks` |
| `renta_variable.py` | `proyecto1/blocks` |
| `restantes.py` | `proyecto1/blocks` |
| `rf_corto.py` | `proyecto1/blocks` |
| `rf_flexible.py` | `proyecto1/blocks` |
<!-- AUTO:END p1-module-map -->

---

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
    pipeline/
      run_pipeline.py
    readers/
      db_readers.py
    reports/
      rolling_dashboard.py
    utils/
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
      test_consistency.py
      test_drawdown.py
      test_macro_sensitivity.py
      test_nav_scale_repair_20260719.py
      test_regime_returns.py
      test_rolling_stats.py
      test_short_horizon.py
    discovery/
      test_eurostat.py
      test_fred_es.py
      test_fred_es2.py
      test_historia.py
      test_nav_monthly_write_20260913.py
    readers/
      test_preflight.py
      test_reliability_signals.py
    reports/
      test_rolling_dashboard.py
    utils/
      test_fingerprint.py
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
| `fund_metric_timeseries` | `(ISIN, metric, date, ...)` | Long-format rolling metric series (~16.6M rows) |
| `fund_metric_alerts` | `(ISIN, alert_type, ...)` | Rolling-signal alerts |
| `fund_metric_state` | `ISIN` | Per-fund calc fingerprint/state (cache control) |
| `p2_pipeline_log` | `id` | Per-run traceability |

### Metric horizons

`since_inception` (always), `crisis_windows` (per `CRISIS_WINDOWS` in config), rolling windows (per `ROLLING_WINDOWS` in config).  
`real_flag=0` → nominal; `real_flag=1` → deflated by IPC.

### Idempotency & caching

Runs are idempotent via an input fingerprint: `utils/fingerprint.py::compute_input_hash()` (SHA-1 over NAV
last-date/rows/value + IPC coverage + `METRIC_VERSION` + `CALC_VERSION`) is stored in `fund_metric_state`.
Unchanged inputs → 100% cache-hit, 0 recomputed. **Bump `CALC_VERSION` (`run_pipeline.py`, currently
`"20260820"`) to force a full recompute** of all ISINs (e.g. after changing calculation logic).

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
| `m2_global_yoy` | `beta_m2_global` |
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

---

## P3 — Scoring & Portfolio

### Module map

<!-- AUTO:BEGIN p3-module-map -->
| Module | Folder |
|--------|--------|
| `backtesting.py` | `proyecto3/src` |
| `fund_scorer.py` | `proyecto3/src` |
| `m2_global_builder.py` | `proyecto3/src` |
| `monthly_report.py` | `proyecto3/src` |
| `portfolio_builder.py` | `proyecto3/src` |
| `regime_classifier.py` | `proyecto3/src` |
| `regime_returns.py` | `proyecto3/src` |
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

**Layer 3 – Regime multipliers:** beta_oil > 0.01 → ×1.20; beta_rate_eu < −0.10 → ×0.70; fx_contribution_pct > 0.60 → ×0.80; alpha_persistence > 0.60 → ×1.15; macro_r2 > 0.50 → ×0.85.

### Portfolio construction constraints

Max 10 funds/sub-portfolio · max 20% per fund · max 30% per manager · min 3% per fund · max 5 same Fund_Nature per sub-portfolio.  
Weight method: `score_proportional`.

### Monthly report (monthly_report.py)

`generate_report(conn, output_dir="c:/data/fondos/reports")` → Excel with sheets: `0_Portada`, `1_Cartera`, `2_Regimen`, `3_Backtesting`, `4_Rotacion`.

### DB tables used by P3

| Table | Contents |
|-------|----------|
| `fund_scores` | Per-fund scores by sub-portfolio and regime |
| `portfolio_scenarios` | Built portfolios per scenario |
| `portfolio_weights` | Fund weights within each scenario |

---

## P4 — Analytics / BI Sync

Pushes SQLite metric tables to a Docker **Postgres** analytics store and surfaces them in **Superset**
for rolling-signal visualization.

| Component | Path / Target |
|-----------|---------------|
| Launcher | `scripts/launch/P4_syncToPostgres.bat` |
| ETL | `shared/load_fondos_to_postgres.py` (SQLite → Postgres) |
| Schema DDL | `db/postgres_analytics_ddl.sql` (one-time) |
| Postgres | Docker, port **5433** (`postgresql://superset:superset@localhost:5433/fondos`) |
| Superset | Docker, port **8088** |

Datasets registered: `fund_metric_timeseries` (long format), `fund_metric_alerts`, `fund_master` (dimension).
Local-only alternative: `proyecto2/src/reports/rolling_dashboard.py` emits a self-contained HTML dashboard.

---

## Commands

All commands: activate Conda env `des` first.

**Full P1 + P2 cycle (recommended for monthly runs):**
```batch
cd C:\desarrollo\fondos\scripts\launch
P1_P2_Complete.bat
```
Runs sequentially: P1_refreshBenchmarks → P1_discoverAllFunds → P2_discoverLoadMetrics → P2_calculateIndicators.
Aborts on the first step that fails. Log: `proyecto1/log/log_P1_P2_complete_YYYYMMDD_HHMMSS.log`.

**P1 full pipeline:**
```batch
cd C:\desarrollo\fondos\scripts\launch
P1_discoverAllFunds.bat
```
Log: `proyecto1/log/log_pipeline_YYYYMMDD_HHMMSS.log`. Duration: ~8–12 min.

**P1 single block:**
```batch
cd C:\desarrollo\fondos\proyecto1
python run_block.py --block mixtos --db ..\db\fondos.sqlite --master "c:\data\fondos\in\GestoresDeFondosv1.xlsx"
```

**P1 specific ISINs:**
```batch
python run_block.py --block mixtos --db ..\db\fondos.sqlite --master "..." --list-isin LU0232465467,LU1873127366
```

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

**P3 monthly report:**
```batch
scripts\launch\P3_generateReport.bat
```

**P4 sync to Postgres/Superset:**
```batch
scripts\launch\P4_syncToPostgres.bat
# one-time DDL:
psql "postgresql://superset:superset@localhost:5433/fondos" -f db\postgres_analytics_ddl.sql
```

**Tests:**
```batch
# P1 tests (from repo root)
python -m pytest proyecto1/tests/

# P2 tests (from proyecto2/)
cd C:\desarrollo\fondos\proyecto2
python -m pytest tests/
python -m pytest tests/calculations/test_drawdown.py
```

**AST validation (mandatory after any Python edit):**
```bash
python -c "import ast; ast.parse(open('archivo.py').read()); print('AST OK')"
```

**Mark fund for re-download:**
```sql
UPDATE fund_kiid_metadata SET KIID_Status='FORCE_REFRESH' WHERE ISIN='<isin>' AND KIID_Class=1;
```

### Canonical launchers (machine-verified)

<!-- AUTO:BEGIN launchers -->
| Script | Domain |
|--------|--------|
| `AUDIT_statistical.bat` | — |
| `P1_diagCost.bat` | P1 |
| `P1_discoverAllFunds.bat` | P1 |
| `P1_discoverAllFundsPlusCostDiag.bat` | P1 |
| `P1_P2_Complete.bat` | P1 |
| `P1_refreshBenchmarks.bat` | P1 |
| `P2_calculateIndicators.bat` | P2 |
| `P2_discoverLoadMetrics.bat` | P2 |
| `P3_generateReport.bat` | P3 |
| `P4_syncToPostgres.bat` | P4 |
<!-- AUTO:END launchers -->

---

## Operational Tooling — Audit Skills (`.claude/skills/`)

Repo-scoped skills used for diagnostics and backlog maintenance. Invoke by name.

<!-- AUTO:BEGIN skills-table -->
| Skill | Purpose |
|-------|---------|
| `auditStatisticalDataDistributionCostAttributes` | Statistical distribution audit of every cost attribute in `fund_master` and `fund_cost_schedule` — distributions, cross-component equality, shape mome |
| `auditStatisticalDataDistributionP2Metrics` | Statistical distribution audit of every quantitative metric and indicator in `fund_metrics` and `fund_metric_timeseries` — distributions, cross-series |
| `costP1AuditPipelineAndDiagCost` | Diagnostic and auditing workflow for troubleshooting pipeline cost extraction failures and generating code-level fixes. |
| `crossValidateFundAttribute` | Add or audit a dual-signal (fund name + KIID text) cross-validated fund_master attribute, following the discipline established for Asset_Currency/Fund |
| `debugErrorCode` | Four-phase debugging methodology with root cause analysis. Use when investigating bugs, fixing test failures, or troubleshooting unexpected behavior.  |
| `pipelineP1Audit` | Full diagnostic audit of a P1 classification pipeline run — log triage, DQ issue analysis, process-efficiency pass, benchmark-consistency audit (B1–B7 |
| `pipelineP1P2Audit` | Full-cycle diagnostic audit of a P1→P2 pipeline run — classification log triage, DQ analysis, benchmark-consistency (B1–B7), quantitative-metrics reli |
| `pipelineP2Audit` | Deep-dive audit of a P2 quantitative-metrics run (`P2_calculateIndicators.bat`) — log triage, process-efficiency & redundancy analysis, data-reliabili |
<!-- AUTO:END skills-table -->

---

## Maintenance — Dynamic Sync

Mechanical sections are kept in sync by `scripts/audit/sync_agents_md.py`. Hand-written prose
(regime tables, scoring weights, principles, rationale) stays outside sentinel markers and is
never touched by the generator. The generator is **dependency-free** (AST-parses only — no
project imports), so it runs on bare `python3` (CI, developer machines, Linux runners).

**Implemented sentinels (11, all enforced at commit):**

| Sentinel | Target file | Source of truth |
|----------|-------------|----------------|
| `schema-version` | `AGENTS.md` | `SCHEMA_VERSION` in `shared/config.py` |
| `kill-switches-line` | `AGENTS.md` | `*_ENABLED` booleans in `shared/config.py` (source order) |
| `skills-table` | `AGENTS.md` | `.claude/skills/*.md` (alpha order) |
| `p1-module-map` | `AGENTS.md` | `proyecto1/{core,blocks}/*.py` minus non-canonical |
| `p2-module-map` | `AGENTS.md` | `proyecto2/src/**/*.py` + `tests/**/*.py` minus non-canonical |
| `p3-module-map` | `AGENTS.md` | `proyecto3/src/*.py` minus non-canonical |
| `db-tables` | `AGENTS.md` | All `CREATE TABLE` in `db/schema_fondos.sql`, bucketed P1/P2/P3 |
| `launchers` | `AGENTS.md` | `scripts/launch/*.bat` minus non-canonical |
| `macro-factors` | `AGENTS.md` | `_FACTOR_TO_METRIC` dict in `macro_sensitivity.py` |
| `data-sources` | `AGENTS.md` | `SOURCES` registry in `macro_discovery.py` |
| `schema-reference-tables` | `doc/reglas/SCHEMA_REFERENCE.md` | Same DDL roster as `db-tables` |

**Non-canonical exclusion rule** — files matching any of these patterns are excluded from module
maps and the launchers table: dated suffix `_YYYYMMDD`, `_prod` suffix, `_last` suffix,
`_` prefix (private/init), `BackUp`/`Backup` in name, `test_*` outside a proper `tests/`
directory. These files are operational noise; their canonical replacements are listed instead.

**Prose consistency rules (enforced by advisory checks — not blocking but reported at every run):**
- **No hardcoded counts** — never write "five documents" or "24 betas" in prose; counts go stale
  silently. Let the table or the sentinel be the count; prose just says "see table below".
- **No version labels in hand-written tables** — never write "new v24" or "added in v22"; schema
  versions increment and the label becomes misleading. Version history belongs in `git log`.
- Domain-doc table row count is checked against `doc/reglas/*.md` (non-legacy) at every `--check`
  and `--write` run; a mismatch emits an advisory.

**Generator modes:**
```bash
python scripts/audit/sync_agents_md.py            # report mode — show drift
python scripts/audit/sync_agents_md.py --check    # exit 1 on drift (used by pre-commit hook + CI)
python scripts/audit/sync_agents_md.py --write    # update all governed files in-place
```

**Two-tier enforcement:**
- **Local** — `.git/hooks/pre-commit` runs `--check`; any sentinel drift blocks the commit.
  Fix with `--write`, then re-stage the modified file(s).
- **Hosted CI** — `.github/workflows/agents-sync.yml` mirrors `--check` on every push/PR.
  Server-side gate is independent of local hook installation state.

**Hook installer:** `scripts/audit/pre-commit.sh` — portable, resolves Python via Conda env `des`.

---

## Non-Negotiable Design Principles (governance summary)

This is a **summary index**. The canonical, full-text statement of every principle lives in
`doc/reglas/PRINCIPIOS_DISENO.md`; the restrictions in `doc/reglas/RESTRICCIONES_ARQUITECTURA.md`;
the logging normative in `doc/reglas/NORMAS_IMPLEMENTACION.md` §4. **Violation → fix rejected.**

### The 11 principles (canonical: `PRINCIPIOS_DISENO.md`)

1. **COALESCE mandatory** — All SQLite upserts on extracted fields: `COALESCE(excluded.col, col)`. Exception: `SRRI_Visual` (regenerated each cycle).
2. **Root cause only** — No symptomatic patches. No ad-hoc SQL to fix classification data (use the Python module).
3. **Read before modifying** — Always read the production file. Never assume content.
4. **Regime-aware scoring** — Metrics conditioned on macro regime, not global history.
5. **Generic signals only** — KIID text patterns for classification. No hardcoded fund names.
6. **SRRI ≠ classification** — SRRI informs `Profile` only. Never used to derive `Fund_Nature`.
7. **Fix in the correct module** — Classification fix → Python classifier. SQL only for: FORCE_REFRESH triggers, diagnostic SELECTs, one-shot schema migrations.
8. **Linguistic homogeneity** — Each categorical column uses one language only (see `MODELO_SEMANTICO.md` §8). No ES/EN mix in a column.
9. **Semantic consistency** — Cross-attribute INTER rules (`validate_all_semantic_consistency`); inconsistencies are auto-corrected, DQ-flagged, or WARN'd — never silenced (catalog SC-A1..SC-F4 in `MODELO_SEMANTICO.md` §10).
10. **Sentinel vs NULL** — "Indeterminate by nature" uses a categorical sentinel (`MCY` / `Global` / `Broad`), not NULL; NULL means "not discovered".
11. **Scalability & DRY** — No duplicated business logic across modules; when the same need appears in several places, centralize it in a generic module. Pairs with P#2 as the two founding principles; R-1 is its enforced instance.

### Architecture restrictions R-1..R-8 (canonical: `RESTRICCIONES_ARQUITECTURA.md`)

- **R-1** — Normalization maps live only in `classify_utils.py`. No duplicates elsewhere (except `sqlite_writer._normalize_record` as intentional defense-in-depth). *(This is the enforced instance of P#11.)*
- **R-2** — Changing a persisted attribute requires: (1) fix the classifier, (2) fix INTER rules in pipeline, (3) SQL migration or FORCE_REFRESH on affected funds.
- **R-3** — Adding an attribute to `characterize_fund()` → add its column to `_v3_row` SELECT in `pipeline.py` (~line 643) that controls `_needs_char`.
- **R-4** — INTER rules use effective values: `_X_eff = record.get("X") or _X_bd`. Never `record.get("X")` alone (CACHED funds may have None in record).
- **R-5** — `\b` fails between two letters (e.g., `EURHDG`). Use lookaheads for suffix patterns on fund names.
- **R-6** — Text inference: bounded window (~1500 chars) around keyword, not full KIID text.
- **R-7** — Every BL must have tests runnable without `pipeline.py` or `core.io` imports.
- **R-8** — AST validation after every Python edit.

### Logging format (canonical: `NORMAS_IMPLEMENTACION.md` §4)

`[BL-XX] ISIN message` for backlog rules · `[NORM-XXX]` for normalizations · `[ERROR-XXX]` for structural errors.  
Levels: `ERROR` = fund not persisted · `WARNING` = inconsistency corrected · `INFO` = fallback inference · `DEBUG` = internal.

---

## DB Schema Quick Reference

Full reference: `doc/reglas/SCHEMA_REFERENCE.md` · DDL: `db/schema_fondos.sql`

Key `Fund_Nature` values: `Renta Variable` · `Mixtos` · `Renta Fija Flexible` · `Renta Fija Corto Plazo` · `Monetario` · `Alternativo` · `Restantes` · `Estructurado`

### All DB tables (machine-verified)

<!-- AUTO:BEGIN db-tables -->
| Table | Domain |
|-------|--------|
| `fund_master` | P1 |
| `fund_cost_schedule` | P1 |
| `fund_kiid_metadata` | P1 |
| `ingestion_log` | P1 |
| `fund_data_quality_issues` | P1 |
| `fund_families` | P1 |
| `series_macro` | P2 |
| `series_benchmark` | P2 |
| `series_inflation` | P2 |
| `fund_metrics` | P2 |
| `p2_pipeline_log` | P2 |
| `fund_scores` | P3 |
| `portfolio_scenarios` | P3 |
| `portfolio_weights` | P3 |
| `rotation_costs` | P3 |
| `nav_sources` | P2 |
| `fund_benchmarks` | P1 |
| `fund_nav_monthly` | P2 |
| `fund_nav_daily` | P2 |
| `fund_metric_timeseries` | P2 |
| `fund_metric_alerts` | P2 |
| `fund_metric_state` | P2 |
| `fund_cost_corrections` | P1 |
| `audit_statistic` | P1/P2 |
| `audit_finding` | P1/P2 |
<!-- AUTO:END db-tables -->

### Known bugs

| ID | Column | Issue |
|----|--------|-------|
| P13 | `Processing_Time_Ms` | Stores seconds, not milliseconds |
| — | `KIID_Downloaded_At` | 478 funds with NULL (legacy bug, self-healing) |
| — | `ACI_RHP` | Residual NULL on funds whose KID over-time table exposes no bindable RHP horizon (collapsed grid / legacy UCITS "Impact on return (RIY)" layout). Values-path recovery covers the majority; see backlog ACT-06. |
