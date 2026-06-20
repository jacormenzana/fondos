# ESPECIFICACIÓN DE IMPLEMENTACIÓN — SPRINT BL-57 v3 + BL-FAM-FIX

**Destinatario:** Sonnet 4.6
**Origen del análisis:** Opus 4.7 (sesión 26-abr-2026)
**Estado:** ESPECIFICACIÓN CERRADA — implementación directa, sin reabrir decisiones de diseño.
**Política de escalado:** ver Sección 6.

---

## SECCIÓN 1 — CONTEXTO MÍNIMO REQUERIDO

### 1.1 Lectura obligatoria antes de codificar

| Fichero | Líneas críticas | Para qué |
|---|---|---|
| `/core/classify_utils.py` | 2154-2240, 2580-2610, 2780-2810 | `ALLOWED_FAMILY_BY_NATURE`, `_DEFAULT_FAMILY_BY_NATURE`, `validate_nature_family_coherence`, `apply_semantic_validation` |
| `/blocks/mixtos.py` | 80-180 | Emisor de `Family='Income Oriented'` (línea 130) |
| `/core/fund_family_builder.py` | módulo completo (491 líneas) | Tarea 2 |
| `/PRINCIPIOS_DISENO.md` | Principios #1, #2, #8, #9 | Marco doctrinal del proyecto |
| `/ESTADO_BACKLOG_APR2026_v3_2.md` | BL-57, BL-44 | Historia de la migración previa |

### 1.2 Principios aplicables (no negociables durante este sprint)

- **Principio #1** — root cause sobre síntoma. Aplica especialmente a Tarea 2: cualquier corrección que parchee los 11 ISINs específicos del log es inaceptable. La regla debe ser escalable.
- **Principio #2** — DRY, punto único de emisión por literal.
- **Principio v3.2** — punto único de emisión por atributo (constante única, no literales inline).
- **Norma BL-57 v3 (NUEVA, ver Sección 5)** — toda migración de literal categórico debe localizar todos los emisores antes de cambiar el validador.

### 1.3 Antipatrón documentado a evitar

> *Migrar el literal en validador (`ALLOWED_*`) y normalizadores SQL sin localizar todos los emisores produce regresión silenciosa: el validador rechaza el literal viejo, P07 lo redirige al default, los datos pierden granularidad sin warning. 104 fondos perdidos en BL-57 v2 por este patrón el 26-abr-2026.*

### 1.4 Estado actual confirmado de la BD (post-ciclo 074126)

```
Family               n
RV Core              1455
Mixtos                469   ← contiene los 104 fondos que deberían estar en 'Orientado a Renta'
Renta Fija Corto P.   427
Renta Fija Flexible   415
RV Temática           218
Monetario              99
Retorno Absoluto       43
RF High Yield          39
Activos Reales         17
Estructurado            8
RF Inflación            5
RF Emergentes           5
Flexible Estratégico    4
                    ----
                    3204
```

Tras Tarea 1 + re-ejecución del pipeline, los conteos esperados son:
```
Mixtos             ≈ 365   (469 - 104)
Orientado a Renta  ≈ 104   (NUEVO)
                  ----
Total              = 3204  (conservación obligatoria)
```

---

## SECCIÓN 2 — TAREA 1: CENTRALIZACIÓN DE `FAMILY_INCOME_ORIENTED`

### 2.1 Objetivo

Constituir el patrón de constantes canónicas para literales de Family, empezando por `FAMILY_INCOME_ORIENTED`. Cerrar la regresión observada en el ciclo del 26-abr donde 104 fondos cayeron en `'Mixtos'` por desincronización emisor-validador.

### 2.2 Alcance EXPLÍCITO

**SÍ entra:**
- Constante `FAMILY_INCOME_ORIENTED` en `classify_utils.py`.
- Importación de la constante en `blocks/mixtos.py:130`.
- Tests unitarios mínimos.

**NO entra (será BL-58 dedicado):**
- Refactor del resto de `ALLOWED_FAMILY_BY_NATURE` para usar constantes (Nivel 2).
- Función `_validate_family_catalog_consistency` ejecutada al importar (Nivel 3).
- Migración de los emisores latentes detectados:
  - `mixtos.py:126` → `"Lifecycle"` (0 fondos afectados actualmente — emisor inactivo)
  - `mixtos.py:128` → `"Retirement"` (0 fondos afectados — emisor inactivo)
  - `fund_characterizer.py:681` → `"Renta Fija Corto"` (red de seguridad SQL absorbe correctamente)
  - `fund_characterizer.py:680` → `"Fixed Income"` (red de seguridad SQL absorbe)

Estos emisores latentes están listados en BL-58 para futuro sprint.

### 2.3 Cambios concretos

**Fichero 1: `/core/classify_utils.py`**

**Inserción única — Constante.**

