# -*- coding: utf-8 -*-
"""
Created on Fri Jan  2 20:39:10 2026

@author: Administrador
"""

def validate_nav(nav_df):
    if nav_df.isnull().any().any():
        return False, "NAV contiene nulos"
    if not nav_df["date"].is_monotonic_increasing:
        return False, "Fechas no ordenadas"
    if (nav_df["nav"] <= 0).any():
        return False, "NAV no positivo"
    # FIX-P2-NAV-SCALE-1 (2026-07-19): detectar mezcla de escalas NAV.
    # Un fondo regulado no puede tener un salto >8x entre observaciones
    # mensuales adyacentes. Si se detecta, la serie no es fiable para el
    # cálculo de métricas (srri_volatility, sharpe, max_drawdown, etc.).
    if len(nav_df) > 1:
        vals = nav_df["nav"].values
        ratios = vals[1:] / vals[:-1]
        if (ratios > 8).any() or (ratios < 0.125).any():
            return False, (
                "NAV contiene saltos >8x entre observaciones adyacentes "
                "(posible mezcla de escalas — revisar la serie en nav_discovery)"
            )
    return True, None


def validate_ipc(ipc_df):
    if ipc_df["ipc_index"].isnull().any():
        return False, "IPC con nulos"
    if (ipc_df["ipc_index"] <= 0).any():
        return False, "IPC no positivo"
    return True, None
