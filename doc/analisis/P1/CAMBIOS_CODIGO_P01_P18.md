# CAMBIOS DE CÓDIGO P01-P18 — Plan de Acción Calidad P1

**Fecha:** 11/04/2026  
**Aplicar en orden:** P01 → P02 (REFRESH) → P03-P15 → P16-P18

---

## P01 — Normalización pre-INSERT en sqlite_writer.py

**Fichero:** `core/sqlite_writer.py`  
**Acción:** Añadir función `_normalize_record()` y llamarla antes del INSERT.

### Añadir función (antes de `upsert_fund_master`, ~línea 78):

```python
# ============================================================
# NORMALIZACIÓN PRE-ESCRITURA (Principio #8)
# ============================================================

# Sector_Focus: normalizar EN→ES (P11)
_SECTOR_FOCUS_EN_TO_ES = {
    "Technology & Innovation": "Tecnología e Innovación",
    "Energy & Resources": "Energía y Recursos",
    "Utilities & Environment": "Utilities y Medio Ambiente",
    "Healthcare & Life Sciences": "Salud y Ciencias de la Vida",
    "Materials & Mining": "Materiales y Minería",
    "Financial Services": "Servicios Financieros",
    "Financials & Insurance": "Servicios Financieros",
    "Consumer Discretionary": "Consumo",
    "Real Estate & Infrastructure": "Real Assets",
}


def _normalize_record(record: Dict[str, Optional[Any]]) -> Dict[str, Optional[Any]]:
    """
    Normalización canónica pre-escritura.
    
    Se ejecuta SIEMPRE antes del INSERT/UPSERT, garantizando que
    los valores escritos en BD cumplen Principio #8 independientemente
    de la fuente (bloque, characterizer, parser, o COALESCE previo).
    """
    # Accumulation_Policy: unificar a UPPER (P01)
    ap = record.get("Accumulation_Policy")
    if isinstance(ap, str):
        ap_upper = ap.upper()
        if ap_upper in ("ACCUMULATION", "DISTRIBUTION"):
            record["Accumulation_Policy"] = ap_upper

    # Currency_Hedged: "Yes" → "Hedged" (P01)
    if record.get("Currency_Hedged") == "Yes":
        record["Currency_Hedged"] = "Hedged"

    # Sector_Focus: EN → ES (P11)
    sf = record.get("Sector_Focus")
    if sf in _SECTOR_FOCUS_EN_TO_ES:
        record["Sector_Focus"] = _SECTOR_FOCUS_EN_TO_ES[sf]

    return record
```

### Modificar `upsert_fund_master` (~línea 80):

Añadir llamada a `_normalize_record` al inicio de la función:

```python
def upsert_fund_master(conn: sqlite3.Connection,
                       record: Dict[str, Optional[Any]]) -> None:
    # ── Normalización pre-escritura (Principio #8) ──
    record = _normalize_record(record)
    
    # ... (resto sin cambios) ...
```

---

## P02 — FORCE_REFRESH para fondos con datos stale

**Fichero:** Script SQL one-shot (ejecutar tras desplegar P01)

