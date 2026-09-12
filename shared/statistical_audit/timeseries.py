"""Function #11 (AUDITORIA_ESTADISTICA.md §4): check_timeseries_integrity.

Gap detection is deliberately opt-in via an externally supplied
expected_dates index/mapping rather than "next month = this month + 1" —
that would fabricate false gaps from the source calendar's own irregularities
(AUDITORIA_ESTADISTICA.md §3, reinforcing the reviewed spec's own point on
expected_date_index). A monotonicity check on data already sorted for
grouping would be tautological, so it is not implemented here — duplicates
and gaps are the only two integrity signals this generic function can assert
without seeing the true source calendar.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd


@dataclass
class TimeseriesIntegrityResult:
    n_series: int
    duplicates: pd.DataFrame
    gaps: pd.DataFrame


def check_timeseries_integrity(
    df: pd.DataFrame,
    entity_keys: Sequence[str],
    date_column: str,
    expected_dates: "pd.DatetimeIndex | Mapping[tuple, pd.DatetimeIndex] | None" = None,
) -> TimeseriesIntegrityResult:
    entity_keys = list(entity_keys)

    if df.empty:
        return TimeseriesIntegrityResult(0, df.copy(), pd.DataFrame(columns=entity_keys + [date_column]))

    n_series = df.drop_duplicates(subset=entity_keys).shape[0]

    dup_mask = df.duplicated(subset=entity_keys + [date_column], keep=False)
    duplicates = df.loc[dup_mask]

    gap_rows = []
    if expected_dates is not None:
        for key, group in df.groupby(entity_keys, dropna=False):
            key_tuple = key if isinstance(key, tuple) else (key,)
            actual = pd.DatetimeIndex(pd.to_datetime(group[date_column]))
            exp = expected_dates.get(key_tuple) if isinstance(expected_dates, Mapping) else expected_dates
            if exp is None:
                continue
            missing = pd.DatetimeIndex(exp).difference(actual)
            if len(missing):
                gap_df = pd.DataFrame({date_column: missing})
                for k, v in zip(entity_keys, key_tuple):
                    gap_df[k] = v
                gap_rows.append(gap_df)

    gaps = (
        pd.concat(gap_rows, ignore_index=True)
        if gap_rows
        else pd.DataFrame(columns=entity_keys + [date_column])
    )

    return TimeseriesIntegrityResult(n_series, duplicates, gaps)
