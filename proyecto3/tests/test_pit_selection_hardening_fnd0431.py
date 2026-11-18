"""FND-0431 / P3-1.3: hardening of the ranking-layer harness (proyecto3/src/pit_selection_test.py). No database, synthetic data (R-7).

Pins five defects found by the 2026-11-16/18 functional audit, all in code that the recorded FND-0429 verdict (commit 83b296a) does NOT depend on:
  E-14 a missing drawdown quantity passed the drawdown condition silently (default 0.0); H-11 the volatility floor was a literal copy of
  shared.config.RATIO_MIN_VOL_ANN; H-12 n_dates counted dates without any outcome; E-15 the verdict hid how firm the sign of the estimate is;
  E-16 a null fund_family_id collapsed all such funds of a date into one unit."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from proyecto3.src import pit_selection_test as T  # noqa: E402
from shared import config  # noqa: E402
from test_pit_selection_test_fnd0429 import _m2, _synthetic  # noqa: E402


# ---- E-14: a missing quantity is an error, not a pass --------------------------------------------------------------------------------------------
def test_a_missing_drawdown_quantity_is_an_error_and_not_a_silent_pass():
    no_dd = {"estimate": 0.2, "lower_bound": 0.1, "se": 0.03, "q10_gap_mean": -1.0}
    with pytest.raises(ValueError, match="dd_excess_mean"):
        T._klass(no_dd, 0.02)
    with pytest.raises(ValueError, match="dd_excess_mean"):
        T.verdict(no_dd, _m2(0.1, 0.0), _m2(0.18, 0.08), 0.006)


def test_a_missing_survivorship_gap_is_an_error():
    with pytest.raises(ValueError, match="q10_gap_mean"):
        T.attrition_adjusted({"estimate": 0.1, "se": 0.04})


def test_a_non_positive_estimate_needs_no_drawdown_quantity():
    assert T._klass({"estimate": -0.01, "lower_bound": -0.1}, 0.02) == "PIVOT"       # the rule decides before the drawdown condition is read


# ---- H-11: one definition of the volatility floor -------------------------------------------------------------------------------------------------
def test_the_volatility_floor_is_the_configured_one_and_not_a_copy():
    assert T.VOL_FLOOR == config.RATIO_MIN_VOL_ANN
    assert "VOL_FLOOR = RATIO_MIN_VOL_ANN" in Path(T.__file__).read_text(encoding="utf-8")


# ---- H-12: dates without any outcome are not dates of the panel -----------------------------------------------------------------------------------
def test_n_dates_counts_only_dates_with_a_valid_outcome():
    units, outcomes, fx = _synthetic(n_dates=24, n_fam=90, seed=3)
    first_dates = sorted(outcomes["as_ord"].unique())[:12]
    late = outcomes[~outcomes["as_ord"].isin(first_dates)]
    p = T.build_panel(units, outcomes.drop(late.index), fx)                             # the last 12 months have no outcome, as in real data
    assert len(p.score) == 24 * 90 and p.n_dates == 12
    assert p.date_id.max() < p.n_dates and (p.date_id >= 0).all()
    boot = T.make_boot(p.n_dates, p.n_families, n_boot=200)
    assert np.isfinite(T.estimate(T.sums_for(p, T.top_mask(p.group_id, p.score))))
    assert np.isfinite(T.bootstrap_se(T.sums_for(p, T.top_mask(p.group_id, p.score)), boot)["se"])


def test_the_date_index_keeps_time_order():
    units, outcomes, fx = _synthetic(n_dates=24, n_fam=90, seed=4)
    p = T.build_panel(units, outcomes, fx)
    first_row_of_each_date = pd.Series(p.date_id).drop_duplicates().to_numpy()
    assert (np.diff(first_row_of_each_date) > 0).all() and p.n_dates == 24            # the stationary bootstrap needs consecutive dates in time order


# ---- E-15: the verdict says how firm the sign is -------------------------------------------------------------------------------------------------
def test_the_verdict_carries_the_estimate_the_standard_error_and_the_lower_bound():
    v = T.verdict(_m2(0.2, 0.1), _m2(0.1, 0.0), _m2(0.18, 0.08), 0.006)
    assert (v["main_estimate"], v["main_se"], v["main_lower_bound"]) == (0.2, 0.03, 0.1)


# ---- E-16: a fund with no family is its own family -----------------------------------------------------------------------------------------------
def test_funds_without_a_family_id_are_not_merged_into_one_unit():
    d = pd.Timestamp("2020-01-31")
    s = pd.DataFrame({"as_of": [d] * 4, "subportfolio": ["Defensiva"] * 4, "fund_family_id": [None, np.nan, "", "F9"],
                      "isin": ["A", "B", "C", "D"], "eligible": [True] * 4})
    u = T.pick_units(s, pd.Series({"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0}))
    assert sorted(u["isin"]) == ["A", "B", "C", "D"]                                      # before: one of the three null families survived
    assert u["fund_family_id"].notna().all()
