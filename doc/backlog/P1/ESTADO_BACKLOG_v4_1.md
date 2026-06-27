# Backlog P1 — Consolidated State v4.1 (self-contained)

**Date:** 2026-06-27
**Version:** v4.1
**Schema:** v20 (`config.py:57` `SCHEMA_VERSION="v20"`)
**Supersedes:** `ESTADO_BACKLOG_v4_0.md` (2026-06-17, schema v20)
**Delta basis:** Backlog grooming 2026-06-27 against commits `1d47f6b`, `41c3a9c`, `60871bd` (2026-06-20 → 2026-06-27) and `cost_table_parser.py` ACI_RHP fix sessions (Round 1 + Round 2, 2026-06-27).

---

## 0. Audit context

- Schema remains **v20**. No schema bump since v4.0.
- Live feature flags unchanged: `PRIIPS_COST_EXTRACTION_ENABLED=True`, `DLA2_ARBITRATION_ENABLED=True`, `DLA_ENABLED=True`, `DLA_TABLE_SERIALIZATION_ENABLED=True`.
- **Key delta since v4.0:** 10 fixes shipped in `cost_table_parser.py` (FIX-P1-M through FIX-P1-W + deduplication fix) recovering ~62% of 1,122 ACI_RHP-NULL funds. Full pipeline run pending — DB not yet updated. BL-COST-5 baseline declared stale.
- **Logs caveat** (inherited): no v20 execution log produced yet. P3 items remain gated.

---

## 1. [RESOLVED]

| Item | Evidence (code) | Note |
|---|---|---|
| **BL-DLA-2-LOGIC-FIX** | `dla2_decision_diag.py:799-806` `ATTRIBUTE_CATEGORY_MAPPING`; `fase6_atributo_vs_cat()` v1.1 (`:809`) groups by per-attribute category, not `cat_max`. | Matches v3.9 §7 spec exactly. `cat_max` retained backward-compat only (`:315-317`). |
| **BL-DLA-1 Sub-fase 1D** | `DLA_ENABLED=True` (`io.py:46`); delegation `io.py:121-123` → `dla_extractor.extract_text_dla_aware()`. | Global deployment active. Stale comment defect (§5). |
| **BL-49** (Currency_Hedged from KIID) | `detect_currency_hedged_from_kiid()` `classify_utils.py:2352`; characterizer fallback `fund_characterizer.py:33-38,94`. | Column scheduled for DELETE in v20 (redundant w/ `Hedging_Policy`). Code complete; value now low. |
| **BL-48-ext** (MMF Family) | `pipeline.py:900-903` → `Family="Money Market"`, `Subtype` = LVNAV/VNAV/CNAV. | — |
| **BL-57/65b** (Income Oriented EN) | `FAMILY_INCOME_ORIENTED="Income Oriented"` `classify_utils.py:255`; ES→EN corrected per BL-57 v3. | Principle #8 honored. |
| **BL-56** (centralized post-characterize normalization) | Inline block emission removed, centralized `classify_utils.py:126-128`. | — |
| **ACI_RHP recovery — Round 1** (partial) | FIX-P1-M (extra RHP column inference), FIX-P1-N (`_match_with_join` no-separator join), FIX-P1-O (synthetic header for mega-cell), FIX-P1-P (`_compact_values` mid-value split join). `cost_table_parser.py`. 2026-06-27. | 386/1,122 NULL funds recovered. 736 remaining → addressed by Round 2. |
| **ACI_RHP recovery — Round 2** (partial) | FIX-P1-Q (plain text window 800→1500), FIX-P1-R (`ACI_ROW` + "Impacto" for IE/Irish funds ~218), FIX-P1-T (date-based RHP column), FIX-P1-U (`_BASE_CELL_RE` filter in FIX-P1-G), FIX-P1-V (DLA2→plain fallback when all `aci_pct=None`), FIX-P1-W (mega-cell ACI extraction). + deduplication fix (`seen_horizons` set). `cost_table_parser.py`. 2026-06-27. | 282/306 truly-null subset recovered (92.2%). 24 hard residuals remain. **DB not yet updated — pipeline run required.** |

---

## 2. [OBSOLETE]

