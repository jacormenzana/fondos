# Backlog P1 — Execution Plan v4.2

**Date:** 2026-06-29
**Version:** v4.2
**Schema:** v20 (`config.py:57` `SCHEMA_VERSION="v20"`)
**Supersedes:** `ESTADO_BACKLOG_v4_1.md` (2026-06-27)
**Delta basis:** Session 2026-06-28 — cost bottleneck analysis (FIX-P1-X through FIX-P1-AB); BL-BENCH-NORM reintegrated (was omitted from FIX_SUMMARY_BOTTLENECKS.md).

---

## Execution order

| # | Item | Priority | Estimate | Deps |
|---|---|---|---|---|
| 1 | BL-SRRI-GUARD-FULL | P0 — live bug | 1h | none |
| 2 | PIPELINE-RUN-V20 | P1 — blocker | pipeline time | none |
| 3 | BL-53/54 | P1B | 3-4h | none |
| 4 | BL-INTER3-WARN | P1B | 2-3h + modelling | none |
| 5 | BL-COST-ACI-RHP | P2 | TBD | PIPELINE-RUN-V20 |
| 6 | BL-COST-REGRESS | P2 | 2-3h | none |
| 7 | BL-BENCH-NORM | P2 | 4-6h | PIPELINE-RUN-V20 (re-measure) |
| 8 | P3 closure sprint | P3 | TBD | PIPELINE-RUN-V20 |
| 9 | P4 triage | P4 | TBD | none |

---

## P0 — Live production bug

### BL-SRRI-GUARD-FULL · `PENDING` · `pipeline.py`

**Problem:** `_safe_scalar()` does not exist (0 occurrences repo-wide). Crash path intact at `pipeline.py:907-908` — `'>=' not supported between dict and int` when a DDF PDF returns a dict for SRRI.
- Guard at `pipeline.py:607-611` only protects `_srri_for_classify`, not the root assignment.
- `pipeline.py:817` → `"SRRI": parsed.get("SRRI")` — raw, unguarded.
- Dict→int coercion inside `detect_profile_from_srri` (`classify_utils.py:2279-2282`) does NOT cover this path.

**Fix:**
1. Add `_safe_scalar(v)` — extract scalar if dict, int-coerce, else return None.
2. Apply at `pipeline.py:817` (root assignment, not `:907` consumer).

**Verification ISINs:** LU1951199022, LU1951200648, LU2095320268 — assert SRRI persists as int or NULL after re-run.

**DoD:** 3 ISINs produce int SRRI; no crash on dict-valued SRRI input.
**Estimate:** 1h. No dependencies.

---

## P1A — Blocker

### PIPELINE-RUN-V20 · `PENDING` · BLOCKER

**Purpose:** apply Round 1+2 ACI_RHP fixes (FIX-P1-M through FIX-P1-W + X/Y/Z/AA/AB) to DB; capture v20 execution log + `dla_inv` output.

**Unlocks:**
- All P3 items (BL-DLA-2-RECALIBRACIÓN, BL-DLA-2-DIAG, BL-50, BL-COST-5).
- BL-COST-ACI-RHP re-count (current 24 residuals figure is pre-pipeline).
- BL-BENCH-NORM re-measurement on v20 corpus.

**No code changes required.** Run pipeline, save log.

---

## P1B — Architectural debt

### BL-53/54 · `PENDING` · `classify_utils.py` + SQL

**Problem:** `SECTOR_FOCUS_TRANSLATION_MAP` targets Spanish (`classify_utils.py:2022-2056`). Contradicts v20 §8 and §2A.1 #6 (English, GICS-EN).

**Target value set (EN, canonical):**
`Technology & Innovation` · `Healthcare & Life Sciences` · `Energy & Resources` · `Financial Services` · `Consumer` · `Materials & Mining` · `Utilities & Environment` · `Real Assets`

