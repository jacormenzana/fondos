# shared/config.py
# -*- coding: utf-8 -*-
"""
Configuracion centralizada del sistema de fondos.

Todos los proyectos (P1, P2, P3) importan de aqui la ruta a la base de
datos unificada, los parametros globales y los vocabularios de dominio. (La ruta a la BD SQLite, DB_PATH, se elimino
con su retirada — 2026-09-26, FND-0102; la BD es PostgreSQL, ver shared/db.py.)

Sustituye a proyecto1/src/config.py y proyecto2/src/config.py.

Uso desde cualquier modulo:
    from shared.config import RISK_FREE_RATE_ANN

Cambios v27 (2026-09-27, pivot de real_flag):
  - SCHEMA_VERSION: "v26" → "v27".
  - gold.fund_metric_timeseries: real_flag (smallint, parte de la PK) pivotado a
    value_nominal/value_real/has_real — real_flag deja de existir como columna.
    ref_type/ref_value ELIMINADAS (100% NULL en produccion antes del pivote).
    gold.v_fund_metric_timeseries_long reconstruye la forma larga (real_flag) pre-pivote,
    sin perdida, para todo lector que aun la necesite (Superset, auditoria estadistica).
    Ver db/pg/30_gold.sql, db/pg/40_matviews.sql, scripts/ops/apply_fmts_pivot.py.
  - gold.fund_metrics NO tocada (data dispersa 59/41, ~24 lectores en P3 — fuera de alcance).

Cambios v23 (2026-07-18, FIX-UNIVERSE-RECON-1):
  - SCHEMA_VERSION: "v22" → "v23".
  - fund_master: In_Current_Universe (INTEGER NOT NULL DEFAULT 1) añadida.
    Soft-delete flag regenerado en cada ciclo por reconcile_universe_membership()
    en fund_writer.py. 1 = en el harvest vigente, 0 = huérfano. No COALESCE-
    protegido (mismo patrón que SRRI_Visual). No clasificación: no entra en
    ATTRIBUTE_CATALOG.

Cambios v22 (2026-07-05, FIX-DQ-1):
  - SCHEMA_VERSION: "v21" → "v22".
  - fund_data_quality_issues (tabla nueva): estado "actual" de issues de
    calidad de datos por fondo, uno por (ISIN, check_code), reconstruida
    en cada ciclo de pipeline.py (DELETE+INSERT por ISIN). Complementa a
    ingestion_log (histórico append-only de todos los eventos de todos
    los ciclos): da una vista consultable de "qué está mal con el fondo
    X ahora mismo" sin tener que filtrar el histórico completo.
  - DATA_QUALITY_SEVERITY: ranking de severidad usado para calcular
    Data_Quality_Flag de forma determinista como el máximo entre el
    nivel base (derivado de SRRI_Quality_Flag) y el de cada issue
    acumulado durante el procesamiento del ISIN -- sustituye a las
    mutaciones secuenciales dispersas ("solo si Data_Quality_Flag=='OK'",
    "solo si != 'WARN'", o sin guard alguno) que existían en pipeline.py
    antes de este fix, y que en al menos un caso (INTER_NTC_CONTRADICTION)
    llegaban a suprimir el propio registro en ingestion_log cuando un
    chequeo anterior ya había tocado el flag.

Cambios v21 (2026-07-05, BL-44-FX):
  - SCHEMA_VERSION: "v20" → "v21".
  - fund_master: Asset_Currency (TEXT) añadida -- divisa de los activos/
    estrategia del fondo (vs. Fund_Currency = divisa de la clase de
    participación), inferida del nombre del fondo vía classify_utils.
    detect_asset_currency_from_name(). No es un revival de Portfolio_Currency
    (eliminada en v20, 98.7% NULL vía extracción de texto KIID demasiado
    literal) -- nombre y fuente de datos distintos; Portfolio_Currency
    permanece en V20_DELETED_ATTRIBUTES.
  - ATTRIBUTE_CASING: Asset_Currency añadida como "CODE" (mismo tratamiento
    que Fund_Currency).

Cambios v19.2 (BL-COST Sprint 2 S2-D):
  - PRIIPS_COST_EXTRACTION_ENABLED: False → True.
    Activado tras smoke test y despliegue de S2-C (pipeline.py v37,
    fund_writer.py v25, ucits_cost_extractor.py nuevo).

Cambios v19.1:
  - COST_CROSS_VALIDATION_TOLERANCE_PCT corregido: 0.05 → 0.0005 (5bp reales).
    0.05 eran 500bp (5%), no 5bp. Fix detectado en sesión BL-COST Sprint 2 S2-A.
  - COST_SCHEDULE_SOURCE_VALUES ampliado: añadidos 'PRIIPS_COMPOSITION' y 'PRIIPS_TEXT'.
  - Kill-switch PRIIPS_COST_EXTRACTION_ENABLED activado como constante real (False).

Cambios v19:
  - SCHEMA_VERSION: "v18" → "v19".
  - Constantes BL-COST-2: KID_FORMAT_VALUES, COST_EXTRACTION_QUALITY_VALUES,
    COST_SCHEDULE_SOURCE_VALUES, PRIIPS_INVESTMENT_BASE,
    COST_CROSS_VALIDATION_TOLERANCE_PCT.

Cambios v18:
  - SCHEMA_VERSION: "v17" → "v18".
  - DLA2 kill-switch añadido en io.py (referenciado aquí para documentación):
    DLA_TABLE_SERIALIZATION_ENABLED controla la extracción de tablas Cat.1+2.

Cambios v17:
  - SCHEMA_VERSION: constante canónica de versión de schema.
  - LOG_DIR.mkdir() eliminado del nivel de módulo (efecto secundario
    en import). Usar get_log_dir() cuando se necesite el directorio.
  - DOMAIN_VALUES: vocabularios canónicos de atributos con lista cerrada.
    Fuente única de verdad para validaciones en P1/P2/P3.
  - Añadidos vocabularios: Investment_Focus, Credit_Quality, Fee_Known_Flag,
    Profile (con Agresivo), Theme (con Core/General).
"""

from pathlib import Path

# ============================================================
# Raíz del proyecto global
# ============================================================
# Este fichero vive en:  <raiz>/shared/config.py
_ROOT = Path(__file__).resolve().parent.parent   # c:/desarrollo/fondos


# ============================================================
# Fichero .env (interruptor central del backend de BD)
# ============================================================
def load_env_file(path, environ=None) -> dict:
    """
    Carga KEY=VALUE de `path` en `environ` (por defecto os.environ) SIN pisar variables ya
    definidas: el entorno real siempre gana. Devuelve las claves que ha establecido.

    Por qué existe (Postgres migration, FND-0068, 2026-09-23): .env.example llevaba tiempo diciendo
    "copia a .env", pero NINGÚN código lo leía, y ningún lanzador .bat fija FONDOS_DB_BACKEND /
    FONDOS_PG_DSN. Sin un punto central, cada paso de cada lanzador habría tenido que recibir el
    backend por separado — y un solo paso olvidado sigue escribiendo en SQLite (ya retirada) con
    código de salida 0 mientras el resto escribe en Postgres. Con esto, un único .env (fuera de git)
    decide el backend de TODOS los entry points Python, incluidos los pasos lanzados desde .bat.

    Formato deliberadamente mínimo: líneas KEY=VALUE, `#` al inicio = comentario, comillas simples o
    dobles opcionales alrededor del valor. Sin comentarios en línea, sin `export`, sin expansión.
    Un fichero ausente o ilegible no es un error (comportamiento actual intacto).
    """
    import os
    environ = os.environ if environ is None else environ
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    loaded = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key and key not in environ:
            environ[key] = val
            loaded[key] = val
    return loaded


def _autoload_env_file() -> None:
    import os
    import sys
    # Nunca bajo pytest: un .env de desarrollador con FONDOS_DB_BACKEND=postgres redirigiría en
    # silencio cualquier test que llame a get_connection() sin backend explícito.
    if "pytest" in sys.modules:
        return
    load_env_file(os.environ.get("FONDOS_ENV_FILE") or (_ROOT / ".env"))
    # Owner tools (and only they, only at a real console) receive their owner-only strings from the vault, in this process only: shared/owner_vault.py. Never from .env.
    try:
        from shared import owner_vault
        owner_vault.inject_for_current_tool()
    except Exception:                                  # noqa: BLE001 -- the vault must never break a tool that does not need it
        pass


_autoload_env_file()

# ============================================================
# Versión canónica del schema de BD
# ============================================================
SCHEMA_VERSION: str = "v27"

# ============================================================
# v23 (FIX-UNIVERSE-RECON-1, 2026-07-18): In_Current_Universe (fund_master)
# ============================================================
# Soft-delete universe-membership flag (INTEGER NOT NULL DEFAULT 1).
# 1 = fondo pertenece al harvest vigente (db_document_catalogue MAX),
# 0 = huérfano preservado en fund_master por la política append-only.
# No es COALESCE-protegido: regenerado completamente cada ciclo por
# reconcile_universe_membership() (fund_writer.py). No entra en
# ATTRIBUTE_CATALOG ni en characterize_fund() (no es atributo de
# clasificación — es provenance de pipeline).

# ============================================================
# v22 (FIX-DQ-1): severidad de Data_Quality_Flag / fund_data_quality_issues
# ============================================================
# Ranking usado para calcular Data_Quality_Flag como el máximo de severidad
# entre el nivel base (derivado de SRRI_Quality_Flag) y el de cada issue
# acumulado durante el procesamiento de un ISIN (ver _finalize_data_quality_
# issues en proyecto1/core/pipeline.py). Mayor valor = más severo.
# WARN y MISSING comparten nivel: ambos representan un problema real y
# accionable (una discrepancia sin resolver o un dato base ausente),
# mientras que INFERRED es deliberadamente más leve -- indica un valor
# presente pero de menor confianza (inferido, no observado directamente).
DATA_QUALITY_SEVERITY: dict = {
    "OK": 0,
    "INFERRED": 1,
    "WARN": 2,
    "MISSING": 2,
}

# ============================================================
# v19 (BL-COST-2): constantes de coste PRIIPs/KID-aware
# ============================================================

# Valores categóricos permitidos (Principio #8 — Decisión 5)
KID_FORMAT_VALUES: tuple = ('UCITS_KIID', 'PRIIPS_KID', 'UNKNOWN')

COST_EXTRACTION_QUALITY_VALUES: tuple = (
    'HIGH', 'MEDIUM_CROSS', 'MEDIUM_EUR', 'MEDIUM_PCT', 'LOW', 'NONE'
)

COST_SCHEDULE_SOURCE_VALUES: tuple = (
    'PRIIPS_COSTS_OVER_TIME', 'PRIIPS_COMPOSITION', 'PRIIPS_TEXT',
    'UCITS_DERIVED', 'MANUAL'
)

# Inversión base estándar para tablas de coste PRIIPs (10.000 EUR/USD).
# Usado por priips_cost_extractor.py (Sprint 2) para convertir
# valores EUR absolutos a porcentajes.
PRIIPS_INVESTMENT_BASE: float = 10000.0

