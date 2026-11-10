# proyecto3/tests/test_p2_vol_floor_impact.py
# -*- coding: utf-8 -*-
"""The pure parts of scripts/audit/p2_vol_floor_impact.py (the read-only simulation of a volatility floor at the source for the P2 ratio metrics, owner order 2026-11-10).

R-7: no DB; synthetic frames. The script runs the scorer's and the builder's own code on the live store (read-only) when the simulation is produced; these tests pin the arithmetic around it.
"""
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT, _ROOT / "proyecto3"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np
import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location("p2_vol_floor_impact", _ROOT / "scripts" / "audit" / "p2_vol_floor_impact.py")
vf = importlib.util.module_from_spec(spec)
sys.modules["p2_vol_floor_impact"] = vf
spec.loader.exec_module(vf)


def _panel(rows):
    return pd.DataFrame(rows, columns=["isin", "nature", "metric", "value", "source_rows"])


def test_ratio_rows_carry_the_smallest_volatility_of_their_own_denominator_and_their_family_and_kind():
    p = _panel([("A", "Monetario", "sharpe|since_inception|0|v1", -66.0, 200), ("A", "Monetario", "vol_ann|since_inception|0|v1", 0.0037, 200), ("A", "Monetario", "vol_ann|since_inception|1|v1", 0.0021, 200),
                ("A", "Monetario", "sortino_contraccion|since_inception|0|v1", -11.0, 200), ("A", "Monetario", "vol_ann_contraccion|since_inception|0|v1", 0.004, 200),
                ("A", "Monetario", "ret_vol_simple|crisis_2022|0|v1", 3.0, 36), ("A", "Monetario", "vol_ann|crisis_2022|0|v1", 0.01, 36),
                ("A", "Monetario", "max_dd|since_inception|0|v1", -0.02, 200), ("B", "Mixtos", "sharpe|since_inception|0|v1", 0.5, 100)])
    by ={(row.isin, row.metric): row for row in vf.ratio_rows(p).itertuples()}
    a = by[("A", "sharpe|since_inception|0|v1")]
    assert a.vol == pytest.approx(0.0021) and a.family == "sharpe" and a.kind == "since_inception"                 # the smaller of the nominal and real variants
    assert by[("A", "sortino_contraccion|since_inception|0|v1")].kind == "regime" and by[("A", "sortino_contraccion|since_inception|0|v1")].family == "sortino"
    assert by[("A", "ret_vol_simple|crisis_2022|0|v1")].kind == "crisis_window" and by[("A", "ret_vol_simple|crisis_2022|0|v1")].family == "ret_vol_simple"
    assert np.isnan(by[("B", "sharpe|since_inception|0|v1")].vol)                                                      # no denominator in the panel: unknown
    assert ("A", "max_dd|since_inception|0|v1") not in by                                                              # not a ratio


def test_stored_impact_counts_rows_funds_and_alarms_per_floor():
    rows = pd.DataFrame({"isin": ["A", "A", "B", "C"], "nature": ["Monetario"] * 3 + ["Mixtos"], "metric": ["sharpe|h|0|v1", "sortino|h|0|v1", "sharpe|h|0|v1", "sharpe|h|0|v1"],
                         "family": ["sharpe", "sortino", "sharpe", "sharpe"], "kind": ["since_inception"] * 4, "vol": [0.0005, 0.0005, 0.004, 0.05]})
    scored = pd.DataFrame({"isin": ["A", "A", "B", "C"], "metric": ["sharpe|h|0|v1", "sortino|h|0|v1", "sharpe|h|0|v1", "sharpe|h|0|v1"], "z": [60.0, 2.0, -9.0, 8.0], "adequate": [True, True, True, False]})
    out = vf.stored_impact(rows, scored, (0.001, 0.005))
    assert out["ratio_rows"] == 4 and out["ratio_funds"] == 3 and out["alarm_rows_total"] == 2                          # A.sharpe (60) and B.sharpe (-9); C is a small sample
    lo, hi = out["by_floor"]["0.001"], out["by_floor"]["0.005"]
    assert (lo["rows"], lo["funds"], lo["by_family"], lo["alarm_rows_withdrawn"], lo["alarm_rows_left"]) == (2, 1, {"sharpe": 1, "sortino": 1}, 1, 1)
    assert (hi["rows"], hi["funds"], hi["alarm_rows_withdrawn"], hi["alarm_rows_left"]) == (3, 2, 2, 0)
    assert hi["funds_by_nature"] == {"Monetario": 2}


