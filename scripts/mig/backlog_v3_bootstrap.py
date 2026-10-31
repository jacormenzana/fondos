#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_v3_bootstrap.py -- opens the Backlog v3 epic and its workstreams as tickets, and links them (O-V3-1 of doc/especificacion/ACTION_PLAN_BACKLOG_V3.md).
The tickets and links are the fixed, versioned list in scripts/mig/backlog_v3_manifest.yaml.

    python scripts/mig/backlog_v3_bootstrap.py                  # DRY RUN: what would be opened and linked; reads only
    python scripts/mig/backlog_v3_bootstrap.py --apply          # the OWNER, in his own terminal: opens what is missing and records the links

Why a script and why the owner: an agent cannot open tickets on the live backlog until the scoped agent login of W2 exists (the 2026-10-29 live-write order), so a person at a real
console runs this once with FONDOS_ACTOR=human:<login> and FONDOS_BACKLOG_PG_DSN_HUMAN set. The live-write gate asks him to type the confirmation phrase before anything is written.

Through the single door only (backlog_client.open_ticket / record_link_event): ids come from the sequence, every ticket gets its first log and status event, and every text that names an
existing FND-nnnn id proposes that link by itself (PROPOSED RELATED, origin SOURCE). Idempotent: a ticket whose exact title already exists is reported DONE and reused (its id resolves the
links), a link already in place is DONE, a link a person REJECTED is never overridden. Each ticket is its own transaction, so a refusal stops nothing that was already written.
The link statuses of the manifest are what the person who runs this confirms; for a non-human actor they are recorded as PROPOSED.
Exit codes: 0 ok (or dry run), 1 a row failed or the live write was refused, 2 usage, manifest or connection error.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().with_name("backlog_v3_manifest.yaml")
RC_OK, RC_FAILED, RC_USAGE = 0, 1, 2

NATURES = ("BUG", "REQ", "CHG", "OPT", "INV", "DOC", "TSK")                                  # == NORMAS_BACKLOG section 2 (CHECK-enforced in the database)
OBJECT_TYPES = ("TAB", "VW", "SP", "UDF", "TVF", "JOB", "SCHEMA", "N/A")
PRIORITIES = ("HIGH", "MEDIUM", "LOW")
_FND = re.compile(r"^FND-\d{4}$")
_TICKET_FIELDS = ("key", "title", "nature", "object_type", "priority", "description", "first_log")


def load_manifest(path: Path = MANIFEST) -> dict:
    """The manifest, validated: unique keys and titles, the domain values of the norm, links between known keys (or existing FND ids) of an allowed type. Pure (reads the file)."""
    import yaml
    from shared import backlog_client as bc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    tickets, links = data["tickets"], data["links"]
    keys = [t["key"] for t in tickets]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate ticket keys in the manifest")
    titles = [t["title"] for t in tickets]
    if len(titles) != len(set(titles)):
        raise ValueError("duplicate ticket titles in the manifest (the title is what makes the script idempotent)")
    for t in tickets:
        missing = [f for f in _TICKET_FIELDS if not str(t.get(f) or "").strip()]
        if missing:
            raise ValueError(f"{t.get('key')}: missing {missing}")
        for field, allowed in (("nature", NATURES), ("object_type", OBJECT_TYPES), ("priority", PRIORITIES)):
            if t[field] not in allowed:
                raise ValueError(f"{t['key']}: {field} {t[field]!r} is not one of {allowed}")
    for ln in links:
        for end in (ln["from"], ln["to"]):
            if end not in keys and not _FND.match(str(end)):
                raise ValueError(f"link {ln['from']} -> {ln['to']}: {end!r} is neither a key of the manifest nor an FND-nnnn id")
        if ln["from"] == ln["to"]:
            raise ValueError(f"link {ln['from']} -> {ln['to']}: a ticket cannot be linked to itself")
        if ln["type"] not in bc.LINK_TYPES or ln["status"] not in bc.LINK_STATUSES:
            raise ValueError(f"link {ln['from']} -> {ln['to']}: type {ln['type']!r} or status {ln['status']!r} is not allowed")
        if not str(ln.get("evidence") or "").strip():
            raise ValueError(f"link {ln['from']} -> {ln['to']}: evidence is required")
    return data


def effective_status(requested: str, actor: str) -> str:
    """CONFIRMED is a decision of a person: any other actor records PROPOSED. Pure."""
    return requested if (requested != "CONFIRMED" or str(actor).startswith("human:")) else "PROPOSED"


def build_plan(data: dict, existing_by_title: dict, current_links: dict, actor: str) -> dict:
    """What is still to do. `existing_by_title` = {title: ticket id}; `current_links` = {(from_id, to_id, type): status}. Returns
    {'tickets': [(key, 'DONE'|'TODO', id|None)], 'links': [(row, 'DONE'|'TODO'|'SKIP: <why>', wanted_status)]}. Pure."""
    ids = {}
    tickets = []
    for t in data["tickets"]:
        found = existing_by_title.get(t["title"])
        ids[t["key"]] = found
        tickets.append((t["key"], "DONE" if found else "TODO", found))
    links = []
    for ln in data["links"]:
        a, b = ids.get(ln["from"], ln["from"]), ids.get(ln["to"], ln["to"])
        wanted = effective_status(ln["status"], actor)
        if a is None or b is None or not (_FND.match(str(a)) and _FND.match(str(b))):
            links.append((ln, "TODO", wanted))                                             # an endpoint is not opened yet: the link follows its ticket
            continue
        cur = current_links.get((a, b, ln["type"]))
        if cur is None:
            state = "TODO"
        elif cur == "REJECTED":
            state = "SKIP: a person REJECTED this link"
        elif cur == wanted or cur == "CONFIRMED":
            state = "DONE"
        else:
            state = "TODO" if wanted == "CONFIRMED" else "DONE"
        links.append((ln, state, wanted))
    return {"tickets": tickets, "links": links}


