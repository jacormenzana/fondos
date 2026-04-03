-- ============================================================
-- FONDOS.SQLITE — SCHEMA UNIFICADO
-- Proyectos 1, 2 y 3
-- ============================================================
-- Convención de naming:
--   Tablas P1: fund_*, ingestion_log
--   Tablas P2: series_*, fund_metrics (versión canónica)
--   Tablas P3: portfolio_*, fund_scores
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================
-- PROYECTO 1 — TIPIFICACIÓN ESTRUCTURAL
-- (Tablas existentes — no se modifican)
-- ============================================================

-- ------------------------------------------------------------
-- FUND MASTER
-- Visión consolidada y estructural del fondo
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fund_master (
    ISIN                    TEXT PRIMARY KEY,
    Fund_Name               TEXT NOT NULL,
    Management_Company      TEXT,

    -- Clasificación canónica
    Fund_Nature             TEXT NOT NULL,   -- Monetario / RF Corto / RF Flexible / RV / Mixto / Alternativo / Restantes
    Profile                 TEXT,            -- Conservador / Moderado / Dinámico
    Type                    TEXT,
    Family                  TEXT,
    Style_Profile           TEXT,
    Geography               TEXT,
    Theme                   TEXT,
    Exposure_Bias           TEXT,
    Subtype                 TEXT,

    -- Control heurístico
    Heuristic_Block         TEXT NOT NULL,
    Heuristic_Core          INTEGER NOT NULL CHECK (Heuristic_Core IN (0, 1)),

    -- Parsing documental (derivado de KIID)
    SRRI                    INTEGER,
    Fund_Currency           TEXT,
    Portfolio_Currency      TEXT,
    Hedging_Policy          TEXT,
    Replication_Method      TEXT,
    Derivatives_Usage       TEXT,            -- Yes / No / Limited / Unknown
    Benchmark_Declared      TEXT,

    -- Trazabilidad y QA
    Inference_Trace         TEXT,
    SRRI_Quality_Flag       TEXT
        CHECK (SRRI_Quality_Flag IN ('HIGH','MEDIUM_VISUAL','MEDIUM_TEXT','LOW_CONFLICT','NONE')),
    Data_Quality_Flag       TEXT,            -- OK / WARN / MISSING

    Created_At              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Updated_At              TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fm_nature  ON fund_master (Fund_Nature);
CREATE INDEX IF NOT EXISTS idx_fm_block   ON fund_master (Heuristic_Block, Heuristic_Core);
CREATE INDEX IF NOT EXISTS idx_fm_mgmt    ON fund_master (Management_Company);

-- ------------------------------------------------------------
-- KIID METADATA
-- Parsing y trazabilidad documental
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fund_kiid_metadata (
    ISIN                    TEXT NOT NULL,
    KIID_Class              INTEGER NOT NULL,

    KIID_URL                TEXT,
    KIID_PDF_Hash           TEXT,
    KIID_Status             TEXT,
    Language                TEXT,
    Raw_KIID_Text           TEXT,
    KIID_Published_Date     DATE,
    KIID_Downloaded_At      TIMESTAMP,

    SRRI                    INTEGER,
    SRRI_Visual             INTEGER,
    SRRI_Textual            INTEGER,
    SRRI_Validation_Status  TEXT
        CHECK (SRRI_Validation_Status IN (
            'MATCH','VISUAL_ONLY','TEXT_ONLY','CONFLICT','NOT_AVAILABLE'
        )),

    PRIMARY KEY (ISIN, KIID_Class),
    FOREIGN KEY (ISIN) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_kiid_isin ON fund_kiid_metadata (ISIN);

-- ------------------------------------------------------------
-- INGESTION LOG
-- Trazabilidad operativa P1
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ISIN        TEXT,
    Step        TEXT,       -- KIID_DOWNLOAD / PARSE / CLASSIFY / NAV_INGEST
    Status      TEXT,       -- OK / WARN / ERROR
    Message     TEXT,
    Created_At  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ingest_isin ON ingestion_log (ISIN);

-- ------------------------------------------------------------
-- NAV MENSUAL
-- Series temporales de liquidativos (P1 ingesta, P2 consume)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fund_nav_monthly (
    ISIN            TEXT NOT NULL,
    Date            DATE NOT NULL,
    NAV             REAL NOT NULL,
    NAV_Currency    TEXT,
    NAV_Type        TEXT DEFAULT 'NAV',
    Is_Estimated    INTEGER DEFAULT 0,
    Data_Source     TEXT,
    Ingested_At     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (ISIN, Date),
    FOREIGN KEY (ISIN) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_nav_isin  ON fund_nav_monthly (ISIN);
CREATE INDEX IF NOT EXISTS idx_nav_date  ON fund_nav_monthly (Date);


-- ============================================================
-- PROYECTO 2 — ENRIQUECIMIENTO CUANTITATIVO
-- ============================================================

-- ------------------------------------------------------------
-- SERIES MACRO
-- Indicadores macroeconómicos de contexto
-- Fuentes: BCE (SDW), Eurostat, INE, Fed FRED
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS series_macro (
    date            DATE    NOT NULL,
    indicator       TEXT    NOT NULL,   -- código normalizado (ver catálogo abajo)
    geography       TEXT    NOT NULL,   -- ES / EU / US / JP / CN / GLOBAL
    value           REAL,
    unit            TEXT,               -- ratio / index / pct / usd_bn
    source          TEXT,               -- BCE / EUROSTAT / INE / FRED / IMF
    load_ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (date, indicator, geography)
);

CREATE INDEX IF NOT EXISTS idx_macro_indicator ON series_macro (indicator, geography);
CREATE INDEX IF NOT EXISTS idx_macro_date       ON series_macro (date);

-- Catálogo de indicadores (referencia, no constraint):
--   ipc_yoy           IPC variación interanual
--   m2_yoy            Masa monetaria M2 variación interanual
--   m3_yoy            Masa monetaria M3 variación interanual
--   gdp_nom_usd       PIB nominal en USD (miles de millones)
--   gdp_yoy           PIB variación interanual real
--   deficit_gdp_pct   Déficit público / PIB (%)
--   debt_gdp_pct      Deuda pública acumulada / PIB (%)
--   rate_policy       Tipo de interés de referencia del banco central
--   rate_deposit      Tipo de depósito (BCE)
--   spread_ig_eur     Diferencial crédito IG en euros (bps)
--   spread_hy_eur     Diferencial crédito HY en euros (bps)
--   fci_ecb           Financial Conditions Index BCE

-- ------------------------------------------------------------
-- SERIES BENCHMARK
-- NAV de índices y ETF proxy usados como benchmark
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS series_benchmark (
    benchmark_id    TEXT    NOT NULL,   -- código normalizado del índice/ETF
    date            DATE    NOT NULL,
    value           REAL    NOT NULL,   -- precio / nivel de índice
    value_type      TEXT    DEFAULT 'CLOSE',  -- CLOSE / TR (total return)
    currency        TEXT,
    source          TEXT,
    load_ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (benchmark_id, date)
);

CREATE INDEX IF NOT EXISTS idx_bench_id   ON series_benchmark (benchmark_id);
CREATE INDEX IF NOT EXISTS idx_bench_date ON series_benchmark (date);

-- ------------------------------------------------------------
-- SERIES INFLATION
-- IPC mensual para deflactación de NAV (contrato P1↔P2)
-- Subconjunto de series_macro extraído para acceso directo
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS series_inflation (
    date        DATE    NOT NULL,
    geography   TEXT    NOT NULL DEFAULT 'ES',
    ipc_index   REAL    NOT NULL,   -- indice base 100
    source      TEXT,
    load_ts     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (date, geography)
);

-- ------------------------------------------------------------
-- FUND METRICS  (versión canónica P2 — sustituye al placeholder P1)
-- Una fila por (ISIN, métrica, horizonte, real_flag, versión)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fund_metrics (
    isin                TEXT    NOT NULL,
    metric              TEXT    NOT NULL,   -- nombre canónico (ver catálogo)
    horizon             TEXT    NOT NULL,   -- ventana temporal (ver valores)
    value               REAL,
    real_flag           INTEGER NOT NULL    -- 0=nominal  1=deflactado por IPC
        CHECK (real_flag IN (0, 1)),
    calculation_date    DATE    NOT NULL,
    metric_version      TEXT    NOT NULL    DEFAULT 'v1',
    benchmark_id        TEXT,               -- NULL si métrica absoluta
    source_rows         INTEGER,            -- nº de NAV usados en el cálculo
    load_ts             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (isin, metric, horizon, real_flag, metric_version),
    FOREIGN KEY (isin) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_metrics_isin    ON fund_metrics (isin);
CREATE INDEX IF NOT EXISTS idx_metrics_metric  ON fund_metrics (metric, horizon);
CREATE INDEX IF NOT EXISTS idx_metrics_date    ON fund_metrics (calculation_date);

-- Catálogo de métricas (referencia):
--   === Daño ===
--   max_drawdown          Máxima pérdida pico-valle           ratio ≤ 0
--   worst_month           Peor retorno mensual                ratio
--   === Consistencia ===
--   pct_positive_months   % meses con retorno positivo        ratio [0,1]
--   pct_severe_loss_months % meses con pérdida ≤ -2%          ratio [0,1]
--   pct_positive_years    % años con retorno positivo         ratio [0,1]
--   === Recuperación ===
--   drawdown_duration     Duración máxima de drawdown         meses
--   time_to_recovery      Tiempo hasta recuperar máximo       meses / NULL
--   === Retorno ===
--   return_ann            Rentabilidad anualizada nominal      ratio
--   return_real_ann       Rentabilidad anualizada real         ratio
--   volatility_ann        Volatilidad anualizada               ratio
--   === Eficiencia ===
--   sharpe                Ratio Sharpe                        ratio
--   sortino               Ratio Sortino                       ratio
--   ret_vol_simple        Rentabilidad / Volatilidad simple   ratio
--   === Benchmark (requiere benchmark_id) ===
--   alpha_jensen          Alfa de Jensen                      ratio
--   beta                  Beta vs benchmark                   ratio
--   tracking_error        Tracking Error anualizado           ratio
--   information_ratio     Ratio de Información                ratio
--   upside_capture        Ratio de Captura al alza            ratio
--   downside_capture      Ratio de Captura a la baja          ratio

-- Valores de horizonte (cerrados):
--   since_inception / rolling_10y / rolling_5y / rolling_3y / rolling_1y / ytd
--   crisis_2008 / crisis_2011 / crisis_2020 / crisis_2022

-- ------------------------------------------------------------
-- P2 PIPELINE LOG
-- Trazabilidad operativa del pipeline de cálculo P2
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS p2_pipeline_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    isin            TEXT,
    step            TEXT,       -- NAV_LOAD / DEFLATE / CALC_METRICS / WRITE
    status          TEXT,       -- OK / WARN / ERROR / SKIP
    horizon         TEXT,
    metric_version  TEXT,
    message         TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_p2log_isin ON p2_pipeline_log (isin);


-- ============================================================
-- PROYECTO 3 — SELECCIÓN Y CONSTRUCCIÓN DE CARTERA
-- ============================================================

-- ------------------------------------------------------------
-- FUND SCORES
-- Scoring compuesto pre-selección por bloque
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fund_scores (
    isin            TEXT    NOT NULL,
    block           TEXT    NOT NULL,   -- Monetario / RF_Corto / etc.
    score_version   TEXT    NOT NULL    DEFAULT 'v1',
    score_total     REAL,
    score_detail    TEXT,               -- JSON con desglose por componente
    eligible        INTEGER NOT NULL    DEFAULT 0
        CHECK (eligible IN (0, 1)),     -- 1 = supera hard filters
    calculated_at   DATE    NOT NULL,
    notes           TEXT,

    PRIMARY KEY (isin, block, score_version),
    FOREIGN KEY (isin) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scores_block ON fund_scores (block, eligible);

-- ------------------------------------------------------------
-- PORTFOLIO SCENARIOS
-- Escenarios de cartera construidos en P3
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_scenarios (
    scenario_id     TEXT    PRIMARY KEY,  -- ej. 'defensiva_2026Q1'
    profile         TEXT    NOT NULL,     -- Defensiva / Equilibrada / Crecimiento
    macro_regime    TEXT,                 -- descripción del régimen macro
    created_at      DATE    NOT NULL,
    notes           TEXT
);

-- ------------------------------------------------------------
-- PORTFOLIO WEIGHTS
-- Asignación de pesos por fondo y escenario
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_weights (
    scenario_id     TEXT    NOT NULL,
    isin            TEXT    NOT NULL,
    block           TEXT    NOT NULL,
    weight          REAL    NOT NULL CHECK (weight >= 0 AND weight <= 1),
    role            TEXT,               -- descripción del rol en cartera
    notes           TEXT,

    PRIMARY KEY (scenario_id, isin),
    FOREIGN KEY (scenario_id) REFERENCES portfolio_scenarios (scenario_id),
    FOREIGN KEY (isin)        REFERENCES fund_master (ISIN) ON DELETE CASCADE
);


CREATE INDEX IF NOT EXISTS idx_pw_scenario ON portfolio_weights (scenario_id);
CREATE INDEX IF NOT EXISTS idx_pw_block     ON portfolio_weights (block);

-- ------------------------------------------------------------
-- NAV SOURCES
-- Registro de fuentes de datos NAV por ISIN.
-- Resultado del proceso de descubrimiento previo a la descarga.
-- Una fila por ISIN — se actualiza en cada ejecución del
-- descubrimiento o de la carga incremental.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nav_sources (
    isin            TEXT    PRIMARY KEY,
    source          TEXT,               -- MORNINGSTAR / CNMV / NOT_FOUND
    source_id       TEXT,               -- ID interno de la fuente
                                        --   Morningstar: ej. 'F0GBR04S23'
                                        --   CNMV: código registro CNMV
    first_nav_date  DATE,               -- fecha más antigua disponible en la fuente
    last_nav_date   DATE,               -- fecha más reciente disponible
    nav_count       INTEGER,            -- nº de NAV disponibles en la fuente
    discovered_at   DATE,               -- fecha de primer descubrimiento
    last_checked    DATE,               -- fecha de última verificación
    status          TEXT                -- OK / NOT_FOUND / ERROR
        CHECK (status IN ('OK', 'NOT_FOUND', 'ERROR')),

    FOREIGN KEY (isin) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_nav_sources_status ON nav_sources (status);
CREATE INDEX IF NOT EXISTS idx_nav_sources_source ON nav_sources (source);
