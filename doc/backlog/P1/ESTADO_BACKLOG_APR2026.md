# Estado del Backlog P1 — Referencia de Sesión
**Fecha:** Abril 2026  
**Ciclo de referencia:** p1_export_20260418.xlsx (3.204 fondos, schema v17)  
**Módulos desplegados en última entrega:**
- `pipeline.py` v22 — BL-38 refactorizado, Hedging_Policy default simultáneo, Universe+Geography desde Benchmark
- `kiid_parser.py` v21 — BL-35 patrones 5 gestoras (JPMorgan/Schroeder/UBS/Amundi/M&G)

---

## 1. ITEMS RESUELTOS (controles en verde)

| BL | Descripción | Control | Resultado |
|----|-------------|---------|-----------|
| BL-19 | Sin "Mixto" singular en BD | `COUNT(*) WHERE Fund_Nature='Mixto'` | **0 ✅** |
| BL-26 | Currency_Hedged sin "Yes"/"No" | `COUNT(*) WHERE Currency_Hedged IN ('Yes','No')` | **0 ✅** |
| BL-28 | Sin Credit_Quality="No aplica" (v18) | `COUNT(*) WHERE Credit_Quality='No aplica'` | **0 ✅** |
| BL-30 | Sin Investment_Focus=Broad + Sector_Focus poblado | `COUNT(*) WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL` | **0 ✅** |
| BL-31 | Sin contradicción Currency_Hedged vs Hedging_Policy | `COUNT(*) WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED') OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED')` | **0 ✅** |
| BL-32 | Sin Distribution_Frequency con Accumulation_Policy=NULL | `COUNT(*) WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL` | **0 ✅** |
| BL-33 | Sin Monetario/RFC con Investment_Universe=NULL | `COUNT(*) WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL` | **0 ✅** |
| BL-34 | Sin Credit_Quality="No aplica" | `COUNT(*) WHERE Credit_Quality='No aplica'` | **0 ✅** |
| BL-38 | Sin benchmarks contaminados | `COUNT(*) WHERE LENGTH(Benchmark_Declared)>100 AND Benchmark_Declared!='NO_BENCHMARK'` | **0 ✅** |
| BL-27 | Market_Cap_Focus en RV > 200 | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL` | **433 ✅** |
| BL-37 | Ongoing_Charge NULL < 600 | `COUNT(*) WHERE Ongoing_Charge IS NULL` | **270 ✅** (objetivo era <600) |

---

## 2. MEJORAS DE COBERTURA — CICLO ACTUAL vs ANTERIOR

| Atributo | Null anterior | Null actual | Variación | Estado |
|----------|--------------|-------------|-----------|--------|
| `Hedging_Policy` | 72,07% | 24,88% | **−47,19 pp** ✅ Mejora mayor |
| `Entry_Fee_Pct` | 26,84% | 6,74% | **−20,10 pp** ✅ Mejora mayor |
| `Geography` | 11,77% | 9,55% | −2,22 pp ✅ |
| `Investment_Universe` | 8,71% | 6,34% | −2,37 pp ✅ |
| `Currency_Hedged` | 37,30% | 35,83% | −1,47 pp ✅ |
| `Ongoing_Charge` | 8,49% | 8,43% | −0,06 pp → ver BL-37b |
| `Accumulation_Policy` | 18,54% | 18,54% | 0 → ver BL-40 |
| `Benchmark_Declared` | 45,97% | 45,94% | −0,03 pp estable |
| `Exit_Fee_Pct` | 23,06% | 23,00% | −0,06 pp estable |
| `SFDR_Article` | 37,89% | 37,83% | −0,06 pp estable |

---

## 3. ITEMS CON FIX PENDIENTE — SIGUIENTE CICLO

### BL-35b — Entry_Fee NOT_FOUND residual (223 fondos)

**Estado actual:** NOT_FOUND bajó de 867 a 223 tras v21 (−644 fondos, 74% reducción). Los 223 restantes corresponden a gestoras no cubiertas en v21.

**Gestoras con fix identificado y validado (79 fondos adicionales):**

| Gestora | n | Layout | Fix |
|---------|---|--------|-----|
| Thread | 55/58 | `"Costes de entrada Se incluyen costes de distribución del X % ... NNN EUR"` | `_EF_THREAD_DISTRIB` |
| AXA | 24/24 | `"Costes de entrada Nosotros no facturamos el coste de entrada. €0"` | `_EF_AXA_NO_FACTURA` → ZERO_CONFIRMED |

**Gestoras sin fix viable (144 fondos):**

| Gestora | n | Motivo |
|---------|---|--------|
| Natixis (H2O) | 28 | KIIDs son documentos de escisión/liquidación — no DDF estándar |
| HSBC | 26 | Formato KID distinto: sección "Costes a lo largo del tiempo" sin porcentaje explícito |
| Trowe | 17 | Sin sección de costes de entrada estructurada |
| Rotschild | 13 | Ídem |
| FlossBach/GAM | 18 | Ídem |

**Patrones a añadir en kiid_parser.py v22:**
```python
# Thread: "Costes de entrada Se incluyen costes de distribución del X % del importe"
_EF_THREAD_DISTRIB = re.compile(
    r'Costes\s+de\s+entrada\s+Se\s+incluyen\s+costes?\s+de\s+distribuci[oó]n\s+del\s+'
    r'([\d]+[,\.]?\s*[\d]*)\s*%',
    re.IGNORECASE
)
# AXA ZERO: "Nosotros no facturamos el coste de entrada"
_EF_AXA_NO_FACTURA = re.compile(
    r'nosotros\s+no\s+facturamos\s+el\s+coste\s+de\s+entrada',
    re.IGNORECASE
)
```

**Control post-ciclo:**
```sql
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
GROUP BY Fee_Known_Flag ORDER BY 2 DESC;
-- Esperado: NOT_FOUND ≈ 144 (límite estructural)
```

---

### BL-37b — Ongoing_Charge NULL residual (270 fondos)

**Estado actual:** 270 NULL. Objetivo anterior (<600) ya cumplido. JPMorgan concentra 178/270 (66%) con texto OCR 100% fusionado — el patrón `_OC_DEL_VALOR_RE` del parser usa espacios y no matchea texto fused.

**Fix identificado y validado: JPMorgan fused OC (177/178 — 99%)**

Layout: `"comisionesdegestiónyotros1,90%delvalordesuinversiónalaño"`

```python
# Añadir en _OC_FUSED_PATTERNS (kiid_parser.py v22):
re.compile(
    r'comisionesdegesti[oó]nyotros([\d]+[,\.][\d]+)%delvalordesuinversi[oó]n',
    re.IGNORECASE
)
```

**Gestoras residuales sin fix viable:** HSBC (26) usa formato "Costes a lo largo del tiempo" sin porcentaje explícito; Allianz (22) ya tienen patrón pero algunos KIIDs muy antiguos sin sección OCR legible; resto (44) son límite estructural.

**Control post-ciclo:**
```sql
SELECT COUNT(*) FROM fund_master WHERE Ongoing_Charge IS NULL;
-- Esperado: ≈ 93 (270 − 177 JPMorgan)
```

---

### BL-40 — Accumulation_Policy NULL (594 fondos)

**Estado actual:** 594 NULL (18,54%). Sin variación respecto al ciclo anterior. Las señales de nombre (ACC/DIST/INC) y los patrones KIID actuales no cubren los formatos Deutsche y BlackRock.

**Fixes identificados y validados (198 fondos adicionales — 33% del total NULL):**

| Gestora | n NULL | Recoverable | Layout | Valor |
|---------|--------|-------------|--------|-------|
| Deutsche/DWS | 108 | 103 | `"Las acciones del fondo son de acumulación, es decir, los rendimientos y ganancias no se reparten sino que se reinvierten"` | ACCUMULATION |
| BlackRock | 107 | 95 | `"las acciones serán no distributivas (los ingresos por dividendo se incorporarán a su valor)"` | ACCUMULATION |

**Patrones a añadir en kiid_parser.py v22:**
```python
# Deutsche/DWS ACCUMULATION
r'acciones?\s+del\s+fondo\s+son\s+de\s+acumulaci[oó]n'
r'|rendimientos\s+y\s+ganancias\s+no\s+se\s+reparten\s+sino\s+que\s+se\s+reinvierten'

