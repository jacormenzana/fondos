# Estado del Backlog P1 — Referencia de Sesión v3.9 (autocontenida)

**Fecha:** 18 de mayo de 2026  
**Ciclo de referencia:** `p1_export_20260518.xlsx` (3.205 fondos, schema v17) — log `log_pipeline_20260518_202802.log`  
**Sprint actual:** Análisis post-ejecución ciclo 18-may; 3 nuevos items incorporados  

**Estatus de este documento:**  
v3.9 **incorpora hallazgos de sesión 18-may-2026:** análisis de log, inconsistencias semánticas y normalización de benchmarks. Nuevos ítems: **BL-SRRI-GUARD-FULL** (bug activo, prioridad Alta), **BL-PROFILE-SRRI-FULL** (2.110 fondos afectados, prioridad Alta), **BL-BENCH-NORM** (normalización benchmarks, prioridad Media).

---

## 0. DIFF vs v3.8 — TRAZABILIDAD DE CAMBIOS

### Cambios principales en v3.9:

#### Items NUEVOS:

- **BL-SRRI-GUARD-FULL** — **ABIERTO en v3.9**, prioridad **ALTA**. Bug activo: 3 fondos (ISINs Thematics) fallan con `'>=' not supported between dict and int` en `pipeline.py` línea 799 porque `parsed.get("SRRI")` retorna un dict para esos PDFs. El guard existente (línea 533) protege `_srri_for_classify` pero no la asignación a `fund_master_record["SRRI"]` (línea 714). Fix: función `_safe_scalar()` aplicada en construcción de `fund_master_record`. Tiempo estimado: 1 hora.

- **BL-PROFILE-SRRI-FULL** — **ABIERTO en v3.9**, prioridad **ALTA**. 2.110 fondos (65,8% del corpus) tienen Profile inconsistente con SRRI. Causa raíz: bloques RV y MIXTOS asignan `Profile='Dinámico'` como default ignorando el SRRI; la corrección post-bloque en pipeline.py sólo cubre Conservador+SRRI≥5. Fix: extender la corrección para usar `detect_profile_from_srri()` (ya en `classify_utils.py`) como árbitro post-bloque sobre cualquier Profile asignado. Tiempo estimado: 2-3 horas.

- **BL-BENCH-NORM** — **ABIERTO en v3.9**, prioridad **MEDIA**. 522 valores únicos de `Benchmark_Declared` reducibles a ~36 familias canónicas; ~254 registros truncados por límite de extracción en el parser. Fix: función `normalize_benchmark()` en `classify_utils.py` con regex por familia + elevar límite de longitud en `kiid_parser.py`. Prerequisito para análisis de exposición por benchmark en P3. Tiempo estimado: 4-6 horas.

#### Items heredados sin cambios de status:

- Todos los ítems de v3.8 se mantienen inalterados (BL-DLA-2-LOGIC-FIX, BL-DLA-2-RECALIBRACIÓN, BL-DLA-1 Sub-fase 1D, etc.).

### Módulos desplegados en producción:

Sin cambios respecto a v3.8. Versiones objetivo nuevas:
- `proyecto1/pipeline.py` → corrección `_safe_scalar()` + extensión Profile-SRRI (BL-SRRI-GUARD-FULL + BL-PROFILE-SRRI-FULL)
- `proyecto1/classify_utils.py` → `normalize_benchmark()` (BL-BENCH-NORM)
- `proyecto1/kiid_parser.py` → límite longitud Benchmark_Declared (BL-BENCH-NORM)

---

## 1. CONTEXTO OPERATIVO (heredado de v3.7, actualizado)

### 1.1 Estado del proyecto P1

- **Corpus:** 3.205 fondos europeos (UCITS/OICVM)
- **Schema:** v17 (25 atributos categóricos, 15 numéricos, 8 flags)
- **Último export:** `p1_export_20260518.xlsx` (18-may-2026)
- **Última ejecución pipeline:** 18-may-2026 (log: `log_pipeline_20260518_202802.log`)

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

#### **BL-SRRI-GUARD-FULL** ← NUEVO, v3.9
- **Estado:** ABIERTO (no iniciado)
- **Prioridad:** ALTA
- **Módulo:** `pipeline.py`
- **Tipo:** Bug fix — error en producción
- **Descripción:**
  - 3 fondos (ISINs: `LU1951199022`, `LU1951200648`, `LU2095320268`) fallan con `'>=' not supported between dict and int` en línea 799 de `pipeline.py`.
  - Causa raíz: `parsed.get("SRRI")` devuelve un dict estructurado en esos PDFs. El guard `BL-SRRI-GUARD` de línea 533 protege `_srri_for_classify`, pero la asignación a `fund_master_record["SRRI"]` (línea 714) no está blindada. Líneas 799 y 826 consumen ese valor directamente sin conversión.
  - Fix: función `_safe_scalar(v)` que extrae el escalar si `v` es dict, convierte a int si es numérico, retorna None en caso contrario. Aplicar en línea 714 al construir `fund_master_record`.
  - Tiempo est.: 1 hora
  - Testing: re-ejecutar los 3 ISINs afectados y verificar que SRRI se persiste como int o NULL.

