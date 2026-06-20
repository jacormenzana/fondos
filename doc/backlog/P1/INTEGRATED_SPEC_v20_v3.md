# Integrated Spec v3 — Schema v19.2 → v20 + DLA2 cost-arbitration in production flow

**Date:** 2026-06-07 · **Status:** fully determined except 4 named open decisions (§8-bis) · **Schema target:** v20
**Supersedes:** INTEGRATED_SPEC_v20_v2. Changes from v2 are flagged ⟳ (mostly: the **approved inventory is now embedded** — §2A.1 — recovered from the originating analysis chat where it had been decided but omitted from v2).

Two jobs, one deploy:
- **Job A** — approved `fund_master` redesign (5 CREATE, 4 DELETE, 14 MODIFY) + `config.DOMAIN_VALUES` reconciliation + config-as-catalog consolidation.
- **Job B** — promote the tested DLA2 dual-strategy arbitration into the production flow, mirroring the SRRI data-management model, with full per-component parity. **(Job B is already implemented — see §9.)**

All file/line references are to the production modules as read 2026-06-06.

**Root-cause framing ⟳ (decided in the originating chat):** `config.DOMAIN_VALUES` is the **canonical design-intent layer**; the live DB is **implementation drift** from a sound original design. Fix direction is **DB → design-intent**, with config updated only where the drift was *intended* evolution (ES→EN on Type/Family/Sector_Focus, the v17 additions). The drift recurred because the same vocabularies live in ≥3 places; eliminating that duplication (§3, §Y) is the root-cause fix, not the value patch.

---

## 0. Confirmed decisions (closed)
| # | Decision | Value |
|---|---|---|
| Granularity | per component | management + operation, separately |
| Enum (6) | full | `AGREE, OCR_RECOVERED, BOTH_FAIL, ONLY_BANDS_X, ONLY_RULED, CONFLICT` (BOTH_FAIL persisted) |
| Placement | `fund_kiid_metadata` | beside `SRRI_*` |
| Tolerance | hybrid ATOL+RTOL via `math.isclose` | replaces fixed `_TOL=0.011` |
| Casing | two-class | TITLE for fund-characteristic values / UPPER_SNAKE for control-provenance flags (§3-bis) |
| Storage | full SRRI parity | raw method values **and** verdict, per component |
| Backfill | one execution | corpus-wide `FORCE_REFRESH` + `kiid_source='local'`, no downloads |
| Overall verdict | worst-of, derived view | `CONFLICT>BOTH_FAIL>OCR_RECOVERED>ONLY_BANDS_X>ONLY_RULED>AGREE` |
| ⟳ Inventory direction | design-intent canonical | `config.DOMAIN_VALUES` is truth; DB is drift |

---

## 1. Integration principle
Data-orthogonal (no rule reads a cost column; no cost step reads a classification attribute) but mechanically coupled at: one schema-version bump, one corpus reprocess, one COALESCE write-path. Operational independence preserved by kill-switch `DLA2_ARBITRATION_ENABLED` and separate commits with separate pass/fail gates.

**Invariant honored:** the *download* trigger is untouched. PDFs download only on `KIID_Status='FORCE_REFRESH'` or absent ISIN. The backfill is a corpus-wide FORCE_REFRESH with `kiid_source='local'` — reads `{ISIN}.pdf` locally, downloads nothing.

---

## 2. Schema migration (v19.2 → v20)

### 2A. `fund_master` (Job A) — rebuild required (FK dependents). Column count **57 → 58** (−4, +5).
- **DELETE (4):** `Subtype`, `Portfolio_Currency`, `Currency_Hedged`, `Is_ESG`.
- **CREATE (5):** `Development_Status`, `Duration_Profile`, `MMF_Structure`, `Alt_Strategy`, `Payoff_Profile`.
- **MODIFY (14):** value/scope remaps + one type change (`Recommended_Holding_Period` TEXT→INTEGER) + one rename decision (`Type`→`Vehicle_Structure`, §8-bis). Data-level remaps applied by the reprocess; type/rename applied by DDL.

