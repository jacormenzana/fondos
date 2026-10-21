"""The one definition of the portfolio objective (FND-0303, owner decision 2026-10-21).

Two numbers, both NOMINAL and annual, both at PORTFOLIO level:

  * the BASE objective   IPC + M3          the capital-preservation reference every fund and every report has always used;
  * the ASPIRATION       IPC + M3 + 2 pp   (`shared.config.PORTFOLIO_TARGET_SPREAD`): reported against, never a selection constraint.
                                           Measured on the PIT backtest (2005-2026) the model earned 3.44% against 6.94% for the base
                                           objective, so no filter could turn the aspiration into a constraint without emptying the
                                           portfolio.

IPC is the mean of the ES and EU year-on-year inflation, M3 is the euro-area M3 year-on-year in percent (`series_macro`). Without M3
the objective is the IPC alone. The year-on-year base is the same calendar month a year earlier (R-9: a date, never a row offset).
Every consumer (monthly report, PIT backtest through `RegimeClassifier.absolute_target_annual`) calls this module (P#11).
"""
from __future__ import annotations

import pandas as pd

from shared import config


def objective_annual(ipc_yoy, m3_yoy_pct=None):
    """IPC + M3 as an annual fraction. `ipc_yoy` is a fraction (0.04 = 4%), `m3_yoy_pct` is in percent (3.5 = 3.5%).

    Scalars in, scalar (or None) out; Series in, Series out (NaN where IPC is NaN; a missing M3 counts as 0, as the PIT always did)."""
    if isinstance(ipc_yoy, pd.Series):
        m3 = 0.0 if m3_yoy_pct is None else (m3_yoy_pct / 100.0).fillna(0.0)
        return (ipc_yoy + m3).where(ipc_yoy.notna())
    if ipc_yoy is None or pd.isna(ipc_yoy):
        return None
    return float(ipc_yoy) + (0.0 if m3_yoy_pct is None or pd.isna(m3_yoy_pct) else float(m3_yoy_pct) / 100.0)


def aspiration_annual(base_annual, spread: "float | None" = None):
    """IPC + M3 + spread (default `config.PORTFOLIO_TARGET_SPREAD`); None / NaN stay None / NaN."""
    add = config.PORTFOLIO_TARGET_SPREAD if spread is None else spread
    if base_annual is None:
        return None
    return base_annual + add


def monthly_equivalent(annual):
    """Compound monthly rate equivalent to an annual fraction; None stays None."""
    if annual is None:
        return None
    return (1.0 + annual) ** (1.0 / 12.0) - 1.0


def yoy_from_levels(level_now, level_year_ago):
    """Year-on-year fraction from two index levels; None when either is missing or the base is not positive."""
    if level_now is None or level_year_ago is None or level_year_ago <= 0:
        return None
    return (level_now - level_year_ago) / level_year_ago
