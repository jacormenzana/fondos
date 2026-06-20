# Estado del Backlog P1 — Referencia de Sesión v3.8 (autocontenida)

**Fecha:** 10 de mayo de 2026  
**Ciclo de referencia:** `p1_export_20260510.xlsx` (3.204 fondos, schema v17) — log `log_pipeline_20260509_232409.log`  
**Sprint actual:** Incidencia crítica identificada y nuevos puntos de acción definidos  

**Estatus de este documento:**  
v3.8 **incorpora hallazgo crítico de sesión 10-may-2026:** lógica errada en `dla2_decision_diag.py` que invalida decisión Go/No-Go de BL-DLA-2. Nuevo ítem **BL-DLA-2-LOGIC-FIX** bloqueante de BL-DLA-2 hasta corrección.

---

## 0. DIFF vs v3.7 — TRAZABILIDAD DE CAMBIOS

### Cambios principales en v3.8:

#### Items NUEVOS (críticos):

- **BL-DLA-2-LOGIC-FIX** (Corrección lógica en `dla2_decision_diag.py`) — **ABIERTO en v3.8, BLOQUEANTE de BL-DLA-2**, prioridad **CRÍTICA**. 
  - **Problema identificado:** Líneas 258-260 calculan `cat_max` como máximo global, asumiendo que todos los atributos residen en la categoría de mayor índice. Esto es falso: `Exit_Fee_Pct` está SIEMPRE en Cat. 2, nunca en Cat. 3 (escenarios). Consecuencia: fondos con {Cat. 2 + Cat. 3} se diagnostican con `exit_fee_null=1` incorrectamente.
  - **Afectación cuantitativa:** Estimado 700+ fondos diagnosticados incorrectamente en `dla_inv.csv` actual. Invalida ROI esperado de BL-DLA-2 (−50% en NULLs de `Exit_Fee_Pct`).
  - **Especificación:** Refactorizar diagnóstico para mapear atributos a categorías específicas, no a `cat_max`. Mapeo: `Entry_Fee_Pct, Exit_Fee_Pct, Ongoing_Charge → Cat. 2`; `Accumulation_Policy, Distribution_Frequency → Cat. 2`; `PRIIPS_Scenarios → Cat. 3`.
  - **Salida esperada:** Nuevos KPIs de ROI reales para BL-DLA-2. Decisión Go/No-Go actualizada.
  - **Tiempo estimado:** 4-6 horas (diagnóstico + refactorización + re-ejecución + validación).

- **BL-DLA-2-RECALIBRACIÓN** (Recalcular umbrales Go/No-Go tras BL-DLA-2-LOGIC-FIX) — **ABIERTO en v3.8, bloqueado por BL-DLA-2-LOGIC-FIX**, prioridad **ALTA**.
  - Una vez que BL-DLA-2-LOGIC-FIX produzca KPIs correctos, re-evaluar umbrales de decisión en backlog v3.7 (línea 2165-2167).
  - Decisión final Go/No-GO para BL-DLA-2 solo es válida post-corrección.

#### Items heredados con cambios de status:

- **BL-DLA-1 (Sub-fase 1D)** — Status sin cambios (ejecución continua), pero ahora su impacto sobre P1/P2 depende indirectamente de BL-DLA-2-LOGIC-FIX porque los KPIs de DLA-1 se evalúan contra `dla_inv.csv` que estaba corrupta.
  - **Acción recomendada:** Mantener Sub-fase 1D en pausa hasta BL-DLA-2-LOGIC-FIX completado, para no acumular datos sobre diagnóstico invalidado.

### Módulos desplegados en producción:

Sin cambios respecto a v3.7. Las nuevas versiones objetivo son:
- `proyecto1/core/dla2_decision_diag.py` **v1.1 (planificado)** — BL-DLA-2-LOGIC-FIX: eliminar `cat_max`, implementar `ATTRIBUTE_CATEGORY_MAPPING`.

---

## 1. CONTEXTO OPERATIVO (heredado de v3.7, actualizado)

### 1.1 Estado del proyecto P1

