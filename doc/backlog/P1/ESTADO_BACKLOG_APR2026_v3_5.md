# Estado del Backlog P1 — Referencia de Sesión v3.5 (autocontenida)

**Fecha:** 2 de mayo de 2026
**Ciclo de referencia:** `p1_export_20260426.xlsx` (3.204 fondos, schema v17) — log `log_pipeline_20260426_202952.log`
**Sprint cerrado en este documento:** BL-DLA-0 (diagnóstico Document Layout Analysis sobre 300 fondos muestra)

**Estatus de este documento:**
v3.5 es un **documento autocontenido**. Todas las especificaciones de items abiertos heredados de versiones anteriores (v3.2, v3.3, v3.4) están copiadas íntegramente. No se requiere consultar versiones previas para implementar ningún preventivo listado en la sección 3.

**Norma vigente desde v3.4:** cada versión del backlog debe ser autocontenida. Las especificaciones de items abiertos heredados deben copiarse íntegramente; la sección "Diff vs versión anterior" sustituye a la delegación inline. La omisión silenciosa de items heredados constituye violación del Principio #1 aplicado a la gestión documental.

**Módulos desplegados en producción (acumulado, sin cambios respecto a v3.4):**
- `pipeline.py` v25 — BL-50 catálogos ampliados, BL-52 corrección Country↔Región
- `kiid_parser.py` v24 — BL-51A (10 nuevos patrones entry/exit fee, separador decimal opcional)
- `classify_utils.py` v4 + BL-57 v3 — constante `FAMILY_INCOME_ORIENTED` centralizada
- `blocks/mixtos.py` — BL-57 v3: import de constante, eliminado literal inline
- `fund_family_builder.py` — BL-FAM-FIX: D1 `_UNIVERSAL_ADJACENT`, D2 Regla 2-bis, D3 Regla 3 con desempate DQ, D4 regex `inc` sin `ome`
- `fund_characterizer.py` v18, `benchmark_normalizer.py` vBL-39, `restantes.py` v4

**Documentos de decisión vinculados:**
- `BL_DLA_DESIGN_DECISION.md` (en `doc/backlog/`) — propuesta arquitectónica DLA, validada empíricamente.

---

## 0. DIFF vs v3.4 — TRAZABILIDAD DE CAMBIOS

### Items nuevos en v3.5:

- **BL-DLA-0** (Diagnóstico Document Layout Analysis) — **CERRADO en v3.5**. Q-DLA-01/02/03 ejecutadas; resultado n=300 muestra aleatoria: 88,3% IC95% [84,1%, 91,7%] de fondos con al menos una página en layout 2-columnas. Decisión Go robusta sobre umbral 30%.
- **BL-DLA-1** (Implementación Fase 1: serialización 2D-aware de párrafos en dos columnas) — **ABIERTO en v3.5**, prioridad Alta. Especificación íntegra en sección 3.

### Items heredados de v3.4 sin cambios (especificaciones íntegras conservadas):

BL-61 (Strategy/Replication 12 fondos), BL-59 (FAM_000261 Restantes mayoritario), BL-49 (Currency_Hedged extracción KIID), BL-50 (Universe→Geography inferencia inversa), BL-53/54 (Sector_Focus idioma), BL-55 (Exit=0.00 implícito), BL-56 (enrich centralizado), BL-58 (constantes Lifecycle/Retirement), BL-60 (bipartitas empate total), BL-51 Problema B (schema cap/floor), BL-47-ext (SFDR Art. 8 default), BL-48-ext (Family LVNAV/VNAV/CNAV), BL-51A residual.

### Causa raíz documentada en v3.5:

- Sección 5.5 nueva: **Calidad upstream antes que cobertura downstream**. La acumulación de 23 prioridades en `_detect_entry_fee` (v24/v25) y la capa L0-FUSED en `srri_text.py v3` se identifican como síntomas de una causa raíz común: la serialización 2D→1D defectuosa en `extract_text_from_pdf_bytes`. BL-DLA aborda esa causa raíz.

### Principio nuevo introducido en v3.5:

- **Principio de calidad upstream antes que cobertura downstream** (sección 7). Cuando un atributo presenta cobertura insuficiente, antes de añadir patrones regex en el detector, verificar si la causa raíz es la calidad de la entrada que el detector recibe. Una mejora upstream que beneficie a N detectores es preferible a N parches downstream.

### Items copiados íntegramente desde v3.4:

Los items de Alta, Media y Baja prioridad de v3.4 se conservan sin modificación. Las queries de validación de la sección 8 se mantienen y se añade Q-DLA-* en una nueva subsección.

### Items cerrados en v3.4 (mantienen estatus en v3.5):

BL-57 v3, BL-FAM-FIX D1/D2/D3/D4.

---

## 1. ITEMS RESUELTOS — ACUMULADO HISTÓRICO

| BL | Descripción | Control SQL | Resultado |
|----|-------------|-------------|-----------|
| BL-09 | SRRI fallback desde texto | — | **✅ Resuelto** |
| BL-19 | Sin "Mixto" singular | `COUNT(*) WHERE Fund_Nature='Mixto'` | **0 ✅** |
| BL-20 | Credit_Quality language fix | — | **✅ Resuelto** |
| BL-21 | Logging fixes restantes | — | **✅ Resuelto** |
| BL-22 | INTER validaciones | — | **✅ Resuelto** |
| BL-23 | Dictionary unification | — | **✅ Resuelto** |
| BL-24 | Language normalization | — | **✅ Resuelto** |
| BL-26 | Currency_Hedged sin "Yes"/"No" | `COUNT(*) WHERE Currency_Hedged IN ('Yes','No')` | **0 ✅** |
| BL-27 | Market_Cap_Focus en RV > 200 | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL` | **1.820 ✅** |
| BL-27-ext | Market_Cap_Focus All Cap default | — | **✅ Resuelto** |
| BL-28 | Sin Credit_Quality="No aplica" | `COUNT(*) WHERE Credit_Quality='No aplica'` | **0 ✅** |
| BL-29 | Style_Profile KIID-layer | — | **✅ Resuelto** |
| BL-30 | Sin Investment_Focus=Broad + Sector_Focus | `COUNT(*) WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL` | **0 ✅** |
| BL-31 | Sin contradicción CH vs HP | `COUNT(*) WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED') OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED')` | **0 ✅** |
| BL-32 | Sin Dist_Freq con AP=NULL | `COUNT(*) WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL` | **0 ✅** |
| BL-33 | Sin Monetario/RFC con Universe=NULL | `COUNT(*) WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL` | **0 ✅** |
| BL-34 | Sin Credit_Quality="No aplica" | `COUNT(*) WHERE Credit_Quality='No aplica'` | **0 ✅** |
| BL-35 | Entry_Fee NOT_FOUND (gestoras) | `COUNT(*) WHERE Fee_Known_Flag='NOT_FOUND'` | **✅ 99% resuelto** |
| BL-35b | Entry_Fee NOT_FOUND Thread+AXA | — | **✅ Resuelto** |
| BL-37 | Ongoing_Charge NULL < 600 | `COUNT(*) WHERE Ongoing_Charge IS NULL` | **74 ✅** |
| BL-37b | OC NULL JPMorgan fused | — | **74 ✅** |
| BL-38 | Sin benchmarks contaminados | `COUNT(*) WHERE LENGTH(Benchmark_Declared)>100 AND Benchmark_Declared!='NO_BENCHMARK'` | **0 ✅** |
| BL-39 | Benchmark normalizer aliases | — | **✅ Resuelto** |
| BL-40 | Accumulation_Policy NULL Deutsche+BlackRock | `COUNT(*) WHERE Accumulation_Policy IS NULL` | **394 ✅** |
| BL-41 | Style_Profile desde KIID (señales estrictas) | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Style_Profile IS NOT NULL` | **2.334 ✅** |
| BL-41-ext | Style_Profile defaults semánticos (Blend/Not Applicable) | — | **✅ Resuelto** |
| BL-42 | Credit_Quality Mixtos NULL | `COUNT(*) WHERE Fund_Nature='Mixtos' AND Credit_Quality IS NULL` | **0 ✅** |
| BL-43a | Subtype Monetario VNAV/LVNAV/CNAV | — | **✅ Resuelto** |
| BL-43a-ext | Subtype Monetario Standard MMF | `COUNT(*) WHERE Fund_Nature='Monetario' AND Subtype='Standard MMF'` | **38 ✅** |
| BL-43b | Subtype Mixtos Fixed Band + Volatility Target | — | **✅ Resuelto** |
| BL-44 | Misclasificaciones Fund_Nature SRRI elevado | `COUNT(*) WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND CAST(SRRI AS INTEGER) >= 3` | **0 ✅** |
| BL-45 | Hedging_Policy inferida desde Currency_Hedged | `COUNT(*) WHERE Currency_Hedged='Hedged' AND Hedging_Policy IS NULL` | **0 ✅** |
| BL-46 | Benchmark_Type NULL con Benchmark_Declared poblado | `COUNT(*) WHERE Benchmark_Declared IS NOT NULL AND Benchmark_Declared != 'NO_BENCHMARK' AND Benchmark_Type IS NULL` | **0 ✅** |
| BL-47 | Is_ESG=1 sin Sfdr_Article | `COUNT(*) WHERE Is_ESG=1 AND Sfdr_Article IS NULL` | **0 ✅** |
| BL-48 | Subtype Monetario distribución correcta | — | **✅ Distribución correcta** |
| BL-52 | Universe='Country' con región en Geography | `COUNT(*) WHERE Investment_Universe='Country' AND Geography IN (regiones)` | **0 ✅** |
| BL-57 v3 | Centralización `FAMILY_INCOME_ORIENTED`; 104 fondos en 'Orientado a Renta' | Queries A-E: 0 / 104 / 365+104+4 / 0 / 3204 | **✅ RESUELTO v3.3** |
| BL-FAM-FIX D1 | `Restantes` adyacente universal; par `Mixtos/RFCP` añadido | FAM_000382 corregida; `_UNIVERSAL_ADJACENT` operativo | **✅ RESUELTO v3.3** |
| BL-FAM-FIX D2 | Regla 2-bis: bipartitas resueltas por jerarquía DQ/SRRI | Aplica cuando hay asimetría de calidad | **✅ RESUELTO v3.3** |
| BL-FAM-FIX D3 | Regla 3 reescrita: desempate SRRI→DQ | Umbral anterior >2 era inalcanzable; DQ ahora como criterio secundario | **✅ RESUELTO v3.3** |
| BL-FAM-FIX D4 | `_normalize_name`: "Income" no es sufijo de clase | Templeton Global Income ≠ FAM_001293; GS GBL EQ INCOME ≠ FAM_001344 | **✅ RESUELTO v3.3** |
| BL-DLA-0 | Diagnóstico Document Layout Analysis (Q-DLA-01/02/03) | n=300 muestra: 88,3% con 2-col detectado; IC95% [84,1%, 91,7%]; >> umbral 30% | **✅ RESUELTO v3.5** |

