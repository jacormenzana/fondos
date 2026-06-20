# TRASPASO_CONTEXTO_BL_COST_SPRINT2_S2A.md
# Contexto operativo para sesión Sonnet S2-A — Sprint 2 BL-COST
# Generado: 2026-05-21 (post-sesión Nivel-3 Opus)
# Para: sesión Nivel-2 Sonnet — implementación S2-A (fundamentos del extractor)

---

## 0. INSTRUCCIONES PARA SONNET

Esta sesión implementa la **Sesión S2-A** del Sprint 2 de BL-COST. Entrega 4 módulos
nuevos más un fix a un módulo existente. Orden de implementación obligatorio (cada
módulo es dependencia del siguiente):

1. Fix `shared/config.py` — corregir bug constante (§1)
2. Fix `proyecto1/scripts/diag/cost_format_signals.py` — D-S2-2 (§2)
3. `proyecto1/core/cost_format_router.py` — BL-COST-3 (§3)
4. `proyecto1/core/cost_table_parser.py` — dependencia de 4a/4b (§4)
5. `proyecto1/core/cost_cross_validator.py` — dependencia de 4a (§5)

Reglas obligatorias antes de entregar cualquier módulo:
- Leer el fichero completo antes de modificarlo (nunca editar a ciegas)
- AST validation: `python -c "import ast; ast.parse(open('ruta').read())"` tras cada módulo
- Sin full rewrites salvo módulos nuevos
- Tests requeridos: se especifican por módulo en §6

BL-COST-4a (`priips_cost_extractor.py`) y el resto del sprint van en sesiones S2-B/C/D
posteriores. Esta sesión NO toca `pipeline.py`, `sqlite_writer.py` ni `classify_utils.py`.

---

## 1. BUG CRÍTICO: config.py — PRIIPS_INVESTMENT_BASE y COST_CROSS_VALIDATION_TOLERANCE_PCT

**Archivo:** `shared/config.py` (línea 70)

**Bug detectado en sesión Nivel-3:**
```python
# ESTADO ACTUAL (INCORRECTO):
COST_CROSS_VALIDATION_TOLERANCE_PCT: float = 0.05  # comentario dice "5 basis points"
```

`0.05` son **500 basis points (5%)**, no 5bp. La tolerancia correcta para cubrir
errores de redondeo de 1 céntimo sobre base 10.000 EUR es **5bp = 0.0005**.

También verificar que `PRIIPS_INVESTMENT_BASE = 10000.0` (correcto, no tocar).

**Fix:**
```python
# CORRECTO:
COST_CROSS_VALIDATION_TOLERANCE_PCT: float = 0.0005  # 5 basis points (0.05%)
```

**También añadir** (el kill-switch está comentado, debe activarse como constante real):
```python
# Kill-switch BL-COST-4c (Sprint 2). Entregar en False.
# Activar manualmente tras validar extractores sobre 8 PDFs muestra.
PRIIPS_COST_EXTRACTION_ENABLED: bool = False
```

**Añadir también** en `COST_SCHEDULE_SOURCE_VALUES` si no está `'PRIIPS_COMPOSITION'`:
Verificar que el tuple incluye al menos:
`('PRIIPS_COSTS_OVER_TIME', 'PRIIPS_COMPOSITION', 'PRIIPS_TEXT', 'UCITS_DERIVED', 'MANUAL')`
Si faltan valores, añadirlos. No eliminar los existentes.

**Cambios de versión:** actualizar el docstring a v19.1 con nota del fix.

---

## 2. Fix cost_format_signals.py — D-S2-2

**Archivo:** `proyecto1/scripts/diag/cost_format_signals.py`

**Cambio:** añadir parámetro `fee_known_flag` a las dos funciones de detección de
falsos positivos. El umbral de señales mínimas sube a 2 cuando `fee_known_flag='NOT_FOUND'`
porque esos fondos ya reconocieron que no extrajeron nada y pusieron 0 como default —
no son FP en sentido estricto.

