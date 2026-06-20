# Integrated Spec v2 — Schema v19.2 → v20 + DLA2 cost-arbitration in production flow

**Date:** 2026-06-06 · **Status:** fully determined — no open assumptions · **Schema target:** v20
**Supersedes:** INTEGRATED_SPEC_v20 (v1). Changes from v1 are flagged ⟲.

Two jobs, one deploy:
- **Job A** — approved `fund_master` redesign (5 CREATE, 4 DELETE, 14 MODIFY) + `config.DOMAIN_VALUES` reconciliation + config-as-catalog consolidation.
- **Job B** — promote the tested DLA/DLA2 dual-strategy arbitration into the production flow, mirroring the SRRI data-management model, persisting full per-component parity.

All file/line references are to the production modules as read 2026-06-06.

---

## 0. Confirmed decisions (closed)
| # | Decision | Value |
|---|---|---|
| Granularity | per component | management + operation, separately |
| Enum (6) | full | `AGREE, OCR_RECOVERED, BOTH_FAIL, ONLY_BANDS_X, ONLY_RULED, CONFLICT` ⟲ (BOTH_FAIL now persisted, not dropped) |
| Placement | `fund_kiid_metadata` | beside `SRRI_*` (SRRI precedent) |
| Tolerance | hybrid ATOL+RTOL via `math.isclose` | ⟲ replaces fixed `_TOL=0.011` |
| Casing | two-class | UPPER_SNAKE enums / Title Case taxonomy |
| Storage | full SRRI parity | raw method values **and** verdict, per component |
| Backfill | one execution | corpus-wide `FORCE_REFRESH` + `kiid_source='local'`, no downloads |
| Overall verdict | worst-of, derived view | severity `CONFLICT>BOTH_FAIL>OCR_RECOVERED>ONLY_BANDS_X>ONLY_RULED>AGREE` |

---

## 1. Integration principle
Data-orthogonal (no rule reads a cost column; no cost step reads a classification attribute) but mechanically coupled at: one schema-version bump, one corpus reprocess, one COALESCE write-path. Operational independence preserved by kill-switch `DLA2_ARBITRATION_ENABLED` and separate commits with separate pass/fail gates.

**Invariant honored:** the *download* trigger is untouched. PDFs download only on `KIID_Status='FORCE_REFRESH'` or absent ISIN. The backfill is a corpus-wide FORCE_REFRESH executed with `kiid_source='local'` — reads `{ISIN}.pdf` from the local repo, downloads nothing.

---

## 2. Schema migration (v19.2 → v20)

### 2A. `fund_master` (Job A) — rebuild required (FK dependents)
Apply the approved inventory. Column count **57 → 58** (−4, +5).
- **DELETE (4):** `Subtype`, `Portfolio_Currency`, `Currency_Hedged`, `Is_ESG` (confirmed present in v19 canonical list, schema_checks 48/60/75/77).
- **CREATE (5):** `Development_Status`, `Duration_Profile`, `MMF_Structure`, `Alt_Strategy`, `Payoff_Profile`.
- **MODIFY (14):** scope/value remaps (data-level, applied by the reprocess, not DDL).

Safe-rebuild idiom (one transaction; FK targets never dangling):
```
PRAGMA foreign_keys=OFF;
BEGIN;
  CREATE TABLE fund_master_new ( … v20 set: 58 cols … );
  INSERT INTO fund_master_new (<58 surviving cols>) SELECT <same> FROM fund_master;
  DROP TABLE fund_master;
  ALTER TABLE fund_master_new RENAME TO fund_master;
  -- recreate ALL indexes + FK fund_master(fund_family_id) → fund_families
COMMIT;
PRAGMA foreign_keys=ON;
```
Owned by new `migrate_v19_to_v20.py` (also the fix for the `migrate_v18_to_v19` FK-rebuild gap).

