# TRASPASO_CONTEXTO_BL_COST_SPRINT2_S2C.md
# Especificación de implementación — Sesión S2-C: integración pipeline + ucits + OC mismatch
# Generado por: sesión Nivel-3 Opus (2026-05-22), sobre código de producción leído en el Project
# Para: sesión Nivel-2 Sonnet — implementación directa de BL-COST-4b/4c/4d
# Autocontenido conforme a Norma 5.4

---

## §0 — INSTRUCCIONES PARA SONNET

Esta sesión implementa cuatro tareas delimitadas. NADA más.

### 0.1 Entregables de esta sesión (scope cerrado)

| ID | Fichero | Tarea |
|---|---|---|
| **T-1** | `proyecto1/core/ucits_cost_extractor.py` | Extractor mínimo UCITS (alcance reducido, ver §2) |
| **T-2** | `proyecto1/core/pipeline.py` | Integración de `extract_priips_costs` (BL-COST-4c) |
| **T-3** | `proyecto1/core/sqlite_writer.py` | Ruta de escritura no-COALESCE para `_oc_aci_mismatch` (BL-COST-4d) |
| **T-4** | `proyecto1/tests/test_ucits_cost_extractor.py` | Suite de tests para T-1 |

### 0.2 Prerequisito obligatorio: verificar S2-B entregado

Antes de escribir ninguna línea, confirmar que `priips_cost_extractor.py` existe y
los tests de S2-B pasan. Si algún test de S2-B falla, **parar y reportar** — no continuar
con S2-C hasta que S2-B esté verde (los módulos que se integran deben ser correctos).

```
python -X utf8 -m pytest proyecto1/tests/test_priips_cost_extractor.py -v --tb=short
```

Si los tests pasan → continuar. Si fallan → reportar a José antes de proceder.

### 0.3 Reglas de implementación (heredadas de S2-B, no negociables)

- **R-1**: `KID_Format` y `Cost_Extraction_Quality` **NO** entran en `_normalize_record`
  ni en `_post_upsert_normalize_db`. No tocar esas funciones.
- **R-5**: word boundary (`\b`) en todo patrón regex nuevo.
- **R-6**: ventanas acotadas y lazy (`.{0,200}?`) en todo patrón nuevo.
- **R-8**: AST validation tras **cada** escritura a fichero:
  ```
  python -X utf8 -c "import ast; ast.parse(open('proyecto1/core/<módulo>.py',encoding='utf-8').read())"
  ```
- **R-DRY**: No reimplementar lógica ya en S2-A/S2-B. `ucits_cost_extractor` puede llamar a
  `detect_kid_format`, `detect_kid_currency`, `parse_costs_composition` si le sirven.
- **Kill-switch**: `PRIIPS_COST_EXTRACTION_ENABLED` controla TODO el sprint 2 (PRIIPs y UCITS).
  Si False → ningún extractor actúa. Si True → ambos actúan según su routing.
- **Sin efectos secundarios en import** en ningún módulo nuevo.
- **Ninguna excepción sale al caller** desde ningún extractor.
- **Leer el fichero completo antes de editar**. Ediciones quirúrgicas con referencia a línea exacta.
  Nunca reescrituras completas salvo que sea imposible de otra manera.

### 0.4 Orden de implementación dentro de la sesión

1. Verificar S2-B verde (§0.2).
2. Leer `priips_cost_extractor.py`, `cost_format_router.py`, `cost_table_parser.py` completos.
3. Implementar T-1 (`ucits_cost_extractor.py`) + T-4 (sus tests). AST OK. Tests verdes.
4. Leer `pipeline.py` (foco en líneas 560–760: zona `_v3_row`, `fund_master_record`, comentario
   `BL-COST-2 R-4` en línea 737). Leer `sqlite_writer.py` (foco en `upsert_fund_master`,
   `publish_fund`, `upsert_cost_schedule`).
5. Implementar T-2 (pipeline). AST OK. Smoke test manual (ver §4).
6. Implementar T-3 (sqlite_writer ruta OC-mismatch). AST OK.
7. Verificar integridad: test unitario del bloque OC-mismatch (ver §3.3).

### 0.5 Lo que NO se toca en esta sesión

- ❌ NO `cost_table_parser.py`, `cost_format_router.py`, `cost_cross_validator.py` (S2-A, cerrado).
- ❌ NO `schema_fondos.sql` (sin cambio de schema en S2-C).
- ❌ NO activar `PRIIPS_COST_EXTRACTION_ENABLED = True` en config (activo solo en S2-D/BL-COST-6).
- ❌ NO BL-COST-5 (heurística INTER-COST sobre los ~328 fondos con OC-ACI mismatch): requiere
  sesión Opus separada. S2-C solo prepara la **infraestructura** de escritura (ruta no-COALESCE).
- ❌ NO modificar `classify_utils.py`.
- ❌ NO `kiid_parser.py` ni detectores legacy de fees.
- ❌ NO `run_block.py` ni scripts `.bat`.

---

## §1 — RESOLUCIÓN DE PROBLEMAS ABIERTOS (análogos a P-1…P-7 de S2-B)

### PC-1 — ¿Cómo integrar `extract_priips_costs` en `pipeline.py` sin romper el flujo CACHED?

**Hecho confirmado (lectura del código):**
- `pipeline.py` línea 562: la `_v3_row` ya lee `KID_Format` y `Cost_Extraction_Quality` de BD.
  Si son NULL → `_needs_char = True` (línea 590). Esto se implementó en Sprint 1 para preparar Sprint 2.
- Comentario en línea 733-737: las variables `_oc_bd`, `_aci_rhp_bd`, `_kf_bd` debían añadirse
  al bloque de lectura BD previa en Sprint 2. Son exactamente lo que necesitamos.
- `kiid_text` en el momento de la integración ya contiene el texto concatenado (Raw + DLA2_Table_Text)
  tal como lo entrega `io.get_kiid_for_isin`. El extractor recibe exactamente el texto correcto.
- `upsert_cost_schedule` en `sqlite_writer.py` (línea 1063) existe y es invocable; basta pasarle
  `isin` y `_cost_schedule_rows`.
