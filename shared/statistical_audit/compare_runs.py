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

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd



DRIFT_STATS: tuple[str, ...] = (
    "n_valid", "null_pct", "coverage_pct", "mean", "p50", "sd", "p95",
    "zero_pct", "skew", "kurtosis",
)

_KEY_COLUMNS = ["population", "group_key", "stat_name"]

# A difference below this is floating-point noise, not drift: the same statistic recomputed on the same
# data can differ at ~1e-16 (summation order), and reporting it as "moved" buries the real shifts (found
# 2026-09-26: 28 "non-zero deltas" that were all +0.0%). rtol 1e-9 = nine significant digits.
NOISE_RTOL: float = 1e-9
NOISE_ATOL: float = 1e-12


@dataclass
class RunComparisonResult:
    deltas: pd.DataFrame
    new_groups: list[str]
    dropped_groups: list[str]


def load_run_statistics(conn: "psycopg.Connection", run_id: str, domain: str) -> pd.DataFrame:
    ph = "%s"
    # fetchall()+DataFrame(columns=...), not pd.read_sql_query(query, conn, ...): on a plain
    # psycopg3 connection (not SQLAlchemy) it emits a UserWarning on every call — pure log noise
    # on a hermetic/live run (FND-0108); same pattern already used by export_tables.py/pipeline.py/
    # fund_scorer.py/db_readers.py for the identical reason.
    cur = conn.execute(
        "SELECT population, group_key, stat_name, stat_value FROM audit_statistic "
        f"WHERE run_id = {ph} AND domain = {ph}",
        (run_id, domain),
    )
    cols = [d[0] for d in cur.description]   # index, not .name: portable across psycopg3/sqlite3
    return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)


def drift_label(population: str, group_key: str) -> str:
    """How a statistic is named in the drift report. `group_key` alone is NOT unique: the same key (e.g.
    "sortino|since_inception|0|v1") exists once per population (GLOBAL and one PEER:<nature> each), and the report used to
    print them identically -- the same line twice with different numbers. GLOBAL keeps the bare key; any other population
    is appended. (Keys with four parts come from fund_metrics, three-part ones from the fund_metric_timeseries snapshot.)"""
    return group_key if population in (None, "", "GLOBAL") else f"{group_key} @ {population}"


def compare_violators(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """Violator drift between two runs, per (rule_id, group_key): how many ISINs are new, resolved, unchanged.

    previous / current: frames with rule_id, group_key, isin (persistence.load_finding_isins, or the in-memory findings'
    violating_isins). A rule that appears on only one side counts every ISIN as new (or resolved). Sorted so the rules that
    moved most come first."""
    columns = ["rule_id", "group_key", "new", "resolved", "unchanged"]
    keys = ["rule_id", "group_key"]
    if previous.empty and current.empty:
        return pd.DataFrame(columns=columns)

    def _sets(df):
        return {k: set(g["isin"]) for k, g in df.groupby(keys)} if not df.empty else {}

    prev_s, curr_s = _sets(previous), _sets(current)
    rows = []
    for key in sorted(set(prev_s) | set(curr_s)):
        p, c = prev_s.get(key, set()), curr_s.get(key, set())
        rows.append((key[0], key[1], len(c - p), len(p - c), len(p & c)))
    out = pd.DataFrame(rows, columns=columns)
    out["moved"] = out["new"] + out["resolved"]
    return out.sort_values(["moved", "rule_id"], ascending=[False, True]).drop(columns="moved").reset_index(drop=True)


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
    merged["is_noise"] = np.isclose(
        merged["stat_value_current"].astype(float), merged["stat_value_previous"].astype(float),
        rtol=NOISE_RTOL, atol=NOISE_ATOL, equal_nan=True,
    )
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