**Modificar `detect_entry_fee_false_positive`:**
```python
# ANTES:
def detect_entry_fee_false_positive(text: str, entry_fee_pct: float) -> dict:

# DESPUÉS:
def detect_entry_fee_false_positive(
    text: str,
    entry_fee_pct: float,
    fee_known_flag: str = 'UNKNOWN'
) -> dict:
    ...
    # Umbral de señales: NOT_FOUND exige 2; otros exigen 1
    threshold = 2 if fee_known_flag == 'NOT_FOUND' else 1
    is_fp = (entry_fee_pct == 0.0 and signal_count >= threshold)
    ...
```

**Aplicar el mismo cambio a `detect_exit_fee_false_positive`** (mismo parámetro,
misma lógica de umbral).

**La firma de retorno no cambia** — sigue devolviendo dict con `is_fp`, `signal_count`,
`signals_found`.

**Tests a añadir** (al final del módulo o en fichero de test separado):
```python
# Caso 1: NOT_FOUND + 1 señal → NO es FP
result = detect_entry_fee_false_positive(
    "texto con señal de fee positivo",
    0.0,
    fee_known_flag='NOT_FOUND'
)
assert result['is_fp'] == False, "NOT_FOUND con 1 señal no debe ser FP"

# Caso 2: NOT_FOUND + 2 señales → SÍ es FP
result = detect_entry_fee_false_positive(
    "texto con dos señales de fee comisión máximo",
    0.0,
    fee_known_flag='NOT_FOUND'
)
assert result['is_fp'] == True, "NOT_FOUND con 2 señales sí debe ser FP"

# Caso 3: EXTRACTED + 1 señal → SÍ es FP (umbral bajo para no NOT_FOUND)
result = detect_entry_fee_false_positive(
    "texto con señal de fee positivo",
    0.0,
    fee_known_flag='EXTRACTED'
)
assert result['is_fp'] == True, "EXTRACTED con 1 señal debe ser FP"
```

---

## 3. BL-COST-3: cost_format_router.py

**Archivo nuevo:** `proyecto1/core/cost_format_router.py`

**Posición en estructura:** directamente en `proyecto1/core/` (mismo nivel que
`pipeline.py`, `sqlite_writer.py`, `dla_extractor.py`, etc.).
**NO crear subdirectorio `cost/`** — decisión arquitectónica de sesión Nivel-3.

**Dependencia:** importa de `proyecto1/scripts/diag/cost_format_signals.py`
las funciones de señales PRIIPS/UCITS ya definidas. No duplicar esos patrones.

**Interfaz pública:**

```python
def detect_kid_format(text: str) -> str:
    """
    Clasifica el formato del KID a partir del texto extraído.
    
    Returns:
        'PRIIPS_KID'  — documento PRIIPs KID
        'UCITS_KIID'  — documento UCITS KIID clásico
        'UNKNOWN'     — insuficientes señales para clasificar
    """

def detect_kid_currency(text: str) -> Optional[str]:
    """
    Detecta la moneda base de la tabla de costes del KID.
    
    Returns:
        Código ISO-4217 en mayúsculas ('EUR', 'USD', 'GBP', etc.)
        o None si no se detecta.
    """
```

**Lógica de `detect_kid_format` (criterio relajado D-S2-1):**

```python
# Contar señales usando las funciones de cost_format_signals
ucits_count  = count_ucits_signals(text)   # señales fuertes UCITS
priips_count = count_priips_signals(text)  # señales fuertes PRIIPS
cat3_count   = count_cat3_signals(text)    # señales escenarios Cat.3

# Orden de evaluación (UCITS tiene prioridad — sus señales son muy específicas):
if ucits_count >= 2:
    return 'UCITS_KIID'
if priips_count >= 3:
    return 'PRIIPS_KID'
if priips_count >= 1 and ucits_count == 0 and cat3_count >= 1:
    return 'PRIIPS_KID'
return 'UNKNOWN'
```

