# Estado del Backlog P1 — Referencia de Sesión v2

**Fecha:** Abril 2026 (sesión completa)
**Ciclo de referencia:** p1_export_20260416.xlsx (3.204 fondos, schema v17)
**Versiones desplegadas:**

- `pipeline.py` v20 — BL-30/31/38 + Unhedged default + Geography
- `kiid_parser.py` v20 — BL-38 terminadores + OC/Fees v19 + Accumulation v20 + SFDR v20
- `fund_characterizer.py` v18 *(pendiente verificar despliegue)*
- `benchmark_normalizer.py` vBL-39 *(pendiente verificar despliegue)*
- `classify_utils.py` v4, `restantes.py` v4

---

## 1. RESUMEN EJECUTIVO DE LA SESIÓN

Tras análisis sistemático de los 3.204 KIIDs reales, **todos los items del backlog inicial tienen fix deployable o análisis concluyente**. Los cambios acumulados en v20 atacan:

| Categoría | Impacto estimado tras próximo ciclo |
|-----------|-------------------------------------|
| Consistencia INTER (BL-30/31) | 154 fondos corregidos |
| Benchmarks contaminados (BL-38) | 18 fondos limpiados |
| Ongoing_Charge NULL | 743 → 275 (ya dentro de objetivo) |
| Entry_Fee recuperado | +165 EXTRACTED + 117 ZERO_CONFIRMED |
| Exit_Fee recuperado | +355 ZERO + 154 con valor |
| Currency_Hedged Unhedged default | +1.200 fondos |
| Accumulation_Policy (ACC-v20) | +199 fondos ACCUMULATION |
| Geography (inferencia v20) | +47 fondos |
| SFDR_Article (corrección Franklin) | 30 Art.9 → Art.6 |

**Total estimado: ~2.000 fondos afectados en próximo ciclo.**

---

## 2. ITEMS RESUELTOS (controles en verde en ciclo anterior)

| BL | Descripción | Control SQL | Resultado |
|----|-------------|-------------|-----------|
| BL-19 | Sin "Mixto" singular | `COUNT(*) WHERE Fund_Nature='Mixto'` | 0 ✅ |
| BL-32 | Sin Distribution_Frequency con Accumulation_Policy=NULL | `COUNT(*) WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL` | 0 ✅ |
| BL-33 | Sin Monetario/RFC con Investment_Universe=NULL | `COUNT(*) WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL` | 0 ✅ |
| BL-34 | Sin Credit_Quality="No aplica" | `COUNT(*) WHERE Credit_Quality='No aplica'` | 0 ✅ |
| BL-26 | Currency_Hedged sin "Yes"/"No" | `COUNT(*) WHERE Currency_Hedged IN ('Yes','No')` | 0 ✅ |
| BL-37 | Ongoing_Charge NULL < 600 | `COUNT(*) WHERE Ongoing_Charge IS NULL` | 275 ✅ |
| BL-27 | Market_Cap_Focus en RV > 200 | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL` | 433 ✅ |

---

## 3. ITEMS DESPLEGADOS — PENDIENTES DE VERIFICACIÓN POST-CICLO

### 3.1 BL-30 — Broad + Sector_Focus coexistiendo (97 fondos)

**Causa raíz:** COALESCE en sqlite_writer preservaba `Sector_Focus` antiguo cuando el ciclo actual producía `None`, mientras `Investment_Focus='Broad'` sobreescribía.

**Fix en pipeline.py v20:** antes de aplicar INTER-11, el pipeline consulta BD (`SELECT Sector_Focus, Investment_Focus FROM fund_master WHERE ISIN=?`). Si `_sf_bd` poblado y `_if_bd='Broad'`, escribe `Investment_Focus='Sector'` y restaura `Sector_Focus` del BD.

**Control:**
```sql
SELECT COUNT(*) FROM fund_master
WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL;
-- Esperado: 0
```

---

### 3.2 BL-31 — Currency_Hedged='Hedged' + Hedging_Policy='UNHEDGED' (57 fondos)

**Causa raíz:** idéntica a BL-30. `Currency_Hedged='Hedged'` venía de ciclo anterior, `Hedging_Policy='UNHEDGED'` del parser actual, la corrección INTER no detectaba el conflicto.

**Fix en pipeline.py v20:** la corrección INTER lee ambos valores desde BD si `fund_master_record` los tiene a `None`. `Hedging_Policy` prevalece.

**Control:**
```sql
SELECT COUNT(*) FROM fund_master
WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED')
   OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED');
