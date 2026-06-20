# BL-51 Problema B — Comisiones con estructura mixta: análisis y decisión de schema

**Fecha:** 19 de abril de 2026  
**Estado:** Análisis completado. Decisión de schema pendiente de confirmación.  
**Prerequisito de:** implementación en `sqlite_writer.py`, `pipeline.py` y modelo P3.

---

## 1. DESCRIPCIÓN DEL PROBLEMA

El schema actual modela las comisiones de entrada y salida como escalares porcentuales:

```sql
Entry_Fee_Pct  REAL,   -- ej. 0.030 = 3,00%
Exit_Fee_Pct   REAL,   -- ej. 0.010 = 1,00%
```

El análisis exploratorio sobre `Raw_KIID_Text` revela que algunos fondos definen sus comisiones mediante una **estructura mixta**: un porcentaje máximo combinado con un tope o suelo en términos absolutos (euros u otra divisa). Esta estructura no es representable en el schema actual.

---

## 2. TIPOLOGÍA DE ESTRUCTURAS OBSERVADAS EN KIID

Del análisis de los textos KIID, se identifican las siguientes variantes de estructura mixta:

### Tipo A — Porcentaje con tope máximo fijo ("capped fee")
> *"Hasta el 3,00%, con un máximo de 150 EUR por suscripción"*  
> *"Maximum 5%, subject to a cap of EUR 500"*  
> *"Hasta el 2%, máximo 100 €"*

Semántica: el inversor paga `min(importe × pct, cap_EUR)`. Para importes pequeños, el porcentaje es el coste efectivo; para importes grandes, el cap limita el coste real.

### Tipo B — Porcentaje con suelo mínimo fijo ("floored fee")
> *"Mínimo 15 EUR o el 1,50%, lo que sea mayor"*  
> *"Minimum EUR 10 or 1.00% of investment amount"*

Semántica: el inversor paga `max(floor_EUR, importe × pct)`. Para suscripciones pequeñas, el mínimo fijo domina.

### Tipo C — Importe fijo puro (sin porcentaje)
> *"Costes de entrada: 25 EUR por transacción"*  
> *"Entry fee: EUR 10 flat"*

Semántica: tarifa plana independiente del importe. No representable como porcentaje.

### Tipo D — Porcentaje con tope variable por tramo de importe
> *"Hasta 10.000 EUR: 3,00%. Más de 10.000 EUR: 2,00%"*

Semántica: estructura de comisión escalonada. Requeriría una tabla, no un escalar.

### Prevalencia estimada por tipo

| Tipo | Descripción | Prevalencia estimada |
|------|-------------|---------------------|
| A | Pct + cap máximo | ★★★ (más frecuente) |
| B | Pct + suelo mínimo | ★★ |
| C | Importe fijo puro | ★ |
| D | Tramos escalonados | ★ (raro en KIID estándar) |

La gran mayoría de los 3.204 fondos tiene comisiones de Tipo estándar (porcentaje puro). Los tipos A-D son una minoría, pero son exactamente los casos que el schema actual descarta o modela incorrectamente.

---

## 3. IMPACTO EN P3

El motor de scoring de P3 utilizará las comisiones como coste de rotación para calcular el coste efectivo de cada cambio de posición. Si `Entry_Fee_Pct` almacena solo el porcentaje máximo sin el cap, el modelo sobrestimará el coste para suscripciones de importe elevado. Esto puede sesgar:

- El ranking de fondos con comisiones "capped" frente a fondos con porcentaje puro
- La frecuencia óptima de rotación calculada por el motor
- La comparación de coste entre fondos de distintas gestoras (algunas usan caps, otras no)

**El impacto es real pero acotado:** solo afecta a los fondos con estructura mixta (minoría del universo). Para el 90%+ del universo con comisiones porcentuales puras, el schema actual es correcto.

---

## 4. OPCIONES DE SCHEMA

### Opción 1 — Campos adicionales `Entry_Fee_Cap_EUR` / `Exit_Fee_Cap_EUR` / `Entry_Fee_Floor_EUR` (recomendada)

```sql
-- Extensión schema v18
Entry_Fee_Pct      REAL,    -- Porcentaje máximo (sin cambio)
Entry_Fee_Cap_EUR  REAL,    -- Tope máximo en EUR (NULL si no aplica)
Entry_Fee_Floor_EUR REAL,   -- Suelo mínimo en EUR (NULL si no aplica)
Exit_Fee_Pct       REAL,    -- Porcentaje máximo (sin cambio)
Exit_Fee_Cap_EUR   REAL,    -- Tope máximo en EUR (NULL si no aplica)
Exit_Fee_Floor_EUR REAL,    -- Suelo mínimo en EUR (NULL si no aplica)
```

