-- ============================================================
-- schema_fondos.sql  — v18  (2026-05-10)
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
--   v17  Investment_Focus, Credit_Quality, Fee_Known_Flag (fund_master)
--   v18  DLA2_Table_Text (fund_kiid_metadata) — BL-DLA-2 Sub-fase 2B
--        Resultado cacheado de dla_table_serializer.serialize_tables().
--        Misma semántica de ciclo de vida que Raw_KIID_Text: se calcula
--        en descarga real, se reutiliza desde BD en ciclos CACHED,
--        se invalida cuando KIID_PDF_Hash cambia (FORCE_REFRESH).
-- ============================================================

-- ============================================================
-- TABLA 1: fund_master
-- Registro maestro de cada clase de fondo (1 fila = 1 ISIN)
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_master (
    ISIN	TEXT PRIMARY KEY,
    Fund_Name	TEXT,
    Management_Company	TEXT,
    Fund_Nature	TEXT,
    Profile	TEXT,
    Type	TEXT,
    Family	TEXT,
    Style_Profile	TEXT,
    Geography	TEXT,
    Theme	TEXT,
    Exposure_Bias	TEXT,
    Subtype	TEXT,
    Heuristic_Block	TEXT,
    Heuristic_Core	TEXT,
    SRRI	INTEGER,
    Fund_Currency	TEXT,
    Portfolio_Currency	TEXT,
    Hedging_Policy	TEXT,
    Replication_Method	TEXT,
    Derivatives_Usage	TEXT,
    Benchmark_Declared	TEXT,
    Inference_Trace	TEXT,
    SRRI_Quality_Flag	TEXT,
    Data_Quality_Flag	TEXT,
    Created_At	TEXT,
    Updated_At	TEXT,
    Ongoing_Charge	REAL,
    fund_family_id	TEXT,
    Strategy	TEXT,
    Is_ESG	INTEGER DEFAULT 0,
    Benchmark_Type	TEXT,
    Accumulation_Policy	TEXT,
    Entry_Fee_Pct	REAL,
    Exit_Fee_Pct	REAL,
    Sfdr_Article	INTEGER,
    Recommended_Holding_Period	TEXT,
    Leverage_Used	TEXT,
    Liquidity_Profile	TEXT,
    Distribution_Frequency	TEXT,
    Market_Cap_Focus	TEXT,
    Sector_Focus	TEXT,
    Currency_Hedged	TEXT,
    Investment_Universe	TEXT,
    Investment_Focus	TEXT,
    Credit_Quality	TEXT,
    Fee_Known_Flag	TEXT	
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

    -- ── DLA Fase 2 — tablas Cat.1+2 (v18) ───────────────────
    -- Resultado de dla_table_serializer.serialize_tables().
    -- Formato: texto plano "Etiqueta: valor" por línea, concatenable
    -- a Raw_KIID_Text para que kiid_parser.py lo procese sin cambios.
    -- Ciclo de vida idéntico a Raw_KIID_Text:
    --   calculado en descarga real (FORCE_REFRESH / nuevo PDF),
    --   reutilizado desde BD en ciclos CACHED,
    --   invalidado cuando KIID_PDF_Hash cambia.
    -- NULL = pendiente de extracción (DLA_TABLE_SERIALIZATION_ENABLED=False
    --        o fondo no procesado aún con DLA-2 activo).
    DLA2_Table_Text             TEXT,

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
--   ipc_index         Índice IPC base 100 (ES/EU/US/JP/CN)
--   ipc_yoy           IPC variación interanual calculada desde ipc_index
--   m2_yoy            M2 variación interanual
--                       EU: serie BCE observada
--                       US: calculado desde m2_level (source=CALC)
--                       CN/JP: calculado con extensiones estimadas (source=CALC_EST post-2019/2017)
--   m2_level          M2 nivel absoluto en moneda local (US=usd_bn, EU=eur_mn, CN=cny_mn, JP=jpy_mn)
--   m2_global_yoy     M2 Global YoY calculado (US+EU+CN+JP en USD) — source=CALC
--   m3_yoy            M3 variación interanual (EU, serie BCE observada)
--   m3_level          M3 nivel absoluto en EUR millones (EU, serie BCE) — P2 v2
--   gdp_nom_eur       PIB nominal Eurozona en EUR (millones, trimestral) — Eurostat
--   deficit_gdp_pct   Déficit público / PIB (%) — Eurostat
--   debt_gdp_pct      Deuda pública acumulada / PIB (%) — Eurostat
--   rate_policy       Tipo de referencia banco central (EU/US/JP/CN)
--   rate_deposit      Tipo de depósito BCE (EU)
--   oil_wti           Precio petróleo WTI (USD/barril, mensual medio)
--   copper            Precio cobre LME (USD/tonelada, mensual)
--   gold              PPI Metales PPICMM — proxy precio oro (FRED)
--   cli               Índice Adelantado Compuesto OCDE (EU=Alemania proxy/US/JP/CN/ES)
--   unemployment      Tasa desempleo (US)
--   dxy               Dollar Index DXY — índice fortaleza USD (base ene-1997=100)
--   fx_usd_eur        USD por EUR (ej. 1.10)
--   fx_jpy_usd        JPY por USD (ej. 150)
--   fx_usd_gbp        USD por GBP (ej. 1.27)
--   fx_cny_usd        CNY por USD (ej. 7.1)
--   spread_hy         ICE BofA HY Option-Adjusted Spread % (GLOBAL, media mensual) — P2 v10
--   vix               CBOE VIX — volatilidad implícita S&P500 (GLOBAL, media mensual) — P2 v10
--   term_spread       Pendiente curva EEUU 10Y-2Y % (US, mensual) — P2 v10

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
    date        DATE    PRIMARY KEY,
    geography   TEXT    NOT NULL DEFAULT 'ES',
    ipc_index   REAL    NOT NULL,   -- índice base 100
    source      TEXT,
    load_ts     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
--   === Sensibilidad macro OLS (23 factores — pipeline v10) ===
--   macro_r2              R² del modelo OLS
--   macro_alpha           Alpha anualizado no explicado por macro
--   macro_n_obs           Observaciones usadas en la regresión
--   beta_rate_eu          Sensibilidad variación tipo BCE
--   beta_m3_yoy           Sensibilidad M3 Eurozona YoY
--   beta_ipc_es/eu/us/jp/cn  Sensibilidad inflación por geografía
--   beta_rate_us/jp/cn    Sensibilidad variación tipo Fed/BoJ/PBoC
--   beta_oil              Sensibilidad petróleo WTI YoY
--   beta_copper           Sensibilidad cobre YoY
--   beta_cli_eu/us        Sensibilidad CLI OCDE EU/US
--   beta_dxy              Sensibilidad Dollar Index YoY
--   beta_gold             Sensibilidad oro (PPICMM) YoY
--   beta_m2_global        Sensibilidad M2 Global YoY
--   beta_spread_hy        Sensibilidad spread HY nivel (P2 v10)
--   beta_vix              Sensibilidad VIX YoY (P2 v10)
--   beta_term_spread      Sensibilidad pendiente curva 10Y-2Y (P2 v10)
--   beta_eur_jpy          Sensibilidad EUR/JPY YoY (P2 v10)
--   beta_eur_gbp          Sensibilidad EUR/GBP YoY (P2 v10)
--   beta_eur_cny          Sensibilidad EUR/CNY YoY (P2 v10)
--   === Retornos por régimen macro (7 regímenes — pipeline v10) ===
--   Sufijos: expansion / recalentamiento / recalentamiento_tardio /
--            estanflacion / contraccion / shock_energetico / crisis_financiera
--   n_obs_{sufijo}        Meses del fondo en ese régimen (siempre presente)
--   return_ann_{sufijo}   Retorno anualizado en ese régimen (%) — si n_obs ≥ 12
--   vol_ann_{sufijo}      Volatilidad anualizada en ese régimen (%) — si n_obs ≥ 12
--   sharpe_{sufijo}       Sharpe en ese régimen — si n_obs ≥ 12

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
    macro_regime    TEXT,                 -- régimen macro activo al crear el escenario
                                          -- valores: Expansion / Recalentamiento /
                                          -- Recalentamiento_Tardio / Estanflacion /
                                          -- Contraccion / Shock_Energetico /
                                          -- Crisis_Financiera  (pipeline v10)
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
-- ROTATION COSTS
-- Costes de rotacion por tipo de fondo (parche hasta P1 v2)
-- En P1 v2 se extraeran del KIID y se actualizaran automaticamente
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rotation_costs (
    fund_nature      TEXT    PRIMARY KEY,
    redemption_days  INTEGER NOT NULL DEFAULT 3,   -- dias hasta recibir el dinero
    exit_fee_pct     REAL    NOT NULL DEFAULT 0.0, -- comision de reembolso (%)
    entry_fee_pct    REAL    NOT NULL DEFAULT 0.0, -- comision de suscripcion (%)
    min_holding_days INTEGER NOT NULL DEFAULT 0,   -- permanencia minima en dias
    notes            TEXT
);

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

-- ============================================================
-- fund_benchmarks: benchmarks normalizados por fondo y fuente
-- ============================================================
-- Separado de fund_master para soportar múltiples fuentes
-- (KIID, Morningstar, manual) sin contaminar la tabla principal.
-- P1 v2: poblada por benchmark_normalizer.py post-ingesta.
-- Futura integración Morningstar: source = 'MORNINGSTAR'.
CREATE TABLE IF NOT EXISTS fund_benchmarks (
    ISIN                TEXT    NOT NULL,
    source              TEXT    NOT NULL,          -- KIID / MORNINGSTAR / MANUAL
    benchmark_raw       TEXT,                      -- texto original extraido
    benchmark_id        TEXT,                      -- ID canónico: MSCI_ACWI_NR...
    benchmark_name      TEXT,                      -- nombre limpio para mostrar
    provider            TEXT,                      -- MSCI / Bloomberg / ICE BofA...
    asset_class         TEXT,                      -- Equity / Fixed Income / Rate...
    confidence          TEXT,                      -- HIGH / MEDIUM / LOW
    extracted_at        TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    PRIMARY KEY (ISIN, source)
);

CREATE INDEX IF NOT EXISTS idx_fb_isin        ON fund_benchmarks (ISIN);
CREATE INDEX IF NOT EXISTS idx_fb_id          ON fund_benchmarks (benchmark_id);
CREATE INDEX IF NOT EXISTS idx_fb_provider    ON fund_benchmarks (provider);


