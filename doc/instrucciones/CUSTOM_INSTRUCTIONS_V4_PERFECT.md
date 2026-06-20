# CUSTOM INSTRUCTIONS V3 - COMPLETE AND EXHAUSTIVE
# Investment Fund Classification - Funds Project

**Update:** April 9, 2026  
**Version:** 3.0 (Exhaustive - Includes ALL INTRA and INTER attribute rules)

---

## 1. OPERATIONAL CONTEXT

This project implements an automated classification system for European investment funds (~3,200 funds) based on the analysis of KIID/DDF documents (regulatory PDFs).

**Classification pipeline:**
* 1. Text extraction from KIID/DDF → kiid_parser.py
* 2. Specialized classification by blocks → blocks/*.py (MONETARY, FI, EQ, MIXED, ALTERNATIVE, REMAINING)
* 3. Characterization and enrichment → fund_characterizer.py
* 4. Semantic consistency validation → classify_utils.py
* 5. Persistence in SQLite → sqlite_writer.py

**Schema:** v17 (25 categorical attributes, 15 numerical, 8 flags)

---

## 1.1 FUNDAMENTAL PRINCIPLES (Meta-level)

These are the **cross-cutting meta-principles** that guide ALL project development. They are not specific technical rules, but the architectural philosophy that founds every design and implementation decision.

**Context:** In an automated classification system that processes ~3,200 funds with 25 categorical attributes, the accumulation of inconsistencies and duplication of logic can rapidly degrade data quality and code maintainability. These principles prevent that degradation from the root.

---

### **PRINCIPLE #1: Root Cause Analysis > Symptom Patches** CRITICAL

**Statement:**  
When managing, analyzing, or fixing dysfunctions (bugs) in any phase (development, operation, or maintenance), you must focus **exclusively** on identifying and resolving the **root cause**.

**Strict restriction:**  
Never propose temporary solutions that only mitigate the symptoms of the problem. Always apply a preventive and definitive approach.

**Application example in this project:**

# INCORRECT (symptomatic patch):
# Problem: 12 funds have Strategy='Indexado' but Replication_Method='ACTIVE'
UPDATE fund_master 
SET Replication_Method = 'PASSIVE' 
WHERE Strategy = 'Indexado' AND Replication_Method = 'ACTIVE';

# CORRECT (root cause fix):
# Root cause: The REMAINING block does not validate Strategy-Replication coherence
# Fix: Add validation in classify_utils.py that auto-corrects in ALL blocks
def validate_strategy_replication(strategy, replication):
    if strategy in ['Indexado', 'Pasivo'] and replication != 'PASSIVE':
        return 'PASSIVE', "Auto-correction applied"
    return replication, None

**Why it is critical:**  
The SQL patch only fixes the 12 current funds. The next execution of the pipeline will generate the same inconsistency again. The root cause fix prevents **ALL** future occurrences.

---

### **PRINCIPLE #2: Scalability and DRY (Don't Repeat Yourself)**

**Statement:**  
Maximize reusability through modular architecture. It is **strictly prohibited** to duplicate business logic or create similar logics in different modules.

**Obligation:**  
When you detect identical or similar requirements in multiple areas, your duty is to design and implement **generic modules or functions** that centralize that functionality.

**Application example in this project:**

# INCORRECT (logic duplication):
# In blocks/monetarios.py:
def validate_monetarios(classification):
    if classification['Strategy'] in ['Indexado', 'Pasivo']:
        if classification['Replication_Method'] != 'PASSIVE':
            classification['Replication_Method'] = 'PASSIVE'
    return classification

# In blocks/renta_variable.py:
def validate_rv(classification):
    if classification['Strategy'] in ['Indexado', 'Pasivo']:
        if classification['Replication_Method'] != 'PASSIVE':
            classification['Replication_Method'] = 'PASSIVE'
    return classification

# In blocks/restantes.py:
def validate_restantes(classification):
    if classification['Strategy'] in ['Indexado', 'Pasivo']:
        if classification['Replication_Method'] != 'PASSIVE':
            classification['Replication_Method'] = 'PASSIVE'
    return classification

# CORRECT (DRY centralization):
# In classify_utils.py (single reusable function):
def validate_all_semantic_consistency(classification):
    """
    Validates ALL semantic rules.
    Used by ALL blocks (MONETARY, FI, EQ, MIXED, REMAINING).
    """
    # Strategy-Replication
    if classification.get('Strategy') in ['Indexado', 'Pasivo']:
        if classification.get('Replication_Method') != 'PASSIVE':
            classification['Replication_Method'] = 'PASSIVE'
    
    # ... (rest of centralized validations)
    
    return classification

# In ALL blocks:
from classify_utils import validate_all_semantic_consistency

def classify_monetarios_fund(kiid_text, isin):
    classification = { ... }
    return validate_all_semantic_consistency(classification)


**Why it is critical:**  
If you need to modify the Strategy-Replication validation (e.g., adding logging), with duplication you must modify 7 blocks. With DRY centralization, you only modify 1 function.

---

### **Connection with Specific Principles**

**Principles #8 (Linguistic Homogeneity)** and **#9 (Semantic Consistency)** are **concrete applications** of these meta-principles to the specific domain of fund classification:

* **#1 Root Cause** - Principle #9 defines validations that **prevent** inconsistencies at the source (rather than correcting them later)
* **#2 DRY** - Principle #8 and #9 define centralized functions (validate_all_semantic_consistency()) that **all** blocks reuse

---

### **Compliance Checklist**

Before approving any solution, verify:

* **Root Cause:** Does this solution eliminate the cause of the problem, not just its symptoms?
* **Preventive:** Does this solution prevent future recurrences of the same problem?
* **DRY:** Does this logic already exist in another module? If so, can I reuse it?
* **Centralization:** If I need this logic in multiple places, did I centralize it in a common module?
* **Scalable:** Will this solution continue to work when we have 10,000 funds instead of 3,200?

**If any answer is NO → Redesign the solution.**

---

## 2. PRINCIPLE #8: LINGUISTIC HOMOGENEITY

**General rule:** Each column must have values in ONLY ONE LANGUAGE to facilitate queries, groupings, and maintainability.

### 2.1 TARGET LANGUAGE BY COLUMN

* **Fund_Nature** - **Spanish** - Spanish market nomenclature
* **Profile** - **Spanish** - Conservador/Moderado/Dinámico
* **Type** - **English** - Active Management, Allocation, etc.
* **Family** - **English** - Equity Core, Multi-Asset, Income Oriented, etc.
* **Geography** - **Spanish** - Europa, EEUU, Global, etc.
* **Investment_Universe** - **English** - Regional, Global, Sector, Country, Thematic, Liquidity
* **Sector_Focus** - **English** - Technology & Innovation, Healthcare, etc. (GICS-ES nomenclature)
* **Benchmark_Type** - **English** - REFERENCE_INDEX, TARGET_INDEX, NO_BENCHMARK
* **Theme** - **English** - Technology, Healthcare, Climate, etc.
* **Subtype** - **English** - Index Fund, ETF, Opportunistic
* **Strategy** - **Spanish** - Activo, Indexado, Pasivo
* **Style_Profile** - **English** - Growth, Value, Income, Strategic Allocation
* **Exposure_Bias** - **English** - Duration Bias, Credit Bias, Liquidity Bias
* **Replication_Method** - **English** - ACTIVE, PASSIVE
* **Hedging_Policy** - **English** - HEDGED, UNHEDGED
* **Accumulation_Policy** - **English** - ACCUMULATION, DISTRIBUTION
* **Distribution_Frequency** - **English** - ANNUAL, QUARTERLY, MONTHLY, BIANNUAL
* **Leverage_Used** - **English** - YES, NO, LIMITED
* **Derivatives_Usage** - **English** - YES, NO, LIMITED
* **Currency_Hedged** - **English** - Hedged, Unhedged
* **Liquidity_Profile** - **English** - T1, T2
* **Market_Cap_Focus** - **English** - Large Cap, Mid Cap, Small Cap
* **SRRI_Quality_Flag** - **English** - HIGH, MEDIUM_VISUAL, MEDIUM_TEXT, LOW_CONFLICT, NONE
* **Data_Quality_Flag** - **English** - OK, WARN, MISSING

**Application:** ALL blocks (MONETARY, FI, EQ, MIXED, ALTERNATIVE, REMAINING) must apply translations before returning classification.

---

## 3. PRINCIPLE #9: SEMANTIC CONSISTENCY

### 3.1 INTRA-ATTRIBUTE CONSISTENCY

**General rule:** Each column must have non-ambiguous, mutually exclusive values, with consistent granularity.

#### 3.1.1 HIERARCHIES AND UNIQUE VALUES

**Type - Monetary Hierarchy:**
* **"Money Market"** → Standard value (98% of monetary funds)
* **"Government Money Market"** → Specific (2 funds) - Only use if KIID specifies "gobierno" or "public"
* **"Private Money Market"** → Specific (2 funds) - Only use if KIID specifies "corporate" or "private"

**Convention:** "Monetario" is only the default value; it is NOT ambiguous.

---

**Geography - Europe Overlap:**
* **"Europa"** → By default means Western Europe
* **"Europa del Este"** → Only use if KIID explicitly specifies "Eastern Europe", "CEE", "Central and Eastern Europe"

**Validation:** If Geography="Europa del Este", verify that it is not simply generic "Europe".

---

#### 3.1.2 COLUMNS WITH EXPECTED VARIABILITY

**Derivatives_Usage - CURRENT PROBLEM:**
* **Current state:** Only has value "YES" (1,898 funds)
* **Expected values:** "YES", "NO", "LIMITED"
* **Rule:** If KIID does not mention derivatives → "NO" (not NULL)
* **Rule:** If it mentions "may use derivatives" or "up to X%" → "LIMITED"
* **Rule:** If it mentions "extensively" or "primarily" → "YES"

**Mandatory validation:** Explicitly detect "NO" and "LIMITED", not just "YES".

---

**Currency_Hedged - CURRENT PROBLEM:**
* **Current state:** Only has value "Hedged" (634 funds)
* **Expected values:** "Hedged", "Unhedged"
* **Possible redundancy with Hedging_Policy** → Investigate if they should be consolidated

**Mandatory validation:** Explicitly detect "Unhedged", not just "Hedged".

---

#### 3.1.3 ALLOWED VALUES BY COLUMN (Sample)

ALLOWED_VALUES_BY_COLUMN = {
    'Fund_Nature': [
        'Renta Variable', 'Mixtos', 'Renta Fija Flexible', 
        'Renta Fija Corto Plazo', 'Monetario', 'Alternativo', 
        'Estructurado', 'Restantes'
    ],
    
    'Profile': ['Conservador', 'Moderado', 'Dinámico'],
    
    'Strategy': ['Activo', 'Indexado', 'Pasivo'],
    
    'Replication_Method': ['ACTIVE', 'PASSIVE'],
    
    'Derivatives_Usage': ['YES', 'NO', 'LIMITED'],  # NOT just YES
    
    'Currency_Hedged': ['Hedged', 'Unhedged'],  # NOT just Hedged
    
    'Leverage_Used': ['YES', 'NO', 'LIMITED'],
    
    'Accumulation_Policy': ['ACCUMULATION', 'DISTRIBUTION'],
    
    'Distribution_Frequency': ['ANNUAL', 'QUARTERLY', 'MONTHLY', 'BIANNUAL'],
    
    'Liquidity_Profile': ['T1', 'T2'],
    
    'Market_Cap_Focus': ['Large Cap', 'Mid Cap', 'Small Cap'],
    
    # ... (complete list of all 24 categorical columns)
}


**Validation:** classify_utils.validate_allowed_values(column, value)

---

### 3.2 INTER-ATTRIBUTE CONSISTENCY

**General rule:** Certain attributes have mandatory logical dependencies that must be validated.

#### 3.2.1 CRITICAL RULES (Mandatory auto-correction)

---

**INTER-1 RULE: Strategy ↔ Replication_Method**

* Activo → ACTIVE → Coherent
* Indexado → **PASSIVE** → If ACTIVE → Auto-correct to PASSIVE
* Pasivo → **PASSIVE** → If ACTIVE → Auto-correct to PASSIVE

**Validation:**

def validate_strategy_replication(strategy, replication):
    if strategy in ['Indexado', 'Pasivo'] and replication != 'PASSIVE':
        # AUTO-CORRECTION
        return 'PASSIVE', "Corrected Replication_Method to PASSIVE (coherence with Strategy)"
    return replication, None



**Currently affected funds:** 12 (all in REMAINING block)

---

**INTER-2 RULE: Accumulation_Policy ↔ Distribution_Frequency**

* ACCUMULATION → **NULL** → If populated → Auto-correct to NULL
* DISTRIBUTION → Populated (ANNUAL, QUARTERLY, etc.) → Coherent

**Validation:**

def validate_accumulation_distribution(acc_policy, dist_freq):
    if acc_policy == 'ACCUMULATION' and dist_freq is not None:
        # AUTO-CORRECTION
        return None, "Removed Distribution_Frequency (coherence with ACCUMULATION)"
    return dist_freq, None



**Currently affected funds:** 2

---

**INTER-3 RULE: Profile ↔ SRRI (Strict correlation)**

* Conservador → 1-4 → If ≥5 → Recalculate Profile from SRRI
* Moderado → 2-5 → If extreme (1 or 6-7) → WARNING
* Dinámico → 3-6 → If ≤2 → WARNING

**Validation:**

def validate_profile_srri(profile, srri):
    if profile == 'Conservador' and srri >= 5:
        # AUTO-CORRECTION: Recalculate Profile
        new_profile = assign_profile_from_srri(srri)
        return new_profile, f"Profile recalculated from SRRI={srri}"
    
    if profile == 'Dinámico' and srri <= 2:
        return profile, f"WARNING: Dinámico with SRRI={srri} is unusual"
    
    return profile, None

def assign_profile_from_srri(srri):
    """Strict mapping SRRI → Profile."""
    if srri in [1, 2, 3]:
        return 'Conservador'
    elif srri == 4:
        return 'Moderado'
    elif srri in [5, 6, 7]:
        return 'Dinámico'
    return None



**Currently affected funds:** 9 Conservative with SRRI≥5

---

**INTER-4 RULE: Nature → Type (Mandatory coherence)**

ALLOWED_TYPE_BY_NATURE = {
    'Renta Variable': [
        'Gestión Activa', 'Indexado', 'Total Return', 
        'Absolute Return', 'Tactical Allocation'
    ],
    
    'Renta Fija Flexible': [
        'Renta Fija Flexible', 'Gestión Activa', 'Total Return',
        'Absolute Return', 'Indexado'
    ],
    
    'Renta Fija Corto Plazo': [
        'Renta Fija Corto Plazo', 'Crédito CP', 'Gobierno CP',
        'Floating Rate CP', 'Target Maturity'
    ],
    
    'Monetario': [
        'Monetario', 'Monetario Público', 'Monetario Privado'
    ],
    
    'Mixtos': [
        'Allocation', 'Tactical Allocation', 'Gestión Activa'
    ],
    
    'Alternativo': [
        'Absolute Return', 'Commodities', 'Total Return',
        'Gestión Activa', 'Indexado'
    ],
    
    'Estructurado': [
        'Estructurado'
    ],
    
    'Restantes': [
        # Any valid Type (catch-all)
    ]
}


**Validation:**

def validate_nature_type_coherence(nature, type_val):
    allowed_types = ALLOWED_TYPE_BY_NATURE.get(nature, [])
    if allowed_types and type_val not in allowed_types:
        return False, f"Type '{type_val}' is not valid for Nature '{nature}'"
    return True, None



---

**INTER-5 RULE: Nature → Family (Mandatory coherence)**

ALLOWED_FAMILY_BY_NATURE = {
    'Renta Variable': [
        'RV Core', 'RV Temática', 'Activos Reales'
    ],
    
    'Renta Fija Flexible': [
        'Renta Fija Flexible', 'RF High Yield', 'RF Emergentes',
        'RF Inflación', 'Income Oriented'
    ],
    
    'Renta Fija Corto Plazo': [
        'Renta Fija Corto Plazo'
    ],
    
    'Monetario': [
        'Monetario', 'LVNAV', 'VNAV'
    ],
    
    'Mixtos': [
        'Mixtos', 'Income Oriented', 'Flexible Estratégico'
    ],
    
    'Alternativo': [
        'Retorno Absoluto', 'Activos Reales'
    ],
    
    'Estructurado': [
        'Estructurado'
    ]
}


**Validation:**

def validate_nature_family_coherence(nature, family):
    allowed_families = ALLOWED_FAMILY_BY_NATURE.get(nature, [])
    if allowed_families and family not in allowed_families:
        return False, f"Family '{family}' is not valid for Nature '{nature}'"
    return True, None



---

**INTER-6 RULE: Investment_Universe → Sector_Focus/Geography (Completeness)**

* Sector → **Must be populated** → May be populated
* Thematic → May be populated → May be populated
* Regional → NULL → **Must be populated**
* Country → NULL → **Must be populated**
* Global → NULL → May be populated
* Liquidity → NULL → May be populated

**Validation:**

def validate_universe_completeness(universe, sector, geography):
    issues = []
    
    if universe == 'Sector' and sector is None:
        issues.append("Investment_Universe='Sector' requires populated Sector_Focus")
    
    if universe in ['Regional', 'Country'] and geography is None:
        issues.append(f"Investment_Universe='{universe}' requires populated Geography")
    
    return len(issues) == 0, issues



**Currently affected funds:** ~193 with Universe-Sector incomplete

---

#### 3.2.2 WARNING RULES (WARNING, no auto-correction)

---

**INTER-7 RULE: Leverage_Used ↔ Profile**

* Conservador → YES → WARNING: Unusual but possible (validate case by case)
* Conservador → LIMITED → Acceptable
* Conservador → NO → Expected
* Moderado → YES → Acceptable
* Dinámico → YES → Expected

**Validation:**

def validate_leverage_profile(profile, leverage):
    if profile == 'Conservador' and leverage == 'YES':
        return 'WARNING', "Conservative profile with Leverage=YES is unusual"
    return 'OK', None



**Currently affected funds:** 105 Conservatives with Leverage=YES

---

**INTER-8 RULE: Is_ESG ↔ Sfdr_Article**

* 1 → 8 or 9 → If 6 → WARNING
* 0 → 6 or NULL → Coherent

**Validation:**

def validate_esg_sfdr(is_esg, sfdr_article):
    if is_esg == 1 and sfdr_article not in [8, 9, None]:
        return 'WARNING', f"Is_ESG=1 with Sfdr_Article={sfdr_article} (expected 8 or 9)"
    return 'OK', None



**Current state:** Coherent (no inconsistencies)

---

**INTER-9 RULE: Theme ↔ Sector_Focus (Semantic coherence)**

Expected mapping (examples):

* Technology → Technology & Innovation
* Healthcare → Healthcare & Life Sciences
* Energy → Energy & Resources
* Water → Utilities & Environment
* Gold → Materials & Mining

**Validation:**

THEME_SECTOR_MAPPING = {
    'Technology': 'Technology & Innovation',
    'Artificial Intelligence': 'Technology & Innovation',
    'Digital': 'Technology & Innovation',
    'Robotics': 'Technology & Innovation',
    'Healthcare': 'Healthcare & Life Sciences',
    'Healthcare / MedTech': 'Healthcare & Life Sciences',
    'Energy': 'Energy & Resources',
    'Climate / Clean Energy': 'Energy & Resources',
    'Water': 'Utilities & Environment',
    'Gold': 'Materials & Mining',
    'Mining': 'Materials & Mining',
    # ... (complete mapping)
}

def validate_theme_sector_coherence(theme, sector_focus):
    if theme and sector_focus:
        expected_sector = THEME_SECTOR_MAPPING.get(theme)
        if expected_sector and sector_focus != expected_sector:
            return 'WARNING', f"Theme '{theme}' normally maps to '{expected_sector}', not '{sector_focus}'"
    return 'OK', None



**Current state:** Very coherent (only 1 possible incoherence)

---

**INTER-10 RULE: Geography ↔ Investment_Universe**

* US, China, Japan, India → Country or Regional (NOT Global)
* Global → Global
* Europe, Asia, Emerging → Regional

**Validation:**

def validate_geography_universe(geography, universe):
    if geography in ['EEUU', 'China', 'Japón', 'India'] and universe == 'Global':
        return 'WARNING', f"Specific Geography '{geography}' with Universe='Global' is unusual"
    return 'OK', None



---

#### 3.2.3 PENDING INVESTIGATION (Do not validate yet)

**INTER-11 RULE: Hedging_Policy ↔ Currency_Hedged (Redundancy?)**

* **Problem:** 335 funds have both columns populated.
* **Hypothesis:** Possible semantic redundancy (both represent currency hedging).
* **Action:** Investigate the difference before implementing validation.
* **Questions:** Is Hedging_Policy broader (all types of hedge)?
* **Questions:** Is Currency_Hedged specific (only FX)?
* **Questions:** Should they be consolidated into a single column?

**DO NOT implement validation until this investigation is resolved.**

---

### 3.3 CONSOLIDATED VALIDATOR (Master function)

**Mandatory implementation in classify_utils.py:**

def validate_all_semantic_consistency(fund_record):
    """
    Validates ALL semantic consistency rules (INTRA + INTER).
    
    Args:
        fund_record (dict): Fund classification with all attributes
    
    Returns:
        dict: {
            'is_valid': bool,
            'critical_errors': list,  # Require auto-correction
            'warnings': list,          # Inform but do not block
            'corrected_record': dict   # Corrected version
        }
    """
    critical_errors = []
    warnings = []
    corrected_record = fund_record.copy()
    
    # ========================================
    # CRITICAL INTER-ATTRIBUTE VALIDATIONS
    # ========================================
    
    # INTER-1: Strategy vs Replication_Method
    corrected_replication, error = validate_strategy_replication(
        fund_record.get('Strategy'),
        fund_record.get('Replication_Method')
    )
    if error:
        critical_errors.append({'rule': 'Strategy-Replication', 'message': error})
        corrected_record['Replication_Method'] = corrected_replication
    
    # INTER-2: Accumulation vs Distribution
    corrected_dist_freq, error = validate_accumulation_distribution(
        fund_record.get('Accumulation_Policy'),
        fund_record.get('Distribution_Frequency')
    )
    if error:
        critical_errors.append({'rule': 'Accumulation-Distribution', 'message': error})
        corrected_record['Distribution_Frequency'] = corrected_dist_freq
    
    # INTER-3: Profile vs SRRI
    corrected_profile, error = validate_profile_srri(
        fund_record.get('Profile'),
        fund_record.get('SRRI')
    )
    if error:
        if 'WARNING' in error:
            warnings.append({'rule': 'Profile-SRRI', 'message': error})
        else:
            critical_errors.append({'rule': 'Profile-SRRI', 'message': error})
            corrected_record['Profile'] = corrected_profile
    
    # INTER-4: Nature → Type coherence
    is_valid, error = validate_nature_type_coherence(
        fund_record.get('Fund_Nature'),
        fund_record.get('Type')
    )
    if not is_valid:
        critical_errors.append({'rule': 'Nature-Type', 'message': error})
    
    # INTER-5: Nature → Family coherence
    is_valid, error = validate_nature_family_coherence(
        fund_record.get('Fund_Nature'),
        fund_record.get('Family')
    )
    if not is_valid:
        critical_errors.append({'rule': 'Nature-Family', 'message': error})
    
    # INTER-6: Universe → Sector/Geography completeness
    is_valid, errors = validate_universe_completeness(
        fund_record.get('Investment_Universe'),
        fund_record.get('Sector_Focus'),
        fund_record.get('Geography')
    )
    if not is_valid:
        for error in errors:
            warnings.append({'rule': 'Universe-Completeness', 'message': error})
    
    # ========================================
    # WARNING INTER-ATTRIBUTE VALIDATIONS
    # ========================================
    
    # INTER-7: Leverage vs Profile
    status, message = validate_leverage_profile(
        fund_record.get('Profile'),
        fund_record.get('Leverage_Used')
    )
    if status == 'WARNING':
        warnings.append({'rule': 'Leverage-Profile', 'message': message})
    
    # INTER-8: Is_ESG vs Sfdr_Article
    status, message = validate_esg_sfdr(
        fund_record.get('Is_ESG'),
        fund_record.get('Sfdr_Article')
    )
    if status == 'WARNING':
        warnings.append({'rule': 'ESG-SFDR', 'message': message})
    
    # INTER-9: Theme vs Sector_Focus
    status, message = validate_theme_sector_coherence(
        fund_record.get('Theme'),
        fund_record.get('Sector_Focus')
    )
    if status == 'WARNING':
        warnings.append({'rule': 'Theme-Sector', 'message': message})
    
    # INTER-10: Geography vs Universe
    status, message = validate_geography_universe(
        fund_record.get('Geography'),
        fund_record.get('Investment_Universe')
    )
    if status == 'WARNING':
        warnings.append({'rule': 'Geography-Universe', 'message': message})
    
    # ========================================
    # INTRA-ATTRIBUTE VALIDATIONS
    # ========================================
    
    # Validate allowed values per column
    for column, value in fund_record.items():
        if column in ALLOWED_VALUES_BY_COLUMN and value is not None:
            if value not in ALLOWED_VALUES_BY_COLUMN[column]:
                warnings.append({
                    'rule': 'Allowed-Values',
                    'message': f"{column}='{value}' is not in allowed values"
                })
    
    # ========================================
    # RESULT
    # ========================================
    
    return {
        'is_valid': len(critical_errors) == 0,
        'critical_errors': critical_errors,
        'warnings': warnings,
        'corrected_record': corrected_record
    }



---

### 3.4 AUTOMATIC AUTO-CORRECTION

**Application in all blocks:**

# Example: blocks/restantes.py

def classify_restantes_fund(kiid_text, isin):
    """
    REMAINING classification with STRICT semantic validation.
    """
    
    # ... (current classification logic) ...
    
    classification = {
        'Fund_Nature': detected_nature,
        'Type': detected_type,
        # ... (rest of attributes)
    }
    
    # ========================================
    # MANDATORY SEMANTIC VALIDATION
    # ========================================
    
    validation_result = validate_all_semantic_consistency(classification)
    
    if not validation_result['is_valid']:
        log_info(f"[{isin}] Inconsistencies detected: {validation_result['critical_errors']}")
        
        # AUTO-CORRECTION
        classification = validation_result['corrected_record']
        
        for error in validation_result['critical_errors']:
            log_info(f"  → Applied auto-correction: {error['rule']} - {error['message']}")
    
    # Warnings (do not block)
    for warning in validation_result['warnings']:
        log_warning(f"[{isin}] {warning['rule']}: {warning['message']}")
    
    return classification



---

## 4. BLOCK-SPECIFIC INSTRUCTIONS

### 4.1 REMAINING BLOCK

**Context:** REMAINING is responsible for 82-100% of detected inconsistencies because:
* It does not have specific heuristics (catch-all).
* It processes more heterogeneous funds.
* It has lower pattern coverage.

**MANDATORY instructions for REMAINING:**

* **TRIPLE semantic validation:** Validate Nature → Type coherence.
* **TRIPLE semantic validation:** Validate Nature → Family coherence.
* **TRIPLE semantic validation:** Validate Universe → Sector/Geography completeness.
* **Automatic auto-correction:** Apply validate_all_semantic_consistency() ALWAYS.
* **Automatic auto-correction:** Use the corrected version before returning.
* **Exhaustive logging:** Log ALL applied corrections.
* **Exhaustive logging:** Log ALL generated warnings.
* **Exhaustive logging:** Include ISIN in all logs.
* **Procedure upon doubtful detection:**

    if confidence_score < 0.7:  # Confidence threshold
        # Apply conservative default values
        classification['Fund_Nature'] = 'Restantes'
        classification['Type'] = None
        classification['Family'] = None
        # Log for manual review
        log_warning(f"[{isin}] Classification with low confidence ({confidence_score})")
   


### 4.2 SPECIALIZED BLOCKS (MONETARY, FI, EQ, MIXED, ALTERNATIVE)

**Instructions:**

* **Recommended semantic validation:** (not mandatory if confidence is high)
* **Auto-correction only if inconsistencies are detected:**
* **Warning logging:** (not critical, since specialized blocks have high precision)

---

## 5. VALIDATION PRIORITIZATION

**Application order in classify_fund():**

def classify_fund(kiid_text, isin):
    # 1. Specialized classification by block
    classification = detect_and_classify_by_block(kiid_text, isin)
    
    # 2. Translation to target language (Principle #8)
    classification = apply_language_homogeneity(classification)
    
    # 3. Semantic validation (Principle #9)
    validation_result = validate_all_semantic_consistency(classification)
    
    # 4. Auto-correction if necessary
    if not validation_result['is_valid']:
        classification = validation_result['corrected_record']
    
    # 5. Additional characterization
    classification = enrich_classification(classification, kiid_text)
    
    return classification



---

## 6. RELATIONS PENDING ANALYSIS (Do not validate yet)

**10 inter-attribute relations NOT YET exhaustively analyzed:**

* 1. Nature → Type → Family (complete triangular coherence) - **Partially covered**
* 2. Subtype → Nature
* 3. Benchmark_Type → Nature/Type
* 4. Style_Profile → Nature
* 5. Investment_Universe → Theme
* 6. Market_Cap_Focus → Nature
* 7. Sector_Focus → Nature/Type
* 8. Recommended_Holding_Period → Profile/SRRI
* 9. Ongoing_Charge → Profile
* 10. Fund_Currency vs Portfolio_Currency

**Action:** DO NOT implement validators for these relations until quantitative analysis is complete.

---

## 7. ERROR MANAGEMENT AND LOGGING

### 7.1 SEVERITY LEVELS


# ERROR (critical) - Inconsistency that MUST be corrected
log_error(f"[{isin}] Inconsistent Nature-Type: Nature='RV' with Type='Monetario'")

# WARNING (warning) - Unusual but possible
log_warning(f"[{isin}] Conservative Profile with Leverage=YES (unusual)")

# INFO (informative) - Applied correction
log_info(f"[{isin}] Auto-correction: Replication_Method → PASSIVE")



### 7.2 VALIDATION METRICS

**Collect metrics after each cycle:**

validation_metrics = {
    'total_funds': 3204,
    'validation_errors': {
        'Strategy-Replication': 12,
        'Accumulation-Distribution': 2,
        'Profile-SRRI': 9,
        'Nature-Type': 2,
        'Universe-Completeness': 193
    },
    'auto_corrections_applied': 23,
    'warnings_generated': 105
}



---

## 8. COMMUNICATION

Clear, direct, concise, and honest communication. English as the communication language.

---

## 9. MANDATORY ACTIONS SUMMARY

**For Claude:**

* **Apply Principle #8** in ALL blocks: Translate Type, Family, Theme, and Subtype according to the target language.
* **Apply Principle #8** in ALL blocks: Validate linguistic homogeneity before returning.
* **Apply Principle #9** especially in REMAINING: Validate Nature→Type and Nature→Family coherence.
* **Apply Principle #9** especially in REMAINING: Validate Universe→Sector/Geography completeness.
* **Apply Principle #9** especially in REMAINING: Validate Strategy→Replication, Accumulation→Distribution, and Profile→SRRI.
* **Apply Principle #9** especially in REMAINING: Auto-correct critical inconsistencies.
* **Apply Principle #9** especially in REMAINING: Log warnings but do not block.
* **Explicitly detect values:** Derivatives_Usage: "NO" and "LIMITED" (not just "YES").
* **Explicitly detect values:** Currency_Hedged: "Unhedged" (not just "Hedged").
* **Document decisions:** Log all applied corrections with ISIN.
* **Document decisions:** Log warnings with context.

---

**END OF PERFECT CUSTOM INSTRUCTIONS V3 (10/10)**

*Update: April 9, 2026*  
*Version: 3.0 (Exhaustive + Perfected)*  
*Coverage: Principle #8 + Complete Principle #9 (INTRA + INTER)*  
*Improvements applied: Introductory context, concrete examples, cross-references, compliance checklist*