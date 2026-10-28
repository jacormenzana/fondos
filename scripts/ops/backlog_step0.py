#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_step0.py -- step 0 of Backlog v2 (FND-0333): the fixed, versioned list of historical corrections in scripts/ops/backlog_step0_manifest.yaml.

    python scripts/ops/backlog_step0.py                                    # DRY RUN: lists every row of the manifest and whether it is still to do; changes nothing
    python scripts/ops/backlog_step0.py --apply --actor agent:<model>      # applies the rows still to do, through backlog_client only

Append-only and idempotent: a row already in place is reported DONE and skipped, so running it twice changes nothing the second time. Link rows are link events
(origin STEP0), blocker / revisit rows are structured wait annotations (backlog.sp_annotate_wait), FND-0041 is one transition (a counted override with its reason),
FND-0223 gets one marked log. Nothing rewrites a log or a ticket and nothing reverses a link or a closure. Needs Phase B applied (40_relations_v2.sql): the dry run
says so and stops when it is not. Applied by the OWNER on his express order after Phase B is deployed; `--deployed-on` sets the day the DEFAULT_90D dates count from.
Exit codes: 0 ok (or dry run), 1 a row failed, 2 usage or connection error.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().with_name("backlog_step0_manifest.yaml")
RC_OK, RC_FAILED, RC_USAGE = 0, 1, 2
ACTIONS = ("link", "annotate_blocker", "annotate_revisit", "transition", "record_exception")
SESSION_TOPIC = "STEP0"


def load_manifest(path: Path = MANIFEST) -> dict:
    """The manifest, validated: unique ids, known actions, the fields each action needs. Pure (reads the file)."""
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = data["rows"]
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate row ids in the step-0 manifest")
    need = {"link": ("from", "to", "type", "status"), "annotate_blocker": (), "annotate_revisit": (), "transition": ("from_status", "to_status"), "record_exception": ("marker",)}
    for r in rows:
        if r["action"] not in ACTIONS:
            raise ValueError(f"{r['id']}: unknown action {r['action']!r}")
        missing = [k for k in ("ticket", "basis") + need[r["action"]] if not r.get(k)]
        if missing:
            raise ValueError(f"{r['id']}: missing {missing}")
        if r["action"] == "annotate_blocker" and not (r.get("blocker") or r.get("condition")):
            raise ValueError(f"{r['id']}: annotate_blocker needs a blocker or a condition")
    return data


def state_of(row: dict, ctx: dict) -> str:
    """'DONE' when the row is already in place, 'TODO' when it still has to be applied, 'SKIP: <why>' when it cannot apply. `ctx` holds the current facts. Pure."""
    t, act = row["ticket"], row["action"]
    ticket = ctx["tickets"].get(t)
    if ticket is None:
        return f"SKIP: {t} does not exist"
    if act == "link":
        cur = ctx["links"].get((row["from"], row["to"], row["type"]))
        return "DONE" if cur == row["status"] else "TODO"
    if act == "annotate_blocker":
        waits = [w for w in ctx["waits"].get(t, []) if w["kind"] == "BLOCKED"]
        if any((row.get("blocker") and w["blocker_ref"] == row["blocker"]) or (row.get("condition") and w["condition_text"] == row["condition"]) for w in waits):
            return "DONE"
        return "TODO" if ticket["status"] == "BLOCKED" else f"SKIP: {t} is {ticket['status']}, not BLOCKED"
    if act == "annotate_revisit":
        if any(w["kind"] == "DEFERRED" and w["revisit_basis"] == "DEFAULT_90D" for w in ctx["waits"].get(t, [])):
            return "DONE"
        return "TODO" if ticket["status"] == "DEFERRED" else f"SKIP: {t} is {ticket['status']}, not DEFERRED"
    if act == "transition":
        if ticket["status"] == row["to_status"]:
            return "DONE"
        return "TODO" if ticket["status"] == row["from_status"] else f"SKIP: {t} is {ticket['status']}, not {row['from_status']}"
    if act == "record_exception":
        return "DONE" if any(row["marker"] in (log["comment"] or "") for log in ctx["logs"].get(t, [])) else "TODO"
    return "SKIP: unknown action"