# BlackRock ACCUMULATION
r'acciones?\s+ser[aá]n\s+no\s+distributivas?'
```

**Residual después del fix (396 fondos):** No tienen señal de política en el KIID. Son fondos con KIIDs muy antiguos (pre-2015) o con texto OCR degradado. Límite estructural.

**Control post-ciclo:**
```sql
SELECT COUNT(*) FROM fund_master WHERE Accumulation_Policy IS NULL;
-- Esperado: ≈ 396 (594 − 198)
```

---

## 4. GAPS ESTRUCTURALES — LÍMITE REAL DE EXTRACCIÓN

Estos atributos tienen null ratio alto **por diseño o por límite de señal disponible**. No son bugs ni gaps de cobertura del parser: reflejan fondos que genuinamente no tienen esa información en el KIID.

| Atributo | NULL actual | Null% | Naturaleza del gap | Acción |
|----------|-------------|-------|--------------------|--------|
| `Subtype` | 3.021 | 94,3% | Solo aplica a combinaciones Nature×Type con variante estructural. 183 fondos con valor es cobertura correcta. | Ninguna |
| `Sector_Focus` | 2.830 | 88,3% | Solo fondos sectoriales (~374 tienen valor). Cobertura correcta. | Ninguna |
| `Market_Cap_Focus` | 2.738 | 85,5% | 433 RV poblados (post BL-27). Los ~1.242 RV restantes sin señal de nombre ni benchmark. Los 2.305 no-RV son correctamente NULL. | Ninguna hasta P2 |
| `Style_Profile` | 2.209 | 68,95% | 72 RV adicionales recuperables por KIID (Growth/Value/Income estricto). Los 1.138 restantes son fondos generalistas sin estilo declarado — NULL correcto. | BL-41 (baja prioridad) |
| `Benchmark_Declared` | 1.472 | 45,94% | 1.732 fondos con benchmark extraído. Los 1.472 NULL genuinamente no tienen benchmark detectable en KIID. | Ninguna |
| `SFDR_Article` | 1.212 | 37,83% | Fondos pre-SFDR o KIIDs muy antiguos sin mención de artículo. Límite regulatorio. | Ninguna hasta fuente externa |
| `Currency_Hedged` | 1.148 | 35,83% | Los fondos sin divisa/geografía en BD o con combinación ambigua no pueden inferirse con precisión suficiente. | Ninguna |
| `Accumulation_Policy` | 594 | 18,54% | Ver BL-40 — 198 recuperables, 396 límite estructural. | BL-40 |
| `Exit_Fee_Pct` | 737 | 23,00% | 106 NOT_FOUND (con `Fee_Known_Flag=NOT_FOUND`) + 481 EXTRACTED + 150 ZERO. Los 106 NOT_FOUND son el mismo problema estructural que BL-35. | Monitorear |
| `Geography` | 306 | 9,55% | 71 fondos recuperados en v22 (Universe+Benchmark). Los 306 restantes sin señal geográfica detectable. | Ninguna |
| `Ongoing_Charge` | 270 | 8,43% | Ver BL-37b — 177 JPMorgan recuperables, ~93 límite estructural. | BL-37b |
| `Credit_Quality` | 274 | 8,55% | Mixtos (sin default asignado) y Alternativo. Potencial default `Mixed` para Mixtos. | BL-42 (análisis pendiente) |

---

## 5. CAUSA RAÍZ SISTÉMICA — COALESCE EN sqlite_writer

**Documentación para referencia arquitectónica.**

`sqlite_writer.publish_fund()` usa `COALESCE(new_value, old_value)` en la sentencia UPDATE:
- Si `fund_master_record["X"] = None` → BD preserva el valor antiguo
- Si `fund_master_record["X"] = valor_nuevo` → BD sobrescribe

**Consecuencias identificadas y resueltas:**

| Issue | Fix aplicado | Versión |
|-------|-------------|---------|
| BL-30/31: correcciones INTER con COALESCE | Leer BD previo antes de comparar | pipeline.py v22 |
| BL-38: benchmarks contaminados no limpiables | `_is_bench_contaminated()` en dict + UPDATE directo | pipeline.py v22 |
| BL-34: `Credit_Quality='No aplica'` perpetuado | Normalización explícita en pipeline | pipeline.py v20 |

**Principio documentado:** Toda corrección INTER debe operar sobre valores efectivos `(fund_master_record OR BD_previo)`, nunca solo sobre el dict del ciclo actual.

---

## 6. MÓDULOS DESPLEGADOS — VERSIONES VIGENTES

| Módulo | Versión | Cambios principales | Estado |
|--------|---------|---------------------|--------|
| `pipeline.py` | v22 | BL-38 unificado, Hedging_Policy default simultáneo, Universe+Geography desde Benchmark | **DESPLEGADO** |
| `kiid_parser.py` | v21 | BL-35 patrones JPMorgan/Schroeder/UBS/Amundi/M&G (585/591, 99%) | **DESPLEGADO** |
| `classify_utils.py` | v4 | BL-19/22/23/24/30/31/32/33 | **DESPLEGADO** |
| `fund_characterizer.py` | v18 | BL-26/27/28/29 | **DESPLEGADO** |
| `benchmark_normalizer.py` | vBL-39 | +20 aliases, +9 false positives | **DESPLEGADO** |
| `restantes.py` | v4 | BL-09/20/21 | **DESPLEGADO** |

---

## 7. PRÓXIMOS ITEMS — PRIORIZACIÓN

Ordenados por impacto/esfuerzo, todos acometibles con `metadata.ods` ya en mano:

### Alta prioridad (datos validados, fix conocido)

**BL-37b** — Ongoing_Charge NULL: JPMorgan fused (177 fondos recuperables)
- Módulo: `kiid_parser.py` v22
- Patrón: `_OC_FUSED_PATTERNS` + variante `comisionesdegestiónyotros X%delvalor`
- Impacto: OC NULL baja de 270 a ~93

**BL-35b** — Entry_Fee NOT_FOUND residual: Thread + AXA (79 fondos recuperables)
- Módulo: `kiid_parser.py` v22
- Patrones: `_EF_THREAD_DISTRIB`, `_EF_AXA_NO_FACTURA`
- Impacto: NOT_FOUND baja de 223 a ~144

**BL-40** — Accumulation_Policy NULL: Deutsche + BlackRock (198 fondos recuperables)
- Módulo: `kiid_parser.py` v22
- Patrones: Deutsche ACCUM, BlackRock `no distributivas`
- Impacto: AP NULL baja de 594 a ~396

> Los tres items anteriores son candidatos a implementarse juntos en una sola sesión como `kiid_parser.py v22`.

### Media prioridad (análisis pendiente)

**BL-42** — Credit_Quality NULL en Mixtos (estimado ~240 fondos)
- Análisis pendiente: ¿es `Mixed` el default correcto para todos los Mixtos?
- Requiere validación de distribución actual antes de implementar default

**BL-41** — Style_Profile RV (72 fondos recuperables vía KIID estricto)
- Impacto bajo. Los 1.138 restantes son límite estructural.
- Baja prioridad hasta P2

### Baja prioridad / futura

**P2 — Factores macro** — Añadir series FRED: `BAMLH0A0HYM2` (HY spread), `VIXCLS` (VIX), `T10Y2YM` (term spread). Infraestructura de descarga y almacenamiento.

**P3 scoring** — Régimen macroeconómico, pesos empíricos, reglas de rotación.

---

## 8. QUERIES DE VALIDACIÓN COMPLETAS (post-ciclo)

```sql
-- ITEMS RESUELTOS (deben devolver 0)
SELECT 'BL-19' AS bl, COUNT(*) AS n FROM fund_master WHERE Fund_Nature='Mixto'
UNION ALL SELECT 'BL-26', COUNT(*) FROM fund_master WHERE Currency_Hedged IN ('Yes','No')
UNION ALL SELECT 'BL-30', COUNT(*) FROM fund_master WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL
UNION ALL SELECT 'BL-31', COUNT(*) FROM fund_master
  WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED')
     OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED')