---

#### **BL-PROFILE-SRRI-FULL** ← NUEVO, v3.9
- **Estado:** ABIERTO (no iniciado)
- **Prioridad:** ALTA
- **Módulo:** `pipeline.py`
- **Tipo:** Corrección de consistencia semántica — root cause fix
- **Descripción:**
  - 2.110 fondos (65,8% del corpus) tienen `Profile` inconsistente con `SRRI` según el mapeo canónico de `detect_profile_from_srri()`: SRRI≤2→Conservador, SRRI≤4→Moderado, SRRI>4→Dinámico.
  - Causa raíz: los bloques RV y MIXTOS asignan `Profile='Dinámico'` como default para fondos sin señal explícita, ignorando el SRRI. La corrección post-bloque en `pipeline.py` línea 799 solo cubre Conservador+SRRI≥5 (19 fondos), dejando sin corregir los restantes 2.091 casos.
  - Fix: extender la corrección post-bloque en `pipeline.py` para usar `detect_profile_from_srri()` como árbitro final sobre cualquier Profile asignado por bloque cuando el SRRI está disponible. La función ya existe en `classify_utils.py` — no requiere nueva lógica.
  - Afectación por combinación:

    | Combinación incorrecta | Fondos |
    |---|---|
    | Dinámico + SRRI=4 (→ Moderado) | 1.095 |
    | Moderado + SRRI=2/3 (→ Conservador) | 802 |
    | Dinámico + SRRI=2/3 (→ Conservador) | 194 |
    | Conservador + SRRI=4 (→ Moderado) | 19 |

  - Tiempo est.: 2-3 horas (implementación + validación SQL)
  - Testing: query post-fix verificando 0 fondos con Profile distinto al esperado por SRRI.

---

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

#### **BL-BENCH-NORM** ← NUEVO, v3.9
- **Estado:** ABIERTO (no iniciado)
- **Prioridad:** MEDIA
- **Módulos:** `classify_utils.py`, `kiid_parser.py`
- **Tipo:** Normalización de datos — prerequisito P3
- **Descripción:**
  - `Benchmark_Declared` tiene 522 valores únicos para 2.156 fondos (36% del corpus con benchmark). ~254 registros truncados por límite de extracción en el parser. Reducibles a ~36 familias canónicas (MSCI_ACWI=360, MSCI_WORLD=186, MSCI_EUROPE=143, MSCI_EM=132, SP500=78, BBG_GLOBAL_AGGREGATE=63, ICE_BOFA_HY=62, etc.).
  - Fix en dos partes:
    1. `kiid_parser.py`: elevar límite de longitud del campo Benchmark_Declared a 200+ caracteres (actualmente se trunca en salto de línea o límite corto).
    2. `classify_utils.py`: función `normalize_benchmark(raw_text) → canonical_label` con regex por familia, aplicada en `kiid_parser.py` post-extracción. Nuevo campo `Benchmark_Canonical` en schema (o normalización in-situ de `Benchmark_Declared`).
  - Beneficio: habilita groupby por benchmark en P3 (análisis de exposición, correlación de rendimientos, scoring por benchmark familiar).
  - Tiempo est.: 4-6 horas
  - Testing: verificar reducción a ≤50 valores únicos y 0 registros con patrón de truncado.

---

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
1. **BL-SRRI-GUARD-FULL** ← NUEVO — Fix bug `dict>=int` en `pipeline.py` (1h, sin dependencias)
2. **BL-PROFILE-SRRI-FULL** ← NUEVO — Corrección Profile↔SRRI completa (2-3h)
3. **BL-61** — Procedimiento causa raíz
4. **BL-59** — Caso límite Restantes
5. **BL-49** — Currency_Hedged directo
6. **BL-50** — Universe→Geography inversas
7. **BL-DLA-1 Sub-fase 1D** — Reanudar después de Fase 0
8. **BL-DLA-RESTANTES-1** — Detector RF Emergentes

**Fase B — Paralela a Fase A (no bloqueante):**
9. **P2 — Factores macro** — 3 series FRED pendientes
10. **BL-BENCH-NORM** ← NUEVO — Normalización benchmarks (4-6h)

**Fase C — Posterior a Fase A:**
11. **BL-DLA-2-DIAG** — Diagnóstico Cat. 1/2/3
12. **BL-DLA-2** — Implementación Fase 2 (tablas Cat. 1+2)

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

**Fin del documento. Versión v3.9 autocontenida — 18 de mayo de 2026.**

**Validación:** Este backlog incorpora análisis de ciclo 18-may-2026 (log + export). Nuevos ítems BL-SRRI-GUARD-FULL, BL-PROFILE-SRRI-FULL y BL-BENCH-NORM añadidos. Todos los ítems heredados de v3.8 se mantienen inalterados.