-- Esperado: 0
```

---

### 3.3 BL-38 — Benchmarks contaminados (18 fondos)

**Casos identificados en BD:**
- `"sofr), además"` × 11 fondos
- `"msci european último informe"` × 3
- `"msci europe través de todos los canales de"` × 3
- Dirección postal completa con MSCI All Country Asia ex-Japan × 1

**Fix kiid_parser.py v20:** añadidos terminadores `además`, `través`, `último`, `canal(es)`, `management`, `limited`, `bank`, `centre`, `business`, `route`, `avenida`, `calle`, `street`, `road`. Nuevo patrón `\),?\s+[a-z]{3,}` para cortar tras `)` seguido de texto.

**Fix pipeline.py v20:** limpieza defensiva de `Benchmark_Declared`. Si el parser devuelve `None` y BD contiene marcadores de contaminación, fuerza `None` explícito con log `BENCHMARK_CLEANUP`.

**Control:**
```sql
SELECT COUNT(*) FROM fund_master
WHERE LENGTH(Benchmark_Declared) > 100 AND Benchmark_Declared != 'NO_BENCHMARK'
   OR Benchmark_Declared LIKE '%además%'
   OR Benchmark_Declared LIKE '%último informe%'
   OR Benchmark_Declared LIKE '%través%'
   OR Benchmark_Declared LIKE '%management (ireland)%';
