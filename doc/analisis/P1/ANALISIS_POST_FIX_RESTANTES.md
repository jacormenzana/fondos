# Análisis Post-Fix: Caracterización RESTANTES

**Fecha:** 11 abril 2026
**Estado:** Tras aplicar Fase 0 (fix NameError) + Paso 1A (eliminar except:pass)

---

## 1. RESUMEN DE IMPACTO

La corrección del NameError en línea 199 de `restantes.py` ha producido un cambio transformador:

| Métrica | Pre-fix | Post-fix | Cambio |
|---|---|---|---|
| Fondos con Nature='Restantes' | 2.322 | 25 | -98,9% |
| Type NULL% (bloque RESTANTES) | 100,0% | 1,1% | -98,9 pp |
| Family NULL% (bloque RESTANTES) | 100,0% | 1,4% | -98,6 pp |
| Media atributos (bloque RESTANTES) | 10,4 | 13,3 | +27,9% |
| Media atributos (portfolio completo) | — | 13,4 | — |
| Exposure_Bias NULL% (bloque RESTANTES) | 91,2% | 15,8% | -75,4 pp |

La delegación funciona correctamente para 2.305 de 2.330 fondos. Los 25 restantes son fondos con textos KIID que no contienen señales reconocibles por los patrones actuales (mayoritariamente fondos JPMorgan con OCR problemático y fondos cuyo nombre no está en las listas NAME_SIGNALS).

---

## 2. DISTRIBUCIÓN POST-FIX POR NATURALEZA

| Fund_Nature | Fondos | Type | Family | Avg attrs |
|---|---|---|---|---|
| Renta Variable | 1.093 | 100% | 100% | 13,8 |
| Renta Fija Flexible | 435 | 100% | 100% | 13,5 |
| Renta Fija Corto Plazo | 414 | 100% | 100% | 12,7 |
| Mixtos | 240 | 100% | 100% | 13,2 |
| Monetario | 84 | 100% | 100% | 12,0 |
| Alternativo | 31 | 100% | 100% | 12,5 |
| Estructurado | 8 | 100% | 100% | 14,0 |
| Restantes | 25 | 0% | 0% | 9,0 |

---

## 3. PROBLEMAS DETECTADOS

### 3.1 Los 25 fondos residuales

Son 12 JPMorgan, 6 Fidelity, 3 Vanguard, 1 Carmignac, 1 DWS, 1 MFS, 1 Rothschild. Los tres mecanismos de detección fallan para todos ellos:

**Capa 1 (KIID texto):** Los fondos JPMorgan tienen textos OCR con layout de dos columnas que desplazan la sección de objetivo fuera de la ventana [500:5000]. Los fondos Fidelity/Vanguard tienen DDFs en español donde las frases clave no coinciden con los patrones actuales.

**Capa 2 (nombre del fondo):** Los nombres usan abreviaciones no cubiertas por NAME_SIGNALS. Patrones ausentes: "stk" (stock), "indx" (index), "bnd" (bond), "aggregate", "divdnd"/"dividend" (como señal genérica), "ashare", "genetic therap", "infrastr".

**Capa 3 (SRRI):** El SRRI regex `\b([1-7])\s*/\s*7\b` no funciona para DDFs que usan el formato "clase de riesgo N en una escala de 7" sin barra `/`.

**Resolución factible para 22 de 25:**

| Método | Fondos resolubles | ISINs ejemplo |
|---|---|---|
| SRRI BD ≥5 → RV | 5 | JPM CHINA ASHARE, JPM GENETIC THERAP, CARMIGNAC |
| SRRI BD ≤2 → RF | 7 | JPM AGGREGATE, JPM GBL SRT DR BND, MFS CREDIT |
| Nombre "stk"/"indx"/"dividend" → RV | 5 | Vanguard STK INX, Fidelity DIVIDEND |
| Nombre "bnd"/"aggregate"/"credit" → RF | 2 | JPM US AGGREGATE, JPMORGAN EURO AGGRE |
| Nombre señales EM → RV | 3 | JPM EMERG MKT, FIDELITY GBL DIV |
| **Sin resolver** | **3** | DWS INFRASTR (SRRI=None), FIDELITY EM EU M EA AF (SRRI=None) |

### 3.2 Regresión en Theme

