# CUSTOM INSTRUCTIONS V3 - COMPLETA Y EXHAUSTIVA
# Clasificación de Fondos de Inversión - Proyecto Fondos

**Actualización:** 9 de abril de 2026  
**Versión:** 3.0 (Exhaustiva - Incluye TODAS las reglas INTRA e INTER atributos)

---

## 1. CONTEXTO OPERATIVO

Este proyecto implementa un sistema de clasificación automatizada de fondos de inversión europeos (~3.200 fondos) basado en análisis de documentos KIID/DDF (PDFs regulatorios).

**Pipeline de clasificación:**
1. Extracción de texto de KIID/DDF → `kiid_parser.py`
2. Clasificación especializada por bloques → `blocks/*.py` (MONETARIOS, RF, RV, MIXTOS, ALTERNATIVO, RESTANTES)
3. Caracterización y enriquecimiento → `fund_characterizer.py`
4. Validación de consistencia semántica → `classify_utils.py`
5. Persistencia en SQLite → `sqlite_writer.py`

**Esquema:** v17 (25 atributos categóricos, 15 numéricos, 8 flags)

---

## 1.1 PRINCIPIOS FUNDAMENTALES (Meta-nivel)

Estos son los **meta-principios transversales** que guían TODO el desarrollo del proyecto. No son reglas técnicas específicas, sino la filosofía arquitectónica que fundamenta cada decisión de diseño e implementación.

**Contexto:** En un sistema de clasificación automatizada que procesa ~3.200 fondos con 25 atributos categóricos, la acumulación de inconsistencias y duplicaciones de lógica puede degradar rápidamente la calidad de datos y la mantenibilidad del código. Estos principios previenen esa degradación desde la raíz.

---

### **PRINCIPIO #1: Root Cause Analysis > Parches de Síntomas** ⚠️ CRÍTICO

**Enunciado:**  
Al gestionar, analizar o corregir disfunciones (bugs) en cualquier fase (desarrollo, operación o mantenimiento), debes enfocarte **exclusivamente** en identificar y resolver la **causa raíz**.

**Restricción estricta:**  
Nunca propongas soluciones temporales que solo mitiguen los síntomas del problema. Aplica siempre un enfoque preventivo y definitivo.

**Ejemplo de aplicación en este proyecto:**

```python
# ❌ INCORRECTO (parche sintomático):
# Problema: 12 fondos tienen Strategy='Indexado' pero Replication_Method='ACTIVE'
UPDATE fund_master 
SET Replication_Method = 'PASSIVE' 
WHERE Strategy = 'Indexado' AND Replication_Method = 'ACTIVE';

# ✅ CORRECTO (root cause fix):
# Causa raíz: El bloque RESTANTES no valida coherencia Strategy-Replication
# Fix: Añadir validación en classify_utils.py que auto-corrija en TODOS los bloques
def validate_strategy_replication(strategy, replication):
    if strategy in ['Indexado', 'Pasivo'] and replication != 'PASSIVE':
        return 'PASSIVE', "Auto-corrección aplicada"
    return replication, None
```

**Por qué es crítico:**  
El parche SQL solo corrige los 12 fondos actuales. La próxima ejecución del pipeline volverá a generar la misma inconsistencia. El root cause fix previene **TODAS** las ocurrencias futuras.

---

### **PRINCIPIO #2: Escalabilidad y DRY (Don't Repeat Yourself)**

**Enunciado:**  
Maximiza la reusabilidad mediante arquitectura modular. Está **estrictamente prohibido** duplicar lógica de negocio o crear lógicas similares en distintos módulos.

**Obligación:**  
Cuando detectes requerimientos iguales o parecidos en múltiples áreas, tu deber es diseñar e implementar **módulos o funciones genéricas** que centralicen esa funcionalidad.

**Ejemplo de aplicación en este proyecto:**