Safe-rebuild idiom (one transaction; FK targets never dangling):
```
PRAGMA foreign_keys=OFF;
BEGIN;
  CREATE TABLE fund_master_new ( … v20 set: 58 cols; Recommended_Holding_Period INTEGER … );
  INSERT INTO fund_master_new (<53 surviving cols>) SELECT <same> FROM fund_master;  -- 5 new → NULL
  DROP TABLE fund_master;
  ALTER TABLE fund_master_new RENAME TO fund_master;
  -- recreate ALL indexes (NOT idx_fm_esg) + FK fund_master(fund_family_id) → fund_families
COMMIT;
PRAGMA foreign_keys=ON;
```
Owned by `migrate_v19_to_v20.py` (also fixes the `migrate_v18_to_v19` FK-rebuild gap).

### 2A.1 — Approved Inventory (authoritative) ⟳
`value set` = design-canonical; `cov` = funds meaningfully characterized (non-NULL / non-Not-Applicable). Op tally vs v19: **KEEP 38 · MODIFY 14 · CREATE 5 · DELETE 4**.

**CREATE (5)** — each decomposes an overloaded column; each carries the explicit `Not Applicable` sentinel.

| Column | Value set | cov | Origin | Casing |
|---|---|---|---|---|
| `Development_Status` | `Developed; Emerging; Frontier; Global/Mixed` | 3068 | split from `Geography` (removes place/development overlap) | TITLE |
| `Duration_Profile` | `Ultra-Short; Short; Intermediate; Long; Flexible; Not Applicable` | 964 | FI duration band pulled out of `Exposure_Bias`; serves IPC+M3 / max-DD | TITLE |
| `MMF_Structure` | `CNAV; LVNAV; VNAV; Standard MMF; Not Applicable` | 89 | from `Subtype` (MMF regulatory class) | TITLE (acronyms upper) |
| `Alt_Strategy` | `Long/Short; Market Neutral; Global Macro; Relative Value/Arbitrage; Opportunistic; Volatility Target; Not Applicable` | 75 | from `Subtype` (alternative-strategy) | TITLE |
| `Payoff_Profile` | `Autocallable; Capital Protected; Fixed Coupon Band; Not Applicable` | 8 | from `Subtype` (structured payoff) | TITLE |

**DELETE (4)**

| Column | Reason |
|---|---|
| `Subtype` | Grab-bag of ≥5 orthogonal dimensions; decomposed into the 3 structure columns above. 333→0. |
| `Portfolio_Currency` | 98.7% NULL, never consumed in P3. |
| `Currency_Hedged` | 100% redundant with `Hedging_Policy` (INTER-11, 0 mismatches); derive as a view if needed. |
| `Is_ESG` | Fully derivable from `Sfdr_Article`; derive as a view if needed. |

**MODIFY (14)** — Tier-1 = restore lost design semantics; Tier-2 = design optimization.

