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


def find_unshared_jumps(nav_df, peers, isin, min_abs_ret=None, min_peers=None, max_peer_share=None, recent_months=None):
    """FND-0392: the one-month moves of a MONTHLY NAV that its Fund_Nature peers did not make, largest first. Each item: {month, ret, peer_median, n_peers, peer_share}.

    A move qualifies when (1) it spans exactly one calendar month (a step over a NAV gap is not a one-month move), (2) |ret| >= min_abs_ret, (3) at least min_peers OTHER funds of the
    same Fund_Nature report that month (fewer = not decidable, never a finding), (4) the fund differs from the peer median by min_abs_ret or more, (5) at most max_peer_share of the peers
    moved the same way by at least half as much, and (6) FND-0393: the month is among the last `recent_months` months of the fund's OWN series (counted back from its newest NAV month, never from
    the clock, so the answer is reproducible; 0 = the whole history). The window exists because the rule quarantines: an old re-pricing of a single-region fund (2000 - 2022 in the census of
    2026-11-08) must not remove a fund for good. Pure: `peers` is a proyecto2/src/utils/peer_returns.PeerReturns, the defaults come from shared.config (NAV_UNSHARED_JUMP_*)."""
    import numpy as np
    import pandas as pd
    from shared import config as _cfg
    from shared.period_key import period_ordinals, to_period_key
    from src.utils.peer_returns import one_month_returns
    min_abs_ret = _cfg.NAV_UNSHARED_JUMP_MIN_ABS_RET if min_abs_ret is None else min_abs_ret
    min_peers = _cfg.NAV_UNSHARED_JUMP_MIN_PEERS if min_peers is None else min_peers
    max_peer_share = _cfg.NAV_UNSHARED_JUMP_MAX_PEER_SHARE if max_peer_share is None else max_peer_share
    recent_months = _cfg.NAV_UNSHARED_JUMP_RECENT_MONTHS if recent_months is None else recent_months
    rets = one_month_returns(nav_df)
    if recent_months and len(rets):
        last = int(np.max(period_ordinals(pd.DatetimeIndex(to_period_key(nav_df["date"])))))     # P#11: the one month-ordinal definition
        age = last - np.asarray(period_ordinals(rets.index))
        rets = rets[age < int(recent_months)]
    out = []
    for month, r in rets[rets.abs() >= min_abs_ret].items():
        arr = peers.peers_at(isin, month)
        if len(arr) < min_peers:
            continue
        median = float(np.median(arr))
        if abs(r - median) < min_abs_ret:
            continue
        share = float(((np.sign(arr) == np.sign(r)) & (np.abs(arr) >= 0.5 * abs(r))).mean())
        if share <= max_peer_share:
            out.append({"month": month, "ret": float(r), "peer_median": median, "n_peers": int(len(arr)), "peer_share": share})
    return sorted(out, key=lambda j: -abs(j["ret"]))


def validate_nav_unshared_jump(nav_df, peers, isin, min_abs_ret=None, min_peers=None, max_peer_share=None, recent_months=None):
    """FND-0392: (ok, message) over find_unshared_jumps. The message names the largest move, the peer median and how many peers moved the same way. It does NOT read the mode
    (OFF / SHADOW / ON): the caller decides what a False means (quarantine only in ON). Call it on the monthly series only."""
    jumps = find_unshared_jumps(nav_df, peers, isin, min_abs_ret, min_peers, max_peer_share, recent_months)
    if not jumps:
        return True, None
    j = jumps[0]
    same_way = int(round(j["peer_share"] * j["n_peers"]))
    extra = f" (+{len(jumps) - 1} mas)" if len(jumps) > 1 else ""
    return False, (
        f"NAV con un salto de {j['ret']:+.0%} en {str(j['month'])[:7]} que sus pares no compartieron: mediana de {j['n_peers']} pares {j['peer_median']:+.1%}, "
        f"{same_way} se movieron en el mismo sentido{extra} (cambio de nivel o de escala dentro del 8x; revisar la serie en nav_discovery)"
    )


def validate_ipc(ipc_df):
    if ipc_df["ipc_index"].isnull().any():
        return False, "IPC con nulos"
    if (ipc_df["ipc_index"] <= 0).any():
        return False, "IPC no positivo"
    return True, None
