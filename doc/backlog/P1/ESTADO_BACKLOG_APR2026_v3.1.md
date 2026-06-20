# Estado del Backlog P1 — Referencia de Sesión v3.1
**Fecha:** 19 de abril de 2026  
**Ciclo de referencia:** p1_export_20260419.xlsx (3.204 fondos, schema v17)  
**Módulos desplegados (acumulado):**
- `kiid_parser.py` v24 — BL-51 Problema A (6 nuevos patrones entry fee, 4 exit fee)
- `kiid_parser.py` v23 — BL-37b, BL-35b, BL-40, BL-41, BL-43a, BL-43b
- `pipeline.py` v24 — BL-42, BL-43a-ext, BL-41-ext, BL-27-ext, BL-45
- `classify_utils.py` v4, `fund_characterizer.py` v18, `benchmark_normalizer.py` vBL-39, `restantes.py` v4

**Novedades v3.1 respecto a v3:**
- BL-51 Problema A resuelto: `kiid_parser.py` v24 entregado (26/26 tests unitarios ✅)
- BL-51 Problema B: análisis de schema completado, documentado en `BL51_SCHEMA_DECISION.md`

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
| BL-35b | Entry_Fee NOT_FOUND Thread+AXA | `COUNT(*) WHERE Fee_Known_Flag='NOT_FOUND'` | **139 ✅** |
| BL-37 | Ongoing_Charge NULL < 600 | `COUNT(*) WHERE Ongoing_Charge IS NULL` | **74 ✅** |
| BL-37b | OC NULL JPMorgan fused | `COUNT(*) WHERE Ongoing_Charge IS NULL` | **74 ✅** |
| BL-38 | Sin benchmarks contaminados | `COUNT(*) WHERE LENGTH(Benchmark_Declared)>100 AND Benchmark_Declared!='NO_BENCHMARK'` | **0 ✅** |
| BL-39 | Benchmark normalizer aliases | — | **✅ Resuelto** (benchmark_normalizer.py vBL-39) |
| BL-40 | Accumulation_Policy NULL Deutsche+BlackRock | `COUNT(*) WHERE Accumulation_Policy IS NULL` | **394 ✅** |
| BL-41 | Style_Profile desde KIID (señales estrictas) | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Style_Profile IS NOT NULL` | **544 ✅** (+78 desde 466) |
| BL-41-ext | Style_Profile defaults semánticos (Blend/Not Applicable) | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Style_Profile IS NULL AND Strategy IS NOT NULL` | **0 esperado post-v24** |
| BL-42 | Credit_Quality Mixtos NULL | `COUNT(*) WHERE Fund_Nature='Mixtos' AND Credit_Quality IS NULL` | **0 ✅** |
| BL-43a | Subtype Monetario VNAV/LVNAV/CNAV | `SELECT Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Monetario' GROUP BY Subtype` | **36 tipificados ✅** |
| BL-43a-ext | Subtype Monetario Standard MMF | `COUNT(*) WHERE Fund_Nature='Monetario' AND Subtype='Standard MMF'` | **~38 esperado post-v24** |
| BL-43b | Subtype Mixtos Fixed Band + Volatility Target | `SELECT Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Mixtos' AND Subtype IS NOT NULL` | **12 fondos ✅** |
| BL-44 | Misclasificaciones Fund_Nature SRRI elevado | `COUNT(*) WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND CAST(SRRI AS INTEGER) >= 3` | **0 ✅** |
| BL-45 | Hedging_Policy inferida desde Currency_Hedged | `COUNT(*) WHERE Currency_Hedged='Hedged' AND Hedging_Policy IS NULL` | **0 ✅** |
| BL-46 | Benchmark_Type NULL con Benchmark_Declared poblado | `COUNT(*) WHERE Benchmark_Declared IS NOT NULL AND Benchmark_Declared != 'NO_BENCHMARK' AND Benchmark_Type IS NULL` | **0 ✅** |
| BL-47 | Is_ESG=1 sin Sfdr_Article | `COUNT(*) WHERE Is_ESG=1 AND Sfdr_Article IS NULL` | **0 ✅** |
| BL-48 | Subtype Monetario distribución correcta post-normalización | `SELECT Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Monetario' GROUP BY Subtype` | **✅ Distribución correcta** |
| BL-27-ext | Market_Cap_Focus All Cap default | `COUNT(*) WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus='All Cap'` | **~1.041 esperado post-v24** |