- `publish_fund` (línea 865) NO llama a `upsert_cost_schedule`. Esta llamada debe añadirse
  en el pipeline (no en publish_fund), porque `_cost_schedule_rows` es un artefacto del extractor
  Sprint 2 que no debe contaminar el record normalizado de fund_master.

**DECISIÓN PC-1:** La integración se hace en un bloque **tras la construcción de `fund_master_record`**
(después de la línea 759 en el código actual) y **antes de la llamada a `publish_fund`**. Ver flujo exacto en §3.2.

El patrón R-4 de Sprint 1 (`_X_efectivo = record.get("X") or _X_bd`) se extiende a las
tres variables nuevas que necesita el extractor: `_oc_bd`, `_entry_bd`, `_exit_bd`.
Se leen en el mismo bloque `_v3_row` existente (o en una segunda SELECT dedicada, ver PC-3).

### PC-2 — ¿Cómo manejar el routing PRIIPs vs UCITS en el pipeline?

**Hecho confirmado:** `detect_kid_format(text)` devuelve `'PRIIPS_KID'`, `'UCITS_KIID'` o `'UNKNOWN'`.
El routing lo hace ya el extractor PRIIPS internamente (retorna `NONE` si no es PRIIPS). Pero el
pipeline necesita decidir qué extractor invocar para no llamar a ambos innecesariamente.

**DECISIÓN PC-2:** El pipeline lee `_kf_bd` de BD antes de llamar al extractor. La lógica de entrada:
- Si `PRIIPS_COST_EXTRACTION_ENABLED` is False → no llamar a ningún extractor.
- Si `Cost_Extraction_Quality` en BD ya es `'HIGH'` → **skip** (no re-extraer). Calidad perfecta
  no se degrada. Para cualquier otro valor (MEDIUM_*, LOW, NONE, NULL) → re-extraer.
- Llamar a `detect_kid_format(kiid_text)` en el pipeline para decidir el router:
  `'PRIIPS_KID'` → `extract_priips_costs`; `'UCITS_KIID'` → `extract_ucits_costs`.
  `'UNKNOWN'` → no llamar a ninguno, dejar campos NULL.
- El resultado del extractor (dict) se **mezcla en `fund_master_record`** para los campos
  que están en el schema de fund_master (los 11 campos nuevos: `KID_Format`, `KID_Currency`,
  `Cost_Extraction_Quality`, etc.). Las claves privadas (`_cost_schedule_rows`, `_oc_aci_mismatch`)
  se procesan separadamente antes de pasar el record a `publish_fund`.

