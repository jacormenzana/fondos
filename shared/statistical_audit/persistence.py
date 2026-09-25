"""Functions #15-#16 (AUDITORIA_ESTADISTICA.md §4): emit_statistics,
emit_findings, preserve_and_write. The only module in this package that
writes to the database (Phase C — snapshot.build_population is read-only,
everything in distributions/outliers/comparisons/invariants/timeseries is
pure). Targets audit_statistic and audit_finding (§6) plus, for
preserve_and_write, fund_cost_corrections.
"""
from __future__ import annotations

import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

try:
    from shared.db import is_postgres_connection, executemany
except ModuleNotFoundError:
    _shared_root = Path(__file__).resolve().parents[2]
    if str(_shared_root) not in sys.path:
        sys.path.insert(0, str(_shared_root))
    from shared.db import is_postgres_connection, executemany


def _split_value(value: Any) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, str(value)
    if isinstance(value, (int, float)):
        return (None if math.isnan(value) else float(value)), None
    return None, str(value)


def emit_statistics(
    conn: sqlite3.Connection,
    run_id: str,
    domain: str,
    population: str,
    group_key: str,
    stats: Mapping[str, Any],
    n: int | None = None,
    catalog_version: str | None = None,
) -> int:
    """One row per stats entry. Numeric values go to stat_value; NaN is
    stored as NULL (NULL means "not computed", matching profile_moments'/
    profile_mass_points' own NaN-for-not-applicable convention); everything
    else (status labels like MEAN_NEAR_ZERO, dominant values that are
    strings, mass_class) goes to stat_text so no information from the
    profiling functions is lost.
    """
    rows = []
    for stat_name, value in stats.items():
        stat_value, stat_text = _split_value(value)
        rows.append((run_id, domain, population, group_key, stat_name, stat_value, stat_text, n, catalog_version))

    if is_postgres_connection(conn):
        sql = (
            "INSERT INTO audit_statistic "
            "(run_id, domain, population, group_key, stat_name, stat_value, stat_text, n, catalog_version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (run_id, domain, population, group_key, stat_name) DO UPDATE SET "
            "stat_value = excluded.stat_value, stat_text = excluded.stat_text, "
            "n = excluded.n, catalog_version = excluded.catalog_version, computed_at = DEFAULT"
        )
    else:
        sql = (
            "INSERT OR REPLACE INTO audit_statistic "
            "(run_id, domain, population, group_key, stat_name, stat_value, stat_text, n, catalog_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
    executemany(conn, sql, rows)
    conn.commit()
    return len(rows)


def clear_run(conn: sqlite3.Connection, run_id: str, domain: str) -> tuple[int, int]:
    """Deletes any rows a previous persist left for (run_id, domain) so that
    re-persisting the same run_id replaces it instead of duplicating findings
    (audit_finding is a plain INSERT; audit_statistic's upsert would also leave
    stale rows for groups a re-run no longer produces). Does NOT commit: the
    caller's first emit_* commit lands the delete and the first inserts
    together, so an interrupted persist rolls back to the prior run intact.
    """
    ph = "%s" if is_postgres_connection(conn) else "?"
    n_findings = conn.execute(
        f"DELETE FROM audit_finding WHERE run_id = {ph} AND domain = {ph}", (run_id, domain)
    ).rowcount
    n_stats = conn.execute(
        f"DELETE FROM audit_statistic WHERE run_id = {ph} AND domain = {ph}", (run_id, domain)
    ).rowcount
    return n_stats, n_findings


def statistics_to_frame(
    statistics: Sequence[tuple[str, str, Mapping[str, Any], int | None]],
) -> pd.DataFrame:
    """The inverse of emit_statistics' row shape, without touching the DB —
    lets compare_runs.compare_runs() diff a freshly-computed AuditRun.statistics
    list against a previously *persisted* run (loaded via
    compare_runs.load_run_statistics) without writing the fresh run first.
    Only numeric values survive (matching what audit_statistic.stat_value
    actually stores); status-label stats are dropped here, same as they'd be
    dropped from stat_value on a real emit_statistics call. Entries are
    (population, group_key, stats, n) — the same 4-tuple shape AuditRun.statistics
    carries, so PEER-segmented statistics compare correctly against their own
    peer population, not against GLOBAL.
    """
    rows = []
    for population, group_key, stats, _n in statistics:
        for stat_name, value in stats.items():
            stat_value, _stat_text = _split_value(value)
            rows.append({
                "population": population, "group_key": group_key,
                "stat_name": stat_name, "stat_value": stat_value,
            })
    return pd.DataFrame(rows, columns=["population", "group_key", "stat_name", "stat_value"])


_FINDING_COLUMNS = (
    "block", "rule_id", "rule_class", "severity", "group_key", "isin",
    "value", "reference_value", "threshold", "distance", "evidence",
    "root_cause_candidate",
)


def emit_findings(
    conn: sqlite3.Connection,
    run_id: str,
    domain: str,
    findings: Sequence[Mapping[str, Any]],
    catalog_version: str | None = None,
) -> int:
    """findings: mappings keyed by (a subset of) _FINDING_COLUMNS; any
    column absent from a given finding is written as NULL.
    """
    if not findings:
        return 0

    rows = [
        (run_id, domain, catalog_version, *(f.get(col) for col in _FINDING_COLUMNS))
        for f in findings
    ]
    ph = "%s" if is_postgres_connection(conn) else "?"
    placeholders = ", ".join([ph] * (3 + len(_FINDING_COLUMNS)))
    executemany(
        conn,
        f"INSERT INTO audit_finding (run_id, domain, catalog_version, {', '.join(_FINDING_COLUMNS)}) "
        f"VALUES ({placeholders})",
        rows,
    )
    conn.commit()
    return len(rows)


@dataclass
class CorrectionRecord:
    isin: str
    column: str
    old_value: float | None
    new_value: float | None
    reason: str
    evidence: str | None = None


def preserve_and_write(
    conn: sqlite3.Connection,
    record: CorrectionRecord,
    apply_write: Callable[[sqlite3.Connection], None],
) -> None:
    """Writes the preservation row to fund_cost_corrections and then runs
    apply_write(conn), both inside one transaction. Closes finding J
    (AUDITORIA_ESTADISTICA.md §7): fund_cost_corrections had 6,077 rows in
    production but zero code writers — the "write here before any cost
    correction" contract was a prompt instruction, never enforced. If
    apply_write raises, the whole transaction rolls back: the preservation
    row must never exist without the write it documents, or vice versa.
    """
    try:
        if is_postgres_connection(conn):
            conn.execute(
                "INSERT INTO fund_cost_corrections "
                "(isin, column_name, old_value, new_value, reason, evidence) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (record.isin, record.column, record.old_value, record.new_value,
                 record.reason, record.evidence),
            )
        else:
            conn.execute(
                "INSERT INTO fund_cost_corrections "
                "(ISIN, Column_Name, Old_Value, New_Value, Reason, Evidence) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (record.isin, record.column, record.old_value, record.new_value,
                 record.reason, record.evidence),
            )
        apply_write(conn)
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