---

## 2. ESTADO DE COBERTURA — 26-ABRIL-2026 (post-ciclo BL-57v3+BL-FAM-FIX)

| Atributo | Filled | NULL | NULL% | Tendencia |
|----------|--------|------|-------|-----------|
| `Fund_Nature` | 3.204 | 0 | 0,00% | ✅ Completo |
| `Profile` | 3.204 | 0 | 0,00% | ✅ Completo |
| `Strategy` | 3.204 | 0 | 0,00% | ✅ Completo |
| `Family` | 3.204 | 0 | 0,00% | ✅ Completo |
| `Type` | 3.204 | 0 | 0,00% | ✅ Completo |
| `Theme` | 3.204 | 0 | 0,00% | ✅ Completo |
| `Leverage_Used` | 3.204 | 0 | 0,00% | ✅ Completo |
| `Derivatives_Usage` | 3.204 | 0 | 0,00% | ✅ Cobertura total con distribución correcta (YES=1.649 / NO=1.308 / LIMITED=247) |
| `Currency_Hedged` | 2.669 | 535 | 16,70% | ⚠️ BL-49 pendiente |
| `SRRI` | 3.186 | 18 | 0,56% | ✅ Límite estructural |
| `Investment_Focus` | 3.169 | 35 | 1,09% | ✅ Límite estructural |
| `Fund_Currency` | 3.147 | 57 | 1,78% | ✅ Límite estructural |
| `Ongoing_Charge` | 3.130 | 74 | 2,31% | ✅ Límite estructural |
| `Entry_Fee_Pct` | 3.075 | 129 | 4,03% | ⚠️ BL-51A residual (115 NOT_FOUND) |
| `Investment_Universe` | 3.001 | 203 | 6,34% | ⚠️ BL-50 pendiente |
| `Geography` | 2.898 | 306 | 9,55% | ⚠️ BL-50 pendiente |
| `Accumulation_Policy` | 2.810 | 394 | 12,30% | ✅ Límite estructural |
| `Hedging_Policy` | 2.611 | 593 | 18,51% | ✅ Estable post-BL-45 |
| `Exit_Fee_Pct` | 2.528 | 676 | 21,10% | ⚠️ BL-55 pendiente |
| `Style_Profile` | 2.334 | 870 | 27,15% | ✅ Estable |
| `Sfdr_Article` | 2.048 | 1.156 | 36,08% | Límite regulatorio |
| `Market_Cap_Focus` | 1.820 | 1.384 | 43,20% | ✅ Estable |
| `Benchmark_Declared` | 1.732 | 1.472 | 45,94% | Límite estructural |
| `Sector_Focus` | 374 | 2.830 | 88,33% | ⚠️ BL-53/BL-54 pendientes |
| `Subtype` | 270 | 2.934 | 91,57% | ⚠️ BL-53 residual lingüístico |

**Distribución `Family` post-sprint v3.3:**

| Family | n |
|--------|---|
| RV Core | 1.455 |
| Renta Fija Corto Plazo | 427 |
| Renta Fija Flexible | 415 |
| Mixtos | 365 |
| RV Temática | 218 |
| Orientado a Renta | 104 |
| Monetario | 99 |
| Retorno Absoluto | 43 |
| RF High Yield | 39 |
| Activos Reales | 17 |
| Estructurado | 8 |
| RF Inflación | 5 |
| RF Emergentes | 5 |
| Flexible Estratégico | 4 |
| **Total** | **3.204** |

**Distribución `Fund_Nature` post-sprint v3.3:**

| Nature | n |
|--------|---|
| Renta Variable | 1.674 |
| Mixtos | 469 |
| Renta Fija Flexible | 468 |
| Renta Fija Corto Plazo | 418 |
| Monetario | 74 |
| Alternativo | 60 |
| Restantes | 33 |
| Estructurado | 8 |

**Estado de familias:**

| Métrica | Valor |
|---------|-------|
| Familias totales | 2.629 |
| Familias con múltiples clases | 373 |
| Familias inconsistentes (Nature) | 8 |
| Correcciones aplicadas en sprint v3.3 | 2 |

---

## 3. ITEMS ABIERTOS — PRIORIZACIÓN

### Alta prioridad

---

**BL-DLA-1 — Implementación Fase 1 DLA: serialización 2D-aware de párrafos en dos columnas**

- **Estado:** Abierto en v3.5 tras cierre exitoso de BL-DLA-0 (diagnóstico). Documento de decisión arquitectónica completo en `doc/backlog/BL_DLA_DESIGN_DECISION.md`.

- **Causa raíz atacada:** `proyecto1/core/io.py:extract_text_from_pdf_bytes()` usa `pdfplumber.page.extract_text()` con configuración por defecto, que serializa el PDF en orden estricto de coordenada Y. En layouts de dos columnas (88,3% del corpus según Q-DLA-03), esto intercala líneas físicamente alineadas que pertenecen a párrafos lógicamente distintos, produciendo frases sintácticamente incoherentes que silencian patrones regex correctos y habilitan matches espurios en el conjunto de detectores downstream (`kiid_parser.py`, `srri_text.py`, bloques de clasificación).

- **Evidencia empírica del cierre BL-DLA-0:**

  | Métrica | Valor |
  |---|---|
  | Muestra Q-DLA-03 | 300 fondos aleatorios (semilla 42) |
  | Con ≥1 página en 2-cols | 265 (88,3%) |
  | IC95% Wilson | [84,1%, 91,7%] |
  | Con ≥2 páginas en 2-cols | 138 (46,0%) |
  | Layout `T,T,T` (íntegramente 2-cols) | 65 (21,7%) |
  | Layout `S,S,S` (sin patología) | 34 (11,3%) |
  | Layout MIXED (≥1 página) | 23 (7,7%) |
  | Distinct layout signatures | 12 |
  | Errores de descarga | 1/300 (0,3%) |

- **Diseño técnico (resumen del documento de decisión):**

  Estructura de módulos:
  - **Nuevo:** `proyecto1/core/dla_extractor.py` — clasificación de layout por página + serialización 2D-aware.
  - **Modificación quirúrgica:** `proyecto1/core/io.py:extract_text_from_pdf_bytes()` — kill-switch `DLA_ENABLED`, fallback a comportamiento actual si DLA falla.
  - **Sin cambios:** `kiid_parser.py`, `srri_text.py`, `srri_v4_geometric.py`, `classify_utils.py`, bloques. La promesa de la arquitectura es que los módulos downstream no se tocan.

  Estrategia de detección por página (decisión registrada 02-may-2026, Nivel-3):
  - **Nivel 1 (siempre):** heurística width-only. Clasifica una página en `SINGLE_COL`, `TWO_COL`, `MIXED`, `NO_TEXT` según anchos de bloque y centros X. Cubre 92,3% de páginas con clasificación clara.
  - **Nivel 2 (solo si MIXED):** heurística de gap-detection. Histograma de coordenadas X; si hay banda vertical con ausencia de bloques superior a un umbral, clasifica como `TWO_COL`; si no, como `SINGLE_COL`. Coste marginal acotado al 7,7% de páginas (~5 ms extra).

  Algoritmo de serialización por página:
  - `SINGLE_COL`: orden natural por (y, x).
  - `TWO_COL`: separar por mitad horizontal de página, ordenar dentro de cada mitad por (y, x), emitir izquierda completa luego derecha completa.
  - `MIXED` tras Nivel 2: idem según resultado del Nivel 2.
  - `NO_TEXT`: fallback OCR existente (sin DLA en Fase 1; OCR-aware se difiere a Fase 4).

  Backward compatibility:
  - Kill switch `DLA_ENABLED = False` en `io.py` revierte al comportamiento actual sin tocar más código.
  - No hay migración masiva forzada de `Raw_KIID_Text`. La mejora se propaga progresivamente vía `mark_stale_for_refresh` (180 días, 50 fondos/ciclo).

- **Sub-fases de implementación:**

  | Sub-fase | Entregable | Criterio de salida |
  |---|---|---|
  | 1A | `proyecto1/core/dla_extractor.py` aislado + tests unitarios | 5 PDFs muestreados producen layout signatures correctos; regex `Tipo[\s\n]+Este\s+producto\s+es\s+un\s+subfondo` matchea en re-serialización de IE0032875985; AST OK |
  | 1B | Modificación quirúrgica en `proyecto1/core/io.py` con kill-switch | Pipeline ejecutado con `DLA_ENABLED=False` produce salida idéntica al estado pre-fix |
  | 1C | Activar `DLA_ENABLED=True` para 25 ISINs piloto (los 5 muestreados + 20 con ≥2 pág 2-cols seleccionados de Q-DLA-03) | Sin regresión: 0 atributos poblados pasan a NULL; ≥3 fondos con mejora demostrable; 0 errores de pipeline |
  | 1D | Activación global con migración natural | Resumen de ciclo DLA en log; cobertura agregada neta no negativa |

