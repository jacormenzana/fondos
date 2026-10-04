# proyecto3/src/pit_crisis_variants.py
# -*- coding: utf-8 -*-
"""
Sign / scale variants of the Crisis_Financiera multiplier, evaluated point in time -- FND-0224 phase B (FND-0225).

Why: in production the crisis legs (beta_vix > +0.02, beta_spread_hy > +0.02 / < -0.01) never fire (stored betas are about
1e-4, 40x inside the thresholds) and, in the data, the funds that lose in a crisis have the most NEGATIVE beta_vix, the
opposite side to the one the rule penalises (evidence in FND-0225; in-sample, so it must be confirmed here).

This module only EVALUATES candidate rules on the PIT run (read-only, nothing here touches the live scorer):

  sign_raw     malus when beta_vix < -VIX_CRISIS_THRESHOLD: the sign flip alone (expected to stay silent: scale);
  z_vix        malus when the cross-sectional z-score of beta_vix inside the (date, sub-portfolio) pool is < -z_cut;
  z_vix_resid  same on beta_vix after removing its dependence on the fund's own volatility (the incremental signal:
               beta_vix is tied to volatility, rho about -0.76, which the base score already sees);
  z_spread     same on beta_spread_hy (only funds that have it).

evaluate_crisis_variants answers, per variant and cut-off and for the 'crisis' dates (production semantics) and 'all'
dates (a power diagnostic, the rule applied everywhere): how many rows are flagged, the forward 12m return of flagged vs
unflagged funds, the rank information coefficient of the signal and of volatility alone, and the change in the
top-N-vs-pool selection effect when the malus is applied to the score.
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_candidates import asof_snapshot

logger = logging.getLogger(__name__)

# Production values of the retired crisis legs (fund_scorer, removed 2026-10-04, FND-0225), kept here only so the
# evaluation of the candidates stays reproducible: threshold of the sign-only variant and the malus applied to flagged funds.
VIX_CRISIS_THRESHOLD = 0.02
MULT_CRISIS_SPREAD_MALUS = 0.60

CRISIS_VARIANTS = ("sign_raw", "z_vix", "z_vix_resid", "z_spread")
Z_CUTS = (1.0, 1.5)
MIN_GROUP = 8                      # non-null betas needed in a pool before a cross-sectional rule is applied
CRISIS = "Crisis_Financiera"


def _nm(values) -> float:
    """Mean ignoring NaN; NaN (no warning) when nothing is finite."""
    a = np.asarray(values, dtype=float)
    return float(a[np.isfinite(a)].mean()) if np.isfinite(a).any() else np.nan


def _zscore(s: pd.Series) -> pd.Series:
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and np.isfinite(sd) and sd > 0 else s * 0.0


def signal(group: pd.DataFrame, variant: str) -> pd.Series:
    """Signal per fund where HIGH = good and LOW = penalised (NaN where not computable). group: one (date, sub)
    pool, index isin, columns beta_vix, beta_spread_hy, vol_ann."""
    if variant == "sign_raw":
        return group["beta_vix"]
    if variant == "z_vix":
        col = group["beta_vix"].dropna()
    elif variant == "z_spread":
        col = group["beta_spread_hy"].dropna()
    elif variant == "z_vix_resid":
        d = group[["beta_vix", "vol_ann"]].dropna()
        if len(d) < MIN_GROUP or d["vol_ann"].std(ddof=0) == 0:
            return pd.Series(np.nan, index=group.index)
        x = d["vol_ann"].to_numpy()
        slope, intercept = np.polyfit(x, d["beta_vix"].to_numpy(), 1)
        col = d["beta_vix"] - (intercept + slope * d["vol_ann"])
    else:
        raise ValueError(f"unknown variant {variant!r}; choose one of {CRISIS_VARIANTS}")
    if len(col) < MIN_GROUP:
        return pd.Series(np.nan, index=group.index)
    return _zscore(col).reindex(group.index)


def penalised(group: pd.DataFrame, variant: str, z_cut: float) -> pd.Series:
    """Boolean Series (False where the signal is missing)."""
    sig = signal(group, variant)
    cut = -VIX_CRISIS_THRESHOLD if variant == "sign_raw" else -z_cut
    return sig < cut                      # a comparison with NaN is False: no signal, no penalty


def forward_max_drawdown(nav: pd.DataFrame, at: pd.DatetimeIndex, months: int = 12, max_stale_days: int = 45) -> pd.DataFrame:
    """Worst peak-to-trough fall of each fund over the `months` after every date (month-end grid, as-of NAV), as a
    negative fraction (0 = no fall). NaN where any month of the window has no usable NAV. Shape (dates x funds). This is
    the yardstick of a DEFENSIVE rule: it gives up return on purpose, so what matters is the fall it avoids."""
    at = pd.DatetimeIndex(at)
    grid = pd.date_range(at.min(), at.max() + pd.offsets.MonthEnd(months), freq=pd.offsets.MonthEnd())
    v = asof_snapshot(nav, grid, max_stale_days)[0].to_numpy(dtype=float)
    out = np.full((len(at), v.shape[1]), np.nan)
    for k, i in enumerate(grid.get_indexer(at)):
        w = v[i:i + months + 1]
        if i < 0 or len(w) < months + 1:
            continue
        ok = np.isfinite(w).all(axis=0) & (w[0] > 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            rel = w / w[0]
            dd = rel / np.maximum.accumulate(rel, axis=0) - 1.0
        out[k] = np.where(ok, dd.min(axis=0), np.nan)
    return pd.DataFrame(out, index=at, columns=nav.columns)


def _spearman(a: pd.Series, b: pd.Series) -> float:
    d = pd.concat([a, b], axis=1).dropna()
    return float(d.iloc[:, 0].corr(d.iloc[:, 1], method="spearman")) if len(d) >= MIN_GROUP else np.nan


def _top_vs_pool(score: pd.Series, r: pd.Series, top_n: int) -> float:
    d = pd.concat([score, r], axis=1, keys=["s", "r"]).dropna()
    if len(d) <= top_n:
        return np.nan
    return float(d.sort_values("s", ascending=False)["r"].head(top_n).mean() - d["r"].mean())


def evaluate_crisis_variants(run, forward_12m: pd.DataFrame, at: pd.DatetimeIndex, variants=CRISIS_VARIANTS,
                             z_cuts=Z_CUTS, top_n: int = 10, macro_max_stale_days: int = 100,
                             forward_dd: "pd.DataFrame | None" = None) -> pd.DataFrame:
    """One row per (scope, variant, z_cut). run: pit_run.PitRun computed with group B (run.macro, run.vol_ann);
    forward_12m: pit_backtest.forward_returns(nav, at, 12, ...) (dates x isin); forward_dd (optional):
    forward_max_drawdown(...), adds the drawdown columns (dd_* are negative fractions: higher = shallower fall)."""
    if run.macro is None:
        raise ValueError("the PIT run has no group B (compute it with macro_variant=...)")
    at = pd.DatetimeIndex(at)
    betas = {m: asof_snapshot(run.macro[m], at, macro_max_stale_days)[0] for m in ("beta_vix", "beta_spread_hy")}
    vol = asof_snapshot(run.vol_ann, at, 45)[0] if run.vol_ann is not None else None
    scores = run.scores[run.scores["eligible"]].drop_duplicates(["as_of", "subportfolio", "isin"])
    groups = []
    for (t, sub), g in scores.groupby(["as_of", "subportfolio"]):
        if t not in forward_12m.index or t not in at:
            continue
        i = at.get_loc(t)
        iso = g["isin"].to_numpy()
        frame = pd.DataFrame({
            "beta_vix": betas["beta_vix"].iloc[i].reindex(iso).to_numpy(),
            "beta_spread_hy": betas["beta_spread_hy"].iloc[i].reindex(iso).to_numpy(),
            "vol_ann": (vol.iloc[i].reindex(iso).to_numpy() if vol is not None else np.nan),
            "score": g["score_final"].to_numpy(),
            "r": forward_12m.loc[t].reindex(iso).to_numpy(),
            "dd": (forward_dd.loc[t].reindex(iso).to_numpy() if forward_dd is not None and t in forward_dd.index else np.nan),
        }, index=iso)
        groups.append((t, g["regime"].iloc[0], frame))

    rows = []
    for scope in ("crisis", "all"):
        pool = [(t, f) for t, reg, f in groups if scope == "all" or reg == CRISIS]
        for variant in variants:
            for z_cut in ((z_cuts[0],) if variant == "sign_raw" else z_cuts):
                n_rows = n_flag = n_cov = 0
                fl_r, un_r, ic_sig, ic_vol, d_sel = [], [], [], [], []
                fl_dd, un_dd, ic_dd, ic_vol_dd = [], [], [], []
                for t, f in pool:
                    sig = signal(f, variant)
                    flag = penalised(f, variant, z_cut)
                    cov = sig.notna()
                    n_rows += len(f)
                    n_cov += int(cov.sum())
                    n_flag += int(flag.sum())
                    if cov.sum() >= MIN_GROUP:
                        fl_r.append(f.loc[flag & f["r"].notna(), "r"].mean())
                        un_r.append(f.loc[~flag & cov & f["r"].notna(), "r"].mean())
                        fl_dd.append(f.loc[flag & f["dd"].notna(), "dd"].mean())
                        un_dd.append(f.loc[~flag & cov & f["dd"].notna(), "dd"].mean())
                        ic_dd.append(_spearman(sig, f["dd"]))
                        ic_vol_dd.append(_spearman(-f["vol_ann"], f["dd"]))
                        ic_sig.append(_spearman(sig, f["r"]))
                        ic_vol.append(_spearman(-f["vol_ann"], f["r"]))
                        base = _top_vs_pool(f["score"], f["r"], top_n)
                        adj = _top_vs_pool(f["score"] * np.where(flag, MULT_CRISIS_SPREAD_MALUS, 1.0), f["r"], top_n)
                        d_sel.append(adj - base)
                rows.append({
                    "scope": scope, "variant": variant, "z_cut": z_cut, "groups": len(pool), "rows": n_rows,
                    "rows_with_signal": n_cov, "flagged": n_flag,
                    "share_flagged": n_flag / n_cov if n_cov else np.nan,
                    "fwd12_flagged": _nm(fl_r) if fl_r else np.nan,
                    "fwd12_unflagged": _nm(un_r) if un_r else np.nan,
                    "ic_signal": _nm(ic_sig) if ic_sig else np.nan,
                    "ic_low_vol": _nm(ic_vol) if ic_vol else np.nan,
                    "d_top_vs_pool": _nm(d_sel) if d_sel else np.nan,
                    "dd_flagged": _nm(fl_dd) if fl_dd else np.nan,
                    "dd_unflagged": _nm(un_dd) if un_dd else np.nan,
                    "ic_signal_dd": _nm(ic_dd) if ic_dd else np.nan,
                    "ic_low_vol_dd": _nm(ic_vol_dd) if ic_vol_dd else np.nan,
                })
    out = pd.DataFrame(rows)
    out["flagged_minus_unflagged"] = out["fwd12_flagged"] - out["fwd12_unflagged"]
    out["dd_flagged_minus_unflagged"] = out["dd_flagged"] - out["dd_unflagged"]       # negative = flagged funds fell more
    return out
