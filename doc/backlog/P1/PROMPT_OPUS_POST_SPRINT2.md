# PROMPT_OPUS_POST_SPRINT2.md
# Prompt para sesión Opus — Diagnóstico y diseño BL-COST Sprint 2 Post-Ejecución
# Uso: pegar íntegramente en nueva conversación Opus antes de cualquier pregunta.
# Generado por: sesión Sonnet S2-D (post-pipeline, 2026-05-23)

---

## ROL Y OBJETIVO DE ESTA SESIÓN

Eres el arquitecto del sistema de extracción de costes de fondos de inversión europeos.
Esta sesión produce un único entregable: el documento de traspaso
`TRASPASO_CONTEXTO_BL_COST_POST_SPRINT2.md`, que especifica con precisión suficiente
para implementación directa (sin ambigüedad) los fixes y mejoras necesarios tras
la ejecución completa del pipeline con el Sprint 2 activo.

**No se escribe código en esta sesión.** Solo diagnóstico, decisiones y especificación.
Los documentos resultantes los implementarán sesiones Sonnet posteriores.

---

## 1. ESTADO DEL SISTEMA AL CIERRE DEL SPRINT 2

### 1.1 Módulos desplegados y operativos

| Módulo | Versión | Estado |
|---|---|---|
| `shared/config.py` | v19.2 | ✅ `PRIIPS_COST_EXTRACTION_ENABLED=True` |
| `proyecto1/core/pipeline.py` | v38 | ✅ Bloque BL-COST-4c integrado |
| `proyecto1/core/sqlite_writer.py` | v25 | ✅ `publish_fund` con `cost_schedule_rows` |
| `proyecto1/core/priips_cost_extractor.py` | S2-B | ✅ Extractor PRIIPs completo |
| `proyecto1/core/ucits_cost_extractor.py` | S2-C | ✅ Extractor UCITS mínimo |
| `proyecto1/core/cost_format_router.py` | S2-D fix | ✅ `\s*` en señales ES |
| `proyecto1/core/cost_table_parser.py` | S2-D fix | ✅ Guard rango + `\s*` en patrones |

### 1.2 Schema BD activo

```sql
-- fund_master: 11 columnas de coste (Sprint 2)
KID_Format              TEXT   -- 'PRIIPS_KID' | 'UCITS_KIID' | 'UNKNOWN'
KID_Currency            TEXT
Cost_Extraction_Quality TEXT   -- 'HIGH'|'MEDIUM_CROSS'|'MEDIUM_EUR'|'MEDIUM_PCT'|'LOW'|'NONE'
Cost_RHP_Years          REAL   CHECK (IS NULL OR (> 0 AND <= 50))
Entry_Fee_Pct_Max       REAL   CHECK (IS NULL OR (>= 0 AND <= 25))
Exit_Fee_Pct_Max        REAL   CHECK (IS NULL OR (>= 0 AND <= 25))
Management_Fee_Pct      REAL   CHECK (IS NULL OR (>= 0 AND <= 10))
Transaction_Cost_Pct    REAL   CHECK (IS NULL OR (>= 0 AND <= 5))
Performance_Fee_Pct     REAL   CHECK (IS NULL OR (>= 0 AND <= 30))
ACI_1Y                  REAL   CHECK (IS NULL OR (>= 0 AND <= 50))
ACI_RHP                 REAL   CHECK (IS NULL OR (>= 0 AND <= 25))
-- Columna legacy con problema de escala:
Ongoing_Charge_Recurrent REAL  -- valores legacy en ratio decimal (0.005); nuevos en % entero (0.5)

-- fund_cost_schedule
PRIMARY KEY (ISIN, Horizon_Years)
CHECK Source IN ('PRIIPS_COSTS_OVER_TIME','UCITS_DERIVED','MANUAL')
```

### 1.3 Arquitectura del bloque BL-COST-4c en pipeline.py

```python
# Posición: tras Geography, antes de publish_fund
# 1. Añade proyecto1/core/ a sys.path (fix S2-D)
# 2. Lee _ceq_bd, _oc_bd, _entry_bd, _exit_bd desde BD (SELECT dedicado)
# 3. Skip si _ceq_bd == 'HIGH'
# 4. detect_kid_format(kiid_text) → routing PRIIPs/UCITS
# 5. extract_priips_costs() o extract_ucits_costs()
# 6. Mezcla campos en fund_master_record (11 campos _COST_FIELDS)
# 7. publish_fund(..., cost_schedule_rows=_schedule_rows)
```

