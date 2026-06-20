## PRINCIPIO #8: Homogeneidad lingüística por columna (nivel global)

**Fecha última revisión:** 5 de abril de 2026  
**Fuente:** Análisis exhaustivo de Fund_master_20260401.xlsx (3.204 fondos)

---

### REGLA FUNDAMENTAL

**TODOS los valores de una misma columna clasificatoria deben estar en el MISMO idioma a nivel de toda la base de datos.**  
No se permite mezcla de idiomas dentro de una columna.

---

### ALCANCE

Aplica a **todas las columnas de texto con valores categóricos o semi-enumer ados** en `fund_master`:

**Columnas core de negocio (idioma: ESPAÑOL):**
- `Fund_Nature`, `Type`, `Family`, `Profile`, `Strategy`, `Geography`

**Columnas técnicas/especializadas (idioma: INGLÉS):**
- `Subtype`, `Theme`, `Style_Profile`, `Exposure_Bias`, `Benchmark_Type`
- `Sector_Focus`, `Market_Cap_Focus`, `Investment_Universe`
- `Hedging_Policy`, `Replication_Method`, `Accumulation_Policy`
- `Distribution_Frequency`, `Leverage_Used`, `Currency_Hedged`
- `SRRI_Quality_Flag`

**Columnas neutrales/técnicas (valores sin idioma):**
- `Derivatives_Usage`, `Data_Quality_Flag`, `Recommended_Holding_Period`, `Liquidity_Profile`

**Columnas excluidas del principio:**
- Identificadores: `ISIN`, `Fund_Name`, `Management_Company`, `fund_family_id`
- Numéricos: `SRRI`, `Ongoing_Charge`, `Entry_Fee_Pct`, `Exit_Fee_Pct`, `Sfdr_Article`
- Booleanos: `Is_ESG`
- Códigos ISO: `Fund_Currency`, `Portfolio_Currency`
- Nombres propios: `Benchmark_Declared` (nombres oficiales de índices)
- Timestamps: `Created_At`, `Updated_At`
- Metadata técnica: `Heuristic_Block`, `Heuristic_Core`

---

### RAZÓN

La homogeneidad lingüística por columna es **crítica** para:

1. **Agrupaciones y estadísticas consistentes**

```sql
-- ❌ PROBLEMA con mezcla de idiomas en Type:
SELECT Type, COUNT(*) AS n FROM fund_master GROUP BY Type;

-- Resultado con mezcla:
-- 'Gestión Activa': 1564
-- 'Active Management': 245  ← Mismo concepto, duplicado
-- Total aparente: 2 categorías | Total real: 1 categoría

-- ✓ SOLUCIÓN con homogeneidad (todo en ES):
-- 'Gestión Activa': 1809  ← Suma total sin duplicación
-- Total: 1 categoría única
```

2. **Filtros y búsquedas predecibles**

```sql
-- ❌ PROBLEMA: Usuario busca fondos de "Allocation" 
-- pero pierde los clasificados como "Asignación"
SELECT COUNT(*) FROM fund_master WHERE Type = 'Allocation';
-- Resultado: 467 fondos (incompleto)

-- ✓ SOLUCIÓN: Con homogeneidad, captura el 100%
SELECT COUNT(*) FROM fund_master WHERE Type = 'Asignación';
-- Resultado: 467 fondos (completo)
```

3. **Integridad de datos en P2/P3**
   - Cálculo de métricas por categoría requiere valores normalizados
   - Scoring regime-aware necesita agrupaciones sin ambigüedad semántica
   - Reportes mensuales sin duplicación de categorías

4. **Mantenibilidad del código**
   - Elimina necesidad de normalización ad-hoc en cada query
   - Lógica de clasificación más simple (no requiere diccionarios de traducción en tiempo de ejecución)
   - Reducción de bugs por valores semánticamente duplicados

---

### IDIOMA OBJETIVO POR COLUMNA