-- Esperado: 0
```

---

### 3.4 Default Currency_Hedged=Unhedged (~1.200 fondos)

**Problema:** Currency_Hedged tenía solo 8 `Unhedged` explícitos. Los fondos con divisa natural de su geografía (EUR+Europa, USD+EEUU, etc.) quedaban NULL sin razón.

**Fix pipeline.py v20:** si ambos `Currency_Hedged` y `Hedging_Policy` son NULL, combinación `(Fund_Currency, Geography)` es natural (`EUR+Europa/Global`, `USD+EEUU/Norteamérica/Global`, `GBP+Reino Unido`, `JPY+Japón`, `CHF+Suiza`), y nombre no contiene señal de hedge → asigna `Currency_Hedged='Unhedged'`.

**Control:**
```sql
SELECT Currency_Hedged, COUNT(*) FROM fund_master GROUP BY Currency_Hedged;
-- Esperado: Unhedged crece de 8 a ~1.200
```

---

### 3.5 Accumulation_Policy ACC-v20 (+199 fondos)

**Problema:** 786 fondos NULL por patrones KIID incompletos.

**Fix kiid_parser.py v20 — `_ACCUM_PATTERNS_ES` ampliado:**
- `"(clase|clases|acciones|participaciones|subfondo) de acumulación"` (97.6% precisión)
- `"(acumula|acumulan|capitaliza) (ingresos|rentas|rendimientos)"` (97.7%)
- `"(ingresos|rentas|...) ... se reinvierten"` (forma general, 89%)

**Fix kiid_parser.py v20 — `_DIST_PATTERNS_ES` ampliado** (separador `[ \t]+` sin `\n`):
- `"(distribuyen|paga|reparte) dividendos (anual|trimestral|mensual|semestral|periódic)"` (98.4%)
- `"(se distribuirán|pagarán|repartirán) (ingresos|rentas|dividendos)"` (100%)

**Fix adicional:** corrección del patrón existente `"política de distribución[^.]{0,80}distribuye"` que capturaba "no distribuye" como señal DIST. Añadido grupo `(?:(?!no\s+distribuye)[^.]){0,80}`.

**Control:**
```sql
SELECT Accumulation_Policy, COUNT(*) FROM fund_master GROUP BY Accumulation_Policy;
-- Esperado: ACCUMULATION ~2.105 (+199), DISTRIBUTION ~513 (+1), NULL ~586 (-200)
```

---

### 3.6 Geography (inferencia v20, +47 fondos)

**Problema:** 424 NULL (13.2%). La mayoría son fondos RESTANTES con nombres ambiguos.

**Fix pipeline.py v20:** nuevo bloque con 4 reglas ordenadas por precisión validada:

| Regla | Trigger | Destino | Precisión |
|-------|---------|---------|-----------|
| R1 | `Investment_Universe='Global'` + no-liquidity | Global | 100% |
| R2 nombre | `US/USA/AMERICAN` | EEUU | 98.8% |
| R2 nombre | `EUROP/EURO` | Europa | 91.7% |
| R2 nombre | `CHINA/CHN` | China | 100% |
| R2 nombre | `ASIA` | Asia | 100% |
| R3 bench | `Russell XXXX` / `MSCI USA` | EEUU | 97-100% |
| R3 bench | `MSCI China` / `CSI 300` / `Hang Seng` | China | 95.5% |
| R4 KIID | "invierte en EEUU/Norteamérica" | EEUU | 86.8% |
| R4 KIID | "invierte en Asia" | Asia | 87.5% |

**Reglas descartadas** (precisión < 85%): `MSCI World` → Global (60%), `MSCI Emerging` → Emergentes (37%), nombre `GLOBAL` → Global (72%), nombre `UK` → Reino Unido (0%).

**Control:**
```sql
SELECT Geography, COUNT(*) FROM fund_master GROUP BY Geography ORDER BY COUNT(*) DESC;
-- Esperado: Global +38, EEUU +12, Asia +4, Europa +1. NULL 424 → 377.
```

---

### 3.7 SFDR-v20 (corrección bug Franklin, 30 fondos)

**Bug detectado durante análisis:** 30 fondos Franklin con `Sfdr_Article='9'` en BD realmente son **Art.6** según el KIID. El texto dice literalmente `"Categoría según el SFDR Artículo 6 (no promueve características..."`. El parser anterior detectaba `"artículo 9"` de cualquier sección del texto y asignaba Art.9.

**Fix kiid_parser.py v20 — `_detect_sfdr_article` reestructurado:**

- **Prioridad 0** (nuevo): `"Categoría según SFDR Artículo N"` con grupo regex `(\d)` → captura `N` directamente. Validado 100% en 119 fondos reales.
- **Prioridad 1** (nuevo): `"Artículo N del SFDR"` → 100% precisión en 193 fondos.
- **Prioridad 2-4**: lógica heurística anterior preservada como fallback.

**Validación en 3.204 fondos:**
- 30 correcciones Art.9 → Art.6 (fondos Franklin)
- 2 NULL recuperados como Art.6
- 0 regresiones (16 fondos pasan a NULL en parser pero COALESCE preserva BD)

**Control:**
```sql
SELECT COUNT(*) FROM fund_master
WHERE Fund_Name LIKE 'FRANKLIN%' AND Sfdr_Article = '6';
-- Esperado: ~30 casos nuevos tras ciclo
```

**Límite teórico confirmado:** SFDR NULL = 1.219 es prácticamente inmutable. 601 fondos (49%) no mencionan SFDR en absoluto. Los 618 restantes mencionan indirectamente pero sin frase categórica extractable.

---

## 4. CAUSA RAÍZ SISTÉMICA — COALESCE EN sqlite_writer

**Documentada como principio arquitectónico para futuras sesiones.**

`sqlite_writer.publish_fund()` usa `COALESCE(new_value, old_value)`:
- `fund_master_record["X"] = None` → BD preserva el valor antiguo
- `fund_master_record["X"] = valor_nuevo` → BD sobrescribe

**Consecuencias identificadas y resueltas:**

| BL | Síntoma | Causa COALESCE |
|----|---------|----------------|
| BL-30 | `Investment_Focus='Broad'` + `Sector_Focus='Real Assets'` coexisten | Ciclo actual produce Broad (no-NULL) pero Sector_Focus=None → BD preserva antiguo |
| BL-31 | `Currency_Hedged='Hedged'` + `Hedging_Policy='UNHEDGED'` | Currency_Hedged antiguo persiste mientras parser escribe nuevo Hedging_Policy |
| BL-38 | Benchmarks contaminados persisten | Parser v20 rechaza el benchmark (None) pero BD preserva valor antiguo |
| BL-34 previo | Credit_Quality='No aplica' | Characterizer antiguo escribía valor no-NULL incorrecto |

**Principio derivado (Principio #10 candidate):**
> Toda corrección INTER que compare dos atributos persistentes debe operar sobre **valores efectivos** (`fund_master_record` OR `BD_previo`), nunca solo sobre `fund_master_record`.

Aplicación en v20: BL-30, BL-31, BL-38 y Geography leen BD preventivamente antes de aplicar reglas. Para BL-38 se fuerza `None` explícito cuando se detecta contaminación en BD para evitar que COALESCE preserve el valor antiguo.

---

## 5. GAPS ESTRUCTURALES — LÍMITES TEÓRICOS ALCANZADOS

Estos atributos tienen null ratio alto **por diseño o por límite de señal disponible**. No hay fix pendiente — se documentan como estado final y razón:

| Atributo | NULL tras v20 | Razón | Acción |
|----------|---------------|-------|--------|
| `Subtype` | ~3.020 | Solo aplica a Alternativo/RF específico. Cobertura correcta. | Ninguna |
| `Sector_Focus` | ~2.830 | Solo aplica a fondos sectoriales (~374 tienen valor). | Ninguna |
| `Market_Cap_Focus` | ~2.305 no-RV + ~500 RV sin señal | Los 2.305 no-RV son correctamente NULL. | Ninguna |
| `Currency_Hedged` | ~1.365 | Tras default Unhedged, los restantes son fondos con combinación ambigua o sin Geography. | Ninguna |
| `Hedging_Policy` | 2.309 | Solo se extrae del KIID. Requiere señal positiva explícita. | Ninguna |
| `Style_Profile` | 2.210 | Solo RV con señal explícita. Análisis confirmó límite teórico ≈ +61 (5% del gap). | Ninguna |
| `Benchmark_Declared` | 1.345 | Fondos sin benchmark detectable en KIID. | Ninguna |
| `Sfdr_Article` | 1.219 | 49% sin mención SFDR, 51% con mención indirecta. Análisis confirmó límite. | Ninguna |
| `Entry_Fee NOT_FOUND` | 870 | ~265 sin patrón de fee (límite teórico). ~100 Morgan Stanley solo importe absoluto. ~20 Allianz OCR garbled. | Pendiente BL-35 |
| `Geography` | ~377 | Fondos RESTANTES con nombres genéricos sin señal extractable. | Ninguna |

---

## 6. QUERIES DE VALIDACIÓN POST-CICLO

```sql
-- ========================================
-- ITEMS RESUELTOS (deben devolver 0)
-- ========================================
SELECT 'BL-19' AS bl, COUNT(*) AS n FROM fund_master WHERE Fund_Nature='Mixto'
UNION ALL
SELECT 'BL-32', COUNT(*) FROM fund_master WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL
UNION ALL
SELECT 'BL-33', COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL
UNION ALL
SELECT 'BL-34', COUNT(*) FROM fund_master WHERE Credit_Quality='No aplica'
UNION ALL
SELECT 'BL-26', COUNT(*) FROM fund_master WHERE Currency_Hedged IN ('Yes','No');

