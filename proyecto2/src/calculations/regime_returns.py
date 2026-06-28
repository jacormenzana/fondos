# proyecto2/src/calculations/regime_returns.py
# -*- coding: utf-8 -*-
"""
Calculo de retornos y riesgo por regimen macroeconomico para cada fondo.

Para cada fondo, cruza su serie de retornos mensuales con la clasificacion
historica de regimenes y calcula estadisticas de rendimiento por regimen.

Metricas generadas por fondo (horizon=since_inception, real_flag=0):
    return_ann_{regimen}   retorno anualizado en ese regimen (%)
    sharpe_{regimen}       ratio Sharpe en ese regimen (rf anualizado)
    vol_ann_{regimen}      volatilidad anualizada en ese regimen (%)
    n_obs_{regimen}        numero de meses en ese regimen con retorno disponible

Sufijo de regimen (nombre en minusculas con guiones bajos):
    expansion
    recalentamiento
    recalentamiento_tardio
    estanflacion
    contraccion
    shock_energetico
    crisis_financiera

Requisitos:
    MIN_OBS_REGIME = 12 meses minimos en un regimen para calcular estadisticas
    MIN_NAV_TOTAL  = 36 meses totales de NAV (mismo umbral que macro_sensitivity)

Uso en pipeline:
    from src.calculations.regime_returns import (
        load_regime_history, compute_regime_returns
    )
    regime_df = load_regime_history(conn)          # una vez, fuera del bucle
    metrics   = compute_regime_returns(nav_df, regime_df)
"""

import numpy as np
import pandas as pd
import sqlite3
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT))

from shared.config import RISK_FREE_RATE_ANN

MIN_OBS_REGIME = 12   # minimo de meses en un regimen para calcular estadisticas
MIN_NAV_TOTAL  = 36   # minimo de meses totales de NAV

# Tasa libre de riesgo mensual (para Sharpe por regimen)
_RF_MONTHLY = (1 + RISK_FREE_RATE_ANN) ** (1 / 12) - 1

# Mapa nombre de regimen -> sufijo de metrica (minusculas, guiones bajos)
_REGIME_SUFFIX = {
    "Expansion":              "expansion",
    "Recalentamiento":        "recalentamiento",
    "Recalentamiento_Tardio": "recalentamiento_tardio",
    "Estanflacion":           "estanflacion",
    "Contraccion":            "contraccion",
    "Shock_Energetico":       "shock_energetico",
    "Crisis_Financiera":      "crisis_financiera",
}


# ============================================================
# Carga del historico de regimenes
# ============================================================

def load_regime_history(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Construye el historico de regimenes usando RegimeClassifier.

    Devuelve DataFrame indexado por fecha (fin de mes) con columna 'regime'.
    Si no hay datos macro suficientes devuelve DataFrame vacio.

    Se llama UNA VEZ fuera del bucle de fondos -- es comun a todos.
    """
    try:
        # Importacion local para evitar dependencia circular en tests
        _P3 = Path(__file__).resolve().parents[4] / "proyecto3" / "src"
        sys.path.insert(0, str(_P3.parent.parent))
        from proyecto3.src.regime_classifier import RegimeClassifier

        clf    = RegimeClassifier(conn)
        hist   = clf.classify_historical()

        if hist.empty:
            print("  [RegimeReturns] Sin historico de regimenes disponible")
            return pd.DataFrame()

        # Normalizar indice a fin de mes
        hist.index = pd.to_datetime(hist.index) + pd.offsets.MonthEnd(0)
        hist = hist[~hist.index.duplicated(keep="last")]

        n_regimes = hist["regime"].nunique()
        print(f"  [RegimeReturns] Historico cargado: {len(hist)} meses | "
              f"{n_regimes} regimenes distintos")
        return hist[["regime"]]

    except Exception as e:
        print(f"  [RegimeReturns] Error cargando historico: {e}")
        return pd.DataFrame()


# ============================================================
# Calculo de estadisticas por regimen para un fondo
# ============================================================

def _annualized_return(monthly_log_returns: np.ndarray) -> float:
    """Retorno anualizado desde retornos logaritmicos mensuales."""
    total_log = float(np.sum(monthly_log_returns))
    n_months   = len(monthly_log_returns)
    # Convertir de log a geometrico anualizado
    return (np.exp(total_log * 12 / n_months) - 1) * 100


def _annualized_vol(monthly_log_returns: np.ndarray) -> float:
    """Volatilidad anualizada desde retornos logaritmicos mensuales."""
    return float(np.std(monthly_log_returns, ddof=1)) * np.sqrt(12) * 100


def _sharpe(monthly_log_returns: np.ndarray) -> float:
    """
    Sharpe ratio anualizado.
    Usa exceso de retorno sobre rf mensual, anualizado geometricamente.
    """
    excess    = monthly_log_returns - _RF_MONTHLY
    mean_exc  = float(np.mean(excess))
    std_exc   = float(np.std(excess, ddof=1))
    if std_exc < 1e-10:
        return 0.0
    return (mean_exc / std_exc) * np.sqrt(12)


def compute_regime_returns(
    nav_df:    pd.DataFrame,
    regime_df: pd.DataFrame,
) -> list[tuple]:
    """
    Calcula retornos y riesgo por regimen para un fondo.

    Parametros:
        nav_df:    DataFrame con columnas [date, nav] (fechas fin de mes)
        regime_df: DataFrame indexado por fecha con columna 'regime'
                   (salida de load_regime_history)

    Devuelve lista de (metric_name, value, real_flag).
    Devuelve lista vacia si no hay suficientes datos o regimes.
    """
    if nav_df.empty or regime_df is None or regime_df.empty:
        return []

    if len(nav_df) < MIN_NAV_TOTAL:
        return []

    # Calcular retornos logaritmicos mensuales
    nav = nav_df.set_index("date")["nav"].sort_index()
    nav.index = pd.to_datetime(nav.index) + pd.offsets.MonthEnd(0)
    # MonthEnd snap puede crear duplicados si hay dos registros en el mismo mes;
    # conservar el último (cierre de mes más reciente)
    nav = nav[~nav.index.duplicated(keep="last")]
    r_log = np.log(nav / nav.shift(1)).dropna()

    # Cruzar con regimenes
    merged = pd.concat(
        [r_log.rename("r_log"), regime_df["regime"]],
        axis=1, join="inner"
    ).dropna()

    if merged.empty:
        return []

    metrics = []

    for regime_name, suffix in _REGIME_SUFFIX.items():
        mask      = merged["regime"] == regime_name
        r_regime  = merged.loc[mask, "r_log"].values
        n_obs     = len(r_regime)

        # Siempre persistir n_obs (aunque sea 0 -- util para saber cobertura)
        metrics.append((f"n_obs_{suffix}", float(n_obs), 0))

        if n_obs < MIN_OBS_REGIME:
            # Sin suficientes datos -- no calcular estadisticas
            continue

        ret_ann = _annualized_return(r_regime)
        vol_ann = _annualized_vol(r_regime)
        sharpe  = _sharpe(r_regime)

        metrics.append((f"return_ann_{suffix}", ret_ann,  0))
        metrics.append((f"vol_ann_{suffix}",    vol_ann,  0))
        metrics.append((f"sharpe_{suffix}",     sharpe,   0))

    return metrics
