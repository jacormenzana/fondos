# proyecto2/src/calculations/drawdown.py
# -*- coding: utf-8 -*-
"""
Cálculo de métricas de drawdown sobre series NAV.

Todas las funciones reciben pandas Series con índice numérico secuencial
(no DatetimeIndex) para independencia del tipo de índice.
"""

import numpy as np
import pandas as pd


def compute_drawdown(series: pd.Series) -> pd.Series:
    """
    Calcula la serie de drawdown relativo al máximo histórico acumulado.
    Resultado: ratio ≤ 0 en cada punto.
    """
    rolling_max = series.cummax()
    return series / rolling_max - 1.0


def max_drawdown(drawdown_series: pd.Series) -> float:
    """Máxima pérdida pico-valle. Valor ≤ 0."""
    return float(drawdown_series.min())


def drawdown_duration(drawdown_series: pd.Series) -> int:
    """
    Número máximo de meses consecutivos en drawdown (< 0).
    Devuelve 0 si nunca hay drawdown.
    """
    in_dd = drawdown_series < 0
    max_dur = 0
    current = 0

    for val in in_dd:
        if val:
            current += 1
            max_dur = max(max_dur, current)
        else:
            current = 0

    return max_dur


def time_to_recovery(series: pd.Series) -> float:
    """
    Meses desde el mínimo drawdown hasta recuperar el máximo previo.

    La serie debe tener índice entero secuencial (0, 1, 2, ...) donde
    cada unidad representa un mes. El pipeline debe resetear el índice
    antes de llamar a esta función.

    Devuelve:
        float: número de meses hasta recuperación
        np.nan: si nunca recupera dentro de la serie
    """
    if len(series) < 2:
        return np.nan

    # Asegurar índice entero secuencial
    series = series.reset_index(drop=True)

    rolling_max = series.cummax()
    drawdown    = series / rolling_max - 1.0

    trough_idx  = int(drawdown.idxmin())
    peak_value  = float(rolling_max.iloc[trough_idx])

    # Buscar recuperación desde el mínimo
    after_trough = series.iloc[trough_idx:]
    recovered    = after_trough[after_trough >= peak_value]

    if recovered.empty:
        return np.nan

    recovery_idx = int(recovered.index[0])
    return float(recovery_idx - trough_idx)
