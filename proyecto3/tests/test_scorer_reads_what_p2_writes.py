# proyecto3/tests/test_scorer_reads_what_p2_writes.py
# -*- coding: utf-8 -*-
"""
Contract between what the scorer asks for (fund_scorer.scoring_metric_requests) and where P2 stores it -- FND-0199.

The category percentiles (vol_ann / max_dd / return_ann _pctile_cat) are written by P2 per rolling window, never at
since_inception; the scorer used to ask for since_inception and so would have read nothing the day ROLLING_PCTILE_P3_ENABLED
was turned on. R-7: pure, no DB.

    python -m pytest proyecto3/tests/test_scorer_reads_what_p2_writes.py -v
"""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import fund_scorer as fs
from shared import config

PCTILE = ("vol_ann_pctile_cat", "max_dd_pctile_cat", "return_ann_pctile_cat")


def test_the_percentile_horizon_is_a_rolling_window_p2_writes():
    assert config.ROLLING_PCTILE_HORIZON in set(config.ROLLING_WINDOWS)          # keys are the horizon labels (rolling_3y ...)
    assert config.ROLLING_PCTILE_HORIZON != "since_inception"


def test_requests_do_not_include_the_percentiles_while_the_flag_is_off(monkeypatch):
    monkeypatch.setattr(fs, "ROLLING_PCTILE_P3_ENABLED", False)
    asked = {m for m, _, _ in fs.scoring_metric_requests()}
    assert not asked & set(PCTILE)


def test_with_the_flag_on_the_percentiles_are_read_at_the_rolling_horizon(monkeypatch):
    monkeypatch.setattr(fs, "ROLLING_PCTILE_P3_ENABLED", True)
    req = {m: h for m, h, _ in fs.scoring_metric_requests()}
    for m in PCTILE:
        assert req[m] == config.ROLLING_PCTILE_HORIZON, m
    assert req["sharpe_slope"] == "rolling_3y"


def test_the_static_metrics_are_still_read_at_since_inception():
    req = {m: h for m, h, _ in fs.scoring_metric_requests()}
    for m in ("return_ann", "sharpe", "max_dd", "alpha_persistence", "capture_ratio", "momentum_rank", "srri_nav"):
        assert req[m] == "since_inception"


def test_regime_metrics_are_requested_only_when_a_regime_is_given():
    plain = {m for m, _, _ in fs.scoring_metric_requests()}
    with_regime = {m for m, _, _ in fs.scoring_metric_requests("Crisis_Financiera")}
    assert "regime_coverage_ratio" not in plain and "regime_coverage_ratio" in with_regime
