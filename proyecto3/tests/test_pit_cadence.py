# proyecto3/tests/test_pit_cadence.py
# -*- coding: utf-8 -*-
"""
Rebalance cadence (FND-0217): the hold logic of build_weights, cadence_tables over every phase, and the report that
chooses on the design period and compares on the blind one. Synthetic worlds; no DB (R-7).

    python -m pytest proyecto3/tests/test_pit_cadence.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_backtest import build_weights, monthly_turnover
from proyecto3.src.pit_oos import cadence_report, cadence_tables
from proyecto3.tests.test_pit_backtest import AT, MONTHS, _attrs, _hist, chain  # noqa: F401  (chain is a fixture)

NATURES = ["Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible"]


def _scores(months, flip=True):
    """12 Defensiva candidates per month; the funds at rank 10 and 9 swap places every other month, so a fresh selection
    changes the top 10 and a held one does not."""
    rows = []
    for j, t in enumerate(months):
        a, b = (0.50, 0.60) if (j % 2 == 0 or not flip) else (0.70, 0.60)
        for i in range(12):
            score = {10: a, 9: b}.get(i, 1.0 - 0.04 * i if i < 9 else 0.45)
            rows.append(dict(as_of=t, regime="Expansion", isin=f"F{i}", subportfolio="Defensiva", fund_name=f"F{i}",
                             fund_nature=NATURES[i % 3], fund_family_id=None, score_base=score, multiplier=1.0,
                             score_final=score, eligible=True, exclusion_reason=None))
    return pd.DataFrame(rows)


def _random_scores(months, seed=7):
    """12 Defensiva candidates with fresh random scores every month: the selection changes at random, as in real data."""
    rng = np.random.default_rng(seed)
    rows = []
    for t in months:
        for i in range(12):
            score = float(rng.uniform(0.2, 1.0))
            rows.append(dict(as_of=t, regime="Expansion", isin=f"F{i}", subportfolio="Defensiva", fund_name=f"F{i}",
                             fund_nature=NATURES[i % 3], fund_family_id=None, score_base=score, multiplier=1.0,
                             score_final=score, eligible=True, exclusion_reason=None))
    return pd.DataFrame(rows)


ATS = pd.DatetimeIndex(MONTHS[50:56])
ATTRS = _attrs([f"F{i}" for i in range(12)])
HIST = _hist(MONTHS[50], weights=(1.0, 0.0, 0.0))


def _held(W, t):
    return set(W.loc[t].dropna().index)


# ---------------- build_weights ----------------

def test_cadence_one_is_the_default_behaviour():
    sc = _scores(ATS)
    default, _ = build_weights(sc, ATTRS, HIST, ATS)
    explicit, _ = build_weights(sc, ATTRS, HIST, ATS, rebalance_every=1, phase=0)
    pd.testing.assert_frame_equal(default, explicit)


def test_portfolio_is_held_between_rebalances_and_reselected_on_them():
    sc = _scores(ATS)
    W, cash = build_weights(sc, ATTRS, HIST, ATS, rebalance_every=3, phase=0)
    monthly, _ = build_weights(sc, ATTRS, HIST, ATS)
    # positions 0 and 3 rebalance; 1-2 and 4-5 hold
    pd.testing.assert_series_equal(W.loc[ATS[1]], W.loc[ATS[0]], check_names=False)
    pd.testing.assert_series_equal(W.loc[ATS[2]], W.loc[ATS[0]], check_names=False)
    pd.testing.assert_series_equal(W.loc[ATS[4]], W.loc[ATS[3]], check_names=False)
    assert _held(W, ATS[3]) == _held(monthly, ATS[3])            # a rebalance month selects like the monthly portfolio
    assert _held(W, ATS[3]) != _held(W, ATS[0])                  # and the scores moved in between
    assert cash.loc[ATS[1]] == cash.loc[ATS[0]]


def test_the_phase_moves_the_rebalance_months():
    sc = _scores(ATS)
    p0, _ = build_weights(sc, ATTRS, HIST, ATS, rebalance_every=3, phase=0)
    p1, _ = build_weights(sc, ATTRS, HIST, ATS, rebalance_every=3, phase=1)
    monthly, _ = build_weights(sc, ATTRS, HIST, ATS)
    # first date always selects; with phase 1 the next selection is at position 1, with phase 0 at position 3
    assert _held(p1, ATS[1]) == _held(monthly, ATS[1])
    assert _held(p0, ATS[1]) == _held(p0, ATS[0])


def test_turnover_falls_when_the_cadence_is_slower():
    long = pd.DatetimeIndex(MONTHS[20:80])
    sc = _random_scores(long)
    # skip the first month: entering from cash is a one-off that does not depend on the cadence
    t = {k: monthly_turnover(build_weights(sc, ATTRS, HIST, long, rebalance_every=k)[0]).iloc[1:].mean() for k in (1, 2, 3, 6)}
    assert t[1] > t[2] >= t[3] >= t[6]


def test_a_month_without_scores_empties_the_portfolio_and_forces_a_reselection():
    sc = _scores(ATS)
    sc = sc[sc["as_of"] != ATS[2]]
    W, cash = build_weights(sc, ATTRS, HIST, ATS, rebalance_every=3, phase=0)
    assert W.loc[ATS[2]].dropna().empty and np.isnan(cash[ATS[2]])
    monthly, _ = build_weights(sc, ATTRS, HIST, ATS)
    assert _held(W, ATS[3]) == _held(monthly, ATS[3])        # position 3 would rebalance anyway
    assert _held(W, ATS[4]) == _held(W, ATS[3])              # and is held afterwards


# ---------------- cadence_tables and the report ----------------

def test_cadence_tables_run_every_phase_and_slower_cadences_trade_less(chain):
    nav, attrs, regime_hist, inputs, cache, run1 = chain
    tables = cadence_tables(run1, inputs, regime_hist, AT, cadences=(1, 2, 3))
    assert set(tables) == {(1, 0), (2, 0), (2, 1), (3, 0), (3, 1), (3, 2)}
    t1 = tables[(1, 0)]["turnover"].mean()
    t3 = np.mean([tables[(3, p)]["turnover"].mean() for p in range(3)])
    assert t3 <= t1 + 1e-12
    assert {"net_turnover_1m", "cash_ret_1m", "turnover"} <= set(tables[(2, 1)].columns)


def _table(idx, mean, turnover, seed):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"net_turnover_1m": pd.Series(rng.normal(mean, 0.02, len(idx)), index=idx), "cash_ret_1m": 0.001,
                         "turnover": turnover}, index=idx)


def test_cadence_report_chooses_on_the_design_period_and_compares_blind_with_monthly():
    idx = pd.date_range("2008-01-31", periods=180, freq=pd.offsets.MonthEnd())
    tables = {(1, 0): _table(idx, 0.003, 0.30, 1)}
    base = tables[(1, 0)]
    for k, boost, turn in ((3, 0.004, 0.10), (6, -0.004, 0.05)):
        for ph in range(k):
            tables[(k, ph)] = base.assign(net_turnover_1m=base["net_turnover_1m"] + boost, turnover=turn)
    rep = cadence_report(tables, "2016-12-31", n_boot=300)
    assert rep["chosen"] == 3
    g = rep["grid"]
    assert set(g["period"]) == {"design", "blind"} and set(g["cadence_months"]) == {1, 3, 6}
    assert (g["n_phases"].groupby(g["cadence_months"]).first().to_dict()) == {1: 1, 3: 3, 6: 6}
    b = rep["blind"].set_index("cadence_months")
    assert b.loc[1, "is_reference"] and b.loc[3, "is_chosen"]
    assert b.loc[3, "diff"] > 0 > b.loc[6, "diff"] and b.loc[1, "diff"] == pytest.approx(0.0)
    d = rep["diagnostics"]
    assert d["n_trials"] == 3 and 0.0 <= d["design_deflated_sharpe"] <= 1.0 and d["blind_effective_months"] <= d["blind_months"]


# ---------------- regime trigger ----------------

def _hist_with_change(change_at):
    """Constant weights (all Defensiva); the regime label switches to 'Contraccion' from position `change_at`."""
    labels = ["Expansion" if i < change_at else "Contraccion" for i in range(len(ATS))]
    return pd.DataFrame({"regime": labels, "weight_defensive": 1.0, "weight_balanced": 0.0, "weight_dynamic": 0.0},
                        index=ATS)                                                  # label i is in force at date i


def test_a_regime_change_forces_a_rebalance_between_scheduled_ones():
    sc = _scores(ATS)
    hist = _hist_with_change(1)                       # the label changes at position 1; the scores differ between 0 and 1
    monthly, _ = build_weights(sc, ATTRS, hist, ATS)
    off, _ = build_weights(sc, ATTRS, hist, ATS, rebalance_every=3, phase=0, regime_trigger=False)
    on, _ = build_weights(sc, ATTRS, hist, ATS, rebalance_every=3, phase=0, regime_trigger=True)
    pd.testing.assert_series_equal(off.loc[ATS[1]], off.loc[ATS[0]], check_names=False)        # held through the change
    assert _held(on, ATS[1]) == _held(monthly, ATS[1])                                         # reacted at once
    assert _held(on, ATS[1]) != _held(off, ATS[1])
    pd.testing.assert_series_equal(on.loc[ATS[0]], off.loc[ATS[0]], check_names=False)          # before the change: identical
    pd.testing.assert_series_equal(on.loc[ATS[2]], on.loc[ATS[1]], check_names=False)           # and held again afterwards
    assert _held(on, ATS[3]) == _held(monthly, ATS[3])                                         # the calendar rebalance still happens


def test_without_a_regime_change_the_trigger_changes_nothing():
    sc = _scores(ATS)
    hist = _hist_with_change(99)
    off, _ = build_weights(sc, ATTRS, hist, ATS, rebalance_every=3, regime_trigger=False)
    on, _ = build_weights(sc, ATTRS, hist, ATS, rebalance_every=3, regime_trigger=True)
    pd.testing.assert_frame_equal(off, on)


def test_a_regime_set_only_triggers_on_changes_into_or_out_of_those_regimes():
    sc = _scores(ATS)
    hist = _hist_with_change(1)                       # Expansion -> Contraccion at position 1
    monthly, _ = build_weights(sc, ATTRS, hist, ATS)
    off, _ = build_weights(sc, ATTRS, hist, ATS, rebalance_every=3, regime_trigger=False)
    other, _ = build_weights(sc, ATTRS, hist, ATS, rebalance_every=3, regime_trigger={"Crisis_Financiera"})
    into, _ = build_weights(sc, ATTRS, hist, ATS, rebalance_every=3, regime_trigger={"Contraccion"})
    out_of, _ = build_weights(sc, ATTRS, hist, ATS, rebalance_every=3, regime_trigger={"Expansion"})
    pd.testing.assert_frame_equal(other, off)                                   # a change not involving the set: ignored
    assert _held(into, ATS[1]) == _held(monthly, ATS[1]) != _held(off, ATS[1])  # INTO a listed regime: reacts
    assert _held(out_of, ATS[1]) == _held(monthly, ATS[1])                      # OUT OF a listed regime: reacts too
