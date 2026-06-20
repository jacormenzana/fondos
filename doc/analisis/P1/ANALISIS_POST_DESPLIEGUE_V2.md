# Análisis Post-Despliegue: Estado actual y correcciones pendientes

**Fecha:** 11 abril 2026
**Estado:** Tras Fase 0 + 1A + 1B + 1C + 1D + 4A-4E

---

## 1. ESTADO ACTUAL — RESUMEN

| Métrica | Pre-fix (sesión 1) | Post-fix (sesión 2) | Post-despliegue (actual) |
|---|---|---|---|
| Fondos Nature='Restantes' | 2.322 | 25 | **0** |
| Type NULL% (portfolio) | ~72% | ~1% | **0,0%** |
| Family NULL% (portfolio) | ~72% | ~1% | **0,2%** (8 Estructurado) |
| Media attrs (bloque REST) | 10,4 | 13,3 | **13,6** |
| Media attrs (portfolio) | — | — | **13,7** |

Logros principales: 0 fondos en Restantes, 100% de fondos con Type y 99,8% con Family. La media de atributos del portfolio (13,7) está alineada entre bloques.

---

## 2. PROBLEMAS DETECTADOS — DIAGNÓSTICO

### 2.1 Normalización Accumulation_Policy/Currency_Hedged NO funciona

**Síntoma:** Los valores siguen sin normalizar:
- Accumulation_Policy: `Accumulation` (1.650) y `Distribution` (65) persisten
- Currency_Hedged: `Yes` (202) persiste

**Causa raíz:** La normalización se ejecuta en el lugar equivocado. El código de normalización está dentro de `validate_all_semantic_consistency()`, que se invoca desde `apply_semantic_validation()` dentro de `restantes.classify_fund()`. Pero en `pipeline.py`, **después** de la clasificación ocurren dos cosas que sobreescriben:

1. **`fund_characterizer`** (línea ~392) → asigna Accumulation_Policy con Title Case ("Accumulation") y Currency_Hedged con "Yes"
2. **`fund_master_record`** (línea ~499) → combina classification + parsed: `classification.get("Accumulation_Policy") or parsed.get("Accumulation_Policy")` — el parser devuelve Title Case

La normalización se ejecutó sobre el dict de classification (que en ese momento ni siquiera tenía Accumulation_Policy), y luego los valores correctos fueron sobreescritos por el characterizer y el constructor del record.

**Además:** El problema afecta a TODOS los bloques, no solo a RESTANTES. Los bloques especializados (MONETARIOS, RENTA_VARIABLE, etc.) también generan valores Title Case.

### 2.2 Profile-SRRI NO se auto-corrige

**Síntoma:** 10 fondos Conservador con SRRI≥5 siguen sin corregir.

**Causa raíz:** `validate_profile_srri(cr.get("Profile"), cr.get("SRRI"))` se invoca dentro de `validate_all_semantic_consistency()`. Pero cuando esta función se ejecuta (dentro de `classify_fund`), el dict de clasificación **no contiene SRRI**. El SRRI está en `parsed` (resultado de `kiid_parser.py`) y solo se incorpora al `fund_master_record` en pipeline.py línea ~489. La validación recibe `SRRI=None` y no puede detectar la inconsistencia.

### 2.3 Theme para Monetario: 79,6% en lugar de >95%

**Síntoma:** 21 fondos Monetario sin Theme.

**Causa raíz:** Los 21 fondos provienen del bloque MONETARIOS original (no de delegación RESTANTES). El fix 1D solo enriquece Theme tras la delegación en `restantes.py`. Los fondos que entran directamente por el bloque MONETARIOS nunca reciben Theme porque `monetarios.py` no lo asigna.

### 2.4 Dos fondos RV con Type=Monetario (bug grave)

**Fondos:** AMUNDI EUROLAND EQ A EUR INC (LU1883303718, SRRI=5) y BNP P. SMART FOOD N EUR ACC (LU1165137495, SRRI=None).

Ambos tienen Nature=Renta Variable pero Type=Monetario, Family=Monetario, Exposure_Bias=Liquidity Bias, Investment_Universe=Liquidity. Son fondos equity evidentes que el characterizer ha sobreescrito incorrectamente con atributos monetarios. Este es el bug documentado en las memorias del proyecto (LU1165137495 con Investment_Universe='Liquidity').

