"""The one definition of "which calendar month does an observation belong to" (R-9, `doc/reglas/RESTRICCIONES_ARQUITECTURA.md`).

A monthly series in this repository carries one of three stamps for the same observation: the CALENDAR month-end
(`fund_nav_monthly`, 71.3% of rows), the last BUSINESS day (28.7% of rows; 39.6% of the 2026 rows -- 2026-02-27 is February),
or the month START (`series_inflation`, 100%; the whole macro OLS factor set in `series_macro`). `series_macro` is mixed: the OLS
factors are month-start, while `m2_global_yoy`, `m2_yoy` CN/JP/US and `fx_eom_eur` are month-end and `fx_d_eur` is daily. A stamp is
therefore NOT an identity: two series that describe the same month disagree on the label of that month, and every place where two
series meet has, until now, re-decided the convention by hand.

What that cost, measured (2026-10-18 reliability study, `out/audit/p2_reliability_20261018/REPORT.md`):
  * `compute_macro_sensitivity` inner-joined RAW NAV dates to the month-end factor index and lost ~30% of each fund's months:
    96% of the betas of last-business-day funds moved materially once the NAV was snapped, and 23% changed validity;
  * the rolling horizon cutoff (`DateOffset(months=N)` on raw dates) gives a window 12 or 13 monthly returns depending on the
    fund's stamp: 9-13% of the stored rolling time-series rows move, and 36% of the derived percentiles and slopes;
  * a NAV panel built from many funds puts the same month in its index twice (121 months in the live database), which is how the
    PIT risk-free / IPC lookup came to raise "cannot reindex on an axis with duplicate labels" (b586ba2, FND-0298).

FND-0241 (deflation) and FND-0253 (annualisation) each fixed one instance of this and neither prevented the macro regression,
because the convention lived in people's heads. R-9 puts it in one place: **the canonical period key is the calendar month-end**,
every stamp is MAPPED to it at the boundary where a series enters a calculation, and series are joined, aligned, windowed and aged
only on that key.

The key is DERIVED, never destructive: `snap_frame` adds the key beside the raw stamp and never overwrites it, so a trailing
partial month keeps the date it was actually observed on (R-9 rule 7) while still joining on the month it belongs to.

This module has NO kill-switch, deliberately. `deflation_alignment.cpi_lookup_dates` and the by-dates annualisation each keep
theirs, because flipping those changes STORED values and needs a recompute; converting a date to the month it falls in changes
nothing by itself. The callers decide when to use the key; the mapping is always the same.

Scope: monthly and coarser series. A DAILY series keeps its daily dates -- R-9 applies where a daily series is AGGREGATED to, or
joined with, a monthly one (`short_horizon` must never snap: every day of a month would collapse to that month's key).
"""
from __future__ import annotations

import logging

import pandas as pd

from shared.annualization import month_ordinals

logger = logging.getLogger(__name__)

# R-9 rule 7: a trailing month whose newest stamp is more than this many days before its own month-end is still OPEN -- the
# series has not finished reporting it. Five days covers a long weekend plus a holiday at a month boundary (the widest gap
# between the last business day and the calendar month-end is 3 days; 5 leaves room for a late publication) without reaching
# back far enough to call a genuinely complete month partial.
PARTIAL_TAIL_DAYS = 5

#: What `snap_frame` does with an incomplete trailing month.
PARTIAL_TAIL_MODES = ("keep", "drop", "snap")


def to_period_key(dates) -> pd.DatetimeIndex:
    """The canonical period key of each date: a normalised ``Timestamp`` at the LAST CALENDAR DAY of that date's own month.

    Accepts anything pandas can read as dates -- a Series, Index, numpy array, list or scalar of ``date`` / ``datetime`` /
    ``Timestamp`` / ISO string. Always returns a ``DatetimeIndex`` positionally aligned to the input (the caller's own index is
    NOT carried over: a key is a value, not a label, and silently re-aligning on the caller's index is exactly the class of bug
    R-9 exists to prevent).

    Month-start, last-business-day and mid-month stamps all map to the same key, which is the point:

        >>> to_period_key(["2026-02-01", "2026-02-27", "2026-02-28"]).tolist() == [pd.Timestamp("2026-02-28")] * 3
        True
    """
    idx = pd.DatetimeIndex(pd.to_datetime(pd.Series(dates).reset_index(drop=True), errors="coerce"))
    return pd.DatetimeIndex(idx.normalize() + pd.offsets.MonthEnd(0))


def period_ordinals(dates):
    """Integer month ordinal (``year * 12 + month - 1``) of each date -- the key as an integer, for windowing and differences.

    Delegates to ``shared.annualization.month_ordinals`` (P#11: one implementation, and it is the fast numpy one persistence's
    peer loop depends on). Use this, never a second ordinal formula.
    """
    return month_ordinals(dates)


