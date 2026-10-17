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
| `NORMAS_BATCH.md` | *how to write a `.bat` launcher* | The single criterion for every `.bat`: skeleton, no hard-coded paths/versions, own exit codes 100-199, single instance, standby/codepage restore, `cmd.exe` pitfalls; reference implementation `scripts/launch/lib/common.bat` + `P1_P2_Complete.bat`, template `_template.bat` |
| `NORMAS_BACKLOG.md` | *how action points are recorded and related* | `gestion.backlog` domains and state machine, the relationship vocabulary (DEPENDS_ON / OVERLAPS / ...), the create/start/close procedure, the Scope Absorption Protocol, link discovery and review (`scripts/audit/backlog_links.py`), the automatic-incident overlap policy and its calibration gate |
| `GOBIERNO_CONTROLES.md` | *which controls gate a deployment or a promotion* | The two-pillar control catalogue (`T-*` technical, `D-*` data, `G-*` orchestration: owner, criterion, consequence), the PASS / PASS_WITH_WARNINGS / FAIL / INCOMPLETE states, the CODE_DEPLOY vs PORTFOLIO_PROMOTION decision matrix and the precision policy; machine mirror `shared/governance/controls.py` |

| Backlog registers | Answers | Canonical for |
|---|---|---|
| **Postgres `gestion.backlog`** (§ below) | *consolidated master view* | All-domain action-points backlog — live system of record since 2026-09-21, superseding the HTML artifact below |
| ~~Live HTML artifact~~ (superseded 2026-09-21) | *historical snapshot only* | Frozen at v26 (2026-09-19); its 54 items were migrated to `gestion.backlog` (`FND-0001`..`FND-0054`) — do not add new items here: `https://claude.ai/code/artifact/24eb50ad-1274-4149-bae6-6cda184f1a0e` |

**Workflow before touching code:** read this file → read the relevant domain doc(s) above →
verify your change against `RESTRICCIONES_ARQUITECTURA.md` (R-1..R-8) and the pre-commit checklist.
Creating or editing a `.bat`: read `NORMAS_BATCH.md` first and start from `scripts/launch/_template.bat`.
Opening, starting or closing a backlog ticket: read `NORMAS_BACKLOG.md` (check related tickets first).
Continuing from a summary, a stored plan or a long idle -- and before any commit, push, live write, launcher or ticket -- run the
read-only pre-flight first (`python scripts/audit/preflight_check.py --mine <your files>`, skill `preflightCheck`): several sessions share
this repo, and another one's uncommitted files or long jobs are never yours to touch.
If a request conflicts with a principle or restriction, stop and report.

---

## Gestión de Puntos de Acción (Action Points Backlog en PostgreSQL)

**System of record since 2026-09-21.** Database `gestion` (schema `backlog`) on the same Postgres
server used for the P1/P2/P3 operational migration (`db/pg/`, `docker/`) — a **separate**
database on that server, not a schema inside `fondos`. Connection: `FONDOS_BACKLOG_PG_DSN` (its
own env var — deliberately distinct from `FONDOS_PG_DSN`, which points at the `fondos` migration
target). Tables: `backlog.BACKLOG` (one row per action point), `backlog.BACKLOG_LOGS` (append-only
history, FK `ON DELETE CASCADE`) and `backlog.backlog_link_event` (append-only relationships between tickets).
**The categorical domains, the state machine, the creation/transition/closure rules, the automatic incident
capture, the relationship vocabulary and the review procedure are canonical in `doc/reglas/NORMAS_BACKLOG.md`** —
this section is the summary.

Every finding, error, or improvement identified during a session must be registered in `BACKLOG`.

- **Never invent a domain value** (`nature`, `object_type`, `priority`, `status`, `solution_type`): all five are
  `CHECK`-enforced; the lists are in `NORMAS_BACKLOG.md` §2. Ids are minted from `backlog.seq_backlog_fnd`, never by hand.
- **Never overwrite progress:** every advance is an `INSERT` into `BACKLOG_LOGS`; closing needs `closure_ts` +
  `solution_type`, a production deploy also `deployment_ts` + `release_version`.
- **Check relationships before acting:** `python scripts/audit/backlog_links.py --new "<text>"` before opening a ticket,
  `--for FND-nnnn` before starting one (exit 1 while a confirmed `DEPENDS_ON` prerequisite is open); write the
  affects / affected-by / absorbs note in the first log and apply the Scope Absorption Protocol (`NORMAS_BACKLOG.md` §4-§5).
