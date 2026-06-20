# Estado del Backlog P1 — Referencia de Sesión
**Fecha:** 19 de abril de 2026  
**Ciclo de referencia:** p1_export_20260419.xlsx (3.204 fondos, schema v17)  
**Módulos desplegados en esta sesión:**
- `kiid_parser.py` v22 — BL-37b, BL-35b, BL-40 (alta prioridad)
- `kiid_parser.py` v23 — BL-41, BL-43a, BL-43b (media prioridad + nuevo ítem Subtype)
- `pipeline.py` v24 — BL-42, BL-43a-ext, BL-41-ext, BL-27-ext, BL-45 (defaults semánticos + inferencia INTER)

---

## 1. ITEMS RESUELTOS — ACUMULADO HISTÓRICO

| BL | Descripción | Control SQL | Resultado |
|----|-------------|-------------|-----------|
| BL-09 | SRRI fallback desde texto | — | **✅ Resuelto** (restantes.py v4) |
| BL-19 | Sin "Mixto" singular | `COUNT(*) WHERE Fund_Nature='Mixto'` | **0 ✅** |
| BL-20 | Credit_Quality language fix | — | **✅ Resuelto** (restantes.py v4) |
| BL-21 | Logging fixes restantes | — | **✅ Resuelto** (restantes.py v4) |
| BL-22 | INTER validaciones | — | **✅ Resuelto** (classify_utils.py v4) |
| BL-23 | Dictionary unification | — | **✅ Resuelto** (classify_utils.py v4) |
| BL-24 | Language normalization | — | **✅ Resuelto** (classify_utils.py v4) |
| BL-26 | Currency_Hedged sin "Yes"/"No" | `COUNT(*) WHERE Currency_Hedged IN ('Yes','No')` | **0 ✅** |
| BL-27 | Market_Cap_Focus en RV > 200 | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL` | **466 ✅** |
| BL-28 | Sin Credit_Quality="No aplica" | `COUNT(*) WHERE Credit_Quality='No aplica'` | **0 ✅** |
| BL-29 | Style_Profile KIID-layer | — | **✅ Resuelto** (fund_characterizer.py v18) |
| BL-30 | Sin Investment_Focus=Broad + Sector_Focus | `COUNT(*) WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL` | **0 ✅** |
| BL-31 | Sin contradicción CH vs HP | `COUNT(*) WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED') OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED')` | **0 ✅** |
| BL-32 | Sin Dist_Freq con AP=NULL | `COUNT(*) WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL` | **0 ✅** |
| BL-33 | Sin Monetario/RFC con Universe=NULL | `COUNT(*) WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL` | **0 ✅** |
| BL-34 | Sin Credit_Quality="No aplica" | `COUNT(*) WHERE Credit_Quality='No aplica'` | **0 ✅** |
| BL-35 | Entry_Fee NOT_FOUND (5 gestoras) | `COUNT(*) WHERE Fee_Known_Flag='NOT_FOUND'` | **585/591 resueltos (99%) ✅** |
| BL-35b | Entry_Fee NOT_FOUND Thread+AXA | `COUNT(*) WHERE Fee_Known_Flag='NOT_FOUND'` | **139 ✅** (esperado ≤144, mejor que previsto) |
| BL-37 | Ongoing_Charge NULL < 600 | `COUNT(*) WHERE Ongoing_Charge IS NULL` | **74 ✅** (objetivo era <600, mejor que previsto) |
| BL-37b | OC NULL JPMorgan fused | `COUNT(*) WHERE Ongoing_Charge IS NULL` | **74 ✅** (esperado ~93, mejor que previsto) |
| BL-38 | Sin benchmarks contaminados | `COUNT(*) WHERE LENGTH(Benchmark_Declared)>100 AND Benchmark_Declared!='NO_BENCHMARK'` | **0 ✅** |
| BL-39 | Benchmark normalizer aliases | — | **✅ Resuelto** (benchmark_normalizer.py vBL-39) |
| BL-40 | Accumulation_Policy NULL Deutsche+BlackRock | `COUNT(*) WHERE Accumulation_Policy IS NULL` | **394 ✅** (esperado ~396) |
| BL-41 | Style_Profile desde KIID (señales estrictas) | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Style_Profile IS NOT NULL` | **544 ✅** (+78 desde 466) |
| BL-41-ext | Style_Profile defaults semánticos (Blend/Not Applicable) | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Style_Profile IS NULL AND Strategy IS NOT NULL` | **0 esperado post-v24** |
| BL-42 | Credit_Quality Mixtos NULL | `COUNT(*) WHERE Fund_Nature='Mixtos' AND Credit_Quality IS NULL` | **0 ✅** |
| BL-43a | Subtype Monetario VNAV/LVNAV/CNAV | `SELECT Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Monetario' GROUP BY Subtype` | **36 tipificados ✅** |
| BL-43a-ext | Subtype Monetario Standard MMF | `COUNT(*) WHERE Fund_Nature='Monetario' AND Subtype='Standard MMF'` | **~38 esperado post-v24** |
| BL-43b | Subtype Mixtos Fixed Band + Volatility Target | `SELECT Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Mixtos' AND Subtype IS NOT NULL` | **12 fondos ✅** (3×FB15, 3×FB50, 3×FB75, 3×VT) |
| BL-27-ext | Market_Cap_Focus All Cap default | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus='All Cap'` | **~1.041 esperado post-v24** |
| BL-45 | Hedging_Policy inferida desde Currency_Hedged | `COUNT(*) WHERE Currency_Hedged='Hedged' AND Hedging_Policy IS NULL` | **0 esperado post-v24** |

---

## 2. ESTADO DE COBERTURA — 19-ABRIL-2026 (pre-v24)

Referencia: p1_export_20260419.xlsx. Los efectos de v24 (BL-41-ext, BL-27-ext, BL-43a-ext, BL-45) se reflejarán en el siguiente ciclo.

| Atributo | Filled | NULL | NULL% | Variación vs ciclo anterior | Tendencia |
|----------|--------|------|-------|-----------------------------|-----------|
| `profile` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `strategy` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `family` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `type` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `theme` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `leverage_used` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `srri` | 3.186 | 18 | 0,56% | estable | ✅ Límite estructural |
| `investment_focus` | 3.169 | 35 | 1,09% | estable | ✅ Límite estructural |
| `credit_quality` | 3.150 | 54 | 1,69% | **−220 (antes 274)** | ✅ BL-42 resuelto |
| `fund_currency` | 3.147 | 57 | 1,78% | estable | ✅ Límite estructural |
| `ongoing_charge` | 3.130 | 74 | 2,31% | **−196 (antes 270)** | ✅ BL-37b resuelto |
| `entry_fee_pct` | 3.070 | 134 | 4,18% | **−89 (antes 223 NOT_FOUND)** | ✅ BL-35b resuelto |
| `investment_universe` | 3.001 | 203 | 6,34% | estable | Límite estructural |
| `geography` | 2.898 | 306 | 9,55% | estable | Límite estructural |
| `accumulation_policy` | 2.810 | 394 | 12,30% | **−200 (antes 594)** | ✅ BL-40 resuelto |
| `exit_fee_pct` | 2.469 | 735 | 22,94% | estable | Monitorear |
| `hedging_policy` | 2.412 | 792 | 24,72% | estable pre-v24 | ⏳ BL-45 pendiente ejecución |
| `sfdr_article` | 1.994 | 1.210 | 37,77% | estable | Límite regulatorio |
| `currency_hedged` | 2.056 | 1.148 | 35,83% | estable | Límite estructural |
| `benchmark_declared` | 1.851 | 1.353 | 42,23% | estable | Límite estructural |
| `style_profile` | 1.215 | 1.989 | 62,08% | **+78 (antes 466 filled)** | ⏳ BL-41-ext pendiente ejecución |
| `market_cap_focus` | 466 | 2.738 | 85,46% | **+33 (antes 433 filled)** | ⏳ BL-27-ext pendiente ejecución |
| `sector_focus` | 374 | 2.830 | 88,33% | estable | Límite estructural |
| `subtype` | 232 | 2.972 | 92,76% | **+49 (antes 183 filled)** | ⏳ BL-43a-ext pendiente ejecución |

**Previsión post-v24 (siguiente ciclo):**

| Atributo | NULL actual | NULL esperado | Reducción |
|----------|-------------|---------------|-----------|
| `hedging_policy` | 792 | ~593 | −199 (BL-45) |
| `style_profile` | 1.989 | ~869 | −1.120 (BL-41-ext: ~1.036 Blend + ~84 Not Applicable) |
| `market_cap_focus` | 2.738 | ~1.697 | −1.041 (BL-27-ext: All Cap en RV no sectorial) |
| `subtype` | 2.972 | ~2.934 | −38 (BL-43a-ext: Standard MMF) |

---

## 3. ITEMS ABIERTOS — PRIORIZACIÓN

### Alta prioridad (datos validados, fix conocido, impacto directo)

**BL-44 — Misclasificaciones Fund_Nature por SRRI elevado**
- **Descripción:** ~26 fondos con SRRI 3-4 clasificados en Fund_Nature conservadora (Monetario o Renta Fija Corto Plazo). Fondos tipo `AMUNDI GLO PERSPECTIVES` (SRRI 4), `AXA WF ACT GREEN` (SRRI 3), `VONTOBEL COMMOD` (SRRI 4), `BGF DYNAMIC HIGH INCOME` (SRRI 3), `UBS STR FUND BALANCED` (SRRI 3) en Monetario; `EDR SICAV GLO RESILI` (SRRI 4) en RFC.
- **Causa raíz:** Bloques MONETARIOS y RFC no validan coherencia SRRI↔Nature. La regla INTER Profile↔SRRI existe pero no actúa sobre Nature directamente.
- **Impacto colateral:** Estos fondos reciben Subtype=`Standard MMF` (BL-43a-ext) o Credit_Quality=`Investment Grade` (P14) incorrectamente por herencia de Nature errónea.
- **Módulos:** Bloque MONETARIOS + bloque RFC (no disponibles en sesión actual) + validación INTER en pipeline.
- **Acción requerida:** Análisis del bloque MONETARIOS para identificar umbrales SRRI. Considerar regla INTER preventiva: `Fund_Nature='Monetario' AND SRRI >= 3` → warning + reclasificación.
- **Requiere:** Subir `blocks/monetarios.py` y `blocks/renta_fija_corto.py` en próxima sesión.

---

### Media prioridad (análisis pendiente o impacto moderado)

**BL-46 — Benchmark_Type NULL con Benchmark_Declared poblado (4 fondos)**
- **Descripción:** 4 fondos tienen Benchmark_Declared informado pero Benchmark_Type=NULL. La función `_detect_benchmark_type()` no matchea estos formatos específicos.
- **Fondos afectados:** H2O Adagio (`caceis bank` — benchmark contaminado residual), Nomura HY (nombre largo ICE BofA), Amundi Euroland (`msci emu net total return`), Amundi Volatility (`sofr) index + 3%` — paréntesis residual).
- **Fix:** Ampliar `_detect_benchmark_type()` con los patrones faltantes. Los 3 últimos deberían devolver `REFERENCE_INDEX`. El de H2O probablemente `NO_BENCHMARK` (benchmark contaminado que pasó el filtro de longitud).
- **Módulo:** `pipeline.py` — función `_detect_benchmark_type`.

**BL-47 — Fondos Is_ESG=1 sin Sfdr_Article (43 fondos)**
- **Descripción:** 43 fondos marcados como ESG (`Is_ESG=1`) con `Sfdr_Article=NULL`. Violación de coherencia INTER (regla INTER-8 del documento de principios): fondos ESG deberían tener SFDR Art. 8 o 9.
- **Causa raíz:** El detector SFDR no extrae el artículo de algunos KIIDs ESG (posiblemente por formatos de texto no cubiertos).
- **Acción:** Análisis de los 43 KIIDs para identificar patrones no cubiertos, o asignación defensiva de Art. 8 como default para fondos ESG sin artículo declarado.
- **Nota:** Alternativa — fuente externa (registros ESMA) más fiable que inferencia desde KIID.

---

### Baja prioridad / futura

**BL-48 — Revisión solapamiento Family/Subtype en Monetarios JPMorgan**
- **Descripción:** 18 fondos JPMorgan tienen `Family=LVNAV/VNAV/CNAV` (en lugar de `Family=Monetario`). Tras BL-43a, Subtype captura la tipología regulatoria de forma explícita. La decisión de mantener Family con el valor sigla se tomó conservadoramente, pero en próximas versiones debería normalizarse: `Family → Monetario`, `Subtype → LVNAV/VNAV/CNAV`.
- **Prerequisito:** Confirmar que ningún consumidor de P2/P3 depende del valor `Family='LVNAV'` como selector antes de modificar.
- **Módulo:** `fund_characterizer.py` o bloque MONETARIOS.

**P2 — Factores macro**
- Series FRED: `BAMLH0A0HYM2` (HY spread), `VIXCLS` (VIX), `T10Y2YM` (term spread).
- Infraestructura de descarga, normalización y almacenamiento en SQLite.
- Régimen macroeconómico: etiquetado de datos históricos para training dataset.

**P3 — Scoring régimen-dependiente**
- Framework de cinco fases diseñado, no implementado.
- Pesos empíricos por régimen: pendiente dataset etiquetado de P2.
- Reglas de rotación: pendiente definición de umbrales.

---

## 4. GAPS ESTRUCTURALES — LÍMITE REAL DE EXTRACCIÓN

Estos atributos tienen NULL alto por límite de señal disponible. No son bugs. No requieren acción en P1.

| Atributo | NULL actual | NULL% | Naturaleza | Acción |
|----------|-------------|-------|------------|--------|
| `Subtype` | 2.972 | 92,8% | La gran mayoría de natures×types no tienen variante estructural diferenciable. 232 fondos con valor es cobertura correcta post-BL-43. Los ~2.934 restantes tras v24 serán genuinamente sin subtipo aplicable. | Ninguna |
| `Sector_Focus` | 2.830 | 88,3% | Solo fondos sectoriales (~374). Cobertura correcta. | Ninguna |
| `Market_Cap_Focus` | 2.738 | 85,5% | Baja a ~1.697 con BL-27-ext (All Cap). Los 2.272 non-RV son correctamente NULL. | BL-27-ext (en v24) |
| `Style_Profile` | 1.989 | 62,1% | Baja a ~869 con BL-41-ext. Los ~405 RV no activos y los non-RV sin estilo son correctamente NULL o Blend/Not Applicable. | BL-41-ext (en v24) |
| `Benchmark_Declared` | 1.353 | 42,2% | Fondos sin benchmark detectable en KIID. Límite estructural. | Ninguna |
| `Sfdr_Article` | 1.210 | 37,8% | 386 fondos pre-PRIIPs genuinos + 824 post-PRIIPs sin declaración explícita de artículo. Ver BL-47 para los 43 ESG. | BL-47 (análisis) |
| `Currency_Hedged` | 1.148 | 35,8% | Fondos sin divisa/geografía combinación detectable. Límite estructural. | Ninguna |
| `Hedging_Policy` | 792 | 24,7% | Baja a ~593 con BL-45. Residual: combinaciones divisa/geografía no naturales sin señal explícita. | BL-45 (en v24) |
| `Exit_Fee_Pct` | 735 | 22,9% | Mismo límite estructural que Entry_Fee. | Monitorear |
| `Accumulation_Policy` | 394 | 12,3% | 198 resueltos en BL-40. Los 394 restantes: KIIDs pre-2015 o texto OCR degradado sin señal de política. | Ninguna |
| `Geography` | 306 | 9,6% | Fondos sin señal geográfica detectable en KIID ni benchmark. | Ninguna |
| `Investment_Universe` | 203 | 6,3% | Fondos con universe ambiguo tras todos los fallbacks. | Ninguna |
| `Credit_Quality` | 54 | 1,7% | Alternativo (46) y Estructurado (8). Requiere análisis por subtipo de Alternativo antes de asignar default. | Análisis futuro |

---

## 5. CAUSA RAÍZ SISTÉMICA — COALESCE EN sqlite_writer

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
| `pipeline.py` | **v24** | BL-42, BL-43a-ext (Standard MMF), BL-41-ext (Blend/NA), BL-27-ext (All Cap), BL-45 (HP desde CH) | **DESPLEGADO** |
| `kiid_parser.py` | **v23** | BL-37b (OC fused JPM), BL-35b (Thread+AXA), BL-40 (DWS+BlackRock), BL-41 (Style_Profile), BL-43a/b (Subtype Mon+Mix) | **DESPLEGADO** |
| `classify_utils.py` | v4 | BL-19/22/23/24/30/31/32/33 | **DESPLEGADO** |
| `fund_characterizer.py` | v18 | BL-26/27/28/29 | **DESPLEGADO** |
| `benchmark_normalizer.py` | vBL-39 | +20 aliases, +9 false positives | **DESPLEGADO** |
| `restantes.py` | v4 | BL-09/20/21 | **DESPLEGADO** |

---

## 7. PRINCIPIOS DE DISEÑO CONSOLIDADOS (nuevos de esta sesión)

### Principio de valores semánticos explícitos sobre NULL

**Enunciado:** Cuando NULL puede significar cosas semánticamente distintas en un mismo atributo, se asigna un valor explícito que elimina la ambigüedad. NULL se reserva para "genuinamente desconocido o no aplicable sin clasificación posible".

**Catálogo de valores semánticos introducidos:**

| Atributo | Valor | Significado | Alternativa descartada |
|----------|-------|-------------|----------------------|
| `Subtype` | `Standard MMF` | Fondo monetario UCITS no sujeto al Reglamento MMF 2017/1131 | NULL ambiguo entre pre-regulación y post-regulación sin señal |
| `Style_Profile` | `Blend` | RV activa sin sesgo de estilo declarado (agnóstico Growth/Value) | NULL interpretable como "no detectado" |
| `Style_Profile` | `Not Applicable` | RV indexada/pasiva donde el estilo de gestión no existe | NULL confundible con falta de detección |
| `Market_Cap_Focus` | `All Cap` | RV sin restricción de capitalización (no sectorial) | NULL interpretable como "no detectado" |

**Implementación:** Todos en pipeline P14 (bloque de defaults por Nature), donde están consolidados todos los atributos. La condición `not fund_master_record.get("X")` garantiza que no se sobreescriben valores asignados por bloques especializados o el parser.

---

## 8. QUERIES DE VALIDACIÓN COMPLETAS (post-ciclo v24)

```sql
-- ── ITEMS RESUELTOS (deben devolver 0) ──────────────────────────────────
SELECT 'BL-19' AS bl, COUNT(*) AS n FROM fund_master WHERE Fund_Nature='Mixto'
UNION ALL SELECT 'BL-26', COUNT(*) FROM fund_master WHERE Currency_Hedged IN ('Yes','No')
UNION ALL SELECT 'BL-30', COUNT(*) FROM fund_master WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL
UNION ALL SELECT 'BL-31', COUNT(*) FROM fund_master
  WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED')
     OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED')