---

## 2. ESTADO DE COBERTURA — 19-ABRIL-2026 (post-v24 ejecución)

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
| `investment_universe` | 3.001 | 203 | 6,34% | estable | Ver BL-50 |
| `geography` | 2.898 | 306 | 9,55% | estable | Ver BL-50 |
| `accumulation_policy` | 2.810 | 394 | 12,30% | **−200 (antes 594)** | ✅ BL-40 resuelto |
| `exit_fee_pct` | 2.469 | 735 | 22,94% | estable | Ver BL-51 |
| `hedging_policy` | 2.412 | 792 | 24,72% | estable pre-v24 | ⏳ BL-45 pendiente ejecución |
| `currency_hedged` | 2.056 | 1.148 | 35,83% | estable | Ver BL-49 |
| `sfdr_article` | 1.994 | 1.210 | 37,77% | estable | Límite regulatorio |
| `benchmark_declared` | 1.851 | 1.353 | 42,23% | estable | Límite estructural |
| `style_profile` | 1.215 | 1.989 | 62,08% | **+78 filled** | ⏳ BL-41-ext pendiente ejecución |
| `market_cap_focus` | 466 | 2.738 | 85,46% | **+33 filled** | ⏳ BL-27-ext pendiente ejecución |
| `sector_focus` | 374 | 2.830 | 88,33% | estable | Límite estructural |
| `subtype` | 232 | 2.972 | 92,76% | **+49 filled** | ⏳ BL-43a-ext pendiente ejecución |

**Previsión post-v24 (próximo ciclo):**

| Atributo | NULL actual | NULL esperado | Reducción |
|----------|-------------|---------------|-----------|
| `hedging_policy` | 792 | ~593 | −199 (BL-45) |
| `style_profile` | 1.989 | ~869 | −1.120 (BL-41-ext: ~1.036 Blend + ~84 Not Applicable) |
| `market_cap_focus` | 2.738 | ~1.697 | −1.041 (BL-27-ext: All Cap en RV no sectorial) |
| `subtype` | 2.972 | ~2.934 | −38 (BL-43a-ext: Standard MMF) |

---

## 3. ITEMS ABIERTOS — PRIORIZACIÓN

### Alta prioridad

---

**BL-49 — Currency_Hedged: extensión de extracción al texto KIID**

- **Descripción:** `Currency_Hedged` presenta un 35,83% de nulos (1.148 fondos). De estos, al menos 267 fondos tienen `Fund_Currency` distinto al EUR (`USD`, `GBP`, `CHF`, `JPY`, `CNH`) y el campo sigue en NULL, lo que indica que la extracción por nombre de fondo no es suficiente para estos casos. La señal de cobertura de divisa está disponible en el texto KIID pero actualmente no se lee cuando el nombre de fondo no aporta la señal.

  ```sql
  -- Cuantificación del gap
  SELECT COUNT(*) FROM fund_master
  WHERE Fund_Currency IN ('USD','GBP','CHF','JPY','CNH')
    AND Currency_Hedged IS NULL;
  -- Resultado actual: 267
  ```

- **Causa raíz:** El detector de `Currency_Hedged` opera exclusivamente sobre el nombre del fondo. Los fondos en divisas no-EUR con cobertura declarada en el cuerpo del KIID no son captados.

- **Acción:** Extender `_detect_currency_hedged()` en `kiid_parser.py` para buscar patrones en `Raw_KIID_Text` cuando la búsqueda por nombre resulta infructuosa. Patrones candidatos: `"currency hedged"`, `"share class hedged"`, `"hedged against"`, `"currency risk is hedged"`, `"fully hedged"`, `"sin cobertura"`, `"cobertura de divisa"`, `"no cubre el riesgo de cambio"`.

- **Principio general derivado:** Todo atributo extraído exclusivamente desde el nombre del fondo que registre una tasa de nulos superior al 2% debe incorporar una fase de fallback sobre el texto KIID. Atributos potencialmente afectados además de `Currency_Hedged`: revisar `Hedging_Policy` (24,72% NULL) y cualquier otro detector que no consulte `Raw_KIID_Text`.

- **Impacto estimado:** Reducción de los 267 fondos no-EUR con CH=NULL en una fracción significativa. El techo real de nulos post-fix necesita validación sobre los 267 KIIDs.

- **Módulo:** `kiid_parser.py` — función `_detect_currency_hedged()`.

