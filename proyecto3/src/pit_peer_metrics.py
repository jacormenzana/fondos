# proyecto3/src/pit_peer_metrics.py
# -*- coding: utf-8 -*-
"""
Point-in-time peer-relative metrics for the P3 backtester -- FND-0159, Wave B step d1b.

In-memory, expanding-window versions of the P2 metrics that compare a fund with the other funds of its
Fund_Nature and that P2 computes with per-fund DB queries (so they cannot be called on a truncated
slice): alpha_persistence, capture_ratio (+ upside/downside) and momentum_rank. Inputs are a wide
RAW-DATE NAV panel (DatetimeIndex x ISIN, NaN where a fund has no observation; do NOT month-end
normalize it, see pit_metrics) and a Series isin -> Fund_Nature.

FIDELITY CHOICE (owner decision 2026-10-03): P2's definitions are replicated EXACTLY, including their
known defects, so the PIT result can be pinned to P2; each one is a backlog item and the PIT code is
switched when P2 is fixed (change the flagged lines below + the oracle together):
  * FND-0200  persistence peer return = (MAX(NAV)/MIN(NAV)) ** (12/n) - 1 over the window (NOT last/first):
              biased upward for every peer. Replicated in `_peer_window_returns`; with the P2 flag
              PERSISTENCE_FIRST_LAST_NAV_ENABLED it follows P2 to the first/last NAV definition.
  * FND-0202  capture ratios join the fund's returns on RAW dates with a MONTH-END-normalized peer
              benchmark: observations that are not month-end dated silently drop out. Replicated. With the P2 flag
              CAPTURE_MONTH_END_ENABLED the month-end version is used (`_capture_ratios_month_end`): month bars, 1-month
              returns only, values reported on month-end dates (FND-0227).
  * momentum_rank ranks the fund's since_inception return_ann inside its Fund_Nature including itself,
              and P2's peer set includes retired funds with their last stored value (unlimited staleness,
              `max_stale_days=None`); a PIT run should pass a finite `max_stale_days`.

Why PIT-safe: a persistence window's outcome depends only on NAV inside the window (end <= t); the peer
capture benchmark at month-end d only on returns dated <= d; momentum on each fund's expanding return as
of t. Verified by tests that apply P2's own functions to the panel truncated at t.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import config as _config
from shared.config import (
    CAPTURE_MIN_PERIODS,
    MIN_PEERS,
    PERSISTENCE_MIN_WINDOWS,
    PERSISTENCE_STEP_MONTHS,
    PERSISTENCE_WINDOW_MONTHS,
)

_CLAMP = 5.0
_DOWNSIDE_FLOOR = 1e-6


# ============================================================
# momentum_rank
# ============================================================

def momentum_rank(
    return_ann: pd.DataFrame,
    nature: pd.Series,
    at: pd.DatetimeIndex,
    max_stale_days: "int | None" = None,
    min_peers: int = MIN_PEERS,
) -> pd.DataFrame:
    """Percentile of each fund's nominal return_ann among the funds of its Fund_Nature (itself included),
    P2 momentum.compute_momentum semantics: share of the category strictly below the fund; needs at least
    `min_peers` funds in the category.

    return_ann: expanding return (pit_metrics) on the raw-date grid. `at`: dates to evaluate (e.g. month-ends).
    A fund's value at t is its latest observation <= t; if `max_stale_days` is set, observations older than
    that are dropped from the snapshot (None = P2: a retired fund keeps its last value forever)."""
    grid = return_ann.index.union(at).sort_values()
    vals = return_ann.reindex(grid).to_numpy(dtype=float)
    seen = np.isfinite(vals)
    last_idx = np.maximum.accumulate(np.where(seen, np.arange(len(grid))[:, None], -1), axis=0)   # latest obs <= row
    at_pos = grid.get_indexer(at)
    li = last_idx[at_pos]                                                    # (len(at), n_funds)
    snap = np.where(li >= 0, vals[np.clip(li, 0, None), np.arange(vals.shape[1])[None, :]], np.nan)
    if max_stale_days is not None:
        gdays = grid.to_numpy().astype("datetime64[D]").astype("int64")
        age = gdays[at_pos][:, None] - gdays[np.clip(li, 0, None)]
        snap = np.where(age <= max_stale_days, snap, np.nan)

    out = np.full(snap.shape, np.nan)
    nat = nature.reindex(return_ann.columns)
    for n_name in nat.dropna().unique():
        cols = np.flatnonzero((nat == n_name).to_numpy())
        block = snap[:, cols]
        for i in range(block.shape[0]):
            row = block[i]
            ok = np.isfinite(row)
            n = int(ok.sum())
            if n < min_peers:
                continue
            ordered = np.sort(row[ok])
            ranks = np.full(len(row), np.nan)
            ranks[ok] = np.searchsorted(ordered, row[ok], side="left") / n
            out[i, cols] = ranks
    return pd.DataFrame(out, index=at, columns=return_ann.columns)


# ============================================================
# capture ratios
# ============================================================

def _returns_long(nav: pd.DataFrame) -> pd.DataFrame:
    """Per-fund simple returns over its own consecutive observations (P2: sort by (isin, date), pct_change),
    long format with the RAW date and the month-end-normalized date."""
    parts = []
    for col in nav.columns:
        s = nav[col].dropna()
        if len(s) < 2:
            continue
        r = s.pct_change().iloc[1:]
        parts.append(pd.DataFrame({"isin": col, "date": r.index, "ret": r.to_numpy()}))
    if not parts:
        return pd.DataFrame(columns=["isin", "date", "ret", "me_date"])
    df = pd.concat(parts, ignore_index=True)
    df = df[np.isfinite(df["ret"])]
    df["me_date"] = df["date"] + pd.offsets.MonthEnd(0)
    return df


def _capture_ratios_month_end(nav: pd.DataFrame, nature: pd.Series, min_periods: int = CAPTURE_MIN_PERIODS) -> dict:
    """PIT replica of P2 capture_ratios with CAPTURE_MONTH_END_ENABLED (FND-0202 fixed, FND-0227).

    P2 puts every fund and peer on a month-end grid (the last observation of the month), takes returns only between
    CONSECUTIVE months, builds the peer benchmark as the mean of the peers' returns of that month (the fund excluded)
    and joins on the same month-end dates. Replicated with cumulative sums, so one pass gives the value of every month.

    Result frames are indexed by the month-end grid (not by nav's raw dates) and hold the value at month-end t for the
    funds that have a bar in that month. PIT-safe by construction: the value at t only uses bars up to t, and a month-end
    row is the first moment at which every observation of the month is known (a mid-month row would mix a partial month
    with peers that report later in it). Same output contract as capture_ratios otherwise."""
    me = nav.index + pd.offsets.MonthEnd(0)
    bars = nav.groupby(me).last()                                  # last non-null NAV of each month, per fund
    grid = pd.date_range(bars.index.min(), bars.index.max(), freq=pd.offsets.MonthEnd())
    bars = bars.reindex(grid)
    ret = bars.pct_change(fill_method=None).to_numpy(dtype=float)  # a gap month gives no return, never a multi-month one
    fin = np.isfinite(ret)
    rz = np.where(fin, ret, 0.0)
    nat = nature.reindex(bars.columns).to_numpy(dtype=object)
    bench = np.full(ret.shape, np.nan)
    for n in pd.unique(nat[pd.notna(nat)]):
        idx = np.where(nat == n)[0]
        cat_sum, cat_n = rz[:, idx].sum(axis=1), fin[:, idx].sum(axis=1)
        peers_n = cat_n[:, None] - fin[:, idx]
        peers_sum = cat_sum[:, None] - rz[:, idx]
        with np.errstate(divide="ignore", invalid="ignore"):
            bench[:, idx] = np.where(peers_n > 0, peers_sum / peers_n, np.nan)
    valid = fin & np.isfinite(bench)
    up, down = valid & (bench > 0), valid & (bench < 0)
    c = np.cumsum
    n_ret, m = c(fin, axis=0), c(valid, axis=0)
    up_n, dn_n = c(up, axis=0), c(down, axis=0)
    up_f, up_b = c(np.where(up, ret, 0.0), axis=0), c(np.where(up, bench, 0.0), axis=0)
    dn_f, dn_b = c(np.where(down, ret, 0.0), axis=0), c(np.where(down, bench, 0.0), axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        upside = (up_f / up_n) / (up_b / up_n)
        downside = (dn_f / dn_n) / (dn_b / dn_n)
        ratio = upside / downside
    ok = ((n_ret >= min_periods * 2) & (m >= min_periods * 2) & (up_n >= min_periods) & (dn_n >= min_periods)
          & (np.abs(downside) >= _DOWNSIDE_FLOOR) & bars.notna().to_numpy())
    out = {}
    for name, v in (("upside_capture", upside), ("downside_capture", downside), ("capture_ratio", ratio)):
        vv = np.where(np.isnan(v), np.nan, np.clip(v, -_CLAMP, _CLAMP))
        out[name] = pd.DataFrame(np.where(ok, vv, np.nan), index=grid, columns=nav.columns)
    return out


def capture_ratios(nav: pd.DataFrame, nature: pd.Series, min_periods: int = CAPTURE_MIN_PERIODS) -> dict:
    """Expanding upside/downside capture and capture_ratio vs the peer benchmark (P2 capture_ratios).

    Benchmark at month-end d for fund f = mean return at d of the other funds of f's nature, excluding f
    (computed as category sum/count minus f's own rows). FND-0202 replicated: f's returns are joined on RAW
    date, so only f's month-end-dated observations enter. Returns {upside_capture, downside_capture,
    capture_ratio}: DataFrames on `nav`'s index/columns, values at the fund's own observation dates."""
    if _config.CAPTURE_MONTH_END_ENABLED:
        return _capture_ratios_month_end(nav, nature, min_periods)
    long = _returns_long(nav)
    shape = nav.shape
    res = {k: np.full(shape, np.nan) for k in ("upside_capture", "downside_capture", "capture_ratio")}
    if long.empty:
        return {k: pd.DataFrame(v, index=nav.index, columns=nav.columns) for k, v in res.items()}

    long["nature"] = long["isin"].map(nature)
    long = long[long["nature"].notna()]
    cat = long.groupby(["nature", "me_date"])["ret"].agg(cat_sum="sum", cat_n="count")
    col_pos = {c: j for j, c in enumerate(nav.columns)}
    row_pos = pd.Series(np.arange(len(nav.index)), index=nav.index)

    for isin, own in long.groupby("isin"):
        nat = own["nature"].iloc[0]
        c = cat.loc[nat]
        own_agg = own.groupby("me_date")["ret"].agg(o_sum="sum", o_n="count")
        j = c.join(own_agg, how="left").fillna({"o_sum": 0.0, "o_n": 0})
        peers_n = j["cat_n"] - j["o_n"]
        bench = ((j["cat_sum"] - j["o_sum"]) / peers_n).where(peers_n > 0).dropna()

        r_f = own.set_index("date")["ret"]
        n_ret = np.arange(1, len(r_f) + 1)
        merged = pd.concat([r_f.rename("r_fondo"), bench.rename("r_bench")], axis=1, join="inner").dropna()
        if merged.empty:
            continue
        up = (merged["r_bench"] > 0).to_numpy()
        down = (merged["r_bench"] < 0).to_numpy()
        rf_, rb_ = merged["r_fondo"].to_numpy(), merged["r_bench"].to_numpy()
        cum = {
            "m": np.arange(1, len(merged) + 1),
            "up_n": np.cumsum(up), "dn_n": np.cumsum(down),
            "up_f": np.cumsum(np.where(up, rf_, 0.0)), "up_b": np.cumsum(np.where(up, rb_, 0.0)),
            "dn_f": np.cumsum(np.where(down, rf_, 0.0)), "dn_b": np.cumsum(np.where(down, rb_, 0.0)),
        }
        mdates = merged.index.to_numpy()
        obs_dates = nav[isin].dropna().index
        k = np.searchsorted(mdates, obs_dates.to_numpy(), side="right") - 1          # last merged row <= t
        n_r_at_t = np.searchsorted(r_f.index.to_numpy(), obs_dates.to_numpy(), side="right")
        ok = (k >= 0) & (n_r_at_t >= min_periods * 2)
        kk = np.clip(k, 0, None)
        ok &= cum["m"][kk] >= min_periods * 2
        ok &= (cum["up_n"][kk] >= min_periods) & (cum["dn_n"][kk] >= min_periods)
        with np.errstate(divide="ignore", invalid="ignore"):
            upside = (cum["up_f"][kk] / cum["up_n"][kk]) / (cum["up_b"][kk] / cum["up_n"][kk])
            downside = (cum["dn_f"][kk] / cum["dn_n"][kk]) / (cum["dn_b"][kk] / cum["dn_n"][kk])
            ratio = upside / downside
        ok &= np.abs(downside) >= _DOWNSIDE_FLOOR
        rows = row_pos.loc[obs_dates].to_numpy()
        for name, v in (("upside_capture", upside), ("downside_capture", downside), ("capture_ratio", ratio)):
            vv = np.where(np.isnan(v), np.nan, np.clip(v, -_CLAMP, _CLAMP))
            res[name][rows[ok], col_pos[isin]] = vv[ok]
    return {k: pd.DataFrame(v, index=nav.index, columns=nav.columns) for k, v in res.items()}


# ============================================================
# alpha_persistence
# ============================================================

def _peer_window_returns(values: np.ndarray, dates: np.ndarray, ws: np.datetime64, we: np.datetime64,
                         min_obs: int) -> np.ndarray:
    """Per-peer window return over [ws, we] (inclusive, raw dates) for every column of `values`.

    FND-0200 (replicated, P2 persistence._category_return_in_window): (MAX(NAV) / MIN(NAV)) ** (12/n) - 1,
    not last/first. NaN for peers with fewer than `min_obs` observations in the window or MIN <= 0."""
    lo = np.searchsorted(dates, ws, side="left")
    hi = np.searchsorted(dates, we, side="right")
    block = values[lo:hi]
    finite = np.isfinite(block)
    n = finite.sum(axis=0)
    if _config.PERSISTENCE_FIRST_LAST_NAV_ENABLED and block.shape[0]:
        # FND-0200 fixed in P2: return of the period, first and last observation inside the window
        cols = np.arange(block.shape[1])
        first = block[finite.argmax(axis=0), cols]
        last = block[block.shape[0] - 1 - finite[::-1].argmax(axis=0), cols]
        with np.errstate(all="ignore"):
            ret = (last / first) ** (12.0 / n) - 1.0
        ret[(n < min_obs) | ~np.isfinite(first) | (first <= 0)] = np.nan
        return ret
    with np.errstate(all="ignore"):
        mn = np.where(finite, block, np.inf).min(axis=0)
        mx = np.where(finite, block, -np.inf).max(axis=0)
        ret = (mx / mn) ** (12.0 / n) - 1.0
    ret[(n < min_obs) | ~np.isfinite(mn) | (mn <= 0)] = np.nan
    return ret


def alpha_persistence(
    nav: pd.DataFrame,
    nature: pd.Series,
    window_months: int = PERSISTENCE_WINDOW_MONTHS,
    step_months: int = PERSISTENCE_STEP_MONTHS,
    min_windows: int = PERSISTENCE_MIN_WINDOWS,
) -> dict:
    """Expanding alpha_persistence (share of rolling windows where the fund beats the mean of its nature,
    P2 persistence.compute_persistence) and alpha_persistence_n (windows evaluated).

    Windows start at the fund's first observation and step `step_months`; the window ending at we is
    scored once (its outcome only uses NAV in [ws, we]) and counts from the first observation date >= we.
    Needs `window_months + step_months` observations and `min_windows` scored windows at t.
    Returns {alpha_persistence, alpha_persistence_n} on `nav`'s index/columns."""
    dates = nav.index.to_numpy()
    values = nav.to_numpy(dtype=float)
    min_obs = window_months - 3
    out_p = np.full(nav.shape, np.nan)
    out_n = np.full(nav.shape, np.nan)
    nat = nature.reindex(nav.columns)
    peers_of = {n: np.flatnonzero((nat == n).to_numpy()) for n in nat.dropna().unique()}
    window_cache: dict = {}

    for j, isin in enumerate(nav.columns):
        if pd.isna(nat.iloc[j]):
            continue
        col = values[:, j]
        pos = np.flatnonzero(np.isfinite(col))
        if len(pos) < window_months + step_months:
            continue
        fdates = nav.index[pos]
        fvals = col[pos]
        start, last = fdates[0], fdates[-1]
        group = peers_of[nat.iloc[j]]

        ends, beat, counted = [], [], []
        ws = start
        while True:
            we = ws + pd.DateOffset(months=window_months)
            if we > last:
                break
            lo = np.searchsorted(fdates.to_numpy(), ws.to_datetime64(), side="left")
            hi = np.searchsorted(fdates.to_numpy(), we.to_datetime64(), side="right")
            nw = hi - lo
            if nw >= min_obs:
                ret_f = (fvals[hi - 1] / fvals[lo]) ** (12.0 / nw) - 1.0
                key = (nat.iloc[j], ws, we)
                if key not in window_cache:
                    window_cache[key] = _peer_window_returns(values[:, group], dates, ws.to_datetime64(),
                                                             we.to_datetime64(), min_obs)
                peer_ret = window_cache[key]
                mine = np.where(group == j)[0]
                keep = np.isfinite(peer_ret)
                if len(mine):
                    keep[mine] = False                                   # exclude the fund itself
                if keep.any():
                    cat = float(peer_ret[keep].mean())
                    ends.append(we)
                    counted.append(True)
                    beat.append(ret_f > cat)
                else:
                    ends.append(we); counted.append(False); beat.append(False)
            else:
                ends.append(we); counted.append(False); beat.append(False)
            ws = ws + pd.DateOffset(months=step_months)

        if not ends:
            continue
        ends = np.array(ends, dtype="datetime64[ns]")
        c_cum = np.cumsum(counted)
        b_cum = np.cumsum(np.array(beat) & np.array(counted))
        t_idx = np.searchsorted(ends, fdates.to_numpy(), side="right") - 1      # windows with end <= t
        has = t_idx >= 0
        ti = np.clip(t_idx, 0, None)
        total = np.where(has, c_cum[ti], 0)
        wins = np.where(has, b_cum[ti], 0)
        ok = (np.arange(1, len(pos) + 1) >= window_months + step_months) & (total >= min_windows)
        out_p[pos[ok], j] = wins[ok] / total[ok]
        out_n[pos[ok], j] = total[ok]
    return {
        "alpha_persistence": pd.DataFrame(out_p, index=nav.index, columns=nav.columns),
        "alpha_persistence_n": pd.DataFrame(out_n, index=nav.index, columns=nav.columns),
    }