| # | Column | Value set (design-canonical) | cov | Tier | Note |
|---|---|---|---|---|---|
| 1 | `Type` → rename **`Vehicle_Structure`** (decision §8-bis) | `Open-End UCITS; ETF; Fund of Funds; Money Market Fund; Structured Product` | 3202 | 1 | Repurposed to legal/structural vehicle form; was overlapping `Fund_Nature`+`Strategy`. Ripple §6-bis. |
| 2 | `Profile` | `Conservador; Moderado; Dinámico; Agresivo` | 3204 | 2 | Define as `f(SRRI, Fund_Nature)` (INTER-3); **`Agresivo` ADDED** (SRRI 7). |
| 3 | `Recommended_Holding_Period` | `Integer years 1–10; NULL if KIID silent` | 2537 | 1 | INTEGER, not code strings (live `1D/1M/1Y…` lexically unsortable). |
| 4 | `Geography` | `Global; Europe; North America; Asia-Pacific; Japan; China; India; Latin America; Eastern Europe; Middle East & Africa` | 3068 | 2 | Pure SPATIAL, single tier, **English**; dev tier → `Development_Status`. |
| 5 | `Investment_Universe` | `Global; Regional; Country` | 3088 | 2 | Drop non-geographic `Liquidity`; reclassify by actual geography. |
| 6 | `Sector_Focus` | `Technology & Innovation; Healthcare & Life Sciences; Energy & Resources; Financial Services; Consumer; Materials & Mining; Utilities & Environment; Real Assets` | 269 | 2 | English; populated iff `Investment_Focus='Sector'`, else NULL. |
| 7 | `Market_Cap_Focus` | `Large Cap; Mid Cap; Small Cap; SMID Cap; All Cap; Not Applicable` | 1820 | 2 | `All Cap`=diversified equity; `Not Applicable`=non-equity; NULL=unknown. |
| 8 | `Strategy` | `Activo; Indexado; Pasivo` | 3204 | 1/2 | Active/passive axis only; drop vehicle `ETF`; language **ES**. |
| 9 | `Replication_Method` | `Physical; Synthetic; Sampling; Not Applicable` | 124 | 1 | Restore replication-technique semantics; active funds → `Not Applicable`. |
| 10 | `Style_Profile` | `Value; Growth; Blend; Income; Quality; Momentum; Low Volatility; Strategic Allocation; Not Applicable` | 2369 | 2 | NULL(unknown) vs `Not Applicable`(non-equity). |
| 11 | `Exposure_Bias` | `Long Only; Long/Short; Market Neutral; Net Short; Not Applicable` | ~1700 | 2 | Single dimension (directional); FI factors → `Duration_Profile`/`Credit_Quality`. |
| 12 | `Derivatives_Usage` | `None; Hedging Only; Investment; Both` | 3204 | 1 | Restore derivative-PURPOSE distinction (live drifted to YES/NO/LIMITED). |
| 13 | `Leverage_Used` | `No; Limited; Yes` | 3204 | 2 | 3-state; confirmed `No` ≠ unknown. |
| 14 | `Hedging_Policy` | `Hedged; Unhedged; Partially Hedged` | 2735 | 2 | Single source of truth (absorbs deleted `Currency_Hedged`). |

**KEEP (selected, for the catalog):** `SRRI`=1..7 · `SRRI_Quality_Flag`=`HIGH; MEDIUM_VISUAL; MEDIUM_TEXT; LOW_CONFLICT; NONE` · `Investment_Focus`=`Broad; Sector; Thematic` · `Credit_Quality`=`Investment Grade; High Yield; Mixed; Not Applicable` (define IG/HY→Mixed threshold, §8-bis) · `Theme`=`Core/General + ~19 themes` · `Fund_Currency`=`EUR; USD; GBP; CHF; JPY; CNH` · `Accumulation_Policy`=`Accumulation; Distribution` · `Distribution_Frequency`=`Monthly; Quarterly; Semi-Annual; Annual` · cost columns numeric.

**DEFERRED:** `Liquidity_Profile` — restore-as-dealing-frequency vs retire (§8-bis). Do not implement blind.

