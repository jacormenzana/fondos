# Estado del Backlog P1 — Referencia de Sesión
**Fecha:** Abril 2026  
**Ciclo de referencia:** p1_export_20260416.xlsx (3.204 fondos, schema v17)  
**Módulos desplegados en última entrega:**
- `pipeline.py` v20 — fixes BL-30/31/38 + default Unhedged
- `kiid_parser.py` v20 — terminadores BL-38 + patrones OC/fees v19
- `fund_characterizer.py` v18 — BL-26/27/28/29 *(pendiente verificar despliegue)*
- `benchmark_normalizer.py` vBL-39 — aliases nuevos y false positives

---

## 1. ITEMS RESUELTOS (controles en verde)

| BL | Descripción | Control | Resultado |
|----|-------------|---------|-----------|
| BL-19 | Sin "Mixto" singular en BD | `COUNT(*) WHERE Fund_Nature='Mixto'` | **0 ✅** |
| BL-32 | Sin Distribution_Frequency con Accumulation_Policy=NULL | `COUNT(*) WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL` | **0 ✅** |
| BL-33 | Sin Monetario/RFC con Investment_Universe=NULL | `COUNT(*) WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL` | **0 ✅** |
| BL-34 | Sin Credit_Quality="No aplica" | `COUNT(*) WHERE Credit_Quality='No aplica'` | **0 ✅** |
| BL-26 | Currency_Hedged sin "Yes"/"No" | `COUNT(*) WHERE Currency_Hedged IN ('Yes','No')` | **0 ✅** |
| BL-28 | Sin Credit_Quality="No aplica" (v18) | Ídem BL-34 | **0 ✅** |
| BL-37 | Ongoing_Charge NULL < 600 | `COUNT(*) WHERE Ongoing_Charge IS NULL` | **275 ✅** (objetivo <600) |
| BL-27 | Market_Cap_Focus en RV > 200 | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL` | **433 ✅** |

---

## 2. ITEMS DESPLEGADOS — VERIFICACIÓN PENDIENTE

Estos fixes están en los módulos entregados pero **aún no se ha ejecutado el pipeline** con la última versión. Los controles están en rojo en el ciclo anterior por la causa raíz COALESCE. Se espera que pasen en el próximo ciclo.

### BL-30 — Investment_Focus=Broad + Sector_Focus poblado (97 fondos)

**Causa raíz confirmada:** todos los 97 fondos son `Heuristic_Block=RESTANTES`, `Heuristic_Core=0`, `Theme=Core/General`. En el ciclo actual `fund_master_record["Sector_Focus"]=None` (el bloque no lo asigna) y `fund_master_record["Investment_Focus"]='Broad'` (del characterizer). El COALESCE de `sqlite_writer` preservaba el `Sector_Focus` antiguo de BD mientras sobreescribía `Investment_Focus='Broad'` — generando la inconsistencia.

**Fix en pipeline.py v20:** antes de aplicar INTER-11, el pipeline ahora lee `Sector_Focus` e `Investment_Focus` actuales de BD via `SELECT ... FROM fund_master WHERE ISIN=?`. Si `_sf_bd` está poblado y `_if_bd='Broad'`, escribe `Investment_Focus='Sector'` explícitamente (sobrescribe el COALESCE).

**Control post-ciclo:**
```sql
SELECT COUNT(*) FROM fund_master
WHERE Investment_Focus = 'Broad' AND Sector_Focus IS NOT NULL;
-- Esperado: 0
```

---

### BL-31 — Currency_Hedged='Hedged' + Hedging_Policy='UNHEDGED' (57 fondos)

**Causa raíz confirmada:** los 57 fondos son todos `Currency_Hedged='Hedged'` + `Hedging_Policy='UNHEDGED'`. `Currency_Hedged` venía de un ciclo anterior (detectado por nombre: `EURH`, `HDG`, etc.) y persistía vía COALESCE. En el ciclo actual `fund_master_record["Hedging_Policy"]='UNHEDGED'` (del parser KIID), pero la corrección INTER operaba con `fund_master_record.get("Currency_Hedged")=None` — no detectaba el conflicto.

**Fix en pipeline.py v20:** la corrección INTER lee `Currency_Hedged` y `Hedging_Policy` desde BD si `fund_master_record` los tiene a `None`. Con ambos valores en mano, aplica la corrección: `Hedging_Policy` prevalece sobre `Currency_Hedged`.

**Control post-ciclo:**
```sql
SELECT COUNT(*) FROM fund_master
WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED')
   OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED');
