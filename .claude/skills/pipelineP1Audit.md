# Skill: pipelineP1Audit
**Description:** Full diagnostic audit of a P1 classification pipeline run — log triage, DQ issue analysis, benchmark-consistency audit, root-cause fixes, and regression tests. Produces permanent code-level fixes (never symptomatic patches). Also invoked as `pipelineP1Audit`.

---

## 1. Role & Constraints

**Role:** Lead Data Pipeline Architect & QA Engineer.  
**Language:** English. **Tone:** Ultra-executive. **Format:** Short phrases, bullet points, zero filler.  
**Non-negotiable:** All fixes must satisfy the 11 design principles (P#1–P#11) and R-1..R-8 in `doc/reglas/`. Root cause only — no ad-hoc SQL to patch classification data.

---

## 2. Assets — locate immediately on invocation

| Asset | Pattern | Location |
|-------|---------|----------|
| Pipeline log | `log_pipeline_*.log` (latest) | `proyecto1/log/` |
| P1 export | `p1_export_*.xlsx` (latest) | `out/export/` |
| Benchmark audit | `benchmark_audit_findings.json` (latest) | `out/audit/` |
| Audit tool | `audit_benchmark_consistency.py` | `proyecto1/tools/` |
| DB | `fondos.sqlite` | `db/` |

If any asset is missing, say so immediately before proceeding.

---

## 3. Execution Workflow

### Step 1 — Log Triage

Parse the latest `log_pipeline_*.log`. Extract and report:

1. **ERROR lines** — funds not persisted (exit immediately on crash; count per block).
2. **WARNING lines** — semantic inconsistencies, DQ flags, INTER-rule fires (count + group by tag).
3. **Block counts** — `monetarios=N, rf_corto=N, rf_flexible=N, renta_variable=N, mixtos=N, alternativos=N, restantes=N`.
4. **Named signals** — BL_B6_HY_KIID fires, FamilyBuilder inconsistencies, WRONG_DOC hits, FORCE_REFRESH triggers.

Compare against prior run baseline (if known). Flag any count that regressed.

### Step 2 — DQ Issue Analysis

Query the DB:
```sql
SELECT check_code, level, COUNT(*) n FROM fund_data_quality_issues
GROUP BY check_code, level ORDER BY n DESC;
```

- Group by `level` (WARN > INFERRED > MISSING).
- For each WARN group with `n > 5`, identify whether it is:
  - **Fixable** (root-cause bug in a classifier or INTER rule)
  - **Stale value** (COALESCE preserved a wrong value; need FORCE_REFRESH or pipeline re-run)
  - **Benign** (known sentinel / design choice)
- Surface the top 3 actionable WARNs for fixing.

### Step 3 — Benchmark Consistency Audit

Run the audit tool (regenerates `benchmark_audit_findings.json`):
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
- **Root-cause bug** → fix in the correct Python module (P#7)
- **Stale value** → schedule FORCE_REFRESH or identify self-healing path
- **Benign mismatch** → document in `_GEO_BENIGN_PAIRS` / `_BENIGN_PAIRS` in the audit tool

### Step 4 — Root Cause Fixes

For each confirmed root-cause bug:

1. **Read the target file** before editing (P#3).
2. **Fix in the correct module** — classifier bug → `blocks/<block>.py`; normalization map → `classify_utils.py`; arbitration logic → `pipeline.py`; KIID parsing → `kiid_parser.py` (P#7, R-1).
3. **AST validate** immediately after every Python edit (R-8):
   ```
   python -c "import ast; ast.parse(open('file.py', encoding='utf-8').read()); print('AST OK')"
   ```
4. **Write regression tests** (R-7 — no imports of `pipeline.py` or `core.io` in tests).
5. **Run full test suite** — must stay green:
   ```
   C:\Users\Administrador\anaconda3\envs\des\python.exe -m pytest proyecto1/tests/ -q
   ```
6. **Commit** with tag pattern `FIX-<SURFACE>-<N>` (e.g. `FIX-GEO-12`, `FIX-B6-HY-LOGGER`).

### Step 5 — Session Summary

Report at end:
- Fixes applied (tag + file + what changed)
- Tests: N passed, N skipped (delta from session start)
- Open findings deferred (reason for each: stale value / benign / needs KIID redownload / complex)
- Recommended next action (launch pipeline / FORCE_REFRESH ISINs / further triage)

---

## 4. Key Architecture Reminders

- **COALESCE (P#1):** New non-NULL value wins; NULL preserves stale DB value. A fix that returns None fixes nothing — must return a concrete value.
- **Generic signals only (P#5):** No hardcoded fund names in classifiers. Use name patterns + KIID text patterns.
- **R-4:** INTER rules use effective values: `_X_eff = record.get("X") or _X_bd`. Never `record.get("X")` alone.
- **R-5:** `\b` fails between two letters (e.g., `EURHDG`). Use lookaheads for suffix patterns.
- **R-6:** Text inference: bounded window (~1500 chars) around keyword, not full KIID text.
- **`detect_geography()` arbitration** (`pipeline.py`): KIID=Global + name specific → name wins; name is sub-region of KIID geography → name wins (`_is_name_subregion`). Otherwise KIID wins over name.
- **`_GEO_NEGATION_MARKERS`** (`classify_utils.py`): list of prefix strings that negate geography signals in KIID text (e.g., "residentes de", "fuera de", "excluyendo").
- **WRONG_DOC funds:** Excluded from reclassification each cycle. DQ entries may be stale from prior correct cycle — do not treat stale DQ as current bug.

---

## 5. Cross-Project Gap Analysis (P1 → P2 / P3 data sufficiency)

**Objective:** assess whether the data surface that **P1 owns and persists** (classification
attributes, data-quality signals, benchmark/family/cost/SRRI metadata) gives the downstream
projects everything they need, and identify **P1-side KPIs or attributes** — new or hardened —
whose addition would raise **P2 metric/indicator precision and reliability** or improve **P3
regime-scoring and portfolio decisions**. This is about what P1 can *supply upstream*, not about
metrics that are P2/P3's own responsibility.

- **Map the consumed surface.** Enumerate the P1 attributes/columns P2 and P3 actually read
  (e.g. `Fund_Nature`, `Credit_Quality`, `Geography`, `SRRI`, `Investment_Universe`, benchmark
  asset class, `In_Current_Universe`). Flag any with high NULL/sentinel rates, low classifier
  confidence, or granularity too coarse for the downstream use.
- **Diagnose the downstream impact.** For each weak attribute, name the concrete P2 metric or P3
  decision it degrades (e.g. missing `Credit_Quality` → weaker P3 Defensiva hard-filter; coarse
  `Geography` → noisier P2 macro-sensitivity attribution).
- **Propose the enhancement.** State the fix precisely: the new/refined P1 KPI or attribute, the
  P1 module that would own it, the expected reliability/precision gain, and the effort/risk.
  Distinguish "P1 should provide this" from "genuinely a P2/P3 concern — out of scope here".
- **Respect the architecture.** Flow is unidirectional P1 → P2 → P3; do not propose P1 consuming
  P2/P3 outputs except the single bounded, accepted `srri_nav` feedback (see `AGENTS.md`).

## 6. Integrated Backlog Artifact — automated maintenance

**Objective:** keep the shared **integrated incident-backlog artifact** (the cross-P1/P2/P3 action-
item register) current as an automatic by-product of every audit, so it always reflects the live
state of open work without manual bookkeeping. Locate the artifact on invocation; if its path is
ambiguous, ask the user before writing.

- **Reconcile against this session's findings.** Advance in-progress items (status change + the
  evidence/measurement that moved them); add newly discovered items (root-cause hypothesis, owning
  module, severity, affected ISIN count); close items whose root cause is resolved, linking the fix
  commit/tag.
- **Refresh ancillary sections when warranted** — open-vs-closed rollups, summary counts, next-action
  recommendations, and dates (convert relative dates to absolute).
- **Preserve structure and history.** Append or annotate in place; never rewrite, reorder, or drop
  prior entries. The artifact is an auditable record, not a scratchpad.

