"""Functions #15-#16 (AUDITORIA_ESTADISTICA.md §4): emit_statistics,
emit_findings, preserve_and_write. The only module in this package that
writes to the database (Phase C — snapshot.build_population is read-only,
everything in distributions/outliers/comparisons/invariants/timeseries is
pure). Targets audit_statistic and audit_finding (§6) plus, for
preserve_and_write, fund_cost_corrections.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

try:
    from shared.db import executemany
except ModuleNotFoundError:
    _shared_root = Path(__file__).resolve().parents[2]
    if str(_shared_root) not in sys.path:
        sys.path.insert(0, str(_shared_root))
    from shared.db import executemany


def _split_value(value: Any) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, str(value)
    if isinstance(value, (int, float)):
        return (None if math.isnan(value) else float(value)), None
    return None, str(value)


def emit_statistics(
    conn: "psycopg.Connection",
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
    rows = _statistic_rows(run_id, domain, population, group_key, stats, n, catalog_version)
    executemany(conn, _STATISTIC_SQL, rows)
    conn.commit()
    return len(rows)


_STATISTIC_SQL = (
    "INSERT INTO audit_statistic "
    "(run_id, domain, population, group_key, stat_name, stat_value, stat_text, n, catalog_version) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (run_id, domain, population, group_key, stat_name) DO UPDATE SET "
    "stat_value = excluded.stat_value, stat_text = excluded.stat_text, "
    "n = excluded.n, catalog_version = excluded.catalog_version, computed_at = DEFAULT"
)


def _statistic_rows(run_id, domain, population, group_key, stats, n, catalog_version) -> list:
    rows = []
    for stat_name, value in stats.items():
        stat_value, stat_text = _split_value(value)
        rows.append((run_id, domain, population, group_key, stat_name, stat_value, stat_text, n, catalog_version))
    return rows


def clear_run(conn: "psycopg.Connection", run_id: str, domain: str) -> tuple[int, int]:
    """Deletes any rows a previous persist left for (run_id, domain) so that
    re-persisting the same run_id replaces it instead of duplicating findings
    (audit_finding is a plain INSERT; audit_statistic's upsert would also leave
    stale rows for groups a re-run no longer produces). Does NOT commit: the
    caller's first emit_* commit lands the delete and the first inserts
    together, so an interrupted persist rolls back to the prior run intact.
    """
    ph = "%s"
    n_findings = conn.execute(
        f"DELETE FROM audit_finding WHERE run_id = {ph} AND domain = {ph}", (run_id, domain)
    ).rowcount
    n_stats = conn.execute(
        f"DELETE FROM audit_statistic WHERE run_id = {ph} AND domain = {ph}", (run_id, domain)
    ).rowcount
    if finding_isin_table_exists(conn):          # FND-0234(d): the ISIN detail of the same run goes with it
        conn.execute(f"DELETE FROM audit_finding_isin WHERE run_id = {ph} AND domain = {ph}", (run_id, domain))
    return n_stats, n_findings


def finding_isin_table_exists(conn: "psycopg.Connection") -> bool:
    """True when audit_finding_isin is visible on the connection's search_path (the owner migration was applied).
    Unqualified on purpose, like every other table name in this module."""
    return conn.execute("SELECT to_regclass('audit_finding_isin')").fetchone()[0] is not None


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


def _persisted_value(finding: Mapping[str, Any], column: str) -> Any:
    """A finding's value for `column`. A fully accepted finding (accepted_residuals.apply_accepted) carries the in-memory class
    ACCEPTED_RESIDUAL, which control.audit_finding.rule_class's CHECK does not allow: it is persisted under the class it had
    before the downgrade (severity INFO, evidence "ACCEPTED RESIDUAL ..."), so accepting every violator of a rule cannot
    make --persist fail. The promotion gate (gating.py) re-derives acceptance from audit_accepted_finding, never from this."""
    value = finding.get(column)
    if column == "rule_class" and value == "ACCEPTED_RESIDUAL":
        return finding.get("accepted_from_class") or "HARD_INVARIANT"
    return value


def emit_findings(
    conn: "psycopg.Connection",
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
    rows = _finding_rows(run_id, domain, findings, catalog_version)
    executemany(conn, _FINDING_SQL, rows)
    conn.commit()
    return len(rows)


_FINDING_SQL = (
    f"INSERT INTO audit_finding (run_id, domain, catalog_version, {', '.join(_FINDING_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * (3 + len(_FINDING_COLUMNS)))})"
)


def _finding_rows(run_id, domain, findings, catalog_version) -> list:
    return [(run_id, domain, catalog_version, *(_persisted_value(f, col) for col in _FINDING_COLUMNS)) for f in findings]


def emit_accepted_residuals(
    conn: "psycopg.Connection",
    domain: str,
    rule_id: str,
    isins: Sequence[str],
    ap_id: str,
    reason: str,
    expires_at: Any,
    accepted_by: str | None = None,
) -> int:
    """Records (or renews) acceptances for `isins` under (domain, rule_id). Re-accepting an ISIN replaces its AP, reason
    and expiry (an explicit renewal). Commits. Returns the number of rows written."""
    if not isins:
        return 0
    rows = [(domain, rule_id, i, ap_id, reason, accepted_by, expires_at) for i in sorted(set(isins))]
    executemany(
        conn,
        "INSERT INTO audit_accepted_finding (domain, rule_id, isin, ap_id, reason, accepted_by, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (domain, rule_id, isin) DO UPDATE SET ap_id = excluded.ap_id, reason = excluded.reason, "
        "accepted_by = excluded.accepted_by, expires_at = excluded.expires_at, accepted_at = current_date",
        rows,
    )
    conn.commit()
    return len(rows)


# ---- exception history (FND-0263, GOV-04) -----------------------------------------------------------------------------------------
# Every decision is an event in control.audit_exception_event (append-only, enforced by a trigger); control.audit_accepted_finding, which
# every reader uses, is refreshed from accepted_residuals.fold_events in the SAME transaction as the event. Decision D6: GRANT (or
# RENEW) then a separate APPROVE; a grant is not active until approved; same person allowed, no time pad.

_EVENT_SQL = (
    "INSERT INTO audit_exception_event (event_type, domain, rule_id, isins, ap_id, reason, expires_at, cause, ref_event_id, actor) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING event_id"
)


def exception_events_table_exists(conn: "psycopg.Connection") -> bool:
    """True when audit_exception_event is visible (scripts/ops/migrate_audit_governance.py was applied)."""
    return conn.execute("SELECT to_regclass('audit_exception_event')").fetchone()[0] is not None


def _refresh_projection(conn: "psycopg.Connection", domain: str, rule_id: str, isins: Sequence[str]) -> None:
    """Rewrite the audit_accepted_finding rows of (domain, rule_id, isins) from the folded history. No commit."""
    from shared.statistical_audit.accepted_residuals import fold_events, load_events
    folded = fold_events(load_events(conn, domain))
    for isin in sorted(set(isins)):
        state = folded.get((rule_id, isin))
        if state is None:
            conn.execute("DELETE FROM audit_accepted_finding WHERE domain = %s AND rule_id = %s AND isin = %s", (domain, rule_id, isin))
            continue
        conn.execute(
            "INSERT INTO audit_accepted_finding (domain, rule_id, isin, ap_id, reason, accepted_by, accepted_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (domain, rule_id, isin) DO UPDATE SET ap_id = excluded.ap_id, reason = excluded.reason, "
            "accepted_by = excluded.accepted_by, accepted_at = excluded.accepted_at, expires_at = excluded.expires_at",
            (domain, rule_id, isin, state["ap_id"], state["reason"], state["approved_by"], state["approved_at"].date(),
             state["expires_at"]))


def _write_event(conn, refresh: tuple | None, *values) -> int:
    """Insert one event (and refresh the projection of `refresh` = (domain, rule_id, isins)) in one transaction; commit."""
    try:
        (event_id,) = conn.execute(_EVENT_SQL, values).fetchone()
        if refresh is not None:
            _refresh_projection(conn, *refresh)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return event_id


def grant_exception(conn: "psycopg.Connection", domain: str, rule_id: str, isins: Sequence[str], ap_id: str, reason: str,
                    expires_at: Any, actor: str, *, renew: bool = False) -> int:
    """Record a GRANT (or RENEW) of (domain, rule_id, isins). Pending: nothing is accepted until approve_exception. Returns event_id."""
    if not isins:
        raise ValueError("a grant needs at least one ISIN")
    return _write_event(conn, None, "RENEW" if renew else "GRANT", domain, rule_id, sorted(set(isins)), ap_id, reason, expires_at,
                        None, None, actor)


def _grant(conn, grant_event_id: int) -> tuple:
    """(the event, its grant_state)."""
    from shared.statistical_audit.accepted_residuals import grant_state, load_events
    row = conn.execute("SELECT domain FROM audit_exception_event WHERE event_id = %s", (grant_event_id,)).fetchone()
    if row is None:
        raise ValueError(f"event {grant_event_id} does not exist")
    events = load_events(conn, row[0])
    return next(e for e in events if e["event_id"] == grant_event_id), grant_state(events, grant_event_id)


def approve_exception(conn: "psycopg.Connection", grant_event_id: int, actor: str, today: Any = None) -> int:
    """APPROVE a pending GRANT / RENEW: from now on its ISINs are accepted until its expiry. Refuses a grant that is not pending or
    already lapsed (a decision on an expired grant is a new grant). Returns event_id."""
    from datetime import date
    g, state = _grant(conn, grant_event_id)
    if state != "PENDING":
        raise ValueError(f"event {grant_event_id} is {state}, not a pending grant")
    if g["expires_at"] <= (today or date.today()):
        raise ValueError(f"grant {grant_event_id} expired on {g['expires_at']}: record a new grant")
    return _write_event(conn, (g["domain"], g["rule_id"], g["isins"]), "APPROVE", g["domain"], g["rule_id"], None, None, None, None,
                        None, grant_event_id, actor)


def revoke_exception(conn: "psycopg.Connection", grant_event_id: int, actor: str, cause: str, reason: str,
                     isins: Sequence[str] | None = None) -> int:
    """REVOKE a grant (all its ISINs, or `isins` among them): they stop being accepted now. `cause` in REVOKE_CAUSES. Returns event_id."""
    from shared.statistical_audit.accepted_residuals import REVOKE_CAUSES
    if cause not in REVOKE_CAUSES:
        raise ValueError(f"cause must be one of {REVOKE_CAUSES}")
    g, state = _grant(conn, grant_event_id)
    if state in ("MISSING", "NOT_A_GRANT", "REVOKED"):
        raise ValueError(f"event {grant_event_id} is {state}: nothing to revoke")
    subset = sorted(set(isins)) if isins else None
    if subset and not set(subset) <= set(g["isins"]):
        raise ValueError(f"ISINs not in grant {grant_event_id}: {sorted(set(subset) - set(g['isins']))}")
    return _write_event(conn, (g["domain"], g["rule_id"], subset or g["isins"]), "REVOKE", g["domain"], g["rule_id"], subset, None,
                        reason, None, cause, grant_event_id, actor)


# ---- incidents (FND-0264, GOV-05) ------------------------------------------------------------------------------------------------------------
# The decisions live in shared/statistical_audit/incidents.py (pure); these functions only write them, each in its own transaction.

def incident_table_exists(conn: "psycopg.Connection") -> bool:
    """True when audit_incident is visible (scripts/ops/migrate_audit_incident.py was applied)."""
    return conn.execute("SELECT to_regclass('audit_incident')").fetchone()[0] is not None


def open_incident(conn: "psycopg.Connection", cand, *, ap_id: str | None = None, affected_calc_version: str | None = None) -> int:
    """INSERT one OPEN incident for a Candidate (incidents.Candidate). Idempotent: the partial unique index "one live incident per finding
    key" turns a second open into ON CONFLICT DO NOTHING, reported as a ValueError (a re-run of --open never duplicates). Commits; returns
    incident_id."""
    try:
        row = conn.execute(
            "INSERT INTO audit_incident (finding_key, domain, rule_id, group_key, ap_id, affected_calc_version, isins, opened_run_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING incident_id",
            (cand.key, cand.domain, cand.rule_id, cand.group_key, ap_id, affected_calc_version, list(cand.isins), cand.run_id)).fetchone()
    except Exception:
        conn.rollback()
        raise
    if row is None:
        conn.rollback()
        raise ValueError(f"a live incident already exists for {cand.key}")
    conn.commit()
    return row[0]


def annotate_incident(conn: "psycopg.Connection", incident_id: int, *, ap_id: str | None = None, root_cause: str | None = None,
                      corrective_action: str | None = None, fix_kind: str | None = None) -> None:
    """Record the owner's analysis on an OPEN incident (COALESCE: an omitted field keeps its value). Commits."""
    from shared.statistical_audit.incidents import FIX_KINDS
    if fix_kind is not None and fix_kind not in FIX_KINDS:
        raise ValueError(f"fix_kind must be one of {FIX_KINDS}")
    try:
        n = conn.execute(
            "UPDATE audit_incident SET ap_id = COALESCE(%s, ap_id), root_cause = COALESCE(%s, root_cause), "
            "corrective_action = COALESCE(%s, corrective_action), fix_kind = COALESCE(%s, fix_kind) "
            "WHERE incident_id = %s AND state = 'OPEN'", (ap_id, root_cause, corrective_action, fix_kind, incident_id)).rowcount
    except Exception:
        conn.rollback()
        raise
    if n != 1:
        conn.rollback()
        raise ValueError(f"incident {incident_id} does not exist or is not OPEN")
    conn.commit()


