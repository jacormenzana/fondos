# proyecto3/tests/_scoring_fixture.py
# -*- coding: utf-8 -*-
"""Shared synthetic scoring frame (the shape load_fund_metrics_for_scoring returns) for the scorer tests.
Not a test module (leading underscore): imported by test_fund_scorer_from_df.py."""

import numpy as np
import pandas as pd

NATURES = ["Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible", "Mixtos", "Renta Variable", "Alternativo"]
SUFFIXES = ["shock_energetico", "crisis_financiera", "expansion", "estanflacion", "contraccion"]


def make_scoring_frame(n: int = 96, seed: int = 21) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.Index([f"XS{i:08d}" for i in range(n)], name="isin")
    nat = [NATURES[i % len(NATURES)] for i in range(n)]
    df = pd.DataFrame(index=idx)
    df["Fund_Name"] = [f"Fund {i}" for i in range(n)]
    df["Fund_Nature"] = nat
    df["srri_kiid"] = rng.integers(1, 8, n).astype(float)
    df["Investment_Focus"] = "Global"
    df["Credit_Quality"] = np.where(rng.random(n) < 0.15, "High Yield", "Investment Grade")
    df["Ongoing_Charge"] = rng.uniform(0.002, 0.025, n)
    df["SRRI_Quality_Flag"] = "OK"
    fam = np.array([f"FAM{i // 2}" if i % 7 else None for i in range(n)], dtype=object)   # pairs share a family
    df["fund_family_id"] = fam

    df["return_ann_real"] = rng.normal(0.02, 0.05, n)
    df["sharpe"] = rng.normal(0.4, 0.5, n)
    df["max_dd"] = -np.abs(rng.normal(0.12, 0.10, n)).clip(0, 0.7)
    df["alpha_persistence"] = rng.uniform(0, 1, n)
    df["capture_ratio"] = rng.normal(1.0, 0.6, n)
    df["momentum_rank"] = rng.uniform(0, 1, n)
    df["srri_nav"] = rng.integers(1, 8, n).astype(float)
    df["beta_oil"] = rng.normal(0.0, 0.05, n)
    df["beta_rate_eu"] = rng.normal(0.0, 0.2, n)
    df["beta_spread_hy"] = rng.normal(0.0, 0.03, n)
    df["beta_vix"] = rng.normal(0.0, 0.03, n)
    df["fx_contribution_pct"] = rng.uniform(0, 1.0, n)
    df["macro_r2"] = rng.uniform(0, 0.8, n)
    df["crisis_stress_score_mdd"] = -np.abs(rng.normal(0.2, 0.12, n))
    df["crisis_stress_score_ttr"] = rng.integers(2, 40, n).astype(float)
    df["regime_coverage_ratio"] = rng.uniform(0.1, 1.0, n)
    for s in SUFFIXES:
        df[f"return_ann_{s}"] = rng.normal(0.05, 0.15, n)
        df[f"sharpe_{s}"] = rng.normal(0.3, 0.7, n)
        df[f"sortino_{s}"] = rng.normal(0.4, 0.9, n)
        df[f"max_dd_{s}"] = -np.abs(rng.normal(0.15, 0.1, n))
        df[f"n_obs_{s}"] = rng.integers(0, 60, n).astype(float)
    df["short_max_drawdown__rolling_6m"] = -np.abs(rng.normal(0.06, 0.07, n))
    df["short_vol_adj__rolling_3m"] = np.abs(rng.normal(0.10, 0.08, n))
    df["short_liquidity_flag__rolling_6m"] = rng.choice([0.0, 0.01, 0.05, 0.3, 0.6], n)

    # holes: every column loses some values (the loader pivots sparse rows)
    for col in df.columns:
        if col in ("Fund_Name", "Fund_Nature", "fund_family_id", "Investment_Focus", "SRRI_Quality_Flag"):
            continue
        df.loc[rng.random(n) < 0.12, col] = np.nan
    return df
