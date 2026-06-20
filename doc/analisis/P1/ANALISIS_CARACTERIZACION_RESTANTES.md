# Análisis de Caracterización: Bloque RESTANTES

**Fecha:** 11 abril 2026  
**Alcance:** 3.204 fondos (2.322 en RESTANTES con Nature='Restantes')  
**Fuentes:** `p1_export_20260411.xlsx` (con Raw_KIID_Text), `classify_utils.py`, `restantes.py`, `pipeline.py`

---

## 1. DIAGNÓSTICO EJECUTIVO

El bloque RESTANTES contiene 2.322 fondos (72,4% del universo total). Estos fondos tienen una caracterización media de **10,4 atributos de 23** (45%), frente a **14,0 en Renta Variable** y **14,2 en RF Flexible**. La causa raíz de esta brecha no es una deficiencia algorítmica generalizada — es un **bug específico en una línea de código** que provoca el colapso silencioso de todo el mecanismo de delegación.

### Hallazgo principal

La línea 199 de `restantes.py` llama a `_apply_semantic_validation(result, fund_name)`, una función que **no existe**. La función correcta, importada en la línea 60, se llama `apply_semantic_validation` (sin guion bajo inicial). Este NameError se ejecuta dentro de un bloque `try/except: pass` (líneas 194-200), lo que causa que:

1. La detección de naturaleza funciona correctamente para ~2.139 fondos (92,1%)
2. La delegación al bloque primario se intenta y posiblemente tiene éxito
3. Al retornar, el NameError lanza una excepción silenciosa
4. El fondo cae al fallback que **sobreescribe** `Fund_Nature = "Restantes"` y establece `Type = None`, `Family = None`

**Impacto:** un cambio de una línea (`_apply_semantic_validation` → `apply_semantic_validation`) desbloquearía la clasificación completa para ~2.139 fondos.

---

## 2. ANÁLISIS CUANTITATIVO DETALLADO

### 2.1 Distribución de atributos poblados por bloque

| Bloque | Fondos | Media attrs | Min | Max | Mediana |
|---|---|---|---|---|---|
| Renta Variable | 680 | 14,0 | 8 | 18 | 14 |
| RF Flexible | 28 | 14,2 | 12 | 16 | 14 |
| Estructurado | 8 | 14,0 | 14 | 14 | 14 |
| Alternativo | 29 | 14,6 | 11 | 17 | 15 |
| Mixtos | 102 | 12,1 | 9 | 15 | 12 |
| RF Corto Plazo | 14 | 11,7 | 10 | 14 | 11 |
| Monetario | 21 | 10,5 | 8 | 12 | 11 |
| **Restantes** | **2.322** | **10,4** | **3** | **15** | **10-11** |

### 2.2 Columnas con mayor brecha RESTANTES vs Especializados

| Columna | REST NULL% | ESPEC NULL% | Gap | Causa raíz |
|---|---|---|---|---|
| **Type** | **100,0%** | 0,0% | +100 | Bug delegación + fallback sin asignación |
| **Family** | **100,0%** | 0,9% | +99 | Bug delegación + fallback sin asignación |
| **Exposure_Bias** | 91,2% | 20,3% | +71 | Detección solo por nombre, no por KIID |
| **Credit_Quality** | 49,3% | 15,5% | +34 | Patrones insuficientes en KIID |
| **Style_Profile** | 76,9% | 56,5% | +21 | Detección solo por nombre, no por KIID |
| Subtype | 100,0% | 89,9% | +10 | No asignado en fallback |
| Geography | 15,5% | 7,9% | +8 | Cobertura parcial en KIID |
| Profile | 5,1% | 0,0% | +5 | 16 fondos sin SRRI |

### 2.3 Columnas donde RESTANTES funciona bien

| Columna | REST NULL% | Comentario |
|---|---|---|
| Fund_Nature | 0,0% | Siempre "Restantes" (erróneamente) |
| Investment_Focus | 0,0% | 100% poblado (fund_characterizer) |
| Theme | 4,0% | 96% poblado — mejor que especializados (25,2% NULL) |
| Profile | 5,1% | 94,9% poblado vía SRRI |
| Investment_Universe | 9,6% | 90,4% poblado (fund_characterizer) |

