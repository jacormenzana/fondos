# CUSTOM INSTRUCTIONS V4 - COMPLETE AND EXHAUSTIVE
# Investment Fund Classification - Funds Project

**Update:** June 6, 2026
**Version:** 4.0 (Sections 2 & 3 re-grounded on the live `fund_master` export `p1_export_20260606.xlsx`; Section 6 relations promoted to concrete rules)
**Schema:** v19+ (categorical + numerical + flags as enumerated in §2.1)

> **Evidence basis (R-principle: empirical verification over speculation).** Every value set, allowed-value list, language assignment and affected-fund count in §2 and §3 is derived directly from a full read of the 3,205-fund `1_FundMaster` export, not from prior assumptions. Counts are point-in-time (2026-06-06) and serve as health baselines, not invariants.

---

## 1. OPERATIONAL CONTEXT

This project implements an automated classification system for European investment funds (~3,205 funds) based on the analysis of KIID/DDF documents (regulatory PDFs).

**Classification pipeline:**
* 1. Text extraction from KIID/DDF → `kiid_parser.py`
* 2. Specialized classification by blocks → `blocks/*.py` (MONETARY, FI-SHORT, FI-FLEX, EQUITY, MIXED, ALTERNATIVE, REMAINING)
* 3. Characterization and enrichment → `fund_characterizer.py`
* 4. Semantic consistency validation → `classify_utils.py`
* 5. Persistence in SQLite → `sqlite_writer.py`

---

## 1.1 FUNDAMENTAL PRINCIPLES (Meta-level)

These are the cross-cutting meta-principles that guide ALL project development. They are not specific technical rules, but the architectural philosophy that founds every design and implementation decision.

**Context:** In an automated classification system that processes ~3,205 funds, the accumulation of inconsistencies and duplication of logic can rapidly degrade data quality and code maintainability. These principles prevent that degradation from the root.

### **PRINCIPLE #1: Root Cause Analysis > Symptom Patches** — CRITICAL

When managing, analyzing, or fixing dysfunctions in any phase, focus **exclusively** on identifying and resolving the **root cause**. Never propose temporary solutions that only mitigate symptoms.

* **INCORRECT (symptomatic patch):** `UPDATE fund_master SET Replication_Method='PASSIVE' WHERE Strategy IN ('Indexado','Pasivo') AND Replication_Method<>'PASSIVE';` — fixes only today's rows; the next pipeline run regenerates the inconsistency.
* **CORRECT (root cause fix):** Add the coherence rule in `classify_utils.py` so it auto-corrects in ALL blocks, preventing every future occurrence.

### **PRINCIPLE #2: Scalability and DRY**

Maximize reusability through modular architecture. It is strictly prohibited to duplicate business logic across modules. When identical or similar requirements appear in multiple areas, centralize them in a single reusable function (e.g. `validate_all_semantic_consistency()` in `classify_utils.py`, consumed by every block). Normalization logic must live exclusively in `classify_utils.py` (single source of truth).

### **Connection with Specific Principles**

Principle #8 (Linguistic Homogeneity, §2) and Principle #9 (Semantic Consistency, §3) are concrete applications of these meta-principles: #9 defines validations that **prevent** inconsistencies at the source; #8 and #9 define centralized functions all blocks reuse.

### **Compliance Checklist**

Before approving any solution: (1) does it eliminate the cause, not the symptom? (2) does it prevent recurrence? (3) does the logic already exist elsewhere (reuse)? (4) is it centralized? (5) will it still hold at 10,000 funds? If any answer is NO → redesign.

---

## 2. PRINCIPLE #8: LINGUISTIC HOMOGENEITY

**General rule:** Each column must hold values in ONE language only, to facilitate queries, grouping and maintainability.

**Status of the corpus (2026-06-06):** No column is *internally* mixed-language. A full scan of every categorical column returned **0 padding, casing or whitespace anomalies**. The issues to fix are therefore not value-level leaks but (a) **declaration drift** — three attributes silently flipped Spanish→English in the DB while older instructions still declared Spanish — and (b) **schema drift** — value sets changed and two new columns appeared that were never documented.

### 2.1 TARGET LANGUAGE BY COLUMN (re-grounded on live data)

Legend: ✅ = declaration already matches DB · 🔁 = **language re-declared this version** · 🆕 = **column newly documented** · 📐 = **value set updated**

**Spanish columns (Spanish-market nomenclature):**

| Column | Lang | Observed value set (with counts) |
|---|---|---|
| **Fund_Nature** | ✅ ES | Renta Variable (1645), Mixtos (497), Renta Fija Flexible (473), Renta Fija Corto Plazo (448), Alternativo (68), Monetario (43), Restantes (23), Estructurado (8) |
| **Profile** | ✅ ES | Dinámico (1702), Moderado (977), Conservador (525) |
| **Strategy** | ✅ ES | Activo (3080), Indexado (98), Pasivo (26) |
| **Geography** | ✅ ES | Europa (1057), Global (833), EEUU (799), Asia (129), Emergentes (91), China (83), Japón (35), India (24), Latinoamérica (11), Europa del Este (6). *`Global`/`Asia`/`China`/`India` are language-neutral.* |

**English columns:**

