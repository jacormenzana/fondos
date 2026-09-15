"""Function #12 (AUDITORIA_ESTADISTICA.md §4 #12, doc §2.6/2.7): reconcile_with_alerts.

Filters a list of audit findings down to those NOT already surfaced by the
production alert engine (fund_metric_alerts) -- avoids double-reporting the
same known issue through two independent mechanisms (this audit's own
statistical detection vs. ALERT_RULES). No DB writes here (package
convention, see __init__.py).

Deliberately NOT wired into scripts/audit/run_statistical_audit.py yet (as
of 2026-09-15): fund_metric_alerts in production still holds rows written
by pre-fix code (AUDITORIA_ESTADISTICA.md §2.5/§2.6 D1-D3) until the next
real P2 run -- reconciling against it today would reconcile against stale
data, per the runner's own documented precondition. Wire it in once that
recompute has happened.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd


def isin_metric_key(finding: dict) -> "tuple[str, str] | None":
    """Default key_fn: (isin, metric) from a BLOCK4-style finding dict --
    the only findings in run_statistical_audit.py that carry a per-fund
    isin (see its _block4()). Assumes the group_key convention used
    throughout that runner, where the metric name is the first
    '|'-separated segment (e.g. 'sharpe|since_inception|0|v1'). Anything
    without both an isin and a group_key returns None (always incremental)."""
    isin = finding.get("isin")
    group_key = finding.get("group_key")
    if not isin or not group_key:
        return None
    metric = str(group_key).split("|", 1)[0]
    return (isin, metric)


def reconcile_with_alerts(
    findings: list[dict],
    alerts_df: pd.DataFrame,
    key_fn: Callable[[dict], "tuple[str, str] | None"] = isin_metric_key,
) -> list[dict]:
    """Returns the subset of `findings` NOT already known via alerts_df.

    A finding whose key_fn(finding) == (isin, metric) matches an
    (isin, metric) pair present in alerts_df (any window/level) is dropped
    -- the production alert engine already surfaces it, so the audit
    reporting it too would be redundant, not incremental.

    A finding for which key_fn returns None (no isin -- most BLOCK1/2/5/7
    findings are group-level statistics, not per-fund) is ALWAYS kept:
    alerts_df is per-fund by construction and cannot corroborate or
    contradict a group-level statistic.
    """
    if (
        alerts_df is None or alerts_df.empty
        or "isin" not in alerts_df.columns or "metric" not in alerts_df.columns
    ):
        return list(findings)

    known_keys = set(zip(alerts_df["isin"], alerts_df["metric"]))

    incremental = []
    for finding in findings:
        key = key_fn(finding)
        if key is not None and key in known_keys:
            continue
        incremental.append(finding)
    return incremental
