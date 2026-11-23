"""Package L, P3-9 (doc/decisiones/PREREG_P3_9_LAYER3.md section 5): the evaluation of a trial arm against the base arm AC, `proyecto3/src/pit_p39_eval.py`.
Invariants pinned: (1) rule 1 (the 15 % cap unstressed at 25 bp and the stressed worst window relative to the base, 0.5 pp) is the one of P3-7, reused; (2) rule 2 is NON-INFERIORITY: the Holm-adjusted
lower bound of the paired net-return difference at the stated fees above -0.10 pp (an improvement is not tested); (3) rule 3 is a RISK BENEFIT: the stressed worst window or the unstressed maximum
drawdown shallower than the base's by at least 1.0 pp, so a lever that does nothing is never a candidate; (4) rule 4 is the collateral check against the lower edge of the net band (3.2 %) and 0.35 pp;
(5) the candidate flag is the conjunction and nothing else; (6) there is no matched-pool condition. Synthetic series only (R-7)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from proyecto3.src import pit_p39_eval as W  # noqa: E402
from test_pit_p37_eval import CRISIS, DESIGN, EPISODES, IDX, N, arm, make_series  # noqa: E402


def evaluate(a, b, name="N5"):
    return W.evaluate_trial(name, a, b, episodes=EPISODES, design=DESIGN, crisis_windows=CRISIS)


def _same(series):
    return series.copy()


def with_dip_halved(series, months=slice(30, 36)):
    """A variant of `series` whose losses in `months` are halved (a shallower drawdown), the rest identical."""
    s = series.copy()
    for col in ("gross", "net25", "netfee"):
        part = s[col].iloc[months]
        s.iloc[months, s.columns.get_loc(col)] = part.where(part > 0, part * 0.5)
    return s


def shifted(series, c):
    """A variant earning `c` more every month: a CONSTANT paired difference (zero standard error) that also makes the 6-month dip `6 c` shallower."""
    s = series.copy()
    for col in ("gross", "net25", "netfee"):
        s[col] = s[col] + c
    return s


def drawdown_series(seed=7):
    """A series with one clear drawdown inside 2012 (month 30-35 of the index) and positive drift elsewhere, within the 15 % cap."""
    s = make_series(mean=0.005, sd=0.004, seed=seed)
    for col in ("gross", "net25", "netfee"):
        s.iloc[30:36, s.columns.get_loc(col)] = -0.02
    return s


# ================================================================ constants =============================================================================================
def test_the_constants_are_the_preregistered_ones():
    assert W.HOLM_TRIALS == 6 and W.HOLM_LEVEL == pytest.approx(0.05 / 6)
    assert W.NI_MARGIN_PP == -0.10 and W.BENEFIT_MIN_PP == 1.0 and W.BAND_LOW == 0.032
    assert W.DD_CAP == -0.15 and W.STRESS_REL == 0.005 and W.DEGRADE_MAX_PP == 0.35


def test_the_band_target_is_the_monthly_equivalent_of_3_2_percent():
    t = W.band_target(IDX)
    assert len(t) == N and t.nunique() == 1 and (1 + t.iloc[0]) ** 12 == pytest.approx(1.032)


# ================================================================ the candidate rule ====================================================================================
def test_a_lever_that_changes_nothing_is_not_a_candidate():
    b = arm(drawdown_series())
    r = evaluate(arm(_same(b["series"])), b)
    assert r["c1"] is True and r["c2"] is True and r["c3"] is True              # non-inferior, within the cap, no collateral damage ...
    assert r["c_benefit"] is False and r["candidate"] is False                  # ... but no risk benefit: not a candidate


def test_a_shallower_drawdown_without_a_return_loss_is_a_candidate():
    base = drawdown_series()
    b = arm(base)
    v = arm(shifted(base, 0.002))                                                # a dominant variant: the dip is 1.2 pp shallower and no month earns less
    r = evaluate(v, b)
    assert r["dd_gain_pp"] >= 1.0 and r["c_benefit"] is True
    assert (r["c1"], r["c2"], r["c3"]) == (True, True, True) and r["candidate"] is True


def _stress_gain_pp(base, stress_var, stress_base):
    f = lambda s, st: float(np.nanmin(W.V.E.stressed_window_dds(s["net25"].to_numpy(), st, W.V.STRESS_SHARE)))
    return (f(base, stress_var) - f(base, stress_base)) * 100


def test_the_benefit_can_come_from_the_stressed_window_alone():
    base = drawdown_series()
    stress_base = np.full((N, 12), -0.030)
    stress_var = np.full((N, 12), -0.015)                                        # the variant's stress leg is shallower, its own path identical
    want = _stress_gain_pp(base, stress_var, stress_base)
    assert want >= 1.0
    r = evaluate(arm(_same(base), stress=stress_var), arm(base, stress=stress_base))
    assert r["stress_gain_pp"] == pytest.approx(want) and abs(r["dd_gain_pp"]) < 1e-9 and r["c_benefit"] is True and r["candidate"] is True
    tiny = np.full((N, 12), -0.0299)                                             # almost the base's stress: far below 1.0 pp
    assert _stress_gain_pp(base, tiny, stress_base) < 1.0
    small = evaluate(arm(_same(base), stress=tiny), arm(base, stress=stress_base))
    assert small["c_benefit"] is False and small["candidate"] is False


def test_the_benefit_threshold_is_inclusive_at_one_point_zero_pp():
    assert W.risk_benefit(1.0, 0.0) is True and W.risk_benefit(0.0, 1.0) is True and W.risk_benefit(0.999, 0.999) is False
    assert W.risk_benefit(-3.0, 1.0) is True                                     # either side is enough, a loss on the other does not cancel it
    assert W.risk_benefit(float("nan"), float("nan")) is False


def test_rule_2_is_non_inferiority_a_return_loss_blocks_a_candidate_even_with_a_benefit():
    base = drawdown_series()
    v = with_dip_halved(base)
    for col in ("gross", "net25", "netfee"):
        v[col] = v[col] - 0.002                                                  # 12 x 0.002 = 2.4 pp a year lost, far more than the dip halving gives back
    r = evaluate(arm(v), arm(base))
    assert r["c_benefit"] is True and r["ret_lb_pp"] < -0.10 and r["c2"] is False and r["candidate"] is False


def test_a_return_gain_is_not_tested_it_only_has_to_be_non_inferior():
    base = drawdown_series()
    v = base.copy()
    for col in ("gross", "net25", "netfee"):
        v[col] = v[col] + 0.0004                                                 # +0.5 pp a year: the shifted dip is 0.24 pp shallower, below the 1.0 pp threshold
    r = evaluate(arm(v), arm(base))
    assert r["c2"] is True and r["c_benefit"] is False and r["candidate"] is False     # a better return without a risk benefit is not what this study adopts


def test_rule_1_still_blocks_a_variant_deeper_than_the_cap():
    base = drawdown_series()
    v = with_dip_halved(base)
    for col in ("gross", "net25", "netfee"):
        v.iloc[60:70, v.columns.get_loc(col)] = -0.03                            # a new 26 % drawdown at 25 bp
    r = evaluate(arm(v), arm(base))
    assert r["dd_cap_ok"] is False and r["c1"] is False and r["candidate"] is False


def test_rule_4_needs_the_share_of_windows_above_the_band_not_to_fall():
    base = drawdown_series()
    v = with_dip_halved(base)
    for col in ("gross", "net25", "netfee"):
        v[col] = v[col] - 0.0002
    r = evaluate(arm(v), arm(base))
    assert "share36_variant" in r and "share36_baseline" in r
    low = base.copy()
    for col in ("gross", "net25", "netfee"):
        low[col] = low[col] - 0.0040                                             # the base earns well under 3.2 % a year
    hi = evaluate(arm(base), arm(low))                                           # a variant with MORE windows above the band than the base: fine for rule 4
    assert hi["share36_variant"] >= hi["share36_baseline"] and hi["c3"] is True
    lo = evaluate(arm(low), arm(base))                                           # fewer windows above the band than the base: rule 4 fails
    assert lo["share36_variant"] < lo["share36_baseline"] and lo["c3"] is False and lo["candidate"] is False


def test_the_candidate_flag_is_the_conjunction_of_the_four_rules(monkeypatch):
    base = drawdown_series()
    b, v = arm(base), arm(shifted(base, 0.002))
    assert evaluate(v, b)["candidate"] is True
    orig = W.V.evaluate_arm
    for key in ("c1", "c3"):
        monkeypatch.setattr(W.V, "evaluate_arm", lambda *a, _k=key, **kw: {**orig(*a, **kw), _k: False})
        assert evaluate(v, b)["candidate"] is False, key
    monkeypatch.setattr(W.V, "evaluate_arm", orig)


def test_there_is_no_matched_pool_condition():
    r = evaluate(arm(with_dip_halved(drawdown_series())), arm(drawdown_series()))
    assert r.get("c4") is None and not any(k.startswith(("persistent_", "redraw_", "beat_")) for k in r)


def test_the_holm_level_is_the_six_arm_family():
    base = drawdown_series()
    noisy = base.copy()
    rng = np.random.default_rng(1)
    for col in ("gross", "net25", "netfee"):
        noisy[col] = noisy[col] + rng.normal(0, 0.01, N)
    r = evaluate(arm(noisy), arm(base))
    # lb = mean - z(1 - 0.05 / 6) * se
    from statistics import NormalDist
    assert r["ret_lb_pp"] == pytest.approx(r["ret_mean_pp"] - NormalDist().inv_cdf(1 - 0.05 / 6) * r["ret_se_pp"])


def test_the_non_inferiority_margin_is_minus_0_10_pp_a_year():
    base = drawdown_series()
    ok = evaluate(arm(shifted(base, -0.00005)), arm(base))                       # a constant loss of 0.06 pp a year: the lower bound is above -0.10
    bad = evaluate(arm(shifted(base, -0.0001)), arm(base))                       # 0.12 pp a year: below the margin
    assert ok["ret_lb_pp"] == pytest.approx(-0.06, abs=1e-6) and ok["c2"] is True
    assert bad["ret_lb_pp"] == pytest.approx(-0.12, abs=1e-6) and bad["c2"] is False