### 2B. `fund_kiid_metadata` (Job B) — additive, no rebuild. Full SRRI parity. ⟲
Column count **16 → 22** (+6). Per component: 2 raw method values + 1 verdict.
```sql
-- management component
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Mgmt_BandsX REAL;
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Mgmt_Ruled  REAL;
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Mgmt_Arbitration TEXT
   CHECK (Cost_Mgmt_Arbitration IS NULL OR Cost_Mgmt_Arbitration IN
     ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT'));
-- operation component
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Oper_BandsX REAL;
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Oper_Ruled  REAL;
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Oper_Arbitration TEXT
   CHECK (Cost_Oper_Arbitration IS NULL OR Cost_Oper_Arbitration IN
     ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT'));
```
Parity with SRRI: `Cost_*_BandsX`/`Cost_*_Ruled` ↔ `SRRI_Visual`/`SRRI_Textual`; `Cost_*_Arbitration` ↔ `SRRI_Validation_Status`.

**Overall verdict — derived view (no stored column):**
```sql
CREATE VIEW v_cost_arbitration_overall AS
SELECT ISIN,
  CASE
    WHEN Cost_Mgmt_Arbitration IS NULL OR Cost_Oper_Arbitration IS NULL THEN NULL
    WHEN 'CONFLICT'      IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'CONFLICT'
    WHEN 'BOTH_FAIL'     IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'BOTH_FAIL'
    WHEN 'OCR_RECOVERED' IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'OCR_RECOVERED'
    WHEN 'ONLY_BANDS_X'  IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'ONLY_BANDS_X'
    WHEN 'ONLY_RULED'    IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'ONLY_RULED'
    ELSE 'AGREE'
  END AS Cost_Arbitration_Overall
FROM fund_kiid_metadata WHERE KIID_Class = 1;
```

**Verdict state semantics (per component):**
both agree → `AGREE`; one strategy only → `ONLY_BANDS_X`/`ONLY_RULED`; both disagree → `CONFLICT`; neither table strategy, OCR recovered → `OCR_RECOVERED`; neither + OCR failed → `BOTH_FAIL`; **never arbitrated** (CACHED pre-backfill / no local PDF / kill-switch off) → **`NULL`**. The `BOTH_FAIL` vs `NULL` distinction (tried-and-failed vs not-tried) is **richer than SRRI's `NOT_AVAILABLE`** and must be preserved by the writer (§4.3).

---

## 3. `config.py` — becomes the canonical attribute catalog ⟲ (Contribution 1b)
- `SCHEMA_VERSION: "v19.2" → "v20"`.
- New kill-switch: `DLA2_ARBITRATION_ENABLED: bool = False` (dark-launch; flip True for backfill).
- New canonical enum: `COST_ARBITRATION_VALUES = ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT')`.
- **Hybrid cost tolerances (single source of truth):**
  ```python
  COST_CMP_ABS_TOL: float = 0.0002   # 0.02 pp — rounding/typography floor
  COST_CMP_REL_TOL: float = 0.01     # 1% relative — scales with magnitude
  ```
  Seed values; tune against the historical CONFLICT set during the test pass (§5).
- **Catalog consolidation.** `config` owns one attribute catalog: per attribute `{group, casing_rule, allowed_values|type}`. `DOMAIN_VALUES` updated to v20 (drops the 4, adds the 5, applies the reconciled/Title-Cased value sets — note `Replication_Method` canonical is already `Physical/Synthetic/Sampling`, line 348). Config is the dependency leaf (no cycle).
- **Casing (Contribution 1c):** per-attribute `casing_rule` ∈ {`UPPER_SNAKE` (control enums), `TITLE` (taxonomy)}. Stored as data here; the normalization *function* lives in `classify_utils` (R-1).

---

## 4. Flow changes — SRRI-mirrored ⟲ (Contribution 2)

