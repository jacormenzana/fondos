# proyecto3/tests/test_pit_short_horizon.py
# -*- coding: utf-8 -*-
"""
PIT short-horizon gate metrics (proyecto3/src/pit_short_horizon.py) pinned to P2 -- FND-0159, step d1c.

Oracle = what run_pipeline does for each short window, re-stated here with P2's own pure functions
(loaded by path): nav_daily[date <= t].tail(N) -> min-obs check -> validators.validate_nav ->
short_horizon.compute_short_horizon_metrics. The vectorized module must return the same numbers for the
same (fund, t), and NaN exactly where P2 would skip or clear the window (the scorer's gates then fail open).
No DB, no pipeline imports (R-7).

    python -m pytest proyecto3/tests/test_pit_short_horizon.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_metrics import load_p2_calc
from proyecto3.src.pit_short_horizon import (
    MIN_OBS_3M, MIN_OBS_6M, SHORT_GATE_METRICS, WINDOW_3M, WINDOW_6M, short_gate_metrics,
)

p2_short = load_p2_calc("short_horizon")
p2_validators = load_p2_calc("validators", "utils")

BDAYS = pd.bdate_range("2019-01-01", periods=760)


def _series(rng, mu, sd, start=0, stop=None):
    nav = 100.0 * np.cumprod(1.0 + rng.normal(mu, sd, len(BDAYS)))
    s = pd.Series(nav, index=BDAYS)
    s.iloc[:start] = np.nan
    if stop is not None:
        s.iloc[stop:] = np.nan
    return s


def _daily() -> pd.DataFrame:
    rng = np.random.default_rng(5)
    funds = {
        "CALM":  _series(rng, 0.0002, 0.002),
        "WILDV": _series(rng, 0.0004, 0.015),
        "SHORT": _series(rng, 0.0003, 0.006, start=700),            # only 60 daily obs at the end
        "STALE": _series(rng, 0.0003, 0.006, stop=500),             # stops reporting at day 500
    }
    liq = _series(rng, 0.0002, 0.004)
    liq[rng.random(len(liq)) < 0.45] = np.nan                        # repeated NAV on ~45% of days
    funds["LIQ"] = liq.ffill()
    glitch = _series(rng, 0.0003, 0.005)
    glitch.iloc[400:] *= 10.0                                        # one x10 scale seam at day 400
    funds["GLITCH"] = glitch
    zero = _series(rng, 0.0003, 0.005)
    zero.iloc[300] = 0.0                                             # non-positive NAV
    funds["ZERO"] = zero
    funds["FLAT"] = pd.Series(100.0, index=BDAYS)                    # sigma 0, liquidity 1.0
    rows = []
    for isin, s in funds.items():
        s = s.dropna()
        rows.append(pd.DataFrame({"isin": isin, "date": s.index, "nav": s.to_numpy()}))
    return pd.concat(rows, ignore_index=True).sample(frac=1.0, random_state=1)     # shuffled: order must not matter


def _oracle(daily: pd.DataFrame, isin: str, t: pd.Timestamp) -> dict:
    nav = daily[(daily["isin"] == isin) & (daily["date"] <= t)].sort_values("date")[["date", "nav"]].reset_index(drop=True)
    out = {}
    for label, n, min_obs, wanted in (
        ("6m", WINDOW_6M, MIN_OBS_6M, ("short_max_drawdown", "short_liquidity_flag")),
        ("3m", WINDOW_3M, MIN_OBS_3M, ("short_vol_adj",)),
    ):
        win = nav.tail(n).reset_index(drop=True)
        if len(win) < min_obs:
            continue
        ok, _ = p2_validators.validate_nav(win)
        if not ok:
            continue
        vals = {m: v for m, v, _ in p2_short.compute_short_horizon_metrics(win, None)}
        for m in wanted:
            if m in vals:
                out[f"{m}_{label}"] = vals[m]
    return out


@pytest.fixture(scope="module")
def daily():
    return _daily()


AT = pd.DatetimeIndex(sorted(list(pd.date_range("2019-03-31", "2021-12-31", freq=pd.offsets.MonthEnd()))
                             + [pd.Timestamp("2020-07-15"), pd.Timestamp("2021-06-02")]))


def test_matches_p2_window_by_window(daily):
    metrics, _ = short_gate_metrics(daily, AT)
    checked = present = nan_expected = 0
    for isin in metrics[SHORT_GATE_METRICS[0]].columns:
        for t in AT:
            exp = _oracle(daily, isin, t)
            for m in SHORT_GATE_METRICS:
                got = metrics[m].loc[t, isin]
                if m in exp:
                    assert got == pytest.approx(exp[m], rel=1e-9, abs=1e-9), f"{m} {isin} {t.date()}: {got} != {exp[m]}"
                    present += 1
                else:
                    assert np.isnan(got), f"{m} {isin} {t.date()}: P2 skips/clears this window but got {got}"
                    nan_expected += 1
                checked += 1
    assert present > 150 and nan_expected > 40                       # both branches exercised
    assert checked == len(AT) * 8 * 3


def test_scale_seam_and_nonpositive_nav_make_windows_invalid_then_recover(daily):
    metrics, cov = short_gate_metrics(daily, AT)
    # GLITCH seam at business-day index 400 (~2020-07-13): 6m windows ending before ~2021-01 contain it
    seam = BDAYS[400]
    in_window = AT[(AT >= seam) & (AT < BDAYS[400 + WINDOW_6M])]
    after = AT[AT >= BDAYS[400 + WINDOW_6M + 5]]
    assert metrics["short_max_drawdown_6m"].loc[in_window, "GLITCH"].isna().all()
    assert metrics["short_max_drawdown_6m"].loc[after, "GLITCH"].notna().all()
    zero_day = BDAYS[300]
    zero_in = AT[(AT >= zero_day) & (AT < BDAYS[300 + WINDOW_6M])]
    assert metrics["short_max_drawdown_6m"].loc[zero_in, "ZERO"].isna().all()
    assert cov["invalid"].sum() > 0


def test_young_fund_is_fail_open_until_it_has_the_minimum_daily_observations(daily):
    metrics, _ = short_gate_metrics(daily, AT)
    short = metrics["short_max_drawdown_6m"]["SHORT"]
    vol3 = metrics["short_vol_adj_3m"]["SHORT"]
    assert short.isna().all()                                        # 60 obs < 90 needed for the 6m window
    assert vol3.loc[AT[-1]] == pytest.approx(_oracle(daily, "SHORT", AT[-1])["short_vol_adj_3m"])


def test_illiquid_fund_reports_a_high_liquidity_flag_so_the_scorer_bypasses_the_gate(daily):
    metrics, _ = short_gate_metrics(daily, AT)
    flag = metrics["short_liquidity_flag_6m"]["LIQ"].dropna()
    assert (flag > 0.20).all()                                       # scorer: SHORT_LIQUIDITY_TRUST_THRESHOLD
    assert metrics["short_liquidity_flag_6m"]["CALM"].dropna().lt(0.05).all()


def test_flat_fund_has_no_volatility_value_and_full_liquidity_flag(daily):
    metrics, _ = short_gate_metrics(daily, AT)
    assert metrics["short_vol_adj_3m"]["FLAT"].isna().all()          # sigma == 0 -> NaN (P2)
    assert (metrics["short_liquidity_flag_6m"]["FLAT"].dropna() == 1.0).all()
    assert (metrics["short_max_drawdown_6m"]["FLAT"].dropna() == 0.0).all()


def test_value_at_t_does_not_depend_on_later_data(daily):
    full, _ = short_gate_metrics(daily, AT)
    cut_t = pd.Timestamp("2020-09-30")
    part, _ = short_gate_metrics(daily[daily["date"] <= cut_t], AT[AT <= cut_t])
    for m in SHORT_GATE_METRICS:
        a = full[m].loc[AT[AT <= cut_t]]
        b = part[m].reindex(columns=a.columns)
        pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-12)


def test_staleness_drops_funds_that_stopped_reporting(daily):
    t = BDAYS[-1]                                                    # last day with data: live funds are fresh here
    at = pd.DatetimeIndex([t])
    unlimited, cov_u = short_gate_metrics(daily, at)
    limited, cov_l = short_gate_metrics(daily, at, max_stale_days=10)
    assert not np.isnan(unlimited["short_max_drawdown_6m"].loc[t, "STALE"])    # P2: tail of a dead fund still used
    assert np.isnan(limited["short_max_drawdown_6m"].loc[t, "STALE"])          # PIT: no longer observable
    assert cov_l.loc[t, "stale"] >= 1 and cov_u.loc[t, "stale"] == 0
    live = limited["short_max_drawdown_6m"].loc[t, "CALM"]
    assert live == pytest.approx(unlimited["short_max_drawdown_6m"].loc[t, "CALM"])


def test_coverage_table_and_fail_open_logging(daily, caplog):
    early = pd.DatetimeIndex(["2018-06-30", "2019-02-28", "2019-03-31", "2021-12-31"])    # data starts 2019-01-01
    with caplog.at_level("INFO", logger="proyecto3.src.pit_short_horizon"):
        _, cov = short_gate_metrics(daily, early)
    assert cov.loc["2018-06-30", "funds_with_daily"] == 0                      # nothing observable yet
    assert cov.loc["2019-02-28", "evaluable_6m"] == 0                          # < 90 daily obs so far
    assert cov.loc["2021-12-31", "evaluable_6m"] > 0
    assert 0.0 < cov.loc["2021-12-31", "evaluable_share_6m"] <= 1.0
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "fail-open on 3 of 4 dates" in messages and "PIT coverage" in messages


def test_empty_input_does_not_crash():
    empty = pd.DataFrame({"isin": pd.Series(dtype=str), "date": pd.Series(dtype="datetime64[ns]"), "nav": pd.Series(dtype=float)})
    metrics, cov = short_gate_metrics(empty, pd.DatetimeIndex(["2020-01-31"]))
    assert all(df.shape == (1, 0) for df in metrics.values()) and len(cov) == 1