Definido según análisis real de Fund_master_20260401.xlsx:

| Columna | Idioma objetivo | Estado actual | Acción |
|---------|----------------|---------------|--------|
| **Fund_Nature** | ESPAÑOL | ✅ Homogéneo ES | Mantener |
| **Type** | ESPAÑOL | ❌ Mezcla ES/EN | **Normalizar a ES** |
| **Subtype** | INGLÉS | ❌ Mezcla EN/ES | **Normalizar a EN** |
| **Family** | ESPAÑOL | ❌ Mezcla ES/EN | **Normalizar a ES** |
| **Profile** | ESPAÑOL | ✅ Homogéneo ES | Mantener |
| **Strategy** | ESPAÑOL | ✅ Homogéneo ES | Mantener |
| **Geography** | ESPAÑOL | ✅ Homogéneo ES | Mantener |
| **Theme** | INGLÉS | ❌ Mezcla UNKNOWN/EN/ES | **Normalizar a EN** |
| **Style_Profile** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **Exposure_Bias** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **Sector_Focus** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **Market_Cap_Focus** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **Investment_Universe** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **Benchmark_Type** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **Hedging_Policy** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **Replication_Method** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **Accumulation_Policy** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **Distribution_Frequency** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **Leverage_Used** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **Currency_Hedged** | INGLÉS | ✅ Homogéneo EN | Mantener |
| **SRRI_Quality_Flag** | INGLÉS | ✅ Homogéneo EN | Mantener |

**Criterio de asignación:**
- **ESPAÑOL:** Columnas core de negocio que el usuario final interpreta directamente (Fund_Nature, Type, Profile, Strategy, Geography, Family)
- **INGLÉS:** Columnas técnicas/especializadas del sector financiero internacional (Style, Sector, Theme, Exposure, Market Cap, Benchmark, etc.)

**Estado actual (5-abr-2026):**
- 20 columnas homogéneas ✅ (80%)
- 5 columnas con mezcla ❌ (20%) → Requieren normalización

---

### MAPEOS DE TRADUCCIÓN COMPLETOS

Basados en valores reales encontrados en la BD:

#### Type (Objetivo: ESPAÑOL)

```python
TYPE_TRANSLATION_MAP = {
    # EN → ES
    'Allocation': 'Asignación',
    'Absolute Return': 'Retorno Absoluto',
    'Commodities': 'Materias Primas',
    'Target Volatility': 'Volatilidad Objetivo',
    'Total Return': 'Retorno Total',
    'Tactical Allocation': 'Asignación Táctica',
    'Real Assets': 'Activos Reales',
    
    # UNKNOWN → ES
    'Target Maturity': 'Vencimiento Objetivo',
    'Floating Rate CP': 'CP Tipo Flotante',
    
    # Ya en ES - mantener
    'Gestión Activa': 'Gestión Activa',
    'Renta Fija Flexible': 'Renta Fija Flexible',
    'Renta Fija Corto Plazo': 'Renta Fija Corto Plazo',
    'Monetario': 'Monetario',
    'Indexado': 'Indexado',
    'Crédito CP': 'Crédito CP',
    'Monetario Privado': 'Monetario Privado',
    'Monetario Público': 'Monetario Público',
    'Deuda Pública CP': 'Deuda Pública CP',
    'Estructurado': 'Estructurado',
    'Gobierno CP': 'Gobierno CP'
}
```

**Fondos afectados:** 567 (17,7%)

#### Family (Objetivo: ESPAÑOL)

