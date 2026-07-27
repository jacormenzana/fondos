# shared/config.py
# -*- coding: utf-8 -*-
"""
Configuracion centralizada del sistema de fondos.

Todos los proyectos (P1, P2, P3) importan de aqui la ruta a la base de
datos unificada, los parametros globales y los vocabularios de dominio.

Sustituye a proyecto1/src/config.py y proyecto2/src/config.py.

Uso desde cualquier modulo:
    from shared.config import DB_PATH, RISK_FREE_RATE_ANN

Cambios v23 (2026-07-18, FIX-UNIVERSE-RECON-1):
  - SCHEMA_VERSION: "v22" → "v23".
  - fund_master: In_Current_Universe (INTEGER NOT NULL DEFAULT 1) añadida.
    Soft-delete flag regenerado en cada ciclo por reconcile_universe_membership()
    en sqlite_writer.py. 1 = en el harvest vigente, 0 = huérfano. No COALESCE-
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
    sqlite_writer.py v25, ucits_cost_extractor.py nuevo).

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
# Versión canónica del schema de BD
# ============================================================
SCHEMA_VERSION: str = "v24"

# ============================================================
# v23 (FIX-UNIVERSE-RECON-1, 2026-07-18): In_Current_Universe (fund_master)
# ============================================================
# Soft-delete universe-membership flag (INTEGER NOT NULL DEFAULT 1).
# 1 = fondo pertenece al harvest vigente (db_document_catalogue MAX),
# 0 = huérfano preservado en fund_master por la política append-only.
# No es COALESCE-protegido: regenerado completamente cada ciclo por
# reconcile_universe_membership() (sqlite_writer.py). No entra en
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
ROLLING_STATS_ENABLED: bool = False

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
# Base de datos unificada
# ============================================================
DB_PATH: Path = _ROOT / "db" / "fondos.sqlite"

# ============================================================
# Directorios de datos externos (inputs no versionados)
# ============================================================
DATA_DIR:     Path = _ROOT / "data"
MASTER_EXCEL: Path = DATA_DIR / "GestoresDeFondosv1.xlsx"

# ============================================================
# Directorios de outputs generados (no versionados)
# ============================================================
METRICS_DIR:  Path = _ROOT / "out" / "metrics"
REPORTS_DIR:  Path = _ROOT / "out" / "export"

# ============================================================
# Logging
# ============================================================
# NOTA: No se crea el directorio aquí para evitar efectos
# secundarios al importar. Usar get_log_dir() cuando se necesite.
_LOG_DIR: Path = _ROOT / "logs"


def get_log_dir() -> Path:
    """Devuelve el directorio de logs, creándolo si no existe."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


# Compatibilidad con código existente que usa config.LOG_DIR
# Se mantiene como property-like para no romper imports,
# pero sin crear el directorio al importar.
LOG_DIR: Path = _LOG_DIR


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
ALERT_RULES: list[dict] = [
    # Volatilidad vs categoría: WARN si > p90, ALARM si > p97
    {
        "rule_code":   "VOL_CAT_P90",
        "metric":      "roll_vol_ann",
        "window":      "rolling_6m",
        "ref_type":    "category",
        "level":       "WARN",
        "direction":   "above",
        "threshold_pctile": 0.90,
    },
    {
        "rule_code":   "VOL_CAT_P97",
        "metric":      "roll_vol_ann",
        "window":      "rolling_6m",
        "ref_type":    "category",
        "level":       "ALARM",
        "direction":   "above",
        "threshold_pctile": 0.97,
    },
    # Drawdown vs categoría (valores son ≤ 0; "peor" = más negativo = below p10)
    {
        "rule_code":   "DD_CAT_P10",
        "metric":      "roll_max_dd",
        "window":      "rolling_1y",
        "ref_type":    "category",
        "level":       "WARN",
        "direction":   "below",
        "threshold_pctile": 0.10,
    },
    {
        "rule_code":   "DD_CAT_P03",
        "metric":      "roll_max_dd",
        "window":      "rolling_1y",
        "ref_type":    "category",
        "level":       "ALARM",
        "direction":   "below",
        "threshold_pctile": 0.03,
    },
    # Retorno vs categoría: WARN si < p10 en 3y, ALARM si < p05
    {
        "rule_code":   "RET_CAT_P10",
        "metric":      "roll_return_ann",
        "window":      "rolling_3y",
        "ref_type":    "category",
        "level":       "WARN",
        "direction":   "below",
        "threshold_pctile": 0.10,
    },
    {
        "rule_code":   "RET_CAT_P05",
        "metric":      "roll_return_ann",
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
ROLLING_TIMESERIES_METRICS: list[str] = [
    "roll_vol_ann",
    "roll_max_dd",
    "roll_return_ann",
]

# Ventanas que generan serie temporal (meses de ROLLING_WINDOWS + días SHORT_WINDOWS)
# Se derivan automáticamente; no duplicar aquí — usar ROLLING_WINDOWS + SHORT_WINDOWS.

# ============================================================
# Parámetros globales P3 — selección y cartera
# ============================================================

# Objetivo de rentabilidad mínima anual real (IPC + inflación monetaria)
# Usado como referencia en scoring, NO como hard filter individual de fondo
MIN_REAL_RETURN_TARGET: float = 0.11   # 11% anual (IPC ~3% + M3 ~4% + margen)

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