| Column | Lang | Observed value set (with counts) |
|---|---|---|
| **Type** | 🔁 EN | Active Management (1554), Allocation (497), Flexible Fixed Income (467), Short-Term Fixed Income (395), Index Fund (98), Absolute Return (52), Money Market (43), Short-Term Credit (43), Commodities (16), Structured (8), Floating Rate CP (7), Target Maturity (6), Total Return (5), Tactical Allocation (4), Short-Term Government (3), Government Money Market (2), Real Assets (1), Prime Money Market (1) |
| **Family** | 🔁 EN | Equity Core (1430), Short-Term Fixed Income (448), Flexible Fixed Income (419), Multi-Asset (397), Thematic Equity (222), Income Oriented (104), Absolute Return (51), Money Market (46), High Yield (46), Real Assets (17), Structured (8), Emerging Market Debt (6), Inflation-Linked (4), Strategic Allocation (4) |
| **Subtype** | 🔁 EN | Index Fund (83), Standard MMF (48), Opportunistic (48), ETF (40), VNAV (27), Physical / Derivatives (16), Low Duration (11), LVNAV (10), Autocallable (8), Floating Rate Notes (7), Total Return Bond (6), Global Macro (5), CNAV (4), Convertibles (4), Fixed Band 15/50/75 (3 each), Volatility Target (3), Real Estate (1), Relative Value / Arbitrage (1), Long/Short (1), Absolute Return (1). *Mostly NULL (2872).* |
| **Style_Profile** | ✅ EN | Blend (922), Strategic Allocation (501), Income (325), Value (253), Growth (251), Not Applicable (81), Low Volatility (18), Quality (7), Momentum (6), Tactical (4), Risk Control (1) |
| **Theme** | ✅ EN | Core/General (2927), Technology (97), Healthcare (35), Energy (25), Water (19), Gold (18), Artificial Intelligence (13), Robotics (9), Digital (8), Financials (8), Climate / Clean Energy (8), Mining (7), Real Estate (6), Cybersecurity (6), Inflation (6), Megatrends (3), Silver Economy (3), Biotechnology (2), Insurance (2), Consumer Brands (2) |
| **Exposure_Bias** | ✅ EN | Long Only (1550), Duration Bias (877), Income Bias (140), Credit Bias (65), Commodity Bias (53), Absolute Return Bias (52), Liquidity Bias (49), Rate Reset Bias (11), Barrier Risk (8), Low Volatility Bias (4), Real Estate Bias (1) |
| **Sector_Focus** | ✅ EN | Technology & Innovation (133), Healthcare & Life Sciences (40), Energy & Resources (33), Materials & Mining (26), Utilities & Environment (19), Financial Services (10), Real Assets (6), Consumer Discretionary (2). *Mostly NULL.* |
| **Investment_Universe** | 📐 EN | **Now 4 values:** Regional (1617), Global (784), Liquidity (526), Country (161). *The former `Sector`/`Thematic` values were split out into the new `Investment_Focus` column.* |
| **Investment_Focus** | 🆕 EN | Broad (2912), Sector (228), Thematic (50). *New axis that absorbed the Sector/Thematic dimension previously inside Investment_Universe.* |
| **Credit_Quality** | 🆕 EN | Not Applicable (1751), Investment Grade (686), Mixed (576), High Yield (138). *Not documented in prior versions.* |
| **Benchmark_Type** | ✅ EN | REFERENCE_INDEX (1952), NO_BENCHMARK (113), TARGET_INDEX (91) |
| **Accumulation_Policy** | ✅ EN | ACCUMULATION (2417), DISTRIBUTION (427) |
| **Distribution_Frequency** | ✅ EN | ANNUAL (95), BIANNUAL (17), MONTHLY (9), QUARTERLY (3) |
| **Replication_Method** | ✅ EN | ACTIVE (3080), PASSIVE (124) |
| **Hedging_Policy** | 📐 EN | UNHEDGED (2057), HEDGED (677), **PARTIAL (1, new)** |
| **Currency_Hedged** | ✅ EN | Unhedged (2058), Hedged (677). *See INTER-11: fully redundant with Hedging_Policy.* |
| **Derivatives_Usage** | ✅ EN | YES (1528), NO (1274), LIMITED (402). *Now fully populated across all three (historic "only YES" problem resolved).* |
| **Leverage_Used** | ✅ EN | NO (2476), YES (479), LIMITED (249) |
| **Liquidity_Profile** | 📐 EN | **T5 (232, dominant — new), T1 (53), T2 (1).** *Prior versions declared only T1/T2.* |
| **Market_Cap_Focus** | 📐 EN | **All Cap (1277, dominant — new), Large Cap (376), Small Cap (91), Mid Cap (69), SMID Cap (7 — new).** |
| **SRRI_Quality_Flag** | ✅ EN | HIGH (3009), MEDIUM_VISUAL (108), MEDIUM_TEXT (67), NONE (17), LOW_CONFLICT (3) |
| **Data_Quality_Flag** | ✅ EN | OK (3183), MISSING (17), WARN (4) |

