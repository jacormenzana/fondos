#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_phase_b_replay.py -- replay of the Phase B gate and review flags over the real history (FND-0333), against the ACTUAL gate code.

    python scripts/audit/backlog_phase_b_replay.py --export SNAPSHOT.json     # read-only: tickets and link events of the live backlog -> a JSON file
    # then the replay itself runs in a throwaway Postgres (never on the live database):
    FONDOS_B_REPLAY_SNAPSHOT=SNAPSHOT.json FONDOS_B_REPLAY_REPORT=REPORT.json python scripts/ops/run_pg_tests.py -- tests/test_backlog_phase_b_replay_pg.py
    python scripts/audit/backlog_phase_b_replay.py --check REPORT.json          # prints the acceptance criteria of the replay; exit 0 only if all pass

The replay loads the snapshot into a scratch database that has the REAL DDL (30_transitions.sql + 40_relations_v2.sql) and, for every historical delivery closure
(CODE_FIX / DATA_FIX / WORKAROUND), puts the database in the state it had at that closure (tickets closed by then are CLOSED, the others open), puts the ticket in
READY_FOR_DEPLOY and calls the real backlog.sp_transition(...'CLOSED'...) inside a savepoint that is always rolled back: a refusal by the delivery gate is a refusal.
The same loop calls the real backlog.fn_raise_review_flags for every closure to measure the flags it would have raised. Acceptance (spec 6.3, and the owner's
requirement of 2026-10-28): the gate (ENFORCE) refuses exactly FND-0223 and no other historical closure; in AUDIT mode it refuses nothing and logs only FND-0223; the flag replay has a median of at most 2 per closure.
Two link states are replayed: the links as they are today (what the spec's Q6 measures) and the links that existed at the moment of each closure (informative).
The only database access here is `export_snapshot` (read-only, default_transaction_read_only=on); the rest takes a connection to a scratch database.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RC_OK, RC_FAIL, RC_USAGE = 0, 1, 2
DELIVERY = ("CODE_FIX", "DATA_FIX", "WORKAROUND")
EXPECTED_REFUSED = ["FND-0223"]                  # the one delivery closure that passed an open prerequisite (spec G2, Q6)
MAX_MEDIAN_FLAGS = 2                             # spec 6.3: the flag replay has a median of at most 2 per closure
SESSION = "FND_20261028_ANL_REPLAY"
REPLAY_ACTOR = "system:replay"
REPLAY_RELEASE = "R2026.10-REPLAY"


# --- snapshot (read-only on the live backlog) --------------------------------------------------------------------------------------------

def export_snapshot(conn) -> dict:
    """The tickets and every link event of the backlog, as plain JSON-able data. Run it on a read-only connection."""
    cur = conn.execute("SELECT * FROM backlog.BACKLOG ORDER BY action_point_id")
    tcols = [d.name for d in cur.description]
    tickets = [dict(zip(tcols, row)) for row in cur.fetchall()]
    cur = conn.execute("SELECT * FROM backlog.backlog_link_event ORDER BY event_id")
    lcols = [d.name for d in cur.description]
    links = [dict(zip(lcols, row)) for row in cur.fetchall()]
    return json.loads(json.dumps({"tickets": tickets, "link_events": links}, default=str))


def load_snapshot(conn, snap: dict) -> None:
    """Insert the snapshot into a scratch database that already has the DDL. Tickets first (foreign keys), then the link events with their ids and timestamps."""
    conn.execute("SELECT set_config('backlog.via_proc', 'on', true)")          # loading is not a direct write (V-00)
    have = {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'backlog' AND table_name = 'backlog'").fetchall()}
    for t in snap["tickets"]:
        cols = [c for c in t if c in have]
        conn.execute(f"INSERT INTO backlog.BACKLOG ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", [t[c] for c in cols])
    lhave = {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'backlog' AND table_name = 'backlog_link_event'").fetchall()}
    for e in snap["link_events"]:
        cols = [c for c in e if c in lhave]
        conn.execute(f"INSERT INTO backlog.backlog_link_event ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", [e[c] for c in cols])
    conn.execute("SELECT setval('backlog.backlog_link_event_event_id_seq', GREATEST((SELECT max(event_id) FROM backlog.backlog_link_event), 1))")
    conn.execute("SELECT set_config('backlog.via_proc', 'off', true)")


# --- the replay (a scratch database with the real DDL) ----------------------------------------------------------------------------------------

def _as_of(conn, closed_at, ticket: str, release: str | None) -> None:
    """The state at `closed_at`: tickets closed by then are CLOSED, the rest open; `ticket` itself READY_FOR_DEPLOY with its release label and no closure."""
    conn.execute("SELECT set_config('backlog.via_proc', 'on', true)")
    conn.execute("UPDATE backlog.BACKLOG SET status = CASE WHEN closure_ts IS NOT NULL AND closure_ts <= %s THEN 'CLOSED' ELSE 'IN_PROGRESS' END", (closed_at,))
    conn.execute("UPDATE backlog.BACKLOG SET status = 'READY_FOR_DEPLOY', release_version = %s, closure_ts = NULL, solution_type = NULL, deployment_ts = NULL "
                 "WHERE action_point_id = %s", (release or REPLAY_RELEASE, ticket))
    conn.execute("SELECT set_config('backlog.via_proc', 'off', true)")


def _try_close(conn, ticket: str, solution: str, other: str, keep: bool = False):
    """Call the REAL sp_transition to close `ticket` inside a savepoint that is rolled back (kept open with keep=True, for the audit rows to be read by the caller, whose own
    transaction is rolled back). Returns ('accepted'|'refused'|'error', detail)."""
    import psycopg
    try:
        with conn.transaction(force_rollback=not keep):
            conn.execute("SELECT backlog.sp_transition(%s, 'CLOSED', %s, %s, 'replay of the historical closure through the real gate code', %s, %s, "
                         "NULL, NULL, NULL, NULL, %s, NULL, false)", (ticket, REPLAY_ACTOR, SESSION, solution, REPLAY_RELEASE, other))
        return "accepted", None
    except psycopg.Error as exc:
        text = str(exc)
        return ("refused" if "delivery gate" in text else "error"), text.splitlines()[0][:300]


def replay(conn, *, links_as_of_closure: bool = False, mode: str = "ENFORCE") -> dict:
    """The gate replay over every delivery closure and the flag replay over every closure, on a scratch database loaded with `load_snapshot`. `mode` is the gate mode in the
    scratch database: ENFORCE (the gate refuses: `refused`) or AUDIT (the deployment default: it never refuses and logs what it WOULD refuse: `audited`)."""
    closed = conn.execute("SELECT action_point_id, solution_type, closure_ts, release_version FROM backlog.BACKLOG WHERE status = 'CLOSED' AND closure_ts IS NOT NULL "
                          "ORDER BY closure_ts, action_point_id").fetchall()
    any_other = conn.execute("SELECT action_point_id FROM backlog.BACKLOG ORDER BY action_point_id LIMIT 1").fetchone()[0]
    gate = {"mode": mode, "delivery_closures": 0, "refused": [], "audited": [], "errors": [], "not_gated_non_delivery": 0}
    flag_counts: list = []
    for ap, sol, ts, rel in closed:
        other = any_other if ap != any_other else conn.execute("SELECT action_point_id FROM backlog.BACKLOG WHERE action_point_id <> %s ORDER BY 1 LIMIT 1", (ap,)).fetchone()[0]
        with conn.transaction(force_rollback=True):                  # the whole as-of state of this closure is scratch
            conn.execute("INSERT INTO backlog.gate_switch (gate, mode, actor, reason) VALUES ('DELIVERY', %s, 'human:replay', 'the replay sets the gate mode of the scratch database')", (mode,))
            if links_as_of_closure:
                conn.execute("DELETE FROM backlog.backlog_link_event WHERE event_ts > %s", (ts,))
            _as_of(conn, ts, ap, rel)
            if sol in DELIVERY:
                gate["delivery_closures"] += 1
                verdict, detail = _try_close(conn, ap, sol, other, keep=(mode == "AUDIT"))
                if mode == "AUDIT":
                    logged = conn.execute("SELECT blockers FROM backlog.v_gate_audit_violation WHERE action_point_id = %s", (ap,)).fetchall()
                    if logged:
                        gate["audited"].append({"ticket": ap, "solution_type": sol, "blockers": logged[0][0]})
                if verdict == "refused":
                    blockers = conn.execute("SELECT backlog.fn_gate_blockers(%s, 'CLOSED', true)", (ap,)).fetchone()[0]
                    gate["refused"].append({"ticket": ap, "solution_type": sol, "blockers": blockers})
                elif verdict == "error":
                    gate["errors"].append({"ticket": ap, "error": detail})
            else:
                gate["not_gated_non_delivery"] += 1                  # REJECTED / DUPLICATED are never gated: sp_transition does not call the gate for them
            n = conn.execute("SELECT backlog.fn_raise_review_flags(%s, 'CLOSED', %s)", (ap, REPLAY_ACTOR)).fetchone()[0]
            flag_counts.append(int(n))
    flags = {"closures": len(flag_counts), "median": statistics.median(flag_counts) if flag_counts else None, "max": max(flag_counts) if flag_counts else None,
             "closures_raising_any": sum(1 for n in flag_counts if n > 0)}
    return {"gate": gate, "flags": flags}


def evaluate(report: dict) -> list:
    """[(criterion, passed, detail)] over a report produced by the replay test. Pure."""
    now, then, audit = report["links_as_of_today"], report["links_as_of_closure"], report["audit_mode"]
    refused_now = sorted(r["ticket"] for r in now["gate"]["refused"])
    refused_then = sorted(r["ticket"] for r in then["gate"]["refused"])
    return [
        ("the gate (the real sp_transition) refuses exactly FND-0223 over today's links", refused_now == EXPECTED_REFUSED, f"refused {refused_now} of {now['gate']['delivery_closures']} delivery closures"),
        ("... and no other historical closure, also with the links that existed at each closure", refused_then == EXPECTED_REFUSED, f"refused {refused_then}"),
        ("in AUDIT mode (the deployment default) the gate refuses nothing and logs exactly FND-0223 as the violation",
         not audit["gate"]["refused"] and sorted(r["ticket"] for r in audit["gate"]["audited"]) == EXPECTED_REFUSED,
         f"refused {len(audit['gate']['refused'])}, audited {sorted(r['ticket'] for r in audit['gate']['audited'])}"),
        ("no replayed closure failed for another reason (every delivery closure was decided by the gate alone)", not now["gate"]["errors"] and not then["gate"]["errors"] and not audit["gate"]["errors"],
         f"errors {len(now['gate']['errors'])} / {len(then['gate']['errors'])} / {len(audit['gate']['errors'])}"),
        ("REJECTED and DUPLICATED closures are never gated", now["gate"]["not_gated_non_delivery"] > 0, f"{now['gate']['not_gated_non_delivery']} non-delivery closures bypass the gate by design"),
        (f"the flag replay has a median of at most {MAX_MEDIAN_FLAGS} per closure", now["flags"]["median"] is not None and now["flags"]["median"] <= MAX_MEDIAN_FLAGS,
         f"median {now['flags']['median']}, max {now['flags']['max']}, {now['flags']['closures_raising_any']} of {now['flags']['closures']} closures raise any"),
    ]


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", metavar="SNAPSHOT.json", help="read-only snapshot of the live backlog")
    g.add_argument("--check", metavar="REPORT.json", help="evaluate a replay report")
    args = ap.parse_args(argv)
    if args.export:
        sys.path.insert(0, str(ROOT))
        import shared.config  # noqa: F401  (loads .env: FONDOS_BACKLOG_PG_DSN)
        from shared import backlog_client as bc
        try:
            with bc._session() as c:
                c.execute("SET TRANSACTION READ ONLY")
                snap = export_snapshot(c)
        except bc.BacklogUnavailable as exc:
            print(f"error: {exc}", file=sys.stderr)
            return RC_USAGE
        Path(args.export).write_text(json.dumps(snap), encoding="utf-8")
        print(f"snapshot written: {args.export} ({len(snap['tickets'])} tickets, {len(snap['link_events'])} link events)")
        return RC_OK
    report = json.loads(Path(args.check).read_text(encoding="utf-8"))
    results = evaluate(report)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'} - {name}: {detail}")
    verdict = all(ok for _, ok, _ in results)
    print(f"RESULT: {'ACCEPTED (all replay criteria met)' if verdict else 'NOT ACCEPTED'}")
    return RC_OK if verdict else RC_FAIL


if __name__ == "__main__":
    sys.exit(main())
