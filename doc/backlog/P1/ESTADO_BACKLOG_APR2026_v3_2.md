# Estado del Backlog P1 — Referencia de Sesión v3.2
**Fecha:** 24 de abril de 2026  
**Ciclo de referencia:** p1_export_20260423.xlsx (3.204 fondos, schema v17) — log `log_pipeline_20260423_224932.log`  
**Módulos desplegados (acumulado):**
- `pipeline.py` v25 — BL-50 catálogos ampliados, BL-52 corrección Country↔Región
- `kiid_parser.py` v24 — BL-51A (10 nuevos patrones entry/exit fee, separador decimal opcional)
- `classify_utils.py` v4, `fund_characterizer.py` v18, `benchmark_normalizer.py` vBL-39, `restantes.py` v4

**Novedades v3.2 respecto a v3.1:**
- BL-52 cerrado: 0 fondos con `Investment_Universe='Country' ∧ Geography ∈ regiones` ✅
- BL-49 reabierto: 1.148 NULL persistente; los patrones del Currency_Hedged sobre KIID no se han incorporado al detector
- BL-50 reabierto: 7 residuales por dirección inversa Universe→Geography no implementada
- BL-51 Problema A reabierto: 676 fondos con `Exit_Fee_Pct=NULL` y `Fee_Known_Flag≠NOT_FOUND` (cero implícito no formalizado) + 110 fondos con `Entry_Fee_Pct=NULL ∧ Fee_Known_Flag=NOT_FOUND`
- BL-53 reabierto: detectada **causa raíz arquitectónica** (dos puntos de emisión paralelos para Sector_Focus, sólo uno pasa por `normalize_sector_focus`)
- BL-54 NUEVO: centralización del mapeo Theme→Sector_Focus (root cause sistémico de BL-53 en Sector_Focus)
- BL-55 NUEVO: registro explícito de `Exit_Fee_Pct=0.00` para declaraciones implícitas de cero (mejora directa solicitada)
- BL-56 NUEVO: invocación de `enrich_classification()` / `apply_language_homogeneity()` post-`characterize_fund` en pipeline
- BL-57 NUEVO: decisión formal sobre `Family='Income Oriented'` (104 fondos) — mantener como excepción inglés o traducir

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
| BL-27 | Market_Cap_Focus en RV > 200 | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL` | **1.820 ✅** |
| BL-27-ext | Market_Cap_Focus All Cap default | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus='All Cap'` | **✅ Resuelto** |
| BL-28 | Sin Credit_Quality="No aplica" | `COUNT(*) WHERE Credit_Quality='No aplica'` | **0 ✅** |
| BL-29 | Style_Profile KIID-layer | — | **✅ Resuelto** (fund_characterizer.py v18) |
| BL-30 | Sin Investment_Focus=Broad + Sector_Focus | `COUNT(*) WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL` | **0 ✅** |
| BL-31 | Sin contradicción CH vs HP | `COUNT(*) WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED') OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED')` | **0 ✅** |
| BL-32 | Sin Dist_Freq con AP=NULL | `COUNT(*) WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL` | **0 ✅** |
| BL-33 | Sin Monetario/RFC con Universe=NULL | `COUNT(*) WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL` | **0 ✅** |
| BL-34 | Sin Credit_Quality="No aplica" | `COUNT(*) WHERE Credit_Quality='No aplica'` | **0 ✅** |
| BL-35 | Entry_Fee NOT_FOUND (5 gestoras) | `COUNT(*) WHERE Fee_Known_Flag='NOT_FOUND'` | **585/591 resueltos (99%) ✅** |
| BL-35b | Entry_Fee NOT_FOUND Thread+AXA | `COUNT(*) WHERE Fee_Known_Flag='NOT_FOUND'` | **139 ✅** |
| BL-37 | Ongoing_Charge NULL < 600 | `COUNT(*) WHERE Ongoing_Charge IS NULL` | **74 ✅** |
| BL-37b | OC NULL JPMorgan fused | `COUNT(*) WHERE Ongoing_Charge IS NULL` | **74 ✅** |
| BL-38 | Sin benchmarks contaminados | `COUNT(*) WHERE LENGTH(Benchmark_Declared)>100 AND Benchmark_Declared!='NO_BENCHMARK'` | **0 ✅** |
| BL-39 | Benchmark normalizer aliases | — | **✅ Resuelto** (benchmark_normalizer.py vBL-39) |
| BL-40 | Accumulation_Policy NULL Deutsche+BlackRock | `COUNT(*) WHERE Accumulation_Policy IS NULL` | **394 ✅** |
| BL-41 | Style_Profile desde KIID (señales estrictas) | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Style_Profile IS NOT NULL` | **2.334 ✅** |
| BL-41-ext | Style_Profile defaults semánticos (Blend/Not Applicable) | — | **✅ Resuelto** |
| BL-42 | Credit_Quality Mixtos NULL | `COUNT(*) WHERE Fund_Nature='Mixtos' AND Credit_Quality IS NULL` | **0 ✅** |
| BL-43a | Subtype Monetario VNAV/LVNAV/CNAV | `SELECT Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Monetario' GROUP BY Subtype` | **✅ Resuelto** |
| BL-43a-ext | Subtype Monetario Standard MMF | `COUNT(*) WHERE Fund_Nature='Monetario' AND Subtype='Standard MMF'` | **38 ✅** |
| BL-43b | Subtype Mixtos Fixed Band + Volatility Target | `SELECT Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Mixtos' AND Subtype IS NOT NULL` | **12 ✅** |
| BL-44 | Misclasificaciones Fund_Nature SRRI elevado | `COUNT(*) WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND CAST(SRRI AS INTEGER) >= 3` | **0 ✅** |
| BL-45 | Hedging_Policy inferida desde Currency_Hedged | `COUNT(*) WHERE Currency_Hedged='Hedged' AND Hedging_Policy IS NULL` | **0 ✅** |
| BL-46 | Benchmark_Type NULL con Benchmark_Declared poblado | `COUNT(*) WHERE Benchmark_Declared IS NOT NULL AND Benchmark_Declared != 'NO_BENCHMARK' AND Benchmark_Type IS NULL` | **0 ✅** |
| BL-47 | Is_ESG=1 sin Sfdr_Article | `COUNT(*) WHERE Is_ESG=1 AND Sfdr_Article IS NULL` | **0 ✅** |
| BL-48 | Subtype Monetario distribución correcta post-normalización | — | **✅ Distribución correcta** |
| **BL-52** | **Inconsistencia Universe='Country' con región en Geography** | `COUNT(*) WHERE Investment_Universe='Country' AND Geography IN (regiones)` | **0 ✅ (cerrado v3.2)** |

---

## 2. ESTADO DE COBERTURA — 23-ABRIL-2026 (post-ciclo v25)