### 2B. `fund_kiid_metadata` (Job B) — additive, no rebuild. Column count **16 → 22** (+6).
```sql
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Mgmt_BandsX REAL;
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Mgmt_Ruled  REAL;
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Mgmt_Arbitration TEXT
   CHECK (Cost_Mgmt_Arbitration IS NULL OR Cost_Mgmt_Arbitration IN
     ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT'));
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Oper_BandsX REAL;
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Oper_Ruled  REAL;
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Oper_Arbitration TEXT
   CHECK (Cost_Oper_Arbitration IS NULL OR Cost_Oper_Arbitration IN
     ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT'));
```
Parity: `Cost_*_BandsX`/`Cost_*_Ruled` ↔ `SRRI_Visual`/`SRRI_Textual`; `Cost_*_Arbitration` ↔ `SRRI_Validation_Status`.

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
**Verdict semantics:** both agree→`AGREE`; one only→`ONLY_BANDS_X`/`ONLY_RULED`; disagree→`CONFLICT`; neither table, OCR recovered→`OCR_RECOVERED`; neither + OCR failed→`BOTH_FAIL`; never arbitrated (CACHED/no local PDF/flag off)→`NULL`. The `BOTH_FAIL`≠`NULL` distinction must be preserved by the writer (§4.3).

---

## 3. `config.py` — the canonical attribute catalog
- `SCHEMA_VERSION: "v19.2" → "v20"`.
- `DLA2_ARBITRATION_ENABLED: bool = False` (dark-launch).
- `COST_ARBITRATION_VALUES = ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT')`.
- Hybrid tolerances (single source): `COST_CMP_ABS_TOL=0.0002`, `COST_CMP_REL_TOL=0.01` (seeds; tune §5).
- **Catalog consolidation ⟳.** `config` owns ONE attribute catalog: per attribute `{group, casing_rule, allowed_values|type, coverage_note}`. `DOMAIN_VALUES` updated to v20 = the §2A.1 sets (drop 4, add 5, apply the 14 remaps, Title/UPPER per §3-bis). Config is the dependency leaf.

## 3-bis. Casing catalog (universal, two-class)
- **`TITLE`** — every value describing a *fund characteristic* (taxonomy + policy/structure). The live UPPER-case (`ACCUMULATION`, `HEDGED`, `YES`) is **drift to normalize to Title** (`Accumulation`, `Hedged`, `No; Limited; Yes`, `None; Hedging Only; Investment; Both`). Language orthogonal (ES: Fund_Nature/Profile/Strategy; EN: the rest, incl. Geography now).
- **`UPPER_SNAKE`** — internal control/provenance/quality/format flags only: `SRRI_Quality_Flag`, `Data_Quality_Flag`, `SRRI_Validation_Status`, `KID_Format`, `Cost_Extraction_Quality`, `Fee_Known_Flag`, `Benchmark_Type`, `Cost_*_Arbitration`.
- **`CODE`/`NUM`** — `SRRI`, `Sfdr_Article` (numeric); `Fund_Currency` (ISO upper); `Recommended_Holding_Period` (integer).

The casing **function** lives in `classify_utils` (R-1); `config` stores `casing_rule` as data. A Track-A query asserts every categorical value matches its rule.

---

## 4. Flow changes — SRRI-mirrored

### 4.1 `io.py` — no new reader. Cache A (693–703) already excludes `FORCE_REFRESH`; such a fund falls through to the local-PDF read (`kiid_source='local'`, line 720) returning `pdf_bytes`.

### 4.2 `pipeline.py` — gate cost on PDF-in-hand
- Reuse the single per-fund PDF open: move `pdf_bytes=None` from line 561 to after the cost+arbitration hook.
- Gate the cost block on `pdf_bytes is not None`, replacing the `_ceq_bd != 'HIGH'` skip. CACHED → skip cost → COALESCE preserves. Named behavior change: cost cached, recomputed only on refresh.
- Arbitration step (gated by `DLA2_ARBITRATION_ENABLED`): `arb = arbitrate_costs_from_pdf(pdf_bytes)` → write the 6 verdict/value fields into `kiid_record`; if `arb['table_text']`, override `DLA2_Table_Text`.
- Cost **values** still flow through `extract_priips_costs`/`extract_ucits_costs`. CONFLICT/OCR value-override deferred to BL-COST-5.