def _require_one(conn, n: int, incident_id: int, expected: str) -> None:
    if n != 1:
        conn.rollback()
        raise ValueError(f"incident {incident_id} is not {expected}")
    conn.commit()


def certify_incident(conn: "psycopg.Connection", incident_id: int, run_id: str) -> None:
    """OPEN -> CERTIFIED by the re-audit `run_id`. The caller has run incidents.certify first; this only records it."""
    try:
        n = conn.execute("UPDATE audit_incident SET state = 'CERTIFIED', certified_run_id = %s, certified_at = clock_timestamp() "
                         "WHERE incident_id = %s AND state = 'OPEN'", (run_id, incident_id)).rowcount
    except Exception:
        conn.rollback()
        raise
    _require_one(conn, n, incident_id, "OPEN")


def accept_incident(conn: "psycopg.Connection", incident_id: int) -> None:
    """OPEN -> ACCEPTED (the caller has run incidents.acceptance against the active residuals)."""
    try:
        n = conn.execute("UPDATE audit_incident SET state = 'ACCEPTED' WHERE incident_id = %s AND state = 'OPEN'", (incident_id,)).rowcount
    except Exception:
        conn.rollback()
        raise
    _require_one(conn, n, incident_id, "OPEN")


def close_incident(conn: "psycopg.Connection", incident_id: int) -> None:
    """CERTIFIED / ACCEPTED -> CLOSED, once the backlog ticket is closed."""
    try:
        n = conn.execute("UPDATE audit_incident SET state = 'CLOSED', closed_at = clock_timestamp() "
                         "WHERE incident_id = %s AND state IN ('CERTIFIED', 'ACCEPTED')", (incident_id,)).rowcount
    except Exception:
        conn.rollback()
        raise
    if n != 1:
        conn.rollback()
        raise ValueError(f"incident {incident_id} is neither CERTIFIED nor ACCEPTED")
    conn.commit()


