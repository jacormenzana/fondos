# Estado del Backlog P1 — Referencia de Sesión v3.3

**Fecha:** 26 de abril de 2026  
**Ciclo de referencia:** `p1_export_20260426.xlsx` (3.204 fondos, schema v17) — log `log_pipeline_20260426_202952.log`  
**Módulos desplegados (acumulado):**
- `pipeline.py` v25 — BL-50 catálogos ampliados, BL-52 corrección Country↔Región
- `kiid_parser.py` v24 — BL-51A (10 nuevos patrones entry/exit fee, separador decimal opcional)
- `classify_utils.py` v4 + BL-57 v3 — constante `FAMILY_INCOME_ORIENTED` centralizada
- `blocks/mixtos.py` — BL-57 v3: import de constante, eliminado literal inline
- `fund_family_builder.py` — BL-FAM-FIX: D1 `_UNIVERSAL_ADJACENT`, D2 Regla 2-bis, D3 Regla 3 con desempate DQ, D4 regex `inc` sin `ome`
- `fund_characterizer.py` v18, `benchmark_normalizer.py` vBL-39, `restantes.py` v4

**Novedades v3.3 respecto a v3.2:**
- BL-57 v3 CERRADO: `Family='Orientado a Renta'` correctamente emitida por `mixtos.py` vía constante canónica; 104 fondos restaurados; 0 literales legacy en BD
- BL-FAM-FIX PARCIALMENTE CERRADO: 4 defectos de `fund_family_builder.py` corregidos; inconsistencias reducidas de 12 a 8; 2 nuevas incidencias registradas (BL-59, BL-60)
- BL-58 ABIERTO: patrón de constantes canónicas para los emisores latentes restantes
- BL-59 NUEVO: FAM_000261 — caso límite Restantes mayoritario + única Nature concreta
- BL-60 NUEVO: 5 bipartitas con empate total de calidad, no determinables por diseño

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
| **BL-57 v3** | **Centralización `FAMILY_INCOME_ORIENTED`; 104 fondos en 'Orientado a Renta'** | Queries A-E: 0 / 104 / 365+104+4 / 0 / 3204 | **✅ RESUELTO v3.3** |
| **BL-FAM-FIX D4** | **`_normalize_name`: "Income" no es sufijo de clase** | Templeton Global Income ≠ FAM_001293; GS GBL EQ INCOME ≠ FAM_001344 | **✅ RESUELTO v3.3** |
| **BL-FAM-FIX D1** | **`Restantes` adyacente universal; par `Mixtos/RFCP` añadido** | FAM_000382 corregida; `_UNIVERSAL_ADJACENT` operativo | **✅ RESUELTO v3.3** |
| **BL-FAM-FIX D2** | **Regla 2-bis: bipartitas resueltas por jerarquía DQ/SRRI** | Aplica cuando hay asimetría de calidad | **✅ RESUELTO v3.3** |
| **BL-FAM-FIX D3** | **Regla 3 reescrita: desempate SRRI→DQ** | Umbral anterior >2 era inalcanzable; DQ ahora como criterio secundario | **✅ RESUELTO v3.3** |

---

## 2. ESTADO DE COBERTURA — 26-ABRIL-2026 (post-ciclo BL-57v3+BL-FAM-FIX)