---

## 3. ANATOMÍA DEL FALLO

### 3.1 El bug: `_apply_semantic_validation` (línea 199)

```python
# restantes.py, líneas 194-200 (producción actual)
block_name = _NATURE_TO_BLOCK.get(nature_raw)
if block_name:
    try:
        block_mod = importlib.import_module(f"blocks.{block_name}")
        result = block_mod.classify_fund(fund_name, kiid_text)
        result["Fund_Nature"] = nature_canonical
        return _apply_semantic_validation(result, fund_name)  # ← NameError
    except Exception:
        pass  # ← Tragado silenciosamente

# Línea 60: from core.classify_utils import apply_semantic_validation
# → SIN guion bajo. _apply_semantic_validation NO EXISTE en ningún módulo.
```

**Fix:** Cambiar línea 199 a `return apply_semantic_validation(result, fund_name)`

### 3.2 El SRRI fantasma

`detect_nature_from_kiid()` contiene un fallback SRRI al final (líneas ~1218-1224) que busca el patrón `\b([1-7])\s*/\s*7\b`. Este patrón NO funciona para documentos DDF/PRIIPs, que expresan el riesgo como "clase de riesgo N en una escala de 7", sin la barra `/`.

**Verificación empírica:** De los 2.322 fondos RESTANTES, ninguno contiene el patrón `X/7` en su texto. El 99% son DDFs en español con el formato "hemos clasificado este compartimento en la clase de riesgo N".

`kiid_parser.py` SÍ extrae correctamente el SRRI con patrones L0 más sofisticados, pero este valor parseado **no se pasa a `restantes.classify_fund()`** — la función solo recibe `fund_name`, `kiid_text` y `benchmark_declared`.

### 3.3 Potencial de detección real

Ejecuté una simulación de `detect_nature_from_kiid` sobre los 2.322 textos KIID de fondos RESTANTES:

| Método | Fondos detectados | % |
|---|---|---|
| Solo patrones textuales | 2.139 | 92,1% |
| + SRRI de BD como fallback | 2.212 | 95,3% |
| **Sin detectar** | **110** | **4,7%** |

Desglose por naturaleza detectada (con SRRI de BD):

| Naturaleza detectada | Fondos | Ejemplos |
|---|---|---|
| Renta Variable | 1.102 | AB Select US Eq, Allianz AI, fondos equity genéricos |
| RF (Corto/Flexible) | 825 | AB Fixed Maturity, fondos bond/HY/IG |
| Mixtos | 167 | Multi-asset, balanced, allocation |
| Monetario | 97 | Enhanced short term, money market |
| Alternativo | 21 | Absolute return, long/short |
| Sin detectar | 110 | Nombres opacos, SRRI=3-4, sin señales textuales |

### 3.4 El `except: pass` — Patrón anti-diagnóstico

Este antipatrón es especialmente dañino porque:

1. Oculta el NameError que causa el 100% de las delegaciones fallidas
2. Oculta posibles errores reales en los bloques primarios (ej: el log muestra "claves no canónicas" en RV para 5 fondos)
3. No deja rastro en logs — la caída al fallback es silenciosa
4. Hace imposible distinguir entre "delegación intentada y fallida" vs "naturaleza no detectada"

---

## 4. PROBLEMAS DE CALIDAD DE DATOS

### 4.1 Inconsistencias de casing (Principio #8)

**Accumulation_Policy** — 4 variantes para 2 conceptos:

| Valor actual | Fondos | Valor correcto |
|---|---|---|
| `Accumulation` | 1.572 | `ACCUMULATION` |
| `ACCUMULATION` | 319 | `ACCUMULATION` |
| `DISTRIBUTION` | 465 | `DISTRIBUTION` |
| `Distribution` | 58 | `DISTRIBUTION` |