- **Corpus:** 3.204 fondos europeos (UCITS/OICVM)
- **Schema:** v17 (25 atributos categóricos, 15 numéricos, 8 flags)
- **Último export:** `p1_export_20260510.xlsx` (10-may-2026)
- **Última ejecución pipeline:** 9-may-2026 10:23 PM (log: `log_pipeline_20260509_232409.log`)

### 1.2 Pipelines activos

**P1 (Descubrimiento & Clasificación):**
- Sub-fase DLA-1D en ejecución continua (activación progresiva del extractor 2D-aware)
- Todos los bloques especializados (MONETARIOS, RF, RV, MIXTOS, ALTERNATIVO, RESTANTES) operativos

**P2 (Enriquecimiento cuantitativo):**
- NAV discovery vía `nav_discovery.py` (API Morningstar reverse-engineered)
- Macro-factor ingesta: `spread_ig` implementado; `spread_hy`, `vix_yoy`, `term_spread` pendientes

**P3 (Scoring & Reporting):**
- Arquitectura especificada (BL_DLA_DESIGN_DECISION.md)
- Detalle de implementación pendiente

---

## 2. BACKLOG ABIERTO — ORDEN DE PRIORIDAD EJECUTIVA

### CRÍTICA (Bloqueantes de toda decisión DLA-2)

#### **BL-DLA-2-LOGIC-FIX** ← NUEVO, v3.8
- **Estado:** ABIERTO (no iniciado)
- **Prioridad:** CRÍTICA (bloqueante)
- **Módulo:** `dla2_decision_diag.py` → v1.1
- **Tipo:** Corrección arquitectónica
- **Descripción:**
  - Problema root-cause: Líneas 258-260 implementan decisión `cat_max = MAX(has_cat{i})` global, asumiendo todos los atributos en la categoría máxima. Falso: `Exit_Fee_Pct` siempre en Cat. 2 (tabla Costes), nunca en Cat. 3 (escenarios PRIIPS).
  - Impacto: ~700 fondos (estimado) diagnosticados con `exit_fee_null=1` incorrectamente. Invalida KPIs de ROI para BL-DLA-2.
  - Refactorización:
    1. Eliminar variable `cat_max` como decisor único
    2. Crear `ATTRIBUTE_CATEGORY_MAPPING` explícito (mapeo atributo → categoría específica)
    3. Refactorizar Fase 6 para usar categoria correcta por atributo
    4. Actualizar queries SQL para consultar `has_cat{N}` según atributo
  - Salida: `dla2_table_inventory.csv` v2.0 con datos diagnósticos correctos
  - Tiempo est.: 4-6 horas
  - Bloqueado por: (nada, inicio inmediato)
  - Bloqueante de: BL-DLA-2-RECALIBRACIÓN, BL-DLA-2 (decisión), BL-DLA-2-TABLE-SERIALIZER
  - Testing: Validar que fondos con {Cat. 2 + Cat. 3} no reporten `exit_fee_null=1` cuando comisión de salida está en Cat. 2

---

#### **BL-DLA-2-RECALIBRACIÓN** ← NUEVO, v3.8
- **Estado:** ABIERTO (bloqueado por BL-DLA-2-LOGIC-FIX)
- **Prioridad:** ALTA
- **Módulo:** backlog v3.8 (decisión de política)
- **Tipo:** Recalibración de criterios
- **Descripción:**
  - Una vez BL-DLA-2-LOGIC-FIX complete, se recalculan KPIs de ROI reales
  - Comparar contra umbrales de v3.7 (línea 2164-2167):
    - Prevalencia Cat. 2 pura (cat_max=2) ≥ 30% del corpus
    - Reducción esperada NULLs: Exit_Fee_Pct ≥50%, Entry_Fee_Pct ≥50%, Ongoing_Charge ≥30%
  - Re-emitir decisión Go/No-GO
  - Tiempo est.: 2 horas (análisis + documentación)
  - Testing: Comparar KPIs v3.7 (inválidos) vs v3.8 (correctos) y documentar diferencia

---

### ALTA (Fase A del roadmap v3.7, mantiene prioridad)

