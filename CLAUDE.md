# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Context

~3,200 European investment funds. Goal: capital preservation relative to IPC+M3 (~6–7% annual, max drawdown 15%, 3–5 year horizon).  
Stack: Python 3.13, SQLite, Windows 10, Conda env `des`.  
DB: `db/fondos.sqlite` (schema v20). Master list: `c:\data\fondos\in\GestoresDeFondosv1.xlsx`.

---

## Architecture

```
P1  Ingestion + classification     → ACTIVE
P2  Quantitative metrics           → ACTIVE
P3  Regime-aware scoring + portfolio → ACTIVE (modules exist, production use evolving)

Flow: P1 → P2 → P3  (unidirectional, no cycles)
```

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

### KIID_Status state machine

| Status | Meaning |
|--------|---------|
| `CACHED` / `OK` | Text in DB → no HTTP, returned from DB in < 1s |
| `FORCE_REFRESH` | Re-download on next cycle |
| `WRONG_DOC` | PDF mismatch; fund excluded from all blocks |
| `NOT_FOUND` | URL unreachable |

HTTP policy: 3 retries (1s/2s/4s backoff), timeout 15s. 429 does NOT retry.  
`scripts/launch/mark_stale.py` marks max 50 funds/cycle as FORCE_REFRESH (age > 180 days).

### Key support modules

- `shared/config.py` — all constants: `DB_PATH`, `SCHEMA_VERSION`, `DOMAIN_VALUES`, `ATTRIBUTE_CATALOG`, kill-switches (`PRIIPS_COST_EXTRACTION_ENABLED`, `DLA2_ARBITRATION_ENABLED`)
- `shared/schema_checks.py` — `assert_schema_alignment()` validates DB columns at startup
- `proyecto1/core/classify_utils.py` — **single source of truth** for all categorical normalization maps (EN→ES for Sector_Focus, Type, Family). Import from here; never duplicate elsewhere.
- `proyecto1/core/cost_arbitration.py` — dual-path cost arbitration (PRIIPs vs UCITS)
- `proyecto1/core/priips_cost_extractor.py` + `ucits_cost_extractor.py` — cost extraction

---

## P2 — Quantitative Metrics Pipeline

### Module map

```
proyecto2/
  src/
    pipeline/run_pipeline.py     ← entry point
    readers/db_readers.py        ← load_nav(), load_ipc(), get_isins_with_nav()
    discovery/
      nav_discovery.py           ← Morningstar (mstarpy) NAV download
      macro_discovery.py         ← public APIs: INE, BCE SDW, Fed FRED, Eurostat
    calculations/
      risk_metrics.py            ← return_ann, vol_ann, sharpe, max_drawdown, SRRI
      returns.py                 ← log returns, annualized
      drawdown.py                ← max drawdown, recovery time
      consistency.py             ← real vs nominal consistency check
      macro_sensitivity.py       ← OLS regression vs 24 macro factors (min 60 obs)
      regime_returns.py          ← return/sharpe/vol per macro regime (min 12 obs/regime)
      momentum.py                ← rolling momentum ranks
      capture_ratios.py          ← upside/downside capture vs benchmark
      persistence.py             ← alpha persistence metric
      currency_factor.py         ← FX contribution to return
      deflation.py               ← nominal → real return conversion
      m2_global_builder.py       ← builds M2 Global YoY series
    writers/metrics_writer.py    ← writes to fund_metrics table
    utils/
      validators.py              ← validate_nav(), validate_ipc()
      time_windows.py            ← slice_window()
      logger.py
  tests/
    calculations/                ← test_drawdown.py, test_consistency.py
    discovery/                   ← test_eurostat.py, test_fred_es.py, test_historia.py
```

### DB tables used by P2

| Table | Key | Contents |
|-------|-----|----------|
| `fund_nav_monthly` | `(ISIN, Date)` | Monthly NAV series (source: Morningstar) |
| `nav_sources` | `ISIN` | Morningstar ms_id + date range + status |
| `series_macro` | `(date, indicator, geography)` | All macro time series |
| `fund_metrics` | `(ISIN, metric, horizon, real_flag)` | All calculated metrics |
| `p2_pipeline_log` | `id` | Per-run traceability |

### Metric horizons

`since_inception` (always), `crisis_windows` (per `CRISIS_WINDOWS` in config), rolling windows (per `ROLLING_WINDOWS` in config).  
`real_flag=0` → nominal; `real_flag=1` → deflated by IPC.

### Macro factors (OLS model, 24 betas)

