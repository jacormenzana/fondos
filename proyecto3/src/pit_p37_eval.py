# proyecto3/src/pit_p37_eval.py
# -*- coding: utf-8 -*-
"""Package L, P3-7 (doc/decisiones/PREREG_P3_7.md sections 5 and 11): the evaluation of one arm against the baseline and the matched pools. PURE (pandas / numpy; the helpers are those of P3-5A, one definition).

An arm is a CANDIDATE for the forward shadow (R7: history never adopts) only if all four hold, every number at the owner's fees (the stated fees, fund by fund) unless said:
  1  drawdown, RELATIVE under the stress (D3): the unstressed drawdown at the flat 25 bp is within 15 % over the full sample and in each informative episode, and the stressed worst window (primary
     stress of P3-5A, unchanged) is no deeper than the baseline's by more than 0.5 pp, over the full sample and in each informative episode;
  2  improvement: a Holm-adjusted lower bound (family of 8, level 0.00625) above 0 AND a point estimate of at least 0.5 pp a year on the paired monthly net-return difference to the baseline;
  3  no collateral damage: non-crisis degradation at most 0.35 pp a year and the share of 36-month windows beating the target not lower than the baseline's;
  4  both matched pools beaten (D2): the persistent pool at the arm's band AND the monthly redraw, each costed on its own turnover, 20 draws each, read on the draw average.
The Amendment 5 side verdict (`amendment5_verdict`) applies the rules of Amendment 5 of PREREG_FND-0193 AS WRITTEN to a band-only arm; it is reported next to the candidate flag and never mixed with it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from proyecto3.src import pit_benchmarks as B
from proyecto3.src import pit_p35a_eval as E
from proyecto3.src.pit_backtest import monthly_turnover, portfolio_returns, series_stats
from proyecto3.src.pit_cost_check import fee_cost_series

DD_CAP = -0.15
BPS = 25.0
STRESS_SHARE = 0.10                 # a x k = 5 % x 2: the primary stress of P3-5A, unchanged
MATERIALITY_PP, DEGRADE_MAX_PP = 0.5, 0.35
STRESS_REL = 0.005                  # D3: 0.5 pp
# Amendment 5 of PREREG_FND-0193, the parameter table (values as committed; NOT re-synchronised by P3-7, section 11 item 2)
A5 = {"turnover_reduction_min": 0.30, "cost_drag_reduction_min_pp": 0.50, "net_return_tolerance_pp": 0.10, "max_dd_tolerance": 0.01, "mandate_dd_cap": -0.15, "net_return_tolerance_25bp_pp": 0.15}


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# pools
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def pool_series(W: pd.DataFrame, R1: pd.DataFrame, cash: pd.Series, entry: pd.Series, exit_: pd.Series, default_entry: float, default_exit: float, bps: float = BPS) -> pd.DataFrame:
    """gross, turnover, net25 and netfee of a weight matrix: the arithmetic the champion's series use (net25 = gross - bps x turnover; netfee = gross - the stated entry and exit fee of every fund traded)."""
    gross, _ = portfolio_returns(W, R1, cash)
    turnover = monthly_turnover(W)
    cost, _miss = fee_cost_series(W, entry, exit_, default_entry, default_exit)
    return pd.DataFrame({"gross": gross, "turnover": turnover, "net25": gross - bps / 1e4 * turnover, "netfee": gross - cost})


def pool_comparison(champion_ann: float, draw_anns) -> dict:
    """The champion's annualised net return against the draws of one pool: the pool's mean, the share of draws the champion beats, and `beats_mean` (the champion is above the average draw).
    A pool with no usable draw is never beaten (nothing to compare with is not a win)."""
    a = np.asarray(draw_anns, dtype=float)
    a = a[np.isfinite(a)]
    if not len(a) or not np.isfinite(champion_ann):
        return {"pool_mean_ann": float("nan"), "pool_min_ann": float("nan"), "pool_max_ann": float("nan"), "share_draws_beaten": float("nan"), "beats_mean": False, "n_draws": int(len(a))}
    return {"pool_mean_ann": float(a.mean()), "pool_min_ann": float(a.min()), "pool_max_ann": float(a.max()), "share_draws_beaten": float((champion_ann > a).mean()),
            "beats_mean": bool(champion_ann > a.mean()), "n_draws": int(len(a))}


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# the arm
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def _dd_blocks(series: pd.Series, dd_stress: np.ndarray, first, last) -> tuple:
    """(unstressed window drawdown, worst stressed window drawdown over windows STARTING in [first, last])."""
    un = E.window_dd(series, first, last)
    sel = (series.index >= first) & (series.index <= last)
    d = dd_stress[sel]
    return un, (float(np.nanmin(d)) if np.isfinite(d).any() else float("nan"))


