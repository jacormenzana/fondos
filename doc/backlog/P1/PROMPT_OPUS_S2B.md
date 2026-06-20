# PROMPT_OPUS_S2B.md
# Prompt para sesión Opus — Diseño S2-B: priips_cost_extractor.py
# Uso: pegar íntegramente en nueva conversación Opus antes de cualquier pregunta.
# Generado por: sesión Sonnet S2-A (post-cierre, 2026-05-21)

---

## ROL Y OBJETIVO DE ESTA SESIÓN

Eres el arquitecto de un sistema de extracción de costes de fondos de inversión
europeos (PRIIPs). Esta sesión produce un único entregable: el documento de traspaso
`TRASPASO_CONTEXTO_BL_COST_SPRINT2_S2B.md`, que especifica con precisión suficiente
para implementación directa (sin ambigüedad) el módulo `priips_cost_extractor.py`.

**No se escribe código en esta sesión.** Solo diseño, decisiones y especificación.
El documento resultante lo implementará una sesión Sonnet posterior.

---

## 1. ESTADO ACTUAL DEL PROYECTO

### 1.1 Contexto de negocio

Sistema de clasificación automatizada de ~3.200 fondos de inversión europeos. Los
documentos regulatorios (KID/KIID en PDF) contienen tablas de costes estandarizadas
que el pipeline extrae. La infraestructura está lista; falta el extractor.

### 1.2 Módulos entregados en S2-A (disponibles para S2-B)

Los siguientes módulos existen y están testeados. S2-B los usa como dependencias:

**`cost_format_router.py`** — API pública:
```python
detect_kid_format(text: str) -> str       # 'PRIIPS_KID' | 'UCITS_KIID' | 'UNKNOWN'
detect_kid_currency(text: str) -> Optional[str]  # 'EUR' | 'USD' | None
get_format_signals_detail(text: str) -> dict     # diagnóstico
```

**`cost_table_parser.py`** — API pública:
```python
parse_costs_over_time(text: str) -> List[dict]
# Cada dict: {horizon_label, horizon_years, total_cost_eur, aci_pct, is_rhp, source}
# horizon_years=-1.0 cuando is_rhp=True (el parser no resuelve el valor real de RHP)
# source: 'DLA2' | 'PLAIN_TEXT'

parse_costs_composition(text: str) -> dict
# Claves presentes según lo extraído:
# entry_fee_pct, entry_fee_eur, entry_fee_max_pct
# exit_fee_pct, exit_fee_eur, exit_fee_max_pct
# management_fee_pct, management_fee_eur
# transaction_cost_pct, transaction_cost_eur
# performance_fee_pct, performance_fee_eur
# (todos como ratio decimal: 0.005 = 0.5%)
```

**`cost_cross_validator.py`** — API pública:
```python
@dataclass
class ValidationResult:
    status: str           # 'OK'|'DISCREPANCY'|'PCT_ONLY'|'EUR_ONLY'|'NONE'
    validated_pct: Optional[float]
    discrepancy_bp: Optional[float]
    notes: str

validate_pct_eur(
    pct: Optional[float],
    eur_amount: Optional[float],
    base: float = 10000.0,
    tolerance: float = 0.0005   # 5bp
) -> ValidationResult
# Lógica: diff=|pct - eur/base|
# diff<=5bp → OK, usar pct
# 5bp<diff<=50bp → DISCREPANCY leve, usar pct
# diff>50bp → DISCREPANCY grave, validated_pct=None
```

**`cost_format_signals.py`** — funciones diagnósticas (NO usar para lógica principal;
`cost_format_router.py` es el punto de entrada correcto):
```python
detect_entry_fee_false_positive(text, entry_fee_db, fee_known_flag='UNKNOWN') -> dict
detect_exit_fee_false_positive(text, exit_fee_db, fee_known_flag='UNKNOWN') -> dict
detect_oc_aci_gap(text, oc_db) -> dict
```