**Ventajas:**
- Backward compatible: los fondos con porcentaje puro tienen los nuevos campos en NULL, sin impacto.
- Tipado fuerte: los campos numéricos son consultables directamente en P3.
- Expresa exactamente la semántica (cap distinto de pct, floor distinto de pct).
- P3 puede implementar la función de coste efectivo: `min(importe × pct, cap)` cuando cap no es NULL.

**Desventajas:**
- Requiere ALTER TABLE en schema v18 + actualización del UPSERT en `sqlite_writer.py`.
- Requiere nuevos patrones de extracción en `kiid_parser.py` para los importes cap/floor.
- El Tipo C (tarifa plana pura) sigue sin representarse (Entry_Fee_Pct=NULL con Entry_Fee_Cap_EUR=valor).

**Tratamiento del Tipo C:** se puede representar como `Entry_Fee_Pct=0.0` (sin porcentaje) + `Entry_Fee_Cap_EUR=25` (tarifa plana), usando cap como el importe fijo. O bien añadir un campo `Entry_Fee_Flat_EUR`. Decisión pendiente.

---

### Opción 2 — Campo textual `Fee_Structure_Notes TEXT`

```sql
Fee_Structure_Notes TEXT,   -- Descripción textual de la estructura de comisión
                             -- ej. "Hasta 3%, máx 150 EUR" | "Mínimo 15 EUR o 1.5%"
```

**Ventajas:**
- Implementación trivial: solo añadir un campo TEXT.
- No requiere parseo estructurado: se almacena el extracto del KIID directamente.

**Desventajas:**
- No consultable directamente por P3 (requiere parseo en tiempo de ejecución o preprocesado).
- Inconsistencia con el principio de tipado fuerte del resto del schema.
- No resuelve el problema de modelado en P3: los campos numéricos seguirían siendo incorrectos.

**Recomendación:** descartada como solución principal. Puede usarse como campo auxiliar de trazabilidad junto con la Opción 1.

---

### Opción 3 — Mantener schema actual + flag de advertencia

```sql
Fee_Known_Flag TEXT,  -- Ampliar valores: 'EXTRACTED' | 'ZERO_CONFIRMED' | 
                      -- 'NOT_FOUND' | 'MIXED_STRUCTURE' (nuevo)
```

Cuando se detecte estructura mixta, registrar `Fee_Known_Flag='MIXED_STRUCTURE'` y almacenar solo el porcentaje máximo en `Entry_Fee_Pct`, con un aviso de que el valor es una cota superior no el coste efectivo.

**Ventajas:**
- Cambio mínimo, sin ALTER TABLE.
- Señaliza el problema sin perder el dato porcentual existente.

**Desventajas:**
- No resuelve el problema de modelado en P3.
- El cap/floor se pierde: no es recuperable después sin re-parsear los KIIDs.
- Solución temporal que crea deuda técnica para P3.

**Recomendación:** válida como solución transitoria hasta despliegue de Opción 1, pero no como solución definitiva.

---

## 5. DECISIÓN RECOMENDADA

**Implementar Opción 1** con la siguiente secuencia de actuaciones:

### Fase 1 — Schema (schema v18)

```sql
ALTER TABLE fund_master ADD COLUMN Entry_Fee_Cap_EUR   REAL DEFAULT NULL;
ALTER TABLE fund_master ADD COLUMN Entry_Fee_Floor_EUR REAL DEFAULT NULL;
ALTER TABLE fund_master ADD COLUMN Exit_Fee_Cap_EUR    REAL DEFAULT NULL;
ALTER TABLE fund_master ADD COLUMN Exit_Fee_Floor_EUR  REAL DEFAULT NULL;
```

**Alternativa no destructiva:** si se prefiere un script idempotente, añadir los campos en `schema_fondos.sql` con `IF NOT EXISTS` o gestionar mediante versión del schema.

### Fase 2 — Extracción en `kiid_parser.py` (nueva función `_detect_fee_cap_floor`)

