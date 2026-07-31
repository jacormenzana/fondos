# AGENTS.md — Single Source of Truth (índice + orientación)

**This is the authoritative entry point for any agent (human or LLM) working in this repository.**
Read this file first. It carries the full project orientation, and it is the **index** to the five
canonical specification documents in `doc/reglas/`. Those five documents are canonical for their
respective domains; this file must not duplicate their content — it points to them.

| Domain doc (`doc/reglas/`) | Answers | Canonical for |
|---|---|---|
| `PRINCIPIOS_DISENO.md` | *why* | The 11 non-negotiable design principles (P#1–P#11) |
| `RESTRICCIONES_ARQUITECTURA.md` | *what not to do* | Code restrictions R-1..R-8 + pre-commit checklist |
| `NORMAS_IMPLEMENTACION.md` | *how to build/test/log* | Runtime standards, DQ `check_code`, logging v2, regressions |
| `MODELO_SEMANTICO.md` | *what values mean* | Attribute semantics + SC-A1..SC-F4 consistency rules |
| `SCHEMA_REFERENCE.md` | *where data lives* | Full DB table/column reference (P1/P2/P3) |

| Backlog registers (`doc/backlog/`) | Answers | Canonical for |
|---|---|---|
| `P1/EXECUTIVE_SUMMARY_pending_actions_20260716.md` | *P1 open items* | P1 classification pending actions (OPT-B, RFC-RFF, benchmark, SC-H2…) |
| `P2/EXECUTIVE_SUMMARY_rolling_indicators_20260727.md` | *P2/P3/P4 open items* | Rolling-indicators + BI backlog (ROLL-P0..P5) |
| **Live HTML artifact** | *consolidated master view* | All-domain incident backlog (32 items, always-current): `https://claude.ai/code/artifact/24eb50ad-1274-4149-bae6-6cda184f1a0e` |

**Workflow before touching code:** read this file → read the relevant domain doc(s) above →
verify your change against `RESTRICCIONES_ARQUITECTURA.md` (R-1..R-8) and the pre-commit checklist.
If a request conflicts with a principle or restriction, stop and report.

---

## Context

~3,200 European investment funds. Goal: capital preservation relative to IPC+M3 (~6–7% annual, max drawdown 15%, 3–5 year horizon).  
Stack: Python 3.13, SQLite, Windows 10, Conda env `des`.  
<!-- AUTO:BEGIN schema-version -->
DB: `db/fondos.sqlite` (schema v24). Master list: `c:\data\fondos\in\GestoresDeFondosv1.xlsx`.
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

<!-- AUTO:BEGIN kill-switches-line -->
- `shared/config.py` — all constants: `DB_PATH`, `SCHEMA_VERSION` (`"v24"`), `DOMAIN_VALUES`, `ATTRIBUTE_CATALOG`, kill-switches (`PRIIPS_COST_EXTRACTION_ENABLED`, `SHORT_HORIZON_SCORING_ENABLED`, `ROLLING_STATS_ENABLED`, `BENCHMARK_DECOMP_ENABLED`, `BENCHMARK_ROLE_ENABLED`, `INTER18_RECONCILIATION_ENABLED`, `DLA2_ARBITRATION_ENABLED`)
<!-- AUTO:END kill-switches-line -->
- `shared/schema_checks.py` — `assert_schema_alignment()` validates DB columns at startup
- `proyecto1/core/classify_utils.py` — **single source of truth** for all categorical normalization maps (EN→ES for Sector_Focus, Type, Family). Import from here; never duplicate elsewhere (P#11 / R-1).
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
      rolling_stats.py           ← rolling engine (roll_vol_ann/max_dd/return_ann) → fund_metric_timeseries [ROLLING_STATS_ENABLED]
      short_horizon.py           ← daily short-horizon metrics on fund_nav_daily (metric_version='d1'; Getmansky AC(1) illiquidity)
      srri.py                    ← SRRI calculation
    writers/metrics_writer.py    ← writes to fund_metrics table
    analysis/export_metrics.py   ← Excel export of P2 metrics (per-block sheets); orchestrated by P2_calculateIndicators.bat after pipeline RC=0, not run standalone
    reports/rolling_dashboard.py ← self-contained HTML dashboard (Chart.js) from fund_metric_timeseries + fund_metric_alerts
    utils/
      validators.py              ← validate_nav(), validate_ipc()
      time_windows.py            ← slice_window()
      fingerprint.py             ← compute_input_hash() SHA-1 idempotency (keys on CALC_VERSION + METRIC_VERSION)
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
| `fund_nav_daily` | `(ISIN, Date)` | Daily NAV series — short-horizon source (**new v24**) |
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
`"20260730"`) to force a full recompute** of all ISINs (e.g. after changing calculation logic).

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

---

## Operational Tooling — Audit Skills (`.claude/skills/`)

Repo-scoped skills used for diagnostics and backlog maintenance. Invoke by name.

<!-- AUTO:BEGIN skills-table -->
| Skill | Purpose |
|-------|---------|
| `costP1AuditPipelineAndDiagCost` | Diagnostic and auditing workflow for troubleshooting pipeline cost extraction failures and generating code-level fixes. |
| `crossValidateFundAttribute` | Add or audit a dual-signal (fund name + KIID text) cross-validated fund_master attribute, following the discipline established for Asset_Currency/Fund |
| `debugErrorCode` | Four-phase debugging methodology with root cause analysis. Use when investigating bugs, fixing test failures, or troubleshooting unexpected behavior.  |
| `pipelineP1Audit` | Full diagnostic audit of a P1 classification pipeline run — log triage, DQ issue analysis, benchmark-consistency audit, root-cause fixes, and regressi |
| `pipelineP2Audit` | Deep-dive audit of a P2 quantitative-metrics run (`P2_calculateIndicators.bat`) — process-efficiency & redundancy audit, data-reliability assessment,  |
<!-- AUTO:END skills-table -->

---

## Maintenance — Dynamic AGENTS.md Sync (proposed)

To prevent this file from drifting from the codebase again (P#11 / generate-from-code, don't hand-maintain):

- **Sentinel blocks** — wrap mechanical sections (module maps, kill-switches, DB-table lists, batch launchers,
  skills list, `SCHEMA_VERSION`) in `<!-- AUTO:BEGIN <section> -->` … `<!-- AUTO:END -->`. Hand-written prose
  (principles, rationale) stays outside markers and is never touched.
- **Generator** `scripts/audit/sync_agents_md.py` — read-only introspection: `SCHEMA_VERSION` + kill-switches via
  `ast` over `shared/config.py`; module trees via `glob`; DB tables via regex over `db/schema_fondos.sql`;
  launchers via `scripts/launch/*.bat`; skills via `.claude/skills/*.md`. Re-renders only the AUTO blocks.
  Modes: `--check` (diff, exit 1 on drift) · `--write`.
- **Enforcement** — a `pre-commit` hook (and mirrored CI job) runs `--check`; drift blocks the commit.

*Status: built. Script: `scripts/audit/sync_agents_md.py`. Pre-commit hook installed at `.git/hooks/pre-commit`.*

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

### P1 tables

| Table | Key | Columns |
|-------|-----|---------|
| `fund_master` | `ISIN` | 42 cols: identity, classification, SRRI, costs, family FK |
| `fund_kiid_metadata` | `(ISIN, KIID_Class)` | KIID URL/text/status, SRRI_Visual/Textual/Validation_Status |
| `fund_families` | `family_id` | family_name, Fund_Nature, n_funds |
| `ingestion_log` | `id` | step, status (ERROR/WARNING/INFO), message — append-only history, every cycle |
| `fund_data_quality_issues` | `(ISIN, check_code)` | level (OK/INFERRED/WARN/MISSING), message, detected_at — **current** issues only, rebuilt each cycle |

Key `Fund_Nature` values: `Renta Variable` · `Mixtos` · `Renta Fija Flexible` · `Renta Fija Corto Plazo` · `Monetario` · `Alternativo` · `Restantes` · `Estructurado`

### Known bugs

| ID | Column | Issue |
|----|--------|-------|
| P13 | `Processing_Time_Ms` | Stores seconds, not milliseconds |
| — | `KIID_Downloaded_At` | 478 funds with NULL (legacy bug, self-healing) |