# Tolerancia para cross-validation %↔EUR en priips_cost_extractor.
# Si |implied_pct - declared_pct| <= este valor → quality = 'HIGH'.
COST_CROSS_VALIDATION_TOLERANCE_PCT: float = 0.0005  # 5 basis points (0.05%)

# Kill-switch BL-COST-4c (Sprint 2). Activado en v19.2 tras smoke test S2-D.
PRIIPS_COST_EXTRACTION_ENABLED: bool = True

# Kill-switch v24: pesos de scoring sobre métricas de horizonte corto (d1).
# False = solo se aplica el gate duro (check_hard_filters); el score base
# no recibe aún señales cortas. Activar SOLO después de backtest walk-forward.
SHORT_HORIZON_SCORING_ENABLED: bool = False

# Kill-switch v26: motor de indicadores rolling + WARN/ALARM.
# False = la pipeline P2 salta los módulos rolling_stats y alarm_engine;
#         fund_metric_timeseries y fund_metric_alerts no se escriben.
# True  = activa el cálculo completo. Activar SOLO después de backfill
#         inicial y validación de las señales percentil vs P3 scoring.
ROLLING_STATS_ENABLED: bool = True

# Kill-switch P2-10: habilita el uso de señales rolling-percentil (pctile_cat)
# en la Capa 3 del scoring P3. Activar SOLO tras validar correlación predictiva
# con retorno futuro en el régimen activo (backtest walk-forward pendiente).
ROLLING_PCTILE_P3_ENABLED: bool = False
# FND-0199: horizon under which P2 stores the category percentiles (vol_ann / max_dd / return_ann _pctile_cat) that the
# scorer reads when ROLLING_PCTILE_P3_ENABLED. P2 writes them per rolling window (rolling_1y .. rolling_10y), never at
# since_inception, so the old read returned nothing. rolling_3y matches the slope signals read next to them.
ROLLING_PCTILE_HORIZON: str = "rolling_3y"

# Kill-switch P3 optimization plan Phase 2c (2026-09-18): activa la carga
# automática de la cartera del escenario anterior en PortfolioBuilder.build()
# cuando no se pasa previous_portfolio explícitamente, habilitando el bonus
# de histéresis (HYSTERESIS_BAND) sobre los titulares. Antes de este
# kill-switch, previous_portfolio quedaba en None SIEMPRE porque ningún
# caller del repo lo pasaba -- el código de histéresis era correcto pero
# nunca se ejecutaba en producción. Default False: comportamiento actual
# sin cambios hasta activarlo.
PORTFOLIO_HYSTERESIS_ENABLED: bool = False
# FND-0158 (2026-10-04, owner decision): incumbent score bonus used when PORTFOLIO_HYSTERESIS_ENABLED. 10% = best blind
# (2017+) net Sharpe in 3 of 4 full-universe PIT attribution runs, inside the owner's 10-20% range; 20% only lowers turnover
# further without a better Sharpe (out/reports/pit_backtest/20261004_*). Single source: portfolio_builder.HYSTERESIS_BAND
# reads it. Enabling the switch waits for the P2 recompute (portfolios on one CALC_VERSION) and the first uniform P3 build.
PORTFOLIO_HYSTERESIS_BAND: float = 0.10
# FND-0193 SIMPLIFY (verdict of record 2026-10-19, doc/decisiones/SIMPLIFY_RUNBOOK_FND-0193.md): weights inside a sub-portfolio are EQUAL among
# the selected funds; score-proportional weighting did not earn its place out of sample (Sharpe -0.023 without it, 90% CI -0.005..+0.053, max DD
# not worse). The score still ranks and selects (hysteresis included). ONE source for the live builder (portfolio_builder.WEIGHT_METHOD) and the
# engine default (portfolio_engine.PortfolioConstraints.weight_method), so the Backtester and the PIT build what production builds
# (test_backtest_parity). The FND-0193 study names its pre-registered model's method explicitly (pit_value.MODEL_WEIGHT_METHOD =
# "score_proportional"), so the saved runs keep reproducing. Rollback: "score_proportional".
PORTFOLIO_WEIGHT_METHOD: str = "equal"

# Kill-switch P3 optimization plan Phase 2d (2026-09-18): activa el filtro
# de coste de rotación en PortfolioBuilder.build() -- revierte al titular
# las rotaciones cuya mejora de score no supera el coste estimado (spread +
# comisiones) más el margen mínimo. Antes de este kill-switch,
# should_rotate/rotation_plan solo los invocaba scripts/test/test_portfolio.py;
# build()/_persist() nunca los consultaban, así que las carteras persistidas
# no reflejaban ningún chequeo de coste de rotación. Default False.
ROTATION_COST_GATE_ENABLED: bool = False

# ============================================================
# Phase 1 — Benchmark asset-class derivation engine (BL-BENCH-DECOMP)
# ============================================================
# Kill-switch de la descomposición de benchmarks compuestos + fallback de
# cobertura en core.benchmark_normalizer.normalize_benchmark (dark-launch).
#   - Default False: normalize_benchmark conserva el comportamiento legacy
#     (primer match startswith gana; asset_class NULL si no hay alias).
#   - True: detecta benchmarks multi-activo (Equity+Fixed Income) → 'Mixed',
#     y asigna asset_class por familia de tokens cuando el alias falta (SG5).
# Resuelve SG1-compuesto (~36) + SG5-recuperable (~14). NO toca benchmarks de
# tipo cash/hurdle (SG3, Phase 2) ni el mislabel a nivel Fund_Nature
# (SG1-pure equity sobre fondo allocation, Phase 3 / INTER-18).
BENCHMARK_DECOMP_ENABLED: bool = False

# ============================================================
# Phase 2 — Benchmark role axis (hurdle vs asset proxy) (BL-BENCH-ROLE)
# ============================================================
# Kill-switch del eje benchmark_role en fund_benchmarks (dark-launch).
#   - Default False: el writer escribe benchmark_role='asset_proxy' (neutro);
#     la columna existe tras la migración pero la feature está inactiva.
#   - True: benchmark_role()=hurdle_rate para benchmarks de tipo cash/overnight
#     (SOFR, €STR, SONIA, TONA, SARON, EURIBOR, overnight, 1-month, eurodeposit)
#     sin componente invertible. Un benchmark hurdle_rate NO debe usarse como
#     proxy de clase de activo (excluir del alineamiento QA; Phase 3/INTER-18
#     lo salta al corroborar Fund_Nature).
# Resuelve SG3 (~67) + SG2 (~6) etiquetando hurdles en lugar de forzar Rate/FI.
BENCHMARK_ROLE_ENABLED: bool = True

# ============================================================
# Phase 3 — INTER-18 Benchmark-Composition ↔ Fund_Nature (BL-BENCH-NATURE)
# ============================================================
# Kill-switch del pase de reconciliación corroborativa contra Morningstar
# (dark-launch). WARNING-ONLY: nunca corrige Fund_Nature.
#   - Default False: el driver no escribe warnings en ingestion_log.
#   - True: scripts/diag/inter18_reconciliation.py compara asset_class
#     Morningstar (asset_proxy, no hurdle) vs Fund_Nature y registra los
#     desajustes (paso 'INTER-18') para revisión manual.
INTER18_RECONCILIATION_ENABLED: bool = False

# ============================================================
# v20 (INTEGRATED_SPEC_v20_v2 — Job B: arbitración de coste DLA2)
# ============================================================

# Kill-switch de la arbitración dual bands-X / ruled (dark-launch).
# Default False: el hook de pipeline no se ejecuta y no se escriben veredictos.
# Se pone a True solo para el backfill corpus-wide (FORCE_REFRESH + local).
DLA2_ARBITRATION_ENABLED: bool = True

# Enum canónico de veredicto de arbitración por componente (6 estados).
# Severidad (peor → mejor) para la vista overall worst-of:
#   CONFLICT > BOTH_FAIL > OCR_RECOVERED > ONLY_BANDS_X > ONLY_RULED > AGREE
# NULL (no almacenado aquí) = nunca arbitrado (CACHED/sin PDF local/flag off).
COST_ARBITRATION_VALUES: tuple = (
    'AGREE', 'OCR_RECOVERED', 'BOTH_FAIL',
    'ONLY_BANDS_X', 'ONLY_RULED', 'CONFLICT',
)

# Tolerancias híbridas para comparar valores de coste (puntos %).
# Fuente única de verdad (R-1): usadas por classify_utils.cost_values_agree
# tanto en la arbitración dual como en la cross-validation %↔EUR.
# Sustituyen al _TOL=0.011 fijo del prototipo. math.isclose(rel_tol, abs_tol).
# Semilla; ajustar contra el set histórico de CONFLICT en el test pass (§5).
COST_CMP_ABS_TOL: float = 0.0002   # 0.02 pp — suelo de redondeo/tipografía
COST_CMP_REL_TOL: float = 0.01     # 1% relativo — escala con la magnitud

# ============================================================
# Morningstar benchmark loader (proyecto1/src/loaders/benchmark_loader.py)
# ============================================================
# Negative cache: ISINs for which Morningstar returned no benchmark are not re-queried until
# `next_check_at` (control.benchmark_ms_checks). Days to wait after the 1st, 2nd and later
# consecutive miss — newly launched funds are the likeliest to gain a benchmark, so the first
# re-check is soon and the interval then decays. `--mode load` and `--recheck-negatives` bypass it.
BENCH_NEGATIVE_BACKOFF_DAYS: tuple = (7, 30, 90)

# Anomaly circuit-breaker: a raw `indexName` that is NOT already a known benchmark and is returned
# for more than MIN_FUNDS funds AND for more than MIN_SHARE of the funds processed so far is
# quarantined for the rest of the run (no fund_benchmarks write, no cache row) and logged as
# BENCH_MS/ANOMALY. Guards against the vendor starting to return a new placeholder at scale.
BENCH_ANOMALY_MIN_FUNDS: int = 20
BENCH_ANOMALY_MIN_SHARE: float = 0.40

# Network/HTTP failure guard (2026-09-26 rehearsal: an outage made 610 of 687 calls fail and the run
# still exited 0). The loader stops early after this many CONSECUTIVE failed calls (an outage, not a
# throttled call), and exits BENCH_EXIT_NETWORK when the failure rate of the calls it made exceeds
# BENCH_MAX_ERROR_RATE. Errors are never cached, so a re-run simply retries them; P1P2_runCycle.bat
# then stops at PASO 1 and is resumed with `--from 1` once the network is back.
BENCH_CONSECUTIVE_ERROR_LIMIT: int = 10
BENCH_MAX_ERROR_RATE: float = 0.10
BENCH_EXIT_NETWORK: int = 3

# Payload-shape errors (wrong types, non-object body) tolerated in one run before the loader stops
# writing negatives for the rest of that run (an endpoint change must not poison the cache).
BENCH_SCHEMA_ERROR_LIMIT: int = 5