**Lógica de `detect_kid_currency`:**

Buscar en el texto la base de inversión estándar PRIIPs (`10.000 EUR`, `10,000 USD`, etc.)
que aparece en la sección de costes. Patrón:

```python
INVESTMENT_BASE_PATTERN = re.compile(
    r'10[.,]000\s*(EUR|USD|GBP|CHF|SEK|NOK|DKK|PLN|CZK)',
    re.IGNORECASE
)
```

Si no encontrado, buscar también `"Inversión: 10.000 (MONEDA)"` o similar.
Devolver el código en mayúsculas o None.

**Imports:**
```python
import re
from typing import Optional
# Reutilizar funciones de cost_format_signals (DRY):
import sys, os
# Ajustar path si necesario para importar desde scripts/diag
# Alternativa: duplicar solo los contadores (más simple, menos DRY)
```

**Nota sobre el import de cost_format_signals:** si el import cruzado
`core/ → scripts/diag/` resulta problemático por la estructura de paths,
la alternativa aceptable es extraer las funciones `count_*_signals()` a un
módulo `shared/` o duplicar solo los patrones compilados en `cost_format_router.py`
con un comentario `# DRY: sincronizar con cost_format_signals.py si se modifican`.
Escoger la opción que no requiera modificar `__init__.py` o `sys.path`.

**Tests obligatorios:**

Crear `proyecto1/tests/test_cost_format_router.py`:

```python
# Los 8 ISINs muestra son todos PRIIPS_KID:
PRIIPS_ISINS = [
    'IE00BJGT6Q17', 'LU1084165304', 'IE0032875985', 'IE00B45H7020',
    'FR0000989626', 'LU0135992385', 'IE00BZ4D7085', 'LU1502282632'
]
# Los PDFs están en /mnt/project/*.pdf — leer con pdfplumber para los tests
# En producción el router recibe texto de BD, no PDF.

# Test adicional: texto UCITS sintético con "datos fundamentales para el inversor"
# + "gastos corrientes" → debe retornar 'UCITS_KIID'

# Test: texto vacío → 'UNKNOWN'

# Test: detect_kid_currency en LU1084165304 (USD) → 'USD'
# Test: detect_kid_currency en IE00BZ4D7085 (EUR) → 'EUR'
# Test: detect_kid_currency en texto sin moneda → None
```

---

## 4. cost_table_parser.py

**Archivo nuevo:** `proyecto1/core/cost_table_parser.py`

**Propósito:** parsear las dos tablas PRIIPs de costes desde el texto del KID.
Reutilizado por `priips_cost_extractor.py` (S2-B) y `ucits_cost_extractor.py` (S2-C).
Es la dependencia más crítica del extractor — debe ser robusta.

**Interfaz pública:**

```python
def parse_costs_over_time(text: str) -> List[dict]:
    """
    Parsea la tabla "Costes a lo largo del tiempo" / "Costs over time".
    
    Detecta automáticamente si el texto está en formato DLA2 (contiene '|||')
    o en texto plano. Aplica el parser adecuado.
    
    Returns: lista de dicts, uno por horizonte temporal encontrado:
        {
            'horizon_label': str,      # "1 año", "5 años", "período recomendado"
            'horizon_years': float,    # 1.0, 5.0, 0.25, etc.
            'total_cost_eur': float|None,
            'aci_pct': float|None,
            'is_rhp': bool,            # True si es el horizonte RHP
            'source': str              # 'DLA2' o 'PLAIN_TEXT'
        }
    Retorna [] si la tabla no se encuentra o no se parsea correctamente.
    """

def parse_costs_composition(text: str) -> dict:
    """
    Parsea la tabla "Composición de los costes" / "Composition of costs".
    
    Returns: dict con las claves presentes (ausentes = no extraídas):
        entry_fee_pct: float
        entry_fee_eur: float
        entry_fee_max_pct: float       # "hasta X%" / "up to X%"
        exit_fee_pct: float
        exit_fee_eur: float
        exit_fee_max_pct: float
        management_fee_pct: float
        management_fee_eur: float
        transaction_cost_pct: float
        transaction_cost_eur: float
        performance_fee_pct: float
        performance_fee_eur: float
    Retorna {} si la tabla no se encuentra.
    """
```

