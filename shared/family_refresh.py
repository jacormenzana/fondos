"""
shared/family_refresh.py -- which family-corrected funds still owe a derived-attribute refresh (FND-0244 follow-up).

fund_family_builder rule 4 rewrites ONLY Fund_Nature of a share class (and logs `FAMILY_NATURE_CORRECTION` in
ingestion_log). Everything derived from the nature (profile, style, credit quality, duration, heuristic_core...)
keeps the values computed from the class's own previous nature until a P1 pass recomputes them, and a plain pass
would first flip the nature back by the class's own evidence. `run_block.py --family-nature-refresh` recomputes
those funds with the persisted (family) nature and writes `FAMILY_REFRESH_DONE` in the SAME transaction as the
fund upsert (fund_writer.publish_fund), so a fund is either refreshed and marked, or neither.

PENDING = the latest correction row of an ISIN has no later DONE row. "Later" is judged by the identity `id`
(monotonic, immune to the second-resolution timestamps and to the TEXT/timestamptz difference between the test
fixtures and production). One definition for every consumer (P#11): the refresh selector, the P3 freshness gate,
the closure script and the telemetry flag.

An ACTIVE fund only: a retired fund feeds no output and is not reachable by the classifier (it is absent from the
harvest), so it cannot block anything. A fund whose KIID is WRONG_DOC is excluded from every block and could never
be refreshed, so it must not block P3 either.

The SQL below is written as literals (not assembled from constants) so that tests/test_sql_explain_sweep_pg.py can
resolve and EXPLAIN every statement.
"""
from __future__ import annotations

STEP_CORRECTION = "FAMILY_NATURE_CORRECTION"
STEP_DONE = "FAMILY_REFRESH_DONE"


def pending_family_refresh(conn, active_only: bool = True) -> list:
    """ISINs whose latest FAMILY_NATURE_CORRECTION has no later FAMILY_REFRESH_DONE.

    active_only=True (default, what blocks P3): active funds that are not WRONG_DOC. False: every ISIN with an open
    correction, retired ones included (diagnostics only)."""
    params = (STEP_CORRECTION, STEP_DONE)
    if active_only:
        rows = conn.execute(
            "WITH latest AS (SELECT isin, MAX(id) AS correction_id FROM ingestion_log "
            "WHERE step = %s AND isin IS NOT NULL GROUP BY isin) "
            "SELECT l.isin FROM latest l JOIN fund_master fm ON fm.isin = l.isin "
            "WHERE fm.in_current_universe = 1 "
            "AND NOT EXISTS (SELECT 1 FROM ingestion_log d WHERE d.isin = l.isin AND d.step = %s AND d.id > l.correction_id) "
            "AND NOT EXISTS (SELECT 1 FROM fund_kiid_metadata k WHERE k.isin = l.isin AND k.kiid_class = 1 "
            "AND k.kiid_status = 'WRONG_DOC') "
            "ORDER BY l.isin", params).fetchall()
    else:
        rows = conn.execute(
            "WITH latest AS (SELECT isin, MAX(id) AS correction_id FROM ingestion_log "
            "WHERE step = %s AND isin IS NOT NULL GROUP BY isin) "
            "SELECT l.isin FROM latest l "
            "WHERE NOT EXISTS (SELECT 1 FROM ingestion_log d WHERE d.isin = l.isin AND d.step = %s AND d.id > l.correction_id) "
            "ORDER BY l.isin", params).fetchall()
    return [r[0] for r in rows]


def summarize_pending(isins: list, limit: int = 50) -> str:
    """Short, bounded text for a gate detail / log line: the count and the first `limit` ISINs."""
    if not isins:
        return "0 pending"
    shown = ", ".join(isins[:limit])
    more = f" (+{len(isins) - limit} more)" if len(isins) > limit else ""
    return f"{len(isins)} pending: {shown}{more}"


def split_reachable(pending: list, master_isins: list) -> tuple:
    """(pending ISINs the classifier can reach, pending ISINs it cannot). The classifier loads its universe from the latest
    harvest, so an ISIN absent from it (retired) cannot be refreshed; pure, order of `master_isins` preserved."""
    wanted = set(pending)
    reachable = [i for i in master_isins if i in wanted]
    seen = set(reachable)
    return reachable, [i for i in pending if i not in seen]


def apply_family_nature(own_nature, family_nature, ev_trace: dict, conf) -> tuple:
    """(nature to use, trace, overridden). In a refresh pass the persisted family nature wins over the fund's own vote: the pass exists to
    recompute what is DERIVED from it, and the own vote is exactly what the family builder overruled. The own vote stays visible in the
    trace. No family nature (None / empty) leaves the fund's own resolution untouched. Pure (R-7)."""
    if not family_nature:
        return own_nature, ev_trace, False
    if own_nature != family_nature:
        ev_trace = {**ev_trace, "reason": f"FAMILY_REFERENCE nature {family_nature} kept "
                                          f"(own evidence: {own_nature}, conf={conf}); {ev_trace.get('reason')}"}
    return family_nature, ev_trace, True