Ubicación: tras los `import` y constantes de uso general, ANTES de las primeras estructuras numeradas (`# 1. ...`, `# 2. ...`). Buscar un comentario tipo `# === Constants ===` o el primer `_DICT`/`_MAP` y colocar el bloque inmediatamente arriba.

```python
# ============================================================
# Family canonical literals
# ------------------------------------------------------------
# Punto único de definición de literales Family. Cualquier emisor
# (blocks/*.py, fund_characterizer.py, futuras extensiones) debe
# importar la constante en lugar de escribir el literal inline.
#
# Norma BL-57 v3 (26-abr-2026): todo literal Family añadido al
# catálogo debe registrarse aquí Y aparecer en
# ALLOWED_FAMILY_BY_NATURE antes de ser emitido por cualquier
# bloque. Su uso debe ser por importación, nunca por literal inline.
#
# Antipatrón evitado: la migración BL-57 v2 (25-abr) actualizó el
# validador y los normalizadores SQL sin tocar el emisor primario
# (blocks/mixtos.py:130). El validador INTER-5 rechazó el literal
# viejo y la auto-corrección P07 absorbió silenciosamente 104 fondos
# al default 'Mixtos', destruyendo granularidad semántica sin alarma.
# ============================================================
FAMILY_INCOME_ORIENTED = "Orientado a Renta"
```

**Fichero 2: `/blocks/mixtos.py`**

**Modificación 1 — Importación (al inicio del módulo).**

Localizar el bloque de imports existente que trae cosas de `core.classify_utils`. Añadir `FAMILY_INCOME_ORIENTED` a los imports existentes. Si el módulo aún no importa nada de `classify_utils` (caso improbable), añadir un import nuevo:

```python
from core.classify_utils import (
    # ... imports existentes ...
    FAMILY_INCOME_ORIENTED,
)
```

**Modificación 2 — Línea 130.**

```python
# Antes:
elif "income" in name_l:
    result["Family"] = "Income Oriented"

# Después:
elif "income" in name_l:
    result["Family"] = FAMILY_INCOME_ORIENTED
```

### 2.4 Restricciones explícitas

NO eliminar:
- Entrada `'Income Oriented' → 'Orientado a Renta'` en `FAMILY_TRANSLATION_MAP` (línea 1889 de `classify_utils.py`). Es red de seguridad legítima para emisores futuros desconocidos.
- Cláusulas `WHEN TRIM(Family) = 'Income Oriented' THEN 'Orientado a Renta'` en `sqlite_writer.py` (líneas 264-265 y 767-768). Son red de seguridad de capa BD.
- Control SELECT de `sqlite_writer.py:724`. Es detector automático de regresión.

NO refactorizar:
- `ALLOWED_FAMILY_BY_NATURE` para usar constantes (BL-58).
- `_DEFAULT_FAMILY_BY_NATURE` para usar constantes (BL-58).

NO añadir:
- Función `_validate_family_catalog_consistency` (BL-58).

### 2.5 Validación post-implementación

**Tests unitarios.** Crear fichero `/tests/test_classify_utils_family_constants.py`:

```python
"""
Tests de constantes canónicas de Family (BL-57 v3).

Verifican el contrato mínimo de la centralización:
- la constante existe y tiene el valor canónico esperado
- el literal está aceptado por el validador para Nature='Mixtos'
- el literal está aceptado por el validador para Nature='Renta Fija Flexible'
"""

from core.classify_utils import (
    FAMILY_INCOME_ORIENTED,
    ALLOWED_FAMILY_BY_NATURE,
)


def test_family_income_oriented_constant_value():
    """La constante tiene el valor canónico definido por BL-57 v2."""
    assert FAMILY_INCOME_ORIENTED == "Orientado a Renta"


def test_family_income_oriented_in_allowed_for_mixtos():
    """El validador INTER-5 acepta la constante para Nature='Mixtos'."""
    assert FAMILY_INCOME_ORIENTED in ALLOWED_FAMILY_BY_NATURE["Mixtos"]


def test_family_income_oriented_in_allowed_for_rff():
    """El validador INTER-5 acepta la constante para Nature='Renta Fija Flexible'."""
    assert FAMILY_INCOME_ORIENTED in ALLOWED_FAMILY_BY_NATURE["Renta Fija Flexible"]
```

**Validación de integración.** Tras re-ejecutar `pipeline.py`, ejecutar las queries:

```sql
-- A) literal legacy desaparecido en BD
SELECT COUNT(*) FROM fund_master WHERE Family = 'Income Oriented';
-- Objetivo: 0

-- B) literal canónico restaurado
SELECT COUNT(*) FROM fund_master WHERE Family = 'Orientado a Renta';
-- Objetivo: ~104

-- C) conservación poblacional sobre Mixtos+Orientado a Renta
SELECT Family, COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Mixtos' GROUP BY Family;
-- Esperado: Mixtos ≈ 365; Orientado a Renta ≈ 104; Flexible Estratégico ≈ 4

-- D) no-regresión sobre validador INTER-5 para Mixtos
SELECT COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Mixtos'
  AND Family NOT IN ('Mixtos', 'Orientado a Renta', 'Flexible Estratégico');
-- Objetivo: 0

-- E) suma total invariante
SELECT COUNT(*) FROM fund_master;
-- Objetivo: 3204 (sin pérdida de filas)
```

