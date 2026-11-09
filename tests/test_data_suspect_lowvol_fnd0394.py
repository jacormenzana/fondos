# tests/test_data_suspect_lowvol_fnd0394.py
# -*- coding: utf-8 -*-
"""FND-0394 / FND-0404: the structural rule for near-zero-volatility funds (money-market and constant-NAV classes) in the shared suspicion predicate.

A Sharpe / Sortino / return-to-volatility figure whose own volatility denominator is below DATA_SUSPECT_LOW_VOL_FLOOR is arithmetic on a near-zero denominator, not evidence of a defect: it does not count. The rule reads the DENOMINATOR the fund has, never its
nature or name (P#5), unknown is not degenerate, and the other metrics of the same fund still count. R-7: no DB; synthetic panels.
"""
import importlib.util
import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import pandas as pd
import pytest

from shared import config
from shared.data_suspect import (KIND_DEVIATION, KIND_INTEGRITY, degenerate_hits, find_suspects, pit_panel, pit_suspect_reasons, volatility_quantity_of)

N = 60


@pytest.mark.parametrize("quantity, denominator", [
    ("sharpe|since_inception", "vol_ann|since_inception"),
    ("sortino|crisis_2022", "vol_ann|crisis_2022"),
    ("ret_vol_simple|crisis_2008", "vol_ann|crisis_2008"),
    ("sharpe_contraccion|since_inception", "vol_ann_contraccion|since_inception"),
    ("sortino_crisis_financiera|since_inception", "vol_ann_crisis_financiera|since_inception"),
    ("sharpe_shock_energetico|since_inception", "vol_ann_shock_energetico|since_inception"),
    ("sharpe|pit", "vol_ann|pit"),
])
def test_a_ratio_quantity_names_its_volatility_denominator(quantity, denominator):
    assert volatility_quantity_of(quantity) == denominator


@pytest.mark.parametrize("quantity", ["max_dd|since_inception", "return_ann|since_inception", "vol_ann|since_inception", "sharpe_slope|rolling_3y", "sharpe_pctile_cat|rolling_1y",
                                      "sharpe_zscore_cat|rolling_2y", "sharpe", "metric3"])
def test_only_direct_ratios_have_a_denominator(quantity):
    assert volatility_quantity_of(quantity) is None


def _panel(sharpe_extreme_vol=None, drop_vol=False, extra_extreme_dd=False, real_vol=None):
    """60 healthy funds with `sharpe|h` (nominal), `vol_ann|h` (nominal and real) and `max_dd|h`; X is 80 modified-z away on its Sharpe (and optionally on max_dd), with the volatility given."""
    rng = np.random.default_rng(8)
    rows = []

    def col(key, extreme=None):
        v = rng.normal(0.05, 0.01, N) if key.startswith("vol_ann") else rng.normal(0, 1, N)
        med, mad = float(np.median(v)), float(np.median(np.abs(v - np.median(v))))
        rows.extend((f"H{i:02d}", "N1", key, float(x), 100) for i, x in enumerate(v))
        value = extreme if extreme is not None else (med + 80.0 / 0.6745 * mad if key in ("sharpe|h|0|v1", "max_dd|h|0|v1") and (key.startswith("sharpe") or extra_extreme_dd) else float(rng.normal(0, 1)))
        rows.append(("X", "N1", key, value, 100))
    col("sharpe|h|0|v1")
    col("max_dd|h|0|v1")
    if not drop_vol:
        col("vol_ann|h|0|v1", extreme=sharpe_extreme_vol)
        col("vol_ann|h|1|v1", extreme=real_vol if real_vol is not None else sharpe_extreme_vol)
    return pd.DataFrame(rows, columns=["isin", "nature", "metric", "value", "source_rows"])


def test_an_extreme_ratio_on_a_near_zero_volatility_is_not_a_suspect():
    assert find_suspects(_panel(sharpe_extreme_vol=0.003), min_metrics=2) == []


def test_the_same_extreme_ratio_on_an_ordinary_volatility_is_a_suspect():
    s = find_suspects(_panel(sharpe_extreme_vol=0.05), min_metrics=2)
    assert [x.representative for x in s] == ["X"] and s[0].kinds == (KIND_DEVIATION,)