### 4.1 `io.py` — no new reader needed
The v1 `load_local_pdf_bytes`-for-CACHED idea is **dropped**. Cache A (io.py 693–703) already excludes `FORCE_REFRESH`, so a FORCE_REFRESH fund falls through to the local-PDF read path (`kiid_source='local'`, line 720) and returns `pdf_bytes`. No change to Cache A or the remote `Flujo B`.

### 4.2 `pipeline.py` — gate cost on PDF-in-hand, mirroring SRRI visual
- **Reuse the single per-fund PDF open.** `pdf_bytes` is already loaded for `parse_kiid_generic` (line 556). **Move the `pdf_bytes=None` free from line 561 to after the cost hook** so the arbitration reuses it. One open per fund (DRY).
- **Gate the whole cost block (1571–1644) on `pdf_bytes is not None`** (⇔ status ∈ refresh/new), replacing the `_ceq_bd != 'HIGH'` skip (line 1598). CACHED → skip cost entirely → COALESCE preserves DB values + verdict. This is a *named behavior change*: today the text extractor re-runs on CACHED; now it caches, recomputing only on refresh (justified by your >96% agreement / >98% accuracy).
- **Arbitration step** (gated by `DLA2_ARBITRATION_ENABLED`, inside the PDF-in-hand block):
  ```python
  if _arb_enabled and pdf_bytes is not None:
      arb = arbitrate_costs_from_pdf(pdf_bytes)   # core fn, §4.4
      kiid_record.update({
          'Cost_Mgmt_BandsX': arb['mgmt']['bandsx'], 'Cost_Mgmt_Ruled': arb['mgmt']['ruled'],
          'Cost_Mgmt_Arbitration': arb['mgmt']['verdict'],
          'Cost_Oper_BandsX': arb['oper']['bandsx'], 'Cost_Oper_Ruled': arb['oper']['ruled'],
          'Cost_Oper_Arbitration': arb['oper']['verdict'],
      })
      if arb.get('table_text'):
          kiid_record['DLA2_Table_Text'] = arb['table_text']   # higher-fidelity → existing extractor
  ```
- Cost **values** still flow through `extract_priips_costs`/`extract_ucits_costs` over the (improved) table text. Direct value-override on CONFLICT/OCR deferred to **BL-COST-5**.

### 4.3 `sqlite_writer.py` — CACHED-safe persistence
- `upsert_kiid_metadata` (706–786): add the **6** new columns to INSERT list, VALUES tuple, and `ON CONFLICT DO UPDATE` with **COALESCE** (mirror `DLA2_Table_Text`, line 763). Critical: COALESCE must **preserve a real `BOTH_FAIL`** and never let a later CACHED `NULL` overwrite it.
- `upsert_fund_master` (405+): remove dead COALESCE refs for the 4 drops (`Currency_Hedged` 538, `Portfolio_Currency` 555, `Is_ESG`, `Subtype` 534); add COALESCE for the 5 new columns. Stays explicit column-by-column (your principle — no dict dumps).

### 4.4 Promote arbitration to `core/` — one *extension* (per-component) ⟲
`dla2_dual_strategy_compare.py` is a `scripts/diag` prototype (CLI `main()`, line 504). Required:
1. Extract pure callable `arbitrate_costs_from_pdf(pdf_bytes) -> dict` (no CSV/DB side-effects), single `pdfplumber.open` per fund (already the pattern, 577).
2. **Per-component arbitration:** the tested `classify(bx,rl)` (481–497) runs on consolidated OC only. Apply it **twice** — management and operation — using the per-component values the strategies already expose (`_recover_oc_block`, `_oc_from_lines` return `(gest, oper)`). New logic on validated primitives → needs its own test pass (§5).
3. **Replace `_TOL=0.011`** with the shared comparator:
   ```python
   # classify_utils.py — DRY single source for arbitration AND %↔EUR cross-validation
   import math
   def cost_values_agree(a, b):
       if a is None or b is None: return False
       return math.isclose(a, b, rel_tol=COST_CMP_REL_TOL, abs_tol=COST_CMP_ABS_TOL)
   ```
