# proyecto2/src/calculations/peer_panel.py
# -*- coding: utf-8 -*-
"""
Per-run peer panel cache for the two peer-based P2 families that were re-reading and re-converting a whole category for every fund (FND-0255).

Measured 2026-10-18 on the 36-ISIN sample (out/audit/p2_perf_harness.py): persistence 1.87 s/fund and capture 0.72 s/fund of a ~5.8 s/fund P2 run.
  * capture_ratios.load_peer_benchmark read every peer of the nature (Renta Variable: ~1,600 funds, ~370k rows), converted it to EUR, pivoted and averaged it,
    once per fund;
  * persistence._category_return_in_window ran one SQL aggregate (plus a EUR conversion of the first and last NAV of every peer) per rolling window per fund,
    ~30 windows per fund, and the windows start at each fund's OWN first date, so caching by window would almost never hit.
Here one NAV panel per nature is built ONCE per run and sliced in memory:
  * capture: the month-end return matrix of the whole nature; a fund's benchmark is the mean of that matrix without its own column;
  * persistence: a raw NAV matrix and a EUR-converted one (dates x funds); a window's peer return is read from a slice of them.

Equivalence with the legacy code is the contract (tests/test_peer_panel_fnd0255_pg.py runs both on the same database, EUR view on and off):
  * a peer's month count and first/last DATES come from the RAW rows, its first/last VALUES from the converted matrix, and a peer whose first or last point is
    not convertible leaves the group -- exactly as _peer_rows_in_eur did (which converted only those two points);
  * capture's benchmark is bit-identical (same pivot, same grid, the self column dropped before the mean); persistence's category return is the mean of the same
    per-peer returns in a different summation order (the legacy order was the database's GROUP BY order, i.e. undefined), so it can differ by a few ulps,
    which only matters if a fund's own window return equals it to 1e-15.

The cache is OPT-IN and bound to the run's connection: run_pipeline calls start_run(conn); every other caller (tests, notebooks, other tools) gets the legacy
code path untouched, and a different connection never sees another run's panel. Kill-switch: shared.config.PEER_PANEL_CACHE_ENABLED. Only the production flag
combination is accelerated (CAPTURE_MONTH_END_ENABLED and PERSISTENCE_FIRST_LAST_NAV_ENABLED); with either off the legacy path runs.
"""

from __future__ import annotations

import weakref

import numpy as np
import pandas as pd

from shared import config as _config
from shared.annualization import by_dates_enabled, month_ordinals
from shared.eur_nav import apply_eur_view_frame

_Q_NATURE_NAV = """
    SELECT fnm.ISIN, fnm.Date, fnm.NAV
    FROM fund_nav_monthly fnm
    JOIN fund_master fm ON fm.ISIN = fnm.ISIN
    WHERE fm.Fund_Nature = %s
    ORDER BY fnm.ISIN, fnm.Date
"""

_state: dict = {"conn": None, "capture": {}, "persistence": {}}


def start_run(conn) -> bool:
    """Activate the cache for `conn` (called once per pipeline run, before the per-fund loop). False when the kill-switch is off."""
    reset()
    if not getattr(_config, "PEER_PANEL_CACHE_ENABLED", False):
        return False
    _state["conn"] = weakref.ref(conn)
    return True


def reset() -> None:
    """Drop every panel and deactivate (end of a run, and between tests)."""
    _state["conn"] = None
    _state["capture"] = {}
    _state["persistence"] = {}


def active_for(conn) -> bool:
    """Is the cache active for THIS connection object? A different or garbage-collected connection never matches."""
    ref = _state["conn"]
    return ref is not None and ref() is conn


def _nature_frame(conn, nature: str):
    rows = conn.execute(_Q_NATURE_NAV, (nature,)).fetchall()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["isin", "date", "nav"])
    df["nav"] = df["nav"].astype(float)
    return df


# ---------------------------------------------------------------- capture
def capture_returns(conn, nature: str) -> pd.DataFrame:
    """Month-end 1-month return matrix (month-end grid x funds) of every fund of `nature`, EUR view applied, built once per run. Same pivot, grid and
    pct_change as the legacy load_peer_benchmark, only over the whole nature instead of the nature without one fund."""
    key = (nature, bool(_config.EUR_NAV_CONVERSION_ENABLED))
    cache = _state["capture"]
    if key in cache:
        return cache[key]
    out = pd.DataFrame()
    df = _nature_frame(conn, nature)
    if df is not None:
        df = apply_eur_view_frame(conn, df)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
            wide = df.pivot_table(index="date", columns="isin", values="nav", aggfunc="last").sort_index()
            wide = wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq=pd.offsets.MonthEnd()))
            out = wide.pct_change(fill_method=None)
    cache[key] = out
    return out