def read_facts(data: dict, bc) -> tuple:
    """(existing_by_title, current_links), read through backlog_client (read-only)."""
    wanted = {t["title"] for t in data["tickets"]}
    by_title = {t["title"]: t["action_point_id"] for t in bc.list_tickets() if t["title"] in wanted}
    ids = sorted(set(by_title.values()))
    links = {(r["from_id"], r["to_id"], r["link_type"]): r["status"] for r in bc.list_links(ids=ids)} if ids else {}
    return by_title, links


def apply_plan(data: dict, plan: dict, bc, *, actor: str) -> dict:
    """Open the missing tickets (one transaction each), then record the missing links. Returns {'opened': {key: id}, 'links': n, 'failed': [..]}. A failed ticket skips its links."""
    session = bc.make_session_id(data["session_topic"], "DEV")
    ids = {key: tid for key, _state, tid in plan["tickets"] if tid}
    spec = {t["key"]: t for t in data["tickets"]}
    out = {"opened": {}, "links": 0, "failed": []}
    for key, state, _tid in plan["tickets"]:
        if state != "TODO":
            continue
        t = spec[key]
        try:
            new = bc.open_ticket(title=t["title"], nature=t["nature"], object_type=t["object_type"], priority=t["priority"], session_id=session,
                                 description=" ".join(t["description"].split()), first_log=" ".join(t["first_log"].split()), actor=actor,
                                 object_name=t.get("object_name"), schema_name=t.get("schema_name"))
            ids[key] = str(new)
            out["opened"][key] = str(new)
            print(f"  {key:5} OPENED  {new}" + (f"  (proposed RELATED to {', '.join(new.linked)})" if getattr(new, "linked", ()) else ""))
        except Exception as exc:                                # one refused ticket must not hide the others
            out["failed"].append(key)
            print(f"  {key:5} FAILED  {type(exc).__name__}: {str(exc).splitlines()[0][:200]}", file=sys.stderr)
    for ln, state, wanted in plan["links"]:
        if state != "TODO":
            continue
        a, b = ids.get(ln["from"], ln["from"]), ids.get(ln["to"], ln["to"])
        if not (_FND.match(str(a)) and _FND.match(str(b))):
            print(f"  link {ln['from']} -> {ln['to']} SKIPPED (an endpoint was not opened)", file=sys.stderr)
            continue
        try:
            ev = bc.record_link_event(from_id=a, to_id=b, link_type=ln["type"], status=wanted, evidence=ln["evidence"], username=actor)
            out["links"] += 1
            print(f"  link  {a} -{ln['type']}-> {b}  {wanted}  (event {ev})")
        except Exception as exc:
            out["failed"].append(f"{ln['from']}->{ln['to']}")
            print(f"  link {ln['from']} -> {ln['to']} FAILED  {type(exc).__name__}: {str(exc).splitlines()[0][:200]}", file=sys.stderr)
    return out


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="open the missing tickets and record the missing links (default: dry run)")
    ap.add_argument("--actor", help="canonical actor (human:<login> | agent:<model>); default from FONDOS_ACTOR")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import shared.config  # noqa: F401  (loads .env: FONDOS_BACKLOG_PG_DSN)
    from shared import backlog_client as bc
    if args.apply:                                              # the dry run only reads; applying writes, which only a person at a terminal may do on the live backlog
        rc_live = bc.require_live_write_cli()
        if rc_live:
            return rc_live
    try:
        data = load_manifest()
        actor = bc.resolve_actor(args.actor)
        if args.apply and not bc.transitions_store_present():
            print("step A-1 (db/pg_backlog/30_transitions.sql) is not applied: tickets cannot be opened. Nothing to do.", file=sys.stderr)
            return RC_USAGE
        by_title, links = read_facts(data, bc)
    except (bc.BacklogUnavailable, bc.ActorUnknown, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RC_USAGE
    plan = build_plan(data, by_title, links, actor)
    print(f"Backlog v3 bootstrap manifest v{data['version']}: {len(plan['tickets'])} tickets, {len(plan['links'])} links; actor {actor}")
    for key, state, tid in plan["tickets"]:
        print(f"  ticket {key:5} {state:5} {tid or ''}")
    for ln, state, wanted in plan["links"]:
        print(f"  link   {ln['from']:5} -{ln['type']}-> {ln['to']:5} {wanted:9} {state}")
    todo_t = sum(1 for _, s, _ in plan["tickets"] if s == "TODO")
    todo_l = sum(1 for _, s, _ in plan["links"] if s == "TODO")
    print(f"{todo_t} ticket(s) and {todo_l} link(s) to do.")
    if not args.apply:
        print("DRY RUN: nothing changed. Add --apply (in your own terminal, as a human actor) to open and link.")
        return RC_OK
    res = apply_plan(data, plan, bc, actor=actor)
    print(f"OPENED {len(res['opened'])} ticket(s), recorded {res['links']} link(s)" + (f"; FAILED: {', '.join(res['failed'])}" if res["failed"] else ""))
    return RC_FAILED if res["failed"] else RC_OK


if __name__ == "__main__":
    sys.exit(main())