- **Tests obligatorios (Sub-fase 1A):**
  ```python
  def test_classify_page_single_col():
      # Página con bloques anchos predominantes -> SINGLE_COL.
      ...

  def test_classify_page_two_col_pimco():
      # IE0032875985 página 0 -> TWO_COL (3/3 páginas en 2-cols).
      ...

  def test_serialize_two_col_preserves_lexical_integrity():
      # Re-serialización IE0032875985 página 0: regex
      # 'Tipo[\s\n]+Este producto es un subfondo' matchea.
      ...

  def test_serialize_two_col_eliminates_corrupt_pattern():
      # Re-serialización IE0032875985 página 0: regex
      # 'Tipo\s+(tres|cinco|diez)\s+a[ñn]os' NO matchea.
      ...

  def test_mixed_fallback_to_gap_detection():
      # Página clasificada MIXED por width-only -> Nivel 2 gap-detection ejecutado.
      ...

  def test_jpmorgan_fused_pattern_preserved():
      # KIID JPMorgan/Amundi: el espaciado intra-columna NO debe alterarse para
      # no romper la capa L0-FUSED de srri_text.py v3.
      ...
  ```

- **Riesgos y mitigaciones:**

  | # | Riesgo | Severidad | Mitigación |
  |---|---|---|---|
  | R1 | Regex calibrados con `t_fused = text.replace(" ", "")` (JPMorgan/Amundi en `srri_text.py v3` y `_detect_entry_fee` v24/v25) podrían perder cobertura | Alta | Test específico (`test_jpmorgan_fused_pattern_preserved`) en Sub-fase 1A; comparación de matches FUSED en kiid_text actual vs aware sobre KIID JPMorgan del histórico |
  | R2 | Heurística calibrada sobre 5 PDFs puede no generalizar a layouts atípicos (3 columnas, sidebar) | Media | Q-DLA-03 ya ejecutado sobre 300 fondos confirma 12 signatures; layouts no previstos → fallback con log |
  | R3 | PyMuPDF nueva dependencia para `io.py` | Baja | `srri_v4_geometric.py` ya importa `fitz`; dependencia ya instalada |
  | R4 | Coste computacional adicional por fondo | Baja | PyMuPDF más rápido que pdfplumber; benchmark obligatorio en 1A; umbral aceptable: <50 ms overhead |
  | R5 | Detectores con dependencia ordinal entre secciones del KIID podrían confundirse al invertirse el orden de columnas | Media | Inspección de código completa de `kiid_parser.py` antes de Sub-fase 1B |
  | R6 | `Raw_KIID_Text` post-DLA distinto al previo en fondos CACHED | Baja | Aceptable: propagación progresiva vía `mark_stale_for_refresh` |

- **Disparadores de roll-back:**
  - Sub-fase 1C: cualquier regresión detectada en C-1 (atributo poblado pasa a NULL) o C-4 (errores de pipeline introducidos).
  - Post-despliegue: variación neta de cobertura agregada negativa.
  - Acción inmediata: `DLA_ENABLED = False`, análisis de causa, no progresar a Sub-fase 1D.

- **Métricas baseline pre-fix (capturar antes de Sub-fase 1C):**
  ```sql
  -- B-1: Cobertura de detectores principales pre-DLA
  SELECT
      SUM(CASE WHEN Type IS NULL THEN 1 ELSE 0 END) AS null_type,
      SUM(CASE WHEN Family IS NULL THEN 1 ELSE 0 END) AS null_family,
      SUM(CASE WHEN Entry_Fee_Pct IS NULL THEN 1 ELSE 0 END) AS null_entry,
      SUM(CASE WHEN Exit_Fee_Pct IS NULL THEN 1 ELSE 0 END) AS null_exit,
      SUM(CASE WHEN Ongoing_Charge IS NULL THEN 1 ELSE 0 END) AS null_oc,
      SUM(CASE WHEN Benchmark_Declared IS NULL THEN 1 ELSE 0 END) AS null_bm,
      SUM(CASE WHEN SRRI IS NULL THEN 1 ELSE 0 END) AS null_srri
  FROM fund_master;
  ```

- **Control SQL post-fix (Sub-fase 1D):**
  ```sql
  -- Comparar B-1 con métricas equivalentes post-DLA tras ciclo completo
  -- Criterio: variación neta agregada >= 0; ningún atributo individual con regresión > 5 fondos
  ```

- **Módulos:**
  - Nuevo: `proyecto1/core/dla_extractor.py`
  - Modificación: `proyecto1/core/io.py:extract_text_from_pdf_bytes()` (≤15 líneas)
  - Sin cambios: `kiid_parser.py`, `srri_text.py`, `srri_v4_geometric.py`, `classify_utils.py`, bloques de clasificación

- **Prioridad:** Alta. Bloqueante de Fase D (BL-51A residual, BL-55) porque la mejora upstream del DLA puede resolver parte de esos NULLs sin necesidad de añadir más patrones regex.

---

**BL-61 — Strategy/Replication inconsistente: 12 fondos (ROOT CAUSE INVESTIGATIVO REQUERIDO ANTES DEL FIX)**

- **Estado:** Detectado en el análisis post-sprint v3.3. 12 fondos tienen `Strategy='Indexado'` con `Replication_Method='ACTIVE'`.

- **Fondos afectados:**

  | Fondo | Nature | Bloque |
  |-------|--------|--------|
  | CARMIGNAC INVESTISSE.EUR A ACC | Renta Variable | RESTANTES |
  | CARMIGNAC INVESTISSEMENT E ACC | Renta Variable | RESTANTES |
  | DB GLOBAL EQ STRATEGY SC (×8 variantes) | Renta Variable | RENTA_VARIABLE |
  | JPMORGAN GLOBAL INCO.D ACC EU | Renta Variable | RENTA_VARIABLE |

- **Importancia:** Esta es la **REGLA INTER-1 del Principio #9** (`PRINCIPIOS_DISENO.md`), declarada de auto-corrección obligatoria. Su violación contradice el principio fundacional del proyecto.

- **Procedimiento de verificación de causa raíz (OBLIGATORIO antes de codificar el fix):**

  v3.3 dejó tres hipótesis abiertas. v3.4 las convierte en pasos secuenciales de verificación. Sonnet debe ejecutar estos pasos en orden y detenerse en el primero que aporte respuesta concluyente.

  **Paso 1 — Verificar invocación de `apply_semantic_validation` en el pipeline:**
  ```bash
  grep -n "apply_semantic_validation\|validate_strategy_replication\|validate_all_semantic_consistency" pipeline.py
  ```
  Resultado esperado para discriminar:
  - Si NO hay invocación → causa raíz **A**: el validador no se llama. Fix: añadir llamada en pipeline.py al final del flujo `classify_fund`, sobre el record completo.
  - Si hay invocación pero condicionada a un bloque concreto (ej. solo RESTANTES) → causa raíz **B**: alcance restringido. Fix: extender a todos los bloques.
  - Si hay invocación universal → continuar al Paso 2.

  **Paso 2 — Verificar la condición de auto-corrección en `validate_strategy_replication`:**
  ```bash
  grep -n -A 10 "def validate_strategy_replication" classify_utils.py
  ```
  Inspeccionar la condición. Si la condición es:
  ```python
  if strategy in ['Indexado', 'Pasivo'] and replication != 'PASSIVE':
  ```
  esta NO captura el caso `Replication_Method=NULL` cuando se evalúa con operador `!=` en Python (en Python `None != 'PASSIVE'` es True, así que sí cubre NULL). Pero si en SQL la condición se traduce con operador estricto que filtra NULL, sí puede haber escape.

  Verificación complementaria: ¿la corrección retorna correctamente o se descarta por la lógica del caller?
  ```bash
  grep -n -B 2 -A 5 "validate_strategy_replication" pipeline.py blocks/
  ```

  **Paso 3 — Verificar que el bloque RENTA_VARIABLE no emite `Replication_Method` distinto de `PASSIVE` cuando `Strategy='Indexado'`:**
  ```bash
  grep -n "Replication_Method\|Strategy" blocks/renta_variable.py | head -50
  ```
  Identificar dónde se asigna `Strategy='Indexado'` en este bloque y verificar qué se asigna simultáneamente como `Replication_Method`. Los 10 fondos `DB GLOBAL EQ STRATEGY SC` y `JPMORGAN GLOBAL INCO.D ACC EU` provienen de este bloque, lo que indica un defecto local.

  **Paso 4 — Verificar persistencia en `sqlite_writer`:**
  ```bash
  grep -n "Replication_Method" sqlite_writer.py
  ```
  Confirmar que el campo se escribe correctamente en el UPSERT y que `INSERT ... ON CONFLICT DO UPDATE SET ... COALESCE(...)` no está sobreescribiendo silenciosamente.

- **Fix preventivo según causa raíz identificada:**

  - **Si es A o B (validador no llamado o llamado parcialmente):** invocación universal en `pipeline.py` justo antes de `validate_classification_contract`:
    ```python
    # ── Validación semántica obligatoria (BL-61, REGLA INTER-1) ─────────
    from classify_utils import validate_all_semantic_consistency
    validation_result = validate_all_semantic_consistency(classification)
    if not validation_result['is_valid']:
        for err in validation_result['critical_errors']:
            log_info(f"[{isin}] BL-61 auto-correct: {err['rule']} — {err['message']}")
        classification = validation_result['corrected_record']
    for w in validation_result['warnings']:
        log_warning(f"[{isin}] {w['rule']}: {w['message']}")
    ```

  - **Si es la condición de la función (Paso 2 inconcluso):** ajustar para cubrir explícitamente NULL:
    ```python
    def validate_strategy_replication(strategy, replication):
        if strategy in ['Indexado', 'Pasivo']:
            if replication is None or replication != 'PASSIVE':
                return 'PASSIVE', "Auto-corrección a PASSIVE (coherencia con Strategy)"
        return replication, None
    ```

  - **Si es defecto en `blocks/renta_variable.py` (Paso 3):** corregir en origen — el bloque debe asignar `Replication_Method='PASSIVE'` simultáneamente con `Strategy='Indexado'`. La auto-corrección global queda como red de seguridad, pero el fix primario es local.