**Causa:** Dos fuentes de asignación con convenciones diferentes — probablemente bloques especializados usan Title Case y `fund_characterizer` usa UPPER.

### 4.2 Valores inválidos

**Currency_Hedged** — valor `"Yes"` no está en el dominio permitido:

| Valor | Fondos | Acción |
|---|---|---|
| `Hedged` | 436 | Correcto |
| `Yes` | 198 | → Mapear a `Hedged` |

Falta `"Unhedged"` — 0 fondos lo tienen pese a que 2.570 fondos no tienen la columna poblada.

### 4.3 Detección unidireccional

**Derivatives_Usage:** Solo detecta "YES" (1.898 fondos). Nunca detecta "NO" ni "LIMITED". Los 1.306 fondos sin valor probablemente incluyen fondos que SÍ mencionan derivados con matices ("may use", "limited use") o que NO mencionan derivados en absoluto.

**Leverage_Used:** Solo detecta "YES" (499) y "LIMITED" (279). Solo 2 fondos tienen "NO". Los 2.424 sin valor probablemente incluyen la mayoría de fondos que simplemente no usan apalancamiento.

### 4.4 Falsos positivos en detect_style_from_kiid

El patrón `"crecimiento del capital a largo plazo"` está incluido como señal de estilo Growth (línea 1444 de classify_utils.py). Esta frase es **genérica** — aparece en el objetivo estándar de cualquier fondo que busque revalorización. Afecta a 142 fondos RESTANTES y probablemente más en especializados. Debería eliminarse de los patrones Growth o requerir co-ocurrencia con términos más específicos como "empresas de alto crecimiento" o "growth stocks".

### 4.5 Funciones duplicadas en classify_utils.py

4 funciones están definidas dos veces (Python usa la última definición, sin error):

| Función | Líneas |
|---|---|
| `detect_nature_from_name` | 789 y 883 |
| `_detect_kiid_format` | 837 y 931 |
| `_get_obj_bounds` | 872 y 966 |
| `_extract_window` | 878 y 972 |

Riesgo: Si las versiones difieren, la primera es dead code invisible. Si son iguales, es deuda técnica que dificulta mantenimiento.

---

## 5. PLAN DE TRABAJO REVISADO

### FASE 0: Bug fix crítico (impacto: ~2.139 fondos) — 5 minutos

**Acción única:** Cambiar línea 199 de `restantes.py`:

```python
# ANTES (bug):
return _apply_semantic_validation(result, fund_name)

# DESPUÉS (fix):
return apply_semantic_validation(result, fund_name)
```

**Impacto esperado:** ~2.139 fondos (92,1%) obtienen Nature correcta + Type + Family vía delegación a bloques primarios. La media de atributos para RESTANTES sube de 10,4 a ~13-14.

**Validación:** Re-ejecutar bloque RESTANTES y verificar:
```sql
SELECT Fund_Nature, COUNT(*) FROM fund_master
WHERE Heuristic_Block = 'RESTANTES'
GROUP BY Fund_Nature ORDER BY 2 DESC;
-- Esperado: Renta Variable ~1.100, RF ~825, Mixtos ~167, etc.
-- Restantes residual: ~110-183
```

### FASE 1: Mejoras en la infraestructura de delegación (impacto: ~73 fondos adicionales)

**1A. Eliminar `except: pass` y reemplazar con logging**

```python
except Exception as exc:
    logger.error(
        "[%s] Delegación a bloque '%s' fallida: %s",
        fund_name, block_name, exc,
    )
    # Mantener nature_canonical detectada para la vía fallback
```

**1B. Pasar SRRI parseado como argumento**

Modificar `pipeline.py` para pasar SRRI de BD a `restantes.classify_fund()`:
```python
classification = classifier(fund_name, kiid_text,
                            benchmark_declared=_bench,
                            srri_parsed=parsed.get("SRRI"))
```

Y en `restantes.py`, usar `srri_parsed` en Capa 3 en lugar del regex `X/7`.