Si A, D, E devuelven 0/0/3204 y B+C cuadran, Tarea 1 está cerrada.

---

## SECCIÓN 3 — TAREA 2: FIX DE `fund_family_builder.py` (11 INCONSISTENCIAS)

### 3.1 Diagnóstico de causa raíz por defecto

Cuatro defectos distintos del módulo, ninguno relacionado con los ISINs específicos del log. Todos se corrigen por modificación de las reglas escalables.

**Defecto D1 — Par adyacente faltante: `(RFCP, Restantes)` y, en general, `Restantes` no se trata como adyacente universal.**

Casos afectados del log: FAM_000261 (BGF China Bond ×3), FAM_000382 (BGF US Sh Durat ×3). Patrón: 2 hermanos correctamente clasificados como `Renta Fija Corto Plazo`, 1 hermano caído en `Restantes`.

`Restantes` es por definición la categoría de fallback del clasificador: cualquier fondo en `Restantes` que pertenece a una familia donde la mayoría está en una Nature concreta es, casi por definición, un error de clasificación. La regla actual (`_ADJACENT_NATURE_PAIRS` línea 142-149) no contempla `Restantes`.

**Defecto D2 — Familias de 2 miembros nunca alcanzan mayoría ≥ 2/3.**

Casos afectados del log: FAM_000945, FAM_000946 (DWS Multi Opp), FAM_001776 (M&G Optimal Income), FAM_002121 (PIMCO Inflatn). Patrón: 2 hermanos clasificados en Nature distintas, normalmente ACC vs INC del mismo fondo.

La regla 2 (`_resolve_family_nature` línea 202): `if majority_count / total >= 2/3`. Con `total=2`, `majority_count=1`, ratio = 0.5 < 0.667 → nunca aplica. Esto excluye sistemáticamente todas las familias bipartitas.

**Defecto D3 — Regla de adyacencia + calidad SRRI con umbral demasiado restrictivo y sin desempate.**

La regla 3 (línea 232-233) exige `best_q - max(others_q) > 2`. Con dos fondos con `SRRI_Quality_Flag='HIGH'` (rank=4) cada uno, las calidades agregadas se igualan y la diferencia es 0. No hay desempate por `Data_Quality_Flag` ni otros criterios.

**Defecto D4 — `_normalize_name` colapsa "Income" como sufijo de clase, agrupando fondos distintos en la misma familia.**

Casos afectados del log: FAM_001293 (Templeton Global Income vs Templeton Global), FAM_001343 (GS Gbl Eq Income vs GS Gbl Eq).

`_CLASS_SUFFIXES` línea 67 contiene `inc(?:ome)?`. La intención es capturar variantes como `Inc` o `Income` como sufijo de clase de distribución. Pero el regex elimina cualquier palabra "Income" al final del nombre, incluso cuando es parte del nombre del fondo (no sufijo de clase). Resultado: "Templeton Global Income" se normaliza como "templeton global", colisionando con "Templeton Global" que es un fondo distinto.

Este defecto es **estructural del matcher**, no del clasificador. La consecuencia: dos fondos diferentes se agrupan en la misma `fund_family_id`, y luego la validación INTER-5 los marca como inconsistentes. El fix correcto **no es** corregir la Nature: es **no agrupar en primer lugar**.

### 3.2 Cambios concretos

#### 3.2.1 Fix D1: Tratar `Restantes` como adyacente universal

Localización: `core/fund_family_builder.py`, línea 142-149.

```python
# Antes:
_ADJACENT_NATURE_PAIRS = {
    frozenset({"Mixtos", "Renta Variable"}),
    frozenset({"Mixtos", "Renta Fija Flexible"}),
    frozenset({"Renta Fija Flexible", "Renta Fija Corto Plazo"}),
    frozenset({"Monetario", "Renta Fija Corto Plazo"}),
    frozenset({"Alternativo", "Renta Variable"}),
    frozenset({"Monetario", "Renta Variable"}),
}

# Después:
_ADJACENT_NATURE_PAIRS = {
    frozenset({"Mixtos", "Renta Variable"}),
    frozenset({"Mixtos", "Renta Fija Flexible"}),
    frozenset({"Mixtos", "Renta Fija Corto Plazo"}),       # NUEVO (cubre FAM_945,946)
    frozenset({"Renta Fija Flexible", "Renta Fija Corto Plazo"}),
    frozenset({"Monetario", "Renta Fija Corto Plazo"}),
    frozenset({"Alternativo", "Renta Variable"}),
    frozenset({"Monetario", "Renta Variable"}),
}

# NUEVO — naturalezas que SIEMPRE son adyacentes con cualquier otra
# (porque Restantes = fallback del clasificador → cualquier inconsistencia
# entre Restantes y otra Nature es error del bloque clasificador, no
# heterogeneidad real).
_UNIVERSAL_ADJACENT = {"Restantes"}
```

