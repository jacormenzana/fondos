# FORENSIC DIAGNOSIS — export p1_export_20260610.xlsx + log 215906

Grounded on the uploaded modules, the export (both sheets), `pragma_output_fund_master.txt`,
and `log_pipeline_20260610_215906.log`. Fixes AST-checked and unit-tested.

---

## 0. Headline
The reported "severe data integrity issues" split three ways:
- **Two real bugs** with delivered fixes: `Recommended_Holding_Period` and `KID_Format` (both 100% NULL).
- **By-design semantics misread as bugs**: the "Not Applicable inflation" and the high NULL ratios on `Sector_Focus`/`Distribution_Frequency`.
- **Already resolved** in this export: the casing drift (Hedging/Leverage/Accumulation are fully canonical Title here).

Plus one **input inconsistency to resolve** (§6) and one **crash** (§5).

---

## 1. Log & error analysis (Task 1)
The log shows **no dropped DataFrames and no silent import failures**. The only errors are the
RESTANTES crash on 3 funds (§5). Critically, the 100% NULLs are **not** caused by log errors — they
are deterministic outcomes of the writer logic (§3, §4). The "Not Applicable" counts are sentinel
semantics, not failures (§2).

---

## 2. "Not Applicable inflation" + high NULL ratios — NOT bugs (design)
These are correct **NULL-vs-sentinel** semantics (§D of the project rules): `Not Applicable` is a
real category meaning *structurally inapplicable*; NULL means *applies but undisclosed*.

| Attribute | Not Applicable / NULL | Why it is correct |
|---|---|---|
| `Payoff_Profile` | 3193 NA | Only **8** structured funds exist; everything else is correctly Not Applicable. |
| `MMF_Structure` | 3153 NA | Only ~52 monetary funds; non-MMF → Not Applicable. |
| `Alt_Strategy` | 3153 NA | Only ~52 alternatives; non-alt → Not Applicable. |
| `Duration_Profile` | 2219 NA | Non-FI (equity/mixed) have no duration band. |
| `Credit_Quality` | 2217 NA | Equity/Monetary → Not Applicable (INTER-12). |
| `Sector_Focus` | 91.6% NULL | NULL unless `Investment_Focus='Sector'` (269 funds) — INTER-6. |
| `Distribution_Frequency` | 96.1% NULL | NULL for ACCUMULATION funds (2419) — INTER-2. |

No action needed; these match the canonical model. (If anything, they confirm the new attrs are being
derived correctly.)

---

## 3. `Recommended_Holding_Period` = 100% NULL — REAL BUG (fixed)
**Root cause:** the v20 schema migrated this column TEXT→INTEGER (PRAGMA line 32) and
`sqlite_writer` wraps it in `_safe_int(...)` (L446). But `kiid_parser._detect_recommended_holding_period`
still emits **code strings** (`"5Y"`, `"3Y"`, `"1D-3M"`, `"10Y+"`). `_safe_int("5Y")` raises and
returns `None` → **every** value becomes NULL. The migration was applied to schema + writer but not to
the parser's output format.

**Fix (delivered, `sqlite_writer.py`):** new `_rhp_to_years()` converter (code→integer years:
`5Y→5`, `10Y+→10`, sub-year→`None`), used on the RHP column instead of `_safe_int`. Unit-tested over
the full `_RHP_NORMALIZER` code set. **Decision point:** sub-year codes (`1D/3M/6M/<1Y`, MMF/cash) map
to `None`; change `_RHP_SUBYEAR_CODES` handling to `1` if you prefer a 1-year minimum bucket.
Note: existing rows populate as funds are refreshed (PDF in hand) — a corpus FORCE_REFRESH backfills all.

---

## 4. `KID_Format` = 100% NULL — REAL BUG (fixed)
**Root cause (policy asymmetry):** `KID_Format` is produced only by the cost block
(`extract_priips_costs`/`extract_ucits_costs`), which runs **only when `pdf_bytes is not None`**
(refresh/new funds; CACHED funds skip it by design, §4.2). For CACHED funds `KID_Format` is therefore
absent from the record. The writer spec then uses policy **`'ow'`** for it
(`KID_Format = excluded.KID_Format`), which **overwrites the DB value with NULL**. Its siblings
`KID_Currency` and `Cost_Extraction_Quality` use **`'co'`** (`COALESCE(excluded, fund_master)`), which
*preserves* — that is exactly why those two are populated (~50% / ~99%) while `KID_Format` is 100% NULL.

