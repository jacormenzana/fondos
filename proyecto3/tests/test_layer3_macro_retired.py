# proyecto3/tests/test_layer3_macro_retired.py
# -*- coding: utf-8 -*-
"""
Layer 3 macro multipliers retired from the scorer (FND-0225, owner decision 2026-10-04 after the full-universe PIT
backtests): crisis VIX / HY-spread legs, oil bonus, rate malus and macro_r2 malus must not move a score any more, while
the alpha_persistence bonus and the FX malus still do. R-7: imports fund_scorer only, no DB.

    python -m pytest proyecto3/tests/test_layer3_macro_retired.py -v
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

REGIMES = ["Crisis_Financiera", "Shock_Energetico", "Estanflacion", "Recalentamiento", "Recalentamiento_Tardio",
           "Contraccion", "Expansion"]
EXTREME_MACRO = {"beta_oil": 0.9, "beta_rate_eu": -0.9, "beta_spread_hy": 0.9, "beta_vix": 0.9, "macro_r2": 0.99}
RETIRED_KEYS = {"crisis_spread_malus", "crisis_spread_bonus", "crisis_vix_malus", "beta_oil_bonus",
                "beta_rate_eu_malus", "macro_r2_malus"}
RETIRED_CONSTANTS = ("BETA_OIL_THRESHOLD", "BETA_RATE_EU_THRESHOLD", "MACRO_R2_LIMIT", "MULT_OIL_BONUS",
                     "MULT_RATE_EU_MALUS", "MULT_MACRO_MALUS", "SPREAD_HY_CRISIS_THRESHOLD", "SPREAD_HY_HEDGE_THRESHOLD",
                     "VIX_CRISIS_THRESHOLD", "MULT_CRISIS_SPREAD_MALUS", "MULT_CRISIS_SPREAD_BONUS")


@pytest.mark.parametrize("regime", REGIMES)
def test_extreme_macro_betas_do_not_move_the_multiplier_in_any_regime(regime):
    mult, detail = fs.compute_regime_multiplier(pd.Series(EXTREME_MACRO), regime)
    assert mult == 1.0 and not (set(detail) & RETIRED_KEYS)


def test_the_alpha_bonus_and_the_fx_malus_still_apply():
    both, d = fs.compute_regime_multiplier(pd.Series({"alpha_persistence": 0.9, "fx_contribution_pct": 0.9, **EXTREME_MACRO}),
                                           "Crisis_Financiera")
    assert both == pytest.approx(round(fs.MULT_ALPHA_BONUS * fs.MULT_FX_MALUS, 4))
    assert {"alpha_persistence_bonus", "fx_malus"} <= set(d) and not (set(d) & RETIRED_KEYS)


def test_the_retired_constants_are_gone():
    assert not [n for n in RETIRED_CONSTANTS if hasattr(fs, n)]


def test_scores_do_not_depend_on_the_macro_columns():
    rng = np.random.default_rng(1)
    n = 30
    df = pd.DataFrame({
        "Fund_Name": [f"F{i}" for i in range(n)], "Fund_Nature": rng.choice(["Renta Variable", "Mixtos", "Monetario"], n),
        "return_ann_real": rng.normal(0.03, 0.02, n), "sharpe": rng.normal(0.5, 0.3, n), "max_dd": -rng.uniform(0.02, 0.12, n),
        "srri_nav": rng.integers(1, 5, n).astype(float), "alpha_persistence": rng.uniform(0, 1, n),
        "capture_ratio": rng.uniform(0.5, 1.5, n), "momentum_rank": rng.uniform(0, 1, n),
        "fund_family_id": [None] * n,
    }, index=pd.Index([f"ISIN{i:03d}" for i in range(n)], name="isin"))
    base = fs.score_funds_from_df(df.copy(), "Crisis_Financiera", verbose=False)
    loaded = df.copy()
    for k, v in EXTREME_MACRO.items():
        loaded[k] = v
    with_macro = fs.score_funds_from_df(loaded, "Crisis_Financiera", verbose=False)
    cols = ["isin", "subportfolio", "score_base", "multiplier", "score_final", "eligible"]
    pd.testing.assert_frame_equal(base[cols].reset_index(drop=True), with_macro[cols].reset_index(drop=True))