```python
# ❌ INCORRECTO (duplicación de lógica):
# En blocks/monetarios.py:
def validate_monetarios(classification):
    if classification['Strategy'] in ['Indexado', 'Pasivo']:
        if classification['Replication_Method'] != 'PASSIVE':
            classification['Replication_Method'] = 'PASSIVE'
    return classification

# En blocks/renta_variable.py:
def validate_rv(classification):
    if classification['Strategy'] in ['Indexado', 'Pasivo']:
        if classification['Replication_Method'] != 'PASSIVE':
            classification['Replication_Method'] = 'PASSIVE'
    return classification

# En blocks/restantes.py:
def validate_restantes(classification):
    if classification['Strategy'] in ['Indexado', 'Pasivo']:
        if classification['Replication_Method'] != 'PASSIVE':
            classification['Replication_Method'] = 'PASSIVE'
    return classification

# ✅ CORRECTO (centralización DRY):
# En classify_utils.py (función única reutilizable):
def validate_all_semantic_consistency(classification):
    """
    Valida TODAS las reglas semánticas.
    Usada por TODOS los bloques (MONETARIOS, RF, RV, MIXTOS, RESTANTES).
    """
    # Strategy-Replication
    if classification.get('Strategy') in ['Indexado', 'Pasivo']:
        if classification.get('Replication_Method') != 'PASSIVE':
            classification['Replication_Method'] = 'PASSIVE'
    
    # ... (resto de validaciones centralizadas)
    
    return classification

# En TODOS los bloques:
from classify_utils import validate_all_semantic_consistency

def classify_monetarios_fund(kiid_text, isin):
    classification = { ... }
    return validate_all_semantic_consistency(classification)
```

**Por qué es crítico:**  
Si necesitas modificar la validación Strategy-Replication (ej: añadir logging), con duplicación debes modificar 7 bloques. Con centralización DRY, solo modificas 1 función.

---

### **Conexión con Principios Específicos**

Los **Principios #8 (Homogeneidad Lingüística)** y **#9 (Consistencia Semántica)** son **aplicaciones concretas** de estos meta-principios al dominio específico de clasificación de fondos:

| Meta-Principio | Aplicación Concreta |
|----------------|---------------------|
| **#1 Root Cause** | Principio #9 define validaciones que **previenen** inconsistencias en origen (no las corrige después) |
| **#2 DRY** | Principio #8 y #9 definen funciones centralizadas (`validate_all_semantic_consistency()`) que **todos** los bloques reutilizan |

---

### **Checklist de Cumplimiento**

Antes de aprobar cualquier solución, verificar:

- [ ] **Root Cause:** ¿Esta solución elimina la causa del problema, no solo sus síntomas?
- [ ] **Preventivo:** ¿Esta solución previene recurrencias futuras del mismo problema?
- [ ] **DRY:** ¿Esta lógica ya existe en otro módulo? Si sí, ¿puedo reutilizarla?
- [ ] **Centralización:** Si necesito esta lógica en múltiples lugares, ¿la centralicé en un módulo común?
- [ ] **Escalable:** ¿Esta solución seguirá funcionando cuando tengamos 10.000 fondos en vez de 3.200?

**Si alguna respuesta es NO → Rediseñar solución.**

---

## 2. PRINCIPIO #8: HOMOGENEIDAD LINGÜÍSTICA

**Regla general:** Cada columna debe tener valores en UN SOLO IDIOMA para facilitar consultas, agrupaciones, y mantenibilidad.

### 2.1 IDIOMA OBJETIVO POR COLUMNA

