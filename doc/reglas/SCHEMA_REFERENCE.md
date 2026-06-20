# SCHEMA REFERENCE — Base de Datos v16

**Base de datos:** `db/fondos.sqlite`  
**Schema SQL:** `db/schema_fondos.sql`  
**Versión:** v16 (31-mar-2026)  
**Propósito:** Referencia rápida de tablas y columnas (sin descripciones largas)

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
| Fund_Nature | TEXT | Renta Variable \| Mixtos \| Renta Fija Flexible \| Renta Fija Corto Plazo \| Monetario \| Alternativo \| Restantes \| Estructurado |
| Profile | TEXT | Agresivo \| Moderado \| Conservador \| Muy Conservador |
| Type | TEXT | Depende de Nature (ej: Bolsa Global, Renta Fija Europea) |
| Strategy | TEXT | - |
| Family | TEXT | - |
| Style_Profile | TEXT | Growth \| Value \| Blend \| - |
| Geography | TEXT | Global \| Europa \| EE.UU. \| Eurozona \| Asia \| Japón \| ... |
| Theme | TEXT | Tecnología \| Salud \| Sostenibilidad \| ... |
| Is_ESG | INTEGER | 0 \| 1 |
| Exposure_Bias | TEXT | Large Cap \| Mid Cap \| Small Cap \| Multi Cap |
| Benchmark_Type | TEXT | - |
| Subtype | TEXT | - |

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
| Data_Quality_Flag | TEXT | - |

### Divisa y cobertura (3 columnas)

| Columna | Tipo | Nota |
|---------|------|------|
| Fund_Currency | TEXT | EUR \| USD \| GBP \| CHF \| ... |
| Portfolio_Currency | TEXT | (Obsoleto, no usar) |
| Hedging_Policy | TEXT | HEDGED \| UNHEDGED \| PARTIALLY_HEDGED |

### Política de inversión (3 columnas)

| Columna | Tipo | Valores |
|---------|------|---------|
| Replication_Method | TEXT | PHYSICAL \| SYNTHETIC \| PASSIVE \| ACTIVE |
| Derivatives_Usage | TEXT | YES \| NO \| LIMITED |
| Benchmark_Declared | TEXT | Nombre del índice/benchmark declarado |

### Costes y condiciones (9 columnas)

| Columna | Tipo | Nota |
|---------|------|------|
| Ongoing_Charge | REAL | TER (Total Expense Ratio) en % anual |
| Accumulation_Policy | TEXT | ACCUMULATION \| DISTRIBUTION \| MIXED |
| Entry_Fee_Pct | REAL | Comisión entrada en % |
| Exit_Fee_Pct | REAL | Comisión salida en % |
| Sfdr_Article | INTEGER | 6 \| 8 \| 9 \| NULL |
| Recommended_Holding_Period | TEXT | - |
| Leverage_Used | TEXT | YES \| NO \| MODERATE \| HIGH |
| Liquidity_Profile | TEXT | DAILY \| WEEKLY \| MONTHLY \| ... |
| Distribution_Frequency | TEXT | ANNUAL \| QUARTERLY \| MONTHLY \| ... |

### Fund family (1 columna)

| Columna | Tipo | Constraint |
|---------|------|------------|
| fund_family_id | TEXT | FK → fund_families.family_id |

### Trazabilidad (2 columnas)

| Columna | Tipo | Nota |
|---------|------|------|
| Inference_Trace | TEXT | JSON con decisiones de clasificación |
| Updated_At | TEXT | ISO 8601 timestamp última actualización |

### Atributos v3 — fund_characterizer (4 columnas, v16)

| Columna | Tipo | Valores |
|---------|------|---------|
| Market_Cap_Focus | TEXT | Large Cap \| Mid Cap \| Small Cap \| Multi Cap |
| Sector_Focus | TEXT | Technology \| Healthcare \| Financials \| ... |
| Currency_Hedged | TEXT | YES \| NO \| PARTIAL |
| Investment_Universe | TEXT | Global \| Regional \| Country-specific |

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

## RELACIONES ENTRE TABLAS

```
fund_master (ISIN)
    ├─→ fund_kiid_metadata (ISIN, KIID_Class)
    ├─→ ingestion_log (ISIN)
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

**FIN SCHEMA REFERENCE**

*Última actualización: 5 abril 2026*  
*Schema version: v16 (31-mar-2026)*  
*Tokens estimados: ~2.800*