# ============================================================
# P3 data-freshness gate (proyecto3/src/data_freshness.py, FND-0098)
# ============================================================
# p3_build_portfolio aborts (exit 2) when its inputs are older than these limits, unless
# `--allow-stale`. Ages are in calendar days vs. today. The check is universe-level, NOT per fund:
# a few permanently frozen funds (FND-0041) must not block every run.
#   nav_p10        : the 10th percentile of "newest monthly NAV date" over the active universe
#                    (so up to ~10% of funds may lag; a stale load moves the whole distribution)
#   macro_market   : last valid date of the market-driven regime inputs (macro series are indexed
#                    at month-end, so an age can be negative for the current month -> treated as 0)
#   macro_release  : same for the release-lag inputs (IPC / OECD CLI publish 4-8 weeks late)
#   harvest        : age of the newest db_document_catalogue harvest_ts (fund universe membership)
P3_FRESHNESS_MAX_AGE_DAYS: dict = {
    "nav_p10": 14,
    "macro_market": 45,
    "macro_release": 100,
    "harvest": 45,
}
P3_NAV_UNIVERSE_PERCENTILE: float = 0.10
# Metric-version uniformity: at least this share of the active universe must have its metrics computed
# under ONE CALC_VERSION (the dominant one). Peer percentiles / momentum rank / the P3 score mix funds,
# so a partial refresh after a version bump (or a sample run) must not silently produce a portfolio.
P3_MIN_UNIFORM_METRICS_SHARE: float = 0.98
P3_MACRO_RELEASE_LAG_INDICATORS: tuple = ("ipc_yoy_avg", "cli_eu")
# Publication lag, in months, of the release-lagged regime inputs, keyed by series_macro.indicator (FND-0194).
# Used ONLY by the backtester (RegimeClassifier(publication_lags=...)): the value dated month m is treated as
# observable from month m+lag, so the regime at t uses only data already published at t. Deliberately
# conservative fixed lags (not the real, revised release calendar): IPC flash ~2 weeks but final/EU series
# ~4-6 weeks, OECD CLI 4-8 weeks, ECB M3 ~4 weeks, M2/unemployment 4-6 weeks. Live classification does not
# lag (it always takes the latest value). Market-driven inputs (oil, VIX, spreads, rates, FX) are not lagged.
REGIME_PUBLICATION_LAG_MONTHS: dict = {
    "ipc_index":     2,
    "cli":           2,
    "m3_yoy":        2,
    "m2_yoy":        2,
    "m2_global_yoy": 2,
    "unemployment":  2,
}
P3_EXIT_STALE_INPUTS: int = 2
# Net unhedged non-EUR exposure budget (share of the MASTER portfolio weight), owner decision 2026-10-11. Derived from the mandate: a -10% move
# of the foreign currency vs EUR costs 0.10 x w of the portfolio, and 3 pp (w = 30%) is the share of the 15% max-drawdown budget granted to this
# single factor. Measured by shared/fx_exposure.py; used by the build log, the report cover, the promotion gate (HARD, no override) and, with
# FX_EXPOSURE_CONSTRAINT_ENABLED, by the builder itself.
MAX_NON_EUR_UNHEDGED_WEIGHT: float = 0.30
# Owner decision 2026-10-11: the budget is also a CONSTRUCTION constraint (portfolio_engine.select_within_exposure): each sub-portfolio is built so
# its exposed weight stays within the budget, hence so does the master portfolio -- the October DRAFT must be promotable by construction, not by
# luck. It was written as the safety net under a regime-evidence FX-neutralisation that was never built (FND-0258, closed as moot 2026-10-25: every
# MULT_* is 1.0 since the FND-0193 SIMPLIFY configuration); it stays as the build-time guard on non-EUR exposure and as the net to keep if a regime
# multiplier is ever reinstated. False restores the unconstrained build (the promotion gate still blocks an over-budget scenario).
FX_EXPOSURE_CONSTRAINT_ENABLED: bool = True
# Largest structural cash residue (share of the MASTER portfolio) a scenario may DECLARE and still be promoted (owner decision 2026-10-11).
# Cash appears only when a sub-portfolio has too few eligible non-exposed funds to reach 100% under the per-fund cap (FND-0187); the build
# records it in the scenario notes and prints which sub-portfolio is short. Hard gate, no override: change the constant by a reviewed commit.
MAX_PORTFOLIO_CASH_WEIGHT: float = 0.10
# FND-0251 (Commit B), build-time data eligibility in the live scorer: with the switch ON a fund is INELIGIBLE (exclusion_reason set, score 0) when
#   * its newest monthly NAV is older than P3_FUND_NAV_MAX_AGE_DAYS (today's incidental protection is the drawdown filter, which only
#     catches frozen funds that also fell), or
#   * it carries an ACTIVE accepted audit residual (control.audit_accepted_finding, domain p2_metrics): a residual is tolerated in the
#     universe, never in a portfolio -- the promotion gate refuses such a portfolio, so the build must not select it in the first place.
# ON since 2026-10-11 (owner decision), after scripts/audit/eligibility_switch_diff.py showed an EMPTY eligible-set difference on the live inputs
# (it shipped OFF in 385e31c). Rollback = set it back to False: nothing is written, no DDL. The point-in-time backtest frames carry neither
# input, so the PIT run is NOT affected by this switch (no PIT parity yet: FND-0254; its run manifest says so).
FUND_DATA_ELIGIBILITY_ENABLED: bool = True
P3_FUND_NAV_MAX_AGE_DAYS: int = 60        # same threshold as NAV_STALE_DAYS of the cycle report (monthly NAV, month-end indexed)
# NAV continuity (FND-0251 item C, same switch): over its OWN first..last monthly NAV span a fund is ineligible when its coverage (rows / expected
# months) is below P3_FUND_NAV_MIN_COVERAGE, or its longest run of consecutive missing months reaches P3_FUND_NAV_MAX_GAP_RUN_MONTHS.
# Why: P2 annualises by NUMBER OF ROWS, not dates (FND-0253), so a sparse series gets an inflated return_ann (LU1663960000: 20.9% stored vs 6.5% by
# dates, scored 0.78 in Dinamica). This is the GUARD; the fix is FND-0253. Measured 2026-10-11: 5 of 2,933 active funds trip it, 2 of them eligible,
# 0 in PRODUCTION; no quarterly reporter exists in the active universe.
P3_FUND_NAV_MIN_COVERAGE: float = 0.80
P3_FUND_NAV_MAX_GAP_RUN_MONTHS: int = 6
# FND-0323 D1 (owner decision 2026-10-25, option a): an ISIN whose KIID_Status is WRONG_DOC has no valid document, so every attribute that is only
# discoverable from the KIID cannot be trusted for it; with the switch ON such a fund is INELIGIBLE in P3 (exclusion_reason 'wrong_doc: ...', score 0).
# Scope: P3 eligibility ONLY -- P1 already drops WRONG_DOC from every block and P2 keeps computing their metrics. The PIT backtest applies the SAME
# rule through the scorer's check_hard_filters (parity, as FND-0254): the status has no history, so today's status is applied at every date
# (documented look-ahead, like every static attribute of the PIT universe). Shipped OFF in 34d68fa; ON since 2026-10-25 after
# scripts/audit/wrong_doc_switch_diff.py passed on the 40-ISIN sample (live + PIT, 7/7) and the hermetic gate was green; PRODUCTION and the latest drafts
# hold 0 WRONG_DOC funds. Rollback = False: nothing is written, no DDL.
P3_WRONG_DOC_EXCLUSION_ENABLED: bool = True
# FND-0289 (owner directive 2026-10-17), PRODUCTION alarm watch at promotion (scenario_lifecycle.GATE_ALARM_WATCH, control D-ALARM-WATCH): a fund
# of the PRODUCTION scenario that carries a current ALARM row in fund_metric_alerts for one of ALARM_WATCH_RULES may not get a HIGHER master
# weight in the promoted DRAFT (tolerance ALARM_WATCH_WEIGHT_TOLERANCE, one unit of the 4th decimal: largest-remainder rounding). Holding or
# lowering it is fine; the watch ends when P2 rewrites the alert to WARN/OK. Reasoned override (--accept-alarms + --ap). Only the rule codes'
# own (metric, window) pairs from ALERT_RULES are read, so legacy alert rows under other metric names never count. Fail closed: an unreadable
# alerts table refuses. Generic rule over held funds (owner choice; P#5: no fund is named). Not mirrored in the PIT backtest: the alerts have
# no history (same as the accepted-residual eligibility rule). Interim manual SQL check in FND-0289 until this switch is ON and pushed.
ALARM_WATCH_GATE_ENABLED: bool = True
ALARM_WATCH_RULES: tuple = ("DD_CAT_P03", "VOL_CAT_P97")
ALARM_WATCH_WEIGHT_TOLERANCE: float = 1e-4
# FND-0251: an accepted audit residual (control.audit_accepted_finding) is announced this many days before it lapses (audit report, promotion,
# cycle report). A warning, never a gate: a lapsed residual simply stops counting, so the finding comes back by itself at the next audit.
AUDIT_RESIDUAL_EXPIRY_WARN_DAYS: int = 30
# FND-0261 (GOV-02), audit completeness contract (shared/statistical_audit/completeness.py). Always on, whatever the switch: every skipped
# check is classified (MISSING / EMPTY / NOT_APPLICABLE), the run status (PASS / PASS_WITH_WARNINGS / FAIL / INCOMPLETE) is printed and, once
# the owner applies scripts/ops/migrate_audit_run.py, written to control.audit_run in the same transaction as the run's rows. With the switch ON:
#   * an INCOMPLETE run exits 2 (a required check could not run); the cycle launchers treat any audit RC as a [WARN], never a cycle failure;
#   * the promotion gate takes its evidence from control.audit_run: the latest FULL-scope run whose status is not INCOMPLETE (a FAIL run
#     counts: decision D4), instead of the latest audit_finding run id. A run persisted before the table has no header and is not evidence.
# ON since 2026-10-12 (owner decision). Shadow evidence on the live data instead of a 9-hour cycle (the cycle reruns the same code on the
# same tables): control.audit_run applied; FULL p2 run audit_20261012T182959Z = PASS_WITH_WARNINGS and FULL costs run audit_20261012T184300Z =
# FAIL (known cost defects; costs do not feed the gate yet, G-COST), both with 0 required checks missing; the gate selected the same run, defects,
# residuals and coverage for the PRODUCTION scenario with the switch OFF and ON. Rollback = False (no DDL to undo; headers stay harmless).
AUDIT_COMPLETENESS_CONTRACT_ENABLED: bool = True
# FND-0263 (GOV-04), governed exceptions. Whatever the switch, once the owner applies scripts/ops/migrate_audit_governance.py every accepted
# residual is an append-only event (control.audit_exception_event: GRANT / RENEW, then a separate APPROVE, decision D6; REVOKE with a cause)
# and control.audit_accepted_finding is refreshed from that history in the same transaction (accept_audit_residual.py). With the switch ON:
#   * the promotion gate refuses ([GATE:EXCEPTIONS], no override) when the history cannot be read or the table disagrees with it, instead of
#     reading an unreadable store as "no residuals";
#   * accept_audit_residual.py refuses the legacy direct write when the history table is missing.
# ON since 2026-10-12 (owner decision): migration applied by the owner (4 backfill events, projection == history in both domains, verified as
# fondos_app); shadow on the live data: the gate's history check returns no problem for the PRODUCTION scenario with the switch OFF and ON.
# Rollback = False (no DDL to undo; the history stays and keeps being written).
AUDIT_EXCEPTION_EVENTS_ENABLED: bool = True
# FND-0265 (GOV-06), two-pillar orchestrator (shared/governance/orchestrator.py). The promotion always computes and prints its verdict (each
# pillar PASS / PASS_WITH_WARNINGS / FAIL / INCOMPLETE and the decision); these switches decide what it may refuse:
#   GOVERNANCE_ORCHESTRATOR_ENABLED  the verdict DECIDES the promotion (OFF: shadow, the legacy gate decides): weights tolerance 5e-5 (D2) and
#                                    [GATE:MANIFEST] (the evidence audit run must carry git sha, config hash, catalogue and data manifest);
#   CODE_INTEGRITY_GATE_ENABLED      [GATE:CODE] blocks when HEAD != origin/master or tracked files are modified (D1); OFF: a warning. Cycle
#                                    launchers only ever warn (D3: p1p2_state preflight). Push before promoting;
#   COST_DOMAIN_GATE_ENABLED         [GATE:COST] blocks on open hard defects of the cost_attributes audit on a fund of the scenario (D8: first
#                                    scripts/audit/cost_gate_replay.py over the persisted runs, then one shadow cycle); OFF: a warning.
# The last two act only inside the orchestrator, i.e. with GOVERNANCE_ORCHESTRATOR_ENABLED. ORCHESTRATOR and CODE_INTEGRITY ON since 2026-10-17 (owner read the
# shadow verdict against evidence run audit_20261017T080449Z: DATA=PASS, only blocker = the scenario already PRODUCTION); COST stays OFF (FND-0276..0285).
GOVERNANCE_ORCHESTRATOR_ENABLED: bool = True
# FND-0264 (GOV-05), closed-loop remediation (shared/statistical_audit/incidents.py, scripts/audit/audit_incident.py). The plan (--plan, the D7
# shadow log) and the closure check (--close-check) always read-only work; this switch lets `audit_incident.py --open` WRITE: it records an
# incident in control.audit_incident (owner DDL: scripts/ops/migrate_audit_incident.py) and opens its backlog ticket through
# backlog_client.report_incident(). Decision D7 = open on the FIRST CRITICAL occurrence (policy parameter); ON since 2026-10-17 (owner confirmed D7 = first on the plan).
AUDIT_INCIDENT_LOOP_ENABLED: bool = True
CODE_INTEGRITY_GATE_ENABLED: bool = True
COST_DOMAIN_GATE_ENABLED: bool = False