- **Control SQL post-fix:**
  ```sql
  SELECT COUNT(*) FROM fund_master
  WHERE Fund_Currency IN ('USD','GBP','CHF','JPY','CNH')
    AND Currency_Hedged IS NULL;
  -- Objetivo: reducción significativa desde 267
  ```

---

**BL-50 — Nulos cruzados Investment_Universe / Geography (inferencia INTER)**

- **Descripción:** Existen fondos donde uno de los dos atributos geográficos está informado y el otro es NULL, cuando la relación semántica entre ambos permite inferir el valor faltante con alta fiabilidad. Casos identificados:

  - `Geography` poblado con valor específico (ej. `EEUU`, `Asia`, `Europa`) pero `Investment_Universe=NULL` → puede inferirse `Country` o `Regional` o `Global` según el valor de Geography.
  - `Investment_Universe` poblado con valor `Country` o `Regional` pero `Geography=NULL` → la señal de universe indica que hay una geografía específica que debería estar informada; si el nombre del fondo o el KIID aportan la señal, se puede poblar Geography.

  El volumen estimado de fondos afectados es parte de los 203 nulos en `Investment_Universe` y de los 306 nulos en `Geography`.

- **Causa raíz:** La inferencia INTER entre estos dos atributos no está implementada. La Regla INTER-6 del documento de principios (sección 3.2.1) define la completitud Universe→Sector/Geography como validación, pero no implementa la inferencia inversa (Geography→Universe).

- **Acción:**
  1. Cuantificar el gap exacto con las siguientes queries:
     ```sql
     -- Geography poblado pero Universe NULL
     SELECT Geography, COUNT(*) FROM fund_master
     WHERE Investment_Universe IS NULL AND Geography IS NOT NULL
     GROUP BY Geography ORDER BY 2 DESC;

     -- Universe poblado pero Geography NULL (Country/Regional)
     SELECT Investment_Universe, COUNT(*) FROM fund_master
     WHERE Investment_Universe IN ('Country','Regional') AND Geography IS NULL
     GROUP BY Investment_Universe;
     ```
  2. Implementar en `pipeline.py` (bloque de defaults INTER):
     - Si `Geography IN ('EEUU','China','Japón','India','Brasil')` y `Investment_Universe IS NULL` → inferir `Investment_Universe = 'Country'`.
     - Si `Geography IN ('Europa','Asia','Emergentes','América Latina','Europa del Este')` y `Investment_Universe IS NULL` → inferir `Investment_Universe = 'Regional'`.
     - Si `Geography = 'Global'` y `Investment_Universe IS NULL` → inferir `Investment_Universe = 'Global'`.

- **Módulo:** `pipeline.py` — bloque de defaults semánticos INTER (P14 o nuevo P15).

- **Control SQL post-fix:**
  ```sql
  SELECT COUNT(*) FROM fund_master
  WHERE Investment_Universe IS NULL AND Geography IS NOT NULL
    AND Geography IN ('EEUU','China','Japón','India','Brasil','Europa','Asia','Emergentes','Global');
  -- Objetivo: 0
  ```

---

**BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo**

- **Estado:** Problema A ✅ resuelto en `kiid_parser.py` v24. Problema B 🔍 análisis completado, implementación pendiente de ratificación de schema.

**Problema A — Patrones de extracción no cubiertos → RESUELTO en v24**

Nuevos patrones añadidos a `_detect_entry_fee()` (6 patrones, 26/26 tests unitarios ✅):

| Patrón | Tipo | Formulación cubierta |
|--------|------|----------------------|
| `_EF_NO_HAY_GASTOS_RE` | ZERO | "no hay gastos de entrada" (Fidelity, BNP) |
| `_EF_SIN_CARGO_RE` | ZERO | "sin cargo de entrada / inicial" (Vanguard, Robeco) |
| `_EF_NO_FRONT_LOAD_RE` | ZERO | "no front-end load / no sales charge / entry fee: nil" (EN) |
| `_EF_GASTOS_ENTRADA_RE` | PCT | "gastos de entrada del X% / hasta el X%" (ES sinónimo) |
| `_EF_CARGO_INICIAL_RE` | PCT | "cargo inicial máximo del X%" (Robeco ES, gestoras DE) |
| `_EF_FRONT_LOAD_EN_RE` | PCT | "front-end load / sales charge / initial charge X%" (EN) |

Nuevos patrones añadidos a `_detect_exit_fee()` (5 patrones):