**Funciones internas (privadas, prefijo `_`):**

```python
def _parse_costs_over_time_dla2(text: str) -> List[dict]:
    """Parser para formato DLA2 (texto contiene '|||')."""

def _parse_costs_over_time_plain(text: str) -> List[dict]:
    """Parser para texto plano (Raw_KIID_Text sin DLA2)."""

def _parse_composition_dla2(text: str) -> dict:
    """Parser composición desde DLA2."""

def _parse_composition_plain(text: str) -> dict:
    """Parser composición desde texto plano."""

def _normalize_amount(s: str) -> Optional[float]:
    """
    Normaliza string numérico a float.
    "1.360" → 1360.0
    "1,360" → 1360.0  (contexto europeo: punto=miles, coma=decimal)
    "153"   → 153.0
    "1,5"   → 1.5     (porcentaje europeo)
    "1.5"   → 1.5
    Retorna None si no parseable.
    """

def _parse_horizon_years(label: str) -> float:
    """
    Convierte etiqueta de horizonte a años float.
    "1 año" / "1 year"                → 1.0
    "5 años" / "5 years"              → 5.0
    "3 años"                          → 3.0
    "período de mantenimiento" / RHP  → -1.0  (señal especial, is_rhp=True)
    "3 meses" / "3 months"            → 0.25
    "6 meses"                         → 0.5
    "1 mes"                           → 0.083
    """

def _is_rhp_label(label: str) -> bool:
    """True si la etiqueta corresponde al período de mantenimiento recomendado."""
```

**Lógica del parser DLA2 para "Costes a lo largo del tiempo":**

El formato DLA2 serializa tablas como:
```
|||Inversión: 10.000 EUR|||En caso de salida después de 1 año|||En caso de salida después de 5 años|||
|||Costes totales|||153 EUR|||1.360 EUR|||
|||Incidencia anual de los costes (*)|||1,5%|||1,6%|||
```

Estrategia:
1. Localizar el bloque que contiene `"costes a lo largo"` o `"costs over time"` en el texto DLA2.
2. Identificar la fila de encabezado: la que contiene etiquetas de horizonte (`"1 año"`, `"5 años"`, `"período"`).
3. Identificar fila de costes totales: contiene `"costes totales"` / `"total costs"`.
4. Identificar fila de ACI: contiene `"incidencia anual"` / `"annual cost impact"`.
5. Parsear cada columna del encabezado → `_parse_horizon_years()`.
6. Para cada horizonte: extraer `total_cost_eur` de la fila de costes, `aci_pct` de la fila ACI.

**Lógica del parser de texto plano:**

Para texto sin DLA2, las tablas PRIIPs aparecen como texto corrido. Buscar:
- Encabezado: `"Si sale después de"` + valor numérico + unidad de tiempo
- Valores en líneas subsiguientes: importe EUR en una línea, porcentaje en la siguiente
- Usar ventana de ±500 caracteres alrededor del encabezado de la sección de costes

**Lógica para "Composición de los costes":**

Filas esperadas (etiqueta → tipo de coste):
```python
COMPOSITION_ROW_LABELS = {
    # Costes de entrada/salida
    r'costes?\s+de\s+entrada|entry\s+(?:charge|cost)': 'entry',
    r'costes?\s+de\s+salida|exit\s+(?:charge|cost)':   'exit',
    # Costes corrientes
    r'comisiones?\s+de\s+gesti[oó]n|management\s+(?:fee|cost)': 'management',
    r'costes?\s+de\s+operaci[oó]n|transaction\s+cost':           'transaction',
    # Costes accesorios
    r'comisiones?\s+(?:en\s+funci[oó]n\s+de\s+la\s+rentabilidad|de\s+(?:éxito|rendimiento))'
    r'|performance\s+fee':                                        'performance',
}
```