# ============================================================
# Directorios de datos externos (inputs no versionados)
# ============================================================
DATA_DIR:     Path = _ROOT / "data"
MASTER_EXCEL: Path = DATA_DIR / "GestoresDeFondosv1.xlsx"

# ============================================================
# Directorios de outputs generados (no versionados)
# ============================================================
REPORTS_DIR:  Path = _ROOT / "out" / "reports"     # cross-project artifacts: rolling dashboard, PIT backtest runs, backlog_links

# Per-project report outputs (the files a person opens): one directory each under REPORTS_DIR. Single source for the generators:
#   p1 -> proyecto1.src.analysis.export_p1   (p1_export_*.xlsx)
#   p2 -> proyecto2.src.analysis.export_metrics (p2_metricas_*.xlsx)
#   p3 -> proyecto3.src.monthly_report       (informe_cartera_*.xlsx, drafts under p3/borradores)
# (FND-0270: before it, p1 and p3 wrote to the export folder of out/ and p2 to its metrics folder)
REPORT_DIRS: dict[str, Path] = {
    "p1": REPORTS_DIR / "p1",
    "p2": REPORTS_DIR / "p2",
    "p3": REPORTS_DIR / "p3",
}
METRICS_DIR:  Path = REPORT_DIRS["p2"]             # compat alias, no callers left

# Audit outputs (the files an audit leaves besides its rows in control.audit_*): one directory per project under out/audit, inside the
# repo and never under C:\data. Single source for the Python writers; the launchers derive the same two folders in lib/common.bat
# (AUDIT_P1_DIR / AUDIT_P2_DIR; the P1P2_AUDIT_DIR environment variable moves the root for both):
#   p1 -> benchmark consistency findings (proyecto1.tools.audit_benchmark_consistency, benchmark_audit_*.json)
#   p2 -> macro-beta snapshots and shift outliers (scripts/audit/beta_shift_audit.py, macro_betas_*.csv, beta_shift_outliers_*.csv)
AUDIT_DIRS: dict[str, Path] = {
    "p1": _ROOT / "out" / "audit" / "P1",
    "p2": _ROOT / "out" / "audit" / "P2",
}

# ============================================================
# Logging
# ============================================================
# NOTA: No se crea ningún directorio aquí para evitar efectos
# secundarios al importar. Usar get_log_dir() cuando se necesite.
#
# Registro único de enrutado de logs (doc/reglas/NORMAS_IMPLEMENTACION.md §4):
# todo log va al log/ de su proyecto o contexto. El estado de los launchers
# (lock, .state, env_driver_ok, env_blocked.log) vive en proyecto1/log por
# decisión del propietario; no es un log y no se enruta por aquí.
LOG_DIRS: dict[str, Path] = {
    "p1":     _ROOT / "proyecto1" / "log",
    "p2":     _ROOT / "proyecto2" / "log",
    "p3":     _ROOT / "proyecto3" / "log",
    "shared": _ROOT / "shared" / "log",
    "tests":  _ROOT / "tests" / "log",
}


def get_log_dir(context: str = "shared") -> Path:
    """Devuelve el directorio de logs del contexto (p1|p2|p3|shared|tests), creándolo si no existe.

    KeyError si el contexto no está registrado: un log nuevo no puede inventarse un destino.
    """
    try:
        d = LOG_DIRS[context]
    except KeyError:
        raise KeyError(f"unknown log context {context!r}; registered: {sorted(LOG_DIRS)}") from None
    d.mkdir(parents=True, exist_ok=True)
    return d


# Compatibilidad con código existente que usa config.LOG_DIR (sin callers hoy).
# Apunta al log/ de shared; no crea el directorio al importar.
LOG_DIR: Path = LOG_DIRS["shared"]


# ============================================================
# Parámetros globales P2 — cálculo de métricas
# ============================================================

# Tipo libre de riesgo de referencia (EUR STR / tipo deposito BCE)
# Actualizar manualmente cada trimestre o cargar desde series_macro
RISK_FREE_RATE_ANN: float = 0.040   # 4.0% anual (referencia marzo 2026)

# Versión canónica de métricas P2 activa
METRIC_VERSION: str = "v1"

# Umbral de pérdida mensual severa (para pct_severe_loss_months)
SEVERE_LOSS_THRESHOLD: float = -0.02   # -2% mensual

# Horizontes estándar disponibles (lista cerrada)
HORIZONS: list[str] = [
    "since_inception",
    "rolling_10y",
    "rolling_5y",
    "rolling_3y",
    "rolling_2y",   # v26: añadido para cerrar el gap 1y–3y
    "rolling_1y",
    "ytd",
    "crisis_2008",
    "crisis_2011",
    "crisis_2020",
    "crisis_2022",
]

# Región IPC por defecto para deflactación de NAV
REGION_IPC: str = "ES"

# Mínimo de observaciones mensuales para calcular métricas
MIN_NAV_ROWS: int = 12
# Mínimo de observaciones para regresión macro OLS (REL-5: centralizado aquí)
MIN_NAV_MACRO: int = 36
# FND-0164: a P2 run that quarantines more funds than this (validate_nav failures) opens a backlog ticket.
P2_QUARANTINE_ALERT_THRESHOLD: int = 10
# Mínimo de observaciones para métricas de persistencia del alpha (≥7 años)
MIN_NAV_PERSIST: int = 84

# Ventanas de crisis históricas (nombre -> (inicio, fin) inclusive)
CRISIS_WINDOWS: dict = {
    "crisis_2008": ("2007-10-01", "2009-03-31"),
    "crisis_2011": ("2010-12-01", "2012-07-31"),
    "crisis_2020": ("2020-02-01", "2020-04-30"),
    "crisis_2022": ("2021-11-01", "2022-10-31"),
}

# Horizontes rolling en meses (para slice automático)
ROLLING_WINDOWS: dict = {
    "rolling_1y":   12,
    "rolling_2y":   24,   # v26: gap genuino entre 1y y 3y — serie completa en fund_metric_timeseries
    "rolling_3y":   36,
    "rolling_5y":   60,
    "rolling_10y": 120,
}

# ============================================================
# Parámetros horizonte corto (P2 daily-NAV) — v24
# ============================================================
# Ventanas en días bursátiles (~21/día). Separadas de ROLLING_WINDOWS
# (que son meses) para evitar confusión. El pipeline P2 itera estas
# sobre fund_nav_daily, NO sobre fund_nav_monthly.
SHORT_WINDOWS: dict[str, int] = {
    "rolling_1m":  21,   # ~1 mes bursátil
    "rolling_3m":  63,   # ~3 meses bursátiles
    "rolling_6m": 126,   # ~6 meses bursátiles
}

# Observaciones mínimas por horizonte corto (días NAV).
# Más permisivos que MIN_NAV_ROWS (12) porque la serie es diaria.
# rolling_1m: ≥15 días (vacaciones reducen disponibilidad)
# rolling_3m: ≥45 días; rolling_6m: ≥90 días.
SHORT_WINDOW_MIN_OBS: dict[str, int] = {
    "rolling_1m":  15,
    "rolling_3m":  45,
    "rolling_6m":  90,
}

# Versión de métrica para la serie corta diaria — separa 'd1' de 'v1'
# (mensual) en fund_metrics. Nunca mezclar en queries sin filtrar por esta.
METRIC_VERSION_SHORT: str = "d1"

# Umbral de iliquidez: fracción de retornos diarios cero/repetidos por
# encima de la cual la volatilidad diaria no es de confianza.
LIQUIDITY_FLAG_THRESHOLD: float = 0.20   # >20% días sin movimiento → ilíquido

# ============================================================
# P2 — Umbrales de negocio centralizados (P0 canonicalización, 2026-09-14)
# ============================================================
# Antes vivían como constantes de módulo duplicadas/no auditables en cada
# fichero de src/calculations/ — sin índice único, mismo patrón de riesgo
# que causó la divergencia de fórmula de sortino (una copia editada, las
# demás no). MIN_PEERS en particular estaba triplicado con el mismo valor
# en momentum.py y dos veces literal en run_pipeline.py — ahora una sola
# fuente. Numérico/epsilon guards (ej. abs(x) < 1e-6) permanecen inline:
# no son decisiones de negocio, son estabilidad de punto flotante.