```python
FAMILY_TRANSLATION_MAP = {
    # EN → ES
    'RV Core': 'RV Núcleo',
    'Income Oriented': 'Orientado a Ingresos',
    'RF High Yield': 'RF Alto Rendimiento',
    
    # Ya en ES - mantener
    'Renta Fija Corto Plazo': 'Renta Fija Corto Plazo',
    'Renta Fija Flexible': 'Renta Fija Flexible',
    'Mixtos': 'Mixtos',
    'RV Temática': 'RV Temática',
    'Monetario': 'Monetario',
    'Retorno Absoluto': 'Retorno Absoluto',
    'Flexible Estratégico': 'Flexible Estratégico',
    'Activos Reales': 'Activos Reales',
    'RF Emergentes': 'RF Emergentes',
    'RF Inflación': 'RF Inflación',
    
    # Acrónimos técnicos - mantener
    'VNAV': 'VNAV',
    'LVNAV': 'LVNAV',
    'CNAV': 'CNAV'
}
```

**Fondos afectados:** 1.575 (49,2%)

#### Theme (Objetivo: INGLÉS)

```python
THEME_TRANSLATION_MAP = {
    # ES → EN
    'Inflación': 'Inflation',
    
    # Ya en EN/UNKNOWN (técnicos internacionales) - mantener
    'Technology': 'Technology',
    'Climate / Clean Energy': 'Climate / Clean Energy',
    'Artificial Intelligence': 'Artificial Intelligence',
    'Healthcare': 'Healthcare',
    'Gold': 'Gold',
    'Energy': 'Energy',
    'Water': 'Water',
    'Infrastructure': 'Infrastructure',
    'Financials': 'Financials',
    'Silver Economy': 'Silver Economy',
    'Real Estate': 'Real Estate',
    'Digital': 'Digital',
    'Mining': 'Mining',
    'Consumer Brands': 'Consumer Brands',
    'Megatrends': 'Megatrends',
    'Robotics': 'Robotics',
    'Biotechnology': 'Biotechnology',
    'Consumer / Food & Beverage': 'Consumer / Food & Beverage',
    'Cybersecurity': 'Cybersecurity',
    'Healthcare / MedTech': 'Healthcare / MedTech',
    'Insurance': 'Insurance'
}
```

**Fondos afectados:** 6 (0,2%)

#### Subtype (Objetivo: INGLÉS)

```python
SUBTYPE_TRANSLATION_MAP = {
    # ES → EN
    'Fondo Indexado': 'Index Fund',
    
    # Ya en EN/UNKNOWN (técnicos) - mantener
    'Opportunistic': 'Opportunistic',
    'Physical / Derivatives': 'Physical / Derivatives',
    'ETF': 'ETF',
    'Low Duration': 'Low Duration',
    'Autocallable': 'Autocallable',
    'Floating Rate Notes': 'Floating Rate Notes',
    'Global Macro': 'Global Macro',
    'Total Return Bond': 'Total Return Bond',
    'Long/Short': 'Long/Short',
    'Real Estate': 'Real Estate',
    'Relative Value / Arbitrage': 'Relative Value / Arbitrage'
}
```

**Fondos afectados:** 70 (2,2%)

---

### VALIDACIÓN EN CÓDIGO

Implementación en `classify_utils.py`:

```python
# classify_utils.py — Diccionario de valores permitidos por columna

ALLOWED_VALUES_BY_COLUMN = {
    'Fund_Nature': {
        'lang': 'ES',
        'values': [
            'Renta Variable', 'Mixtos', 'Renta Fija Flexible',
            'Renta Fija Corto Plazo', 'Monetario', 'Alternativo',
            'Restantes', 'Estructurado'
        ]
    },
    'Type': {
        'lang': 'ES',
        'values': [
            'Gestión Activa', 'Asignación', 'Renta Fija Flexible',
            'Renta Fija Corto Plazo', 'Monetario', 'Indexado',
            'Crédito CP', 'Retorno Absoluto', 'Materias Primas',
            'Volatilidad Objetivo', 'Retorno Total', 'Asignación Táctica',
            'Activos Reales', 'Monetario Privado', 'Monetario Público',
            'Deuda Pública CP', 'Estructurado', 'Vencimiento Objetivo',
            'Gobierno CP', 'CP Tipo Flotante'
        ]
    },
    'Profile': {
        'lang': 'ES',
        'values': ['Dinámico', 'Moderado', 'Conservador']
    },
    'Strategy': {
        'lang': 'ES',
        'values': ['Activo', 'Indexado', 'Pasivo']
    },
    'Family': {
        'lang': 'ES',
        'values': [
            'RV Núcleo', 'Renta Fija Corto Plazo', 'Renta Fija Flexible',
            'Mixtos', 'RV Temática', 'Orientado a Ingresos', 'Monetario',
            'Retorno Absoluto', 'RF Alto Rendimiento', 'Flexible Estratégico',
            'VNAV', 'Activos Reales', 'RF Emergentes', 'LVNAV',
            'RF Inflación', 'CNAV'
        ]
    },
    'Geography': {
        'lang': 'ES',
        'values': [
            'Global', 'Europa', 'EE.UU.', 'Asia', 'Japón', 'Emergentes',
            'Eurozona', 'Reino Unido', 'China', 'Pacífico'
        ]
    },
    'Theme': {
        'lang': 'EN',
        'values': [
            'Technology', 'Climate / Clean Energy', 'Artificial Intelligence',
            'Healthcare', 'Gold', 'Energy', 'Water', 'Infrastructure',
            'Financials', 'Silver Economy', 'Real Estate', 'Digital',
            'Mining', 'Consumer Brands', 'Megatrends', 'Inflation',
            'Robotics', 'Biotechnology', 'Consumer / Food & Beverage',
            'Cybersecurity', 'Healthcare / MedTech', 'Insurance'
        ]
    },
    'Subtype': {
        'lang': 'EN',
        'values': [
            'Index Fund', 'Opportunistic', 'Physical / Derivatives', 'ETF',
            'Low Duration', 'Autocallable', 'Floating Rate Notes',
            'Global Macro', 'Total Return Bond', 'Long/Short',
            'Real Estate', 'Relative Value / Arbitrage'
        ]
    },
    # ... resto de columnas
}


def validate_column_language_homogeneity(column_name, value):
    """
    Valida que el valor propuesto para una columna cumple homogeneidad lingüística.
    
    Args:
        column_name: Nombre de la columna (ej: 'Type', 'Fund_Nature')
        value: Valor propuesto a asignar
    
    Returns:
        tuple: (is_valid: bool, expected_lang: str, correction: str or None, error_msg: str or None)
    """
    if column_name not in ALLOWED_VALUES_BY_COLUMN:
        return True, None, None, None  # Columna no regulada
    
    config = ALLOWED_VALUES_BY_COLUMN[column_name]
    expected_lang = config['lang']
    allowed_values = config['values']
    
    if value in allowed_values:
        return True, expected_lang, None, None
    
    # Valor no está en lista permitida
    # Intentar auto-traducción usando mapeos conocidos
    translation_maps = {
        'Type': TYPE_TRANSLATION_MAP,
        'Family': FAMILY_TRANSLATION_MAP,
        'Theme': THEME_TRANSLATION_MAP,
        'Subtype': SUBTYPE_TRANSLATION_MAP
    }
    
    if column_name in translation_maps:
        translation_map = translation_maps[column_name]
        if value in translation_map:
            corrected_value = translation_map[value]
            if corrected_value in allowed_values:
                return False, expected_lang, corrected_value, (
                    f"Valor '{value}' auto-traducido a '{corrected_value}' "
                    f"para cumplir homogeneidad {expected_lang}"
                )
    
    # No se puede auto-corregir
    error_msg = (
        f"Columna '{column_name}' requiere valores en {expected_lang}. "
        f"Valor '{value}' no válido. "
        f"Valores permitidos: {allowed_values[:10]}..."  # Primeros 10 para no saturar log
    )
    return False, expected_lang, None, error_msg


# Integración en classify_fund()
def classify_fund(kiid_text, isin):
    """Clasificación con validación de homogeneidad lingüística."""
    
    classification = {
        'Fund_Nature': detect_nature(kiid_text),
        'Type': detect_type(kiid_text),
        'Profile': assign_profile_from_srri(srri),
        'Strategy': detect_strategy(kiid_text),
        'Family': infer_family(kiid_text, nature, type_val),
        'Geography': detect_geography(kiid_text),
        'Theme': detect_theme(kiid_text),
        'Subtype': detect_subtype(kiid_text),
        # ... resto de atributos
    }
    
    # VALIDAR HOMOGENEIDAD LINGÜÍSTICA POR COLUMNA
    corrected = {}
    for column, value in classification.items():
        if value is None:
            corrected[column] = None
            continue
        
        is_valid, expected_lang, correction, error_msg = \
            validate_column_language_homogeneity(column, value)
        
        if is_valid:
            corrected[column] = value
        elif correction:
            # Auto-corrección aplicada
            corrected[column] = correction
            log_info(f"ISIN {isin}: {error_msg}")
        else:
            # No se puede auto-corregir → NULL + log para revisión manual
            corrected[column] = None
            log_warning(f"ISIN {isin}: {error_msg}")
    
    return corrected
```