Y modificar la lógica de adyacencia en `_resolve_family_nature`. Localización: línea 219-220.

```python
# Antes:
if nature_set in _ADJACENT_NATURE_PAIRS or \
   any(frozenset(nature_set) == p for p in _ADJACENT_NATURE_PAIRS):

# Después:
def _is_adjacent_pair(nature_set: set[str]) -> bool:
    """Adyacencia entre Naturalezas. Universal_adjacent absorbe a cualquier otra."""
    if nature_set & _UNIVERSAL_ADJACENT:
        return True  # Restantes adyacente a todo
    fs = frozenset(nature_set)
    return any(fs == p for p in _ADJACENT_NATURE_PAIRS)

# y reemplazar:
if _is_adjacent_pair(nature_set):
```

#### 3.2.2 Fix D2: Tratar familias bipartitas con regla específica

Localización: insertar lógica nueva en `_resolve_family_nature` ANTES de la Regla 2 actual. La nueva regla aplica a familias de exactamente 2 miembros.

```python
# Después de:
#     if len(nature_set) <= 1:
#         return None, []  # ya es consistente
# Y antes de:
#     # Regla 2: mayoría ≥ 2/3 + discordantes con calidad baja

# === REGLA 2-bis: Familias bipartitas (2 miembros, 1-1 en Nature) ===
# Las familias de 2 miembros nunca alcanzan mayoría ≥ 2/3 con la regla
# clásica. Aplicamos resolución por calidad de datos cuando uno de los
# dos miembros tiene calidad claramente inferior.
if len(members) == 2:
    m_a, m_b = members[0], members[1]
    
    # Caso 2-bis-A: uno de los dos es 'Restantes' → el otro gana
    if m_a["Fund_Nature"] == "Restantes" and m_b["Fund_Nature"] != "Restantes":
        return m_b["Fund_Nature"], [m_a["ISIN"]]
    if m_b["Fund_Nature"] == "Restantes" and m_a["Fund_Nature"] != "Restantes":
        return m_a["Fund_Nature"], [m_b["ISIN"]]
    
    # Caso 2-bis-B: uno tiene Data_Quality_Flag MISSING/WARN y el otro OK
    dq_a = m_a.get("Data_Quality_Flag")
    dq_b = m_b.get("Data_Quality_Flag")
    if dq_a == "OK" and dq_b in ("MISSING", "WARN"):
        return m_a["Fund_Nature"], [m_b["ISIN"]]
    if dq_b == "OK" and dq_a in ("MISSING", "WARN"):
        return m_b["Fund_Nature"], [m_a["ISIN"]]
    
    # Caso 2-bis-C: uno tiene SRRI_Quality_Flag=NONE y el otro tiene HIGH/MEDIUM_*
    sq_a_rank = _SRRI_QUALITY_RANK.get(m_a.get("SRRI_Quality_Flag"), 0)
    sq_b_rank = _SRRI_QUALITY_RANK.get(m_b.get("SRRI_Quality_Flag"), 0)
    if sq_a_rank >= 2 and sq_b_rank == 0:
        return m_a["Fund_Nature"], [m_b["ISIN"]]
    if sq_b_rank >= 2 and sq_a_rank == 0:
        return m_b["Fund_Nature"], [m_a["ISIN"]]
    
    # Caso 2-bis-D: ambos tienen calidad equivalente → no determinable
    # Cae a regla 3 (adyacencia)
```

#### 3.2.3 Fix D3: Desempate por Data_Quality_Flag en regla 3

Localización: `_resolve_family_nature`, regla 3 (línea 218-238).