def emit_finding_isins(
    conn: "psycopg.Connection",
    run_id: str,
    domain: str,
    findings: Sequence[Mapping[str, Any]],
) -> int:
    """One row per (rule, group_key, ISIN) for every finding that carries `violating_isins` (FND-0234(d)). audit_finding keeps
    one aggregate row per invariant ("88/2809 rows violate"); this table says WHICH funds. Idempotent (ON CONFLICT DO NOTHING).
    Commits. Returns the rows written; 0 -- and no error -- when the table does not exist yet (migration not applied)."""
    rows = _finding_isin_rows(run_id, domain, findings)
    if not rows or not finding_isin_table_exists(conn):
        return 0
    executemany(conn, _FINDING_ISIN_SQL, rows)
    conn.commit()
    return len(rows)


_FINDING_ISIN_SQL = (
    "INSERT INTO audit_finding_isin (run_id, domain, rule_id, group_key, isin) VALUES (%s, %s, %s, %s, %s) "
    "ON CONFLICT DO NOTHING"
)
# FND-0275: the same row plus the lineage of the metric rows the ISIN carried (feature-detected: scripts/ops/migrate_audit_finding_lineage.py)
_FINDING_ISIN_LINEAGE_SQL = (
    "INSERT INTO audit_finding_isin (run_id, domain, rule_id, group_key, isin, algorithm_versions, batch_ids) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"
)
# Only the p2_metrics domain has stamped rows to look up: cost attributes carry no algorithm_version / batch_id (their lineage columns stay NULL).
LINEAGE_DOMAINS = ("p2_metrics",)