| Atributo | Filled | NULL | NULL% | Variación vs v3.1 | Tendencia |
|----------|--------|------|-------|-------------------|-----------|
| `profile` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `strategy` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `family` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `type` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `theme` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `leverage_used` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `srri` | 3.186 | 18 | 0,56% | estable | ✅ Límite estructural |
| `investment_focus` | 3.169 | 35 | 1,09% | estable | ✅ Límite estructural |
| `fund_currency` | 3.147 | 57 | 1,78% | estable | ✅ Límite estructural |
| `ongoing_charge` | 3.130 | 74 | 2,31% | estable | ✅ Límite estructural |
| `entry_fee_pct` | 3.075 | 129 | 4,03% | **−5 (mejora marginal)** | ⚠️ BL-51A residual (110 NOT_FOUND) |
| `investment_universe` | 3.001 | 203 | 6,34% | estable | ⚠️ BL-50 reabierto (7 residuales) |
| `geography` | 2.898 | 306 | 9,55% | estable | ⚠️ BL-50 reabierto |
| `accumulation_policy` | 2.810 | 394 | 12,30% | estable | ✅ Límite estructural |
| `hedging_policy` | 2.611 | 593 | 18,51% | **−199 (BL-45 ejecutado)** | ✅ Estable post-BL-45 |
| `exit_fee_pct` | 2.528 | 676 | 21,10% | **−59 (BL-51A v24 ejecutado)** | ⚠️ **BL-51A residual + BL-55 nuevo** |
| `style_profile` | 2.334 | 870 | 27,15% | **−1.119 (BL-41-ext ejecutado)** | ✅ Estable post-extensiones |
| `currency_hedged` | 2.056 | 1.148 | 35,83% | estable | ⚠️ **BL-49 sigue sin acción real** |
| `sfdr_article` | 2.048 | 1.156 | 36,08% | estable | Límite regulatorio |
| `market_cap_focus` | 1.820 | 1.384 | 43,20% | **−1.354 (BL-27-ext ejecutado)** | ✅ Estable |
| `benchmark_declared` | 1.732 | 1.472 | 45,94% | estable | Límite estructural |
| `sector_focus` | 374 | 2.830 | 88,33% | estable | ⚠️ **BL-53/BL-54: 20 inconsistencias EN/ES** |
| `subtype` | 270 | 2.934 | 91,57% | **+38 (BL-43a-ext ejecutado)** | ⚠️ BL-53 residual lingüístico |

**Lectura del ciclo v25:**
- BL-41-ext, BL-27-ext, BL-43a-ext, BL-45 entregaron las reducciones previstas en v3.1 (totales: −2.731 NULL absorbidos).
- BL-51A v24 entregó solo −59 sobre exit_fee (esperado: −25 a −55 → dentro de rango pero techo bajo) y −5 sobre entry_fee. La señal indica que la mayor bolsa de NULL en exit_fee NO es por patrones inexistentes sino por **ausencia de declaración explícita** que debe asumirse 0.
- Ningún BL del bloque {BL-49, BL-50 dirección inversa, BL-51 Problema B, BL-53} ha sido tocado en código. La variación es 0 en sus contadores principales.

---

## 3. ITEMS ABIERTOS — PRIORIZACIÓN

### Alta prioridad

---

**BL-49 — Currency_Hedged: detección directa en texto KIID (REABIERTO)**

- **Estado:** Implementación parcial. La firma `detect_currency_hedged(name_l, kiid_text)` ya admite `kiid_text` desde v18 (`fund_characterizer.py` línea 514), pero **el cuerpo de la función no contiene patrones de extracción sobre `kiid_text`**. La inferencia indirecta `Hedging_Policy → Currency_Hedged` (pipeline v25 líneas 1021–1044) ha aportado parte de la cobertura, pero los 1.148 NULL persisten.

- **Diagnóstico cuantitativo:**
  ```sql
  -- Distribución de NULL Currency_Hedged por divisa del fondo
  SELECT Fund_Currency, COUNT(*) FROM fund_master
  WHERE Currency_Hedged IS NULL
  GROUP BY Fund_Currency ORDER BY 2 DESC;
  -- Esperado: bolsa principal en EUR (cobertura no aplica conceptualmente),
  -- bolsa accionable en USD/GBP/CHF/JPY/CNH (~267 fondos).
  ```

- **Causa raíz:** No se han añadido patrones explícitos sobre `Raw_KIID_Text` en `detect_currency_hedged()`. Solo el nombre del fondo se examina en el cuerpo de la función; la señal del KIID no se lee.

- **Acción preventiva (especificación para codificación):**

  1. En `fund_characterizer.py`, ampliar `detect_currency_hedged()` con una segunda fase de extracción cuando la fase basada en nombre devuelva `None`:
     ```python
     def detect_currency_hedged(name_l, kiid_text=None):
         # Fase 1 (existente): nombre del fondo
         result_from_name = _detect_ch_from_name(name_l)
         if result_from_name:
             return result_from_name
         # Fase 2 (NUEVA): texto KIID
         if kiid_text:
             return _detect_ch_from_kiid_text(kiid_text)
         return None
     ```
  2. Definir `_detect_ch_from_kiid_text(text)` con catálogo de patrones (orden de prioridad):
     - **HEDGED (alta confianza, EN):** `\bcurrency[- ]hedged\s+share\s+class\b`, `\bhedged\s+share\s+class\b`, `\bcurrency\s+risk\s+is\s+hedged\b`, `\bfully\s+hedged\b`, `\bhedge[d]?\s+against\s+(?:eur|usd|gbp|chf|jpy)\b`.
     - **HEDGED (alta confianza, ES):** `\bclase\s+(?:de\s+)?(?:acciones|participaciones)\s+(?:con\s+)?cobertura\s+(?:de\s+divisa|cambiaria)\b`, `\bcobertura\s+(?:total|íntegra)\s+del?\s+(?:riesgo|tipo)\s+de\s+cambio\b`, `\briesgo\s+de\s+(?:cambio|divisa)\s+est[áa]\s+cubierto\b`.
     - **UNHEDGED (alta confianza, EN):** `\b(?:unhedged|not\s+hedged|without\s+(?:currency\s+)?hedging)\s+share\s+class\b`, `\bno\s+currency\s+hedging\b`, `\bcurrency\s+risk\s+is\s+not\s+hedged\b`.
     - **UNHEDGED (alta confianza, ES):** `\bsin\s+cobertura\s+(?:de\s+divisa|cambiaria|del?\s+riesgo\s+de\s+cambio)\b`, `\bno\s+(?:se\s+)?cubre\s+el\s+(?:riesgo\s+de\s+)?(?:cambio|divisa)\b`, `\bno\s+aplica\s+cobertura\s+de\s+divisa\b`.
  3. **Restricción de aplicación:** la fase 2 solo se aplica si `Fund_Currency` es identificable y NO es la divisa local del inversor objetivo, o si los patrones se encuentran junto a una declaración explícita de "share class". Para fondos en EUR sin señal alguna, mantener `NULL` (no aplica conceptualmente).
  4. **Tests unitarios obligatorios:** mínimo 12 (3 por idioma × 2 estados HEDGED/UNHEDGED), con muestras reales del KIID. Validar que la fase 1 (nombre) sigue ganando cuando aporta señal.
  5. **Logging:** cada extracción desde KIID debe emitir log `[ISIN] CH-FROM-KIID: {valor} via patrón {patrón_id}` para trazabilidad.
  6. **Coordinación con BL-31:** la regla de prevalencia "Hedging_Policy gana sobre Currency_Hedged" en classify_utils.py línea 2502 debe seguir aplicándose al final del pipeline, después de la extracción ampliada.

- **Módulo:** `fund_characterizer.py` — función `detect_currency_hedged()` y nueva función auxiliar `_detect_ch_from_kiid_text()`.