Return contract:
```python
{ 'oc': float|None,
  'mgmt': {'bandsx': float|None, 'ruled': float|None, 'verdict': str|None},
  'oper': {'bandsx': float|None, 'ruled': float|None, 'verdict': str|None},
  'table_text': str|None }
```

---

## 5. Binary pass/fail — two independent tracks

**Track A — classification (Job A):**
- `PRAGMA table_info(fund_master)`: 4 drops absent, 5 new present, count = 58.
- `schema_checks` aligned: `assert len(EXPECTED_COLUMNS_V20) == 58`; metadata = 22.
- `Replication_Method` ⊆ {Physical, Synthetic, Sampling, NULL} (0 rows ACTIVE/PASSIVE).
- INTER-rule baselines (V4 instructions §7): INTER-1=0, INTER-2≤2, RV-rated-Credit=0 post-remap.
- New-column coverage ≈ proposal estimates: `Duration_Profile`≈964, `Development_Status`≈3068, `MMF_Structure`≈89, `Alt_Strategy`≈75, `Payoff_Profile`=8.
- All categorical values conform to their `casing_rule`.

**Track B — cost arbitration (Job B):**
- 6 metadata columns populated for funds with a local PDF post-backfill.
- Report `AGREE/ONLY_*/CONFLICT/OCR_RECOVERED/BOTH_FAIL` distribution under seed `ATOL/RTOL`; **tune constants before the corpus run if CONFLICT exceeds the ~6–10 history**.
- No regression in `Cost_Extraction_Quality='HIGH'` count vs pre-release.
- `BOTH_FAIL` survives a subsequent CACHED cycle (COALESCE-preservation test).
- `v_cost_arbitration_overall` returns worst-of for mixed-verdict funds (unit check).

**Kill-switch test:** `DLA2_ARBITRATION_ENABLED=False` ⇒ no arbitration calls, no verdict writes, classification unaffected, cost path = pre-release behavior.

---

## 6. Commit sequence & risk
1. **Migration** — `migrate_v19_to_v20.py` + `schema_fondos.sql` v20 + `schema_checks.py` (column lists *derived from config catalog*; new V20 delta constants; `check_schema_v20()`; assert 58/22). Dry-run on DB copy; verify FK + indexes recreated.
2. **Job A logic** — config catalog/`DOMAIN_VALUES` v20 + tolerances + casing rules; `classify_utils` value remaps, INTER rules, `cost_values_agree`, casing-normalizer; `upsert_fund_master` COALESCE edits. Track-A queries.
3. **Job B logic** — `core/` arbitration callable + per-component extension; `pipeline.py` hook + `pdf_bytes` lifetime change; `upsert_kiid_metadata` 6 columns; the view. Behind `DLA2_ARBITRATION_ENABLED=False`. Track-B after flag flip in backfill.

Each commit: read full target region → surgical `str_replace` → AST validation → single-line Windows-safe `python -X utf8` control query.

---

## 7. Backfill (one execution)
Pre-conditions: v20 migrated; `DLA2_ARBITRATION_ENABLED=True`; `PRIIPS_COST_EXTRACTION_ENABLED=True`; corpus-wide `KIID_Status='FORCE_REFRESH'`; `kiid_source='local'`. Reads every `{ISIN}.pdf` from the local repo, downloads nothing, repopulates classification + cost + arbitration in one pass via `publish_fund`. Coverage limited to funds whose local PDF exists — **confirm repo completeness**; the rest stay NULL until their next refresh.

---
*Grounded in: pipeline.py (446, 500–575, 1571–1690), io.py (318–352, 592–720), sqlite_writer.py (405, 524–556, 706–786), config.py (82, 84–85, 348, 405–408), schema_checks.py (35–115, 120–148, 199–219, 282–327), init_db.py (58–88, 289), dla2_dual_strategy_compare.py (471–497, 504, 577–610).*