| Columna | Idioma objetivo | Razón |
|---------|----------------|-------|
| **Fund_Nature** | **Español** | Nomenclatura de mercado español |
| **Profile** | **Español** | Conservador/Moderado/Dinámico |
| **Type** | **Inglés** | Active Management, Allocation, etc. |
| **Family** | **Inglés** | Equity Core, Multi-Asset, Income Oriented, etc. |
| **Geography** | **Español** | Europa, EEUU, Global, etc. |
| **Investment_Universe** | **Inglés** | Regional, Global, Sector, Country, Thematic, Liquidity |
| **Sector_Focus** | **Inglés** | Technology & Innovation, Healthcare, etc. (nomenclatura GICS-ES) |
| **Benchmark_Type** | **Inglés** | REFERENCE_INDEX, TARGET_INDEX, NO_BENCHMARK |
| **Theme** | **Inglés** | Technology, Healthcare, Climate, etc. |
| **Subtype** | **Inglés** | Index Fund, ETF, Opportunistic |
| **Strategy** | **Español** | Activo, Indexado, Pasivo |
| **Style_Profile** | **Inglés** | Growth, Value, Income, Strategic Allocation |
| **Exposure_Bias** | **Inglés** | Duration Bias, Credit Bias, Liquidity Bias |
| **Replication_Method** | **Inglés** | ACTIVE, PASSIVE |
| **Hedging_Policy** | **Inglés** | HEDGED, UNHEDGED |
| **Accumulation_Policy** | **Inglés** | ACCUMULATION, DISTRIBUTION |
| **Distribution_Frequency** | **Inglés** | ANNUAL, QUARTERLY, MONTHLY, BIANNUAL |
| **Leverage_Used** | **Inglés** | YES, NO, LIMITED |
| **Derivatives_Usage** | **Inglés** | YES, NO, LIMITED |
| **Currency_Hedged** | **Inglés** | Hedged, Unhedged |
| **Liquidity_Profile** | **Inglés** | T1, T2 |
| **Market_Cap_Focus** | **Inglés** | Large Cap, Mid Cap, Small Cap |
| **SRRI_Quality_Flag** | **Inglés** | HIGH, MEDIUM_VISUAL, MEDIUM_TEXT, LOW_CONFLICT, NONE |
| **Data_Quality_Flag** | **Inglés** | OK, WARN, MISSING |



**Aplicación:** TODOS los bloques (MONETARIOS, RF, RV, MIXTOS, ALTERNATIVO, RESTANTES) deben aplicar traducciones antes de retornar clasificación.

---

## 3. PRINCIPIO #9: CONSISTENCIA SEMÁNTICA

### 3.1 CONSISTENCIA INTRA-ATRIBUTO

**Regla general:** Cada columna debe tener valores no ambiguos, mutuamente excluyentes, y con granularidad consistente.

#### 3.1.1 JERARQUÍAS Y VALORES ÚNICOS

**Type - Jerarquía Monetario:**
- ✅ **"Money Market"** → Valor estándar (98% de fondos monetarios)
- ⚠️ **"Government Money Market"** → Específico (2 fondos) - Solo usar si KIID especifica "gobierno" o "public"
- ⚠️ **"Private Money Market"** → Específico (2 fondos) - Solo usar si KIID especifica "corporate" o "private"

**Convención:** "Monetario" solo es el valor por defecto, NO es ambiguo.

---

**Geography - Solapamiento Europa:**
- ✅ **"Europa"** → Por defecto significa Europa Occidental
- ⚠️ **"Europa del Este"** → Solo usar si KIID especifica explícitamente "Eastern Europe", "CEE", "Central and Eastern Europe"

**Validación:** Si Geography="Europa del Este", verificar que no sea simplemente "Europe" genérico.

---

#### 3.1.2 COLUMNAS CON VARIABILIDAD ESPERADA

**Derivatives_Usage - PROBLEMA ACTUAL:**
- ❌ **Estado actual:** Solo tiene valor "YES" (1.898 fondos)
- ✅ **Valores esperados:** "YES", "NO", "LIMITED"
- ⚠️ **Regla:** Si KIID no menciona derivados → "NO" (no NULL)
- ⚠️ **Regla:** Si menciona "may use derivatives" o "up to X%" → "LIMITED"
- ⚠️ **Regla:** Si menciona "extensively" o "primarily" → "YES"

**Validación obligatoria:** Detectar explícitamente "NO" y "LIMITED", no solo "YES".

---