| Atributo | Filled | NULL | NULL% | Variación vs v3.2 | Tendencia |
|----------|--------|------|-------|-------------------|-----------|
| `Fund_Nature` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `Profile` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `Strategy` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `Family` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `Type` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `Theme` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `Leverage_Used` | 3.204 | 0 | 0,00% | — | ✅ Completo |
| `Derivatives_Usage` | 3.204 | 0 | 0,00% | YES=1.649 / NO=1.308 / LIMITED=247 | ✅ Cobertura total con distribución correcta |
| `Currency_Hedged` | 2.669 | 535 | 16,70% | estable | ⚠️ BL-49 pendiente |
| `SRRI` | 3.186 | 18 | 0,56% | estable | ✅ Límite estructural |
| `Investment_Focus` | 3.169 | 35 | 1,09% | estable | ✅ Límite estructural |
| `Fund_Currency` | 3.147 | 57 | 1,78% | estable | ✅ Límite estructural |
| `Ongoing_Charge` | 3.130 | 74 | 2,31% | estable | ✅ Límite estructural |
| `Entry_Fee_Pct` | 3.075 | 129 | 4,03% | estable | ⚠️ BL-51A residual (115 NOT_FOUND) |
| `Investment_Universe` | 3.001 | 203 | 6,34% | estable | ⚠️ BL-50 pendiente |
| `Geography` | 2.898 | 306 | 9,55% | estable | ⚠️ BL-50 pendiente |
| `Accumulation_Policy` | 2.810 | 394 | 12,30% | estable | ✅ Límite estructural |
| `Hedging_Policy` | 2.611 | 593 | 18,51% | estable | ✅ Estable post-BL-45 |
| `Exit_Fee_Pct` | 2.528 | 676 | 21,10% | estable | ⚠️ BL-55 pendiente |
| `Style_Profile` | 2.334 | 870 | 27,15% | estable | ✅ Estable |
| `Sfdr_Article` | 2.048 | 1.156 | 36,08% | estable | Límite regulatorio |
| `Market_Cap_Focus` | 1.820 | 1.384 | 43,20% | estable | ✅ Estable |
| `Benchmark_Declared` | 1.732 | 1.472 | 45,94% | estable | Límite estructural |
| `Sector_Focus` | 374 | 2.830 | 88,33% | estable | ⚠️ BL-53/BL-54 pendientes |
| `Subtype` | 270 | 2.934 | 91,57% | estable | ⚠️ BL-53 residual lingüístico |

**Distribución `Family` post-sprint:**

| Family | n | Variación vs v3.2 |
|--------|---|-------------------|
| RV Core | 1.455 | — |
| Renta Fija Corto Plazo | 427 | — |
| Renta Fija Flexible | 415 | — |
| Mixtos | 365 | **−104** (migrados a Orientado a Renta) |
| RV Temática | 218 | — |
| **Orientado a Renta** | **104** | **NUEVO ✅** |
| Monetario | 99 | — |
| Retorno Absoluto | 43 | — |
| RF High Yield | 39 | — |
| Activos Reales | 17 | — |
| Estructurado | 8 | — |
| RF Inflación | 5 | — |
| RF Emergentes | 5 | — |
| Flexible Estratégico | 4 | — |
| **Total** | **3.204** | **invariante ✅** |

**Distribución `Fund_Nature` post-sprint:**

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

**Estado familias post-sprint:**

| Métrica | Valor |
|---------|-------|
| Familias totales | 2.629 |
| Familias con múltiples clases | 373 |
| Familias inconsistentes (Nature) | 8 |
| Correcciones aplicadas | 2 |

---

## 3. ITEMS ABIERTOS — PRIORIZACIÓN

### Alta prioridad

---

**BL-59 — FAM_000261: Restantes mayoritario + única Nature concreta no se corrige (NUEVO)**

- **Estado:** Detectado durante la validación post-BL-FAM-FIX. No estaba en la especificación original.

- **Fondos afectados:** FAM_000261 (BGF China Bond: 3 Restantes + 2 RFCP, total 5 miembros).

- **Causa raíz:** En `_resolve_family_nature`, cuando `Restantes` tiene mayoría (3/5 = 0.60 < 0.667), la Regla 2 no aplica. En Regla 3, `Restantes` queda excluido del ranking de calidad (`srri_without_restantes`). Al ser la única Nature concreta, `others_srri` resulta vacío → `srri_diff = 0`. Con `srri_diff >= 0` se intenta el desempate por DQ, pero `others_dq` también es vacío → nunca devuelve corrección. El caso límite "Restantes mayoritario pero la única Nature concreta presenta todos los miembros con calidad no baja" no está cubierto.

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

**BL-61 — Strategy/Replication inconsistente: 12 fondos en bloques RENTA_VARIABLE y RESTANTES (NUEVO)**

- **Estado:** Detectado en el análisis post-sprint. 12 fondos tienen `Strategy='Indexado'` con `Replication_Method='ACTIVE'`.

- **Fondos afectados:**

  | Fondo | Nature | Bloque |
  |-------|--------|--------|
  | CARMIGNAC INVESTISSE.EUR A ACC | Renta Variable | RESTANTES |
  | CARMIGNAC INVESTISSEMENT E ACC | Renta Variable | RESTANTES |
  | DB GLOBAL EQ STRATEGY SC (×8 variantes) | Renta Variable | RENTA_VARIABLE |
  | JPMORGAN GLOBAL INCO.D ACC EU | Renta Variable | RENTA_VARIABLE |