MIN_PEERS: int = 5   # mínimo de fondos en la categoría para percentiles (momentum, alerts)

MACRO_OLS_MIN_OBS: int = 60                    # mínimo meses solapados NAV+macro para el OLS
MACRO_OLS_PER_FUND_MIN_COVERAGE: float = 0.85  # cobertura mínima por-fondo de cada factor
MACRO_VIF_THRESHOLD: float = 10.0              # eliminar factores con VIF > umbral
# FND-0176: a factor with a run of >= this many consecutive exact zeros inside a fund's estimation window is
# degenerate (a stale/zero-filled series, e.g. m2_global_yoy 2021-07..2026-02) and is dropped from that OLS.
# Exempt: MACRO_ZERO_RUN_EXEMPT_PREFIXES (rate-change factors are legitimately flat: d_rate_eu has a 41-month zero run).
MACRO_FACTOR_MAX_ZERO_RUN: int = 12
MACRO_ZERO_RUN_EXEMPT_PREFIXES: tuple = ("d_rate_",)
# FND-0208: numerical-stability guards around the macro OLS (shared/dq_guards.py).
# Pre-OLS: Belsley-Kuh-Welsch condition index of the unit-scaled design (intercept included); while it exceeds
# this level the most collinear factor is dropped for that fund (even a VIF force-keep one). 30 = BKW "severe".
MACRO_OLS_MAX_CONDITION_INDEX: float = 30.0
# Post-OLS circuit breaker: a beta with |beta| above this (or non-finite) is written as NULL, not as a value.
# Also the plausibility limit counted by scripts/audit/beta_shift_audit.py (FND-0176 saw 600-1,100).
MACRO_BETA_PLAUSIBLE_MAX: float = 5.0
# FND-0234: scales of three derived P2 metrics, named once so the producer and the audit's plausibility bound cannot drift.
# energy_sensitivity_pct = beta_oil * ENERGY_SCENARIO_SHOCK (a +25% WTI scenario); hy_spread_sensitivity_pct = beta_spread_hy *
# HY_SPREAD_SCENARIO_SHOCK (+300 bp); both are SIGNED return impacts, bounded by MACRO_BETA_PLAUSIBLE_MAX x the shock (the beta
# circuit breaker nulls anything larger). fx_contribution_pct = fx_ann / total_ann clamped to +-FX_CONTRIBUTION_PCT_CLAMP.
ENERGY_SCENARIO_SHOCK: float = 0.25
HY_SPREAD_SCENARIO_SHOCK: float = 3.0
FX_CONTRIBUTION_PCT_CLAMP: float = 5.0

# ---- P2 recompute bundle (FND-0196, FND-0226, FND-0200, FND-0202), ACTIVE since 2026-10-04 ------------------------------
# Four corrections that CHANGE stored metric values, switched on TOGETHER for ONE full P2 recompute (CALC_VERSION 20261004).
# Until that recompute has run, the stored metrics are still the old ones. The input fingerprint includes the enabled flags
# (utils/fingerprint.effective_calc_version), so the first run recomputes every fund by itself. ROLLBACK: set the flags back
# to False and bump CALC_VERSION again (another full run); the legacy code paths stay in place for that.
#   MACRO_VIF_ITERATIVE_ENABLED  FND-0196  macro OLS: iterative VIF (drop one factor at a time) with the options below,
#                                          instead of dropping every factor over the threshold in one pass (which removed
#                                          both members of a collinear pair, e.g. spread_hy / spread_ig, corr 0.91)
#   MACRO_FACTOR_CLEAN_ENABLED   FND-0226  macro factors: a non-positive CPI index level is missing (series_macro CN has
#                                          14 zeros) and +/-inf cells become NaN
#   PERSISTENCE_FIRST_LAST_NAV_ENABLED  FND-0200  peer return in a window from the FIRST and LAST NAV (was MIN / MAX)
#   CAPTURE_MONTH_END_ENABLED    FND-0202  capture ratios: fund and peers on a month-end grid, 1-month returns only
MACRO_VIF_ITERATIVE_ENABLED: bool = True
MACRO_FACTOR_CLEAN_ENABLED: bool = True
PERSISTENCE_FIRST_LAST_NAV_ENABLED: bool = True
CAPTURE_MONTH_END_ENABLED: bool = True
# FND-0255: PERFORMANCE ONLY, results are equivalent (capture bit-identical; persistence's category mean in a different summation order). One NAV panel per nature per
# run instead of a database read + EUR conversion per fund (capture) and per rolling window per fund (persistence). Deliberately NOT in P2_BUNDLE_FLAGS: it must
# not change any fingerprint, so enabling it recomputes nothing. OFF = the legacy per-fund queries.
PEER_PANEL_CACHE_ENABLED: bool = True
P2_BUNDLE_FLAGS: tuple = ("MACRO_VIF_ITERATIVE_ENABLED", "MACRO_FACTOR_CLEAN_ENABLED",
                          "PERSISTENCE_FIRST_LAST_NAV_ENABLED", "CAPTURE_MONTH_END_ENABLED",
                          "ANNUALIZATION_INTERVAL_ENABLED", "DEFLATION_MONTH_ALIGN_ENABLED",
                          "SORTINO_MIN_DOWNSIDE_COUNT_ENABLED", "FX_CONTRIBUTION_EUR_VIEW_ENABLED",
                          "EUR_NAV_CONVERSION_ENABLED", "ANNUALIZATION_BY_DATES_ENABLED",
                          "NAV_MONTH_END_ALIGN_ENABLED")