- **Links are events decided by a person**, never edited: discovery proposes (`--review-out`), the review file decides
  (`--apply-review`); the DDL (`db/pg_backlog/20_links.sql`) is applied by the owner (`scripts/ops/migrate_backlog_links.py --apply`).
- **Automatic capture** (`shared/backlog_client.report_incident()` / `capture_exceptions()` / `install_excepthook()`) is a
  no-op while `FONDOS_BACKLOG_PG_DSN` is unset and never masks the original exception; its overlap policy
  (`BACKLOG_INCIDENT_AUTO_APPEND_ENABLED`, off) is gated by `scripts/audit/backlog_links_calibration.py --gate`.
- **Analytics:** `backlog.vw_backlog_kpis`; release closure `backlog.sp_close_release(p_release_version)`.

---

## Context

~3,200 European investment funds. Goal: capital preservation relative to IPC+M3 (~6–7% annual, max drawdown 15%, 3–5 year horizon).  
Stack: Python 3.13, PostgreSQL 17 (live operational store), Windows 10, Conda env `des`.  
<!-- AUTO:BEGIN schema-version -->
DB: PostgreSQL 17 (schema v27; legacy SQLite retired 2026-09-26). Master list: `c:\data\fondos\in\GestoresDeFondosv1.xlsx`.
<!-- AUTO:END schema-version -->

**Live store = PostgreSQL 17 (cutover executed 2026-09-23); SQLite is retired (2026-09-26, FND-0102).**
There is no dual-backend code any more: `shared/db.py::get_connection()` returns a psycopg3 connection
and nothing in production may import `sqlite3` (enforced by `tests/test_dialect_coverage.py`). The sealed
`db/fondos.sqlite` (read-only since the cutover) is only awaiting archive on/after 2026-10-03
(`scripts/ops/archive_sqlite.py`, FND-0103) — never write to it and never read it as current data. The
repo `.env` (git-ignored) sets `FONDOS_PG_DSN`; every process prints `[DB] backend=postgres ...`.

| Item | Where |
|------|-------|
| Live server | Docker container `fondos_postgres` (PG17) in WSL2 Ubuntu, `127.0.0.1:5436`, data under `/opt/docker/db/postgresql17/` — compose: `docker/docker-compose.yml` + `docker/docker-compose.wsl.override.yml` |
| Schemas | `bronze` / `silver` / `gold` / `control` (DDL: `db/pg/`, column renames: `db/pg/rename_map.yaml`) |
| Pipeline role | `FONDOS_PG_DSN` → `fondos_app` (DML only). Runtime code must **never** issue DDL |
| Owner role | `FONDOS_PG_DSN_OWNER` → `fondos_owner` (seeds, migrations, DDL; swap dbname to `gestion` for the backlog) |
| Read-only roles | `fondos_ro` (analyst), `superset_ro` (BI: silver+gold) |
| Dev/test server | `pg-server` (PG15, `:5432`) — disposable; never share a server between tests and live data |
| Ops scripts | `scripts/ops/` — `backup_live_pg.sh`, `setup_wal_archive.sh`, `create_readonly_roles.sh`, `apply_benchmark_ms_checks.py`, `run_pg_tests.py` |

Postgres tests: `python scripts/ops/run_pg_tests.py` (hermetic throwaway container, no credentials).
Dialect rules learned in the migration (psycopg3 `with conn:` closes the connection, lowercase
column folding, `date` objects) are enforced by `tests/test_dialect_coverage.py` and
`tests/test_sql_explain_sweep_pg.py`; see `doc/reglas/NORMAS_IMPLEMENTACION.md` §7 before adding SQL.
Recovery state (WAL archiving / PITR, off-box copy) is tracked in `gestion.backlog` (FND-0070,
FND-0071) — check it before any operation that assumes a restore point exists.
See `doc/reglas/P4_BI_CHARTER.md` §0 for the migration design.

---

## Architecture

