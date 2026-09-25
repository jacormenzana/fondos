-- ============================================================
-- schema_fondos.sql  — v25  (2026-07-19)
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
--   v19  BL-COST-2: Ongoing_Charge→Ongoing_Charge_Recurrent,
--        +11 columnas coste PRIIPs/KID-aware, tabla fund_cost_schedule
--        Resultado cacheado de dla_table_serializer.serialize_tables().
--        Misma semántica de ciclo de vida que Raw_KIID_Text: se calcula
--        en descarga real, se reutiliza desde BD en ciclos CACHED,
--        se invalida cuando KIID_PDF_Hash cambia (FORCE_REFRESH).
--   v21  Asset_Currency (fund_master) — BL-44-FX.
--   v22  fund_data_quality_issues (tabla nueva) — FIX-DQ-1: estado actual
--        de issues de calidad de datos por fondo, complementa a
--        ingestion_log. Data_Quality_Flag ahora se calcula como rollup
--        determinista (máximo de severidad) en vez de mutaciones
--        secuenciales dispersas por pipeline.py.
--   v23  In_Current_Universe (fund_master) — FIX-UNIVERSE-RECON-1: soft-
--        delete flag (1 = en el harvest vigente, 0 = huérfano fuera del
--        universo actual). Regenerado en cada ciclo por
--        reconcile_universe_membership() en sqlite_writer.py; no es COALESCE-
--        protegido (mismo patrón que SRRI_Visual, excepción al P#1).
--   v24  fund_nav_monthly + fund_nav_daily (tablas NAV P2) añadidas al
--        schema canónico — antes se creaban fuera de schema_fondos.sql.
--        fund_nav_daily: serie diaria pre-resample usada para métricas de
--        horizonte corto (rolling_1m / rolling_3m / rolling_6m) con
--        corrección de iliquidez (AC-adjusted vol). metric_version='d1'.
--   v25  nav_sources.data_status (TEXT, DEFAULT 'OK') — máquina de
--        estados para control del ciclo de vida de los datos NAV.
--        Paralela a KIID_Status en P1; permite invalidar datos y forzar
--        recálculos sin borrar la fila de nav_sources ni todo el histórico.
-- ============================================================

-- ============================================================
-- TABLA 1: fund_master
-- Registro maestro de cada clase de fondo (1 fila = 1 ISIN)
-- DDL alineado con schema real de producción (PRAGMA table_info v19)
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_master (
    ISIN                    TEXT PRIMARY KEY,
    Fund_Name               TEXT NOT NULL,
    Management_Company      TEXT,
    -- Clasificación canónica
    Fund_Nature             TEXT NOT NULL,
    Profile                 TEXT,
    -- v20 §8-bis Q2: Type → Vehicle_Structure (forma jurídico-estructural).
    Vehicle_Structure       TEXT,
    Family                  TEXT,
    Style_Profile           TEXT,
    Geography               TEXT,
    Theme                   TEXT,
    Exposure_Bias           TEXT,
    -- v20: Subtype ELIMINADA (descompuesta en MMF_Structure/Alt_Strategy/Payoff_Profile)
    -- Control heurístico
    Heuristic_Block         TEXT NOT NULL,
    Heuristic_Core          INTEGER NOT NULL CHECK (Heuristic_Core IN (0, 1)),
    -- Parsing documental (derivado de KIID)
    SRRI                    INTEGER,
    Fund_Currency           TEXT,
    -- v20: Portfolio_Currency ELIMINADA (98.7% NULL, no consumida en P3)
    -- v21 (2026-07-05): Asset_Currency AÑADIDA -- divisa de los activos/
    -- estrategia del fondo (vs. Fund_Currency = divisa de la clase de
    -- participación), inferida del nombre del fondo (no del texto KIID,
    -- que fue la causa de la tasa de NULL de Portfolio_Currency). Consumida
    -- por BL-44-FX (pipeline.py) y disponible para P2 (currency_factor.py).
    Asset_Currency          TEXT,
    Hedging_Policy          TEXT,
    Replication_Method      TEXT,
    Derivatives_Usage       TEXT,
    Benchmark_Declared      TEXT,
    -- Trazabilidad y QA
    Inference_Trace         TEXT,
    SRRI_Quality_Flag       TEXT
        CHECK (SRRI_Quality_Flag IN ('HIGH','MEDIUM_VISUAL','MEDIUM_TEXT','LOW_CONFLICT','NONE')),
    Data_Quality_Flag       TEXT,
    Created_At              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Updated_At              TIMESTAMP,
    -- Columnas añadidas via ALTER TABLE (v12-v17)
    -- v19: renombrado Ongoing_Charge → Ongoing_Charge_Recurrent
    --      (TER recurrente puro, no incluye amortización one-offs)
    -- ⚠ ESCALA: RATIO DECIMAL (0.0075 == 0.75%). Es la convención que produce
    --   kiid_parser._detect_ongoing_charge y la que asume _norm_existing_oc.
    --   NO confundir con las columnas *_Pct / ACI_*, que van en PORCENTAJE
    --   ENTERO. Auditoría 2026-08-23: la vía de relleno del extractor escribía
    --   porcentaje aquí, dejando 81 fondos 100× por encima (FIX-OC-SCALE).
    -- ⚠ NO debe coincidir con ACI_RHP: el ACI incluye la entrada amortizada y es
    --   estructuralmente mayor. Coincidencia exacta = contaminación (FIX-OC-BY-
    --   DESCRIPTION). Valores previos preservados en Ongoing_Charge_Legacy.
    -- ⚠ DEFINICIÓN: solo el componente de GESTIÓN (comisiones de gestión y otros
    --   costes administrativos o de funcionamiento). Los costes de operación NO
    --   forman parte del gasto corriente bajo PRIIPs/UCITS: viven en
    --   Transaction_Cost_Pct. Ver FIX-OC-MGMT-ONLY (2026-08-23).
    -- Preservación: los valores anteriores a cualquier corrección viven en la
    -- tabla fund_cost_corrections (no en columnas *_Legacy, retiradas por no
    -- escalar a un par de columnas por componente de coste).
    Ongoing_Charge_Recurrent  REAL,
    fund_family_id          TEXT,
    Strategy                TEXT,
    -- v20: Is_ESG ELIMINADA (derivable de Sfdr_Article; §8-bis Q4 sin vista)
    Benchmark_Type          TEXT,
    Accumulation_Policy     TEXT,
    Entry_Fee_Pct           REAL,
    Exit_Fee_Pct            REAL,
    Sfdr_Article            INTEGER,
    -- v20 §6-bis #3: TEXT→INTEGER (años de horizonte recomendado)
    Recommended_Holding_Period INTEGER,
    Leverage_Used           TEXT,
    Liquidity_Profile       TEXT,
    Distribution_Frequency  TEXT,
    Market_Cap_Focus        TEXT,
    Sector_Focus            TEXT,
    -- v20: Currency_Hedged ELIMINADA (100% redundante con Hedging_Policy; §8-bis Q4 sin vista)
    Investment_Universe     TEXT,
    Investment_Focus        TEXT,
    Credit_Quality          TEXT,
    Fee_Known_Flag          TEXT,
    -- ============================================================
    -- v19 (BL-COST-2): bloque coste PRIIPs/KID-aware (11 nuevas)
    -- Todas NULL tras migración v18→v19. Sprint 2 las puebla.
    -- ============================================================
    KID_Format              TEXT
        CHECK (KID_Format IN ('UCITS_KIID','PRIIPS_KID','UNKNOWN')),
    KID_Currency            TEXT,
    Cost_Extraction_Quality TEXT
        CHECK (Cost_Extraction_Quality IN (
               'HIGH','MEDIUM_CROSS','MEDIUM_EUR','MEDIUM_PCT','LOW','NONE')),
    Cost_RHP_Years          REAL
        CHECK (Cost_RHP_Years IS NULL OR (Cost_RHP_Years > 0 AND Cost_RHP_Years <= 50)),
    Entry_Fee_Pct_Max       REAL
        CHECK (Entry_Fee_Pct_Max IS NULL OR (Entry_Fee_Pct_Max >= 0 AND Entry_Fee_Pct_Max <= 25)),
    Exit_Fee_Pct_Max        REAL
        CHECK (Exit_Fee_Pct_Max IS NULL OR (Exit_Fee_Pct_Max >= 0 AND Exit_Fee_Pct_Max <= 25)),
    Management_Fee_Pct      REAL
        CHECK (Management_Fee_Pct IS NULL OR (Management_Fee_Pct >= 0 AND Management_Fee_Pct <= 10)),
    Transaction_Cost_Pct    REAL
        CHECK (Transaction_Cost_Pct IS NULL OR (Transaction_Cost_Pct >= 0 AND Transaction_Cost_Pct <= 5)),
    Performance_Fee_Pct     REAL
        CHECK (Performance_Fee_Pct IS NULL OR (Performance_Fee_Pct >= 0 AND Performance_Fee_Pct <= 30)),
    -- ⚠ Performance_Fee_Pct NO es homogénea por sí sola: unas gestoras publican
    --   un % SOBRE PATRIMONIO y otras la TASA SOBRE LA RENTABILIDAD SUPERIOR
    --   (Carmignac: "20 % máx. de la rentabilidad superior"). Promediar o filtrar
    --   la columna sin mirar la base mezcla dos magnitudes incomparables.
    --   Performance_Fee_Basis declara cuál es (FIX-PERF-FEE-NEGATION, 2026-08-23):
    --     'NONE'                   el KID declara que no se aplica → Pct = 0
    --     'RATE_ON_OUTPERFORMANCE' tasa sobre el exceso de rentabilidad
    --     'UNDETERMINED'           hay comisión pero el KID no expone la mecánica
    Performance_Fee_Basis   TEXT
        CHECK (Performance_Fee_Basis IS NULL OR Performance_Fee_Basis IN
               ('NONE','RATE_ON_OUTPERFORMANCE','PCT_OF_ASSETS','UNDETERMINED')),
    ACI_1Y                  REAL
        CHECK (ACI_1Y IS NULL OR (ACI_1Y >= 0 AND ACI_1Y <= 50)),
    ACI_RHP                 REAL
        CHECK (ACI_RHP IS NULL OR (ACI_RHP >= 0 AND ACI_RHP <= 25)),
    -- ============================================================
    -- v20 CREATE (5) — descomposición de Subtype/Geography (approved inventory §2A.1)
    -- TEXT nullable; valores los puebla el reprocess. Sentinela 'Not Applicable'.
    -- ============================================================
    Development_Status      TEXT,
    Duration_Profile        TEXT,
    MMF_Structure           TEXT,
    Alt_Strategy            TEXT,
    Payoff_Profile          TEXT,
    -- v23 (2026-07-18): universe-membership flag — FIX-UNIVERSE-RECON-1.
    -- 1 = fondo en el harvest vigente (universo actual), 0 = huérfano.
    -- No es COALESCE-protegido: se regenera completamente en cada ciclo.
    In_Current_Universe     INTEGER NOT NULL DEFAULT 1,

    FOREIGN KEY (fund_family_id) REFERENCES fund_families (family_id)
);

-- ============================================================
-- TABLA: fund_cost_schedule  (v19 — BL-COST-2)
-- Función de coste por horizonte (PRIIPs).
-- 1:N respecto a fund_master. Para cada fondo, una fila por
-- punto del escenario "salida después de X años" en el KID.
-- Para fondos UCITS (1 valor único de OC) se sintetiza 1 fila
-- con Source='UCITS_DERIVED' (Sprint 2).
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_cost_schedule (
    ISIN              TEXT NOT NULL,
    Horizon_Years     REAL NOT NULL,
    Is_RHP            INTEGER NOT NULL DEFAULT 0,
    Total_Costs_EUR   REAL,
    Total_Costs_Pct   REAL,
    Annual_Impact_Pct REAL,
    Source            TEXT NOT NULL,
    Created_At        TEXT NOT NULL DEFAULT (datetime('now')),
    Updated_At        TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ISIN, Horizon_Years),
    CHECK (Horizon_Years > 0 AND Horizon_Years <= 50),
    CHECK (Is_RHP IN (0, 1)),
    CHECK (Source IN ('PRIIPS_COSTS_OVER_TIME', 'UCITS_DERIVED', 'MANUAL'))
);

