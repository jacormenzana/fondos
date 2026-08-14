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

Python: `C:\Users\Administrador\anaconda3\envs\des\python.exe`.
If any asset is missing, report immediately before proceeding.

**Pre-flight — backlog scope:** Read the live backlog artifact before starting §3. Extract all currently open P1 and P2 items. Scope every investigation step to open items only; skip confirmed findings. Known confirmed-closed: P2-03 regime gap is data-driven (Recalentamiento/Recalentamiento_Tardio n_obs=0 confirmed 2026-08-14) — only action remaining is docstring in `fund_scorer.py`. Re-flag only if a new run produces `n_obs_recalentamiento > 0`.

---

## 3. Execution Workflow

### P1 Branch

#### Step 1 — P1 Log Triage

Parse the latest `log_pipeline_*.log`. Extract and report:

1. **ERROR lines** — funds not persisted; count per block.
2. **WARNING lines** — semantic inconsistencies, DQ flags, INTER-rule fires; count + group by tag.
3. **Block counts** — `monetarios=N, rf_corto=N, rf_flexible=N, renta_variable=N, mixtos=N, alternativos=N, restantes=N`.
4. **Named signals** — BL_B6_HY_KIID fires, FamilyBuilder inconsistencies, WRONG_DOC hits, FORCE_REFRESH triggers.

Compare against prior baseline. Flag any count that regressed.

#### Step 2 — DQ Issue Analysis

Query the DB:
```sql
SELECT check_code, level, COUNT(*) n FROM fund_data_quality_issues
GROUP BY check_code, level ORDER BY n DESC;
```

- Group by `level` (WARN > INFERRED > MISSING).
- For each WARN group with `n > 5`, classify: **Fixable** / **Stale value** / **Benign**.
- Surface the top 3 actionable WARNs for fixing.

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
   - P2: metric-calc → `proyecto2/src/calculations/<module>.py`; writer → `writers/metrics_writer.py`; regime mapping → `regime_returns.py`; fingerprint → `utils/fingerprint.py`.
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
C:\Users\Administrador\anaconda3\envs\des\python.exe -X utf8 proyecto1/tools/audit_benchmark_consistency.py
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
- **Fix in the correct module (P#7).** See Step 7 module map. SQL only for diagnostic SELECTs and FORCE_REFRESH triggers.
- **AST validate after every Python edit (R-8):**
  ```
  C:\Users\Administrador\anaconda3\envs\des\python.exe -c "import ast; ast.parse(open('file.py', encoding='utf-8').read()); print('AST OK')"
  ```
- **Tests must stay green (R-7):**
  ```
  :: P1
  C:\Users\Administrador\anaconda3\envs\des\python.exe -m pytest proyecto1/tests/ -q
  :: P2
  cd proyecto2 && C:\Users\Administrador\anaconda3\envs\des\python.exe -m pytest tests/ -q
  ```
- **COALESCE / graceful degradation:** missing data must yield NULL or a correct sentinel, not an error.