| Patrón | Tipo | Formulación cubierta |
|--------|------|----------------------|
| `_XF_NO_EXIT_CHARGE_EN_RE` | ZERO | "no exit charge / no redemption fee / exit fee: nil" (EN) |
| `_XF_NO_COBRAREMOS_RE` | ZERO | "no cobraremos / no se cobra comisión de reembolso" (ES) |
| `_XF_JPM_FUSED_ZERO` | ZERO | JPMorgan OCR fusionado sin espacios: "Costesdesalida0,00%" |
| `_XF_JPM_FUSED_PCT` | PCT | JPMorgan OCR fusionado: "CostesdesalidaX,XX%delimporte" |

**Fix técnico detectado en tests:** el grupo capturador de porcentaje era `([\d]+[,.][\d]+)` (separador decimal obligatorio). Corregido a `([\d]+(?:[,.][\d]+)?)` para cubrir enteros puros como "5%" además de "5,00%".

Control SQL post-ejecución:
```sql
SELECT COUNT(*) FROM fund_master WHERE Entry_Fee_Pct IS NULL AND Fee_Known_Flag='NOT_FOUND';
-- Objetivo: reducción desde 134 a ~75-90

SELECT COUNT(*) FROM fund_master WHERE Exit_Fee_Pct IS NULL;
-- Objetivo: reducción desde 735 a ~680-710
```

**Problema B — Estructura mixta (pct + tope fijo) → análisis completado, schema pendiente**

Análisis completo documentado en `BL51_SCHEMA_DECISION.md`. Resumen de la decisión:

- **Tipología identificada:** 4 tipos (A: pct+cap, B: pct+floor, C: tarifa plana, D: tramos escalonados).
- **Opción de schema recomendada:** Opción 1 — añadir 4 columnas numéricas en schema v18: `Entry_Fee_Cap_EUR`, `Entry_Fee_Floor_EUR`, `Exit_Fee_Cap_EUR`, `Exit_Fee_Floor_EUR`.
- **Función de coste efectivo P3:** `min(importe × pct, cap)` cuando cap no es NULL.
- **Prerequisito antes de implementar:** ejecutar queries de diagnóstico de prevalencia (sección 6 de `BL51_SCHEMA_DECISION.md`) para confirmar volumen real de fondos afectados.
- **No implementado en v24.** La extracción de cap/floor requiere un v25 del parser posterior a la ratificación del schema.

- **Módulos afectados (Problema B):** `schema_fondos.sql` (v18), `sqlite_writer.py` (upsert), `pipeline.py` (dict record), `kiid_parser.py` (nueva función `_detect_fee_cap_floor` en v25).

---

### Media prioridad

---

**BL-52 — Inconsistencia semántica Investment_Universe vs Geography: valores de región en campo Country**

