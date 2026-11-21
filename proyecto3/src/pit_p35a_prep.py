# proyecto3/src/pit_p35a_prep.py
# -*- coding: utf-8 -*-
"""Pure pieces that prepare PREREG_P3_5A_DRAFT (Package L, session 2 of roadmap v5; doc/backlog/PKG_L_p3_alignment.md and HANDOFF_P3_SESSION2.md). READ-ONLY analysis helpers: pandas / numpy only,
no pipeline import, no database except the one explicit loader at the bottom (R-7). The command line that uses them is scripts/audit/p3/p3_p35a_prep.py.

  * L3 freeze guard: the parameters of the recovery trigger are frozen by a SHA-256 of their canonical text. The pool-drawdown count of the locked episodes refuses to run unless the
    contamination log holds a freeze line with the CURRENT digest and a timestamp that is not in the future (owner decision 4, 2026-11-21).
  * Pool drawdown of an episode: a function of the POOL return series only (no score, no selection, no lever output), so reading it does not leak what a lever would do.
  * Minimum detectable effect from the paired monthly differences of the band / cadence grid (a stationary block bootstrap of the MEAN, never a point estimate).
  * Brinson split on equal-weight MEANS (D1 compares a mean with a median, so an identity cannot be checked against D1; it is reconciled in the report) and the attribute split within nature.
  * Holding spells of the baseline, the L4 scale check, and a rolling drawdown for the lock-in horizon.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from statistics import NormalDist

import numpy as np
import pandas as pd

from proyecto3.src.backtest_inference import stationary_bootstrap_indices

# ============================================================================================================================================================
# L3 freeze
# ============================================================================================================================================================
# The recovery trigger of lever L3 (plan rev. 3.1, section 4 and the owner's approval of 2026-11-21). The values were fixed BEFORE the pool-drawdown count of the locked episodes was run. Values are
# strings so that the canonical text, hence the digest, is exact. Changing ANY entry changes the digest and makes an earlier freeze line stop covering the text.
L3_PARAMS = {
    "version": "L3-v1",
    "state_source": "the pool series only; never the regime label",
    "pool": "equal-weight pool of the eligible funds of the date (unique ISIN)",
    "trailing_window_months": "12",
    "stress_threshold": "0.03",
    "stress_rule": "stress when the pool is more than stress_threshold below its trailing high of trailing_window_months",
    "recovery_return_window_months": "3",
    "recovery_confirmation_month_ends": "2",
    "recovery_min_rebound_from_low": "0.05",
    "recovery_rule": "recovery when stress AND the pool return over recovery_return_window_months is > 0 at recovery_confirmation_month_ends consecutive month-ends AND the pool is at least recovery_min_rebound_from_low above its trailing low of trailing_window_months",
    "design_window": "2007-11..2009-07",
    "max_complete_transitions_in_design_window": "4",
    "max_flips_per_year_after_r9_opens": "2",
    "modulation_nature_bonus": "in recovery b -> 1 + (b - 1) * k with k in {0.5, 0}",
    "modulation_max_dd_weight": "in recovery w_dd -> w_dd * k with k in {0.5, 0}; the other metric weights are rescaled proportionally to sum to 1",
}
FREEZE_TAG = "L3-FREEZE"
_FREEZE_RE = re.compile(r"^\s*(?:[-*]\s*)?" + FREEZE_TAG + r"\s+sha256=(\S+)\s+utc=(\S+)\s*$", re.MULTILINE)


class FreezeNotRecorded(Exception):
    """The contamination log holds no valid freeze line for the current L3 text."""


def l3_canonical_text() -> str:
    return "\n".join(f"{k}={L3_PARAMS[k]}" for k in sorted(L3_PARAMS))


def l3_digest() -> str:
    return hashlib.sha256(l3_canonical_text().encode("utf-8")).hexdigest()


def freeze_line(utc: "str | None" = None) -> str:
    """The line to write in the contamination log: `L3-FREEZE sha256=<digest> utc=<YYYY-MM-DDTHH:MM:SSZ>`."""
    stamp = utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{FREEZE_TAG} sha256={l3_digest()} utc={stamp}"


def assert_freeze_recorded(log_text: str, now: "datetime | None" = None) -> dict:
    """Return {"digest", "utc"} of the first freeze line of `log_text` that carries the CURRENT digest and a parsable timestamp not after `now`; raise FreezeNotRecorded otherwise."""
    now = now or datetime.now(timezone.utc)
    digest = l3_digest()
    problems = []
    for found_digest, stamp in _FREEZE_RE.findall(log_text or ""):
        if found_digest != digest:
            problems.append(f"digest {found_digest[:12]} is not the current {digest[:12]}")
            continue
        try:
            when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            problems.append(f"timestamp {stamp!r} is not YYYY-MM-DDTHH:MM:SSZ")
            continue
        if when > now:
            problems.append(f"timestamp {stamp} is in the future")
            continue
        return {"digest": found_digest, "utc": stamp}
    raise FreezeNotRecorded("no valid L3 freeze line in the contamination log" + (": " + "; ".join(problems) if problems else ""))


# ============================================================================================================================================================
# Pool drawdown of the episodes
# ============================================================================================================================================================
def master_pool_returns(scores: pd.DataFrame, fwd_returns: pd.DataFrame, at) -> pd.Series:
    """Per formation date, the equal-weight 1-month FORWARD return of the unique funds that were eligible then in ANY sub-portfolio (a fund without a return that month drops out of the mean, never
    filled). `scores`: as_of, isin, eligible; `fwd_returns`: formation dates x isin."""
    el = scores.loc[scores["eligible"].astype(bool), ["as_of", "isin"]].drop_duplicates()
    vals = {}
    for t, g in el.groupby("as_of"):
        if t in fwd_returns.index:
            vals[t] = fwd_returns.loc[t].reindex(g["isin"].unique()).mean()
    return pd.Series(vals, dtype=float).reindex(pd.DatetimeIndex(at))


def episode_pool_drawdown(pool_fwd: pd.Series, lo, hi) -> dict:
    """Maximum drawdown of the pool over the CALENDAR MONTHS of the window [lo, hi]. The return of a month is the forward return of the pool at the previous month-end; the level before the first
    month counts as the first peak. A window with a missing month is `complete` False and its drawdown NaN (never filled). Reads the pool series and nothing else."""
    months = pd.date_range(pd.Timestamp(lo) + pd.offsets.MonthEnd(0), pd.Timestamp(hi) + pd.offsets.MonthEnd(0), freq="ME")
    formation = months - pd.offsets.MonthEnd(1)
    r = pool_fwd.reindex(formation).to_numpy(dtype=float)
    complete = bool(len(r) > 0 and np.isfinite(r).all())
    if not complete:
        return {"months": int(len(months)), "pool_max_dd": float("nan"), "complete": False}
    level = np.concatenate(([1.0], np.cumprod(1.0 + r)))
    return {"months": int(len(months)), "pool_max_dd": float((level / np.maximum.accumulate(level) - 1.0).min()), "complete": True}


def pool_dd_count(pool_fwd: pd.Series, episodes: dict, gate: float = 0.04, recovery_months: int = 12) -> pd.DataFrame:
    """One row per episode {name: (first day, last day)}: its pool drawdown, whether it clears the informativeness `gate` (drawdown at least `gate` deep) and whether the data cover the
    `recovery_months` after the episode (a lever confirmation window). Only a drawdown and flags are returned."""
    last_return_end = pool_fwd.dropna().index.max() + pd.offsets.MonthEnd(1)
    rows = []
    for name, (lo, hi) in episodes.items():
        e = episode_pool_drawdown(pool_fwd, lo, hi)
        end = pd.Timestamp(hi) + pd.offsets.MonthEnd(0)
        rec = bool(end + pd.DateOffset(months=recovery_months) <= last_return_end)
        inf = bool(e["complete"] and e["pool_max_dd"] <= -gate)
        rows.append({"episode": name, **e, "informative": inf, "recovery_available": rec, "informative_with_recovery": bool(inf and rec)})
    return pd.DataFrame(rows)


# ============================================================================================================================================================
# Minimum detectable effect
# ============================================================================================================================================================
def paired_se_annual_pp(diff, n_boot: int = 4000, mean_block: float = 6.0, seed: int = 12345, return_n: bool = False):
    """Standard error, in percentage points a year, of the MEAN of a monthly paired difference series (annualised as 12 x the monthly mean), by a stationary block bootstrap. Non-finite months are
    dropped and counted. Never returns the point estimate of the difference."""
    a = np.asarray(diff, dtype=float)
    clean = a[np.isfinite(a)]
    if len(clean) < 2:
        raise ValueError("need at least 2 finite monthly differences")
    idx = stationary_bootstrap_indices(len(clean), n_boot, mean_block, seed)
    se = float(clean[idx].mean(axis=1).std(ddof=1) * 12 * 100)
    return {"se_pp": se, "n": int(len(clean)), "dropped": int(len(a) - len(clean))} if return_n else se


def mde_pp(se_pp: float, alpha: float, power: float = 0.80) -> float:
    """Minimum detectable effect (pp a year) of a ONE-sided test at level `alpha` with the given power: (z(1 - alpha) + z(power)) * se."""
    z = NormalDist().inv_cdf
    return float((z(1 - alpha) + z(power)) * se_pp)


# ============================================================================================================================================================
# Brinson split on equal-weight means
# ============================================================================================================================================================
def _valid_weights(w: pd.Series, ret: pd.Series) -> "tuple[pd.Series, float]":
    ok = ret.reindex(w.index).notna()
    share = float(w[ok].sum() / w.sum()) if w.sum() > 0 else float("nan")
    kept = w[ok]
    return (kept / kept.sum() if kept.sum() > 0 else kept), share


def _label(s: pd.Series, index) -> pd.Series:
    return s.reindex(index).astype(object).where(s.reindex(index).notna(), "NA")


def brinson(sel_w: pd.Series, pool_w: pd.Series, ret: pd.Series, grp: pd.Series) -> dict:
    """Allocation + selection + interaction = gap, an IDENTITY on weighted MEANS. `sel_w` / `pool_w`: weights by ISIN (the selected funds must be inside the pool); `ret`: the outcome by ISIN
    (a fund with no outcome is dropped from both sides and the weights renormalised; the kept shares are reported); `grp`: the group label by ISIN (a missing label is its own group "NA").
    gap = mean outcome of the selection - mean outcome of the pool. A group the selection does not hold contributes allocation only (its selected-cell return is set to the pool's)."""
    if not set(sel_w.index) <= set(pool_w.index):
        raise ValueError("the selected funds must be inside the pool")
    p, vp = _valid_weights(pool_w, ret)
    s, vs = _valid_weights(sel_w, ret)
    gp, gs = _label(grp, p.index), _label(grp, s.index)
    r_p_total = float((p * ret.reindex(p.index)).sum())
    rows = []
    for g in sorted(set(gp) | set(gs), key=str):
        wp, ws = float(p[gp == g].sum()), float(s[gs == g].sum())
        rp = float((p[gp == g] * ret.reindex(p[gp == g].index)).sum() / wp) if wp > 0 else float("nan")
        rs = float((s[gs == g] * ret.reindex(s[gs == g].index)).sum() / ws) if ws > 0 else rp
        alloc = (ws - wp) * (rp - r_p_total) if wp > 0 else 0.0
        sel = wp * (rs - rp) if wp > 0 else 0.0
        inter = (ws - wp) * (rs - rp) if wp > 0 else 0.0
        rows.append({"group": g, "w_selected": ws, "w_pool": wp, "r_selected": rs, "r_pool": rp, "allocation": alloc, "selection": sel, "interaction": inter})
    by = pd.DataFrame(rows)
    gap = float((s * ret.reindex(s.index)).sum() - r_p_total)
    return {"gap": gap, "allocation": float(by["allocation"].sum()), "selection": float(by["selection"].sum()), "interaction": float(by["interaction"].sum()),
            "valid_share_selected": vs, "valid_share_pool": vp, "by_group": by}


def attribute_mix_within_nature(sel_w: pd.Series, pool_w: pd.Series, ret: pd.Series, nature: pd.Series, attr: pd.Series) -> dict:
    """Split of the WITHIN-NATURE part of the gap, sum over natures of w_S,g * (r_S,g - r_P,g), into the mix of one attribute inside each nature (valued at the pool's cell returns) and the
    selection left inside the (nature, attribute) cells. attribute_mix + cell_selection = within_nature_total, an identity. A missing attribute is its own category "NA", reported."""
    if not set(sel_w.index) <= set(pool_w.index):
        raise ValueError("the selected funds must be inside the pool")
    p, _ = _valid_weights(pool_w, ret)
    s, _ = _valid_weights(sel_w, ret)
    nat_p, nat_s = _label(nature, p.index), _label(nature, s.index)
    at_p, at_s = _label(attr, p.index), _label(attr, s.index)
    mix = cell_sel = total = 0.0
    for g in sorted(set(nat_p) | set(nat_s), key=str):
        pg, sg = p[nat_p == g], s[nat_s == g]
        wp_g, ws_g = float(pg.sum()), float(sg.sum())
        if wp_g <= 0 or ws_g <= 0:
            continue
        r_p_g = float((pg * ret.reindex(pg.index)).sum() / wp_g)
        r_s_g = float((sg * ret.reindex(sg.index)).sum() / ws_g)
        total += ws_g * (r_s_g - r_p_g)
        for a in sorted(set(at_p[nat_p == g]) | set(at_s[nat_s == g]), key=str):
            pa, sa = pg[at_p[pg.index] == a], sg[at_s[sg.index] == a]
            wp_a, ws_a = float(pa.sum()), float(sa.sum())
            r_p_a = float((pa * ret.reindex(pa.index)).sum() / wp_a) if wp_a > 0 else float("nan")
            r_s_a = float((sa * ret.reindex(sa.index)).sum() / ws_a) if ws_a > 0 else r_p_a
            if wp_a > 0:
                mix += ws_g * (ws_a / ws_g - wp_a / wp_g) * (r_p_a - r_p_g)
                cell_sel += ws_a * (r_s_a - r_p_a)
    miss = float(p[at_p == "NA"].sum())
    return {"attribute_mix": float(mix), "cell_selection": float(cell_sel), "within_nature_total": float(total), "missing_attribute_share_pool": miss,
            "n_categories": int(len(set(at_p)))}


# ============================================================================================================================================================
# Holding spells, L4 scale, rolling drawdown
# ============================================================================================================================================================
def holding_spells(W: pd.DataFrame) -> dict:
    """Spell lengths (months held without a break) of a weights frame (dates x funds, NaN or 0 = not held). A spell still open on the last date is right-censored (counted in `mean_months`,
    excluded from `completed_mean_months`); a spell that starts on the first date is left-censored (kept, the run cannot see before it)."""
    held = (W.fillna(0.0).to_numpy(dtype=float) > 0)
    ended, open_ = [], []
    for j in range(held.shape[1]):
        run = 0
        for i in range(held.shape[0]):
            if held[i, j]:
                run += 1
            elif run:
                ended.append(run)
                run = 0
        if run:
            open_.append(run)
    every = np.asarray(ended + open_, dtype=float)
    return {"n_spells": int(len(every)), "mean_months": float(every.mean()) if len(every) else float("nan"),
            "median_months": float(np.median(every)) if len(every) else float("nan"),
            "completed_mean_months": float(np.mean(ended)) if ended else float("nan"), "n_censored": int(len(open_)), "n_completed": int(len(ended))}


def penalty_scale(ret_real: pd.Series, fees_total: pd.Series, h_years: float) -> dict:
    """L4 scale check inside ONE (date, nature) cell: the annual penalty (entry + exit) / H of each fund against the cell's spread of `return_ann_real`. An unknown fee takes the median of
    the known penalties of the cell and is counted (`n_unknown_fee`), never silently filled. `rank_change_share` = share of funds whose within-cell percentile moves by at least 0.10 when the
    penalty is deducted."""
    r = pd.to_numeric(ret_real, errors="coerce")
    f = pd.to_numeric(fees_total, errors="coerce").reindex(r.index)
    keep = r.notna()
    r, f = r[keep], f[keep]
    pen_known = f / h_years
    fill = float(pen_known.dropna().median()) if pen_known.notna().any() else float("nan")
    pen = pen_known.fillna(fill)
    iqr = float(r.quantile(0.75) - r.quantile(0.25)) if len(r) else float("nan")
    charging = pen_known[pen_known > 0]
    med = float(charging.median()) if len(charging) else float("nan")
    net = r - pen
    shift = (r.rank(pct=True) - net.rank(pct=True)).abs()
    return {"n": int(len(r)), "iqr": iqr, "n_charging": int(len(charging)), "n_unknown_fee": int(f.isna().sum()), "median_penalty_charging": med,
            "ratio_median_to_iqr": float(med / iqr) if iqr and np.isfinite(med) else float("nan"),
            "share_over_half_iqr": float((pen > 0.5 * iqr + 1e-12).mean()) if len(pen) and np.isfinite(iqr) else float("nan"),       # strictly above; 1e-12 absorbs the float noise of the quantiles
            "rank_change_share": float((shift >= 0.10 - 1e-12).mean()) if len(shift) else float("nan")}


def rolling_max_dd(nav_m: pd.DataFrame, t, months: "int | None") -> pd.Series:
    """Maximum drawdown of each fund over the last `months` month-end points up to `t` (NaN unless the window is full and complete: a hole is never filled), or since inception when `months` is
    None (needs at least 12 observed points). `nav_m`: month-end dates x ISIN."""
    w = nav_m.loc[:pd.Timestamp(t)]
    if months is not None:
        w = w.iloc[-months:]
        if len(w) < months:
            return pd.Series(np.nan, index=nav_m.columns, dtype=float)
        ok = w.notna().all()
    else:
        ok = w.notna().sum() >= 12
    dd = (w / w.cummax() - 1.0).min()
    return dd.where(ok)


# ============================================================================================================================================================
# D6 attributes: their OWN loader (never part of the frame that feeds the score cache key)
# ============================================================================================================================================================
D6_ATTRIBUTES = ("Geography", "Asset_Currency", "Hedging_Policy", "Style_Profile", "Sector_Focus", "Benchmark_Type")


def load_d6_attributes(conn) -> pd.DataFrame:
    """fund_master attributes used by the D6 attribution only (today's values: a documented look-ahead), read-only, ISIN index. Separate from the PIT attribute loader on purpose: that frame is
    hashed into the score cache keys, and a column added there would silently change every cached score."""
    cols = ", ".join(D6_ATTRIBUTES)
    rows = conn.execute(f"SELECT ISIN, {cols} FROM fund_master").fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["ISIN", *D6_ATTRIBUTES]).set_index("ISIN")
    df.index.name = "isin"
    return df
