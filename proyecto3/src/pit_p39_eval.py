# proyecto3/src/pit_p39_eval.py
# -*- coding: utf-8 -*-
"""Package L, P3-9 (doc/decisiones/PREREG_P3_9_LAYER3.md section 5): the evaluation of a trial arm against the base arm AC, built on the P3-7 evaluation (`pit_p37_eval.evaluate_arm`). PURE (pandas / numpy,
no database, no pipeline import: R-7); the command line is `scripts/audit/p3/p3_p39_trials.py --stage report`.

A trial arm is a forward-shadow CANDIDATE only if all four rules hold (history never adopts, R7):
  1  the drawdown cap: the unstressed full-sample maximum drawdown of the master at the flat 25 bp is within 15 %, the stressed worst window is no deeper than the base's by more than 0.5 pp, and the
     same in each informative episode (the P3-7 rule, reused as is: `c1`);
  2  return NON-INFERIORITY at the stated fees: the one-sided lower bound of the paired monthly net-return difference against the base (stationary bootstrap, Holm over the 6 arms) above -0.10 pp a year
     (`c2`); an improvement is not tested (the minimum detectable effect exceeds the 0.5 pp floor);
  3  a RISK BENEFIT: the stressed worst window OR the unstressed full-sample maximum drawdown (both at 25 bp, the regime of the cap) shallower than the base's by at least 1.0 pp (`c_benefit`), so a
     lever that does nothing is never a candidate;
  4  no collateral damage: non-crisis degradation at most 0.35 pp a year and the share of 36-month windows at or above the lower edge of the net band (3.2 %) not lower than the base's (`c3`).
There is no matched-pool condition (the pool question belongs to layer 1). Reading choices where the preregistration is silent: rule 3 uses the 25 bp regime like rule 1; the stated-fee values are reported.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from proyecto3.src import pit_p37_eval as V

E = V.E
HOLM_TRIALS = 6                    # section 5: the Holm family of rule 2
HOLM_LEVEL = 0.05 / HOLM_TRIALS
NI_MARGIN_PP = -0.10               # A5, rule 2
BENEFIT_MIN_PP = 1.0               # rule 3 (assistant's value, approved as D3)
BAND_LOW = 0.032                   # the lower edge of the owner's net band (2026-11-22), the reference of rule 4
DD_CAP = V.DD_CAP
STRESS_REL = V.STRESS_REL
DEGRADE_MAX_PP = V.DEGRADE_MAX_PP
_EPS = 1e-9


def band_target(index) -> pd.Series:
    """The monthly return equivalent of BAND_LOW a year, constant, on `index`: the target series of rule 4."""
    return pd.Series((1.0 + BAND_LOW) ** (1.0 / 12.0) - 1.0, index=index)


def risk_benefit(dd_gain_pp: float, stress_gain_pp: float) -> bool:
    """Rule 3: either gain (pp, positive = shallower than the base) of at least `BENEFIT_MIN_PP`; a gain that is NaN does not count and a loss on the other side does not cancel it."""
    vals = [x for x in (dd_gain_pp, stress_gain_pp) if x is not None and np.isfinite(x)]
    return bool(vals and max(vals) >= BENEFIT_MIN_PP - _EPS)


def evaluate_trial(name: str, t: dict, base: dict, *, episodes: dict, design: tuple, crisis_windows, holm_level: float = HOLM_LEVEL) -> dict:
    """One row of the P3-9 adoption table: `t` / `base` = {"series", "stress" {"master_p10", "master_p05"}, "meta"} (the arm and the base arm AC of the SAME build)."""
    s, b = t["series"], base["series"]
    r = V.evaluate_arm(name, t, base, band_target(s.index), None, episodes=episodes, design=design, crisis_windows=crisis_windows, holm_level=holm_level)
    ni = E.noninferiority((s["netfee"] - b["netfee"]).to_numpy(), holm_level, NI_MARGIN_PP)
    r.update({f"ret_{k}": v for k, v in ni.items()})                                   # replaces the improvement test of P3-7: the lower bound is judged against -0.10 pp
    r["c2"] = bool(ni["pass"])
    base_maxdd = E.max_dd(b["net25"].dropna().to_numpy())
    r["dd_gain_pp"] = float((r["maxdd_25bp"] - base_maxdd) * 100.0)
    r["stress_gain_pp"] = float((r["stressed_worst_25bp"] - r["stressed_worst_25bp_baseline"]) * 100.0)
    r["c_benefit"] = risk_benefit(r["dd_gain_pp"], r["stress_gain_pp"])
    r["c4"] = None                                                                     # the matched-pool condition of P3-7 does not exist here
    r["candidate"] = bool(r["c1"] and r["c2"] and r["c_benefit"] and r["c3"])
    return r