Impacto: Cubre los ~73 fondos donde patrones textuales fallan pero SRRI de BD sí permite clasificar (principalmente SRRI=1→Monetario y SRRI=2→RF en DDFs sin texto objetivo reconocible).

**1C. Preservar naturaleza detectada en fallback**

Cuando la delegación falla pero la naturaleza SÍ fue detectada, el fallback debería usar la naturaleza detectada (no "Restantes"):

```python
# En el fallback (línea ~202):
result: Dict[str, Optional[str]] = {
    "Fund_Nature": nature_canonical,  # ← NO "Restantes" si fue detectada
    ...
}
```

Esto permite que `detect_kiid_attributes` y `detect_type_from_kiid` funcionen con la naturaleza correcta.

### FASE 2: Type y Family para fondos residuales (~110 fondos sin detectar)

Para los ~110 fondos que permanecen sin naturaleza detectable:

**2A. Ampliar `detect_type_from_kiid` para Nature="Restantes"**

Añadir rama genérica que infiera Type sin conocer la naturaleza:

```python
elif fund_nature == "Restantes" or fund_nature is None:
    # Inferir Type genérico desde señales textuales
    if any(k in w for k in ["gestión activa", "active management", "discreción"]):
        return "Gestión Activa"
    if any(k in w for k in ["indexado", "index tracking", "réplica", "seguimiento del índice"]):
        return "Indexado"
    return "Gestión Activa"  # Default conservador
```

**2B. Inferir Family desde combinación de atributos ya poblados**

Crear `infer_family_from_context()` que combine Theme, Investment_Universe, Geography y SRRI:

| Condición | Family inferida |
|---|---|
| Theme ≠ 'Core/General' + SRRI ≥ 4 | RV Temática |
| Investment_Universe = 'Sector' | RV Temática |
| SRRI ≤ 2 + Geography específica | RF según contexto |
| SRRI ≥ 5 + Geography específica | RV Core |
| SRRI = 3-4 | Mixtos (por defecto) |

### FASE 3: Mejoras en atributos secundarios

**3A. Exposure_Bias — ampliar detección en KIID** (gap actual: 71 puntos)

Los bloques especializados detectan Exposure_Bias en el 80% de fondos, pero `detect_exposure_bias` en classify_utils.py solo busca en el nombre. Añadir búsqueda en ventana objetivo para patrones como "duración", "diferencial de crédito", "income/rentas" con contexto discriminante (no palabras genéricas aisladas).

**3B. Credit_Quality — ampliar detección en KIID** (gap actual: 34 puntos)

Verificado en los textos: "investment grade" y "grado de inversión" aparecen en ~486 fondos RESTANTES sin Credit_Quality poblado. "High yield" / "alto rendimiento" en ~89. Estos patrones son bastante específicos y fiables.

**3C. Style_Profile — corregir falso positivo y ampliar** (gap actual: 21 puntos)

Eliminar `"crecimiento del capital a largo plazo"` como señal de Growth (es genérico). Mantener señales más específicas como "empresas de alto crecimiento", "growth stocks". Actualmente 142 fondos podrían recibir un Style_Profile=Growth incorrecto por esta frase.

### FASE 4: Normalización de datos (esfuerzo bajo)

**4A. Normalizar Accumulation_Policy** — unificar a UPPER:
```python
ACCUM_NORMALIZATION = {
    'Accumulation': 'ACCUMULATION',
    'Distribution': 'DISTRIBUTION',
}
```

**4B. Normalizar Currency_Hedged** — mapear "Yes" → "Hedged":
```python
CURRENCY_HEDGED_NORMALIZATION = {'Yes': 'Hedged'}
```

**4C. Detectar "NO" y "LIMITED" en Derivatives_Usage/Leverage_Used**

Añadir lógica de detección explícita de ausencia: si el KIID no menciona derivados/apalancamiento en absoluto → "NO". Si menciona "may use" / "limited use" → "LIMITED".

**4D. Eliminar funciones duplicadas en classify_utils.py**

Eliminar las 4 definiciones redundantes (líneas 789-878) y mantener solo las activas (líneas 883-976).

