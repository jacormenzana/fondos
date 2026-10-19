# proyecto3/tests/test_pit_baselines.py
# -*- coding: utf-8 -*-
"""
Baselines and ablation inputs of the P3 model-value study (proyecto3/src/pit_baselines.py) -- FND-0193.

Synthetic, hand-checkable cases; no DB (R-7). The key invariants:
* B-POOL weights through the model's own return layer give EXACTLY the legacy `pool_benchmark` when every fund has a return;
* the static mix is the unweighted mean of the seven regime rows and sums to 1;
* an incumbent bonus keeps a fund the plain ranking would drop; the default engine weighting is untouched, `weight_method` only equalises;
* the input snapshot of a run round-trips.

    python -m pytest proyecto3/tests/test_pit_baselines.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_backtest import cash_index, pool_benchmark, portfolio_returns
from proyecto3.src.pit_baselines import (balanced_weights, flatten_score_detail, multiplier_variant, pool_weights, rank_weights,
                                         static_mix, static_regime_hist, trailing_metrics)
from proyecto3.src.pit_inputs import has_inputs_snapshot, load_inputs_snapshot, save_inputs_snapshot
from proyecto3.src.pit_run import PitInputs
from proyecto3.src.portfolio_engine import DEFAULT_CONSTRAINTS, PortfolioConstraints, assign_weights

MONTHS = pd.date_range("2010-01-31", periods=48, freq=pd.offsets.MonthEnd())
AT = MONTHS[36:40]


def _hist(index=MONTHS, w=(0.5, 0.3, 0.2)):
    return pd.DataFrame({"regime": "Expansion", "weight_defensive": w[0], "weight_balanced": w[1], "weight_dynamic": w[2]}, index=index)


def _scores(rows):
    """rows: (sub, isin, nature, eligible) repeated for every date of AT."""
    recs = []
    for t in AT:
        for sub, isin, nature, ok in rows:
            recs.append({"as_of": t, "regime": "Expansion", "isin": isin, "subportfolio": sub, "fund_nature": nature, "fund_name": isin,
                         "fund_family_id": isin, "score_base": 1.0, "multiplier": 1.0, "score_final": 1.0 if ok else 0.0, "eligible": ok,
                         "exclusion_reason": None if ok else "x"})
    return pd.DataFrame(recs)


ROWS = [("Defensiva", "D1", "Monetario", True), ("Defensiva", "D2", "Renta Fija Corto Plazo", True), ("Defensiva", "D3", "Renta Fija Flexible", False),
        ("Equilibrada", "E1", "Mixtos", True), ("Equilibrada", "E2", "Renta Variable", True), ("Equilibrada", "E3", "Renta Fija Flexible", True),
        ("Dinamica", "V1", "Renta Variable", True), ("Dinamica", "V2", "Alternativo", True)]


def test_static_mix_is_the_unweighted_regime_mean():
    m = static_mix()
    assert sum(m) == pytest.approx(1.0)
    assert m[0] == pytest.approx(325 / 700) and m[1] == pytest.approx(255 / 700) and m[2] == pytest.approx(120 / 700)
    h = static_regime_hist(_hist())
    assert (h["weight_defensive"] == m[0]).all() and (h["regime"] == "Expansion").all()


def test_pool_weights_equal_the_legacy_pool_benchmark_when_every_fund_has_a_return():
    sc = _scores(ROWS)
    hist = _hist()
    W, cash = pool_weights(sc, hist, AT)
    assert W.loc[AT[0]].sum() == pytest.approx(1.0) and cash.loc[AT[0]] == pytest.approx(0.0)
    assert W.loc[AT[0], "D1"] == pytest.approx(0.5 / 2) and W.loc[AT[0], "V2"] == pytest.approx(0.2 / 2)
    rng = np.random.default_rng(7)
    isins = [r[1] for r in ROWS]
    R = pd.DataFrame(rng.normal(0.004, 0.02, (len(AT), len(isins))), index=AT, columns=isins)
    cash_ret = pd.Series(0.001, index=AT)
    got, _ = portfolio_returns(W, R, cash_ret)
    exp = pool_benchmark(sc, R, cash_ret, hist, AT)
    assert np.allclose(got.to_numpy(), exp.to_numpy(), atol=2e-6)


def test_empty_sub_portfolio_leaves_its_weight_in_cash():
    rows = [r for r in ROWS if r[0] != "Dinamica"]
    W, cash = pool_weights(_scores(rows), _hist(), AT)
    assert cash.loc[AT[0]] == pytest.approx(0.2) and W.loc[AT[0]].sum() == pytest.approx(0.8)


def test_rank_weights_take_the_top_k_and_hysteresis_keeps_an_incumbent():
    rows = [("Defensiva", f"D{i}", "Monetario", True) for i in range(6)]
    sc = _scores(rows)
    hist = _hist(w=(1.0, 0.0, 0.0))
    isins = [r[1] for r in rows]
    metric = pd.DataFrame(0.0, index=AT, columns=isins)
    metric.loc[AT[0]] = [6, 5, 4, 3, 2, 1]                      # higher = better: D0..D2 win with k=3
    metric.loc[AT[1]] = [6, 5, 3.9, 4, 2, 1]                    # D3 (4) now outranks D2 (3.9) -> plain ranking swaps them
    metric.loc[AT[2]] = metric.loc[AT[1]]
    metric.loc[AT[3]] = metric.loc[AT[1]]
    plain, _ = rank_weights(sc, hist, AT, metric, higher_is_better=True, k=3)
    assert set(plain.loc[AT[0]].dropna().index) == {"D0", "D1", "D2"} and plain.loc[AT[0]].dropna().eq(1 / 3).all()
    assert set(plain.loc[AT[1]].dropna().index) == {"D0", "D1", "D3"}
    sticky, _ = rank_weights(sc, hist, AT, metric, higher_is_better=True, k=3, hysteresis_band=0.5)
    assert set(sticky.loc[AT[1]].dropna().index) == {"D0", "D1", "D2"}       # incumbent bonus keeps D2
    low, _ = rank_weights(sc, hist, AT, metric, higher_is_better=False, k=2)
    assert set(low.loc[AT[0]].dropna().index) == {"D5", "D4"}


def test_rank_weights_skip_funds_without_a_metric_and_cross_sub_duplicates():
    sc = _scores([("Defensiva", "A", "Monetario", True), ("Defensiva", "B", "Monetario", True), ("Equilibrada", "A", "Mixtos", True),
                  ("Equilibrada", "C", "Mixtos", True)])
    metric = pd.DataFrame({"A": 3.0, "B": np.nan, "C": 1.0}, index=AT)
    W, _ = rank_weights(sc, _hist(w=(0.5, 0.5, 0.0)), AT, metric, higher_is_better=True, k=1)
    assert W.loc[AT[0]].dropna().to_dict() == {"A": 0.5, "C": 0.5}           # B not rankable; A already used by Defensiva


def test_trailing_metrics_use_only_nav_up_to_the_date():
    rng = np.random.default_rng(3)
    nav = pd.DataFrame({"F": 100 * np.cumprod(1 + rng.normal(0.004, 0.02, 48)), "G": 100 * np.cumprod(1 + rng.normal(0.004, 0.05, 48))}, index=MONTHS)
    m = trailing_metrics(nav, AT, 60)
    assert (m["vol_36m"].loc[AT[0]]["G"] > m["vol_36m"].loc[AT[0]]["F"])
    t = AT[0]
    expect = nav.loc[t, "F"] / nav.loc[t - pd.offsets.MonthEnd(12), "F"] - 1.0
    assert m["ret_12m"].loc[t, "F"] == pytest.approx(expect)
    nav2 = nav.copy()
    nav2.loc[MONTHS[-1], :] = 1e9                                           # a later NAV must not move the metric at AT[0]
    m2 = trailing_metrics(nav2, AT, 60)
    assert m2["vol_36m"].loc[t, "F"] == pytest.approx(m["vol_36m"].loc[t, "F"])


def test_balanced_weights_sleeves():
    W, cash = balanced_weights(_scores(ROWS), AT)
    row = W.loc[AT[0]].dropna()
    assert row["E2"] == pytest.approx(0.6 / 2) and row["V1"] == pytest.approx(0.6 / 2)       # two equity funds share 60%
    assert row["D2"] == pytest.approx(0.4 / 2) and row["E3"] == pytest.approx(0.4 / 2)      # D3 is not eligible
    assert row.sum() == pytest.approx(1.0) and "D1" not in row.index and "V2" not in row.index


def test_flatten_detail_and_multiplier_variant():
    sc = _scores(ROWS[:3]).iloc[:3].copy()
    sc["score_base"] = [2.0, 4.0, 1.0]
    sc["multiplier"] = [1.15 * 0.8, 1.0, 1.0]
    sc["score_final"] = [2.0 * 1.15 * 0.8, 4.0, 0.0]
    sc["detail"] = [{"mult_detail": {"alpha_persistence_bonus": 1.15, "fx_malus": 0.8}, "metrics": {"sharpe": 0.5, "srri": None}},
                    {"mult_detail": {}, "metrics": {"sharpe": 0.2}}, {"mult_detail": {"fx_malus": 0.8}, "metrics": {}}]
    det = flatten_score_detail(sc)
    assert det["m_fx_malus"].tolist()[0] == 0.8 and np.isnan(det["m_fx_malus"].tolist()[1]) and np.isnan(det["x_srri"].tolist()[0])
    base_only = multiplier_variant(sc.drop(columns=["detail"]))
    assert base_only["score_final"].tolist() == [2.0, 4.0, 0.0]                          # excluded fund stays 0
    only_alpha = multiplier_variant(sc.drop(columns=["detail"]), det, keep=("alpha_persistence_bonus",))
    assert only_alpha["score_final"].tolist() == pytest.approx([2.0 * 1.15, 4.0, 0.0])
    no_fx = multiplier_variant(sc.drop(columns=["detail"]), det, drop=("fx_malus",))
    assert no_fx["score_final"].tolist() == pytest.approx([2.0 * 1.15, 4.0, 0.0])
    full = multiplier_variant(sc.drop(columns=["detail"]), det)
    assert full["score_final"].tolist() == pytest.approx(sc["score_final"].tolist())      # all factors back = the model's own score


def test_engine_default_weighting_follows_production_and_weight_method_selects_it():
    """FND-0193 SIMPLIFY: the engine default is shared.config.PORTFOLIO_WEIGHT_METHOD (the live builder's), "equal" since the verdict;
    the study's pre-registered model asks for "score_proportional" explicitly."""
    from shared.config import PORTFOLIO_WEIGHT_METHOD
    from proyecto3.src.pit_value import MODEL_WEIGHT_METHOD
    df = pd.DataFrame({"isin": list("ABCDEFGH"), "score_total": [8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]})
    assert PortfolioConstraints().weight_method == DEFAULT_CONSTRAINTS.weight_method == PORTFOLIO_WEIGHT_METHOD == "equal"
    assert MODEL_WEIGHT_METHOD == "score_proportional"
    sp = assign_weights(df, PortfolioConstraints(weight_method="score_proportional"))["weight"]
    assert sp.iloc[0] > sp.iloc[-1] and sp.sum() == pytest.approx(1.0)                     # score-proportional on request
    eq = assign_weights(df, DEFAULT_CONSTRAINTS)["weight"]
    assert eq.tolist() == pytest.approx([0.125] * 8)


def test_snap_nav_to_month_end_collapses_the_two_date_conventions_and_drops_a_partial_last_month():
    from proyecto3.src.pit_inputs import snap_nav_to_month_end
    idx = pd.to_datetime(["2026-01-30", "2026-01-31", "2026-02-27", "2026-02-28", "2026-03-31", "2026-04-15"])   # weekday and calendar stamps
    nav = pd.DataFrame({"A": [100.0, np.nan, 101.0, np.nan, 102.0, 103.0], "B": [np.nan, 50.0, np.nan, 51.0, 52.0, np.nan]}, index=idx)
    snapped = snap_nav_to_month_end(nav)
    assert list(snapped.index) == list(pd.to_datetime(["2026-01-31", "2026-02-28", "2026-03-31"]))          # April is partial (15th): dropped
    assert snapped.loc["2026-01-31"].tolist() == [100.0, 50.0] and snapped.loc["2026-02-28"].tolist() == [101.0, 51.0]
    assert snapped.index.is_unique and snap_nav_to_month_end(snapped).equals(snapped)                         # idempotent
    complete = nav.iloc[:5]
    assert len(snap_nav_to_month_end(complete)) == 3                                                          # last stamp is the month-end: kept
    assert snap_nav_to_month_end(nav.iloc[:0]).empty


def test_input_snapshot_round_trip(tmp_path):
    nav = pd.DataFrame({"AAA": [100.0, 101.0], "BBB": [50.0, np.nan]}, index=pd.to_datetime(["2020-01-31", "2020-02-29"]))
    attrs = pd.DataFrame({"Fund_Nature": ["Mixtos", "Monetario"], "Ongoing_Charge": [1.2, 0.3]}, index=pd.Index(["AAA", "BBB"], name="isin"))
    rate = pd.DataFrame({"date": pd.to_datetime(["2020-01-31"]), "rate": [0.01]})
    fx = pd.Series([True, False], index=pd.Index(["AAA", "BBB"], name="isin"), dtype=bool)
    hist = _hist(index=nav.index)
    target = pd.Series([0.06, 0.061], index=nav.index)
    assert not has_inputs_snapshot(tmp_path)
    save_inputs_snapshot(tmp_path, PitInputs(nav=nav, attrs=attrs, ipc=None, rate=rate, fx_exposed=fx), hist, target)
    assert has_inputs_snapshot(tmp_path)
    inputs, hist2, target2 = load_inputs_snapshot(tmp_path)
    pd.testing.assert_frame_equal(inputs.nav, nav, check_freq=False)
    pd.testing.assert_frame_equal(inputs.attrs, attrs)
    pd.testing.assert_frame_equal(hist2, hist, check_freq=False)
    assert inputs.fx_exposed.to_dict() == {"AAA": True, "BBB": False}
    assert target2.tolist() == [0.06, 0.061] and inputs.rate["rate"].tolist() == [0.01]
