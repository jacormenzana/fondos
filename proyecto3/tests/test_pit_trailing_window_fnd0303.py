# proyecto3/tests/test_pit_trailing_window_fnd0303.py
# -*- coding: utf-8 -*-
"""
FND-0303 V2, part A: the common trailing window of the PIT selection metrics.

`trailing_risk_metrics` must give, for a fund at date t, exactly what P2 computes on the fund's observations inside
`returns.trailing_window` (date > t - 60 months) -- the window of the stored rolling_5y -- using P2's own scalar functions as the oracle,
the same oracle test_pit_metrics.py uses for the expanding metrics. A fund without a full window is NaN (never a partial window).
No DB, no pipeline imports (R-7).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_TESTS = Path(__file__).resolve().parent
_ROOT = _TESTS.parents[1]
for p in (str(_ROOT), str(_TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import test_pit_metrics as tpm   # noqa: E402  (its panel / ipc / rf fixtures and P2 oracle)
from proyecto3.src import scorer_config   # noqa: E402
from proyecto3.src.pit_metrics import TRAILING_KEYS, expanding_risk_metrics, trailing_risk_metrics   # noqa: E402
from shared import config   # noqa: E402

W = 60


@pytest.fixture(autouse=True)
def _eur_view_off(monkeypatch):
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", False, raising=False)


@pytest.fixture(scope="module")
def result():
    panel, ipc, rf = tpm._panel(), tpm._ipc(), tpm._rf()
    return panel, ipc, rf, trailing_risk_metrics(panel, ipc, rf, window_months=W)


def _window(series: pd.Series, t) -> pd.Series:
    s = series.dropna()
    s = s.loc[:t]
    return s[s.index > t - pd.DateOffset(months=W)]


def test_the_default_is_off():
    assert scorer_config.COMMON_WINDOW_MONTHS == 0


def test_keys_and_shape(result):
    panel, _, _, res = result
    assert set(res) == set(TRAILING_KEYS) | {"n_obs"}
    for df in res.values():
        assert df.shape == panel.shape and df.index.equals(panel.index)


@pytest.mark.parametrize("fund", ["EQ", "BOND", "MM"])
def test_full_windows_match_p2_on_returns_trailing_window(result, fund):
    panel, ipc, rf, res = result
    checked = 0
    for t in panel.index[W + 5::7]:                                    # several dates spread over the history
        if np.isnan(panel.loc[t, fund]):
            continue
        win = _window(panel[fund], t)
        if len(win) < W:                                                 # a fund that started later: not evaluable yet
            assert np.isnan(res["return_ann"].loc[t, fund])
            continue
        exp = tpm._oracle(win, ipc, rf)[t]                                # P2 scalar path on exactly that window
        for metric in TRAILING_KEYS:
            got = res[metric].loc[t, fund]
            e = exp[metric]
            if e is None or (isinstance(e, float) and np.isnan(e)):
                assert np.isnan(got), (fund, t.date(), metric)
            else:
                assert got == pytest.approx(e, rel=1e-9, abs=1e-9), (fund, t.date(), metric, got, e)
        assert res["n_obs"].loc[t, fund] == W
        checked += 1
    assert checked >= 2


def test_a_partial_window_is_nan_and_n_obs_reports_the_count(result):
    panel, _, _, res = result
    young = panel["YOUNG"].dropna()                                      # 20 observations in total
    assert res["n_obs"]["YOUNG"].dropna().max() == len(young) < W
    for k in TRAILING_KEYS:
        assert res[k]["YOUNG"].isna().all(), k
    first_full = panel["EQ"].dropna().index[W - 1]
    assert res["return_ann"].loc[:first_full - pd.offsets.MonthEnd(1), "EQ"].isna().all()   # before its 60th observation
    assert np.isfinite(res["return_ann"].loc[first_full, "EQ"])


def test_a_gap_inside_the_window_makes_the_fund_not_evaluable_until_it_leaves_the_window(result):
    panel, _, _, res = result
    hole = panel.index[30:36]                                            # GAP has no NAV in these 6 months
    after = panel.index[36 + 5:36 + 5 + W]                               # windows that still contain the hole hold < 60 observations
    for t in after[:W - 6]:
        if np.isnan(panel.loc[t, "GAP"]):
            continue
        assert res["n_obs"].loc[t, "GAP"] < W
        assert np.isnan(res["return_ann"].loc[t, "GAP"])
    assert hole.size == 6


def test_the_expanding_frames_are_untouched_by_the_new_producer():
    panel, ipc, rf = tpm._panel(), tpm._ipc(), tpm._rf()
    before = expanding_risk_metrics(panel, ipc, rf)
    trailing_risk_metrics(panel, ipc, rf, window_months=W)
    after = expanding_risk_metrics(panel, ipc, rf)
    for k in before:
        pd.testing.assert_frame_equal(before[k], after[k], check_exact=True)


def test_the_input_panel_is_not_modified():
    panel = tpm._panel()
    snap = panel.copy()
    trailing_risk_metrics(panel, tpm._ipc(), tpm._rf(), window_months=W)
    pd.testing.assert_frame_equal(panel, snap, check_exact=True)
