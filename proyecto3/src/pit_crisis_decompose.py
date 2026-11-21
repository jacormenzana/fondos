# proyecto3/src/pit_crisis_decompose.py
# -*- coding: utf-8 -*-
"""Pure analysis pieces of the turning-point diagnostic, D1 and D2 (FND-0435; doc/backlog/PKG_L_p3_alignment.md). READ-ONLY, no database, no formula change (R-7).

What is here and why.
  `score_components`   the base score of `fund_scorer.compute_base_scores` split into one column per metric (weight x within-nature percentile) and the nature bonus. It reuses the scorer's own
                       normaliser, weights and bonus (P#11) and is pinned equal to `compute_base_scores` by a test, so a contribution is the score and not an approximation of it.
  `contribution_gap`   D1: which metric carries the selected funds above the pool: per-metric contribution of the selected funds against the median of the pool (the eligible candidates).
  `independent_filters` D2: every Layer-1 hard filter that the metrics can decide, evaluated INDEPENDENTLY (the scorer reports only the first failing one).
  `select_master`      the selection and blend of one date, through the very functions the PIT backtest uses (`candidates_by_sub`, `select_and_weight`, `_blend_to_master`).
  `trailing_return` / `forward_path`   realised NAV outcomes read from the NAV panel; forward numbers are HINDSIGHT and are labelled so wherever they are printed.
Definitions are written here before any number of a design date is read and do not change afterwards (R9, FROZEN).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from proyecto3.src import fund_scorer as fs

METRICS = ("return_ann_real", "sharpe", "max_dd", "alpha_persistence", "capture_ratio", "momentum_rank")
SUB_NATURES = fs.SUBPORTFOLIO_MAPPING


def sub_frame(frame: pd.DataFrame, sub: str) -> pd.DataFrame:
    """The rows `score_funds_from_df` scores for one sub-portfolio: every fund whose nature maps to it (eligible or not)."""
    return frame[frame["Fund_Nature"].isin(SUB_NATURES[sub])]


def sub_weights(sub: str) -> dict:
    """The base-score weights `compute_base_scores` applies to `sub` (short-horizon ones only while that switch is on)."""
    w = dict(fs.SUBPORTFOLIO_WEIGHTS.get(sub, fs.BASE_WEIGHTS))
    if fs.SHORT_HORIZON_SCORING_ENABLED:
        w.update(fs.SHORT_HORIZON_WEIGHTS.get(sub, {}))
    return w


def score_components(frame: pd.DataFrame, sub: str, weights: "dict | None" = None, bonus: "dict | None" = None) -> pd.DataFrame:
    """Per fund of `sub`: one column per weighted metric (weight x within-nature percentile; 0 when the fund lacks the metric or no fund of its nature has it, as the scorer does), `bonus`
    (the nature multiplier) and `base` = sum of the columns x bonus. `weights` / `bonus` default to the live ones; the counterfactuals pass their own."""
    weights = sub_weights(sub) if weights is None else dict(weights)
    bonus = dict(fs.NATURE_PROFILE_BONUS.get(sub, {})) if bonus is None else dict(bonus)
    sub_df = sub_frame(frame, sub)
    cols = {m: pd.Series(0.0, index=sub_df.index) for m in weights}
    for nature in sub_df["Fund_Nature"].dropna().unique():
        idx = sub_df.index[sub_df["Fund_Nature"] == nature]
        block = sub_df.loc[idx]
        for metric, weight in weights.items():
            if metric not in block.columns:
                continue
            col = block[metric].dropna()
            if col.empty:
                continue
            cols[metric].loc[col.index] = (fs._normalize_metric(col) * weight).to_numpy()
    out = pd.DataFrame(cols)
    out["bonus"] = sub_df["Fund_Nature"].map(bonus).astype(float).where(sub_df["Fund_Nature"].isin(list(bonus)), 1.0)
    out["base"] = out[list(weights)].sum(axis=1) * out["bonus"]
    return out


def contribution_gap(frame: pd.DataFrame, sub: str, selected, pool) -> pd.DataFrame:
    """D1. For each metric: mean contribution (weight x percentile x bonus) of the `selected` funds, median contribution of the `pool` funds, their gap, the gap's share of the total gap, and the
    raw metric (median of selected, median of pool). The row `total` is the base score itself. `selected` / `pool`: ISINs (the pool = the eligible candidates of the date)."""
    comp = score_components(frame, sub)
    w = [m for m in comp.columns if m not in ("bonus", "base")]
    scaled = comp[w].mul(comp["bonus"], axis=0)
    sel = [i for i in selected if i in scaled.index]
    pol = [i for i in pool if i in scaled.index]
    rows = []
    for m in w:
        raw = frame[m] if m in frame.columns else pd.Series(np.nan, index=frame.index)
        rows.append({"metric": m, "weight": sub_weights(sub)[m], "selected_mean": float(scaled.loc[sel, m].mean()), "pool_median": float(scaled.loc[pol, m].median()),
                     "raw_selected_median": float(raw.reindex(sel).median()), "raw_pool_median": float(raw.reindex(pol).median())})
    out = pd.DataFrame(rows)
    out["gap"] = out["selected_mean"] - out["pool_median"]
    total = out["gap"].sum()
    out["share_of_gap"] = out["gap"] / total if total else np.nan
    out.loc[len(out)] = {"metric": "total", "weight": sum(sub_weights(sub).values()), "selected_mean": float(comp.loc[sel, "base"].mean()), "pool_median": float(comp.loc[pol, "base"].median()),
                         "raw_selected_median": np.nan, "raw_pool_median": np.nan, "gap": float(comp.loc[sel, "base"].mean() - comp.loc[pol, "base"].median()), "share_of_gap": np.nan}
    return out


def nature_bonus_effect(frame: pd.DataFrame, sub: str, selected) -> dict:
    """How much of the selected funds' score the nature bonus adds: mean base score with the live bonus against the same funds with bonus 1.0 (percentiles untouched)."""
    comp = score_components(frame, sub)
    sel = [i for i in selected if i in comp.index]
    plain = comp.loc[sel, [c for c in comp.columns if c not in ("bonus", "base")]].sum(axis=1)
    return {"with_bonus": float(comp.loc[sel, "base"].mean()), "without_bonus": float(plain.mean())}


