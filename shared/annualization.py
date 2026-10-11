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


# ------------------------------------------------------------------------------------------------------------------------------
# FND-0253 -- annualise by DATES, not by rows (behind shared.config.ANNUALIZATION_BY_DATES_ENABLED, default False: the stored
# convention, bit-for-bit).
#
# A monthly NAV series with missing months used to be treated as if its rows were consecutive months: the horizon was understated and
# return_ann inflated (LU1663960000: 35 rows over 104 months, stored 20.9% vs 6.5% by dates), vol mixed multi-month returns as monthly
# ones. With the switch ON:
#   * the horizon is the DENSE-EQUIVALENT number of points of the first..last calendar month (`dense_equivalent_points`) and goes through
#     the SAME years_spanned() as before, so every existing convention (FND-0240 interval switch, the N/12 sites) is preserved and a
#     dense series (rows == months) is unchanged to the last bit;
#   * a return that spans k missing months enters the volatility as a k-month return (variance ~ k sigma^2): `kweighted_std`;
#   * genuine one-month statistics (consistency, regime months) use the one-month returns only.
# Everything that annualises a NAV window -- returns.*, rolling_stats, regime_returns, persistence, pit_metrics, pit_peer_metrics -- and
# everything that audits those stored values (statistical_audit.timeseries) calls these helpers, so producer and audit cannot disagree.
# ------------------------------------------------------------------------------------------------------------------------------

def by_dates_enabled() -> bool:
    """Read at call time (not import time), like the other kill-switches, so tests and the switch can flip it."""
    from shared import config
    return bool(getattr(config, "ANNUALIZATION_BY_DATES_ENABLED", False))


def month_ordinals(dates):
    """int64 array: calendar month ordinal (year * 12 + month - 1) of each date. Month-end alignment is implicit: only the calendar month
    of a date counts, so a NAV dated on the last BUSINESS day and one dated on the calendar month-end are the same month."""
    import numpy as np
    import pandas as pd
    idx = pd.DatetimeIndex(pd.to_datetime(pd.Series(dates).reset_index(drop=True)))
    return (idx.year.to_numpy(dtype="int64") * 12 + idx.month.to_numpy(dtype="int64") - 1)


def dense_equivalent_points(months) -> int:
    """Number of rows a dense monthly series over [first, last] month would have (months = month_ordinals): last - first + 1.
    For a dense series this IS len(series), which is what makes the switch a no-op for it."""
    return int(months[-1] - months[0]) + 1


def gap_months(months):
    """Months spanned by each consecutive return (len(months) - 1 values, each >= 1; two rows in one calendar month count as 1)."""
    import numpy as np
    return np.maximum(np.diff(months), 1)


def kweighted_std(r, k, periods_per_year: int = 12) -> float:
    """Annualised volatility of simple returns `r` that span `k` months each (arrays of equal length >= 2).

    sigma_month^2 = sum((r_i - k_i * mu)^2 / k_i) / (n - 1), mu = sum(r_i) / sum(k_i)   (a k-month return has variance ~ k sigma^2).
    When every k_i == 1 it returns EXACTLY std(r, ddof=1) * sqrt(periods_per_year), the legacy expression, so a dense series is
    bit-identical."""
    import numpy as np
    r = np.asarray(r, dtype=float)
    k = np.asarray(k, dtype=float)
    if len(r) < 2:
        return float("nan")
    if (k == 1.0).all():
        return float(np.std(r, ddof=1) * np.sqrt(periods_per_year))
    mu = r.sum() / k.sum()
    s2 = float(np.sum((r - k * mu) ** 2 / k) / (len(r) - 1))
    return float(np.sqrt(s2) * np.sqrt(periods_per_year))


def standardised_shortfalls(r, k, mar_per_period: float):
    """Returns standardised for the Sortino downside deviation: z_i = (r_i - k_i * MAR) / sqrt(k_i). For k_i == 1 this is r_i - MAR, the
    legacy quantity, so `downside_deviation_ann(z, 0.0)` equals `downside_deviation_ann(r, MAR)` on a dense series."""
    import numpy as np
    r = np.asarray(r, dtype=float)
    k = np.asarray(k, dtype=float)
    return (r - k * mar_per_period) / np.sqrt(k)