def build_context(rows: list, bc, conn=None) -> dict:
    """The current facts the rows need, read through backlog_client (read-only)."""
    ids = sorted({r["ticket"] for r in rows} | {r.get(k) for r in rows for k in ("from", "to", "blocker") if r.get(k)})
    tickets = {t["action_point_id"]: t for t in bc.list_tickets(ids=ids, conn=conn)}
    links = {(r["from_id"], r["to_id"], r["link_type"]): r["status"] for r in bc.list_links(ids=ids, conn=conn)}
    waits: dict = {}
    for w in bc.list_wait_events(ids, conn=conn):
        waits.setdefault(w["action_point_id"], []).append(w)
    logs: dict = {}
    for ap in {r["ticket"] for r in rows if r["action"] == "record_exception"}:
        logs[ap] = bc.list_logs(ids=[ap], conn=conn)
    return {"tickets": tickets, "links": links, "waits": waits, "logs": logs}


def apply_row(row: dict, bc, *, actor: str, deployed_on: date, revisit_days: int, conn=None) -> str:
    """Apply ONE row through backlog_client. Returns a short result. Raises on a refusal."""
    t, act = row["ticket"], row["action"]
    session = bc.make_session_id(SESSION_TOPIC, "SYNC")
    note = f"Step 0 of Backlog v2 ({row['id']}): {row['basis']}"
    if act == "link":
        ev = bc.record_link_event(from_id=row["from"], to_id=row["to"], link_type=row["type"], status=row["status"], evidence=note, username=actor, origin="STEP0", conn=conn)
        return f"link event {ev}"
    if act == "annotate_blocker":
        return f"wait event {bc.annotate_wait(t, note, blocker=row.get('blocker'), condition=row.get('condition'), actor=actor, conn=conn)}"
    if act == "annotate_revisit":
        by = deployed_on + timedelta(days=revisit_days)
        return f"wait event {bc.annotate_wait(t, note + f' (revisit_by {by}, a placeholder: DEFAULT_90D)', revisit_by=by, basis='DEFAULT_90D', actor=actor, conn=conn)}"
    if act == "transition":
        comment = f"{note}. Move BLOCKED -> TODO by the owner's step-0 decision (a legal move of the matrix)."
        return f"status event {bc.transition(t, row['to_status'], session_id=session, comment=comment, actor=actor, force=bool(row.get('force')), conn=conn)}"
    if act == "record_exception":
        text = row["marker"] + ": " + note
        return f"log {bc.append_log(t, actor, text, conn=conn)}"
    raise ValueError(act)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="execute the rows still to do (default: dry run)")
    ap.add_argument("--actor", help="canonical actor (agent:<model> | human:<login>); default from FONDOS_ACTOR")
    ap.add_argument("--deployed-on", help="YYYY-MM-DD, the day Phase B was deployed: DEFERRED placeholders are this day + revisit_days (default: today)")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import shared.config  # noqa: F401  (loads .env: FONDOS_BACKLOG_PG_DSN)
    from shared import backlog_client as bc
    try:
        data = load_manifest()
        rows = data["rows"]
        if not bc.relations_store_present():
            print("Phase B (db/pg_backlog/40_relations_v2.sql) is not applied: step 0 needs it. Nothing to do.", file=sys.stderr)
            return RC_USAGE
        actor = bc.resolve_actor(args.actor)
        deployed_on = date.fromisoformat(args.deployed_on) if args.deployed_on else date.today()
        ctx = build_context(rows, bc)
    except (bc.BacklogUnavailable, bc.ActorUnknown, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RC_USAGE
    todo = []
    print(f"step 0 manifest v{data['version']}: {len(rows)} rows; actor {actor}; DEFERRED placeholders = {deployed_on} + {data['revisit_days']} days")
    for row in rows:
        st = state_of(row, ctx)
        print(f"  {row['id']}  {row['ticket']:9}  {row['action']:17}  {st}")
        if st == "TODO":
            todo.append(row)
    print(f"{len(todo)} row(s) to do, {sum(1 for r in rows if state_of(r, ctx) == 'DONE')} already done.")
    if not args.apply:
        print("DRY RUN: nothing changed. Add --apply to apply the rows marked TODO.")
        return RC_OK
    failed = 0
    for row in todo:
        try:
            print(f"  {row['id']}  APPLIED  {apply_row(row, bc, actor=actor, deployed_on=deployed_on, revisit_days=data['revisit_days'])}")
        except Exception as exc:                      # one refused row must not hide the others; each row is its own transaction
            failed += 1
            print(f"  {row['id']}  FAILED   {type(exc).__name__}: {str(exc).splitlines()[0][:200]}", file=sys.stderr)
    print(f"APPLIED {len(todo) - failed} of {len(todo)} row(s)" + (f"; {failed} FAILED" if failed else ""))
    return RC_FAILED if failed else RC_OK


if __name__ == "__main__":
    sys.exit(main())