**`shared/config.py` v19.1**:
```python
PRIIPS_INVESTMENT_BASE: float = 10000.0
COST_CROSS_VALIDATION_TOLERANCE_PCT: float = 0.0005   # 5bp
PRIIPS_COST_EXTRACTION_ENABLED: bool = False           # kill-switch
COST_SCHEDULE_SOURCE_VALUES: tuple = (
    'PRIIPS_COSTS_OVER_TIME', 'PRIIPS_COMPOSITION', 'PRIIPS_TEXT',
    'UCITS_DERIVED', 'MANUAL'
)
```

### 1.3 Schema de BD (v19)

**Columnas nuevas en `fund_master`** (actualmente todas NULL):
```sql
KID_Format              TEXT CHECK (IN 'UCITS_KIID','PRIIPS_KID','UNKNOWN')
KID_Currency            TEXT
Cost_Extraction_Quality TEXT CHECK (IN 'HIGH','MEDIUM_CROSS','MEDIUM_EUR','MEDIUM_PCT','LOW','NONE')
Cost_RHP_Years          REAL CHECK (IS NULL OR (> 0 AND <= 50))
Entry_Fee_Pct_Max       REAL CHECK (IS NULL OR (>= 0 AND <= 25))
Exit_Fee_Pct_Max        REAL CHECK (IS NULL OR (>= 0 AND <= 25))
Management_Fee_Pct      REAL CHECK (IS NULL OR (>= 0 AND <= 10))
Transaction_Cost_Pct    REAL CHECK (IS NULL OR (>= 0 AND <= 5))
Performance_Fee_Pct     REAL CHECK (IS NULL OR (>= 0 AND <= 30))
ACI_1Y                  REAL CHECK (IS NULL OR (>= 0 AND <= 50))
ACI_RHP                 REAL CHECK (IS NULL OR (>= 0 AND <= 25))
```
Nota: los valores en `fund_master` son **ratio decimal** (0.005 = 0.5%), igual que
el resto de columnas de fee. Los CHECK son como % (0-25 = 0-2500bp), no ratio.
Verificar la escala real antes de especificar.

**Tabla `fund_cost_schedule`**:
```sql
CREATE TABLE fund_cost_schedule (
    ISIN              TEXT NOT NULL,
    Horizon_Years     REAL NOT NULL,
    Is_RHP            INTEGER NOT NULL DEFAULT 0,
    Total_Costs_EUR   REAL,
    Total_Costs_Pct   REAL,
    Annual_Impact_Pct REAL,
    Source            TEXT NOT NULL,
    Created_At        TEXT,
    Updated_At        TEXT,
    PRIMARY KEY (ISIN, Horizon_Years),
    CHECK (Horizon_Years > 0 AND Horizon_Years <= 50),   -- ⚠ NO acepta -1.0
    CHECK (Is_RHP IN (0, 1)),
    CHECK (Source IN ('PRIIPS_COSTS_OVER_TIME','UCITS_DERIVED','MANUAL'))
)
```

**`upsert_cost_schedule(conn, isin, schedule_rows) -> int`** en `sqlite_writer.py`:
- Hace DELETE de todas las filas del ISIN + INSERT de las nuevas (atómico)
- `schedule_rows`: lista de dicts con claves `Horizon_Years`, `Is_RHP`, `Source`,
  opcionales: `Total_Costs_EUR`, `Total_Costs_Pct`, `Annual_Impact_Pct`

**Contexto de `sqlite_writer.py` para los campos de cost**:
```python
# ON CONFLICT DO UPDATE SET para las 11 columnas nuevas:
KID_Format              = excluded.KID_Format            # sobrescritura directa
KID_Currency            = COALESCE(excluded, fund_master) # COALESCE (no sobrescribe si NULL)
Cost_Extraction_Quality = COALESCE(excluded, fund_master)
Cost_RHP_Years          = COALESCE(excluded, fund_master)
# ... mismo COALESCE para las 7 restantes
ACI_1Y                  = COALESCE(excluded, fund_master)
ACI_RHP                 = COALESCE(excluded, fund_master)
```

### 1.4 Posición del extractor en el pipeline