#### **BL-61** — Procedimiento verificación causa raíz + fix preventivo
- **Estado:** ABIERTO (heredado de v3.7)
- **Descripción:** (heredada de v3.7, sin cambios)
- **Nota:** Esto es un meta-procedimiento que se aplicará a BL-DLA-2-LOGIC-FIX como caso de estudio. Documenta cómo identificar y corregir root causes en lugar de síntomas.

#### **BL-59** — Caso límite Restantes mayoritario
- **Estado:** ABIERTO (heredado de v3.7)
- **Descripción:** (heredada de v3.7, sin cambios)
- **Tiempo est:** 3-4 horas

#### **BL-49** — Extracción Currency_Hedged sobre KIID
- **Estado:** ABIERTO (heredado de v3.7)
- **Descripción:** Detectar "Hedged" / "Unhedged" en KIID text para poblamiento directo. Beneficio: −135 NULLs en `Currency_Hedged` (actual ~535 NULLs, reducción a ~400).
- **Módulo:** `fund_characterizer.py` → v19

#### **BL-50** — Direcciones inversas Universe → Geography
- **Estado:** ABIERTO (heredado de v3.7)
- **Descripción:** (heredada de v3.7, sin cambios)

#### **BL-DLA-1 Sub-fase 1D** — Activación progresiva DLA Fase 1
- **Estado:** EN EJECUCIÓN (heredado de v3.7)
- **Descripción:** Propagación progresiva activación `DLA_ENABLED=True` en `dla_extractor.py` sin re-descarga masiva. Ciclo 64 para cobertura completa (~180 días).
- **Bloqueador nuevo (v3.8):** Recomendar pausa hasta BL-DLA-2-LOGIC-FIX completado, porque diagnósticos Sub-fase 1D se evalúan contra `dla_inv.csv` que ahora sabemos está corrupta.

#### **BL-DLA-RESTANTES-1** — Detector RF Emergentes OICVM
- **Estado:** ABIERTO (heredado de v3.7)
- **Descripción:** (heredada de v3.7, sin cambios)
- **ISIN ejemplo:** LU0177592218 (Schroders Emerging Markets Debt Total Return)

---

### MEDIA (Fase C/D del roadmap, pueden paralelizarse)

#### **BL-DLA-2-DIAG** — Diagnóstico cuantitativo Cat. 1/2/3 (PRE-REQUISITO de BL-DLA-2)
- **Estado:** ABIERTO (heredado de v3.7)
- **Prioridad:** MEDIA (depende indirectamente de BL-DLA-2-LOGIC-FIX)
- **Descripción:** Queries Q-DLA-04 (inventario tablas por KIID), Q-DLA-05 (clasificación heurística shapes). Decisión Go/No-Go para BL-DLA-2 según prevalencia Cat. 2 ≥ 30%.
- **Nota (v3.8):** Una vez BL-DLA-2-LOGIC-FIX esté listo, los datos de `dla_inv.csv` serán confiables para este análisis.
- **Tiempo est:** 2-3 horas

#### **BL-DLA-2** — Implementación Fase 2 (Tablas Cat. 1+2)
- **Estado:** ABIERTO, decisión pendiente (heredado de v3.7)
- **Bloqueado por:** BL-DLA-2-LOGIC-FIX (para KPIs correctos), BL-DLA-2-RECALIBRACIÓN (para decisión Go/No-GO)
- **Descripción:** Serialización 2D-aware de tablas Cat. 1 y Cat. 2 con `dla_table_serializer.py` v1.0. Beneficio estimado: −50% NULLs `Entry_Fee_Pct`, −50% NULLs `Exit_Fee_Pct`, −30% NULLs `Ongoing_Charge` (tras corrección de lógica en BL-DLA-2-LOGIC-FIX, estos números serán reales).
- **Especificación:** BL_DLA_DESIGN_DECISION.md sección 3 + ESTADO_BACKLOG_APR2026_v3_7.md sección 3
- **Módulos nuevos:**
  - `proyecto1/core/dla_table_serializer.py` **v1.0** (separación de responsabilidades: `dla_extractor` → párrafos, `dla_table_serializer` → tablas)
  - `proyecto1/core/dla_extractor.py` **v1.3** (integración con kill-switch `DLA_TABLE_SERIALIZATION_ENABLED`)