`beta_rate_eu`, `beta_m3_yoy`, `beta_ipc_{es,eu,us,jp,cn}`, `beta_rate_{us,jp,cn}`, `beta_oil`, `beta_copper`, `beta_cli_{eu,us}`, `beta_dxy`, `beta_gold`, `beta_m2_global`, `beta_spread_{hy,ig}`, `beta_vix`, `beta_term_spread`, `beta_eur_{jpy,gbp,cny}`.  
VIF filter applied; factors with VIF > 10 excluded.

### Data discovery (run before pipeline)

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

```
proyecto3/src/
  regime_classifier.py    ← RegimeClassifier: classify_current() / classify_historical()
  fund_scorer.py          ← 3-layer score: hard filters → base score → regime multipliers
  portfolio_builder.py    ← PortfolioBuilder: combines 3 sub-portfolios
  backtesting.py          ← Backtester: simplified (look-ahead bias acknowledged)
  monthly_report.py       ← generate_report() → Excel with 5 sheets
```

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

## Commands

All commands: activate Conda env `des` first.

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

**P2 full pipeline:**
```batch
cd C:\desarrollo\fondos
python -X utf8 -m proyecto2.src.pipeline.run_pipeline --source eurostat
```
Or: `scripts/launch/P2_calculateIndicators.bat`

**P2 single ISIN (debug):**
```batch
python -X utf8 -m proyecto2.src.pipeline.run_pipeline --isin LU1234567890 --dry-run
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

---

## Non-Negotiable Design Principles

Full details: `doc/reglas/PRINCIPIOS_DISENO.md` · `doc/reglas/RESTRICCIONES_ARQUITECTURA.md`

### The 7 principles

1. **COALESCE mandatory** — All SQLite upserts on extracted fields: `COALESCE(excluded.col, col)`. Exception: `SRRI_Visual` (regenerated each cycle).
2. **Root cause only** — No symptomatic patches. No ad-hoc SQL to fix classification data (use the Python module).
3. **Read before modifying** — Always read the production file. Never assume content.
4. **Regime-aware scoring** — Metrics conditioned on macro regime, not global history.
5. **Generic signals only** — KIID text patterns for classification. No hardcoded fund names.
6. **SRRI ≠ classification** — SRRI informs `Profile` only. Never used to derive `Fund_Nature`.
7. **Fix in the correct module** — Classification fix → Python classifier. SQL only for: FORCE_REFRESH triggers, diagnostic SELECTs, one-shot schema migrations.

### Architecture restrictions (R-1 through R-8)

- **R-1** — Normalization maps live only in `classify_utils.py`. No duplicates elsewhere (except `sqlite_writer._normalize_record` as intentional defense-in-depth).
- **R-2** — Changing a persisted attribute requires: (1) fix the classifier, (2) fix INTER rules in pipeline, (3) SQL migration or FORCE_REFRESH on affected funds.
- **R-3** — Adding an attribute to `characterize_fund()` → add its column to `_v3_row` SELECT in `pipeline.py` (~line 643) that controls `_needs_char`.
- **R-4** — INTER rules use effective values: `_X_eff = record.get("X") or _X_bd`. Never `record.get("X")` alone (CACHED funds may have None in record).
- **R-5** — `\b` fails between two letters (e.g., `EURHDG`). Use lookaheads for suffix patterns on fund names.
- **R-6** — Text inference: bounded window (~1500 chars) around keyword, not full KIID text.
- **R-7** — Every BL must have tests runnable without `pipeline.py` or `core.io` imports.
- **R-8** — AST validation after every Python edit.

### Logging format

`[BL-XX] ISIN message` for backlog rules · `[NORM-XXX]` for normalizations · `[ERROR-XXX]` for structural errors.  
Levels: `ERROR` = fund not persisted · `WARNING` = inconsistency corrected · `INFO` = fallback inference · `DEBUG` = internal.

---

## DB Schema Quick Reference

Full reference: `doc/reglas/SCHEMA_REFERENCE.md` · DDL: `db/schema_fondos.sql`

### P1 tables

| Table | Key | Columns |
|-------|-----|---------|
| `fund_master` | `ISIN` | 42 cols: identity, classification, SRRI, costs, family FK |
| `fund_kiid_metadata` | `(ISIN, KIID_Class)` | KIID URL/text/status, SRRI_Visual/Textual/Validation_Status |
| `fund_families` | `family_id` | family_name, Fund_Nature, n_funds |
| `ingestion_log` | `id` | step, status (ERROR/WARNING/INFO), message |

Key `Fund_Nature` values: `Renta Variable` · `Mixtos` · `Renta Fija Flexible` · `Renta Fija Corto Plazo` · `Monetario` · `Alternativo` · `Restantes` · `Estructurado`

### Known bugs

| ID | Column | Issue |
|----|--------|-------|
| P13 | `Processing_Time_Ms` | Stores seconds, not milliseconds |
| — | `KIID_Downloaded_At` | 478 funds with NULL (legacy bug, self-healing) |