La delegación exitosa ha provocado una regresión en Theme para dos naturalezas: Monetario cayó de ~96% a 2,4% y RF Corto Plazo de ~96% a 1,9%.

**Causa raíz:** El fallback pre-fix llamaba a `detect_theme(name_l)` que asignaba "Core/General" a casi todos los fondos. Ahora, la delegación envía estos fondos a los bloques `monetarios.py` y `rf_corto.py`, que no asignan Theme. Tras la delegación, `detect_kiid_attributes()` se ejecuta pero solo rellena atributos NULL del resultado del bloque — y el bloque ya devolvió un dict sin key "Theme" (no es NULL, es ausente), por lo que no se rellena.

**Solución:** En el flujo de delegación dentro de `restantes.py`, tras obtener el resultado del bloque primario, aplicar `detect_theme(name_l)` si Theme no está poblado:

```python
result = block_mod.classify_fund(fund_name, kiid_text)
result["Fund_Nature"] = nature_canonical
# Enriquecer con atributos que el bloque no cubre
if not result.get("Theme"):
    result["Theme"] = _detect_theme(name_l) or "Core/General"
```

Fondos afectados: ~496 (84 Monetario + 414 RF Corto Plazo, menos los ~10 que ya tienen Theme).

### 3.3 Mixtos sin Exposure_Bias

Los 240 fondos Mixtos delegados tienen 0% de Exposure_Bias poblado. El bloque `mixtos.py` no asigna este atributo, y el characterizer tampoco lo rellena para fondos mixtos. Para fondos mixtos, un valor por defecto de "Strategic Allocation" o NULL es aceptable conceptualmente (no tienen un sesgo de exposición dominante), pero convendría documentar esta decisión explícitamente.

### 3.4 Inconsistencias lingüísticas (Principio #8)

**Accumulation_Policy — 4 variantes para 2 conceptos (todo el portfolio):**

| Valor actual | Fondos | Corrección |
|---|---|---|
| `Accumulation` | 1.569 | → `ACCUMULATION` |
| `ACCUMULATION` | 322 | ✅ correcto |
| `DISTRIBUTION` | 465 | ✅ correcto |
| `Distribution` | 58 | → `DISTRIBUTION` |

Causa: Los bloques especializados devuelven Title Case; `fund_characterizer` devuelve UPPER. La normalización debe aplicarse en un punto único (idealmente `apply_semantic_validation` o `sqlite_writer.py`).

**Currency_Hedged — valor "Yes" no pertenece al dominio:**

| Valor | Fondos | Corrección |
|---|---|---|
| `Hedged` | 436 | ✅ correcto |
| `Yes` | 198 | → `Hedged` |

Falta: 2.570 fondos sin Currency_Hedged. El valor `Unhedged` nunca se detecta.

### 3.5 Style_Profile='Tactical' — valor no permitido

6 fondos Mixtos (3 Allianz Strategy, 2 JPM Glob Macro, 1 M&G Episode Macro) reciben `Style_Profile='Tactical'` que no está en la lista de valores permitidos. Dos opciones:

**Opción A:** Añadir "Tactical" a `ALLOWED_VALUES_BY_COLUMN['Style_Profile']` — es un estilo legítimo para fondos de asignación táctica.

**Opción B:** Mapear "Tactical" → "Strategic Allocation" — si se quiere simplificar la taxonomía.

Recomendación: **Opción A**, porque estos fondos son genuinamente tácticos (asignación dinámica entre clases de activos) y no estratégicos.

### 3.6 Detección unidireccional persistente

Estos problemas existían pre-fix y persisten:

**Derivatives_Usage:** Solo detecta "YES" (1.898 fondos). Los 1.306 sin valor incluyen fondos que no mencionan derivados (deberían ser "NO") o que los mencionan con matices ("LIMITED").

**Leverage_Used:** Solo detecta "YES" (499) y "LIMITED" (279). Los 2.424 sin valor incluyen fondos que simplemente no usan apalancamiento (deberían ser "NO").

### 3.7 Funciones duplicadas en classify_utils.py

4 funciones definidas dos veces (Python usa la última silenciosamente):

| Función | Líneas |
|---|---|
| `detect_nature_from_name` | 789 y 883 |
| `_detect_kiid_format` | 837 y 931 |
| `_get_obj_bounds` | 872 y 966 |
| `_extract_window` | 878 y 972 |