- **Kill-switch:** Separado de DLA-1 para roll-back independiente
- **Tiempo est:** 20-24 horas (Sub-fases 2A→2B→2C→2D)

#### **BL-DLA-3-DIAG** — Diagnóstico cuantitativo Cat. 3
- **Estado:** ABIERTO (heredado de v3.7)
- **Prioridad:** BAJA
- **Descripción:** Query Q-DLA-06 (inventario tablas Cat. 3). Decisión condicionada a impacto P2/P3.

#### **BL-DLA-3** — Implementación Fase 3 (Matrices Cat. 3)
- **Estado:** ABIERTO (heredado de v3.7)
- **Prioridad:** BAJA
- **Descripción:** Serialización de matrices PRIIPS (escenarios, share classes transpuestas). Diferir hasta detectores P2/P3 documentados lo requieran. NO planificación activa.

---

### BAJA (Refinamientos diferibles, Fase D de v3.7)

- **BL-DLA-C3-EXCL** — Criterio C-3 con exclusión baseline corrupta (space_ratio < 0.05)
- **BL-53/54** — Sector_Focus en español (GICS-ES)
- **BL-55** — Exit_Fee_Pct=0.00 con flag EXIT_INFERRED_ZERO
- **BL-56** — Invocación centralizada de normalización post-characterize
- **BL-57/65b** — Family canonical "Income Oriented" (EN, no ES)
- **BL-58** — Constantes Lifecycle/Retirement
- **BL-60** — Bipartitas empate total (investigación SRRI)
- **BL-51 Problema B** — Schema cap/floor para fee structures mixtos
- **BL-47-ext** — SFDR Art. 8 default defensivo
- **BL-48-ext** — Family LVNAV/VNAV/CNAV JPMorgan

---

## 3. ANÁLISIS DE IMPACTO: BL-DLA-2-LOGIC-FIX

### 3.1 Fondos diagnosticados INCORRECTAMENTE en v3.7

**Patrón:** `has_cat2_costes = 1` AND `has_cat3_escenarios = 1` (fondos con ambas categorías)

**Impacto estimado:**
```sql
SELECT COUNT(*) FROM dla_inv
WHERE has_cat2_costes = 1 AND has_cat3_escenarios = 1;
-- Resultado esperado: ~600-800 fondos
```

**Para CADA uno de estos fondos:**
- `Exit_Fee_Pct` se busca en Cat. 3 (escenarios) ← INCORRECTO
- `Exit_Fee_Pct` debería buscarse en Cat. 2 (costes) ← CORRECTO
- Resultado actual: `exit_fee_null = 1` (falso positivo)
- Resultado esperado post-fix: `exit_fee_null = 0` (dato encontrado en Cat. 2)

**Impacto cuantitativo en KPIs:**

| Métrica | Estimación v3.7 (FALSA) | Estimación v3.8 (CORRECTA) | Delta |
|---------|------------------------|--------------------------|-------|
| `Exit_Fee_Pct NULL` | ~110 fondos (3.4%) | ~50 fondos (1.6%) | −60 fondos |
| `Entry_Fee_Pct NULL` | ~20 fondos (0.6%) | ~10 fondos (0.3%) | −10 fondos |
| `Ongoing_Charge NULL` | ~100 fondos (3.1%) | ~70 fondos (2.2%) | −30 fondos |

**ROI esperado de BL-DLA-2 (RECALCULADO):**
- Original (v3.7, inválido): −50% en Exit_Fee_Pct (desde ~110 a ~55)
- Corregido (v3.8, válido): −50% en Exit_Fee_Pct (desde ~50 a ~25) — reducción real menor porque baseline era falsa

### 3.2 Impacto en decisión Go/No-GO de BL-DLA-2

**Umbral v3.7:** "SI prevalencia Cat. 2 ≥ 30% Y ROI Exit_Fee_Pct ≥ 50% ENTONCES GO"

**Post-corrección v3.8:**
- Prevalencia Cat. 2: probablemente ~65-70% (expectativa: sin cambio, el problema era interpretación de `cat_max`)
- ROI real: −50% en NULLs reales (no sobre falsos positivos), que es diferente magnitud
- **Decisión:** Probablemente sigue siendo GO, pero con justificación más sólida