---

### MIGRACIÓN DE DATOS EXISTENTES

Ver script completo `scripts/mig/normalize_column_languages_v17.py` para:

1. Backup automático de BD
2. Dry-run con reporte de cambios
3. Aplicación de normalización
4. Verificación post-migración

**Comando de ejecución:**

```batch
cd c:\desarrollo\fondos
conda activate des

REM Dry run (solo reporta cambios)
python scripts\mig\normalize_column_languages_v17.py

REM Aplicar cambios (tras revisar dry run)
python scripts\mig\normalize_column_languages_v17.py --apply
```

---

### CONSECUENCIAS DE VIOLACIÓN

Si durante clasificación se detecta un valor en idioma incorrecto:

1. **Log de WARNING** en `ingestion_log` con detalle del valor problemático
2. **Intento de auto-traducción** usando mapeos conocidos (TYPE_TRANSLATION_MAP, etc.)
3. **Si auto-traducción exitosa:** Aplicar corrección + log INFO de la normalización
4. **Si no hay traducción conocida:** valor = NULL + log WARNING para revisión manual
5. **Documentar en `Inference_Trace`** el valor rechazado y la razón

---

### QUERIES DE VERIFICACIÓN

```sql
-- Verificar homogeneidad de Type (debe ser todo ES)
SELECT Type, COUNT(*) AS n
FROM fund_master
WHERE Type IS NOT NULL
GROUP BY Type
ORDER BY n DESC;
-- Resultado esperado: Solo valores en español

-- Detectar posibles mezclas en Type
SELECT Type, COUNT(*) AS n
FROM fund_master
WHERE Type IN ('Allocation', 'Absolute Return', 'Commodities', 
               'Target Volatility', 'Total Return', 'Tactical Allocation',
               'Real Assets', 'Target Maturity', 'Floating Rate CP')
GROUP BY Type;
-- Resultado esperado: 0 filas (todos traducidos a ES)

-- Verificar homogeneidad de Theme (debe ser todo EN)
SELECT Theme, COUNT(*) AS n
FROM fund_master
WHERE Theme IS NOT NULL
GROUP BY Theme
ORDER BY n DESC;
-- Resultado esperado: Solo valores en inglés, sin 'Inflación'
```

---

**FIN PRINCIPIO #8 (ACTUALIZADO CON DATOS REALES)**

*Última actualización: 5 de abril de 2026*  
*Fuente: Análisis exhaustivo de Fund_master_20260401.xlsx*  
*Fondos analizados: 3.204*  
*Columnas analizadas: 25*  
*Columnas con mezcla detectada: 5 (Type, Family, Theme, Subtype, Strategy)*