-- Esperado: 0
```

---

### BL-38 — Benchmarks contaminados (18 fondos: 1 largo, 17 con texto español)

**Casos exactos identificados en BD:**

| Valor contaminado | Fondos | Causa |
|-------------------|--------|-------|
| `"sofr), además"` | 11 | Parser captura SOFR + texto narrativo siguiente |
| `"msci european último informe"` | 3 | Terminador `último` no estaba en v19 |
| `"msci europe través de todos los canales de"` | 3 | Terminadores `través`, `canales` no estaban |
| `"msci all country asia ex-japan management (ireland) limited, european bank..."` | 1 | Texto de dirección postal; `management`, `limited`, `bank`, `centre`, `route` no estaban |

**Fix 1 — kiid_parser.py v20:** añadidos terminadores `además`, `través`, `último`, `canal(es)`, `management`, `limited`, `bank`, `centre`, `business`, `route`, `avenida`, `calle`, `street`, `road` y el patrón `\),?\s+[a-z]{3,}` (cierre de paréntesis seguido de texto).

**Fix 2 — pipeline.py v20:** nuevo bloque de limpieza defensiva. Cuando el parser devuelve `Benchmark_Declared=None`, se consulta BD y si el valor existente contiene alguno de los marcadores de contaminación (`además`, `través`, `último informe`, `management (ireland)`, `limited,`, `bank and`, `business centre`, `route de`, `hemos clasificado`, `corro `, `página`, `producto`, ` canales`), se fuerza `None` explícito (el COALESCE no preserva). Se registra en `ingestion_log` con tipo `BENCHMARK_CLEANUP`.

**Control post-ciclo:**
```sql
SELECT COUNT(*) FROM fund_master
WHERE LENGTH(Benchmark_Declared) > 100
  AND Benchmark_Declared != 'NO_BENCHMARK';
-- Esperado: 0

SELECT COUNT(*) FROM fund_master
WHERE Benchmark_Declared LIKE '%además%'
   OR Benchmark_Declared LIKE '%último informe%'
   OR Benchmark_Declared LIKE '%través%'
   OR Benchmark_Declared LIKE '%management (ireland)%';