`pipeline.py` actualmente tiene el hook preparado pero vacío:
```python
# v19 BL-COST-2 R-3: KID_Format o Cost_Extraction_Quality NULL → re-characterize
if not _needs_char and (_v3_row[7] is None or _v3_row[8] is None):
    _needs_char = True
```

El extractor se integrará en `pipeline.py` (BL-COST-4c, sesión S2-C) después de
que `characterize_fund()` retorne. S2-B NO toca `pipeline.py`.

**Fuentes de texto disponibles por fondo en el pipeline:**
- `kiid_text`: texto plano del KIID (Raw_KIID_Text de BD)
- `DLA2_Table_Text`: serialización DLA2 de tablas (en `fund_kiid_metadata`)
- El texto concatenado para el extractor sería: `kiid_text + "\n" + dla2_table_text`

---

## 2. LOS 8 PDFs MUESTRA (ground truth para los tests de S2-B)

Todos son PRIIPS_KID. Esta es la información conocida sobre sus costes. S2-B debe
especificar los valores exactos esperados tras leer los PDFs reales.

| ISIN | Gestora | Moneda | RHP | Características clave |
|---|---|---|---|---|
| IE00BJGT6Q17 | PIMCO | EUR | ? | Exit fee EUR alto (197 EUR) — sospechoso FP |
| LU1084165304 | Fidelity | USD | ? | Entry 5.25% / 510 USD — base 10.000 USD |
| IE0032875985 | PIMCO | EUR | 3Y | OC=ACI@3Y (2.4%) — mezcla TER/ACI paradigmática |
| IE00B45H7020 | BlackRock | EUR | 1Y | Monetario, costes mínimos (0.04%) |
| FR0000989626 | Groupama | EUR | 3M | Entry 0.50% / 50€, RHP=3 meses |
| LU0135992385 | Schroders | EUR | 5Y | Entry "no cobramos" — correctamente 0 |
| IE00BZ4D7085 | Polar Capital | EUR | 1Y | Entry 0% + "hasta 5% futuro", management 0.10%, transaction 0.02% |
| LU1502282632 | Candriam | EUR | 5Y | Entry 3.50% máximo |

**Información adicional confirmada en S2-A** (de los textos sintéticos de los tests,
basada en datos reales de los PDFs):

- **IE00BZ4D7085** (Polar Capital): `total_cost_eur=12`, `aci_pct=0.12%`, RHP=1Y,
  `entry_fee_pct=0%`, `management_fee_pct=0.10%`, `transaction_cost_pct=0.02%`
- **IE00B45H7020** (BlackRock): 2 columnas (1Y + RHP=1Y), `total_cost_eur=10`, `aci_pct=0.10%`
- **LU1502282632** (Candriam): 2 columnas (1Y + 5Y), `entry_fee_max_pct=3.50%`
- **FR0000989626** (Groupama): `entry_fee_pct=0.50%`, `entry_fee_eur=50`, RHP=3 meses
- **LU1084165304** (Fidelity): base=10.000 USD, `entry_fee_pct=5.25%`, `total_cost_eur=510 USD`

