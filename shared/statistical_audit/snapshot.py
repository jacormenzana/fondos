"""Functions #1-#2 (AUDITORIA_ESTADISTICA.md §4): build_population, build_snapshot."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Sequence

import pandas as pd


class UniverseFilterMissingError(ValueError):
    """Raised when a population query omits the In_Current_Universe filter."""


def build_population(
    conn: sqlite3.Connection,
    query: str,
    params: Sequence = (),
    require_universe_filter: bool = True,
) -> pd.DataFrame:
    if require_universe_filter and "in_current_universe" not in query.lower():
        raise UniverseFilterMissingError(
            "Query does not reference In_Current_Universe; pass "
            "require_universe_filter=False only for tables with no universe "
            "concept (reference/lookup tables)."
        )
    return pd.read_sql_query(query, conn, params=params)


@dataclass
class SnapshotResult:
    eligible: pd.DataFrame
    held_out: pd.DataFrame
    slice_max_date: pd.DataFrame
    date_spread_days: float


def build_snapshot(
    df: pd.DataFrame,
    entity_key: str,
    slice_keys: Sequence[str],
    date_column: str,
    tolerance_days: int = 5,
) -> SnapshotResult:
    """Per-entity latest row within each slice, held out beyond tolerance_days
    of that slice's own max date. Mirrors the per-fund-latest contract of
    proyecto2/src/calculations/rolling_stats.py:compute_category_snapshot
    (sort by date, groupby(...).last()) rather than a global MAX(date), which
    that module's own comment records as a prior bug (BUG-ROLL-LATEST-B):
    a global peak date matches only a handful of funds and starves min_peers.
    """
    slice_keys = list(slice_keys)
    empty_cols = list(df.columns)

    if df.empty:
        empty = pd.DataFrame(columns=empty_cols)
        empty_max = pd.DataFrame(columns=slice_keys + ["slice_max_date"])
        return SnapshotResult(empty, empty, empty_max, 0.0)

    working = df.copy()
    working[date_column] = pd.to_datetime(working[date_column])

    group_keys = slice_keys + [entity_key]
    latest = (
        working.sort_values(date_column)
        .groupby(group_keys, as_index=False, dropna=False)
        .last()
    )

    if slice_keys:
        slice_max = (
            latest.groupby(slice_keys, dropna=False)[date_column]
            .max()
            .rename("slice_max_date")
            .reset_index()
        )
        latest = latest.merge(slice_max, on=slice_keys, how="left")
    else:
        overall_max = latest[date_column].max()
        latest = latest.assign(slice_max_date=overall_max)
        slice_max = pd.DataFrame({"slice_max_date": [overall_max]})

    lag_days = (latest["slice_max_date"] - latest[date_column]).dt.days
    eligible_mask = lag_days <= tolerance_days

    eligible = latest.loc[eligible_mask].drop(columns=["slice_max_date"]).reset_index(drop=True)
    held_out = latest.loc[~eligible_mask].drop(columns=["slice_max_date"]).reset_index(drop=True)

    date_spread_days = float((latest[date_column].max() - latest[date_column].min()).days)

    return SnapshotResult(eligible, held_out, slice_max, date_spread_days)
