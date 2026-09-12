# SCHEMA REFERENCE — Base de Datos v22

**Base de datos:** `db/fondos.sqlite`  
**Schema SQL:** `db/schema_fondos.sql`  
**Versión:** v22 (2026-07-05); última revisión de valores: 2026-07-12  
**Propósito:** Referencia rápida de tablas y columnas (sin descripciones largas)

---

## Índice de tablas (machine-verified)

<!-- AUTO:BEGIN schema-reference-tables -->
| Table | Domain |
|-------|--------|
| `fund_master` | P1 |
| `fund_cost_schedule` | P1 |
| `fund_kiid_metadata` | P1 |
| `ingestion_log` | P1 |
| `fund_data_quality_issues` | P1 |
| `fund_families` | P1 |
| `series_macro` | P2 |
| `series_benchmark` | P2 |
| `series_inflation` | P2 |
| `fund_metrics` | P2 |
| `p2_pipeline_log` | P2 |
| `fund_scores` | P3 |
| `portfolio_scenarios` | P3 |
| `portfolio_weights` | P3 |
| `rotation_costs` | P3 |
| `nav_sources` | P2 |
| `fund_benchmarks` | P1 |
| `fund_nav_monthly` | P2 |
| `fund_nav_daily` | P2 |
| `fund_metric_timeseries` | P2 |
| `fund_metric_alerts` | P2 |
| `fund_metric_state` | P2 |
| `fund_cost_corrections` | P1 |
| `audit_statistic` | P1/P2 |
| `audit_finding` | P1/P2 |
<!-- AUTO:END schema-reference-tables -->

---

## Medallion Architecture — Bronze / Silver / Gold

The database follows a logical **Medallion Architecture** matching the physical table structure.
No data migration is required; this section names the layering that already exists.

| Layer | Tables | Description |
|-------|--------|-------------|
| **Bronze** (raw, append-only) | `fund_nav_monthly`, `fund_nav_daily`, `series_macro`, `series_benchmark`, `series_inflation`; `fund_kiid_metadata.Raw_KIID_Text` | Immutable source data. Never transformed in place. |
| **Silver** (validated / normalized) | `fund_master`, `fund_benchmarks`, `fund_cost_schedule`, `fund_families`, `fund_data_quality_issues` | Classified and consistency-checked records; rebuilt each pipeline cycle. |
| **Gold** (calculated indicators) | `fund_metrics`, `fund_metric_timeseries`, `fund_metric_alerts`, `fund_scores`, `portfolio_scenarios`, `portfolio_weights` | Derived outputs; fully recomputable from Bronze+Silver. Each Gold row carries `algorithm_version` (= `CALC_VERSION`) and `batch_id` (per-run id) for audit traceability (v26). |
| **State / control** (cross-cutting) | `fund_metric_state`, `p2_pipeline_log`, `ingestion_log`, `nav_sources` | Pipeline orchestration state; not domain data. |

