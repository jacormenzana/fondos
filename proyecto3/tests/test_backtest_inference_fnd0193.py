# proyecto3/tests/test_backtest_inference_fnd0193.py
# -*- coding: utf-8 -*-
"""
Inference pieces of the P3 model-value study (FND-0193): Holm step-down, ledger-aware deflated Sharpe, CSCV probability of backtest
overfitting, bootstrap drawdown probability. Synthetic cases whose answer is known; no DB (R-7).

    python -m pytest proyecto3/tests/test_backtest_inference_fnd0193.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.backtest_inference import (cscv_pbo, deflated_sharpe_ratio, drawdown_probability, expected_max_sharpe,
                                              holm_stepdown, ledger_deflated_sharpe)


def test_holm_stepdown_stops_at_the_first_failure():
    out = holm_stepdown({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.20}, alpha=0.05)
    assert out["a"]["reject"] is True                       # 0.01 <= 0.05 / 4
    assert out["c"]["reject"] is False                      # 0.03 > 0.05 / 3: stop, so b (0.04) fails as well
    assert out["b"]["reject"] is False and out["d"]["reject"] is False
    assert out["a"]["p_adj"] == pytest.approx(0.04) and out["c"]["p_adj"] >= out["a"]["p_adj"]
    assert holm_stepdown({"x": 0.001, "y": 0.002})["y"]["reject"] is True
    assert holm_stepdown({"x": float("nan")})["x"]["reject"] is False


def test_ledger_dsr_converts_the_annual_variance_and_falls_with_more_trials():
    rng = np.random.default_rng(1)
    r = rng.normal(0.004, 0.012, 260)
    var_annual = 0.030465
    assert ledger_deflated_sharpe(r, 437, var_annual) == pytest.approx(deflated_sharpe_ratio(r, 437, var_annual / 12.0))
    assert ledger_deflated_sharpe(r, 874, var_annual) < ledger_deflated_sharpe(r, 437, var_annual) < ledger_deflated_sharpe(r, 1, var_annual)
    assert expected_max_sharpe(874, var_annual / 12.0) > expected_max_sharpe(437, var_annual / 12.0) > 0


def _months(n):
    return pd.date_range("2005-01-31", periods=n, freq=pd.offsets.MonthEnd())


def test_pbo_is_about_one_half_for_pure_noise_and_near_zero_for_a_dominant_config():
    rng = np.random.default_rng(5)
    noise = pd.DataFrame(rng.normal(0.0, 0.02, (256, 40)), index=_months(256), columns=[f"c{i}" for i in range(40)])
    res = cscv_pbo(noise, n_blocks=16)
    assert res["n_splits"] == 12870 and res["n_configs"] == 40 and res["months_used"] == 256
    assert 0.30 < res["pbo"] < 0.70
    dom = noise.copy()
    dom["winner"] = rng.normal(0.02, 0.02, 256)              # a real edge (Sharpe ~3.5 annualised) that holds in every block
    res2 = cscv_pbo(dom, n_blocks=16)
    assert res2["pbo"] < 0.05 and res2["oos_rank_mean"] > 0.9


def test_pbo_drops_leading_months_that_do_not_fill_a_block_and_rejects_bad_input():
    x = pd.DataFrame(np.random.default_rng(2).normal(0, 0.01, (170, 5)), index=_months(170), columns=list("abcde"))
    assert cscv_pbo(x, n_blocks=16)["months_used"] == 160
    assert np.isnan(cscv_pbo(x[["a"]], n_blocks=16)["pbo"])                 # one configuration: nothing to overfit
    assert np.isnan(cscv_pbo(x.iloc[:20], n_blocks=16)["pbo"])             # blocks too short


def test_drawdown_probability_extremes_and_determinism():
    calm = np.full(120, 0.004)
    assert drawdown_probability(calm) == 0.0
    rng = np.random.default_rng(3)
    wild = rng.normal(-0.01, 0.06, 240)
    p = drawdown_probability(wild)
    assert p > 0.9 and p == drawdown_probability(wild)                      # fixed seed: reproducible
    assert np.isnan(drawdown_probability(calm[:20]))                        # shorter than the 36-month horizon
    mild = rng.normal(0.004, 0.012, 240)
    assert drawdown_probability(mild) < drawdown_probability(wild)