-- ========================================
-- ITEMS v20 — VERIFICAR TRAS PRIMER CICLO
-- ========================================
SELECT 'BL-30' AS bl, COUNT(*) AS n FROM fund_master WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL
UNION ALL
SELECT 'BL-31', COUNT(*) FROM fund_master
  WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED')
     OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED')
UNION ALL
SELECT 'BL-38_largo', COUNT(*) FROM fund_master
  WHERE LENGTH(Benchmark_Declared) > 100 AND Benchmark_Declared != 'NO_BENCHMARK'
UNION ALL
SELECT 'BL-38_esp', COUNT(*) FROM fund_master
  WHERE Benchmark_Declared LIKE '%además%' OR Benchmark_Declared LIKE '%último informe%'
     OR Benchmark_Declared LIKE '%través%' OR Benchmark_Declared LIKE '%management (ireland)%';

-- ========================================
-- COBERTURA — progreso de null ratios
-- ========================================
SELECT 'OC_null' AS attr, COUNT(*) AS n FROM fund_master WHERE Ongoing_Charge IS NULL
UNION ALL
SELECT 'entry_NOT_FOUND', COUNT(*) FROM fund_master WHERE Fee_Known_Flag='NOT_FOUND'
UNION ALL
SELECT 'exit_null', COUNT(*) FROM fund_master WHERE Exit_Fee_Pct IS NULL
UNION ALL
SELECT 'currency_Unhedged', COUNT(*) FROM fund_master WHERE Currency_Hedged='Unhedged'
UNION ALL
SELECT 'accum_accumulation', COUNT(*) FROM fund_master WHERE Accumulation_Policy='ACCUMULATION'
UNION ALL
SELECT 'geo_null', COUNT(*) FROM fund_master WHERE Geography IS NULL;