-- Esperado: 0
```

---

### DEFAULT UNHEDGED — Currency_Hedged (nuevo, sin BL asignado)

**Problema:** Currency_Hedged tiene solo 8 valores `Unhedged` explícitos en los 3.204 fondos. Los fondos sin señal positiva de hedge en nombre/KIID quedan NULL. La heurística natural es que si un fondo tiene divisa EUR e invierte en Europa, o USD e invierte en EEUU, no necesita cobertura de divisa → es Unhedged por defecto.

**Fix en pipeline.py v20:** si `Currency_Hedged IS NULL` y `Hedging_Policy IS NULL` y la combinación `(Fund_Currency, Geography)` es natural (`EUR+Europa`, `EUR+Global`, `USD+EEUU`, `USD+Norteamérica`, `USD+Global`, `GBP+Reino Unido`, `JPY+Japón`, `CHF+Suiza`) y el nombre no contiene señal de hedge (`hedged`, `hdg`, `hgd`, `cubierto`, `cobertura`), se asigna `Currency_Hedged='Unhedged'`.

**Impacto estimado:** ~1.200 fondos adicionales con Currency_Hedged=Unhedged.

**Control post-ciclo:**
```sql
SELECT Currency_Hedged, COUNT(*) FROM fund_master
GROUP BY Currency_Hedged ORDER BY COUNT(*) DESC;
-- Esperado: Unhedged >> 8 (objetivo ~1200)
```

---

## 3. ITEMS EN PROGRESO — PARCIALMENTE RESUELTOS

### BL-35 — Entry_Fee NOT_FOUND (870 fondos, 27.2%)

**Estado actual:** Fee_Known_Flag distribution: ZERO_CONFIRMED=1.460, EXTRACTED=874, NOT_FOUND=870.

**Análisis de los 870 restantes (del que existen KIID texts):**

| Layout pendiente | Gestoras | Fondos | Motivo de fallo |
|-----------------|----------|--------|-----------------|
| `Costes de entrada` + importe EUR absoluto sin % | MorganStanley | ~100 | No hay porcentaje — solo "Hasta 200 EUR" |
| `Costes de entrada` sin sección de % (KIID antiguo sin tabla DDF) | Varios | ~265 | No hay ningún patrón de fee en el texto |
| Layout especial "400% del importe" (OCR noise Allianz) | Allianz | ~20 | OCR garbled: "400%" debería ser "4.00%" |
| Fondos UCITS sin sección de costes estructurada | Varios | ~485 | No aplicable (sin cobertura posible) |

**Próxima acción:**
- Los ~265 sin ningún patrón de fee son el **límite teórico** — no hay información en el KIID.
- Los ~100 MorganStanley necesitan patrón específico para "Hasta 200 EUR" → mapear a importe absoluto con lookup de NAV (fuera de alcance P1).
- Los ~20 OCR Allianz necesitan patrón de limpieza: `r'(\d+[,.]?\d*)\s*%\s+del\s+importe'` con filtro `if val <= 0.10`.

**Control:**
```sql
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master GROUP BY Fee_Known_Flag;
-- NOT_FOUND objetivo: < 500 en próximo ciclo
```

---

### BL-37 — Ongoing_Charge NULL (275 fondos, 8.6%) ✅ DENTRO DE OBJETIVO

Objetivo era <600. Con v19 se redujo de 743 a 275. Los 275 restantes son el límite real de extracción: el texto KIID no contiene ninguna de las estructuras de costes reconocidas (ni `incidencia de costes`, ni `del valor de su inversión`, ni `cada año/afio`, ni `Gastos corrientes`). Son principalmente fondos muy antiguos (KIID formato 2010-2012) o con texto OCR muy degradado.

**Estado:** CERRADO como ítem activo. Monitorear con control periódico.

---

## 4. GAPS ESTRUCTURALES — ANÁLISIS COMPLETADO, ACCIÓN FUTURA

Estos atributos tienen null ratio alto **por diseño o por límite de señal disponible**, no por bugs en la pipeline. Se documentan para decisión en backlog futuro.

| Atributo | NULL | Null% | Naturaleza del gap | Prioridad |
|----------|------|-------|--------------------|-----------|
| `Subtype` | 3.020 | 94.3% | Solo aplica a Alternativo/RF específico. Cobertura actual correcta. | BAJA |
| `Sector_Focus` | 2.830 | 88.3% | Solo aplica a fondos sectoriales (~374 tienen valor). Cobertura correcta. | BAJA |
| `Market_Cap_Focus` | 2.738 | 85.5% | Solo aplica a RV. Tras BL-27: 433 RV poblados (+319). Los 2.305 no-RV son correctamente NULL. Los ~500 RV restantes sin señal de nombre ni benchmark conocido. | MEDIA |
| `Currency_Hedged` | 2.565 | 80.1% | Tras fix Unhedged: se espera ~1.200 recuperados. Los ~1.365 restantes son fondos sin divisa/geografía en BD o con combinación ambigua. | MEDIA (en ciclo) |
| `Hedging_Policy` | 2.309 | 72.1% | Solo se extrae del KIID (patrón textual). 488 UNHEDGED + 407 HEDGED. Los 2.309 NULL no tienen señal en KIID. Diferente semántica a `Currency_Hedged`. | MEDIA |
| `Style_Profile` | 2.210 | 69.0% | Solo RV con señal explícita Growth/Value/Income en nombre o KIID. Los 994 poblados son correctos. Ampliar cobertura requiere análisis de KIID de RV sin señal (sesión futura). | MEDIA |
| `Benchmark_Declared` | 1.345 | 42.0% | 1.740 fondos con benchmark extraído + 119 con `NO_BENCHMARK` explícito. Los 1.345 NULL genuinamente no tienen benchmark detectable en KIID. | BAJA |
| `Sfdr_Article` | 1.219 | 38.1% | Fondos sin mención explícita de Art. 6/8/9 en KIID. Muchos son pre-SFDR o tienen KIIDs muy antiguos. | MEDIA |
| `Accumulation_Policy` | 786 | 24.5% | 2.418 poblados. Los 786 no tienen señal ni en nombre ("ACC"/"DIST"/etc.) ni en KIID. | MEDIA |
| `Geography` | 424 | 13.2% | Fondos de naturaleza Global sin señal específica o RESTANTES con nombre ambiguo. | MEDIA |

---

## 5. CAUSA RAÍZ SISTÉMICA — COALESCE EN sqlite_writer

**Documentación para referencia arquitectónica.**

`sqlite_writer.publish_fund()` usa `COALESCE(new_value, old_value)` en la sentencia UPDATE. Esto significa:
- Si `fund_master_record["X"] = None` (el ciclo actual no produjo valor), BD preserva el valor antiguo.
- Si `fund_master_record["X"] = valor_nuevo` (no None), BD sobrescribe con el valor nuevo.

**Consecuencias identificadas:**
1. **BL-30/31:** el ciclo actual produce `Investment_Focus='Broad'` (no NULL) pero `Sector_Focus=None` → COALESCE preserva `Sector_Focus` antiguo. Resultado: inconsistencia nueva a pesar de que el pipeline tiene la corrección INTER.
2. **BL-38:** el parser produce `Benchmark_Declared=None` para benchmarks que ahora rechaza → COALESCE preserva el benchmark contaminado antiguo.
3. **BL-34 previo:** `fund_characterizer` antiguo generaba `Credit_Quality='No aplica'` → al ser no-NULL, COALESCE dejaba pasar el valor incorrecto.

**Soluciones aplicadas:**
- BL-30/31: leer BD previo antes de comparar en INTER → escribir valor coherente explícito.
- BL-38: detectar contaminación en BD y forzar NULL explícito antes de que COALESCE actúe.
- Principio general (Principio #X a documentar): **Toda corrección INTER debe operar sobre valores efectivos (actual OR BD_previo), no solo sobre `fund_master_record`.**

---

## 6. MÓDULOS DESPLEGADOS — VERSIONES VIGENTES

| Módulo | Versión | Cambios principales | Estado |
|--------|---------|---------------------|--------|
| `pipeline.py` | v20 | BL-30/31 (lectura BD previo en INTER), BL-38 (limpieza defensiva Benchmark), default Unhedged, BL-34b normaliza "No aplica", BL-27 Market_Cap_Focus benchmark | **DESPLEGADO** |
| `kiid_parser.py` | v20 | BL-38 terminadores v20 (`además`, `través`, `último`, `canal`, `management`, `limited`, `bank`, `centre`, `business`, `route`); OC patrones v19 DWS/Allianz; fees patrones v19 Ninguna/cobrarle | **DESPLEGADO** |
| `classify_utils.py` | v4 | BL-19/22/23/24/30/31/32/33 | **DESPLEGADO** |
| `fund_characterizer.py` | v18 | BL-26/27/28/29 | **PENDIENTE VERIFICAR** |
| `benchmark_normalizer.py` | vBL-39 | +20 aliases, +9 false positives | **PENDIENTE VERIFICAR** |
| `restantes.py` | v4 | BL-09/20/21 | **DESPLEGADO** |

> ⚠️ `fund_characterizer.py` v18 y `benchmark_normalizer.py` vBL-39 fueron entregados pero no se ha confirmado su despliegue en producción. Algunos efectos de BL-26/27/28/29 pueden estar ausentes si se usa la versión antigua.

---

## 7. QUERIES DE VALIDACIÓN COMPLETAS (post-ciclo)

```sql
-- ITEMS RESUELTOS (deben devolver 0)
SELECT 'BL-19' AS bl, COUNT(*) AS n FROM fund_master WHERE Fund_Nature='Mixto'
UNION ALL
SELECT 'BL-32', COUNT(*) FROM fund_master WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL
UNION ALL
SELECT 'BL-33', COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL
UNION ALL
SELECT 'BL-34', COUNT(*) FROM fund_master WHERE Credit_Quality='No aplica'
UNION ALL
SELECT 'BL-26', COUNT(*) FROM fund_master WHERE Currency_Hedged IN ('Yes','No');