def independent_filters(frame: pd.DataFrame, sub: str) -> pd.DataFrame:
    """D2. The Layer-1 hard filters that the metrics decide, each evaluated on its own for every fund of `sub` (True = the fund FAILS it): `f_max_drawdown`, `f_return_real`, and for Defensiva
    `f_srri` and `f_high_yield`. The short gates need daily NAV and are NOT here (the sealed first-failing reason is the only trace of them). Missing metrics never fail (fail-open, as live)."""
    df = sub_frame(frame, sub)
    out = pd.DataFrame(index=df.index)
    dd_limit = fs.MAX_DRAWDOWN_BY_SUB.get(sub, fs.MAX_DRAWDOWN_LIMIT)
    ret_limit = fs.MIN_REAL_RETURN_BY_SUB.get(sub, fs.MIN_REAL_RETURN)
    out["f_max_drawdown"] = df["max_dd"].lt(dd_limit)                                  # NaN compares False: a missing metric never fails
    out["f_return_real"] = df["return_ann_real"].lt(ret_limit)
    if sub == "Defensiva":
        out["f_srri"] = df["srri_nav"].gt(fs.MAX_SRRI_DEFENSIVE) if "srri_nav" in df.columns else False
        out["f_high_yield"] = df["Credit_Quality"].eq("High Yield") if "Credit_Quality" in df.columns else False
    return out.astype(bool)


