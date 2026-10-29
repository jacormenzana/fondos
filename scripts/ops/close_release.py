#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
close_release.py -- close deployed backlog tickets through backlog.sp_close_release, with the audit-incident gate (FND-0264, GOV-05).

    python scripts/ops/close_release.py --release R2026.10-GOV-05 --tickets FND-0264,FND-0275 --log "what was deployed, where verified"
    ... --apply        # without it: DRY RUN, everything is checked and rolled back

One transaction in gestion.backlog: every ticket must exist, be IN_PROGRESS or READY_FOR_DEPLOY and carry no release yet; the release label
must be unused; each ticket gets the same log (append-only) and the label; sp_close_release closes them; the result must be EXACTLY the
requested tickets, CLOSED. Before any of it, the INCIDENT GATE: a ticket linked to a control.audit_incident that is not CERTIFIED (by a later
re-audit run) or ACCEPTED (by an active residual) cannot be closed -- no CRITICAL defect closes by a claim or by a recompute alone. The
backlog and fondos are different databases, so no SQL constraint can express this: it lives here, in the one tool that closes releases.
After the commit, the closed tickets' CERTIFIED / ACCEPTED incidents are marked CLOSED in fondos (best effort, reported).

Nothing here closes a ticket as REJECTED / DUPLICATED / WORKAROUND without a deployment: that is a plain, logged status change.
Backlog v2 Phase A (FND-0331): the move to READY_FOR_DEPLOY goes through backlog.sp_transition and the closing through backlog.sp_close_release, with the identity given
by --actor (or FONDOS_ACTOR). The raw UPDATE of the pre-Phase-A form was REMOVED (owner order 2026-10-28): without step A-1 (db/pg_backlog/30_transitions.sql) the script refuses
with exit code 2 and writes nothing.
Exit codes: 0 ok (or dry run), 1 refused by a check (message says which), 2 usage or connection error (also: step A-1 not applied).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

AP_RE = re.compile(r"^FND-\d{4}$")
RELEASE_RE = re.compile(r"^R\d{4}\.\d{2}-[A-Z0-9][A-Z0-9-]*$")
CLOSABLE_STATUSES = ("IN_PROGRESS", "READY_FOR_DEPLOY")
MIN_LOG_CHARS = 40
RC_OK, RC_REFUSED, RC_USAGE = 0, 1, 2


def validate_args(release: str, tickets: list, log: str) -> list:
    """Problems with the arguments. Pure."""
    problems = []
    if not RELEASE_RE.match(release or ""):
        problems.append(f"--release {release!r} must look like R2026.10-GOV-05")
    if not tickets or any(not AP_RE.match(t) for t in tickets) or len(set(tickets)) != len(tickets):
        problems.append("--tickets must be distinct ids like FND-0264")
    if len((log or "").strip()) < MIN_LOG_CHARS:
        problems.append(f"--log must say what was deployed and where it was verified (>= {MIN_LOG_CHARS} characters)")
    return problems


def check_tickets(rows: dict, tickets: list) -> list:
    """Reasons a ticket cannot be closed by a release. `rows` = {ap_id: (status, release_version)}. Pure."""
    why = []
    for t in tickets:
        if t not in rows:
            why.append(f"{t} does not exist")
            continue
        status, release = rows[t]
        if status not in CLOSABLE_STATUSES:
            why.append(f"{t} is {status}; a release closes IN_PROGRESS or READY_FOR_DEPLOY tickets")
        if release:
            why.append(f"{t} already carries release {release}")
    return why