```sql
-- P02: Marcar para re-descarga los 176 fondos con datos stale
-- Ejecutar DESPUÉS de desplegar P01 en sqlite_writer.py

-- Accumulation_Policy con casing incorrecto (116 fondos)
UPDATE fund_kiid_metadata
SET KIID_Status = 'FORCE_REFRESH'
WHERE ISIN IN (
    SELECT ISIN FROM fund_master
    WHERE Accumulation_Policy IN ('Accumulation', 'Distribution')
)
AND KIID_Class = 1;

-- Currency_Hedged = 'Yes' (60 fondos, puede solapar con los anteriores)
UPDATE fund_kiid_metadata
SET KIID_Status = 'FORCE_REFRESH'
WHERE ISIN IN (
    SELECT ISIN FROM fund_master
    WHERE Currency_Hedged = 'Yes'
)
AND KIID_Class = 1;

-- Conservador con SRRI >= 5 (10 fondos, probablemente ya incluidos)
UPDATE fund_kiid_metadata
SET KIID_Status = 'FORCE_REFRESH'
WHERE ISIN IN (
    SELECT ISIN FROM fund_master
    WHERE Profile = 'Conservador' AND SRRI >= 5
)
AND KIID_Class = 1;

-- Investment_Universe = 'Liquidity' incoherente con Nature (32 fondos)
UPDATE fund_kiid_metadata
SET KIID_Status = 'FORCE_REFRESH'
WHERE ISIN IN (
    SELECT ISIN FROM fund_master
    WHERE Investment_Universe = 'Liquidity'
      AND Fund_Nature NOT IN ('Monetario', 'Renta Fija Corto Plazo')
)
AND KIID_Class = 1;

-- Sector_Focus en inglés (26 fondos)
UPDATE fund_kiid_metadata
SET KIID_Status = 'FORCE_REFRESH'
WHERE ISIN IN (
    SELECT ISIN FROM fund_master
    WHERE Sector_Focus IN (
        'Technology & Innovation', 'Energy & Resources',
        'Utilities & Environment', 'Healthcare & Life Sciences'
    )
)
AND KIID_Class = 1;

-- Nature-Type/Family incoherentes (2 fondos RV con Type=Monetario)
UPDATE fund_kiid_metadata
SET KIID_Status = 'FORCE_REFRESH'
WHERE ISIN IN ('LU1883303718', 'LU1165137495')
AND KIID_Class = 1;

-- Verificar total de fondos marcados
SELECT COUNT(*) AS fondos_refresh
FROM fund_kiid_metadata
WHERE KIID_Status = 'FORCE_REFRESH' AND KIID_Class = 1;
```

---

## P03 — Default Strategy="Activo" y Replication_Method="ACTIVE"

**Fichero:** `core/pipeline.py`  
**Ubicación:** Bloque de normalización final (~línea 560, después del bloque Theme)

### Añadir DESPUÉS de la línea `fund_master_record["Theme"] = ...` (~línea 559):

```python
            # Strategy default: si no detectado y sin señales de indexación → Activo (P03)
            if not fund_master_record.get("Strategy"):
                fund_master_record["Strategy"] = "Activo"
                fund_master_record["Replication_Method"] = (
                    fund_master_record.get("Replication_Method") or "ACTIVE"
                )

            # Replication_Method: coherencia con Strategy (P03)
            if not fund_master_record.get("Replication_Method"):
                _strat = fund_master_record.get("Strategy")
                if _strat in ("Indexado", "Pasivo"):
                    fund_master_record["Replication_Method"] = "PASSIVE"
                elif _strat == "Activo":
                    fund_master_record["Replication_Method"] = "ACTIVE"
```

---

## P04 — Inferencia Investment_Universe desde Geography

**Fichero:** `core/pipeline.py`  
**Ubicación:** En el bloque de normalización final, después de P03.

### Añadir DESPUÉS del bloque P03:

```python
            # Investment_Universe: inferir desde Geography si NULL (P04)
            if not fund_master_record.get("Investment_Universe"):
                _geo = fund_master_record.get("Geography")
                _nat = fund_master_record.get("Fund_Nature")
                if _nat in ("Monetario", "Renta Fija Corto Plazo"):
                    fund_master_record["Investment_Universe"] = "Liquidity"
                elif _geo in ("EEUU", "China", "Japón", "India"):
                    fund_master_record["Investment_Universe"] = "Country"
                elif _geo in ("Europa", "Asia", "Emergentes",
                              "Latinoamérica", "Europa del Este"):
                    fund_master_record["Investment_Universe"] = "Regional"
                elif _geo == "Global":
                    fund_master_record["Investment_Universe"] = "Global"
```

---

## P05 — Ampliar detección Accumulation_Policy por nombre

**Fichero:** `core/pipeline.py`  
**Ubicación:** En el bloque de normalización final, después de P04.

### Añadir DESPUÉS del bloque P04:

```python
            # Accumulation_Policy: inferir desde nombre si NULL (P05)
            if not fund_master_record.get("Accumulation_Policy"):
                _fn_l = (fund_name or "").lower()
                # ACC/ACCUM al final del nombre o como token separado
                if re.search(r"\bacc(?:um)?\b", _fn_l):
                    fund_master_record["Accumulation_Policy"] = "ACCUMULATION"
                elif re.search(r"\b(?:inc|dis(?:t)?)\b", _fn_l):
                    fund_master_record["Accumulation_Policy"] = "DISTRIBUTION"
```

**Nota:** Añadir `import re` al inicio de pipeline.py si no está (ya está importado indirectamente pero mejor asegurar).

---

## P06 — Ampliar _needs_char para Geography=NULL y otros gaps