- **Control SQL post-fix:**
  ```sql
  SELECT COUNT(*) FROM fund_master
  WHERE Strategy IN ('Indexado','Pasivo') AND (Replication_Method IS NULL OR Replication_Method != 'PASSIVE');
  -- Objetivo: 0
  ```

- **Tests obligatorios:**
  ```python
  def test_bl61_strategy_indexado_replication_active_corrected():
      """BL-61: Strategy='Indexado' con Replication='ACTIVE' → auto-corregir a PASSIVE."""
      record = {'Strategy': 'Indexado', 'Replication_Method': 'ACTIVE'}
      result = validate_all_semantic_consistency(record)
      assert result['corrected_record']['Replication_Method'] == 'PASSIVE'
      assert any('Strategy-Replication' in e['rule'] for e in result['critical_errors'])

  def test_bl61_strategy_indexado_replication_null_corrected():
      """BL-61: Strategy='Indexado' con Replication=NULL → auto-corregir a PASSIVE."""
      record = {'Strategy': 'Indexado', 'Replication_Method': None}
      result = validate_all_semantic_consistency(record)
      assert result['corrected_record']['Replication_Method'] == 'PASSIVE'

  def test_bl61_strategy_activo_replication_active_unchanged():
      """BL-61: Strategy='Activo' con Replication='ACTIVE' → sin cambio."""
      record = {'Strategy': 'Activo', 'Replication_Method': 'ACTIVE'}
      result = validate_all_semantic_consistency(record)
      assert result['corrected_record']['Replication_Method'] == 'ACTIVE'
      assert result['is_valid'] is True
  ```

- **Módulos:** `pipeline.py` (invocación), `blocks/renta_variable.py` (origen probable), `classify_utils.py` (`validate_strategy_replication`).
- **Prioridad:** Alta. Bloqueante para progresión a P3 (calidad de clasificación impacta scoring régimen-dependiente).

---

**BL-59 — FAM_000261: Restantes mayoritario + única Nature concreta no se corrige**

- **Estado:** Detectado durante la validación post-BL-FAM-FIX en sprint v3.3. No estaba en la especificación original.

- **Fondos afectados:** FAM_000261 (BGF China Bond: 3 Restantes + 2 RFCP, total 5 miembros).

- **Causa raíz:** En `_resolve_family_nature` de `fund_family_builder.py`, cuando `Restantes` tiene mayoría (3/5 = 0.60 < 0.667), la Regla 2 no aplica. En Regla 3, `Restantes` queda excluido del ranking de calidad (`srri_without_restantes`). Al ser la única Nature concreta, `others_srri` resulta vacío → `srri_diff = 0`. Con `srri_diff >= 0` se intenta el desempate por DQ, pero `others_dq` también es vacío → nunca devuelve corrección. El caso límite "Restantes mayoritario pero la única Nature concreta presenta todos los miembros con calidad no baja" no está cubierto.

- **Fix especificado:**

  En `_resolve_family_nature`, dentro del bloque `if _is_adjacent_pair(nature_set)`, tras excluir Restantes del ranking y calcular `srri_without_restantes`, añadir:

  ```python
  # Caso límite BL-59: única Nature concreta (todos los demás son Restantes)
  # → corregir directamente, no hay comparación de calidad posible ni necesaria.
  if len(srri_without_restantes) == 1:
      sole_nature = next(iter(srri_without_restantes))
      to_correct = [m["ISIN"] for m in members
                    if m["Fund_Nature"] == "Restantes"]
      return sole_nature, to_correct
  ```

  **Posición de inserción:** inmediatamente antes de la línea `best_nature = max(srri_without_restantes, ...)`.

