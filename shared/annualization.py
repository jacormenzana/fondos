"""The one definition of "how many years does a series of N observations span" (FND-0240).

A NAV series of N points spans N - 1 periods (N points have N - 1 intervals between them). P2 historically divided by N, so a
12-point window (11 months of returns) was annualised as a full year: an exponent off by (N - 1) / N, about 8% relative on the
12-13 point windows (rolling_1y, the crisis windows) and 0.5% since inception. regime_returns already counts returns, so the two
paths disagreed on the same fund.

Behind shared.config.ANNUALIZATION_INTERVAL_ENABLED (default False: the stored convention, bit-for-bit). Everything that annualises a
NAV window -- returns.annualized_return, rolling_stats._roll_return_ann -- and everything that audits those stored values against the
data (timeseries.build_window_deflation_frame, scalar_window_cpi) calls this, so the producer and its audit can never disagree on
the convention. Do NOT flip the switch without the recompute of the families it touches (see utils.family_versions.FLAG_FAMILIES):
until then the stored values follow the old convention and the audit's Fisher/nominal identities would report the gap.
"""
from __future__ import annotations


def interval_convention_enabled() -> bool:
    """Read at call time (not import time), like the other kill-switches, so tests and the switch can flip it."""
    from shared import config
    return bool(getattr(config, "ANNUALIZATION_INTERVAL_ENABLED", False))


def years_spanned(n_points, periods_per_year: int = 12, interval_correct: "bool | None" = None):
    """Years spanned by a series of `n_points` observations (a number, or a pandas Series / numpy array of them).

    interval_correct=None reads the switch; True gives (N - 1) / periods_per_year, False the stored N / periods_per_year.
    """
    if interval_correct is None:
        interval_correct = interval_convention_enabled()
    return ((n_points - 1) if interval_correct else n_points) / periods_per_year
