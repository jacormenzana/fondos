-- ============================================================
-- schema_fondos.sql  — v16  (2026-03-31)
-- Base de datos: db/fondos.sqlite
-- ============================================================
-- Historial de versiones relevantes:
--   v10  Esquema base: fund_master, fund_kiid_metadata, ingestion_log
--   v12  fund_family_id en fund_master; SRRI_Visual/Textual en kiid_metadata
--   v14  Benchmark_Declared, Leverage_Used, Distribution_Frequency
--   v15  SRRI_Quality_Flag, Data_Quality_Flag, Inference_Trace
--   v16  Atributos v3 characterizer (fund_master) + telemetría (kiid_metadata)
-- ============================================================

-- ============================================================
-- TABLA 1: fund_master
-- Registro maestro de cada clase de fondo (1 fila = 1 ISIN)
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_master (

    -- ── Identificación ────────────────────────────────────────
    ISIN                        TEXT PRIMARY KEY,
    Fund_Name                   TEXT,
    Management_Company          TEXT,

    -- ── Clasificación P1 ──────────────────────────────────────
    -- Naturaleza y perfil asignados por el pipeline de clasificación
    Fund_Nature                 TEXT,   -- Renta Variable | Renta Fija Corto Plazo |
                                        -- Renta Fija Flexible | Mixtos | Monetario |
                                        -- Alternativo | Estructurado | Restantes
    Profile                     TEXT,   -- sub-perfil interno del bloque (p.ej. "Equity Growth")
    Type                        TEXT,   -- tipo canónico (p.ej. "Index", "Active", "ETF")
    Strategy                    TEXT,
    Family                      TEXT,
    Style_Profile               TEXT,   -- Value | Growth | Blend | Income | …
    Geography                   TEXT,   -- Global | Europe | USA | EM | España | …
    Theme                       TEXT,   -- Technology | Healthcare | Water | ESG | …
    Is_ESG                      INTEGER DEFAULT 0,  -- 1 si SFDR Art.8/9 o label ESG
    Exposure_Bias               TEXT,
    Benchmark_Type              TEXT,   -- Equity | Fixed Income | Rate | Commodity | Mixed
    Subtype                     TEXT,

    -- ── Atributos v3 — fund_characterizer (v16) ──────────────
    Market_Cap_Focus            TEXT,   -- Large | Mid | Small | Multi
    Sector_Focus                TEXT,   -- sector temático granular (más específico que Theme)
    Currency_Hedged             TEXT,   -- HEDGED | UNHEDGED | MULTI
    Investment_Universe         TEXT,   -- Global | Regional | Country | Sector | Thematic | Liquidity
    Accumulation_Policy         TEXT,   -- ACCUMULATION | DISTRIBUTION

    -- ── Bloques heurísticos ───────────────────────────────────
    Heuristic_Block             TEXT,   -- bloque primario de clasificación (clave en restantes.py)
    Heuristic_Core              TEXT,   -- señal que activó el bloque

    -- ── SRRI y calidad de datos ───────────────────────────────
    SRRI                        INTEGER,            -- 1-7, valor canónico
    SRRI_Quality_Flag           TEXT,               -- HIGH | MEDIUM_TEXT | MEDIUM_VISUAL |
                                                    -- LOW_CONFLICT | NONE
    Data_Quality_Flag           TEXT,   -- OK | WARN | MISSING

    -- ── Divisa y cobertura ────────────────────────────────────
    Fund_Currency               TEXT,   -- EUR | USD | GBP | CHF | …
    Portfolio_Currency          TEXT,
    Hedging_Policy              TEXT,   -- HEDGED | UNHEDGED | PARTIAL

    -- ── Política de inversión ─────────────────────────────────
    Replication_Method          TEXT,   -- PHYSICAL | SYNTHETIC | SAMPLING
    Derivatives_Usage           TEXT,   -- YES | NO | HEDGING_ONLY
    Benchmark_Declared          TEXT,   -- nombre normalizado del benchmark declarado

    -- ── Costes y condiciones ─────────────────────────────────
    Ongoing_Charge              REAL,   -- TER decimal (0.015 = 1.5%)
    Entry_Fee_Pct               REAL,   -- comisión entrada decimal
    Exit_Fee_Pct                REAL,   -- comisión salida decimal
    Sfdr_Article                INTEGER,-- 6 | 8 | 9
    Recommended_Holding_Period  TEXT,   -- "3Y" | "5Y" | …
    Leverage_Used               TEXT,   -- YES | NO
    Liquidity_Profile           TEXT,
    Distribution_Frequency      TEXT,   -- ACCUMULATION | ANNUAL | QUARTERLY | MONTHLY

    -- ── Fund family ───────────────────────────────────────────
    fund_family_id              TEXT,   -- FK → fund_families (si se usa)

    -- ── Trazabilidad ─────────────────────────────────────────
    Inference_Trace             TEXT,   -- cadena de señales que llevaron a la clasificación
    Updated_At                  TEXT    -- ISO-8601 timestamp última actualización
);

