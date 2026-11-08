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


def validate_nav_stale_flat(nav_df, min_run=None, min_jump=None, enabled=None):
    """FND-0383: a MONTHLY NAV series that holds one identical value for `min_run` consecutive observations and then moves by more than `min_jump`
    (either direction) in one step is a stale carry-forward followed by a catch-up, not a traded price. Returns (ok, message); the message names the month,
    the length of the flat run and the move. Pure: the defaults come from shared.config (NAV_STALE_FLAT_*), `enabled=False` (or the kill switch) accepts everything.
    Call it on the monthly series only: a daily window has flat runs by nature."""
    from shared import config as _cfg
    if not (_cfg.NAV_STALE_FLAT_ENABLED if enabled is None else enabled):
        return True, None
    min_run = _cfg.NAV_STALE_FLAT_MIN_RUN if min_run is None else min_run
    min_jump = _cfg.NAV_STALE_FLAT_MIN_JUMP if min_jump is None else min_jump
    if len(nav_df) <= min_run:
        return True, None
    vals = nav_df["nav"].to_numpy(dtype=float)
    dates = nav_df["date"].to_numpy()
    run = 1
    for i in range(1, len(vals)):
        if vals[i] == vals[i - 1]:
            run += 1
            continue
        if run >= min_run and vals[i - 1] > 0 and abs(vals[i] / vals[i - 1] - 1.0) > min_jump:
            return False, (
                f"NAV congelado {run} observaciones seguidas en {vals[i - 1]:g} y luego un salto de "
                f"{vals[i] / vals[i - 1] - 1.0:+.0%} en {str(dates[i])[:7]} "
                "(arrastre de un valor obsoleto y recuperacion de golpe; revisar la serie en nav_discovery)"
            )
        run = 1
    return True, None


def validate_ipc(ipc_df):
    if ipc_df["ipc_index"].isnull().any():
        return False, "IPC con nulos"
    if (ipc_df["ipc_index"] <= 0).any():
        return False, "IPC no positivo"
    return True, None