**Fix (R-2 — three parts):**
1. **Pipeline:** invert/replace `SECTOR_FOCUS_TRANSLATION_MAP` → EN-target labels; classifier emits EN.
2. **Migration:** SQL remap existing ES values → EN (`LIKE`/`TRIM`, R-9).
3. **COALESCE review** in `publish_fund()` — confirm CACHED path overwrites. Populate iff `Investment_Focus='Sector'`, else NULL (INTER-6 / INTER-15).
4. **Doc-drift fix:** `classify_utils.py:2022` comment `idioma objetivo: español` — fix at zero extra cost.

**DoD:** classifier emits EN-only; all existing ES rows migrated; COALESCE verified.
**Estimate:** 3-4h.

---

### BL-INTER3-WARN · `PENDING` · `classify_utils.py` + `pipeline.py`

**Problem:** INTER-3 auto-remaps Profile from SRRI in two independent places:
- `validate_profile_srri()` `classify_utils.py:2692-2715` — remaps `Conservador & SRRI≥5` and `Agresivo & SRRI≤4`.
- `pipeline.py:905-909` — independently remaps `Conservador & SRRI≥5 → Dinámico`.

Note: `Agresivo` is live (`_assign_profile_from_srri` `classify_utils.py:2687-2688`, SRRI=7) — not dead code.

**Target:** warnings-only. `Profile = f(SRRI, Fund_Nature)`. No auto-correct.

**Fix:**
1. Strip remap branches in `validate_profile_srri()` — return WARNING tuples only, never a new Profile.
2. Remove `pipeline.py:905-909`.
3. Define the `f(SRRI, Fund_Nature)` band table (open modelling task).

**DoD:** no Profile auto-remap on pipeline run; warnings emitted; band table defined.
**Estimate:** 2-3h + modelling.

---

## P2 — Feature / data

### BL-COST-ACI-RHP · `PENDING` · `cost_table_parser.py`

**Context:** 10 fixes (Round 1+2, FIX-P1-M through FIX-P1-W) recovered ~668/1,122 NULL funds. Bottleneck session (2026-06-28) added FIX-P1-X through FIX-P1-AB covering 16 more funds (11 fixed, 5 non-actionable). 24 hard residuals remain from Round 2 truly-null subset.

**Open sub-items:**

| Sub-group | Funds | Status |
|---|---|---|
| ACI Sub-group A (FIX-P1-Z a/b/c) | 4 | Fixed |
| ACI Sub-group B (SSGA/Alger — no OT table) | 5 | Non-actionable |
| ACI Sub-group C (FIX-P1-AA + FIX-P1-AB) | 2 | Fixed |
| ACI Remaining | **16** | **PENDING — undiagnosed** |
| swap_mgmt_oper Group C (FIX-P1-X) | 5 | Fixed |
| swap_mgmt_oper Remaining | **10** | **PENDING — undiagnosed** |
| Round 2 hard residuals | **24** | **PENDING — post pipeline run** |

**Next step:** run full corpus diagnostic (`diag_cost_extraction.py`); classify 26 undiagnosed funds; attempt fix or declare hard-unextractable with root cause per ISIN.

**DoD:** ≤50 residuals corpus-wide OR all remaining classified as hard-unextractable with root cause logged per ISIN.
**Estimate:** TBD (depends on residual pattern).
**Dep:** PIPELINE-RUN-V20 (for accurate corpus re-count).

---

### BL-COST-REGRESS · `PENDING` · `cost_table_parser.py`

**Problem:** Round 2 shipped 7 interacting fixes. "0/50 regressions" was a spot-check on a sample, not a corpus guard. Risk of silent regression on future cost fix iterations.

**Fix:** implement golden-ISIN test set with expected ACI_RHP values per ISIN.
- Minimum 50 ISINs covering all fix categories: DLA2, plain text, mega-cell, IE/Irish, date-RHP.
- Execute on every `cost_table_parser.py` change.

**DoD:** automated harness; 0 regressions on golden set on each PR touching cost parsing.
**Estimate:** 2-3h. No dependencies.

---

### BL-BENCH-NORM · `PENDING` · `classify_utils.py` + `kiid_parser.py`

**Done (parser-side):** `_trim_benchmark` (`kiid_parser.py:1633`, cap `[:120]` at `:1704`) + `_BENCH_TERMINATORS`. Decontamination only.

