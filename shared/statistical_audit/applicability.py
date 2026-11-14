"""D1 (FND-0392, WS0.5 of FND-0391): how many rows each rule actually tested in a run, stored per rule per run.

Why: a rule that finds nothing writes nothing. "WINDOW_FISHER_IDENTITY found no violation" and "WINDOW_FISHER_IDENTITY tested no row" look the same afterwards, and `n_applicable` lived only in the
evidence text of a VIOLATING finding ("40/245 applicable rows violate"). So after the 6 FND-0250 funds left the universe (2026-11-04) the history could not say whether the detector kept testing live
rows. Each evaluated rule now leaves two statistics in control.audit_statistic, no DDL:

    population = "INVARIANT", group_key = the rule id, stat_name = "n_applicable" (rows the rule could judge) and "n_violating" (rows that broke it); the row's `n` is n_applicable.

A rule that did not run at all (its columns were missing, its table empty) is not recorded here: it is a skip in the run header (`audit_run.skips`, FND-0261). The drift comparison (compare_runs.DRIFT_STATS) only reads
the profiling statistics, so these rows never appear in a drift report. The consumers of the history (the applicability-drop rule and the detector canaries) are WS1 (FND-0393). Pure except
load_applicability_history, which only reads.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

APPLICABILITY_POPULATION = "INVARIANT"
STAT_APPLICABLE = "n_applicable"
STAT_VIOLATING = "n_violating"


def applicability_entry(rule_id: str, n_applicable: int, n_violating: int) -> tuple:
    """The (population, group_key, stats, n) 4-tuple that AuditRun.statistics holds and persistence.persist_run writes."""
    n_app, n_viol = int(n_applicable), int(n_violating)
    if n_app < 0 or n_viol < 0 or n_viol > n_app:
        raise ValueError(f"{rule_id}: n_violating must be within 0..n_applicable, got {n_viol}/{n_app}")
    return (APPLICABILITY_POPULATION, str(rule_id), {STAT_APPLICABLE: n_app, STAT_VIOLATING: n_viol}, n_app)


APPLICABILITY_DROP_RULE_ID = "APPLICABILITY_DROP"
APPLICABILITY_DROP_PCT = 0.50          # a rule that tested more than this share fewer rows than its usual median has probably lost its input (plan D1, FND-0393)
APPLICABILITY_DROP_RUNS = 5            # the history window: the last 5 FULL runs
APPLICABILITY_DROP_MIN_RUNS = 3        # fewer runs of history than this and there is no usual level to compare with: nothing is judged


def current_applicability(statistics) -> dict:
    """{rule_id: n_applicable} of the run in memory, read from the same (population, group_key, stats, n) entries `applicability_entry` builds. Pure."""
    return {str(group): int(stats[STAT_APPLICABLE]) for population, group, stats, _n in statistics
            if population == APPLICABILITY_POPULATION and STAT_APPLICABLE in stats}


def applicability_drops(current: dict, history: "pd.DataFrame", drop_pct: float = APPLICABILITY_DROP_PCT, min_runs: int = APPLICABILITY_DROP_MIN_RUNS,
                        exclude_run_id: Optional[str] = None) -> list:
    """The rules whose tested-row count fell by MORE than `drop_pct` against the median of their stored FULL-run history (plan D1, FND-0393: the coverage-gap rule that completes the canaries).

    `current` = {rule_id: n_applicable} of this run; `history` = load_applicability_history() rows (newest first); `exclude_run_id` leaves this run's own stored row out when it was persisted already.
    A rule with fewer than `min_runs` historical values, or whose median is 0, has no usual level and is never judged (a rule new to the catalogue must not alarm). A rule absent from `current` did not
    run: that is a skip of the run header, not a drop. One dict per drop: rule_id, current, median, runs (how many history values), change (current / median - 1). Pure."""
    if history is None or len(history) == 0:
        return []
    hist = history if exclude_run_id is None else history[history["run_id"] != exclude_run_id]
    out = []
    for rule_id, now in sorted(current.items()):
        values = pd.to_numeric(hist.loc[hist["rule_id"] == rule_id, "n_applicable"], errors="coerce").dropna()
        if len(values) < int(min_runs):
            continue
        median = float(values.median())
        if median <= 0:
            continue
        if float(now) < median * (1.0 - float(drop_pct)):
            out.append({"rule_id": str(rule_id), "current": int(now), "median": median, "runs": int(len(values)), "change": float(now) / median - 1.0})
    return out


def applicability_drop_finding(drop: dict, drop_pct: float = APPLICABILITY_DROP_PCT) -> dict:
    """The audit finding for one drop (BLOCK1, WARN, like COVERAGE_CLIFF): the same dict shape the runner's other Block 1 escalations append. Pure."""
    return {
        "block": "BLOCK1", "rule_id": APPLICABILITY_DROP_RULE_ID, "rule_class": "STATISTICAL_ANOMALY", "severity": "WARN", "group_key": drop["rule_id"],
        "value": drop["current"], "reference_value": drop["median"], "threshold": -float(drop_pct), "distance": drop["change"],
        "evidence": f"{drop['rule_id']} tested {drop['current']:,} rows; the median of its last {drop['runs']} FULL runs is {drop['median']:,.0f} ({drop['change']:+.0%})",
        "root_cause_candidate": "The detector lost its input (a table, a join or a filter changed): a clean result is no longer evidence. Read the rule's inputs before trusting its silence",
    }


def load_applicability_history(conn: "psycopg.Connection", domain: str, rule_id: Optional[str] = None, runs: int = 5) -> pd.DataFrame:
    """The stored applicability of the last `runs` FULL runs of `domain` (newest first), one row per (run, rule): run_id, finished_at, rule_id, n_applicable, n_violating.
    SAMPLE runs are left out (their counts describe a few funds). Runs from before D1 have no rows. Read-only."""
    rows = conn.execute(
        "SELECT r.run_id, r.finished_at, s.group_key, "
        "MAX(s.stat_value) FILTER (WHERE s.stat_name = %s), MAX(s.stat_value) FILTER (WHERE s.stat_name = %s) "
        "FROM audit_statistic s JOIN audit_run r ON r.run_id = s.run_id AND r.domain = s.domain "
        "WHERE s.domain = %s AND s.population = %s AND r.scope = 'FULL' AND (%s::text IS NULL OR s.group_key = %s) "
        "AND r.run_id IN (SELECT run_id FROM audit_run WHERE domain = %s AND scope = 'FULL' ORDER BY finished_at DESC LIMIT %s) "
        "GROUP BY r.run_id, r.finished_at, s.group_key ORDER BY r.finished_at DESC, s.group_key",
        (STAT_APPLICABLE, STAT_VIOLATING, domain, APPLICABILITY_POPULATION, rule_id, rule_id, domain, int(runs))).fetchall()
    return pd.DataFrame([tuple(r) for r in rows], columns=["run_id", "finished_at", "rule_id", "n_applicable", "n_violating"])