**Fichero:** `core/pipeline.py`  
**Ubicación:** Bloque `_needs_char` (~línea 382-390)

### REEMPLAZAR las líneas 382-390:

```python
            if not _needs_char:
                # Para CACHED: verificar si faltan atributos v3 en BD
                # P06: ampliado para detectar Geography=NULL y
                #      inconsistencia Nature/Investment_Universe (P09)
                _v3_row = conn.execute(
                    "SELECT Investment_Universe, Accumulation_Policy, Currency_Hedged, "
                    "Investment_Focus, Credit_Quality, Geography, Fund_Nature "
                    "FROM fund_master WHERE ISIN=?", (isin,)
                ).fetchone()
                if _v3_row is None:
                    _needs_char = True
                else:
                    # Campos v3 originales (NULL → re-char)
                    _needs_char = any(v is None for v in _v3_row[:5])
                    # P06: Geography NULL
                    if not _needs_char and _v3_row[5] is None:
                        _needs_char = True
                    # P09: Investment_Universe incoherente con Nature
                    if not _needs_char:
                        _db_universe = _v3_row[0]
                        _db_nature = _v3_row[6]
                        if (_db_universe == "Liquidity"
                                and _db_nature not in (
                                    "Monetario", "Renta Fija Corto Plazo")):
                            _needs_char = True
```

---

## P07 — Auto-corrección Nature-Type-Family en validador

**Fichero:** `core/classify_utils.py`  
**Ubicación:** Dentro de `validate_all_semantic_consistency()` (~línea 2256-2268)

### REEMPLAZAR las líneas 2256-2268 (INTER-4 y INTER-5):

```python
    # INTER-4: Nature → Type (con auto-corrección P07)
    ok, msg = validate_nature_type_coherence(
        cr.get("Fund_Nature"), cr.get("Type")
    )
    if not ok:
        critical_errors.append({"rule": "Nature-Type", "message": msg})
        # P07: Auto-corrección — asignar Type por defecto de la Nature
        _default_type = _DEFAULT_TYPE_BY_NATURE.get(cr.get("Fund_Nature"))
        if _default_type:
            cr["Type"] = _default_type
            critical_errors[-1]["message"] += f" → corregido a '{_default_type}'"

    # INTER-5: Nature → Family (con auto-corrección P07)
    ok, msg = validate_nature_family_coherence(
        cr.get("Fund_Nature"), cr.get("Family")
    )
    if not ok:
        critical_errors.append({"rule": "Nature-Family", "message": msg})
        # P07: Auto-corrección — asignar Family por defecto de la Nature
        _default_family = _DEFAULT_FAMILY_BY_NATURE.get(cr.get("Fund_Nature"))
        if _default_family:
            cr["Family"] = _default_family
            critical_errors[-1]["message"] += f" → corregido a '{_default_family}'"
```

### Añadir constantes (después de `ALLOWED_FAMILY_BY_NATURE`, ~línea 1919):

```python
# ============================================================
# 14b. DEFAULT TYPE/FAMILY BY NATURE (P07 — auto-corrección)
# ============================================================

_DEFAULT_TYPE_BY_NATURE: dict = {
    "Renta Variable":         "Gestión Activa",
    "Renta Fija Flexible":    "Renta Fija Flexible",
    "Renta Fija Corto Plazo": "Renta Fija Corto Plazo",
    "Monetario":              "Monetario",
    "Mixtos":                 "Allocation",
    "Alternativo":            "Absolute Return",
    "Estructurado":           "Estructurado",
}

_DEFAULT_FAMILY_BY_NATURE: dict = {
    "Renta Variable":         "RV Core",
    "Renta Fija Flexible":    "Renta Fija Flexible",
    "Renta Fija Corto Plazo": "Renta Fija Corto Plazo",
    "Monetario":              "Monetario",
    "Mixtos":                 "Mixtos",
    "Alternativo":            "Retorno Absoluto",
    "Estructurado":           "Estructurado",
}
```

---

## P08 — NAME_SIGNALS para fondos misclassificados

**Fichero:** `core/classify_utils.py`

### Añadir señales a `NAME_SIGNALS_ALTERNATIVO` (~línea 747):

Buscar la lista `NAME_SIGNALS_ALTERNATIVO` y añadir al final:

```python
    # P08: fondos de volatilidad (AMUNDI VOLATILITY)
    "volatility",
    "volatilidad",
    "volatilit",  # nombre truncado en AMUNDI VOLATILIT WLD
```

### Añadir señales a `NAME_SIGNALS_RV` (~línea 505):

Buscar la lista `NAME_SIGNALS_RV` y añadir al final:

```python
    # P08: fondos con nombre inequívoco de RV
    "us forty",
    "euroland eq",
    "smart food",
    "global technology",
    "gbl tech",
    "gbl tch",
]
```

### Señal anti-monetario en `NAME_SIGNALS_MONETARIO` (~línea 96):

Aquí el problema es que "M MKT" y "MK" activan MONETARIO incluso cuando el fondo es otra cosa. Necesitamos que el SRRI prevalezca. Esto ya ocurre en `restantes.py` Capa 3 (SRRI≥5 → RV), pero el problema es que Capa 2 (nombre) se evalúa antes y "M MKT" matchea MONETARIO.

La solución es que `restantes.py` tenga un guardia: si el nombre matchea monetario PERO el SRRI≥5, no confiar en el match de nombre. Esto se implementa en restantes.py, no en las señales:

### Modificar `restantes.py`, función `classify_fund()` (~línea 188):

**REEMPLAZAR** las líneas 186-192:

```python
        if srri == 1:
            nature_raw = "Monetario"
        elif srri is not None and srri >= 5:
            nature_raw = "Renta Variable"
        elif srri == 2:
            nature_raw = "_RF_pending"
```

**CON:**

```python
        if srri == 1:
            nature_raw = "Monetario"
        elif srri is not None and srri >= 5:
            nature_raw = "Renta Variable"
        elif srri == 2:
            nature_raw = "_RF_pending"

    # P08: Guardia SRRI vs Nature — SRRI prevalece sobre nombre
    # Un fondo con SRRI≥5 NO puede ser Monetario ni RF Corto Plazo
    if (srri_parsed is not None and srri_parsed >= 5
            and nature_raw in ("Monetario", "Renta Fija Corto Plazo",
                               "RF_Corto", None)):
        nature_raw = "Renta Variable"
        logger.info("[%s] P08: SRRI=%d fuerza Nature=RV (era %s)",
                    fund_name, srri_parsed, nature_raw)
```

**Nota:** Este bloque debe ir DESPUÉS de las 3 capas de detección y ANTES de la línea `nature_canonical = _NATURE_CANONICAL.get(...)`. Es decir, insertar justo antes de la línea 198 actual.

---

## P09 — _needs_char detectar inconsistencia Nature/Universe

**Ya incluido en P06** (ver el bloque que detecta `_db_universe == "Liquidity"` incoherente con `_db_nature`).

---

## P10 — Mapeo Theme→Sector_Focus para Investment_Focus=Sector

**Fichero:** `core/pipeline.py`  
**Ubicación:** En el bloque de normalización final, después de P05.

### Añadir:

```python
            # Sector_Focus: inferir desde Theme si Investment_Focus=Sector y SF=NULL (P10)
            if (fund_master_record.get("Investment_Focus") == "Sector"
                    and not fund_master_record.get("Sector_Focus")):
                _theme = fund_master_record.get("Theme")
                # Mapeo directo Theme → Sector_Focus (español)
                _theme_to_sector = {
                    "Technology":             "Tecnología e Innovación",
                    "Artificial Intelligence":"Tecnología e Innovación",
                    "Digital":                "Tecnología e Innovación",
                    "Robotics":               "Tecnología e Innovación",
                    "Cybersecurity":           "Tecnología e Innovación",
                    "Healthcare":             "Salud y Ciencias de la Vida",
                    "Biotechnology":          "Salud y Ciencias de la Vida",
                    "Silver Economy":         "Salud y Ciencias de la Vida",
                    "Energy":                 "Energía y Recursos",
                    "Climate / Clean Energy": "Energía y Recursos",
                    "Water":                  "Utilities y Medio Ambiente",
                    "Gold":                   "Materiales y Minería",
                    "Mining":                 "Materiales y Minería",
                    "Real Estate":            "Real Assets",
                    "Insurance":              "Servicios Financieros",
                    "Financials":             "Servicios Financieros",
                    "Consumer Brands":        "Consumo",
                }
                _sf = _theme_to_sector.get(_theme)
                if _sf:
                    fund_master_record["Sector_Focus"] = _sf
                elif _theme == "Megatrends":
                    # Megatrends es multisectorial → reclasificar a Thematic
                    fund_master_record["Investment_Focus"] = "Thematic"
                elif _theme == "Core/General":
                    # Sector sin tema específico → reclasificar a Broad
                    fund_master_record["Investment_Focus"] = "Broad"
```