### 1.4 Política de escala confirmada

- **Campos Sprint 2** (`Management_Fee_Pct`, `ACI_1Y`, `ACI_RHP`, etc.): **% entero** (0.85 para 0.85%)
- **Campo legacy** (`Ongoing_Charge_Recurrent`): **ratio decimal** (0.0085 para 0.85%) — valores pre-Sprint 2
- Los extractores convierten con `_ratio_to_pct()`: ratio decimal → % entero
- La mezcla de escalas en `Ongoing_Charge_Recurrent` es el problema BL-COST-5

---

## 2. RESULTADOS DEL PIPELINE COMPLETO (20260523_223928)

### 2.1 Métricas de ejecución

- **Fondos procesados:** 3.205
- **Duración:** 21 minutos (22:39 → 23:01)
- **Bloques:** MONETARIOS(30) · RF_CORTO(14) · RF_FLEXIBLE(28) · RENTA_VARIABLE(707) · MIXTOS · ALTERNATIVO · ESTRUCTURADO · RESTANTES(2.322)
- **Errores PUBLISH_FUND:** 6 (todos por CHECK constraint)
- **Errores RESTANTES_PROCESS:** 9

### 2.2 Q1 — Distribución Cost_Extraction_Quality

| Quality | n | pct | Objetivo | Estado |
|---|---|---|---|---|
| **LOW** | **1.605** | **50.1%** | 200-500 | ❌ Crítico |
| MEDIUM_EUR | 597 | 18.6% | 800-1200 | ❌ Bajo |
| HIGH | 587 | 18.3% | 500-800 | ✅ |
| NONE | 347 | 10.8% | 600-900 | ✅ |
| NULL | 38 | 1.2% | ~0 | ⚠️ |
| MEDIUM_CROSS | 28 | 0.9% | 100-300 | ❌ Bajo |
| MEDIUM_PCT | 3 | 0.1% | 200-400 | ❌ Muy bajo |

**HIGH + MEDIUM_* = 37.9%** — objetivo era >70%. La anomalía principal es el 50.1% en LOW.

### 2.3 Q2 — fund_cost_schedule

- Fondos con schedule: **1.522**
- Coherente con HIGH + MEDIUM_* (1.215 fondos)

### 2.3 Q3 — KID_Format

| Format | n |
|---|---|
| PRIIPS_KID | 2.798 |
| UCITS_KIID | 5 |
| NULL (sin KIID) | 402 |

### 2.4 Q4 — Mismatches OC/ACI (BL-COST-5)

**6 ISINs únicos** con `Ongoing_Charge_Recurrent` en BD que parece ACI@RHP:
`LU1840769696`, `LU1193126809`, `LU0871827464`, `LU0173778175`, `LU0289472085`, `LU0323456896`

(El log muestra 13 entradas por re-ejecuciones previas de ciclos de prueba — los únicos son 6.)

### 2.5 Q5 — Escala de valores CRÍTICO

```
campo                    min_val    max_val    avg_val     n_poblado
Management_Fee_Pct       0          3.3        1.322       1.047     ← % entero ✅
Ongoing_Charge_Recurrent 0.0001     2.7        0.0181      3.180     ← ratio decimal ❌ MEZCLA
ACI_RHP                  0.05       8.99       5.167       650       ← % entero ✅
ACI_1Y                   0.03       12.85      3.796       846       ← % entero ✅
```

`Ongoing_Charge_Recurrent` AVG=0.018 confirma que los 3.180 valores son legacy en escala ratio.
Los valores nuevos del Sprint 2 (cuando `existing_oc is None`) estarían en % entero —
pero el COALESCE-safe hace que solo se escriban para fondos sin valor previo (~0 fondos,
ya que casi todos tienen `Ongoing_Charge_Recurrent` legacy).

### 2.6 Q6 — Schedule: avg 1.5 filas/fondo

```
Source                  total_filas  fondos  avg_filas_por_fondo
PRIIPS_COSTS_OVER_TIME  1.515        1.515   1.5
UCITS_DERIVED           7            7       1.0
```

Esperábamos 2-3 filas por fondo PRIIPs (1Y + RHP + opcional). El 1.5 indica que
muchos fondos solo generan 1 fila → el parser `parse_costs_over_time` no encuentra
la tabla completa en la mayoría de fondos.

