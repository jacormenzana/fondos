# proyecto3/src/pit_p38_regime.py
# -*- coding: utf-8 -*-
"""Package L, P3-8 (doc/decisiones/PREREG_P3_8_CPI_REGIME_INPUT.md): the CPI input variants of the regime classifier and the evaluation of a trial. PURE (pandas / numpy; the helpers are those of P3-5A / P3-7).

The classifier reads `ipc_yoy_avg` = the mean of the Spanish and euro-area year-on-year CPI, each lagged 2 months. The study changes the aggregator (mean or maximum) and the lag (2 or 1 month) of THAT input only; the
IPC + M3 target (FND-0303) is not recomputed here. `cpi_input` reproduces the classifier's own series for the reference arm (a script check pins it against the stored `ipc_yoy_avg`).

A trial is ACCEPTABLE iff its maximum drawdown, at the stated fees AND at the flat 25 bp, is no deeper than the reference's by more than 0.5 pp and the Holm-adjusted lower bound of its paired annual net-return
difference at the stated fees is above -0.10 pp; ACCEPTABLE trials are BENEFICIAL when they also gain 0.5 pp a year of net return or 1.0 pp of drawdown at the stated fees, else NEUTRAL; the rest are HARMFUL.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from proyecto3.src import pit_p35a_eval as E
from proyecto3.src import pit_p35a_prep as P

HOLM_LEVEL = 0.05 / 3                                  # the three trials of the preregistration
TRIALS = {"R1": ("max", 2), "R2": ("avg", 1), "R3": ("max", 1)}
REFERENCE = ("avg", 2)
NI_MARGIN_PP = -0.10                                   # non-inferiority bound of the paired net-return difference, pp a year
DD_TOLERANCE = 0.005                                   # the maximum drawdown may be at most 0.5 pp deeper than the reference's
BENEFIT_NET_PP, BENEFIT_DD = 0.5, 0.010                # reported benefit: 0.5 pp a year of net return or 1.0 pp of drawdown (stated fees)


def cpi_input(levels: pd.DataFrame, aggregator: str, lag: int) -> pd.Series:
    """The classifier's CPI input as known at each month-end: the year-on-year change of each region's index against the SAME MONTH a year earlier (a date, never a row offset), made observable `lag` months after
    its reference month (the PIT publication lag), then the mean or the maximum over the regions that have a reading (a missing region never blanks the month, as in the classifier). `levels`: month-end index, columns ES and EU."""
    if aggregator not in ("avg", "max"):
        raise ValueError(f"aggregator must be 'avg' or 'max', not {aggregator!r}")
    prev = levels.copy()
    prev.index = prev.index + pd.offsets.MonthEnd(12)
    yoy = levels / prev.reindex(levels.index) - 1.0
    known = yoy.copy()
    known.index = known.index + pd.offsets.MonthEnd(int(lag))
    agg = known.mean(axis=1) if aggregator == "avg" else known.max(axis=1)
    return agg.rename(f"cpi_{aggregator}_lag{lag}")


def label_summary(ref: pd.DataFrame, alt: pd.DataFrame) -> dict:
    """Descriptive effect of an alternative regime history on the common dates: the number and the months (YYYYMM) whose label differs, the regime shares of both and the mean sub-portfolio weights of both."""
    idx = ref.index.intersection(alt.index)
    r, a = ref.loc[idx], alt.loc[idx]
    changed = r["regime"].to_numpy() != a["regime"].to_numpy()
    out = {"n_dates": int(len(idx)), "n_changed": int(changed.sum()), "share_changed": float(changed.mean()) if len(idx) else float("nan"),
           "changed_months": [d.strftime("%Y%m") for d in idx[changed]],
           "shares_ref": r["regime"].value_counts(normalize=True).to_dict(), "shares_alt": a["regime"].value_counts(normalize=True).to_dict()}
    for c in ("weight_defensive", "weight_balanced", "weight_dynamic"):
        out[f"mean_{c}_ref"], out[f"mean_{c}_alt"] = float(r[c].mean()), float(a[c].mean())
    return out


def max_drawdown(r) -> float:
    """Maximum drawdown (negative) of a monthly net-return series, months without a return dropped (the last date has no forward return)."""
    return E.max_dd(pd.Series(r).dropna().to_numpy())


def evaluate_trial(name: str, ref: pd.DataFrame, alt: pd.DataFrame, holm_level: float = HOLM_LEVEL) -> dict:
    """The classification of one trial against the reference. `ref` / `alt`: monthly frames with `netfee` (the owner's stated fees, primary) and `net25` (the flat 25 bp cross-check), same index."""
    out = {"trial": name}
    for tag, col in (("fee", "netfee"), ("25", "net25")):
        out[f"ann_ref_{tag}"], out[f"ann_alt_{tag}"] = E.ann_return(ref[col].dropna().to_numpy()), E.ann_return(alt[col].dropna().to_numpy())
        out[f"dd_ref_{tag}"], out[f"dd_alt_{tag}"] = max_drawdown(ref[col]), max_drawdown(alt[col])
        out[f"dd_delta_{tag}"] = out[f"dd_alt_{tag}"] - out[f"dd_ref_{tag}"]                 # positive = shallower
        out[f"dd_ok_{tag}"] = bool(out[f"dd_delta_{tag}"] >= -DD_TOLERANCE - 1e-12)
    ni = E.noninferiority((alt["netfee"] - ref["netfee"]).to_numpy(), holm_level, NI_MARGIN_PP)
    out.update({"net_diff_pp": ni["mean_pp"], "net_se_pp": ni["se_pp"], "net_lb_pp": ni["lb_pp"], "ni_pass": bool(ni["pass"]), "n_months": ni["n"], "mde_pp": P.mde_pp(ni["se_pp"], holm_level)})
    out["acceptable"] = bool(out["ni_pass"] and out["dd_ok_fee"] and out["dd_ok_25"])
    out["benefit_net"] = bool(out["net_diff_pp"] >= BENEFIT_NET_PP)
    out["benefit_dd"] = bool(out["dd_delta_fee"] >= BENEFIT_DD - 1e-12)
    out["label"] = "HARMFUL" if not out["acceptable"] else ("BENEFICIAL" if (out["benefit_net"] or out["benefit_dd"]) else "NEUTRAL")
    return out