**Numeric / flag fields (no language; documented for completeness):** SRRI (1–7), Is_ESG (0/1), Sfdr_Article (6/8/9), Recommended_Holding_Period (e.g. 1Y, 3Y, 5Y, 7Y as code strings), Fund_Currency / Portfolio_Currency (ISO codes), Fee_Known_Flag (EXTRACTED / ZERO_CONFIRMED / NOT_FOUND), KID_Format (PRIIPS_KID / UCITS_KIID), Cost_Extraction_Quality (HIGH / MEDIUM_EUR / MEDIUM_CROSS / MEDIUM_PCT / LOW / NONE).

### 2.2 MANDATORY TRANSLATION MAPS

Because the DB target language for **Type, Family, Subtype** is now **English**, any block that still emits Spanish for these must translate before returning. These maps live in `classify_utils.py` (DRY — single source).

```python
TYPE_TRANSLATION_MAP = {  # ES legacy → EN (DB target)
    'Gestión Activa': 'Active Management',
    'Indexado': 'Index Fund',
    'Monetario': 'Money Market',
    'Monetario Público': 'Government Money Market',
    'Monetario Privado': 'Prime Money Market',
    'Renta Fija Corto Plazo': 'Short-Term Fixed Income',
    'Crédito CP': 'Short-Term Credit',
    'Gobierno CP': 'Short-Term Government',
    'Floating Rate CP': 'Floating Rate CP',
    'Renta Fija Flexible': 'Flexible Fixed Income',
    'Estructurado': 'Structured',
    # Already-English values pass through unchanged.
}

FAMILY_TRANSLATION_MAP = {  # ES legacy → EN (DB target)
    'RV Core': 'Equity Core',
    'RV Temática': 'Thematic Equity',
    'Activos Reales': 'Real Assets',
    'Renta Fija Flexible': 'Flexible Fixed Income',
    'Renta Fija Corto Plazo': 'Short-Term Fixed Income',
    'RF High Yield': 'High Yield',
    'RF Emergentes': 'Emerging Market Debt',
    'RF Inflación': 'Inflation-Linked',
    'Monetario': 'Money Market',
    'Mixtos': 'Multi-Asset',
    'Income Oriented': 'Income Oriented',
    'Flexible Estratégico': 'Strategic Allocation',
    'Retorno Absoluto': 'Absolute Return',
    'Estructurado': 'Structured',
}

SUBTYPE_TRANSLATION_MAP = {  # ES legacy → EN (DB target)
    'Fondo Indexado': 'Index Fund',
    'ETF': 'ETF',
    'Opportunistic': 'Opportunistic',
    # MMF / structured subtypes already English: Standard MMF, VNAV, LVNAV, CNAV, Autocallable, ...
}
```

> **Note on LVNAV / VNAV / CNAV:** these are no longer `Family` values; they now live in `Subtype` (Family for monetary funds is uniformly `Money Market`). Do not re-introduce them under `Family`.

### 2.3 HOMOGENEITY VALIDATION

```python
def validate_column_language_homogeneity(column_name, value):
    """Return (is_valid, corrected_value_or_None). Applies the ES→EN maps above
    for Type/Family/Subtype and rejects out-of-set values for the rest."""
    # Implementation centralized in classify_utils.py.
```

**Application:** every block applies translations and value-set validation before returning.

---

## 3. PRINCIPLE #9: SEMANTIC CONSISTENCY

All rules below are consolidated in `validate_all_semantic_consistency(fund_record)` (§3.5). INTER rules must evaluate **effective values** (`_X_p` / `_X_bd`), never just the in-memory record, to avoid the BL-44 class of undetected persistence bugs.

### 3.1 INTRA-ATTRIBUTE CONSISTENCY

**General rule:** each column must hold non-ambiguous, mutually exclusive values with consistent granularity.

**Hierarchies / defaults:**
* `Type='Money Market'` is the default monetary value (40 funds). `Government Money Market` (2) only if the KIID specifies government/public; `Prime Money Market` (1) only if it specifies prime/corporate.
* `Geography='Europa'` defaults to Western Europe. `Europa del Este` (6) only for explicit "Eastern Europe / CEE".

**Allowed values (authoritative, from live data):**

```python
ALLOWED_VALUES_BY_COLUMN = {
    'Fund_Nature': ['Renta Variable','Mixtos','Renta Fija Flexible','Renta Fija Corto Plazo',
                    'Alternativo','Monetario','Restantes','Estructurado'],
    'Profile': ['Conservador','Moderado','Dinámico'],
    'Strategy': ['Activo','Indexado','Pasivo'],
    'Replication_Method': ['ACTIVE','PASSIVE'],
    'Geography': ['Europa','Global','EEUU','Asia','Emergentes','China','Japón','India',
                  'Latinoamérica','Europa del Este'],
    'Investment_Universe': ['Regional','Global','Country','Liquidity'],          # 📐 4 values only
    'Investment_Focus': ['Broad','Sector','Thematic'],                            # 🆕
    'Credit_Quality': ['Investment Grade','High Yield','Mixed','Not Applicable'], # 🆕
    'Derivatives_Usage': ['YES','NO','LIMITED'],
    'Leverage_Used': ['YES','NO','LIMITED'],
    'Hedging_Policy': ['HEDGED','UNHEDGED','PARTIAL'],                             # 📐 +PARTIAL
    'Currency_Hedged': ['Hedged','Unhedged'],
    'Accumulation_Policy': ['ACCUMULATION','DISTRIBUTION'],
    'Distribution_Frequency': ['ANNUAL','BIANNUAL','QUARTERLY','MONTHLY'],
    'Liquidity_Profile': ['T1','T2','T5'],                                        # 📐 +T5
    'Market_Cap_Focus': ['All Cap','Large Cap','Mid Cap','Small Cap','SMID Cap'], # 📐 +All Cap,+SMID
    'Benchmark_Type': ['REFERENCE_INDEX','TARGET_INDEX','NO_BENCHMARK'],
    'SRRI_Quality_Flag': ['HIGH','MEDIUM_VISUAL','MEDIUM_TEXT','LOW_CONFLICT','NONE'],
    'Data_Quality_Flag': ['OK','WARN','MISSING'],
    # Sector_Focus, Theme, Style_Profile, Exposure_Bias, Subtype: see §2.1 observed sets.
}
```