def incident_gate(tickets: list, incidents: list, table_present: bool, loop_enabled: bool) -> list:
    """Reasons the incident gate refuses. Fail closed: with the loop switch ON the store must exist. Pure."""
    from shared.statistical_audit.incidents import closable
    if not table_present:
        return ["AUDIT_INCIDENT_LOOP_ENABLED is on and control.audit_incident does not exist (fail closed)"] if loop_enabled else []
    why = []
    for t in tickets:
        why += [f"{t}: {r}" for r in closable(t, incidents).reasons]
    return why


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", required=True)
    ap.add_argument("--tickets", required=True, help="comma-separated, e.g. FND-0264,FND-0275")
    ap.add_argument("--log", required=True)
    ap.add_argument("--actor", default=None, help="canonical identity (agent:<model> | human:<login> | system:<name>) used once Phase A is applied; "
                                                  "default FONDOS_ACTOR; never guessed from the OS user")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    tickets = [t.strip().upper() for t in args.tickets.split(",") if t.strip()]
    problems = validate_args(args.release, tickets, args.log)
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return RC_USAGE


    import shared.config as cfg  # (loads the repo .env)
    from shared import backlog_client as bc
    from shared.db import get_connection
    from shared.statistical_audit import incidents as inc
    from shared.statistical_audit import persistence as ps
    import psycopg

    dsn = os.environ.get("FONDOS_BACKLOG_PG_DSN")
    if not dsn:
        print("error: FONDOS_BACKLOG_PG_DSN is not set", file=sys.stderr)
        return RC_USAGE
    try:             # a human actor writes through the owner-only connection (FND-0337); everyone else through the shared one
        dsn = bc.dsn_for_actor(bc.resolve_actor(args.actor)) or dsn
    except bc.ActorUnknown:
        pass         # no actor given: reported below only if the Phase A routines need one
    except bc.HumanDsnMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RC_USAGE

    rc_live = bc.require_live_write_cli(dsn)          # a dry run writes and rolls back (it burns ids): the same gate applies; exit code 1, nothing touched
    if rc_live:
        return rc_live

    fconn = get_connection()
    try:
        present = ps.incident_table_exists(fconn)
        incidents = inc.load_incidents(fconn) if present else []
        fconn.rollback()
    finally:
        fconn.close()
    refusal = incident_gate(tickets, incidents, present, cfg.AUDIT_INCIDENT_LOOP_ENABLED)
    if refusal:
        print("REFUSED by the incident gate:")
        for r in refusal:
            print(f"  - {r}")
        return RC_REFUSED

    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        if not bc.transitions_store_present(conn=conn):          # the routines are the only door: without step A-1 there is nothing to write through
            print("error: Backlog v2 step A-1 (backlog.sp_transition) is not applied; this script no longer has a raw-SQL form", file=sys.stderr)
            conn.rollback()
            return RC_USAGE
        try:
            actor = bc.resolve_actor(args.actor)                 # explicit identity; FONDOS_ACTOR as the validated alternative; never guessed
        except bc.ActorUnknown as exc:
            print(f"error: {exc}; pass --actor agent:<model> or human:<login>", file=sys.stderr)
            conn.rollback()
            return RC_USAGE
        rows = {r[0]: (r[1], r[2]) for r in cur.execute(
            "SELECT action_point_id, status, release_version FROM backlog.backlog WHERE action_point_id = ANY(%s)", (tickets,)).fetchall()}
        why = check_tickets(rows, tickets)
        used = cur.execute("SELECT count(*) FROM backlog.backlog WHERE release_version = %s", (args.release,)).fetchone()[0]
        if used:
            why.append(f"release label {args.release} is already used by {used} ticket(s)")
        if why:
            print("REFUSED:")
            for r in why:
                print(f"  - {r}")
            conn.rollback()
            return RC_REFUSED
        note = f"Release {args.release}: {args.log.strip()} Closed through backlog.sp_close_release."
        # Backlog v2 Phase A: the move to READY_FOR_DEPLOY (with the release label) goes through sp_transition, with an explicit canonical actor.
        try:
            for t_ in tickets:
                bc.transition(t_, "READY_FOR_DEPLOY", session_id=bc.make_session_id("RELEASE", "SYNC"), comment=note, actor=actor,
                              release_version=args.release, conn=conn)
        except psycopg.Error as exc:
            conn.rollback()
            print(f"REFUSED by the backlog routine: {exc}")
            return RC_REFUSED
        try:
            cur.execute("CALL backlog.sp_close_release(%s, %s)", (args.release, actor))
        except psycopg.Error as exc:
            conn.rollback()
            print(f"REFUSED by the backlog routine: {exc}")
            return RC_REFUSED
        closed = cur.execute("SELECT action_point_id, status, solution_type FROM backlog.backlog WHERE release_version = %s ORDER BY 1",
                             (args.release,)).fetchall()
        print("closed by the release:", [tuple(c) for c in closed])
        if sorted(c[0] for c in closed) != sorted(tickets) or any(c[1] != "CLOSED" for c in closed):
            print("REFUSED: the release did not close exactly the requested tickets; rolled back")
            conn.rollback()
            return RC_REFUSED
        if not args.apply:
            conn.rollback()
            print("DRY RUN (rolled back). Add --apply.")
            return RC_OK
        conn.commit()
        print("APPLIED")

    to_close = [r for r in incidents if r.get("ap_id") in tickets and r["state"] in (inc.STATE_CERTIFIED, inc.STATE_ACCEPTED)]
    if to_close:
        fconn = get_connection()
        try:
            for r in to_close:
                try:
                    ps.close_incident(fconn, r["incident_id"])
                    print(f"  incident #{r['incident_id']} CLOSED")
                except Exception as exc:                         # noqa: BLE001 -- the ticket is closed; say the incident was not
                    print(f"  WARNING: incident #{r['incident_id']} not marked CLOSED ({type(exc).__name__}): "
                          "scripts/audit/audit_incident.py --close", file=sys.stderr)
        finally:
            fconn.close()
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
