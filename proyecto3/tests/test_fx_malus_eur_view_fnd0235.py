# proyecto3/tests/test_fx_malus_eur_view_fnd0235.py
# -*- coding: utf-8 -*-
"""FND-0235: the scorer's FX malus on the EUR investor's signed pp contribution, behind FX_CONTRIBUTION_EUR_VIEW_ENABLED.

Off = the stored rule (|fx_contribution_pct| > 0.60). On = |fx_contribution_ann| > FX_CONTRIBUTION_PP_LIMIT, and the scorer asks the loader
for fx_contribution_ann. R-7: imports fund_scorer only, no DB.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import fund_scorer as fs
from shared import config


@pytest.fixture
def switch(monkeypatch):
    return lambda on: monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", on, raising=False)


def _mult(**row):
    mult, detail = fs.compute_regime_multiplier(pd.Series(row), "Expansion")
    return mult, "fx_malus" in detail


def test_off_the_ratio_rule_is_the_stored_one(switch):
    switch(False)
    assert _mult(fx_contribution_pct=0.9, fx_contribution_ann=0.0) == (fs.MULT_FX_MALUS, True)
    assert _mult(fx_contribution_pct=-0.9) == (fs.MULT_FX_MALUS, True)             # abs
    assert _mult(fx_contribution_pct=0.5, fx_contribution_ann=0.09) == (1.0, False)


def test_on_the_pp_rule_decides_and_the_ratio_no_longer_matters(switch):
    switch(True)
    assert _mult(fx_contribution_pct=0.9, fx_contribution_ann=0.005) == (1.0, False)          # an unstable ratio on a tiny pp
    assert _mult(fx_contribution_pct=0.0, fx_contribution_ann=-0.03) == (fs.MULT_FX_MALUS, True)
    assert _mult(fx_contribution_ann=0.03) == (fs.MULT_FX_MALUS, True)
    assert _mult(fx_contribution_ann=0.02) == (1.0, False)                                    # strictly above the limit
    assert _mult(fx_contribution_ann=np.nan) == (1.0, False)
    assert _mult(fx_contribution_ann=None) == (1.0, False)                                    # an object column with None must not raise
    assert _mult(fx_contribution_ann=None, fx_contribution_pct=0.9) == (1.0, False)           # the ratio is not a fallback when on
    assert _mult(fx_contribution_pct=5.0) == (1.0, False)                                     # ratio only: no pp -> no malus


def test_off_a_missing_or_none_ratio_is_no_malus_either(switch):
    switch(False)
    assert _mult(fx_contribution_pct=None) == (1.0, False)
    assert _mult(fx_contribution_pct=np.nan) == (1.0, False)
    assert _mult() == (1.0, False)


def test_the_loader_requests_the_pp_metric_only_when_on(switch):
    switch(False)
    assert "fx_contribution_ann" not in [m for m, _, _ in fs.scoring_metric_requests()]
    switch(True)
    assert ("fx_contribution_ann", "since_inception", 0) in fs.scoring_metric_requests()
    assert ("fx_contribution_pct", "since_inception", 0) in fs.scoring_metric_requests()