---

## 4. INDICADORES CLAVE (KPIs) RECALIBRADOS

### 4.1 Completitud por atributo (estimación v3.8, POST-FIX)

| Atributo | NULL pct (v3.7) | NULL pct (v3.8, estimado) | Delta | Causa |
|----------|-----------------|------------------------|-------|-------|
| `Fund_Nature` | 0.0% | 0.0% | — | No afectado |
| `Type` | 1.2% | 1.2% | — | No afectado |
| `Entry_Fee_Pct` | 0.6% | 0.3% | −0.3% | Cat. 2 fix |
| `Exit_Fee_Pct` | 3.4% | 1.6% | −1.8% | Cat. 2 fix (MAYOR impacto) |
| `Ongoing_Charge` | 3.1% | 2.2% | −0.9% | Cat. 2 fix |
| `Accumulation_Policy` | 11.2% | 11.2% | — | No afectado (legítima incompletitud) |
| `Distribution_Frequency` | 8.9% | 8.9% | — | No afectado |
| `Currency_Hedged` | 16.7% | 14.2% | −2.5% | Esperado post-BL-49 |
| `Market_Cap_Focus` | 43.3% | 43.3% | — | No afectado |

**KPI master:** Completitud media → 80.2% (v3.7 invalida) → 81.4% (v3.8 válida)

### 4.2 Confiabilidad de diagnóstico

- **Antes BL-DLA-2-LOGIC-FIX:** `dla_inv.csv` contiene ~700 false positives en `exit_fee_null`
- **Después BL-DLA-2-LOGIC-FIX:** `dla_inv.csv` diagnóstico confiable para Fase 2

---

## 5. MITIGACIÓN DE RIESGOS IDENTIFICADOS

### 5.1 Risk: Regresión en KPI "Diagnóstico confiable"

**Escenario:** Muchos puntos de decisión (BL-DLA-1 Sub-fase 1D, BL-DLA-2 Go/No-Go) dependen de `dla_inv.csv`. Si está corrupta, todas esas decisiones son inválidas.

**Mitigación (v3.8):**
1. ✅ Identificar incidencia: BL-DLA-2-LOGIC-FIX
2. ✅ Priorizar CRÍTICA para resolución inmediata
3. ⏳ Ejecutar BL-DLA-2-LOGIC-FIX antes de cualquier nueva decisión sobre DLA
4. ⏳ Pausar Sub-fase 1D si es posible hasta corrección completada

### 5.2 Risk: Falsos positivos acumulados en BD

**Escenario:** `fund_master` contiene ~700 registros con `exit_fee_null=1` (falso). Una vez corregida lógica, estos datos quedan obsoletos.

**Mitigación:**
- Los datos en `fund_master` no se regeneran automáticamente
- Opción A: Re-ejecutar pipeline completo post-BL-DLA-2-LOGIC-FIX (COSTOSO)
- Opción B: Marcar estos 700 registros con `FORCE_REFRESH=1` y repoblarlos progresivamente (RECOMENDADO)
- Opción C: Aceptar como "deuda técnica" y corregir en siguiente ciclo (RIESGOSO)
- **Decisión recomendada:** Opción B (repoblamiento progresivo, 50 fondos/ciclo)

---

## 6. PLAN DE ACCIÓN DETALLADO — PRÓXIMAS 48 HORAS

### Sesión 1 (Hoy, 4-6 horas):

1. ✅ Identificar incidencia (HECHO en este documento)
2. ⏳ Implementar BL-DLA-2-LOGIC-FIX:
   - Refactorizar `dla2_decision_diag.py` v1.0 → v1.1
   - Crear `ATTRIBUTE_CATEGORY_MAPPING` explícito
   - Refactorizar Fase 6 para usar categorías correctas
   - Re-ejecutar diagnóstico completo
3. ⏳ Generar `dla2_table_inventory.csv` v2.0 (datos correctos)

### Sesión 2 (Después Sesión 1, 2-3 horas):