### 2.7 Q7 — Coherencia OC vs ACI_1Y (fondos HIGH)

20 fondos HIGH tienen `OC` en rango 0.005–0.04 (ratio decimal legacy) y `ACI_1Y`
en rango 7.3–7.9 (% entero correcto). `diff_OC_ACI1Y` ~7.5pp en todos.
Confirma: son los mismos fondos del problema BL-COST-5 — `Ongoing_Charge_Recurrent`
legacy en ratio, `ACI_1Y` nuevo en % entero.

### 2.8 Q9 — 402 fondos sin KID_Format

Fondos sin KIID disponible (DOWNLOAD_ERROR). El pipeline hace `continue` — correcto.

### 2.9 Q10 — 306 fondos con 0 filas Is_RHP=1

De 1.522 fondos con schedule:
- 1.216 fondos con exactamente 1 fila RHP ✅
- 306 fondos (20%) con 0 filas RHP ⚠️

El parser encontró filas de costes pero no identificó cuál es el RHP.

### 2.10 Errores CHECK constraint en pipeline

```
LU2809794220: CHECK constraint failed: ACI_RHP IS NULL OR (ACI_RHP >= 0 AND ACI_RHP <= 25)
IE00BYW5Q247: CHECK constraint failed: ACI_RHP IS NULL OR (ACI_RHP >= 0 AND ACI_RHP <= 25)
```

El extractor produce `ACI_RHP > 25` para estos fondos. Posibles causas:
- Fondos con RHP largo (6+ años) donde ACI acumulado supera 25%
- Error de extracción (valor EUR interpretado como %)

---

## 3. DIAGNÓSTICOS TÉCNICOS DE S2-D (ya confirmados)

### 3.1 Causa raíz de LOW quality al 50%

**Hipótesis confirmada parcialmente:** el problema del texto pegado (`\s*` fix) resolvió
4 fondos en el smoke test. Pero 1.605 fondos en LOW sugiere que hay causas adicionales:

**Causa probable principal:** `parse_costs_over_time` no encuentra la tabla "Costes a lo
largo del tiempo" en la mayoría de los fondos. Los fondos LOW tienen `KID_Format=PRIIPS_KID`
(el router los detecta como PRIIPs) pero la tabla de costes no se parsea. Esto puede ser:

1. **Texto pegado más extendido** — el `\s*` fix ayuda pero no cubre todos los casos
2. **Variantes textuales no cubiertas** — "costes durante el período" en lugar de
   "costes a lo largo del tiempo"
3. **Tablas en formato imagen** dentro de PDFs PRIIPs (similar a LU0256839274)
4. **DLA2_Table_Text no disponible** para esos fondos — el texto raw no tiene la tabla
   y el DLA2 tampoco la serializa si no fue detectada

**Acción requerida de Opus:** definir estrategia de diagnóstico y fixes priorizados.

### 3.2 Causa raíz del problema de escala en Ongoing_Charge_Recurrent

**Confirmado:** los 3.180 valores en BD son legacy del pipeline pre-Sprint 2, extraídos
en escala ratio decimal. El Sprint 2 no los puede sobrescribir por la política COALESCE-safe.

**Impacto en BL-COST-5:** la corrección de los ~6 fondos mismatch OC/ACI es trivial.
El problema mayor es la normalización de escala de los 3.180 valores legacy — no es
un mismatch, es una inconsistencia de escala global que afecta a P2 y P3.

**Pregunta abierta para Opus:** ¿BL-COST-5 debe incluir también la normalización de
escala de `Ongoing_Charge_Recurrent` para los 3.180 fondos? ¿O es una tarea separada?
¿El valor correcto es `valor_legacy * 100`?

### 3.3 Causa raíz de 306 fondos sin fila RHP

`parse_costs_over_time` devuelve filas con `is_rhp=False` para todos los horizontes
cuando no puede identificar el período recomendado. `horizon_years=-1.0` se usa como
señal interna pero el CHECK constraint de `fund_cost_schedule` no acepta `-1.0`,
por lo que esas filas se descartan en `upsert_cost_schedule`.

### 3.4 Causa raíz de ACI_RHP > 25 en 2 fondos

Los CHECK constraints del schema permiten `ACI_RHP <= 25`. Para fondos con RHP de 6
años y costes del 4-5% anual, el ACI acumulado puede superar 25% legítimamente
(ej: 4.5% × 6 años = 27%). El CHECK constraint es demasiado restrictivo.