**Currency_Hedged - PROBLEMA ACTUAL:**
- ❌ **Estado actual:** Solo tiene valor "Hedged" (634 fondos)
- ✅ **Valores esperados:** "Hedged", "Unhedged"
- ⚠️ **Posible redundancia con Hedging_Policy** → Investigar si deben consolidarse

**Validación obligatoria:** Detectar "Unhedged" explícitamente, no solo "Hedged".

---

#### 3.1.3 VALORES PERMITIDOS POR COLUMNA (Muestra)

```python
ALLOWED_VALUES_BY_COLUMN = {
    'Fund_Nature': [
        'Renta Variable', 'Mixtos', 'Renta Fija Flexible', 
        'Renta Fija Corto Plazo', 'Monetario', 'Alternativo', 
        'Estructurado', 'Restantes'
    ],
    
    'Profile': ['Conservador', 'Moderado', 'Dinámico'],
    
    'Strategy': ['Activo', 'Indexado', 'Pasivo'],
    
    'Replication_Method': ['ACTIVE', 'PASSIVE'],
    
    'Derivatives_Usage': ['YES', 'NO', 'LIMITED'],  # NO solo YES
    
    'Currency_Hedged': ['Hedged', 'Unhedged'],  # NO solo Hedged
    
    'Leverage_Used': ['YES', 'NO', 'LIMITED'],
    
    'Accumulation_Policy': ['ACCUMULATION', 'DISTRIBUTION'],
    
    'Distribution_Frequency': ['ANNUAL', 'QUARTERLY', 'MONTHLY', 'BIANNUAL'],
    
    'Liquidity_Profile': ['T1', 'T2'],
    
    'Market_Cap_Focus': ['Large Cap', 'Mid Cap', 'Small Cap'],
    
    # ... (listado completo de todas las 24 columnas categóricas)
}
```

**Validación:** `classify_utils.validate_allowed_values(column, value)`

---

### 3.2 CONSISTENCIA INTER-ATRIBUTO

**Regla general:** Ciertos atributos tienen dependencias lógicas obligatorias que deben validarse.

#### 3.2.1 REGLAS CRÍTICAS (Auto-corrección obligatoria)

---

**REGLA INTER-1: Strategy ↔ Replication_Method**

| Strategy | Replication_Method esperado | Acción si inconsistente |
|----------|----------------------------|------------------------|
| Activo | ACTIVE | ✅ Coherente |
| Indexado | **PASSIVE** | ❌ Si ACTIVE → Auto-corregir a PASSIVE |
| Pasivo | **PASSIVE** | ❌ Si ACTIVE → Auto-corregir a PASSIVE |

**Validación:**
```python
def validate_strategy_replication(strategy, replication):
    if strategy in ['Indexado', 'Pasivo'] and replication != 'PASSIVE':
        # AUTO-CORRECCIÓN
        return 'PASSIVE', "Corregido Replication_Method a PASSIVE (coherencia con Strategy)"
    return replication, None
```

**Fondos afectados actualmente:** 12 (todos en bloque RESTANTES)

---

**REGLA INTER-2: Accumulation_Policy ↔ Distribution_Frequency**

| Accumulation_Policy | Distribution_Frequency esperado | Acción si inconsistente |
|---------------------|--------------------------------|------------------------|
| ACCUMULATION | **NULL** | ❌ Si poblado → Auto-corregir a NULL |
| DISTRIBUTION | Poblado (ANNUAL, QUARTERLY, etc.) | ✅ Coherente |

**Validación:**
```python
def validate_accumulation_distribution(acc_policy, dist_freq):
    if acc_policy == 'ACCUMULATION' and dist_freq is not None:
        # AUTO-CORRECCIÓN
        return None, "Eliminado Distribution_Frequency (coherencia con ACCUMULATION)"
    return dist_freq, None
```

**Fondos afectados actualmente:** 2

---

**REGLA INTER-3: Profile ↔ SRRI (Correlación estricta)**

| Profile | SRRI esperado | Acción si inconsistente |
|---------|--------------|------------------------|
| Conservador | 1-4 | ❌ Si ≥5 → Recalcular Profile desde SRRI |
| Moderado | 2-5 | ⚠️ Si extremos (1 o 6-7) → WARNING |
| Dinámico | 3-6 | ⚠️ Si ≤2 → WARNING |

