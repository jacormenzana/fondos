# PROJECT MEMORY — Investment Fund Analysis Platform (`fondos`)

**Version:** 2.0
**Updated:** 2026-06-06
**Schema:** v19+
**Supersedes:** `memoryv1.md`
**Re-grounded on:** `CUSTOM_INSTRUCTIONS_V6.0`, `p1_export_20260606.xlsx`, and direct `grep` verification of `classify_utils.py` (2026-06-06).

> **Status legend (applies throughout).** Never assume a function/value exists because this memory describes it.
> - **[VERIFIED]** — measured against the live `fund_master` export on 2026-06-06. A point-in-time baseline, not an invariant. Re-measure; don't trust blindly.
> - **[CODED]** — function/logic confirmed present in the named module by `grep` on 2026-06-06. Re-confirm before editing.
> - **[SPEC]** — agreed target behaviour, **not yet implemented**. Implementing it is a task; never call it as if it exists, never report it as done.

---

## 1. PURPOSE & CONTEXT

Jose is building a Spanish-language investment-fund analysis platform at `C:\desarrollo\fondos\` processing ~3,205 European UCITS/OICVM funds across three phases:
- **P1** — fund discovery & classification (from KIID/DDF regulatory PDFs).
- **P2** — quantitative enrichment with macro factors.
- **P3** — regime-based portfolio selection & scoring.

**Investment objective:** capital preservation relative to IPC + M3, targeting ~6–7% nominal annual return, maximum tolerable drawdown 15%, horizon 3–5 years.

**Stack:** Python (Conda env **`des`**) on Windows. SQLite DB at `db/fondos.sqlite`, schema **v19+**, WAL mode. Single connection source: `shared/db.py::get_connection()`.

**Architecture (modular):** `proyecto1/core/` (core modules), `proyecto1/blocks/` (classification blocks), `shared/` (config). Key modules: `pipeline.py`, `classify_utils.py`, `sqlite_writer.py`, `io.py`, `kiid_parser.py`, `fund_characterizer.py`, `dla_extractor.py`, and blocks: `monetarios.py`, `rf_corto.py`, `rf_flexible.py`, `renta_variable.py`, `mixtos.py`, `alternativos.py`, `restantes.py`.

**Pipeline order:** text extraction (`kiid_parser.py`) → block classification (`blocks/*.py`: MONETARY, FI-SHORT, FI-FLEX, EQUITY, MIXED, ALTERNATIVE, REMAINING) → characterization (`fund_characterizer.py`) → semantic validation (`classify_utils.py`) → persistence (`sqlite_writer.py`).

---

## 2. WORKSTREAM STATUS DASHBOARD

| Workstream | Phase | Status | Blocker / Next |
|---|---|---|---|
| **BL-DLA-2 / BL-COST** — PRIIPs Cat.2A cost extraction | P1 enrich | Active | Close v1.4 bidirectional-rescue corpus validation (~6–10 conflicts expected) |
| **P1 classification quality** | P1 | Active | Implement [SPEC] INTER rules + migrations (see §5, §6) |
| **BD `Ongoing_Charge_Recurrent` remediation** | P1 | Identified | Column wrong on 600+ funds (0/15 ground-truth) — never use as truth |
| **BL-COST-5** — OC/ACI mismatch (~328 funds, ACI@RHP mislabeled as TER) | P1 | Blocked | Pending dedicated Opus architectural review |
| **BL-DLA-3** — Cat.3 (PRIIPs performance scenarios) extraction | P1 | Blocked | Pending BL-DLA-2 closure + P2/P3 consumer docs |
| **P2 macro factors** | P2 | Partial | Ingest `spread_hy` (BAMLH0A0HYM2), `vix` (VIXCLS), `term_spread` (T10Y2YM); OLS regression derivatives pending |
| **P3 regime-dependent scoring** | P3 | Blocked | Pending P1 classification-quality fixes |
| **`migrate_v18_to_v19.py`** | infra | Defect | Must also rebuild FK-dependent tables during migration |

---

## 3. COST EXTRACTION (BL-DLA-2 / BL-COST) — DETAIL

Extracting PRIIPs Cat.2A cost-table data (**Ongoing Charge = management cost + operation cost**) from ~3,205 KIIDs into SQLite.

**Architecture — dual extractor (pdfplumber):**
- **bands-X strategy** — borderless / plain-text layouts.
- **ruled-table strategy** (`lines` strategy) — bordered / ruled tables (e.g., **DWS family ~425 funds**).
- **Arbitration by result quality, NOT fixed priority** — empirically validated. Jose correctly predicted a "ruled-first" approach would regress borderless funds; arbitration-by-quality is the confirmed correct architecture.

**Progress:** successive corpus runs (3,205 PDFs each) have progressively cut CONFLICT counts. The **v1.4 bidirectional rescue fix** was pending a corpus run at last session end (expected ~6–10 conflicts).

**Cost-data invariants (hard):**
- `BD.Ongoing_Charge_Recurrent` is **systematically wrong on 600+ funds** (0/15 correct on ground-truth). **Never** use it as ground truth or tiebreaker.
- Small operation-cost values (0.02%, 0.07%, 0.22%) are **real** industry-standard transaction-cost ranges — do not dismiss as artifacts.
- **PDF inspection is the only valid evidence** for cost-extraction decisions.
- Never declare "100% solved" on small ground-truth samples — validate at corpus scale (3,205).

**Cost provenance fields (no language):** `Fee_Known_Flag` (EXTRACTED / ZERO_CONFIRMED / NOT_FOUND) · `KID_Format` (PRIIPS_KID / UCITS_KIID) · `Cost_Extraction_Quality` (HIGH / MEDIUM_EUR / MEDIUM_CROSS / MEDIUM_PCT / LOW / NONE).

---

## 4. P1 CLASSIFICATION — VERIFIED COLUMN INVENTORY (2026-06-06)

Each column holds values in **one language**. [VERIFIED] no column is internally mixed-language; 0 padding/casing/whitespace anomalies. Real risks are **declaration drift** (docs vs DB) and **schema drift** (new columns / changed value sets).

### 4.1 Spanish columns
| Column | Top values (counts) |
|---|---|
| **Fund_Nature** | Renta Variable (1645), Mixtos (497), Renta Fija Flexible (473), Renta Fija Corto Plazo (448), Alternativo (68), Monetario (43), Restantes (23), Estructurado (8) |
| **Profile** | Dinámico (1702), Moderado (977), Conservador (525) |
| **Strategy** | Activo (3080), Indexado (98), Pasivo (26) |
| **Geography** | Europa (1057), Global (833), EEUU (799), Asia (129), Emergentes (91), China (83), Japón (35), India (24), Latinoamérica (11), Europa del Este (6) |

### 4.2 English columns
DB target language for **Type, Family, Subtype, Theme** is **English**. **Sector_Focus** target is **Spanish** (legacy `SECTOR_FOCUS_TRANSLATION_MAP`). All translation maps live exclusively in `classify_utils.py` (R-1).

| Column | Top values (counts) | Notes |
|---|---|---|
| **Type** | Active Management (1554), Allocation (497), Flexible Fixed Income (467), Short-Term Fixed Income (395), Index Fund (98), Absolute Return (52), Money Market (43), Short-Term Credit (43), Commodities (16), Structured (8), Floating Rate CP (7), Target Maturity (6), Total Return (5), Tactical Allocation (4), Short-Term Government (3), Government Money Market (2), Real Assets (1), Prime Money Market (1) | EN |
| **Family** | Equity Core (1430), Short-Term Fixed Income (448), Flexible Fixed Income (419), Multi-Asset (397), Thematic Equity (222), Income Oriented (104), Absolute Return (51), Money Market (46), High Yield (46), Real Assets (17), Structured (8), Emerging Market Debt (6), Inflation-Linked (4), Strategic Allocation (4) | EN |
| **Subtype** | mostly NULL (2872); Index Fund (83), Standard MMF (48), Opportunistic (48), ETF (40), VNAV (27), Physical/Derivatives (16), Low Duration (11), LVNAV (10), Autocallable (8), Floating Rate Notes (7), Total Return Bond (6), Global Macro (5), CNAV (4), Convertibles (4), … | EN. LVNAV/VNAV/CNAV belong here, **not** Family (monetary Family is uniformly Money Market) |
| **Style_Profile** | Blend (922), Strategic Allocation (501), Income (325), Value (253), Growth (251), Not Applicable (81), Low Volatility (18), Quality (7), Momentum (6), Tactical (4), Risk Control (1) | EN |
| **Theme** | Core/General (2927), Technology (97), Healthcare (35), Energy (25), Water (19), Gold (18), AI (13), Robotics (9), Digital (8), Financials (8), Climate/Clean Energy (8), Mining (7), Real Estate (6), Cybersecurity (6), Inflation (6), … | EN |
| **Exposure_Bias** | Long Only (1550), Duration Bias (877), Income Bias (140), Credit Bias (65), Commodity Bias (53), Absolute Return Bias (52), Liquidity Bias (49), Rate Reset Bias (11), Barrier Risk (8), Low Volatility Bias (4), Real Estate Bias (1) | EN |
| **Sector_Focus** | mostly NULL; Technology & Innovation (133), Healthcare & Life Sciences (40), Energy & Resources (33), Materials & Mining (26), Utilities & Environment (19), Financial Services (10), Real Assets (6), Consumer Discretionary (2) | **ES target** |
| **Investment_Universe** | **4 values only:** Regional (1617), Global (784), Liquidity (526), Country (161) | Sector/Thematic split out into `Investment_Focus` |
| **Investment_Focus** 🆕 | Broad (2912), Sector (228), Thematic (50) | new column |
| **Credit_Quality** 🆕 | Not Applicable (1751), Investment Grade (686), Mixed (576), High Yield (138) | new column |
| **Benchmark_Type** | REFERENCE_INDEX (1952), NO_BENCHMARK (113), TARGET_INDEX (91) | EN |
| **Accumulation_Policy** | ACCUMULATION (2417), DISTRIBUTION (427) | EN |
| **Distribution_Frequency** | ANNUAL (95), BIANNUAL (17), MONTHLY (9), QUARTERLY (3) | EN |
| **Replication_Method** | ACTIVE (3080), PASSIVE (124) | EN |
| **Hedging_Policy** | UNHEDGED (2057), HEDGED (677), PARTIAL (1) | EN; richer than Currency_Hedged |
| **Currency_Hedged** | Unhedged (2058), Hedged (677) | redundant with Hedging_Policy (INTER-11) |
| **Derivatives_Usage** | YES (1528), NO (1274), LIMITED (402) | fully populated (historic "only YES" resolved) |
| **Leverage_Used** | NO (2476), YES (479), LIMITED (249) | EN |
| **Liquidity_Profile** | T5 (232, dominant), T1 (53), T2 (1) | EN |
| **Market_Cap_Focus** | All Cap (1277, dominant), Large Cap (376), Small Cap (91), Mid Cap (69), SMID Cap (7) | EN |
| **SRRI_Quality_Flag** | HIGH (3009), MEDIUM_VISUAL (108), MEDIUM_TEXT (67), NONE (17), LOW_CONFLICT (3) | EN |
| **Data_Quality_Flag** | OK (3183), MISSING (17), WARN (4) | EN |

**Numeric / flag fields (no language):** SRRI (1–7) · Is_ESG (0/1) · Sfdr_Article (6/8/9) · Recommended_Holding_Period (1Y/3Y/5Y/7Y) · Fund_Currency / Portfolio_Currency (ISO) · plus the cost provenance fields in §3.

---

## 5. SEMANTIC VALIDATION — CODE STATE vs SPEC (grep-verified 2026-06-06)

All rules consolidate in `validate_all_semantic_consistency(fund_record)` → returns `{is_valid, critical_errors, warnings, corrected_record}`.

**[CODED] validators present in `classify_utils.py`:** `validate_strategy_replication` (2552), `validate_accumulation_distribution` (2578, **3-tuple** signature per BL-32), `validate_profile_srri` (2630), `validate_nature_type_coherence` (2660), `validate_nature_family_coherence` (2687), `validate_universe_completeness` (2713, **old single-axis name**), `validate_leverage_profile` (2742), `validate_esg_sfdr` (2756), `validate_theme_sector_coherence` (2772), `validate_geography_universe` (2807), `validate_all_semantic_consistency` (2842).

**[SPEC] — NOT in code:** `validate_universe_focus_completeness` (two-axis successor), `validate_nature_credit_quality` (INTER-12), `validate_nature_style` (INTER-13), `validate_nature_marketcap` (INTER-14), `validate_nature_sector` (INTER-15), `validate_srri_rhp` (INTER-16), `validate_subtype_nature` (INTER-17).

### 5.1 INTER rule register
| Rule | Meaning | Status | [VERIFIED] baseline |
|---|---|---|---|
| **INTER-1** | Strategy ↔ Replication_Method (Indexado/Pasivo ⇒ PASSIVE; auto-correct) | [CODED] guard | 0 (was 12) |
| **INTER-2** | Accumulation ⇒ Distribution_Frequency NULL | [CODED] | 2 (ACC+ANNUAL) |
| **INTER-3** | Profile ↔ SRRI — **target: warnings-only** | ⚠️ **see drift register §5.2** | Conservador SRRI≥5 = 1; Dinámico SRRI≤2 = 40 |
| **INTER-4** | Fund_Nature → Type (auto-correct) | [CODED] | 1 anomaly: `LU1165137495` |
| **INTER-5** | Fund_Nature → Family (auto-correct) | [CODED] | 1 anomaly: `LU1165137495` |
| **INTER-6** | Two-axis completeness (Universe + Focus) | [SPEC] (old single-axis is coded) | Sector 228/228 ✅; geo 100% |
| **INTER-7** | Leverage_Used ↔ Profile (Conservador+YES → WARN) | [CODED] | 107 |
| **INTER-8** | Is_ESG ↔ Sfdr_Article (flag Is_ESG=0 & Sfdr∈{8,9}) | [CODED] verify bidirectional | Is_ESG=0&Sfdr=9 = 4; =8 = 0 |
| **INTER-9** | Theme ↔ Sector_Focus | [CODED] | 1 anomaly |
| **INTER-10** | Geography ↔ Investment_Universe (single-country ≠ Global) | [CODED] guard | 0 |
| **INTER-11** | Hedging_Policy ↔ Currency_Hedged | [VERIFIED] RESOLVED — 100% redundant | 0 mismatch; consolidate (see §7-C) |
| **INTER-12** | Fund_Nature → Credit_Quality (RV ⇒ Not Applicable) | [SPEC] | 2 RV rated IG (auto-correct candidates) |
| **INTER-13** | Fund_Nature → Style_Profile | [SPEC] | Strategic Allocation 492/501 on Mixtos |
| **INTER-14** | Fund_Nature → Market_Cap_Focus (NULL for pure RF/Mon/Estr) | [SPEC] | ~4 short-FI tagged All Cap |
| **INTER-15** | Fund_Nature → Sector_Focus (NULL for pure RF/Mon) | [SPEC] | ~1 (RFCP + Financial Services) |
| **INTER-16** | SRRI ↔ Recommended_Holding_Period (monotone) | [SPEC] | — |
| **INTER-17** | Subtype → Fund_Nature | [SPEC] | — |

### 5.2 SPEC-vs-CODE DRIFT REGISTER ⚠️ (grep-verified findings — V6 was inaccurate here)
1. **INTER-3 is NOT redesigned in code.** V6 marked it `[CODED — warnings-only]`. The deployed `validate_profile_srri` (2630) **still remaps Profile from SRRI**: `Conservador & srri≥5 → recalc`, `Agresivo & srri≤4 → recalc`; only `Dinámico & srri≤2` warns. The warnings-only redesign is in reality **[SPEC]**, not [CODED]. **Action:** treat the redesign as a task; do not report INTER-3 as warnings-only until code is changed.
2. **`Agresivo` branch is dead code.** `validate_profile_srri` tests `profile == "Agresivo"`, but the current Profile value set is only {Conservador, Moderado, Dinámico}. That branch never fires. Remove on next touch.
3. **`_assign_profile_from_srri` (2617) is NOT unused.** It is called at 2643/2647 by `validate_profile_srri`. V6 asked to "verify it is unused" — it is **used**. Removing it requires first neutralizing the remap (finding 1).
4. **`validate_accumulation_distribution` returns a 3-tuple** `(val_ap, val_df, msg)` (BL-32), not the 2-tuple shown in V6 §3.5. Any caller/snippet assuming 2-tuple is wrong.
5. **Stale comment confirmed** at `classify_utils.py:~2031`: `# BL-56/BL-57: TYPE_TRANSLATION_MAP — idioma objetivo: español`, contradicting the BL-LANG-EN override (lines ~5/8/199/1529) that set Type→**English**. Fix the comment when next touching that block (Principle #2, single source of truth).

### 5.3 RESOLVED / informational (do NOT enforce)
- **INTER-11** redundancy resolved: HEDGED↔Hedged 677, UNHEDGED↔Unhedged 2057, NULL↔NULL 470, 0 mismatch. Only edge: `Hedging_Policy='PARTIAL'` (1) collapses to `Unhedged` (info loss). Root-cause fix = consolidate (keep richer `Hedging_Policy`, derive/deprecate `Currency_Hedged`) via pipeline + migration + COALESCE review (R-2); add a 1:1 guard until then.
- **Benchmark_Type → Nature**, **Fund_Currency vs Portfolio_Currency**, **Ongoing_Charge → Profile**: informational/weak — do NOT enforce. OC unreliable on 600+ funds.

### 5.4 Validation order
```
classify_fund(kiid_text, isin):
  c = detect_and_classify_by_block(...)   # 1 block
  c = apply_language_homogeneity(c)        # 2 §4.2 ES→EN (Type/Family/Subtype/Theme)
  r = validate_all_semantic_consistency(c) # 3 §5
  if not r['is_valid']: c = r['corrected_record']  # 4 auto-correct
  c = enrich_classification(c, kiid_text)  # 5 enrich
  return c
```

---

## 6. TELEMETRY REGRESSION GATE (after every full pipeline run)

Regenerate counts with the standard control SQL and **diff against this baseline**. Any *new* critical (above baseline) must be explained before the run is accepted; any drop is reported as improvement.

```python
validation_baseline = {  # 3,205 funds, 2026-06-06
  'critical':   {'INTER-1':0, 'INTER-2':2, 'INTER-4':1, 'INTER-5':1, 'INTER-12':2, 'INTER-6':0},
  'warnings':   {'INTER-3':41, 'INTER-7':107, 'INTER-8':4, 'INTER-9':1, 'INTER-10':0},
  'redundancy': {'INTER-11':0},  # 100% redundant
}
```
**Clear regardless:** `LU1165137495` (BNP P. SMART FOOD) — Renta Variable mis-tagged Type='Money Market' (trips INTER-4 & INTER-5). Thematic equity mislabel.

---

## 7. DURABLE PRINCIPLES & PROTOCOLS

### 7.1 Meta-principles
- **#1 Root cause > symptom patch.** Fix in `classify_utils.py` so it auto-corrects across all blocks; never an `UPDATE … WHERE` the next run undoes. Narrow exception: a documented bypass-COALESCE `UPDATE` when architecturally justified.
- **#2 DRY / single source of truth.** Normalization & validation logic live **exclusively** in `classify_utils.py`. Identical logic in ≥2 places → centralize.
- **#8 Linguistic homogeneity.** One language per column; translate ES→EN before persisting Type/Family/Subtype/Theme.
- **#9 Semantic consistency.** Apply the consolidated validator everywhere (esp. REMAINING).
- **Compliance checklist before approving any solution:** eliminates cause not symptom? · prevents recurrence? · logic reusable/centralized? · holds at 10,000 funds? Any NO → redesign.

### 7.2 R-rule register (referenced across the codebase)
| R | Rule |
|---|---|
| **R-1** | Normalization logic lives exclusively in `classify_utils.py` (single source of truth). |
| **R-2** | Any persisted-attribute change needs three parts: (a) pipeline fix, (b) SQL migration, (c) COALESCE review in `publish_fund()`. |
| **R-4** | INTER rules evaluate **effective values** (`_X_p`/`_X_bd`), never just the in-memory record. Root cause of the BL-44 class of undetected-persistence bugs. |
| **R-5** | `\b` word boundaries fail on fused tokens (e.g. `EURHDG`) — use explicit character-class boundaries. |
| **R-6** | Inference functions use **bounded windows**, never full-text checks (prevents cross-section contamination). |
| **R-9** | SQL migrations use `LIKE`/`TRIM` comparisons, never exact match, to tolerate padding in production DBs. |
| *R-3 / R-7 / R-8* | Not defined in available materials — fill in if/when encountered. |

### 7.3 §A — Execution & environment constraints (hard)
1. Windows, Conda env **`des`**. Always invoke Python with **`python -X utf8`**.
2. **One-liners only** — the operator's terminal cannot run multi-line `python -c`. Deliver every `-c` and inline shell as a **single line**.
3. Paths: DB `C:\desarrollo\fondos\db\fondos.sqlite`; KIIDs `C:\data\fondos\kiid\`; diagnostics `scripts\diag\`.
4. `sys.path`: scripts in `proyecto1\` use `parents[2]` to reach project root; add `proyecto1\core\` explicitly when core modules import each other without the `core.` prefix.
5. Full pipeline runs via the `.bat` wrapper in `scripts\launch\`, **not** `run_block.py` directly.
6. Every new feature block ships behind a **kill-switch constant in `config.py`** (e.g. `PRIIPS_COST_EXTRACTION_ENABLED`, `DLA_TABLE_SERIALIZATION_ENABLED`), default **OFF** until validated.

### 7.4 §B — Working agreement / definition of done
1. **Read before write.** Read the target region first; never rewrite a whole file — surgical `str_replace` scoped to the minimum change.
2. **Verify, don't assume.** `grep` for a function before calling/reporting it. [SPEC] = work to do, never done.
3. **AST + content check after every write.** `python -X utf8 -c "import ast; ast.parse(open(r'<file>',encoding='utf-8').read())"` plus `grep` of the specific changed lines. Never declare delivery from a version header alone.
4. **Control SQL after every pipeline-affecting change** — exact query + expected delta vs §6 baseline.
5. **Explicit column-by-column DB writes** — never dynamic dict-dump.
6. **No "100% solved" on small samples** — validate at corpus scale (3,205). PDF inspection is the only valid evidence for cost decisions.
7. **Empirical > explanation** — check against telemetry / control query / PDF, don't argue.
8. **Session hygiene** — Opus for architecture & closing ambiguities; Sonnet for implementation against a closed spec. Self-contained handoff at session end. Backlog docs fully self-contained (autocontención), no incremental deltas. `CERRADO_` titles = closed sprints.

### 7.5 §C — Schema-change & idempotency protocol
- **C-1 (R-2):** new columns `Investment_Focus`/`Credit_Quality` and updated sets (Investment_Universe→4, +T5, +All Cap/SMID, +PARTIAL) each need a migration if not already applied. Migrations use `LIKE`/`TRIM`, never exact match (R-9). `migrate_v18_to_v19.py` must also rebuild FK-dependent tables.
- **C-2 CACHED/COALESCE hazard (root of BL-44 class):** `COALESCE` in `publish_fund()` preserves stale values for CACHED funds, so a corrected in-memory value may never persist. INTER rules must read effective values (`_X_p`/`_X_bd`) or do an explicit DB fallback read (`EffectiveReader` pattern). When changing a value's meaning, verify the CACHED path actually overwrites.
- **C-3 Idempotency:** a re-run over unchanged input must produce **identical** DB rows. Row changes without input changes = defect (non-deterministic classifier or COALESCE/cache interaction).

### 7.6 §D — NULL vs sentinel semantics
- **Explicit sentinel** where the schema defines a real category: `Credit_Quality='Not Applicable'`, `Style_Profile='Not Applicable'`. These are *values*, count in allowed-sets, are **not** NULL.
- **NULL** for "not applicable / undisclosed" with no sentinel: `Sector_Focus`, `Subtype`, `Distribution_Frequency` (under ACCUMULATION), `Portfolio_Currency`.
- **Never** invent a new sentinel string (`'N/A'`, `'None'`, `''`) — it fails allowed-value validation and fragments grouping.
- INTER-6/14/15 completeness checks treat NULL vs sentinel differently — always test both branches.

### 7.7 §E — Provenance & confidence
- Record **why** a categorical value was assigned where feasible: source = `name` (fund-name heuristic) · `kiid_text` (windowed extraction) · `default` (fallback) · `corrected` (INTER auto-correct). Supports auditing and the REMAINING `<0.7` path.
- REMAINING block (catch-all, historically largest inconsistency source): always run validator, use corrected record, log every correction/warning with ISIN. On confidence `<0.7`: set `Fund_Nature='Restantes'`, `Type=None`, `Family=None`, flag for manual review.

---

## 8. TOOLS & RESOURCES
- **Runtime:** Python, Conda env `des`, Windows, `python -X utf8`.
- **DB:** SQLite `C:\desarrollo\fondos\db\fondos.sqlite` (v19+); `shared/db.py` single-source `get_connection()` (WAL).
- **PDF extraction:** pdfplumber (primary; `lines` strategy for ruled tables), PyMuPDF/fitz (DLA extractor), Tesseract OCR (fallback).
- **Data files:** KIIDs at `C:\data\fondos\kiid\`; diagnostic scripts in `scripts\diag\`.
- **External data:** FRED API (macro series); Morningstar SAL service (`api-global.morningstar.com/sal-service/v1/fund/performance/v4/`) with `SecuritySearch.ashx` for ISIN→code resolution.

---

## 9. COMMUNICATION
- With Jose: **Spanish**, clear, direct, honest, detailed, executive / low-verbosity (token-conscious).
- Documentation files (this memory, custom instructions): maintained in **English** for consistency with codebase and DB target language.

---

## 10. IMMEDIATE NEXT ACTIONS (synthesized)
1. **Close BL-DLA-2:** run v1.4 bidirectional-rescue corpus validation; confirm ~6–10 conflicts; diff vs prior CONFLICT counts.
2. **Fix the INTER-3 drift (§5.2):** decide warnings-only vs current remap; if warnings-only is the target, rewrite `validate_profile_srri`, remove the dead `Agresivo` branch, retire `_assign_profile_from_srri`. Pipeline fix + control SQL + §6 baseline diff.
3. **Implement [SPEC] validators** INTER-6(two-axis), INTER-12…17 against the closed spec; wire into `validate_all_semantic_consistency`.
4. **Apply migrations (R-2)** for `Investment_Focus`, `Credit_Quality`, and updated value sets (Universe→4, +T5, +All Cap/SMID, +PARTIAL); fix `migrate_v18_to_v19.py` FK rebuild.
5. **Consolidate INTER-11** (Hedging_Policy / Currency_Hedged) as root-cause (pipeline + migration + COALESCE), not a patch.
6. **Fix stale comment** at `classify_utils.py:~2031`.
7. **Clear `LU1165137495`** anomaly (INTER-4/5).

**END — Memory v2.0**