---

## 4. ISSUES PRIORIZADOS PARA DIAGNÓSTICO Y DISEÑO

### ISSUE-1 [CRÍTICO]: LOW quality al 50% — 1.605 fondos

**Impacto:** la mitad del corpus no tiene datos de costes utilizables para P2/P3.
**Objetivo:** reducir LOW a <20% (de 50% a <20%).
**Estrategia de diagnóstico requerida:**
- Muestra de 10-20 fondos LOW: ¿tienen tabla de costes en su texto?
- ¿El DLA2_Table_Text aporta datos que el raw text no tiene?
- ¿Qué variantes textuales no están cubiertas por `COSTS_OVER_TIME_HEADER`?
- ¿Cuántos fondos LOW son escaneados (solo imagen)?

**Módulos afectados:** `cost_table_parser.py`, `cost_format_router.py`

### ISSUE-2 [CRÍTICO]: Ongoing_Charge_Recurrent en escala ratio — 3.180 fondos

**Impacto:** P2 (macro sensitivities) y P3 (scoring) usarían 0.018 como TER en lugar
de 1.8% — error de factor 100x en todos los cálculos de coste neto.
**Opciones a evaluar por Opus:**
- A) Script de normalización masiva: `UPDATE fund_master SET Ongoing_Charge_Recurrent = Ongoing_Charge_Recurrent * 100 WHERE Ongoing_Charge_Recurrent < 0.1`
- B) Verificar caso a caso antes de multiplicar (riesgo: algún fondo puede ya estar
  en % entero si se escribió en un ciclo de prueba)
- C) Crear columna nueva `Ongoing_Charge_Pct` en % entero, mantener legacy para
  auditabilidad

**Restricción:** la opción A es simple pero necesita validación antes — el criterio
`< 0.1` puede incluir fondos con TER real muy bajo (ej: ETFs con 0.07%).

### ISSUE-3 [IMPORTANTE]: 306 fondos sin fila Is_RHP=1

**Causa confirmada:** `horizon_years=-1.0` descartado por CHECK constraint.
**Fix requerido:** en `priips_cost_extractor.py`, cuando `is_rhp=True`, resolver
`horizon_years` desde `Cost_RHP_Years` del fondo antes de construir la fila schedule.
**Módulos afectados:** `priips_cost_extractor.py`

### ISSUE-4 [IMPORTANTE]: CHECK constraint ACI_RHP <= 25 demasiado restrictivo

**Fondos afectados confirmados:** LU2809794220, IE00BYW5Q247
**Causa:** fondos con RHP largo (≥6 años) y costes >4% anual → ACI_RHP legítimamente >25%
**Opciones:**
- A) Ampliar CHECK a `<= 50` (coherente con ACI_1Y que ya permite <= 50)
- B) Mantener <= 25 y añadir lógica de truncado + WARNING en el extractor
**Requiere migración de schema si se elige A.**

### ISSUE-5 [MENOR]: MEDIUM_PCT y MEDIUM_CROSS muy bajos (31 fondos vs 300-700 esperados)

**Hipótesis:** los fondos que deberían ser MEDIUM_CROSS o MEDIUM_PCT están cayendo
en LOW porque la validación cruzada no se activa (la tabla over_time no se parsea).
Si ISSUE-1 se resuelve, este issue puede resolverse automáticamente.
**Acción:** verificar tras resolver ISSUE-1 antes de implementar fix específico.

### ISSUE-6 [DOCUMENTACIÓN]: NULL en 38 fondos tras ciclo completo

**Causa probable:** fondos que entraron en el bloque pero cuyo `fund_master_record`
no incluyó `KID_Format` (posiblemente `_cost_dict = {}` y ningún campo se escribió).
**Acción:** diagnosticar si son fondos con KIID pero extractor devolvió vacío.

### ISSUE-7 [BL-COST-5]: Normalización OC/ACI mismatch

**6 ISINs confirmados** con `Ongoing_Charge_Recurrent` en BD que contiene ACI@RHP
en lugar de TER. La infraestructura `correct_oc_aci_mismatch` en `sqlite_writer.py`
ya está lista. Solo falta la lógica de detección y corrección.

---

## 5. MÓDULOS ACTUALES RELEVANTES — API COMPLETA

### `cost_format_router.py` (con fixes S2-D)

