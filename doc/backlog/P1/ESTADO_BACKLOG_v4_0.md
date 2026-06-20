# Backlog P1 — Consolidated State v4.0 (self-contained)

**Date:** 2026-06-17
**Version:** v4.0
**Schema:** v20 (`config.py:57` `SCHEMA_VERSION="v20"`)
**Supersedes:** `ESTADO_BACKLOG_APR2026_v3_9.md` (2026-05-18, schema v17)
**Basis:** Gap analysis of v3.9 backlog against live modules — `pipeline.py`, `classify_utils.py`, `dla2_decision_diag.py`, `config.py`, `fund_characterizer.py`, `kiid_parser.py`, `dla_table_serializer.py`, `dla_extractor.py`, `io.py` (read 2026-06-17).

---

## 0. Audit context

- v3.9 was schema **v17**; codebase is now **v20**. Three generations (v18→v19→v20) resolved or obsoleted most legacy items.
- Live feature flags: `PRIIPS_COST_EXTRACTION_ENABLED=True` (`config.py:85`), `DLA2_ARBITRATION_ENABLED=True` (`config.py:94`), `DLA_ENABLED=True` (`io.py:46`), `DLA_TABLE_SERIALIZATION_ENABLED=True` (`io.py:55`).
- **Logs caveat:** no v20 execution log provided. All count/KPI verdicts are code-grounded only. Items needing telemetry confirmation are tagged `(LOGS REQUIRED)` and listed under PENDING.
- Legacy metrics below are quoted as **[legacy v3.9, unverified at v20]** unless re-grounded against code.

---

## 1. [RESOLVED]

| Item | Evidence (code) | Note |
|---|---|---|
| **BL-DLA-2-LOGIC-FIX** | `dla2_decision_diag.py:799-806` `ATTRIBUTE_CATEGORY_MAPPING`; `fase6_atributo_vs_cat()` v1.1 (`:809`, header `:790-791`) groups by per-attribute category, not `cat_max`. | Matches v3.9 §7 spec exactly. `cat_max` retained backward-compat only (`:315-317`). |
| **BL-DLA-1 Sub-fase 1D** | `DLA_ENABLED=True` (`io.py:46`); delegation `io.py:121-123` → `dla_extractor.extract_text_dla_aware()`. | Global deployment active. Stale comment defect (§5). |
| **BL-49** (Currency_Hedged from KIID) | `detect_currency_hedged_from_kiid()` `classify_utils.py:2352`; characterizer fallback `fund_characterizer.py:33-38,94`. | **Caveat:** column scheduled for DELETE in v20 (redundant w/ `Hedging_Policy`). Code complete; value now low. |
| **BL-48-ext** (MMF Family) | `pipeline.py:900-903` → `Family="Money Market"`, `Subtype` = LVNAV/VNAV/CNAV. | — |
| **BL-57/65b** (Income Oriented EN) | `FAMILY_INCOME_ORIENTED="Income Oriented"` `classify_utils.py:255`; ES→EN corrected per BL-57 v3. | Principle #8 honored. |
| **BL-56** (centralized post-characterize normalization) | Inline block emission removed, centralized `classify_utils.py:126-128`. | — |

---

## 2. [OBSOLETE]