def months_between(start, end):
    """Whole calendar months from `start` to `end`, counted on the period key, elementwise if either side is a sequence.

    ``months_between("2026-01-31", "2026-02-01") == 1``: one calendar month apart, although the stamps are one day apart. That
    is the whole reason staleness and horizons are measured here and not with ``(end - start).days``.
    """
    a, b = period_ordinals(start), period_ordinals(end)
    diff = b - a
    return int(diff[0]) if getattr(diff, "shape", ()) == (1,) and _is_scalar(start) and _is_scalar(end) else diff


def _is_scalar(value) -> bool:
    return not hasattr(value, "__len__") or isinstance(value, str)


def period_index(n_months: int, end=None) -> pd.DatetimeIndex:
    """A dense ``DatetimeIndex`` of `n_months` consecutive period keys ending at `end`'s key (default: today's month).

    The reindex / alignment target R-9 rule 3 asks for: a calendar index with no gaps, so a series reindexed onto it shows its
    missing months as NaN instead of silently shortening the window.
    """
    if n_months <= 0:
        return pd.DatetimeIndex([], dtype="datetime64[ns]")
    last = to_period_key([pd.Timestamp.today() if end is None else end])[0]
    return pd.date_range(end=last, periods=int(n_months), freq="ME")


def is_period_complete(stamp, key=None) -> bool:
    """Has the month that `stamp` belongs to finished reporting? (R-9 rule 7.)

    True when the stamp is within ``PARTIAL_TAIL_DAYS`` of its own month-end. `key` is accepted so a caller that already computed
    the key does not pay for it twice.
    """
    ts = pd.Timestamp(stamp).normalize()
    month_end = (to_period_key([ts])[0] if key is None else pd.Timestamp(key).normalize())
    return (month_end - ts).days <= PARTIAL_TAIL_DAYS


def snap_frame(df: pd.DataFrame, date_col: str = "date", key_col: str = "period_key",
               partial_tail: str = "keep", group_cols=None, label: str | None = None) -> pd.DataFrame:
    """Return a copy of `df` carrying its canonical period key, with at most one row per key (R-9 rules 1, 5 and 7).

    The raw stamp in `date_col` is PRESERVED -- the key is added in `key_col`. Nothing is reordered beyond the sort on
    (group, key) that makes "keep last" deterministic.

    partial_tail:
      ``"keep"``  (default) an incomplete trailing month keeps its row and its raw stamp, and still carries its key, so it joins
                  on the month it belongs to without pretending to be a finished observation.
      ``"drop"``  the incomplete trailing month is removed -- for a calculation that must not mix a partial month with whole ones.
      ``"snap"``  explicitly accept the partial month as if complete. Only for a caller that has a reason; it is never the default.

    group_cols: the columns that make a series (e.g. ``["isin"]``, or ``["indicator", "geography"]``). Duplicates are collapsed
    WITHIN one series only: two funds reporting the same month are two observations, not a duplicate, which is why the live
    database has 121 duplicate months but zero duplicate ``(isin, month)`` pairs.

    A collapse is logged ONCE per call at INFO with the count, never per row (R-9 rule 5): a silent collapse is how a revised
    NAV quietly replaced an estimated one with no trace.
    """
    if partial_tail not in PARTIAL_TAIL_MODES:
        raise ValueError(f"partial_tail must be one of {PARTIAL_TAIL_MODES}, got {partial_tail!r}")
    if date_col not in df.columns:
        raise KeyError(f"{date_col!r} is not a column of the frame (columns: {list(df.columns)})")

    out = df.copy()
    if out.empty:
        out[key_col] = pd.Series(dtype="datetime64[ns]")
        return out

    out[key_col] = to_period_key(out[date_col]).to_numpy()
    keys = list(group_cols or []) + [key_col]

    before = len(out)
    out = out.sort_values(keys + [date_col], kind="mergesort").drop_duplicates(subset=keys, keep="last")
    collapsed = before - len(out)
    if collapsed:
        logger.info("[R-9] %s: %d row(s) collapsed to one per period key (keep last) over %s",
                    label or "snap_frame", collapsed, keys)

    if partial_tail != "snap":
        newest = out[date_col].max()
        if not is_period_complete(newest):
            if partial_tail == "drop":
                out = out[to_period_key(out[date_col]).to_numpy() != to_period_key([newest])[0]]
            else:
                logger.debug("[R-9] %s: trailing month of %s is still open (newest stamp %s); raw stamp kept",
                             label or "snap_frame", to_period_key([newest])[0].date(), pd.Timestamp(newest).date())

    return out.reset_index(drop=True)