```python
# Patrón Tipo A: "hasta el X%, con un máximo de NNN EUR"
_EF_CAP_RE = re.compile(
    r'(?:costes?\s+de\s+entrada|gastos?\s+de\s+entrada|comisi[oó]n\s+de\s+suscripci[oó]n)'
    r'[\s\S]{0,400}?'
    r'(?:m[aá]ximo\s+(?:de\s+)?|cap(?:\s+of)?\s+)'
    r'(?:EUR\s*|€\s*)?([\d]+(?:[,.][\d]+)?)'
    r'\s*(?:EUR|€|euros?)',
    re.IGNORECASE)

# Patrón Tipo B: "mínimo NNN EUR o el X%"
_EF_FLOOR_RE = re.compile(
    r'(?:costes?\s+de\s+entrada|gastos?\s+de\s+entrada|comisi[oó]n\s+de\s+suscripci[oó]n)'
    r'[\s\S]{0,400}?'
    r'(?:m[íi]nimo\s+(?:de\s+)?|minimum\s+(?:of\s+)?)'
    r'(?:EUR\s*|€\s*)?([\d]+(?:[,.][\d]+)?)'
    r'\s*(?:EUR|€|euros?)',
    re.IGNORECASE)

def _detect_fee_cap_floor(text: str) -> dict:
    """
    Extrae importes de tope (cap) y suelo (floor) de comisiones de entrada/salida.
    
    Returns:
        dict con claves Entry_Fee_Cap_EUR, Entry_Fee_Floor_EUR,
        Exit_Fee_Cap_EUR, Exit_Fee_Floor_EUR (todos REAL o None).
    """
    result = {
        'Entry_Fee_Cap_EUR': None,
        'Entry_Fee_Floor_EUR': None,
        'Exit_Fee_Cap_EUR': None,
        'Exit_Fee_Floor_EUR': None,
    }
    if not text:
        return result
    
    # Extraer cap entrada
    m_cap = _EF_CAP_RE.search(text) or _EF_CAP_RE.search(text.lower())
    if m_cap:
        try:
            result['Entry_Fee_Cap_EUR'] = float(m_cap.group(1).replace(',', '.'))
        except ValueError:
            pass
    
    # Extraer floor entrada
    m_floor = _EF_FLOOR_RE.search(text) or _EF_FLOOR_RE.search(text.lower())
    if m_floor:
        try:
            result['Entry_Fee_Floor_EUR'] = float(m_floor.group(1).replace(',', '.'))
        except ValueError:
            pass
    
    # Análogos para salida (pendiente de implementación con triggers de salida)
    return result
```

**Nota:** esta función se implementa en una versión futura del parser (v25 o posterior) una vez confirmado el schema. No está incluida en el v24 actual para no anticipar decisiones de schema no ratificadas.

### Fase 3 — Pipeline

Añadir en el bloque de construcción de `fund_master_record` (líneas ~520-530 de `pipeline.py`):

```python
# BL-51B: campos de estructura mixta de comisiones
"Entry_Fee_Cap_EUR":   parsed.get("Entry_Fee_Cap_EUR"),
"Entry_Fee_Floor_EUR": parsed.get("Entry_Fee_Floor_EUR"),
"Exit_Fee_Cap_EUR":    parsed.get("Exit_Fee_Cap_EUR"),
"Exit_Fee_Floor_EUR":  parsed.get("Exit_Fee_Floor_EUR"),
```

### Fase 4 — sqlite_writer

Añadir los 4 nuevos campos al INSERT y al ON CONFLICT con política COALESCE (igual que `Entry_Fee_Pct`):

```sql
Entry_Fee_Cap_EUR   = COALESCE(excluded.Entry_Fee_Cap_EUR,   fund_master.Entry_Fee_Cap_EUR),
Entry_Fee_Floor_EUR = COALESCE(excluded.Entry_Fee_Floor_EUR, fund_master.Entry_Fee_Floor_EUR),
Exit_Fee_Cap_EUR    = COALESCE(excluded.Exit_Fee_Cap_EUR,    fund_master.Exit_Fee_Cap_EUR),
Exit_Fee_Floor_EUR  = COALESCE(excluded.Exit_Fee_Floor_EUR,  fund_master.Exit_Fee_Floor_EUR),
```

### Fase 5 — Función de coste efectivo en P3

```python
def entry_fee_effective(entry_fee_pct: float, entry_fee_cap_eur: float,
                        importe_eur: float) -> float:
    """
    Calcula la comisión de entrada efectiva considerando el tope fijo.
    
    Args:
        entry_fee_pct:     Comisión porcentual (ej. 0.03 = 3%)
        entry_fee_cap_eur: Tope máximo en EUR (None si no aplica)
        importe_eur:       Importe de la suscripción en EUR
    
    Returns:
        float: Comisión efectiva en EUR
    """
    fee_pct_eur = importe_eur * entry_fee_pct
    if entry_fee_cap_eur is not None:
        return min(fee_pct_eur, entry_fee_cap_eur)
    return fee_pct_eur
```

