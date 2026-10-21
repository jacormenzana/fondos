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


# ------------------------------------------------------------------ parts B and C: capture and persistence
from proyecto3.src.pit_peer_metrics import (alpha_persistence, capture_ratios, trailing_alpha_persistence,   # noqa: E402
                                            trailing_capture_ratios)


def _peers_panel():
    rng = np.random.default_rng(31)
    n, dates = 130, pd.date_range("2008-01-31", periods=130, freq=pd.offsets.MonthEnd())
    starts = {"A1": 0, "A2": 0, "A3": 12, "A4": 20, "A5": 0, "B1": 0, "B2": 40}
    cols = {}
    for name, start in starts.items():
        s = pd.Series(100.0 * np.cumprod(1.0 + rng.normal(0.004, 0.025, n)), index=dates)
        s.iloc[:start] = np.nan
        cols[name] = s
    panel = pd.DataFrame(cols)
    panel.loc[dates[70:73], "A5"] = np.nan                              # a 3-month hole: windows containing it are not full
    nature = pd.Series({c: ("A" if c.startswith("A") else "B") for c in panel.columns})
    return panel, nature


def _slice(panel, t):
    return panel[(panel.index > t - pd.DateOffset(months=W)) & (panel.index <= t)]


def _same(got, exp, msg):
    if np.isnan(exp):
        assert np.isnan(got), msg
    else:
        assert got == pytest.approx(exp, rel=1e-9, abs=1e-12), (msg, got, exp)


def test_trailing_capture_equals_the_expanding_capture_of_the_window_rows():
    panel, nature = _peers_panel()
    res = trailing_capture_ratios(panel, nature, W)
    checked = 0
    for t in panel.index[W + 2::9]:
        sl = _slice(panel, t)
        exp = capture_ratios(sl, nature)
        for fund in panel.columns:
            if np.isnan(panel.loc[t, fund]):
                continue
            full = sl[fund].notna().sum() == W                              # the V2 rule: a bar in every month of the window
            for metric in ("upside_capture", "downside_capture", "capture_ratio"):
                if full:
                    _same(res[metric].loc[t, fund], exp[metric].loc[t, fund], (metric, fund, t.date()))
                    checked += 1
                else:
                    assert np.isnan(res[metric].loc[t, fund]), (metric, fund, t.date(), "partial window must be NaN")
    assert checked > 60
    assert res["capture_ratio"].loc[panel.index[-1], ["A1", "A2", "B1"]].notna().any()


def test_trailing_persistence_equals_the_expanding_persistence_of_the_window_rows():
    panel, nature = _peers_panel()
    res = trailing_alpha_persistence(panel, nature, W)
    checked = valid = 0
    for t in panel.index[W + 2::6]:
        sl = _slice(panel, t)
        exp = alpha_persistence(sl, nature)
        for fund in panel.columns:
            if np.isnan(panel.loc[t, fund]):
                continue
            if sl[fund].notna().sum() == W:                                 # a full window: the oracle applies
                for metric in ("alpha_persistence", "alpha_persistence_n"):
                    _same(res[metric].loc[t, fund], exp[metric].loc[t, fund], (metric, fund, t.date()))
                checked += 1
                valid += int(not np.isnan(res["alpha_persistence"].loc[t, fund]))
            else:
                assert np.isnan(res["alpha_persistence"].loc[t, fund]), (fund, t.date(), "partial window must be NaN")
    assert checked > 25 and valid > 10, (checked, valid)


def test_persistence_values_are_shares_of_four_or_five_windows():
    panel, nature = _peers_panel()
    res = trailing_alpha_persistence(panel, nature, W)
    n = res["alpha_persistence_n"].stack()
    p = res["alpha_persistence"].stack()
    assert len(p) > 0 and set(n.unique()) <= {4.0, 5.0}                     # 36 / 6 / min 4: a 60-month window holds 4 or 5 windows
    assert ((p >= 0) & (p <= 1)).all()
    assert ((p * n).round(9) % 1 == 0).all()                                # a share of whole windows


def test_the_v2_chain_needs_no_guard_flag_any_more():
    text = (_ROOT / "proyecto3" / "src" / "pit_run.py").read_text(encoding="utf-8")
    assert "_V2_PEERS_WINDOWED" not in text and "NotImplementedError" not in text
