# proyecto2/tests/calculations/test_report_reconciliation_matches_p2_fnd0428.py
# -*- coding: utf-8 -*-
"""
FND-0428: the independent reconciliation (`shared.report_reconciliation`, plain pandas, no code shared with P2) must agree with P2's own
return functions on the SAME series and windows, under every annualization convention, with and without internal gaps. This pins the deliberate
second implementation: if P2's convention moves, this test fails before the report gate starts rejecting good reports.

R-7: no pipeline.py, no database.
"""
import numpy as np
import pandas as pd
import pytest

from shared import config
from shared import report_reconciliation as rr
from src.calculations.returns import annualized_return, trailing_window, ytd_return


def _series(first, last, drop=(), g=0.004):
    d = pd.date_range(first, last, freq="ME")
    if len(drop):
        d = d[~d.isin(pd.to_datetime(list(drop)))]
    rng = np.random.default_rng(7)
    nav = 100.0 * np.cumprod(1.0 + g + 0.01 * rng.standard_normal(len(d)))
    return pd.DataFrame({"date": d, "nav": nav})


CASES = {
    "dense": _series("2018-01-31", "2026-10-31"),
    "midmonth_last": pd.concat([_series("2018-01-31", "2026-09-30"),
                                pd.DataFrame({"date": [pd.Timestamp("2026-11-05")], "nav": [150.0]})], ignore_index=True),
    "with_gaps": _series("2018-01-31", "2026-10-31", drop=["2025-03-31", "2025-04-30", "2022-08-31"]),
}


@pytest.mark.parametrize("by_dates,interval", [(True, True), (True, False), (False, True), (False, False)])
@pytest.mark.parametrize("case", sorted(CASES))
def test_annualized_headlines_match_p2_under_every_convention(monkeypatch, case, by_dates, interval):
    monkeypatch.setattr(config, "ANNUALIZATION_BY_DATES_ENABLED", by_dates, raising=False)
    monkeypatch.setattr(config, "ANNUALIZATION_INTERVAL_ENABLED", interval, raising=False)
    nav = CASES[case]
    win = trailing_window(nav, 12)
    p2_1y = annualized_return(win["nav"], 12, dates=win["date"])
    p2_si = annualized_return(nav["nav"], 12, dates=nav["date"])
    mine = rr.recompute_headline(nav, by_dates=by_dates, interval=interval)
    assert mine["ret_1y"] == pytest.approx(p2_1y, rel=1e-12, abs=1e-12)
    assert mine["ret_si_nominal"] == pytest.approx(p2_si, rel=1e-12, abs=1e-12)


@pytest.mark.parametrize("case", sorted(CASES))
def test_ytd_matches_p2(case):
    nav = CASES[case]
    p2 = ytd_return(nav)
    assert p2 is not None
    assert rr.recompute_headline(nav)["ytd"] == pytest.approx(p2["value"], rel=1e-12, abs=1e-12)


def test_a_stale_base_is_rejected_by_both():
    nav = pd.DataFrame({"date": pd.to_datetime(["2025-12-10", "2026-05-29"]), "nav": [100.0, 103.0]})
    assert ytd_return(nav) is None and rr.recompute_headline(nav)["ytd"] is None


def test_a_launched_this_year_fund_agrees_in_both():
    nav = pd.DataFrame({"date": pd.to_datetime(["2026-02-27", "2026-10-31"]), "nav": [100.0, 107.0]})
    assert ytd_return(nav)["partial"] is True
    assert rr.recompute_headline(nav)["ytd"] == pytest.approx(0.07)
