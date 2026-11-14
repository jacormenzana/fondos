# proyecto2/tests/utils/test_nav_forensics_fnd0393.py
# -*- coding: utf-8 -*-
"""FND-0393 (WS1 of FND-0391): the pure parts of scripts/audit/nav/nav_forensics.py (robust z, flat run, step ratio, key parsing and the verdict order).

R-7: no DB. The script is loaded by path; its database parts are exercised read-only against the live store when the report is run. They sit in proyecto2/tests because the script imports proyecto2's `src`.
"""
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "proyecto2"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np
import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location("nav_forensics", _ROOT / "scripts" / "audit" / "nav" / "nav_forensics.py")
nf = importlib.util.module_from_spec(spec)
sys.modules["nav_forensics"] = nf
spec.loader.exec_module(nf)

OK = (True, None)
BAD = (False, "x")
CLEAN = {"validate_nav": OK, "validate_nav_stale_flat": OK, "validate_nav_unshared_jump": OK}


def _nav(values, start="2020-01-31"):
    return pd.DataFrame({"date": pd.date_range(start, periods=len(values), freq="ME"), "nav": [float(v) for v in values]})


def test_robust_z_is_the_modified_z_and_refuses_what_it_cannot_judge():
    vals = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert nf.robust_z(5, vals) == pytest.approx(0.0)
    assert nf.robust_z(9, vals) == pytest.approx(0.6745 * 4 / 2)           # median 5, MAD 2
    assert nf.robust_z(1, [1, 2, 3]) is None                               # fewer than 5 values
    assert nf.robust_z(1, [3, 3, 3, 3, 3, 3]) is None                      # MAD = 0
    assert nf.robust_z(5, [1, 2, None, float("nan"), 3, 4, 5, 6]) is not None   # missing values are ignored


def test_longest_flat_run_and_max_step_ratio():
    nav = _nav([10, 10, 10, 10, 12, 12, 13])
    r = nf.longest_flat_run(nav)
    assert (r["length"], r["value"], r["ends"]) == (4, 10.0, "2020-04")
    assert nf.longest_flat_run(_nav([1, 2, 3]))["length"] == 1
    assert nf.max_step_ratio(_nav([100, 101, 50])) == pytest.approx(2.0 * 101 / 100)     # the larger of up / down steps
    assert nf.max_step_ratio(_nav([5])) == 1.0


def test_group_keys_that_are_not_fund_metrics_are_refused():
    assert nf.parse_group_key("sharpe_slope|rolling_3y|0|v1") == ("sharpe_slope", "rolling_3y", 0, "v1")
    assert nf.parse_group_key("time_to_recovery|rolling_1y|1|v1") == ("time_to_recovery", "rolling_1y", 1, "v1")
    assert nf.parse_group_key("short_liquidity_flag|rolling_1m|d1") is None          # three-part snapshot key
    assert nf.parse_group_key("a|b|x|v1") is None


def test_verdict_order_nav_defect_first_then_jump_then_sample_then_ratio_then_baseline():
    assert nf.verdict({**CLEAN, "validate_nav": BAD}, [], 5, 400, source_rows=3, rows_p10=35, rows_p50=36) == "NAV_DEFECT_GUARDED"      # a validator beats everything
    assert nf.verdict(CLEAN, [{"ret": 0.9}], 5, 400, source_rows=3, rows_p10=35, rows_p50=36) == "LEVEL_SHIFT_UNSHARED"
    assert nf.verdict(CLEAN, [], 300, 300, source_rows=3, rows_p10=35, rows_p50=36) == "SMALL_SAMPLE_METRIC"
    assert nf.verdict(CLEAN, [], 300, 300, source_rows=100, rows_p10=35, rows_p50=36, ratio_metric=True, vol=0.001, vol_p10=0.01) == "LOW_VOLATILITY_RATIO"
    assert nf.verdict(CLEAN, [], 5, 400) == "BASELINE_ARTIFACT"                       # extreme in the universe, ordinary inside its nature
    assert nf.verdict(CLEAN, [], 300, 300) == "REVIEW"                                 # extreme in both: a human looks


def test_a_sample_is_small_only_when_it_is_below_the_p10_and_below_half_the_median():
    base = dict(validators=CLEAN, jumps=[], z_nature=300, z_universe=300)
    assert nf.verdict(**base, source_rows=220, rows_p10=237, rows_p50=324) != "SMALL_SAMPLE_METRIC"      # 7% under the p10 is not a small sample (a money-market crisis metric)
    assert nf.verdict(**base, source_rows=65, rows_p10=70, rows_p50=155) == "SMALL_SAMPLE_METRIC"
    assert nf.verdict(**base, source_rows=65, rows_p10=70) == "SMALL_SAMPLE_METRIC"                       # no median known: the p10 alone decides
    assert nf.verdict(**base, source_rows=None, rows_p10=70, rows_p50=155) == "REVIEW"


def test_the_ratio_verdict_needs_a_ratio_metric_and_a_known_volatility_floor():
    base = dict(validators=CLEAN, jumps=[], z_nature=300, z_universe=300, source_rows=100, rows_p10=35, rows_p50=36)
    assert nf.verdict(**base, ratio_metric=False, vol=0.001, vol_p10=0.01) == "REVIEW"
    assert nf.verdict(**base, ratio_metric=True, vol=None, vol_p10=0.01) == "REVIEW"
    assert nf.verdict(**base, ratio_metric=True, vol=0.02, vol_p10=0.01) == "REVIEW"


def test_top_moves_use_consecutive_months_and_report_how_many_peers_moved():
    from src.utils.peer_returns import build_peer_returns
    rng = np.random.default_rng(1)
    rows = []
    for i in range(12):
        v = [100.0]
        for r in rng.normal(0.003, 0.01, 11):
            v.append(v[-1] * (1 + r))
        rows.append(_nav(v).assign(isin=f"P{i:02d}", nature="N"))
    target = _nav([100, 101, 102, 150, 151, 152, 153, 154, 155, 156, 157, 158])          # +47% in month 4
    rows.append(target.assign(isin="T", nature="N"))
    peers = build_peer_returns(pd.concat(rows, ignore_index=True))
    mv = nf.top_moves(target, peers, "T", k=2)
    assert mv[0]["month"] == "2020-04" and abs(mv[0]["ret"] - (150 / 102 - 1)) < 1e-3
    assert mv[0]["n_peers"] == 12 and mv[0]["peer_share"] == 0.0 and abs(mv[0]["peer_median"]) < 0.05