Los PDFs reales están en `C:\desarrollo\fondos\data\kiids\` (o equivalente en tu
entorno). Opus debe leerlos para obtener los valores exactos para los tests.

---

## 3. PROBLEMAS ABIERTOS QUE OPUS DEBE RESOLVER

Estos son los puntos de diseño sin respuesta definitiva. Son el núcleo del trabajo
de esta sesión.

### P-1: Resolución de RHP en `fund_cost_schedule` ⚠ BLOQUEANTE

**Problema:** `cost_table_parser` devuelve `horizon_years=-1.0` cuando la columna
es RHP (período de mantenimiento recomendado). Pero `fund_cost_schedule` tiene
`CHECK (Horizon_Years > 0)` — no admite -1.0 como PK.

**Opciones a evaluar:**
- **Opción A:** El extractor resuelve el valor numérico de RHP desde el texto
  (`Cost_RHP_Years`) y lo usa como `Horizon_Years` en `fund_cost_schedule`.
  Requiere extraer "período de mantenimiento recomendado: X años/meses" del texto.
- **Opción B:** Fila RHP siempre usa `Horizon_Years = Cost_RHP_Years` (precondición:
  `Cost_RHP_Years` debe extraerse antes que las filas de `fund_cost_schedule`).
- **Opción C:** Cambiar el schema — añadir columna `Is_RHP_Only` o usar
  `Horizon_Years = 0` con CHECK relajado. Requiere migración de schema.

**Restricción:** no hacer migración de schema en S2-B si es evitable.

### P-2: Cálculo de `Cost_Extraction_Quality`

**Problema:** los 6 niveles son `HIGH | MEDIUM_CROSS | MEDIUM_EUR | MEDIUM_PCT | LOW | NONE`.
No hay especificación detallada de cuándo asignar cada uno.

**Propuesta de criterios a confirmar/refinar:**
- `HIGH`: ambos (%) y EUR extraídos, cross-validation OK (≤5bp)
- `MEDIUM_CROSS`: ambos extraídos, DISCREPANCY leve (5bp-50bp)
- `MEDIUM_EUR`: solo EUR extraído (sin %)
- `MEDIUM_PCT`: solo % extraído (sin EUR) — caso más común
- `LOW`: solo texto libre (sin tablas DLA2, solo PLAIN_TEXT), datos parciales
- `NONE`: no se encontró ninguna tabla de costes

**Pregunta:** ¿`MEDIUM_CROSS` aplica cuando hay discrepancia grave (>50bp)?
¿O eso es `LOW`?

### P-3: Gestión de `Ongoing_Charge_Recurrent` cuando el KID reporta ACI

**Problema:** 328 fondos tienen `Ongoing_Charge_Recurrent` = ACI (no TER). El extractor
puede identificar esto porque `detect_oc_aci_gap()` lo detecta.

**Pregunta:** ¿el extractor debe SOBRESCRIBIR `Ongoing_Charge_Recurrent` con el TER
extraído de la tabla de composición (management + transaction), incluso si el valor
actual en BD no es NULL? ¿O solo cuando el valor actual es NULL?

**Restricción clave:** `sqlite_writer.py` usa COALESCE para `Ongoing_Charge_Recurrent`,
por lo que si el extractor devuelve un valor nuevo, COALESCE lo ignorará si ya existe
uno en BD. Para sobrescribir habría que cambiar el comportamiento del writer para esa
columna específica, o usar una ruta alternativa.

### P-4: Escala de valores en `fund_master`

**Problema ambiguo:** las columnas de fee existentes (ej. `Entry_Fee_Pct`,
`Exit_Fee_Pct`, `Ongoing_Charge_Recurrent`) en BD, ¿están en ratio decimal (0.005)
o en porcentaje entero (0.5)? Los CHECKs del schema dicen:
```sql
Entry_Fee_Pct_Max REAL CHECK (IS NULL OR (>= 0 AND <= 25))
```
Si 25 = 25%, los valores en BD son porcentajes (no ratio). Pero `cost_table_parser`
devuelve ratios decimales (0.005 para 0.5%). El extractor debe convertir.

**Verificar en la BD real:** `SELECT AVG(Entry_Fee_Pct), MAX(Entry_Fee_Pct) FROM fund_master WHERE Entry_Fee_Pct > 0`

### P-5: Fondos con RHP < 1 año (FR0000989626, RHP=3 meses)

**Pregunta confirmada como resuelta en el documento de diseño:**
> "ACI_1Y queda NULL para fondos con RHP < 1 año. ACI_RHP siempre se puebla."

**Pero falta especificar:** ¿`Cost_RHP_Years = 0.25` para un fondo con RHP 3 meses?
¿Y cómo se almacena en `fund_cost_schedule`? ¿`Horizon_Years=0.25, Is_RHP=1`?

### P-6: Comportamiento cuando DLA2 y texto plano difieren

**Caso:** el extractor encuentra, para el mismo fondo:
- DLA2 → `entry_fee_pct = 0.05` (5%)
- PLAIN_TEXT → `entry_fee_pct = 0.00` (0%)

**Pregunta:** ¿DLA2 siempre tiene prioridad? ¿O hay casos en que PLAIN_TEXT es
más fiable? ¿Cómo refleja esto `Cost_Extraction_Quality`?

### P-7: Integración con `fund_cost_schedule` — ¿qué `Source` usar?

**Problema:** `COST_SCHEDULE_SOURCE_VALUES` ahora tiene 5 valores:
`'PRIIPS_COSTS_OVER_TIME', 'PRIIPS_COMPOSITION', 'PRIIPS_TEXT', 'UCITS_DERIVED', 'MANUAL'`

Pero `fund_cost_schedule` tiene CHECK que solo admite 3:
`'PRIIPS_COSTS_OVER_TIME', 'UCITS_DERIVED', 'MANUAL'`

Si el extractor obtiene la tabla de `parse_costs_over_time()`, `Source = 'PRIIPS_COSTS_OVER_TIME'` — OK.
Pero si deriva valores de `parse_costs_composition()` o de texto libre, ¿qué Source usa?
¿Hay que actualizar el CHECK en el schema (requiere migración)?

---

## 4. INTERFAZ MÍNIMA ESPERADA DE `priips_cost_extractor.py`

Lo que Opus debe especificar completamente:

```python
# proyecto1/core/priips_cost_extractor.py