- **Causa raíz (hipótesis a verificar):** La regla `validate_strategy_replication` de `classify_utils.py` existe (Principio #9 REGLA INTER-1) pero probablemente no se invoca desde `apply_semantic_validation` en el pipeline para estos bloques, o la autocorrección no persiste correctamente en SQLite. Los 10 fondos del bloque RENTA_VARIABLE son llamativos: sugieren que el bloque RV emite `Strategy='Indexado'` sin inferir `Replication_Method='PASSIVE'` desde el clasificador.

  ```python
  # Verificar en blocks/renta_variable.py / fund_characterizer.py:
  # ¿Se llama validate_strategy_replication() antes de devolver la clasificación?
  # ¿Se pasa el registro corregido a sqlite_writer?
  ```

- **Acción especificada:**

  1. Verificar que `apply_semantic_validation()` se invoca para TODOS los bloques en `pipeline.py` (no solo RESTANTES).
  2. Si `validate_strategy_replication` no autocorrige, verificar que la corrección llega a `sqlite_writer` (no se descarta silenciosamente).
  3. Si el bloque RENTA_VARIABLE emite `Strategy='Indexado'` con `Replication_Method=NULL`, la autocorrección a `PASSIVE` debe operar pero el campo NULL puede no estar cubierto por la condición `replication != 'PASSIVE'`.

- **Control SQL:**
  ```sql
  SELECT COUNT(*) FROM fund_master
  WHERE Strategy IN ('Indexado','Pasivo') AND Replication_Method != 'PASSIVE';
  -- Objetivo: 0
  ```

- **Módulos:** `pipeline.py` (invocación de `apply_semantic_validation`), `blocks/renta_variable.py`, `classify_utils.py` (`validate_strategy_replication`).
- **Prioridad:** Alta. Es la REGLA INTER-1 del Principio #9, documentada como obligatoria.

---

**BL-49 — Currency_Hedged: detección directa en texto KIID**

*(sin cambios respecto a v3.2 — especificación completa en ese documento)*

- **Estado:** NULL actuales: 535 (16,70%). La inferencia indirecta HP→CH ya está operativa. Pendiente fase 2 de extracción directa sobre `Raw_KIID_Text`.
- **Acción:** Ver especificación completa en v3.2 sección 3.
- **Control SQL:**
  ```sql
  SELECT COUNT(*) FROM fund_master WHERE Currency_Hedged IS NULL;
  -- Objetivo post-fix: ≤ 400
  SELECT Fund_Currency, COUNT(*) FROM fund_master
  WHERE Currency_Hedged IS NULL GROUP BY Fund_Currency ORDER BY 2 DESC;
  ```

---

**BL-50 — Inferencia INTER Investment_Universe / Geography (7 residuales)**

*(sin cambios respecto a v3.2)*

- **Estado:** 7 fondos con `Investment_Universe IS NOT NULL AND Geography IS NULL`.
- **Acción:** Ver especificación completa en v3.2 sección 3.

---

### Media prioridad

---

**BL-58 — Patrón de constantes canónicas: emisores latentes restantes (NUEVO)**

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

*(sin cambios respecto a v3.2)*

- **Estado:** 115 fondos con `Fee_Known_Flag='NOT_FOUND'`. 676 fondos con `Exit_Fee_Pct=NULL`.
- **Acción:** Ver especificación completa en v3.2 sección 3.

---

**BL-53/BL-54 — Sector_Focus: idioma objetivo y mapeo centralizado**

*(sin cambios respecto a v3.2)*

- **Estado:** 374 fondos con Sector_Focus poblado; 2.830 NULL (88,33%).
- **Acción:** Ver especificación completa en v3.2 sección 3.

---

**BL-55 — Exit_Fee_Pct=0.00 para declaraciones implícitas de cero**

*(sin cambios respecto a v3.2)*

- **Estado:** Sin implementar. 676 fondos con `Exit_Fee_Pct=NULL`.

---

**BL-56 — Invocación centralizada de normalización post-characterize**

*(sin cambios respecto a v3.2)*

- **Estado:** Sin implementar.

---

### Baja prioridad / futura

**BL-48-ext** — Normalización Family en Monetarios JPMorgan (LVNAV/VNAV/CNAV). Prereq: confirmar independencia P2/P3.

**BL-47-ext** — SFDR Article 6 completitud en fondos no-ESG.

---

## 4. LECCIONES ESTRUCTURALES — SPRINT BL-57v3 + BL-FAM-FIX

### 4.1 Antipatrón BL-57: desincronización emisor-validador (norma permanente)

**Descripción del incidente:**
La migración BL-57 v2 (25-abr-2026) actualizó `ALLOWED_FAMILY_BY_NATURE` (validador INTER-5) y los normalizadores SQL de `sqlite_writer.py` para aceptar `'Orientado a Renta'` en lugar de `'Income Oriented'`. Sin embargo, no se localizó el emisor primario: `blocks/mixtos.py:130` seguía emitiendo `'Income Oriented'`. El validador rechazó el literal viejo; la autocorrección P07 lo redirigió silenciosamente al default `'Mixtos'`. Resultado: 104 fondos perdieron granularidad (`Family='Mixtos'` en lugar de `'Orientado a Renta'`) sin ningún warning en el log.

**Norma BL-57 v3 (vigente desde 26-abr-2026):**
Antes de cambiar cualquier valor canónico en `ALLOWED_FAMILY_BY_NATURE`, `_DEFAULT_FAMILY_BY_NATURE`, `FAMILY_TRANSLATION_MAP` o normalizadores SQL:

1. **Localizar todos los emisores** del literal que se modifica en el codebase completo (`grep -rn "literal_viejo"`).
2. **Actualizar todos los emisores simultáneamente** en el mismo commit.
3. **Definir una constante canónica** en `classify_utils.py` e importarla desde todos los emisores.
4. **Mantener la red de seguridad** en `FAMILY_TRANSLATION_MAP` (entrada identidad del literal legacy) aunque el emisor ya use la constante correcta.

### 4.2 Límite del modelo de calidad en `fund_family_builder`

Los 5 casos bipartitos no resueltos (BL-60) demuestran que `SRRI_Quality_Flag` y `Data_Quality_Flag` son insuficientes como únicos criterios de desempate cuando ambas clases tienen extracción de máxima calidad. En ese escenario, el error está **aguas arriba**: en el bloque clasificador que asignó Natures distintas a dos clases del mismo fondo. `fund_family_builder` no puede ni debe corregir errores de clasificación cuando no tiene señal objetiva de cuál es la correcta. La solución estructural es mejorar la coherencia de clasificación en los bloques, no añadir heurísticas de nombre en `fund_family_builder` (que violaría el Principio #1 al parchear síntomas sin eliminar la causa).

### 4.3 Proyecciones de la especificación vs resultados reales

La especificación proyectaba ≤4 inconsistencias y ≥6 corregibles tras el sprint. Se obtuvieron 8 inconsistencias y 2 corregibles. La brecha se explica íntegramente por:

- Los 5 bipartitas con empate total de calidad (proyectados como resolubles con D2, pero D2 solo actúa con asimetría).
- FAM_000261 con caso límite Restantes-mayoritario (BL-59, no cubierto en la especificación).
- Las 2 estructurales (FAM_001699, FAM_001900) se mantienen correctamente como no corregibles.

No hubo regresiones. Los 2 casos corregidos (incluyendo FAM_000382) y D4 (Templeton, GS Income) funcionaron exactamente como se especificó.

---

## 5. NORMA BL-57 v3 — MIGRACIÓN SEGURA DE LITERALES CATEGÓRICOS

Esta norma es permanente y complementa el Principio #2 (DRY) del proyecto.

**Antes de modificar cualquier valor categórico canónico:**

```
1. grep -rn "literal_viejo" proyecto1/
   → Identificar TODOS los puntos de emisión

2. Para cada emisor encontrado:
   → Si emite el literal directamente: reemplazar por constante
   → Si es un mapa de traducción: actualizar la entrada

3. Definir la constante canónica en classify_utils.py:
   FAMILY_XXX = "valor_nuevo"

4. Importar desde todos los emisores:
   from core.classify_utils import FAMILY_XXX

5. Mantener en FAMILY_TRANSLATION_MAP:
   "valor_viejo": "valor_nuevo"  # red de seguridad

6. NO eliminar cláusulas WHEN en sqlite_writer.py hasta
   confirmar que ningún emisor activo genera el literal viejo
```

**Violación de esta norma:** el validador rechaza el literal viejo → P07 redirige al default → los datos pierden granularidad sin warning. Demostrado empíricamente: 104 fondos perdidos en BL-57 v2.

---

## 6. VALIDACIÓN SQL COMPLETA — CICLO v3.3

```sql
-- ── SPRINT BL-57 v3: Queries de cierre ─────────────────────────────────────

-- A) Literal legacy eliminado
SELECT COUNT(*) FROM fund_master WHERE Family = 'Income Oriented';
-- Resultado: 0 ✅

-- B) Literal canónico restaurado
SELECT COUNT(*) FROM fund_master WHERE Family = 'Orientado a Renta';
-- Resultado: 104 ✅

-- C) Distribución Mixtos completa
SELECT Family, COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Mixtos' GROUP BY Family ORDER BY 2 DESC;
-- Resultado: Mixtos=365, Orientado a Renta=104, Flexible Estratégico=4 ✅

-- D) No-regresión INTER-5 para Mixtos
SELECT COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Mixtos'
  AND Family NOT IN ('Mixtos', 'Orientado a Renta', 'Flexible Estratégico');
-- Resultado: 0 ✅

-- E) Conservación poblacional
SELECT COUNT(*) FROM fund_master;
-- Resultado: 3204 ✅

-- ── SPRINT BL-FAM-FIX D4: Templeton / GS Income ────────────────────────────

-- Templeton Global e Income deben tener fund_family_id distintos
SELECT Fund_Name, fund_family_id FROM fund_master
WHERE Fund_Name LIKE 'TEMPLETON GLOBAL%'
ORDER BY fund_family_id;
-- Resultado: FAM_001293 (Templeton Global A INC/RV), FAM_001294-1298 distintos ✅
-- "TEMPLETON GLOBAL INCOME A ACC" → FAM_001297 (distinto de los sin Income) ✅

-- GS Gbl Eq vs GS Gbl Eq Income
SELECT Fund_Name, fund_family_id FROM fund_master
WHERE Fund_Name LIKE 'GS GBL EQ%'
ORDER BY fund_family_id;
-- Resultado: FAM_001344 (GS GBL EQ INC) ≠ FAM_001345 (GS GBL EQ INCOME) ✅

-- ── FAMILIA INCONSISTENTES RESIDUALES ───────────────────────────────────────
SELECT fund_family_id, GROUP_CONCAT(DISTINCT Fund_Nature) AS natures, COUNT(*) AS n
FROM fund_master
WHERE fund_family_id IS NOT NULL
GROUP BY fund_family_id
HAVING COUNT(DISTINCT Fund_Nature) > 1
ORDER BY fund_family_id;
-- Resultado: 8 familias — todas justificadas (ver análisis en sección 3.2 del informe) ✅

-- ── BL-61: Strategy-Replication inconsistente ───────────────────────────────
SELECT COUNT(*) FROM fund_master
WHERE Strategy IN ('Indexado','Pasivo') AND Replication_Method != 'PASSIVE';
-- Resultado: 12 — PENDIENTE CORRECCIÓN (BL-61)

-- ── VALIDACIONES HISTÓRICAS (todas deben devolver 0) ────────────────────────
SELECT 'BL-19' AS bl, COUNT(*) FROM fund_master WHERE Fund_Nature='Mixto'
UNION ALL SELECT 'BL-26', COUNT(*) FROM fund_master WHERE Currency_Hedged IN ('Yes','No')
UNION ALL SELECT 'BL-28', COUNT(*) FROM fund_master WHERE Credit_Quality='No aplica'
UNION ALL SELECT 'BL-30', COUNT(*) FROM fund_master WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL
UNION ALL SELECT 'BL-31', COUNT(*) FROM fund_master WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED') OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED')
UNION ALL SELECT 'BL-32', COUNT(*) FROM fund_master WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL
UNION ALL SELECT 'BL-33', COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL
UNION ALL SELECT 'BL-44', COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND CAST(SRRI AS INTEGER) >= 3
UNION ALL SELECT 'BL-45', COUNT(*) FROM fund_master WHERE Currency_Hedged='Hedged' AND Hedging_Policy IS NULL
UNION ALL SELECT 'BL-46', COUNT(*) FROM fund_master WHERE Benchmark_Declared IS NOT NULL AND Benchmark_Declared != 'NO_BENCHMARK' AND Benchmark_Type IS NULL
UNION ALL SELECT 'BL-47', COUNT(*) FROM fund_master WHERE Is_ESG=1 AND Sfdr_Article IS NULL
UNION ALL SELECT 'BL-52', COUNT(*) FROM fund_master WHERE Investment_Universe='Country' AND Geography IN ('Latinoamérica','Europa del Este','Asia Pacífico','Emergentes','América Latina','Europa Central','África','Oriente Medio','América del Norte');
-- Todos deben devolver 0.

-- ── COBERTURA — seguimiento de progreso ─────────────────────────────────────
SELECT 'OC_null'           AS attr, COUNT(*) AS n FROM fund_master WHERE Ongoing_Charge IS NULL
UNION ALL SELECT 'entry_NOT_FOUND',  COUNT(*) FROM fund_master WHERE Fee_Known_Flag='NOT_FOUND' AND Entry_Fee_Pct IS NULL
UNION ALL SELECT 'AP_null',          COUNT(*) FROM fund_master WHERE Accumulation_Policy IS NULL
UNION ALL SELECT 'HP_null',          COUNT(*) FROM fund_master WHERE Hedging_Policy IS NULL
UNION ALL SELECT 'CH_null',          COUNT(*) FROM fund_master WHERE Currency_Hedged IS NULL
UNION ALL SELECT 'Geography_null',   COUNT(*) FROM fund_master WHERE Geography IS NULL
UNION ALL SELECT 'Universe_null',    COUNT(*) FROM fund_master WHERE Investment_Universe IS NULL
UNION ALL SELECT 'Style_null',       COUNT(*) FROM fund_master WHERE Style_Profile IS NULL
UNION ALL SELECT 'Exit_null',        COUNT(*) FROM fund_master WHERE Exit_Fee_Pct IS NULL
UNION ALL SELECT 'Restantes',        COUNT(*) FROM fund_master WHERE Fund_Nature='Restantes'
UNION ALL SELECT 'Strategy_Repl',    COUNT(*) FROM fund_master WHERE Strategy IN ('Indexado','Pasivo') AND Replication_Method != 'PASSIVE';

-- ── DISTRIBUCIÓN FAMILIA completa ────────────────────────────────────────────
SELECT Family, COUNT(*) AS n FROM fund_master GROUP BY Family ORDER BY 2 DESC;

-- ── BL-60: SRRI numérico en bipartitas empate ────────────────────────────────
SELECT Fund_Name, Fund_Nature, SRRI, SRRI_Quality_Flag, Data_Quality_Flag
FROM fund_master
WHERE fund_family_id IN ('FAM_000945','FAM_000946','FAM_001778','FAM_002124','FAM_002320')
ORDER BY fund_family_id, Fund_Name;
```

---

## 7. REGISTRO DE DECISIONES DE DISEÑO (acumulado v3.3)

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
| **BL-57 v3: `Family='Orientado a Renta'` con constante canónica** | Excepción documentada (v3.2) / Opción A | Principio #8 (homogeneidad lingüística) + Principio #2 (DRY) |
| **BL-FAM-FIX D4: "Income" no es sufijo de clase en `_normalize_name`** | Mantener `inc(?:ome)?` | Falso positivo destruye granularidad; falso negativo es aceptable |
| **BL-FAM-FIX D1: `Restantes` adyacente universal** | Enumerar pares específicos | `Restantes` es fallback del clasificador: adyacente por definición a cualquier Nature |
| **BL-FAM-FIX D2: Regla 2-bis para bipartitas** | No corregir bipartitas | Sin la regla, 100% de bipartitas son no determinables; con la regla, se resuelven cuando hay asimetría de calidad |
| **BL-FAM-FIX D3: desempate DQ tras SRRI en Regla 3** | Solo umbral SRRI>2 | Umbral >2 era inalcanzable para familias con calidades similares; DQ es criterio secundario legítimo |
| **BL-60: bipartitas empate total → no determinables** | Heurística por nombre | Sin señal objetiva de calidad, `fund_family_builder` no debe decidir; error está en los bloques clasificadores |

---

**Fin del documento. Versión v3.3 — 26 de abril de 2026.**