Riesgo: Dead code que dificulta mantenimiento. Si alguien edita la primera versión creyendo que es la activa, el cambio no tiene efecto.

### 3.8 Profile-SRRI: 10 inconsistencias críticas

10 fondos tienen Profile='Conservador' con SRRI≥5, lo que viola la Regla INTER-3 del Principio #9. La validación semántica (`validate_profile_srri`) debería auto-corregir estos casos, pero o bien no se ejecuta o el resultado no se persiste.

---

## 4. PLAN DE TRABAJO ACTUALIZADO

Las fases del documento original siguen vigentes. A continuación se integran los nuevos hallazgos como acciones concretas dentro de cada fase.

### FASE 1B: Pasar SRRI de BD como fallback (del plan original)

**Modificación en `pipeline.py`** — pasar el SRRI ya parseado:

```python
# En pipeline.py, donde se llama a classifier:
classification = classifier(fund_name, kiid_text,
                            benchmark_declared=_bench,
                            srri_parsed=parsed.get("SRRI"))
```

**Modificación en `restantes.py`** — usar `srri_parsed` en Capa 3:

```python
def classify_fund(fund_name, kiid_text, benchmark_declared=None, srri_parsed=None):
    ...
    # Capa 3: SRRI como árbitro (usar valor parseado, no regex)
    if not nature_raw and srri_parsed is not None:
        if srri_parsed == 1:
            nature_raw = "Monetario"
        elif srri_parsed >= 5:
            nature_raw = "Renta Variable"
        elif srri_parsed == 2:
            nature_raw = "_RF_pending"
```

Impacto: Resuelve 12 de los 25 fondos residuales (5 RV con SRRI≥5, 7 RF con SRRI≤2).

### FASE 1C: Ampliar NAME_SIGNALS (nuevo)

Añadir patrones genéricos ausentes a las listas de señales de nombre:

```python
# NAME_SIGNALS_RV — añadir:
"stk",          # Vanguard: VANG PAC EXJAP STK
"stk indx",     # Vanguard: VGD US 500 STK INDX
"ashare",       # JPM CHINA ASHARE
"genetic therap", # JPM GENETIC THERAP
"gbl div",      # FIDELITY GBL DIV
"gl dividend",  # FIDELITY GL DIVIDEND
"divdnd",       # FIDELITY GLO DIVDND
"strat grow",   # JPMORGAN EUR STRAT GROWT

# NAME_SIGNALS_RF_FLEXIBLE — añadir:
"aggregate",    # JPM US AGGREGATE, JPMORGAN EURO AGGRE
"aggre",        # variante abreviada
"srt dr bnd",   # JPM GBL SRT DR BND (short duration bond)
"glb bnd",      # JPM GLB BND OPP
"eur cred",     # MFS MERIDI EUR CRED
"conv credi",   # R-CO CONV CREDI EURO
```

Impacto: Resuelve ~10 fondos adicionales vía Capa 2.

### FASE 1D: Corregir regresión de Theme (nuevo)

En `restantes.py`, tras la delegación exitosa, enriquecer con Theme si no está poblado:

```python
block_name = _NATURE_TO_BLOCK.get(nature_raw)
if block_name:
    try:
        block_mod = importlib.import_module(f"blocks.{block_name}")
        result = block_mod.classify_fund(fund_name, kiid_text)
        result["Fund_Nature"] = nature_canonical
        # Enriquecer Theme si el bloque no lo asigna
        if not result.get("Theme"):
            result["Theme"] = _detect_theme(name_l) or "Core/General"
        return apply_semantic_validation(result, fund_name)
```

Impacto: ~496 fondos (Monetario + RF Corto Plazo) recuperan Theme.

### FASE 4A: Normalización Accumulation_Policy (del plan original, refinado)

Punto de aplicación: `apply_semantic_validation()` en classify_utils.py, sección de validaciones intra-atributo.

```python
# En validate_all_semantic_consistency():
_ACCUM_NORM = {'Accumulation': 'ACCUMULATION', 'Distribution': 'DISTRIBUTION'}
accum = fund_record.get('Accumulation_Policy')
if accum in _ACCUM_NORM:
    corrected_record['Accumulation_Policy'] = _ACCUM_NORM[accum]
```

Impacto: 1.627 fondos normalizados.

### FASE 4B: Normalización Currency_Hedged (del plan original, refinado)

