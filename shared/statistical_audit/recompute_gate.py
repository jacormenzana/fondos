"""Function #14 (AUDITORIA_ESTADISTICA.md §4 #14, catalogued but never built): assert_recompute_happened.

P2 Method Control #3 ("verify fund_metric_state.input_hash actually changed before trusting an
A/B recompute result -- an un-forced re-run is a no-op under the idempotency-hash gate, so an
unchanged hash means the 'after' numbers are stale") as machine-checked tooling instead of pure
manual discipline (B6, FND-0128, 2026-09-28). Read-only, P2-only -- the cost domain has no
input_hash/fund_metric_state equivalent (its idempotency story is COALESCE-based, not hash-based).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def capture_state(conn: "psycopg.Connection", isins: list[str]) -> pd.DataFrame:
    """Snapshot of control.fund_metric_state for the given ISINs -- one row per
    (isin, metric_version) with its input_hash and calculated_at -- to be captured before and
    after a fix so assert_recompute_happened() can verify the recompute actually ran.
    calculated_at is cast to str immediately: it is a DATE (not timestamp), and keeping the
    snapshot all-string makes JSON round-tripping (--state-snapshot/--verify-recompute) trivial
    and avoids any pandas date-epoch serialization ambiguity -- only equality is ever needed.
    """
    if not isins:
        return pd.DataFrame(columns=["isin", "metric_version", "input_hash", "calculated_at"])
    placeholders = ",".join("%s" for _ in isins)
    cur = conn.execute(
        f"""SELECT isin, metric_version, input_hash, calculated_at
            FROM fund_metric_state WHERE isin IN ({placeholders})""",
        isins,
    )
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)
    if not df.empty:
        df["calculated_at"] = df["calculated_at"].astype(str)
    return df


@dataclass
class RecomputeCheckResult:
    unchanged: pd.DataFrame  # (isin, metric_version) present before AND after with an identical input_hash/calculated_at
    missing_after: pd.DataFrame  # (isin, metric_version) present before but absent from the after-snapshot
    n_checked: int


def assert_recompute_happened(before: pd.DataFrame, after: pd.DataFrame) -> RecomputeCheckResult:
    """Compares two capture_state() snapshots on (isin, metric_version). A row counts as
    genuinely recomputed only if its input_hash OR calculated_at changed. `unchanged` and
    `missing_after` both being empty is the only "recompute verified" outcome; either non-empty
    means the A/B result being measured is void for those rows (Method Control #3)."""
    if before.empty:
        return RecomputeCheckResult(
            unchanged=pd.DataFrame(columns=["isin", "metric_version", "input_hash", "calculated_at"]),
            missing_after=pd.DataFrame(columns=["isin", "metric_version"]),
            n_checked=0,
        )
    merged = before.merge(after, on=["isin", "metric_version"], how="left", suffixes=("_before", "_after"))
    missing_after = merged[merged["input_hash_after"].isna()][["isin", "metric_version"]].reset_index(drop=True)
    present = merged.dropna(subset=["input_hash_after"])
    same_hash = present["input_hash_before"] == present["input_hash_after"]
    same_date = present["calculated_at_before"] == present["calculated_at_after"]
    unchanged = (
        present[same_hash & same_date][["isin", "metric_version", "input_hash_before", "calculated_at_before"]]
        .rename(columns={"input_hash_before": "input_hash", "calculated_at_before": "calculated_at"})
        .reset_index(drop=True)
    )
    return RecomputeCheckResult(unchanged=unchanged, missing_after=missing_after, n_checked=len(before))