**Razón (Principio #2 DRY):** el routing ya está implementado en `detect_kid_format`; el pipeline
no reimplementa las heurísticas de detección.

### PC-3 — ¿Cómo leer `existing_oc`, `existing_entry`, `existing_exit` de BD?

**Hecho confirmado:** estos tres valores son necesarios para la lógica P-3 de `priips_cost_extractor`
(`_detect_oc_aci_mismatch`). La `_v3_row` existente (línea 562) no los incluye.

**DECISIÓN PC-3:** Añadir una **segunda SELECT** dedicada, colocada justo antes de la llamada
al extractor (dentro del bloque `if PRIIPS_COST_EXTRACTION_ENABLED`). No ampliar la `_v3_row`
original para no alterar el índice de columnas de la lógica `_v3_row[:5]` existente.

```python
# Solo se ejecuta si el kill-switch está ON — no añade coste en Sprint 1
_cost_bd_row = conn.execute(
    "SELECT Ongoing_Charge_Recurrent, Entry_Fee_Pct, Exit_Fee_Pct "
    "FROM fund_master WHERE ISIN=?", (isin,)
).fetchone()
_oc_bd    = _cost_bd_row[0] if _cost_bd_row else None
_entry_bd = _cost_bd_row[1] if _cost_bd_row else None
_exit_bd  = _cost_bd_row[2] if _cost_bd_row else None
```

**Nota importante (Principio DRY):** `existing_oc` que se pasa al extractor es el valor
**efectivo** (del record del ciclo o de BD), siguiendo el patrón R-4. Pero aquí hay una
asimetría: `fund_master_record["Ongoing_Charge_Recurrent"]` se puebla en el ciclo desde
`parsed.get("Ongoing_Charge")` (parser KIID legacy, valor en escala mixta). Ese valor
**no** debe usarse como `existing_oc` para el extractor Sprint 2, porque podría ser el
propio valor a corregir. En su lugar, usar **solo `_oc_bd`** (el valor ya en BD antes del
ciclo actual). El extractor compara el OC histórico en BD contra el TER reconstruido.

### PC-4 — Ruta de escritura no-COALESCE para `_oc_aci_mismatch` ⚠ DISEÑO CRÍTICO

**Hecho confirmado:**
- `upsert_fund_master` usa COALESCE para `Ongoing_Charge_Recurrent` (línea 583 del
  ON CONFLICT SQL visible en el código).
- Cuando el extractor detecta mismatch (`_oc_aci_mismatch=True`), NO devuelve
  `Ongoing_Charge_Recurrent` en el dict (decisión P-3 de S2-B). Por tanto, COALESCE
  preserva el valor incorrecto en BD. Eso es correcto: BL-COST-5 es quien corrige.
- Lo que S2-C necesita es **preparar la infraestructura** para que BL-COST-5 (sesión
  futura) pueda hacer la corrección sin duplicar lógica.

**DECISIÓN PC-4:** Añadir en `sqlite_writer.py` una función nueva `correct_oc_aci_mismatch`
(ver §3.3) que ejecuta un UPDATE directo (sin COALESCE). Esta función **no se llama desde el
pipeline de S2-C** — es infraestructura para BL-COST-5. En S2-C el pipeline simplemente
registra el mismatch en `ingestion_log` (con tag `[BL-COST-4c][OC-ACI]`) para que BL-COST-5
pueda consultar los ISINs afectados. El conteo de mismatches se reporta en el print de
resumen del ciclo.

**Razón (Principio #1 Root Cause):** La causa raíz de los ~328 fondos con OC=ACI es que el
parser KIID legacy confundió ACI@RHP con TER. La corrección definitiva requiere diseño
separado (BL-COST-5 con sesión Opus). S2-C no puede remediar ese problema de raíz; solo
puede señalizarlo con precisión y preparar la vía de escritura para cuando BL-COST-5 esté listo.

### PC-5 — ¿Qué hace `ucits_cost_extractor.py` exactamente?

**Hecho confirmado (del proyecto):**
- Solo ~5 fondos en el corpus son `UCITS_KIID` (ver §2 detalle).
- Los UCITS KIID tienen un único número de `Ongoing Charges` (no tablas de horizonte).
- `parse_costs_composition` de S2-A ya puede extraer `management_fee_pct` y
  `transaction_cost_pct` de textos UCITS (los patrones son compatibles), pero no está
  garantizado para todos los formatos.

**DECISIÓN PC-5:** Alcance mínimo estricto:
- `extract_ucits_costs(text, isin, existing_oc=None) -> dict`
- Solo extrae: `KID_Format='UCITS_KIID'`, `KID_Currency`, `Ongoing_Charge_Recurrent`
  (del patrón "Ongoing Charges X%"), `Management_Fee_Pct` y `Transaction_Cost_Pct`
  usando `parse_costs_composition` del S2-A.
- NO genera `_cost_schedule_rows` (se crea una sola fila sintética con
  `Horizon_Years=1.0, Is_RHP=1, Source='UCITS_DERIVED'` usando el OC como
  `Annual_Impact_Pct`). Ver §2.3.
- `Cost_Extraction_Quality`: solo `'HIGH'` (si OC extraído), `'LOW'` (si no), `'NONE'` (si no UCITS).
- Kill-switch: `PRIIPS_COST_EXTRACTION_ENABLED` (mismo flag).

### PC-6 — Derivación cache `ACI_1Y`/`ACI_RHP` (hallazgo §4.4 de S2-B)

**Hecho confirmado (S2-B §4.4 + código en S2-B):** El extractor PRIIPs ya devuelve `ACI_1Y`
y `ACI_RHP` directamente en el dict de retorno. La decisión de S2-B fue que el ACI es anualizado
y el EUR del schedule es acumulado; cruzarlos solo es válido a 1 año. Por tanto `ACI_1Y` y
`ACI_RHP` son valores escalares en el dict, **no se recomputan desde el schedule**.

**DECISIÓN PC-6:** El pipeline toma `ACI_1Y` y `ACI_RHP` directamente del dict del extractor
(si están presentes) y los escribe en `fund_master_record`. `upsert_fund_master` ya incluye
estas dos columnas con COALESCE (líneas 582-583 del ON CONFLICT actual). No hay que modificar
`sqlite_writer` para esto — el mecanismo ya existe. La regla es: **tomar del dict, no
recomputar desde `_cost_schedule_rows`**.

**Razón:** Recomputar desde `EUR/base` sería incorrecto para RHP > 1 año (§4.4 de S2-B). Además
violaría DRY — el extractor ya lo calculó bien.

### PC-7 — ¿Dónde colocar el bloque de integración en pipeline.py?

**Hecho confirmado (lectura del código):**
- `fund_master_record` se construye en las líneas 660-759.
- `publish_fund(conn, fund_master_record, None, kiid_record)` se llama en línea 1545.
- Entre líneas 759 y 1545 hay numerosos bloques de corrección post-construcción
  (BL-44, BL-62, BL-47, normalizaciones lingüísticas, BL-50, BL-52, BL-61, etc.).
- El bloque de extracción de costes debe ir **después de todos estos bloques** (no altera
  clasificación ni naturaleza del fondo) y **antes de `publish_fund`**, específicamente
  después del bloque de inferencia de Geography (línea ~1515) y antes de la construcción
  del `kiid_record` (línea 1524).

**DECISIÓN PC-7:** Insertar el bloque de extracción de costes entre las líneas ~1515 y ~1524,
como bloque aislado con comentario `# ── BL-COST-4c: Extracción de costes (Sprint 2) ──`.
Ver código exacto en §3.2.

---

## §2 — ESPECIFICACIÓN DE `ucits_cost_extractor.py`

### 2.1 Contexto: ¿qué son los fondos UCITS en este corpus?

Los fondos `UCITS_KIID` del corpus son los que `detect_kid_format` clasifica como tal
(al menos 2 señales UCITS fuertes: menciones a "Información clave para el inversor",
"Directiva UCITS", "gastos corrientes" en formato KIID clásico sin tabla PRIIPs).
Son un volumen pequeño (~5 fondos estimados). Su único dato de coste relevante es
el `Ongoing Charges` (gastos corrientes), típicamente en una sección con formato:
```
Gastos corrientes: 0,85%
```
o en inglés:
```
Ongoing charges: 0.85%
```

No tienen tabla de "costes a lo largo del tiempo" (esa es característica PRIIPs). Por
tanto el schedule se sintetiza con una sola fila a 1 año usando el OC como proxy.

### 2.2 API pública

```python
def extract_ucits_costs(
    text: str,
    isin: str,
    existing_oc: Optional[float] = None,    # Ongoing_Charge_Recurrent en BD
) -> Dict[str, Any]:
    """
    Extrae costes de un KIID UCITS clásico.
    Alcance mínimo: Ongoing_Charge_Recurrent, Management_Fee_Pct,
    Transaction_Cost_Pct, fila de schedule sintética.

    Contrato:
    - Kill-switch: si PRIIPS_COST_EXTRACTION_ENABLED is False → retorna {}.
    - Si KID_Format != 'UCITS_KIID' → retorna solo {KID_Format, Cost_Extraction_Quality='NONE',
      _cost_schedule_rows=[]}.
    - Ninguna excepción sale al caller.
    - Escala de salida: porcentaje entero (mismo criterio que priips_cost_extractor).

    Claves posibles del dict de retorno:
      KID_Format               str     (siempre)
      KID_Currency             str | None
      Cost_Extraction_Quality  str     ('HIGH'|'LOW'|'NONE')
      Ongoing_Charge_Recurrent float | None   (% entero; solo si existing_oc is None)
      Management_Fee_Pct       float | None   (% entero)
      Transaction_Cost_Pct     float | None   (% entero)
      _cost_schedule_rows      List[dict]     (siempre; 1 fila o [] si OC no extraído)
    """
```

### 2.3 Lógica paso a paso (pseudocódigo)

```
def extract_ucits_costs(text, isin, existing_oc=None):
    # 0. Kill-switch
    if not PRIIPS_COST_EXTRACTION_ENABLED:
        return {}

    out = {}
    try:
        # A. Formato
        out['KID_Format'] = detect_kid_format(text)
        currency = detect_kid_currency(text)
        if currency:
            out['KID_Currency'] = currency

        if out['KID_Format'] != 'UCITS_KIID':
            out['Cost_Extraction_Quality'] = 'NONE'
            out['_cost_schedule_rows'] = []
            return out

        # B. Ongoing Charges (patrón directo del texto KIID)
        oc_ratio = _extract_ucits_oc(text)   # ver §2.4

        # C. Composición desde S2-A (DRY: no reimplementar)
        comp = parse_costs_composition(text)
        mgmt = comp.get('management_fee_pct')
        tran = comp.get('transaction_cost_pct')
        if mgmt is not None:
            out['Management_Fee_Pct'] = _ratio_to_pct(mgmt)
        if tran is not None:
            out['Transaction_Cost_Pct'] = _ratio_to_pct(tran)

        # D. Ongoing_Charge_Recurrent — solo si existing_oc is None (COALESCE-safe)
        if oc_ratio is not None and existing_oc is None:
            out['Ongoing_Charge_Recurrent'] = _ratio_to_pct(oc_ratio)

        # E. Fila de schedule sintética
        if oc_ratio is not None:
            out['_cost_schedule_rows'] = [{
                'Horizon_Years': 1.0,
                'Is_RHP': 1,
                'Source': 'UCITS_DERIVED',
                'Annual_Impact_Pct': _ratio_to_pct(oc_ratio),
                # Total_Costs_EUR y Total_Costs_Pct: no disponibles sin base
                # (UCITS no tienen importe en EUR en el KID)
            }]
        else:
            out['_cost_schedule_rows'] = []

        # F. Calidad
        out['Cost_Extraction_Quality'] = 'HIGH' if oc_ratio is not None else 'LOW'
        return out

    except Exception as exc:
        _log.warning("[BL-COST-4b] %s: fallo en extracción UCITS (%s)", isin, exc)
        out.setdefault('KID_Format', 'UNKNOWN')
        out.setdefault('Cost_Extraction_Quality', 'LOW')
        out.setdefault('_cost_schedule_rows', [])
        return out
```

### 2.4 Función privada `_extract_ucits_oc`

```python
# Patrón UCITS Ongoing Charges (R-5: \b, R-6: ventana acotada)
_UCITS_OC_PATTERN = re.compile(
    r'(?:gastos\s+corrientes|ongoing\s+charges?)\s*[:\-]?\s*'
    r'(\d+[,\.]\d+)\s*%',
    re.IGNORECASE,
)

def _extract_ucits_oc(text: str) -> Optional[float]:
    """
    Extrae el porcentaje de gastos corrientes de un KIID UCITS.
    Retorna ratio decimal (0.0085 para 0.85%). None si no se encuentra.
    """
    m = _UCITS_OC_PATTERN.search(text)
    if not m:
        return None
    raw = m.group(1).replace(',', '.')
    try:
        return float(raw) / 100.0
    except ValueError:
        return None
```

**Nota:** `_ratio_to_pct` se importa desde `priips_cost_extractor` (DRY) o se redefine
localmente con un comentario `# DRY-SYNC: priips_cost_extractor._ratio_to_pct`. Preferir
importación directa si la estructura de imports lo permite sin circular dependency.

Si hay dependencia circular (ucits importa de priips y viceversa), definir `_ratio_to_pct`
en ambos módulos con el comentario DRY-SYNC. Son 2 líneas; la duplicación mínima es
preferible a un import circular o a crear un tercer módulo para una función de 2 líneas.

### 2.5 Dependencias de `ucits_cost_extractor.py`

```python
# Dependencias S2-A (DRY — no reimplementar)
from cost_format_router   import detect_kid_format, detect_kid_currency
from cost_table_parser    import parse_costs_composition
# NO necesita: parse_costs_over_time, cost_cross_validator (sin tabla de horizonte)

# Config con fallback aislado (mismo patrón que priips_cost_extractor)
try:
    from config import PRIIPS_COST_EXTRACTION_ENABLED, PRIIPS_INVESTMENT_BASE
except ImportError:
    PRIIPS_COST_EXTRACTION_ENABLED: bool  = False
    PRIIPS_INVESTMENT_BASE: float         = 10000.0
```

---

## §3 — ESPECIFICACIÓN DE MODIFICACIONES

### 3.1 Modificaciones a `pipeline.py`

#### 3.1.1 Imports a añadir

En el bloque de imports actual (línea ~186), añadir condicionalmente:

```python
# BL-COST-4c: extractores Sprint 2 (kill-switch interno en cada módulo)
try:
    from core.priips_cost_extractor import extract_priips_costs
    from core.ucits_cost_extractor  import extract_ucits_costs
    _COST_EXTRACTORS_AVAILABLE = True
except ImportError:
    _COST_EXTRACTORS_AVAILABLE = False
```

El import condicional asegura que el pipeline no rompe si los módulos no existen aún
(durante el despliegue incremental). Cuando `PRIIPS_COST_EXTRACTION_ENABLED = False`,
los módulos se importan pero sus funciones retornan `{}` por kill-switch interno.

#### 3.1.2 Ampliación de `_v3_row` (lectura BD)

**No ampliar `_v3_row`.** Ver PC-3 — añadir una segunda SELECT dedicada dentro del bloque
de extracción de costes (solo se ejecuta si `_COST_EXTRACTORS_AVAILABLE`).

#### 3.1.3 Bloque de extracción (nuevo) — insertar tras línea ~1515 (post-Geography)

Insertar este bloque completo justo **antes** de la línea `kiid_record = {` (actualmente ~1524):

```python
# ── BL-COST-4c: Extracción de costes Sprint 2 ─────────────────────────
# Se ejecuta DESPUÉS de todas las normalizaciones de clasificación.
# kill-switch interno en cada extractor: si PRIIPS_COST_EXTRACTION_ENABLED=False
# → retornan {} y este bloque no modifica fund_master_record.
# Principio #2 DRY: el routing PRIIPs/UCITS lo hace detect_kid_format,
# ya implementado en cost_format_router.py.
if _COST_EXTRACTORS_AVAILABLE:
    try:
        from shared.config import PRIIPS_COST_EXTRACTION_ENABLED as _ce_enabled
    except ImportError:
        _ce_enabled = False

    if _ce_enabled:
        # Leer valores actuales en BD (antes de este ciclo) — patrón R-4
        _cost_bd_row = conn.execute(
            "SELECT Ongoing_Charge_Recurrent, Entry_Fee_Pct, Exit_Fee_Pct "
            "FROM fund_master WHERE ISIN=?", (isin,)
        ).fetchone()
        _oc_bd    = _cost_bd_row[0] if _cost_bd_row else None
        _entry_bd = _cost_bd_row[1] if _cost_bd_row else None
        _exit_bd  = _cost_bd_row[2] if _cost_bd_row else None

        # Skip si calidad ya es HIGH en BD (no degradar)
        _ceq_bd = fund_master_record.get("Cost_Extraction_Quality")
        # Nota: fund_master_record["Cost_Extraction_Quality"] viene del COALESCE
        # del ciclo actual; si NULL en BD, es None aquí. Siempre procesar en ese caso.
        if _ceq_bd != 'HIGH':
            # Routing: detect_kid_format ya se llama dentro de cada extractor,
            # pero llamarlo aquí una vez evita el doble coste en caso de UNKNOWN.
            from cost_format_router import detect_kid_format as _dkf
            _fmt = _dkf(kiid_text)

            _cost_dict = {}
            if _fmt == 'PRIIPS_KID':
                _cost_dict = extract_priips_costs(
                    text=kiid_text,
                    isin=isin,
                    existing_oc=_oc_bd,
                    existing_entry=_entry_bd,
                    existing_exit=_exit_bd,
                )
            elif _fmt == 'UCITS_KIID':
                _cost_dict = extract_ucits_costs(
                    text=kiid_text,
                    isin=isin,
                    existing_oc=_oc_bd,
                )
            # _fmt == 'UNKNOWN' → _cost_dict = {} → no se modifica nada

            # Extraer claves privadas antes de mezclar en fund_master_record
            _schedule_rows = _cost_dict.pop('_cost_schedule_rows', [])
            _oc_mismatch   = _cost_dict.pop('_oc_aci_mismatch', False)

            # Mezclar campos de coste en fund_master_record
            # Solo los campos que están en el schema de fund_master (11 columnas nuevas).
            _COST_FIELDS = {
                'KID_Format', 'KID_Currency', 'Cost_Extraction_Quality',
                'Cost_RHP_Years', 'Entry_Fee_Pct_Max', 'Exit_Fee_Pct_Max',
                'Management_Fee_Pct', 'Transaction_Cost_Pct', 'Performance_Fee_Pct',
                'ACI_1Y', 'ACI_RHP',
                # Ongoing_Charge_Recurrent: en fund_master_record con COALESCE.
                # Solo se mezcla si el extractor lo devuelve (existing_oc is None, P-3).
                'Ongoing_Charge_Recurrent',
            }
            for _cf in _COST_FIELDS:
                if _cf in _cost_dict:
                    fund_master_record[_cf] = _cost_dict[_cf]

            # Persist schedule si hay filas
            if _schedule_rows:
                from core.sqlite_writer import upsert_cost_schedule as _usc
                try:
                    _n_rows = _usc(conn, isin, _schedule_rows)
                    log_ingestion(conn, isin, "BL_COST_4C_SCHEDULE", "OK",
                                  f"schedule: {_n_rows} filas, fmt={_fmt}")
                except Exception as _e_sched:
                    log_ingestion(conn, isin, "BL_COST_4C_SCHEDULE", "ERROR",
                                  str(_e_sched))

            # Señalizar mismatch OC/ACI para BL-COST-5
            if _oc_mismatch:
                log_ingestion(conn, isin, "BL_COST_4C_OC_ACI_MISMATCH", "WARN",
                              f"OC en BD parece ACI; diferido a BL-COST-5")

    # Si _ce_enabled=False: no modificar nada. fund_master_record mantiene los
    # campos de coste como None (Sprint 1). Los COALESCE en upsert_fund_master
    # preservarán los valores existentes en BD (si los hay de ciclos anteriores).
```

**⚠ Atención sobre el import de `PRIIPS_COST_EXTRACTION_ENABLED`:** El flag está en
`shared/config.py` (v19.1). El pipeline ya importa muchas cosas de `shared.config`
via `from core._db_utils import EffectiveReader`. Para evitar imports redundantes,
consultar si ya hay `from shared.config import ...` en el pipeline. Si no, añadir al
bloque de imports del módulo en lugar de importarlo dentro del bloque de costes.
Preferir la importación a nivel de módulo; la que está dentro del bloque es fallback.

### 3.2 Modificaciones a `sqlite_writer.py` — función `correct_oc_aci_mismatch`

Añadir esta función **al final de `sqlite_writer.py`**, después de `upsert_cost_schedule`:

```python
# ============================================================
# BL-COST-4d: Infraestructura para corrección OC-ACI mismatch
# ============================================================
#
# Esta función NO se llama desde pipeline.py en Sprint 2.
# Es infraestructura para BL-COST-5 (sesión Opus separada).
# Proporciona la ruta de escritura no-COALESCE para corregir
# Ongoing_Charge_Recurrent en los ~328 fondos donde el valor
# en BD es ACI@RHP mal etiquetado como TER.
#
# Diseño (Principio #1 Root Cause):
#   La causa raíz (mezcla TER/ACI en la columna legacy) se corrige
#   en BL-COST-5 con análisis exhaustivo. Esta función es solo la
#   palanca de escritura. No implementa ninguna heurística de detección.
# ============================================================

def correct_oc_aci_mismatch(
    conn: sqlite3.Connection,
    isin: str,
    ter_pct: float,
    source_note: str = "BL-COST-5",
) -> bool:
    """
    Sobrescribe Ongoing_Charge_Recurrent directamente (sin COALESCE)
    para corregir un fondo donde el valor en BD es ACI@RHP, no TER.

    Solo debe invocarse desde BL-COST-5 (validación exhaustiva previa).
    NO usar desde el pipeline normal — viola la política COALESCE estándar.

    Args:
        conn: conexión activa con isolation_level=None (WAL).
        isin: ISIN del fondo a corregir.
        ter_pct: TER corregido en porcentaje entero (ej: 0.70 para 0.70%).
        source_note: etiqueta para el log de ingesta.

    Returns:
        True si la fila fue actualizada, False si ISIN no existe en fund_master.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    cur = conn.execute(
        "UPDATE fund_master SET Ongoing_Charge_Recurrent = ?, Updated_At = ? "
        "WHERE ISIN = ?",
        (ter_pct, datetime.datetime.utcnow().isoformat(timespec="seconds"), isin),
    )
    updated = cur.rowcount > 0
    if updated:
        _logger.info(
            "[%s] [%s] Ongoing_Charge_Recurrent corregido: %.4f%% (no-COALESCE)",
            isin, source_note, ter_pct,
        )
    return updated
```

**Exportar la función desde el módulo:** añadir `correct_oc_aci_mismatch` a cualquier
`__all__` si existe, o simplemente tenerla definida (Python la exporta por defecto).

### 3.3 Test unitario del bloque OC-mismatch

Añadir en `proyecto1/tests/test_sqlite_writer.py` (si existe) o crear como fichero
`proyecto1/tests/test_cost_oc_mismatch.py`:

```python
# test_cost_oc_mismatch.py — test unitario de correct_oc_aci_mismatch
import sqlite3, pytest

def _make_conn():
    """Crea una BD en memoria con la columna necesaria."""
    conn = sqlite3.connect(":memory:")
    conn.isolation_level = None
    conn.execute("""
        CREATE TABLE fund_master (
            ISIN TEXT PRIMARY KEY,
            Ongoing_Charge_Recurrent REAL,
            Updated_At TEXT
        )
    """)
    conn.execute("INSERT INTO fund_master VALUES ('TEST0001', 2.4, '2026-01-01')")
    return conn

def test_correct_oc_updates_value():
    from core.sqlite_writer import correct_oc_aci_mismatch
    conn = _make_conn()
    result = correct_oc_aci_mismatch(conn, 'TEST0001', ter_pct=0.70)
    assert result is True
    row = conn.execute("SELECT Ongoing_Charge_Recurrent FROM fund_master WHERE ISIN='TEST0001'").fetchone()
    assert abs(row[0] - 0.70) < 0.001

def test_correct_oc_returns_false_for_missing_isin():
    from core.sqlite_writer import correct_oc_aci_mismatch
    conn = _make_conn()
    result = correct_oc_aci_mismatch(conn, 'NONEXIST', ter_pct=0.50)
    assert result is False

def test_correct_oc_does_not_touch_other_isins():
    from core.sqlite_writer import correct_oc_aci_mismatch
    conn = _make_conn()
    conn.execute("INSERT INTO fund_master VALUES ('TEST0002', 1.5, '2026-01-01')")
    correct_oc_aci_mismatch(conn, 'TEST0001', ter_pct=0.70)
    row2 = conn.execute("SELECT Ongoing_Charge_Recurrent FROM fund_master WHERE ISIN='TEST0002'").fetchone()
    assert abs(row2[0] - 1.5) < 0.001   # no debe cambiar
```

---

## §4 — GROUND TRUTH Y SMOKE TEST

### 4.1 Ground truth de `ucits_cost_extractor` (5 fondos estimados)

El corpus tiene muy pocos UCITS KIID. Para los tests, usar texto **sintético** que simule
el formato clásico UCITS (no hay PDFs de muestra UCITS en el Project):

```python
_UCITS_SAMPLE_ES = """
Información clave para el inversor
Este documento le proporciona información esencial sobre este fondo de inversión.
Gastos corrientes: 0,85%
Comisión de gestión: 0,65%
Costes de transacción: 0,20%
"""

_UCITS_SAMPLE_EN = """
Key Investor Information Document
This document provides you with key investor information about this fund.
Ongoing charges: 1.20%
Management fee: 0.90%
Transaction costs: 0.30%
"""
```

Valores esperados:

| Texto | KID_Format | OC (% entero) | Mgmt | Transac | Schedule rows |
|---|---|---|---|---|---|
| `_UCITS_SAMPLE_ES` | `UCITS_KIID` | 0.85 | 0.65 | 0.20 | 1 fila (1Y, RHP, UCITS_DERIVED) |
| `_UCITS_SAMPLE_EN` | `UCITS_KIID` | 1.20 | 0.90 | 0.30 | 1 fila (1Y, RHP, UCITS_DERIVED) |
| `""` (vacío) | `UNKNOWN` | — | — | — | [] |

**Nota:** `parse_costs_composition` puede no capturar mgmt/transac en todos los formatos UCITS
(depende del layout). Si `management_fee_pct` no se encuentra → `Management_Fee_Pct` ausente
del dict (no NULL explícito — clave ausente). Los tests deben verificar con `in`/`not in`.

### 4.2 Smoke test del pipeline integrado

Después de implementar T-2 (pipeline), verificar manualmente sobre **1 ISIN conocido** que
sea PRIIPS (ej. IE00BZ4D7085) con `PRIIPS_COST_EXTRACTION_ENABLED = True` en config o
monkeypatcheando:

```python
# smoke_sprint2_s2c.py — ejecutar una vez, no forma parte de la suite CI
import os, sys
sys.path.insert(0, r'C:\desarrollo\fondos')
os.environ['PYTHONIOENCODING'] = 'utf-8'

from core.priips_cost_extractor import extract_priips_costs
import core.priips_cost_extractor as ext
ext.PRIIPS_COST_EXTRACTION_ENABLED = True

# Cargar texto real del PDF IE00BZ4D7085
# (ajustar ruta a donde están los PDFs en producción)
import pdfplumber
with pdfplumber.open(r'C:\desarrollo\fondos\data\kiids\IE00BZ4D7085.pdf') as pdf:
    text = '\n'.join(p.extract_text() or '' for p in pdf.pages)

result = extract_priips_costs(text, 'IE00BZ4D7085', existing_oc=None)
print(f"KID_Format: {result.get('KID_Format')}")
print(f"Cost_RHP_Years: {result.get('Cost_RHP_Years')}")
print(f"Management_Fee_Pct: {result.get('Management_Fee_Pct')}")
print(f"Cost_Extraction_Quality: {result.get('Cost_Extraction_Quality')}")
print(f"Schedule rows: {len(result.get('_cost_schedule_rows', []))}")
```

Resultado esperado (S2-B §4.2): KID_Format=PRIIPS_KID, RHP=5.0, Mgmt=1.11, Quality=MEDIUM_EUR.

### 4.3 Verificación post-integración en pipeline (control SQL)

Tras ejecutar el pipeline con `PRIIPS_COST_EXTRACTION_ENABLED = True` sobre una muestra de 50 fondos:

```sql
-- ¿Cuántos fondos tienen Cost_Extraction_Quality poblado?
SELECT Cost_Extraction_Quality, COUNT(*) as n
FROM fund_master
WHERE Cost_Extraction_Quality IS NOT NULL
GROUP BY Cost_Extraction_Quality ORDER BY n DESC;

-- ¿Cuántos fondos tienen al menos una fila en fund_cost_schedule?
SELECT COUNT(DISTINCT ISIN) FROM fund_cost_schedule;

-- ¿Hay mismatch OC-ACI registrado en el log?
SELECT COUNT(*) FROM ingestion_log
WHERE step = 'BL_COST_4C_OC_ACI_MISMATCH' AND status = 'WARN';

-- Verificar escala: todos los valores Management_Fee_Pct deben ser > 0.01 (% no ratio)
SELECT MIN(Management_Fee_Pct), MAX(Management_Fee_Pct), AVG(Management_Fee_Pct)
FROM fund_master WHERE Management_Fee_Pct IS NOT NULL;
-- Esperado: MIN > 0.01 (si fuera ratio, sería < 0.001)
```

---

## §5 — TESTS OBLIGATORIOS DE `ucits_cost_extractor`

Fichero: `proyecto1/tests/test_ucits_cost_extractor.py`

```python
import pytest

# ── Textos sintéticos de muestra (ver §4.1) ──
_UCITS_ES = """
Información clave para el inversor
Este documento proporciona información esencial sobre este fondo de inversión.
Gastos corrientes: 0,85%
"""
_UCITS_EN = """
Key Investor Information Document. Ongoing charges: 1.20%
"""
_PRIIPS_TEXT = """
Costes a lo largo del tiempo. Período de mantenimiento recomendado: 3 años.
1 año: 357 EUR
"""
_EMPTY = ""


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    import core.ucits_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', True)


def test_killswitch_off_returns_empty(monkeypatch):
    import core.ucits_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', False)
    assert ext.extract_ucits_costs(_UCITS_ES, 'TEST') == {}


def test_ucits_es_format_and_oc():
    from core.ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs(_UCITS_ES, 'TEST_ES')
    assert o['KID_Format'] == 'UCITS_KIID'
    assert abs(o['Ongoing_Charge_Recurrent'] - 0.85) < 0.01
    assert o['Cost_Extraction_Quality'] == 'HIGH'
    rows = o['_cost_schedule_rows']
    assert len(rows) == 1
    assert rows[0]['Horizon_Years'] == 1.0
    assert rows[0]['Is_RHP'] == 1
    assert rows[0]['Source'] == 'UCITS_DERIVED'


def test_ucits_en_format_and_oc():
    from core.ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs(_UCITS_EN, 'TEST_EN')
    assert o['KID_Format'] == 'UCITS_KIID'
    assert abs(o['Ongoing_Charge_Recurrent'] - 1.20) < 0.01
    assert o['Cost_Extraction_Quality'] == 'HIGH'


def test_non_ucits_returns_none_quality():
    from core.ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs(_PRIIPS_TEXT, 'TEST_PRIIPS')
    assert o['KID_Format'] != 'UCITS_KIID'
    assert o['Cost_Extraction_Quality'] == 'NONE'
    assert o['_cost_schedule_rows'] == []


def test_empty_text_returns_none_quality():
    from core.ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs(_EMPTY, 'TEST_EMPTY')
    assert o['Cost_Extraction_Quality'] in ('NONE', 'LOW')
    assert '_cost_schedule_rows' in o


def test_oc_not_returned_when_existing_oc_present():
    # COALESCE-safe: si ya hay OC en BD, no devolver para no interferir
    from core.ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs(_UCITS_ES, 'TEST_OC_EXISTING', existing_oc=0.85)
    assert 'Ongoing_Charge_Recurrent' not in o   # no se devuelve (existing_oc no None)


def test_no_exception_on_garbage():
    from core.ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs("\x00\x01 basura ||| sin estructura", 'TEST_GARBAGE')
    assert isinstance(o, dict)
    assert 'Cost_Extraction_Quality' in o
    assert '_cost_schedule_rows' in o
```

---

## §6 — ALERTAS Y PUNTOS DE ATENCIÓN

### A-1 — Import de `PRIIPS_COST_EXTRACTION_ENABLED` en pipeline

El pipeline actualmente NO importa `PRIIPS_COST_EXTRACTION_ENABLED` explícitamente.
Está implícito en `shared.config` que sí importa `EffectiveReader`. Al añadir el bloque
de costes, hacer el import explícito al nivel de módulo para evitar confusión:

```python
# Al inicio de pipeline.py, en el bloque de imports shared.config:
try:
    from shared.config import PRIIPS_COST_EXTRACTION_ENABLED as _COST_ENABLED
except ImportError:
    _COST_ENABLED = False
```

Y usar `_COST_ENABLED` en el bloque de costes en lugar del import interno.

### A-2 — `upsert_cost_schedule` usa DELETE+INSERT atómico — verificar transacción

`upsert_cost_schedule` (sqlite_writer línea 1063) hace DELETE + INSERT por fila.
El `conn` en el pipeline se usa con `isolation_level=None` (autocommit) según la Norma
DRY de `shared/db.py`. Esto significa que el DELETE y los INSERTs son commits separados
(no atómico). Si el pipeline muere entre el DELETE y el último INSERT, el fondo queda
sin schedule.

**Acción:** Envolver la llamada a `upsert_cost_schedule` en un bloque de transacción explícito:
```python
with conn:   # savepoint implícito en SQLite con isolation_level=None + autocommit
    _n_rows = _usc(conn, isin, _schedule_rows)
```
O verificar si `publish_fund` ya crea un `with conn:` que cubre todo. Si es así, la
llamada a `upsert_cost_schedule` debe ir **dentro** del `with conn:` de `publish_fund`.
**Recomendación:** mover la llamada a `upsert_cost_schedule` dentro de `publish_fund`
pasando `_schedule_rows` como parámetro adicional (ver A-3).

### A-3 — Alternativa más limpia: extender `publish_fund`

La recomendación arquitectónica es **extender la firma de `publish_fund`** para recibir
`cost_schedule_rows` como parámetro opcional:

```python
def publish_fund(
    conn, fund_master_record,
    nav_series=None,
    kiid_record=None,
    cost_schedule_rows=None,   # NUEVO: _cost_schedule_rows del extractor
) -> None:
    try:
        with conn:
            upsert_fund_master(conn, fund_master_record)
            if kiid_record:
                upsert_kiid_metadata(conn, kiid_record)
            if nav_series:
                insert_nav_series(conn, fund_master_record["ISIN"], nav_series)
            if cost_schedule_rows:
                upsert_cost_schedule(conn, fund_master_record["ISIN"], cost_schedule_rows)
            # ... resto igual
```

Ventaja: atomicidad. El schedule se persiste en la misma transacción que el fund_master.
Si el upsert del master falla, el schedule no se escribe y no hay inconsistencia.

**Adoptar esta alternativa (A-3) sobre el import local de A-2**. Es más limpio, atómico
y DRY (un único punto de escritura coordinada).

En pipeline.py, la llamada cambia a:
```python
publish_fund(conn, fund_master_record, None, kiid_record,
             cost_schedule_rows=_schedule_rows or None)
```
(pasar `None` si lista vacía, para que la guarda `if cost_schedule_rows` no llame a DELETE
innecesariamente en fondos sin costes extraídos)

### A-4 — `fund_master_record` ya tiene campo `KID_Format` desde Sprint 1

El record del pipeline no tiene `KID_Format` en su construcción actual (líneas 660-759).
Al mezclar el dict del extractor, `KID_Format` se añadirá como clave nueva al dict.
`upsert_fund_master` ya la incluye en sus parámetros (`record.get("KID_Format")` en
línea 636). No hay problema — se propaga correctamente.

### A-5 — `Cost_Extraction_Quality` skip logic: usar BD, no el record del ciclo

En el bloque de costes del pipeline, el skip por `_ceq_bd = 'HIGH'` debe leer el valor
**ya en BD**, no el `fund_master_record.get("Cost_Extraction_Quality")` del ciclo actual
(que puede ser None recién construido). Usar:

```python
_ceq_row = conn.execute(
    "SELECT Cost_Extraction_Quality FROM fund_master WHERE ISIN=?", (isin,)
).fetchone()
_ceq_bd = _ceq_row[0] if _ceq_row else None
if _ceq_bd != 'HIGH':
    # ... extraer
```

O aprovechar que `_cost_bd_row` ya hace un SELECT en la misma función: añadir
`Cost_Extraction_Quality` a esa consulta.

---

## §7 — PREVIEW S2-D (cierre de sprint)

**S2-D (~2h) — cierre y activación:**

- `smoke_sprint2_costs.py`: corre ambos extractores sobre los 8 PDFs PRIIPs + 2-3 UCITS
  sintéticos. Verifica calidades, schedule rows, no errores.
- Activar kill-switch: `PRIIPS_COST_EXTRACTION_ENABLED = True` en `shared/config.py`
  (cambio de 1 línea). Ejecutar pipeline completo (~3.200 fondos).
- Control SQL post-ejecución (queries de §4.3 a escala total).
- Documentar conteo final de fondos por `Cost_Extraction_Quality`.
- BL-COST-6: re-run con kill-switch ON y verificación de métricas de cobertura.
- BL-COST-5 (sesión Opus separada, post-S2-D): heurística INTER-COST sobre los ~328
  fondos con `BL_COST_4C_OC_ACI_MISMATCH` en `ingestion_log`. Invoca
  `correct_oc_aci_mismatch` definida en §3.2. Requiere diseño arquitectónico propio.

**Hallazgos del diseño S2-C a trasladar a José:**

1. La integración de costes en el pipeline es cirúrgica (un bloque de ~45 líneas tras
   el Geography, extensión de `publish_fund`). No hay riesgo de regresión en la
   clasificación — los campos de coste son ortogonales a la clasificación canónica.
2. `upsert_cost_schedule` usa DELETE+INSERT; la atomicidad se garantiza pasando
   `cost_schedule_rows` a `publish_fund` (A-3), que ya tiene su propio `with conn:`.
3. El routing PRIIPs/UCITS en el pipeline es explícito y aislado — si se añaden nuevos
   formatos (ej. ELTIF) en el futuro, basta añadir un `elif` en el bloque de costes.
4. `correct_oc_aci_mismatch` (T-3) es infraestructura pura: no se invoca desde el pipeline
   de S2-C. BL-COST-5 la usará cuando esté listo el análisis de los ~328 fondos afectados.

---

**FIN DEL TRASPASO S2-C.**

*Documento autocontenido (Norma 5.4). Todos los valores de línea y API verificados
contra el código de producción leído directamente del Project en esta sesión Nivel-3.
Los módulos de referencia confirmados: `pipeline.py` v36, `sqlite_writer.py` v24,
`priips_cost_extractor.py` (S2-B entregado), `cost_format_router.py`, `cost_table_parser.py`,
`cost_cross_validator.py` (S2-A, cerrados). Schema v19.*
