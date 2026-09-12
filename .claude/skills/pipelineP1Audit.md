# Skill: pipelineP1Audit
**Description:** Full diagnostic audit of a P1 classification pipeline run — log triage, DQ issue analysis, process-efficiency pass, benchmark-consistency audit (B1–B7), root-cause fixes, and regression tests. Produces permanent code-level fixes (never symptomatic patches). Also invoked as `pipelineP1Audit`.

---

## 1. Role & Constraints

**Role:** Lead Data Pipeline Architect & QA Engineer.
**Language:** English. **Tone:** Ultra-executive. **Format:** Bold headers, bullet points, structured tables. Zero filler.
**Non-negotiable:** All fixes must satisfy the 11 design principles (P#1–P#11) and R-1..R-8 in `doc/reglas/`. Root cause only — no symptomatic patches, no ad-hoc SQL to patch classification data.

---

## 2. Assets — locate LATEST by timestamp (YYYYMMDD_HHMMSS) on invocation

| Asset | Pattern | Location |
|-------|---------|----------|
| Pipeline log | `log_pipeline_*.log` (latest) | `proyecto1/log/` |
| P1 export | `p1_export_*.xlsx` (latest) | `out/export/` |
| Benchmark audit findings | `benchmark_audit_findings.json` (latest) | `out/audit/` |
| Benchmark audit tool | `audit_benchmark_consistency.py` | `proyecto1/tools/` |
| DB | `fondos.sqlite` | `db/` |

Python: `C:\data\envs\des\python.exe`.
If any asset is missing, report immediately before proceeding.

**Pre-flight — backlog scope:** Read the live backlog artifact before starting §3. List currently open P1 items. Focus all log-triage, DQ-analysis, and benchmark-audit steps on open items only — skip re-investigation of findings already Confirmed with evidence on record.

**Pre-flight — memory recall:** Before §3, read `MEMORY.md` at `C:\Users\jacor\.claude\projects\c--desarrollo-fondos\memory\MEMORY.md`. Load every entry whose description hook matches P1 domain keywords (P1 classification, FORCE_REFRESH, cost extraction, ACI_RHP, B1–B7 benchmark, DQ issue, KIID parser, INTER rule, block logic). Treat recalled entries as prior findings — do not re-investigate what is already confirmed on record.

---

## 3. Execution Workflow

### Step 1 — Log Triage

**P1 has no Python `logging`-module log file with structured per-fund events.** The log file
(`log_pipeline_*.log`) is stdout/stderr redirected by the `.bat` launcher — useful for top-level
block timings, unhandled-exception `[ERROR]` prints, and the cycle incidencias summary printed at
the end. **The authoritative per-fund audit trail is `ingestion_log` (DB).** Query it for all
INTER fires, WARN events, and step counts. The log file is a secondary source for timing and
catastrophic failures only.

Scan the latest `log_pipeline_*.log` for:

1. **Unhandled exceptions / crash prints** — lines containing `Traceback`, `Exception`, or `[ERROR]` from unhandled Python errors (these are NOT in `ingestion_log`).
2. **Block timing summary** — extract per-block elapsed times from the launcher output.
3. **Cycle incidencias summary** — the `--- RESUMEN DE INCIDENCIAS DEL CICLO ---` block at the end of the log, printed by the resumen step.

Then query the **canonical DB audit trail** for all structured events:

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

Compare event counts against the prior run's `RUN_START`/`RUN_SUMMARY` rows. Flag any count that regressed.

### Step 2 — DQ Issue Analysis & Reliability Controls

**Note on time scope:** `_finalize_data_quality_issues()` DELETE+INSERTs per ISIN each cycle. On full-universe runs, `FIX-DQ-STALE-SWEEP-1` also purges rows with `detected_at` before `_cycle_start_ts`. On partial runs, historical rows may survive. Always scope the query to the current cycle:

```sql
-- Current-cycle DQ issues only
SELECT check_code, level, COUNT(*) n FROM fund_data_quality_issues
WHERE DATE(detected_at) = date('now')
GROUP BY check_code, level ORDER BY n DESC;
```

- Group by `level` (WARN > INFERRED > MISSING).
- For each WARN group with `n > 5`, classify:
  - **Fixable** — root-cause bug in classifier or INTER rule.
  - **Stale value** — COALESCE preserved wrong value; need FORCE_REFRESH or re-run.
  - **Benign** — known sentinel or design choice.
- Surface the top 3 actionable WARNs for fixing.

**Reliability Control 1 — WRONG_DOC stale fund_master.** When WRONG_DOC is detected, `publish_fund` is skipped — the fund's `fund_master` row is never updated. Old classification and cost data persist and appear valid to P2/P3:
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

**Reliability Control 3 — Cost arbitration verdict distribution.** `NULL` vs `'BOTH_FAIL'` are not equivalent (`NULL` = never attempted; `'BOTH_FAIL'` = attempted, both paths failed):
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

**Reliability Control 4 — Family consistency residuals.** Post-correction families still with >1 Fund_Nature indicate a residual `_validate_family_consistency()` couldn't resolve:
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

### Step 3 — Process-Efficiency Pass

- Identify blocks re-running for funds already stable (no KIID change, no FORCE_REFRESH).
- Flag repeated master-load or FamilyBuilder runs with no new ISINs.
- Note WRONG_DOC / CACHED funds consuming unnecessary classifier time.
- Record estimated cycle overhead (count × average per-fund ms).

### Step 4 — Root-Cause Fixes

For each confirmed root-cause bug (from Steps 1–3 and §4 findings):

1. **Read the target file** before editing (P#3).
2. **Fix in the correct module** — classifier → `blocks/<block>.py`; normalization map → `classify_utils.py`; INTER rule → `pipeline.py`; KIID parsing → `kiid_parser.py` (P#7, R-1).
3. **AST validate** immediately after every Python edit (R-8) — see §9.
4. **Write regression tests** (R-7 — no `pipeline.py` / `core.io` imports in tests).
5. **Run full test suite** — must stay green — see §9.
6. **Commit** with tag `FIX-<SURFACE>-<N>` (e.g. `FIX-GEO-12`, `FIX-B6-HY-LOGGER`).

---

## 4. Interface Alignment & Consistency Audit

Run the benchmark audit tool (regenerates `benchmark_audit_findings.json`):
```batch
C:\data\envs\des\python.exe -X utf8 proyecto1/tools/audit_benchmark_consistency.py
```

Report counts for all 7 categories:

| Code | Check | Action threshold |
|------|-------|-----------------|
| B1 | Fund_Nature vs benchmark asset class (CRITICAL) | Any > 0 → investigate |
| B2 | Geography mismatch (fm vs benchmark) | > 10 → triage |
| B3 | Development_Status mismatch | > 5 → triage |
| B4 | Market_Cap mismatch | Ignore "All Cap" gaps (sentinel) |
| B5 | Credit_Quality mismatch | > 3 → triage |
| B6 | SRRI mismatch | > 5 → triage |
| B7 | Declared benchmark drift from historical | > 20 → triage |

For each non-trivial bucket, classify every conflict as:
- **Root-cause bug** → fix in the correct Python module (P#7), enter Step 4.
- **Stale value** → schedule FORCE_REFRESH or identify self-healing path.
- **Benign mismatch** → document in `_GEO_BENIGN_PAIRS` / `_BENIGN_PAIRS` in the audit tool.

---

## 5. Cross-Project Gap Analysis

**Objective:** assess whether P1-owned data (classification attributes, DQ signals, benchmark/family/cost/SRRI metadata) gives P2 and P3 everything they need; identify P1-side KPIs or attributes whose addition would raise P2 metric precision or P3 scoring reliability.

- **Map the consumed surface.** Enumerate P1 attributes P2/P3 actually read (`Fund_Nature`, `Credit_Quality`, `Geography`, `SRRI`, `Investment_Universe`, benchmark asset class, `In_Current_Universe`). Flag any with high NULL/sentinel rates, low classifier confidence, or granularity too coarse for downstream use.
- **Diagnose downstream impact.** For each weak attribute, name the concrete P2 metric or P3 decision it degrades.
- **Propose the enhancement.** State the fix precisely: new/refined P1 attribute, owning module, expected reliability gain, effort/risk. Distinguish "P1 should provide this" from "genuinely a P2/P3 concern".
- **Respect the architecture.** Flow is unidirectional P1 → P2 → P3. Do not propose P1 consuming P2/P3 outputs beyond the single bounded `srri_nav` feedback (see `AGENTS.md`).

---

## 6. Key Architecture Reminders

- **COALESCE (P#1):** A fix returning None changes nothing — return a concrete value or correct sentinel.
- **Generic signals (P#5):** No hardcoded fund names in classifiers. Use name patterns + KIID text patterns.
- **R-4:** INTER rules use effective values: `_X_eff = record.get("X") or _X_bd`. Never `record.get("X")` alone (CACHED funds may have None in record).
- **R-5:** `\b` fails between two letters (e.g., `EURHDG`). Use lookaheads for suffix patterns.
- **R-6:** Text inference: bounded window (~1500 chars) around keyword, not full KIID text.
- **Geography arbitration** (`pipeline.py`): KIID=Global + name specific → name wins; name is sub-region of KIID → name wins (`_is_name_subregion`). Otherwise KIID wins.
- **`_GEO_NEGATION_MARKERS`** (`classify_utils.py`): prefix strings that negate geography signals in KIID text (e.g., "residentes de", "fuera de", "excluyendo").
- **WRONG_DOC funds:** Excluded from reclassification each cycle. Stale DQ entries from a prior correct cycle are not current bugs.

---

## 7. Deliverable — Executive Audit Report

Emit in this order, short and table-driven:

1. **Executive Summary** — findings in ≤ 6 bullets.
2. **Critical Alerts** — B1 conflicts, top WARNs, any block-count regressions.
3. **Action Items** — table: item · step · severity · root cause · fix location.
4. **Efficiency & Gap Findings** — redundancy overhead (Step 3) + P1→P2/P3 attribute gaps (§5).
5. **Deferred Items** — open findings deferred; reason for each (stale value / benign / needs KIID redownload / complex). Recommended next action.

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
- **Fix in the correct module (P#7).** Classifier → `blocks/<block>.py`; normalization map → `classify_utils.py`; INTER rule → `pipeline.py`; KIID parsing → `kiid_parser.py`. SQL only for diagnostic SELECTs and FORCE_REFRESH triggers.
- **AST validate after every Python edit (R-8):**
  ```
  C:\data\envs\des\python.exe -c "import ast; ast.parse(open('file.py', encoding='utf-8').read()); print('AST OK')"
  ```
- **Tests must stay green (R-7 — no `pipeline.py` / `core.io` imports in tests):**
  ```
  C:\data\envs\des\python.exe -m pytest proyecto1/tests/ -q
  ```
- **COALESCE / graceful degradation:** a fix returning None changes nothing — return a concrete value or correct sentinel.
- **Write-on-correction (verified only):** If the user corrects any finding, factual claim, or reasoning during this audit, verify the correction first (read the code, query the DB, check the canonical doc). If confirmed correct, persist it to memory before the session ends. If wrong, explain and do not write. If unverifiable in context, flag it explicitly — do not persist unverified claims.

---

## 10. End-of-Session Memory Audit

Before ending this session, identify every operational insight corrected or confirmed during this audit that is not yet in memory, and write it now.

For each item:
1. Create or update the memory file (type `feedback` for agent-behavior corrections, `project` for operational facts).
2. Add or update the index line in `MEMORY.md`.
3. Link related entries with `[[name]]`.

Closing the session with uncaptured insight is non-compliant.