**NOT done (core deliverable):**
- `normalize_benchmark()` — does not exist (0 occurrences).
- `Benchmark_Canonical` column — does not exist (0 occurrences).
- P3 group-by / exposure analysis is **blocked** without canonical labels.

**Legacy metrics (v3.9, unverified at v20):** 522 unique `Benchmark_Declared` → ~36 families.

**Fix:**
1. Re-measure unique `Benchmark_Declared` on v20 corpus after pipeline run.
2. Implement `normalize_benchmark(raw) → canonical_label` (regex per family).
3. Add `Benchmark_Canonical` column (or normalize in-situ). Acceptance: ≤50 unique labels, 0 truncation patterns.

**DoD:** `Benchmark_Canonical` populated for all funds with a declared benchmark; ≤50 unique canonical values; 0 truncation artifacts.
**Estimate:** 4-6h post re-measurement.
**Dep:** PIPELINE-RUN-V20 (for accurate v20 corpus metrics).

---

### BL-55 · `FROZEN` · `EXIT_INFERRED_ZERO`

Frozen pending BL-COST-5 re-scope after pipeline run. `EXIT_INFERRED_ZERO` flag absent (0 occurrences). Inference rule undefined. **Do not start.**

---

### BL-DLA-3 / BL-DLA-3-DIAG · `DEFERRED`

Deferred until P2/P3 consumer docs require Cat.3 matrices. No active planning.

---

## P3 — Gated on PIPELINE-RUN-V20

All items below require v20 execution log + fresh `dla_inv` output.

| Item | Blocker | Action |
|---|---|---|
| **BL-DLA-2-RECALIBRACIÓN** | v20 `dla_inv` + run log | Recompute Exit/Entry/OC NULL deltas; re-issue Go/No-Go. Legacy est. ~600-800 funds {Cat.2+Cat.3}; Exit_Fee NULL ~110→~50 — **invalid baseline, do not use**. |
| **BL-DLA-2-DIAG** | Reliable `dla_inv` (logic-fix done) + log | Q-DLA-04/05 on v20 data. |
| **BL-50** (Universe→Geography inverse) | No v3.7 spec text; partial coverage only | Verify vs INTER-6 (`classify_utils.py:2787`) + BL-52 (`:2866`); confirm closure or define delta. |
| **BL-COST-5** (OC/ACI mismatch) | "~328 funds" baseline stale (declared obsolete in v4.1 §2) | Re-measure post pipeline run; re-issue with v20 KPIs; then schedule architectural review. |

---

## P4 — Not yet traced (triage required)

No code anchor confirmed. **Do not schedule without a prior grep + scope check.**

`BL-58` (Lifecycle/Retirement constants) · `BL-47-ext` (SFDR Art.8 defensive default) · `BL-59` (Restantes majority edge case) · `BL-60` (bipartite SRRI tie) · `BL-61` (root-cause meta-procedure) · `BL-51 Problema B` (cap/floor schema) · `BL-DLA-RESTANTES-1` (EM Debt OICVM detector) · `BL-DLA-C3-EXCL`

**Action per item:** grep owning module → confirm code anchor → assign estimate → schedule.

---

## Doc-drift defects (fix on next touch of each file)

| Location | Defect |
|---|---|
| `classify_utils.py:2022` (and `:2064`) | `idioma objetivo: español` — contradicts EN target. **Covered by BL-53/54.** |
| `io.py:46` | Comment "Sub-fase 1B: desactivado por defecto" while `DLA_ENABLED=True`. |
| `io.py:55` | Comment "Sub-fase 2B: desactivado" while `DLA_TABLE_SERIALIZATION_ENABLED=True`. |

---

## Definition of done (per change)

Read-before-edit → surgical `str_replace` → AST parse + grep changed lines → control SQL vs baseline → single-line `python -X utf8` commands. R-2 for any persisted-attribute change (pipeline + migration + COALESCE). No "100% solved" without corpus-scale (3,205) validation. Log every correction with ISIN.

---

**End — v4.2. Schema v20. Supersedes v4.1.**
