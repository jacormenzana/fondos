-- ============================================================
-- PROYECTO 1
-- ESQUEMA SQLITE CANÓNICO (RECREACIÓN)
-- ============================================================

PRAGMA foreign_keys = ON;

-- ============================================================
-- FUND MASTER
-- Visión consolidada y estructural del fondo
-- ============================================================

CREATE TABLE  IF NOT EXISTS fund_master (
    ISIN TEXT PRIMARY KEY,
    Fund_Name TEXT NOT NULL,
    Management_Company TEXT,

    -- ----------------------------
    -- Clasificación canónica
    -- ----------------------------
    Fund_Nature TEXT NOT NULL,          -- Monetario / RF Corto / RF Flexible / RV / Mixto / Alternativo / Restantes
    Profile TEXT,                        -- Conservador / Moderado / Dinámico
    Type TEXT,                           -- Estructura del producto
    Family TEXT,                         -- Familia estructural/comercial
    Style_Profile TEXT,                 -- Construcción del retorno
    Geography TEXT,                      -- Solo si explícita
    Theme TEXT,                          -- Uso restringido
    Exposure_Bias TEXT,                 -- Sesgo estructural dominante (valor único)
    Subtype TEXT,                       -- Uso excepcional

    -- ----------------------------
    -- Control heurístico
    -- ----------------------------
    Heuristic_Block TEXT NOT NULL,       -- Bloque que clasifica el fondo
    Heuristic_Core INTEGER NOT NULL CHECK (Heuristic_Core IN (0,1)),

    -- ----------------------------
    -- Parsing documental (derivado de KIID)
    -- ----------------------------
    SRRI INTEGER,                        -- último SRRI observado (estado operativo)
    Fund_Currency TEXT,
    Portfolio_Currency TEXT,
    Hedging_Policy TEXT,
    Replication_Method TEXT,
    Derivatives_Usage TEXT,              -- Yes / No / Limited / Unknown
    Benchmark_Declared TEXT,

    -- ----------------------------
    -- Trazabilidad y QA
    -- ----------------------------
    Inference_Trace TEXT,                -- Explicación breve de inferencias clave

    SRRI_Quality_Flag TEXT               -- HIGH / MEDIUM_VISUAL / MEDIUM_TEXT / LOW_CONFLICT / NONE
        CHECK (SRRI_Quality_Flag IN (
            'HIGH',
            'MEDIUM_VISUAL',
            'MEDIUM_TEXT',
            'LOW_CONFLICT',
            'NONE'
        )),

    Data_Quality_Flag TEXT,              -- OK / WARN / MISSING

    Created_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Updated_At TIMESTAMP
);

CREATE INDEX  IF NOT EXISTS idx_fm_nature ON fund_master (Fund_Nature);
CREATE INDEX  IF NOT EXISTS idx_fm_block ON fund_master (Heuristic_Block, Heuristic_Core);
CREATE INDEX  IF NOT EXISTS idx_fm_mgmt ON fund_master (Management_Company);

-- ============================================================
-- KIID METADATA
-- Parsing y trazabilidad documental
-- ============================================================

CREATE TABLE  IF NOT EXISTS fund_kiid_metadata (
    ISIN TEXT NOT NULL,
    KIID_Class INTEGER NOT NULL,

    KIID_URL TEXT,
    KIID_PDF_Hash TEXT,
    KIID_Status TEXT,

    Language TEXT,
    Raw_KIID_Text TEXT,
    KIID_Published_Date DATE,
    KIID_Downloaded_At TIMESTAMP,

    -- ----------------------------
    -- Atributos documentales (SRRI)
    -- ----------------------------
    SRRI INTEGER,                        -- SRRI consolidado por KIID/clase

    SRRI_Visual INTEGER,                 -- SRRI detectado por visión artificial
    SRRI_Textual INTEGER,                -- SRRI detectado por parsing textual

    SRRI_Validation_Status TEXT          -- MATCH / VISUAL_ONLY / TEXT_ONLY / CONFLICT / NOT_AVAILABLE
        CHECK (SRRI_Validation_Status IN (
            'MATCH',
            'VISUAL_ONLY',
            'TEXT_ONLY',
            'CONFLICT',
            'NOT_AVAILABLE'
        )),

    PRIMARY KEY (ISIN, KIID_Class),
    FOREIGN KEY (ISIN) REFERENCES fund_master(ISIN) ON DELETE CASCADE
);

CREATE INDEX  IF NOT EXISTS idx_kiid_isin ON fund_kiid_metadata (ISIN);

-- ============================================================
-- INGESTION LOG
-- Trazabilidad operativa
-- ============================================================

CREATE TABLE  IF NOT EXISTS ingestion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ISIN TEXT,
    Step TEXT,                -- KIID_DOWNLOAD / PARSE / CLASSIFY / NAV_INGEST
    Status TEXT,              -- OK / WARN / ERROR
    Message TEXT,
    Created_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX  IF NOT EXISTS idx_ingest_isin ON ingestion_log (ISIN);

-- ============================================================
-- NAV MENSUAL
-- Series temporales (consumidas por Proyecto 2)
-- ============================================================

CREATE TABLE  IF NOT EXISTS fund_nav_monthly (
    ISIN TEXT NOT NULL,
    Date DATE NOT NULL,
    NAV REAL NOT NULL,
    NAV_Currency TEXT,
    NAV_Type TEXT DEFAULT 'NAV',
    Is_Estimated INTEGER DEFAULT 0,
    Data_Source TEXT,
    Ingested_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (ISIN, Date),
    FOREIGN KEY (ISIN) REFERENCES fund_master(ISIN) ON DELETE CASCADE
);

CREATE INDEX  IF NOT EXISTS idx_nav_isin ON fund_nav_monthly (ISIN);
CREATE INDEX  IF NOT EXISTS idx_nav_date ON fund_nav_monthly (Date);

-- ============================================================
-- MÉTRICAS (PLACEHOLDER PROYECTO 2)
-- ============================================================

CREATE TABLE  IF NOT EXISTS fund_metrics (
    ISIN TEXT NOT NULL,
    Metric_Name TEXT NOT NULL,
    Metric_Horizon TEXT NOT NULL,
    Metric_Value REAL,
    Metric_Unit TEXT,
    Calculated_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (ISIN, Metric_Name, Metric_Horizon),
    FOREIGN KEY (ISIN) REFERENCES fund_master(ISIN) ON DELETE CASCADE
);