UNION ALL SELECT 'BL-32', COUNT(*) FROM fund_master WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL
UNION ALL SELECT 'BL-33', COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL
UNION ALL SELECT 'BL-34', COUNT(*) FROM fund_master WHERE Credit_Quality='No aplica'
UNION ALL SELECT 'BL-38', COUNT(*) FROM fund_master WHERE LENGTH(Benchmark_Declared) > 100 AND Benchmark_Declared != 'NO_BENCHMARK'
UNION ALL SELECT 'BL-42', COUNT(*) FROM fund_master WHERE Fund_Nature='Mixtos' AND Credit_Quality IS NULL
UNION ALL SELECT 'BL-45', COUNT(*) FROM fund_master WHERE Currency_Hedged='Hedged' AND Hedging_Policy IS NULL;
-- Todos deben devolver 0

-- ── COBERTURA — seguimiento de progreso ────────────────────────────────
SELECT 'OC_null'             AS attr, COUNT(*) AS n FROM fund_master WHERE Ongoing_Charge IS NULL
UNION ALL SELECT 'entry_NOT_FOUND',   COUNT(*) FROM fund_master WHERE Fee_Known_Flag='NOT_FOUND'
UNION ALL SELECT 'AP_null',           COUNT(*) FROM fund_master WHERE Accumulation_Policy IS NULL
UNION ALL SELECT 'HP_null',           COUNT(*) FROM fund_master WHERE Hedging_Policy IS NULL
UNION ALL SELECT 'CH_null',           COUNT(*) FROM fund_master WHERE Currency_Hedged IS NULL
UNION ALL SELECT 'Geography_null',    COUNT(*) FROM fund_master WHERE Geography IS NULL
UNION ALL SELECT 'Universe_null',     COUNT(*) FROM fund_master WHERE Investment_Universe IS NULL
UNION ALL SELECT 'CreditQ_null',      COUNT(*) FROM fund_master WHERE Credit_Quality IS NULL
UNION ALL SELECT 'Style_null',        COUNT(*) FROM fund_master WHERE Style_Profile IS NULL
UNION ALL SELECT 'MCF_null_RV',       COUNT(*) FROM fund_master WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NULL;

