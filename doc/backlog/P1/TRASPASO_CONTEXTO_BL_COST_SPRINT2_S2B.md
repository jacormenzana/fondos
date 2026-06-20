# TRASPASO_CONTEXTO_BL_COST_SPRINT2_S2B.md
# Especificación de implementación — Sesión S2-B: priips_cost_extractor.py
# Generado por: sesión Nivel-3 Opus (2026-05-22), a partir de PROMPT_OPUS_S2B.md
# Para: sesión Nivel-2 Sonnet — implementación directa de BL-COST-4a
# Autocontenido conforme a Norma 5.4

---

## §0 — INSTRUCCIONES PARA SONNET

Esta sesión implementa **BL-COST-4a**: el módulo `proyecto1/core/priips_cost_extractor.py`
y su suite de tests `proyecto1/tests/test_priips_cost_extractor.py`. Nada más.

### 0.1 Verificación de sanidad del config (S2-A confirmado cerrado)

`shared/config.py` está en **v19.1 en producción** (verificado en sesión Nivel-3 sobre la versión
de producción subida al repositorio, líneas 65-81). El fix de S2-A §1 **está aplicado**. Los tres
valores que el extractor necesita son correctos:

| Constante | Valor confirmado en producción (v19.1) |
|---|---|
| `COST_CROSS_VALIDATION_TOLERANCE_PCT` | `0.0005` (= 5bp) ✓ |
| `COST_SCHEDULE_SOURCE_VALUES` | 5 valores: incluye `'PRIIPS_COMPOSITION'`, `'PRIIPS_TEXT'` ✓ |
| `PRIIPS_COST_EXTRACTION_ENABLED` | `False` (constante real) ✓ |
| `PRIIPS_INVESTMENT_BASE` | `10000.0` ✓ |

**Acción del primer paso (sanidad, ~30 s, no bloqueante):** confirmar que la rama de trabajo tiene
el config v19.1 (no una copia obsoleta):
```
grep -n "COST_CROSS_VALIDATION_TOLERANCE_PCT\|PRIIPS_COST_EXTRACTION_ENABLED" shared/config.py
```
Debe mostrar `0.0005` y `PRIIPS_COST_EXTRACTION_ENABLED: bool = False`. Si por alguna razón apareciera
`0.05`, sería una copia desactualizada: hacer `git pull` / sincronizar antes de continuar. No se espera
que ocurra; S2-A está cerrado.

> Nota de procedencia: una versión anterior de este traspaso marcaba este punto como "bloqueante"
> porque la copia de `config.py` en el Project estaba desactualizada (v19.0, sin el fix). La versión
> de producción —8 líneas más larga— sí lo tiene. Corregido. No hay nada que arreglar en el config.

### 0.2 Reglas de implementación (heredadas, no negociables)

- Leer `cost_table_parser.py`, `cost_cross_validator.py`, `cost_format_router.py` **completos**
  antes de escribir el extractor. Son las dependencias directas; su API real manda sobre cualquier
  descripción en prosa de este documento (si hay conflicto, gana el código).
- **R-5**: word boundary (`\b`) en todo patrón regex nuevo del extractor (solo se añaden patrones
  para resolver RHP en texto, §2.4.B; el resto de extracción la hacen los módulos S2-A).
- **R-6**: ventanas acotadas y lazy (`.{0,200}?`) en todo patrón nuevo.
- **R-8**: AST validation tras CADA escritura:
  `python -X utf8 -c "import ast; ast.parse(open('proyecto1/core/priips_cost_extractor.py',encoding='utf-8').read())"`
- Sin efectos secundarios en import: el módulo no abre ficheros, no crea conexiones, no lee BD
  a nivel de módulo. Solo define constantes, patrones compilados y funciones.
- **Manejo de excepciones blindado**: ninguna excepción sale al caller. `extract_priips_costs`
  envuelve su cuerpo en try/except; ante fallo retorna el dict parcial acumulado hasta el punto
  de fallo + `Cost_Extraction_Quality='LOW'` + `KID_Format` y `KID_Currency` si ya se calcularon.
- **Kill-switch**: primera línea ejecutable de `extract_priips_costs` consulta
  `PRIIPS_COST_EXTRACTION_ENABLED`. Si `False` → retorna `{}` inmediatamente (dict vacío, sin claves).