-- Índices de búsqueda frecuente
CREATE INDEX IF NOT EXISTS idx_fm_nature    ON fund_master (Fund_Nature);
CREATE INDEX IF NOT EXISTS idx_fm_block     ON fund_master (Heuristic_Block);
CREATE INDEX IF NOT EXISTS idx_fm_company   ON fund_master (Management_Company);
CREATE INDEX IF NOT EXISTS idx_fm_family    ON fund_master (fund_family_id);


-- ============================================================
-- TABLA 2: fund_kiid_metadata
-- Metadatos del documento KIID/DDF de cada fondo
-- Una fila por (ISIN, KIID_Class): Class=1 documento principal
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_kiid_metadata (

    -- ── Clave ─────────────────────────────────────────────────
    ISIN                        TEXT    NOT NULL,
    KIID_Class                  INTEGER NOT NULL DEFAULT 1,

    -- ── Localización del documento ───────────────────────────
    KIID_URL                    TEXT,
    KIID_PDF_Hash               TEXT,   -- SHA-256 del PDF descargado (detección de cambios)

    -- ── Estado del ciclo de descarga ─────────────────────────
    -- CACHED         → texto en BD, no se re-descarga
    -- OK             → descarga correcta en ciclo anterior (igual que CACHED para el pipeline)
    -- FORCE_REFRESH  → re-descarga obligatoria en el próximo ciclo
    -- WRONG_DOC      → el PDF descargado no corresponde al ISIN (excluido del pipeline)
    -- NOT_FOUND      → URL no responde o fondo retirado
    KIID_Status                 TEXT    DEFAULT 'CACHED',

    -- ── Contenido extraído ───────────────────────────────────
    Language                    TEXT,   -- ES | EN | FR | DE | PT | IT
    Raw_KIID_Text               TEXT,   -- texto plano extraído del PDF (para clasificación)
    KIID_Published_Date         TEXT,   -- fecha de publicación del documento (ISO-8601)
    KIID_Downloaded_At          TEXT,   -- timestamp de última descarga (ISO-8601)

    -- ── SRRI ─────────────────────────────────────────────────
    -- SRRI canónico (resultado del desempate visual vs textual)
    SRRI                        INTEGER,
    -- Visual: extraído por el extractor geométrico v4/v5 del widget gráfico del PDF
    -- Solo se extrae cuando hay pdf_bytes (FORCE_REFRESH o fondo nuevo)
    SRRI_Visual                 INTEGER,
    -- Textual: extraído de la capa de texto del PDF o del Raw_KIID_Text
    -- Jerarquía: L0_FUSED > L0 > L1 > L2
    SRRI_Textual                INTEGER,
    -- Estado de validación cruzada:
    --   MATCH          → Visual = Textual (HIGH quality)
    --   TEXT_ONLY      → Textual existe, Visual ausente o sospechoso
    --   VISUAL_ONLY    → Visual existe, Textual no extraíble
    --   CONFLICT       → Visual ≠ Textual, desempate pendiente o fallido
    --   NOT_AVAILABLE  → ninguno extraíble
    SRRI_Validation_Status      TEXT,

    -- ── Telemetría de proceso (v16) ───────────────────────────
    -- Tiempo total de proceso del fondo en el pipeline (ms)
    Processing_Time_Ms          INTEGER,
    -- Desglose por fase: "kiid_fetch:120ms|kiid_parse:3400ms|classify:45ms"
    Processing_Breakdown        TEXT,

    PRIMARY KEY (ISIN, KIID_Class)
);

-- Índices de búsqueda frecuente
CREATE INDEX IF NOT EXISTS idx_km_status    ON fund_kiid_metadata (KIID_Status);
CREATE INDEX IF NOT EXISTS idx_km_srri_val  ON fund_kiid_metadata (SRRI_Validation_Status);
CREATE INDEX IF NOT EXISTS idx_km_visual    ON fund_kiid_metadata (SRRI_Visual);


-- ============================================================
-- TABLA 3: ingestion_log
-- Registro de eventos del pipeline (errores, avisos, trazas)
-- ============================================================
CREATE TABLE IF NOT EXISTS ingestion_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ISIN        TEXT,
    block       TEXT,   -- nombre del bloque o fase que genera el evento
    level       TEXT,   -- INFO | WARN | ERROR
    message     TEXT,
    ts          TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_log_isin  ON ingestion_log (ISIN);