# FND-0240: annualise a NAV window over its INTERVALS (N points span N - 1 periods) instead of its points. OFF = the stored
# convention (years = N / 12), bit-for-bit. ON changes the stored return_ann / sharpe / sortino of the risk family and the
# rolling timeseries: about +0.5 pp on rolling_1y, -1.2 pp on the crisis windows, +0.04 pp since inception -- and nothing in the
# P3 portfolio (Spearman 1.0000 of the scores, same top 10, measured 2026-10-06). The audit mirrors the convention through
# shared/annualization.py. FLIP ONLY TOGETHER WITH THE RECOMPUTE of the families it touches (utils/family_versions.FLAG_FAMILIES:
# risk, rolling): until then the stored values keep the old convention and the audit's nominal/Fisher identities report the gap.
ANNUALIZATION_INTERVAL_ENABLED: bool = True
# FND-0253: annualise by DATES, not by rows. A NAV series with missing months was treated as consecutive: horizon understated, return_ann
# inflated (LU1663960000: 35 rows over 104 months, stored 20.9% vs 6.5% by dates), vol mixing multi-month returns as monthly. ON = the horizon is
# the dense-equivalent number of points of the first..last calendar month (through the SAME years_spanned, so every existing convention is
# kept), k-month returns enter the vol as k-month returns, one-month statistics use one-month returns only. A DENSE series is bit-identical;
# only the funds with an internal gap move (5 of 2,933 active funds on 2026-10-11). OFF = the stored convention. The audit and the PIT mirror
# it through shared/annualization.py. ON since 2026-10-11 (owner decision; it shipped OFF in 7a679ca / e73794b), flipped TOGETHER WITH the
# CALC_VERSION bump (20261011) and the full P2 recompute of the families it touches (utils/family_versions.FLAG_FAMILIES: risk, rolling,
# regime, persistence). Measured before the flip on the production code over 2,950 active funds: 2,871 bit-identical, 5 moved (the funds
# with an internal NAV gap). Rolling back means setting it to False AND recomputing again: the stored values follow the convention they
# were computed under, and the audit's identities would report the gap until then.
ANNUALIZATION_BY_DATES_ENABLED: bool = True
# FND-0300: a monthly NAV dated within 5 days of its calendar month-end (28% of fund_nav_monthly sits on the last BUSINESS day) is labelled
# with the month-end by every reader that feeds a calculation (shared/nav_month_end.py), and persistence windows count whole calendar months.
# Fixes the macro OLS join (~30% of months dropped), stamp-dependent persistence windows and PIT stale ages; the FND-0193 alignment gate
# failed on it (|dSharpe| 0.0126 > 0.01). OFF = the stored raw-date convention, bit-for-bit. ON since 2026-10-19 (owner decision), flipped
# TOGETHER WITH the family epoch 20261019 (utils/family_versions.FAMILY_CALC_OVERRIDES) and the recompute of the families it touches
# (FLAG_FAMILIES: risk, macro, persistence, rolling); the rolling rows dated on the old business-day stamps are removed by the run itself
# (metrics_writer.prune_timeseries, FND-0301). Rolling back means False + another family epoch + recompute.
NAV_MONTH_END_ALIGN_ENABLED: bool = True
# FND-0241: look the CPI up with each MONTHLY NAV's own calendar month-end. OFF = the stored convention (merge_asof backward against
# a CPI stamped on the calendar month-end), under which the 29% of NAVs dated on the last BUSINESS day took the previous month's CPI
# and had real monthly return == nominal. ON changes the stored real series: worst_month real up to ~2 pp (18% of funds > 0.1 pp),
# real vol_ann up to ~0.5 pp, return_ann real up to ~0.3 pp (400-fund sample, 2026-10-07). The audit mirrors it through
# shared/deflation_alignment.py. FLIP ONLY TOGETHER WITH THE RECOMPUTE of the families it touches (utils/family_versions.FLAG_FAMILIES:
# risk, rolling; short_horizon is daily and never aligned): until then the stored values keep the old lookup.
DEFLATION_MONTH_ALIGN_ENABLED: bool = True
# FND-0242: Sortino is NaN when its downside deviation rests on fewer than max(SORTINO_MIN_DOWNSIDE_OBS, ceil(SORTINO_MIN_DOWNSIDE_SHARE * n))
# below-MAR periods (relative SE of the deviation ~ 0.7 / sqrt(k): 35% at k = 4). Replaces nothing: the FND-0075 magnitude floor stays as a
# second guard. Scalar (sortino_ratio) and rolling (_roll_sortino) paths only; the regime path (sortino_ratio_from_returns) is untouched.
# Measured 2026-10-07: removes 0.7% of the stored Sortino values (2.2% of rolling_1y, 11% of Monetario) and the removed ones have median |Sortino|
# 4.3 vs 0.8. FLIP ONLY TOGETHER WITH THE RECOMPUTE of the families it touches (utils/family_versions.FLAG_FAMILIES: risk, rolling).
SORTINO_MIN_DOWNSIDE_COUNT_ENABLED: bool = True
SORTINO_MIN_DOWNSIDE_OBS: int = 3
SORTINO_MIN_DOWNSIDE_SHARE: float = 0.15
# FND-0235: the FX contribution seen by a EUR investor, as a signed annual pp figure. OFF = the stored metric (fx = mean of the log change
# of foreign-units-per-EUR, i.e. the OPPOSITE sign of a EUR investor's contribution, divided into the fund's own-currency return: for the
# 579 of 711 funds in non-EUR share classes the NAV already is in the foreign currency, so the ratio mixes two currency bases; and an
# unstable ratio clamped to +-5). ON: fx_contribution_ann = -(foreign-per-EUR log change) of the ASSET currency (Asset_Currency = EUR means no
# exposure whatever the class currency), the total return is converted to EUR for non-EUR classes, fx_contribution_pct is the ratio to that
# EUR total and is NOT written when |EUR total| < FX_RATIO_MIN_TOTAL_ANN (no fake 0.0), and the P3 scorer applies MULT_FX_MALUS when
# |fx_contribution_ann| > FX_CONTRIBUTION_PP_LIMIT instead of |fx_contribution_pct| > 0.60 (it also stops reading the ratio).
# FX_CONTRIBUTION_PP_LIMIT = 2 pp/yr: ~a third of the 6-7% IPC+M3 target; on 630 exposed funds it flags 24 (the ratio rule flags 38, 10 in common).
# FLIP ONLY TOGETHER WITH THE RECOMPUTE of the 'fx' family (utils/family_versions.FLAG_FAMILIES) and BEFORE the next P3 run.
FX_CONTRIBUTION_EUR_VIEW_ENABLED: bool = True
FX_CONTRIBUTION_PP_LIMIT: float = 0.02
FX_RATIO_MIN_TOTAL_ANN: float = 0.01
# FND-0243: the platform's investor is EUR-domiciled, so every share class is scored on a EUR NAV. ON = readers convert a non-EUR class's
# NAV to EUR with the ECB reference rate (units of currency per EUR) of the SAME date convention as FND-0241: the end-of-period monthly rate
# for monthly NAV, the same business day's rate for daily NAV. MUST equal FX_CONTRIBUTION_EUR_VIEW_ENABLED (owner directive 2026-10-07:
# neither may be enabled alone; P2/P3 refuse to start otherwise), because with a EUR NAV the class-conversion branch of the FX view would
# convert a second time. Rates: series_macro, loaded by macro_discovery --source bce for every currency in EUR_FX_CURRENCIES.
# A P2 bundle flag on ALL metric families: turning it on must recompute EVERY fund, not only the non-EUR ones whose NAV
# fingerprint moves -- the peer-based metrics (capture, persistence) of a EUR fund change too, because its peers are now in EUR.
EUR_NAV_CONVERSION_ENABLED: bool = True
EUR_FX_CURRENCIES: tuple = ("USD", "GBP", "JPY", "CHF")
EUR_FX_MONTHLY_INDICATOR: str = "fx_eom_eur"       # EXR.M.<CCY>.EUR.SP00.E, stamped on the calendar month-end
EUR_FX_DAILY_INDICATOR: str = "fx_d_eur"           # EXR.D.<CCY>.EUR.SP00.A
# FND-0236: per-metric-family calculation versions. OFF = today's behaviour (one global CALC_VERSION and one input hash per
# fund). ON = each of the nine families has its own state row (control.fund_metric_family_state), so a change that touches one
# family (bump FAMILY_CALC_OVERRIDES[family] in utils/family_versions.py) recomputes only that family. Switch-on order:
# owner applies scripts/ops/migrate_fund_metric_family_state.py --apply, THEN this flag (P2 asserts the table at startup);
# the first run adopts every fund from its legacy hash without recomputing. ROLLBACK: set False -- the next plain run
# recomputes every fund once (the legacy hash column then holds a composite hash), no schema change needed.
FAMILY_VERSIONING_ENABLED: bool = True
# Two-tier overlap policy of the automatic incident capture (shared/backlog_client.report_incident; NORMAS_BACKLOG.md §7,
# owner safeguards S-2 / D-1). OFF = today's behaviour: only the exact (project_code, object_name, 24 h) dedup. ON = before a new
# ticket is opened the incident is compared with the OPEN tickets: HIGH confidence (names an open ticket, or the same object_name
# with text similarity >= BACKLOG_INCIDENT_HIGH_COSINE) is appended to that ticket with a log note saying why; MEDIUM
# (similarity >= BACKLOG_INCIDENT_MEDIUM_COSINE, or the same object_name with a weaker match) opens the new ticket and only
# SUGGESTS the related one. Switch-on gate (D-1): `scripts/audit/backlog_links_calibration.py --gate` reports ZERO false merges
# on the historical replay and the synthetic fixture (tests/fixtures/backlog_incident_cases.json); attach its report to the ticket.
BACKLOG_INCIDENT_AUTO_APPEND_ENABLED: bool = False
# FND-0246: the family-of-share-classes key. OFF = today's behaviour: (management company, _normalize_name(fund_name)), a ~30-character catalogue string
# whose abbreviations differ between classes of the SAME fund (451 pairs measured by FND-0244), so one fund splits into several families and P3 can put two
# classes of it in one portfolio. ON = (management company, official sub-fund name read from the KIID head, fund_master.official_fund_name); a class whose
# head carries no name (~15% of the active funds) joins the official group of its own catalogue stem ONLY when that stem maps to exactly one official group
# of the same manager, otherwise it keeps its catalogue key -- a missing extraction never splits or merges a family by itself. The key keeps currency words
# (Pictet Short-Term Money Market USD / CHF / JPY are different sub-funds). Switch-on order (owner): migration db/migrations/20261024_fund_master_official_fund_name.sql
# applied and a P1 pass done (the column is filled), THEN scripts/audit/family_key_switch_diff.py read (merges, nature conflicts, P3 de-duplication and
# PRODUCTION effect), THEN this flag, THEN P1_discoverAllFunds.bat (builder + --family-nature-refresh), verify_family_refresh.py exit 0, a P3 DRAFT.
# Rollback = set it back to False and rebuild the families (no DDL). Family ids are re-issued on every builder run either way.
# ON since 2026-10-25 (owner decision), after: (1) the paired point-in-time backtest, key OFF vs ON on the full universe (260 months, same cached scores): Sharpe
# 0.5985 -> 0.6023, max drawdown -15.39% -> -14.93%, turnover +0.7 pp, i.e. neutral out of sample; (2) the read-only replay of the family arbitration under the new
# key: 11 inconsistent families, 5 of them unresolved -> after FND-0310 (the word "short" is not a structural signal), FND-0311 (English "does not have any capital
# guarantee" no longer votes Estructurado; non-adjacent natures of a same-official-name family settled by a >= 0.80 reference class) 7 inconsistent, 7 settled, 0 left;
# (3) scripts/audit/family_key_switch_diff.py read (out/audit/P1/family_key_switch_diff_20261025_001153.*: families 2,880 -> 2,100, 410 merges, 26 splits).
OFFICIAL_NAME_FAMILY_KEY_ENABLED: bool = True
BACKLOG_INCIDENT_HIGH_COSINE: float = 0.70
BACKLOG_INCIDENT_MEDIUM_COSINE: float = 0.45
# Backlog v2 Phase C (FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.1 / 6.4.8): a commit whose `Refs: FND-nnnn` trailer cites a ticket in OPEN or TODO moves it
# to IN_PROGRESS (actor system:sync, the commit as evidence) when scripts/audit/backlog_sync.py --apply records that commit for the first time. Never
# from BLOCKED or DEFERRED, never backward, never from a mention in the subject; closures stay manual. Owner decision 2026-10-26: forward moves are
# automatic. OFF = the sync still records the commit events and the violations, and moves nothing.
BACKLOG_AUTO_FORWARD_ENABLED: bool = True
# Backlog v3 W2 (FND-0357; owner decision 2026-10-31, doc/especificacion/ACTION_PLAN_BACKLOG_V3.md): an agent:<model> identity opens tickets, moves them (never forcing, reopening or making a
# delivery closure), records links and notes through its OWN database login (role backlog_agent, DSN FONDOS_BACKLOG_PG_DSN_AGENT in the git-ignored .env) with no terminal. The DSN is the real
# gate: without it, or with this switch OFF, every agent write takes the console path exactly as before (a person types the confirmation phrase). Rollback = set it back to False.
BACKLOG_AGENT_WRITES_ENABLED: bool = True
# Backlog v3 W3 (FND-0358; doc/especificacion/ACTION_PLAN_BACKLOG_V3.md): the CEILING of the autopilot (scripts/audit/backlog_autopilot.py). "OFF" = it only prints what it would do; "SHADOW" = it
# records the MERGE facts and writes ONE note per decision ("would move X to Y, evidence ...") and moves nothing; "ON" = it also makes the two evidence-backed moves through its own database login
# (IN_PROGRESS -> READY_FOR_DEPLOY on a merge that carries `Closes:`, READY_FOR_DEPLOY -> CLOSED as CODE_FIX / DATA_FIX when every condition holds; v3.1 adds REOPEN, UNBLOCK and UNDEFER). A run can
# only LOWER this with --mode. Backlog v3.1 (FND-0355): the ceiling is "ON" and the REAL switch is the database's own mode event (backlog.fn_autopilot_mode(), default SHADOW, set by sp_set_autopilot_mode):
# the effective mode is the lowest of the two, and when the database mode cannot be read (the `events` DDL is not applied) the run is capped at SHADOW. Nothing is written without the login
# backlog_autopilot (FONDOS_BACKLOG_PG_DSN_AUTOPILOT). Rollback = set it back to "SHADOW".
BACKLOG_AUTOPILOT_MODE: str = "ON"
# Backlog v2 Phase C slice 2 (FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.1 / 6.4.6): the instant from which scripts/audit/backlog_sync.py judges a commit that cites a ticket
# by its `Backlog-Gate:` trailer (an ISO timestamp WITH offset, e.g. "2026-11-02T09:00:00+01:00"). "" = the gate is NOT ARMED: the sync reports the trailers it sees and
# records no bypass, raises no hold. The owner arms it once the commit-msg hook is installed (bash scripts/audit/commit-msg.sh) and hook_bypass exists (migrate --phase hook);
# an earlier commit is never judged (history has no trailers). To disarm, set it back to "": the rows already recorded stay (append-only).
BACKLOG_HOOK_EPOCH: str = ""
# Strict traceability of the commit-msg hook (owner decision 2026-10-31: built, OFF for now). ON = the hook also REJECTS a commit whose subject and `Refs:` trailer cite no
# `FND-nnnn`, except a merge, a revert, a `chore` commit (`chore:` / `chore(x):`) and any commit on a `chore/` branch (NORMAS_BACKLOG.md 4.5). OFF = such a commit goes through
# (only a malformed token is rejected). Rollback = set it back to False.
BACKLOG_REQUIRE_TICKET_ENABLED: bool = False
# Backlog v2 Phase C slice 3 (FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.2 to 6.4.5): the CEILING of the collision gate: "OFF" (not evaluated: the default until the owner arms it), "AUDIT" (the commit-msg
# hook records the pairs of commits that touch a code file cited by another ticket in progress, warns, blocks nothing) or "BLOCK" (also a hold on the ticket, and the pre-push hook refuses to publish
# while a pair of the pushed commits is undecided). The EFFECTIVE mode is the lower of this and the latest backlog.gate_state event, so a retirement by the calibrator (an appended event) lowers it
# without editing this file; raising it again is a person's event with a reason. BLOCK needs 14 days of AUDIT and a share of commits that introduce a pair of 10 % or less (checked by the database).
# AUDIT since 2026-10-31 (owner decision): the DDL `--phase collision` is applied, the role backlog_adjudicator exists, and the owner wrote the gate_state event AUDIT (event 1, human:jacor,
# 2026-10-31 12:42) with gate_mode.py. The 14-day audit counts from that event. Rollback = set it back to "OFF" (the hook then does not even ask the database); the effective mode also drops to
# OFF if the calibrator retires the gate (an appended event, no edit of this file).
BACKLOG_COLLISION_GATE_MODE: str = "AUDIT"
# Backlog v2 Phase C slice 4 (FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.6): where scripts/audit/backlog_sync.py reads whether `backlog-gate` is a REQUIRED status check of the branch (GitHub API). The
# token is NOT a setting: it is the environment variable BACKLOG_GATE_GITHUB_TOKEN (a fine-grained, read-only token held outside the repository and .env, typed in the owner's terminal).
BACKLOG_GATE_GITHUB_REPO: str = "jacormenzana/fondos"
BACKLOG_GATE_GITHUB_BRANCH: str = "master"
# Options of the iterative VIF (used when MACRO_VIF_ITERATIVE_ENABLED, and by the PIT variant "iterative_hy"): keep the two
# crisis factors, drop spread_ig (the near-duplicate of spread_hy), and at most n_obs / 10 factors per fund.
MACRO_ITERATIVE_VIF: dict = {
    "extra_priority": ("spread_hy", "vix_yoy"),
    "exclude": ("spread_ig",),
    "max_factors_per_obs": 10.0,
}