| Item | Reason |
|---|---|
| **BL-PROFILE-SRRI-FULL** | Proposed hard SRRI→Profile remap of ~2,110 funds. Conflicts with the v20 decision `Profile = f(SRRI, Fund_Nature)`, warnings-only (V6 INTER-3 / INTEGRATED_SPEC §2A.1 #2). Mass remap is a disallowed optimization path. Replaced by BL-INTER3-WARN (PENDING §3). |
| **BL-DLA-2 (Phase 2 — serialize Cat.1+2 for fee-NULL reduction)** | `dla_table_serializer.py` is now v2; `serialize_tables()` (`io.py:568`) feeds `_fed_text_for_cost` for cost arbitration, not Entry/Exit/OC NULL reduction. Original framing dead; superseded by INTEGRATED_SPEC Job B (`DLA2_ARBITRATION_ENABLED=True`). |
| **INTER-4 (Fund_Nature → Type)** | Retired in v20, no-op stub `classify_utils.py:2719-2734`. `Type→Vehicle_Structure` makes the asset-class constraint meaningless. |
| **BL-DLA-2-RECALIBRACIÓN (as scoped in v3.9)** | KPI thresholds defined against corrupt `dla_inv.csv` baseline. Re-issued under PENDING §3 (LOGS REQUIRED). |
| **BL-COST-5 baseline ("~328 OC/ACI mismatch funds")** | Figure predates Round 1+2 ACI_RHP recovery. Count will change materially after next pipeline run. **Do not schedule architectural review against this KPI.** Re-measure post pipeline run; re-issue BL-COST-5 with v20 figures. |

---

## 3. [PENDING] — enriched with current code constraints

### P0 — Live production bug

#### BL-SRRI-GUARD-FULL — `[PENDING]` · ALTA · `pipeline.py`
- **Current state:** `_safe_scalar()` does not exist (0 occurrences, repo-wide).
- **Constraint (verified):** guard at `pipeline.py:607-611` only protects `_srri_for_classify`. Assignment to `fund_master_record["SRRI"]` is raw: `pipeline.py:817 → "SRRI": parsed.get("SRRI")`. Crash path intact at `pipeline.py:907-908` (`_srri_val >= 5`) → `'>=' not supported between dict and int` when a DDF PDF returns a dict.
- **Partial mitigation only:** dict→int coercion exists inside `detect_profile_from_srri` (`classify_utils.py:2279-2282`) — does not cover the `fund_master_record` path.
- **Next step:** add `_safe_scalar(v)` (extract scalar if dict, int-coerce, else None); apply at **`pipeline.py:817`** (root assignment, not the `:907` consumer). Re-run ISINs `LU1951199022`, `LU1951200648`, `LU2095320268`; assert SRRI persists as int or NULL.
- **Estimate:** 1h. No dependencies.

---

### P1A — Gate: full pipeline run (precondition for P3 and BL-COST-5)

#### PIPELINE-RUN-V20 — `[PENDING]` · BLOCKER
- **Purpose:** apply Round 1+2 ACI_RHP fixes to DB; capture v20 execution log + `dla_inv` output.
- **Unlocks:**
  - P3 items: BL-DLA-2-RECALIBRACIÓN, BL-DLA-2-DIAG, BL-50 (all LOGS REQUIRED).
  - BL-COST-5 re-measurement (current baseline declared stale in §2).
- **Precondition for:** any BL-COST-5 architectural work; BL-55 re-scope.
- **Estimate:** pipeline execution time. No code changes.

---

### P1B — Architectural debt

#### BL-53/54 — Sector_Focus EN — `[PENDING]` · ALTA · `classify_utils.py` + SQL
- **DIRECTIVE (v4.0, reaffirmed v4.1):** Sector_Focus must be in **English (GICS-EN)**. Legacy v3.9 Spanish requirement overridden.
- **Current state (drift):** `SECTOR_FOCUS_TRANSLATION_MAP` targets Spanish (`classify_utils.py:2022-2056`). Contradicts v20 §8 and §2A.1 #6.
- **Target value set (English, canonical):** `Technology & Innovation; Healthcare & Life Sciences; Energy & Resources; Financial Services; Consumer; Materials & Mining; Utilities & Environment; Real Assets`.
- **Next step (R-2, three parts):**
  1. **Pipeline:** invert/replace `SECTOR_FOCUS_TRANSLATION_MAP` → EN-target labels; classifier emits EN.
  2. **Migration:** SQL remap existing ES values → EN (`LIKE`/`TRIM`, R-9).
  3. **COALESCE review** in `publish_fund()`; confirm CACHED path overwrites.
  - Populate iff `Investment_Focus='Sector'`, else NULL (INTER-6 / INTER-15).
  - **Fix `classify_utils.py:2022` comment** (`idioma objetivo: español`) — covers §5 doc-drift item at zero extra cost.
- **Estimate:** 3-4h.

#### BL-INTER3-WARN — `[PENDING]` · ALTA · `classify_utils.py` + `pipeline.py`
- **Current state:** INTER-3 still remaps Profile from SRRI.
  - `validate_profile_srri()` `classify_utils.py:2692-2715` recalculates on `Conservador & SRRI≥5` and `Agresivo & SRRI≤4`.
  - `pipeline.py:905-909` independently remaps `Conservador & SRRI≥5 → Dinámico`.
- **Target:** warnings-only; `Profile = f(SRRI, Fund_Nature)`. No auto-correct.
- **Note:** `Agresivo` is a live value (`_assign_profile_from_srri` `classify_utils.py:2687-2688`, SRRI=7) — not dead code.
- **Next step:** strip remap branches in `validate_profile_srri` (return WARNING tuples, never a new Profile); remove `pipeline.py:905-909`. Define the `f(SRRI, Fund_Nature)` band table (open modelling task).
- **Estimate:** 2-3h + modelling.

---

### P2 — Feature / data

#### BL-COST-ACI-RHP — `[PENDING]` · MEDIA · `cost_table_parser.py` *(new — v4.1)*
- **Context:** 10 fixes (Round 1+2) recovered ~668/1,122 ACI_RHP-NULL funds. 24 hard residuals remain after Round 2's truly-null subset analysis.
- **Current state:** DB not yet updated (pipeline run pending). 24 funds NULL after all fixes.
- **Root cause categories already classified (Round 2):** `has_struct_aci` (47), `no_aci_row_at_all` (218), `no_ot_in_dla2` (28), `mega_only_no_struct` (13), `truly_null` recovered (282/306).
- **Next step:** run full pipeline; re-count NULL corpus; classify 24 residuals — attempt FIX-P1-X iteration or declare hard-unextractable with root cause documented per ISIN.
- **DoD:** ≤50 residuals corpus-wide OR all remaining classified as hard-unextractable with root cause logged.
- **Estimate:** TBD (depends on residual pattern). No architectural conflict.

#### BL-COST-REGRESS — `[PENDING]` · MEDIA · `cost_table_parser.py` *(new — v4.1)*
- **Gap:** Round 2 shipped 7 interacting fixes. "0/50 regressions" was a spot-check, not a corpus guard.
- **Risk:** future cost fixes risk silent regression across 3,205-fund corpus.
- **Next step:** implement golden-ISIN test set (expected ACI_RHP values per ISIN); execute on every `cost_table_parser.py` change. Min set: 50 ISINs covering all fix categories (DLA2, plain text, mega-cell, IE/Irish, date-RHP).
- **DoD:** automated harness; 0 regressions on golden set on each PR touching cost parsing.
- **Estimate:** 2-3h.

#### BL-BENCH-NORM — `[PENDING]` · MEDIA · `classify_utils.py` (+ `kiid_parser.py`)
- **Done (different approach):** parser-side de-contamination — `_trim_benchmark` (`kiid_parser.py:1633`, cap `[:120]` at `:1704`) + `_BENCH_TERMINATORS` (BL-38-v20).
- **NOT done (core deliverable):** canonical normalization. No `normalize_benchmark()`, no `Benchmark_Canonical` (0 occurrences).
- **Constraint:** P3 group-by / exposure analysis blocked without canonical labels.
- **Metrics [legacy v3.9, unverified at v20]:** 522 unique `Benchmark_Declared` → ~36 families. **Re-measure against v20 data first** before implementation.
- **Next step:** re-measure 522/~254 unique benchmarks on v20 corpus; implement `normalize_benchmark(raw)→canonical_label` (regex per family); add `Benchmark_Canonical` or normalize in-situ. Acceptance: ≤50 unique, 0 truncation pattern.
- **Estimate:** 4-6h (post re-measurement). No architectural conflict.

#### BL-55 — Exit_Fee_Pct=0.00 / `EXIT_INFERRED_ZERO` — `[PENDING]` · BAJA · **FROZEN**
- **Status:** frozen pending BL-COST-5 re-scoping after pipeline run. Do not start.
- Flag absent (0 occurrences). Inference rule undefined.

#### BL-DLA-3 / BL-DLA-3-DIAG — `[PENDING]` · BAJA
- Deferred by design until P2/P3 consumer docs require Cat.3 matrices. No active planning.

---

### P3 — LOGS REQUIRED (gate: PIPELINE-RUN-V20)

| Item | Blocker | Action |
|---|---|---|
| **BL-DLA-2-RECALIBRACIÓN** (re-scoped) | Needs fresh v20 `dla_inv` + run log. | Run v20 pipeline; recompute Exit/Entry/OC NULL deltas; re-issue Go/No-Go. Legacy est. ~600-800 funds {Cat.2+Cat.3}; Exit_Fee NULL ~110→~50 [legacy, invalid baseline]. |
| **BL-DLA-2-DIAG** | Depends on reliable `dla_inv` (logic-fix done) + log. | Q-DLA-04/05 on v20 data. |
| **BL-50** (Universe→Geography inverse) | No v3.7 spec text; partial coverage only. | Verify against INTER-6 (`classify_utils.py:2787`) + BL-52 (`:2866`); confirm closure or define delta. |
| **BL-COST-5** (OC/ACI mismatch) | "~328 funds" baseline is stale (§2). | Re-measure post pipeline run; re-issue with v20 KPIs; then schedule architectural review. |

---

### P4 — NOT TRACED (triage required)
Out of provided module scope or no specific code anchor — **do not assume status**:
`BL-58` (Lifecycle/Retirement constants) · `BL-47-ext` (SFDR Art.8 defensive default) · `BL-59` (Restantes majority edge case) · `BL-60` (bipartite SRRI tie) · `BL-61` (root-cause meta-procedure) · `BL-51 Problema B` (cap/floor schema) · `BL-DLA-RESTANTES-1` (EM Debt OICVM detector) · `BL-DLA-C3-EXCL`
**Action:** per-item grep / scope the owning module before scheduling.

---

## 4. Cost-arbitration / Job B (context)
DLA2 dual-strategy cost arbitration is **DELIVERED and active** (INTEGRATED_SPEC v20 §9): `DLA2_ARBITRATION_ENABLED=True`, schema v20 metadata columns present, serializer v2 feeding cost path.

**Open downstream:**
- **BL-COST-ACI-RHP** (P2 above) — 24 residuals; DB update pending pipeline run.
- **BL-COST-5** (P3 above) — architectural review frozen; baseline must be re-measured post pipeline run.
- **BL-COST-REGRESS** (P2 above) — regression harness absent; required before next cost fix iteration.

---

## 5. Doc-drift defects (sweep on next touch)
| Location | Defect | Coverage |
|---|---|---|
| `classify_utils.py:2022` (and `:2064`) | `idioma objetivo: español` for Sector_Focus / TYPE_TRANSLATION_MAP — contradicts EN target. | **Covered by BL-53/54 sprint.** |
| `io.py:46` | Comment "Sub-fase 1B: desactivado por defecto" while `DLA_ENABLED=True`. | Fix on next `io.py` touch. |
| `io.py:55` | Comment "Sub-fase 2B: desactivado" while `DLA_TABLE_SERIALIZATION_ENABLED=True`. | Fix on next `io.py` touch. |

---

## 6. Execution order (v4.1)
1. **BL-SRRI-GUARD-FULL** — live bug, 1h, no deps. Ship first.
2. **PIPELINE-RUN-V20** — apply ACI_RHP fixes to DB; capture log. Unblocks P3 + BL-COST-5.
3. **BL-53/54 (EN override)** — pipeline + migration + COALESCE + doc-drift fix (3-4h).
4. **BL-INTER3-WARN** — strip Profile-from-SRRI remap; model bands (2-3h + modelling).
5. **BL-COST-ACI-RHP** — classify 24 residuals; FIX-P1-X or declare unextractable.
6. **BL-COST-REGRESS** — implement golden-ISIN harness (2-3h).
7. **BL-BENCH-NORM** — re-measure v20 metrics; implement `normalize_benchmark()` (4-6h).
8. **P3 closure sprint** — BL-DLA-2-RECALIBRACIÓN, BL-DLA-2-DIAG, BL-50, BL-COST-5 (post log).
9. **P4 triage** — grep each item; assign module; schedule.

---

## 7. Gap register (v4.1 additions)

| ID | Gap | Risk | Resolution |
|---|---|---|---|
| **G-1** | ACI_RHP work had no formal backlog item; 24 residuals untracked; no closure criterion. | HIGH — BL-COST-5 review cannot be scoped without ACI_RHP DoD. | Resolved: added **BL-COST-ACI-RHP** (P2). |
| **G-2** | No cost regression harness. Round 2 has 7 interacting fixes; "0/50" is a spot-check. | HIGH — silent regression risk on future cost fix iterations. | Resolved: added **BL-COST-REGRESS** (P2). |
| **G-3** | BL-COST-5 "~328 funds" baseline stale post ACI_RHP recovery. | MEDIUM — wrong baseline wastes architectural review sprint. | Resolved: declared OBSOLETE (§2); moved to P3 with re-measure gate. |

---

## 8. Definition of done (per change)
Read-before-edit → surgical `str_replace` → AST parse + grep changed lines → control SQL vs baseline → single-line `python -X utf8` commands. R-2 for any persisted-attribute change (pipeline + migration + COALESCE). No "100% solved" without corpus-scale (3,205) validation. Log every correction with ISIN.

---
**End — v4.1 (self-contained). Schema v20. Supersedes v4.0.**
