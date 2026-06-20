# CUSTOM INSTRUCTIONS V6 — Investment Fund Classification (Funds Project)

**Update:** 2026-06-06
**Version:** 6.0
**Schema:** v19+
**Supersedes:** V5.0 (re-grounded on `p1_export_20260606.xlsx`)

**What changed vs V5 (read first):** V5 conflated three different kinds of statement in the present tense — *verified DB facts*, *implemented code*, and *target specification*. V6 separates them with an explicit status legend (§0), corrects the spec-vs-code drift (INTER-12…17 are **not yet coded**; §3 tags every rule), and adds the operational instruction types V5 lacked: execution constraints (§A), the working agreement / definition-of-done (§B), the schema-change & idempotency protocol (§C), NULL/sentinel semantics (§D), provenance & confidence (§E), and the telemetry regression gate (§7). The empirical content of V5 (value sets, counts, INTER logic) is preserved verbatim where still valid.

---

## 0. DOCUMENT CONTRACT & STATUS LEGEND  ⚠️ READ BEFORE ACTING

Every normative statement in this file carries one of three statuses. **Never assume a function exists because this document describes it.** Verify against the code first (see §B).

| Tag | Meaning | Obligation when you act on it |
|---|---|---|
| **[VERIFIED]** | Measured against the live `fund_master` export on 2026-06-06. A point-in-time *baseline*, not an invariant. | Treat counts as health baselines; re-measure, don't trust blindly. |
| **[CODED]** | The function/logic exists in the named module **as of this writing**. | Confirm with `grep` before calling/editing (§B-2). |
| **[SPEC]** | Target behaviour **agreed but not yet implemented in code**. | Implementing it is a task. Do NOT call it as if it exists; do NOT report it as done. |