```python
detect_kid_format(text: str) -> str       # 'PRIIPS_KID'|'UCITS_KIID'|'UNKNOWN'
detect_kid_currency(text: str) -> Optional[str]
_count_priips_signals(text: str) -> int   # señales PRIIPs encontradas
_count_ucits_signals(text: str) -> int    # señales UCITS encontradas
```

Señales PRIIPs (con `\s*` para texto pegado):
```python
_PRIIPS_SIGNALS = [
    r'documento\s*de\s*datos\s*fundamentales',
    r'key information document',
    r'composici[óo]n\s*de\s*los\s*costes?',
    r'composition of costs',
    r'costes?\s*a\s*lo\s*largo\s*del\s*tiempo',
    r'costs over time',
    r'incidencia\s*anual\s*de\s*los\s*costes?',
    r'annual cost impact',
    r'escenarios?\s*de\s*rentabilidad',
    r'performance scenarios',
    r'per[ií]odo\s*de\s*mantenimiento\s*recomendado',
    r'recommended holding period',
]
# Umbral: >= 2 señales → PRIIPS_KID
```

### `cost_table_parser.py` (con fixes S2-D)

```python
parse_costs_over_time(text: str) -> List[dict]
# Cada dict: {horizon_label, horizon_years, total_cost_eur, aci_pct, is_rhp, source}
# horizon_years = -1.0 cuando is_rhp=True y no se resuelve el RHP real

parse_costs_composition(text: str) -> dict
# Claves (ratio decimal): entry_fee_pct, exit_fee_pct, management_fee_pct,
#                         transaction_cost_pct, performance_fee_pct + variantes _eur, _max_pct
```

Fixes aplicados en S2-D:
- `\s*` en todos los patrones multipalabra ES (texto pegado)
- Guard de rango `_COMPOSITION_MAX_PCT` por tipo de coste (evita cross-sección FP)
- `_ratio_to_pct_safe()` para comparación de rangos

### `priips_cost_extractor.py`

```python
extract_priips_costs(
    text: str,
    isin: str,
    existing_oc: Optional[float] = None,
    existing_entry: Optional[float] = None,
    existing_exit: Optional[float] = None,
) -> dict
# Retorna campos de coste + _cost_schedule_rows + _oc_aci_mismatch
# Nunca lanza excepción al caller
```

### `sqlite_writer.py` v25

```python
publish_fund(conn, fund_master_record, nav_series=None, kiid_record=None,
             cost_schedule_rows=None)  # ← nuevo parámetro S2-C

correct_oc_aci_mismatch(conn, isin, ter_pct, source_note='BL-COST-5') -> bool
# Escritura no-COALESCE para corrección de valores legacy
```

---

## 6. RESTRICCIONES DE IMPLEMENTACIÓN (heredadas)

- **R-1**: `KID_Format` y `Cost_Extraction_Quality` NO entran en `_normalize_record`.
- **R-5**: word boundary `\b` en todos los patrones regex nuevos.
- **R-6**: ventanas acotadas (`.{0,200}?` lazy) en todos los patrones nuevos.
- **R-8**: AST validation + `python -W error::SyntaxWarning` tras cada modificación.
- **R-DRY**: nunca reimplementar lógica existente; importar y reutilizar.
- **Kill-switch**: `PRIIPS_COST_EXTRACTION_ENABLED` — respetarlo en todo el código nuevo.
- **Versiones**: actualizar número de versión Y changelog en el encabezado de cada
  fichero modificado. Esta regla es **obligatoria e inexcusable**.
- **Windows/paths**: `parents[2]` desde `proyecto1/` para llegar a raíz del proyecto.
  `proyecto1/core/` debe estar en `sys.path` cuando los módulos core se importan.
- **Sin excepciones al caller**: todo try/except dentro de los extractores.
- **Sin SQL ad-hoc**: ningún UPDATE directo fuera de las funciones de `sqlite_writer.py`.

---

## 7. FORMATO DEL ENTREGABLE

El documento `TRASPASO_CONTEXTO_BL_COST_POST_SPRINT2.md` debe incluir:

### §0 — Instrucciones para Sonnet
Orden de implementación de los fixes, reglas obligatorias, dependencias entre issues.

### §1 — ISSUE-1: Diagnóstico de LOW quality al 50%
- Estrategia de diagnóstico con las queries SQL y el código Python necesario
- Hipótesis ordenadas por probabilidad con criterio de confirmación/refutación
- Especificación de los fixes a implementar en `cost_table_parser.py` y/o
  `cost_format_router.py` según el diagnóstico
