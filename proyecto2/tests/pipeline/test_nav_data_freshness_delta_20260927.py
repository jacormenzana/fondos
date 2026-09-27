# proyecto2/tests/pipeline/test_nav_data_freshness_delta_20260927.py
# -*- coding: utf-8 -*-
"""
FND-0021 (2026-09-27, BI-02): run_pipeline.py::_nav_data_freshness_delta -- days since the fund's
last NAV, written once per fund on since_inception only (a crisis/rolling horizon slice ends years
ago by construction, so computing this on it would always report a large, misleading delta).

Pure function, no DB -- these tests pass an explicit `as_of` to stay independent of the real clock.

R-7 compliant: no pipeline.py (proyecto1) / core.io imports. (This IS proyecto2's own
pipeline module, not proyecto1's -- the R-7 restriction is about proyecto1's classification
pipeline and its heavier import chain.)
"""
import pandas as pd

import src.pipeline.run_pipeline as rp


def _nav(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(dates), "nav": [100.0] * len(dates)})


def test_computes_days_since_the_last_nav_on_since_inception():
    nav_df = _nav(["2026-06-01", "2026-08-01", "2026-08-15"])
    rows = rp._nav_data_freshness_delta(nav_df, "since_inception", as_of=pd.Timestamp("2026-09-27"))
    assert rows == [{
        "metric": "nav_data_freshness_delta", "value": 43.0, "real_flag": 0, "source_rows": 3,
    }]


def test_returns_nothing_for_a_non_since_inception_horizon():
    """A crisis_2022 or rolling_1y slice ends years ago by construction -- computing this there
    would always report a large, misleading delta, so it is deliberately skipped."""
    nav_df = _nav(["2022-01-01", "2022-06-01"])
    for horizon in ("crisis_2022", "rolling_1y", "rolling_3y"):
        assert rp._nav_data_freshness_delta(nav_df, horizon, as_of=pd.Timestamp("2026-09-27")) == []


def test_returns_nothing_for_an_empty_series():
    assert rp._nav_data_freshness_delta(pd.DataFrame(columns=["date", "nav"]),
                                          "since_inception") == []


def test_zero_when_the_last_nav_is_as_of_today():
    nav_df = _nav(["2026-09-27"])
    rows = rp._nav_data_freshness_delta(nav_df, "since_inception", as_of=pd.Timestamp("2026-09-27"))
    assert rows[0]["value"] == 0.0


def test_defaults_as_of_to_today_when_not_given():
    """Sanity check on the production default path (no as_of override) -- must not raise and must
    return a plausible non-negative delta for a recent NAV."""
    nav_df = _nav([pd.Timestamp.today().normalize().strftime("%Y-%m-%d")])
    rows = rp._nav_data_freshness_delta(nav_df, "since_inception")
    assert rows[0]["value"] == 0.0