def finding_isin_has_lineage(conn: "psycopg.Connection") -> bool:
    """True when audit_finding_isin carries the lineage columns (the FND-0275 owner migration was applied)."""
    return conn.execute("SELECT count(*) FROM pg_attribute WHERE attrelid = to_regclass('audit_finding_isin') "
                        "AND attname IN ('algorithm_versions', 'batch_ids') AND NOT attisdropped").fetchone()[0] == 2


def load_metric_lineage(conn: "psycopg.Connection", isins: Sequence[str]) -> dict:
    """{isin: (sorted distinct algorithm_versions, sorted distinct batch_ids)} of the fund_metrics rows each ISIN carries NOW (an ISIN
    with no stamped row maps to two empty lists). Read on the persisting connection, right after the audit read the data (FND-0275)."""
    out = {i: ([], []) for i in isins}
    if not out:
        return out
    cur = conn.execute(
        "SELECT isin, array_agg(DISTINCT algorithm_version) FILTER (WHERE algorithm_version IS NOT NULL), "
        "array_agg(DISTINCT batch_id) FILTER (WHERE batch_id IS NOT NULL) FROM fund_metrics WHERE isin = ANY(%s) GROUP BY isin",
        (sorted(out),))
    for isin, versions, batches in cur.fetchall():
        out[isin] = (sorted(versions or []), sorted(batches or []))
    return out