### 3.2 INTER-ATTRIBUTE — CRITICAL RULES (auto-correct)

---

**INTER-1: Strategy ↔ Replication_Method** — *preventive guard (0 violations today)*

| Strategy | Replication_Method | Action |
|---|---|---|
| Activo | ACTIVE | coherent |
| Indexado / Pasivo | **PASSIVE** | if ACTIVE → auto-correct to PASSIVE |

```python
def validate_strategy_replication(strategy, replication):
    if strategy in ['Indexado','Pasivo'] and replication != 'PASSIVE':
        return 'PASSIVE', "Corrected Replication_Method to PASSIVE (coherence with Strategy)"
    return replication, None
```
**Affected now:** 0 (was 12). Keep as a regression guard.

---

**INTER-2: Accumulation_Policy ↔ Distribution_Frequency**

`ACCUMULATION` ⇒ `Distribution_Frequency` must be NULL. `DISTRIBUTION` may have it populated or NULL (NULL is acceptable when frequency is undisclosed).

```python
def validate_accumulation_distribution(acc_policy, dist_freq):
    if acc_policy == 'ACCUMULATION' and dist_freq is not None:
        return None, "Removed Distribution_Frequency (coherence with ACCUMULATION)"
    return dist_freq, None
```
**Affected now:** 2 (ACCUMULATION + ANNUAL).

---

**INTER-3: Profile ↔ SRRI** — ⚠️ **REDESIGNED (old strict mapping was empirically wrong)**

The previous rule auto-recomputed Profile from SRRI bands (1–3→Conservador, 4→Moderado, 5–7→Dinámico). The live distribution **falsifies** that mapping: Profile is co-determined by Fund_Nature, not by SRRI alone.

Observed (SRRI → count):
* **Conservador:** 1:109, 2:249, 3:145, 4:20, 5:1 → effective span **1–5**
* **Moderado:** 1:3, 2:327, 3:410, 4:160, 5:53, 6:10, 7:1 → effective span **1–7**
* **Dinámico:** 2:40, 3:161, 4:1085, 5:374, 6:39 → effective span **2–6**

Therefore: **do NOT auto-correct Profile from SRRI.** Replace with soft outlier WARNINGs at the genuine tails only:

```python
def validate_profile_srri(profile, srri):
    if srri is None:
        return 'OK', None
    if profile == 'Conservador' and srri >= 6:        # 0 funds today — true outlier
        return 'WARNING', f"Conservador with SRRI={srri} is anomalous (max observed 5)"
    if profile == 'Dinámico' and srri <= 2:           # 40 funds, SRRI=2 — borderline
        return 'WARNING', f"Dinámico with SRRI={srri} is low (review)"
    if profile == 'Moderado' and srri in (1, 7):      # extremes — review
        return 'WARNING', f"Moderado with SRRI={srri} is at the distribution extreme"
    return 'OK', None
```
**Affected now:** Conservador SRRI≥5 = 1; Dinámico SRRI≤2 = 40 (all SRRI=2). No auto-correction — warnings only. *(Open follow-up: model the real Profile = f(SRRI, Fund_Nature) assignment so the bands can be tightened per asset class.)*

---

**INTER-4: Fund_Nature → Type** — 🔁 **rewritten with English (DB) values**

```python
ALLOWED_TYPE_BY_NATURE = {
    'Renta Variable':         ['Active Management','Index Fund'],
    'Renta Fija Flexible':    ['Flexible Fixed Income','Total Return','Target Maturity','Absolute Return'],
    'Renta Fija Corto Plazo': ['Short-Term Fixed Income','Short-Term Credit','Floating Rate CP',
                               'Short-Term Government','Flexible Fixed Income'],
    'Monetario':              ['Money Market','Government Money Market','Prime Money Market'],
    'Mixtos':                 ['Allocation','Tactical Allocation'],
    'Alternativo':            ['Absolute Return','Commodities','Real Assets'],
    'Estructurado':           ['Structured'],
    'Restantes':              [],  # catch-all: any valid Type
}
def validate_nature_type_coherence(nature, type_val):
    allowed = ALLOWED_TYPE_BY_NATURE.get(nature, [])
    if allowed and type_val not in allowed:
        return False, f"Type '{type_val}' is not valid for Nature '{nature}'"
    return True, None
```
**Known anomaly (1):** `LU1165137495` (BNP P. SMART FOOD) is `Renta Variable` but `Type='Money Market'` — a thematic equity fund mis-tagged. Auto-correct/flag for review.