def evaluate_arm(name: str, t: dict, base: dict, target: pd.Series, pools: "dict | None", *, episodes: dict, design: tuple, crisis_windows, holm_level: float) -> dict:
    """One row of the adoption table. `t` / `base`: {"series", "stress" {"master_p10", "master_p05"}, "meta"}; `pools`: {"persistent": ann draws, "redraw": ann draws, ...} of the net return at the stated fees,
    or None (conditions 4 and the candidate flag are then None)."""
    s, b = t["series"], base["series"]
    at = s.index
    is_base = name == "T0_baseline"
    res = {"trial": name}
    for tag, col in (("25bp", "net25"), ("fee", "netfee")):
        r = s[col]
        res[f"ann_{tag}"] = E.ann_return(r.dropna().to_numpy())
        res[f"maxdd_{tag}"] = E.max_dd(r.dropna().to_numpy())
        res[f"maxdd_repo_{tag}"] = series_stats(r, s["cash"])["max_drawdown"]
    res["turnover"] = float(s["turnover"].mean())
    res["cost_drag_pp"] = float((s["gross"] - s["netfee"]).mean() * 12 * 100)
    # ---- condition 1: unstressed within the cap, stressed relative to the baseline, full sample and each episode ----
    st = {tag: E.stressed_window_dds(s[col].to_numpy(), t["stress"]["master_p10"], STRESS_SHARE) for tag, col in (("25bp", "net25"), ("fee", "netfee"))}
    stb = E.stressed_window_dds(b["net25"].to_numpy(), base["stress"]["master_p10"], STRESS_SHARE)
    res["stressed_worst_25bp"], res["stressed_worst_fee"] = float(np.nanmin(st["25bp"])), float(np.nanmin(st["fee"]))
    res["stressed_worst_25bp_baseline"] = float(np.nanmin(stb))
    rel_ok = [res["stressed_worst_25bp"] - res["stressed_worst_25bp_baseline"] >= -STRESS_REL - 1e-12]
    dd_ok = [res["maxdd_25bp"] >= DD_CAP]
    for label, (lo, hi, rec) in {"design": (design[0], design[1], 0), **{k: (v[0], v[1], 12) for k, v in episodes.items()}}.items():
        first, last = E.episode_formation_range(lo, hi, rec)
        un, stz = _dd_blocks(s["net25"], st["25bp"], first, last)
        _unb, stzb = _dd_blocks(b["net25"], stb, first, last)
        res[f"dd_{label}_25bp"], res[f"dds_{label}_25bp"], res[f"dds_{label}_25bp_baseline"] = un, stz, stzb
        _u2, stz_fee = _dd_blocks(s["netfee"], st["fee"], first, last)
        res[f"dds_{label}_fee"] = stz_fee
        if label != "design":                                                       # the informative episodes; the design window is reported, not a gate
            dd_ok.append(bool(un >= DD_CAP))
            rel_ok.append(bool(stz - stzb >= -STRESS_REL - 1e-12))
    res["dd_cap_ok"], res["stress_rel_ok"] = bool(all(dd_ok)), bool(all(rel_ok))
    res["c1"] = bool(res["dd_cap_ok"] and res["stress_rel_ok"])
    res["holding_months"] = t["meta"].get("holding", {}).get("mean_months", float("nan"))
    if is_base:
        return res
    # ---- condition 2: Holm-adjusted lower bound above 0 and a point estimate of at least 0.5 pp a year, at the stated fees ----
    ni = E.noninferiority((s["netfee"] - b["netfee"]).to_numpy(), holm_level, 0.0)
    res.update({f"ret_{k}": v for k, v in ni.items()})
    res["c2"] = bool(ni["pass"] and ni["mean_pp"] >= MATERIALITY_PP)
    ni25 = E.noninferiority((s["net25"] - b["net25"]).to_numpy(), holm_level, 0.0)
    res.update({f"ret25_{k}": v for k, v in ni25.items()})                          # the flat-25 bp cross-check, reported
    # ---- condition 3 ----
    mask = ~E.crisis_mask(at, crisis_windows)
    d = (s["netfee"] - b["netfee"])[mask].dropna()
    res["noncrisis_degradation_pp"] = float(-d.mean() * 12 * 100)
    sh_v = B.window_summary(B.rolling_excess(s["netfee"].dropna(), target, 36))["share_above"]
    sh_b = B.window_summary(B.rolling_excess(b["netfee"].dropna(), target, 36))["share_above"]
    res["share36_variant"], res["share36_baseline"] = sh_v, sh_b
    res["c3"] = bool(res["noncrisis_degradation_pp"] <= DEGRADE_MAX_PP and sh_v >= sh_b - 1e-12)
    # ---- condition 4: both matched pools beaten ----
    if pools is None:
        res["c4"], res["candidate"] = None, None
        return res
    for kind in ("persistent", "redraw"):
        cmp_ = pool_comparison(res["ann_fee"], pools[kind])
        res.update({f"{kind}_{k}": v for k, v in cmp_.items()})
        res[f"beat_{kind}"] = cmp_["beats_mean"]
        res[f"{kind}_turnover"] = pools.get(f"{kind}_turnover", float("nan"))
    res["c4"] = bool(res["beat_persistent"] and res["beat_redraw"])
    res["candidate"] = bool(res["c1"] and res["c2"] and res["c3"] and res["c4"])
    return res


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Amendment 5 of PREREG_FND-0193, applied as written to a band-only arm
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def amendment5_verdict(s_v: pd.DataFrame, s_b: pd.DataFrame) -> dict:
    """Rules 1-3 of the Amendment 5 decision rule for a variant V against the baseline B on the same history: (1) turnover reduction >= 30 % and cost-drag reduction >= 0.50 pp a year at the stated fees;
    (2) net annual return at the stated fees no lower than the baseline's minus 0.10 pp, drawdown not worse by more than 1.0 pp and no deeper than 15 % (when the baseline itself is deeper: not worse);
    (3) at 25 bp the net return no lower than the baseline's minus 0.15 pp, and at 100 bp higher. The rule E2 (the best Sharpe among qualifying variants) is not evaluated here: it ranks several variants."""
    cols = ["gross", "turnover", "net25", "netfee"]
    ok = s_v[cols].notna().all(axis=1) & s_b[cols].notna().all(axis=1)           # the last date has no forward return: only the months both series have, the same months for both
    s_v, s_b = s_v.loc[ok], s_b.loc[ok]
    tu_v, tu_b = float(s_v["turnover"].mean()), float(s_b["turnover"].mean())
    cut = 1.0 - tu_v / tu_b if tu_b > 0 else float("nan")
    drag_v, drag_b = float((s_v["gross"] - s_v["netfee"]).mean() * 12 * 100), float((s_b["gross"] - s_b["netfee"]).mean() * 12 * 100)
    ann_v, ann_b = E.ann_return(s_v["netfee"].to_numpy()), E.ann_return(s_b["netfee"].to_numpy())
    dd_v, dd_b = E.max_dd(s_v["netfee"].to_numpy()), E.max_dd(s_b["netfee"].to_numpy())
    a25_v, a25_b = E.ann_return(s_v["net25"].to_numpy()), E.ann_return(s_b["net25"].to_numpy())
    a100_v, a100_b = E.ann_return((s_v["gross"] - 0.01 * s_v["turnover"]).to_numpy()), E.ann_return((s_b["gross"] - 0.01 * s_b["turnover"]).to_numpy())
    cap_ok = (dd_v >= A5["mandate_dd_cap"]) if dd_b >= A5["mandate_dd_cap"] else (dd_v >= dd_b - 1e-12)
    r1 = bool(cut >= A5["turnover_reduction_min"] and (drag_b - drag_v) >= A5["cost_drag_reduction_min_pp"])
    r2 = bool((ann_v - ann_b) * 100 >= -A5["net_return_tolerance_pp"] and dd_v >= dd_b - A5["max_dd_tolerance"] and cap_ok)
    r3 = bool((a25_v - a25_b) * 100 >= -A5["net_return_tolerance_25bp_pp"] and a100_v > a100_b)
    return {"turnover_cut": float(cut), "drag_cut_pp": drag_b - drag_v, "ret_diff_pp": (ann_v - ann_b) * 100, "maxdd_fee": dd_v, "maxdd_fee_baseline": dd_b,
            "ret25_diff_pp": (a25_v - a25_b) * 100, "ret100_diff_pp": (a100_v - a100_b) * 100, "rule1": r1, "rule2": r2, "rule3": r3, "pass": bool(r1 and r2 and r3)}