| Item | Reason |
|---|---|
| **BL-PROFILE-SRRI-FULL** | Proposed hard SRRI→Profile remap of ~2,110 funds. **Conflicts with the v20 decision** `Profile = f(SRRI, Fund_Nature)`, warnings-only (V6 INTER-3 / INTEGRATED_SPEC §2A.1 #2). Mass remap is now a disallowed optimization path. Replaced by the INTER-3 warnings-only redesign (see PENDING §3). |
| **BL-DLA-2 (Phase 2 — serialize Cat.1+2 for fee-NULL reduction)** | `dla_table_serializer.py` is now **v2 (BL-COST/DLA2 fix)**; `serialize_tables()` (`io.py:568`) feeds `_fed_text_for_cost` for **cost arbitration**, not Entry/Exit/OC NULL reduction. Original framing dead; superseded by INTEGRATED_SPEC Job B (`DLA2_ARBITRATION_ENABLED=True`). Module is live, but under the new architecture. |
| **INTER-4 (Fund_Nature → Type)** | Retired in v20, no-op stub `classify_utils.py:2719-2734`. `Type→Vehicle_Structure` makes the asset-class constraint meaningless. Any legacy item assuming INTER-4 is void. INTER-5 (Nature→Family) retained. |
| **BL-DLA-2-RECALIBRACIÓN (as scoped in v3.9)** | KPI thresholds were defined against the corrupt `dla_inv.csv` baseline. Logic-fix corrected the diagnostic; the recalibration must now be re-derived from a fresh v20 run, not the v3.9 figures. Re-issued under PENDING §3 (LOGS REQUIRED). |

---

## 3. [PENDING] — enriched with current code constraints

### P0 — Live production bug

#### BL-SRRI-GUARD-FULL — `[PENDING]` · ALTA · `pipeline.py`
- **Current state:** `_safe_scalar()` **does not exist** (0 occurrences, repo-wide).
- **Constraint (verified):** guard at `pipeline.py:607-611` only protects `_srri_for_classify`. Assignment to `fund_master_record["SRRI"]` is raw: `pipeline.py:817 → "SRRI": parsed.get("SRRI")`. Crash path intact at `pipeline.py:907-908` (`_srri_val >= 5`) → `'>=' not supported between dict and int` when a DDF PDF returns a dict.
- **Partial mitigation only:** dict→int coercion exists *inside* `detect_profile_from_srri` (`classify_utils.py:2279-2282`) — does not cover the `fund_master_record` path.
- **Next step:** add `_safe_scalar(v)` (extract scalar if dict, int-coerce, else None); apply at **`pipeline.py:817`** (root, not the `:907` consumer). Re-run affected ISINs `LU1951199022`, `LU1951200648`, `LU2095320268`; assert SRRI persists as int or NULL.
- **Estimate:** 1h. No dependencies.

### P1 — Architectural debt (re-scoped)

#### BL-INTER3-WARN (replaces BL-PROFILE-SRRI-FULL) — `[PENDING]` · ALTA · `classify_utils.py` + `pipeline.py`
- **Current state:** INTER-3 **still remaps** Profile from SRRI — the opposite of the v20 target.
  - `validate_profile_srri()` `classify_utils.py:2692-2715` recalculates on `Conservador & SRRI≥5` and `Agresivo & SRRI≤4`.
  - `pipeline.py:905-909` independently remaps `Conservador & SRRI≥5 → Dinámico`.
- **Target:** warnings-only; `Profile = f(SRRI, Fund_Nature)`. No auto-correct.
- **Note:** `Agresivo` is now a live value (`_assign_profile_from_srri` `classify_utils.py:2687-2688`, SRRI=7) — not dead code.
- **Next step:** strip the remap branches in `validate_profile_srri` (return WARNING tuples, never a new Profile); remove `pipeline.py:905-909`. Define the `f(SRRI, Fund_Nature)` band table (INTER-3 modelling task, open).
- **Estimate:** 2-3h + modelling.

#### BL-53/54 — Sector_Focus — `[PENDING]` · ⚠️ LANGUAGE OVERRIDE · `classify_utils.py`
- **DIRECTIVE CHANGE (v4.0):** Sector_Focus must be developed and resolved in **English (GICS-EN)**. This **overrides the legacy v3.9 requirement (GICS-ES)**. The previously RESOLVED Spanish implementation is hereby reclassified PENDING for re-development.
- **Current state (drift):** `SECTOR_FOCUS_TRANSLATION_MAP` targets **Spanish** (`classify_utils.py:2022-2056`, header comment `:2022` `idioma objetivo: español`). This contradicts v20 §8 (Sector_Focus → English) and §2A.1 #6.
- **Target value set (English, canonical, v20 §2A.1 #6):** `Technology & Innovation; Healthcare & Life Sciences; Energy & Resources; Financial Services; Consumer; Materials & Mining; Utilities & Environment; Real Assets`.
- **Next step (R-2, three parts):**
  1. **Pipeline:** invert/replace `SECTOR_FOCUS_TRANSLATION_MAP` to EN-target; align labels to the v20 set; classifier emits EN.
  2. **Migration:** SQL remap of existing ES values → EN (`LIKE`/`TRIM`, R-9).
  3. **COALESCE review** in `publish_fund()`; confirm CACHED path overwrites.
  - Populate iff `Investment_Focus='Sector'`, else NULL (INTER-6 / INTER-15).
  - Fix the `idioma objetivo: español` comment at `classify_utils.py:2022`.
- **Estimate:** 3-4h.

### P2 — Feature / data

#### BL-BENCH-NORM — `[PENDING]` · MEDIA · `classify_utils.py` (+ `kiid_parser.py`)
- **Done (different approach):** parser-side de-contamination — `_trim_benchmark` (`kiid_parser.py:1633`, cap `[:120]` at `:1704`) + `_BENCH_TERMINATORS` (BL-38-v20). Extraction coverage 47.5%→~65% [legacy v3.9]; "18 residual benchmarks" resolved.
- **NOT done (core deliverable):** canonical normalization. No `normalize_benchmark()`, no `Benchmark_Canonical` (0 occurrences). Only `detect_benchmark_type()` (`classify_utils.py:2256`, different purpose).
- **Constraint:** P3 group-by / exposure analysis is blocked without canonical labels.
- **Metrics [legacy v3.9, unverified at v20]:** 522 unique `Benchmark_Declared` → ~36 families (MSCI_ACWI=360, MSCI_WORLD=186, MSCI_EUROPE=143, MSCI_EM=132, SP500=78, BBG_GLOBAL_AGGREGATE=63, ICE_BOFA_HY=62…).
- **Next step:** implement `normalize_benchmark(raw)→canonical_label` (regex per family) in `classify_utils.py`; add `Benchmark_Canonical` (or normalize in-situ); apply post-extraction. Acceptance: ≤50 unique, 0 truncation pattern. **Re-measure 522/~254 against v20 first** (figures predate three schema bumps).
- **Estimate:** 4-6h. No architectural conflict.

#### BL-55 — Exit_Fee_Pct=0.00 / `EXIT_INFERRED_ZERO` — `[PENDING]` · BAJA
- **Current state:** flag absent (0 occurrences). Not implemented.
- **Next step:** define inference rule + sentinel flag in cost path; defer behind cost-arbitration closure (BL-COST-5).

#### BL-DLA-3 / BL-DLA-3-DIAG — `[PENDING]` · BAJA
- Deferred by design until P2/P3 consumer docs require Cat.3 matrices. No active planning.

### P3 — LOGS REQUIRED (cannot close from code)

| Item | Blocker | Action |
|---|---|---|
| **BL-DLA-2-RECALIBRACIÓN** (re-scoped) | Needs fresh v20 `dla_inv` + run log. | Run v20 pipeline; recompute Exit/Entry/OC NULL deltas; re-issue Go/No-Go. Legacy est. ~600-800 funds {Cat.2+Cat.3}; Exit_Fee NULL ~110→~50 [legacy, invalid baseline]. |
| **BL-DLA-2-DIAG** | Depends on reliable `dla_inv` (logic-fix done) + log. | Q-DLA-04/05 on v20 data. |
| **BL-50** (Universe→Geography inverse) | No v3.7 spec text; partial coverage only. | Verify against INTER-6 (`classify_utils.py:2787`) + BL-52 (`:2866`); confirm closure or define delta. |

### P4 — NOT TRACED (triage required)
Out of provided module scope or no specific code anchor — **do not assume status**:
`BL-58` (Lifecycle/Retirement constants) · `BL-47-ext` (SFDR Art.8 defensive default) · `BL-59` (Restantes majority edge case) · `BL-60` (bipartite SRRI tie) · `BL-61` (root-cause meta-procedure) · `BL-51 Problema B` (cap/floor schema) · `BL-DLA-RESTANTES-1` (EM Debt OICVM detector) · `BL-DLA-C3-EXCL`.
**Action:** per-item grep / scope the owning module before scheduling.

---

## 4. Cost-arbitration / Job B (context, not a v3.9 item)
DLA2 dual-strategy cost arbitration is **DELIVERED and active** (INTEGRATED_SPEC v20 §9): `DLA2_ARBITRATION_ENABLED=True`, schema v20 metadata columns present, serializer v2 feeding cost path. Open downstream: **BL-COST-5** (OC/ACI mismatch ~328 funds) — dedicated architectural review pending.

---

## 5. Doc-drift defects (sweep on next touch)
| Location | Defect |
|---|---|
| `classify_utils.py:2022` (and `:2064`) | `idioma objetivo: español` for Sector_Focus / TYPE_TRANSLATION_MAP — contradicts EN target. |
| `io.py:46` | Comment "Sub-fase 1B: desactivado por defecto" while `DLA_ENABLED=True`. |
| `io.py:55` | Comment "Sub-fase 2B: desactivado" while `DLA_TABLE_SERIALIZATION_ENABLED=True`. |

---

## 6. Execution order (recommended)
1. **BL-SRRI-GUARD-FULL** — live bug, 1h, no deps. Ship first.
2. **BL-53/54 (EN override)** — pipeline + migration + COALESCE (R-2), 3-4h.
3. **BL-BENCH-NORM** — canonical `normalize_benchmark()`, 4-6h (re-measure metrics on v20).
4. **BL-INTER3-WARN** — strip Profile-from-SRRI remap; model bands, 2-3h.
5. **Telemetry run** — v20 full pipeline + control SQL → close P3 (LOGS REQUIRED) items; regenerate the §7 regression baseline.
6. **Doc-drift sweep** — 3 stale comments (§5).

---

## 7. Definition of done (per change)
Read-before-edit → surgical `str_replace` → AST parse + grep changed lines → control SQL vs baseline → single-line `python -X utf8` commands. R-2 for any persisted-attribute change (pipeline + migration + COALESCE). No "100% solved" without corpus-scale (3,205) validation. Log every correction with ISIN.

---
**End — v4.0 (self-contained). Schema v20.**