```python
_HEDGED_NORM = {'Yes': 'Hedged'}
ch = fund_record.get('Currency_Hedged')
if ch in _HEDGED_NORM:
    corrected_record['Currency_Hedged'] = _HEDGED_NORM[ch]
```

Impacto: 198 fondos corregidos.

### FASE 4C: Añadir "Tactical" a valores permitidos de Style_Profile (nuevo)

```python
ALLOWED_VALUES_BY_COLUMN['Style_Profile'] = [
    'Growth', 'Value', 'Income', 'Strategic Allocation',
    'Low Volatility', 'Risk Control', 'Momentum',
    'Tactical',  # ← añadir
]
```

Impacto: 6 fondos dejan de generar warning.

### FASE 4D: Eliminar funciones duplicadas en classify_utils.py (del plan original)

Eliminar las definiciones en líneas 789-878 (primera ocurrencia), conservar las de 883-976 (segunda ocurrencia, que son las activas).

### FASE 4E: Añadir 'Flexible Estratégico' como Family válida para RF Flexible (nuevo)

4 fondos RF Flexible tienen Family='Flexible Estratégico', que solo estaba permitido para Mixtos. Añadir a `ALLOWED_FAMILY_BY_NATURE`:

```python
'Renta Fija Flexible': [
    'Renta Fija Flexible', 'RF High Yield', 'RF Emergentes',
    'RF Inflación', 'Income Oriented',
    'Flexible Estratégico',  # ← añadir
],
```

---

## 5. PRIORIZACIÓN REVISADA

| Prioridad | Acción | Fondos | Esfuerzo |
|---|---|---|---|
| 1 | **1D: Fix regresión Theme** | 496 | 10 min |
| 2 | **1B: SRRI de BD como fallback** | 12 | 30 min |
| 3 | **1C: Ampliar NAME_SIGNALS** | 10 | 20 min |
| 4 | **4A: Normalizar Accumulation_Policy** | 1.627 | 10 min |
| 5 | **4B: Normalizar Currency_Hedged** | 198 | 5 min |
| 6 | **4C: Añadir Style_Profile='Tactical'** | 6 | 5 min |
| 7 | **4D: Eliminar funciones duplicadas** | — | 10 min |
| 8 | **4E: Family 'Flexible Estratégico' para RF** | 4 | 5 min |
| 9 | **Fase 3 (original): Detección NO/LIMITED** | ~3.700 | 2h |

**Impacto acumulado:** Con las acciones 1-8 (~1,5h de trabajo), el portfolio alcanzaría: 22 de 25 residuales resueltos, ~13,5 atributos medios para todo el portfolio, 0 inconsistencias lingüísticas en Accumulation_Policy y Currency_Hedged, 0 warnings de valores no permitidos en Style_Profile.

---

## 6. QUERIES DE VERIFICACIÓN POST-DESPLIEGUE

```sql
-- 1. Fondos residuales (objetivo: ≤3)
SELECT COUNT(*) FROM fund_master
WHERE Heuristic_Block='RESTANTES' AND Fund_Nature='Restantes';

-- 2. Theme para Monetario/RF Corto (objetivo: >95%)
SELECT Fund_Nature,
       COUNT(*) total,
       SUM(CASE WHEN Theme IS NOT NULL THEN 1 ELSE 0 END) con_theme
FROM fund_master
WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo')
GROUP BY Fund_Nature;

-- 3. Accumulation_Policy normalizado (objetivo: 0 minúscula)
SELECT Accumulation_Policy, COUNT(*)
FROM fund_master WHERE Accumulation_Policy IS NOT NULL
GROUP BY Accumulation_Policy;

-- 4. Currency_Hedged sin "Yes" (objetivo: 0)
SELECT Currency_Hedged, COUNT(*)
FROM fund_master WHERE Currency_Hedged IS NOT NULL
GROUP BY Currency_Hedged;

-- 5. Style_Profile sin warnings (objetivo: 0)
SELECT Style_Profile, COUNT(*)
FROM fund_master WHERE Style_Profile IS NOT NULL
GROUP BY Style_Profile;

-- 6. Profile-SRRI coherencia (objetivo: 0 Conservador con SRRI≥5)
SELECT Profile, SRRI, COUNT(*)
FROM fund_master
WHERE Profile='Conservador' AND SRRI >= 5
GROUP BY Profile, SRRI;
```