**Validación:**
```python
def validate_profile_srri(profile, srri):
    if profile == 'Conservador' and srri >= 5:
        # AUTO-CORRECCIÓN: Recalcular Profile
        new_profile = assign_profile_from_srri(srri)
        return new_profile, f"Profile recalculado desde SRRI={srri}"
    
    if profile == 'Dinámico' and srri <= 2:
        return profile, f"WARNING: Dinámico con SRRI={srri} es inusual"
    
    return profile, None

def assign_profile_from_srri(srri):
    """Mapeo estricto SRRI → Profile."""
    if srri in [1, 2, 3]:
        return 'Conservador'
    elif srri == 4:
        return 'Moderado'
    elif srri in [5, 6, 7]:
        return 'Dinámico'
    return None
```

**Fondos afectados actualmente:** 9 Conservadores con SRRI≥5

---

**REGLA INTER-4: Nature → Type (Coherencia obligatoria)**

```python
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
        # Cualquier Type válido (catch-all)
    ]
}
```

**Validación:**
```python
def validate_nature_type_coherence(nature, type_val):
    allowed_types = ALLOWED_TYPE_BY_NATURE.get(nature, [])
    if allowed_types and type_val not in allowed_types:
        return False, f"Type '{type_val}' no es válido para Nature '{nature}'"
    return True, None
```

---

**REGLA INTER-5: Nature → Family (Coherencia obligatoria)**

```python
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
```

**Validación:**
```python
def validate_nature_family_coherence(nature, family):
    allowed_families = ALLOWED_FAMILY_BY_NATURE.get(nature, [])
    if allowed_families and family not in allowed_families:
        return False, f"Family '{family}' no es válida para Nature '{nature}'"
    return True, None
```

---

**REGLA INTER-6: Investment_Universe → Sector_Focus/Geography (Completitud)**

| Investment_Universe | Sector_Focus esperado | Geography esperado |
|---------------------|----------------------|-------------------|
| Sector | **Debe estar poblado** | Puede estar poblado |
| Thematic | Puede estar poblado | Puede estar poblado |
| Regional | NULL | **Debe estar poblado** |
| Country | NULL | **Debe estar poblado** |
| Global | NULL | Puede estar poblado |
| Liquidity | NULL | Puede estar poblado |

**Validación:**
```python
def validate_universe_completeness(universe, sector, geography):
    issues = []
    
    if universe == 'Sector' and sector is None:
        issues.append("Investment_Universe='Sector' requiere Sector_Focus poblado")
    
    if universe in ['Regional', 'Country'] and geography is None:
        issues.append(f"Investment_Universe='{universe}' requiere Geography poblado")
    
    return len(issues) == 0, issues
```

**Fondos afectados actualmente:** ~193 con Universe-Sector incompleto

---

#### 3.2.2 REGLAS DE ADVERTENCIA (WARNING, no auto-corrección)

---

**REGLA INTER-7: Leverage_Used ↔ Profile**

| Profile | Leverage_Used | Acción |
|---------|--------------|--------|
| Conservador | YES | ⚠️ WARNING: Inusual pero posible (validar caso por caso) |
| Conservador | LIMITED | ✅ Aceptable |
| Conservador | NO | ✅ Esperado |
| Moderado | YES | ✅ Aceptable |
| Dinámico | YES | ✅ Esperado |

**Validación:**
```python
def validate_leverage_profile(profile, leverage):
    if profile == 'Conservador' and leverage == 'YES':
        return 'WARNING', "Perfil Conservador con Leverage=YES es inusual"
    return 'OK', None
```

**Fondos afectados actualmente:** 105 Conservadores con Leverage=YES

---

**REGLA INTER-8: Is_ESG ↔ Sfdr_Article**