# Factores a proteger del filtro VIF según geografía del fondo — se suman al
# conjunto base {d_rate_eu, oil_yoy, m3_yoy} para evitar que factores
# regionales clave sean descartados por correlación con factores globales.
MACRO_GEO_FORCE_KEEP: dict = {
    "China":          {"ipc_yoy_cn", "d_rate_cn", "eur_cny_yoy"},
    "Japan":          {"ipc_yoy_jp", "d_rate_jp", "eur_jpy_yoy"},
    "North America":  {"ipc_yoy_us", "d_rate_us", "term_spread"},
    "Asia-Pacific":   {"ipc_yoy_jp", "ipc_yoy_cn", "d_rate_jp", "d_rate_cn"},
    "India":          {"ipc_yoy_us", "dxy_yoy"},
    "Latin America":  {"ipc_yoy_us", "d_rate_us", "dxy_yoy"},
    "Eastern Europe": {"ipc_yoy_eu", "d_rate_eu"},
    "Europe":         {"ipc_yoy_eu", "d_rate_eu", "eur_gbp_yoy"},
    "Middle East & Africa": {"oil_yoy", "dxy_yoy"},
}

# Factores a proteger según estado de desarrollo (se acumulan con los de geografía)
MACRO_DEV_STATUS_FORCE_KEEP: dict = {
    "Emerging": {"spread_hy", "dxy_yoy"},
    "Frontier": {"spread_hy", "dxy_yoy"},
}

PERSISTENCE_WINDOW_MONTHS: int = 36  # duración ventana rolling (3 años)
PERSISTENCE_STEP_MONTHS: int = 6     # paso entre ventanas (semestral)
PERSISTENCE_MIN_WINDOWS: int = 4     # mínimo de ventanas para calcular la métrica

CAPTURE_MIN_PERIODS: int = 12  # mínimo de periodos positivos/negativos vs benchmark

REGIME_MIN_NAV_TOTAL: int = 36            # mínimo meses totales de NAV para stats por régimen
REGIME_MIN_OBS: int = 12                  # mínimo meses en un régimen para calcular estadísticas
REGIME_MIN_OBS_SORTINO_DOWNSIDE: int = 2  # mínimo retornos negativos para downside deviation

# ============================================================
# v26 — Reglas del motor WARN/ALARM (fund_metric_alerts)
# ============================================================
# Fuente única de verdad para los umbrales del alarm engine.
# Cada regla: (metric, window, ref_type, warn_pctile, alarm_pctile, direction)
#   direction: 'above' → alertar si el valor > umbral (volatilidad, drawdown abs)
#              'below' → alertar si el valor < umbral (retorno, Sharpe)
# Los percentiles se calculan sobre el peer group (Fund_Nature) en la fecha
# más reciente disponible en fund_metric_timeseries.
# Fail-open: si ref_type='category' y la categoría tiene < 5 fondos con datos,
#            no se emite alerta (no hay base estadística suficiente).
# FIX-ALERT-D3 (2026-09-13, AUDITORIA_ESTADISTICA.md §2.5): VOL_CAT_P90/P97
# targeted window='rolling_6m', which has 0 rows in fund_metric_timeseries —
# the v29 curated hybrid model only tracks rolling_1y/2y/3y/5y/10y there
# (ROLLING_WINDOWS); rolling_1m/3m/6m are short-horizon (SHORT_WINDOWS,
# metric_version='d1') and live in fund_metrics.horizon, a different table
# and dimension that compute_category_snapshot()/compute_alerts() never
# read. Both rules silently never fired. Retargeted to rolling_1y, the
# shortest window this alert engine can actually see — a proper rolling_6m
# peer-alert would need a separate feature building an analogous
# category-percentile snapshot over fund_metrics' d1 rows; out of scope here.
ALERT_RULES: list[dict] = [
    # Volatilidad vs categoría: WARN si > p90, ALARM si > p97
    {
        "rule_code":   "VOL_CAT_P90",
        "metric":      "vol_ann",
        "window":      "rolling_1y",
        "ref_type":    "category",
        "level":       "WARN",
        "direction":   "above",
        "threshold_pctile": 0.90,
    },
    {
        "rule_code":   "VOL_CAT_P97",
        "metric":      "vol_ann",
        "window":      "rolling_1y",
        "ref_type":    "category",
        "level":       "ALARM",
        "direction":   "above",
        "threshold_pctile": 0.97,
    },
    # Drawdown vs categoría (valores son ≤ 0; "peor" = más negativo = below p10)
    {
        "rule_code":   "DD_CAT_P10",
        "metric":      "max_dd",
        "window":      "rolling_1y",
        "ref_type":    "category",
        "level":       "WARN",
        "direction":   "below",
        "threshold_pctile": 0.10,
    },
    {
        "rule_code":   "DD_CAT_P03",
        "metric":      "max_dd",
        "window":      "rolling_1y",
        "ref_type":    "category",
        "level":       "ALARM",
        "direction":   "below",
        "threshold_pctile": 0.03,
    },
    # Retorno vs categoría: WARN si < p10 en 3y, ALARM si < p05
    {
        "rule_code":   "RET_CAT_P10",
        "metric":      "return_ann",
        "window":      "rolling_3y",
        "ref_type":    "category",
        "level":       "WARN",
        "direction":   "below",
        "threshold_pctile": 0.10,
    },
    {
        "rule_code":   "RET_CAT_P05",
        "metric":      "return_ann",
        "window":      "rolling_3y",
        "ref_type":    "category",
        "level":       "ALARM",
        "direction":   "below",
        "threshold_pctile": 0.05,
    },
]

# Valores permitidos para fund_metric_alerts.level
METRIC_ALERT_LEVELS: list[str] = ["OK", "WARN", "ALARM"]

# Métricas para las que se mantiene serie temporal completa
# (Hybrid model: fund_metric_timeseries). El resto sólo van a fund_metrics.
# 5 series curadas: vol_ann, max_dd, return_ann, sharpe, sortino (v29).
# metric = pure indicator name; window column carries the rolling horizon.
ROLLING_TIMESERIES_METRICS: list[str] = [
    "vol_ann",
    "max_dd",
    "return_ann",
    "sharpe",
    "sortino",
]

# Ventanas que generan serie temporal (meses de ROLLING_WINDOWS + días SHORT_WINDOWS)
# Se derivan automáticamente; no duplicar aquí — usar ROLLING_WINDOWS + SHORT_WINDOWS.

# ============================================================
# Parámetros globales P3 — selección y cartera
# ============================================================

# Objetivo de la cartera (FND-0303, decision del propietario 2026-10-21): rentabilidad NOMINAL anual >= IPC + M3 + este diferencial,
# a nivel de CARTERA. Es una aspiracion que se informa (informe mensual, backtest PIT), NO una restriccion de seleccion: en
# 2005-2026 el modelo rindio 3.44% frente a 6.94% de IPC+M3. Definicion unica en shared/objective.py.
PORTFOLIO_TARGET_SPREAD: float = 0.02

# Versión de scoring activa
SCORE_VERSION: str = "v1"

# ============================================================
# Vocabularios de dominio — listas cerradas (v17)
# ============================================================
# Fuente única de verdad para valores permitidos en fund_master.
# Usar en validaciones de pipeline (P1), filtros de scoring (P3)
# y controles de calidad de dato.
#
# Convención: frozenset para O(1) lookup en validaciones,
#             list para mantener orden en exports/reports.