```python
# Antes (regla 3 actual):
if nature_set in _ADJACENT_NATURE_PAIRS or \
   any(frozenset(nature_set) == p for p in _ADJACENT_NATURE_PAIRS):
    quality_by_nature: dict[str, int] = {}
    for m in members:
        nat = m["Fund_Nature"]
        q = _SRRI_QUALITY_RANK.get(m.get("SRRI_Quality_Flag"), 0)
        quality_by_nature[nat] = quality_by_nature.get(nat, 0) + q

    if quality_by_nature:
        best_nature = max(quality_by_nature, key=quality_by_nature.get)
        best_q = quality_by_nature[best_nature]
        others_q = {k: v for k, v in quality_by_nature.items() if k != best_nature}
        if others_q and best_q - max(others_q.values()) > 2:
            to_correct = [m["ISIN"] for m in members
                          if m["Fund_Nature"] != best_nature]
            return best_nature, to_correct

# Después (regla 3 con desempate jerárquico):
if _is_adjacent_pair(nature_set):
    # Calidad agregada por Nature (SRRI primario, DQ secundario)
    srri_by_nature: dict[str, int] = {}
    dq_by_nature: dict[str, int] = {}
    for m in members:
        nat = m["Fund_Nature"]
        srri_by_nature[nat] = srri_by_nature.get(nat, 0) + \
            _SRRI_QUALITY_RANK.get(m.get("SRRI_Quality_Flag"), 0)
        # DQ rank: OK=2, WARN=1, MISSING=0, None=0
        dq_rank = {"OK": 2, "WARN": 1, "MISSING": 0}.get(
            m.get("Data_Quality_Flag"), 0
        )
        dq_by_nature[nat] = dq_by_nature.get(nat, 0) + dq_rank
    
    if srri_by_nature:
        # Criterio primario: SRRI_Quality
        best_nature = max(srri_by_nature, key=srri_by_nature.get)
        best_srri = srri_by_nature[best_nature]
        others_srri = {k: v for k, v in srri_by_nature.items() if k != best_nature}
        srri_diff = best_srri - max(others_srri.values()) if others_srri else 0
        
        # Si SRRI difiere claramente (>2), aplicar
        if srri_diff > 2:
            to_correct = [m["ISIN"] for m in members
                          if m["Fund_Nature"] != best_nature]
            return best_nature, to_correct
        
        # Si SRRI empata o difiere poco, desempatar por DQ
        if srri_diff >= 0:  # SRRI igual o mejor en best_nature
            best_dq_nature = max(dq_by_nature, key=dq_by_nature.get)
            best_dq = dq_by_nature[best_dq_nature]
            others_dq = {k: v for k, v in dq_by_nature.items() if k != best_dq_nature}
            dq_diff = best_dq - max(others_dq.values()) if others_dq else 0
            
            # DQ diferencia ≥ 1 puntos por familia y SRRI no contradice
            if dq_diff >= 1 and best_dq_nature == best_nature:
                to_correct = [m["ISIN"] for m in members
                              if m["Fund_Nature"] != best_nature]
                return best_nature, to_correct
```

#### 3.2.4 Fix D4: `_normalize_name` no debe colapsar "Income" como sufijo de clase

Este es el cambio más delicado. El sufijo `inc(?:ome)?` está pensado para capturar la variante de clase "Income" (distribución de rentas), pero está agrupando fondos cuyo nombre real contiene la palabra "Income" como parte del nombre del fondo (Templeton Global Income, GS Gbl Eq Income).

**Decisión de diseño aplicada:** preferir falsos negativos (no agrupar dos clases que sí son del mismo fondo) sobre falsos positivos (agrupar fondos distintos). Razón: un falso negativo deja un fondo solitario en su propia familia FAM_xxx (correcto desde el punto de vista de granularidad), mientras que un falso positivo destruye granularidad inventando una familia donde no la hay.

Localización: `core/fund_family_builder.py`, línea 67.

```python
# Antes:
| inc(?:ome)?             # Inc, Income

# Después:
| inc                     # Inc abreviado únicamente.
                          # "Income" como palabra completa NO se trata
                          # como sufijo de clase porque es ambiguo:
                          # puede ser parte del nombre del fondo
                          # (Templeton Global Income, GS Eq Income).
                          # Trade-off: dos clases hermanas que sólo
                          # difieran en el sufijo "Income" (raro)
                          # quedarán en familias separadas. Falso
                          # negativo aceptable; falso positivo no
                          # (BL-FAM-FIX D4).
```

**Verificación post-fix:** las familias FAM_001293 y FAM_001343 dejarán de existir como tales. Los fondos se redistribuirán en familias distintas (Templeton Global y Templeton Global Income en familias separadas; idem GS). La cuenta total de familias subirá ligeramente y la cuenta de inconsistencias bajará en 2.

### 3.3 Casos esperados como NO determinables tras los fixes

Tras aplicar D1-D4, los 11 casos del log se resuelven así:

| FAM | Nat A | Nat B | Defecto que aplica | Resolución esperada |
|---|---|---|---|---|
| 261 | RFCP | Restantes | D1 | Corregido por mayoría: el fondo Restantes hereda RFCP |
| 382 | RFCP | Restantes | D1 | Corregido por mayoría: el fondo Restantes hereda RFCP |
| 945 | Mixtos | RFCP | D2 | Resolución por calidad bipartita |
| 946 | Mixtos | RFCP | D2 | Resolución por calidad bipartita |
| 1293 | Mixtos | RV | D4 | **Familia disuelta**: Templeton Global ≠ Templeton Global Income |
| 1343 | Mixtos | RV | D4 | **Familia disuelta**: GS Gbl Eq ≠ GS Gbl Eq Income |
| 1697 | Alternativo | RV | (estructural) | Mantener: heterogeneidad intencional |
| 1776 | Mixtos | RFF | D2 | Resolución por calidad bipartita |
| 1897 | Alternativo | RV | (estructural) | Mantener: heterogeneidad intencional |
| 2121 | Mixtos | RFF | D2 | Resolución por calidad bipartita |
| (#11) | ? | ? | — | Por revelar (log mostraba "...y 1 más") |

Esperado tras el fix: ≥7 inconsistencias resueltas, ≤2 estructurales mantenidas, ≤2 disueltas por D4.

### 3.4 Tests específicos para Tarea 2

Crear `/tests/test_fund_family_builder_resolve.py`:

```python
"""Tests de _resolve_family_nature tras BL-FAM-FIX (D1-D4)."""

from core.fund_family_builder import (
    _resolve_family_nature,
    _is_structural_heterogeneity,
)


def _member(isin, name, nature, dq="OK", sq="MEDIUM_TEXT"):
    return {
        "ISIN": isin, "Fund_Name": name, "Fund_Nature": nature,
        "Data_Quality_Flag": dq, "SRRI_Quality_Flag": sq,
    }


# === D1: Restantes adyacente universal ===
def test_d1_restantes_majority_rfcp_wins():
    members = [
        _member("ISIN1", "BGF China Bond A", "Renta Fija Corto Plazo"),
        _member("ISIN2", "BGF China Bond B", "Renta Fija Corto Plazo"),
        _member("ISIN3", "BGF China Bond C", "Restantes"),
    ]
    nature, to_fix = _resolve_family_nature(members)
    assert nature == "Renta Fija Corto Plazo"
    assert to_fix == ["ISIN3"]


def test_d1_restantes_minority_does_not_override():
    """Si Restantes es mayoritario, no se aplica regla."""
    members = [
        _member("ISIN1", "X", "Restantes"),
        _member("ISIN2", "Y", "Restantes"),
        _member("ISIN3", "Z", "Renta Variable"),
    ]
    nature, to_fix = _resolve_family_nature(members)
    # Mayoría es Restantes (no debería ganar como respuesta)
    # → resolver por calidad o no determinable
    # (especificación abierta: aceptar None o 'Renta Variable' según calidad)
    assert nature in (None, "Renta Variable")


# === D2: Familias bipartitas ===
def test_d2_bipartite_one_restantes_wins_other():
    members = [
        _member("ISIN1", "DWS Multi Opp ACC", "Mixtos"),
        _member("ISIN2", "DWS Multi Opp ACC2", "Restantes"),
    ]
    nature, to_fix = _resolve_family_nature(members)
    assert nature == "Mixtos"
    assert to_fix == ["ISIN2"]


def test_d2_bipartite_dq_quality_decides():
    members = [
        _member("ISIN1", "X ACC", "Mixtos", dq="OK"),
        _member("ISIN2", "X INC", "Renta Fija Corto Plazo", dq="MISSING"),
    ]
    nature, to_fix = _resolve_family_nature(members)
    assert nature == "Mixtos"
    assert to_fix == ["ISIN2"]


def test_d2_bipartite_srri_quality_decides_when_dq_equal():
    members = [
        _member("ISIN1", "X ACC", "Mixtos", dq="OK", sq="HIGH"),
        _member("ISIN2", "X INC", "Renta Fija Corto Plazo", dq="OK", sq="NONE"),
    ]
    nature, to_fix = _resolve_family_nature(members)
    assert nature == "Mixtos"
    assert to_fix == ["ISIN2"]


def test_d2_bipartite_full_tie_returns_none():
    """Calidades equivalentes y no adyacentes → no determinable."""
    members = [
        _member("ISIN1", "X", "Renta Variable", dq="OK", sq="HIGH"),
        _member("ISIN2", "Y", "Estructurado",   dq="OK", sq="HIGH"),
    ]
    nature, to_fix = _resolve_family_nature(members)
    assert nature is None
    assert to_fix == []


# === D3: SRRI con desempate por DQ ===
def test_d3_srri_tie_dq_breaks():
    members = [
        _member("ISIN1", "A", "Mixtos", dq="OK",      sq="HIGH"),
        _member("ISIN2", "B", "Mixtos", dq="OK",      sq="HIGH"),
        _member("ISIN3", "C", "Renta Variable", dq="MISSING", sq="HIGH"),
    ]
    nature, to_fix = _resolve_family_nature(members)
    assert nature == "Mixtos"
    assert "ISIN3" in to_fix


# === D4: _normalize_name no colapsa "Income" como palabra ===
def test_d4_normalize_income_preserved():
    from core.fund_family_builder import _normalize_name
    n1 = _normalize_name("Templeton Global Income A EUR Acc")
    n2 = _normalize_name("Templeton Global A EUR Acc")
    # Tras el fix, NO deben colapsar a la misma forma
    assert n1 != n2
    # Y "income" debe aparecer en el primero
    assert "income" in n1


def test_d4_normalize_inc_abbreviation_still_stripped():
    """'Inc' como abreviatura sí se sigue tratando como sufijo."""
    from core.fund_family_builder import _normalize_name
    n1 = _normalize_name("XYZ Equity Fund A EUR Inc")
    n2 = _normalize_name("XYZ Equity Fund A EUR Acc")
    # Inc/Acc sí se eliminan por igual → mismo nombre normalizado
    assert n1 == n2
```

### 3.5 Validación de integración Tarea 2

Re-ejecutar `python proyecto1/core/fund_family_builder.py` después del fix. Esperado en log:

```
[FamilyBuilder] N familias identificadas (M con multiples clases) | 3204 ISINs
[FamilyBuilder] 3204 fondos actualizados con fund_family_id
[FamilyBuilder] Inconsistencias encontradas: ≤4   ← antes: 12
[FamilyBuilder]   Corregibles (regla escalable): ≥6   ← antes: 1
[FamilyBuilder]   Heterogeneidad estructural:    2    ← se mantiene (FAM_1697, FAM_1897)
[FamilyBuilder]   No determinables:              ≤2   ← antes: 9
[FamilyBuilder] ≥6 correcciones aplicadas
```

Validación SQL adicional:

```sql
-- Familias residuales con inconsistencia
SELECT fund_family_id, GROUP_CONCAT(DISTINCT Fund_Nature) AS natures, COUNT(*) AS n
FROM fund_master
WHERE fund_family_id IS NOT NULL
GROUP BY fund_family_id
HAVING COUNT(DISTINCT Fund_Nature) > 1;
-- Esperado: ≤4 filas, todas justificadas (heterogeneidad estructural o casos límite)

-- Templeton Global e Income deben estar en familias DIFERENTES
SELECT Fund_Name, fund_family_id FROM fund_master
WHERE Fund_Name LIKE 'TEMPLETON GLOBAL%';
-- Esperado: "TEMPLETON GLOBAL ..." y "TEMPLETON GLOBAL INCOME ..." con
--           fund_family_id distintos

-- GS Gbl Eq vs GS Gbl Eq Income igual
SELECT Fund_Name, fund_family_id FROM fund_master
WHERE Fund_Name LIKE 'GS GBL EQ%';
-- Esperado: dos fund_family_id distintos según presencia/ausencia de "INCOME"
```

---

## SECCIÓN 4 — DELIVERABLES

Sonnet entregará al cerrar el sprint:

1. **Código modificado:**
   - `/core/classify_utils.py`
   - `/blocks/mixtos.py`
   - `/core/fund_family_builder.py`
2. **Tests creados:**
   - `/tests/test_classify_utils_family_constants.py`
   - `/tests/test_fund_family_builder_resolve.py`
3. **Log de ejecución del pipeline post-fix.**
4. **Resultado de las 5 queries de validación de Tarea 1 + las 3 queries de Tarea 2.**
5. **Documento `/ESTADO_BACKLOG_APR2026_v3_3.md`** que contiene:
   - BL-57 marcado RESUELTO con la lección estructural completa.
   - BL-FAM-FIX RESUELTO con detalle de los 4 defectos corregidos y casos resultantes.
   - BL-58 ABIERTO listando los emisores latentes y el refactor pendiente:
     - `mixtos.py:126` → constante `FAMILY_LIFECYCLE`
     - `mixtos.py:128` → constante `FAMILY_RETIREMENT`
     - `fund_characterizer.py:680-681` → constantes `FAMILY_RFCP_CANONICAL`, `TYPE_*`
     - Refactor `ALLOWED_FAMILY_BY_NATURE` y `_DEFAULT_FAMILY_BY_NATURE` a constantes
     - Función `_validate_family_catalog_consistency()` con check al importar
   - **Norma BL-57 v3** incorporada al cuerpo de Principios Consolidados (ver Sección 5).
6. **Reporte breve** de discrepancias o decisiones tomadas fuera de la especificación. Idealmente vacío.

---

## SECCIÓN 5 — NORMA BL-57 v3 (TEXTO PARA INCORPORAR A `ESTADO_BACKLOG_APR2026_v3_3.md`)

```markdown
### Norma BL-57 v3 — Migración segura de literales categóricos

**Origen:** regresión silenciosa del 26-abr-2026 donde 104 fondos fueron
absorbidos al default `Family='Mixtos'` por desincronización entre el
emisor primario (`blocks/mixtos.py:130`) y el validador
`ALLOWED_FAMILY_BY_NATURE`.

**Diagnóstico:** la migración BL-57 v2 (25-abr) actualizó el validador, los
mapas de traducción y los normalizadores SQL, pero no localizó todos los
emisores. El emisor primario siguió emitiendo el literal viejo
(`'Income Oriented'`); el validador INTER-5 lo rechazó; la auto-corrección
P07 lo redirigió silenciosamente al default de la Nature
(`'Mixtos'`). La cadena de redes de seguridad quedó inerte porque
el literal viejo nunca llegó a la BD.

**Norma:** toda migración futura de literal categórico (Family, Type,
Subtype, Nature, etc.) debe seguir esta secuencia obligatoria:

1. **Localizar emisores:** `grep -rn '"<literal_viejo>"' blocks/ core/ --include="*.py"`.
   Si hay un emisor activo en código no-legacy, **migrarlo primero**.
2. **Constituir constante:** definir una constante canónica del nuevo
   literal en `core/classify_utils.py` y usarla por importación en
   todos los emisores.
3. **Migrar emisores:** sustituir el literal inline por la constante
   en cada punto de emisión.
4. **Actualizar validador:** añadir el nuevo literal a
   `ALLOWED_FAMILY_BY_NATURE` (o equivalente).
5. **Mantener red de seguridad:** la entrada en `FAMILY_TRANSLATION_MAP`
   y las cláusulas `CASE WHEN` SQL se mantienen para protección frente a
   regresiones futuras desconocidas.
6. **Validar conservación poblacional:** ejecutar el patrón de tres queries
   tras el ciclo del pipeline:

   ```sql
   SELECT COUNT(*) FROM fund_master WHERE <col> = '<literal_viejo>';     -- Objetivo: 0
   SELECT COUNT(*) FROM fund_master WHERE <col> = '<literal_nuevo>';     -- Objetivo: N
   SELECT COUNT(*) FROM fund_master WHERE <col> IN ('<viejo>','<nuevo>') -- Objetivo: N
        AND ISIN IN (<lista_pre_migración>);
   ```

   Si la tercera query devuelve <N, **hay regresión semántica encubierta**:
   los fondos perdidos están en otro literal (probablemente el default de
   Nature). Bloquear el cierre del BL hasta resolver.
```

---

## SECCIÓN 6 — POLÍTICA DE ESCALADO

Sonnet debe **DETENERSE y consultar** antes de:

- Modificar literales de Family/Type/Subtype/Nature **fuera de los cambios listados explícitamente** en Secciones 2 y 3.
- Tocar `ALLOWED_FAMILY_BY_NATURE`, `_DEFAULT_FAMILY_BY_NATURE` o cualquier mapa de validación INTER-x. Cambios autorizados: solo añadir el par adyacente `("Mixtos", "Renta Fija Corto Plazo")` y la constante `_UNIVERSAL_ADJACENT` según Sección 3.2.1.
- Eliminar redes de seguridad (`FAMILY_TRANSLATION_MAP`, traducciones SQL post-UPSERT, controles SELECT, comentarios documentales).
- Cambiar firmas públicas de funciones existentes (`build_fund_families`, `correct_family_inconsistencies`, `_resolve_family_nature`).
- Cualquier cambio en `pipeline.py`, `sqlite_writer.py`, `fund_characterizer.py` o cualquier otro fichero no listado en la Sección 4.
- Cualquier cosa que la especificación no cubra explícitamente y que afecte a más de un fichero.

Sonnet **puede proceder sin consultar** para:

- Corrección de erratas en docstrings y comentarios.
- Imports faltantes que el código nuevo requiera.
- Tests adicionales más allá de los mínimos especificados (sin cambiar la lógica de los tests obligatorios).
- Logging adicional con prefijo `[BL-57v3]` o `[BL-FAM-FIX]` para trazabilidad.
- Reformateo de líneas largas (≤100 cols).
- Reordenación interna de un mismo bloque cuando no afecte al comportamiento.

Si Sonnet detecta durante la implementación un caso no cubierto por la especificación que requeriría decisión de diseño, debe:

1. Implementar la opción más conservadora (la que menos cambia comportamiento).
2. Documentar el caso y la decisión tomada en el reporte de la Sección 4.6.
3. Marcar el caso en `ESTADO_BACKLOG_APR2026_v3_3.md` como punto a revisar en BL-58.

---

## SECCIÓN 7 — ORDEN DE EJECUCIÓN RECOMENDADO

1. Tarea 1 completa: constante + import + tests + ejecutar pipeline + queries A-E.
2. **Punto de control 1**: validar que conteos esperados se cumplen (Mixtos≈365, Orientado a Renta≈104).
3. Tarea 2.D4 aislado: cambiar `_normalize_name` + tests D4. Re-ejecutar `fund_family_builder` standalone.
4. **Punto de control 2**: validar que FAM_1293 y FAM_1343 desaparecen como inconsistencias.
5. Tarea 2.D1 + D2 + D3: añadir reglas escalables + tests. Re-ejecutar `fund_family_builder`.
6. **Punto de control 3**: validar que el log muestra ≤4 inconsistencias residuales.
7. Tarea 4 (deliverables): redactar `ESTADO_BACKLOG_APR2026_v3_3.md` con la lección estructural y los BL nuevos.

Si en el Punto de control 2 el matching de familias produce >50 familias nuevas (efecto colateral inesperado de D4), parar y consultar antes de continuar con D1-D3.

---

**FIN DE ESPECIFICACIÓN**