---

## 6. CUANTIFICACIÓN PREVIA A IMPLEMENTACIÓN

Antes de iniciar la Fase 2, ejecutar las siguientes queries de diagnóstico sobre `Raw_KIID_Text` para confirmar la prevalencia real de estructuras mixtas en el universo de 3.204 fondos:

```sql
-- Fondos con posible estructura Tipo A (cap máximo)
SELECT COUNT(*) FROM fund_kiid_metadata
WHERE LOWER(Raw_KIID_Text) LIKE '%máximo%eur%'
   OR LOWER(Raw_KIID_Text) LIKE '%maximum%eur%'
   OR LOWER(Raw_KIID_Text) LIKE '%cap of eur%'
   OR LOWER(Raw_KIID_Text) LIKE '%sujeto a un máximo%';

-- Fondos con posible estructura Tipo B (suelo mínimo)
SELECT COUNT(*) FROM fund_kiid_metadata
WHERE LOWER(Raw_KIID_Text) LIKE '%mínimo%eur%'
   OR LOWER(Raw_KIID_Text) LIKE '%minimum%eur%'
   OR LOWER(Raw_KIID_Text) LIKE '%minimum of eur%';

-- Fondos con posible estructura Tipo C (tarifa plana)
SELECT COUNT(*) FROM fund_kiid_metadata
WHERE LOWER(Raw_KIID_Text) LIKE '%eur por transacción%'
   OR LOWER(Raw_KIID_Text) LIKE '%eur per transaction%'
   OR LOWER(Raw_KIID_Text) LIKE '%flat fee%';
```

Si el volumen de fondos con estructura mixta es inferior al 2% del universo (~64 fondos), la extensión del schema sigue siendo recomendable pero puede posponerse a después de P2. Si supera el 5% (~160 fondos), la implementación debe adelantarse.

---

## 7. RESUMEN DE DECISIONES

| Decisión | Elección | Razón |
|----------|----------|-------|
| Opción de schema | **Opción 1** (campos numéricos explícitos) | Tipado fuerte, consultable por P3, backward compatible |
| Alcance v24 | Solo Problema A (patrones) | No anticipar schema hasta confirmar prevalencia |
| Schema v18 | Añadir 4 columnas REAL con DEFAULT NULL | ALTER TABLE no destructivo |
| Tratamiento Tipo C (tarifa plana) | `Entry_Fee_Pct=0.0` + `Entry_Fee_Cap_EUR=importe` | Reutilizar cap como tarifa plana |
| Tratamiento Tipo D (tramos) | No modelar en v18 | Prevalencia mínima; requeriría tabla separada |
| Función coste efectivo P3 | `min(importe × pct, cap)` si cap no NULL | Semántica correcta del cap |

---

## 8. CONTROL SQL POST-IMPLEMENTACIÓN (Fase 2+)

```sql
-- Fondos con estructura mixta detectada (post-v25)
SELECT COUNT(*) FROM fund_master
WHERE Entry_Fee_Cap_EUR IS NOT NULL OR Entry_Fee_Floor_EUR IS NOT NULL;

-- Distribución por tipo de estructura
SELECT
    CASE
        WHEN Entry_Fee_Cap_EUR IS NOT NULL AND Entry_Fee_Floor_EUR IS NULL THEN 'Tipo A (cap)'
        WHEN Entry_Fee_Floor_EUR IS NOT NULL AND Entry_Fee_Cap_EUR IS NULL THEN 'Tipo B (floor)'
        WHEN Entry_Fee_Cap_EUR IS NOT NULL AND Entry_Fee_Floor_EUR IS NOT NULL THEN 'Tipo AB (cap+floor)'
        ELSE 'Estándar (pct puro)'
    END AS fee_structure_type,
    COUNT(*) AS n
FROM fund_master
GROUP BY 1 ORDER BY 2 DESC;

-- Verificar coherencia: cap debe ser >= 0, floor >= 0
SELECT COUNT(*) FROM fund_master
WHERE Entry_Fee_Cap_EUR < 0 OR Entry_Fee_Floor_EUR < 0
   OR Exit_Fee_Cap_EUR < 0 OR Exit_Fee_Floor_EUR < 0;
-- Esperado: 0
```

---

**Fin del documento. Pendiente de ratificación de la decisión de schema antes de implementar Fases 1-4.**