---

**INTER-5: Fund_Nature → Family** — 🔁 **rewritten with English (DB) values**

```python
ALLOWED_FAMILY_BY_NATURE = {
    'Renta Variable':         ['Equity Core','Thematic Equity'],
    'Renta Fija Flexible':    ['Flexible Fixed Income','High Yield','Emerging Market Debt',
                               'Inflation-Linked','Strategic Allocation'],
    'Renta Fija Corto Plazo': ['Short-Term Fixed Income','Emerging Market Debt'],
    'Monetario':              ['Money Market'],
    'Mixtos':                 ['Multi-Asset','Income Oriented'],
    'Alternativo':            ['Absolute Return','Real Assets'],
    'Estructurado':           ['Structured'],
    'Restantes':              [],  # catch-all
}
def validate_nature_family_coherence(nature, family):
    allowed = ALLOWED_FAMILY_BY_NATURE.get(nature, [])
    if allowed and family not in allowed:
        return False, f"Family '{family}' is not valid for Nature '{nature}'"
    return True, None
```
**Known anomaly (1):** same `LU1165137495` (`Family='Money Market'` under `Renta Variable`).

---

**INTER-6: Completeness** — 📐 **redesigned for the new two-axis schema**

The Sector/Thematic dimension moved from `Investment_Universe` into `Investment_Focus`. Completeness now spans two independent axes:

| Driver | Requirement | Live compliance |
|---|---|---|
| `Investment_Focus == 'Sector'` | `Sector_Focus` **must be populated** | 228/228 ✅ (0 violations) |
| `Investment_Focus == 'Broad'` | `Sector_Focus` **must be NULL** | 0 violations ✅ |
| `Investment_Universe ∈ {Country, Regional, Global}` | `Geography` **must be populated** | 100% ✅ |
| `Investment_Universe == 'Liquidity'` | `Geography` optional | 506 populated / 20 NULL |

```python
def validate_universe_focus_completeness(universe, focus, sector, geography):
    issues = []
    if focus == 'Sector' and sector is None:
        issues.append("Investment_Focus='Sector' requires Sector_Focus populated")
    if focus == 'Broad' and sector is not None:
        issues.append("Investment_Focus='Broad' should have Sector_Focus NULL")
    if universe in ('Country','Regional','Global') and geography is None:
        issues.append(f"Investment_Universe='{universe}' requires Geography populated")
    return len(issues) == 0, issues
```
**Affected now:** 0.

---

**INTER-12: Fund_Nature → Credit_Quality** — 🆕 **NEW (strong signal)**

| Nature | Expected Credit_Quality | Action |
|---|---|---|
| Renta Variable | **Not Applicable** | if rated (IG/HY/Mixed) → flag/auto-correct to Not Applicable |
| Monetario | Investment Grade | populated; non-IG → WARNING |
| Renta Fija Corto Plazo / Flexible | Investment Grade / High Yield / Mixed (**must be populated**) | NULL → WARNING |
| Mixtos | any (bond sleeve) or Not Applicable | informational |
| Estructurado | NULL / Not Applicable | informational |

```python
def validate_nature_credit_quality(nature, credit_quality):
    if nature == 'Renta Variable' and credit_quality not in (None, 'Not Applicable'):
        return 'Not Applicable', f"RV should be 'Not Applicable', not '{credit_quality}'"
    return credit_quality, None
```
**Affected now:** 2 RV funds rated `Investment Grade` (auto-correct candidates).

---

### 3.3 INTER-ATTRIBUTE — WARNING RULES (no auto-correction)

**INTER-7: Leverage_Used ↔ Profile.** Conservador + `Leverage_Used='YES'` → WARNING (unusual). **Affected now:** 107.

**INTER-8: Is_ESG ↔ Sfdr_Article** — ⚠️ **extended bidirectionally.** Previously only checked `Is_ESG=1`. The new violation direction is **`Is_ESG=0` with `Sfdr_Article ∈ {8,9}`**.
```python
def validate_esg_sfdr(is_esg, sfdr_article):
    if is_esg == 1 and sfdr_article not in (8, 9, None):
        return 'WARNING', f"Is_ESG=1 with Sfdr_Article={sfdr_article} (expected 8 or 9)"
    if is_esg == 0 and sfdr_article in (8, 9):
        return 'WARNING', f"Is_ESG=0 with Sfdr_Article={sfdr_article} (Article 8/9 implies ESG)"
    return 'OK', None
```
**Affected now:** `Is_ESG=0 & Sfdr=9` = 4; `Is_ESG=0 & Sfdr=8` = 0.