- Criterio de éxito: distribución Quality objetivo tras el fix

### §2 — ISSUE-2: Normalización de escala Ongoing_Charge_Recurrent
- Decisión entre opciones A/B/C con justificación
- Script de corrección o estrategia de migración
- Criterio de seguridad para distinguir valores en ratio vs % entero
- Relación con BL-COST-5 (mismatch OC/ACI)

### §3 — ISSUE-3: Fix de 306 fondos sin Is_RHP=1
- Especificación del fix en `priips_cost_extractor.py`
- Cómo resolver `horizon_years=-1.0` → valor real de RHP
- Casos edge: fondos donde `Cost_RHP_Years` no está disponible en el momento
  de construir `_cost_schedule_rows`

### §4 — ISSUE-4: CHECK constraint ACI_RHP <= 25
- Decisión A (ampliar schema) o B (truncado + warning)
- Si A: especificación de la migración de schema (ALTER TABLE o recreación)
- Si B: especificación del comportamiento del extractor

### §5 — ISSUE-5+6: Issues menores
- Confirmar si ISSUE-5 se resuelve automáticamente con ISSUE-1
- Diagnóstico de los 38 fondos NULL post-ciclo

### §6 — BL-COST-5: Corrección OC/ACI mismatch + escala legacy
- Protocolo de corrección para los 6 ISINs confirmados
- Decisión sobre la normalización masiva de los 3.180 valores legacy
- Script SQL o Python de corrección con salvaguardas
- Criterio de verificación post-corrección (Q5 debe mostrar AVG ~1.5-2.0)

### §7 — Plan de sesiones Sonnet post-diagnóstico
- Qué fixes van juntos en la misma sesión Sonnet
- Orden de implementación y dependencias
- Criterios de Go/No-Go para cada sesión

---

## 8. CONTEXTO ADICIONAL

### Principios de diseño que Opus debe respetar

**Principio #1 (Root Cause):** los fixes deben eliminar la causa raíz, no parchear
síntomas. En particular, ISSUE-1 no se resuelve aumentando el umbral de calidad —
se resuelve haciendo que el parser encuentre más tablas de costes reales.

**Principio #2 (DRY):** no reimplementar lógica ya en `cost_table_parser.py` o
`cost_format_router.py`. Si hay que ampliar patrones, hacerlo en esos módulos.

### Decisiones ya tomadas (no reabrir)

- Política COALESCE-safe para `Ongoing_Charge_Recurrent`: es correcta para valores
  nuevos. El problema de escala legacy es pre-existente y no se resuelve cambiando
  la política.
- `\s*` en patrones ES: ya implementado. El fix parcial de S2-D es una solución
  a casos confirmados, no exhaustiva.
- Guard de rango `_COMPOSITION_MAX_PCT`: ya implementado. No revertir.
- `correct_oc_aci_mismatch`: ya implementado en `sqlite_writer.py` v25. Usarla
  para BL-COST-5.

### Datos disponibles para el diagnóstico de ISSUE-1

El fichero `dla2_table_inventory.csv` (3.201 filas) contiene por ISIN:
- `has_cat2_costes`: 1 si DLA2 detectó tabla de costes (Cat.2)
- `n_pct_values`: número de valores porcentuales en el texto
- `n_fee_lines`: número de líneas con fees detectadas
- `oc_null`: 1 si Ongoing_Charge ya era NULL antes del Sprint 2

Este inventario permite cruzar fondos LOW con presencia de tabla en DLA2,
lo que ayudará a cuantificar cuántos fondos LOW tienen datos recuperables
vs. cuántos son estructuralmente irrecuperables (PDFs escaneados, tablas imagen).

### Fondos LOW con tabla DLA2 disponible (estimación)

De los ~3.201 fondos en `dla2_table_inventory.csv`, aproximadamente el 60-70%
tienen `has_cat2_costes=1`. Si los 1.605 fondos LOW tienen un porcentaje similar,
significa que ~960-1.120 fondos LOW SÍ tienen tabla serializada en DLA2 pero
el extractor no la parsea correctamente. Ese es el pool recuperable.

---

Puedes empezar por el ISSUE-1 ya que es el de mayor impacto.
Si necesitas datos adicionales de la BD o muestras de texto de fondos LOW,
indícalo y se proporcionarán en la siguiente iteración.