- **Control SQL post-fix:**
  ```sql
  SELECT COUNT(*) FROM fund_master
  WHERE Fund_Currency IN ('USD','GBP','CHF','JPY','CNH')
    AND Currency_Hedged IS NULL;
  -- Objetivo: reducción significativa desde 267 (al menos −50%)

  SELECT COUNT(*) FROM fund_master WHERE Currency_Hedged IS NULL;
  -- Objetivo: bajada desde 1.148 al rango 850-950
  ```

---

**BL-50 — Inferencia INTER Investment_Universe / Geography (REABIERTO, 7 residuales)**

- **Estado:** Implementación direccional Geography→Universe completa en `pipeline.py` líneas 640–658 (catálogos ampliados). La dirección inversa Universe→Geography no está implementada y los 7 residuales corresponden a fondos con `Universe IN ('Country','Regional','Global')` y `Geography=NULL`.

- **Diagnóstico cuantitativo:**
  ```sql
  SELECT Investment_Universe, COUNT(*) FROM fund_master
  WHERE Investment_Universe IS NOT NULL AND Geography IS NULL
  GROUP BY Investment_Universe;
  -- Resultado actual: 7 fondos residuales
  ```

- **Causa raíz:** La inferencia Universe→Geography solo es unívoca para `Universe='Global'` (→ Geography='Global'). Para `Universe='Country'` o `'Regional'` con Geography NULL, no hay valor canónico inferible sin recurrir al nombre del fondo, KIID o Benchmark_Declared.

- **Acción preventiva (especificación para codificación):**

  1. En `pipeline.py`, extender el bloque INTER (líneas 640–658) con la dirección inversa para el caso unívoco:
     ```python
     # Geography por defecto cuando Universe='Global' y Geography NULL
     if (fund_master_record.get("Investment_Universe") == "Global"
             and not fund_master_record.get("Geography")):
         fund_master_record["Geography"] = "Global"

     # Geography por defecto cuando Universe='Liquidity'
     # (Monetario / Renta Fija Corto Plazo sin geografía explícita)
     if (fund_master_record.get("Investment_Universe") == "Liquidity"
             and not fund_master_record.get("Geography")):
         _nat = fund_master_record.get("Fund_Nature")
         _curr = fund_master_record.get("Fund_Currency")
         if _curr == "EUR":
             fund_master_record["Geography"] = "Europa"
         elif _curr == "USD":
             fund_master_record["Geography"] = "EEUU"
         # Resto: dejar NULL (sin señal canónica)
     ```
  2. Para los casos `Universe='Country'/'Regional'` con `Geography=NULL` (los más complejos), **no inferir por defecto**. Documentar que estos casos requieren intervención del clasificador que asigna `Universe`: si éste asigna `Country`, debe asignar simultáneamente `Geography` con el país concreto, o no asignar `Universe`.

- **Acción complementaria (tracking de coherencia):** auditar dónde se asigna `Investment_Universe='Country'` sin asignar `Geography` y corregir el origen del defecto, no aplicar parche posterior.
  ```sql
  SELECT b.* FROM fund_master b
  WHERE b.Investment_Universe IN ('Country','Regional')
    AND b.Geography IS NULL;
  -- Inspeccionar manualmente los ISIN para identificar el clasificador de origen
  ```

- **Módulo:** `pipeline.py` (defaults INTER); secundariamente bloques especializados que asignen `Universe` sin `Geography`.

- **Control SQL post-fix:**
  ```sql
  SELECT Investment_Universe, COUNT(*) FROM fund_master
  WHERE Investment_Universe IS NOT NULL AND Geography IS NULL
  GROUP BY Investment_Universe;
  -- Objetivo: 0 para Universe='Global'/'Liquidity'; los Country/Regional residuales
  -- requieren auditoría caso a caso.
  ```

---

**BL-51 — Comisiones: estructura mixta y declaración explícita de cero (REABIERTO Problema A)**

- **Estado:**
  - Problema A (patrones nuevos en v24): ✅ desplegado. Reducción real `Exit_Fee_Pct` NULL: 735 → 676 (−59).
  - Problema A residual: 110 fondos siguen con `Entry_Fee_Pct=NULL ∧ Fee_Known_Flag='NOT_FOUND'` y 676 con `Exit_Fee_Pct=NULL`.
  - Problema B (schema cap/floor): 🔍 análisis cerrado en `BL51_SCHEMA_DECISION.md`, **prevalencia diagnóstica pendiente**.

- **Diagnóstico cuantitativo:**
  ```sql
  -- Entry fee residual NOT_FOUND
  SELECT COUNT(*) FROM fund_master
  WHERE Entry_Fee_Pct IS NULL AND Fee_Known_Flag='NOT_FOUND';
  -- Resultado: 110

  -- Exit fee residual sin clasificar como NOT_FOUND
  SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
  WHERE Exit_Fee_Pct IS NULL
  GROUP BY Fee_Known_Flag ORDER BY 2 DESC;
  -- 676 distribuidos entre EXTRACTED (parser detectó algo y no extrajo) y otros
  ```

- **Causa raíz Problema A residual entry_fee:**
  Los 110 fondos `NOT_FOUND` tienen formulaciones aún no cubiertas. Antes de añadir patrones, **es obligatorio una sesión de muestreo manual** sobre `Raw_KIID_Text` de 20 ISIN aleatorios del subconjunto, y agruparlos por gestora.

- **Causa raíz Problema A residual exit_fee:**
  Los 676 fondos NO son por patrones inexistentes — son **declaraciones implícitas de cero** que el parser no formaliza como `0.00`. Ver BL-55 para la solución preventiva específica.

- **Acción preventiva entry_fee (especificación):**
  1. Ejecutar muestreo SQL:
     ```sql
     SELECT ISIN, Manager_Name, Raw_KIID_Text
     FROM fund_master JOIN raw_kiids USING(ISIN)
     WHERE Entry_Fee_Pct IS NULL AND Fee_Known_Flag='NOT_FOUND'
     ORDER BY RANDOM() LIMIT 20;
     ```
  2. Clasificar las formulaciones por gestora y patrón lingüístico.
  3. Añadir patrones a `_detect_entry_fee()` en `kiid_parser.py` siguiendo la convención v24 (compilados con `re.IGNORECASE`, separador decimal opcional).
  4. Verificar tests existentes (26/26) + añadir mínimo 2 tests por nuevo patrón.

- **Problema B (cap/floor):** mantener pendiente de ratificación de schema según `BL51_SCHEMA_DECISION.md`. Ejecutar primero la query de prevalencia (sección 6 del documento) antes de modificar `schema_fondos.sql`.

- **Módulo:** `kiid_parser.py` v25 (nuevos patrones); `BL51_SCHEMA_DECISION.md` (prevalencia para Problema B).

- **Control SQL post-fix:**
  ```sql
  SELECT COUNT(*) FROM fund_master
  WHERE Entry_Fee_Pct IS NULL AND Fee_Known_Flag='NOT_FOUND';
  -- Objetivo: reducción desde 110 a ≤50
  ```

---

**BL-53 — Inconsistencias lingüísticas intra-atributo (REABIERTO con causa raíz refinada)**