def funnel_table(frame: pd.DataFrame, sub: str, sealed_eligible: pd.Series, sealed_reason: pd.Series) -> pd.DataFrame:
    """D2 funnel per nature of one sub-portfolio and date: funds scored; share failing each filter independently; share failing ONLY that filter (the removal that filter alone causes); share that
    fails none of the metric filters; share eligible in the sealed run; share whose sealed first reason is a short gate (not reproducible without daily NAV)."""
    flt = independent_filters(frame, sub)
    sub_df = sub_frame(frame, sub)
    rows = []
    for nat, idx in sub_df.groupby("Fund_Nature").groups.items():
        f = flt.loc[idx]
        n = len(idx)
        only = {c: float((f[c] & (f.drop(columns=c).sum(axis=1) == 0)).mean()) for c in f.columns}
        rows.append({"nature": nat, "n": n, **{c: float(f[c].mean()) for c in f.columns}, **{f"only_{c}": v for c, v in only.items()},
                     "pass_metric_filters": float((~f.any(axis=1)).mean()),
                     "eligible_sealed": float(sealed_eligible.reindex(idx).astype(float).mean()),
                     "short_gate_first": float(sealed_reason.reindex(idx).map(lambda r: isinstance(r, str) and r.startswith("short_")).mean())})
    return pd.DataFrame(rows).set_index("nature")


def select_master(scores_t: pd.DataFrame, attrs: pd.DataFrame, fx_exposed, sub_w: dict, constraints=None) -> tuple:
    """(selection per sub, master weights {isin: weight}) of one date, through the functions `pit_backtest.build_weights` calls (no incumbents, no rebalance cadence)."""
    from proyecto3.src.backtesting import _blend_to_master
    from proyecto3.src.pit_backtest import candidates_by_sub
    from proyecto3.src.portfolio_engine import DEFAULT_CONSTRAINTS, select_and_weight
    from proyecto3.src.score_candidates import exposure_constrained
    cons = DEFAULT_CONSTRAINTS if constraints is None else constraints
    if fx_exposed is not None:
        cons = exposure_constrained(cons)
    selection = select_and_weight(candidates_by_sub(scores_t, attrs, fx_exposed), sub_w, cons, None)
    return selection, _blend_to_master(selection, sub_w)


def trailing_return(nav: pd.DataFrame, t, isins, months: int = 36) -> pd.Series:
    """Nominal return of each fund over the `months` ending at month-end `t` (NaN when either end is missing): the 'trailing 3-year' look-back of a selected fund."""
    t = pd.Timestamp(t)
    prev = t - pd.DateOffset(months=months) + pd.offsets.MonthEnd(0)
    cols = [i for i in isins if i in nav.columns]
    if t not in nav.index or prev not in nav.index:
        return pd.Series(np.nan, index=list(isins), dtype=float)
    return (nav.loc[t, cols] / nav.loc[prev, cols] - 1).reindex(list(isins))


def forward_path(nav: pd.DataFrame, t, weights: pd.Series, months: int = 12) -> dict:
    """HINDSIGHT. Equal-or-given-weight portfolio path over the `months` after `t`: cumulative return at 3 / 6 / `months` and the worst peak-to-trough inside the window. A fund with a missing NAV
    anywhere in the window makes the portfolio's path NaN for the window it is missing, and `valid_share` reports the weight that had a complete path (never silently dropped)."""
    t = pd.Timestamp(t)
    pos = nav.index.get_indexer([t])[0]
    cols = [i for i in weights.index if i in nav.columns]
    if pos < 0 or pos + months >= len(nav.index) or not cols:
        return {"ret_3m": np.nan, "ret_6m": np.nan, f"ret_{months}m": np.nan, "max_dd": np.nan, "valid_share": np.nan}
    window = nav.iloc[pos:pos + months + 1][cols]
    complete = window.notna().all()
    w = weights.reindex(cols)
    share = float(w[complete].sum() / w.sum())
    if not complete.any():
        return {"ret_3m": np.nan, "ret_6m": np.nan, f"ret_{months}m": np.nan, "max_dd": np.nan, "valid_share": 0.0}
    rel = window.loc[:, complete] / window.loc[:, complete].iloc[0]
    wn = w[complete] / w[complete].sum()
    path = rel.mul(wn, axis=1).sum(axis=1)
    return {"ret_3m": float(path.iloc[3] - 1), "ret_6m": float(path.iloc[6] - 1), f"ret_{months}m": float(path.iloc[-1] - 1),
            "max_dd": float((path / path.cummax() - 1).min()), "valid_share": share}