Para cada fila encontrada: extraer el porcentaje del texto de la celda de descripción
(ej: `"1,11% del valor de su inversión al año"` → `0.0111`) y el importe EUR de la
celda de importe (ej: `"111 EUR"` → `111.0`).

Detectar también `"hasta X%"` / `"up to X%"` para `entry_fee_max_pct` y `exit_fee_max_pct`.

**Patrones auxiliares compilados al nivel de módulo:**

```python
import re
from typing import List, Optional

# Importe absoluto (EUR/USD/etc. explícito o implícito)
AMOUNT_PATTERN = re.compile(
    r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(?:EUR|USD|GBP|CHF)?',
    re.IGNORECASE
)

# Porcentaje
PCT_PATTERN = re.compile(r'(\d+[,.]?\d*)\s*%')

# Máximo fee ("hasta X%", "up to X%", "máximo X%")
MAX_FEE_PATTERN = re.compile(
    r'(?:hasta|up\s+to|m[aá]ximo|maximum|at\s+most)\s+(?:el\s+)?(\d+[,.]?\d*)\s*%',
    re.IGNORECASE
)

# Horizonte en años
HORIZON_YEARS_PATTERN = re.compile(
    r'(\d+)\s*a[ñn]os?|(\d+)\s*years?',
    re.IGNORECASE
)

# Horizonte en meses
HORIZON_MONTHS_PATTERN = re.compile(
    r'(\d+)\s*mes(?:es)?|(\d+)\s*months?',
    re.IGNORECASE
)

# Señal de RHP
RHP_PATTERN = re.compile(
    r'per[ií]odo\s+de\s+mantenimiento\s+recomendado|recommended\s+holding\s+period'
    r'|mantenimiento\s+recomendado',
    re.IGNORECASE
)

# Delimitador DLA2
DLA2_SEPARATOR = '|||'
```

**Tests obligatorios:** crear `proyecto1/tests/test_cost_table_parser.py`

```python
# Test 1: parse_costs_over_time con texto DLA2 de IE00BZ4D7085
# → 1 fila (RHP=1Y), horizon_years=1.0, is_rhp=True,
#   total_cost_eur=12.0, aci_pct=0.001

# Test 2: parse_costs_over_time con texto DLA2 de LU0135992385 o IE00B45H7020
# → fondo con 2 columnas (1Y y RHP)

# Test 3: parse_costs_composition de IE00BZ4D7085
# → entry_fee_pct=0.0, entry_fee_max_pct=0.05, exit_fee_pct=0.0,
#   management_fee_pct=0.001, transaction_cost_pct=0.0002

# Test 4: parse_costs_composition de LU1502282632
# → entry_fee_max_pct=0.035 (3.50% máximo)

# Test 5: _normalize_amount("1.360") → 1360.0
# Test 6: _normalize_amount("1,5") → 1.5
# Test 7: _normalize_amount("153") → 153.0
# Test 8: _parse_horizon_years("1 año") → 1.0
# Test 9: _parse_horizon_years("período de mantenimiento recomendado") → -1.0 (is_rhp)
# Test 10: _parse_horizon_years("3 meses") → 0.25
```

---

## 5. cost_cross_validator.py

**Archivo nuevo:** `proyecto1/core/cost_cross_validator.py`

**Propósito:** validar la coherencia entre el porcentaje declarado y el importe EUR
de la tabla de costes. Reutilizable por `priips_cost_extractor.py` y por futuras
reglas INTER-COST.

**Constantes:** importar de `shared.config`:
- `PRIIPS_INVESTMENT_BASE` (10000.0)
- `COST_CROSS_VALIDATION_TOLERANCE_PCT` (0.0005 tras el fix del §1)