def _frame():
    return pd.DataFrame({"Fund_Nature": ["Monetario", "Monetario", "Monetario", "Mixtos"], "sharpe": [1.0, 3.0, 9.0, 0.5], "sharpe_expansion": [1.0, 3.0, 9.0, 0.5],
                         "sortino_expansion": [2.0, 4.0, 8.0, 1.0]}, index=["M1", "M2", "M3", "X1"])


def _vol():
    return pd.DataFrame({"si": [0.002, 0.004, 0.03, 0.05], "reg": [0.002, 0.03, 0.03, 0.05]}, index=["M1", "M2", "M3", "X1"])


def test_the_floor_withdraws_the_sharpe_of_funds_under_it_and_only_those():
    sim, affected = vf.apply_floor(_frame(), _vol(), "expansion", 0.003, with_sortino=False)
    assert affected == {"M1"} and np.isnan(sim.at["M1", "sharpe"]) and np.isnan(sim.at["M1", "sharpe_expansion"]) and sim.at["M1", "sortino_expansion"] == 2.0
    assert sim.at["M2", "sharpe"] == 3.0 and sim.at["X1", "sharpe"] == 0.5
    assert not _frame().equals(sim) and _frame().at["M1", "sharpe"] == 1.0                                              # the input frame is not modified


def test_a_higher_floor_reaches_more_funds_and_the_regime_denominator_is_its_own():
    sim, affected = vf.apply_floor(_frame(), _vol(), "expansion", 0.005, with_sortino=True)
    assert affected == {"M1", "M2"}
    assert np.isnan(sim.at["M2", "sharpe"]) and sim.at["M2", "sharpe_expansion"] == 3.0                                   # M2's regime volatility (0.03) is above the floor
    assert np.isnan(sim.at["M1", "sortino_expansion"]) and sim.at["M2", "sortino_expansion"] == 4.0                       # the Sortino follows its own regime denominator


def test_the_neutral_variant_fills_with_the_median_of_the_nature_over_the_funds_that_kept_theirs():
    sim, affected = vf.apply_floor(_frame(), _vol(), "expansion", 0.005, with_sortino=False, neutral=True)
    assert affected == {"M1", "M2"} and sim.at["M1", "sharpe"] == 9.0 and sim.at["M2", "sharpe"] == 9.0               # the only Monetario fund that kept its Sharpe is M3 (9.0)
    assert sim.at["X1", "sharpe"] == 0.5 and sim.at["M3", "sharpe"] == 9.0


def test_a_floor_under_every_volatility_changes_nothing():
    sim, affected = vf.apply_floor(_frame(), _vol(), "expansion", 0.0005, with_sortino=True)
    assert affected == set() and sim.equals(_frame())


def test_portfolio_metrics_are_weighted_on_the_funds_that_have_the_metric():
    df = pd.DataFrame({"return_ann_real": [0.04, 0.10, np.nan], "sharpe": [1.0, 0.5, 2.0], "max_dd": [-0.05, -0.20, -0.1], "srri_nav": [2.0, 6.0, 4.0]}, index=["A", "B", "C"])
    out = vf.portfolio_metrics({"A": 0.5, "B": 0.25, "C": 0.25}, df)
    assert out["return_ann_real"] == pytest.approx((0.5 * 0.04 + 0.25 * 0.10) / 0.75, abs=1e-4)                           # C has no return: renormalised over A and B
    assert out["sharpe"] == pytest.approx(0.5 * 1 + 0.25 * 0.5 + 0.25 * 2, abs=1e-4) and out["srri"] == pytest.approx(3.5, abs=1e-4)
    assert vf.portfolio_metrics({}, df)["sharpe"] is None