- **DRY (Principio #2)**: el extractor NO reimplementa parsing de tablas, ni cross-validation, ni
  detección de formato. Orquesta las funciones S2-A. La única lógica nueva propia es:
  resolución de RHP numérico desde texto, conversión de escala ratio→%, ensamblado del dict de
  retorno, construcción de `_cost_schedule_rows` y cálculo de `Cost_Extraction_Quality`.

### 0.3 Orden de implementación dentro de la sesión

1. Verificar config (§0.1).
2. Leer las 3 dependencias S2-A completas.
3. Escribir `priips_cost_extractor.py` (esqueleto: imports + constantes + firmas).
4. AST OK.
5. Implementar funciones privadas (§2.3) una a una, AST tras cada una.
6. Implementar `extract_priips_costs` (§2.4).
7. AST OK.
8. Escribir `test_priips_cost_extractor.py` (§5) con el ground truth del §4.
9. Ejecutar tests; iterar hasta verde.
10. Smoke import: `python -X utf8 -c "from core.priips_cost_extractor import extract_priips_costs"`.

### 0.4 Lo que NO se toca en esta sesión

Ver §6 (lista explícita). En resumen: NO `pipeline.py`, NO `sqlite_writer.py`,
NO `classify_utils.py`, NO `schema_fondos.sql` (salvo la migración mínima opcional de §1.7
si José la aprueba), NO `kiid_parser.py`.

---

## §1 — RESOLUCIÓN DE LOS PROBLEMAS ABIERTOS P-1 … P-7

Cada problema cierra con una **DECISIÓN** vinculante para la implementación. El razonamiento es
breve; lo vinculante es la decisión.

### P-1 — Resolución de RHP en `fund_cost_schedule` ⚠ BLOQUEANTE

**Hecho confirmado contra el schema real** (`schema_fondos.sql` líneas 121-134):
`fund_cost_schedule` tiene `PRIMARY KEY (ISIN, Horizon_Years)` y `CHECK (Horizon_Years > 0 AND Horizon_Years <= 50)`.
No admite `-1.0`. `cost_table_parser.parse_costs_over_time()` devuelve `horizon_years=-1.0`
cuando `is_rhp=True`.

**DECISIÓN P-1: Opción B (resolver RHP a su valor numérico antes de construir las filas).**

- El extractor resuelve primero `Cost_RHP_Years` (valor numérico real del RHP) leyendo el texto
  (función `_extract_rhp_years`, §2.3). Esto es necesario de todos modos porque `Cost_RHP_Years`
  es un campo de retorno obligatorio.
- Al construir `_cost_schedule_rows`, toda fila con `is_rhp=True` (o `horizon_years==-1.0`) usa
  `Horizon_Years = Cost_RHP_Years` y `Is_RHP = 1`.
- Si `Cost_RHP_Years` no se pudo resolver (None), esa fila RHP **se descarta** de
  `_cost_schedule_rows` (no se puede insertar sin PK válida) y se registra en log. Las filas con
  horizonte numérico explícito (1Y, 5Y…) sí se insertan.
- **Colisión de PK**: si el RHP coincide con un horizonte ya presente (p. ej. RHP=1Y y existe la
  columna "1 año"), las dos filas tendrían la misma `Horizon_Years`. Regla: si ya existe una fila
  con ese `Horizon_Years`, **fusionar** marcando `Is_RHP=1` en la existente y completando los
  campos vacíos; NO añadir una fila duplicada. Esto ocurre en 3 de los 8 fondos muestra
  (IE00B45H7020 RHP=1Y, LU0135992385 RHP=1Y, FR0000989626 RHP=0.25 única columna).

**Razón:** no requiere migración de schema (cumple la restricción), y `Cost_RHP_Years` ya hay que
extraerlo. La Opción C (cambiar schema) se reserva como fallback documentado en §1.7.

### P-2 — Cálculo de `Cost_Extraction_Quality`

**DECISIÓN P-2:** criterios cerrados en la tabla del §3. Resumen de las dudas del prompt:

- **`MEDIUM_CROSS` aplica solo a discrepancia LEVE (5bp–50bp)** con ambos valores presentes.
- **Discrepancia GRAVE (>50bp)** → el `validate_pct_eur` devuelve `validated_pct=None`; en ese caso
  el campo afectado no es fiable. Si el ACI_RHP (el valor que ancla la calidad) cae en discrepancia
  grave → `Cost_Extraction_Quality='LOW'` (no `MEDIUM_CROSS`). Ver §3 para la regla exacta.
- La calidad se evalúa sobre el **dato ancla**: el ACI del RHP (preferente) o el ACI_1Y. Es el
  número que P3 usa para scoring de coste. Los fees de composición influyen en `LOW` vs `NONE`
  pero no elevan por encima de lo que permita el ancla.

### P-3 — `Ongoing_Charge_Recurrent` cuando el KID reporta ACI en vez de TER

**Hecho confirmado en los PDFs muestra:** el caso paradigmático es IE0032875985 — la tabla
"composición" da el TER real (mgmt 0.49% + transaction 0.21% = **0.70%**), mientras que la tabla
"costes a lo largo del tiempo" da ACI@3Y = **2.4%**. El valor `2.4%` en BD (`Ongoing_Charge`
legacy) es ACI@RHP mal etiquetado como TER. Son magnitudes distintas y no comparables.

**Hecho confirmado sobre escala legacy:** `cost_format_signals.py:291` usa la heurística
`oc_pct = oc_db * 100.0 if oc_db < 0.5 else oc_db`. Esto demuestra que la columna legacy
`Ongoing_Charge_Recurrent` contiene una **mezcla de escalas** (unos fondos en ratio 0.007, otros
en % 0.70). Esta deuda de escala es real y afecta a la decisión de sobrescritura.

**DECISIÓN P-3:**
1. El extractor calcula un **TER reconstruido** = `Management_Fee_Pct + Transaction_Cost_Pct`
   (ambos de `parse_costs_composition`, en su escala de salida; ver P-4 para la escala final).
   El TER reconstruido es el coste recurrente "puro", sin amortización de one-offs.
2. El extractor devuelve `Ongoing_Charge_Recurrent` (clave en el dict) **solo cuando**:
   - se ha podido reconstruir el TER (al menos `Management_Fee_Pct` presente), **Y**
   - se detecta que el valor existente en BD es probablemente ACI (no TER). La señal es:
     `existing_oc` no None **Y** `abs(existing_oc_normalizado - ACI_RHP) <= 0.10pp` **Y**
     `existing_oc_normalizado` difiere del TER reconstruido en `> 0.30pp`.
     (Es decir: el valor en BD se parece al ACI y no al TER → estaba mal etiquetado.)
3. **Mecanismo de sobrescritura (clave):** `sqlite_writer.py` aplica COALESCE a
   `Ongoing_Charge_Recurrent`, por lo que devolver un valor nuevo NO sobrescribe un valor existente.
   Por tanto, en S2-B el extractor **no puede** forzar la sobrescritura por sí mismo. La decisión es:
   - El extractor devuelve `Ongoing_Charge_Recurrent` (TER reconstruido) **únicamente cuando
     `existing_oc is None`** (caso COALESCE-compatible: rellena un hueco).
   - Cuando `existing_oc` no es None pero se detecta mezcla TER/ACI (regla del punto 2), el extractor
     **NO** devuelve `Ongoing_Charge_Recurrent` (porque COALESCE lo ignoraría y daría falsa sensación
     de corrección). En su lugar, añade la clave de diagnóstico `_oc_aci_mismatch: True` al dict de
     retorno y lo registra en log con tag `[BL-COST-4a][OC-ACI]`. La corrección efectiva de esos
     ~328 fondos se hará en **BL-COST-5** (regla INTER-COST con ruta de escritura no-COALESCE),
     que requiere su propia sesión Opus (ver S2-A §11).
4. El TER reconstruido SÍ se expone siempre como dato derivado a través de las columnas nuevas
   `Management_Fee_Pct` y `Transaction_Cost_Pct` (que sí se pueblan vía COALESCE sobre NULL).

**Razón (Principio #1):** sobrescribir vía un parche en el extractor sería tratar el síntoma; la
columna seguiría mezclando escalas y conceptos. La separación limpia es: columnas nuevas = TER puro
desagregado (fuente de verdad nueva); `Ongoing_Charge_Recurrent` legacy = se sanea en BL-COST-5 con
ruta de escritura dedicada. S2-B solo rellena huecos y marca los mismatches.

### P-4 — Escala de valores ⚠ DECISIÓN CRÍTICA

**Hecho confirmado contra el schema real:**
```
Entry_Fee_Pct_Max    CHECK (... <= 25)
Exit_Fee_Pct_Max     CHECK (... <= 25)
Management_Fee_Pct   CHECK (... <= 10)
Transaction_Cost_Pct CHECK (... <= 5)
Performance_Fee_Pct  CHECK (... <= 30)
ACI_1Y               CHECK (... <= 50)
ACI_RHP              CHECK (... <= 25)
```
Si estos valores fueran ratio decimal (0.0525), los límites permitirían 2500%/1000%/etc., absurdo.
**Conclusión inequívoca: las 11 columnas nuevas `*_Pct` / `ACI_*` se almacenan como PORCENTAJE
ENTERO** (5.25 significa 5.25%, no 0.0525).

**Hecho confirmado sobre las dependencias S2-A:** `cost_table_parser._extract_pct_from_cell`
devuelve **ratio decimal** (`val/100.0`): "5,25%" → `0.0525`. `cost_cross_validator.validate_pct_eur`
opera **en ratio decimal** (`implied = eur/base`, base=10000 → 510/10000 = 0.051) y su
`validated_pct` es ratio.

**DECISIÓN P-4:**
1. **Capa interna del extractor: todo en RATIO DECIMAL.** El extractor recibe ratios de
   `cost_table_parser`, hace cross-validation con `cost_cross_validator` en ratio, y mantiene ratio
   en todas sus variables internas. Esto evita reconvertir antes de validar y mantiene una sola escala
   de trabajo.
2. **Capa de salida (frontera con BD): convertir ratio → porcentaje entero** multiplicando por 100,
   SOLO para las claves que van a columnas con CHECK de porcentaje:
   `Entry_Fee_Pct_Max, Exit_Fee_Pct_Max, Management_Fee_Pct, Transaction_Cost_Pct,
   Performance_Fee_Pct, ACI_1Y, ACI_RHP`.
   Función única `_ratio_to_pct(x) -> round(x*100, 4)` (DRY).
3. **`Ongoing_Charge_Recurrent`**: esta columna legacy NO tiene CHECK de escala y su contenido es
   mixto (P-3). Para no agravar la deuda, el extractor devuelve `Ongoing_Charge_Recurrent` en la
   **misma escala que el resto de columnas nuevas: porcentaje entero** (0.70 = 0.70%), y lo documenta
   en el log. BL-COST-5 unificará la escala legacy completa. Justificación: como solo se devuelve
   cuando `existing_oc is None` (P-3 punto 3), no hay riesgo de mezclar con un valor previo de otra
   escala en la misma celda.
4. **`_cost_schedule_rows`**: `Total_Costs_Pct` y `Annual_Impact_Pct` en la tabla
   `fund_cost_schedule` — el schema no les pone CHECK de escala. **Decisión: porcentaje entero**
   (coherencia con las columnas nuevas de `fund_master`). `Total_Costs_EUR` es importe absoluto (sin
   conversión).
5. **Verificación operativa que Sonnet DEBE registrar (no bloquea):** ejecutar contra BD real
   `SELECT AVG(Entry_Fee_Pct), MAX(Entry_Fee_Pct) FROM fund_master WHERE Entry_Fee_Pct > 0;`.
   Si MAX > 1 → la columna legacy está en %. Si MAX < 1 → en ratio. Reportar el resultado a José en
   el cierre de sesión (informa BL-COST-5; no cambia P-4 porque P-4 fija la escala de las columnas
   NUEVAS, cuyos CHECK son inequívocos).

### P-5 — Fondos con RHP < 1 año (FR0000989626, RHP = 3 meses)

**Hecho confirmado:** FR0000989626 tiene "Período de mantenimiento recomendado: 3 meses" y una única
columna de costes "después de 3 meses". IE00B45H7020 tiene RHP=1 año exacto (no < 1).

**DECISIÓN P-5:**
- `Cost_RHP_Years = 0.25` para 3 meses (3/12). `_extract_rhp_years` resuelve meses→años con
  `round(meses/12, 4)` (idéntico criterio a `cost_table_parser._parse_horizon_years`, DRY conceptual).
- `ACI_RHP` SIEMPRE se puebla (es el ancla de calidad). Para FR0000989626 = 0.54%.
- `ACI_1Y` queda **NULL** cuando RHP < 1 año (no existe horizonte 1Y en el documento). Confirmado:
  FR0000989626 no tiene columna de 1 año.
- En `fund_cost_schedule`: la fila del RHP de 3 meses se almacena como
  `Horizon_Years=0.25, Is_RHP=1`. Cumple el CHECK (`0.25 > 0 AND <= 50`).

### P-6 — Comportamiento cuando DLA2 y texto plano difieren

**Hecho relevante:** `cost_table_parser` ya implementa la prioridad internamente: si el texto
contiene `'|||'` intenta DLA2 primero y solo cae a texto plano si DLA2 no produce resultados
(ver `parse_costs_over_time` líneas 642-650 y `parse_costs_composition` 683-690). Es decir, el
extractor recibe ya el resultado de la fuente prioritaria; no recibe ambas a la vez.

**DECISIÓN P-6:**
- El extractor **confía en la prioridad ya resuelta por `cost_table_parser`** (D-S2-3:
  tabla over_time > tabla composition > texto libre). NO reimplementa la elección de fuente.
- El campo `source` que devuelve `parse_costs_over_time` (`'DLA2'` | `'PLAIN_TEXT'`) se usa solo
  para informar `Cost_Extraction_Quality` en el límite inferior: si la única fuente disponible fue
  `'PLAIN_TEXT'` y NO hubo cross-validation posible → tope de calidad `LOW` (ver §3).
- No se intenta "comparar DLA2 vs PLAIN_TEXT" porque el parser no expone ambos simultáneamente;
  hacerlo requeriría llamar a los parsers internos `_parse_*`, lo cual viola la API pública y DRY.
  Si en el futuro se necesita esa comparación, es una mejora del parser (S2-A), no del extractor.

### P-7 — `Source` en `fund_cost_schedule`

**Hecho confirmado contra el código de producción (config v19.1 + schema):**
- `schema_fondos.sql` línea 134: `CHECK (Source IN ('PRIIPS_COSTS_OVER_TIME', 'UCITS_DERIVED', 'MANUAL'))` — **3 valores**.
- `config.py` v19.1 línea 65-68: `COST_SCHEDULE_SOURCE_VALUES` tiene **5 valores** (añade
  `'PRIIPS_COMPOSITION'` y `'PRIIPS_TEXT'`).
- **Hay un desajuste vigente tuple(5) ↔ CHECK(3).** Es deliberado: los 2 valores extra del tuple son
  para metadatos de extracción, NO para la columna `Source` del schedule. Las filas de schedule vienen
  siempre de la tabla "costes a lo largo del tiempo" (`parse_costs_over_time`), nunca de la composición.

⚠ **Riesgo concreto para Sonnet:** NO usar `'PRIIPS_COMPOSITION'` ni `'PRIIPS_TEXT'` como `Source` de
una fila de `_cost_schedule_rows` solo porque estén en el tuple del config. El CHECK del schema los
rechaza → `IntegrityError` en el upsert. El tuple del config y el CHECK del schema NO son la misma
lista de valores permitidos para esta columna.

**DECISIÓN P-7:**
- **Toda fila de `_cost_schedule_rows` usa `Source = 'PRIIPS_COSTS_OVER_TIME'`.** Es correcto porque
  el schedule (horizonte → coste total / ACI) procede exclusivamente de la tabla over_time. La
  composición NO genera filas de schedule (genera fees escalares en `fund_master`).
- **No se requiere migración del CHECK de `fund_cost_schedule` en S2-B.** Los valores
  `'PRIIPS_COMPOSITION'` y `'PRIIPS_TEXT'` del tuple del config se usan, si acaso, para metadatos
  internos / `Cost_Extraction_Quality`, NO como `Source` de filas de schedule.
- El extractor **valida defensivamente** antes de emitir cada fila: si por cualquier razón el
  `Source` calculado no está en `('PRIIPS_COSTS_OVER_TIME','UCITS_DERIVED','MANUAL')`, descarta la
  fila y lo registra en log (evita un `IntegrityError` en `upsert_cost_schedule`). La constante
  `_SCHEDULE_SOURCE_ALLOWED` del módulo (§2.1) refleja el CHECK del schema (3 valores), NO el tuple
  del config.

### §1.7 — Cambio de schema (SOLO si José lo aprueba; por defecto NO se hace)

P-1 y P-7 se resuelven sin tocar el schema. Esta subsección documenta la alternativa por si en
revisión se decide lo contrario:
- **Para P-1 (Opción C):** `ALTER TABLE fund_cost_schedule` no permite cambiar un CHECK en SQLite;
  requeriría export→drop→recreate (patrón de migración del proyecto). Innecesario dada la Decisión B.
- **Para P-7:** ampliar el CHECK de `Source` requeriría la misma migración. Innecesario dada la
  Decisión P-7.
- **Recomendación Nivel-3: NO migrar en S2-B.** Mantener el scope mínimo. Si BL-COST-5 necesita más
  valores de `Source`, se planifica allí con su backup correspondiente.

---

## §2 — ESPECIFICACIÓN COMPLETA DE `priips_cost_extractor.py`

Ruta: `proyecto1/core/priips_cost_extractor.py` (mismo nivel que `pipeline.py`, `cost_table_parser.py`).

### 2.1 Imports y constantes de módulo

```python
# proyecto1/core/priips_cost_extractor.py
# -*- coding: utf-8 -*-
import re
import logging
from typing import Optional, List, Dict, Any

# Dependencias S2-A (mismo paquete core/):
from core.cost_format_router  import detect_kid_format, detect_kid_currency
from core.cost_table_parser   import parse_costs_over_time, parse_costs_composition
from core.cost_cross_validator import validate_pct_eur, ValidationResult

# Config (con fallback aislado, mismo patrón que cost_cross_validator.py):
# Los 3 símbolos existen en config v19.1 (producción) → el import normal tendrá éxito.
# El fallback solo actúa en entornos aislados (p.ej. tests sin config en path).
try:
    from config import (
        PRIIPS_INVESTMENT_BASE,
        COST_CROSS_VALIDATION_TOLERANCE_PCT,
        PRIIPS_COST_EXTRACTION_ENABLED,
    )
except ImportError:
    PRIIPS_INVESTMENT_BASE = 10000.0
    COST_CROSS_VALIDATION_TOLERANCE_PCT = 0.0005   # = config v19.1
    PRIIPS_COST_EXTRACTION_ENABLED = False         # = config v19.1

_log = logging.getLogger(__name__)

# Source único para filas de schedule (P-7)
_SCHEDULE_SOURCE = 'PRIIPS_COSTS_OVER_TIME'
_SCHEDULE_SOURCE_ALLOWED = ('PRIIPS_COSTS_OVER_TIME', 'UCITS_DERIVED', 'MANUAL')

# Patrón RHP numérico (R-5 word boundary, R-6 ventana lazy acotada).
# Captura el número y la unidad tras "período de mantenimiento recomendado: X años/meses"
# y la variante inglesa. Se busca en una ventana de 60 chars tras la etiqueta.
_RHP_VALUE_PATTERN = re.compile(
    r'(?:per[ií]odo\s+de\s+mantenimiento\s+recomendado|recommended\s+holding\s+period)'
    r'\s*[:\-]?\s*'
    r'(\d+)\s*(a[ñn]os?|years?|mes(?:es)?|months?)\b',
    re.IGNORECASE,
)

# Umbral de discrepancia grave (espejo de cost_cross_validator._SEVERE_DISCREPANCY_THRESHOLD)
_SEVERE_DISCREPANCY_THRESHOLD = 0.005  # 50bp

# Umbrales heurística OC/ACI mismatch (P-3), en ratio decimal
_OC_ACI_NEAR_PP   = 0.0010   # 0.10pp: el valor en BD "se parece" al ACI_RHP
_OC_TER_FAR_PP    = 0.0030   # 0.30pp: el valor en BD difiere del TER reconstruido
```

### 2.2 Interfaz pública (firma final)

```python
def extract_priips_costs(
    text: str,
    isin: str,
    existing_oc: Optional[float] = None,    # Ongoing_Charge_Recurrent actual en BD (escala BD)
    existing_entry: Optional[float] = None, # Entry_Fee_Pct actual en BD
    existing_exit: Optional[float] = None,  # Exit_Fee_Pct actual en BD
) -> Dict[str, Any]:
    """
    Extrae los campos de coste de un KID PRIIPs a partir del texto concatenado
    (Raw_KIID_Text + DLA2_Table_Text). Orquesta los módulos S2-A; no reimplementa parsing.

    Contrato:
      - Respeta PRIIPS_COST_EXTRACTION_ENABLED (kill-switch). Si False → retorna {}.
      - Ninguna excepción sale al caller (try/except global → dict parcial + quality 'LOW').
      - Devuelve SOLO las claves extraídas con éxito (claves ausentes = no extraído),
        EXCEPTO Cost_Extraction_Quality, KID_Format y _cost_schedule_rows, que
        siempre están presentes.
      - Escala de salida: porcentaje entero para *_Pct/ACI_* (P-4). EUR absoluto sin convertir.

    Claves posibles del dict de retorno:
      KID_Format               str            (siempre)
      KID_Currency             str | None
      Cost_Extraction_Quality  str            (siempre; uno de los 6 valores)
      Cost_RHP_Years           float | None
      Entry_Fee_Pct_Max        float | None   (% entero)
      Exit_Fee_Pct_Max         float | None   (% entero)
      Management_Fee_Pct       float | None   (% entero)
      Transaction_Cost_Pct     float | None   (% entero)
      Performance_Fee_Pct      float | None   (% entero)
      ACI_1Y                   float | None   (% entero)
      ACI_RHP                  float | None   (% entero)
      Ongoing_Charge_Recurrent float | None   (% entero; SOLO si existing_oc is None y TER reconstruido)
      _cost_schedule_rows      List[dict]     (siempre; puede ser []; para upsert_cost_schedule)
      _oc_aci_mismatch         bool           (solo si se detecta mezcla TER/ACI con existing_oc no None)
    """
```

### 2.3 Funciones privadas necesarias

| Función | Firma | Propósito |
|---|---|---|
| `_ratio_to_pct` | `(x: Optional[float]) -> Optional[float]` | Convierte ratio→% entero: `round(x*100,4)`. None→None. (P-4) |
| `_extract_rhp_years` | `(text: str) -> Optional[float]` | Resuelve el RHP numérico desde texto vía `_RHP_VALUE_PATTERN`; años directos, meses→`round(m/12,4)`. None si no se halla. (P-1, P-5) |
| `_pick_aci_for_horizon` | `(rows: List[dict], target_years: Optional[float], want_rhp: bool) -> Optional[float]` | De la lista de `parse_costs_over_time`, selecciona el `aci_pct` (ratio) de la fila 1Y, o de la fila RHP. Tolerancia de match de horizonte ±0.01. |
| `_pick_eur_for_horizon` | `(rows, target_years, want_rhp) -> Optional[float]` | Igual pero devuelve `total_cost_eur`. |
| `_cross_validate_fee` | `(pct_ratio, eur, base) -> ValidationResult` | Wrapper directo de `validate_pct_eur` (azúcar para legibilidad; opcional). |
| `_build_schedule_rows` | `(rows, rhp_years, isin) -> List[dict]` | Construye `_cost_schedule_rows` resolviendo RHP (P-1), fusionando colisiones de PK, validando Source (P-7), convirtiendo escala (P-4). |
| `_detect_oc_aci_mismatch` | `(existing_oc, oc_norm, ter_recon_ratio, aci_rhp_ratio) -> bool` | Heurística P-3 punto 2. |
| `_assess_quality` | `(...) -> str` | Devuelve uno de los 6 valores de calidad según §3. |
| `_norm_existing_oc` | `(existing_oc) -> Optional[float]` | Normaliza el OC legacy a ratio para comparar: `oc*1` si `oc>=0.5` se asume % → `/100`; si `<0.5` se asume ratio. Espejo de `cost_format_signals.py:291` invertido a ratio. |

> Nota DRY: `_norm_existing_oc` replica la heurística de escala de `cost_format_signals.py:291`
> pero en sentido "a ratio". Añadir comentario `# DRY-SYNC: cost_format_signals.py:291 (oc_pct)`.

**Cuerpo explícito de `_detect_oc_aci_mismatch` (P-3 punto 2, sin ambigüedad):**

```python
def _detect_oc_aci_mismatch(existing_oc, oc_norm, ter_recon_ratio, aci_rhp_ratio):
    """True si el OC legacy en BD parece ser ACI (no TER): cerca del ACI_RHP y lejos del TER.
       Todos los argumentos comparables en RATIO decimal. Conservador: ante cualquier None → False."""
    if existing_oc is None or oc_norm is None:
        return False
    if ter_recon_ratio is None or aci_rhp_ratio is None:
        return False
    near_aci = abs(oc_norm - aci_rhp_ratio) <= _OC_ACI_NEAR_PP   # se parece al ACI
    far_ter  = abs(oc_norm - ter_recon_ratio) >  _OC_TER_FAR_PP  # difiere del TER
    return near_aci and far_ter
```

> Es deliberadamente conservador: si falta el ACI_RHP fiable o el TER reconstruido, NO marca mismatch
> (evita falsos positivos en fondos legítimamente baratos, como advierte S2-A §11). Para IE0032875985
> con `existing_oc=2.4` (% → ratio 0.024), `aci_rhp_ratio≈0.024`, `ter_recon≈0.0070`: near_aci=True
> (|0.024−0.024|≤0.001), far_ter=True (|0.024−0.007|>0.003) → **mismatch True**. Correcto.

### 2.4 Lógica paso a paso de `extract_priips_costs` (pseudocódigo comentado)

```
def extract_priips_costs(text, isin, existing_oc=None, existing_entry=None, existing_exit=None):

    # --- 0. KILL-SWITCH (primera línea ejecutable) ---
    if not PRIIPS_COST_EXTRACTION_ENABLED:
        return {}

    out = {}
    try:
        # --- A. Formato y moneda (siempre se intenta) ---
        out['KID_Format']   = detect_kid_format(text)        # 'PRIIPS_KID'|'UCITS_KIID'|'UNKNOWN'
        currency = detect_kid_currency(text)
        if currency:
            out['KID_Currency'] = currency

        # Si no es PRIIPS_KID, este extractor no aplica:
        if out['KID_Format'] != 'PRIIPS_KID':
            out['Cost_Extraction_Quality'] = 'NONE'
            out['_cost_schedule_rows'] = []
            return out
            # (el routing real lo hace pipeline.py en S2-C; aquí defensa interna)

        # --- B. RHP numérico (necesario para P-1 y como campo de retorno) ---
        rhp_years = _extract_rhp_years(text)        # float | None
        if rhp_years is not None:
            out['Cost_RHP_Years'] = rhp_years

        # --- C. Tabla "costes a lo largo del tiempo" ---
        over_time = parse_costs_over_time(text)     # List[dict], aci_pct/total_cost_eur en ratio/EUR
        schedule_source_used = None
        if over_time:
            schedule_source_used = over_time[0].get('source')  # 'DLA2' | 'PLAIN_TEXT'

        # ACI 1Y (None si RHP<1 o no hay columna 1Y) — P-5
        aci_1y_ratio  = _pick_aci_for_horizon(over_time, target_years=1.0, want_rhp=False)
        eur_1y        = _pick_eur_for_horizon(over_time, target_years=1.0, want_rhp=False)

        # ACI RHP (ancla de calidad) — preferir fila is_rhp; si no, fila == rhp_years
        aci_rhp_ratio = _pick_aci_for_horizon(over_time, target_years=rhp_years, want_rhp=True)
        eur_rhp       = _pick_eur_for_horizon(over_time, target_years=rhp_years, want_rhp=True)

        # Cross-validation del ancla (ACI_RHP) y de 1Y:
        vr_rhp = validate_pct_eur(aci_rhp_ratio, eur_rhp, base=PRIIPS_INVESTMENT_BASE)
        vr_1y  = validate_pct_eur(aci_1y_ratio,  eur_1y,  base=PRIIPS_INVESTMENT_BASE)

        # El ACI fiable = validated_pct (puede ser None si discrepancia grave)
        aci_rhp_final = vr_rhp.validated_pct if vr_rhp.status != 'NONE' else None
        aci_1y_final  = vr_1y.validated_pct  if vr_1y.status  != 'NONE' else None

        if aci_1y_final is not None:
            out['ACI_1Y']  = _ratio_to_pct(aci_1y_final)
        if aci_rhp_final is not None:
            out['ACI_RHP'] = _ratio_to_pct(aci_rhp_final)

        # --- D. Tabla "composición de los costes" ---
        comp = parse_costs_composition(text)        # dict, todos en ratio decimal

        # Entry/Exit MAX (techo declarado): preferir *_max_pct; fallback a *_fee_pct
        entry_max = comp.get('entry_fee_max_pct', comp.get('entry_fee_pct'))
        exit_max  = comp.get('exit_fee_max_pct',  comp.get('exit_fee_pct'))
        if entry_max is not None:
            out['Entry_Fee_Pct_Max'] = _ratio_to_pct(entry_max)
        if exit_max is not None:
            out['Exit_Fee_Pct_Max']  = _ratio_to_pct(exit_max)

        mgmt = comp.get('management_fee_pct')
        tran = comp.get('transaction_cost_pct')
        perf = comp.get('performance_fee_pct')
        if mgmt is not None:
            out['Management_Fee_Pct']   = _ratio_to_pct(mgmt)
        if tran is not None:
            out['Transaction_Cost_Pct'] = _ratio_to_pct(tran)
        if perf is not None:
            out['Performance_Fee_Pct']  = _ratio_to_pct(perf)

        # --- E. TER reconstruido y gestión de Ongoing_Charge_Recurrent (P-3) ---
        ter_recon_ratio = None
        if mgmt is not None:
            ter_recon_ratio = mgmt + (tran or 0.0)

        if ter_recon_ratio is not None:
            if existing_oc is None:
                # COALESCE-compatible: rellenar hueco con TER puro
                out['Ongoing_Charge_Recurrent'] = _ratio_to_pct(ter_recon_ratio)
            else:
                oc_norm = _norm_existing_oc(existing_oc)   # a ratio
                if _detect_oc_aci_mismatch(existing_oc, oc_norm, ter_recon_ratio, aci_rhp_final):
                    out['_oc_aci_mismatch'] = True
                    _log.info("[BL-COST-4a][OC-ACI] %s: BD OC parece ACI (%.4f) != TER recon (%.4f); "
                              "diferido a BL-COST-5", isin, oc_norm or -1, ter_recon_ratio)
                # si no hay mismatch y existing_oc no es None → no se toca (COALESCE)

        # --- F. _cost_schedule_rows (P-1, P-7, P-4) ---
        out['_cost_schedule_rows'] = _build_schedule_rows(over_time, rhp_years, isin)

        # --- G. Calidad (§3) ---
        out['Cost_Extraction_Quality'] = _assess_quality(
            vr_rhp=vr_rhp, vr_1y=vr_1y,
            aci_rhp_final=aci_rhp_final, aci_1y_final=aci_1y_final,
            comp=comp, over_time=over_time,
            schedule_source_used=schedule_source_used,
        )
        return out

    except Exception as exc:
        _log.warning("[BL-COST-4a] %s: fallo en extracción (%s); retorno parcial LOW", isin, exc)
        out.setdefault('Cost_Extraction_Quality', 'LOW')
        out.setdefault('_cost_schedule_rows', [])
        return out
```

#### Detalle de `_build_schedule_rows` (P-1 + fusión de PK + P-7 + P-4)

```
def _build_schedule_rows(rows, rhp_years, isin):
    by_horizon = {}   # Horizon_Years -> dict de fila
    for r in rows:
        hy   = r.get('horizon_years')
        rhp  = bool(r.get('is_rhp'))
        # Resolver RHP a su valor numérico (P-1, Decisión B)
        if rhp or hy == -1.0:
            if rhp_years is None:
                _log.info("[BL-COST-4a] %s: fila RHP sin Cost_RHP_Years resuelto; fila descartada", isin)
                continue
            hy = rhp_years
        if hy is None or not (0 < hy <= 50):
            continue   # CHECK de schema
        eur = r.get('total_cost_eur')
        aci = r.get('aci_pct')   # ratio
        row = {
            'Horizon_Years': round(hy, 4),
            'Is_RHP': 1 if (rhp or hy == rhp_years) else 0,
            'Source': _SCHEDULE_SOURCE,
        }
        if eur is not None:
            row['Total_Costs_EUR'] = eur
        if aci is not None:
            row['Annual_Impact_Pct'] = _ratio_to_pct(aci)
        # Total_Costs_Pct: si hay EUR, implied = eur/base en % entero
        if eur is not None:
            row['Total_Costs_Pct'] = _ratio_to_pct(eur / PRIIPS_INVESTMENT_BASE)
        # Fusión de colisión de PK (P-1): mismo Horizon_Years
        key = row['Horizon_Years']
        if key in by_horizon:
            prev = by_horizon[key]
            prev['Is_RHP'] = max(prev['Is_RHP'], row['Is_RHP'])
            for k, v in row.items():
                prev.setdefault(k, v)
        else:
            by_horizon[key] = row
    # Validación defensiva de Source (P-7)
    return [r for r in by_horizon.values() if r['Source'] in _SCHEDULE_SOURCE_ALLOWED]
```

### 2.5 Tabla de mapeo: campo_retorno → fuente → función S2-A

| Campo retorno | Origen | Función S2-A | Transformación en extractor |
|---|---|---|---|
| `KID_Format` | texto | `detect_kid_format` | directo |
| `KID_Currency` | texto | `detect_kid_currency` | directo |
| `Cost_RHP_Years` | texto | (propia) `_extract_rhp_years` | meses→años |
| `ACI_1Y` | over_time 1Y | `parse_costs_over_time` + `validate_pct_eur` | ratio→%; NULL si RHP<1 |
| `ACI_RHP` | over_time RHP | `parse_costs_over_time` + `validate_pct_eur` | ratio→%; ancla calidad |
| `Entry_Fee_Pct_Max` | composición | `parse_costs_composition` (`entry_fee_max_pct`\|`entry_fee_pct`) | ratio→% |
| `Exit_Fee_Pct_Max` | composición | `parse_costs_composition` (`exit_fee_max_pct`\|`exit_fee_pct`) | ratio→% |
| `Management_Fee_Pct` | composición | `parse_costs_composition` (`management_fee_pct`) | ratio→% |
| `Transaction_Cost_Pct` | composición | `parse_costs_composition` (`transaction_cost_pct`) | ratio→% |
| `Performance_Fee_Pct` | composición | `parse_costs_composition` (`performance_fee_pct`) | ratio→% |
| `Ongoing_Charge_Recurrent` | mgmt+transaction | `parse_costs_composition` | ratio→%; solo si `existing_oc is None` |
| `_cost_schedule_rows` | over_time | `parse_costs_over_time` | `_build_schedule_rows` |
| `Cost_Extraction_Quality` | derivado | `validate_pct_eur` + presencia | `_assess_quality` |

---

## §3 — CRITERIOS DE `Cost_Extraction_Quality` (sin ambigüedad)

La calidad se evalúa sobre el **dato ancla** = ACI del RHP (`vr_rhp`); si el RHP no produjo ancla,
se usa ACI_1Y (`vr_1y`). Reglas evaluadas en orden; se asigna el PRIMER valor cuyo criterio se cumple.

`_assess_quality` implementa exactamente esta tabla:

| # | Condición (evaluada en orden) | Valor asignado |
|---|---|---|
| 1 | No hay tabla over_time NI composición (ambos vacíos) | `NONE` |
| 2 | `KID_Format != 'PRIIPS_KID'` | `NONE` (ya cubierto en paso A del flujo) |
| 3 | Ancla tiene **ambos** (pct y EUR) y `vr.status == 'OK'` (≤5bp) | `HIGH` |
| 4 | Ancla tiene **ambos** y `vr.status == 'DISCREPANCY'` con `validated_pct is not None` (5–50bp) | `MEDIUM_CROSS` |
| 5 | Ancla `vr.status == 'EUR_ONLY'` (solo EUR, sin %) | `MEDIUM_EUR` |
| 6 | Ancla `vr.status == 'PCT_ONLY'` (solo %, sin EUR) | `MEDIUM_PCT` |
| 7 | Discrepancia GRAVE (`validated_pct is None` con ambos presentes), o única fuente fue `'PLAIN_TEXT'`, o solo hay composición sin over_time, o datos parciales | `LOW` |
| 8 | (fallback de excepción, ver flujo §2.4) | `LOW` |

**Aclaraciones que cierran las dudas del prompt:**
- `MEDIUM_CROSS` aplica **solo** a discrepancia leve (5–50bp). NUNCA a discrepancia grave.
- Discrepancia grave (>50bp) → `LOW` (regla 7), porque `validate_pct_eur` devolvió `validated_pct=None`
  y el ancla no es fiable.
- Si el ancla del RHP no existe pero sí el de 1Y, se evalúa la tabla con `vr_1y` como ancla.
- "Datos parciales" (regla 7) = hay alguna extracción (algún fee o algún ACI) pero el ancla no
  alcanzó ninguno de los estados 3–6. Mejor `LOW` que `NONE` porque algo se obtuvo.

> Implementación sugerida de `_assess_quality`: recibir `vr_rhp` y `vr_1y`; elegir
> `anchor = vr_rhp if vr_rhp.status != 'NONE' else vr_1y`; aplicar la tabla sobre `anchor.status`
> y `anchor.validated_pct`; aplicar reglas 1/7 con la presencia de `over_time`/`comp` y
> `schedule_source_used`.

---

## §4 — GROUND TRUTH DE LOS 8 ISINs MUESTRA

**Verificado en esta sesión Nivel-3** leyendo el texto extraído real de cada KID (los ficheros
`<ISIN>.pdf` del Project son ZIP con `N.txt` por página + imágenes; el texto de costes es legible).
Todos son **PRIIPS_KID**.

> ⚠ **Correcciones importantes respecto a PROMPT_OPUS_S2B.md** (el prompt contenía varios errores;
> estos valores verificados PREVALECEN):
> - **LU1084165304 (Fidelity):** el prompt decía `total_cost_eur=510 USD`. **FALSO.** 510 USD es el
>   *Entry fee* (5,25%). El total cost 1Y real = **713 USD**; 5Y = **1.904 USD**. RHP = **5 años**.
> - **IE00B45H7020 (BlackRock):** el prompt decía EUR, total 10, 2 columnas, mgmt 0.10%/transac 0.02%.
>   **Parcialmente FALSO.** Moneda = **USD**. Una sola columna (RHP=**1 año**). Total = **12 USD**,
>   ACI = **0.1%**. mgmt **0.10%** (10 USD), transac **0.02%** (2 USD) — esto sí es correcto. El
>   prompt confundió el ACI 0.10% con el de Polar.
> - **IE00BZ4D7085 (Polar):** el prompt decía RHP=1Y, mgmt 0.10%, transac 0.02%, total 12.
>   **FALSO.** RHP = **5 años**, 2 columnas (1Y=153 EUR, 5Y=1.360 EUR), ACI 1,5%/1,6%, mgmt
>   **1,11%** (111 EUR), transac **0,42%** (42 EUR). Los valores "12/0.10%/0.02%" eran de BlackRock.
> - **LU0135992385 (Schroders):** el prompt decía RHP=5Y. **FALSO.** RHP = **1 año**, 1 columna,
>   total EUR 30, ACI 0,3%, mgmt 0,29%, transac 0,01%.
> - **LU1502282632 (Candriam):** el prompt decía RHP=5Y. **FALSO.** RHP = **6 años**, columnas
>   1Y(576 EUR)/6Y(3.878 EUR), ACI 5,8%/3,1%, entry 3,50% máx (Hasta 350 EUR).
> - **FR0000989626 (Groupama):** total 1ª columna = **54 €** (no 50). 50 € es el *Entry fee* (0,50%).
> - **IE00BJGT6Q17 (PIMCO):** RHP = **3 años** (el prompt no lo indicaba). Total 1Y=357/3Y=650 EUR.

### 4.1 ⚠ HALLAZGO NIVEL-3 — los 8 KIDs muestra son TEXTO PLANO con parser limitado

**Verificado ejecutando los parsers S2-A reales sobre el texto extraído de cada ISIN.** Los 8
documentos NO contienen separador DLA2 `'|||'`; el parser usa la ruta `PLAIN_TEXT`. Esta ruta tiene
limitaciones confirmadas que el extractor S2-B y sus tests DEBEN asumir:

1. **Columnas duplicadas (bug del parser plano):** en fondos con 2 horizontes, `parse_costs_over_time`
   devuelve 2 filas pero con `total_cost_eur` y `aci_pct` IDÉNTICOS (los de la 1ª columna), porque la
   ruta plana no separa columnas. Ej. IE00BJGT6Q17 → ambas filas `total_cost_eur=357.0, aci_pct=0.036`
   (la 3Y real es 650/2.1%, pero el parser no la capta). **El valor de la 2ª columna NO es fiable.**
2. **ACI frecuentemente None:** en LU1084165304, IE0032875985, IE00BZ4D7085, LU1502282632 el parser
   devuelve `aci_pct=None` (el layout rompe la fila de ACI). Solo 4/8 capturan ACI.
3. **`detect_kid_currency` falla en 5/8** (devuelve None) porque el texto plano rompe "10.000 EUR" o
   usa variantes "EUR 10 000" / "USD 10.000". Solo IE0032875985, IE00B45H7020, IE00BZ4D7085 → EUR/USD.
4. **Falsos positivos de composición:** IE00B45H7020 → `exit_fee_pct=0.001` (captura espuria del ACI
   0.1%) y `transaction_cost_eur=10` (en realidad es el management EUR). El extractor no los corrige;
   son límite conocido de la ruta plana.
5. **`management_fee_eur` a veces captura el %** (1.45, 1.88, 1.94) en lugar del EUR real — irrelevante
   para S2-B porque el extractor usa `management_fee_pct`, no el EUR.

**Implicación operativa:** la calidad alta (HIGH/MEDIUM_CROSS) solo se logra cuando DLA2 está activo.
Sobre estos 8 PDFs en texto plano, lo esperable es **MEDIUM_EUR** (ACI None pero EUR presente) o
**HIGH** (los 4 que capturan ACI a 1Y con cross-validation OK). Esto es coherente con que el motivo
del proyecto DLA sea precisamente arreglar la extracción en dos columnas. Los tests asertan el output
REAL del parser, no el ideal del documento.

### 4.2 Tabla maestra — OUTPUT REAL del extractor (lo que asertan los tests)

Valores tal como salen del extractor (escala % entero para `*_Pct`, EUR nativo para schedule).
`KID_Format = PRIIPS_KID` para los 8. `Cost_RHP_Years` lo resuelve `_extract_rhp_years` desde texto
(independiente del bug de columnas). Donde el parser no capta el dato → **NULL** (clave ausente).

| ISIN | KID_Currency | Cost_RHP_Years | ACI_1Y | ACI_RHP | Entry_Fee_Pct_Max | Mgmt_Pct | Transac_Pct | Esperado quality |
|---|---|---|---|---|---|---|---|---|
| IE00BJGT6Q17 | None | 3.0 | 3.6 | 3.6¹ | NULL² | 1.45 | 0.15 | HIGH |
| LU1084165304 | None | 5.0 | NULL | NULL | 5.25 | 1.88 | 0.22 | MEDIUM_EUR |
| IE0032875985 | EUR | 3.0 | NULL | NULL | NULL² | 0.49 | NULL | MEDIUM_EUR |
| IE00B45H7020 | USD | 1.0 | 0.1 | 0.1 | NULL² | 0.10 | 0.02 | HIGH |
| FR0000989626 | None | 0.25 | NULL³ | 0.54 | 0.50 | 0.11 | NULL | HIGH |
| LU0135992385 | None | 1.0 | 0.3 | 0.3 | NULL² | NULL⁴ | NULL⁴ | HIGH |
| IE00BZ4D7085 | EUR | 5.0 | NULL | NULL | 5.0 | 1.11 | 0.42 | MEDIUM_EUR |
| LU1502282632 | None | 6.0 | NULL | NULL | 3.50 | 1.94 | 0.08 | MEDIUM_EUR |

Notas:
- ¹ IE00BJGT6Q17: por el bug de columnas, la fila "3 años" hereda el ACI de 1Y (0.036). Como RHP=3
  y RHP≠1, el extractor toma `ACI_RHP` del `aci_pct` de la fila RHP directamente (§4.4) → 3.6 (valor
  heredado, NO el 2.1% real del documento). **Esto es una limitación conocida del texto plano**, no
  un fallo del extractor. Documentar. Con DLA2 saldría 2.1.
- ² Entry/Exit con solo EUR sin % en la celda → el parser NO devuelve `*_pct`/`*_max_pct` → NULL.
  IE0032875985 entry "497 EUR" sin %, IE00BJGT6Q17 exit "197 EUR" sin %, IE00B45H7020/LU0135992385
  "No cobramos" → 0 o ausente. Confirmado contra parser real.
- ³ FR0000989626 RHP<1 → no hay columna 1Y → `ACI_1Y` NULL (P-5). Correcto.
- ⁴ LU0135992385: `parse_costs_composition` devuelve `{}` (el layout con `[0.29%]` entre corchetes
  no casa los patrones de etiqueta) → mgmt/transac NULL. Confirmado.

### 4.3 `_cost_schedule_rows` — OUTPUT REAL esperado

Formato `(Horizon_Years, Is_RHP, Total_Costs_EUR)`. Recordatorio: por el bug de columnas, la 2ª fila
hereda el EUR de la 1ª en texto plano. El extractor NO lo corrige (no es su responsabilidad; es del
parser). Los tests asertan el output real:

| ISIN | Filas reales (texto plano) | Filas ideales (con DLA2, referencia) |
|---|---|---|
| IE00BJGT6Q17 | (1.0,0,357), (3.0,1,357←bug) | (1.0,0,357),(3.0,1,650) |
| LU1084165304 | (1.0,0,713), (5.0,1,713←bug) | (1.0,0,713),(5.0,1,1904) |
| IE0032875985 | (1.0,0,567), (3.0,1,567←bug) | (1.0,0,567),(3.0,1,693) |
| IE00B45H7020 | (1.0,1,12) — fusión PK RHP=1Y | idem |
| FR0000989626 | (0.25,1,54) | idem |
| LU0135992385 | (1.0,1,30) — fusión PK RHP=1Y | idem |
| IE00BZ4D7085 | (1.0,0,153), (5.0,1,153←bug) | (1.0,0,153),(5.0,1,1360) |
| LU1502282632 | (1.0,0,576), (6.0,1,576←bug) | (1.0,0,576),(6.0,1,3878) |

> **Acción para Sonnet:** los tests asertan la columna "Filas reales". Documentar el `←bug` como
> limitación conocida de la ruta plana. Cuando DLA2 esté activo en producción, las filas serán las
> "ideales". Añadir un test marcado `@pytest.mark.skip(reason="requiere DLA2")` con el ground truth
> ideal para regresión futura, o un TODO en el módulo.

### 4.4 ⚠ HALLAZGO DE DISEÑO (Nivel-3) — cross-validation solo válida a 1 año

**Confirmado con los datos reales:** el importe EUR de "costes totales" es **acumulado** sobre el
horizonte; el ACI ("incidencia anual") es **anualizado**. Solo coinciden cuando horizonte = 1 año
(EUR_total ≈ ACI × base). Para RHP > 1 año, `EUR_total / base ≠ ACI` (es ~ ACI × años).

**Por tanto, la cross-validation `validate_pct_eur(ACI_RHP, EUR_RHP)` SOLO es correcta si RHP=1 año.**
Para RHP>1, cruzar ACI_RHP con EUR_RHP produce discrepancia grave espuria.

**DECISIÓN de diseño (refina P-2/P-6) — Sonnet DEBE implementar así:**
- El **ancla de cross-validation es el horizonte de 1 año** cuando existe (`vr_1y`), porque a 1 año
  EUR y ACI son comparables. La calidad se evalúa sobre `vr_1y` si existe; si no existe horizonte 1Y
  (caso RHP<1, p.ej. FR0000989626 a 3 meses), se evalúa sobre el RHP corto (donde EUR≈ACI×0.25, que
  tampoco es 1:1 — ver abajo).
- Para FR0000989626 (RHP=3m): EUR=54, ACI=0.54% (0.0054). EUR/base=0.0054 → coincide con ACI. Aquí
  el "total" del periodo de 3 meses resulta ≈ ACI porque el horizonte es <1 y el documento reporta
  el ACI como impacto sobre el periodo. **Funciona como HIGH.** `[VERIFICAR_EN_DISCO]`.
- `ACI_RHP` (el campo) se sigue poblando con el % del RHP (anualizado, tal cual el documento),
  **sin** depender de cross-validation con el EUR acumulado. Solo se le aplica `validate_pct_eur`
  cuando RHP=1 (entonces es legítimo).
- **Implementación concreta:** en `_assess_quality`, usar como ancla `vr_1y`. `ACI_RHP` se toma del
  `aci_pct` de la fila RHP directamente (ratio→%), sin pasar por `validated_pct` salvo que
  `rhp_years == 1.0`. Ajustar el flujo §2.4 paso C: `aci_rhp_final = aci_rhp_ratio` (directo) salvo
  `rhp_years==1.0` donde se usa `vr_rhp.validated_pct`.

Este hallazgo NO estaba en el prompt y es material para la corrección de scoring de P3. Documentarlo
en el cierre de sesión para José.

---

## §5 — TESTS OBLIGATORIOS (`proyecto1/tests/test_priips_cost_extractor.py`)

**Estrategia de carga del texto real:** los ficheros `<ISIN>.pdf` del Project son archivos ZIP que
contienen `N.txt` (texto por página) + `N.jpeg` + `manifest.json`. El test debe leer el texto
concatenando los `.txt`. Helper sugerido:

```python
import os, zipfile

KIDS_DIR = os.environ.get('KIDS_DIR', r'C:\desarrollo\fondos\data\kiids')  # ajustar a disco real

def load_kid_text(isin):
    """Concatena el texto de todas las páginas del KID. Soporta dos layouts en disco:
       (a) ZIP <isin>.pdf con N.txt dentro; (b) PDF real (usar pdfplumber)."""
    path = os.path.join(KIDS_DIR, f'{isin}.pdf')
    # Si es ZIP (caso del Project actual):
    if zipfile.is_zipfile(path):
        parts = []
        with zipfile.ZipFile(path) as z:
            txts = sorted(n for n in z.namelist() if n.endswith('.txt'))
            for n in txts:
                parts.append(z.read(n).decode('utf-8', errors='replace'))
        return '\n'.join(parts)
    # Si es PDF real:
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return '\n'.join((p.extract_text() or '') for p in pdf.pages)
```

> **`[VERIFICAR_EN_DISCO]`:** confirmar en disco si los KIDs están como ZIP o PDF real y ajustar
> `KIDS_DIR`. El helper soporta ambos. En el entorno del Project son ZIP; en producción pueden ser PDF.

### 5.1 Tests con el kill-switch

```python
def test_killswitch_off_returns_empty(monkeypatch):
    import core.priips_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', False)
    assert ext.extract_priips_costs("cualquier texto", "TEST") == {}

def test_killswitch_on_processes(monkeypatch):
    import core.priips_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', True)
    out = ext.extract_priips_costs(load_kid_text('IE00B45H7020'), 'IE00B45H7020')
    assert out.get('KID_Format') == 'PRIIPS_KID'
    assert 'Cost_Extraction_Quality' in out
    assert '_cost_schedule_rows' in out
```

### 5.2 Tests de funciones privadas

```python
def test_ratio_to_pct():
    from core.priips_cost_extractor import _ratio_to_pct
    assert _ratio_to_pct(0.0525) == 5.25
    assert _ratio_to_pct(0.001)  == 0.1
    assert _ratio_to_pct(None) is None

def test_extract_rhp_years_anios():
    from core.priips_cost_extractor import _extract_rhp_years
    assert _extract_rhp_years("Período de mantenimiento recomendado: 3 años") == 3.0
    assert _extract_rhp_years("Recommended Holding Period: 5 years") == 5.0

def test_extract_rhp_years_meses():
    from core.priips_cost_extractor import _extract_rhp_years
    assert _extract_rhp_years("Período de mantenimiento recomendado: 3 meses") == 0.25

def test_extract_rhp_years_ausente():
    from core.priips_cost_extractor import _extract_rhp_years
    assert _extract_rhp_years("texto sin rhp") is None
```

### 5.3 Tests por ISIN (todos con kill-switch ON vía fixture/autouse)

Cada test afirma SOLO lo verificado contra el parser real (§4.2/§4.3). Donde un campo es NULL en la
tabla, el test verifica que la clave está AUSENTE.

```python
import pytest

@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    import core.priips_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', True)

def _run(isin):
    from core.priips_cost_extractor import extract_priips_costs
    return extract_priips_costs(load_kid_text(isin), isin)

# --- IE00BJGT6Q17: PIMCO, RHP 3Y, ACI capturado (HIGH) ---
def test_ie00bjgt6q17():
    o = _run('IE00BJGT6Q17')
    assert o['KID_Format'] == 'PRIIPS_KID'
    assert o['Cost_RHP_Years'] == 3.0
    assert o['ACI_1Y'] == 3.6
    assert o['Management_Fee_Pct'] == 1.45
    assert o['Transaction_Cost_Pct'] == 0.15
    assert o['Cost_Extraction_Quality'] == 'HIGH'
    rows = {(r['Horizon_Years'], r['Is_RHP']): r for r in o['_cost_schedule_rows']}
    assert (1.0, 0) in rows and (3.0, 1) in rows
    assert rows[(1.0,0)]['Total_Costs_EUR'] == 357.0

# --- LU1084165304: Fidelity USD, RHP 5Y, ACI None (MEDIUM_EUR) ---
def test_lu1084165304():
    o = _run('LU1084165304')
    assert o['KID_Format'] == 'PRIIPS_KID'
    assert o['Cost_RHP_Years'] == 5.0
    assert 'ACI_1Y' not in o          # parser no capta ACI en este layout
    assert o['Entry_Fee_Pct_Max'] == 5.25
    assert o['Management_Fee_Pct'] == 1.88
    assert o['Cost_Extraction_Quality'] == 'MEDIUM_EUR'

# --- IE0032875985: PIMCO, RHP 3Y, OC-ACI mismatch paradigmático ---
def test_ie0032875985():
    o = _run('IE0032875985')
    assert o['KID_Currency'] == 'EUR'
    assert o['Cost_RHP_Years'] == 3.0
    assert o['Management_Fee_Pct'] == 0.49
    # TER reconstruido = mgmt (transac None aquí) ; OC mismatch si existing_oc≈2.4
    # (ver test dedicado 5.4)

# --- IE00B45H7020: BlackRock USD, RHP 1Y, fusión PK, HIGH ---
def test_ie00b45h7020():
    o = _run('IE00B45H7020')
    assert o['KID_Currency'] == 'USD'
    assert o['Cost_RHP_Years'] == 1.0
    assert o['ACI_RHP'] == 0.1
    assert o['Cost_Extraction_Quality'] == 'HIGH'
    rows = o['_cost_schedule_rows']
    assert len(rows) == 1
    assert rows[0]['Horizon_Years'] == 1.0 and rows[0]['Is_RHP'] == 1   # fusión PK

# --- FR0000989626: Groupama, RHP 3 meses, ACI_1Y NULL, HIGH ---
def test_fr0000989626():
    o = _run('FR0000989626')
    assert o['Cost_RHP_Years'] == 0.25
    assert 'ACI_1Y' not in o            # RHP<1 → sin columna 1Y (P-5)
    assert o['ACI_RHP'] == 0.54
    assert o['Entry_Fee_Pct_Max'] == 0.5
    rows = o['_cost_schedule_rows']
    assert rows[0]['Horizon_Years'] == 0.25 and rows[0]['Is_RHP'] == 1

# --- LU0135992385: Schroders, RHP 1Y, composición vacía, HIGH (ancla 1Y OK) ---
def test_lu0135992385():
    o = _run('LU0135992385')
    assert o['Cost_RHP_Years'] == 1.0
    assert o['ACI_1Y'] == 0.3
    assert 'Management_Fee_Pct' not in o   # parse_costs_composition == {} en este layout
    assert o['Cost_Extraction_Quality'] == 'HIGH'

# --- IE00BZ4D7085: Polar, RHP 5Y, entry max 5%, MEDIUM_EUR ---
def test_ie00bz4d7085():
    o = _run('IE00BZ4D7085')
    assert o['KID_Currency'] == 'EUR'
    assert o['Cost_RHP_Years'] == 5.0
    assert o['Entry_Fee_Pct_Max'] == 5.0   # "hasta el 5% en el futuro"
    assert o['Management_Fee_Pct'] == 1.11
    assert o['Transaction_Cost_Pct'] == 0.42
    assert o['Cost_Extraction_Quality'] == 'MEDIUM_EUR'

# --- LU1502282632: Candriam, RHP 6Y, entry max 3.5%, MEDIUM_EUR ---
def test_lu1502282632():
    o = _run('LU1502282632')
    assert o['Cost_RHP_Years'] == 6.0
    assert o['Entry_Fee_Pct_Max'] == 3.5
    assert o['Management_Fee_Pct'] == 1.94
    assert o['Cost_Extraction_Quality'] == 'MEDIUM_EUR'
```

> **Estado de verificación (módulos S2-A CONFIRMADOS):** los tres módulos S2-A
> (`cost_table_parser.py`, `cost_format_router.py`, `cost_cross_validator.py`) en producción son
> idénticos a los del repositorio del Project (confirmado por José). Los valores de §4.2/§4.3 se
> generaron ejecutando **esos mismos módulos** sobre el texto real de los 8 KIDs en la sesión
> Nivel-3, por lo que el comportamiento de extracción, detección de formato/moneda y
> cross-validation NO está en duda y NO hay que reverificarlo.
>
> Lo único que puede variar es el **texto fuente**: si en producción los KIDs se extraen de otra forma
> (otra ruta de extracción de PDF, distinta a los ZIP `N.txt` del Project), el texto de entrada podría
> diferir y, con él, el output. Por tanto la única verificación pendiente para Sonnet es:
> **confirmar que `load_kid_text(isin)` produce el mismo texto que se usó aquí** (basta comparar la
> longitud y la presencia de la sección "Composición de los costes" / "Costs over time"). Si el texto
> coincide → los asserts de §5.3 son válidos tal cual. Si difiere → re-ejecutar el parser sobre el
> texto nuevo y ajustar. Regla invariante: **el assert refleja el output real del parser sobre el
> texto real**, nunca la tabla idealizada del documento.

### 5.4 Tests de la lógica OC/ACI (P-3) — con valores `existing_oc` simulados

```python
def test_oc_fill_when_null():
    # existing_oc None + TER reconstruido → devuelve Ongoing_Charge_Recurrent
    o = _run_with('IE00BZ4D7085', existing_oc=None)
    # TER = mgmt 1.11 + transac 0.42 = 1.53
    assert abs(o['Ongoing_Charge_Recurrent'] - 1.53) < 0.01

def test_oc_mismatch_flag_when_existing_is_aci():
    # IE0032875985: BD trae OC=2.4 (=ACI@3Y), TER real ~0.70 → mismatch, NO sobrescribe
    o = _run_with('IE0032875985', existing_oc=2.4)
    assert o.get('_oc_aci_mismatch') is True
    assert 'Ongoing_Charge_Recurrent' not in o   # COALESCE-safe: no se devuelve

def test_oc_no_action_when_existing_matches_ter():
    # existing_oc ya es un TER correcto y cercano → ni mismatch ni sobrescritura
    o = _run_with('IE00BZ4D7085', existing_oc=1.53)
    assert '_oc_aci_mismatch' not in o
    assert 'Ongoing_Charge_Recurrent' not in o
```
(`_run_with` = wrapper que pasa `existing_oc` a `extract_priips_costs`. Recordar normalizar la escala
de `existing_oc` según `_norm_existing_oc`; los tests usan % entero como en BD nueva.)

### 5.5 Test de robustez (no excepciones)

```python
def test_no_exception_on_garbage():
    from core.priips_cost_extractor import extract_priips_costs
    import core.priips_cost_extractor as ext
    ext.PRIIPS_COST_EXTRACTION_ENABLED = True
    o = extract_priips_costs("\x00\x01 texto basura sin estructura |||", "BAD")
    assert isinstance(o, dict)
    assert 'Cost_Extraction_Quality' in o
    assert '_cost_schedule_rows' in o

def test_empty_text():
    from core.priips_cost_extractor import extract_priips_costs
    import core.priips_cost_extractor as ext
    ext.PRIIPS_COST_EXTRACTION_ENABLED = True
    o = extract_priips_costs("", "EMPTY")
    assert o['KID_Format'] == 'UNKNOWN'
    assert o['Cost_Extraction_Quality'] == 'NONE'
```

### 5.6 Test de regresión DLA2 (skip hasta tener fixture DLA2)

```python
@pytest.mark.skip(reason="requiere fixture con DLA2_Table_Text (||| separado) — BL-DLA-2 producción")
def test_ie00bjgt6q17_dla2_ideal():
    # Con DLA2, la 2ª columna NO hereda el EUR de la 1ª:
    # _cost_schedule_rows debe contener (3.0,1, 650.0) y ACI_RHP=2.1
    ...
```

---

## §6 — LO QUE NO HACE S2-B (delimitación de scope)

- ❌ NO modifica `pipeline.py`. La integración del extractor (llamada tras `characterize_fund`,
  lectura de `existing_oc/entry/exit` desde BD, llamada a `upsert_cost_schedule`) es **S2-C**
  (BL-COST-4c). S2-B solo entrega el módulo y sus tests.
- ❌ NO modifica `sqlite_writer.py`. No toca `upsert_cost_schedule`, ni el bloque ON CONFLICT, ni
  `_normalize_record`, ni `_post_upsert_normalize_db` (R-1).
- ❌ NO modifica `classify_utils.py`. Las reglas INTER-COST-1/2/3 (incluida la corrección efectiva
  del OC-ACI mismatch con ruta no-COALESCE) son **BL-COST-5**, que requiere sesión Opus previa.
- ❌ NO modifica `schema_fondos.sql` (P-1 y P-7 resueltos sin migración; §1.7 solo si José aprueba).
- ❌ NO modifica `kiid_parser.py` ni los detectores legacy de fees.
- ❌ NO implementa `ucits_cost_extractor.py` (S2-C).
- ❌ NO activa el kill-switch en producción (`PRIIPS_COST_EXTRACTION_ENABLED` permanece `False`).
- ❌ NO corrige el bug de columnas duplicadas del parser plano (§4.1). Ese arreglo, si se decide, es
  una mejora de `cost_table_parser.py` (S2-A) o se resuelve por DLA2 en producción. S2-B lo asume.
- ❌ NO infiere `*_Fee_Pct_Max` desde EUR cuando falta el %. (Decisión §4.2 nota ².)

---

## §7 — PREVIEW S2-C / S2-D (actualizado tras decisiones S2-B)

**S2-C (~3h) — integración:**
- `ucits_cost_extractor.py` (alcance mínimo, ~5 fondos UCITS).
- Integración en `pipeline.py`: tras `characterize_fund`, si `KID_Format` o
  `Cost_Extraction_Quality` NULL → llamar al router; si `PRIIPS_KID` → `extract_priips_costs(...)`
  pasando `existing_oc/entry/exit` leídos de BD (ampliar el bloque `_v3_row`). Mapear el dict de
  retorno a `record` (las 11 columnas) y pasar `_cost_schedule_rows` a `upsert_cost_schedule`.
- Derivación cache `ACI_1Y`/`ACI_RHP` desde `fund_cost_schedule` en `sqlite_writer` (Decisión 1 del
  SPEC): **revisar a la luz del §4.4** — el ACI es anualizado y el schedule guarda EUR acumulado;
  la derivación del cache debe tomar el ACI del dict del extractor, no recomputarlo desde el EUR del
  schedule. Ajustar el diseño de BL-COST-4d en consecuencia.
- **Nuevo para S2-C derivado de P-3:** decidir la ruta de escritura no-COALESCE para corregir
  `Ongoing_Charge_Recurrent` en los ~328 fondos con `_oc_aci_mismatch`. Probablemente un UPDATE
  dirigido en `sqlite_writer` activado por una flag, NO el ON CONFLICT estándar.

**S2-D (~3h) — cierre:**
- `smoke_sprint2_costs.py`: corre el extractor sobre los 8 PDFs + una muestra del corpus.
- BL-COST-6: re-ejecución completa con kill-switch ON.
- INTER-COST-1/2/3 (BL-COST-5): sesión Opus separada (heurística OC/ACI sobre los 328 fondos).

**Hallazgos Nivel-3 de S2-B a trasladar a José (resumen ejecutivo):**
1. `config.py` en producción está en v19.1 con el fix S2-A aplicado (tolerancia 5bp, kill-switch real,
   5 valores en `COST_SCHEDULE_SOURCE_VALUES`). **S2-A confirmado cerrado.** (La copia del Project
   estaba obsoleta en v19.0; ya resuelto con la subida de producción.)
2. El prompt S2-B tenía datos de ground truth erróneos para 5 de 8 fondos (corregidos en §4).
3. El parser S2-A en ruta texto-plano duplica columnas y pierde ACI en 4/8 fondos → la calidad alta
   depende de DLA2. Sobre texto plano, lo esperable es MEDIUM_EUR/HIGH limitado.
4. La cross-validation %↔EUR solo es válida a horizonte 1 año (EUR acumulado vs ACI anualizado). Afecta
   el diseño del cache ACI en BL-COST-4d.
5. La escala de las 11 columnas nuevas es % entero (CHECK inequívocos); la columna legacy
   `Ongoing_Charge_Recurrent` tiene escala mixta (deuda para BL-COST-5).

---

**FIN DEL TRASPASO S2-B.**

*Documento autocontenido (Norma 5.4). Ground truth de §4 verificado en sesión Nivel-3 leyendo el
texto real de los 8 KIDs y ejecutando los módulos S2-A. Los tres módulos S2-A
(`cost_table_parser.py`, `cost_format_router.py`, `cost_cross_validator.py`) están confirmados
idénticos a producción, por lo que el output de extracción, detección de formato/moneda y
cross-validation NO requiere reverificación. La única comprobación pendiente para Sonnet es que
`load_kid_text(isin)` reproduzca el mismo texto fuente usado aquí (§5.3); si coincide, los asserts
son definitivos.*