### 4.3 `sqlite_writer.py` — CACHED-safe
- `upsert_kiid_metadata` (706–786): +6 columns in INSERT/VALUES/ON CONFLICT with COALESCE — must **preserve a real `BOTH_FAIL`**, never overwrite with a CACHED NULL.
- `upsert_fund_master` (405+): remove the 4 dropped columns; add COALESCE for the 5 new. Explicit column-by-column.
- **Pre-rebuild ripple:** neutralize `Subtype`/`Currency_Hedged` references in `_post_upsert_normalize_db` and `global_post_pipeline_normalize_db` BEFORE the rebuild, else the next cycle crashes.

### 4.4 `core/` arbitration callable
`arbitrate_costs_from_pdf(pdf_bytes) -> {oc, mgmt{bandsx,ruled,verdict}, oper{...}, table_text}`, single `pdfplumber.open`, no side-effects; per-component (mgmt + oper) via the shared `cost_values_agree` comparator (`classify_utils`, `math.isclose`, replacing `_TOL`).

---

## 5. Binary pass/fail — two tracks

**Track A (Job A):** `PRAGMA table_info(fund_master)` → 4 drops absent, 5 new present, count 58 · `assert len(EXPECTED_COLUMNS_V20)==58`; metadata 22 · `Replication_Method ⊆ {Physical,Synthetic,Sampling,NULL}` (0 ACTIVE/PASSIVE) · INTER baselines (V6 §7): INTER-1=0, INTER-2≤2, RV-rated-Credit=0 post-remap · new-column coverage ≈ §2A.1 (Duration 964, Development 3068, MMF 89, Alt 75, Payoff 8) · every categorical conforms to its `casing_rule`.

**Track B (Job B):** 6 metadata columns populated for funds with a local PDF · `AGREE/ONLY_*/CONFLICT/OCR_RECOVERED/BOTH_FAIL` distribution under seed tolerances (tune if CONFLICT > ~6–10) · no regression in `Cost_Extraction_Quality='HIGH'` count · `BOTH_FAIL` survives a CACHED cycle · `v_cost_arbitration_overall` worst-of unit check.

**Kill-switch:** `DLA2_ARBITRATION_ENABLED=False` ⇒ no arbitration calls/writes; classification + cost path = pre-release.

---

## 6. Commit sequence & risk
1. **Migration** — `migrate_v19_to_v20.py` + `schema_fondos.sql` v20 + `schema_checks.py` (column lists derived from the config catalog; V20 delta constants; `check_schema_v20()`; assert 58/22). Dry-run on a DB copy; verify FK + indexes.
2. **Job A logic** — config catalog/`DOMAIN_VALUES` v20 + casing rules; `classify_utils` value remaps + casing-normalizer + INTER-3/4/5 redesign (§6-bis); neutralize Subtype/Currency_Hedged normalizers; `upsert_fund_master` edits. Reprocess (corpus FORCE_REFRESH, local). Track-A queries per remap.
3. **Job B logic** — already delivered (§9), behind `DLA2_ARBITRATION_ENABLED=False`; Track-B after flag flip in backfill.

Each commit: read region → surgical `str_replace` → AST validation → single-line `python -X utf8` control query.

## 6-bis. Ripple discovered while grounding the inventory
1. **`Type`→`Vehicle_Structure` collapses INTER-4/INTER-5.** Those map `Fund_Nature→Type` on the asset-class vocabulary; with `Type` as an orthogonal vehicle axis the constraint is meaningless. **Redesign or retire INTER-4 and `ALLOWED_TYPE_BY_NATURE`** (review INTER-5 similarly).
2. **Delete-ripple:** neutralize Subtype/Currency_Hedged normalizers before the rebuild (§4.3).
3. **`Recommended_Holding_Period` TEXT→INTEGER** is a column-type migration (DDL), not just a value remap.
4. **`Profile = f(SRRI, Fund_Nature)`** exact bands not yet fixed — open INTER-3 modelling task, required before `Agresivo`/tier assignment is deterministic.