def extract_priips_costs(
    text: str,
    isin: str,
    existing_oc: Optional[float] = None,    # Ongoing_Charge_Recurrent actual en BD
    existing_entry: Optional[float] = None, # Entry_Fee_Pct actual en BD
    existing_exit: Optional[float] = None,  # Exit_Fee_Pct actual en BD
) -> dict:
    """
    Extrae todos los campos de coste de un KID PRIIPs.
    
    Args:
        text: texto concatenado del KID (Raw_KIID_Text + DLA2_Table_Text).
              DLA2_Table_Text contiene '|||' como separador de celdas.
        isin: ISIN del fondo (para logging).
        existing_*: valores actuales en BD para decisiones de sobrescritura.
    
    Returns:
        dict con subset de las siguientes claves (solo las extraídas con éxito):
            KID_Format:              str
            KID_Currency:            str | None
            Cost_Extraction_Quality: str
            Cost_RHP_Years:          float | None
            Entry_Fee_Pct_Max:       float | None
            Exit_Fee_Pct_Max:        float | None
            Management_Fee_Pct:      float | None
            Transaction_Cost_Pct:    float | None
            Performance_Fee_Pct:     float | None
            ACI_1Y:                  float | None
            ACI_RHP:                 float | None
            Ongoing_Charge_Recurrent: float | None  # SOLO si se corrige TER
            _cost_schedule_rows:     List[dict]     # para upsert_cost_schedule
    """