**Dataclass de resultado:**

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ValidationResult:
    status: str           # 'OK' | 'DISCREPANCY' | 'PCT_ONLY' | 'EUR_ONLY' | 'NONE'
    validated_pct: Optional[float]   # el valor más fiable
    discrepancy_bp: Optional[float]  # diferencia en basis points (None si no aplica)
    notes: str
```

**Interfaz pública:**

```python
def validate_pct_eur(
    pct: Optional[float],
    eur_amount: Optional[float],
    base: float = PRIIPS_INVESTMENT_BASE,
    tolerance: float = COST_CROSS_VALIDATION_TOLERANCE_PCT
) -> ValidationResult:
    """
    Cross-valida porcentaje declarado vs importe EUR absoluto.
    
    Lógica:
    - Si ambos disponibles:
        implied_pct = eur_amount / base
        diff = abs(pct - implied_pct)
        if diff <= tolerance:          → OK,          usar pct
        elif diff <= 0.005 (50bp):    → DISCREPANCY, usar pct (confiar en %)
        else:                          → DISCREPANCY, validated_pct=None (error grave)
    - Si solo pct:   → PCT_ONLY,  usar pct
    - Si solo eur:   → EUR_ONLY,  usar implied_pct = eur_amount / base
    - Si ninguno:    → NONE,      validated_pct=None
    """
```

**Umbral de error grave:** 50bp (`0.005`). Por encima de esta discrepancia, el
porcentaje y el EUR son tan inconsistentes que no podemos confiar en ninguno sin
revisión manual. `validated_pct` queda None para esos casos.

**Tests obligatorios:** crear `proyecto1/tests/test_cost_cross_validator.py`

```python
# Test 1: validate_pct_eur(0.005, 50.0, 10000)
# → status='OK', validated_pct=0.005, discrepancy_bp≈0

# Test 2: validate_pct_eur(0.005, 52.0, 10000)
# → status='DISCREPANCY', validated_pct=0.005, discrepancy_bp≈20

# Test 3: validate_pct_eur(0.005, 100.0, 10000)
# → status='DISCREPANCY', validated_pct=None, discrepancy_bp≈500 (error grave)

# Test 4: validate_pct_eur(0.005, None, 10000)
# → status='PCT_ONLY', validated_pct=0.005

# Test 5: validate_pct_eur(None, 50.0, 10000)
# → status='EUR_ONLY', validated_pct=0.005

# Test 6: validate_pct_eur(None, None, 10000)
# → status='NONE', validated_pct=None

# Test 7: validate_pct_eur(0.0525, 510.0, 10000) — caso LU1084165304 (USD)
# → status='DISCREPANCY' (0.0525 vs 0.051 → 15bp) o 'OK' según tolerancia
# Nota: USD base 10.000 USD. Este test verifica que la base se aplica correctamente.