```
P1  Ingestion + classification     → ACTIVE
P2  Quantitative metrics           → ACTIVE
P3  Regime-aware scoring + portfolio → ACTIVE (modules exist, production use evolving)
P4  Analytics/BI (read-only roles on the live Postgres; legacy SQLite mirror retired) → ACTIVE

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
| 6 | `proyecto1/core/fund_writer.py` | Idempotent UPSERT with COALESCE |
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
> logic + the block's `classify_fund()` output. `resolve_nature_evidence` is KIID-primary (calibrated
> 2026-07-16; symmetric voting measured worse), with one bounded exception (ARB-1, FND-0288): a KIID
> vote that **no** other signal backs yields to two independent signals (name / KIID benchmark /
> Morningstar `asset_class`) that agree on another nature, unless the realized volatility fits that
> nature worse. Explicit-rule natures (Estructurado, Alternativo, Monetario) and the RF Corto/Flexible
> split are never arbitrated. Any change to a nature rule: measure old-vs-new over the whole universe first.

### KIID_Status state machine

| Status | Meaning |
|--------|---------|
| `CACHED` / `OK` | Text in DB → no HTTP, returned from DB in < 1s |
| `FORCE_REFRESH` | Re-download on next cycle |
| `WRONG_DOC` | PDF mismatch; fund excluded from all blocks |
| `NOT_FOUND` | URL unreachable |
| `RETIRED` | KIID row left the Deutsche Bank catalogue; set by `p1_kiid_sync --retire-orphans` together with `In_Current_Universe=0`; reversed by `--sync` (→ `FORCE_REFRESH`) if the row returns |

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

**Launcher:** `scripts\launch\P1_harvestFunds.bat` runs this flow (steps 1–3 + a shrink gate
`harvest_gate.py` between the report and any download/archive; `--sync`, `--retire-orphans`, `--full`
opt in to the mutating phases — default is read-only on the PDFs). Audits: `AUDIT_P1.bat`, `AUDIT_P2.bat`.

**Storage:**
Active: `C:\data\fondos\kiid\{ISIN}.pdf` · Retired: `C:\data\fondos\kiid_retired\YYYYMMDD\{ISIN}.pdf`
PDFs and `Raw_KIID_Text` (in `fund_kiid_metadata`) are **never deleted**.

`kiid_lifecycle` table — one row per lifecycle period per ISIN:
- `status = 'commercializing'` (active) or `'retired'`; `retire_dir` = YYYYMMDD archive subdirectory
- `retire_scope` (on retired periods): `FULL` = the ISIN has no document at all in the latest harvest;
  `PARTIAL` = other documents remain in the catalogue but the KIID row is gone. Both cases also get
  `In_Current_Universe=0` and `KIID_Status='RETIRED'`; `--master-db` loads only ISINs with a KIID row.

**"Orphan" (precise):** a local PDF in `kiid/` absent from the latest harvest's `db_document_catalogue` — fund was removed from the live XML catalogue. Not a fund that was "never registered".

**Retirement cause:** fund disappears from Deutsche Bank's `catalogo.xml` → absent from latest `harvest_ts` in `db_document_catalogue` → Phase 3 report flags it → `--retire-orphans` archives it.

**Pipeline impact of retired ISINs:** `--master-db` / `--nature-first` load universe from `db_document_catalogue` MAX `harvest_ts` — retired ISINs have 0 rows there and **cannot be reached by the classifier**. Direct SQL is the correct mechanism for `fund_master` attribute updates on retired ISINs (NOT a P#7 violation — P#7 covers active, classifier-reachable funds only).

**Diagnosing a fund absent from the pipeline:** query `kiid_lifecycle WHERE isin=?` first.
- `status='retired'` → retired KIID; `In_Current_Universe=0`; update via SQL.
- No rows → check `db_document_catalogue`; may never have been in the XML catalogue.

### Key support modules

<!-- AUTO:BEGIN kill-switches-line -->
- `shared/config.py` — all constants: `SCHEMA_VERSION` (`"v27"`), `DOMAIN_VALUES`, `ATTRIBUTE_CATALOG`, kill-switches (`PRIIPS_COST_EXTRACTION_ENABLED`, `SHORT_HORIZON_SCORING_ENABLED`, `ROLLING_STATS_ENABLED`, `ROLLING_PCTILE_P3_ENABLED`, `PORTFOLIO_HYSTERESIS_ENABLED`, `ROTATION_COST_GATE_ENABLED`, `BENCHMARK_DECOMP_ENABLED`, `BENCHMARK_ROLE_ENABLED`, `INTER18_RECONCILIATION_ENABLED`, `DLA2_ARBITRATION_ENABLED`, `FX_EXPOSURE_CONSTRAINT_ENABLED`, `FUND_DATA_ELIGIBILITY_ENABLED`, `ALARM_WATCH_GATE_ENABLED`, `AUDIT_COMPLETENESS_CONTRACT_ENABLED`, `AUDIT_EXCEPTION_EVENTS_ENABLED`, `GOVERNANCE_ORCHESTRATOR_ENABLED`, `AUDIT_INCIDENT_LOOP_ENABLED`, `CODE_INTEGRITY_GATE_ENABLED`, `COST_DOMAIN_GATE_ENABLED`, `MACRO_VIF_ITERATIVE_ENABLED`, `MACRO_FACTOR_CLEAN_ENABLED`, `PERSISTENCE_FIRST_LAST_NAV_ENABLED`, `CAPTURE_MONTH_END_ENABLED`, `ANNUALIZATION_INTERVAL_ENABLED`, `ANNUALIZATION_BY_DATES_ENABLED`, `DEFLATION_MONTH_ALIGN_ENABLED`, `SORTINO_MIN_DOWNSIDE_COUNT_ENABLED`, `FX_CONTRIBUTION_EUR_VIEW_ENABLED`, `EUR_NAV_CONVERSION_ENABLED`, `FAMILY_VERSIONING_ENABLED`, `BACKLOG_INCIDENT_AUTO_APPEND_ENABLED`)
<!-- AUTO:END kill-switches-line -->
- `shared/schema_checks.py` — `assert_schema_alignment()` validates DB columns at startup
- `proyecto1/core/classify_utils.py` — **single source of truth** for all categorical normalization maps (EN→ES for Sector_Focus, Type, Family). Import from here; never duplicate elsewhere (P#11 / R-1).
- `proyecto1/core/cost_arbitration.py` — dual-path cost arbitration (PRIIPs vs UCITS)
- `proyecto1/core/priips_cost_extractor.py` + `ucits_cost_extractor.py` — cost extraction

### All P1 modules (machine-verified)

<!-- AUTO:BEGIN p1-module-map -->
| Module | Folder |
|--------|--------|
| `PATCHES_pipeline.py` | `proyecto1/core` |
| `benchmark_normalizer.py` | `proyecto1/core` |
| `classify_utils.py` | `proyecto1/core` |
| `cost_arbitration.py` | `proyecto1/core` |
| `cost_cross_validator.py` | `proyecto1/core` |
| `cost_format_router.py` | `proyecto1/core` |
| `cost_format_signals.py` | `proyecto1/core` |
| `cost_pct_anchored.py` | `proyecto1/core` |
| `cost_scale.py` | `proyecto1/core` |
| `cost_table_parser.py` | `proyecto1/core` |
| `cost_totals_arbitration.py` | `proyecto1/core` |
| `dla2_dual_strategy_compare.py` | `proyecto1/core` |
| `dla2_ocr_fallback.py` | `proyecto1/core` |
| `dla2_xband_prototype.py` | `proyecto1/core` |
| `dla_extractor.py` | `proyecto1/core` |
| `dla_table_serializer.py` | `proyecto1/core` |
| `fund_characterizer.py` | `proyecto1/core` |
| `fund_family_builder.py` | `proyecto1/core` |
| `fund_writer.py` | `proyecto1/core` |
| `io.py` | `proyecto1/core` |
| `kiid_parser.py` | `proyecto1/core` |
| `mark_stale.py` | `proyecto1/core` |
| `oc_resolution.py` | `proyecto1/core` |
| `pipeline.py` | `proyecto1/core` |
| `priips_cost_extractor.py` | `proyecto1/core` |
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
      test_p2_bundle_flags.py
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
      test_nav_discovery_cli_isin_split_20260927.py
      test_nav_discovery_date_types_pg.py
      test_nav_discovery_freeze_20260915.py
      test_nav_discovery_splice_20260915.py
      test_nav_discovery_writers_pg.py
      test_nav_monthly_write_20260913.py
      test_nav_source_unavailable_rc.py
      test_source_chain_fnd0256.py
    pipeline/
      test_family_versioning_fnd0236_pg.py
      test_family_versioning_wiring_fnd0236.py
      test_metric_state_scoped_run_fnd0144.py
      test_min_nav_rows_clear_fnd0168.py
      test_nav_data_freshness_delta_20260927.py
      test_refresh_matviews_and_update_isin.py
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

---

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

**Layer 3 – Structural multipliers:** fx_contribution_pct > 0.60 → ×0.80; alpha_persistence > 0.60 → ×1.15 (plus the empirical regime-return and crisis-stress multipliers). The macro multipliers (beta_oil, beta_rate_eu, macro_r2, crisis beta_spread_hy / beta_vix) were **retired 2026-10-04** (FND-0225): the oil / VIX / HY legs never fired in production (threshold scale and sign), and the full-universe PIT backtests showed no robust benefit beyond one episode (2008-09). The scorer no longer reads the macro betas; P2 still computes them. Do not reintroduce without out-of-sample validation (FND-0193).

### Portfolio construction constraints

Max 10 funds/sub-portfolio · max 20% per fund · max 2 funds per manager (a count, global across sub-portfolios — there is no weight cap per manager) · min 3% per fund · max 5 same Fund_Nature per sub-portfolio.  
Weight method: `score_proportional`. Sub-portfolio and master weights sum to exactly 1.0000 (largest-remainder rounding, never a weight past
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
output directory, never next to the official report. The cycle launchers (`P2_P3_complete.bat`,
`P1_P2_P3.bat`) report the DRAFT they just built with `--draft`; the official report is the owner's `P3_generateReport.bat` after promotion. Cover sheet
shows the scenario and its currency exposure. The limit is also a **construction constraint** (`FX_EXPOSURE_CONSTRAINT_ENABLED`,
`portfolio_engine.select_within_exposure`): each sub-portfolio drops its lowest-scored exposed fund and re-selects until its exposed weight is within
the budget, so a DRAFT is promotable by construction (a residue the non-exposed pool cannot absorb is held as cash). It is the safety net under the
regime-evidence FX neutralisation, which stays dormant until its PIT validation. The legacy backtester and the PIT backtest build under the same
constraint (`score_candidates.exposure_constrained`; flags in `PitInputs.fx_exposed`, loaded by `pit_inputs.load_fx_exposed`, deliberately not a column
of `attrs`, which feeds the PIT score cache keys), so a PIT run evaluates the model production runs.

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

---

## P4 — Analytics / BI Sync

**Two things live under "P4".** See `doc/reglas/P4_BI_CHARTER.md`
§0 for the full picture; summary here:

1. **BI on the live store** — Metabase/Superset read the live Postgres through the read-only roles
   (`fondos_ro`, `superset_ro`) and the `gold.mv_*` matviews, refreshed at the end of each P2 run. The
   old SQLite→Postgres BI mirror (`P4_syncToPostgres.bat`, `shared/load_fondos_to_postgres.py`) was
   deleted with the SQLite retirement.
2. **The operational migration (DEPLOYED 2026-09-23)** — the complete P1+P2+P3 database now lives
   in PostgreSQL 17 (live server: see the Context table above). Artifacts: `docker/docker-compose.yml`,
   `docker/postgresql.conf`, `db/pg/00_roles_schemas.sql` … `40_matviews.sql`,
   `db/pg/rename_map.yaml`, `scripts/mig/pg_seed.py` (one-shot on an EMPTY database),
   `scripts/mig/pg_reconcile.py`.

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

**Resume after a failure:** `P1_P2_Complete.bat --from N` re-runs step N in full and the steps after it.
Safe because every write in the four steps is idempotent (enforced by `tests/test_sql_explain_sweep_pg.py`);
allowed only when the last run failed at step N or later (state in `proyecto1/log/P1_P2_Complete.state`,
logic in `scripts/launch/p1p2_state.py`); `--from-any` overrides the guard and is recorded in `ingestion_log`.
The launcher also runs the statistical audits (`p2`, `costs`) with `--persist` before PASO 1 and again with
`--compare-to` after a successful run, writing the drift report to `proyecto1/log/log_P1_P2_audit_<STAMP>.log`. (There is
no repair step after PASO 2 any more: `Ongoing_Charge_Recurrent` has a single writer, `cost_scale.resolve_ongoing_charge`, FND-0034.)
The benchmark loader exits 3 (stopping PASO 1) when more than a tenth of its Morningstar calls fail or 10 fail in a row.

**Cycle phases around the four steps** (steps keep their numbers; `P1_P2_Complete.bat --help` lists every option):
a preflight (`p1p2_state.py preflight`: DB reachable, owner DSN, `FRED_API_KEY`, disk; a dead DB aborts with RC 104
without touching the resume state), then the baseline audit and a macro-beta snapshot, then the harvest
(`P1_harvestFunds.bat`, read-only on PDFs unless `--harvest-sync` / `--harvest-retire`; a failed harvest counts as
step 1). After a successful cycle, non-blocking diagnostics: final audit with drift, beta comparison
(`scripts/audit/beta_shift_audit.py`, run with the current `CALC_VERSION`), the P3 freshness verdict
(`scripts/launch/p3_freshness_check.py`, same gate as P3) and the cycle report (`scripts/launch/p1p2_cycle_report.py`,
`[ATENCION]` lines, JSON in `proyecto1/log/`). Step options are forwarded to the sub-launchers (`--no-export`,
`--skip-macro`, `--workers N`, `--force`, `--benchmarks-load`, `-- <run_pipeline args>`); optional diagnostics:
`--shadow N`, `--benchmark-gaps`, `--dashboard`, `--diag-cost`. The diagnostics never change the cycle's RC.
Every launcher follows `doc/reglas/NORMAS_BATCH.md` (shared `scripts/launch/lib/common.bat`): paths derived from the file's
own location, no hard-coded versions/dates, the launcher's own exit codes in 100-199 (100 usage, 101 interpreter, 102 resume
refused, 103 state, 104 preflight, 105 another instance running, 106 PostgreSQL driver blocked by Application Control — checked by `:init` and `shared/env_guard.py`; tool codes 1-99 pass through untouched), single-instance lock
shared by `P1_P2_Complete.bat`, `P2_P3_complete.bat` and `P1_P2_P3.bat` (the integrated P1→P2→beta gate→P3 run: `--only-p3`
resumes just the gate + P3 on the last OK cycle), and the real standby timeout saved and restored.
Postgres SQL validity is checked by an `EXPLAIN` sweep over every statement production code executes — see
`doc/reglas/NORMAS_IMPLEMENTACION.md` §7 before adding SQL or an allowlist entry.

**P1 full pipeline:**
```batch
cd C:\desarrollo\fondos\scripts\launch
P1_discoverAllFunds.bat
```
Log: `proyecto1/log/log_pipeline_YYYYMMDD_HHMMSS.log`. Duration: ~8–12 min.

**P1 specific ISINs** (the only classification mode is nature-first over the harvest catalogue; `--block` and `--master <Excel>` are RETIRED, FND-0247:
refused before any connection is opened, and `pipeline.run_block(nature_first=False)` raises `NotImplementedError`):
```batch
cd C:\desarrollo\fondos\proyecto1
python run_block.py --nature-first --master-db --list-isin LU0232465467,LU1873127366
```

**P1 family-nature refresh** (recomputes the attributes derived from the nature of funds `fund_family_builder` corrected; the set is selected, never passed;
`P1_discoverAllFunds.bat` runs it after the builder; closure check: `scripts/audit/verify_family_refresh.py`):
```batch
cd C:\desarrollo\fondos\proyecto1
python run_block.py --family-nature-refresh --master-db
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
| `AUDIT_P1.bat` | — |
| `AUDIT_P2.bat` | — |
| `AUDIT_statistical.bat` | — |
| `FND-0246.bat` | — |
| `FND-0246_V2.bat` | — |
| `P1_P2_Complete.bat` | P1 |
| `P1_P2_P3.bat` | P1 |
| `P1_diagCost.bat` | P1 |
| `P1_discoverAllFunds.bat` | P1 |
| `P1_discoverAllFundsPlusCostDiag.bat` | P1 |
| `P1_harvestFunds.bat` | P1 |
| `P1_refreshBenchmarks.bat` | P1 |
| `P2_P3_complete.bat` | P2 |
| `P2_calculateIndicators.bat` | P2 |
| `P2_discoverLoadMetrics.bat` | P2 |
| `P3_buildPortfolio.bat` | P3 |
| `P3_generateReport.bat` | P3 |
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
| `preflightCheck` | Read-only pre-flight before acting on a stored plan, summary or handoff, or after a long idle — git dirty files, running jobs and cycle lock, DB state |
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

1. **COALESCE mandatory** — All upserts on extracted fields: `COALESCE(excluded.col, col)`. Exception: `SRRI_Visual` (regenerated each cycle).
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

- **R-1** — Normalization maps live only in `classify_utils.py`. No duplicates elsewhere (except `fund_writer._normalize_record` as intentional defense-in-depth). *(This is the enforced instance of P#11.)*
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