def capture_benchmark(conn, nature: str, exclude_isin: str) -> pd.Series:
    """The peer benchmark of load_peer_benchmark (month-end branch) from the cached matrix: mean of the peers' returns without `exclude_isin`."""
    r = capture_returns(conn, nature)
    if r.empty:
        return pd.Series(dtype=float)
    if exclude_isin in r.columns:
        r = r.drop(columns=exclude_isin)
    if r.shape[1] == 0:
        return pd.Series(dtype=float)
    return r.mean(axis=1).dropna()


# ---------------------------------------------------------------- persistence
class _Panel:
    __slots__ = ("isins", "col", "dates", "raw", "eur")

    def __init__(self, isins, dates, raw, eur):
        self.isins, self.dates, self.raw, self.eur = isins, dates, raw, eur
        self.col = {i: j for j, i in enumerate(isins)}


def persistence_panel(conn, nature: str):
    """Raw and EUR-converted NAV matrices (dates x funds) of `nature`, built once per run; None when the nature has no NAV."""
    key = (nature, bool(_config.EUR_NAV_CONVERSION_ENABLED))
    cache = _state["persistence"]
    if key in cache:
        return cache[key]
    df = _nature_frame(conn, nature)
    panel = None
    if df is not None:
        conv = apply_eur_view_frame(conn, df)                 # rows without a rate and funds without a convertible class currency are dropped
        d64 = pd.to_datetime(df["date"]).values.astype("datetime64[ns]")
        isins = np.array(sorted(df["isin"].unique()), dtype=object)
        dates = np.unique(d64)
        raw = np.full((len(dates), len(isins)), np.nan)
        raw[np.searchsorted(dates, d64), pd.Index(isins).get_indexer(df["isin"])] = df["nav"].to_numpy()
        if conv is df:                                        # EUR view off: the identity
            eur = raw
        else:
            cd = pd.to_datetime(conv["date"]).values.astype("datetime64[ns]")
            eur = np.full_like(raw, np.nan)
            eur[np.searchsorted(dates, cd), pd.Index(isins).get_indexer(conv["isin"])] = conv["nav"].astype(float).to_numpy()
        panel = _Panel(isins, dates, raw, eur)
    cache[key] = panel
    return panel


def persistence_window_return(conn, nature: str, exclude_isin: str, start, end, min_months: int):
    """Mean annualised return of the peers of `nature` (without `exclude_isin`) over [start, end]: the value of the legacy
    persistence._category_return_in_window for the production flag combination (first/last NAV), or None when no peer qualifies."""
    p = persistence_panel(conn, nature)
    if p is None:
        return None
    lo = int(np.searchsorted(p.dates, np.datetime64(pd.Timestamp(start).strftime("%Y-%m-%d"), "ns"), side="left"))
    hi = int(np.searchsorted(p.dates, np.datetime64(pd.Timestamp(end).strftime("%Y-%m-%d"), "ns"), side="right"))
    if hi <= lo:
        return None
    valid = ~np.isnan(p.raw[lo:hi])
    n = valid.sum(axis=0)
    keep = n >= min_months                                    # HAVING COUNT(*) >= WINDOW_MONTHS - 3, counted on RAW rows (before conversion)
    j_self = p.col.get(exclude_isin)
    if j_self is not None:
        keep[j_self] = False
    cols = np.flatnonzero(keep)
    if cols.size == 0:
        return None
    v = valid[:, cols]
    first = v.argmax(axis=0)
    last = (v.shape[0] - 1) - v[::-1].argmax(axis=0)
    nav_s = p.eur[lo + first, cols]
    nav_e = p.eur[lo + last, cols]
    ok = ~(np.isnan(nav_s) | np.isnan(nav_e))                 # a peer whose first or last point is not convertible leaves the group
    if not ok.any():
        return None
    cols, first, last, nav_s, nav_e, n_c = cols[ok], first[ok], last[ok], nav_s[ok], nav_e[ok], n[cols][ok]
    if by_dates_enabled():
        eff = month_ordinals(p.dates[lo + last]) - month_ordinals(p.dates[lo + first]) + 1
        pts = np.where(eff >= 2, eff, n_c)                    # legacy _points_for_years: the calendar span, or the row count when it is < 2 months
    else:
        pts = n_c
    years = pts / 12
    sel = (nav_s != 0) & (nav_e != 0) & (nav_s > 0) & (nav_e > 0) & (years > 0)
    if not sel.any():
        return None
    ret = (nav_e[sel] / nav_s[sel]) ** (1.0 / years[sel]) - 1
    return float(np.mean(ret))