def _lineage_rows(isin_rows: Sequence[tuple], lineage: Mapping[str, tuple]) -> list:
    """`isin_rows` (run_id, domain, rule_id, group_key, isin) extended with the ISIN's (algorithm_versions, batch_ids). Pure."""
    return [(*row, *lineage.get(row[4], ([], []))) for row in isin_rows]


def _finding_isin_rows(run_id, domain, findings) -> list:
    return sorted({
        (run_id, domain, f["rule_id"], f.get("group_key") or f["rule_id"], isin)
        for f in findings for isin in (f.get("violating_isins") or ())
    })


# ---- run header + atomic persistence (FND-0261) -----------------------------------------------------------------------------------

# control.audit_run columns the writer sets (finished_at is the database's now()). No severity / drift counts: those live in
# control.cycle_audit_run, derived from audit_finding by run_id (shared/cycle_telemetry_ingest.py); the two join on run_id (P#11).
AUDIT_RUN_COLUMNS = ("scope", "status", "started_at", "universe_size", "skips", "catalog_version", "git_commit", "git_clean", "config_hash")

# Idempotent as DELETE + INSERT of the same (run_id, domain) inside persist_run: a re-persisted run never keeps an older header.
# finished_at = clock_timestamp(), not the column default now(): the runner reads the whole audit inside ONE transaction, and now() is
# that transaction's START (seen on the first live header, 2026-10-12: finished_at 25 ms after started_at for a 4-minute run).
_AUDIT_RUN_SQL = (
    "INSERT INTO audit_run (run_id, domain, scope, status, started_at, finished_at, universe_size, skips, catalog_version, git_commit, "
    "git_clean, config_hash) VALUES (%s, %s, %s, %s, %s, clock_timestamp(), %s, %s::jsonb, %s, %s, %s, %s)"
)
# FND-0263: the same row plus the execution manifest, once migrate_audit_governance.py added the column (feature-detected, so code deployed
# before the migration keeps persisting instead of failing the whole run's transaction).
AUDIT_RUN_MANIFEST = "manifest"
_AUDIT_RUN_MANIFEST_SQL = (
    "INSERT INTO audit_run (run_id, domain, scope, status, started_at, finished_at, universe_size, skips, catalog_version, git_commit, "
    "git_clean, config_hash, manifest) VALUES (%s, %s, %s, %s, %s, clock_timestamp(), %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb)"
)


def audit_run_has_manifest(conn: "psycopg.Connection") -> bool:
    return conn.execute("SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass('audit_run') AND attname = 'manifest' "
                        "AND NOT attisdropped").fetchone() is not None


def audit_run_table_exists(conn: "psycopg.Connection") -> bool:
    """True when audit_run is visible on the connection's search_path (scripts/ops/migrate_audit_run.py was applied)."""
    return conn.execute("SELECT to_regclass('audit_run')").fetchone()[0] is not None


@dataclass
class PersistResult:
    n_stats: int
    n_findings: int
    n_isin_rows: int
    header_written: bool