---

## P11 — Estandarizar Sector_Focus a español

**Ya incluido en P01** (normalización en `_normalize_record()` dentro de `sqlite_writer.py`, con mapeo `_SECTOR_FOCUS_EN_TO_ES`).

---

## P12 — Derivatives_Usage: detectar LIMITED y default NO

**Fichero:** `core/kiid_parser.py`

### Añadir patrones LIMITED (antes de `ES_DERIVATIVES_NO`, ~línea 975):

```python
# P12: Patrones LIMITED (uso acotado/limitado de derivados)
ES_DERIVATIVES_LIMITED = [
    r"\b(?:puede|podr[aá])\s+(?:utilizar|emplear|usar)\s+(?:instrumentos\s+)?derivados\s+"
    r"(?:con\s+fines\s+de\s+cobertura|de\s+manera\s+limitada|de\s+forma\s+accesoria)",
    r"\buso\s+(?:limitado|moderado|accesorio)\s+de\s+(?:instrumentos\s+)?derivados\b",
    r"\bderivados\s+(?:únicamente|solo|exclusivamente)\s+con\s+fines\s+de\s+cobertura\b",
    r"\bderivados\s+(?:con\s+fines\s+de\s+)?cobertura\b(?!.{0,40}inversi[oó]n)",
]

EN_DERIVATIVES_LIMITED = [
    r"\bmay\s+use\s+(?:financial\s+)?derivatives\s+for\s+(?:hedging|efficient\s+portfolio\s+management)\b",
    r"\blimited\s+use\s+of\s+(?:financial\s+)?derivatives\b",
    r"\bderivatives\s+(?:only|solely|exclusively)\s+for\s+hedging\b",
]
```

### REEMPLAZAR `_detect_derivatives_usage` (~línea 1019-1056):

```python
def _detect_derivatives_usage(text: str, language: Optional[str]) -> Optional[str]:
    """
    Detecta uso de derivados: YES, LIMITED, NO.
    P12: añadido LIMITED y default NO cuando no hay mención.
    """
    if not text:
        return None

    t = text.lower()
    t_nospace = t.replace(" ", "")

    # ── Texto OCR fusionado ──────────────────────────────────────────
    if language is None:
        if any(p in t_nospace for p in ["noderivados", "noderivado"]):
            return "NO"
        if any(p in t_nospace for p in ["derivadosuso:", "instrumentosderivados",
                                          "usodederivados", "derivadosuso"]):
            return "YES"
        return None

    # ── Español ──────────────────────────────────────────────────────
    if language in ("ES", None):
        # NO tiene prioridad
        for rx in ES_DERIVATIVES_NO:
            if re.search(rx, t):
                return "NO"
        # LIMITED antes de YES (P12)
        for rx in ES_DERIVATIVES_LIMITED:
            if re.search(rx, t):
                return "LIMITED"
        for rx in ES_DERIVATIVES_YES:
            if re.search(rx, t):
                return "YES"
        if "instrumentosderivados" in t_nospace or "usodederivados" in t_nospace:
            return "YES"

    # ── Inglés ───────────────────────────────────────────────────────
    if language in ("EN", None):
        for rx in EN_DERIVATIVES_NO:
            if re.search(rx, t):
                return "NO"
        # LIMITED antes de YES (P12)
        for rx in EN_DERIVATIVES_LIMITED:
            if re.search(rx, t):
                return "LIMITED"
        for rx in EN_DERIVATIVES_YES:
            if re.search(rx, t):
                return "YES"

    return None
```

### Default NO en pipeline.py (bloque de normalización):

Añadir después del bloque P10:

```python
            # Derivatives_Usage: default NO si no detectado (P12)
            if not fund_master_record.get("Derivatives_Usage"):
                fund_master_record["Derivatives_Usage"] = "NO"
```

---

## P13 — Leverage_Used: default NO si no mencionado

**Fichero:** `core/pipeline.py`  
**Ubicación:** En el bloque de normalización final, después de P12.