- **Estado:** La auditoría sobre el export del 23/04/2026 confirma 4 atributos con inconsistencias:

  | Atributo | Idioma objetivo | Inconsistencia detectada |
  |----------|-----------------|--------------------------|
  | `Family` | Español | `'Income Oriented'` (104 fondos) — único valor en inglés. Decisión pendiente. |
  | `Sector_Focus` | Español (decisión clase) | 20 fondos con valores en inglés (`'Real Assets'` 11, `'Energy & Resources'` 6, `'Utilities & Environment'` 2, `'Healthcare & Life Sciences'` 1) — coexisten con sus equivalentes españoles. **Defecto técnico**, no de diseño. |
  | `Subtype` | Mixto por diseño | 11 valores en inglés (`Standard MMF`, `VNAV/LVNAV/CNAV`, `ETF`, `Floating Rate Notes`, `Long/Short`, `Global Macro`, `Total Return Bond`). Mayoría son acrónimos/regulación → decisión: **mantener excepción y documentar**. |
  | `Type` | Español con excepciones | `'Allocation'`, `'Absolute Return'`, `'Total Return'`, `'Tactical Allocation'`, `'Target Maturity'`, `'Floating Rate CP'`, `'Materias Primas'` (este último ya en español tras fix). Excepciones documentadas en `TYPE_TRANSLATION_MAP` deben formalizarse. |

- **Causa raíz arquitectónica (Sector_Focus):** Existen **dos puntos de emisión paralelos**:
  - `fund_characterizer.detect_sector_focus()` línea 341 emite `'Real Assets'` directamente (en inglés) mientras que el resto de valores están en español. El resto del catálogo se emite en español.
  - `pipeline.py` líneas 758–776 mapea `Theme→Sector_Focus` con valores hardcoded en español.
  - `classify_utils.normalize_sector_focus()` aplica el mapa de traducción **solo cuando se invoca `enrich_classification()`** (línea 1620), que **no se invoca en el pipeline post-`characterize_fund`**. Verificable en `pipeline.py` líneas 395–418 (no hay llamada a enrich).

  Por eso los 20 fondos con valor en inglés son aquellos cuya emisión proviene de `fund_characterizer.detect_sector_focus` y no son corregidos por nadie corriente abajo.

- **Causa raíz Family='Income Oriented':** decisión de diseño no documentada formalmente. El término no tiene equivalente compacto en español ("Orientado a renta" sería 4 palabras). Requiere decisión explícita (BL-57).

- **Acción preventiva (especificación):**

  1. **Sector_Focus (defecto técnico) — corregir ahora:**
     a. Modificar `fund_characterizer.detect_sector_focus()` línea 341: cambiar `return "Real Assets"` por `return "Activos Reales"` (alinear con el resto del catálogo).
     b. Como cinturón de seguridad, en `pipeline.py` justo después de `characterize_fund` (línea 418), invocar `normalize_sector_focus()` directamente:
        ```python
        from classify_utils import normalize_sector_focus
        if classification.get("Sector_Focus"):
            classification["Sector_Focus"] = normalize_sector_focus(classification["Sector_Focus"])
        ```
     c. Esta solución es paliativa — la solución estructural es BL-56 (invocación de enrich post-characterize).

  2. **Sector_Focus (root cause):** ver BL-54 (centralización del mapeo Theme→Sector_Focus).

  3. **Subtype (decisión de diseño):**
     a. Documentar en `PRINCIPIOS_DISENO.md` que `Subtype` es atributo **multi-idioma por diseño**, con el siguiente catálogo de excepciones inglesas:
        - Acrónimos regulatorios MMF: `Standard MMF`, `VNAV`, `LVNAV`, `CNAV`.
        - Términos de mercado sin equivalente español compacto: `ETF`, `Floating Rate Notes`, `Long/Short`, `Global Macro`, `Total Return Bond`.
     b. Crear catálogo `SUBTYPE_ALLOWED_VALUES` en `classify_utils.py` con los valores aceptados (mezcla EN/ES) y validar contra él.
     c. NO traducir.

  4. **Type (decisión de diseño):**
     a. Confirmar en `TYPE_TRANSLATION_MAP` que las excepciones inglesas son: `Allocation`, `Absolute Return`, `Total Return`, `Tactical Allocation`, `Target Maturity`, `Floating Rate CP`.
     b. Añadir comentario formal en el mapa documentando la razón (ausencia de equivalente español compacto).

  5. **Family ('Income Oriented'):** ver BL-57.

- **Módulo:** `fund_characterizer.py`, `classify_utils.py`, `pipeline.py`, `PRINCIPIOS_DISENO.md`.

- **Control SQL post-fix:**
  ```sql
  -- Sector_Focus debe estar 100% en español tras BL-53/BL-54
  SELECT Sector_Focus, COUNT(*) FROM fund_master
  WHERE Sector_Focus IN ('Real Assets','Energy & Resources','Utilities & Environment',
                         'Healthcare & Life Sciences','Technology & Innovation',
                         'Materials & Mining','Financials & Insurance','Consumer Discretionary')
  GROUP BY Sector_Focus;
  -- Objetivo: 0 filas
  ```

---

**BL-54 — Centralización del mapeo Theme→Sector_Focus (NUEVO, root cause sistémico)**

- **Descripción:** Existen dos mapas paralelos `Theme→Sector_Focus` en el código:
  - `pipeline.py` líneas 758–776: hardcoded inline, idioma **español**, cubre 17 themes.
  - `fund_characterizer.detect_sector_focus()` líneas 333–349: hardcoded inline, **mezcla** (`'Real Assets'` en inglés, resto en español), cubre 14 themes con condicionales `if/elif`.

  La consecuencia es la inconsistencia BL-53 sobre `Sector_Focus` y la fragilidad de mantenimiento (cualquier nuevo theme requiere edición en dos lugares).

- **Causa raíz:** Violación directa del **Principio #2 (DRY)** del documento `PRINCIPIOS_DISENO.md`. El mapa Theme→Sector_Focus debe existir en **un único punto** y ser invocado desde ambos llamadores.

- **Acción preventiva (especificación):**

  1. **Crear el mapa canónico** en `classify_utils.py`, idioma objetivo español (alineado con la decisión de BL-53):
     ```python
     # ============================================================
     # MAPA CANÓNICO Theme → Sector_Focus
     # Idioma objetivo: español (decisión BL-53)
     # Punto único de verdad — usado por pipeline y fund_characterizer
     # ============================================================
     THEME_TO_SECTOR_FOCUS_MAP: dict = {
         "Technology":             "Tecnología e Innovación",
         "Artificial Intelligence":"Tecnología e Innovación",
         "Digital":                "Tecnología e Innovación",
         "Robotics":               "Tecnología e Innovación",
         "Cybersecurity":          "Tecnología e Innovación",
         "Healthcare":             "Salud y Ciencias de la Vida",
         "Healthcare / MedTech":   "Salud y Ciencias de la Vida",
         "Biotechnology":          "Salud y Ciencias de la Vida",
         "Silver Economy":         "Salud y Ciencias de la Vida",
         "Energy":                 "Energía y Recursos",
         "Climate / Clean Energy": "Energía y Recursos",
         "Water":                  "Utilities y Medio Ambiente",
         "Gold":                   "Materiales y Minería",
         "Mining":                 "Materiales y Minería",
         "Real Estate":            "Activos Reales",
         "Infrastructure":         "Activos Reales",
         "Insurance":              "Servicios Financieros",
         "Financials":             "Servicios Financieros",
         "Consumer Brands":        "Consumo",
         "Consumer / Food & Beverage": "Consumo",
     }

     def map_theme_to_sector_focus(theme: Optional[str]) -> Optional[str]:
         """Mapeo canónico Theme → Sector_Focus. Punto único de verdad."""
         return THEME_TO_SECTOR_FOCUS_MAP.get(theme) if theme else None
     ```
  2. **Migrar** `fund_characterizer.detect_sector_focus()`:
     ```python
     from classify_utils import map_theme_to_sector_focus

     def detect_sector_focus(name_l, kiid_text=None, theme=None):
         resolved_theme = theme or detect_theme_extended(name_l)
         return map_theme_to_sector_focus(resolved_theme)
     ```
  3. **Migrar** `pipeline.py` líneas 758–776: reemplazar el dict inline por llamada a `map_theme_to_sector_focus(theme)`.
  4. **Conservar el mapa de traducción** `SECTOR_FOCUS_TRANSLATION_MAP` en `classify_utils.py` por compatibilidad inversa (datos antiguos en BD), pero marcarlo como "legacy normalization" que solo aplica para sanear valores históricos en inglés que pudieran quedar.
  5. **Tests:** crear archivo `test_theme_sector_mapping.py` con un test por entrada del mapa (asegura que cualquier modificación futura no rompa el contrato).