# ============================================================
# Counterfactuals (FROZEN definitions: written before any design-date number is read, never tuned afterwards)
# ============================================================
VARIANT_WEIGHT_ZERO = tuple(f"w0_{m}" for m in METRICS)
FROZEN_VARIANTS = VARIANT_WEIGHT_ZERO + ("bonus_1", "roll36", "roll60", "dd_halflife36", "short_gates_off", "dd_filter_off")
_METRIC_REASONS = ("max_drawdown", "return_ann_real", "srri", "Credit_Quality")
HIGH_BETA_NATURE = "Renta Variable"
CREDIT_NATURES = ("Renta Fija Corto Plazo", "Renta Fija Flexible")


def halflife_drawdown(nav: pd.DataFrame, t, half_life_months: int = 36) -> pd.Series:
    """Drawdown with a half-life: for every fund, the worst of (underwater depth at month s) x 0.5 ** ((t - s) / half_life_months) over the months s <= t, where the depth at s is nav_s / max(nav_<=s) - 1.
    A drawdown `half_life_months` old counts half. Depth is <= 0, so the result is <= 0 and comparable with `max_dd` (which it equals when every drawdown is recent)."""
    t = pd.Timestamp(t)
    hist = nav.loc[:t]
    depth = hist / hist.cummax() - 1
    age = pd.Series(np.arange(len(hist) - 1, -1, -1), index=hist.index, dtype=float)
    return depth.mul(0.5 ** (age / half_life_months), axis=0).min()


def variant_setup(variant: str, sub: str, frame: pd.DataFrame, t, trailing: "dict | None" = None, nav: "pd.DataFrame | None" = None):
    """(frame for the variant, weights, bonus, short_gates_off, dd_filter_off). `trailing`: {36: trailing_risk_metrics dict, 60: ...} for the rolling variants; `nav` for the half-life."""
    weights, bonus, f = sub_weights(sub), dict(fs.NATURE_PROFILE_BONUS.get(sub, {})), frame
    if variant.startswith("w0_"):
        weights[variant[3:]] = 0.0
    elif variant == "bonus_1":
        bonus = {}
    elif variant in ("roll36", "roll60"):
        tr = trailing[int(variant[4:])]
        f = frame.copy()
        for m in ("return_ann_real", "sharpe", "max_dd"):
            f[m] = tr[m].loc[pd.Timestamp(t)].reindex(frame.index)
    elif variant == "dd_halflife36":
        f = frame.copy()
        f["max_dd"] = halflife_drawdown(nav, t, 36).reindex(frame.index)
    elif variant not in ("baseline", "short_gates_off", "dd_filter_off"):
        raise ValueError(f"unknown variant {variant!r}")
    return f, weights, bonus, variant == "short_gates_off", variant == "dd_filter_off"