-- ── DISTRIBUCIÓN Fee_Known_Flag ─────────────────────────────────────────
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
GROUP BY Fee_Known_Flag ORDER BY 2 DESC;
-- Esperado: NOT_FOUND ≈ 139, ZERO_CONFIRMED ~1.669, EXTRACTED ~1.396

-- ── VALIDACIÓN VALORES SEMÁNTICOS v24 ──────────────────────────────────
-- Standard MMF en Monetario
SELECT Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Monetario' GROUP BY Subtype ORDER BY 2 DESC;
-- Esperado: Standard MMF ~38, LVNAV 12, VNAV 19, CNAV 5, NULL ~24 (BL-44 pendiente)

-- Style_Profile en RV
SELECT Style_Profile, COUNT(*) FROM fund_master WHERE Fund_Nature='Renta Variable' GROUP BY Style_Profile ORDER BY 2 DESC;
-- Esperado: Blend ~1.036, Not Applicable ~84, Growth/Value/Income sin cambio, NULL solo RV sin Strategy

-- Market_Cap_Focus en RV
SELECT Market_Cap_Focus, COUNT(*) FROM fund_master WHERE Fund_Nature='Renta Variable' GROUP BY Market_Cap_Focus ORDER BY 2 DESC;
-- Esperado: All Cap ~1.041, Large Cap/Mid Cap/Small Cap sin cambio, NULL solo RV sectoriales

