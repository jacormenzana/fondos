# shared/config.py
# -*- coding: utf-8 -*-
"""
Configuracion centralizada del sistema de fondos.

Todos los proyectos (P1, P2, P3) importan de aqui la ruta
a la base de datos unificada y los parametros globales.

Sustituye a proyecto1/src/config.py y proyecto2/src/config.py.

Uso desde cualquier modulo:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[N]))
    from shared.config import DB_PATH, RISK_FREE_RATE_ANN
"""

from pathlib import Path

# ============================================================
# Raiz del proyecto global
# ============================================================
# Este fichero vive en:  <raiz>/shared/config.py
_ROOT = Path(__file__).resolve().parent.parent   # c:/desarrollo/fondos

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
# Parametros globales P2 — calculo de metricas
# ============================================================

# Tipo libre de riesgo de referencia (EUR STR / tipo deposito BCE)
# Actualizar manualmente cada trimestre o cargar desde series_macro
RISK_FREE_RATE_ANN: float = 0.040   # 4.0% anual (referencia marzo 2026)

# Version canonica de metricas P2 activa
METRIC_VERSION: str = "v1"

# Umbral de perdida mensual severa (para pct_severe_loss_months)
SEVERE_LOSS_THRESHOLD: float = -0.02   # -2% mensual

# Horizontes estandar disponibles (cerrados)
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

# Region IPC por defecto para deflactacion de NAV
REGION_IPC: str = "ES"

# Minimo de observaciones mensuales para calcular metricas
MIN_NAV_ROWS: int = 12

# Ventanas de crisis historicas (nombre -> (inicio, fin) inclusive)
CRISIS_WINDOWS: dict = {
    "crisis_2008": ("2007-10-01", "2009-03-31"),
    "crisis_2011": ("2010-12-01", "2012-07-31"),
    "crisis_2020": ("2020-02-01", "2020-04-30"),
    "crisis_2022": ("2021-11-01", "2022-10-31"),
}

# Horizontes rolling en meses (para slice automatico)
ROLLING_WINDOWS: dict = {
    "rolling_1y":   12,
    "rolling_3y":   36,
    "rolling_5y":   60,
    "rolling_10y": 120,
}

# ============================================================
# Parametros globales P3 — seleccion y cartera
# ============================================================

# Objetivo de rentabilidad minima anual real (IPC + inflacion monetaria)
# Usado como referencia en scoring, NO como hard filter individual de fondo
MIN_REAL_RETURN_TARGET: float = 0.11   # 11% anual (IPC ~3% + M3 ~4% + margen)

# Version de scoring activa
SCORE_VERSION: str = "v1"

# ============================================================
# Logging
# ============================================================
LOG_DIR: Path = _ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
