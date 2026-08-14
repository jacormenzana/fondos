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

Python: `C:\Users\Administrador\anaconda3\envs\des\python.exe`.
If any asset is missing, report immediately before proceeding.

**Pre-flight — backlog scope:** Read the live backlog artifact before starting §3. List currently open P1 items. Focus all log-triage, DQ-analysis, and benchmark-audit steps on open items only — skip re-investigation of findings already Confirmed with evidence on record.

---

## 3. Execution Workflow

### Step 1 — Log Triage

Parse the latest `log_pipeline_*.log`. Extract and report:

1. **ERROR lines** — funds not persisted; count per block.
2. **WARNING lines** — semantic inconsistencies, DQ flags, INTER-rule fires; count + group by tag.
3. **Block counts** — `monetarios=N, rf_corto=N, rf_flexible=N, renta_variable=N, mixtos=N, alternativos=N, restantes=N`.
4. **Named signals** — BL_B6_HY_KIID fires, FamilyBuilder inconsistencies, WRONG_DOC hits, FORCE_REFRESH triggers.

Compare against prior run baseline. Flag any count that regressed.

### Step 2 — DQ Issue Analysis

Query the DB:
```sql
SELECT check_code, level, COUNT(*) n FROM fund_data_quality_issues
GROUP BY check_code, level ORDER BY n DESC;
```

- Group by `level` (WARN > INFERRED > MISSING).
- For each WARN group with `n > 5`, classify:
  - **Fixable** — root-cause bug in classifier or INTER rule.
  - **Stale value** — COALESCE preserved wrong value; need FORCE_REFRESH or re-run.
  - **Benign** — known sentinel or design choice.
- Surface the top 3 actionable WARNs for fixing.

### Step 3 — Process-Efficiency Pass

- Identify blocks re-running for funds already stable (no KIID change, no FORCE_REFRESH).
- Flag repeated master-load or FamilyBuilder runs with no new ISINs.
- Note WRONG_DOC / CACHED funds consuming unnecessary classifier time.
- Record estimated cycle overhead (count × average per-fund ms).

### Step 4 — Root-Cause Fixes

For each confirmed root-cause bug (from Steps 1–2 and §4 findings):

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
C:\Users\Administrador\anaconda3\envs\des\python.exe -X utf8 proyecto1/tools/audit_benchmark_consistency.py
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
  C:\Users\Administrador\anaconda3\envs\des\python.exe -c "import ast; ast.parse(open('file.py', encoding='utf-8').read()); print('AST OK')"
  ```
- **Tests must stay green (R-7 — no `pipeline.py` / `core.io` imports in tests):**
  ```
  C:\Users\Administrador\anaconda3\envs\des\python.exe -m pytest proyecto1/tests/ -q
  ```
- **COALESCE / graceful degradation:** a fix returning None changes nothing — return a concrete value or correct sentinel.