-- ITEMS CON FIX PENDIENTE DE VERIFICAR
SELECT 'BL-30', COUNT(*) FROM fund_master WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL
UNION ALL
SELECT 'BL-31', COUNT(*) FROM fund_master
  WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED')
     OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED')
UNION ALL
SELECT 'BL-38_largo', COUNT(*) FROM fund_master
  WHERE LENGTH(Benchmark_Declared) > 100 AND Benchmark_Declared != 'NO_BENCHMARK'
UNION ALL
SELECT 'BL-38_español', COUNT(*) FROM fund_master
  WHERE Benchmark_Declared LIKE '%además%' OR Benchmark_Declared LIKE '%último informe%'
     OR Benchmark_Declared LIKE '%través%' OR Benchmark_Declared LIKE '%management (ireland)%';

-- COBERTURA — seguimiento de progreso
SELECT 'OC_null'         AS attr, COUNT(*) AS n FROM fund_master WHERE Ongoing_Charge IS NULL
UNION ALL
SELECT 'entry_NOT_FOUND', COUNT(*) FROM fund_master WHERE Fee_Known_Flag='NOT_FOUND'
UNION ALL
SELECT 'exit_null',       COUNT(*) FROM fund_master WHERE Exit_Fee_Pct IS NULL
UNION ALL
SELECT 'Currency_Unhedged', COUNT(*) FROM fund_master WHERE Currency_Hedged='Unhedged'
UNION ALL
SELECT 'MarketCap_RV',    COUNT(*) FROM fund_master
  WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL;