| Is_ESG | Sfdr_Article esperado | Acción si inconsistente |
|--------|----------------------|------------------------|
| 1 | 8 o 9 | ❌ Si 6 → WARNING |
| 0 | 6 o NULL | ✅ Coherente |

**Validación:**
```python
def validate_esg_sfdr(is_esg, sfdr_article):
    if is_esg == 1 and sfdr_article not in [8, 9, None]:
        return 'WARNING', f"Is_ESG=1 con Sfdr_Article={sfdr_article} (esperado 8 o 9)"
    return 'OK', None
```

**Estado actual:** Coherente (sin inconsistencias)

---

**REGLA INTER-9: Theme ↔ Sector_Focus (Coherencia semántica)**

Mapeo esperado (ejemplos):

| Theme | Sector_Focus esperado |
|-------|----------------------|
| Technology | Technology & Innovation |
| Healthcare | Healthcare & Life Sciences |
| Energy | Energy & Resources |
| Water | Utilities & Environment |
| Gold | Materials & Mining |

**Validación:**
```python
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
    # ... (mapeo completo)
}

def validate_theme_sector_coherence(theme, sector_focus):
    if theme and sector_focus:
        expected_sector = THEME_SECTOR_MAPPING.get(theme)
        if expected_sector and sector_focus != expected_sector:
            return 'WARNING', f"Theme '{theme}' normalmente mapea a '{expected_sector}', no '{sector_focus}'"
    return 'OK', None
```

**Estado actual:** Muy coherente (solo 1 posible incoherencia)

---

**REGLA INTER-10: Geography ↔ Investment_Universe**

| Geography | Investment_Universe esperado |
|-----------|----------------------------|
| EEUU, China, Japón, India | Country o Regional (NO Global) |
| Global | Global |
| Europa, Asia, Emergentes | Regional |

**Validación:**
```python
def validate_geography_universe(geography, universe):
    if geography in ['EEUU', 'China', 'Japón', 'India'] and universe == 'Global':
        return 'WARNING', f"Geography específica '{geography}' con Universe='Global' es inusual"
    return 'OK', None
```

---

#### 3.2.3 INVESTIGACIÓN PENDIENTE (No validar aún)

**REGLA INTER-11: Hedging_Policy ↔ Currency_Hedged (¿Redundancia?)**

- **Problema:** 335 fondos tienen ambas columnas pobladas
- **Hipótesis:** Posible redundancia semántica (ambas representan cobertura de divisa)
- **Acción:** Investigar diferencia antes de implementar validación
- **Preguntas:**
  - ¿Hedging_Policy es más amplio (todo tipo de hedge)?
  - ¿Currency_Hedged es específico (solo FX)?
  - ¿Deberían consolidarse en una sola columna?

**NO implementar validación hasta resolver esta investigación.**

---

### 3.3 VALIDADOR CONSOLIDADO (Función master)

**Implementación obligatoria en classify_utils.py:**

```python
def validate_all_semantic_consistency(fund_record):
    """
    Valida TODAS las reglas de consistencia semántica (INTRA + INTER).
    
    Args:
        fund_record (dict): Clasificación del fondo con todos los atributos
    
    Returns:
        dict: {
            'is_valid': bool,
            'critical_errors': list,  # Requieren auto-corrección
            'warnings': list,          # Informar pero no bloquear
            'corrected_record': dict   # Versión corregida
        }
    """
    critical_errors = []
    warnings = []
    corrected_record = fund_record.copy()
    
    # ========================================
    # VALIDACIONES INTER-ATRIBUTO CRÍTICAS
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
    
    # INTER-4: Nature → Type coherencia
    is_valid, error = validate_nature_type_coherence(
        fund_record.get('Fund_Nature'),
        fund_record.get('Type')
    )
    if not is_valid:
        critical_errors.append({'rule': 'Nature-Type', 'message': error})
    
    # INTER-5: Nature → Family coherencia
    is_valid, error = validate_nature_family_coherence(
        fund_record.get('Fund_Nature'),
        fund_record.get('Family')
    )
    if not is_valid:
        critical_errors.append({'rule': 'Nature-Family', 'message': error})
    
    # INTER-6: Universe → Sector/Geography completitud
    is_valid, errors = validate_universe_completeness(
        fund_record.get('Investment_Universe'),
        fund_record.get('Sector_Focus'),
        fund_record.get('Geography')
    )
    if not is_valid:
        for error in errors:
            warnings.append({'rule': 'Universe-Completeness', 'message': error})
    
    # ========================================
    # VALIDACIONES INTER-ATRIBUTO WARNING
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
    # VALIDACIONES INTRA-ATRIBUTO
    # ========================================
    
    # Validar valores permitidos por columna
    for column, value in fund_record.items():
        if column in ALLOWED_VALUES_BY_COLUMN and value is not None:
            if value not in ALLOWED_VALUES_BY_COLUMN[column]:
                warnings.append({
                    'rule': 'Allowed-Values',
                    'message': f"{column}='{value}' no está en valores permitidos"
                })
    
    # ========================================
    # RESULTADO
    # ========================================
    
    return {
        'is_valid': len(critical_errors) == 0,
        'critical_errors': critical_errors,
        'warnings': warnings,
        'corrected_record': corrected_record
    }
```