**Implementation reality snapshot (2026-06-06, verified by grep on `classify_utils.py`):**
- **[CODED]** validators present: `validate_strategy_replication`, `validate_accumulation_distribution`, `validate_profile_srri`, `validate_nature_type_coherence`, `validate_nature_family_coherence`, `validate_universe_completeness` *(old name/signature)*, `validate_leverage_profile`, `validate_esg_sfdr`, `validate_theme_sector_coherence`, `validate_geography_universe`, `validate_all_semantic_consistency`.
- **[SPEC]** NOT yet in code: `validate_universe_focus_completeness` (two-axis successor of the old one), `validate_nature_credit_quality` (INTER-12), and INTER-13…17 (`validate_nature_style`, `validate_nature_marketcap`, `validate_nature_sector`, `validate_srri_rhp`, `validate_subtype_nature`).
- **Known code defect:** stale comment at `classify_utils.py:~2032` declares `TYPE_TRANSLATION_MAP — idioma objetivo: español`, contradicting the BL-LANG-EN override (lines ~8/199/2038) that set Type→English. Fix the comment when next touching that block (single-source-of-truth, Principle #2).

---

## 1. OPERATIONAL CONTEXT

Automated classification of ~3,205 European UCITS/OICVM funds from KIID/DDF regulatory PDFs.

**Pipeline:** (1) text extraction → `kiid_parser.py` · (2) specialized classification by block → `blocks/*.py` (MONETARY, FI-SHORT, FI-FLEX, EQUITY, MIXED, ALTERNATIVE, REMAINING) · (3) characterization → `fund_characterizer.py` · (4) semantic validation → `classify_utils.py` · (5) persistence → `sqlite_writer.py`.

**DB:** SQLite `db/fondos.sqlite`, schema v19+, WAL mode; single connection source `shared/db.py::get_connection()`.

---

## 1.1 FUNDAMENTAL PRINCIPLES (meta-level)

**#1 — Root cause > symptom patch (CRITICAL).** Fix the cause in `classify_utils.py` so it auto-corrects in *all* blocks; never an `UPDATE … WHERE` that the next pipeline run undoes. Narrow exception: a bypass-COALESCE `UPDATE` is permissible only when architecturally justified and documented.

**#2 — DRY / single source of truth.** Normalization and validation logic live **exclusively** in `classify_utils.py`. No duplicated business logic across blocks. If identical logic appears in ≥2 places, centralize it.

**Compliance checklist (before approving any solution):** eliminates cause not symptom? · prevents recurrence? · logic reusable / already exists? · centralized? · holds at 10,000 funds? Any NO → redesign.

**These principles bind Claude too:** they govern not just the fund data but how code changes are made (§B) and how the schema evolves (§C).

---

## 2. PRINCIPLE #8 — LINGUISTIC HOMOGENEITY

**Rule:** each column holds values in ONE language. **[VERIFIED 2026-06-06]** No column is internally mixed-language; 0 padding/casing/whitespace anomalies. The real risks are *declaration drift* (instructions disagreeing with the DB) and *schema drift* (new columns / changed value sets).

### 2.1 TARGET LANGUAGE BY COLUMN  [VERIFIED]
Legend: ✅ declaration matches DB · 🔁 language re-declared in V5 · 🆕 newly documented · 📐 value set updated

**Spanish columns:**

| Column | Lang | Observed value set (counts) |
|---|---|---|
| **Fund_Nature** | ✅ ES | Renta Variable (1645), Mixtos (497), Renta Fija Flexible (473), Renta Fija Corto Plazo (448), Alternativo (68), Monetario (43), Restantes (23), Estructurado (8) |
| **Profile** | ✅ ES | Dinámico (1702), Moderado (977), Conservador (525) |
| **Strategy** | ✅ ES | Activo (3080), Indexado (98), Pasivo (26) |
| **Geography** | ✅ ES | Europa (1057), Global (833), EEUU (799), Asia (129), Emergentes (91), China (83), Japón (35), India (24), Latinoamérica (11), Europa del Este (6) |

**English columns:**

| Column | Lang | Observed value set (counts) |
|---|---|---|
| **Type** | 🔁 EN | Active Management (1554), Allocation (497), Flexible Fixed Income (467), Short-Term Fixed Income (395), Index Fund (98), Absolute Return (52), Money Market (43), Short-Term Credit (43), Commodities (16), Structured (8), Floating Rate CP (7), Target Maturity (6), Total Return (5), Tactical Allocation (4), Short-Term Government (3), Government Money Market (2), Real Assets (1), Prime Money Market (1) |
| **Family** | 🔁 EN | Equity Core (1430), Short-Term Fixed Income (448), Flexible Fixed Income (419), Multi-Asset (397), Thematic Equity (222), Income Oriented (104), Absolute Return (51), Money Market (46), High Yield (46), Real Assets (17), Structured (8), Emerging Market Debt (6), Inflation-Linked (4), Strategic Allocation (4) |
| **Subtype** | 🔁 EN | Index Fund (83), Standard MMF (48), Opportunistic (48), ETF (40), VNAV (27), Physical / Derivatives (16), Low Duration (11), LVNAV (10), Autocallable (8), Floating Rate Notes (7), Total Return Bond (6), Global Macro (5), CNAV (4), Convertibles (4), Fixed Band 15/50/75 (3 each), Volatility Target (3), Real Estate (1), Relative Value / Arbitrage (1), Long/Short (1), Absolute Return (1). Mostly NULL (2872). |
| **Style_Profile** | ✅ EN | Blend (922), Strategic Allocation (501), Income (325), Value (253), Growth (251), Not Applicable (81), Low Volatility (18), Quality (7), Momentum (6), Tactical (4), Risk Control (1) |
| **Theme** | ✅ EN | Core/General (2927), Technology (97), Healthcare (35), Energy (25), Water (19), Gold (18), Artificial Intelligence (13), Robotics (9), Digital (8), Financials (8), Climate / Clean Energy (8), Mining (7), Real Estate (6), Cybersecurity (6), Inflation (6), Megatrends (3), Silver Economy (3), Biotechnology (2), Insurance (2), Consumer Brands (2) |
| **Exposure_Bias** | ✅ EN | Long Only (1550), Duration Bias (877), Income Bias (140), Credit Bias (65), Commodity Bias (53), Absolute Return Bias (52), Liquidity Bias (49), Rate Reset Bias (11), Barrier Risk (8), Low Volatility Bias (4), Real Estate Bias (1) |
| **Sector_Focus** | ✅ EN | Technology & Innovation (133), Healthcare & Life Sciences (40), Energy & Resources (33), Materials & Mining (26), Utilities & Environment (19), Financial Services (10), Real Assets (6), Consumer Discretionary (2). Mostly NULL. |
| **Investment_Universe** | 📐 EN | **4 values:** Regional (1617), Global (784), Liquidity (526), Country (161). Sector/Thematic split out into `Investment_Focus`. |
| **Investment_Focus** | 🆕 EN | Broad (2912), Sector (228), Thematic (50). |
| **Credit_Quality** | 🆕 EN | Not Applicable (1751), Investment Grade (686), Mixed (576), High Yield (138). |
| **Benchmark_Type** | ✅ EN | REFERENCE_INDEX (1952), NO_BENCHMARK (113), TARGET_INDEX (91) |
| **Accumulation_Policy** | ✅ EN | ACCUMULATION (2417), DISTRIBUTION (427) |
| **Distribution_Frequency** | ✅ EN | ANNUAL (95), BIANNUAL (17), MONTHLY (9), QUARTERLY (3) |
| **Replication_Method** | ✅ EN | ACTIVE (3080), PASSIVE (124) |
| **Hedging_Policy** | 📐 EN | UNHEDGED (2057), HEDGED (677), PARTIAL (1, new) |
| **Currency_Hedged** | ✅ EN | Unhedged (2058), Hedged (677). See INTER-11: redundant with Hedging_Policy. |
| **Derivatives_Usage** | ✅ EN | YES (1528), NO (1274), LIMITED (402). Fully populated (historic "only YES" resolved). |
| **Leverage_Used** | ✅ EN | NO (2476), YES (479), LIMITED (249) |
| **Liquidity_Profile** | 📐 EN | T5 (232, dominant — new), T1 (53), T2 (1) |
| **Market_Cap_Focus** | 📐 EN | All Cap (1277, dominant — new), Large Cap (376), Small Cap (91), Mid Cap (69), SMID Cap (7 — new) |
| **SRRI_Quality_Flag** | ✅ EN | HIGH (3009), MEDIUM_VISUAL (108), MEDIUM_TEXT (67), NONE (17), LOW_CONFLICT (3) |
| **Data_Quality_Flag** | ✅ EN | OK (3183), MISSING (17), WARN (4) |

**Numeric / flag fields (no language):** SRRI (1–7) · Is_ESG (0/1) · Sfdr_Article (6/8/9) · Recommended_Holding_Period (code strings, e.g. 1Y/3Y/5Y/7Y) · Fund_Currency / Portfolio_Currency (ISO) · Fee_Known_Flag (EXTRACTED/ZERO_CONFIRMED/NOT_FOUND) · KID_Format (PRIIPS_KID/UCITS_KIID) · Cost_Extraction_Quality (HIGH/MEDIUM_EUR/MEDIUM_CROSS/MEDIUM_PCT/LOW/NONE).

### 2.2 TRANSLATION MAPS  [CODED in classify_utils.py — verify before edit]
DB target for **Type, Family, Subtype, Theme** is **English**; **Sector_Focus** target is **Spanish** (legacy `SECTOR_FOCUS_TRANSLATION_MAP`, normalize then pass-through). Any block emitting Spanish for an English-target column must translate before returning. Maps are the single source in `classify_utils.py`.

```python
TYPE_TRANSLATION_MAP = {  # ES legacy → EN (DB target); EN canonical values pass through
    'Gestión Activa':'Active Management', 'Indexado':'Index Fund', 'Monetario':'Money Market',
    'Monetario Público':'Government Money Market', 'Monetario Privado':'Prime Money Market',
    'Renta Fija Corto Plazo':'Short-Term Fixed Income', 'Crédito CP':'Short-Term Credit',
    'Gobierno CP':'Short-Term Government', 'Floating Rate CP':'Floating Rate CP',
    'Renta Fija Flexible':'Flexible Fixed Income', 'Estructurado':'Structured',
}
FAMILY_TRANSLATION_MAP = {  # ES legacy → EN
    'RV Core':'Equity Core', 'RV Temática':'Thematic Equity', 'Activos Reales':'Real Assets',
    'Renta Fija Flexible':'Flexible Fixed Income', 'Renta Fija Corto Plazo':'Short-Term Fixed Income',
    'RF High Yield':'High Yield', 'RF Emergentes':'Emerging Market Debt',
    'RF Inflación':'Inflation-Linked', 'Monetario':'Money Market', 'Mixtos':'Multi-Asset',
    'Income Oriented':'Income Oriented', 'Flexible Estratégico':'Strategic Allocation',
    'Retorno Absoluto':'Absolute Return', 'Estructurado':'Structured',
}
SUBTYPE_TRANSLATION_MAP = {  # ES legacy → EN
    'Fondo Indexado':'Index Fund', 'ETF':'ETF', 'Opportunistic':'Opportunistic',
    # MMF/structured subtypes already EN: Standard MMF, VNAV, LVNAV, CNAV, Autocallable, ...
}
```
> **LVNAV/VNAV/CNAV** live in `Subtype`, not `Family` (monetary Family is uniformly `Money Market`). Do not re-introduce under `Family`.

---

## 3. PRINCIPLE #9 — SEMANTIC CONSISTENCY

All rules consolidate in `validate_all_semantic_consistency(fund_record)` (§3.5). **INTER rules must evaluate effective values (`_X_p` / `_X_bd`), never just the in-memory record** — this was the root cause of the BL-44 class of undetected persistence bugs (R-4). See §C-2 on the CACHED/COALESCE hazard.

### 3.1 INTRA-ATTRIBUTE  [CODED — allowed-value check active]
Each column: non-ambiguous, mutually exclusive, consistent granularity.
- `Type='Money Market'` is the monetary default (40). `Government Money Market` (2) only if KIID says government/public; `Prime Money Market` (1) only if prime/corporate.
- `Geography='Europa'` = Western Europe default. `Europa del Este` (6) only for explicit "Eastern Europe / CEE".

```python
ALLOWED_VALUES_BY_COLUMN = {
    'Fund_Nature':['Renta Variable','Mixtos','Renta Fija Flexible','Renta Fija Corto Plazo',
                   'Alternativo','Monetario','Restantes','Estructurado'],
    'Profile':['Conservador','Moderado','Dinámico'],
    'Strategy':['Activo','Indexado','Pasivo'],
    'Replication_Method':['ACTIVE','PASSIVE'],
    'Geography':['Europa','Global','EEUU','Asia','Emergentes','China','Japón','India',
                 'Latinoamérica','Europa del Este'],
    'Investment_Universe':['Regional','Global','Country','Liquidity'],          # 📐 4 only
    'Investment_Focus':['Broad','Sector','Thematic'],                            # 🆕
    'Credit_Quality':['Investment Grade','High Yield','Mixed','Not Applicable'], # 🆕
    'Derivatives_Usage':['YES','NO','LIMITED'],
    'Leverage_Used':['YES','NO','LIMITED'],
    'Hedging_Policy':['HEDGED','UNHEDGED','PARTIAL'],                             # 📐 +PARTIAL
    'Currency_Hedged':['Hedged','Unhedged'],
    'Accumulation_Policy':['ACCUMULATION','DISTRIBUTION'],
    'Distribution_Frequency':['ANNUAL','BIANNUAL','QUARTERLY','MONTHLY'],
    'Liquidity_Profile':['T1','T2','T5'],                                        # 📐 +T5
    'Market_Cap_Focus':['All Cap','Large Cap','Mid Cap','Small Cap','SMID Cap'],  # 📐 +All Cap,+SMID
    'Benchmark_Type':['REFERENCE_INDEX','TARGET_INDEX','NO_BENCHMARK'],
    'SRRI_Quality_Flag':['HIGH','MEDIUM_VISUAL','MEDIUM_TEXT','LOW_CONFLICT','NONE'],
    'Data_Quality_Flag':['OK','WARN','MISSING'],
}
```

### 3.2 INTER — CRITICAL (auto-correct)

**INTER-1 — Strategy ↔ Replication_Method** [CODED] · *preventive guard*
Indexado/Pasivo ⇒ PASSIVE; if ACTIVE → auto-correct. **[VERIFIED]** affected: 0 (was 12). Keep as regression guard.

**INTER-2 — Accumulation ↔ Distribution_Frequency** [CODED]
ACCUMULATION ⇒ Distribution_Frequency NULL; DISTRIBUTION may be populated or NULL. **[VERIFIED]** affected: 2 (ACCUMULATION+ANNUAL).

**INTER-3 — Profile ↔ SRRI** [CODED — but logic REDESIGNED; verify code matches]
⚠️ Old strict band-remap was empirically wrong; Profile is co-determined by Fund_Nature. **Do NOT auto-correct Profile from SRRI.** Warn only at genuine tails:
```python
def validate_profile_srri(profile, srri):
    if srri is None: return 'OK', None
    if profile=='Conservador' and srri>=6: return 'WARNING', f"Conservador SRRI={srri} anomalous (max obs 5)"
    if profile=='Dinámico'   and srri<=2: return 'WARNING', f"Dinámico SRRI={srri} low (review)"
    if profile=='Moderado'   and srri in (1,7): return 'WARNING', f"Moderado SRRI={srri} at extreme"
    return 'OK', None
```
**[VERIFIED]** Conservador SRRI≥5 = 1 (SRRI=5); Dinámico SRRI≤2 = 40 (all SRRI=2). *Open follow-up: model Profile = f(SRRI, Fund_Nature) to tighten bands per asset class.* **Action:** confirm the deployed `validate_profile_srri` no longer remaps (the old `_assign_profile_from_srri` may still be present — verify it is unused).

**INTER-4 — Fund_Nature → Type** [CODED] · 🔁 EN values
```python
ALLOWED_TYPE_BY_NATURE = {
    'Renta Variable':['Active Management','Index Fund'],
    'Renta Fija Flexible':['Flexible Fixed Income','Total Return','Target Maturity','Absolute Return'],
    'Renta Fija Corto Plazo':['Short-Term Fixed Income','Short-Term Credit','Floating Rate CP',
                              'Short-Term Government','Flexible Fixed Income'],
    'Monetario':['Money Market','Government Money Market','Prime Money Market'],
    'Mixtos':['Allocation','Tactical Allocation'],
    'Alternativo':['Absolute Return','Commodities','Real Assets'],
    'Estructurado':['Structured'], 'Restantes':[],  # catch-all
}
```
**[VERIFIED]** anomaly (1): `LU1165137495` (BNP P. SMART FOOD) is Renta Variable but Type='Money Market' — mis-tagged thematic equity. Flag/auto-correct.

**INTER-5 — Fund_Nature → Family** [CODED] · 🔁 EN values
```python
ALLOWED_FAMILY_BY_NATURE = {
    'Renta Variable':['Equity Core','Thematic Equity'],
    'Renta Fija Flexible':['Flexible Fixed Income','High Yield','Emerging Market Debt',
                           'Inflation-Linked','Strategic Allocation'],
    'Renta Fija Corto Plazo':['Short-Term Fixed Income','Emerging Market Debt'],
    'Monetario':['Money Market'], 'Mixtos':['Multi-Asset','Income Oriented'],
    'Alternativo':['Absolute Return','Real Assets'], 'Estructurado':['Structured'], 'Restantes':[],
}
```
**[VERIFIED]** anomaly (1): same `LU1165137495`.

**INTER-6 — Completeness (two-axis)** [SPEC — code still has old `validate_universe_completeness`]
Sector/Thematic moved from Investment_Universe → Investment_Focus, so completeness now spans two axes:
| Driver | Requirement | [VERIFIED] compliance |
|---|---|---|
| `Investment_Focus=='Sector'` | Sector_Focus populated | 228/228 ✅ |
| `Investment_Focus=='Broad'` | Sector_Focus NULL | 0 violations ✅ |
| `Investment_Universe ∈ {Country,Regional,Global}` | Geography populated | 100% ✅ |
| `Investment_Universe=='Liquidity'` | Geography optional | 506 / 20 NULL |
```python
def validate_universe_focus_completeness(universe, focus, sector, geography):  # SPEC — to implement
    issues=[]
    if focus=='Sector' and sector is None: issues.append("Focus='Sector' requires Sector_Focus")
    if focus=='Broad'  and sector is not None: issues.append("Focus='Broad' should have Sector_Focus NULL")
    if universe in ('Country','Regional','Global') and geography is None:
        issues.append(f"Universe='{universe}' requires Geography")
    return len(issues)==0, issues
```
**Task:** rename/replace the old `validate_universe_completeness` (it references the pre-split single axis). Per §C, this is a logic change to a validator — confirm no other caller depends on the old name.

**INTER-12 — Fund_Nature → Credit_Quality** [SPEC — NOT in code]
| Nature | Expected | Action |
|---|---|---|
| Renta Variable | Not Applicable | if rated → auto-correct to Not Applicable |
| Monetario | Investment Grade | non-IG → WARNING |
| RF Corto / Flexible | IG / HY / Mixed (populated) | NULL → WARNING |
| Mixtos / Estructurado | any / NULL | informational |
```python
def validate_nature_credit_quality(nature, cq):  # SPEC — to implement
    if nature=='Renta Variable' and cq not in (None,'Not Applicable'):
        return 'Not Applicable', f"RV should be 'Not Applicable', not '{cq}'"
    return cq, None
```
**[VERIFIED]** affected: 2 RV funds rated Investment Grade (auto-correct candidates).

### 3.3 INTER — WARNING (no auto-correct)

**INTER-7 — Leverage_Used ↔ Profile** [CODED]. Conservador+YES → WARNING. **[VERIFIED]** 107.
**INTER-8 — Is_ESG ↔ Sfdr_Article** [CODED — verify bidirectional]. Also flag `Is_ESG=0 & Sfdr∈{8,9}`. **[VERIFIED]** Is_ESG=0&Sfdr=9 = 4; =8 = 0.
**INTER-9 — Theme ↔ Sector_Focus** [CODED]. Updated mapping; cross-sector themes (Core/General, Inflation, Megatrends) → NULL sector. **[VERIFIED]** anomaly 1 (Core/General with Materials & Mining).
**INTER-10 — Geography ↔ Investment_Universe** [CODED]. Single-country geo (EEUU/China/Japón/India) must not be Global. **[VERIFIED]** 0. Keep guard.
**INTER-13 — Fund_Nature → Style_Profile** [SPEC]. `Strategic Allocation`→Mixtos; equity styles (Blend/Growth/Value/Quality/Momentum/Low Volatility)→RV/Mixtos/Restantes. **[VERIFIED]** Strategic Allocation 492/501 on Mixtos; minor RF Value/Blend leaks → review.
**INTER-14 — Fund_Nature → Market_Cap_Focus** [SPEC]. Should be NULL for pure RF/Monetario/Estructurado. **[VERIFIED]** ~4 short-FI tagged All Cap.
**INTER-15 — Fund_Nature → Sector_Focus** [SPEC]. Should be NULL for pure RF/Monetario. **[VERIFIED]** ~1 (RFCP + Financial Services).
**INTER-16 — SRRI ↔ Recommended_Holding_Period** [SPEC]. RHP monotone with SRRI; flag SRRI≥4 with short RHP, SRRI=1 with 5Y/7Y.
**INTER-17 — Subtype → Fund_Nature** [SPEC]. Index Fund/ETF→RV; MMF subtypes→Monetario/RF-Corto; Autocallable→Estructurado; Physical/Derivatives→Alternativo.

### 3.4 RESOLVED / INFORMATIONAL
**INTER-11 — Hedging_Policy ↔ Currency_Hedged** [VERIFIED — RESOLVED]. 100% redundant (HEDGED↔Hedged 677, UNHEDGED↔Unhedged 2057, NULL↔NULL 470, 0 mismatches). Only edge: `Hedging_Policy='PARTIAL'` (1) collapses to `Unhedged` (info loss). **Root-cause action (per §C):** keep richer `Hedging_Policy`, deprecate/derive `Currency_Hedged` as a view, or drop it — needs pipeline fix + migration + COALESCE review (R-2). Until then add a 1:1 guard.
**Benchmark_Type → Nature**, **Fund_Currency vs Portfolio_Currency**, **Ongoing_Charge → Profile**: informational/weak — do NOT enforce. OC is unreliable on 600+ funds; never ground-truth.

### 3.5 CONSOLIDATED VALIDATOR  [CODED for INTER-1/2/3/4/5/7/8/9/10 + old completeness; INTER-6(new)/12/13/14/15/16/17 are SPEC]
Target shape once §3.2/§3.3 SPEC rules land:
```python
def validate_all_semantic_consistency(fund_record):
    """Return {'is_valid':bool,'critical_errors':list,'warnings':list,'corrected_record':dict}"""
    critical, warnings = [], []; rec = fund_record.copy()
    # CRITICAL (auto-correct)
    v,e = validate_strategy_replication(rec.get('Strategy'), rec.get('Replication_Method'))
    if e: critical.append({'rule':'INTER-1','message':e}); rec['Replication_Method']=v
    v,e = validate_accumulation_distribution(rec.get('Accumulation_Policy'), rec.get('Distribution_Frequency'))
    if e: critical.append({'rule':'INTER-2','message':e}); rec['Distribution_Frequency']=v
    ok,e = validate_nature_type_coherence(rec.get('Fund_Nature'), rec.get('Type'))
    if not ok: critical.append({'rule':'INTER-4','message':e})
    ok,e = validate_nature_family_coherence(rec.get('Fund_Nature'), rec.get('Family'))
    if not ok: critical.append({'rule':'INTER-5','message':e})
    ok,errs = validate_universe_focus_completeness(rec.get('Investment_Universe'),       # SPEC
                rec.get('Investment_Focus'), rec.get('Sector_Focus'), rec.get('Geography'))
    if not ok: warnings += [{'rule':'INTER-6','message':m} for m in errs]
    v,e = validate_nature_credit_quality(rec.get('Fund_Nature'), rec.get('Credit_Quality'))  # SPEC
    if e: critical.append({'rule':'INTER-12','message':e}); rec['Credit_Quality']=v
    # WARNING
    for rule, fn, args in [
        ('INTER-3', validate_profile_srri, (rec.get('Profile'), rec.get('SRRI'))),
        ('INTER-7', validate_leverage_profile, (rec.get('Profile'), rec.get('Leverage_Used'))),
        ('INTER-8', validate_esg_sfdr, (rec.get('Is_ESG'), rec.get('Sfdr_Article'))),
        ('INTER-9', validate_theme_sector_coherence, (rec.get('Theme'), rec.get('Sector_Focus'))),
        ('INTER-10', validate_geography_universe, (rec.get('Geography'), rec.get('Investment_Universe'))),
        ('INTER-13', validate_nature_style, (rec.get('Fund_Nature'), rec.get('Style_Profile'))),       # SPEC
        ('INTER-14', validate_nature_marketcap, (rec.get('Fund_Nature'), rec.get('Market_Cap_Focus'))),# SPEC
        ('INTER-15', validate_nature_sector, (rec.get('Fund_Nature'), rec.get('Sector_Focus'))),       # SPEC
        ('INTER-16', validate_srri_rhp, (rec.get('SRRI'), rec.get('Recommended_Holding_Period'))),     # SPEC
        ('INTER-17', validate_subtype_nature, (rec.get('Subtype'), rec.get('Fund_Nature'))),           # SPEC
    ]:
        status, msg = fn(*args)
        if status=='WARNING': warnings.append({'rule':rule,'message':msg})
    for col,val in rec.items():
        if col in ALLOWED_VALUES_BY_COLUMN and val is not None and val not in ALLOWED_VALUES_BY_COLUMN[col]:
            warnings.append({'rule':'Allowed-Values','message':f"{col}='{val}' not in allowed set"})
    return {'is_valid':len(critical)==0,'critical_errors':critical,'warnings':warnings,'corrected_record':rec}
```

---

## 4. BLOCK-SPECIFIC

**4.1 REMAINING** — catch-all, historically largest inconsistency source. MANDATORY: always run the validator; use corrected record; log every correction/warning with ISIN. On confidence `<0.7`: set `Fund_Nature='Restantes'`, `Type=None`, `Family=None`, log for manual review.

**4.2 SPECIALIZED** (MONETARY, FI-SHORT, FI-FLEX, EQUITY, MIXED, ALTERNATIVE) — recommended (not mandatory if confidence high): run validator; auto-correct only on detected inconsistency; log warnings.

---

## 5. VALIDATION ORDER
```python
def classify_fund(kiid_text, isin):
    c = detect_and_classify_by_block(kiid_text, isin)   # 1 block
    c = apply_language_homogeneity(c)                    # 2 §2 ES→EN (Type/Family/Subtype/Theme)
    r = validate_all_semantic_consistency(c)             # 3 §3
    if not r['is_valid']: c = r['corrected_record']      # 4 auto-correct
    c = enrich_classification(c, kiid_text)              # 5 enrich
    return c
```

---

## 6. RELATIONS — STATUS
INTER-4/5 cover Nature→Type→Family · INTER-13/14/15/16/17 = [SPEC] new · INTER-11 resolved (consolidate) · Benchmark/Currency/OC = informational. **Open follow-ups:** model `Profile=f(SRRI,Fund_Nature)`; execute INTER-11 column consolidation (root-cause, with migration).

---

## 7. ERROR MANAGEMENT, LOGGING & TELEMETRY GATE

**7.1 Severity:** `log_error` (must correct) · `log_warning` (unusual) · `log_info` (correction applied). Always include ISIN.

**7.2 Baseline [VERIFIED 2026-06-06] — treat as a REGRESSION GATE, not a printout.** After every full pipeline run, regenerate these counts with the standard control SQL and **diff against this baseline**. Any *new* critical (count above baseline) must be explained before the run is accepted as good; any drop is reported as an improvement.
```python
validation_baseline = {  # 3,205 funds
  'critical': {'INTER-1':0,'INTER-2':2,'INTER-4':1,'INTER-5':1,'INTER-12':2,'INTER-6':0},
  'warnings': {'INTER-3':41,'INTER-7':107,'INTER-8':4,'INTER-9':1,'INTER-10':0},
  'redundancy': {'INTER-11':0},  # 100% redundant
}
```
Anomaly to clear regardless: `LU1165137495` (INTER-4 & INTER-5).

---

## A. EXECUTION & ENVIRONMENT CONSTRAINTS  🆕 (was missing in V5)

Honor these in every command/snippet handed to the operator — they are hard constraints of the runtime, not preferences.
1. **OS/runtime:** Windows, Conda env **`des`**. Invoke Python with **`python -X utf8`** always (encoding-safe).
2. **One-liners only:** the operator's terminal cannot execute multi-line `python -c`. Deliver every `-c` as a **single line**. Same for any inline shell.
3. **Paths:** DB at `C:\desarrollo\fondos\db\fondos.sqlite`; KIIDs at `C:\data\fondos\kiid\`; diagnostics in `scripts\diag\`.
4. **sys.path:** scripts in `proyecto1\` use `parents[2]` to reach project root; add `proyecto1\core\` explicitly when core modules import each other without the `core.` prefix.
5. **Pipeline launch:** via the `.bat` wrapper in `scripts\launch\`, not `run_block.py` directly for full runs.
6. **Feature gating:** every new feature block ships behind a **kill-switch constant in `config.py`** (pattern: `PRIIPS_COST_EXTRACTION_ENABLED`, `DLA_TABLE_SERIALIZATION_ENABLED`). Default the switch OFF until validated.

---

## B. WORKING AGREEMENT WITH CLAUDE — DEFINITION OF DONE  🆕

These govern *how* Claude edits this codebase. They are as binding as the data rules.
1. **Read before write.** Read the target file (or the exact region) before editing. Never rewrite a whole file; apply **surgical `str_replace`** scoped to the minimum change.
2. **Verify, don't assume.** Before calling or reporting any function as existing, `grep` for it. Treat §0 [SPEC] tags as work-to-do, never as done.
3. **AST + content check after every write.** Run an AST parse (`python -X utf8 -c "import ast,sys; ast.parse(open(r'<file>',encoding='utf-8').read())"`) and `grep` the *specific changed lines* to confirm the edit landed. Never declare delivery from a version header alone.
4. **Control SQL after every pipeline-affecting change.** Provide the exact query and expected delta vs the §7.2 baseline.
5. **Explicit column-by-column DB writes.** Never dynamic dict-dump to the DB.
6. **No "100% solved" on small samples.** A fix is validated only at corpus scale (3,205), never on a handful of ground-truth rows. PDF inspection is the only valid evidence for cost-extraction decisions.
7. **Empirical > explanation.** When a hypothesis can be checked against telemetry, a control query, or the PDF, check it — don't argue it.
8. **Session hygiene.** Use Opus for architecture / closing ambiguities; Sonnet for implementation against a closed spec. Produce a self-contained handoff at session end. Backlog docs must be fully self-contained (no incremental deltas referencing prior versions). `CERRADO_` titles = closed sprints.

---

## C. SCHEMA-CHANGE & IDEMPOTENCY PROTOCOL  🆕

**C-1 — R-2 (any persisted-attribute change needs three parts).** Modifying a persisted column requires: (a) **pipeline fix** (the classifier emits the new value), (b) **SQL migration** (existing rows updated), and (c) **COALESCE review** in `sqlite_writer.publish_fund()`. The new columns `Investment_Focus`/`Credit_Quality` and updated sets (Investment_Universe→4, +T5, +All Cap/SMID, +PARTIAL) each need a migration if not already applied. Migrations use `LIKE`/`TRIM` comparisons, never exact match, to tolerate padding (R-9). `migrate_v18_to_v19.py` must also rebuild FK-dependent tables.

**C-2 — CACHED / COALESCE hazard (root of BL-44 class).** `COALESCE` in `publish_fund()` preserves stale values for CACHED funds, so a corrected in-memory value may never persist. INTER rules must therefore read **effective values** (`_X_p`/`_X_bd`) or do an explicit DB fallback read (`EffectiveReader` pattern), not just the current record. When changing a value's meaning, verify the CACHED path actually overwrites.

**C-3 — Idempotency.** A re-run over unchanged input must produce **identical** DB rows. If a run changes rows without an input change, that is a defect (usually a non-deterministic classifier or a COALESCE/cache interaction) — investigate before accepting.

---

## D. NULL vs SENTINEL SEMANTICS  🆕

Be deliberate about absence; mixing conventions silently breaks INTER completeness checks.
- **Use the explicit sentinel** where the schema defines one as a real category: `Credit_Quality='Not Applicable'`, `Style_Profile='Not Applicable'`. These are *values*, count in allowed-sets, and are **not** NULL.
- **Use NULL** for "attribute not applicable / undisclosed" where no sentinel exists: `Sector_Focus`, `Subtype`, `Distribution_Frequency` (under ACCUMULATION), `Portfolio_Currency`.
- **Never** invent a new sentinel string (e.g. `'N/A'`, `'None'`, `''`) — it will fail allowed-value validation and fragment grouping.
- INTER-6/14/15 completeness checks treat NULL and the wrong-cased sentinel differently; always test both branches.

---

## E. PROVENANCE & CONFIDENCE  🆕

- Where feasible, record **why** a categorical value was assigned: source = `name` (fund-name heuristic), `kiid_text` (windowed extraction), `default` (fallback), or `corrected` (INTER auto-correct). This makes anomalies auditable and supports the REMAINING `<0.7` path.
- **Bounded windows, not full-text.** Inference functions scan bounded windows around object bounds, never the whole KIID (R-6) — prevents cross-contamination between sections.
- **Word boundaries fail on fused tokens.** `\b` breaks on sequences like `EURHDG`; use explicit character-class boundaries (R-5).
- **Cost data:** `BD.Ongoing_Charge_Recurrent` is wrong on 600+ funds — never a tiebreaker or ground truth. Small operation-cost values (0.02–0.22%) are real, not artifacts.

---

## 8. COMMUNICATION
With the operator (Jose): Spanish, clear, direct, honest, detailed. This documentation file is maintained in English (consistency with the codebase and DB target language). Keep responses executive and low-verbosity to conserve tokens.

---

## 9. MANDATORY ACTIONS SUMMARY
- **§2/#8:** translate Type/Family/Subtype/Theme ES→EN before persisting; validate against live allowed sets; **document & implement** the migrations for `Investment_Focus`, `Credit_Quality`, and updated sets; fix the stale §0 line-2032 comment.
- **§3/#9:** apply the consolidated validator everywhere (esp. REMAINING). [CODED] auto-correct INTER-1/2/4/5; warn INTER-3/7/8/9/10. **[SPEC] to implement:** INTER-6 two-axis rename, INTER-12, and INTER-13…17. Do NOT report SPEC rules as done.
- **Do NOT** auto-correct Profile from SRRI (INTER-3 is warnings-only).
- **Detect explicitly:** Derivatives_Usage NO/LIMITED; Currency_Hedged Unhedged (keep coverage).
- **Resolve:** INTER-11 consolidation as root-cause (pipeline + migration + COALESCE), not a patch.
- **Every change:** read-before-edit, surgical edit, AST + grep verification, control SQL vs §7.2 baseline, single-line commands, `python -X utf8`. Log all corrections/warnings with ISIN.
- **Clear regardless:** `LU1165137495` (INTER-4/5).

**END — V6.0**
