# -*- coding: utf-8 -*-
"""
backlog_release.py -- the rule that decides whether a ticket may be closed by a delivery, shared by scripts/ops/close_release.py (a person, at a console) and the Backlog v3
autopilot (scripts/audit/backlog_sync.py, W3 of doc/especificacion/ACTION_PLAN_BACKLOG_V3.md): one rule in one place (P#11).

The audit-incident gate (FND-0264, GOV-05): a ticket linked to a control.audit_incident that is not CERTIFIED (by a later, complete re-audit) or ACCEPTED (by an active residual)
cannot be closed -- no CRITICAL defect closes by a claim or by a recompute alone. The backlog and `fondos` are different databases, so no SQL constraint can express it: it lives
here, in the code that closes tickets. Fail closed: with the loop switch ON the incident store must exist.

Pure functions take plain values; `read_incident_facts()` is the only I/O (a read-only look at `fondos`).
"""
from __future__ import annotations

from typing import Optional

CLOSABLE_STATUSES = ("IN_PROGRESS", "READY_FOR_DEPLOY")


def incident_gate(tickets: list, incidents: list, table_present: bool, loop_enabled: bool) -> list:
    """Reasons the incident gate refuses, as a flat list of `FND-nnnn: reason` strings. Fail closed: with the loop switch ON the store must exist. Pure."""
    from shared.statistical_audit.incidents import closable
    if not table_present:
        return ["AUDIT_INCIDENT_LOOP_ENABLED is on and control.audit_incident does not exist (fail closed)"] if loop_enabled else []
    why = []
    for t in tickets:
        why += [f"{t}: {r}" for r in closable(t, incidents).reasons]
    return why


def incident_reasons(tickets: list, incidents: list, table_present: bool, loop_enabled: bool) -> dict:
    """{ticket: [reasons]} for every ticket that the incident gate refuses (a ticket that passes is absent). With the store missing under a switch that is ON, EVERY ticket is refused. Pure."""
    out: dict = {}
    for t in tickets:
        why = incident_gate([t], incidents, table_present, loop_enabled)
        if why:
            out[t] = why
    return out


def read_incident_facts() -> tuple:
    """(table_present, incidents): a read-only look at the incident store of `fondos`. Raises on a connection failure (the caller treats that as 'cannot verify', never as 'clear')."""
    from shared.db import get_connection
    from shared.statistical_audit import incidents as inc
    from shared.statistical_audit import persistence as ps
    conn = get_connection()
    try:
        present = ps.incident_table_exists(conn)
        incidents = inc.load_incidents(conn) if present else []
        conn.rollback()
    finally:
        conn.close()
    return present, incidents


def closable_by_release(rows: dict, tickets: list, *, closable_statuses: tuple = CLOSABLE_STATUSES) -> list:
    """Reasons a ticket cannot be closed by a release. `rows` = {ap_id: (status, release_version)}. Pure."""
    why = []
    for t in tickets:
        if t not in rows:
            why.append(f"{t} does not exist")
            continue
        status, release = rows[t]
        if status not in closable_statuses:
            why.append(f"{t} is {status}; a release closes IN_PROGRESS or READY_FOR_DEPLOY tickets")
        if release:
            why.append(f"{t} already carries release {release}")
    return why


def release_label(year: int, month: int, tag: str) -> Optional[str]:
    """`R<yyyy>.<mm>-<TAG>`: the label sp_transition and sp_close_release accept for a delivery closure, or None when the tag is not [A-Z0-9][A-Z0-9-]*. Pure."""
    import re
    tag = str(tag or "").upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]*", tag) or not (1 <= int(month) <= 12) or not (2000 <= int(year) <= 2999):
        return None
    return f"R{int(year):04d}.{int(month):02d}-{tag}"
