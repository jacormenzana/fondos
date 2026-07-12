# PRINCIPIO #9: Consistencia semántica inter e intra-atributos

**Fecha:** 5 de abril de 2026  
**Prioridad:** CRÍTICA (tan importante como Principio #1 Root Cause)  
**Estado:** Pendiente análisis cuantitativo completo

---

## REGLA FUNDAMENTAL

Los datos de un fondo deben ser **semánticamente consistentes a dos niveles**:

1. **Consistencia INTER-atributos:** Las relaciones lógicas entre diferentes columnas deben respetarse
2. **Consistencia INTRA-atributo:** Los valores dentro de una misma columna deben tener significado claro, no ambiguo, y mutuamente excluyentes

---

## PROBLEMA 1: Inconsistencia INTER-atributos

### Definición

Cuando dos o más atributos de un fondo contienen valores que son **lógicamente incompatibles** o **incompletos** entre sí.

### Ejemplos de inconsistencias críticas detectadas

#### Caso 1: Fund_Nature vs Type (INCOMPATIBILIDAD LÓGICA)

```python
# ❌ IMPOSIBLE SEMÁNTICAMENTE
{
    'ISIN': 'LU1234567890',
    'Fund_Nature': 'Renta Variable',
    'Type': 'Monetario'  # ← Un fondo de RV NO PUEDE ser Type=Monetario
}

# ✓ CORRECTO
{
    'ISIN': 'LU1234567890',
    'Fund_Nature': 'Renta Variable',
    'Type': 'Gestión Activa'  # ← Coherente con Nature
}
```

**Consecuencia:** Queries de búsqueda producen resultados absurdos:
```sql
-- Usuario busca fondos monetarios
SELECT * FROM fund_master WHERE Type = 'Monetario';
-- Resultado incluye fondos de Renta Variable ❌ ← Incorrecto
```

---

#### Caso 2: Fund_Nature vs Family (INCOMPATIBILIDAD LÓGICA)

```python
# ❌ IMPOSIBLE SEMÁNTICAMENTE
{
    'ISIN': 'LU9876543210',
    'Fund_Nature': 'Renta Variable',
    'Family': 'Monetario'  # ← Un fondo de RV NO PUEDE pertenecer a Family=Monetario
}

# ✓ CORRECTO
{
    'ISIN': 'LU9876543210',
    'Fund_Nature': 'Renta Variable',
    'Family': 'RV Núcleo'  # ← Coherente con Nature
}
```

---

#### Caso 3: Investment_Universe vs Sector_Focus (INCOMPLETITUD)

```python
# ❌ INCOMPLETO
{
    'ISIN': 'LU1111111111',
    'Investment_Universe': 'Sector',
    'Sector_Focus': None  # ← Si Universe=Sector, DEBE especificar cuál sector
}

# ✓ CORRECTO
{
    'ISIN': 'LU1111111111',
    'Investment_Universe': 'Sector',
    'Sector_Focus': 'Technology'  # ← Especifica el sector
}
```

**Consecuencia:** Fondos sectoriales sin identificación del sector → Imposible agrupar o analizar correctamente

---

#### Caso 4: Type="...Global..." vs Geography (INCOHERENCIA)

```python
# ❌ INCOHERENTE
{
    'ISIN': 'LU2222222222',
    'Type': 'Bolsa Global',
    'Geography': 'Europa'  # ← Si Type dice Global, Geography debe ser Global
}

# ✓ CORRECTO
{
    'ISIN': 'LU2222222222',
    'Type': 'Bolsa Global',
    'Geography': 'Global'  # ← Coherente
}
```

---

### Reglas de dependencia lógica INTER-atributos

Estas reglas deben validarse **SIEMPRE** antes de persistir un fondo:

#### Regla I-1: Fund_Nature → Type (dependencia fuerte)

| Fund_Nature | Type permitidos |
|-------------|-----------------|
| **Monetario** | Monetario, Monetario Público, Monetario Privado |
| **Renta Fija Corto Plazo** | Renta Fija Corto Plazo, Crédito CP, Deuda Pública CP, Gobierno CP, CP Tipo Flotante |
| **Renta Fija Flexible** | Renta Fija Flexible, Retorno Total, Vencimiento Objetivo |
| **Renta Variable** | Gestión Activa, Indexado (solo si Strategy=Indexado/Pasivo) |
| **Mixtos** | Asignación, Asignación Táctica, Volatilidad Objetivo |
| **Alternativo** | Retorno Absoluto, Materias Primas, Activos Reales, Estructurado |

**Validación en código:**

```python
ALLOWED_TYPE_BY_NATURE = {
    'Monetario': ['Monetario', 'Monetario Público', 'Monetario Privado'],
    'Renta Fija Corto Plazo': [
        'Renta Fija Corto Plazo', 'Crédito CP', 'Deuda Pública CP', 
        'Gobierno CP', 'CP Tipo Flotante'
    ],
    'Renta Fija Flexible': [
        'Renta Fija Flexible', 'Retorno Total', 'Vencimiento Objetivo'
    ],
    'Renta Variable': ['Gestión Activa', 'Indexado'],
    'Mixtos': ['Asignación', 'Asignación Táctica', 'Volatilidad Objetivo'],
    'Alternativo': [
        'Retorno Absoluto', 'Materias Primas', 'Activos Reales', 'Estructurado'
    ],
    'Restantes': None,  # Sin restricción (catch-all)
    'Estructurado': ['Estructurado']
}

def validate_nature_type_consistency(nature, type_val):
    """Valida coherencia Nature → Type."""
    if nature is None or type_val is None:
        return True, None  # NULL permitido
    
    allowed_types = ALLOWED_TYPE_BY_NATURE.get(nature)
    
    if allowed_types is None:
        return True, None  # Nature sin restricción
    
    if type_val not in allowed_types:
        error_msg = (
            f"Type='{type_val}' incompatible con Fund_Nature='{nature}'. "
            f"Tipos permitidos: {allowed_types}"
        )
        return False, error_msg
    
    return True, None
```

---

#### Regla I-2: Fund_Nature → Family (dependencia fuerte)

| Fund_Nature | Family permitidos |
|-------------|-------------------|
| **Monetario** | Monetario, VNAV, LVNAV, CNAV |
| **Renta Fija Corto Plazo** | Renta Fija Corto Plazo |
| **Renta Fija Flexible** | Renta Fija Flexible, RF Alto Rendimiento, RF Emergentes, RF Inflación |
| **Renta Variable** | RV Núcleo, RV Temática, Orientado a Ingresos |
| **Mixtos** | Mixtos, Flexible Estratégico |
| **Alternativo** | Retorno Absoluto, Activos Reales |

**Validación:**

```python
ALLOWED_FAMILY_BY_NATURE = {
    'Monetario': ['Monetario', 'VNAV', 'LVNAV', 'CNAV'],
    'Renta Fija Corto Plazo': ['Renta Fija Corto Plazo'],
    'Renta Fija Flexible': [
        'Renta Fija Flexible', 'RF Alto Rendimiento', 
        'RF Emergentes', 'RF Inflación'
    ],
    'Renta Variable': ['RV Núcleo', 'RV Temática', 'Orientado a Ingresos'],
    'Mixtos': ['Mixtos', 'Flexible Estratégico'],
    'Alternativo': ['Retorno Absoluto', 'Activos Reales'],
    'Restantes': None,
    'Estructurado': None
}

def validate_nature_family_consistency(nature, family):
    """Valida coherencia Nature → Family."""
    if nature is None or family is None:
        return True, None
    
    allowed_families = ALLOWED_FAMILY_BY_NATURE.get(nature)
    
    if allowed_families is None:
        return True, None
    
    if family not in allowed_families:
        error_msg = (
            f"Family='{family}' incompatible con Fund_Nature='{nature}'. "
            f"Familias permitidas: {allowed_families}"
        )
        return False, error_msg
    
    return True, None
```

---

#### Regla I-3: Investment_Universe → Atributos especializados (completitud)

| Investment_Universe | Atributo requerido | Validación |
|---------------------|-------------------|------------|
| **Sector** | Sector_Focus | MUST NOT be NULL |
| **Regional** | Geography | MUST NOT be NULL |
| **Liquidity** | Liquidity_Profile | (ya poblado por defecto) |

**Validación:**

```python
def validate_universe_completeness(universe, sector_focus, geography):
    """Valida completitud según Investment_Universe."""
    if universe is None:
        return True, None
    
    if universe == 'Sector' and sector_focus is None:
        return False, "Investment_Universe='Sector' requiere Sector_Focus poblado"
    
    if universe == 'Regional' and geography is None:
        return False, "Investment_Universe='Regional' requiere Geography poblado"
    
    return True, None
```

---

#### Regla I-4: Type="...Global..." → Geography (coherencia textual)

```python
def validate_global_coherence(type_val, geography):
    """Si Type contiene 'Global', Geography debe ser 'Global'."""
    if type_val is None or geography is None:
        return True, None
    
    if 'global' in type_val.lower() and geography != 'Global':
        return False, (
            f"Type='{type_val}' contiene 'Global' pero Geography='{geography}'. "
            f"Debe ser Geography='Global'"
        )
    
    return True, None
```

---

## PROBLEMA 2: Inconsistencia INTRA-atributo

### Definición

Cuando los **valores de una misma columna** tienen **significado ambiguo** o no son mutuamente excluyentes.

### Ejemplo crítico: Type="Monetario" vs "Monetario Público" vs "Monetario Privado"

**Problema identificado por José:**

```python
# Valores actuales en Type para fondos Monetario:
Type_monetario_values = [
    'Monetario',           # ← ¿Qué significa esto?
    'Monetario Público',   # ← Específico
    'Monetario Privado'    # ← Específico
]
```

**Pregunta semántica:** ¿Qué significa "Monetario" solo (sin Público/Privado)?

**Opciones interpretativas:**
1. **"Monetario" = "Monetario Mixto"** (tiene activos públicos y privados)
2. **"Monetario" = "No determinado"** (no se pudo extraer del KIID si es público o privado)
3. **"Monetario" = "Valor genérico"** (nivel superior, sin especificación)

**Consecuencia de la ambigüedad:**

```sql
-- Usuario quiere contar fondos monetarios mixtos
SELECT COUNT(*) FROM fund_master WHERE Type = 'Monetario';
-- ¿Este query captura mixtos? ¿O captura no-determinados? ← AMBIGUO
```

---

### Propuesta de resolución: Jerarquía semántica explícita

#### Opción A: Eliminar ambigüedad mediante especificidad completa

```python
# ANTES (ambiguo)
Type_values = ['Monetario', 'Monetario Público', 'Monetario Privado']

# DESPUÉS (sin ambigüedad)
Type_values = [
    'Monetario Mixto',      # ← Explícito: tiene públicos y privados
    'Monetario Público',    # ← Explícito: solo públicos
    'Monetario Privado',    # ← Explícito: solo privados
    'Monetario Indeterminado'  # ← Explícito: no se pudo determinar del KIID
]
```

**Migración:**
```sql
-- Análisis previo: ¿Qué fondos tienen Type='Monetario' solo?
-- Si tienen activos públicos Y privados → 'Monetario Mixto'
-- Si no se pudo determinar del KIID → 'Monetario Indeterminado'
```

---

#### Opción B: Jerarquía de valores con convención clara

```python
# Convención documentada:
# - 'Monetario' = Valor genérico (nivel 1, sin especificación)
# - 'Monetario Público' / 'Monetario Privado' = Especificación (nivel 2)

# Regla de query:
# - Para capturar TODOS los monetarios: WHERE Fund_Nature = 'Monetario'
# - Para capturar solo específicos: WHERE Type IN ('Monetario Público', 'Monetario Privado')
# - 'Monetario' solo se usa cuando no hay suficiente info en KIID
```

---

### Otros casos de ambigüedad INTRA-atributo a revisar

#### Geography: "Global" vs "Mundial"

```python
# ¿Son sinónimos o tienen significado distinto?
Geography_values = ['Global', 'Mundial']  # ← Verificar si ambos existen
```

#### Investment_Universe: Granularidad de valores

```python
# Verificar que valores son mutuamente excluyentes
Investment_Universe_values = [
    'Global',
    'Regional',
    'Sector',
    'Liquidity',
    # ... resto de valores
]
# ¿'Global' y 'Regional' son excluyentes? ¿O un fondo puede ser ambos?
```

---

## IMPLEMENTACIÓN: Validador completo de consistencia semántica

```python
# classify_utils.py — Añadir validador de consistencia

def validate_semantic_consistency(fund_record):
    """
    Valida consistencia semántica inter e intra-atributos.
    
    Returns:
        tuple: (is_valid: bool, errors: list[str])
    """
    errors = []
    
    # VALIDACIONES INTER-ATRIBUTOS
    
    # Regla I-1: Nature → Type
    is_valid, error_msg = validate_nature_type_consistency(
        fund_record.get('Fund_Nature'),
        fund_record.get('Type')
    )
    if not is_valid:
        errors.append(f"[I-1] {error_msg}")
    
    # Regla I-2: Nature → Family
    is_valid, error_msg = validate_nature_family_consistency(
        fund_record.get('Fund_Nature'),
        fund_record.get('Family')
    )
    if not is_valid:
        errors.append(f"[I-2] {error_msg}")
    
    # Regla I-3: Universe → Completitud
    is_valid, error_msg = validate_universe_completeness(
        fund_record.get('Investment_Universe'),
        fund_record.get('Sector_Focus'),
        fund_record.get('Geography')
    )
    if not is_valid:
        errors.append(f"[I-3] {error_msg}")
    
    # Regla I-4: Type Global → Geography Global
    is_valid, error_msg = validate_global_coherence(
        fund_record.get('Type'),
        fund_record.get('Geography')
    )
    if not is_valid:
        errors.append(f"[I-4] {error_msg}")
    
    # VALIDACIONES INTRA-ATRIBUTO
    # (A implementar tras análisis cuantitativo completo)
    
    if errors:
        return False, errors
    else:
        return True, []


# Integración en classify_fund()
def classify_fund(kiid_text, isin):
    """Clasificación con validación de consistencia semántica."""
    
    # ... clasificación actual
    
    classification = {
        'Fund_Nature': detect_nature(kiid_text),
        'Type': detect_type(kiid_text),
        'Family': infer_family(...),
        # ... resto de atributos
    }
    
    # VALIDAR CONSISTENCIA SEMÁNTICA
    is_consistent, errors = validate_semantic_consistency(classification)
    
    if not is_consistent:
        for error in errors:
            log_error(f"ISIN {isin}: Inconsistencia semántica - {error}")
        
        # Estrategia de corrección:
        # 1. Si Nature-Type inconsistente → Recalcular Type desde Nature
        # 2. Si Universe-Sector inconsistente → Marcar Sector como pendiente extracción
        # 3. Documentar en Inference_Trace
        
        classification = auto_correct_inconsistencies(classification, errors)
    
    return classification


def auto_correct_inconsistencies(classification, errors):
    """Intenta auto-corregir inconsistencias detectadas."""
    
    for error in errors:
        if '[I-1]' in error:
            # Inconsistencia Nature-Type → Recalcular Type
            nature = classification['Fund_Nature']
            allowed_types = ALLOWED_TYPE_BY_NATURE.get(nature, [])
            
            if allowed_types:
                # Asignar primer Type permitido (genérico)
                classification['Type'] = allowed_types[0]
                log_info(f"Auto-corrección I-1: Type ajustado a '{allowed_types[0]}'")
        
        elif '[I-2]' in error:
            # Inconsistencia Nature-Family → Recalcular Family
            nature = classification['Fund_Nature']
            allowed_families = ALLOWED_FAMILY_BY_NATURE.get(nature, [])
            
            if allowed_families:
                classification['Family'] = allowed_families[0]
                log_info(f"Auto-corrección I-2: Family ajustado a '{allowed_families[0]}'")
        
        elif '[I-3]' in error:
            # Incompletitud Universe-Sector → Marcar para extracción manual
            if 'Sector_Focus' in error:
                classification['Investment_Universe'] = None  # Resetear hasta tener Sector
                log_warning("Auto-corrección I-3: Universe reseteado (falta Sector_Focus)")
        
        elif '[I-4]' in error:
            # Incoherencia Type-Geography → Ajustar Geography desde Type
            if 'global' in classification['Type'].lower():
                classification['Geography'] = 'Global'
                log_info("Auto-corrección I-4: Geography ajustado a 'Global'")
    
    return classification
```

---

## QUERIES DE DETECCIÓN DE INCONSISTENCIAS

### Query 1: Detectar fondos con Nature-Type inconsistentes

```sql
-- Fondos de Renta Variable con Type=Monetario (IMPOSIBLE)
SELECT ISIN, Fund_Name, Fund_Nature, Type
FROM fund_master
WHERE Fund_Nature = 'Renta Variable'
  AND Type IN ('Monetario', 'Monetario Público', 'Monetario Privado');
-- Esperado: 0 filas
```

### Query 2: Detectar fondos con Nature-Family inconsistentes

```sql
-- Fondos de Renta Variable con Family=Monetario (IMPOSIBLE)
SELECT ISIN, Fund_Name, Fund_Nature, Family
FROM fund_master
WHERE Fund_Nature = 'Renta Variable'
  AND Family = 'Monetario';
-- Esperado: 0 filas
```

### Query 3: Detectar fondos con Universe-Sector incompletos

```sql
-- Fondos con Universe=Sector sin Sector_Focus
SELECT ISIN, Fund_Name, Investment_Universe, Sector_Focus
FROM fund_master
WHERE Investment_Universe = 'Sector'
  AND Sector_Focus IS NULL;
-- Esperado: 0 filas (o lista pequeña para revisión manual)
```

### Query 4: Detectar ambigüedad "Monetario" solo

```sql
-- Analizar fondos con Type='Monetario' (sin Público/Privado)
SELECT ISIN, Fund_Name, Type, Management_Company
FROM fund_master
WHERE Type = 'Monetario'
ORDER BY Management_Company;
-- Revisar si hay patrón que sugiera si son Mixtos o Indeterminados
```

---

## PRÓXIMOS PASOS

### Fase 1: Análisis cuantitativo completo (PENDIENTE)

**Requiere:** Volver a subir Fund_master_20260401.xlsx

**Análisis a ejecutar:**
1. Detectar TODAS las inconsistencias Nature-Type-Family
2. Cuantificar fondos con Universe-Sector incompletos
3. Identificar todos los casos de ambigüedad INTRA-atributo
4. Generar matriz completa de combinaciones Nature×Type×Family existentes
5. Reportar fondos problemáticos con ISINs específicos

---

### Fase 2: Definición de convenciones semánticas

Basado en análisis cuantitativo, decidir:

1. **"Monetario" solo:** ¿Es Mixto o Indeterminado?
2. **Jerarquía de valores:** ¿Genérico vs Específico?
3. **Valores mutuamente excluyentes:** Documentar explícitamente

---

### Fase 3: Migración de datos inconsistentes

Script `scripts/mig/fix_semantic_inconsistencies_v17.py`:
- Corregir Nature-Type-Family inconsistentes
- Normalizar "Monetario" según convención decidida
- Completar Universe-Sector incompletos (o marcar para revisión manual)

---

### Fase 4: Integración en pipeline

Modificar `classify_utils.py`:
- Añadir `validate_semantic_consistency()` en `classify_fund()`
- Añadir `auto_correct_inconsistencies()` con estrategias inteligentes
- Log de advertencia para casos no auto-corregibles

---

## CONSECUENCIAS DE VIOLACIÓN

Si se persiste un fondo con inconsistencia semántica:

1. **Log de ERROR crítico** en `ingestion_log`
2. **Intento de auto-corrección** usando reglas I-1 a I-4
3. **Si no auto-corregible:** Rechazar clasificación completa → todos los atributos = NULL
4. **Documentar en `Inference_Trace`** la inconsistencia detectada y acción tomada
5. **Marcar fondo para revisión manual** en tabla de control

---

**FIN PRINCIPIO #9 (MARCO CONCEPTUAL)**

*Pendiente: Análisis cuantitativo completo con Fund_master_20260401.xlsx*  
*Próximo paso: José vuelve a subir archivo para ejecutar detección exhaustiva*