-- Subtype Mixtos
SELECT Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Mixtos' AND Subtype IS NOT NULL GROUP BY Subtype ORDER BY 2 DESC;
-- Esperado: Fixed Band 15 (3), Fixed Band 50 (3), Fixed Band 75 (3), Volatility Target (3)

-- Verificación solapamiento Family/Subtype Monetario (seguimiento BL-48)
SELECT Family, Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Monetario' GROUP BY Family, Subtype ORDER BY 1,2;

-- ── BL-44: Misclasificaciones Fund_Nature ──────────────────────────────
SELECT Fund_Nature, SRRI, COUNT(*) FROM fund_master
WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND CAST(SRRI AS INTEGER) >= 3
GROUP BY Fund_Nature, SRRI ORDER BY 1, 2;
-- Esperado post-BL-44: 0 en ambas combinaciones

-- ── BL-47: ESG sin SFDR Article ────────────────────────────────────────
SELECT COUNT(*) FROM fund_master WHERE Is_ESG=1 AND Sfdr_Article IS NULL;
-- Actual: 43. Objetivo post-BL-47: 0
```

---

## 9. REGISTRO DE DECISIONES DE DISEÑO

| Decisión | Alternativa considerada | Razón de elección |
|----------|------------------------|-------------------|
| `Standard MMF` como Subtype para monetarios pre/fuera-MMF 2017/1131 | NULL o valor en Data_Quality_Flag | Semánticamente preciso, útil en P3, no contamina DQF global del fondo |
| `Blend` para RV activa sin estilo declarado | NULL o "Unknown" | Convención estándar del sector (Morningstar, MSCI); distingue "agnóstico" de "no detectado" |
| `Not Applicable` para RV indexada/pasiva | NULL o no asignar | Coherencia con el mismo valor usado en Credit_Quality; evita ambigüedad con "no detectado" |
| `All Cap` para RV no sectorial sin restricción de cap | NULL o "Multi Cap" | Convención estándar; distingue "sin restricción" de "sin señal" |
| BL-45: inferir HP desde CH en lugar de pipeline nuevo | Nuevo detector en parser | Más eficiente; CH ya está validado y coherente tras BL-31 |
| Family Monetario se mantiene con valores LVNAV/VNAV/CNAV | Normalizar a Family=Monetario | Decisión conservadora hasta confirmar independencia de consumidores P2/P3 (BL-48) |
