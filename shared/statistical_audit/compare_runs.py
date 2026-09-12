"""Function #13 (AUDITORIA_ESTADISTICA.md §4): compare_runs — the "8th
block" neither skill originally had: not "is this distribution rare?" but
"has it shifted since the last run?" A simultaneous population-wide drift
(e.g. a median dropping 50%) can hide inside individually-plausible values
and is invisible to every within-run check in this package.

Reads two persisted audit_statistic snapshots (or one persisted + one fresh
in-memory, via statistics_to_frame in persistence.py) and diffs them on the
handful of interpretable statistics the doc calls out — deliberately not KS
statistic / PSI / Wasserstein distance yet (AUDITORIA_ESTADISTICA.md §41
in the reviewed ChatGPT analysis this whole engine is built from: start
with interpretable statistic differences, add distributional-distance
measures only if that turns out to be insufficient).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Sequence

import pandas as pd

DRIFT_STATS: tuple[str, ...] = (
    "n_valid", "null_pct", "coverage_pct", "mean", "p50", "sd", "p95",
    "zero_pct", "skew", "kurtosis",
)

_KEY_COLUMNS = ["population", "group_key", "stat_name"]


@dataclass
class RunComparisonResult:
    deltas: pd.DataFrame
    new_groups: list[str]
    dropped_groups: list[str]


def load_run_statistics(conn: sqlite3.Connection, run_id: str, domain: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT population, group_key, stat_name, stat_value FROM audit_statistic "
        "WHERE run_id = ? AND domain = ?",
        conn, params=(run_id, domain),
    )


def compare_runs(
    previous: pd.DataFrame,
    current: pd.DataFrame,
    stats: Sequence[str] = DRIFT_STATS,
) -> RunComparisonResult:
    prev = previous[previous["stat_name"].isin(stats)]
    curr = current[current["stat_name"].isin(stats)]

    merged = prev.merge(
        curr, on=_KEY_COLUMNS, how="outer", suffixes=("_previous", "_current"),
    )
    merged["delta"] = merged["stat_value_current"] - merged["stat_value_previous"]
    denom = merged["stat_value_previous"].abs()
    merged["pct_change"] = (merged["delta"] / denom).where(denom > 0)
    merged = merged.rename(columns={
        "stat_value_previous": "previous_value", "stat_value_current": "current_value",
    })

    prev_groups = set(prev["group_key"])
    curr_groups = set(curr["group_key"])

    return RunComparisonResult(
        deltas=merged,
        new_groups=sorted(curr_groups - prev_groups),
        dropped_groups=sorted(prev_groups - curr_groups),
    )