### 2.5 Nature-Type: 11 inconsistencias por tipos faltantes en diccionario

| Nature | Type no permitido | Fondos | Solución |
|---|---|---|---|
| RF Flexible | Target Maturity | 7 | Añadir a ALLOWED_TYPE_BY_NATURE |
| Alternativo | Real Assets | 1 | Añadir a ALLOWED_TYPE_BY_NATURE |
| Mixtos | Target Volatility | 1 | Añadir a ALLOWED_TYPE_BY_NATURE |
| **Renta Variable** | **Monetario** | **2** | **Bug characterizer** |

### 2.6 Family NULL para 8 fondos Estructurado

Los 8 DB AUTOCALLABLE no reciben Family en el fallback de restantes.py. Añadir `"Family": "Estructurado"` al bloque de Estructurado.

---

## 3. SOLUCIÓN: NORMALIZACIÓN EN `pipeline.py`

**Principio:** La normalización debe ejecutarse en el último punto antes de persistir, después de que TODOS los componentes (bloque, characterizer, parser) hayan contribuido sus valores.

### Cambio en `pipeline.py` — Añadir import + insertar bloque de normalización

**Import:** En las líneas 23-26, ampliar el import de classify_utils:

```python
from core.classify_utils import (
    detect_strategy        as _detect_strategy,
    detect_benchmark_type  as _detect_benchmark_type,
    detect_theme           as _detect_theme_pipeline,       # ← NUEVO
)
```

**Normalización:** Entre la línea `fund_master_record["Is_ESG"] = 1` (~línea 521) y la línea `_total_ms = ...` (~línea 523). Justo después de construir el record completo y antes de publicar.

```python
            # Is_ESG override: SFDR Art.8/9 es más fiable que keywords en nombre
            if parsed.get("Sfdr_Article") in (8, 9):
                fund_master_record["Is_ESG"] = 1

            # ── Normalización final (Principio #8) ──────────────────────
            # Se ejecuta AQUÍ porque Accumulation_Policy y Currency_Hedged
            # provienen de múltiples fuentes (bloque, characterizer, parser)
            # con convenciones de casing diferentes.

            # Accumulation_Policy: unificar a UPPER
            _ap = fund_master_record.get("Accumulation_Policy")
            if _ap == "Accumulation":
                fund_master_record["Accumulation_Policy"] = "ACCUMULATION"
            elif _ap == "Distribution":
                fund_master_record["Accumulation_Policy"] = "DISTRIBUTION"

            # Currency_Hedged: mapear "Yes" → "Hedged"
            if fund_master_record.get("Currency_Hedged") == "Yes":
                fund_master_record["Currency_Hedged"] = "Hedged"

            # Profile-SRRI coherencia: corregir Conservador con SRRI≥5
            _profile = fund_master_record.get("Profile")
            _srri_val = fund_master_record.get("SRRI")
            if _profile == "Conservador" and _srri_val is not None and _srri_val >= 5:
                fund_master_record["Profile"] = "Dinámico"
                print(
                    f"  [NORM] {isin} Profile recalculado: "
                    f"Conservador→Dinámico (SRRI={_srri_val})"
                )

            # Theme: rellenar para bloques que no lo asignan
            if not fund_master_record.get("Theme"):
                fund_master_record["Theme"] = (
                    _detect_theme_pipeline((fund_name or "").lower())
                    or "Core/General"
                )

            _total_ms = round((time.perf_counter() - _t_fund_start) * 1000)
```

### Cambio en `classify_utils.py` — Eliminar normalización duplicada

En `validate_all_semantic_consistency()`, **eliminar** el bloque de normalización que se añadió en la sesión anterior (líneas ~2310-2316) ya que ahora se ejecuta en pipeline.py:

```python
    # ELIMINAR estas líneas:
    _accum_norm = {"Accumulation": "ACCUMULATION", "Distribution": "DISTRIBUTION"}
    _accum = cr.get("Accumulation_Policy")
    if _accum in _accum_norm:
        cr["Accumulation_Policy"] = _accum_norm[_accum]
    if cr.get("Currency_Hedged") == "Yes":
        cr["Currency_Hedged"] = "Hedged"
```

### Cambio en `restantes.py` — Family para Estructurado

En el bloque de fallback para Estructurado (dentro de `classify_fund`), añadir Family:

```python
    # Estructurado con caracterizacion minima
    if nature_raw == "Estructurado":
        result["Type"] = "Estructurado"
        result["Family"] = "Estructurado"                    # ← NUEVO
        if any(k in name_l for k in ["autocall", "autocallable"]):
            result["Subtype"]       = "Autocallable"
            result["Exposure_Bias"] = "Barrier Risk"
```

### Cambio en `classify_utils.py` — Ampliar ALLOWED_TYPE_BY_NATURE

Añadir los tipos que faltan:

```python
ALLOWED_TYPE_BY_NATURE: dict = {
    "Renta Variable": [
        "Gestión Activa", "Indexado", "Total Return",
        "Absolute Return", "Tactical Allocation",
    ],
    "Renta Fija Flexible": [
        "Renta Fija Flexible", "Gestión Activa", "Total Return",
        "Absolute Return", "Indexado",
        "Target Maturity",                                   # ← NUEVO (7 fondos)
    ],
    "Renta Fija Corto Plazo": [
        "Renta Fija Corto Plazo", "Crédito CP", "Gobierno CP",
        "Floating Rate CP", "Target Maturity",
        "Deuda Pública CP",                                  # ← NUEVO
    ],
    "Monetario": [
        "Monetario", "Monetario Público", "Monetario Privado",
    ],
    "Mixtos": [
        "Allocation", "Tactical Allocation", "Gestión Activa",
        "Target Volatility",                                 # ← NUEVO (1 fondo)
    ],
    "Alternativo": [
        "Absolute Return", "Commodities", "Total Return",
        "Gestión Activa", "Indexado",
        "Real Assets",                                       # ← NUEVO (1 fondo)
    ],
    "Estructurado": [
        "Estructurado",
    ],
}
```

---

## 4. RESUMEN DE ACCIONES

| # | Acción | Fichero | Impacto | Esfuerzo |
|---|---|---|---|---|
| 1 | **Normalización Accum/Hedged/Profile/Theme en pipeline.py** | pipeline.py | 1.925 fondos | 15 min |
| 2 | Eliminar normalización duplicada en validate_all | classify_utils.py | Limpieza | 2 min |
| 3 | Family='Estructurado' para Autocallables | restantes.py | 8 fondos | 2 min |
| 4 | Ampliar ALLOWED_TYPE_BY_NATURE | classify_utils.py | 9 warnings | 5 min |
| — | **Bug RV con Type=Monetario (LU1883303718, LU1165137495)** | **Investigar** | 2 fondos | **TBD** |

El bug de los 2 fondos RV con Type=Monetario requiere investigar `fund_characterizer.py` para entender por qué sobreescribe Type/Family/Investment_Universe con valores monetarios para fondos cuya Nature es Renta Variable. Esto está vinculado al gap arquitectónico documentado en las memorias del proyecto. Recomiendo no aplicar un parche SQL sino investigar la causa raíz en el characterizer.

---

## 5. QUERIES DE VERIFICACIÓN

```sql
-- 1. Accumulation_Policy (objetivo: solo ACCUMULATION y DISTRIBUTION)
SELECT Accumulation_Policy, COUNT(*) FROM fund_master
WHERE Accumulation_Policy IS NOT NULL GROUP BY 1;

-- 2. Currency_Hedged (objetivo: solo Hedged, sin Yes)
SELECT Currency_Hedged, COUNT(*) FROM fund_master
WHERE Currency_Hedged IS NOT NULL GROUP BY 1;

-- 3. Profile-SRRI (objetivo: 0 Conservador con SRRI≥5)
SELECT Profile, SRRI, COUNT(*) FROM fund_master
WHERE Profile='Conservador' AND SRRI >= 5 GROUP BY 1,2;

-- 4. Theme completitud (objetivo: >99%)
SELECT Fund_Nature, COUNT(*) total,
       SUM(CASE WHEN Theme IS NOT NULL THEN 1 ELSE 0 END) con_theme
FROM fund_master GROUP BY 1;

-- 5. Family NULL (objetivo: 0)
SELECT COUNT(*) FROM fund_master WHERE Family IS NULL;

-- 6. Nature-Type inconsistencias (objetivo: 2 — solo los bugs RV/Monetario)
-- (usar ALLOWED_TYPE_BY_NATURE actualizado)
```