CREATE INDEX IF NOT EXISTS idx_log_level ON ingestion_log (level);


-- ============================================================
-- TABLA 4: fund_families  (opcional — si se usa fund_family_builder)
-- Agrupación de clases del mismo subfondo
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_families (
    family_id       TEXT PRIMARY KEY,   -- p.ej. "FAM_000001"
    family_name     TEXT,               -- nombre normalizado de la familia
    Fund_Nature     TEXT,               -- naturaleza consenso de la familia
    n_funds         INTEGER,            -- número de clases en la familia
    Updated_At      TEXT
);


-- ============================================================
-- SCRIPTS DE MIGRACIÓN (para BD existentes)
-- Ejecutar solo si la BD ya existe y faltan columnas.
-- Son idempotentes si se ejecutan con sqlite3 -bail OFF
-- ============================================================

-- v16: atributos v3 (fund_characterizer)
-- ALTER TABLE fund_master ADD COLUMN Market_Cap_Focus    TEXT;
-- ALTER TABLE fund_master ADD COLUMN Sector_Focus        TEXT;
-- ALTER TABLE fund_master ADD COLUMN Currency_Hedged     TEXT;
-- ALTER TABLE fund_master ADD COLUMN Investment_Universe TEXT;
-- ALTER TABLE fund_master ADD COLUMN Accumulation_Policy TEXT;

-- v16: telemetría de proceso
-- ALTER TABLE fund_kiid_metadata ADD COLUMN Processing_Time_Ms   INTEGER;
-- ALTER TABLE fund_kiid_metadata ADD COLUMN Processing_Breakdown TEXT;

-- ============================================================
-- QUERIES DE VERIFICACIÓN POST-MIGRACIÓN
-- ============================================================

-- Verificar columnas v3 en fund_master:
-- SELECT name FROM pragma_table_info('fund_master')
--   WHERE name IN ('Market_Cap_Focus','Sector_Focus','Currency_Hedged',
--                  'Investment_Universe','Accumulation_Policy');
-- → debe devolver 5 filas

-- Verificar telemetría en fund_kiid_metadata:
-- SELECT name FROM pragma_table_info('fund_kiid_metadata')
--   WHERE name IN ('Processing_Time_Ms','Processing_Breakdown');
-- → debe devolver 2 filas

-- Estado SRRI del universo:
-- SELECT
--     COUNT(*) AS total,
--     SUM(CASE WHEN SRRI_Visual IS NOT NULL THEN 1 ELSE 0 END) AS con_visual,
--     SUM(CASE WHEN SRRI_Validation_Status = 'MATCH'         THEN 1 ELSE 0 END) AS match,
--     SUM(CASE WHEN SRRI_Validation_Status = 'TEXT_ONLY'     THEN 1 ELSE 0 END) AS text_only,
--     SUM(CASE WHEN SRRI_Validation_Status = 'CONFLICT'      THEN 1 ELSE 0 END) AS conflict,
--     SUM(CASE WHEN SRRI_Validation_Status = 'VISUAL_ONLY'   THEN 1 ELSE 0 END) AS visual_only,
--     SUM(CASE WHEN SRRI_Validation_Status = 'NOT_AVAILABLE' THEN 1 ELSE 0 END) AS not_available,
--     SUM(CASE WHEN SRRI_Visual IS NULL                      THEN 1 ELSE 0 END) AS sin_visual
-- FROM fund_kiid_metadata WHERE KIID_Class = 1;

-- Fondos que requieren FORCE_REFRESH para extraer SRRI_Visual:
-- SELECT COUNT(*) FROM fund_kiid_metadata
--   WHERE SRRI_Visual IS NULL AND KIID_Class = 1;
-- → actualmente 21; ejecutar para programar re-descarga:
-- UPDATE fund_kiid_metadata SET KIID_Status = 'FORCE_REFRESH'
--   WHERE SRRI_Visual IS NULL AND KIID_Class = 1;

-- Top 20 fondos más lentos (para diagnóstico de rendimiento):
-- SELECT km.ISIN, fm.Fund_Name, fm.Management_Company,
--        km.Processing_Time_Ms, km.Processing_Breakdown, km.KIID_Status
-- FROM fund_kiid_metadata km
-- JOIN fund_master fm ON km.ISIN = fm.ISIN
-- WHERE km.Processing_Time_Ms IS NOT NULL
-- ORDER BY km.Processing_Time_Ms DESC LIMIT 20;
