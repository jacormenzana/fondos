"""Volatility floor for the ratio metrics at the source (FND-0405, owner decision 2026-11-10; plan amendments A14/A15).

A Sharpe or a return-to-volatility figure divides by an annualised volatility; when that volatility is vanishingly small (a constant-NAV money-market class, a near-flat fund) the quotient is arithmetic on a
near-zero denominator and explodes to a meaningless magnitude (live: a Sharpe of -66 on a 0.37% volatility). P2 now does not publish such a ratio: it is NaN, which the writer stores as a NULL row (never a stale
value: the upsert is `value = excluded.value`), and "not discovered" is what P#10 says a missing value means. The Sortino already had the same guard on its DOWNSIDE deviation (FND-0075, `_MIN_DOWNSIDE_DEV_ANN` = 0.001).

The floor is `shared.config.RATIO_MIN_VOL_ANN` (0.001 = 0.1% a year, the same number as the Sortino's) behind the switch `RATIO_VOL_FLOOR_ENABLED`; both are read at CALL time, so P2, the rolling windows and the PIT backtest cannot
disagree (P#11) and a rollback is one switch, not a revert. It is NOT the same number as `DATA_SUSPECT_LOW_VOL_FLOOR` (0.005, the SRRI class-1 boundary): that one only stops the audit's suspicion predicate from reading a
ratio as a defect, it removes nothing from the store. Pure functions.
"""
from __future__ import annotations

import numpy as np

from shared import config as _cfg


def ratio_vol_floor() -> float:
    """The annualised-volatility floor in force: `RATIO_MIN_VOL_ANN`, or 0.0 (no floor) while `RATIO_VOL_FLOOR_ENABLED` is False."""
    return float(_cfg.RATIO_MIN_VOL_ANN) if _cfg.RATIO_VOL_FLOOR_ENABLED else 0.0


def vol_is_degenerate(vol) -> bool:
    """True when `vol` (a scalar annualised volatility) is below the floor in force. NaN is not degenerate (unknown is not degenerate: the callers already return NaN for it)."""
    floor = ratio_vol_floor()
    return bool(floor > 0.0 and vol == vol and vol < floor)


def degenerate_vol_mask(vol) -> np.ndarray:
    """Boolean mask of the entries of `vol` (array of annualised volatilities) below the floor in force; all False with no floor, NaN never True."""
    v = np.asarray(vol, dtype=float)
    floor = ratio_vol_floor()
    if floor <= 0.0:
        return np.zeros(v.shape, dtype=bool)
    with np.errstate(invalid="ignore"):
        return np.isfinite(v) & (v < floor)