def persist_run(
    conn: "psycopg.Connection",
    run_id: str,
    domain: str,
    statistics: Sequence[tuple],
    findings: Sequence[Mapping[str, Any]],
    *,
    catalog_version: str | None = None,
    header: Mapping[str, Any] | None = None,
) -> PersistResult:
    """One audit run -- previous rows of the same (run_id, domain) removed, statistics, findings, ISIN detail and the audit_run header
    -- in ONE transaction: a run is either all there or not there at all (before FND-0261 each table committed on its own, so a crash
    left a partial run that the promotion gate could read as a real one). `statistics` = AuditRun.statistics 4-tuples
    (population, group_key, stats, n); `header` = AUDIT_RUN_COLUMNS values (`skips` a JSON-ready list) plus, optionally, `manifest`
    (a JSON-ready dict, written when the column exists). Tables that do not exist yet
    (audit_finding_isin, audit_run: owner migrations) are skipped, as before. Rolls back and re-raises on any failure."""
    try:
        clear_run(conn, run_id, domain)
        header_table = audit_run_table_exists(conn)
        if header_table:
            conn.execute("DELETE FROM audit_run WHERE run_id = %s AND domain = %s", (run_id, domain))
        stat_rows = [row for population, group_key, stats, n in statistics
                     for row in _statistic_rows(run_id, domain, population, group_key, stats, n, catalog_version)]
        if stat_rows:
            executemany(conn, _STATISTIC_SQL, stat_rows)
        finding_rows = _finding_rows(run_id, domain, findings, catalog_version)
        if finding_rows:
            executemany(conn, _FINDING_SQL, finding_rows)
        isin_rows = _finding_isin_rows(run_id, domain, findings) if finding_isin_table_exists(conn) else []
        if isin_rows:
            if domain in LINEAGE_DOMAINS and finding_isin_has_lineage(conn):             # FND-0275
                lineage = load_metric_lineage(conn, sorted({r[4] for r in isin_rows}))
                executemany(conn, _FINDING_ISIN_LINEAGE_SQL, _lineage_rows(isin_rows, lineage))
            else:
                executemany(conn, _FINDING_ISIN_SQL, isin_rows)
        header_written = False
        if header is not None and header_table:
            values = [json.dumps(header[c]) if c == "skips" else header[c] for c in AUDIT_RUN_COLUMNS]
            if AUDIT_RUN_MANIFEST in header and audit_run_has_manifest(conn):
                conn.execute(_AUDIT_RUN_MANIFEST_SQL, (run_id, domain, *values, json.dumps(header[AUDIT_RUN_MANIFEST], default=str)))
            else:
                conn.execute(_AUDIT_RUN_SQL, (run_id, domain, *values))
            header_written = True
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return PersistResult(len(stat_rows), len(finding_rows), len(isin_rows), header_written)


def load_finding_isins(conn: "psycopg.Connection", run_id: str, domain: str) -> pd.DataFrame:
    """The persisted violators of one run: columns rule_id, group_key, isin (empty frame when there are none, or no table)."""
    columns = ["rule_id", "group_key", "isin"]
    if not finding_isin_table_exists(conn):
        return pd.DataFrame(columns=columns)
    cur = conn.execute(
        "SELECT rule_id, group_key, isin FROM audit_finding_isin WHERE run_id = %s AND domain = %s",
        (run_id, domain),
    )
    return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=columns)


def load_finding_lineage(conn: "psycopg.Connection", run_id: str, domain: str) -> dict:
    """{(rule_id, group_key, isin): (algorithm_versions, batch_ids)} persisted with one run (FND-0275). Rows written before the
    migration, or a domain without lineage, are absent; empty dict when the columns do not exist."""
    if not (finding_isin_table_exists(conn) and finding_isin_has_lineage(conn)):
        return {}
    cur = conn.execute("SELECT rule_id, group_key, isin, algorithm_versions, batch_ids FROM audit_finding_isin "
                       "WHERE run_id = %s AND domain = %s AND algorithm_versions IS NOT NULL", (run_id, domain))
    return {(r[0], r[1], r[2]): (list(r[3]), list(r[4] or [])) for r in cur.fetchall()}


@dataclass
class CorrectionRecord:
    isin: str
    column: str
    old_value: float | None
    new_value: float | None
    reason: str
    evidence: str | None = None


def preserve_and_write(
    conn: "psycopg.Connection",
    record: CorrectionRecord,
    apply_write: Callable[["psycopg.Connection"], None],
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
        conn.execute(
            "INSERT INTO fund_cost_corrections "
            "(isin, column_name, old_value, new_value, reason, evidence) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (record.isin, record.column, record.old_value, record.new_value,
             record.reason, record.evidence),
        )
        apply_write(conn)
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
