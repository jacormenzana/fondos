"""Derived columns for the Sortino/Sharpe consistency checks (FND-0234, 2026-10-06).

Why this exists. The two ratios share their numerator (return_ann - rf used by P2), so the only difference is the
denominator: Sharpe divides by the sample standard deviation sigma, Sortino by the downside deviation dd, the root of the
mean squared shortfall below the MAR = rf/12 over ALL periods. The old invariants assumed dd <= sigma and so
"sortino >= sharpe" (positive numerator) / "sortino <= sharpe" (negative numerator). Two things were wrong:

1. The sign was taken from `return_ann - RISK_FREE_RATE_ANN` (config, 4.0%), but P2 divides by the date-aligned risk-free
   rate at the horizon's end (resolve_rf_rate: 2.5% for most rows, 0.75% in crisis_2022). For every row with a return
   between the two, the rule applied the wrong half: 4,332 of the 8,700 SORTINO_VS_SHARPE_DOWN "violations".
2. With a NEGATIVE numerator the premise is false: dd^2 <= sigma_pop^2 + (mean - MAR)^2, so a fund far below the MAR
   (a -40% crisis window, a money-market fund under a higher rate) legitimately has dd > sigma, hence sortino > sharpe
   (the remaining 4,368). With a POSITIVE numerator dd <= sigma does hold (0 violations in 23,000 rows).

So the sign now comes from the stored Sharpe itself (vol_ann > 0, hence sign(sharpe) = sign of the numerator), and the
negative side is checked with the bound that DOES hold:

    dd_ann^2 <= vol_ann^2 + 12 * shortfall^2,   shortfall = max(MAR - g, 0) + sigma_m^2 / 2

(g = monthly geometric mean, which cannot exceed the arithmetic mean; sigma_m^2/2 absorbs the arithmetic-geometric gap when
the mean is above the MAR). MAR is recovered exactly from the stored pair, rf = return_ann - sharpe * vol_ann, so no rf is
assumed. g is derived from return_ann with the exponent P2 uses, n_obs / 12 years over (n_obs - 1) returns
(returns.annualized_return counts NAV points, not intervals: FND-0240 tracks that); the more permissive of that and the
interval-correct exponent is used so the check stays valid if the convention is ever fixed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = 12


def add_downside_deviation_columns(df: pd.DataFrame, n_obs_column: str = "n_obs") -> pd.DataFrame:
    """Adds rf_implied, dd_ann_implied and dd_ann_max to a wide metrics frame (columns return_ann, vol_ann, sharpe, sortino).

    Rows where a quantity is undefined (sortino 0/NaN, 1 + return_ann <= 0, missing operand) get NaN, so a rule evaluated on
    them is not applicable ("undecidable", never a violation). `n_obs_column` may be absent or NaN: the exponent then falls
    back to 1/12 per month.
    """
    out = df.copy()
    needed = {"return_ann", "vol_ann", "sharpe", "sortino"}
    if not needed <= set(out.columns):
        for col in ("rf_implied", "dd_ann_implied", "dd_ann_max"):
            out[col] = np.nan
        return out

    vol = out["vol_ann"].astype(float)
    numerator = out["sharpe"].astype(float) * vol                       # = return_ann - rf used by P2
    out["rf_implied"] = out["return_ann"].astype(float) - numerator
    out["dd_ann_implied"] = numerator / out["sortino"].replace(0, np.nan).astype(float)

    n = out[n_obs_column].astype(float) if n_obs_column in out.columns else pd.Series(np.nan, index=out.index)
    per_month = 1.0 / PERIODS_PER_YEAR                                   # interval convention (FND-0240 on): total = (1+ret)^((n-1)/12)
    legacy = n / (PERIODS_PER_YEAR * (n - 1))                            # points convention (FND-0240 off): total = (1+ret)^(n/12)
    base = 1.0 + out["return_ann"].astype(float)
    pos = base.where(base > 0)
    # The more permissive of the two conventions is the one giving the SMALLER monthly geometric mean (a larger shortfall):
    # with base > 1 that is the smaller exponent, with base < 1 the larger one. Taking the max exponent everywhere (the
    # earlier code) under-allowed positive returns once FND-0240 made the interval convention the real one: 10-19 false
    # SORTINO_DOWNSIDE_BOUND violations per 1,500 correct series (2026-10-10). Valid under either convention, no config read.
    g_interval = np.power(pos, per_month) - 1.0
    g_legacy = np.power(pos, legacy.where(n > 1)) - 1.0
    g_m = np.fmin(g_interval, g_legacy).where(base > 0)                   # fmin ignores the NaN of an undefined legacy exponent

    mar = out["rf_implied"] / PERIODS_PER_YEAR
    sigma_m = vol / np.sqrt(PERIODS_PER_YEAR)
    shortfall = np.maximum(mar - g_m, 0.0) + sigma_m ** 2 / 2.0
    out["dd_ann_max"] = np.sqrt(vol ** 2 + PERIODS_PER_YEAR * shortfall ** 2)
    return out