def variant_rows(frame: pd.DataFrame, sealed_block: pd.DataFrame, sub: str, variant: str, t, trailing=None, nav=None) -> pd.DataFrame:
    """The scored rows of one sub-portfolio under a variant, before the family de-duplication. Eligibility = no metric filter fails (re-evaluated on the variant's metrics, except for
    `dd_filter_off` which drops that filter) AND the sealed run did not exclude the fund for a NON-metric reason (stale NAV, data hold / suspicion, NAV gap, wrong document: not re-derivable here)
    AND the sealed run did not exclude it at a short gate (not re-derivable without daily NAV; lifted by `short_gates_off`). LIMIT: a fund the sealed run excluded for a metric reason could also
    fail a short gate, which is unknown here (treated as passing)."""
    f, weights, bonus, short_off, dd_off = variant_setup(variant, sub, frame, t, trailing, nav)
    comp = score_components(f, sub, weights, bonus)
    flt = independent_filters(f, sub)
    if dd_off:
        flt["f_max_drawdown"] = False
    reason = sealed_block.set_index("isin")["exclusion_reason"].reindex(comp.index)
    lead = reason.map(lambda r: (r.split("=")[0].split(":")[0].split(" ")[0] if isinstance(r, str) else None))
    blocked_other = lead.notna() & ~lead.isin(_METRIC_REASONS) & ~lead.map(lambda x: isinstance(x, str) and x.startswith("short_"))
    blocked_short = lead.map(lambda x: isinstance(x, str) and x.startswith("short_")) & (not short_off)
    ok = ~flt.any(axis=1) & ~blocked_other & ~blocked_short
    sub_df = sub_frame(f, sub)
    return pd.DataFrame({"isin": comp.index, "fund_name": sub_df["Fund_Name"].to_numpy() if "Fund_Name" in sub_df.columns else "", "fund_nature": sub_df["Fund_Nature"].to_numpy(),
                         "fund_family_id": sub_df["fund_family_id"].to_numpy() if "fund_family_id" in sub_df.columns else None, "subportfolio": sub,
                         "score_base": comp["base"].to_numpy(), "score_final": np.where(ok, comp["base"], 0.0), "eligible": ok.to_numpy()}).reset_index(drop=True)


def variant_outcome(master: dict, attrs: pd.DataFrame, rows: pd.DataFrame, nav: pd.DataFrame, t) -> dict:
    """What a variant changed, measured on the master portfolio it selects: weight by nature, the high-beta share (Renta Variable), the Monetario share, the credit eligibility (share of the
    Defensiva funds of the credit natures that are eligible) and, as HINDSIGHT, the 12-month forward return and worst drawdown of that portfolio."""
    w = pd.Series(master, dtype=float)
    nat = attrs["Fund_Nature"].reindex(w.index)
    by_nature = w.groupby(nat).sum()
    cred = rows[(rows["subportfolio"] == "Defensiva") & rows["fund_nature"].isin(CREDIT_NATURES)]
    fp = forward_path(nav, t, w, 12) if len(w) else {"ret_12m": np.nan, "max_dd": np.nan, "valid_share": np.nan}
    return {"n_funds": int(len(w)), "high_beta_share": float(by_nature.get(HIGH_BETA_NATURE, 0.0)), "monetario_share": float(by_nature.get("Monetario", 0.0)),
            "credit_eligible_share": float(cred["eligible"].mean()) if len(cred) else float("nan"), "nature_mix": {k: round(float(v), 3) for k, v in by_nature.items()},
            "fwd12_HINDSIGHT": fp["ret_12m"], "fwd_maxdd_HINDSIGHT": fp["max_dd"], "fwd_valid_share": fp["valid_share"]}


def dedupe_by_family(rows: pd.DataFrame) -> pd.DataFrame:
    """The family de-duplication of `fund_scorer.deduplicate_by_family` without its per-family Python loop (that loop ran 13 variants x 18 dates x thousands of families): per (family,
    sub-portfolio) the row with the highest `score_final` stays (first in input order on a tie), rows without a family stay. Pinned equal to the scorer's function by a test."""
    fam = rows["fund_family_id"]
    no_family = fam.isna() | (fam.astype(str) == "")
    keep = no_family.copy()
    keyed = rows[~no_family]
    best = keyed.sort_values("score_final", ascending=False, kind="stable").drop_duplicates(["fund_family_id", "subportfolio"], keep="first")
    keep.loc[best.index] = True
    return rows[keep]