**INTER-9: Theme ↔ Sector_Focus** — 📐 **mapping updated to live values** (highly coherent — 1 anomaly).
```python
THEME_SECTOR_MAPPING = {
    'Technology':'Technology & Innovation','Artificial Intelligence':'Technology & Innovation',
    'Digital':'Technology & Innovation','Robotics':'Technology & Innovation',
    'Cybersecurity':'Technology & Innovation',
    'Healthcare':'Healthcare & Life Sciences','Biotechnology':'Healthcare & Life Sciences',
    'Silver Economy':'Healthcare & Life Sciences',
    'Energy':'Energy & Resources','Climate / Clean Energy':'Energy & Resources',
    'Gold':'Materials & Mining','Mining':'Materials & Mining',
    'Water':'Utilities & Environment',
    'Financials':'Financial Services','Insurance':'Financial Services',
    'Consumer Brands':'Consumer Discretionary',
    'Real Estate':'Real Assets',
    # Cross-sector themes legitimately map to NULL Sector_Focus:
    'Core/General':None,'Inflation':None,'Megatrends':None,
}
def validate_theme_sector_coherence(theme, sector_focus):
    if theme in THEME_SECTOR_MAPPING and sector_focus is not None:
        exp = THEME_SECTOR_MAPPING[theme]
        if exp and sector_focus != exp:
            return 'WARNING', f"Theme '{theme}' normally maps to '{exp}', not '{sector_focus}'"
    return 'OK', None
```
**Affected now:** 1 (`Core/General` with `Materials & Mining`).

**INTER-10: Geography ↔ Investment_Universe.** Specific single-country geographies must not be `Global`.
```python
def validate_geography_universe(geography, universe):
    if geography in ('EEUU','China','Japón','India') and universe == 'Global':
        return 'WARNING', f"Specific Geography '{geography}' with Universe='Global' is unusual"
    return 'OK', None
```
**Affected now:** 0 (none of these geographies is currently `Global`). Keep as guard.

**INTER-13: Fund_Nature → Style_Profile** — 🆕 **NEW.** `Strategic Allocation` is the Mixtos style; equity styles (Blend/Growth/Value/Quality/Momentum/Low Volatility) belong to Renta Variable.
```python
EQUITY_STYLES = {'Blend','Growth','Value','Quality','Momentum','Low Volatility'}
def validate_nature_style(nature, style):
    if style == 'Strategic Allocation' and nature != 'Mixtos':
        return 'WARNING', f"Style 'Strategic Allocation' expected on Mixtos, not '{nature}'"
    if style in EQUITY_STYLES and nature not in ('Renta Variable','Mixtos','Restantes'):
        return 'WARNING', f"Equity style '{style}' unusual on Nature '{nature}'"
    return 'OK', None
```
Observed: Strategic Allocation 492/501 on Mixtos; equity styles ~99% on RV. Minor RF leaks (Value/Blend) → review.

**INTER-14: Fund_Nature → Market_Cap_Focus** — 🆕 **NEW.** Populated for equity exposure (RV, equity sleeve of Mixtos); should be NULL for pure RF / Monetario / Estructurado.
```python
def validate_nature_marketcap(nature, mcap):
    if mcap is not None and nature in ('Renta Fija Corto Plazo','Monetario','Estructurado'):
        return 'WARNING', f"Market_Cap_Focus='{mcap}' unusual on Nature '{nature}'"
    return 'OK', None
```
**Affected now:** ~4 short-term-FI funds tagged `All Cap`.

**INTER-15: Fund_Nature → Sector_Focus** — 🆕 **NEW.** Relevant for RV / Alternativo (real assets) / sector-Mixtos; should be NULL for pure RF and Monetario.
```python
def validate_nature_sector(nature, sector):
    if sector is not None and nature in ('Renta Fija Corto Plazo','Monetario'):
        return 'WARNING', f"Sector_Focus='{sector}' unusual on Nature '{nature}'"
    return 'OK', None
```
**Affected now:** ~1 (RFCP with Financial Services).

**INTER-16: SRRI ↔ Recommended_Holding_Period** — 🆕 **NEW.** RHP rises monotonically with SRRI (SRRI 1 → 1D–1Y; SRRI ≥4 → 5Y–7Y). Flag the tails:
```python
SHORT_RHP = {'1D','1M','3M','6M','1Y'}
def validate_srri_rhp(srri, rhp):
    if srri is None or rhp is None:
        return 'OK', None
    if srri >= 4 and rhp in SHORT_RHP:
        return 'WARNING', f"SRRI={srri} with short RHP='{rhp}' is inconsistent"
    if srri == 1 and rhp in {'5Y','7Y'}:
        return 'WARNING', f"SRRI=1 with long RHP='{rhp}' is inconsistent"
    return 'OK', None
```

**INTER-17: Subtype → Fund_Nature** — 🆕 **NEW.** Passive subtypes (Index Fund, ETF) imply RV + PASSIVE; MMF subtypes (Standard MMF, VNAV, LVNAV, CNAV) imply Monetario or Short-Term FI; Autocallable implies Estructurado.
```python
SUBTYPE_NATURE = {
    'Index Fund':{'Renta Variable'}, 'ETF':{'Renta Variable'},
    'Standard MMF':{'Monetario','Renta Fija Corto Plazo'},
    'VNAV':{'Monetario','Renta Fija Corto Plazo'}, 'LVNAV':{'Monetario','Renta Fija Corto Plazo'},
    'CNAV':{'Monetario'}, 'Autocallable':{'Estructurado'},
    'Physical / Derivatives':{'Alternativo'},
}
def validate_subtype_nature(subtype, nature):
    allowed = SUBTYPE_NATURE.get(subtype)
    if allowed and nature not in allowed:
        return 'WARNING', f"Subtype '{subtype}' unusual on Nature '{nature}'"
    return 'OK', None
```