```

**Opus debe especificar:**
1. La lógica paso a paso dentro de `extract_priips_costs`
2. Cómo se calculan cada uno de los campos de retorno
3. Cómo se determina `Cost_Extraction_Quality`
4. Cómo se construye `_cost_schedule_rows` (resolviendo P-1, P-7)
5. Si se necesitan funciones auxiliares privadas y cuáles
6. Los valores exactos esperados para cada uno de los 8 ISINs muestra

---

## 5. RESTRICCIONES DE IMPLEMENTACIÓN (heredadas)

- **R-1**: `KID_Format` y `Cost_Extraction_Quality` NO entran en `_normalize_record`
  ni `_post_upsert_normalize_db` de `sqlite_writer.py`. No tocar esas funciones.
- **R-5**: word boundary en todos los patrones regex nuevos.
- **R-6**: ventanas acotadas (`.{0,200}?` lazy) en todos los patrones de extracción.
- **R-8**: AST validation obligatoria tras cada modificación.
- **No migración de schema en S2-B** si es evitable (ver P-1 y P-7).
- **Kill-switch**: el extractor respeta `PRIIPS_COST_EXTRACTION_ENABLED`. Si False,
  retorna dict vacío sin hacer nada.
- **Sin efectos secundarios en import**: el módulo no crea archivos ni conexiones
  a nivel de módulo.
- **Manejo de excepciones**: ninguna excepción sale al caller. Si el extractor falla,
  retorna el dict parcial con lo que haya extraído hasta ese punto + `Cost_Extraction_Quality='LOW'`.

---

## 6. FORMATO DEL ENTREGABLE

El documento `TRASPASO_CONTEXTO_BL_COST_SPRINT2_S2B.md` debe incluir:

### §0 — Instrucciones para Sonnet
Orden de implementación, reglas obligatorias, AST validation, tests requeridos.

### §1 — Resoluciones de los problemas abiertos P-1 a P-7
Una sección por problema. Decisión tomada + razonamiento breve.
Si algún problema requiere cambio de schema, especificarlo como sub-tarea previa.

### §2 — Especificación completa de `priips_cost_extractor.py`
- Interfaz pública final (con firma completa y docstring)
- Funciones privadas necesarias con firma y propósito
- Lógica paso a paso de `extract_priips_costs` en pseudocódigo comentado
- Tabla de mapeo: campo_retorno → fuente de datos → función S2-A que la provee

### §3 — Criterios de `Cost_Extraction_Quality`
Tabla exhaustiva: condición → valor asignado. Sin ambigüedad.

### §4 — Ground truth de los 8 ISINs muestra
Tabla por ISIN con los valores exactos esperados para cada campo. Estos serán los
assert en los tests de S2-B. Opus los obtiene leyendo los PDFs reales.

Si los PDFs no son accesibles en esta sesión, marcar con `[VERIFICAR_EN_DISCO]`
los valores que Sonnet debe confirmar antes de escribir los tests.

### §5 — Tests obligatorios
Lista de los tests que Sonnet debe implementar en `test_priips_cost_extractor.py`.
Especificar: nombre del test, input (texto o ISIN), output esperado.

### §6 — Lo que NO hace S2-B
Lista explícita para delimitar scope (evitar que Sonnet toque pipeline.py, etc.).

### §7 — Sesiones S2-C/D preview
Actualización del preview con lo que cambie según las decisiones tomadas en S2-B.

---

## 7. CONTEXTO ADICIONAL PARA OPUS

### Decisiones ya tomadas (no reabrir):

- **D-S2-1**: criterio de detección PRIIPS relajado — solo señales textuales, sin
  exigir EUR near costs. Ya implementado en `cost_format_router.py`. ✅
- **D-S2-2**: parámetro `fee_known_flag` en detectores FP. Ya implementado. ✅
- **D-S2-3**: extractor opera sobre texto concatenado (Raw_KIID_Text + DLA2_Table_Text),
  prioridad: tabla over_time > tabla composition > texto libre.
- **D-S2-4**: kill-switch `PRIIPS_COST_EXTRACTION_ENABLED = False`. Ya implementado. ✅
- **D-S2-5**: `ucits_cost_extractor.py` es alcance mínimo (5 fondos). Va en S2-C.

### Deuda técnica documentada (no bloquea S2-B):
- Bug en `migrate_v18_to_v19.py` (FK no actualizadas, COMMIT fallido). No urgente.
- `cost_format_signals.py` — `EUR_VALUES_NEAR_COSTS_PATTERN` no detecta formatos
  compactos. Resuelto por D-S2-1 (el router ya no depende del patrón EUR). ✅

### Invariantes del pipeline que S2-B debe respetar:
- Los valores en `fund_master` para fees (`Entry_Fee_Pct`, `Exit_Fee_Pct`,
  `Ongoing_Charge_Recurrent`) usan COALESCE — no se sobrescriben si ya existen.
  El extractor de S2-B debe decidir explícitamente cuándo quiere sobrescribir
  (en cuyo caso necesita un mecanismo distinto a COALESCE).
- `fund_cost_schedule` es DELETE+INSERT atómico por ISIN en cada extracción.
- El campo `_cost_schedule_rows` en el dict de retorno del extractor es la
  interfaz hacia `upsert_cost_schedule()` — no una función interna del extractor.

---

Puedes empezar consultando los PDFs de los 8 ISINs muestra para construir el
ground truth del §4. Si no tienes acceso a los PDFs, especifica los valores con
`[VERIFICAR_EN_DISCO]` y proporciona la lógica para que Sonnet los verifique.