---

## 6. PRIORIZACIÓN Y CALENDARIO

| Fase | Acción | Fondos impactados | Esfuerzo | Atributos ganados |
|---|---|---|---|---|
| **0** | **Fix NameError línea 199** | **~2.139** | **5 min** | **Type, Family + delegación completa** |
| 1A | Eliminar except:pass | Debug/visibilidad | 10 min | — |
| 1B | Pasar SRRI de BD | ~73 | 30 min | Nature + delegación |
| 1C | Preservar nature en fallback | ~110 | 15 min | Nature correcta |
| 2A | Type para Restantes residual | ~110 | 1h | Type |
| 2B | Family inferida por contexto | ~110 | 2h | Family |
| 3A | Exposure_Bias en KIID | ~1.500+ | 2h | Exposure_Bias |
| 3B | Credit_Quality en KIID | ~575 | 1h | Credit_Quality |
| 3C | Style_Profile fix + ampliar | ~200 | 1h | Style_Profile |
| 4A-D | Normalizaciones | ~2.200 | 2h | Calidad de datos |

**Impacto total estimado:** La media de atributos para RESTANTES pasaría de 10,4 a ~13,5-14,0 (alineada con bloques especializados).

---

## 7. VERIFICACIÓN POST-FIX

### Queries de control tras Fase 0

```sql
-- 1. Distribución de Nature para RESTANTES block
SELECT Fund_Nature, COUNT(*) cnt
FROM fund_master WHERE Heuristic_Block = 'RESTANTES'
GROUP BY Fund_Nature ORDER BY cnt DESC;
-- Esperado: RV ~1100, RF_Flex ~600, RF_Corto ~200, Mixtos ~167, etc.

-- 2. Type ya no debería ser 100% NULL
SELECT Type, COUNT(*) FROM fund_master
WHERE Heuristic_Block = 'RESTANTES' AND Type IS NOT NULL
GROUP BY Type ORDER BY 2 DESC;

-- 3. Family ya no debería ser 100% NULL
SELECT Family, COUNT(*) FROM fund_master
WHERE Heuristic_Block = 'RESTANTES' AND Family IS NOT NULL
GROUP BY Family ORDER BY 2 DESC;

-- 4. Media de atributos poblados
SELECT AVG(attr_count) FROM (
  SELECT ISIN,
    (CASE WHEN Fund_Nature IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN Type IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN Family IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN Profile IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN Geography IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN Investment_Universe IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN Strategy IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN Style_Profile IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN Exposure_Bias IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN Theme IS NOT NULL THEN 1 ELSE 0 END
    ) as attr_count
  FROM fund_master WHERE Heuristic_Block = 'RESTANTES'
);
```

---

## 8. RIESGOS Y CONSIDERACIONES

**Riesgo 1 — Delegación exitosa pero con errores en bloques primarios:** El fix de Fase 0 reenviará ~2.139 fondos a bloques que nunca los procesaron. Podrían aparecer errores como los "claves no canónicas" ya observados en RV (5 fondos en el log). Fase 1A (eliminar except:pass) es necesaria para detectar estos casos.

**Riesgo 2 — Volumen de re-procesamiento:** Ejecutar RESTANTES con el fix afecta 2.330 fondos. El tiempo de ejecución será significativamente mayor porque cada delegación ejecuta el clasificador del bloque primario completo. Estimar ~5-10 minutos vs los ~2 minutos actuales.

**Riesgo 3 — Regresiones en fondos ya clasificados correctamente:** Los 8 fondos Estructurado que SÍ funcionan actualmente no deberían verse afectados (su detección usa un path diferente al de delegación). Verificar post-fix.

**Riesgo 4 — Falsos positivos de naturaleza:** Mi simulación muestra 2.139 detecciones, pero algunas podrían ser incorrectas (ej: un fondo mixto clasificado como RV porque "renta variable" aparece como mención incidental). Las validaciones semánticas (Principio #9) deberían atrapar la mayoría de estos casos vía `apply_semantic_validation`.