---

## 7. Backfill (one execution)
v20 migrated; `DLA2_ARBITRATION_ENABLED=True`; `PRIIPS_COST_EXTRACTION_ENABLED=True`; corpus-wide `KIID_Status='FORCE_REFRESH'`; `kiid_source='local'`. Reads every local `{ISIN}.pdf`, downloads nothing, repopulates classification + cost + arbitration in one pass via `publish_fund`. Coverage limited to funds with a local PDF — confirm repo completeness; the rest stay NULL until their next refresh.

---

## 8. Language (Principle #8)
ES columns: Fund_Nature, Profile, Strategy. EN columns: everything else taxonomy/policy (Type/Vehicle_Structure, Family, Style_Profile, Theme, Exposure_Bias, Sector_Focus, Market_Cap_Focus, Credit_Quality, Geography ⟳ now EN, the 5 new). Translate ES→EN before persisting EN-target columns.

## 8-bis. Open decisions (gate the data-population step only) ⟳
1. **`Liquidity_Profile`** — restore as dealing-frequency vs retire.
2. **`Type` rename** — keep column name `Type` with the new value-set, OR rename to `Vehicle_Structure` (cleaner; ripples to P2/P3). Recommend keeping `Type` unless downstream expects the rename.
3. **INTER-4/5** — redesign vs retire (consequence of #1 in §2A.1).
4. **Thresholds** — `Profile=f(SRRI,Fund_Nature)` bands; `Credit_Quality` IG/HY→Mixed threshold.

The migration (commit 1) and Job B (commit 3) do **not** depend on these; only the value-remap reprocess (commit 2) does.

---

## §Y. Architectural optimizations (DRY root-cause)
1. **One source of truth for value sets.** Today vocabularies live in ≥3 places (`config.DOMAIN_VALUES`, `classify_utils.ALLOWED_VALUES_BY_COLUMN`, per-nature dicts) — the cause of the drift. `config` becomes the single catalog; `classify_utils` and `schema_checks` **derive** their lists from it.
2. **Casing normalizer = one function** (`classify_utils`, reads `config.casing_rule`) applied pre-persist in `_normalize_record` — values cannot mutate mid-flow.
3. **NULL vs `Not Applicable`** stated once and enforced: `Not Applicable` = structurally inapplicable (a value, in the set); NULL = applies but undisclosed. INTER completeness checks test both branches; never `'N/A'`/`'None'`/`''`.
4. **Derived columns as views** (`Currency_Hedged` from `Hedging_Policy`, `Is_ESG` from `Sfdr_Article`) — removes the redundancy class permanently (cost-view precedent).

---

## 9. Job B status — DELIVERED (2026-06-07)
Implemented, AST-clean, integration-tested against in-memory SQLite (DDL, view, enum CHECK, migration idempotency, `BOTH_FAIL`-survives-CACHED, 58-col rebuild DDL, callable contract). Files: `config.py`, `classify_utils.py` (`cost_values_agree`), `core/cost_arbitration.py` (new), `dla2_xband_prototype.py` (per-component accessor), `schema_fondos.sql`, `schema_checks.py`, `sqlite_writer.py` (`upsert_kiid_metadata` +6), `pipeline.py` (hook + `pdf_bytes` lifetime), `migrate_v19_to_v20.py` (new; Job B runs by default, fund_master rebuild guarded behind `--rebuild-fund-master`). Behind `DLA2_ARBITRATION_ENABLED=False`.

---
*Grounded in: pipeline.py (446, 500–575, 1571–1690), io.py (592–720), sqlite_writer.py (405, 524–556, 706–786), config.py (57, 100–111, 220–443), schema_checks.py (35–115), dla2_dual_strategy_compare.py (471–497, 504, 577–610), dla2_xband_prototype.py (177–205, 468–516). Inventory recovered from the originating analysis chat / `fund_master_schema_proposal.xlsx`.*