-- ========================================
-- SFDR — verificar corrección Franklin
-- ========================================
SELECT Sfdr_Article, COUNT(*) FROM fund_master GROUP BY Sfdr_Article ORDER BY Sfdr_Article;
-- Esperado: Art.9 ~212 (baja 30), Art.6 ~32 (sube de ~0)

SELECT COUNT(*) FROM fund_master
WHERE Fund_Name LIKE 'FRANKLIN%' AND Sfdr_Article = '6';
-- Esperado: ~30 casos
```

---

## 7. MÓDULOS ENTREGADOS — VERSIONES FINALES

| Módulo | Versión | Contenido | Líneas | Estado |
|--------|---------|-----------|--------|--------|
| `pipeline.py` | v20 | BL-30/31 lookup BD, BL-38 cleanup, Unhedged default, Geography v20 | 888 | **LISTO** |
| `kiid_parser.py` | v20 | BL-38 terminadores, OC v19 (DWS/Allianz), Fees v19, ACC-v20, SFDR-v20 | 2.861 | **LISTO** |
| `classify_utils.py` | v4 | BL-19/22/23/24/30/31/32/33 | — | **DESPLEGADO** |
| `restantes.py` | v4 | BL-09/20/21 | — | **DESPLEGADO** |
| `fund_characterizer.py` | v18 | BL-26/27/28/29 | — | **VERIFICAR** |
| `benchmark_normalizer.py` | vBL-39 | +20 aliases, +9 false positives | — | **VERIFICAR** |

---

## 8. PRÓXIMAS ACCIONES RECOMENDADAS

1. **Ejecutar ciclo completo** con v20 (`pipeline.py` + `kiid_parser.py`) y validar los controles SQL de sección 6.
2. **Medir diferenciales** (expected vs real) en null ratios tras el ciclo:
   - Si los diferenciales coinciden con estimaciones → fixes validados.
   - Si divergen → analizar causas (probablemente algún flujo CACHED no pasa por las correcciones INTER).
3. **Verificar despliegue** de `fund_characterizer.py` v18 y `benchmark_normalizer.py` vBL-39 en producción.
4. **Documentar Principio #10** (COALESCE-aware corrections) en `PRINCIPIOS_DISENO.md`.
5. **Mover a P2:**
   - Cálculo de sensibilidades macro con series FRED (BAMLH0A0HYM2, VIXCLS, T10Y2YM).
   - Infraestructura de descarga histórica de NAVs.
6. **BL-35 residual** (~20 fondos Allianz con OCR `400%`): patrón especial pendiente. Impacto bajo.

---

**Fin del documento.**