```

---

## 8. PRÓXIMOS ITEMS (backlog pendiente)

Ordenados por impacto/esfuerzo:

1. **BL-35 continuación** — Analizar ~485 fondos NOT_FOUND sin ningún patrón de fee en KIID. Verificar si son genuinamente KIIDs sin sección de costes o si hay un layout no cubierto.

2. **Style_Profile** (69% NULL, 2.210 fondos) — Análisis de KIIDs de fondos RV sin estilo detectado. Identificar si mencionan "crecimiento", "valor", "dividendo", "quality" en el objetivo de inversión.

3. **Accumulation_Policy** (24.5% NULL, 786 fondos) — Los fondos sin señal en nombre ni KIID. Verificar si `Sfdr_Article` o `Distribution_Frequency` pueden inferirlo.

4. **Geography** (13.2% NULL, 424 fondos) — Análisis de fondos RESTANTES sin geografía detectable. Potencial uso de Benchmark_Declared para inferir geografía.

5. **SFDR_Article** (38% NULL, 1.219 fondos) — Ampliar patrones para formatos DDF modernos que no mencionan artículo explícitamente pero sí "promueve características medioambientales" o "sin objetivo de inversión sostenible".

6. **P2 — Factores macro** — Añadir series FRED: `BAMLH0A0HYM2` (High-Yield spread), `VIXCLS` (VIX), `T10Y2YM` (term spread). Infraestructura de descarga y almacenamiento.

7. **Principio de arquitectura COALESCE** — Documentar como Principio #10: toda corrección de valores persistentes en BD debe leer el valor actual de BD antes de aplicar la corrección, no confiar en que `fund_master_record` lo tenga. Considerar si `sqlite_writer` debe exponer modo `force_overwrite` para campos específicos.