- **Descripción:** Existen fondos donde `Investment_Universe='Country'` pero `Geography` registra un valor que denota una región geográfica amplia en lugar de un país concreto. Ejemplos identificados: `Geography='Latinoamérica'`, `Geography='Europa del Este'`. Estas denominaciones son regiones (conjunto de países), no países individuales, por lo que son semánticamente incompatibles con `Investment_Universe='Country'`.

  La misma lógica aplica en dirección opuesta: si `Geography` contiene un valor de región como `'Latinoamérica'` o `'Europa del Este'`, el valor correcto de `Investment_Universe` debería ser `'Regional'`, no `'Country'`.

  Adicionalmente, se identifica un problema de consistencia lingüística interna en el atributo `Geography`: `'Europa del Este'` es una denominación válida pero específica, reservada para cuando el KIID especifica explícitamente "Eastern Europe" o "CEE" (Principio #9, sección 3.1.1). Si el texto solo indica "Europe" genérico, el valor correcto es `'Europa'`.

- **Causa raíz:** La validación INTER Geography↔Universe (Regla INTER-10) existe en el documento de principios pero no implementa la corrección del caso región-asignada-como-Country. La asignación de `'Europa del Este'` no verifica el contexto de uso de Universe.

- **Acción:**
  1. Cuantificar:
     ```sql
     SELECT Geography, Investment_Universe, COUNT(*) FROM fund_master
     WHERE Investment_Universe = 'Country'
       AND Geography IN ('Latinoamérica','Europa del Este','Asia','Emergentes','América Latina','Europa Central')
     GROUP BY Geography, Investment_Universe;
     ```
  2. Implementar corrección en `classify_utils.py`:
     - Si `Geography` es una región y `Investment_Universe='Country'` → corregir `Investment_Universe` a `'Regional'` (auto-corrección).
     - Definir catálogo explícito: `REGION_VALUES = ['Latinoamérica','Europa del Este','Asia Pacífico','Emergentes','América Latina','Europa Central','África','Oriente Medio']`.
  3. Revisar asignaciones de `Geography='Europa del Este'`: verificar que el KIID de origen especifica realmente "Eastern Europe"/"CEE" y no "Europe" genérico.

- **Módulo:** `classify_utils.py` — ampliar `validate_geography_universe()` con corrección de región-vs-país.

---

**BL-53 — Inconsistencias lingüísticas intra-atributo: valores en inglés en columnas con idioma objetivo español**

- **Descripción:** Se detectan valores en inglés en atributos cuyo idioma objetivo según el Principio #8 es el español. Los casos identificados son:

  **`Family`:** Se registra el valor `'Income'`. El idioma objetivo de Family es español. El valor correcto según el mapeo del Principio #8 es `'Income Oriented'` (si se acepta como excepción) o debe mapearse a `'Renta Fija Flexible'` / `'Mixtos'` según el contexto de Nature. Requiere análisis para determinar el mapeo correcto y si `'Income Oriented'` debe considerarse valor canónico en español o si debe traducirse.

  **`Type`:** Se registran valores en inglés: `'Allocation'`, `'Absolute Return'`, `'Commodities'`. Según el Principio #8, Type tiene idioma objetivo español. El `TYPE_TRANSLATION_MAP` define que `'Allocation'` y `'Absolute Return'` se mantienen como excepciones (sin traducción directa al español), pero esto debe confirmarse y el mapa debe aplicarse de forma consistente. `'Commodities'` no tiene traducción canónica definida en el principio → requiere decisión de diseño.

  **`Subtype`:** Se detectan valores en inglés en un atributo cuyo idioma objetivo es español. Los valores concretos deben auditarse con:
  ```sql
  SELECT DISTINCT Subtype FROM fund_master WHERE Subtype IS NOT NULL ORDER BY 1;
  ```

  **`Sector_Focus`:** Atributo con idioma objetivo inglés (GICS-ES). Verificar que no hay valores en español mezclados:
  ```sql
  SELECT DISTINCT Sector_Focus FROM fund_master WHERE Sector_Focus IS NOT NULL ORDER BY 1;
  ```

- **Causa raíz:** La función `apply_language_homogeneity()` referenciada en el Principio #8 (sección 5) y los mapas de traducción `TYPE_TRANSLATION_MAP`, `FAMILY_TRANSLATION_MAP`, `SUBTYPE_TRANSLATION_MAP` no están implementados de forma completa o no se aplican en todos los bloques clasificadores.

- **Acción:**
  1. Ejecutar auditoría lingüística completa sobre los atributos con idioma objetivo definido:
     ```sql
     SELECT 'family' AS col, Family AS val, COUNT(*) AS n FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
     UNION ALL
     SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
     UNION ALL
     SELECT 'subtype', Subtype, COUNT(*) FROM fund_master WHERE Subtype IS NOT NULL GROUP BY Subtype
     UNION ALL
     SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
     ORDER BY 1, 3 DESC;
     ```
  2. Para cada valor detectado como inconsistente con el idioma objetivo, determinar:
     - Si existe traducción canónica en los mapas del Principio #8 → aplicar traducción.
     - Si el valor se mantiene en inglés por diseño (excepción documentada en P#8) → registrar como aceptado.
     - Si falta el valor en los mapas → añadir al mapa antes de aplicar la corrección.
  3. Implementar `apply_language_homogeneity()` de forma completa en `classify_utils.py` y verificar que todos los bloques la invocan antes de retornar la clasificación.
  4. Resolver específicamente:
     - `Family='Income'` → determinar valor canónico correcto y aplicar.
     - `Type='Commodities'` → decidir si es excepción (mantener inglés) o si se añade al mapa con traducción (ej. `'Materias Primas'`).
     - `Type='Allocation'` y `'Absolute Return'` → confirmar que son excepciones documentadas o añadir traducción española.

- **Módulo:** `classify_utils.py` — implementar `apply_language_homogeneity()` completa. Bloques clasificadores (todos) — verificar que la invocan.

---

### Baja prioridad / futura

**BL-48-ext — Normalización Family en Monetarios JPMorgan**
- **Descripción:** 18 fondos JPMorgan tienen `Family=LVNAV/VNAV/CNAV` en lugar de `Family=Monetario`. Tras BL-43a, `Subtype` captura la tipología regulatoria de forma explícita. La decisión conservadora de mantener el valor de Family se tomó para no romper consumidores de P2/P3.
- **Prerequisito:** Confirmar que ningún consumidor P2/P3 depende de `Family='LVNAV'` como selector antes de modificar.
- **Módulo:** `fund_characterizer.py` o bloque MONETARIOS.

**BL-47-ext — SFDR Art. 8 como default defensivo para fondos ESG sin artículo**
- **Descripción:** Aunque BL-47 se cerró (0 fondos ESG sin SFDR), la estrategia defensiva de asignar Art. 8 como default para fondos `Is_ESG=1` sin artículo declarado debe documentarse formalmente y validarse frente a fuentes externas (registros ESMA).
- **Acción:** Evaluar integración con fuente externa ESMA para mejorar fiabilidad de SFDR.

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

Estos atributos tienen NULL alto por límite de señal disponible. No son bugs. No requieren acción adicional en P1 salvo los indicados.

| Atributo | NULL actual | NULL% | Naturaleza | Acción |
|----------|-------------|-------|------------|--------|
| `Subtype` | 2.972 | 92,8% | Gran mayoría de natures×types sin variante estructural diferenciable. 232 fondos con valor es cobertura correcta post-BL-43. | BL-43a-ext en v24 (−38) |
| `Sector_Focus` | 2.830 | 88,3% | Solo fondos sectoriales (~374). Cobertura correcta. Límite real. | Ninguna |
| `Market_Cap_Focus` | 2.738 | 85,5% | Baja a ~1.697 con BL-27-ext (All Cap). Los non-RV son correctamente NULL. | BL-27-ext en v24 (−1.041) |
| `Style_Profile` | 1.989 | 62,1% | Baja a ~869 con BL-41-ext. RV indexada → Not Applicable, RV activa sin sesgo → Blend. | BL-41-ext en v24 (−1.120) |
| `Benchmark_Declared` | 1.353 | 42,2% | Fondos sin benchmark detectable en KIID. Límite estructural. | Ninguna |
| `Sfdr_Article` | 1.210 | 37,8% | 386 fondos pre-PRIIPs genuinos + 824 sin declaración explícita. | Ninguna (ver BL-47-ext) |
| `Currency_Hedged` | 1.148 | 35,8% | 267 fondos no-EUR con gap actionable. Resto: combinación divisa/geografía sin señal. | **BL-49** |
| `Hedging_Policy` | 792 | 24,7% | Baja a ~593 con BL-45. Residual: sin señal explícita en KIID. | BL-45 en v24 (−199) |
| `Exit_Fee_Pct` | 735 | 22,9% | Patrones parcialmente extractables. Ver análisis BL-51. | **BL-51 Problema A** |
| `Accumulation_Policy` | 394 | 12,3% | KIIDs pre-2015 o texto OCR degradado sin señal de política. Límite real. | Ninguna |
| `Geography` | 306 | 9,6% | Mayoría sin señal geográfica detectable. Gap parcial accionable via inferencia INTER. | **BL-50** |
| `Investment_Universe` | 203 | 6,3% | Gap parcial accionable via inferencia INTER desde Geography. | **BL-50** |
| `Credit_Quality` | 54 | 1,7% | Alternativo (46) y Estructurado (8). Requiere análisis por subtipo de Alternativo antes de asignar default. | Análisis futuro |

---

## 5. CAUSA RAÍZ SISTÉMICA — COALESCE EN sqlite_writer

`sqlite_writer.publish_fund()` usa `COALESCE(new_value, old_value)` en la sentencia UPDATE:
- Si `fund_master_record["X"] = None` → BD preserva el valor antiguo.
- Si `fund_master_record["X"] = valor_nuevo` → BD sobrescribe.

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
| `kiid_parser.py` | **v24** | BL-51A: 6 nuevos patrones entry fee (3 ZERO + 3 PCT), 4 exit fee (3 ZERO + 1 PCT). Fix separador decimal opcional en grupos capturadores. | **DESPLEGADO** |
| `kiid_parser.py` | v23 | BL-37b (OC fused JPM), BL-35b (Thread+AXA), BL-40 (DWS+BlackRock), BL-41 (Style_Profile), BL-43a/b (Subtype Mon+Mix) | base de v24 |
| `classify_utils.py` | v4 | BL-19/22/23/24/30/31/32/33 | **DESPLEGADO** |
| `fund_characterizer.py` | v18 | BL-26/27/28/29 | **DESPLEGADO** |
| `benchmark_normalizer.py` | vBL-39 | +20 aliases, +9 false positives | **DESPLEGADO** |
| `restantes.py` | v4 | BL-09/20/21 | **DESPLEGADO** |

---

## 7. PRINCIPIOS DE DISEÑO CONSOLIDADOS

### Principio de valores semánticos explícitos sobre NULL

**Enunciado:** Cuando NULL puede significar cosas semánticamente distintas en un mismo atributo, se asigna un valor explícito que elimina la ambigüedad. NULL se reserva para "genuinamente desconocido o no aplicable sin clasificación posible".

| Atributo | Valor | Significado | Alternativa descartada |
|----------|-------|-------------|----------------------|
| `Subtype` | `Standard MMF` | Fondo monetario UCITS no sujeto al Reglamento MMF 2017/1131 | NULL ambiguo entre pre-regulación y post-regulación sin señal |
| `Style_Profile` | `Blend` | RV activa sin sesgo de estilo declarado (agnóstico Growth/Value) | NULL interpretable como "no detectado" |
| `Style_Profile` | `Not Applicable` | RV indexada/pasiva donde el estilo de gestión no existe | NULL confundible con falta de detección |
| `Market_Cap_Focus` | `All Cap` | RV sin restricción de capitalización (no sectorial) | NULL interpretable como "no detectado" |

### Principio de extracción en cascada (nuevo — sesión v3)

**Enunciado:** Para atributos con tasa de nulos superior al 2%, la extracción debe operar en cascada: primero nombre del fondo, luego texto KIID completo si la primera fase es infructuosa. No es aceptable limitar la extracción al nombre cuando existe señal disponible en el KIID.

**Aplicación documentada:** BL-49 (Currency_Hedged), potencialmente aplicable a Hedging_Policy.

### Principio de inferencia INTER para nulos geográficos (nuevo — sesión v3)

**Enunciado:** Los atributos `Geography` e `Investment_Universe` están semánticamente vinculados. Un valor poblado en uno debe permitir inferir el valor del otro cuando la relación es unívoca (ej. Geography='EEUU' → Universe='Country'). La inferencia INTER de nulos es preferible a mantener incoherencias entre atributos relacionados.

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
UNION ALL SELECT 'BL-44', COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND CAST(SRRI AS INTEGER) >= 3
UNION ALL SELECT 'BL-45', COUNT(*) FROM fund_master WHERE Currency_Hedged='Hedged' AND Hedging_Policy IS NULL
UNION ALL SELECT 'BL-46', COUNT(*) FROM fund_master WHERE Benchmark_Declared IS NOT NULL AND Benchmark_Declared != 'NO_BENCHMARK' AND Benchmark_Type IS NULL
UNION ALL SELECT 'BL-47', COUNT(*) FROM fund_master WHERE Is_ESG=1 AND Sfdr_Article IS NULL;
-- Todos deben devolver 0

-- ── COBERTURA — seguimiento de progreso ────────────────────────────────
SELECT 'OC_null'             AS attr, COUNT(*) AS n FROM fund_master WHERE Ongoing_Charge IS NULL
UNION ALL SELECT 'entry_NOT_FOUND',   COUNT(*) FROM fund_master WHERE Fee_Known_Flag='NOT_FOUND'
UNION ALL SELECT 'AP_null',           COUNT(*) FROM fund_master WHERE Accumulation_Policy IS NULL
UNION ALL SELECT 'HP_null',           COUNT(*) FROM fund_master WHERE Hedging_Policy IS NULL
UNION ALL SELECT 'CH_null',           COUNT(*) FROM fund_master WHERE Currency_Hedged IS NULL
UNION ALL SELECT 'CH_nonEUR_null',    COUNT(*) FROM fund_master WHERE Fund_Currency IN ('USD','GBP','CHF','JPY','CNH') AND Currency_Hedged IS NULL
UNION ALL SELECT 'Geography_null',    COUNT(*) FROM fund_master WHERE Geography IS NULL
UNION ALL SELECT 'Universe_null',     COUNT(*) FROM fund_master WHERE Investment_Universe IS NULL
UNION ALL SELECT 'CreditQ_null',      COUNT(*) FROM fund_master WHERE Credit_Quality IS NULL
UNION ALL SELECT 'Style_null',        COUNT(*) FROM fund_master WHERE Style_Profile IS NULL
UNION ALL SELECT 'MCF_null_RV',       COUNT(*) FROM fund_master WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NULL;

-- ── BL-49: Diagnóstico Currency_Hedged ─────────────────────────────────
SELECT Fund_Currency, COUNT(*) FROM fund_master
WHERE Currency_Hedged IS NULL
GROUP BY Fund_Currency ORDER BY 2 DESC;

-- ── BL-50: Diagnóstico Geography / Universe cruzados ───────────────────
SELECT Geography, COUNT(*) FROM fund_master
WHERE Investment_Universe IS NULL AND Geography IS NOT NULL
GROUP BY Geography ORDER BY 2 DESC;

SELECT Investment_Universe, COUNT(*) FROM fund_master
WHERE Investment_Universe IN ('Country','Regional') AND Geography IS NULL
GROUP BY Investment_Universe;

-- ── BL-52: Investment_Universe='Country' con región en Geography ────────
SELECT Geography, Investment_Universe, COUNT(*) FROM fund_master
WHERE Investment_Universe = 'Country'
  AND Geography IN ('Latinoamérica','Europa del Este','Asia Pacífico','Emergentes','América Latina')
GROUP BY Geography, Investment_Universe;

-- ── BL-53: Auditoría lingüística ────────────────────────────────────────
SELECT 'family' AS col, Family AS val, COUNT(*) AS n FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
UNION ALL
SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
UNION ALL
SELECT 'subtype', Subtype, COUNT(*) FROM fund_master WHERE Subtype IS NOT NULL GROUP BY Subtype
UNION ALL
SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
ORDER BY 1, 3 DESC;

-- ── DISTRIBUCIÓN Fee_Known_Flag ─────────────────────────────────────────
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
GROUP BY Fee_Known_Flag ORDER BY 2 DESC;
-- Esperado: NOT_FOUND ≈ 139, ZERO_CONFIRMED ~1.669, EXTRACTED ~1.396

-- ── VALIDACIÓN VALORES SEMÁNTICOS v24 ──────────────────────────────────
SELECT Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Monetario' GROUP BY Subtype ORDER BY 2 DESC;
SELECT Style_Profile, COUNT(*) FROM fund_master WHERE Fund_Nature='Renta Variable' GROUP BY Style_Profile ORDER BY 2 DESC;
SELECT Market_Cap_Focus, COUNT(*) FROM fund_master WHERE Fund_Nature='Renta Variable' GROUP BY Market_Cap_Focus ORDER BY 2 DESC;
SELECT Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Mixtos' AND Subtype IS NOT NULL GROUP BY Subtype ORDER BY 2 DESC;
SELECT Family, Subtype, COUNT(*) FROM fund_master WHERE Fund_Nature='Monetario' GROUP BY Family, Subtype ORDER BY 1,2;
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
| Family Monetario se mantiene con valores LVNAV/VNAV/CNAV (BL-48-ext) | Normalizar a Family=Monetario | Decisión conservadora hasta confirmar independencia de consumidores P2/P3 |
| BL-51 Problema B: análisis de schema antes de implementar | Implementar directamente nuevos campos | La extensión de schema tiene impacto transversal en pipeline, BD y P3; requiere decisión de diseño formal previa |
| BL-51A: separador decimal opcional en grupo capturador `([\d]+(?:[,.][\d]+)?)` | `([\d]+[,.][\d]+)` (decimal obligatorio) | KIIDs declaran "5%" sin decimales; el patrón obligatorio los descartaba silenciosamente sin error visible |
| BL-51B: 4 campos REAL explícitos para cap/floor vs campo textual `Fee_Structure_Notes` | `Fee_Structure_Notes TEXT` | Tipado fuerte, consultable directamente por P3; función de coste efectivo `min(pct×importe, cap)` implementable sin preprocesado |
| BL-49: extracción Currency_Hedged en cascada (nombre → KIID) | Mantener solo nombre de fondo | 267 fondos no-EUR con gap accionable; la señal existe en KIID y el coste de implementación es bajo |

---

**Fin del documento. Versión v3.1 — 19 de abril de 2026.**
