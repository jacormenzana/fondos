-- ============================================================
-- schema_fondos.sql  — v16  (2026-03-31)
-- Base de datos: db/fondos.sqlite
-- Ruta esperada: <raiz_proyecto>/db/schema_fondos.sql
-- Cargado por: core/sqlite_writer.py → create_schema()
-- ============================================================
-- Historial de versiones:
--   v10  Esquema base: fund_master, fund_kiid_metadata, ingestion_log
--   v12  fund_family_id; SRRI_Visual/Textual/Validation_Status
--   v14  Benchmark_Declared, Leverage_Used, Distribution_Frequency
--   v15  SRRI_Quality_Flag, Data_Quality_Flag, Inference_Trace
--   v16  Atributos v3 characterizer (5 cols) + telemetría proceso (2 cols)
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
    Fund_Nature                 TEXT,
    Profile                     TEXT,
    Type                        TEXT,
    Strategy                    TEXT,
    Family                      TEXT,
    Style_Profile               TEXT,
    Geography                   TEXT,
    Theme                       TEXT,
    Is_ESG                      INTEGER DEFAULT 0,
    Exposure_Bias               TEXT,
    Benchmark_Type              TEXT,
    Subtype                     TEXT,

    -- ── Bloques heurísticos ───────────────────────────────────
    Heuristic_Block             TEXT,
    Heuristic_Core              TEXT,

    -- ── SRRI y calidad de datos ───────────────────────────────
    SRRI                        INTEGER,
    SRRI_Quality_Flag           TEXT,
    Data_Quality_Flag           TEXT,

    -- ── Divisa y cobertura ────────────────────────────────────
    Fund_Currency               TEXT,
    Portfolio_Currency          TEXT,
    Hedging_Policy              TEXT,

    -- ── Política de inversión ─────────────────────────────────
    Replication_Method          TEXT,
    Derivatives_Usage           TEXT,
    Benchmark_Declared          TEXT,

    -- ── Costes y condiciones ─────────────────────────────────
    Ongoing_Charge              REAL,
    Accumulation_Policy         TEXT,
    Entry_Fee_Pct               REAL,
    Exit_Fee_Pct                REAL,
    Sfdr_Article                INTEGER,
    Recommended_Holding_Period  TEXT,
    Leverage_Used               TEXT,
    Liquidity_Profile           TEXT,
    Distribution_Frequency      TEXT,

    -- ── Fund family ───────────────────────────────────────────
    fund_family_id              TEXT,

    -- ── Trazabilidad ─────────────────────────────────────────
    Inference_Trace             TEXT,
    Updated_At                  TEXT,

    -- ── Atributos v3 — fund_characterizer (v16) ──────────────
    Market_Cap_Focus            TEXT,
    Sector_Focus                TEXT,
    Currency_Hedged             TEXT,
    Investment_Universe         TEXT
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
    KIID_PDF_Hash               TEXT,

    -- ── Estado del ciclo de descarga ─────────────────────────
    -- CACHED         → texto en BD, no se re-descarga
    -- OK             → descarga correcta anterior (igual que CACHED para el pipeline)
    -- FORCE_REFRESH  → re-descarga obligatoria en el próximo ciclo
    -- WRONG_DOC      → PDF descargado no corresponde al ISIN
    -- NOT_FOUND      → URL no responde
    KIID_Status                 TEXT    DEFAULT 'CACHED',

    -- ── Contenido extraído ───────────────────────────────────
    Language                    TEXT,
    Raw_KIID_Text               TEXT,
    KIID_Published_Date         TEXT,
    KIID_Downloaded_At          TEXT,

    -- ── SRRI ─────────────────────────────────────────────────
    SRRI                        INTEGER,
    SRRI_Visual                 INTEGER,
    SRRI_Textual                INTEGER,
    -- MATCH | TEXT_ONLY | VISUAL_ONLY | CONFLICT | NOT_AVAILABLE
    SRRI_Validation_Status      TEXT,

    -- ── Telemetría de proceso (v16) ───────────────────────────
    Processing_Time_Ms          INTEGER,
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
-- COLUMNAS CANÓNICAS: step, status (no block/level — nombres históricos)
-- ============================================================
CREATE TABLE IF NOT EXISTS ingestion_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ISIN        TEXT,
    step        TEXT,
    status      TEXT,
    message     TEXT,
    created_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_log_isin   ON ingestion_log (ISIN);
CREATE INDEX IF NOT EXISTS idx_log_status ON ingestion_log (status);


-- ============================================================
-- TABLA 4: fund_families  (fund_family_builder.py)
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_families (
    family_id       TEXT PRIMARY KEY,
    family_name     TEXT,
    Fund_Nature     TEXT,
    n_funds         INTEGER,
    Updated_At      TEXT
);