```python
            # Leverage_Used: default NO si no detectado (P13)
            if not fund_master_record.get("Leverage_Used"):
                fund_master_record["Leverage_Used"] = "NO"
```

---

## P14 — Credit_Quality: mejorar detección para RF Flexible

**Fichero:** `core/pipeline.py`  
**Ubicación:** En el bloque de normalización final, después de P13.

```python
            # Credit_Quality: default para Nature sin detección (P14)
            if not fund_master_record.get("Credit_Quality"):
                _nat14 = fund_master_record.get("Fund_Nature")
                if _nat14 == "Renta Variable":
                    fund_master_record["Credit_Quality"] = "No aplica"
                elif _nat14 in ("Renta Fija Flexible", "Renta Fija Corto Plazo"):
                    # Si no se detectó, Mixed es el default conservador
                    fund_master_record["Credit_Quality"] = "Mixed"
                elif _nat14 == "Monetario":
                    fund_master_record["Credit_Quality"] = "Investment Grade"
```

---

## P15 — Ongoing_Charge: documentar gap (no hay fix simple)

**Fichero:** Sin cambios de código directos.

El parser (`_detect_ongoing_charge`) ya tiene 4 capas de detección (DDF composición, PRIIPs, UCITS, OCR fusionado). El 26% de NULLs (840 fondos) probablemente corresponde a:
- KIIDs en formatos no cubiertos por los regex actuales
- PDFs escaneados con OCR deficiente donde el porcentaje no se extrajo correctamente
- Fondos cuyo KIID_Status=CACHED no se re-procesó con la versión actual del parser

**Recomendación:** Tras el FORCE_REFRESH de P02, verificar cuántos Ongoing_Charge siguen en NULL. Si el gap persiste, revisar una muestra de 10-15 KIIDs para identificar patrones no cubiertos.

---

## P16 — Actualizar Principio #8: Sector_Focus en español

**Fichero:** `PRINCIPIO_8_ACTUALIZADO.md` (o `CONTEXTO_OPERATIVO_V2.md`)

### Modificar la tabla de idioma objetivo:

```
| Sector_Focus | **Español** | Tecnología e Innovación, Salud y Ciencias de la Vida, etc. |
```

En lugar de:

```
| Sector_Focus | **Inglés** | Technology & Innovation, Healthcare, etc. (nomenclatura GICS-ES) |
```

---

## P17 — Documentar Style_Profile como "best effort"

**Fichero:** `SCHEMA_REFERENCE.md`

### Añadir nota en la sección de Style_Profile:

```
Style_Profile: Cobertura parcial por diseño.
- Mixtos: 100% (siempre "Strategic Allocation")
- Renta Variable: ~28% (Growth/Value/Income/etc. cuando detectable en KIID/nombre)
- Otros: <10% (solo cuando hay señales explícitas)
- Monetario, Estructurado: N/A (NULL es correcto)
```

---

## P18 — Redundancia Hedging_Policy ↔ Currency_Hedged

**Sin cambios de código.** Investigación pendiente.

Datos actuales:
- Hedging_Policy: 407 HEDGED, 488 UNHEDGED (895 total, 27.9%)
- Currency_Hedged: 574 Hedged, 60 Yes→Hedged (634 total, 19.8%)

Las dos columnas NO son redundantes:
- `Hedging_Policy` procede del parser (texto KIID) y cubre cualquier tipo de cobertura
- `Currency_Hedged` procede del characterizer (nombre del fondo, señales como "HDG", "HEDGED")

**Recomendación:** Mantener ambas. Añadir validación de coherencia en futuro: si `Currency_Hedged = "Hedged"` entonces `Hedging_Policy` debería ser `"HEDGED"` (o NULL). No implementar hasta que se resuelvan P01-P14.

---

## RESUMEN DE CAMBIOS POR FICHERO

### `core/sqlite_writer.py`
- **P01/P11:** Añadir `_SECTOR_FOCUS_EN_TO_ES`, `_normalize_record()`, llamarla en `upsert_fund_master()`

### `core/pipeline.py`
- **P03:** Default Strategy="Activo" + Replication_Method="ACTIVE"
- **P04:** Inferencia Investment_Universe desde Geography
- **P05:** Detección Accumulation_Policy por nombre (ACC/INC/DIS)
- **P06/P09:** Ampliar `_needs_char` con Geography=NULL + Nature/Universe inconsistencia
- **P10:** Mapeo Theme→Sector_Focus para Investment_Focus=Sector
- **P12:** Default Derivatives_Usage="NO"
- **P13:** Default Leverage_Used="NO"
- **P14:** Default Credit_Quality por Nature

