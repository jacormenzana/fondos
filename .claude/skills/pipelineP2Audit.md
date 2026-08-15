# Skill: pipelineP2Audit
**Description:** Deep-dive audit of a P2 quantitative-metrics run (`P2_calculateIndicators.bat`) — log triage, process-efficiency & redundancy analysis, data-reliability assessment, P2↔P3 regime-interface alignment (5-vs-7 scenario flag), gap analysis, and backlog maintenance. Produces a short, ultra-executive Metrics Audit Report. Also invoked as `pipelineP2Audit`.

---

## 1. Role & Constraints

**Role:** Senior System Auditor & Data Operations Consultant — enterprise process optimization and data integrity.
**Language:** English. **Tone:** Ultra-executive, concise, direct. **Format:** Bold headers, bullet points, structured tables. Zero filler.
**Non-negotiable:** All fixes must satisfy the 11 design principles (P#1–P#11) and R-1..R-8 in `doc/reglas/`. Root cause only — no symptomatic patches, no ad-hoc SQL to patch metrics data.

---

## 2. Assets — locate LATEST by timestamp (YYYYMMDD_HHMMSS) on invocation

| Asset | Pattern | Location |
|-------|---------|----------|
| Standard log | `log_P2_calcIndicators_*.log` (latest, non-`_err`) | `proyecto2/log/` |
| Error log | `log_P2_calcIndicators_*_err.log` (latest) | `proyecto2/log/` |
| Metrics export | `p2_metricas_*.xlsx` (latest) | `out/metrics/` |
| Per-run trace | `p2_pipeline_log` table | `db/fondos.sqlite` |
| Metrics table | `fund_metrics` (ISIN, metric, horizon, real_flag) | `db/fondos.sqlite` |
| Regime source of truth | `_REGIME_SUFFIX` (7 regimes) | `proyecto2/src/calculations/regime_returns.py` |
| Backlog artifact | integrated incident-backlog | ask user if path unclear |

Python: `C:\Users\Administrador\anaconda3\envs\des\python.exe`.
If any asset is missing, report immediately before proceeding.

**Pre-flight — backlog scope:** Read the live backlog artifact before starting §3. List currently open P2 items. Scope triage, reliability, and efficiency steps to open items only. Note: P2-03 (regime gap) is confirmed data-driven as of 2026-08-14 — only remaining action is a docstring in `fund_scorer.py`; do not re-investigate unless `n_obs_recalentamiento > 0` appears in a new run.

---

## 3. Execution Workflow

### Step 1 — Log Triage

Parse the latest `log_P2_calcIndicators_*.log` and `_err.log`. Extract and report:

1. **ERROR lines** — ISINs not persisted; every execution error in the `_err.log`.
2. **WARNING lines** — NaN propagation, fingerprint mismatches, metric-range anomalies; count + group by type.
3. **Throughput** — total ISINs processed, cache-hit count, recomputed count, skipped count; per-run duration.
4. **Cross-check** `p2_pipeline_log` status counts against the standard log.

Compare against prior run baseline. Flag any regression (drop in throughput, new error class).

### Step 2 — Data Reliability & Integrity

- Spot-check metric ranges: vol ≥ 0, |sharpe| plausible, max_drawdown ∈ [−100%, 0], real vs nominal consistency.
- Surface NaN/NULL coverage per metric (query `fund_metrics` grouped by `metric`).
- Flag ISINs with full-NULL metric rows (no data at all → likely NAV gap).
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
- **Run-stamp vs value-stamp reconciliation.** Assert `MAX(fm.load_ts) ≤ fms.calculated_at` per ISIN for all processed funds. A `load_ts` **newer** than `calculated_at` indicates a write/commit ordering bug:
  ```sql
  SELECT fms.isin, MAX(DATE(fm.load_ts)) AS max_load_ts, fms.calculated_at
  FROM fund_metric_state fms
  JOIN fund_metrics fm ON fms.isin = fm.isin
  WHERE fms.metric_version = 'v1' AND fms.calculated_at = date('now')
  GROUP BY fms.isin, fms.calculated_at
  HAVING max_load_ts > fms.calculated_at;
  ```
- **Orphan-beta staleness.** Flag any `beta_*` row whose `load_ts` predates `calculated_at` by more than 91 days — beyond the EFF-1 cadence window it is a stuck/orphan value (VIF-dropped factor never cleaned up):
  ```sql
  SELECT fm.isin, fm.metric, DATE(fm.load_ts) AS load_date, fms.calculated_at
  FROM fund_metrics fm
  JOIN fund_metric_state fms ON fm.isin = fms.isin AND fms.metric_version = 'v1'
  WHERE fm.metric LIKE 'beta_%'
    AND (julianday(fms.calculated_at) - julianday(DATE(fm.load_ts))) > 91;
  ```
- **NAV-staleness gate.** Use the `[NAV STALE]` log line as the primary source. Any WARNING means funds were recomputed on prices > 60 days old — fresh-looking metrics on stale data. Verify with:
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

### Step 3 — Process-Efficiency & Redundancy

- Identify ISINs recomputed unnecessarily (fingerprint unchanged → should be cache-hit).
- Flag repeated regime-history loads inside per-fund loops (load once, reuse).
- Note static / pre-determined metrics recomputed each cycle on stable NAV series.
- Estimate compute waste (ISIN count × average per-ISIN ms × redundant fraction).

### Step 4 — Root-Cause Fixes

For each confirmed root-cause bug (from Steps 1–3 and §4 findings):

1. **Read the target file** before editing (P#3).
2. **Fix in the correct module** — metric-calc bug → `proyecto2/src/calculations/<module>.py`; write-path bug → `run_pipeline.py` write helpers (`_write_metrics` / `_write_timeseries` / `_write_metric_alerts` / `_replace_beta_set`); `writers/metrics_writer.py` is a **legacy stub — do not edit**; regime mapping → `regime_returns.py`; fingerprint logic → `utils/fingerprint.py` (P#7).
3. **AST validate** immediately after every Python edit (R-8) — see §9.
4. **Write regression tests** (R-7 — no `run_pipeline` / `core.io` imports in tests).
5. **Run full test suite** — must stay green — see §9.
6. **Commit** with tag `FIX-<SURFACE>-<N>` (e.g. `FIX-REGIME-2`, `FIX-SHARPE-NULL-1`).

---

## 4. Interface Alignment & Consistency Audit

**P2 ↔ P3 Regime-Interface Alignment (P2-03 — confirmed, no re-investigation needed):**

- **Status: Confirmed data-driven gap (2026-08-14).** Recalentamiento and Recalentamiento_Tardio have `n_obs = 0` for every ISIN across 320 months of EU history. Shock_Energetico (priority 2) preempts every EU high-IPC window at `IPC_HIGH_THRESHOLD = 0.04`. Not an interface bug.
- `_REGIME_SUFFIX` in `regime_returns.py` correctly declares all 7 regimes. `MIN_OBS_REGIME = 12` silently skips the two absent regimes — expected graceful degradation; P3 Layers 1–3 fall back to base score.
- **P3 operational impact: none.** No sub-portfolio weight or multiplier is stranded without data.
- **Only remaining action (P2-03):** add a docstring in `fund_scorer.py` Layer 3 explaining the structural absence. No code change, no rerun.
- On future audits: re-flag only if a new pipeline run produces `n_obs_recalentamiento > 0` for any ISIN.

---

## 5. Cross-Project Gap Analysis

**Objective:** identify missing KPIs/metrics that would raise P3 decision precision and reliability.

- **Map the consumed surface.** Enumerate P2 metrics P3 actually reads (`return_ann_real`, `sharpe`, `max_drawdown`, `alpha_persistence`, `capture_ratio`, `momentum_rank`, regime-specific betas). Flag any with high NULL rates, thin obs count, or stale NAV series.
- **Diagnose downstream impact.** For each weak metric, name the P3 decision it degrades (hard filter, base score, regime multiplier).
- **Propose enhancements.** Candidates: regime-coverage flag per fund (obs count per regime), confidence/completeness score, NAV-series staleness indicator. Distinguish "P2 should provide this" from "genuinely a P3 concern".
- **Respect the architecture.** Flow is unidirectional P1 → P2 → P3. Do not propose P2 consuming P3 outputs.

---

## 6. Key Architecture Reminders

- **Fingerprint cache:** `compute_input_hash()` (SHA-1 over NAV last-date / rows / value + IPC coverage + `METRIC_VERSION` + `CALC_VERSION`). Unchanged inputs → 100% cache-hit. **Bump `CALC_VERSION`** in `run_pipeline.py` to force full recompute after changing calculation logic.
- **`MIN_OBS_REGIME = 12`:** Regimes with < 12 historical months are silently skipped — expected gap, not a bug, unless naming diverges from P3.
- **VIF > 10:** Macro factors with VIF above threshold are excluded from the OLS model; their betas will be NULL.
- **COALESCE / graceful degradation (P#1):** missing NAV or thin regime history must yield NULL, not error. A fix returning None changes nothing.
- **Generic signals (P#5):** No hardcoded fund names in calculators. Use metric thresholds and statistical guards.
- **R-4:** Effective-value pattern applies in P2 where multiple data sources may be NULL — always test for None before use.

---

## 7. Deliverable — Executive Audit Report

Emit in this order, short and table-driven:

1. **Executive Summary** — findings in ≤ 6 bullets.
2. **Critical Alerts** — 5-vs-7 regime-scenario drift verdict (data-driven vs interface bug) + P3 impact; any `_err.log` failures.
3. **Action Items** — table: item · step · severity · root cause · fix location.
4. **Efficiency & Gap Findings** — compute waste estimate (Step 3) + P2→P3 metric gaps (§5).
5. **Deferred Items** — open findings deferred; reason for each (thin obs / stale NAV / complex). Recommended next action.

---

## 8. Backlog Artifact Maintenance

**Objective:** keep the shared integrated incident-backlog artifact current as an automatic by-product of every audit.

- **Reconcile against session findings.** Advance in-progress items (status + evidence); add newly discovered items (root-cause hypothesis, owning module, severity, ISIN count); close resolved items, linking fix commit/tag.
- **Refresh ancillary sections** — open/closed rollups, summary counts, next-action recommendations, absolute dates.
- **Preserve structure and history.** Append or annotate in place. Never rewrite, reorder, or drop prior entries.

If the artifact path is ambiguous, ask the user before writing.

---

## 9. Execution Rules

- **Read before modifying (P#3).** Always read the production file before editing. Never assume content.
- **Fix in the correct module (P#7).** Metric-calc bug → `proyecto2/src/calculations/<module>.py`; write-path bug → `run_pipeline.py` write helpers (`_write_metrics` / `_write_timeseries` / `_write_metric_alerts` / `_replace_beta_set`); `writers/metrics_writer.py` is a **legacy stub — do not edit**; regime mapping → `regime_returns.py`. SQL only for diagnostic SELECTs.
- **AST validate after every Python edit (R-8):**
  ```
  C:\Users\Administrador\anaconda3\envs\des\python.exe -c "import ast; ast.parse(open('file.py', encoding='utf-8').read()); print('AST OK')"
  ```
- **Tests must stay green (R-7 — no `run_pipeline` / `core.io` imports in tests):**
  ```
  cd proyecto2 && C:\Users\Administrador\anaconda3\envs\des\python.exe -m pytest tests/ -q
  ```
- **COALESCE / graceful degradation:** missing NAV or thin regime history must yield NULL, not error.
