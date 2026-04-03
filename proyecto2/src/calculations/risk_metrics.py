# proyecto2/src/calculations/risk_metrics.py
# -*- coding: utf-8 -*-
"""
Calculo del conjunto completo de metricas de riesgo y eficiencia.
Combina drawdown, retorno, consistencia y ratios de eficiencia.
"""

import numpy as np
import pandas as pd

from src.calculations.drawdown import (
    compute_drawdown,
    max_drawdown,
    drawdown_duration,
    time_to_recovery,
)
from src.calculations.deflation import deflate_nav
from src.calculations.returns import (
    annualized_return,
    annualized_volatility,
    sharpe_ratio,
    sortino_ratio,
    ret_vol_simple,
)
from src.calculations.srri import srri_metrics


def compute_risk_metrics(
    nav_df: pd.DataFrame,
    ipc_df: pd.DataFrame | None = None,
    risk_free_rate_ann: float = 0.04,
) -> pd.DataFrame:
    """
    Calcula el conjunto completo de metricas de riesgo para una serie NAV.

    Parametros:
        nav_df: DataFrame con columnas ['date', 'nav'], ordenado por fecha.
        ipc_df: DataFrame con columnas ['date', 'ipc_index'].
                Si None, solo se calculan metricas nominales.
        risk_free_rate_ann: tipo libre de riesgo anual para Sharpe/Sortino.

    Devuelve:
        DataFrame con columnas ['metric', 'value', 'real_flag']
    """
    results = []

    nav_df = nav_df.sort_values("date").reset_index(drop=True)
    nav_series = nav_df["nav"]

    # -- METRICAS NOMINALES -----------------------------------
    dd_nom = compute_drawdown(nav_series)

    results.extend([
        # Dano
        {"metric": "max_drawdown",       "value": max_drawdown(dd_nom),             "real_flag": 0},
        {"metric": "drawdown_duration",  "value": drawdown_duration(dd_nom),        "real_flag": 0},
        {"metric": "time_to_recovery",   "value": time_to_recovery(nav_series),     "real_flag": 0},
        # Retorno
        {"metric": "return_ann",         "value": annualized_return(nav_series),    "real_flag": 0},
        {"metric": "volatility_ann",     "value": annualized_volatility(nav_series),"real_flag": 0},
        # Eficiencia
        {"metric": "sharpe",             "value": sharpe_ratio(nav_series, risk_free_rate_ann),  "real_flag": 0},
        {"metric": "sortino",            "value": sortino_ratio(nav_series, risk_free_rate_ann), "real_flag": 0},
        {"metric": "ret_vol_simple",     "value": ret_vol_simple(nav_series),       "real_flag": 0},
    ])

    # -- METRICAS REALES (deflactadas) ------------------------
    if ipc_df is not None and not ipc_df.empty:
        df_real    = deflate_nav(nav_df, ipc_df)
        nav_real   = df_real["nav_real"].reset_index(drop=True)
        dd_real    = compute_drawdown(nav_real)

        # Tipo libre de riesgo real ~ nominal - inflacion media del periodo
        # (aproximacion conservadora: usamos el mismo rf nominal)
        results.extend([
            # Dano real
            {"metric": "max_drawdown",       "value": max_drawdown(dd_real),             "real_flag": 1},
            {"metric": "drawdown_duration",  "value": drawdown_duration(dd_real),        "real_flag": 1},
            {"metric": "time_to_recovery",   "value": time_to_recovery(nav_real),        "real_flag": 1},
            # Retorno real
            {"metric": "return_ann",         "value": annualized_return(nav_real),       "real_flag": 1},
            {"metric": "volatility_ann",     "value": annualized_volatility(nav_real),   "real_flag": 1},
            # Eficiencia real
            {"metric": "sharpe",             "value": sharpe_ratio(nav_real, risk_free_rate_ann),  "real_flag": 1},
            {"metric": "sortino",            "value": sortino_ratio(nav_real, risk_free_rate_ann), "real_flag": 1},
            {"metric": "ret_vol_simple",     "value": ret_vol_simple(nav_real),          "real_flag": 1},
        ])

    # -- SRRI (calculado desde NAV nominal) --
    for metric, value, real_flag in srri_metrics(nav_series):
        results.append({"metric": metric, "value": value, "real_flag": real_flag})

    return pd.DataFrame(results)