### `core/kiid_parser.py`
- **P12:** Patrones `ES_DERIVATIVES_LIMITED`, `EN_DERIVATIVES_LIMITED`, reescribir `_detect_derivatives_usage()`

### `core/classify_utils.py`
- **P07:** `_DEFAULT_TYPE_BY_NATURE`, `_DEFAULT_FAMILY_BY_NATURE`, auto-corrección en `validate_all_semantic_consistency()`
- **P08:** Señales adicionales en `NAME_SIGNALS_ALTERNATIVO` y `NAME_SIGNALS_RV`

### `blocks/restantes.py`
- **P08:** Guardia SRRI≥5 vs Nature Monetario/RF Corto

### Scripts SQL
- **P02:** FORCE_REFRESH para fondos stale

### Documentación
- **P16:** Sector_Focus → español en Principio #8
- **P17:** Style_Profile como "best effort" en schema reference

---

## ORDEN DE DESPLIEGUE

```
1. Desplegar código: sqlite_writer.py, pipeline.py, kiid_parser.py,
   classify_utils.py, restantes.py
2. Ejecutar SQL P02 (FORCE_REFRESH)
3. Ejecutar pipeline RESTANTES completo (procesará los ~200 FORCE_REFRESH
   + todos los RESTANTES habituales con las nuevas normalizaciones)
4. Ejecutar pipeline bloques especializados (si se quiere normalizar
   Derivatives/Leverage/Credit para los fondos de bloques primarios)
5. Verificar controles post-despliegue
```

### Controles post-despliegue:

```sql
-- Control 1: Accumulation_Policy casing (objetivo: 0)
SELECT Accumulation_Policy, COUNT(*)
FROM fund_master
WHERE Accumulation_Policy NOT IN ('ACCUMULATION', 'DISTRIBUTION')
  AND Accumulation_Policy IS NOT NULL
GROUP BY Accumulation_Policy;

-- Control 2: Currency_Hedged (objetivo: 0 "Yes")
SELECT Currency_Hedged, COUNT(*)
FROM fund_master
WHERE Currency_Hedged NOT IN ('Hedged', 'Unhedged')
  AND Currency_Hedged IS NOT NULL
GROUP BY Currency_Hedged;

-- Control 3: Profile-SRRI (objetivo: 0)
SELECT COUNT(*)
FROM fund_master
WHERE Profile = 'Conservador' AND SRRI >= 5;

-- Control 4: Strategy NULL (objetivo: <1%)
SELECT COUNT(*) AS strategy_null
FROM fund_master WHERE Strategy IS NULL;

-- Control 5: Investment_Universe NULL (objetivo: <5%)
SELECT COUNT(*) AS universe_null
FROM fund_master WHERE Investment_Universe IS NULL;

-- Control 6: Nature-Type coherencia (objetivo: 0)
SELECT fm.ISIN, fm.Fund_Name, fm.Fund_Nature, fm.Type
FROM fund_master fm
WHERE fm.Fund_Nature = 'Renta Variable'
  AND fm.Type IN ('Monetario', 'Monetario Público', 'Monetario Privado',
                  'Renta Fija Corto Plazo', 'Renta Fija Flexible');

-- Control 7: Sector_Focus idioma (objetivo: 0 inglés)
SELECT Sector_Focus, COUNT(*)
FROM fund_master
WHERE Sector_Focus IN ('Technology & Innovation', 'Energy & Resources',
                       'Utilities & Environment', 'Healthcare & Life Sciences')
GROUP BY Sector_Focus;

-- Control 8: Derivatives_Usage distribución (objetivo: YES + NO + LIMITED)
SELECT Derivatives_Usage, COUNT(*)
FROM fund_master
GROUP BY Derivatives_Usage;

-- Control 9: Investment_Universe=Liquidity coherencia
SELECT COUNT(*)
FROM fund_master
WHERE Investment_Universe = 'Liquidity'
  AND Fund_Nature NOT IN ('Monetario', 'Renta Fija Corto Plazo');

-- Control 10: Investment_Focus=Sector sin Sector_Focus
SELECT COUNT(*)
FROM fund_master
WHERE Investment_Focus = 'Sector' AND Sector_Focus IS NULL;
```