---

### 3.4 AUTO-CORRECCIÓN AUTOMÁTICA

**Aplicación en todos los bloques:**

```python
# Ejemplo: blocks/restantes.py

def classify_restantes_fund(kiid_text, isin):
    """
    Clasificación RESTANTES con validación semántica ESTRICTA.
    """
    
    # ... (lógica de clasificación actual) ...
    
    classification = {
        'Fund_Nature': detected_nature,
        'Type': detected_type,
        # ... (resto de atributos)
    }
    
    # ========================================
    # VALIDACIÓN SEMÁNTICA OBLIGATORIA
    # ========================================
    
    validation_result = validate_all_semantic_consistency(classification)
    
    if not validation_result['is_valid']:
        log_info(f"[{isin}] Inconsistencias detectadas: {validation_result['critical_errors']}")
        
        # AUTO-CORRECCIÓN
        classification = validation_result['corrected_record']
        
        for error in validation_result['critical_errors']:
            log_info(f"  → Auto-corrección aplicada: {error['rule']} - {error['message']}")
    
    # Advertencias (no bloquean)
    for warning in validation_result['warnings']:
        log_warning(f"[{isin}] {warning['rule']}: {warning['message']}")
    
    return classification
```

---

## 4. INSTRUCCIONES ESPECÍFICAS POR BLOQUE

### 4.1 BLOQUE RESTANTES

**Contexto:** RESTANTES es responsable del 82-100% de inconsistencias detectadas porque:
- No tiene heurísticas específicas (catch-all)
- Procesa fondos más heterogéneos
- Tiene menor cobertura de patrones

**Instrucciones OBLIGATORIAS para RESTANTES:**

1. **Validación semántica TRIPLE:**
   - ✅ Validar Nature → Type coherencia
   - ✅ Validar Nature → Family coherencia
   - ✅ Validar Universe → Sector/Geography completitud

2. **Auto-corrección automática:**
   - ✅ Aplicar `validate_all_semantic_consistency()` SIEMPRE
   - ✅ Usar versión corregida antes de retornar

3. **Logging exhaustivo:**
   - ✅ Log TODAS las correcciones aplicadas
   - ✅ Log TODAS las advertencias generadas
   - ✅ Incluir ISIN en todos los logs

4. **Procedimiento ante detección dudosa:**
   ```python
   if confidence_score < 0.7:  # Umbral de confianza
       # Aplicar valores conservadores por defecto
       classification['Fund_Nature'] = 'Restantes'
       classification['Type'] = None
       classification['Family'] = None
       # Log para revisión manual
       log_warning(f"[{isin}] Clasificación con baja confianza ({confidence_score})")
   ```

### 4.2 BLOQUES ESPECIALIZADOS (MONETARIOS, RF, RV, MIXTOS, ALTERNATIVO)

**Instrucciones:**

