# proyecto3/tests/test_fund_scorer_from_df.py
# -*- coding: utf-8 -*-
"""
Pure scoring core (fund_scorer.score_funds_from_df / compute_regime_percentiles) -- FND-0159, Wave B step d2.

score_funds_from_df is the SAME code the live score_funds runs after loading from the DB and the code the
point-in-time backtester calls per date. Beyond these unit tests, the refactor that extracted it was
verified bit-identical (check_exact) against the pre-refactor score_funds on 10 synthetic scenarios.
No DB (R-7).

    python -m pytest proyecto3/tests/test_fund_scorer_from_df.py -v
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _scoring_fixture import make_scoring_frame
from proyecto3.src import fund_scorer as fs

REGIMES = ["Shock_Energetico", "Crisis_Financiera", "Expansion", "Recalentamiento"]


@pytest.fixture(scope="module")
def frame():
    return make_scoring_frame()


@pytest.mark.parametrize("regime", REGIMES)
def test_score_funds_is_a_thin_wrapper_over_the_pure_core(frame, regime, monkeypatch, capsys):
    monkeypatch.setattr(fs, "load_fund_metrics_for_scoring", lambda conn, regime=None: frame.copy())
    via_db_path = fs.score_funds(None, SimpleNamespace(regime=regime), dry_run=True)
    pure = fs.score_funds_from_df(frame.copy(), regime)
    capsys.readouterr()
    pd.testing.assert_frame_equal(via_db_path.reset_index(drop=True), pure.reset_index(drop=True), check_exact=True)


def test_core_needs_no_connection_and_returns_the_documented_columns(frame):
    res = fs.score_funds_from_df(frame, "Expansion")
    assert {"isin", "fund_name", "fund_nature", "fund_family_id", "subportfolio", "score_base", "multiplier",
            "score_final", "eligible", "exclusion_reason", "detail"} <= set(res.columns)
    assert set(res["subportfolio"]) == {"Defensiva", "Equilibrada", "Dinamica"}
    assert res["score_final"].between(0, 5).all()


def test_verbose_false_is_silent(frame, capsys):
    fs.score_funds_from_df(frame, "Expansion", verbose=False)
    assert capsys.readouterr().out == ""
    fs.score_funds_from_df(frame, "Expansion", verbose=True)
    assert "Deduplicación por familia" in capsys.readouterr().out


def test_empty_frame_returns_empty_result(frame):
    assert fs.score_funds_from_df(frame.iloc[0:0], "Expansion").empty


def _five(**over):
    base = pd.DataFrame({
        "Fund_Name": list("ABCDE"), "Fund_Nature": "Renta Fija Corto Plazo", "srri_kiid": 2.0,
        "Investment_Focus": "Global", "Credit_Quality": "Investment Grade", "Ongoing_Charge": 0.005,
        "SRRI_Quality_Flag": "OK", "fund_family_id": [None] * 5,
        "return_ann_real": [0.01, 0.02, 0.03, 0.04, 0.05],
        "sharpe": [0.1, 0.2, 0.3, 0.4, 0.5],
        "max_dd": [-0.05, -0.05, -0.05, -0.05, -0.05],
        "alpha_persistence": [0.1, 0.2, 0.3, 0.4, 0.9],
        "srri_nav": [2.0] * 5,
    }, index=pd.Index([f"F{i}" for i in range(5)], name="isin"))
    for k, v in over.items():
        base[k] = v
    return base


def test_base_score_ranks_funds_and_group_b_absence_is_neutral():
    res = fs.score_funds_from_df(_five(), "Shock_Energetico")
    d = res[res["subportfolio"] == "Defensiva"].set_index("isin")
    assert list(d["score_base"].sort_values().index) == ["F0", "F1", "F2", "F3", "F4"]   # monotone metrics -> ranks
    assert (d.loc[["F0", "F1", "F2", "F3"], "multiplier"] == 1.0).all()                   # no group B columns -> neutral
    assert d.loc["F4", "multiplier"] == pytest.approx(fs.MULT_ALPHA_BONUS)                # alpha_persistence 0.9 > 0.60
    assert d["eligible"].all()


def test_hard_filter_excludes_and_zeroes_the_final_score():
    res = fs.score_funds_from_df(_five(max_dd=[-0.05, -0.50, -0.05, -0.05, -0.05]), "Expansion")
    d = res[res["subportfolio"] == "Defensiva"].set_index("isin")
    assert not d.loc["F1", "eligible"] and d.loc["F1", "score_final"] == 0.0
    assert "max_drawdown" in d.loc["F1", "exclusion_reason"]
    assert d.loc["F0", "eligible"]


def test_regime_percentiles_unknown_regime_is_all_none(frame):
    pct = fs.compute_regime_percentiles(frame, "No_Such_Regime")
    assert set(pct) == {"regime_return_p25", "regime_return_p75", "regime_sharpe_p25", "regime_sharpe_p75",
                        "regime_sortino_p25", "regime_sortino_p75", "regime_maxdd_p25", "regime_maxdd_p75"}
    assert all(v is None for v in pct.values())


def test_regime_percentiles_match_pandas_quantiles(frame):
    pct = fs.compute_regime_percentiles(frame, "Shock_Energetico")
    assert pct["regime_return_p25"] == frame["return_ann_shock_energetico"].quantile(0.25)
    assert pct["regime_sharpe_p75"] == frame["sharpe_shock_energetico"].quantile(0.75)
    assert pct["regime_maxdd_p25"] == frame["max_dd_shock_energetico"].quantile(0.25)


def test_no_regime_history_warning_is_silent_when_not_verbose(capsys):
    # the PIT backtester has no per-regime columns (group B) on every one of its hundreds of dates
    fs.score_funds_from_df(_five(), "Shock_Energetico", verbose=False)
    assert capsys.readouterr().out == ""


def test_regime_without_history_warns_and_stays_neutral(capsys):
    pct = fs.compute_regime_percentiles(_five(), "Shock_Energetico")        # no per-regime columns at all
    assert all(v is None for v in pct.values())
    assert "no tiene métricas históricas" in capsys.readouterr().out