### 3.4 RESOLVED / INFORMATIONAL

**INTER-11: Hedging_Policy ↔ Currency_Hedged** — ✅ **RESOLVED (was "pending investigation").** The two columns are **100% redundant**: HEDGED↔Hedged (677), UNHEDGED↔Unhedged (2057), NULL↔NULL (470), **0 mismatches**. The single edge case is `Hedging_Policy='PARTIAL'` (1 fund), which `Currency_Hedged` collapses to `Unhedged` (information loss).
**Recommendation (root-cause):** deprecate one column — keep `Hedging_Policy` (richer: supports `PARTIAL`) and derive `Currency_Hedged` as a view, OR drop `Currency_Hedged` entirely. Requires pipeline fix + SQL migration + optional COALESCE change (R-2). Until consolidated, add a guard asserting the 1:1 mapping so divergence is caught early.

**Benchmark_Type → Nature** *(informational, weak):* no strong dependency; only stable fact is Estructurado → NULL benchmark. Do not enforce.

**Fund_Currency vs Portfolio_Currency** *(informational, weak):* `Portfolio_Currency` populated for only 43 funds; the EUR-fund/USD-portfolio pattern (31) reflects share-class vs base currency, not an error. Do not enforce.

**Ongoing_Charge → Profile** *(informational, low priority):* OC rises with risk profile (median Conservador 1.00%, Moderado 1.61%, Dinámico 1.82%). Given documented OC unreliability on 600+ funds, treat any Conservador-with-very-high-OC signal as soft WARNING only; never use OC as ground truth.

### 3.5 CONSOLIDATED VALIDATOR (master function)

```python
def validate_all_semantic_consistency(fund_record):
    """Validate ALL semantic rules (INTRA + INTER). Returns:
       {'is_valid':bool,'critical_errors':list,'warnings':list,'corrected_record':dict}"""
    critical, warnings = [], []
    rec = fund_record.copy()

    # --- CRITICAL (auto-correct) ---
    v, e = validate_strategy_replication(rec.get('Strategy'), rec.get('Replication_Method'))
    if e: critical.append({'rule':'INTER-1','message':e}); rec['Replication_Method'] = v

    v, e = validate_accumulation_distribution(rec.get('Accumulation_Policy'), rec.get('Distribution_Frequency'))
    if e: critical.append({'rule':'INTER-2','message':e}); rec['Distribution_Frequency'] = v

    ok, e = validate_nature_type_coherence(rec.get('Fund_Nature'), rec.get('Type'))
    if not ok: critical.append({'rule':'INTER-4','message':e})

    ok, e = validate_nature_family_coherence(rec.get('Fund_Nature'), rec.get('Family'))
    if not ok: critical.append({'rule':'INTER-5','message':e})

    ok, errs = validate_universe_focus_completeness(rec.get('Investment_Universe'),
                rec.get('Investment_Focus'), rec.get('Sector_Focus'), rec.get('Geography'))
    if not ok: warnings += [{'rule':'INTER-6','message':m} for m in errs]

    v, e = validate_nature_credit_quality(rec.get('Fund_Nature'), rec.get('Credit_Quality'))
    if e: critical.append({'rule':'INTER-12','message':e}); rec['Credit_Quality'] = v

    # --- WARNING (no auto-correct) ---
    for rule, fn, args in [
        ('INTER-3',  validate_profile_srri,        (rec.get('Profile'), rec.get('SRRI'))),
        ('INTER-7',  validate_leverage_profile,    (rec.get('Profile'), rec.get('Leverage_Used'))),
        ('INTER-8',  validate_esg_sfdr,            (rec.get('Is_ESG'), rec.get('Sfdr_Article'))),
        ('INTER-9',  validate_theme_sector_coherence,(rec.get('Theme'), rec.get('Sector_Focus'))),
        ('INTER-10', validate_geography_universe,  (rec.get('Geography'), rec.get('Investment_Universe'))),
        ('INTER-13', validate_nature_style,        (rec.get('Fund_Nature'), rec.get('Style_Profile'))),
        ('INTER-14', validate_nature_marketcap,    (rec.get('Fund_Nature'), rec.get('Market_Cap_Focus'))),
        ('INTER-15', validate_nature_sector,       (rec.get('Fund_Nature'), rec.get('Sector_Focus'))),
        ('INTER-16', validate_srri_rhp,            (rec.get('SRRI'), rec.get('Recommended_Holding_Period'))),
        ('INTER-17', validate_subtype_nature,      (rec.get('Subtype'), rec.get('Fund_Nature'))),
    ]:
        status, msg = fn(*args)
        if status == 'WARNING': warnings.append({'rule':rule,'message':msg})

    # --- INTRA allowed-values ---
    for col, val in rec.items():
        if col in ALLOWED_VALUES_BY_COLUMN and val is not None:
            if val not in ALLOWED_VALUES_BY_COLUMN[col]:
                warnings.append({'rule':'Allowed-Values','message':f"{col}='{val}' not in allowed set"})

    return {'is_valid': len(critical)==0, 'critical_errors':critical,
            'warnings':warnings, 'corrected_record':rec}
```