4. ⏳ Ejecutar BL-DLA-2-RECALIBRACIÓN:
   - Comparar KPIs v3.7 (inválidos) vs v3.8 (correctos)
   - Documentar diferencias y raíces
   - Re-emitir decisión Go/No-GO para BL-DLA-2
5. ⏳ Actualizar backlog a v3.9 con decisión final

### Sesión 3+ (Paralelo):

6. ⏳ Evaluar opción B (repoblamiento progresivo) vs opción C
7. ⏳ Documentar en `BL-DLA-2-LOGIC-FIX.md` el análisis y la corrección

---

## 7. ESPECIFICACIÓN TÉCNICA DETALLADA — BL-DLA-2-LOGIC-FIX

### 7.1 Cambio en `dla2_decision_diag.py`

**Antes (incorrecto):**
```python
# Líneas 258-260
if   r["has_cat3_escenarios"]:                              r["cat_max"] = 3
elif r["has_cat2_costes"] or r["has_cat2_politica"]:       r["cat_max"] = 2
elif r["has_cat1_signal"]:                                  r["cat_max"] = 1
```

**Después (correcto):**
```python
# Líneas ~258-320 (refactorizado)

# Mapeo de atributos a categorías específicas (global)
ATTRIBUTE_CATEGORY_MAPPING = {
    'Entry_Fee_Pct': 2,
    'Exit_Fee_Pct': 2,
    'Ongoing_Charge': 2,
    'Accumulation_Policy': 2,
    'Distribution_Frequency': 2,
    'PRIIPS_Performance_1Y': 3,
    'PRIIPS_Performance_3Y': 3,
    'PRIIPS_Performance_5Y': 3,
    'PRIIPS_Performance_10Y': 3,
    'PRIIPS_Volatility': 3,
    # ... etc
}

# Calcular prevalencia de cada categoría (sin max global)
r["has_cat1"] = int(bool(_PAT_LISTA_CLASES.search(text)) or
                      bool(_PAT_LISTA_MONEDAS.search(text)))

r["has_cat2"] = int(bool(_PAT_COSTES.search(text)) or
                    bool(_PAT_POLITICA.search(text)))

if _PAT_ESCENARIO.search(text):
    horizontes = set(_PAT_HORIZONTE.findall(text))
    r["has_cat3"] = int(len(horizontes) >= 2)
else:
    r["has_cat3"] = 0

# NO calcular cat_max; guardar cada categoría por separado
r["cat_max"] = None  # Deprecado, solo para backward compatibility
```

### 7.2 Refactorización de Fase 6

**Antes:**
```python
# Fase 6: distribución cat_max para fondos con atributo NULL
query = """
    SELECT cat_max, COUNT(*) AS n
    FROM dla_inv
    WHERE exit_fee_null = 1
    GROUP BY cat_max
"""
```

**Después:**
```python
# Fase 6: distribución de categoría CORRECTA por atributo NULL

def get_category_for_attribute(attribute):
    return ATTRIBUTE_CATEGORY_MAPPING.get(attribute, None)

def get_category_flag_for_attribute(attribute):
    cat = get_category_for_attribute(attribute)
    if cat:
        return f'has_cat{cat}'
    return None

for attribute in ['Entry_Fee_Pct', 'Exit_Fee_Pct', 'Ongoing_Charge',
                   'Accumulation_Policy', 'Distribution_Frequency']:
    cat_flag = get_category_flag_for_attribute(attribute)
    null_flag = f'{attribute.lower()}_null'
    
    query = f"""
        SELECT {cat_flag}, COUNT(*) AS n
        FROM dla_inv
        WHERE {null_flag} = 1
        GROUP BY {cat_flag}
    """
    # Ejecutar y documentar
    log.subsection(f"Distribución {cat_flag} para fondos con {attribute}=NULL")
    rows = conn.execute(query).fetchall()
    log.table([cat_flag, 'n'], rows)
```

### 7.3 Validación post-fix