- **Beneficios:**
  - Cumplimiento DRY (Principio #2).
  - Cualquier nuevo Theme se añade en un solo lugar.
  - Cumplimiento Root Cause (Principio #1) sobre BL-53 Sector_Focus.

- **Módulo:** `classify_utils.py` (nuevo mapa); `fund_characterizer.py` (refactor); `pipeline.py` (refactor).

- **Control SQL post-fix:** mismo control que BL-53 sobre `Sector_Focus` (objetivo: 0 valores en inglés).

---

**BL-55 — Registro explícito de Exit_Fee_Pct=0.00 para declaraciones implícitas (NUEVO)**

- **Descripción:** El estado actual reporta `Exit_Fee_Pct=NULL` en 676 fondos. La hipótesis (sustentada por la mejora marginal post-BL-51A v24, solo −59) es que la mayoría de estos fondos **declaran cero comisión de salida de forma implícita o indirecta** que el parser actual no formaliza. El usuario solicita explícitamente que estos casos se registren como `0.00` en lugar de `NULL`.

- **Diagnóstico cuantitativo:**
  ```sql
  -- Distribución de Exit_Fee_Pct=NULL por Fee_Known_Flag
  SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
  WHERE Exit_Fee_Pct IS NULL
  GROUP BY Fee_Known_Flag;
  -- Permite separar: NOT_FOUND (sin señal) vs ZERO_CONFIRMED (cero implícito) vs otros
  ```

- **Causa raíz:** La función `_detect_exit_fee()` cubre patrones de declaración explícita ("no cobramos", "ninguna", "no exit charge"…) pero no infiere `0.00` cuando el KIID:
  - Lista la sección "comisiones" o "costes" sin mencionar comisión de salida (ausencia ≡ no aplica).
  - Declara que la única comisión es la de gestión / suscripción.
  - Declara la estructura de costes en formato tabla y la fila correspondiente está vacía o muestra `—`, `-`, `n/a`.
  - Indica "única comisión: X%" donde X es la comisión de entrada o gestión.

- **Acción preventiva (especificación):**

  1. **Auditoría muestral:** seleccionar 30 ISIN aleatorios del subconjunto `Exit_Fee_Pct=NULL` y revisar `Raw_KIID_Text` para clasificar las formulaciones. Hipótesis a contrastar:
     - ¿Cuántos casos son "tabla con celda vacía"?
     - ¿Cuántos son "estructura sin mención"?
     - ¿Cuántos son "única comisión declarada es la de gestión"?
     - ¿Cuántos son genuinamente OCR ilegibles (no debe asumirse 0)?

  2. **Patrones nuevos para añadir a `_detect_exit_fee()`:**
     - **ZERO por declaración estructural negativa, ES:**
       - `\bsin\s+(?:comisi[oó]n|gastos?|cargos?)\s+(?:de\s+)?(?:salida|reembolso|cancelaci[oó]n)\b`
       - `\b(?:no\s+(?:hay|existen?)|inexistentes?)\s+(?:comisi[oó]n|gastos?|cargos?)\s+(?:de\s+)?(?:salida|reembolso|cancelaci[oó]n)\b`
       - `\bcomisi[oó]n\s+de\s+(?:salida|reembolso|cancelaci[oó]n)[\s:.\-]*(?:0\b|0[,\.]00\s*%|cero|ninguna|nil|n\.?\s*a\.?|n/a|—|–|-)`
     - **ZERO por declaración estructural negativa, EN:**
       - `\b(?:no|nil|none|n\.?\s*a\.?)\s+(?:exit|redemption|back[- ]end)\s+(?:charge|fee|load)\b`
       - `\b(?:exit|redemption)\s+(?:charge|fee|load)\s*[:.\-]\s*(?:0\b|0\.00\s*%|none|nil|n\.?a\.?|—)`
     - **ZERO por estructura tabular fusionada (extender JPMorgan):**
       - `costesdesalida[\s:.\-]*0[,\.]00%`
       - `costesdesalida[\s:.\-]*ninguno?s?`
       - `exitcharges?[\s:.\-]*0\.00%`
     - **ZERO por declaración de comisión única (cuidado con falsos positivos):**
       - SOLO aplicar si el KIID indica explícitamente "única comisión" / "single charge" / "only fee" referida a la entrada o gestión, y NO menciona salida en ningún punto del texto.

  3. **Lógica de cierre por defecto (con cinturón):**
     - Si tras todos los patrones anteriores `Exit_Fee_Pct` sigue siendo NULL pero `Entry_Fee_Pct` está extraído (ZERO o no), Y el KIID contiene la sección de costes claramente identificada (presencia de palabra-clave "Composición de costes" / "Composition of charges" / "Charges"), Y la sección NO menciona ninguna palabra-clave de salida (`salida|reembolso|exit|redemption`):
       - Asignar `Exit_Fee_Pct = 0.0` con `Fee_Known_Flag` extendido a un nuevo valor `EXIT_INFERRED_ZERO`.
       - Justificación: ausencia de mención en sección estructurada de costes ≡ no aplica.

  4. **NUEVO valor de `Fee_Known_Flag`:** `EXIT_INFERRED_ZERO` para distinguir:
     - `EXIT_EXPLICIT_ZERO` (declarado): patrones explícitos.
     - `EXIT_INFERRED_ZERO` (estructural): paso 3 anterior.
     - `NOT_FOUND` (genuinamente ausente): no se localiza la sección de costes.

  5. **Logging y trazabilidad:** cada inferencia debe emitir log `[ISIN] EXIT_FEE_INFERRED_ZERO via {patrón_id|sección_estructural}`.

  6. **Tests:** mínimo 15 nuevos tests unitarios distribuidos por patrón.

- **Restricción de seguridad:** la inferencia estructural (paso 3) **NO** debe aplicarse si:
  - El texto KIID es muy corto (< 500 caracteres) → señal de OCR degradado.
  - El texto contiene marcadores de sección truncada (`...`, `[truncado]`).
  - `Raw_KIID_Text` es NULL.

- **Módulo:** `kiid_parser.py` v25 — función `_detect_exit_fee()` y nuevo helper `_infer_exit_fee_from_structure()`. Schema: añadir valores nuevos a la enumeración de `Fee_Known_Flag` (sin breaking change si la columna es TEXT).

- **Control SQL post-fix:**
  ```sql
  SELECT COUNT(*) FROM fund_master WHERE Exit_Fee_Pct IS NULL;
  -- Objetivo: bajada desde 676 al rango 200-300

  SELECT Fee_Known_Flag, COUNT(*) FROM fund_master GROUP BY Fee_Known_Flag;
  -- Esperado: aparición de EXIT_INFERRED_ZERO con ~300-400 casos
  ```

---

### Media prioridad

---

**BL-56 — Invocación de enrich_classification / apply_language_homogeneity post-characterize (NUEVO, transversal)**

- **Descripción:** El pipeline (líneas 395–418 de `pipeline.py`) invoca `characterize_fund()` pero **no invoca `enrich_classification()` ni `apply_language_homogeneity()`** después. Esto significa que las normalizaciones lingüísticas centralizadas en `classify_utils.py` (incluyendo `normalize_sector_focus`, los mapas TYPE/FAMILY/SUBTYPE/THEME) **no se aplican consistentemente** sobre el dict de clasificación final.

- **Causa raíz:** Diseño actual: el bloque clasificador llama a `characterize_fund` en condiciones específicas (línea 398 `if _needs_char`), y el resultado se mezcla en `classification`. La función `enrich_classification()` existe en `classify_utils.py` (que invoca a `normalize_sector_focus` en línea 1620) pero solo se usa internamente desde otros llamadores y nunca desde el pipeline tras characterize.

- **Acción preventiva (especificación):**

  1. **Definir función agregadora** `apply_post_characterize_normalization(classification: dict) -> dict` en `classify_utils.py`:
     ```python
     def apply_post_characterize_normalization(classification: dict) -> dict:
         """
         Aplica TODAS las normalizaciones lingüísticas centralizadas
         post-characterize. Punto único de invocación desde pipeline.

         Cumple con Principio #2 (DRY): un único punto donde se ejecutan
         todas las traducciones a idioma objetivo.
         """
         if classification.get("Sector_Focus"):
             classification["Sector_Focus"] = normalize_sector_focus(
                 classification["Sector_Focus"]
             )
         if classification.get("Type"):
             classification["Type"] = TYPE_TRANSLATION_MAP.get(
                 classification["Type"], classification["Type"]
             )
         if classification.get("Family"):
             classification["Family"] = FAMILY_TRANSLATION_MAP.get(
                 classification["Family"], classification["Family"]
             )
         if classification.get("Theme"):
             classification["Theme"] = THEME_TRANSLATION_MAP.get(
                 classification["Theme"], classification["Theme"]
             )
         # Subtype: validar contra catálogo permitido sin traducir
         #   (decisión BL-53: Subtype es multi-idioma por diseño)
         return classification
     ```
  2. **Invocar desde pipeline** justo después de la mezcla del resultado de `characterize_fund` (línea 419) y antes de `validate_classification_contract`:
     ```python
     # ── Normalización lingüística centralizada (BL-56) ─────────
     classification = apply_post_characterize_normalization(classification)

     # --- validación estricta ---
     validate_classification_contract(...)
     ```
  3. **Verificación:** correr el pipeline en modo `FORCE_REFRESH` sobre los 20 fondos con `Sector_Focus IN ('Real Assets', 'Energy & Resources', ...)` y validar que tras el ciclo todos están en español.

- **Beneficios:**
  - Cumplimiento DRY (Principio #2): un único punto de aplicación de normalización.
  - Resuelve BL-53 estructuralmente (no solo Sector_Focus, también Type y Family futuros).
  - Habilita evolución del Principio #8 sin tocar pipeline.

- **Módulo:** `classify_utils.py` (nueva función agregadora); `pipeline.py` (invocación).

- **Control:** validación visual en log + SQL de BL-53.

---

**BL-57 — Decisión formal sobre Family='Income Oriented' (NUEVO)**

- **Descripción:** 104 fondos tienen `Family='Income Oriented'`, valor único en inglés en una columna cuyo idioma objetivo es español. Las opciones de tratamiento son:

  | Opción | Acción | Pros | Contras |
  |--------|--------|------|---------|
  | A | Mantener "Income Oriented" como **excepción documentada** en español | No requiere recodificación; término reconocido | Rompe homogeneidad lingüística |
  | B | Traducir a "Orientado a Renta" | Coherencia plena con Principio #8 | 4 palabras vs 2; ruptura con literatura sectorial |
  | C | Traducir a "Renta Periódica" | Compacto, español canónico | Pérdida de precisión semántica (rentas pueden no ser periódicas) |
  | D | Migrar a `Family='Mixtos'` con `Income_Oriented_Flag=1` | Mantiene taxonomía limpia; expone el matiz como flag separado | Requiere cambio de schema (nueva columna) |

- **Causa raíz:** Decisión no formalizada en `PRINCIPIOS_DISENO.md`. El `FAMILY_TRANSLATION_MAP` no incluye una entrada para "Income" / "Income Oriented".

- **Acción preventiva (especificación):**

  1. **Diagnóstico previo a la decisión:**
     ```sql
     -- Distribución de Income Oriented por Nature (orienta opción D)
     SELECT Fund_Nature, COUNT(*) FROM fund_master
     WHERE Family='Income Oriented'
     GROUP BY Fund_Nature ORDER BY 2 DESC;

     -- ¿Income Oriented coexiste con Accumulation_Policy='ACCUMULATION'?
     -- (test de coherencia interna)
     SELECT Accumulation_Policy, COUNT(*) FROM fund_master
     WHERE Family='Income Oriented'
     GROUP BY Accumulation_Policy;
     ```
  2. **Decisión** (recomendada: **opción A** + documentación) — pendiente de tu validación. Justificación: "Income Oriented" es un término sectorial reconocido sin equivalente compacto en español; Morningstar y MSCI lo usan en taxonomías españolas.
  3. Si la decisión es A: añadir entrada en `FAMILY_TRANSLATION_MAP` (identidad: `'Income Oriented' → 'Income Oriented'`) y comentario formal en `PRINCIPIOS_DISENO.md` sección 2.1 documentando la excepción.
  4. Si la decisión es D: requiere cambio de schema (nueva columna `Income_Oriented_Flag BOOLEAN`), pipeline de migración, y P3 debe consumir el flag separado.

- **Módulo:** `PRINCIPIOS_DISENO.md` (decisión); `classify_utils.py` (FAMILY_TRANSLATION_MAP); secundariamente schema si opción D.

---

### Baja prioridad / futura

---

**BL-48-ext — Normalización Family en Monetarios JPMorgan**
- 18 fondos JPMorgan con `Family=LVNAV/VNAV/CNAV`. Subtype ya captura el matiz.
- **Prerequisito:** confirmar independencia de consumidores P2/P3.

**BL-47-ext — SFDR Art. 8 como default defensivo**
- BL-47 cerrado pero estrategia defensiva sin documentar formalmente.
- **Acción:** evaluar integración con fuente externa ESMA.

**BL-51 Problema B — Estructura mixta cap/floor**
- Esquema decidido en `BL51_SCHEMA_DECISION.md`, prevalencia diagnóstica pendiente.
- **Prerequisito:** ejecutar query de prevalencia (sección 6 del documento).

**P2 — Factores macro**
- Series FRED: `BAMLH0A0HYM2` (HY spread), `VIXCLS` (VIX), `T10Y2YM` (term spread).
- Infraestructura de descarga, normalización y almacenamiento en SQLite.

**P3 — Scoring régimen-dependiente**
- Framework de cinco fases diseñado, no implementado.
- Pesos empíricos por régimen: pendiente dataset etiquetado de P2.

---

## 4. GAPS ESTRUCTURALES — LÍMITE REAL DE EXTRACCIÓN

| Atributo | NULL actual | NULL% | Naturaleza | Acción |
|----------|-------------|-------|------------|--------|
| `Subtype` | 2.934 | 91,6% | Gran mayoría de natures×types sin variante estructural diferenciable. 270 con valor es cobertura correcta post-BL-43. | Ninguna activa |
| `Sector_Focus` | 2.830 | 88,3% | Solo fondos sectoriales (~374). Cobertura correcta. Límite real. **20 inconsistencias EN/ES residuales (BL-53/54).** | **BL-53/BL-54** |
| `Market_Cap_Focus` | 1.384 | 43,2% | Estable post-BL-27-ext. Los non-RV son correctamente NULL. | Ninguna |
| `Benchmark_Declared` | 1.472 | 45,9% | Fondos sin benchmark detectable en KIID. Límite estructural. | Ninguna |
| `Sfdr_Article` | 1.156 | 36,1% | 386 fondos pre-PRIIPs genuinos + ~770 sin declaración. | Ninguna (BL-47-ext) |
| `Currency_Hedged` | 1.148 | 35,8% | 267 fondos no-EUR con gap actionable. | **BL-49** |
| `Style_Profile` | 870 | 27,2% | Estable post-BL-41-ext. Residual sin señal explícita. | Ninguna |
| `Exit_Fee_Pct` | 676 | 21,1% | **Ceros implícitos no formalizados.** | **BL-51 + BL-55** |
| `Hedging_Policy` | 593 | 18,5% | Estable post-BL-45. Sin señal explícita en KIID. | Ninguna |
| `Accumulation_Policy` | 394 | 12,3% | KIIDs pre-2015 o texto OCR degradado. Límite real. | Ninguna |
| `Geography` | 306 | 9,6% | Mayoría sin señal geográfica. Gap accionable via inferencia INTER. | **BL-50** |
| `Investment_Universe` | 203 | 6,3% | Gap accionable via inferencia INTER. | **BL-50** |
| `Entry_Fee_Pct` | 129 | 4,0% | 110 NOT_FOUND residuales. | **BL-51A** |

---

## 5. CAUSA RAÍZ SISTÉMICA — DOBLES EMISIONES SIN NORMALIZACIÓN

Adicional a la causa raíz documentada en v3.1 sobre `COALESCE` en `sqlite_writer`, esta versión documenta una **segunda causa raíz transversal**:

**Doble punto de emisión sin normalización centralizada:**
- Atributos como `Sector_Focus` se emiten desde **dos puntos paralelos** (`fund_characterizer.detect_sector_focus` y `pipeline.py` mapeo Theme→Sector_Focus inline).
- La función centralizadora `normalize_sector_focus` existe pero **solo se invoca desde `enrich_classification`**, que **no se llama en pipeline post-characterize**.
- Resultado: 20 fondos con valores en inglés sobreviven al ciclo.

**Solución estructural:** BL-54 (mapa único Theme→Sector_Focus) + BL-56 (invocación centralizada de normalización post-characterize).

**Principio derivado y a documentar:**

> Todo atributo que pueda emitirse desde más de un punto del pipeline (ej. clasificador especializado + inferencia post-clasificación) debe tener una **función agregadora canónica** en `classify_utils.py` que se invoque obligatoriamente como último paso antes de la persistencia. La existencia de mapas o lógicas paralelas (incluso "equivalentes") es una violación del Principio #2 (DRY) y crónicamente genera deriva semántica.

---

## 6. MÓDULOS DESPLEGADOS — VERSIONES VIGENTES

| Módulo | Versión | Cambios principales | Estado |
|--------|---------|---------------------|--------|
| `pipeline.py` | **v25** | BL-50 catálogos ampliados (Country/Regional/Liquidity), BL-52 corrección semántica Country↔Región | **DESPLEGADO** |
| `kiid_parser.py` | **v24** | BL-51A: 6 patrones entry fee (3 ZERO + 3 PCT), 4 patrones exit fee (3 ZERO + 1 PCT). Separador decimal opcional | **DESPLEGADO** |
| `classify_utils.py` | v4 | BL-19/22/23/24/30/31/32/33 | **DESPLEGADO** |
| `fund_characterizer.py` | v18 | BL-26/27/28/29/49 (firma con kiid_text) | **DESPLEGADO** |
| `benchmark_normalizer.py` | vBL-39 | +20 aliases, +9 false positives | **DESPLEGADO** |
| `restantes.py` | v4 | BL-09/20/21 | **DESPLEGADO** |

**Próximas versiones a producir según especificaciones de este backlog:**

| Módulo | Versión objetivo | BL involucrados | Prioridad |
|--------|------------------|-----------------|-----------|
| `fund_characterizer.py` | v19 | BL-49 (cuerpo de detect_currency_hedged), BL-53 ('Real Assets' → 'Activos Reales'), BL-54 (refactor sector_focus) | Alta |
| `kiid_parser.py` | v25 | BL-51A residual (110 entry NOT_FOUND), BL-55 (Exit_Fee_Pct=0 implícito + EXIT_INFERRED_ZERO) | Alta |
| `classify_utils.py` | v5 | BL-54 (THEME_TO_SECTOR_FOCUS_MAP centralizado), BL-56 (apply_post_characterize_normalization), BL-57 (FAMILY_TRANSLATION_MAP) | Alta |
| `pipeline.py` | v26 | BL-50 dirección inversa (Universe='Global'/'Liquidity' → Geography), BL-54 (uso del mapa centralizado), BL-56 (invocación post-characterize) | Alta |

---

## 7. PRINCIPIOS DE DISEÑO CONSOLIDADOS (v3.2)

### Principios heredados de v3.1
- **Principio de valores semánticos explícitos sobre NULL** (Subtype, Style_Profile, Market_Cap_Focus).
- **Principio de extracción en cascada**: nombre → KIID, para atributos con NULL > 2%.
- **Principio de inferencia INTER para nulos geográficos**: Geography ↔ Investment_Universe.
- **Principio de toda corrección INTER opera sobre valor efectivo (record OR BD)**: por COALESCE en sqlite_writer.

### Nuevos principios introducidos en v3.2

#### Principio de punto único de emisión por atributo (DRY estructural)

**Enunciado:** Cualquier atributo que pueda asignarse desde más de un punto del código debe tener una **función canónica única** en `classify_utils.py` que centralice la lógica. Los puntos de invocación deben llamar a esa función en lugar de replicar la lógica inline.

**Aplicación:** Theme→Sector_Focus (BL-54), traducciones lingüísticas (BL-56), mapas Type/Family/Subtype.

#### Principio de declaración implícita formalizada

**Enunciado:** Cuando un atributo puede asumirse cero/ausente por **estructura del documento fuente** (sección estructurada de costes sin mención de la comisión, tabla con celda vacía, declaración negativa indirecta), el parser debe formalizar el valor como `0.00` con un flag de trazabilidad (`EXIT_INFERRED_ZERO`) en lugar de mantener NULL. La distinción entre cero explícito y cero estructural debe quedar registrada para auditoría.

**Aplicación documentada:** BL-55 (Exit_Fee_Pct).

---

## 8. QUERIES DE VALIDACIÓN COMPLETAS (post-ciclo v25)

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
UNION ALL SELECT 'BL-44', COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND CAST(SRRI AS INTEGER) >= 3
UNION ALL SELECT 'BL-45', COUNT(*) FROM fund_master WHERE Currency_Hedged='Hedged' AND Hedging_Policy IS NULL
UNION ALL SELECT 'BL-46', COUNT(*) FROM fund_master WHERE Benchmark_Declared IS NOT NULL AND Benchmark_Declared != 'NO_BENCHMARK' AND Benchmark_Type IS NULL
UNION ALL SELECT 'BL-47', COUNT(*) FROM fund_master WHERE Is_ESG=1 AND Sfdr_Article IS NULL
UNION ALL SELECT 'BL-52', COUNT(*) FROM fund_master
  WHERE Investment_Universe='Country'
    AND Geography IN ('Latinoamérica','Europa del Este','Asia Pacífico',
                      'Emergentes','América Latina','Europa Central',
                      'África','Oriente Medio','América del Norte');
-- Todos deben devolver 0.

-- ── COBERTURA — seguimiento de progreso ────────────────────────────────
SELECT 'OC_null'             AS attr, COUNT(*) AS n FROM fund_master WHERE Ongoing_Charge IS NULL
UNION ALL SELECT 'entry_NOT_FOUND',   COUNT(*) FROM fund_master WHERE Fee_Known_Flag='NOT_FOUND' AND Entry_Fee_Pct IS NULL
UNION ALL SELECT 'AP_null',           COUNT(*) FROM fund_master WHERE Accumulation_Policy IS NULL
UNION ALL SELECT 'HP_null',           COUNT(*) FROM fund_master WHERE Hedging_Policy IS NULL
UNION ALL SELECT 'CH_null',           COUNT(*) FROM fund_master WHERE Currency_Hedged IS NULL
UNION ALL SELECT 'CH_nonEUR_null',    COUNT(*) FROM fund_master WHERE Fund_Currency IN ('USD','GBP','CHF','JPY','CNH') AND Currency_Hedged IS NULL
UNION ALL SELECT 'Geography_null',    COUNT(*) FROM fund_master WHERE Geography IS NULL
UNION ALL SELECT 'Universe_null',     COUNT(*) FROM fund_master WHERE Investment_Universe IS NULL
UNION ALL SELECT 'Universe_no_Geo',   COUNT(*) FROM fund_master WHERE Investment_Universe IS NOT NULL AND Geography IS NULL
UNION ALL SELECT 'Style_null',        COUNT(*) FROM fund_master WHERE Style_Profile IS NULL
UNION ALL SELECT 'Exit_null',         COUNT(*) FROM fund_master WHERE Exit_Fee_Pct IS NULL
UNION ALL SELECT 'MCF_null_RV',       COUNT(*) FROM fund_master WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NULL;

-- ── BL-49: Diagnóstico Currency_Hedged ─────────────────────────────────
SELECT Fund_Currency, COUNT(*) FROM fund_master
WHERE Currency_Hedged IS NULL
GROUP BY Fund_Currency ORDER BY 2 DESC;

-- ── BL-50: Diagnóstico Universe / Geography (residuales 7) ──────────────
SELECT Investment_Universe, COUNT(*) FROM fund_master
WHERE Investment_Universe IS NOT NULL AND Geography IS NULL
GROUP BY Investment_Universe;

SELECT Geography, COUNT(*) FROM fund_master
WHERE Investment_Universe IS NULL AND Geography IS NOT NULL
GROUP BY Geography ORDER BY 2 DESC;

-- ── BL-51A: Entry/Exit residuales ───────────────────────────────────────
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
WHERE Entry_Fee_Pct IS NULL
GROUP BY Fee_Known_Flag ORDER BY 2 DESC;

SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
WHERE Exit_Fee_Pct IS NULL
GROUP BY Fee_Known_Flag ORDER BY 2 DESC;

-- ── BL-53: Auditoría lingüística ────────────────────────────────────────
SELECT 'family' AS col, Family AS val, COUNT(*) AS n FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
UNION ALL
SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
UNION ALL
SELECT 'subtype', Subtype, COUNT(*) FROM fund_master WHERE Subtype IS NOT NULL GROUP BY Subtype
UNION ALL
SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
ORDER BY 1, 3 DESC;

-- ── BL-53 control específico Sector_Focus (debe devolver 0 post-fix) ────
SELECT COUNT(*) FROM fund_master
WHERE Sector_Focus IN ('Real Assets','Energy & Resources','Utilities & Environment',
                       'Healthcare & Life Sciences','Technology & Innovation',
                       'Materials & Mining','Financials & Insurance','Consumer Discretionary');

-- ── BL-57 diagnóstico Family='Income Oriented' ──────────────────────────
SELECT Fund_Nature, COUNT(*) FROM fund_master
WHERE Family='Income Oriented' GROUP BY Fund_Nature ORDER BY 2 DESC;

SELECT Accumulation_Policy, COUNT(*) FROM fund_master
WHERE Family='Income Oriented' GROUP BY Accumulation_Policy;
```

---

## 9. REGISTRO DE DECISIONES DE DISEÑO (acumulado v3.2)

| Decisión | Alternativa considerada | Razón de elección |
|----------|------------------------|-------------------|
| `Standard MMF` como Subtype para monetarios pre/fuera-MMF 2017/1131 | NULL o valor en Data_Quality_Flag | Semánticamente preciso, útil en P3 |
| `Blend` para RV activa sin estilo declarado | NULL o "Unknown" | Convención sectorial (Morningstar, MSCI) |
| `Not Applicable` para RV indexada/pasiva | NULL o no asignar | Coherencia con Credit_Quality |
| `All Cap` para RV no sectorial sin restricción | NULL o "Multi Cap" | Convención estándar |
| BL-45: inferir HP desde CH | Nuevo detector en parser | CH ya validado; coste menor |
| Family Monetario LVNAV/VNAV/CNAV (BL-48-ext) | Normalizar a Monetario | Decisión conservadora hasta confirmar P2/P3 |
| BL-51 Problema B: schema antes de implementar | Implementación directa | Impacto transversal P1/BD/P3 |
| BL-51A: separador decimal opcional | Decimal obligatorio | Cubre "5%" sin decimales |
| BL-51B: 4 campos REAL cap/floor | Campo textual `Fee_Structure_Notes` | Tipado fuerte consultable por P3 |
| BL-49: extracción cascada nombre→KIID | Solo nombre | 267 fondos con gap accionable |
| **BL-52: corrección Country→Regional cuando Geography=región** | Sin acción | Inconsistencia semántica, no aceptable en BD |
| **BL-53/54: idioma objetivo Sector_Focus = español** | Inglés (GICS-EN) | Coherencia con Family/Type/Geography; mapa centralizado |
| **BL-55: Exit_Fee_Pct=0.00 con flag EXIT_INFERRED_ZERO** | Mantener NULL | Permite a P3 distinguir cero estructural de cero desconocido |
| **BL-56: invocación centralizada de normalización post-characterize** | Replicar inline en pipeline | DRY; un único punto de mantenimiento |
| **BL-57 (recomendado): Family='Income Oriented' como excepción documentada** | Traducir a "Orientado a Renta" | Término sectorial sin equivalente compacto |

---

**Fin del documento. Versión v3.2 — 24 de abril de 2026.**
