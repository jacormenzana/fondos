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
| `NORMAS_SQL_TOKENS.md` | *how to limit db outputs* | Token-efficiency constraints, mandatory limits for SQL outputs, and exploratory queries |
| `P4_BI_CHARTER.md` | *how BI sync works* | P4 Postgres/Superset charter (ETL, ports, DDL, datasets) |
| `AUDITORIA_ESTADISTICA.md` | *how statistical distribution audits work* | 7-block model evaluation, indicator catalog, generic function/config catalog for the cost-attribute and P2-metrics statistical audits |
| `NORMAS_BATCH.md` | *how to write a `.bat` launcher* | The single criterion for every `.bat`: skeleton, no hard-coded paths/versions, own exit codes 100-199, single instance, standby/codepage restore, `cmd.exe` pitfalls; reference implementation `scripts/launch/lib/common.bat` + `P1P2_runCycle.bat`, template `_template.bat` |
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

**Publishing (branch protection of `master`, FND-0336 / owner action O-5; the rule is active from the moment the owner applies it, its state is tracked in that ticket):** never `git push origin master`. Create a branch from `origin/master`, push it, open a pull request and merge it once the four required CI checks are green (`PG fixture smoke test`, `Full test suite (environment-linux.yml, real Postgres 17)`, `Verify all AUTO sentinels are in sync`, `Verify statistical-audit catalog consistency`); the rule includes administrators, so the owner's own account is bound too. Settings, read-only verification and the emergency path: `scripts/ops/github_protect_master.py` and `doc/especificacion/PROPUESTA_BACKLOG_V2.md` §6.4.7 (emergency only, by the owner: disable the rule, log `V-GATE-EMERGENCY` in FND-0336, restore it within 48 h).

---

## Gestión de Puntos de Acción (Action Points Backlog en PostgreSQL)

**System of record since 2026-09-21:** database `gestion` (schema `backlog`) on the live server; DSN `FONDOS_BACKLOG_PG_DSN` (deliberately distinct from `FONDOS_PG_DSN`). **Canonical rules: `doc/reglas/NORMAS_BACKLOG.md` — read it before opening, starting or closing a ticket.**

Every finding, error, or improvement identified during a session must be registered in `BACKLOG`.

- **Never invent a domain value** (`nature`, `object_type`, `priority`, `status`, `solution_type`: `CHECK`-enforced, lists in §2); ids come from `backlog.seq_backlog_fnd`, never by hand.
- **Never overwrite progress:** every advance is an `INSERT` into `BACKLOG_LOGS`; closing needs `closure_ts` + `solution_type`, a production deploy also `deployment_ts` + `release_version`.
- **Single door (Backlog v2 Phase A, FND-0331):** open and move tickets with `shared.backlog_client.open_ticket()` / `transition()` and close releases with `scripts/ops/close_release.py` (dry run first), always with an explicit canonical actor (`agent:<model>` / `human:<login>`, or `FONDOS_ACTOR`; never guessed from the OS user); no hand-written `INSERT`/`UPDATE` on `backlog.backlog` (`tests/test_backlog_single_door.py`). The owner applies the DDL in steps (A-1 additive, A-2 revoke); until A-1 is applied the client detects it and the legacy forms in `NORMAS_BACKLOG.md` §2 apply.
- **Check relationships before acting:** `python scripts/audit/backlog_links.py --new "<text>"` before opening a ticket, `--for FND-nnnn` before starting one (exit 1 while a confirmed `DEPENDS_ON` prerequisite is open). Links are events decided by a person, never edited (§3, §6).
- **Automatic capture** (`shared/backlog_client.report_incident()` / `capture_exceptions()` / `install_excepthook()`) is a no-op while `FONDOS_BACKLOG_PG_DSN` is unset and never masks the original exception; overlap policy and calibration gate: §7.

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
## Area guides

Before changing code in a folder below, read its guide. Claude Code loads it automatically (the folder's `CLAUDE.md` imports it) the first time you read a file there.

| Working in | Guide | Holds |
|------------|-------|-------|
| `proyecto1/` | `proyecto1/AGENTS.md` | P1 execution order, blocks, KIID_Status, harvest and KIID lifecycle, key modules, P1 module map, P1 commands |
| `proyecto2/` | `proyecto2/AGENTS.md` | P2 tables, horizons, annualisation, momentum, caching, macro factors and sources, module map, P2 commands |
| `proyecto3/` | `proyecto3/AGENTS.md` | regimes, weights, scoring, constraints, report, scenario lifecycle and promotion gates, freshness gate, module map, P3 commands |
| `scripts/launch/` | `scripts/launch/AGENTS.md` | cycle launchers, resume, audit ownership, exit codes, canonical launcher list |
| `scripts/audit/` | `scripts/audit/AGENTS.md` | sentinel sync of the governed documents, its enforcement, the split check |

Kill-switch registry (every `*_ENABLED`): `doc/reglas/NORMAS_IMPLEMENTACION.md` §8. **P2 recompute:** bump `CALC_VERSION` (`proyecto2/src/pipeline/run_pipeline.py`, currently `"20261011"`) to force a full recompute of all ISINs; details in `proyecto2/AGENTS.md`.

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

- Cycle launchers (`P1P2_`, `P2P3_`, `P1P2P3_runCycle`), resume, audit ownership, exit codes and the canonical launcher list: `scripts/launch/AGENTS.md`.
- Single-area commands: P1 `proyecto1/AGENTS.md`, P2 `proyecto2/AGENTS.md`, P3 `proyecto3/AGENTS.md`.

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

Mechanical sections of the governed documents are regenerated by `scripts/audit/sync_agents_md.py` (sentinels, modes, enforcement and the split check: `scripts/audit/AGENTS.md`). Hand-written prose stays outside the sentinel markers and is never touched by the generator.

**Prose consistency rules (enforced by advisory checks — not blocking but reported at every run):**
- **No hardcoded counts** — never write "five documents" or "24 betas" in prose; counts go stale
  silently. Let the table or the sentinel be the count; prose just says "see table below".
- **No version labels in hand-written tables** — never write "new v24" or "added in v22"; schema
  versions increment and the label becomes misleading. Version history belongs in `git log`.
- Domain-doc table row count is checked against `doc/reglas/*.md` (non-legacy) at every `--check`
  and `--write` run; a mismatch emits an advisory.

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

Full table roster (generated): `doc/reglas/SCHEMA_REFERENCE.md`.

### Known bugs

| ID | Column | Issue |
|----|--------|-------|
| P13 | `Processing_Time_Ms` | Stores seconds, not milliseconds |
| — | `KIID_Downloaded_At` | 478 funds with NULL (legacy bug, self-healing) |
| — | `ACI_RHP` | Residual NULL on funds whose KID over-time table exposes no bindable RHP horizon (collapsed grid / legacy UCITS "Impact on return (RIY)" layout). Values-path recovery covers the majority; see backlog ACT-06. |