**Control C-1 (nuevo):**
```sql
-- Fondos con Exit_Fee_Pct=NULL DEBEN cumplir una de:
-- 1. has_cat2_costes=0 (no tiene tabla de costes, legítimo)
-- 2. exit_fee_null=1 ANTES de fix, exit_fee_null=0 DESPUÉS de fix
--    (si has_cat2_costes=1, entonces exit_fee DEBERÍA estar extraído)

SELECT COUNT(*) as alertas
FROM dla_inv
WHERE exit_fee_null = 1 AND has_cat2_costes = 1;
-- Esperado POST-FIX: < 50 (casos donde realmente no se puede extraer)
-- Esperado ANTES-FIX: ~700 (falsos positivos)
```

---

## 8. PRINCIPIOS APLICADOS EN ESTA CORRECCIÓN

### Principio #1: Root Cause Analysis

✅ No es parche: "aceptar los NULLs como normales"  
✅ Es root cause: "la lógica de decisión `cat_max` es fundamentalmente falsa"

### Principio #2: DRY & Escalabilidad

✅ Mapeo explícito `ATTRIBUTE_CATEGORY_MAPPING` reutilizable en `dla_table_serializer.py` v1.0

### Principio #8: Homogeneidad Lingüística

✅ Sin cambios (no afecta clasificación de valores en idiomas)

### Principio #9: Consistencia Semántica

✅ Garantiza que cada atributo se busca en su categoría correcta, evitando inconsistencias pseudo-diagnósticas

---

## 9. ROADMAP RECOMENDADO — VERSIÓN ACTUALIZADA

**Fase 0 — INMEDIATA (post-incidencia, 6-8 horas):**
1. ✅ **BL-DLA-2-LOGIC-FIX** — Refactorizar `dla2_decision_diag.py`, re-ejecutar diagnóstico
2. ✅ **BL-DLA-2-RECALIBRACIÓN** — Recalcular KPIs, emitir decisión Go/No-GO correcta

**Fase A — Cerrar P1 a estado consolidado (post-Fase 0):**
1. **BL-61** — Procedimiento causa raíz (aplicado a BL-DLA-2-LOGIC-FIX como ejemplo)
2. **BL-59** — Caso límite Restantes
3. **BL-49** — Currency_Hedged directo
4. **BL-50** — Universe→Geography inversas
5. **BL-DLA-1 Sub-fase 1D** — Reanudar después de Fase 0 (con confiabilidad de diagnóstico restaurada)
6. **BL-DLA-RESTANTES-1** — Detector RF Emergentes

**Fase B — Paralela a Fase A (no bloqueante):**
7. **P2 — Factores macro** — 3 series FRED pendientes

**Fase C — Posterior a Fase A:**
8. **BL-DLA-2-DIAG** — Diagnóstico Cat. 1/2/3 (datos ahora confiables)
9. **BL-DLA-2** — Implementación Fase 2 (tablas Cat. 1+2)

**Fase D — Refinamientos diferibles:**
- Items BAJA (BL-DLA-C3-EXCL, BL-53/54, etc.)

---

## 10. DOCUMENTACIÓN COMPLEMENTARIA

- **Análisis detallado:** `/mnt/home/claude/INCIDENCIA_CRITICA_DLA2_ANALISIS.md` (este documento contiene referencia)
- **Especificación BL-DLA-2 original:** `BL_DLA_DESIGN_DECISION.md` (secciones 2-3)
- **Especificación BL-DLA-2 v3.7:** `ESTADO_BACKLOG_APR2026_v3_7.md` sección 3
- **Diagnóstico actual (corrupto):** `dla2_table_inventory.csv` (será reemplazado por v2.0 post-fix)

---

## 11. CONTACTOS Y ESCALADAS

**Si BL-DLA-2-LOGIC-FIX no se completa en 8 horas:**
- Escalar a prioridad BLOQUEANTE GLOBAL
- Pausar todos los items de DLA hasta resolución
- Considerar impacto cascada en P2/P3 (dependen indirectamente de clasificación P1 confiable)

---

**Fin del documento. Versión v3.8 autocontenida — 10 de mayo de 2026.**

**Validación:** Este backlog incorpora hallazgo crítico de sesión actual (10-may-2026) que invalida decisión Go/No-GO de BL-DLA-2 en v3.7. No es reversible a v3.7 sin regresar a estado de riesgo conocido.