- **Justificación (Principio #1 — root cause):** cuando una familia tiene una única Nature concreta y el resto son Restantes, la corrección es unívoca independientemente del número de miembros ni de las calidades relativas. No existe ambigüedad semántica posible.

- **Control SQL post-fix:**
  ```sql
  -- FAM_000261 debe resolverse
  SELECT fund_family_id, GROUP_CONCAT(DISTINCT Fund_Nature), COUNT(*)
  FROM fund_master
  WHERE fund_family_id = 'FAM_000261'
  GROUP BY fund_family_id;
  -- Esperado: una sola Nature = 'Renta Fija Corto Plazo'

  -- No deben aparecer nuevas familias Restantes-mayoritarias corregidas incorrectamente
  SELECT fund_family_id, GROUP_CONCAT(DISTINCT Fund_Nature), COUNT(*)
  FROM fund_master
  WHERE fund_family_id IS NOT NULL
  GROUP BY fund_family_id
  HAVING COUNT(DISTINCT Fund_Nature) > 1;
  -- Esperado: ≤7 filas (FAM_000261 resuelta; los otros 7 son justificados)
  ```

- **Tests a añadir en `test_fund_family_builder_resolve.py`:**
  ```python
  def test_bl59_restantes_majority_sole_concrete_nature_corrected():
      """BL-59: 3 Restantes + 2 RFCP → RFCP gana, Restantes se corrigen."""
      members = [
          _m("I1", "BGF China Bond A", "Renta Fija Corto Plazo", dq="OK", sq="HIGH"),
          _m("I2", "BGF China Bond B", "Renta Fija Corto Plazo", dq="WARN", sq="LOW_CONFLICT"),
          _m("I3", "BGF China Bond C", "Restantes", dq="OK", sq="HIGH"),
          _m("I4", "BGF China Bond D", "Restantes", dq="OK", sq="HIGH"),
          _m("I5", "BGF China Bond E", "Restantes", dq="OK", sq="HIGH"),
      ]
      nature, to_fix = _resolve_family_nature(members)
      assert nature == "Renta Fija Corto Plazo"
      assert set(to_fix) == {"I3", "I4", "I5"}

  def test_bl59_restantes_majority_two_concrete_natures_not_auto_corrected():
      """Con dos Natures concretas distintas + Restantes, no aplicar BL-59 (sin Nature única)."""
      members = [
          _m("I1", "X", "Mixtos",         dq="OK", sq="HIGH"),
          _m("I2", "Y", "Renta Variable", dq="OK", sq="HIGH"),
          _m("I3", "Z", "Restantes",      dq="OK", sq="HIGH"),
          _m("I4", "W", "Restantes",      dq="OK", sq="HIGH"),
      ]
      nature, to_fix = _resolve_family_nature(members)
      # Dos Natures concretas → no es caso BL-59, debe caer a lógica normal
      assert nature in (None, "Mixtos", "Renta Variable")
  ```

- **Módulo:** `core/fund_family_builder.py` — función `_resolve_family_nature`.
- **Prioridad:** Alta. Solo afecta a 1 familia conocida pero el patrón puede repetirse con nuevos fondos.

---

**BL-49 — Currency_Hedged: detección directa en texto KIID**

- **Estado:** Implementación parcial. La firma `detect_currency_hedged(name_l, kiid_text)` ya admite `kiid_text` desde v18 (`fund_characterizer.py` línea 514), pero **el cuerpo de la función no contiene patrones de extracción sobre `kiid_text`**. La inferencia indirecta `Hedging_Policy → Currency_Hedged` (pipeline v25 líneas 1021–1044) ha aportado parte de la cobertura, pero los 535 NULL persisten (16,70%).

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
  6. **Coordinación con BL-31:** la regla de prevalencia "Hedging_Policy gana sobre Currency_Hedged" en `classify_utils.py` línea 2502 debe seguir aplicándose al final del pipeline, después de la extracción ampliada.

- **Módulo:** `fund_characterizer.py` — función `detect_currency_hedged()` y nueva función auxiliar `_detect_ch_from_kiid_text()`.

- **Control SQL post-fix:**
  ```sql
  SELECT COUNT(*) FROM fund_master
  WHERE Fund_Currency IN ('USD','GBP','CHF','JPY','CNH')
    AND Currency_Hedged IS NULL;
  -- Objetivo: reducción significativa desde 267 (al menos −50%)

  SELECT COUNT(*) FROM fund_master WHERE Currency_Hedged IS NULL;
  -- Objetivo: bajada desde 535 al rango ≤ 400
  ```

---

**BL-50 — Inferencia INTER Investment_Universe / Geography (7 residuales)**

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

### Media prioridad

---

**BL-58 — Patrón de constantes canónicas: emisores latentes restantes**

- **Estado:** Abierto tras BL-57 v3. El patrón está establecido. Los siguientes emisores emiten literales Family inline que deberían migrar a constantes.

- **Emisores latentes identificados (0 fondos afectados actualmente):**

  | Fichero | Línea aprox. | Literal | Constante propuesta |
  |---------|-------------|---------|---------------------|
  | `blocks/mixtos.py` | ~126 | `"Lifecycle"` | `FAMILY_LIFECYCLE` |
  | `blocks/mixtos.py` | ~128 | `"Retirement"` | `FAMILY_RETIREMENT` |
  | `fund_characterizer.py` | ~680 | `"Fixed Income"` / `"Renta Fija Corto"` | `FAMILY_RFF_CANONICAL` / `FAMILY_RFCP_CANONICAL` |

- **Refactors pendientes en `classify_utils.py`:**
  - `ALLOWED_FAMILY_BY_NATURE`: sustituir literales inline por constantes importadas.
  - `_DEFAULT_FAMILY_BY_NATURE`: ídem.
  - Nueva función `_validate_family_catalog_consistency()`: al importar el módulo, verificar que todo literal en `ALLOWED_FAMILY_BY_NATURE` tiene una constante canónica definida. Falla rápido en desarrollo, transparente en producción.

- **Procedimiento de implementación recomendado:**
  1. `grep -rn "Lifecycle\|Retirement" blocks/ fund_characterizer.py classify_utils.py` para confirmar las posiciones exactas (las líneas en la tabla son aproximadas).
  2. Definir constantes en `classify_utils.py` siguiendo el patrón establecido para `FAMILY_INCOME_ORIENTED`.
  3. Importar desde cada emisor.
  4. Mantener `FAMILY_TRANSLATION_MAP` con entradas identidad como red de seguridad.

- **Prioridad:** Media. No hay fondos afectados actualmente; los literales `"Lifecycle"` y `"Retirement"` no tienen fondos activos en la BD (ver distribución Family). Ejecutar en el próximo sprint de `classify_utils.py`.

---

**BL-60 — 5 bipartitas con empate total de calidad: no determinables por diseño**

- **Estado:** FAM_000945, FAM_000946 (DWS Multi Opp), FAM_001778 (M&G Optimal Income), FAM_002124 (PIMCO Inflation), FAM_002320 (SISF Global Credit). Todos tienen ambos miembros con `DQ=OK` y `SRRI_Q=HIGH`.

- **Análisis de cada caso:**

  | FAM | Natures | Patrón |
  |-----|---------|--------|
  | FAM_000945 | Mixtos / RFCP | DWS Multi Opp TFC — misma clase, mismo nombre |
  | FAM_000946 | Mixtos / RFCP | DWS Multi Opp TFD — misma clase, mismo nombre |
  | FAM_001778 | Mixtos / RFF | M&G Optimal Income — ACC vs INC del mismo fondo |
  | FAM_002124 | Mixtos / RFF | PIMCO Inflation — ACC vs INC del mismo fondo |
  | FAM_002320 | RFCP / RFF | SISF Glob Credit — ACC vs INC del mismo fondo |

- **Causa raíz:** Dos clases del mismo fondo fueron clasificadas en Natures distintas por el pipeline con máxima calidad en ambas. Es un error de clasificación en la fuente (bloques), no un problema de `fund_family_builder`.

- **Criterio adicional analizado:** SRRI numérico podría discriminar (ej: SRRI=3 → RFCP, SRRI=5 → Mixtos). Pendiente de verificar si las dos clases del mismo fondo tienen SRRI distinto o idéntico.

  ```sql
  SELECT Fund_Name, Fund_Nature, SRRI, SRRI_Quality_Flag
  FROM fund_master
  WHERE fund_family_id IN ('FAM_000945','FAM_000946','FAM_001778','FAM_002124','FAM_002320')
  ORDER BY fund_family_id, Fund_Name;
  ```

- **Acción recomendada:** Si los dos miembros tienen SRRI distinto, implementar criterio SRRI numérico en Regla 3 como tercer desempate (tras SRRI_Quality y DQ). Si el SRRI es idéntico, el problema es de clasificación en los bloques emisores y debe corregirse allí.

- **Prioridad:** Media. Solo 5 familias. El comportamiento actual (no determinable) es conservador y correcto.

---

**BL-51A — Entry/Exit fee residuales**

- **Estado:**
  - Problema A (patrones nuevos en v24): ✅ desplegado. Reducción real `Exit_Fee_Pct` NULL: 735 → 676 (−59).
  - Problema A residual: 115 fondos siguen con `Entry_Fee_Pct=NULL ∧ Fee_Known_Flag='NOT_FOUND'` y 676 con `Exit_Fee_Pct=NULL`.

- **Diagnóstico cuantitativo:**
  ```sql
  -- Entry fee residual NOT_FOUND
  SELECT COUNT(*) FROM fund_master
  WHERE Entry_Fee_Pct IS NULL AND Fee_Known_Flag='NOT_FOUND';
  -- Resultado: 115

  -- Exit fee residual sin clasificar como NOT_FOUND
  SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
  WHERE Exit_Fee_Pct IS NULL
  GROUP BY Fee_Known_Flag ORDER BY 2 DESC;
  -- 676 distribuidos entre EXTRACTED (parser detectó algo y no extrajo) y otros
  ```

- **Causa raíz Problema A residual entry_fee:**
  Los 115 fondos `NOT_FOUND` tienen formulaciones aún no cubiertas. Antes de añadir patrones, **es obligatorio una sesión de muestreo manual** sobre `Raw_KIID_Text` de 20 ISIN aleatorios del subconjunto, y agruparlos por gestora.

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

- **Módulo:** `kiid_parser.py` v25 (nuevos patrones).

- **Control SQL post-fix:**
  ```sql
  SELECT COUNT(*) FROM fund_master
  WHERE Entry_Fee_Pct IS NULL AND Fee_Known_Flag='NOT_FOUND';
  -- Objetivo: reducción desde 115 a ≤50
  ```

---

**BL-53 — Inconsistencias lingüísticas intra-atributo (causa raíz arquitectónica)**

- **Estado:** La auditoría sobre el export del 23/04/2026 confirma 4 atributos con inconsistencias:

  | Atributo | Idioma objetivo | Inconsistencia detectada |
  |----------|-----------------|--------------------------|
  | `Family` | Español | ✅ RESUELTO en BL-57 v3 (104 fondos a 'Orientado a Renta') |
  | `Sector_Focus` | Español (decisión clase) | 20 fondos con valores en inglés (`'Real Assets'` 11, `'Energy & Resources'` 6, `'Utilities & Environment'` 2, `'Healthcare & Life Sciences'` 1) — coexisten con sus equivalentes españoles. **Defecto técnico**, no de diseño. |
  | `Subtype` | Mixto por diseño | 11 valores en inglés (`Standard MMF`, `VNAV/LVNAV/CNAV`, `ETF`, `Floating Rate Notes`, `Long/Short`, `Global Macro`, `Total Return Bond`). Mayoría son acrónimos/regulación → decisión: **mantener excepción y documentar**. |
  | `Type` | Español con excepciones | `'Allocation'`, `'Absolute Return'`, `'Total Return'`, `'Tactical Allocation'`, `'Target Maturity'`, `'Floating Rate CP'`, `'Materias Primas'` (este último ya en español tras fix). Excepciones documentadas en `TYPE_TRANSLATION_MAP` deben formalizarse. |

- **Causa raíz arquitectónica (Sector_Focus):** Existen **dos puntos de emisión paralelos**:
  - `fund_characterizer.detect_sector_focus()` línea 341 emite `'Real Assets'` directamente (en inglés) mientras que el resto de valores están en español. El resto del catálogo se emite en español.
  - `pipeline.py` líneas 758–776 mapea `Theme→Sector_Focus` con valores hardcoded en español.
  - `classify_utils.normalize_sector_focus()` aplica el mapa de traducción **solo cuando se invoca `enrich_classification()`** (línea 1620), que **no se invoca en el pipeline post-`characterize_fund`**. Verificable en `pipeline.py` líneas 395–418 (no hay llamada a enrich).

  Por eso los 20 fondos con valor en inglés son aquellos cuya emisión proviene de `fund_characterizer.detect_sector_focus` y no son corregidos por nadie corriente abajo.

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

**BL-54 — Centralización del mapeo Theme→Sector_Focus (root cause sistémico)**

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

     def map_theme_to_sector_focus(theme):
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

**BL-55 — Registro explícito de Exit_Fee_Pct=0.00 para declaraciones implícitas**

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

**BL-56 — Invocación de enrich_classification / apply_language_homogeneity post-characterize (transversal)**

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

### Baja prioridad / futura

---

**BL-48-ext — Normalización Family en Monetarios JPMorgan**

- 18 fondos JPMorgan con `Family=LVNAV/VNAV/CNAV`. Subtype ya captura el matiz.
- **Prerequisito:** confirmar independencia de consumidores P2/P3.

---

**BL-47-ext — SFDR Art. 8 como default defensivo**

- BL-47 cerrado pero estrategia defensiva sin documentar formalmente.
- **Acción:** evaluar integración con fuente externa ESMA.

*Nota v3.4: este enunciado es el original de v3.2. v3.3 lo había renombrado a "SFDR Article 6 completitud en fondos no-ESG" sin justificación documentada. Se restaura el enunciado original; cualquier cambio de alcance debe registrarse explícitamente.*

---

**BL-51 Problema B — Estructura mixta cap/floor**

- Esquema decidido en `BL51_SCHEMA_DECISION.md`, prevalencia diagnóstica pendiente.
- **Prerequisito:** ejecutar query de prevalencia (sección 6 del documento referenciado).
- **Decisión registrada:** schema antes de implementar; impacto transversal P1/BD/P3.
- **Tipado decidido:** 4 campos REAL para cap/floor en lugar de campo textual `Fee_Structure_Notes` (tipado fuerte consultable por P3).

*Nota v3.4: item recuperado. v3.3 lo había omitido del listado de items abiertos, manteniéndolo solo como entrada en la tabla de decisiones. Restaurado.*

---

**P2 — Factores macro adicionales**

- **Series FRED pendientes de incorporar:**
  - `BAMLH0A0HYM2` (HY spread)
  - `VIXCLS` (VIX)
  - `T10Y2YM` (term spread)
- **Estado:** Infraestructura de descarga, normalización y almacenamiento en SQLite ya existente para los 17 factores actuales (DXY, gold proxy PPICMM, M2 Global, BoJ rate y otros). La incorporación de las 3 series adicionales es extensión incremental sobre `macro_sensitivity.py` y el módulo `m2_global_builder.py` (este último solo si se construye un agregado nuevo).
- **Independencia con P1:** la ingesta de series FRED no toca clasificación de fondos. Puede progresar en paralelo con cualquier sprint P1.
- **Acción posterior dependiente de P1:** las regresiones OLS sobre los nuevos factores y la construcción del dataset etiquetado por régimen requieren clasificación P1 consolidada (BL-61, BL-49, BL-50 cerrados como mínimo).

*Nota v3.4: item recuperado. v3.3 lo había omitido enteramente del backlog. Esta omisión y la de P3 son las que motivaron el saneamiento documental que ha producido v3.4.*

---

**P3 — Scoring régimen-dependiente**

- **Estado:** Framework de cinco fases diseñado, no implementado.
- **Las cinco fases:**
  1. Construir etiquetas de régimen sobre el histórico macro.
  2. Análisis descriptivo por régimen (rendimiento, volatilidad, drawdown por Nature/Family/Geography).
  3. Derivar pesos empíricos de scoring por régimen.
  4. Implementar el scoring dinámico en función del régimen vigente.
  5. Construir reglas de rotación entre regímenes.
- **Pesos empíricos por régimen:** pendiente del dataset etiquetado producido en P2.
- **Dependencia con P1:** todas las fases dependen de la calidad de clasificación P1. El scoring filtra y pondera fondos por atributos categóricos (`Profile`, `Family`, `Geography`, `Style_Profile`, `Currency_Hedged`); inconsistencias en P1 se amplifican en P3.
- **No iniciar antes de:** BL-61 cerrado (REGLA INTER-1 violada actualmente), BL-49 cerrado (16,7% NULLs en Currency_Hedged afectan filtros de cobertura), BL-50 cerrado (9,55% NULLs en Geography afectan filtros geográficos por régimen).

*Nota v3.4: item recuperado. v3.3 lo había omitido enteramente del backlog.*

---

## 4. GAPS ESTRUCTURALES — LÍMITE REAL DE EXTRACCIÓN

| Atributo | NULL actual | NULL% | Naturaleza | Acción |
|----------|-------------|-------|------------|--------|
| `Subtype` | 2.934 | 91,6% | Gran mayoría de natures×types sin variante estructural diferenciable. 270 con valor es cobertura correcta post-BL-43. | Ninguna activa |
| `Sector_Focus` | 2.830 | 88,3% | Solo fondos sectoriales (~374). Cobertura correcta. Límite real. **20 inconsistencias EN/ES residuales (BL-53/54).** | **BL-53/BL-54** |
| `Market_Cap_Focus` | 1.384 | 43,2% | Estable post-BL-27-ext. Los non-RV son correctamente NULL. | Ninguna |
| `Benchmark_Declared` | 1.472 | 45,9% | Fondos sin benchmark detectable en KIID. Límite estructural. | Ninguna |
| `Sfdr_Article` | 1.156 | 36,1% | 386 fondos pre-PRIIPs genuinos + ~770 sin declaración. | Ninguna (BL-47-ext) |
| `Currency_Hedged` | 535 | 16,7% | Bolsa accionable en USD/GBP/CHF/JPY/CNH (~267 fondos). | **BL-49** |
| `Style_Profile` | 870 | 27,2% | Estable post-BL-41-ext. Residual sin señal explícita. | Ninguna |
| `Exit_Fee_Pct` | 676 | 21,1% | **Ceros implícitos no formalizados.** | **BL-51A + BL-55** |
| `Hedging_Policy` | 593 | 18,5% | Estable post-BL-45. Sin señal explícita en KIID. | Ninguna |
| `Accumulation_Policy` | 394 | 12,3% | KIIDs pre-2015 o texto OCR degradado. Límite real. | Ninguna |
| `Geography` | 306 | 9,6% | Mayoría sin señal geográfica. Gap accionable via inferencia INTER. | **BL-50** |
| `Investment_Universe` | 203 | 6,3% | Gap accionable via inferencia INTER. | **BL-50** |
| `Entry_Fee_Pct` | 129 | 4,0% | 115 NOT_FOUND residuales. | **BL-51A** |

---

## 5. CAUSAS RAÍCES SISTÉMICAS DOCUMENTADAS

### 5.1 Doble punto de emisión sin normalización centralizada (heredado v3.2)

**Descripción:**
- Atributos como `Sector_Focus` se emiten desde **dos puntos paralelos** (`fund_characterizer.detect_sector_focus` y `pipeline.py` mapeo Theme→Sector_Focus inline).
- La función centralizadora `normalize_sector_focus` existe pero **solo se invoca desde `enrich_classification`**, que **no se llama en pipeline post-characterize**.
- Resultado: 20 fondos con valores en inglés sobreviven al ciclo.

**Solución estructural:** BL-54 (mapa único Theme→Sector_Focus) + BL-56 (invocación centralizada de normalización post-characterize).

**Principio derivado:**
> Todo atributo que pueda emitirse desde más de un punto del pipeline (ej. clasificador especializado + inferencia post-clasificación) debe tener una **función agregadora canónica** en `classify_utils.py` que se invoque obligatoriamente como último paso antes de la persistencia. La existencia de mapas o lógicas paralelas (incluso "equivalentes") es una violación del Principio #2 (DRY) y crónicamente genera deriva semántica.

### 5.2 Antipatrón BL-57: desincronización emisor-validador (heredado v3.3)

**Descripción del incidente:**
La migración BL-57 v2 (25-abr-2026) actualizó `ALLOWED_FAMILY_BY_NATURE` (validador INTER-5) y los normalizadores SQL de `sqlite_writer.py` para aceptar `'Orientado a Renta'` en lugar de `'Income Oriented'`. Sin embargo, no se localizó el emisor primario: `blocks/mixtos.py:130` seguía emitiendo `'Income Oriented'`. El validador rechazó el literal viejo; la autocorrección P07 lo redirigió silenciosamente al default `'Mixtos'`. Resultado: 104 fondos perdieron granularidad (`Family='Mixtos'` en lugar de `'Orientado a Renta'`) sin ningún warning en el log.

**Norma BL-57 v3 (vigente desde 26-abr-2026):**
Antes de cambiar cualquier valor canónico en `ALLOWED_FAMILY_BY_NATURE`, `_DEFAULT_FAMILY_BY_NATURE`, `FAMILY_TRANSLATION_MAP` o normalizadores SQL:

1. **Localizar todos los emisores** del literal que se modifica en el codebase completo (`grep -rn "literal_viejo"`).
2. **Actualizar todos los emisores simultáneamente** en el mismo commit.
3. **Definir una constante canónica** en `classify_utils.py` e importarla desde todos los emisores.
4. **Mantener la red de seguridad** en `FAMILY_TRANSLATION_MAP` (entrada identidad del literal legacy) aunque el emisor ya use la constante correcta.

### 5.3 Límite del modelo de calidad en `fund_family_builder` (heredado v3.3)

Los 5 casos bipartitos no resueltos (BL-60) demuestran que `SRRI_Quality_Flag` y `Data_Quality_Flag` son insuficientes como únicos criterios de desempate cuando ambas clases tienen extracción de máxima calidad. En ese escenario, el error está **aguas arriba**: en el bloque clasificador que asignó Natures distintas a dos clases del mismo fondo. `fund_family_builder` no puede ni debe corregir errores de clasificación cuando no tiene señal objetiva de cuál es la correcta. La solución estructural es mejorar la coherencia de clasificación en los bloques, no añadir heurísticas de nombre en `fund_family_builder` (que violaría el Principio #1 al parchear síntomas sin eliminar la causa).

### 5.4 Documento backlog no autocontenido (causa raíz documental, identificada y corregida en v3.4)

**Síntoma:** v3.3 omitió silenciosamente items heredados de v3.2 (P2, P3, BL-51 Problema B) y delegó las especificaciones de 6 items abiertos a "ver v3.2 sección 3", convirtiendo el backlog en un documento inútil para implementación sin acceso simultáneo al archivo anterior.

**Causa raíz:** ausencia de norma de redacción del backlog que especificara la obligación de autocontención.

**Norma vigente desde v3.4:** cada versión del backlog debe ser autocontenida. Las especificaciones de items abiertos heredados de versiones anteriores deben copiarse íntegramente. Una sección "Diff vs versión anterior" al inicio sustituye a la delegación inline.

### 5.5 Calidad upstream antes que cobertura downstream (identificada en v3.5)

**Descripción:**
La acumulación de 23 prioridades en `_detect_entry_fee` (v24/v25), la capa L0-FUSED de `srri_text.py v3` con su `t_fused = text.replace(" ", "")` específico para JPMorgan/Amundi, y la ventana acotada de ±1500 chars en BL-55/2 son **síntomas de una causa raíz común que ninguno aborda**: el componente upstream `proyecto1/core/io.py:extract_text_from_pdf_bytes` está perdiendo información estructural del PDF al serializar layout 2D (dos columnas, tablas) en orden 1D estricto por coordenada Y.

**Evidencia cuantitativa (BL-DLA-0):**
- 88,3% del corpus (n=300 muestra) tiene al menos una página con layout 2-columnas mal serializado.
- Solo 11,3% del corpus está libre de patología (layout `S,S,S`).
- 7,7% tiene al menos una página clasificada como MIXED por la heurística primaria.

**Solución estructural:** BL-DLA-1 (Fase 1) ataca esta causa raíz introduciendo serialización 2D-aware en `dla_extractor.py` upstream, beneficiando a TODOS los detectores downstream (`kiid_parser.py`, `srri_text.py`, bloques de clasificación) sin tocarlos.

**Principio derivado:** documentado como nuevo principio en sección 7 (introducido en v3.5).

---

## 6. MÓDULOS DESPLEGADOS — VERSIONES VIGENTES

| Módulo | Versión | Cambios principales | Estado |
|--------|---------|---------------------|--------|
| `pipeline.py` | **v25** | BL-50 catálogos ampliados (Country/Regional/Liquidity), BL-52 corrección semántica Country↔Región | **DESPLEGADO** |
| `kiid_parser.py` | **v24** | BL-51A: 6 patrones entry fee (3 ZERO + 3 PCT), 4 patrones exit fee (3 ZERO + 1 PCT). Separador decimal opcional | **DESPLEGADO** |
| `classify_utils.py` | v4 + BL-57v3 | BL-19/22/23/24/30/31/32/33 + constante `FAMILY_INCOME_ORIENTED` | **DESPLEGADO** |
| `fund_characterizer.py` | v18 | BL-26/27/28/29/49 (firma con kiid_text) | **DESPLEGADO** |
| `benchmark_normalizer.py` | vBL-39 | +20 aliases, +9 false positives | **DESPLEGADO** |
| `restantes.py` | v4 | BL-09/20/21 | **DESPLEGADO** |
| `blocks/mixtos.py` | post-BL-57v3 | Import de `FAMILY_INCOME_ORIENTED`, eliminado literal inline | **DESPLEGADO** |
| `fund_family_builder.py` | post-BL-FAM-FIX | D1 `_UNIVERSAL_ADJACENT`, D2 Regla 2-bis, D3 Regla 3 con desempate DQ, D4 regex `inc` sin `ome` | **DESPLEGADO** |

**Próximas versiones a producir según especificaciones de este backlog:**

| Módulo | Versión objetivo | BL involucrados | Prioridad |
|--------|------------------|-----------------|-----------|
| `pipeline.py` | v26 | BL-50 dirección inversa (Universe='Global'/'Liquidity' → Geography), BL-54 (uso del mapa centralizado), BL-56 (invocación post-characterize), BL-61 (invocación universal de `validate_all_semantic_consistency`) | Alta |
| `classify_utils.py` | v5 | BL-54 (THEME_TO_SECTOR_FOCUS_MAP centralizado), BL-56 (apply_post_characterize_normalization), BL-58 (constantes Lifecycle/Retirement), BL-61 (refinar `validate_strategy_replication` para cubrir NULL si Paso 2 lo confirma) | Alta |
| `fund_characterizer.py` | v19 | BL-49 (cuerpo de `detect_currency_hedged`), BL-53 ('Real Assets' → 'Activos Reales'), BL-54 (refactor `detect_sector_focus`) | Alta |
| `kiid_parser.py` | v25 | BL-51A residual (115 entry NOT_FOUND), BL-55 (Exit_Fee_Pct=0 implícito + EXIT_INFERRED_ZERO) | Alta |
| `fund_family_builder.py` | post-BL-59 | BL-59 (caso límite Restantes mayoritario única Nature concreta) | Alta |
| `blocks/renta_variable.py` | revisión BL-61 | Asegurar emisión coherente de `Replication_Method='PASSIVE'` cuando `Strategy='Indexado'` (si Paso 3 lo confirma como causa raíz) | Alta |
| `proyecto1/core/dla_extractor.py` | **v1 (NUEVO)** | BL-DLA-1 Sub-fase 1A: clasificación layout por página (width-only + gap-detection si MIXED), serialización 2D-aware | Alta |
| `proyecto1/core/io.py` | v2 (modificación quirúrgica) | BL-DLA-1 Sub-fase 1B: integración kill-switch `DLA_ENABLED`, fallback a comportamiento actual | Alta |

---

## 7. PRINCIPIOS DE DISEÑO CONSOLIDADOS

### Principios meta-nivel (vigentes en `PRINCIPIOS_DISENO.md`)

- **Principio #1 — Root Cause Analysis > Parches de Síntomas.** Toda solución debe atacar la causa raíz, no el síntoma. Aplicado en BL-54, BL-56, BL-59, BL-61, y en la propia regeneración de v3.4.
- **Principio #2 — Escalabilidad y DRY.** Lógica replicada en múltiples módulos debe centralizarse en `classify_utils.py`. Aplicado en BL-54 (mapa Theme→Sector_Focus), BL-56 (función agregadora post-characterize), BL-57v3 (constante `FAMILY_INCOME_ORIENTED`), BL-58 (constantes Lifecycle/Retirement).
- **Principio #8 — Homogeneidad lingüística.** Cada columna en su idioma objetivo (definido en `PRINCIPIOS_DISENO.md`).
- **Principio #9 — Consistencia semántica INTRA e INTER.** REGLA INTER-1 (Strategy↔Replication) actualmente violada en 12 fondos: ver BL-61.

### Principios específicos heredados de v3.1/v3.2

- **Principio de valores semánticos explícitos sobre NULL** (Subtype, Style_Profile, Market_Cap_Focus).
- **Principio de extracción en cascada**: nombre → KIID, para atributos con NULL > 2%.
- **Principio de inferencia INTER para nulos geográficos**: Geography ↔ Investment_Universe.
- **Principio de toda corrección INTER opera sobre valor efectivo (record OR BD)**: por COALESCE en `sqlite_writer`.

### Principios introducidos en v3.2

- **Principio de punto único de emisión por atributo (DRY estructural).** Cualquier atributo que pueda asignarse desde más de un punto del código debe tener una **función canónica única** en `classify_utils.py` que centralice la lógica. Los puntos de invocación deben llamar a esa función en lugar de replicar la lógica inline. Aplicación: BL-54, BL-56.
- **Principio de declaración implícita formalizada.** Cuando un atributo puede asumirse cero/ausente por **estructura del documento fuente** (sección estructurada de costes sin mención de la comisión, tabla con celda vacía, declaración negativa indirecta), el parser debe formalizar el valor como `0.00` con un flag de trazabilidad (`EXIT_INFERRED_ZERO`) en lugar de mantener NULL. Aplicación: BL-55.

### Principio introducido en v3.4

- **Principio de autocontención del backlog.** Cada versión del backlog debe ser autocontenida. Las especificaciones de items abiertos heredados de versiones anteriores deben copiarse íntegramente, no referenciarse mediante "ver v.X". La omisión silenciosa de items heredados constituye violación documental del Principio #1. Una sección "Diff vs versión anterior" al inicio sustituye la delegación inline.

### Principio introducido en v3.5

- **Principio de calidad upstream antes que cobertura downstream.** Cuando un atributo presenta cobertura insuficiente, antes de añadir nuevos patrones regex en el detector correspondiente, verificar si la causa raíz es la calidad de la entrada que el detector recibe. Una mejora upstream que beneficie a N detectores es preferible a N parches downstream que solo benefician a uno cada uno. La acumulación de niveles de prioridad en un detector individual (ej. 23 prioridades en `_detect_entry_fee`) es señal de que la causa raíz puede estar aguas arriba. Aplicación documentada: BL-DLA-1 (sustitución del extractor de texto upstream para resolver la patología 2-columnas que afecta a 88,3% del corpus).

---

## 8. QUERIES DE VALIDACIÓN COMPLETAS

```sql
-- ── ITEMS RESUELTOS (deben devolver 0) ──────────────────────────────────
SELECT 'BL-19' AS bl, COUNT(*) AS n FROM fund_master WHERE Fund_Nature='Mixto'
UNION ALL SELECT 'BL-26', COUNT(*) FROM fund_master WHERE Currency_Hedged IN ('Yes','No')
UNION ALL SELECT 'BL-28', COUNT(*) FROM fund_master WHERE Credit_Quality='No aplica'
UNION ALL SELECT 'BL-30', COUNT(*) FROM fund_master WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL
UNION ALL SELECT 'BL-31', COUNT(*) FROM fund_master
  WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED')
     OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED')
UNION ALL SELECT 'BL-32', COUNT(*) FROM fund_master WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL
UNION ALL SELECT 'BL-33', COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL
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
                      'África','Oriente Medio','América del Norte')
UNION ALL SELECT 'BL-57v3-A', COUNT(*) FROM fund_master WHERE Family='Income Oriented';
-- Todos deben devolver 0.

-- ── BL-57 v3: Validaciones de cierre ────────────────────────────────────
SELECT COUNT(*) FROM fund_master WHERE Family='Orientado a Renta';
-- Resultado esperado: 104

-- ── BL-FAM-FIX D4: Templeton / GS Income ────────────────────────────────
SELECT Fund_Name, fund_family_id FROM fund_master
WHERE Fund_Name LIKE 'TEMPLETON GLOBAL%'
ORDER BY fund_family_id;
-- Resultado: FAM_001293 (Templeton Global A INC/RV), FAM_001294-1298 distintos
-- "TEMPLETON GLOBAL INCOME A ACC" → FAM_001297 (distinto de los sin Income)

SELECT Fund_Name, fund_family_id FROM fund_master
WHERE Fund_Name LIKE 'GS GBL EQ%'
ORDER BY fund_family_id;
-- Resultado: FAM_001344 (GS GBL EQ INC) ≠ FAM_001345 (GS GBL EQ INCOME)

-- ── FAMILIAS INCONSISTENTES RESIDUALES ──────────────────────────────────
SELECT fund_family_id, GROUP_CONCAT(DISTINCT Fund_Nature) AS natures, COUNT(*) AS n
FROM fund_master
WHERE fund_family_id IS NOT NULL
GROUP BY fund_family_id
HAVING COUNT(DISTINCT Fund_Nature) > 1
ORDER BY fund_family_id;
-- Resultado actual: 8 familias — todas justificadas
-- Post BL-59: debe bajar a 7 (FAM_000261 corregida)

-- ── BL-61: Strategy-Replication inconsistente ───────────────────────────
SELECT COUNT(*) FROM fund_master
WHERE Strategy IN ('Indexado','Pasivo')
  AND (Replication_Method IS NULL OR Replication_Method != 'PASSIVE');
-- Resultado actual: 12 — PENDIENTE CORRECCIÓN
-- Objetivo post-fix: 0

-- ── COBERTURA — seguimiento de progreso ─────────────────────────────────
SELECT 'OC_null'              AS attr, COUNT(*) AS n FROM fund_master WHERE Ongoing_Charge IS NULL
UNION ALL SELECT 'entry_NOT_FOUND',    COUNT(*) FROM fund_master WHERE Fee_Known_Flag='NOT_FOUND' AND Entry_Fee_Pct IS NULL
UNION ALL SELECT 'AP_null',            COUNT(*) FROM fund_master WHERE Accumulation_Policy IS NULL
UNION ALL SELECT 'HP_null',            COUNT(*) FROM fund_master WHERE Hedging_Policy IS NULL
UNION ALL SELECT 'CH_null',            COUNT(*) FROM fund_master WHERE Currency_Hedged IS NULL
UNION ALL SELECT 'CH_nonEUR_null',     COUNT(*) FROM fund_master WHERE Fund_Currency IN ('USD','GBP','CHF','JPY','CNH') AND Currency_Hedged IS NULL
UNION ALL SELECT 'Geography_null',     COUNT(*) FROM fund_master WHERE Geography IS NULL
UNION ALL SELECT 'Universe_null',      COUNT(*) FROM fund_master WHERE Investment_Universe IS NULL
UNION ALL SELECT 'Universe_no_Geo',    COUNT(*) FROM fund_master WHERE Investment_Universe IS NOT NULL AND Geography IS NULL
UNION ALL SELECT 'Style_null',         COUNT(*) FROM fund_master WHERE Style_Profile IS NULL
UNION ALL SELECT 'Exit_null',          COUNT(*) FROM fund_master WHERE Exit_Fee_Pct IS NULL
UNION ALL SELECT 'MCF_null_RV',        COUNT(*) FROM fund_master WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NULL
UNION ALL SELECT 'Restantes',          COUNT(*) FROM fund_master WHERE Fund_Nature='Restantes'
UNION ALL SELECT 'Strategy_Repl_bad',  COUNT(*) FROM fund_master WHERE Strategy IN ('Indexado','Pasivo') AND (Replication_Method IS NULL OR Replication_Method != 'PASSIVE');

-- ── BL-49: Diagnóstico Currency_Hedged ──────────────────────────────────
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

-- ── BL-60: SRRI numérico en bipartitas empate ───────────────────────────
SELECT Fund_Name, Fund_Nature, SRRI, SRRI_Quality_Flag, Data_Quality_Flag
FROM fund_master
WHERE fund_family_id IN ('FAM_000945','FAM_000946','FAM_001778','FAM_002124','FAM_002320')
ORDER BY fund_family_id, Fund_Name;

-- ── DISTRIBUCIÓN FAMILIA completa ───────────────────────────────────────
SELECT Family, COUNT(*) AS n FROM fund_master GROUP BY Family ORDER BY 2 DESC;
```

---

-- ── BL-DLA-0: queries de cierre (resultados consolidados) ──────────────
-- Q-DLA-01 baseline distribución idiomas:
SELECT Language, COUNT(*) AS n_funds, AVG(LENGTH(Raw_KIID_Text)) AS avg_len
FROM fund_kiid_metadata
WHERE Raw_KIID_Text IS NOT NULL AND LENGTH(Raw_KIID_Text) > 100
GROUP BY Language ORDER BY n_funds DESC;
-- Resultado: ES=3.182, NULL=17, EN=5.

-- Q-DLA-02 firmas léxicas patológicas (cota inferior):
SELECT COUNT(*) AS n_sospechosos FROM fund_kiid_metadata
WHERE Raw_KIID_Text LIKE '%Tipo tres años%' OR Raw_KIID_Text LIKE '%subfondos mide%';
-- Resultado: 17 (cota inferior). Nota: Q-DLA-03 confirma que la población expuesta es mucho mayor.

-- Q-DLA-03 inventario físico de layouts (script externo, n=300):
-- Output: c:\desarrollo\fondos\proyecto1\db\dla_layout_inventory.csv
-- 88,3% con ≥1 página en 2-cols; 12 layout signatures distintas; 7,7% MIXED.

---

## 9. REGISTRO DE DECISIONES DE DISEÑO (acumulado v3.5)

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
| BL-52: corrección Country→Regional cuando Geography=región | Sin acción | Inconsistencia semántica, no aceptable en BD |
| BL-53/54: idioma objetivo Sector_Focus = español | Inglés (GICS-EN) | Coherencia con Family/Type/Geography; mapa centralizado |
| BL-55: Exit_Fee_Pct=0.00 con flag EXIT_INFERRED_ZERO | Mantener NULL | Permite a P3 distinguir cero estructural de cero desconocido |
| BL-56: invocación centralizada de normalización post-characterize | Replicar inline en pipeline | DRY; un único punto de mantenimiento |
| BL-57 v3: `Family='Orientado a Renta'` con constante canónica | Excepción documentada (v3.2 op. A) | Principio #8 (homogeneidad lingüística) + Principio #2 (DRY) |
| BL-FAM-FIX D4: "Income" no es sufijo de clase en `_normalize_name` | Mantener `inc(?:ome)?` | Falso positivo destruye granularidad; falso negativo es aceptable |
| BL-FAM-FIX D1: `Restantes` adyacente universal | Enumerar pares específicos | `Restantes` es fallback del clasificador: adyacente por definición a cualquier Nature |
| BL-FAM-FIX D2: Regla 2-bis para bipartitas | No corregir bipartitas | Sin la regla, 100% de bipartitas son no determinables; con la regla, se resuelven cuando hay asimetría de calidad |
| BL-FAM-FIX D3: desempate DQ tras SRRI en Regla 3 | Solo umbral SRRI>2 | Umbral >2 era inalcanzable para familias con calidades similares; DQ es criterio secundario legítimo |
| BL-60: bipartitas empate total → no determinables | Heurística por nombre | Sin señal objetiva de calidad, `fund_family_builder` no debe decidir; error está en los bloques clasificadores |
| **v3.4: backlog autocontenido** | Backlog incremental tipo "delta" (v3.3) | Principio #1 aplicado a documentación: causa raíz de la pérdida de eficiencia detectada |
| **BL-DLA-1: módulo nuevo upstream `dla_extractor.py`** | Modificar `kiid_parser.py` con regex tolerantes a cruce de columnas | DRY estructural: una mejora upstream beneficia a N detectores; el camino regex acumulativo (v24/v25) ya muestra rendimientos decrecientes |
| **BL-DLA-1: heurística de dos niveles (width-only + gap-detection si MIXED)** | Heurística width-only sola; clustering k-means para todos los casos | Coste marginal acotado al 7,7% de páginas MIXED; gap-detection es más interpretable que k-means; cubre layouts 3+ columnas como subproducto |
| **BL-DLA-1: kill-switch `DLA_ENABLED` con fallback a comportamiento actual** | Sustitución directa del extractor sin kill-switch | Permite roll-back instantáneo sin tocar código; consistente con disciplina de no-regresión del proyecto |
| **BL-DLA-1: migración progresiva vía `mark_stale_for_refresh` sin re-descarga forzada** | Marcar todos los fondos como FORCE_REFRESH para repoblar `Raw_KIID_Text` inmediatamente | La migración masiva consumiría todos los slots de descarga del periodo; la propagación progresiva (50/ciclo, 180 días) cubre el corpus en ~64 ciclos sin saturar el servidor |

---

## 10. ROADMAP RECOMENDADO

**Fase A — Cerrar P1 a estado consolidado (bloqueante para P3):**
1. **BL-61** — Procedimiento de verificación de causa raíz + fix preventivo. Requisito del Principio #9 REGLA INTER-1.
2. **BL-59** — Caso límite Restantes mayoritario. Especificación cerrada, listo para implementar.
3. **BL-49** — Extracción directa Currency_Hedged sobre KIID. Reduce 535 NULLs a ≤400.
4. **BL-50** — Direcciones inversas Universe→Geography para casos unívocos.
5. **BL-DLA-1** — Implementación Fase 1 DLA: serialización 2D-aware (sub-fases 1A → 1B → 1C → 1D). **Justificación de prioridad:** ataca causa raíz upstream que afecta al 88,3% del corpus; mejora upstream beneficia a TODOS los detectores downstream sin tocarlos; potencialmente reduce la presión sobre Fase D (BL-51A residual, BL-55).

**Fase B — Paralela a Fase A (no bloqueante):**
6. **P2 — Factores macro**: ingesta de las 3 series FRED pendientes (`BAMLH0A0HYM2`, `VIXCLS`, `T10Y2YM`). Código autocontenido en `macro_sensitivity.py`, no toca clasificación.

**Fase C — Posterior al cierre de Fase A:**
7. Regresiones OLS sobre los nuevos factores con clasificación P1 consolidada.
8. Construcción del dataset etiquetado por régimen (P2).
9. **P3 — Scoring régimen-dependiente** (las cinco fases).

**Fase D — Refinamientos diferibles sin riesgo (re-evaluar tras BL-DLA-1):**
- BL-53/54 (Sector_Focus español)
- BL-55 (Exit=0.00 implícito) — *posiblemente parcialmente resuelto por BL-DLA-1*
- BL-56 (enrich centralizado)
- BL-58 (constantes Lifecycle/Retirement)
- BL-60 (bipartitas empate total — investigación SRRI)
- BL-51 Problema B (schema cap/floor)
- BL-47-ext (SFDR Art. 8 default defensivo)
- BL-48-ext (Family LVNAV/VNAV/CNAV JPMorgan)
- **BL-DLA-2** (Fase 2 DLA: tablas Cat. 1+2 Gemini) — *evaluar tras BL-DLA-1 con métricas*
- **BL-DLA-3** (Fase 3 DLA: matrices Cat. 3 Gemini) — *baja prioridad*
- **BL-DLA-4** (Fase 4 DLA: OCR-aware) — *muy baja prioridad, depende de número de KIIDs sin capa de texto*

---

**Fin del documento. Versión v3.5 autocontenida — 2 de mayo de 2026.**
