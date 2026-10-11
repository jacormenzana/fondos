# proyecto3/tests/test_weight_normalization.py
# -*- coding: utf-8 -*-
"""
Exact weight normalisation (owner review 2026-10-11): sub-portfolio weights and master weights sum to exactly 1.0000.

Root cause of the 1.0001 seen in a built portfolio: clamp_and_renormalize rounded each weight to 4 dp, corrected the residual, and then
rounded each weight AGAIN, undoing the correction. It now rounds once, by largest remainder. Pure (R-7: no DB).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.portfolio_builder import Portfolio, SubPortfolioAllocation  # noqa: E402
from proyecto3.src.portfolio_engine import clamp_and_renormalize  # noqa: E402

LO, HI = 0.03, 0.20


def _units(series) -> list:
    return [int(round(float(v) * 10000)) for v in series]


@pytest.mark.parametrize("seed", range(40))
def test_clamped_weights_sum_to_exactly_one_and_stay_inside_the_bounds(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(5, 11))                                   # feasible: n*LO <= 1 <= n*HI
    raw = pd.Series(rng.pareto(1.5, size=n) + 0.01, index=[f"F{i}" for i in range(n)])
    w = clamp_and_renormalize(raw, LO, HI)
    assert sum(_units(w)) == 10000                                 # exactly 1.0000, not 0.9999 / 1.0001
    assert (w >= LO - 1e-12).all() and (w <= HI + 1e-12).all()     # the correction never crosses a bound
    assert all(abs(v * 10000 - round(v * 10000)) < 1e-6 for v in w)  # on the 4-dp grid


def test_weights_already_on_the_cap_are_not_pushed_over_it():
    raw = pd.Series([0.20, 0.20, 0.20, 0.20, 0.199996], index=list("ABCDE"))   # four funds at the cap and one just under
    w = clamp_and_renormalize(raw, LO, HI)
    assert sum(_units(w)) == 10000
    assert w.max() <= HI + 1e-12


def test_input_order_does_not_change_the_result():
    raw = pd.Series([0.31, 0.27, 0.18, 0.11, 0.07, 0.04, 0.02], index=list("ABCDEFG"))
    a = clamp_and_renormalize(raw, LO, HI)
    b = clamp_and_renormalize(raw.sample(frac=1.0, random_state=3), LO, HI).reindex(raw.index)
    assert a.tolist() == b.tolist()


def test_infeasible_cap_still_holds_the_cap_strictly_and_leaves_cash():
    w = clamp_and_renormalize(pd.Series([0.4, 0.3, 0.2, 0.1], index=list("ABCD")), LO, HI)   # 4 x 20% < 100%
    assert (w == HI).all() and w.sum() == pytest.approx(0.80)


@pytest.mark.parametrize("seed", range(20))
def test_master_weights_of_a_built_portfolio_sum_to_exactly_one(seed):
    rng = np.random.default_rng(1000 + seed)
    regime = rng.dirichlet(np.ones(3))
    subs = []
    for k, name in enumerate(("Defensiva", "Equilibrada", "Dinamica")):
        n = int(rng.integers(5, 11))
        raw = pd.Series(rng.pareto(1.5, size=n) + 0.01, index=[f"{name[:3]}{i}" for i in range(n)])
        w = clamp_and_renormalize(raw, LO, HI)
        funds = [{"isin": i, "weight": round(float(v), 4), "fund_nature": "X", "role": "r", "score": 0.5} for i, v in w.items()]
        subs.append(SubPortfolioAllocation(name=name, regime_weight=float(regime[k]), funds=funds))
    p = Portfolio(scenario_id="t", regime="Expansion", profile="Equilibrada", sub_portfolios=subs)
    assert sum(_units([f["master_weight"] for f in p.all_funds])) == 10000
    assert p.cash_weight == 0.0
