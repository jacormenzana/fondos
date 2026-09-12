# Skill: pipelineP1P2Audit
**Description:** Full-cycle diagnostic audit of a P1→P2 pipeline run — classification log triage, DQ analysis, benchmark-consistency (B1–B7), quantitative-metrics reliability, process-efficiency, P2↔P3 regime-interface alignment, cross-project gap analysis, root-cause fixes, and backlog maintenance. Covers both domains in one pass. Also invoked as `pipelineP1P2Audit`.

---

## 1. Role & Constraints

**Role:** Lead Data Pipeline Architect & QA Engineer — full-cycle P1→P2→P3 coverage.
**Language:** English. **Tone:** Ultra-executive. **Format:** Bold headers, bullet points, structured tables. Zero filler.
**Non-negotiable:** All fixes must satisfy the 11 design principles (P#1–P#11) and R-1..R-8 in `doc/reglas/`. Root cause only — no symptomatic patches, no ad-hoc SQL to patch classification or metrics data.

---

## 2. Assets — locate LATEST by timestamp (YYYYMMDD_HHMMSS) on invocation

**P1 assets:**

| Asset | Pattern | Location |
|-------|---------|----------|
| Pipeline log | `log_pipeline_*.log` (latest) | `proyecto1/log/` |
| P1 export | `p1_export_*.xlsx` (latest) | `out/export/` |
| Benchmark audit findings | `benchmark_audit_findings.json` (latest) | `out/audit/` |
| Benchmark audit tool | `audit_benchmark_consistency.py` | `proyecto1/tools/` |

**P2 assets:**

| Asset | Pattern | Location |
|-------|---------|----------|
| Standard log | `log_P2_calcIndicators_*.log` (latest, non-`_err`) | `proyecto2/log/` |
| Error log | `log_P2_calcIndicators_*_err.log` (latest) | `proyecto2/log/` |
| Metrics export | `p2_metricas_*.xlsx` (latest) | `out/metrics/` |
| Per-run trace | `p2_pipeline_log` table | `db/fondos.sqlite` |
| Metrics table | `fund_metrics` (ISIN, metric, horizon, real_flag) | `db/fondos.sqlite` |
| Regime source of truth | `_REGIME_SUFFIX` (7 regimes) | `proyecto2/src/calculations/regime_returns.py` |

**Shared:**

| Asset | Pattern | Location |
|-------|---------|----------|
| DB | `fondos.sqlite` | `db/` |
| Backlog artifact | integrated incident-backlog | ask user if path unclear |

Python: `C:\data\envs\des\python.exe`.
If any asset is missing, report immediately before proceeding.

**Pre-flight — backlog scope:** Read the live backlog artifact before starting §3. Extract all currently open P1 and P2 items. Scope every investigation step to open items only; skip confirmed findings. Known confirmed-closed: P2-03 regime gap is data-driven (Recalentamiento/Recalentamiento_Tardio n_obs=0 confirmed 2026-08-14) — only action remaining is docstring in `fund_scorer.py`. Re-flag only if a new run produces `n_obs_recalentamiento > 0`.

**Pre-flight — memory recall:** Before §3, read `MEMORY.md` at `C:\Users\jacor\.claude\projects\c--desarrollo-fondos\memory\MEMORY.md`. Load every entry whose description hook matches this audit's domain keywords (P1 classification, FORCE_REFRESH, cost extraction, ACI_RHP, B1–B7 benchmark, DQ issue, regime attribution, OLS/VIF, NAV staleness, fingerprint, CALC_VERSION). Treat recalled entries as prior findings — do not re-investigate what is already confirmed on record.

---

## 3. Execution Workflow

### P1 Branch

#### Step 1 — P1 Log Triage

**P1 has no Python `logging`-module log file with structured per-fund events.** The log file
(`log_pipeline_*.log`) is stdout/stderr redirected by the `.bat` launcher — useful for top-level
block timings, unhandled-exception `[ERROR]` prints, and the cycle incidencias summary. **The
authoritative per-fund audit trail is `ingestion_log` (DB).** Query it for INTER fires, WARN
events, and step counts. The log file is a secondary source for timing and catastrophic failures only.

Scan the latest `log_pipeline_*.log` for:

1. **Unhandled exceptions / crash prints** — lines containing `Traceback`, `Exception`, or `[ERROR]` from unhandled Python errors (not in `ingestion_log`).
2. **Block timing summary** — per-block elapsed times from launcher output.
3. **Cycle incidencias summary** — the `--- RESUMEN DE INCIDENCIAS DEL CICLO ---` block at log end.

Then query the **canonical DB audit trail**:

```sql
-- Run boundaries: when did this cycle start and end?
SELECT step, status, message, created_at
FROM ingestion_log
WHERE step IN ('RUN_START', 'RUN_SUMMARY')
ORDER BY created_at DESC LIMIT 4;

-- High-signal events not surfaced in fund_data_quality_issues
SELECT step, status, COUNT(DISTINCT ISIN) AS n
FROM ingestion_log
WHERE step IN (
    'NATURE_LOW_CONFIDENCE','COST_RANGE_GUARD','BL_COST_4C_OC_ACI_MISMATCH',
    'FIX_ARB_FALLBACK','INTER_DBLCLAIM_RV_WINS','INTER_DBLCLAIM_RV_WINS_BENCHMARK',
    'INTER_VOTE3_RECLASSIFIED','INTER_VOTE3_MONETARIO_FLAG_ONLY',
    'BL64E_FAMILY_RFC_CORRECTION','BL30_INVESTMENT_FOCUS_SECTOR',
    'BL31_CH_HP_RECONCILE','BL45_HP_FROM_CH_PROPAGATE','BL49_CH_FROM_HP_PROPAGATE'
)
  AND created_at >= (
      SELECT message FROM ingestion_log
      WHERE step = 'RUN_START' ORDER BY created_at DESC LIMIT 1
  )
GROUP BY step, status ORDER BY n DESC;
```

Compare event counts against the prior run's `RUN_START`/`RUN_SUMMARY` rows. Flag any regression.

#### Step 2 — DQ Issue Analysis & Reliability Controls

**Note on time scope:** `_finalize_data_quality_issues()` DELETE+INSERTs per ISIN each cycle. On full-universe runs, `FIX-DQ-STALE-SWEEP-1` purges rows with `detected_at` before `_cycle_start_ts`. On partial runs, historical rows may survive. Always scope the query to the current cycle:

```sql
-- Current-cycle DQ issues only
SELECT check_code, level, COUNT(*) n FROM fund_data_quality_issues
WHERE DATE(detected_at) = date('now')
GROUP BY check_code, level ORDER BY n DESC;
```

- Group by `level` (WARN > INFERRED > MISSING).
- For each WARN group with `n > 5`, classify: **Fixable** / **Stale value** / **Benign**.
- Surface the top 3 actionable WARNs for fixing.

**Reliability Control 1 — WRONG_DOC stale fund_master.** When WRONG_DOC is detected, `publish_fund` is skipped — old classification and cost data persist and appear valid to P2/P3:
```sql
SELECT fm.ISIN, fm.Fund_Nature, fm.Data_Quality_Flag,
       km.KIID_Status, km.KIID_Downloaded_At
FROM fund_master fm
JOIN fund_kiid_metadata km ON fm.ISIN = km.ISIN AND km.KIID_Class = 1
WHERE km.KIID_Status = 'WRONG_DOC'
ORDER BY km.KIID_Downloaded_At DESC;
```
Any fund here with `Data_Quality_Flag != 'WARN'` is silently stale to P2/P3. Remediation: `UPDATE fund_master SET Data_Quality_Flag='WARN' WHERE ISIN=?` (diagnostic SQL allowed per P#7), then FORCE_REFRESH when a replacement KIID is available.

**Reliability Control 2 — SRRI NULL / Data_Quality_Flag distribution.** `SRRI=NULL` prevents BL-44 from firing and leaves `Profile=NULL` for Restantes funds:
```sql
SELECT
  COUNT(*) FILTER (WHERE SRRI IS NULL)               AS srri_null,
  COUNT(*) FILTER (WHERE Data_Quality_Flag='WARN')   AS dqf_warn,
  COUNT(*) FILTER (WHERE Data_Quality_Flag='MISSING') AS dqf_missing,
  COUNT(*) FILTER (WHERE Profile IS NULL)             AS profile_null
FROM fund_master;
-- Drill: NULL Profile per Fund_Nature
SELECT Fund_Nature, COUNT(*) n FROM fund_master
WHERE Profile IS NULL GROUP BY Fund_Nature ORDER BY n DESC;
```
Verify that no known-valid SRRI was silently cleared (`sqlite_writer.py` COALESCE protection applies only when `SRRI_Quality_Flag IS NOT NULL AND != 'NONE'`).

**Reliability Control 3 — Cost arbitration verdict distribution.** `NULL` vs `'BOTH_FAIL'` are not equivalent:
```sql
SELECT
  COUNT(*) FILTER (WHERE Cost_Mgmt_Arbitration IS NULL)        AS never_attempted,
  COUNT(*) FILTER (WHERE Cost_Mgmt_Arbitration = 'BOTH_FAIL')  AS attempted_failed,
  COUNT(*) FILTER (WHERE Cost_Mgmt_Arbitration = 'AGREE')      AS agree,
  COUNT(*) FILTER (WHERE Cost_Mgmt_Arbitration LIKE 'ONLY_%')  AS partial
FROM fund_kiid_metadata WHERE KIID_Class = 1;
-- Verify no fund has all three cost columns NULL
SELECT COUNT(*) FROM fund_cost_schedule
WHERE Total_Costs_EUR IS NULL AND Total_Costs_Pct IS NULL AND Annual_Impact_Pct IS NULL;
```
High `BOTH_FAIL` → investigate `FIX_ARB_FALLBACK` events in `ingestion_log`. High `NULL` → check `PRIIPS_COST_EXTRACTION_ENABLED` kill-switch.

**Reliability Control 4 — Family consistency residuals.** Post-correction families still with >1 Fund_Nature:
```sql
SELECT ff.family_id, ff.family_name, ff.Fund_Nature AS family_nature,
       GROUP_CONCAT(DISTINCT fm.Fund_Nature) AS member_natures,
       COUNT(*) AS n
FROM fund_families ff
JOIN fund_master fm ON fm.fund_family_id = ff.family_id
GROUP BY ff.family_id
HAVING COUNT(DISTINCT fm.Fund_Nature) > 1
ORDER BY n DESC;
```
Investigate with `fund_family_builder.py` logic (structural heterogeneity or bipartite tie).

#### Step 3 — P1 Process-Efficiency Pass

- Identify blocks re-running for stable funds (no KIID change, no FORCE_REFRESH).
- Flag repeated master-load or FamilyBuilder runs with no new ISINs.
- Note WRONG_DOC / CACHED funds consuming unnecessary classifier time.
- Record estimated overhead (count × average per-fund ms).

### P2 Branch

#### Step 4 — P2 Log Triage

Parse the latest `log_P2_calcIndicators_*.log` and `_err.log`. Extract and report:

1. **ERROR lines** — ISINs not persisted; every execution error in the `_err.log`.
2. **WARNING lines** — NaN propagation, fingerprint mismatches, metric-range anomalies; count + group by type.
3. **Throughput** — total ISINs processed, cache-hit count, recomputed count, skipped count; per-run duration.
4. **Cross-check** `p2_pipeline_log` status counts against the standard log.

Compare against prior baseline. Flag any regression.

#### Step 5 — P2 Data Reliability & Integrity

- Spot-check metric ranges: vol ≥ 0, |sharpe| plausible, max_drawdown ∈ [−100%, 0], real vs nominal consistency.
- Surface NaN/NULL coverage per metric (query `fund_metrics` grouped by `metric`).
- Flag ISINs with full-NULL metric rows.
- **`load_ts` cohort check.** Use the `[RUN COHORT]` log line as the primary source. A split is EXPECTED only when every stale row's `metric` is in the OLS-cadence set (`beta_*`, `energy_sensitivity_pct`, `hy_spread_sensitivity_pct`) AND the fund's `calculated_at` advanced. Any stale row **outside** that set, or any fund with stale rows but a non-advanced `calculated_at`, is an **ANOMALY** (silent write failure) — flag with ISIN + metric list using this aggregate query:
  ```sql
  -- Identify ANOMALY funds: stale load_ts on a non-cadence metric
  SELECT fm.isin, fm.metric, DATE(fm.load_ts) AS load_date, fms.calculated_at
  FROM fund_metrics fm
  JOIN fund_metric_state fms ON fm.isin = fms.isin AND fms.metric_version = 'v1'
  WHERE fms.calculated_at = date('now')
    AND DATE(fm.load_ts) < date('now')
    AND fm.metric NOT LIKE 'beta_%'
    AND fm.metric NOT IN ('energy_sensitivity_pct','hy_spread_sensitivity_pct');
  ```
- **Run-stamp vs value-stamp reconciliation.** Assert `MAX(fm.load_ts) ≤ fms.calculated_at` per ISIN. A `load_ts` **newer** than `calculated_at` indicates a write/commit ordering bug:
  ```sql
  SELECT fms.isin, MAX(DATE(fm.load_ts)) AS max_load_ts, fms.calculated_at
  FROM fund_metric_state fms
  JOIN fund_metrics fm ON fms.isin = fm.isin
  WHERE fms.metric_version = 'v1' AND fms.calculated_at = date('now')
  GROUP BY fms.isin, fms.calculated_at
  HAVING max_load_ts > fms.calculated_at;
  ```
- **Orphan-beta staleness.** Flag any `beta_*` row whose `load_ts` predates `calculated_at` by more than 91 days — beyond EFF-1 cadence it is a stuck/orphan value:
  ```sql
  SELECT fm.isin, fm.metric, DATE(fm.load_ts) AS load_date, fms.calculated_at
  FROM fund_metrics fm
  JOIN fund_metric_state fms ON fm.isin = fms.isin AND fms.metric_version = 'v1'
  WHERE fm.metric LIKE 'beta_%'
    AND (julianday(fms.calculated_at) - julianday(DATE(fm.load_ts))) > 91;
  ```
- **NAV-staleness gate.** Use the `[NAV STALE]` log line as the primary source. Any WARNING means funds were recomputed on prices > 60 days old. Verify with:
  ```sql
  SELECT fms.isin, MAX(n.Date) AS newest_nav, fms.calculated_at,
         julianday(fms.calculated_at) - julianday(MAX(n.Date)) AS age_days
  FROM fund_metric_state fms
  JOIN fund_nav_monthly n ON fms.isin = n.ISIN
  WHERE fms.metric_version = 'v1' AND fms.calculated_at = date('now')
  GROUP BY fms.isin, fms.calculated_at
  HAVING age_days > 60
  ORDER BY age_days DESC;
  ```
  Cross-reference `nav_sources.data_status`. Fix: `nav_discovery --mode update`, then P2 with `--force`.
- **Real/nominal pairing integrity.** When IPC is available, every deflatable `real_flag=0` metric must have a `real_flag=1` pair. Orphan singles indicate a silent deflation gap:
  ```sql
  SELECT a.isin, a.metric, a.horizon
  FROM fund_metrics a
  WHERE a.real_flag = 0
    AND a.metric IN (
        'return_ann_real','sharpe','max_drawdown',
        'alpha_persistence','capture_ratio','momentum_rank'
    )
    AND NOT EXISTS (
        SELECT 1 FROM fund_metrics b
        WHERE b.isin = a.isin AND b.metric = a.metric
          AND b.horizon = a.horizon AND b.real_flag = 1
    );
  ```
- **Coverage delta vs baseline.** Use the `[COVERAGE]` log line (diff current run vs previous run in the log). A drop > ~2% on any P3-consumed metric (`return_ann_real`, `sharpe`, `max_drawdown`, `alpha_persistence`, `capture_ratio`, `momentum_rank`) signals an upstream NAV loss or calc regression — triage immediately.

#### Step 6 — P2 Process-Efficiency & Redundancy

- Identify ISINs recomputed unnecessarily (fingerprint unchanged → should be cache-hit).
- Flag repeated regime-history loads inside per-fund loops.
- Note static metrics recomputed each cycle on stable NAV series.
- Estimate compute waste (count × average ms × redundant fraction).

### Shared Step

#### Step 7 — Root-Cause Fixes

For each confirmed root-cause bug (from Steps 1–6 and §4 findings):

1. **Read the target file** before editing (P#3).
2. **Fix in the correct module:**
   - P1: classifier → `blocks/<block>.py`; normalization map → `classify_utils.py`; INTER rule → `pipeline.py`; KIID parsing → `kiid_parser.py`.
   - P2: metric-calc → `proyecto2/src/calculations/<module>.py`; write-path bug → `run_pipeline.py` write helpers (`_write_metrics` / `_write_timeseries` / `_write_metric_alerts` / `_replace_beta_set`); `writers/metrics_writer.py` is a **legacy stub — do not edit**; regime mapping → `regime_returns.py`; fingerprint → `utils/fingerprint.py`.
   - Never duplicate business logic (P#11, R-1).
3. **AST validate** immediately after every Python edit (R-8) — see §9.
4. **Write regression tests** — P1: no `pipeline.py` / `core.io` imports; P2: no `run_pipeline` / `core.io` imports (R-7).
5. **Run full test suites** — both must stay green — see §9.
6. **Commit** with tag `FIX-<SURFACE>-<N>`.

---

## 4. Interface Alignment & Consistency Audit

### P1 — Benchmark Consistency (B1–B7)

Run the audit tool (regenerates `benchmark_audit_findings.json`):
```batch
C:\data\envs\des\python.exe -X utf8 proyecto1/tools/audit_benchmark_consistency.py
```

| Code | Check | Action threshold |
|------|-------|-----------------|
| B1 | Fund_Nature vs benchmark asset class (CRITICAL) | Any > 0 → investigate |
| B2 | Geography mismatch (fm vs benchmark) | > 10 → triage |
| B3 | Development_Status mismatch | > 5 → triage |
| B4 | Market_Cap mismatch | Ignore "All Cap" gaps (sentinel) |
| B5 | Credit_Quality mismatch | > 3 → triage |
| B6 | SRRI mismatch | > 5 → triage |
| B7 | Declared benchmark drift from historical | > 20 → triage |

Classify each non-trivial conflict: **Root-cause bug** → Step 7 · **Stale value** → FORCE_REFRESH · **Benign** → `_GEO_BENIGN_PAIRS` / `_BENIGN_PAIRS`.

### P2 — Regime-Interface Alignment (P2-03 — confirmed, no re-investigation needed)

- **Status: Confirmed data-driven gap (2026-08-14).** Recalentamiento and Recalentamiento_Tardio have `n_obs = 0` for every ISIN across 320 months of EU history. Not an interface bug.
- `_REGIME_SUFFIX` correctly declares all 7. `MIN_OBS_REGIME = 12` silently skips the two absent regimes — expected graceful degradation; P3 falls back to base score.
- **P3 operational impact: none.** Only remaining action: docstring in `fund_scorer.py` Layer 3 (P2-03).
- On future audits: re-flag only if a new run produces `n_obs_recalentamiento > 0` for any ISIN.

---

## 5. Cross-Project Gap Analysis

**Objective:** unified upstream data-sufficiency assessment across the P1→P2→P3 chain; identify enhancements at each layer.

**P1 → P2:**
- Enumerate P1 attributes P2 reads (`Fund_Nature`, `Credit_Quality`, `Geography`, `SRRI`, `Investment_Universe`, benchmark asset class, `In_Current_Universe`).
- Flag any with high NULL/sentinel rates, low classifier confidence, or granularity too coarse for P2 OLS / regime attribution.

**P2 → P3:**
- Enumerate P2 metrics P3 reads (`return_ann_real`, `sharpe`, `max_drawdown`, `alpha_persistence`, `capture_ratio`, `momentum_rank`, regime-specific betas).
- Flag any with high NULL rates, thin obs count, or stale NAV series.

**Proposed enhancements:**
- State the fix precisely: owning module, reliability gain, effort/risk.
- Distinguish "this layer should provide it" from "genuinely the next layer's concern".
- **Respect the architecture.** P1 → P2 → P3 is unidirectional. Only accepted feedback: P1 reads `srri_nav` from P2 (bounded, accepted — see `AGENTS.md`).

---

## 6. Key Architecture Reminders

**P1:**
- **COALESCE (P#1):** A fix returning None changes nothing — return a concrete value or correct sentinel.
- **Generic signals (P#5):** No hardcoded fund names; use name + KIID text patterns.
- **R-4:** INTER rules: `_X_eff = record.get("X") or _X_bd`. Never `record.get("X")` alone.
- **R-5:** `\b` fails between two letters (e.g., `EURHDG`). Use lookaheads for suffix patterns.
- **R-6:** Text inference: bounded window (~1500 chars) around keyword, not full KIID text.
- **Geography arbitration** (`pipeline.py`): KIID=Global + specific name → name wins; name is sub-region of KIID → name wins (`_is_name_subregion`). Otherwise KIID wins.
- **WRONG_DOC funds:** Stale DQ entries from a prior correct cycle are not current bugs.

**P2:**
- **Fingerprint cache:** bump `CALC_VERSION` in `run_pipeline.py` to force full recompute after logic changes.
- **`MIN_OBS_REGIME = 12`:** Regimes with < 12 months silently skipped — expected, not a bug, unless naming diverges from P3.
- **VIF > 10:** Macro factors excluded from OLS; their betas will be NULL.
- **Graceful degradation:** missing NAV or thin regime history → NULL, not error.

---

## 7. Deliverable — Executive Audit Report

Emit in this order, short and table-driven:

1. **Executive Summary** — combined findings in ≤ 8 bullets (P1 ≤ 4, P2 ≤ 4).
2. **Critical Alerts** — B1 conflicts; 5-vs-7 regime drift verdict (data-driven vs interface bug) + P3 impact; any `_err.log` failures; block-count regressions.
3. **Action Items** — table: item · domain · step · severity · root cause · fix location.
4. **Efficiency & Gap Findings** — P1 + P2 redundancy overhead; P1→P2→P3 attribute/metric gaps (§5).
5. **Deferred Items** — open findings deferred; reason for each; recommended next action per domain.

---

## 8. Backlog Artifact Maintenance

**Objective:** single reconciliation pass across P1 and P2 items — keep the integrated incident-backlog artifact current as an automatic by-product of every audit.

- **Reconcile against session findings.** Advance in-progress items (status + evidence); add newly discovered items (root-cause hypothesis, owning module, severity, ISIN count); close resolved items, linking fix commit/tag.
- **Refresh ancillary sections** — open/closed rollups, summary counts, next-action recommendations, absolute dates.
- **Preserve structure and history.** Append or annotate in place. Never rewrite, reorder, or drop prior entries.

If the artifact path is ambiguous, ask the user before writing.

---

## 9. Execution Rules

- **Read before modifying (P#3).** Always read the production file before editing. Never assume content.
- **Fix in the correct module (P#7).** See Step 7 module map. P2 write-path bug → `run_pipeline.py` write helpers; `writers/metrics_writer.py` is a **legacy stub — do not edit**. SQL only for diagnostic SELECTs and FORCE_REFRESH triggers.
- **AST validate after every Python edit (R-8):**
  ```
  C:\data\envs\des\python.exe -c "import ast; ast.parse(open('file.py', encoding='utf-8').read()); print('AST OK')"
  ```
- **Tests must stay green (R-7):**
  ```
  :: P1
  C:\data\envs\des\python.exe -m pytest proyecto1/tests/ -q
  :: P2
  cd proyecto2 && C:\data\envs\des\python.exe -m pytest tests/ -q
  ```
- **COALESCE / graceful degradation:** missing data must yield NULL or a correct sentinel, not an error.
- **Write-on-correction (verified only):** If the user corrects any finding, factual claim, or reasoning during this audit, verify the correction first (read the code, query the DB, check the canonical doc). If confirmed correct, persist it to memory before the session ends. If wrong, explain and do not write. If unverifiable in context, flag it explicitly — do not persist unverified claims.

---

## 10. End-of-Session Memory Audit

Before ending this session, identify every operational insight corrected or confirmed during this audit that is not yet in memory, and write it now.

For each item:
1. Create or update the memory file (type `feedback` for agent-behavior corrections, `project` for operational facts).
2. Add or update the index line in `MEMORY.md`.
3. Link related entries with `[[name]]`.

Closing the session with uncaptured insight is non-compliant.
