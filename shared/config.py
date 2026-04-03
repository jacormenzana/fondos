# shared/config.py
# -*- coding: utf-8 -*-
"""
Configuracion centralizada del sistema de fondos.

Todos los proyectos (P1, P2, P3) importan de aqui la ruta a la base de
datos unificada, los parametros globales y los vocabularios de dominio.

Sustituye a proyecto1/src/config.py y proyecto2/src/config.py.

Uso desde cualquier modulo:
    from shared.config import DB_PATH, RISK_FREE_RATE_ANN

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
SCHEMA_VERSION: str = "v17"

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
    "rolling_3y":   36,
    "rolling_5y":   60,
    "rolling_10y": 120,
}

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

    # ── Clasificación ────────────────────────────────────────────
    "Fund_Nature": [
        "Renta Variable",
        "Mixtos",
        "Renta Fija Flexible",
        "Renta Fija Corto Plazo",
        "Monetario",
        "Alternativo",
        "Restantes",
        "Estructurado",
    ],

    "Profile": [
        "Conservador",    # SRRI 1-2
        "Moderado",       # SRRI 3-4
        "Dinámico",       # SRRI 5-6
        "Agresivo",       # SRRI 7  (v17)
    ],

    "Type": [
        "Equity",
        "Fixed Income",
        "Balanced",
        "Absolute Return",
        "Index",
        "ETF",
        "Fund of Funds",
        "Commodity",
        "Real Estate",
        "Multi-Asset",
        "Money Market",
    ],

    # ── Exposición ───────────────────────────────────────────────
    "Investment_Universe": [
        "Global",
        "Regional",
        "Country",
        "Liquidity",
    ],

    "Investment_Focus": [    # v17 NUEVO
        "Broad",
        "Sector",
        "Thematic",
    ],

    "Theme": [
        "Core/General",                  # v17: no temático confirmado
        "Technology",
        "Artificial Intelligence",
        "Digital",
        "Robotics",
        "Cybersecurity",
        "Climate / Clean Energy",
        "Energy",
        "Healthcare / MedTech",
        "Healthcare",
        "Biotechnology",
        "Water",
        "Gold",
        "Mining",
        "Infrastructure",
        "Real Estate",
        "Financials",
        "Consumer Brands",
        "Consumer / Food & Beverage",
        "Silver Economy",
        "Megatrends",
        "Insurance",
        "Inflación",
        "Mobility",
    ],

    "Geography": [
        "Global",
        "Europa",
        "EEUU",
        "Asia-Pacífico",
        "Emergentes",
        "China",
        "Japón",
        "India",
        "Latinoamérica",
        "Europa del Este",
        "Rusia",
        "Italia",
        "Alemania",
        "España",
    ],

    "Sector_Focus": [
        "Tecnología e Innovación",
        "Salud y Ciencias de la Vida",
        "Energía y Recursos",
        "Real Assets",
        "Servicios Financieros",
        "Consumo",
        "Materiales y Minería",
        "Utilities y Medio Ambiente",
    ],

    "Market_Cap_Focus": [
        "Large Cap",
        "Mid Cap",
        "Small Cap",
        "SMID Cap",
        "Multi Cap",
    ],

    "Credit_Quality": [      # v17 NUEVO
        "Investment Grade",
        "High Yield",
        "Mixed",
        "No aplica",
    ],

    # ── Estrategia ───────────────────────────────────────────────
    "Style_Profile": [
        "Value",
        "Growth",
        "Blend",
        "Income",
        "Yield",
    ],

    "Exposure_Bias": [
        "Long Only",
        "Long/Short",
        "Market Neutral",
        "Directional",
        "Credit Bias",
        "Macro",
        "Commodity",
    ],

    "Strategy": [
        "Active",
        "Passive",
        "Index",
        "Quant/Systematic",
    ],

    # ── Estructura ───────────────────────────────────────────────
    "Hedging_Policy": [
        "Totalmente Cubierto",
        "Parcialmente Cubierto",
        "No Cubierto",
    ],

    "Currency_Hedged": ["Yes", "No"],

    "Replication_Method": ["Physical", "Synthetic", "Sampling"],

    "Derivatives_Usage": [
        "Hedging Only",
        "Investment",
        "Both",
        "No Usage",
    ],

    "Leverage_Used": ["Yes", "No"],

    "Liquidity_Profile": [
        "Diaria",
        "Semanal",
        "Quincenal",
        "Mensual",
        "Trimestral",
    ],

    "Accumulation_Policy": ["Accumulation", "Distribution"],

    "Distribution_Frequency": [
        "Mensual",
        "Trimestral",
        "Semestral",
        "Anual",
    ],

    # ── Costes ───────────────────────────────────────────────────
    "Fee_Known_Flag": [      # v17 NUEVO
        "EXTRACTED",
        "ZERO_CONFIRMED",
        "NOT_FOUND",
    ],

    # ── Regulación ───────────────────────────────────────────────
    "Sfdr_Article": ["6", "8", "9"],

    "Benchmark_Type": [
        "Reference",
        "Target",
        "Hurdle Rate",
        "None",
    ],

    # ── Calidad y control ────────────────────────────────────────
    "SRRI_Quality_Flag": [
        "HIGH",
        "MEDIUM_VISUAL",
        "MEDIUM_TEXT",
        "LOW",
        "NONE",
    ],

    "Data_Quality_Flag": ["OK", "WARN", "ERROR"],

    "SRRI_Validation_Status": [
        "MATCH",
        "TEXT_ONLY",
        "VISUAL_ONLY",
        "CONFLICT",
        "NOT_AVAILABLE",
    ],
}

# Conjuntos para lookup O(1) en validaciones
DOMAIN_SETS: dict[str, frozenset] = {
    k: frozenset(v) for k, v in DOMAIN_VALUES.items()
}