1. **Validación semántica recomendada** (no obligatoria si confianza alta)
2. **Auto-corrección solo si detecta inconsistencias**
3. **Logging de advertencias** (no críticas, ya que bloques especializados tienen alta precisión)

---

## 5. PRIORIZACIÓN DE VALIDACIONES

**Orden de aplicación en `classify_fund()`:**

```python
def classify_fund(kiid_text, isin):
    # 1. Clasificación especializada por bloque
    classification = detect_and_classify_by_block(kiid_text, isin)
    
    # 2. Traducción a idioma objetivo (Principio #8)
    classification = apply_language_homogeneity(classification)
    
    # 3. Validación semántica (Principio #9)
    validation_result = validate_all_semantic_consistency(classification)
    
    # 4. Auto-corrección si necesaria
    if not validation_result['is_valid']:
        classification = validation_result['corrected_record']
    
    # 5. Caracterización adicional
    classification = enrich_classification(classification, kiid_text)
    
    return classification
```

---

## 6. RELACIONES PENDIENTES DE ANÁLISIS (No validar aún)

**10 relaciones inter-atributos AÚN NO analizadas exhaustivamente:**

1. Nature → Type → Family (coherencia triangular completa) - **Parcialmente cubierto**
2. Subtype → Nature
3. Benchmark_Type → Nature/Type
4. Style_Profile → Nature
5. Investment_Universe → Theme
6. Market_Cap_Focus → Nature
7. Sector_Focus → Nature/Type
8. Recommended_Holding_Period → Profile/SRRI
9. Ongoing_Charge → Profile
10. Fund_Currency vs Portfolio_Currency

**Acción:** NO implementar validadores para estas relaciones hasta completar análisis cuantitativo.

---

## 7. GESTIÓN DE ERRORES Y LOGGING

### 7.1 NIVELES DE SEVERIDAD

```python
# ERROR (crítico) - Inconsistencia que DEBE corregirse
log_error(f"[{isin}] Nature-Type inconsistente: Nature='RV' con Type='Monetario'")

# WARNING (advertencia) - Inusual pero posible
log_warning(f"[{isin}] Perfil Conservador con Leverage=YES (inusual)")

# INFO (informativo) - Corrección aplicada
log_info(f"[{isin}] Auto-corrección: Replication_Method → PASSIVE")
```

### 7.2 MÉTRICAS DE VALIDACIÓN

**Recopilar métricas tras cada ciclo:**

```python
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
```

---

## 8. COMUNICACION

Comunicación clara, directa, concisa y honesta. Ingles como lenguaje de comunicación.


## 9. RESUMEN DE ACCIONES OBLIGATORIAS

**Para Claude:**

1. ✅ **Aplicar Principio #8** en TODOS los bloques:
   - Traducir Type, Family, Theme, Subtype según idioma objetivo
   - Validar homogeneidad lingüística antes de retornar

2. ✅ **Aplicar Principio #9** especialmente en RESTANTES:
   - Validar coherencia Nature→Type, Nature→Family
   - Validar completitud Universe→Sector/Geography
   - Validar Strategy→Replication, Accumulation→Distribution, Profile→SRRI
   - Auto-corregir inconsistencias críticas
   - Log advertencias pero no bloquear

3. ✅ **Detectar valores explícitamente:**
   - Derivatives_Usage: "NO" y "LIMITED" (no solo "YES")
   - Currency_Hedged: "Unhedged" (no solo "Hedged")

4. ✅ **Documentar decisiones:**
   - Log todas las correcciones aplicadas con ISIN
   - Log advertencias con contexto

---

**FIN CUSTOM INSTRUCTIONS V3 PERFECTA (10/10)**

*Actualización: 9 de abril de 2026*  
*Versión: 3.0 (Exhaustiva + Perfeccionada)*  
*Cobertura: Principio #8 + Principio #9 completo (INTRA + INTER)*  
*Mejoras aplicadas: Contexto introductorio, ejemplos concretos, referencias cruzadas, checklist de cumplimiento*
