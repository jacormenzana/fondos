"""Package L, P3-8 (doc/decisiones/PREREG_P3_8_CPI_REGIME_INPUT.md): the CPI input variants of the regime classifier and the evaluation of a trial (`proyecto3/src/pit_p38_regime.py`).
Invariants pinned: (1) a CPI input is the year-on-year change against the SAME MONTH a year earlier (a date, never a row offset), shifted by its publication lag by date, and aggregated as the mean
or the maximum of Spain and the euro area over the regions that have a reading (the classifier's own behaviour: a missing region does not blank the month); (2) the maximum is never below the mean
and a shorter lag makes a reading available one month earlier; (3) the label summary counts exactly the months whose regime differs and reports the mean weights; (4) a trial is ACCEPTABLE only if its
maximum drawdown is within 0.5 pp of the reference at the stated fees AND at 25 bp and its paired net-return difference passes the non-inferiority bound; (5) BENEFICIAL needs, on top, 0.5 pp of net
return or 1.0 pp of drawdown at the stated fees; (6) the Holm family is the three trials. Synthetic frames only (R-7)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import pit_p38_regime as R  # noqa: E402


def levels(n=40):
    """Two index series with VARYING monthly growth, so that the year-on-year rate differs from month to month and between the regions (a constant growth would make every lag and every aggregator look the same)."""
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    rng = np.random.default_rng(7)
    return pd.DataFrame({"ES": 100.0 * np.cumprod(1 + rng.normal(0.004, 0.004, n)), "EU": 100.0 * np.cumprod(1 + rng.normal(0.002, 0.004, n))}, index=idx)


def yoy_at(lv, col, i):
    """Year-on-year change of month i against month i-12, computed by hand."""
    return lv.loc[lv.index[i], col] / lv.loc[lv.index[i - 12], col] - 1.0


def test_the_cpi_input_is_the_same_month_yoy_shifted_by_its_lag_by_date():
    lv = levels()
    t = lv.index[25]                                                                # known at month-end t: the reading of month t-2
    assert R.cpi_input(lv, "avg", 2).loc[t] == pytest.approx((yoy_at(lv, "ES", 23) + yoy_at(lv, "EU", 23)) / 2)
    assert R.cpi_input(lv, "avg", 1).loc[t] == pytest.approx((yoy_at(lv, "ES", 24) + yoy_at(lv, "EU", 24)) / 2)


def test_the_maximum_is_never_below_the_mean_and_equals_it_when_both_regions_agree():
    lv = levels()
    a, m = R.cpi_input(lv, "avg", 2).dropna(), R.cpi_input(lv, "max", 2).dropna()
    assert (m >= a - 1e-15).all() and list(m.index) == list(a.index)
    t = lv.index[27]                                                                # lag 2: the reading of month 25, by hand
    es, eu = yoy_at(lv, "ES", 25), yoy_at(lv, "EU", 25)
    assert abs(es - eu) > 1e-4 and m.loc[t] == pytest.approx(max(es, eu)) and m.loc[t] > a.loc[t] + 1e-5
    assert (m > a + 1e-9).any() and a.loc[t] == pytest.approx((es + eu) / 2)
    same = lv.assign(EU=lv["ES"])
    pd.testing.assert_series_equal(R.cpi_input(same, "max", 2).dropna(), R.cpi_input(same, "avg", 2).dropna(), check_names=False)


def test_a_region_with_no_reading_does_not_blank_the_month_like_the_classifier():
    lv = levels()
    lv.loc[lv.index[:15], "EU"] = np.nan                                              # the euro-area index starts later
    t = lv.index[16]                                                                # the reading of month 14: Spain has one, the euro area has none
    expected = yoy_at(lv, "ES", 14)
    assert R.cpi_input(lv, "avg", 2).loc[t] == pytest.approx(expected) and R.cpi_input(lv, "max", 2).loc[t] == pytest.approx(expected)


def test_a_shorter_lag_makes_a_reading_available_one_month_earlier():
    lv = levels()
    l2, l1 = R.cpi_input(lv, "avg", 2), R.cpi_input(lv, "avg", 1)
    assert l1.loc[lv.index[30]] == pytest.approx(l2.loc[lv.index[31]])
    assert abs(l1.loc[lv.index[30]] - l2.loc[lv.index[30]]) > 1e-6                  # and the two lags really are different series here


def hist(labels):
    idx = pd.date_range("2020-01-31", periods=len(labels), freq="ME")
    w = {"A": (0.6, 0.3, 0.1), "B": (0.5, 0.35, 0.15), "C": (0.2, 0.45, 0.35)}
    return pd.DataFrame({"regime": labels, "weight_defensive": [w[x][0] for x in labels], "weight_balanced": [w[x][1] for x in labels], "weight_dynamic": [w[x][2] for x in labels]}, index=idx)


def test_the_label_summary_counts_the_changed_months_and_the_mean_weights():
    ref, alt = hist(["A", "A", "A", "C", "C"]), hist(["A", "B", "A", "C", "B"])
    s = R.label_summary(ref, alt)
    assert s["n_dates"] == 5 and s["n_changed"] == 2 and s["share_changed"] == pytest.approx(0.4)
    assert s["mean_weight_defensive_ref"] == pytest.approx((0.6 * 3 + 0.2 * 2) / 5)
    assert s["mean_weight_defensive_alt"] == pytest.approx((0.6 * 2 + 0.5 * 2 + 0.2) / 5)
    assert s["changed_months"] == ["202002", "202005"]
    assert s["shares_alt"]["B"] == pytest.approx(0.4)


def series(n=120, seed=1, shift=0.0):
    idx = pd.date_range("2010-01-31", periods=n, freq="ME")
    base = np.random.default_rng(seed).normal(0.004, 0.01, n)
    net = pd.Series(base + shift, index=idx)
    return pd.DataFrame({"netfee": net, "net25": net + 0.002, "gross": net + 0.003})


def test_an_identical_trial_is_acceptable_and_neutral():
    r = R.evaluate_trial("R1", series(), series(), R.HOLM_LEVEL)
    assert r["acceptable"] and r["label"] == "NEUTRAL" and r["net_diff_pp"] == pytest.approx(0.0)


def test_a_material_steady_gain_is_beneficial():
    r = R.evaluate_trial("R1", series(), series(shift=0.0006), R.HOLM_LEVEL)                 # +0.72 pp a year, every month
    assert r["acceptable"] and r["benefit_net"] and r["label"] == "BENEFICIAL"


def test_a_loss_beyond_the_non_inferiority_bound_is_harmful():
    r = R.evaluate_trial("R1", series(), series(shift=-0.001), R.HOLM_LEVEL)                  # -1.2 pp a year
    assert not r["ni_pass"] and not r["acceptable"] and r["label"] == "HARMFUL"


def test_a_deeper_drawdown_beyond_half_a_point_fails_the_drawdown_condition_and_is_harmful():
    ref = series()
    alt = series()
    for c in ("netfee", "net25"):
        alt.iloc[60, alt.columns.get_loc(c)] = -0.10                                      # one bad month: deeper drawdown
    for c in ("netfee", "net25"):                                                          # and a recovery elsewhere so the mean change stays small
        alt.iloc[61:70, alt.columns.get_loc(c)] += 0.011
    assert R.max_drawdown(alt["netfee"]) < R.max_drawdown(ref["netfee"]) - 0.005
    r = R.evaluate_trial("R1", ref, alt, R.HOLM_LEVEL)
    assert not r["dd_ok_fee"] and not r["acceptable"] and r["label"] == "HARMFUL"


def test_the_drawdown_tolerance_is_checked_at_the_stated_fees_and_at_25_bp():
    ref = series()
    alt = ref.copy()
    alt.iloc[60, alt.columns.get_loc("net25")] = -0.10                                     # only the 25 bp series gets deeper
    r = R.evaluate_trial("R1", ref, alt, R.HOLM_LEVEL)
    assert r["dd_ok_fee"] and not r["dd_ok_25"] and r["label"] == "HARMFUL"


def test_the_holm_family_is_the_three_trials_of_the_preregistration():
    assert R.HOLM_LEVEL == pytest.approx(0.05 / 3)
    assert R.TRIALS == {"R1": ("max", 2), "R2": ("avg", 1), "R3": ("max", 1)} and R.REFERENCE == ("avg", 2)