**Fix (delivered, `sqlite_writer.py`):** change `KID_Format` policy `'ow'`→`'co'`, matching its cost
siblings. After this, `KID_Format` stops being nulled on CACHED cycles and accumulates as funds refresh
(same trajectory `Cost_Extraction_Quality` already followed). A FORCE_REFRESH backfill populates it fully.

---

## 5. RESTANTES crash (3 funds) — fixed at the DRY chokepoint
`restantes.py` falls back to `srri_text.extract_srri(kiid_text)`, which returns a **dict** for 3 funds
(`LU1951199022`, `LU1951200648`, `LU2095320268`). That dict flows into
`classify_utils.detect_profile_from_srri`, whose `srri <= 2` then raises
`TypeError: '<=' not supported between 'dict' and 'int'`.
**Fix (delivered, `classify_utils.py`):** `detect_profile_from_srri` now coerces dict/str→int at entry
(covers all callers, DRY). Verified: dict `{'SRRI':6}`→`Dinámico`, no crash.
**True root cause** is `extract_srri` returning a dict — fix at source in `srri_text.py` (not uploaded; see §7).

---

## 6. Casing (Task 3) — already canonical in THIS export
`Hedging_Policy` = `Unhedged`/`Hedged`/`Partially Hedged` (no `UNHEDGED`/`HEDGED`); `Leverage_Used` =
`No`/`Yes`/`Limited`; `Accumulation_Policy` = `Accumulation`/`Distribution`. The `HEDGED` vs `Hedged`
drift is **not present** in this file. Maintained by `classify_utils.normalize_casing` (config-canonical)
applied in `sqlite_writer._normalize_record`. For any residual stale casing on cached funds, the
idempotent `normalize_db_casing_v20.py` (prior delivery) sweeps the whole table.

### ⚠️ Input inconsistency to confirm
The uploaded `pipeline.py` does **not** contain code that writes the 5 new attrs or `Vehicle_Structure`
into `fund_master_record` (line 758 still emits the dropped `"Type"`), yet the export has them populated.
Because those columns use `'co'` (COALESCE), the populated values are being **preserved from a prior run**
that did write them — not produced by the uploaded `pipeline.py`. Please confirm which `pipeline.py`
actually produced this export (file-revert hazard). If the deployed dict truly omits them, new/never-yet-
populated funds will be NULL there; the prior P1 patch (add the 6 keys to the dict) is the durable fix.

---

## 7. Export column exclusion (Task 2)
- **`1_FundMaster`: 57 cols, excludes `Inference_Trace`.** Confirmed by diffing the export columns vs the
  58-col PRAGMA: the only absent column is `Inference_Trace` (an internal trace/debug column) → the
  `[exclude=1cols]`.
- **`2_KIIDMetadata`: 22 cols (this run used `--include-kiid-text`).** The default-excluded column is
  **`Raw_KIID_Text`** (the large text blob); the flag re-includes it. That matches "excludes 1 in standard,
  all 22 with the flag."

**To confirm the mechanism/why (not just which), I need `export_p1.py`** — to verify whether the exclusion
is a hardcoded skip list, a size/type heuristic, or intentional config, and whether `Inference_Trace`
should be in the export at all.

---

## 8. Files delivered (AST-checked)
- `sqlite_writer.py` — `_rhp_to_years` + RHP column uses it; `KID_Format` `'ow'`→`'co'`.
- `classify_utils.py` — `detect_profile_from_srri` dict/str guard.
- (prior round, still valid) `pipeline.py`, `config.py`, `restantes.py`, `normalize_db_casing_v20.py`.

## 9. Code I still need (per your constraint)
1. **`export_p1.py`** — to confirm the column-exclusion mechanism (Task 2 "why") and whether `Inference_Trace` exclusion is intentional.
2. **`srri_text.py`** — to fix the dict-return at its source (the `detect_profile_from_srri` guard neutralizes the crash, but the real bug is `extract_srri`).
(Not needed for the fixes above: `priips_cost_extractor` — the `KID_Format` fix is entirely in the writer policy.)