**Key invariant (P#1 + Gold decoupling):** Gold tables can be truncated and rebuilt from Bronze+Silver
without touching source data. Bump `CALC_VERSION` in `run_pipeline.py` and run with `--force` to
trigger a full Gold recompute. All Gold rows produced by that run share the new `batch_id`.

---

## TABLA 1: fund_master

**Propósito:** Registro maestro de cada clase de fondo (1 fila = 1 ISIN)  
**Clave primaria:** `ISIN` (TEXT)  
**Total columnas:** 42

### Identificación (3 columnas)

| Columna | Tipo | Constraint |
|---------|------|------------|
| ISIN | TEXT | PRIMARY KEY |
| Fund_Name | TEXT | - |
| Management_Company | TEXT | - |

### Clasificación P1 (12 columnas)

| Columna | Tipo | Valores típicos |
|---------|------|-----------------|
| Fund_Nature | TEXT | `Renta Variable` \| `Mixtos` \| `Renta Fija Flexible` \| `Renta Fija Corto Plazo` \| `Monetario` \| `Alternativo` \| `Restantes` \| `Estructurado` |
| Profile | TEXT | `Conservador` \| `Moderado` \| `Agresivo` (SRRI 1-4 / 3-5 / 5-7; ver INTER-3) |
| Type | TEXT | Clasificación interna por naturaleza (ej: `Bolsa Global`, `Renta Fija Europea`) |
| Strategy | TEXT | `Activo` \| `Indexado` \| `Pasivo` |
| Family | TEXT | `Equity Core` \| `Thematic Equity` \| `Fixed Income Flexible` \| `Mixed` \| `Money Market Fund` \| `Absolute Return` \| `Real Assets` \| ... (**EN**, migrado de ES en v20) |
| Style_Profile | TEXT | `Growth` \| `Value` \| `Blend` \| `Income` \| `Low Volatility` \| `Quality` \| `Momentum` \| `Strategic Allocation` \| `Not Applicable` |
| Geography | TEXT | `Global` \| `Europa` \| `EE.UU.` \| `Eurozona` \| `Asia` \| `Japón` \| `China` \| `Emergentes` \| `Norte de África` \| ... (**ES**) |
| Theme | TEXT | `Technology` \| `Healthcare` \| `Climate / Clean Energy` \| `Artificial Intelligence` \| `Gold` \| `Inflation` \| `Megatrends` \| ... (**EN**) |
| Is_ESG | INTEGER | `0` \| `1` |
| Exposure_Bias | TEXT | `Long Only` \| `Long/Short` \| `Market Neutral` \| `Net Short` \| `Not Applicable` |
| Benchmark_Type | TEXT | `Reference Index` \| `Target Index` \| `No Benchmark` |
| Subtype | TEXT | `ETF` \| `Index Fund` \| `Autocallable` \| `Global Macro` \| `Long/Short` \| ... |

### Bloques heurísticos (2 columnas)

| Columna | Tipo | Propósito |
|---------|------|-----------|
| Heuristic_Block | TEXT | Nombre del bloque que clasificó (monetarios \| rf_corto \| ...) |
| Heuristic_Core | TEXT | Núcleo de la clasificación (trazabilidad) |

### SRRI y calidad de datos (3 columnas)

| Columna | Tipo | Valores |
|---------|------|---------|
| SRRI | INTEGER | 1-7 |
| SRRI_Quality_Flag | TEXT | HIGH \| MEDIUM_VISUAL \| LOW |
| Data_Quality_Flag | TEXT | OK \| INFERRED \| WARN \| MISSING (v22, FIX-DQ-1, 2026-07-05) — rollup determinista: el máximo de severidad (`shared.config.DATA_QUALITY_SEVERITY`) entre el nivel base derivado de SRRI_Quality_Flag y todos los issues acumulados durante el ciclo para ese ISIN. El detalle por issue vive en `fund_data_quality_issues` (tabla 5), no en esta columna. |

### Divisa y cobertura (3 columnas)

| Columna | Tipo | Nota |
|---------|------|------|
| Fund_Currency | TEXT | EUR \| USD \| GBP \| CHF \| ... |
| Portfolio_Currency | TEXT | (Obsoleto, no usar -- eliminada en v20) |
| Asset_Currency | TEXT | EUR \| USD \| GBP \| JPY \| CHF \| CNH \| ... (v21, 2026-07-05) — divisa de los activos/estrategia del fondo, inferida del nombre; NULL = fondo diversificado sin mandato de divisa única |
| Hedging_Policy | TEXT | HEDGED \| UNHEDGED \| PARTIALLY_HEDGED |

### Política de inversión (3 columnas)

| Columna | Tipo | Valores |
|---------|------|---------|
| Replication_Method | TEXT | `Physical` \| `Synthetic` \| `Sampling` \| `Active` \| `Not Applicable` |
| Derivatives_Usage | TEXT | `None` \| `Hedging Only` \| `Investment` \| `Both` (MODIFY #12; legacy: `NO`→`None`, `LIMITED`→`Hedging Only`) |
| Benchmark_Declared | TEXT | Nombre del índice/benchmark declarado |

### Costes y condiciones (9 columnas)

| Columna | Tipo | Nota |
|---------|------|------|
| Ongoing_Charge | REAL | TER (Total Expense Ratio) en % anual |
| Accumulation_Policy | TEXT | `Accumulation` \| `Distribution` \| `Mixed` |
| Entry_Fee_Pct | REAL | Comisión entrada en % |
| Exit_Fee_Pct | REAL | Comisión salida en % |
| SFDR_Article | INTEGER | `6` \| `8` \| `9` \| NULL |
| Recommended_Holding_Period | TEXT | Período de tenencia recomendado (texto libre) |
| Leverage_Used | TEXT | `Yes` \| `No` |
| Liquidity_Profile | TEXT | `Daily` \| `Weekly` \| `Bi-Weekly` \| `Monthly` \| `Not Applicable` (legacy: `T1`→`Daily`) |
| Distribution_Frequency | TEXT | `Annual` \| `Semi-Annual` \| `Quarterly` \| `Monthly` (legacy: `BIANNUAL`→`Semi-Annual`) |

### Fund family (1 columna)

| Columna | Tipo | Constraint |
|---------|------|------------|
| fund_family_id | TEXT | FK → fund_families.family_id |

### Trazabilidad (2 columnas)

| Columna | Tipo | Nota |
|---------|------|------|
| Inference_Trace | TEXT | JSON con decisiones de clasificación |
| Updated_At | TEXT | ISO 8601 timestamp última actualización |

### Atributos v3 — fund_characterizer (columnas v16+)

| Columna | Tipo | Valores |
|---------|------|---------|
| Market_Cap_Focus | TEXT | `Large Cap` \| `Mid Cap` \| `Small Cap` \| `All Cap` \| `Not Applicable` |
| Sector_Focus | TEXT | `Technology & Innovation` \| `Healthcare & Life Sciences` \| `Energy & Resources` \| `Utilities & Environment` \| `Materials & Mining` \| `Financial Services` \| `Consumer` \| `Real Assets` (8 buckets GICS-aligned) |
| Investment_Universe | TEXT | `Global` \| `Regional` \| `Country` \| `Thematic` \| `Sector` \| `Liquidity` (legacy: `Liquidity`→`Global` para Monetario via INTER-13-LIQ) |
| Investment_Focus | TEXT | `Broad` \| `Sector` \| `Thematic` |
| Credit_Quality | TEXT | `Investment Grade` \| `High Yield` \| `Mixed` \| `Not Applicable` |
| Duration_Profile | TEXT | `Ultra-Short` \| `Short` \| `Intermediate` \| `Long` \| `Flexible` \| `Not Applicable` |
| MMF_Structure | TEXT | `CNAV` \| `LVNAV` \| `VNAV` \| `Standard MMF` \| `Not Applicable` (MMFR EU 2017/1131) |
| Hedging_Policy | TEXT | `Hedged` \| `Unhedged` \| `Partially Hedged` (legacy: `PARTIAL`→`Partially Hedged`) |
| Asset_Currency | TEXT | ISO-4217 (`EUR`/`USD`/…) \| `MCY` (multi-currency sentinel, PRINCIPIO_10) \| NULL |
| Development_Status | TEXT | `Developed` \| `Emerging` \| `Frontier` \| `Global/Mixed` (MSCI classification) |

### Índices en fund_master

```sql
idx_fm_nature    ON (Fund_Nature)
idx_fm_block     ON (Heuristic_Block)
idx_fm_company   ON (Management_Company)
idx_fm_family    ON (fund_family_id)
```

---

## TABLA 2: fund_kiid_metadata

**Propósito:** Metadatos del documento KIID/DDF de cada fondo  
**Clave primaria:** `(ISIN, KIID_Class)` — Class=1 documento principal  
**Total columnas:** 17

### Clave (2 columnas)

| Columna | Tipo | Constraint |
|---------|------|------------|
| ISIN | TEXT | NOT NULL |
| KIID_Class | INTEGER | NOT NULL, DEFAULT 1 |

### Localización documento (2 columnas)

| Columna | Tipo | Nota |
|---------|------|------|
| KIID_URL | TEXT | URL del PDF KIID |
| KIID_PDF_Hash | TEXT | SHA256 del PDF |

### Estado del ciclo de descarga (1 columna)

| Columna | Tipo | Valores posibles |
|---------|------|------------------|
| KIID_Status | TEXT | CACHED \| OK \| FORCE_REFRESH \| WRONG_DOC \| NOT_FOUND |

**Valores KIID_Status:**
- `CACHED` — Texto en BD, sin descarga HTTP (<1s proceso)
- `OK` — Descarga correcta anterior (igual que CACHED para el pipeline)
- `FORCE_REFRESH` — Re-descarga obligatoria en próximo ciclo
- `WRONG_DOC` — PDF no corresponde al ISIN
- `NOT_FOUND` — URL no responde

### Contenido extraído (4 columnas)

| Columna | Tipo | Nota |
|---------|------|------|
| Language | TEXT | ES \| EN \| FR \| DE \| IT \| NULL (fused OCR) |
| Raw_KIID_Text | TEXT | Texto completo extraído del PDF |
| KIID_Published_Date | TEXT | Fecha de publicación del KIID |
| KIID_Downloaded_At | TEXT | Timestamp descarga (ISO 8601) |

### SRRI (4 columnas)

| Columna | Tipo | Valores |
|---------|------|---------|
| SRRI | INTEGER | 1-7 (valor consolidado) |
| SRRI_Visual | INTEGER | 1-7 (extracción visual) \| NULL |
| SRRI_Textual | INTEGER | 1-7 (extracción textual) \| NULL |
| SRRI_Validation_Status | TEXT | MATCH \| TEXT_ONLY \| VISUAL_ONLY \| CONFLICT \| NOT_AVAILABLE |

**Valores SRRI_Validation_Status:**
- `MATCH` — Visual = Textual (HIGH confidence)
- `TEXT_ONLY` — Solo extracción textual (MEDIUM confidence)
- `VISUAL_ONLY` — Solo extracción visual (MEDIUM_VISUAL confidence)
- `CONFLICT` — Visual ≠ Textual (LOW confidence)
- `NOT_AVAILABLE` — Ni visual ni textual disponible

### Telemetría de proceso (2 columnas, v16)

| Columna | Tipo | Nota |
|---------|------|------|
| Processing_Time_Ms | INTEGER | **ATENCIÓN:** Almacena segundos (no ms) — bug conocido P13 |
| Processing_Breakdown | TEXT | JSON con tiempos por fase |

### Índices en fund_kiid_metadata

```sql
idx_km_status    ON (KIID_Status)
idx_km_srri_val  ON (SRRI_Validation_Status)
idx_km_visual    ON (SRRI_Visual)
```

---

## TABLA 3: ingestion_log

**Propósito:** Registro de eventos del pipeline (errores, avisos, trazas)  
**Clave primaria:** `id` (AUTOINCREMENT)  
**Total columnas:** 6

| Columna | Tipo | Constraint | Nota |
|---------|------|------------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | - |
| ISIN | TEXT | - | ISIN del fondo afectado |
| step | TEXT | - | Nombre del paso/bloque (ej: "monetarios", "SRRI_extraction") |
| status | TEXT | - | ERROR \| WARNING \| INFO |
| message | TEXT | - | Descripción del evento |
| created_at | TEXT | - | ISO 8601 timestamp |

**IMPORTANTE:** Columnas canónicas son `step` y `status` (no `block`/`level` — nombres históricos obsoletos)

### Índices en ingestion_log

```sql
idx_log_isin     ON (ISIN)
idx_log_status   ON (status)
```

---

## TABLA 4: fund_families

**Propósito:** Agrupación de clases de un mismo fondo  
**Módulo:** `fund_family_builder.py`  
**Clave primaria:** `family_id` (TEXT)  
**Total columnas:** 5

| Columna | Tipo | Nota |
|---------|------|------|
| family_id | TEXT | PRIMARY KEY (ej: FAM_001234) |
| family_name | TEXT | Nombre representativo de la familia |
| Fund_Nature | TEXT | Naturaleza consolidada de la familia |
| n_funds | INTEGER | Número de clases en la familia |
| Updated_At | TEXT | ISO 8601 timestamp |

**Reglas de consistencia:**
- Todas las clases de una familia deben tener la misma `Fund_Nature`
- Si hay inconsistencia, `fund_family_builder.py` aplica reglas de corrección:
  - Regla 1: Mayoría simple
  - Regla 2: SRRI más alto
  - Regla 3: Benchmarks más poblados
  - Regla 4 (v16): Nombre + SRRI para familias 50/50

---

## TABLA 5: fund_data_quality_issues (v22, FIX-DQ-1, 2026-07-05)

**Propósito:** Estado ACTUAL de issues de calidad de datos por fondo — complementa a `ingestion_log`
**Clave primaria:** `id` (AUTOINCREMENT); unicidad lógica por `(ISIN, check_code)`
**Total columnas:** 6

| Columna | Tipo | Constraint | Nota |
|---------|------|------------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | - |
| ISIN | TEXT | NOT NULL | ISIN del fondo afectado |
| check_code | TEXT | NOT NULL | Código del chequeo (ej. `FUNDCCY_NAME_KIID_MISMATCH`, `HEDGCCY_NO_MISMATCH_INCONSISTENCY`) |
| level | TEXT | NOT NULL | OK \| INFERRED \| WARN \| MISSING — **mismo vocabulario que `fund_master.Data_Quality_Flag`**, NO el de `ingestion_log.status` (son dos vocabularios distintos, ver `shared.config.DATA_QUALITY_SEVERITY`) |
| message | TEXT | - | Descripción del issue |
| detected_at | TEXT | - | ISO 8601 timestamp del ciclo que lo detectó |

**Diferencia clave con `ingestion_log`:** `ingestion_log` es un histórico append-only de TODOS los eventos de TODOS los ciclos (nunca se borra). `fund_data_quality_issues` se **reconstruye por completo en cada ciclo** (`DELETE FROM ... WHERE ISIN=?` seguido de un INSERT por issue activo) — consultar esta tabla responde "¿qué está mal con el fondo X ahora mismo?" sin tener que filtrar el histórico completo ni deducir cuál es el evento más reciente por `(ISIN, step)`.

`fund_master.Data_Quality_Flag` es el rollup rápido para `WHERE Data_Quality_Flag != 'OK'`; esta tabla es el detalle consultable por issue. Ambos se calculan juntos, una sola vez por ISIN, en `_finalize_data_quality_issues()` (`proyecto1/core/pipeline.py`), justo antes de `publish_fund`.

### Índices en fund_data_quality_issues

```sql
idx_dqissues_isin       ON (ISIN)
idx_dqissues_isin_code  ON (ISIN, check_code)  -- UNIQUE
```

---

## TABLA 6: fund_benchmarks (v22, añadida 2026-03-28)

**Propósito:** Benchmark de mercado asociado a cada fondo; señal independiente para
el cluster de consistencia SC-H (`MODELO_SEMANTICO.md` §7 Cluster H).  
**Clave primaria:** `(ISIN, source)`  
**Total columnas:** 8

| Columna | Tipo | Descripción |
|---------|------|-------------|
| ISIN | TEXT | FK → fund_master.ISIN |
| source | TEXT | `MORNINGSTAR` o `KIID` — fuente del benchmark |
| asset_class | TEXT | Asset class canónica del benchmark (`'Equity'`, `'Fixed Income'`, `'Rate'`, `'Mixed'`, `'Money Market'`, `'Commodity'`) |
| benchmark_role | TEXT | `'asset_proxy'` (benchmark de rentabilidad) o `'hurdle_rate'` (referencia de superación — no implica clase de activo) |
| benchmark_name | TEXT | Nombre completo del benchmark (p.ej. `'MSCI World Net Return EUR'`) |
| confidence | TEXT | `HIGH` / `MEDIUM` / `LOW` — calidad de la extracción |
| updated_at | TEXT | ISO 8601 timestamp de la última actualización |
| notes | TEXT | Notas libres (método de extracción, variantes) |

**Prioridad de fuente para SC-H:** `MORNINGSTAR` > `KIID`. El pipeline batch-carga ambas filas y
usa la MORNINGSTAR; si no hay fila MORNINGSTAR para un ISIN, recae en KIID.

**Carga inicial:** `benchmark_loader --mode load` (2026-03-28, fuente: Morningstar SAL API).  
**Actualización incremental:** `benchmark_loader --mode update` — ejecutado en cada ciclo P1
como Paso 0b en `P1_discoverAllFunds.bat` (solo ISINs nuevos sin fila MORNINGSTAR, rápido).  
**Refresh periódico:** `P1_refreshBenchmarks.bat` con argumento `load` — recomendado cada 1-3
meses para mantener los nombres de benchmark actualizados (afecta la tokenización en SC-H2/H3).

**Nota de diseño:** las filas KIID se generan por el clasificador al parsear el KIID del fondo
y son semi-redundantes (misma fuente que el clasificador); los errores que SC-H detecta
típicamente proceden de la fila MORNINGSTAR, que es genuinamente independiente.

---

## RELACIONES ENTRE TABLAS

```
fund_master (ISIN)
    ├─→ fund_kiid_metadata (ISIN, KIID_Class)
    ├─→ ingestion_log (ISIN)
    ├─→ fund_data_quality_issues (ISIN)
    ├─→ fund_benchmarks (ISIN)          [1:N por source]
    └─→ fund_families (fund_family_id)

fund_kiid_metadata
    └─→ fund_master (ISIN)

fund_families (family_id)
    └─→ fund_master (fund_family_id) [1:N]
```

---

## QUERIES DE VERIFICACIÓN RÁPIDA

### Verificar columnas v16 presentes
```sql
SELECT name FROM pragma_table_info('fund_master')
WHERE name IN ('Market_Cap_Focus','Sector_Focus','Currency_Hedged','Investment_Universe');
-- Debe devolver 4 filas
```

### Contar fondos por naturaleza
```sql
SELECT Fund_Nature, COUNT(*) AS n
FROM fund_master
GROUP BY Fund_Nature
ORDER BY n DESC;
```

### Verificar integridad ISIN (fund_master ↔ fund_kiid_metadata)
```sql
SELECT COUNT(*) AS orphan_kiid
FROM fund_kiid_metadata km
LEFT JOIN fund_master fm ON km.ISIN = fm.ISIN
WHERE fm.ISIN IS NULL;
-- Debe devolver 0
```

### Fondos sin familia asignada
```sql
SELECT COUNT(*) AS sin_familia
FROM fund_master
WHERE fund_family_id IS NULL;
```

### Distribución SRRI_Validation_Status
```sql
SELECT SRRI_Validation_Status, COUNT(*) AS n
FROM fund_kiid_metadata
WHERE KIID_Class = 1
GROUP BY SRRI_Validation_Status
ORDER BY n DESC;
```

---

---

## TABLAS P2 — Métricas cuantitativas

### TABLA P2-1: fund_nav_monthly

**Propósito:** Series mensuales de NAV por ISIN — último NAV de cada mes (fuente: Morningstar)  
**Clave primaria:** `(ISIN, Date)`  
**Módulo productor:** `proyecto2/src/discovery/nav_discovery.py`  
**Nota v24:** incorporada al schema canónico `schema_fondos.sql` (antes se creaba fuera).

| Columna | Tipo | Nota |
|---------|------|------|
| ISIN | TEXT PK | FK → fund_master |
| Date | DATE PK | Último día del mes |
| NAV | REAL | Valor liquidativo (total return index) |
| NAV_Currency | TEXT | Divisa (ej. EUR) |
| NAV_Type | TEXT | `NAV` por defecto |
| Is_Estimated | INTEGER | 0=real, 1=estimado |
| Data_Source | TEXT | `MORNINGSTAR` |
| Ingested_At | TIMESTAMP | Timestamp de ingesta |

### TABLA P2-1b: fund_nav_daily  *(NUEVA v24)*

**Propósito:** Serie diaria de NAV por ISIN, antes del resample a mensual. Usada para métricas de
horizonte corto (`rolling_1m`, `rolling_3m`, `rolling_6m`) con corrección de iliquidez
(AC-adjusted volatility). Métricas escritas con `metric_version='d1'` en `fund_metrics`.  
**Clave primaria:** `(ISIN, Date)`  
**Módulo productor:** `proyecto2/src/discovery/nav_discovery.py`  
**Módulo calculador:** `proyecto2/src/calculations/short_horizon.py`

| Columna | Tipo | Nota |
|---------|------|------|
| ISIN | TEXT PK | FK → fund_master |
| Date | DATE PK | Fecha de sesión |
| NAV | REAL | Valor liquidativo diario (total return index) |
| NAV_Currency | TEXT | Divisa |
| NAV_Type | TEXT | `TOTAL_RETURN_IDX` |
| Is_Estimated | INTEGER | 0=real, 1=estimado |
| Data_Source | TEXT | `MORNINGSTAR` |
| Ingested_At | TIMESTAMP | Timestamp de ingesta |

**Volumen esperado:** ~6–12M filas (~3,200 fondos × ~10 años × ~252 días/año).  
**Nota operacional:** backfill mediante `--mode load` en `nav_discovery.py` (proceso largo, ejecutar
off-peak). SQLite WAL + commit-por-lote evitan bloqueos concurrentes.

### TABLA P2-2: nav_sources

**Propósito:** Resultado del proceso de descubrimiento de fuentes NAV por ISIN  
**Clave primaria:** `ISIN`  
**Módulo:** `proyecto2/src/discovery/nav_discovery.py`

| Columna | Tipo | Valores / Nota |
|---------|------|----------------|
| isin | TEXT | PRIMARY KEY; FK → fund_master |
| source | TEXT | `MORNINGSTAR` \| `CNMV` \| `NOT_FOUND` |
| source_id | TEXT | ID interno: Morningstar ej. 'F0GBR04S23'; CNMV: código registro |
| first_nav_date | DATE | Fecha más antigua disponible en la fuente |
| last_nav_date | DATE | Fecha más reciente disponible |
| nav_count | INTEGER | Nº de NAV disponibles en la fuente |
| discovered_at | DATE | Fecha de primer descubrimiento |
| last_checked | DATE | Fecha de última verificación |
| status | TEXT | `OK` \| `NOT_FOUND` \| `ERROR` |
| data_status | TEXT | **v25 — máquina de estados NAV** (análoga a `KIID_Status` en P1): `OK` (normal) \| `FORCE_REFRESH` (re-descarga completa desde Morningstar) \| `RECALCULATE_MONTHLY` (resamplear diario→mensual sin red) \| `RECALCULATE_METRICS` (recalcular métricas P2 sin tocar NAV) \| `PENDING` (descubierto, aún no cargado). El pipeline consume y resetea el flag a `OK` tras cada operación. |

### TABLA P2-3: series_macro

**Propósito:** Indicadores macroeconómicos de contexto (BCE SDW, Eurostat, INE, Fed FRED)  
**Clave primaria:** `(date, indicator, geography)`

| Columna | Tipo | Valores / Nota |
|---------|------|----------------|
| date | DATE | Fecha del dato (mensual) |
| indicator | TEXT | Código normalizado — ver catálogo en `schema_fondos.sql` (`ipc_index`, `rate_policy`, `oil_wti`, `dxy`, `spread_hy`, `vix`, `term_spread`, `m2_yoy`, etc.) |
| geography | TEXT | `ES` \| `EU` \| `US` \| `JP` \| `CN` \| `GLOBAL` |
| value | REAL | Valor del indicador |
| unit | TEXT | `ratio` \| `index` \| `pct` \| `usd_bn` |
| source | TEXT | `BCE` \| `EUROSTAT` \| `INE` \| `FRED` \| `IMF` \| `CALC` |
| load_ts | TIMESTAMP | Timestamp de carga |

**Índices:** `idx_macro_indicator ON (indicator, geography)`, `idx_macro_date ON (date)`

### TABLA P2-4: fund_metrics

**Propósito:** Todas las métricas calculadas por fondo (una fila por combinación métrica/horizonte)  
**Clave primaria:** `(isin, metric, horizon, real_flag, metric_version)`

| Columna | Tipo | Valores / Nota |
|---------|------|----------------|
| isin | TEXT | FK → fund_master |
| metric | TEXT | Nombre canónico — ver catálogo completo en `schema_fondos.sql` (`return_ann`, `sharpe`, `max_drawdown`, `beta_rate_eu`, `macro_r2`, `return_ann_expansion`, etc.) |
| horizon | TEXT | `since_inception` \| `rolling_10y` \| `rolling_5y` \| `rolling_3y` \| `rolling_1y` \| `ytd` \| `crisis_2008` \| `crisis_2011` \| `crisis_2020` \| `crisis_2022` |
| value | REAL | Valor de la métrica |
| real_flag | INTEGER | `0` = nominal; `1` = deflactado por IPC |
| calculation_date | DATE | Fecha del cálculo |
| metric_version | TEXT | Versión del algoritmo (default `v1`) |
| benchmark_id | TEXT | NULL si métrica absoluta |
| source_rows | INTEGER | Nº de NAV usados en el cálculo |

### TABLA P2-5: p2_pipeline_log

**Propósito:** Trazabilidad operativa por ejecución del pipeline de cálculo P2  
**Clave primaria:** `id` (AUTOINCREMENT)

| Columna | Tipo | Valores |
|---------|------|---------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| isin | TEXT | ISIN procesado |
| step | TEXT | `NAV_LOAD` \| `DEFLATE` \| `CALC_METRICS` \| `WRITE` |
| status | TEXT | `OK` \| `WARN` \| `ERROR` \| `SKIP` |
| horizon | TEXT | Horizonte de cálculo |
| metric_version | TEXT | Versión de la métrica |
| message | TEXT | Descripción del evento |
| created_at | TIMESTAMP | Timestamp |

---

## TABLAS P3 — Scoring y cartera

### TABLA P3-1: fund_scores

**Propósito:** Scoring compuesto pre-selección por bloque de cartera  
**Clave primaria:** `(isin, block, score_version)`

| Columna | Tipo | Valores / Nota |
|---------|------|----------------|
| isin | TEXT | FK → fund_master |
| block | TEXT | Sub-cartera: `Defensiva` \| `Equilibrada` \| `Dinámica` (o equivalente por naturaleza) |
| score_version | TEXT | Versión del algoritmo de scoring (default `v1`) |
| score_total | REAL | Score compuesto final |
| score_detail | TEXT | JSON con desglose por componente |
| eligible | INTEGER | `0` = no supera hard filters; `1` = supera hard filters |
| calculated_at | DATE | Fecha del cálculo |
| notes | TEXT | Observaciones opcionales |

### TABLA P3-2: portfolio_scenarios

**Propósito:** Escenarios de cartera construidos en P3  
**Clave primaria:** `scenario_id`

| Columna | Tipo | Valores / Nota |
|---------|------|----------------|
| scenario_id | TEXT | Ej. `'defensiva_2026Q1'` |
| profile | TEXT | `Defensiva` \| `Equilibrada` \| `Crecimiento` |
| macro_regime | TEXT | Régimen macro activo: `Expansion` \| `Recalentamiento` \| `Recalentamiento_Tardio` \| `Estanflacion` \| `Contraccion` \| `Shock_Energetico` \| `Crisis_Financiera` |
| created_at | DATE | Fecha de creación del escenario |
| notes | TEXT | Observaciones opcionales |

### TABLA P3-3: portfolio_weights

**Propósito:** Pesos por fondo en cada escenario de cartera  
**Clave primaria:** `(scenario_id, isin)`

| Columna | Tipo | Valores / Nota |
|---------|------|----------------|
| scenario_id | TEXT | FK → portfolio_scenarios |
| isin | TEXT | FK → fund_master |
| block | TEXT | Sub-cartera a la que pertenece el fondo en este escenario |
| weight | REAL | Peso en cartera [0, 1]; constraints: max 20% por fondo, max 30% por gestora, min 3% |
| role | TEXT | Descripción del rol en cartera (opcional) |
| notes | TEXT | Observaciones opcionales |

---

## NOTAS CRÍTICAS

### COALESCE en sqlite_writer.py

Columnas con COALESCE (preservan valor anterior si nuevo es NULL):
- `Raw_KIID_Text`
- `KIID_Downloaded_At`
- `SRRI_Textual`
- `Language`
- Todas las columnas extraídas del KIID

Columnas SIN COALESCE (sobreescriben siempre):
- `KIID_Status` (pero con lógica: nuevo=CACHED preserva previo si era OK)
- `SRRI_Visual` (regenerado cada ciclo)

### Bugs conocidos

| ID | Columna | Descripción | Estado |
|----|---------|-------------|--------|
| P13 | Processing_Time_Ms | Almacena segundos, no milisegundos | Pendiente renombrar |
| - | KIID_Downloaded_At | 478 fondos con NULL (herencia bug previo) | Autosanante con próximas descargas |

---

---

## NOTAS DE EVOLUCIÓN DE SCHEMA

| Versión | Fecha | Cambio principal |
|---------|-------|-----------------|
| v16 | 2026-03-31 | Añade columnas v3: Market_Cap_Focus, Sector_Focus, Currency_Hedged, Investment_Universe |
| v17 | ~2026-04 | Adds telemetría (Processing_Time_Ms, Processing_Breakdown) |
| v20 | ~2026-05 | Family migrada a inglés (RV Núcleo→Equity Core, etc.); Schema MODIFY #12: Derivatives_Usage binary→purpose-based |
| v21 | 2026-07-05 | Añade Asset_Currency; elimina Portfolio_Currency |
| v22 | 2026-07-05 | Añade fund_data_quality_issues; Data_Quality_Flag rollup determinista |

---

**FIN SCHEMA REFERENCE**

*Última actualización: 2026-07-12*  
*Schema version: v22 (2026-07-05)*