---

## 4. BLOCK-SPECIFIC INSTRUCTIONS

### 4.1 REMAINING BLOCK
Catch-all; historically the largest source of inconsistencies. MANDATORY: run `validate_all_semantic_consistency()` always; use the corrected record before returning; log every correction and warning with ISIN. On low confidence (`< 0.7`): set `Fund_Nature='Restantes'`, `Type=None`, `Family=None`, log for manual review.

### 4.2 SPECIALIZED BLOCKS (MONETARY, FI-SHORT, FI-FLEX, EQUITY, MIXED, ALTERNATIVE)
Recommended (not mandatory if confidence is high): run the validator; auto-correct only on detected inconsistency; log warnings.

---

## 5. VALIDATION PRIORITIZATION

```python
def classify_fund(kiid_text, isin):
    c = detect_and_classify_by_block(kiid_text, isin)   # 1. specialized block
    c = apply_language_homogeneity(c)                    # 2. §2 translations (Type/Family/Subtype ES→EN)
    r = validate_all_semantic_consistency(c)             # 3. §3 semantic validation
    if not r['is_valid']: c = r['corrected_record']      # 4. auto-correct
    c = enrich_classification(c, kiid_text)              # 5. enrichment
    return c
```

---

## 6. RELATIONS — STATUS

The 10 relations formerly "pending analysis" have been quantitatively analyzed against the live export and **promoted into §3** as follows:

| Former item | Outcome |
|---|---|
| Nature→Type→Family triangular | INTER-4 + INTER-5 (covered) |
| Subtype → Nature | **INTER-17** (new) |
| Benchmark_Type → Nature/Type | §3.4 informational (weak, not enforced) |
| Style_Profile → Nature | **INTER-13** (new) |
| Investment_Universe → Theme | folded into INTER-6 / INTER-9 axis logic |
| Market_Cap_Focus → Nature | **INTER-14** (new) |
| Sector_Focus → Nature/Type | **INTER-15** (new) |
| Recommended_Holding_Period → SRRI | **INTER-16** (new) |
| Ongoing_Charge → Profile | §3.4 informational (low priority; OC unreliable) |
| Fund_Currency vs Portfolio_Currency | §3.4 informational (weak) |

**Remaining open follow-up:** model the empirical `Profile = f(SRRI, Fund_Nature)` so INTER-3 bands can be tightened per asset class; resolve the INTER-11 column consolidation (deprecate `Currency_Hedged`).

---

## 7. ERROR MANAGEMENT, LOGGING & METRICS

### 7.1 Severity levels
`log_error` (critical, must correct) · `log_warning` (unusual but possible) · `log_info` (correction applied). Always include ISIN.

### 7.2 Validation metrics — baseline 2026-06-06 (3,205 funds)

```python
validation_baseline = {
    'total_funds': 3205,
    'critical': {
        'INTER-1_strategy_replication': 0,
        'INTER-2_accumulation_distribution': 2,
        'INTER-4_nature_type_anomaly': 1,     # LU1165137495
        'INTER-5_nature_family_anomaly': 1,   # LU1165137495 (same fund)
        'INTER-12_RV_credit_quality': 2,
        'INTER-6_completeness': 0,
    },
    'warnings': {
        'INTER-3_profile_srri_outliers': 41,  # Cons SRRI>=5:1 ; Din SRRI<=2:40
        'INTER-7_conservador_leverage_yes': 107,
        'INTER-8_esg0_sfdr9': 4,
        'INTER-9_theme_sector': 1,
        'INTER-10_geo_global': 0,
    },
    'redundancy': {'INTER-11_hedging_currencyhedged_mismatch': 0},  # 100% redundant
}
```

---

## 8. COMMUNICATION
Clear, direct, concise, honest. English is the documentation language for this file.

---

## 9. MANDATORY ACTIONS SUMMARY

* **§2 / Principle #8:** translate **Type, Family, Subtype** ES→EN before persisting (DB target is now English); validate against the live allowed-value sets; document & validate the new columns **Investment_Focus** and **Credit_Quality** and the updated sets (Investment_Universe=4 values, +T5, +All Cap/SMID, +PARTIAL).
* **§3 / Principle #9:** apply the consolidated validator everywhere, especially REMAINING. Auto-correct INTER-1/2/4/5/12; warn on INTER-3/7/8/9/10/13/14/15/16/17.
* **Do NOT** auto-correct Profile from SRRI (INTER-3 redesigned to warnings).
* **Detect explicitly:** Derivatives_Usage NO/LIMITED, Currency_Hedged Unhedged (already populated — keep coverage).
* **Resolve:** INTER-11 column consolidation (root-cause, not a patch).
* **Log** every correction and warning with ISIN.

---

**END OF CUSTOM INSTRUCTIONS V4**
*Version 4.0 — Sections 2 & 3 re-grounded on `p1_export_20260606.xlsx`; Section 6 relations operationalized as INTER-12…INTER-17.*