DOMAIN_VALUES: dict[str, list[str]] = {
    # ============================================================
    # v20 (INTEGRATED_SPEC_v20_v3 §2A.1) — design-canonical value sets.
    # config.DOMAIN_VALUES es la CAPA DE INTENCIÓN DE DISEÑO (fuente única).
    # La BD viva es "drift"; el reprocess realinea BD → este catálogo.
    # Idioma: ES en Fund_Nature/Profile/Strategy; EN en el resto (§8).
    # Casing canónico embebido aquí (ver ATTRIBUTE_CASING para la regla).
    # ============================================================

    # ── Clasificación (ES) ───────────────────────────────────────
    "Fund_Nature": [
        "Renta Variable", "Mixtos", "Renta Fija Flexible",
        "Renta Fija Corto Plazo", "Monetario", "Alternativo",
        "Restantes", "Estructurado",
    ],
    "Family": [                        # AUDIT v20: gobernada (antes sin dominio)
        "Equity Core", "Thematic Equity", "Short-Term Fixed Income",
        "Flexible Fixed Income", "High Yield", "Emerging Market Debt",
        "Inflation-Linked", "Multi-Asset", "Income Oriented",
        "Strategic Allocation", "Absolute Return", "Money Market",
        "Real Assets", "Structured", "Target Date",
    ],
    "Profile": [                       # MODIFY #2: +Agresivo (SRRI 7)
        "Conservador", "Moderado", "Dinámico", "Agresivo",
    ],
    "Strategy": [                      # MODIFY #8: eje activo/pasivo; drop ETF
        "Activo", "Indexado", "Pasivo",
    ],

    # ── Estructura del vehículo (EN) ─────────────────────────────
    "Vehicle_Structure": [             # MODIFY #1: ex-Type, repropuesta
        "Open-End UCITS", "ETF", "Fund of Funds",
        "Money Market Fund", "Structured Product",
    ],

    # ── Exposición geográfica / universo (EN) ────────────────────
    "Geography": [                     # MODIFY #4: EN, puramente espacial
        "Global", "Europe", "North America", "Asia-Pacific", "Japan",
        "China", "India", "Latin America", "Eastern Europe",
        "Middle East & Africa",
    ],
    "Development_Status": [            # CREATE: tier de desarrollo (split de Geography)
        "Developed", "Emerging", "Frontier", "Global/Mixed",
    ],
    "Investment_Universe": [           # MODIFY #5: drop 'Liquidity'
        "Global", "Regional", "Country",
    ],
    "Investment_Focus": [
        "Broad", "Sector", "Thematic",
    ],
    "Theme": [                         # KEEP (EN-homogeneizado, observado V6 §2.1)
        "Core/General", "Technology", "Artificial Intelligence", "Robotics",
        "Digital", "Cybersecurity", "Climate / Clean Energy", "Energy",
        "Healthcare", "Biotechnology", "Water", "Gold", "Mining",
        "Real Estate", "Financials", "Consumer Brands", "Silver Economy",
        "Megatrends", "Insurance", "Inflation",
    ],
    "Sector_Focus": [                  # MODIFY #6: EN; iff Investment_Focus='Sector'
        "Technology & Innovation", "Healthcare & Life Sciences",
        "Energy & Resources", "Financial Services", "Consumer",
        "Materials & Mining", "Utilities & Environment", "Real Assets",
    ],
    "Market_Cap_Focus": [              # MODIFY #7
        "Large Cap", "Mid Cap", "Small Cap", "SMID Cap", "All Cap",
        "Not Applicable",
    ],
    "Credit_Quality": [                # KEEP (umbral IG/HY→Mixed: §8-bis Q4, pendiente)
        "Investment Grade", "High Yield", "Mixed", "Not Applicable",
    ],

    # ── Estilo / estrategia de cartera (EN) ──────────────────────
    "Style_Profile": [                 # MODIFY #10
        "Value", "Growth", "Blend", "Income", "Quality", "Momentum",
        "Low Volatility", "Strategic Allocation", "Not Applicable",
    ],
    "Exposure_Bias": [                 # MODIFY #11: única dimensión direccional
        "Long Only", "Long/Short", "Market Neutral", "Net Short",
        "Not Applicable",
    ],
    "Duration_Profile": [              # CREATE: banda de duración FI (split Exposure_Bias)
        "Ultra-Short", "Short", "Intermediate", "Long", "Flexible",
        "Not Applicable",
    ],

    # ── Estructura / política (EN) ───────────────────────────────
    "Hedging_Policy": [                # MODIFY #14: absorbe Currency_Hedged
        "Hedged", "Unhedged", "Partially Hedged",
    ],
    "Replication_Method": [            # MODIFY #9: técnica; activos → Not Applicable
        "Physical", "Synthetic", "Sampling", "Not Applicable",
    ],
    "Derivatives_Usage": [             # MODIFY #12: PROPÓSITO (no YES/NO/LIMITED)
        "None", "Hedging Only", "Investment", "Both",
    ],
    "Leverage_Used": [                 # MODIFY #13: 3-estado
        "No", "Limited", "Yes",
    ],
    "Accumulation_Policy": [
        "Accumulation", "Distribution",
    ],
    "Distribution_Frequency": [        # EN
        "Monthly", "Quarterly", "Semi-Annual", "Annual",
    ],
    "Liquidity_Profile": [             # §8-bis Q1: RESTAURADA como dealing-frequency.
        # PROPUESTO (EN) — confirmar miembros antes del reprocess (§D no-fabricación).
        "Daily", "Weekly", "Bi-Weekly", "Monthly", "Not Applicable",
    ],

    # ── Estructuras especializadas (CREATE, EN) ──────────────────
    "MMF_Structure": [                 # split de Subtype (clase regulatoria MMF)
        "CNAV", "LVNAV", "VNAV", "Standard MMF", "Not Applicable",
    ],
    "Alt_Strategy": [                  # split de Subtype (estrategia alternativa)
        "Long/Short", "Market Neutral", "Global Macro",
        "Relative Value/Arbitrage", "Opportunistic", "Volatility Target",
        "Not Applicable",
    ],
    "Payoff_Profile": [                # split de Subtype (payoff estructurado)
        "Autocallable", "Capital Protected", "Fixed Coupon Band",
        "Not Applicable",
    ],

    # ── Divisa (CODE, ISO) ───────────────────────────────────────
    "Fund_Currency": ["EUR", "USD", "GBP", "CHF", "JPY", "CNH"],
    # Asset_Currency admite además el centinela categórico "MCY" (multi-divisa
    # por naturaleza, BL-ASSET-CCY-MULTI 2026-07-11): distingue "indeterminado
    # por diseño" de NULL="no descubierto". "MCY" no es un código ISO-4217
    # asignado (sin colisión). Nota: las columnas CODE se excluyen del chequeo
    # allowed-values (ver ALLOWED_VALUES_BY_COLUMN en classify_utils), así que
    # esta lista documenta el contrato de intención, no una validación dura.
    "Asset_Currency": ["EUR", "USD", "GBP", "CHF", "JPY", "CNH", "MCY"],

    # ── Flags de control / provenance (UPPER_SNAKE) ──────────────
    "Benchmark_Type": [                # §3-bis: flag de control
        "REFERENCE_INDEX", "TARGET_INDEX", "NO_BENCHMARK",
    ],
    "Fee_Known_Flag": ["EXTRACTED", "ZERO_CONFIRMED", "NOT_FOUND",
                       "ENTRY_CONDITIONAL", "EXIT_INFERRED_ZERO"],
    "Sfdr_Article": ["6", "8", "9"],
    "KID_Format": ["UCITS_KIID", "PRIIPS_KID", "UNKNOWN"],
    "Cost_Extraction_Quality": [
        "HIGH", "MEDIUM_CROSS", "MEDIUM_EUR", "MEDIUM_PCT", "LOW", "NONE",
    ],
    "SRRI_Quality_Flag": [
        "HIGH", "MEDIUM_VISUAL", "MEDIUM_TEXT", "LOW_CONFLICT", "NONE",
    ],
    "Data_Quality_Flag": ["OK", "WARN", "MISSING"],
    "SRRI_Validation_Status": [
        "MATCH", "TEXT_ONLY", "VISUAL_ONLY", "CONFLICT", "NOT_AVAILABLE",
    ],
    "Cost_Mgmt_Arbitration": [
        "AGREE", "OCR_RECOVERED", "BOTH_FAIL",
        "ONLY_BANDS_X", "ONLY_RULED", "CONFLICT",
    ],
    "Cost_Oper_Arbitration": [
        "AGREE", "OCR_RECOVERED", "BOTH_FAIL",
        "ONLY_BANDS_X", "ONLY_RULED", "CONFLICT",
    ],
}

# ============================================================
# Casing catalog (§3-bis) — regla de casing por atributo (DATO).
# La FUNCIÓN normalizadora vive en classify_utils (R-1); aquí solo el dato.
#   TITLE        → valores de característica de fondo (taxonomía/política/estructura)
#   UPPER_SNAKE  → flags internos de control/provenance/calidad/formato
#   CODE         → ISO/códigos (mayúscula)
#   NUM          → numéricos (sin casing)
# ============================================================
ATTRIBUTE_CASING: dict[str, str] = {
    # TITLE — fund-characteristic (idioma ortogonal: ES o EN)
    "Fund_Nature": "TITLE", "Profile": "TITLE", "Strategy": "TITLE",
    "Family": "TITLE",
    "Vehicle_Structure": "TITLE", "Family": "TITLE", "Geography": "TITLE",
    "Development_Status": "TITLE", "Investment_Universe": "TITLE",
    "Investment_Focus": "TITLE", "Theme": "TITLE", "Sector_Focus": "TITLE",
    "Market_Cap_Focus": "TITLE", "Credit_Quality": "TITLE",
    "Style_Profile": "TITLE", "Exposure_Bias": "TITLE",
    "Duration_Profile": "TITLE", "Hedging_Policy": "TITLE",
    "Replication_Method": "TITLE", "Derivatives_Usage": "TITLE",
    "Leverage_Used": "TITLE", "Accumulation_Policy": "TITLE",
    "Distribution_Frequency": "TITLE", "Liquidity_Profile": "TITLE",
    "MMF_Structure": "TITLE", "Alt_Strategy": "TITLE", "Payoff_Profile": "TITLE",
    # UPPER_SNAKE — control/provenance flags
    "Benchmark_Type": "UPPER_SNAKE", "Fee_Known_Flag": "UPPER_SNAKE",
    "KID_Format": "UPPER_SNAKE", "Cost_Extraction_Quality": "UPPER_SNAKE",
    "SRRI_Quality_Flag": "UPPER_SNAKE", "Data_Quality_Flag": "UPPER_SNAKE",
    "SRRI_Validation_Status": "UPPER_SNAKE",
    "Cost_Mgmt_Arbitration": "UPPER_SNAKE", "Cost_Oper_Arbitration": "UPPER_SNAKE",
    # CODE / NUM
    "Fund_Currency": "CODE", "Asset_Currency": "CODE",
    "SRRI": "NUM", "Sfdr_Article": "NUM", "Recommended_Holding_Period": "NUM",
}

# Atributos eliminados en v20 (no deben aparecer en escrituras nuevas).
V20_DELETED_ATTRIBUTES: frozenset = frozenset(
    {"Subtype", "Portfolio_Currency", "Currency_Hedged", "Is_ESG", "Type"}
)

# ============================================================
# Catálogo consolidado (§Y-1) — UN solo objeto por atributo.
#   {casing_rule, allowed_values}  (None = no lista cerrada / numérico)
# classify_utils y schema_checks DERIVAN de aquí (dependency leaf).
# ============================================================
ATTRIBUTE_CATALOG: dict[str, dict] = {
    attr: {
        "casing_rule": ATTRIBUTE_CASING.get(attr, "TITLE"),
        "allowed_values": DOMAIN_VALUES.get(attr),
    }
    for attr in set(ATTRIBUTE_CASING) | set(DOMAIN_VALUES)
}

# Conjuntos para lookup O(1) en validaciones
# ============================================================
# Legacy VALUE → v20 canonical VALUE (NO es casing; lo consume
# classify_utils.normalize_casing antes del lookup de casing).
# Fuente única de remaps de valor legacy. Distinto de un .title():
# 'PARTIAL' no casa por casing con 'Partially Hedged' (texto distinto).
# ============================================================
LEGACY_VALUE_REMAP: dict[str, dict[str, str]] = {
    "Hedging_Policy": {"PARTIAL": "Partially Hedged"},
}

DOMAIN_SETS: dict[str, frozenset] = {
    k: frozenset(v) for k, v in DOMAIN_VALUES.items()
}
