"""When is a Sortino denominator estimated from enough downside observations? (FND-0242)

The downside deviation is the root of the MEAN of k squared shortfalls below the MAR (the other months contribute 0), so its relative
standard error is about 0.7 / sqrt(k): 35% at k = 4, 25% at k = 8, 14% at k = 24. A ratio built on 2-4 shortfalls is noise, and it
explodes when the few shortfalls are small (one USD money fund with 4 downside months out of 51 had Sortino 12.95 and moved the
Monetario peer kurtosis from 0.8 to 36.8). returns._MIN_DOWNSIDE_DEV_ANN (FND-0075) guards only the MAGNITUDE of the denominator, a cliff:
0.00101 keeps the ratio, 0.0009 makes it NaN.

Rule (window-scaled, so a 12-month window is not wiped out the way a fixed k >= 5 would, which removes 34% of rolling_1y):
    k >= max(SORTINO_MIN_DOWNSIDE_OBS, ceil(SORTINO_MIN_DOWNSIDE_SHARE * n))      k = #{r < MAR}, n = #returns
Measured 2026-10-07 on every stored window (22,311 valid Sortino): removes 0.7% of rows (2.2% of rolling_1y, 11% of Monetario),
the removed rows have median |Sortino| 4.3 vs 0.8 for the kept ones.

Applies to the scalar path (returns.sortino_ratio) and the rolling path (rolling_stats._roll_sortino) only. NOT to
sortino_ratio_from_returns: the regime path works on the few months of one macro regime by design and P3 reads its percentiles.
Behind shared.config.SORTINO_MIN_DOWNSIDE_COUNT_ENABLED (default False: the stored behaviour, bit-for-bit).
"""
from __future__ import annotations

import math

import numpy as np


def count_rule_enabled() -> bool:
    """Read at call time (not import time), like the other kill-switches, so tests and the switch can flip it."""
    from shared import config
    return bool(getattr(config, "SORTINO_MIN_DOWNSIDE_COUNT_ENABLED", False))


def min_downside_obs(n_returns: int) -> int:
    """Minimum number of below-MAR periods for a window of `n_returns` periods."""
    from shared import config
    floor = int(getattr(config, "SORTINO_MIN_DOWNSIDE_OBS", 3))
    share = float(getattr(config, "SORTINO_MIN_DOWNSIDE_SHARE", 0.15))
    return max(floor, math.ceil(share * n_returns))


def downside_count_reliable(rets, mar_per_period: float, enabled: "bool | None" = None) -> bool:
    """True when the downside deviation of `rets` about `mar_per_period` rests on enough shortfalls (always True when the rule is off)."""
    if enabled is None:
        enabled = count_rule_enabled()
    if not enabled:
        return True
    arr = np.asarray(rets, dtype=float)
    k = int((arr < mar_per_period).sum())
    return k >= min_downside_obs(len(arr))