UNION ALL SELECT 'BL-32', COUNT(*) FROM fund_master WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL
UNION ALL SELECT 'BL-33', COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL
UNION ALL SELECT 'BL-34', COUNT(*) FROM fund_master WHERE Credit_Quality='No aplica'
UNION ALL SELECT 'BL-38', COUNT(*) FROM fund_master
  WHERE LENGTH(Benchmark_Declared) > 100 AND Benchmark_Declared != 'NO_BENCHMARK';

-- COBERTURA — seguimiento de progreso (post siguiente ciclo)
SELECT 'OC_null'            AS attr, COUNT(*) AS n FROM fund_master WHERE Ongoing_Charge IS NULL
UNION ALL SELECT 'entry_NOT_FOUND',  COUNT(*) FROM fund_master WHERE Fee_Known_Flag='NOT_FOUND'
UNION ALL SELECT 'AP_null',          COUNT(*) FROM fund_master WHERE Accumulation_Policy IS NULL
UNION ALL SELECT 'Hedging_null',     COUNT(*) FROM fund_master WHERE Hedging_Policy IS NULL
UNION ALL SELECT 'Currency_H_null',  COUNT(*) FROM fund_master WHERE Currency_Hedged IS NULL
UNION ALL SELECT 'Geography_null',   COUNT(*) FROM fund_master WHERE Geography IS NULL
UNION ALL SELECT 'Universe_null',    COUNT(*) FROM fund_master WHERE Investment_Universe IS NULL
UNION ALL SELECT 'MarketCap_RV',     COUNT(*) FROM fund_master
  WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL;

-- DISTRIBUCIÓN Fee_Known_Flag
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
GROUP BY Fee_Known_Flag ORDER BY 2 DESC;
```