CREATE INDEX IF NOT EXISTS idx_cost_schedule_isin ON fund_cost_schedule(ISIN);
CREATE INDEX IF NOT EXISTS idx_cost_schedule_rhp  ON fund_cost_schedule(ISIN) WHERE Is_RHP = 1;


-- Índices de búsqueda frecuente
CREATE INDEX IF NOT EXISTS idx_fm_nature    ON fund_master (Fund_Nature);
CREATE INDEX IF NOT EXISTS idx_fm_block     ON fund_master (Heuristic_Block, Heuristic_Core);
CREATE INDEX IF NOT EXISTS idx_fm_mgmt      ON fund_master (Management_Company);
CREATE INDEX IF NOT EXISTS idx_fm_family    ON fund_master (fund_family_id);
CREATE INDEX IF NOT EXISTS idx_fm_strategy  ON fund_master (Strategy);
-- v20: idx_fm_esg ELIMINADO (Is_ESG borrada)
CREATE INDEX IF NOT EXISTS idx_fm_company   ON fund_master (Management_Company);
CREATE INDEX IF NOT EXISTS idx_fm_credit_quality ON fund_master (Credit_Quality);


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

    -- ── v20 (INTEGRATED_SPEC_v20_v2 §2B) — arbitración de coste DLA2 ────────
    -- Paridad con SRRI: por componente, 2 valores crudos por estrategia + 1
    -- veredicto. Cost_*_BandsX/Ruled ↔ SRRI_Visual/Textual; Cost_*_Arbitration
    -- ↔ SRRI_Validation_Status. NULL = nunca arbitrado (CACHED / sin PDF local
    -- / kill-switch off). BOTH_FAIL (intentado y fallido) se distingue de NULL.
    Cost_Mgmt_BandsX            REAL,
    Cost_Mgmt_Ruled             REAL,
    Cost_Mgmt_Arbitration       TEXT
        CHECK (Cost_Mgmt_Arbitration IS NULL OR Cost_Mgmt_Arbitration IN
            ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT')),
    Cost_Oper_BandsX            REAL,
    Cost_Oper_Ruled             REAL,
    Cost_Oper_Arbitration       TEXT
        CHECK (Cost_Oper_Arbitration IS NULL OR Cost_Oper_Arbitration IN
            ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT')),
    Cost_ACI_RHP_BandsX         REAL,
    Cost_ACI_RHP_Ruled          REAL,
    Cost_ACI_RHP_Arbitration    TEXT
        CHECK (Cost_ACI_RHP_Arbitration IS NULL OR Cost_ACI_RHP_Arbitration IN
            ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT')),
    Cost_ACI_1Y_BandsX          REAL,
    Cost_ACI_1Y_Ruled           REAL,
    Cost_ACI_1Y_Arbitration     TEXT
        CHECK (Cost_ACI_1Y_Arbitration IS NULL OR Cost_ACI_1Y_Arbitration IN
            ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT')),

    PRIMARY KEY (ISIN, KIID_Class)
);

-- Índices de búsqueda frecuente
CREATE INDEX IF NOT EXISTS idx_km_status    ON fund_kiid_metadata (KIID_Status);
CREATE INDEX IF NOT EXISTS idx_km_srri_val  ON fund_kiid_metadata (SRRI_Validation_Status);
CREATE INDEX IF NOT EXISTS idx_km_visual    ON fund_kiid_metadata (SRRI_Visual);

-- ============================================================
-- VISTA v20: veredicto overall de arbitración de coste (worst-of)
-- No se almacena columna; se deriva de los dos veredictos por componente.
-- Severidad: CONFLICT > BOTH_FAIL > OCR_RECOVERED > ONLY_BANDS_X
--            > ONLY_RULED > AGREE. NULL si algún componente nunca se arbitró.
-- ============================================================
DROP VIEW IF EXISTS v_cost_arbitration_overall;
CREATE VIEW v_cost_arbitration_overall AS
SELECT ISIN,
  CASE
    WHEN Cost_Mgmt_Arbitration IS NULL OR Cost_Oper_Arbitration IS NULL THEN NULL
    WHEN 'CONFLICT'      IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'CONFLICT'
    WHEN 'BOTH_FAIL'     IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'BOTH_FAIL'
    WHEN 'OCR_RECOVERED' IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'OCR_RECOVERED'
    WHEN 'ONLY_BANDS_X'  IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'ONLY_BANDS_X'
    WHEN 'ONLY_RULED'    IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'ONLY_RULED'
    ELSE 'AGREE'
  END AS Cost_Arbitration_Overall
FROM fund_kiid_metadata WHERE KIID_Class = 1;


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
CREATE INDEX IF NOT EXISTS idx_log_step   ON ingestion_log (step, created_at);


-- ============================================================
-- TABLA 4: fund_data_quality_issues  (v22, FIX-DQ-1, 2026-07-05)
-- Estado ACTUAL de issues de calidad de datos por fondo, uno por
-- (ISIN, check_code), reconstruida en cada ciclo de pipeline.py
-- (DELETE+INSERT por ISIN antes de publish_fund). Complementa a
-- ingestion_log (histórico append-only de TODOS los eventos de TODOS
-- los ciclos): da una vista consultable de "qué está mal con el fondo
-- X ahora mismo" sin filtrar el histórico completo. `level` usa el
-- mismo vocabulario que fund_master.Data_Quality_Flag (OK/INFERRED/
-- WARN/MISSING), NO el de ingestion_log.status (ERROR/WARNING/INFO/
-- DEBUG) -- son dos vocabularios distintos, ver DATA_QUALITY_SEVERITY
-- en shared/config.py.
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_data_quality_issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ISIN        TEXT NOT NULL,
    check_code  TEXT NOT NULL,
    level       TEXT NOT NULL,
    message     TEXT,
    detected_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_dqissues_isin ON fund_data_quality_issues (ISIN);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dqissues_isin_code
    ON fund_data_quality_issues (ISIN, check_code);


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
--   spread_ig         Moody's Baa-Treasury yield spread % proxy IG (GLOBAL, mensual, BAA10YM) — P2-07
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
    algorithm_version   TEXT,               -- v26: CALC_VERSION que produjo la fila (p.ej. "20260820")
    batch_id            TEXT,               -- v26: id del run P2 que escribió la fila (p.ej. "P2-20260821_120000-ab12cd")

    PRIMARY KEY (isin, metric, horizon, real_flag, metric_version),
    FOREIGN KEY (isin) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

-- v29: dropped idx_metrics_isin — strict prefix of the PK
--   (isin, metric, horizon, real_flag, metric_version); the PK B-tree already
--   serves any isin-only lookup via a leading-column scan.
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
--   === Sensibilidad macro OLS (24 factores — pipeline v10 + P2-07) ===
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
--   beta_spread_ig        Sensibilidad spread IG nivel (P2-07)
--   beta_vix              Sensibilidad VIX YoY (P2 v10)
--   beta_term_spread      Sensibilidad pendiente curva 10Y-2Y (P2 v10)
--   beta_eur_jpy          Sensibilidad EUR/JPY YoY (P2 v10)
--   beta_eur_gbp          Sensibilidad EUR/GBP YoY (P2 v10)
--   beta_eur_cny              Sensibilidad EUR/CNY YoY (P2 v10)
--   energy_sensitivity_pct    Escenario +25% WTI: beta_oil × 0.25  (P3-03)
--   hy_spread_sensitivity_pct Escenario +300bp HY: beta_spread_hy × 3.0  (P3-04)
--   === Retornos por régimen macro (7 regímenes — pipeline v10) ===
--   Sufijos: expansion / recalentamiento / recalentamiento_tardio /
--            estanflacion / contraccion / shock_energetico / crisis_financiera
--   n_obs_{sufijo}        Meses del fondo en ese régimen (siempre presente)
--   return_ann_{sufijo}   Retorno anualizado en ese régimen (%) — si n_obs ≥ 12
--   vol_ann_{sufijo}      Volatilidad anualizada en ese régimen (%) — si n_obs ≥ 12
--   sharpe_{sufijo}       Sharpe en ese régimen — si n_obs ≥ 12
--   regime_coverage_ratio    Fracción de 7 regímenes con n_obs ≥ 12  [0,1]  (P3-01)
--   crisis_stress_score_mdd  Max drawdown sobre meses de Crisis_Financiera (ratio ≤ 0)  (P3-02)
--   crisis_stress_score_ttr  Meses de recuperación sobre meses de Crisis_Financiera  (P3-02)

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
    step            TEXT,       -- NAV_LOAD / DEFLATE / CALC_METRICS / WRITE / BACKFILL_START / BACKFILL_END / RUN_SUMMARY
    status          TEXT,       -- OK / WARN / ERROR / SKIP / INFO / ABORT_NO_NEW_NAV
    horizon         TEXT,
    metric_version  TEXT,
    message         TEXT,
    batch_id        TEXT,       -- v26: id del run P2 (correlaciona filas Gold con el run que las produjo)
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
    -- v25 (2026-07-19): estado del ciclo de vida de los datos NAV descargados.
    -- Permite invalidar datos y forzar recálculos sin borrar filas ni re-descobrir.
    --   OK                  — datos válidos y actualizados (estado normal)
    --   FORCE_REFRESH       — re-descargar todo desde Morningstar (anula delta al-día)
    --   RECALCULATE_MONTHLY — daily OK, solo resamplear diario→mensual (sin red)
    --   RECALCULATE_METRICS — NAV OK, solo recalcular métricas P2 (sin red ni NAV)
    --   PENDING             — descubierto pero todavía no cargado
    --   STALE_FROZEN        — fuente estructuralmente detenida (sanción/baja del
    --                         proveedor); nunca se reintenta (v26, commit 36fa49f).
    --                         FIX-FROZEN-NAV-SCHEMA-1 (2026-09-15): este valor
    --                         faltaba en el CHECK -- la tabla viva no tiene el
    --                         constraint (columna añadida via ALTER TABLE antes
    --                         de que este CHECK existiera), por eso producción
    --                         nunca lo notó; una BD nueva construida desde este
    --                         fichero sí lo habría rechazado.
    data_status     TEXT    DEFAULT 'OK'
        CHECK (data_status IN (
            'OK','FORCE_REFRESH','RECALCULATE_MONTHLY','RECALCULATE_METRICS',
            'PENDING','STALE_FROZEN'
        )),

    FOREIGN KEY (isin) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_nav_sources_status      ON nav_sources (status);
CREATE INDEX IF NOT EXISTS idx_nav_sources_source      ON nav_sources (source);
CREATE INDEX IF NOT EXISTS idx_nav_sources_data_status ON nav_sources (data_status);

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
    benchmark_role      TEXT DEFAULT 'asset_proxy',-- Phase 2 BL-BENCH-ROLE: hurdle_rate / asset_proxy
    extracted_at        TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    PRIMARY KEY (ISIN, source)
);

CREATE INDEX IF NOT EXISTS idx_fb_isin        ON fund_benchmarks (ISIN);
CREATE INDEX IF NOT EXISTS idx_fb_id          ON fund_benchmarks (benchmark_id);
CREATE INDEX IF NOT EXISTS idx_fb_provider    ON fund_benchmarks (provider);

-- ============================================================
-- fund_nav_monthly  (v24: incorporada al schema canónico)
-- Serie mensual de NAV por ISIN — último NAV de cada mes.
-- Poblada por proyecto2/src/discovery/nav_discovery.py.
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_nav_monthly (
    ISIN            TEXT    NOT NULL,
    Date            DATE    NOT NULL,
    NAV             REAL    NOT NULL,
    NAV_Currency    TEXT,
    NAV_Type        TEXT    DEFAULT 'NAV',
    Is_Estimated    INTEGER DEFAULT 0,
    Data_Source     TEXT,
    Ingested_At     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (ISIN, Date),
    FOREIGN KEY (ISIN) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_nav_monthly_isin ON fund_nav_monthly (ISIN);
CREATE INDEX IF NOT EXISTS idx_nav_monthly_date ON fund_nav_monthly (Date);

-- ============================================================
-- fund_nav_daily  (v24: NUEVA — horizonte corto defensivo)
-- Serie diaria de NAV por ISIN, antes del resample mensual.
-- Usada por proyecto2/src/calculations/short_horizon.py para
-- métricas rolling_1m / rolling_3m / rolling_6m (metric_version='d1').
-- Corrección de iliquidez (AC-adjusted vol) aplicada sobre esta serie.
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_nav_daily (
    ISIN            TEXT    NOT NULL,
    Date            DATE    NOT NULL,
    NAV             REAL    NOT NULL,
    NAV_Currency    TEXT,
    NAV_Type        TEXT    DEFAULT 'TOTAL_RETURN_IDX',
    Is_Estimated    INTEGER DEFAULT 0,
    Data_Source     TEXT,
    Ingested_At     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (ISIN, Date),
    FOREIGN KEY (ISIN) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_nav_daily_isin ON fund_nav_daily (ISIN);
CREATE INDEX IF NOT EXISTS idx_nav_daily_date ON fund_nav_daily (Date);

-- ============================================================
-- fund_metric_timeseries  (v26 — P2 rolling indicators)
-- Serie temporal de métricas rolling por ISIN.
-- Modelo Hybrid: serie completa para 3 métricas curadas
-- (roll_vol_ann, roll_max_dd, roll_return_ann) en todas las
-- ventanas ROLLING_WINDOWS + SHORT_WINDOWS.
-- Poblar incrementalmente (upsert por date); NO sobreescribir
-- toda la historia cada ciclo → columna date en la PK.
-- ref_type / ref_value: cuando la métrica es vs categoría o
-- benchmark (ref_type='category' / 'benchmark'), permite guardar
-- el valor de referencia junto al del fondo.
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_metric_timeseries (
    isin            TEXT    NOT NULL,
    metric          TEXT    NOT NULL,   -- vol_ann / max_dd / return_ann / sharpe / sortino (v29)
    window          TEXT    NOT NULL,   -- rolling_1y / rolling_2y / rolling_3y / rolling_5y
                                        -- rolling_10y / rolling_1m / rolling_3m / rolling_6m
    date            DATE    NOT NULL,   -- fecha de cálculo (último día del período)
    value           REAL,               -- valor de la métrica (puede ser NULL si min_obs no alcanzado)
    real_flag       INTEGER NOT NULL    DEFAULT 0
        CHECK (real_flag IN (0, 1)),    -- 0=nominal  1=deflactado por IPC
    ref_type        TEXT,               -- NULL=absoluto / 'category' / 'benchmark'
    ref_value       REAL,               -- valor de referencia (categoría / benchmark)
    source_rows     INTEGER,            -- nº de NAV usados en el cálculo
    load_ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    algorithm_version TEXT,             -- v26: CALC_VERSION que produjo la fila (primer INSERT; preservado por INSERT OR IGNORE)
    batch_id        TEXT,               -- v26: id del run P2 que insertó la fila por primera vez

    PRIMARY KEY (isin, metric, window, date, real_flag),
    FOREIGN KEY (isin) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

-- v29 index rationalization: four indexes dropped (from original five), two remain.
-- Dropped:
--   idx_fmts_isin_metric       — strict prefix of idx_fmts_isin_metric_window_real_date.
--   idx_fmts_date              — global date scan; superseded by composites.
--   idx_fmts_metric_window     — strict prefix of idx_fmts_mwr_isin_date.
--   idx_fmts_metric_window_real_date — superseded by idx_fmts_mwr_isin_date which adds
--                                      isin before date, making it optimal for both the
--                                      cross-sectional GROUP BY and the per-fund max-date
--                                      subquery.
-- Two indexes remain (both hot-path covering indexes):
-- Per-fund max-date subquery — covering; makes 16M-row table index-served.
CREATE INDEX IF NOT EXISTS idx_fmts_isin_metric_window_real_date
    ON fund_metric_timeseries (isin, metric, window, real_flag, date);
-- Cross-sectional GROUP BY (post-loop snapshot, fallback DB query) — isin before date
-- makes this better than the old idx_fmts_metric_window_real_date for both use cases.
CREATE INDEX IF NOT EXISTS idx_fmts_mwr_isin_date
    ON fund_metric_timeseries (metric, window, real_flag, isin, date);

-- ============================================================
-- fund_metric_alerts  (v26 — P2 WARN/ALARM engine)
-- Estado actual de alertas de valor de métrica por ISIN.
-- Tabla de estado ACTUAL (current-state), reconstruida cada ciclo
-- como fund_data_quality_issues. Una fila por (isin, metric, window).
-- Compara fondos vs su peer group (Fund_Nature) o vs su benchmark.
-- rule_code: código de la regla que disparó la alerta (ALERT_RULES).
-- reference_value: valor del umbral (p90/p97 de categoría, etc.).
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_metric_alerts (
    isin            TEXT    NOT NULL,
    metric          TEXT    NOT NULL,   -- vol_ann / max_dd / return_ann (v29 normalized names)
    window          TEXT    NOT NULL,   -- ventana temporal de la métrica
    level           TEXT    NOT NULL    -- OK / WARN / ALARM
        CHECK (level IN ('OK', 'WARN', 'ALARM')),
    rule_code       TEXT    NOT NULL,   -- código de la regla (ej. 'VOL_CAT_P90')
    value           REAL,               -- valor de la métrica del fondo
    reference_value REAL,               -- valor de referencia (umbral de la categoría)
    ref_type        TEXT,               -- 'category' / 'benchmark'
    detected_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (isin, metric, window),
    FOREIGN KEY (isin) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fma_level      ON fund_metric_alerts (level);
CREATE INDEX IF NOT EXISTS idx_fma_rule       ON fund_metric_alerts (rule_code);
CREATE INDEX IF NOT EXISTS idx_fma_isin       ON fund_metric_alerts (isin);


-- fund_metric_state  (v27 — idempotency / input-hash cache; EFF-1 OLS cadence)
-- One row per (isin, metric_version): records the SHA-1 fingerprint of the
-- inputs used in the last successful P2 calculation for that fund.
-- Pipeline skips a fund when stored hash == current hash (unless --force).
-- EFF-1 columns: last_ols_quarter / last_ols_nav_count gate the OLS regression
-- to quarterly cadence; added at runtime via ALTER TABLE if not present.
CREATE TABLE IF NOT EXISTS fund_metric_state (
    isin                TEXT    NOT NULL,
    metric_version      TEXT    NOT NULL DEFAULT 'v1',
    input_hash          TEXT    NOT NULL,           -- SHA-1 of NAV+IPC+code version
    calculated_at       TEXT    NOT NULL,           -- ISO date of last successful run
    last_ols_quarter    TEXT,                        -- YYYY-Q of last OLS run (EFF-1)
    last_ols_nav_count  INTEGER,                     -- NAV row count at last OLS (EFF-1)
    PRIMARY KEY (isin, metric_version),
    FOREIGN KEY (isin) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

-- ============================================================
-- fund_cost_corrections  (P1 — promoted from db/migrations/20260823_cost_
-- corrections_audit.sql to canonical schema; doc/reglas/AUDITORIA_ESTADISTICA.md
-- §7 finding J: the table existed in production with 6,077 rows but no
-- CREATE TABLE in this file and zero code writers — preserve_and_write()
-- in shared/statistical_audit/persistence.py is now the single writer.)
-- One row per correction: preserves the prior value before any cost
-- component in fund_master/fund_cost_schedule is overwritten. No cost datum
-- is ever lost (P#1).
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_cost_corrections (
    ISIN         TEXT    NOT NULL,
    Column_Name  TEXT    NOT NULL,   -- e.g. 'ACI_RHP'
    Old_Value    REAL,               -- preserved value (NULL if there was none)
    New_Value    REAL,               -- value after the correction (NULL = nulled out)
    Reason       TEXT    NOT NULL,   -- fix code, e.g. 'F3-PROJECTION-NO-ANCHOR'
    Evidence     TEXT,               -- concrete evidence (KID excerpt, etc.)
    Corrected_At TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ISIN, Column_Name, Corrected_At)
);

CREATE INDEX IF NOT EXISTS idx_fcc_isin   ON fund_cost_corrections(ISIN);
CREATE INDEX IF NOT EXISTS idx_fcc_reason ON fund_cost_corrections(Reason);
CREATE INDEX IF NOT EXISTS idx_fcc_column ON fund_cost_corrections(Column_Name);

-- ============================================================
-- audit_statistic / audit_finding  (cross-domain — statistical audit engine,
-- doc/reglas/AUDITORIA_ESTADISTICA.md §6). Append-only, keyed by run_id.
-- fund_data_quality_issues does not serve this purpose: it is UNIQUE
-- (ISIN, check_code) — one row per fund per code — and is rebuilt by
-- DELETE+INSERT every P1 pipeline cycle, while most findings here are
-- population-level (no ISIN) and must survive across audit runs for
-- compare_runs() (function #13) to have history to compare against.
--
-- audit_statistic = the STATISTICAL FACT (a computed number or status
-- label): population, group, stat name, value. Never a rule evaluation.
-- audit_finding    = the RULE EVALUATION over a fact or a row: severity,
-- threshold, evidence. Separating the two means thresholds can be revised
-- without recomputing distributions (§2.4/§4 in the doc).
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_statistic (
    run_id          TEXT    NOT NULL,
    computed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    domain          TEXT    NOT NULL CHECK (domain IN ('p2_metrics', 'cost_attributes')),
    population      TEXT    NOT NULL,   -- 'GLOBAL' or a PEER segment label
    group_key       TEXT    NOT NULL,   -- e.g. 'vol_ann|since_inception|0|v1' or 'Ongoing_Charge_Recurrent'
    stat_name       TEXT    NOT NULL,   -- 'n_total' / 'p50' / 'skew' / 'zero_pct' / 'cv_status' / ...
    stat_value      REAL,               -- numeric statistics; NULL = not computed (never NaN)
    stat_text       TEXT,               -- status labels / non-numeric facts (mass_class, cv_status, ...)
    n               INTEGER,
    catalog_version TEXT,               -- content hash of the catalogs+tolerances that produced the row; NULL = pre-versioning

    PRIMARY KEY (run_id, domain, population, group_key, stat_name)
);

CREATE INDEX IF NOT EXISTS idx_audit_statistic_group ON audit_statistic (domain, group_key, stat_name);
CREATE INDEX IF NOT EXISTS idx_audit_statistic_run   ON audit_statistic (run_id);

CREATE TABLE IF NOT EXISTS audit_finding (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT    NOT NULL,
    detected_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    domain                TEXT    NOT NULL CHECK (domain IN ('p2_metrics', 'cost_attributes')),
    block                 TEXT    NOT NULL,   -- 'BLOCK1' .. 'BLOCK7'
    rule_id               TEXT    NOT NULL,
    rule_class            TEXT    NOT NULL
        CHECK (rule_class IN ('HARD_INVARIANT', 'PLAUSIBILITY', 'STATISTICAL_ANOMALY')),
    severity              TEXT    NOT NULL CHECK (severity IN ('INFO', 'WARN', 'ALARM')),
    group_key             TEXT    NOT NULL,
    isin                  TEXT,               -- NULL for population-level findings (most of Block 1/2/3)
    value                 REAL,
    reference_value       REAL,
    threshold             REAL,
    distance              REAL,
    evidence              TEXT,
    root_cause_candidate  TEXT,
    catalog_version       TEXT,         -- content hash of the catalogs+tolerances that produced the row; NULL = pre-versioning

    FOREIGN KEY (isin) REFERENCES fund_master (ISIN) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audit_finding_run      ON audit_finding (run_id);
CREATE INDEX IF NOT EXISTS idx_audit_finding_rule     ON audit_finding (domain, rule_id);
CREATE INDEX IF NOT EXISTS idx_audit_finding_isin     ON audit_finding (isin);
CREATE INDEX IF NOT EXISTS idx_audit_finding_severity ON audit_finding (severity);