def test_the_floor_is_the_switch_zero_turns_the_rule_off():
    panel = _panel(sharpe_extreme_vol=0.003)
    assert len(find_suspects(panel, min_metrics=2, low_vol_floor=0)) == 1
    assert find_suspects(panel, min_metrics=2, low_vol_floor=0.005) == []
    assert len(find_suspects(panel, min_metrics=2, low_vol_floor=0.001)) == 1          # a lower floor does not cover a volatility of 0.003


def test_a_ratio_whose_denominator_is_unknown_is_not_degenerate():
    assert len(find_suspects(_panel(drop_vol=True), min_metrics=2)) == 1


def test_the_smallest_volatility_across_nominal_and_real_variants_decides():
    assert find_suspects(_panel(sharpe_extreme_vol=0.05, real_vol=0.002), min_metrics=2) == []


def test_other_metrics_of_the_same_fund_still_count():
    s = find_suspects(_panel(sharpe_extreme_vol=0.003, extra_extreme_dd=True), min_metrics=2)
    assert [x.representative for x in s] == ["X"] and [m for m, _z, _n in s[0].detail["top_metrics"]] == ["max_dd|h|0|v1"]     # the Sharpe is withdrawn, the drawdown stands
    assert KIND_INTEGRITY not in s[0].kinds                                                                                      # one quantity left


def test_every_withdrawn_row_stays_visible():
    out = degenerate_hits(_panel(sharpe_extreme_vol=0.003))
    assert list(out["metric"]) == ["sharpe|h|0|v1"] and list(out["isin"]) == ["X"] and list(out["quantity"]) == ["sharpe|h"]
    assert degenerate_hits(_panel(sharpe_extreme_vol=0.05)).empty


def test_the_shipped_floor_is_the_first_srri_boundary():
    spec = importlib.util.spec_from_file_location("srri_for_floor", os.path.join(_ROOT, "proyecto2", "src", "calculations", "srri.py"))
    src = open(spec.origin, encoding="utf-8").read()
    assert "(0.0050, 2)" in src and config.DATA_SUSPECT_LOW_VOL_FLOOR == 0.005         # volatility < 0.5% is risk class 1: the same constant, so the two cannot drift apart unnoticed


# ---- point in time -------------------------------------------------------------------------------------------------------------------------------------------------------

def _pit(sharpe_vol):
    rng = np.random.default_rng(6)
    isins = [f"P{i:02d}" for i in range(40)] + ["X"]
    f = pd.DataFrame({"Fund_Nature": "Monetario"}, index=pd.Index(isins, name="isin"))
    for m in ("max_dd", "return_ann_real", "sharpe", "srri_nav", "alpha_persistence", "capture_ratio"):
        v = rng.normal(0, 1, len(isins))
        if m == "sharpe":
            med, mad = float(np.median(v[:-1])), float(np.median(np.abs(v[:-1] - np.median(v[:-1]))))
            v[-1] = med + 90 / 0.6745 * mad
        f[m] = v
    vol = pd.Series(rng.normal(0.05, 0.01, len(isins)), index=f.index)
    vol["X"] = sharpe_vol
    return f, pd.Series(120.0, index=f.index), vol


def test_the_pit_panel_carries_the_volatility_denominator_only_when_given():
    f, n, vol = _pit(0.003)
    assert "vol_ann|pit|0|v1" in set(pit_panel(f, n, vol=vol)["metric"]) and "vol_ann|pit|0|v1" not in set(pit_panel(f, n)["metric"])


def test_a_pit_sharpe_on_a_near_zero_volatility_is_not_a_suspect_and_on_an_ordinary_one_it_is():
    f, n, vol = _pit(0.003)
    assert "X" not in pit_suspect_reasons(f, n, vol=vol)
    f2, n2, vol2 = _pit(0.08)
    assert "X" in pit_suspect_reasons(f2, n2, vol=vol2)
    assert "X" in pit_suspect_reasons(f, n)                                              # without a denominator the rule cannot apply: unknown is not degenerate