# Test 8: validate_pct_eur(0.001, 12.0, 10000) — caso IE00BZ4D7085 (monetario)
# → status='DISCREPANCY' (0.001 vs 0.0012 → 20bp) — por redondeo en tabla
# Verificar que validated_pct=0.001 (confiamos en %, no en EUR redondeado)
```

---

## 6. Resumen de entregas S2-A

| Módulo | Tipo | Tests | Estado objetivo |
|---|---|---|---|
| `shared/config.py` | fix bug | — | `COST_CROSS_VALIDATION_TOLERANCE_PCT=0.0005`, kill-switch activo |
| `scripts/diag/cost_format_signals.py` | fix | 3 casos nuevos inline | parámetro `fee_known_flag` en ambas funciones |
| `proyecto1/core/cost_format_router.py` | nuevo | `tests/test_cost_format_router.py` | 8 PDFs → PRIIPS, UCITS sintético, vacío |
| `proyecto1/core/cost_table_parser.py` | nuevo | `tests/test_cost_table_parser.py` | ≥10 tests |
| `proyecto1/core/cost_cross_validator.py` | nuevo | `tests/test_cost_cross_validator.py` | 8 tests |

**Criterio de cierre S2-A:**
- [ ] `python -X utf8 shared/config.py` → sin errores de import
- [ ] AST OK en los 5 módulos
- [ ] Todos los tests pasan
- [ ] `COST_CROSS_VALIDATION_TOLERANCE_PCT = 0.0005` en config.py (verificar con grep)
- [ ] `PRIIPS_COST_EXTRACTION_ENABLED = False` en config.py (verificar con grep)
- [ ] `cost_format_router.detect_kid_format` retorna 'PRIIPS_KID' para los 8 ISINs muestra

---

## 7. Lo que NO hace esta sesión

- ❌ No implementa `priips_cost_extractor.py` (S2-B)
- ❌ No implementa `ucits_cost_extractor.py` (S2-C)
- ❌ No modifica `pipeline.py` (S2-C)
- ❌ No modifica `sqlite_writer.py` (S2-C)
- ❌ No implementa INTER-COST-1/2/3 en `classify_utils.py` (pendiente sesión Opus adicional)
- ❌ No activa el kill-switch en producción

---

## 8. Contexto técnico de los 8 PDFs muestra

Los PDFs están disponibles en el entorno de trabajo. Todos son PRIIPS_KID.

| ISIN | Gestora | Particularidad clave para tests |
|---|---|---|
| IE00BJGT6Q17 | PIMCO | Exit fee FP (197 EUR) |
| LU1084165304 | Fidelity | Entry fee en USD (5.25% / 510 USD) — base 10.000 USD |
| IE0032875985 | PIMCO | OC=ACI@3Y (2.4%) — gap OC-ACI paradigmático |
| IE00B45H7020 | BlackRock | Monetario, RHP corto, costes mínimos |
| FR0000989626 | Groupama | Entry 0.50% / 50€ — cross-validation exacta |
| LU0135992385 | Schroders | Entry "no cobramos" — correctamente 0 |
| IE00BZ4D7085 | Polar Capital | Entry 0% + "hasta 5% en el futuro" → entry_fee_max_pct |
| LU1502282632 | Candriam | Entry 3.50% máximo |

---

## 9. Reglas anti-regresión heredadas de Sprint 1

- R-1: `KID_Format` y `Cost_Extraction_Quality` NO entran en `_normalize_record` ni
  `_post_upsert_normalize_db` de `sqlite_writer.py`. No tocar esas funciones.
- R-5: word boundary en todos los patrones regex nuevos donde aplique.
- R-6: ventanas acotadas (`.{0,200}?` lazy) en patrones de extracción para evitar
  capturar texto de secciones adyacentes.
- R-8: AST validation obligatoria tras cada modificación.

---

## 10. Sesiones S2-B/C/D — preview (no implementar ahora)

**S2-B (~4h):** `priips_cost_extractor.py` — núcleo del sprint. Usa `cost_table_parser`
y `cost_cross_validator` entregados en S2-A. Tests sobre los 8 PDFs muestra.

**S2-C (~3h):** `ucits_cost_extractor.py` + integración `pipeline.py` + derivación
cache `ACI_1Y`/`ACI_RHP` en `sqlite_writer.py`.

**S2-D (~3h):** `smoke_sprint2_costs.py` + activación kill-switch + BL-COST-6
re-ejecución completa. INTER-COST-1/2/3 requieren sesión Opus previa separada
(heurística OC/ACI necesita validación de diseño antes de implementar).

---

## 11. Nota sobre BL-COST-5 (INTER-COST)

**No implementar en este sprint sin sesión Opus previa.**

La heurística `validate_oc_vs_aci()` (ratio OC/ACI_RHP como señal de mezcla TER/ACI)
puede generar falsos positivos en fondos legítimamente baratos. El diseño de esa
lógica requiere validación sobre los 328 fondos con gap OC-ACI real antes de codificar.
Las sesiones S2-A/B/C/D no dependen de BL-COST-5.
