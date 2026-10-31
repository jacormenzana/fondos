#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_autopilot.py -- moves tickets on the evidence the system recorded (Backlog v3 W3, FND-0358; doc/especificacion/ACTION_PLAN_BACKLOG_V3.md, NORMAS_BACKLOG.md 2).

    python scripts/audit/backlog_autopilot.py                  # DRY RUN: what it would do; reads only, writes nothing
    python scripts/audit/backlog_autopilot.py --apply          # records the MERGE facts and acts according to BACKLOG_AUTOPILOT_MODE (shared/config.py)

Facts. A merge commit on master (default origin/master) whose branch carries a commit with `Closes: FND-nnnn` delivers that ticket: one backlog.ops_event row of kind MERGE per (merge,
ticket), with the pull request number, the files the merge changed and the `Solution:` trailer (default CODE_FIX). Recording is idempotent, so the whole recent history is read every time.

Decisions. shared/backlog_autopilot.py (pure, tested edge by edge) decides from those facts and from the gate verdicts read here: the delivery gate of Phase B, the closing conditions
(scripts/audit/rfd_matrix_probe.py), the audit-incident gate of FND-0264 (shared/backlog_release.py, a read of `fondos`; if it cannot be read the closure WAITS, never passes) and the
DEPLOY / CYCLE facts. Anything short of a clear closure is a WAIT with its reasons.

Modes (the ceiling is BACKLOG_AUTOPILOT_MODE; --mode can only lower it):
  OFF     prints only.
  SHADOW  records the MERGE facts and writes ONE note per decision on the ticket ("would move X to Y, evidence ..."); moves nothing. The owner reviews the first 20 (O-V3-4).
  ON      also makes the two moves through backlog_client.auto_transition (sp_auto_transition: actor system:autopilot, never forced, evidence re-checked in the database); a move the gates
          refuse becomes a WAIT note, once.

It writes ONLY through its own database login (FONDOS_BACKLOG_PG_DSN_AUTOPILOT, role backlog_autopilot). Without it, or before the owner applied the W3 DDL (`migrate_backlog_v2.py --phase
autonomy`), --apply refuses with exit code 2 and writes nothing: it never falls back to another login and never asks for a terminal. DEPLOY and CYCLE facts are not made here (the autopilot
cannot vouch for its own closure): a ticket whose merge changes db/** waits until the owner's migration records the DEPLOY, a DATA_FIX until a CYCLE is recorded.
Exit codes: 0 ok (or dry run), 1 a write failed, 2 usage, connection or configuration error.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import backlog_autopilot as ap_mod  # noqa: E402
from shared import backlog_gate as gate  # noqa: E402
from shared import backlog_release as rel  # noqa: E402

ACTOR = "system:autopilot"                  # == backlog_client.AUTOPILOT_ACTOR (a test pins it)
RC_OK, RC_FAILED, RC_USAGE = 0, 1, 2
_MODE_RANK = {"OFF": 0, "SHADOW": 1, "ON": 2}
_MERGE_FMT = "%H%x1f%P%x1f%aI%x1f%s%x1e"


# ── pure ────────────────────────────────────────────────────────────────────────

def effective_mode(ceiling: str, requested: str | None = None) -> str:
    """The mode of a run: the configured ceiling (an unknown value is OFF), lowered by --mode when given; --mode can never raise it. Pure."""
    top = ceiling if ceiling in _MODE_RANK else "OFF"
    if requested is None:
        return top
    if requested not in _MODE_RANK:
        raise ValueError(f"mode must be one of {tuple(_MODE_RANK)}, got {requested!r}")
    return requested if _MODE_RANK[requested] < _MODE_RANK[top] else top


def parse_merges(text: str) -> list:
    """The merge commits of `git log --first-parent --merges --format=_MERGE_FMT`: [{'sha', 'parents', 'author_ts', 'subject'}] (a commit with fewer than two parents is skipped). Pure."""
    out = []
    for rec in text.split("\x1e"):
        rec = rec.strip("\r\n")
        if not rec:
            continue
        sha, parents, ts, subject = (rec.split("\x1f") + [""] * 4)[:4]
        ps = parents.split()
        if len(ps) >= 2:
            out.append({"sha": sha.strip(), "parents": ps, "author_ts": datetime.fromisoformat(ts.strip()), "subject": subject})
    return out


def deliveries(messages: list) -> dict:
    """{ticket: solution} for the tickets that the commit messages of ONE merged branch deliver (`Closes:`). The solution is the `Solution:` trailer of the delivering commit(s): None when
    none says one (the default, CODE_FIX); two different values join as `A/B`, which the rule engine refuses to close automatically. Pure."""
    seen: dict = {}
    for text in messages:
        m = gate.parse_message(text)
        for t in m.closes:
            seen.setdefault(t, set()).add(m.solution)
    out = {}
    for t, sols in seen.items():
        real = {s for s in sols if s}
        out[t] = "/".join(sorted(real)) if real else None
    return out


def merge_object(merge: dict, solution: str | None, paths: list) -> "ap_mod.Merge":
    """The rule engine's Merge for one merge commit and one ticket it delivers. The release month is the merge's, in UTC. Pure."""
    when = merge["author_ts"].astimezone(timezone.utc)
    return ap_mod.Merge(ref=merge["sha"], pr=ap_mod.pr_number(merge["subject"]), paths=tuple(sorted(paths)), year=when.year, month=when.month, solution=solution)


def blocker_text(b) -> str:
    """One readable line for a gate blocker (a dict from backlog_client.gate_blockers, or a string). Pure."""
    if isinstance(b, dict):
        return ", ".join(f"{k}={v}" for k, v in b.items() if v not in (None, ""))
    return str(b)


def act(decisions: list, mode: str, bc, notes: dict, session: str, out=print) -> dict:
    """Carry out `decisions` in `mode` through `bc` (the client). SHADOW and WAIT write ONE note per decision (the key of a decision already noted on the ticket is skipped); ON also makes the
    MOVE and CLOSE moves; a move the database refuses (a gate, a missing fact) is noted once as a WAIT with the refusal and is not an error. A problem of the login or the connection raises.
    `notes` = {ticket: [existing log texts]}, extended as notes are written. Returns {'moved','closed','noted','skipped','refused'}. Not pure: it calls `bc`."""
    res = {"moved": 0, "closed": 0, "noted": 0, "skipped": 0, "refused": 0}
    for d in decisions:
        if mode == "ON" and d.action in (ap_mod.MOVE, ap_mod.CLOSE):
            try:
                bc.auto_transition(d.ap, d.to_status, evidence_ref=d.evidence_ref, session_id=session, comment=ap_mod.shadow_note(d, "ON"),
                                   solution_type=d.solution_type, release_version=d.release if d.action == ap_mod.CLOSE else None)
                res["moved" if d.action == ap_mod.MOVE else "closed"] += 1
                out(f"  {d.ap}: {d.from_status} -> {d.to_status} DONE ({d.evidence_kind} {d.evidence_ref[:12]})")
                continue
            except bc.BacklogUnavailable:
                raise                                                         # no login / no connection / the live-write guard: a configuration problem, never a refusal of a gate
            except Exception as exc:                                         # noqa: BLE001 -- the database refused the move (a gate, a missing fact): it becomes a WAIT
                why = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
                d = ap_mod.Decision(ap=d.ap, action=ap_mod.WAIT, from_status=d.from_status, to_status=d.to_status, evidence_kind=d.evidence_kind, evidence_ref=d.evidence_ref,
                                    solution_type=d.solution_type, release=d.release, reasons=(f"refused by the database: {why}",))
                res["refused"] += 1
        if ap_mod.already_noted(d, notes.get(d.ap, [])):
            res["skipped"] += 1
            continue
        text = ap_mod.shadow_note(d, mode)
        bc.append_log(d.ap, bc.AUTOPILOT_ACTOR, text)
        notes.setdefault(d.ap, []).append(text)
        res["noted"] += 1
        out(f"  {d.ap}: {d.action} noted ({'; '.join(d.reasons) if d.reasons else 'would ' + d.action.lower()})")
    return res


# ── I/O ────────────────────────────────────────────────────────────────────────

def _git(*args, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def collect_merges(rev: str, since_days: int, *, cwd: Path | None = None) -> dict:
    """{ticket: [(merge dict, Merge)]} oldest first, for the merge commits of `rev` newer than `since_days` whose branch carries `Closes:`. Reads git only."""
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%d")
    merges = parse_merges(_git("log", "--first-parent", "--merges", f"--since={since}", rev, f"--format={_MERGE_FMT}", cwd=cwd))
    out: dict = {}
    for m in sorted(merges, key=lambda x: x["author_ts"]):
        base, tip = m["parents"][0], m["parents"][1]
        messages = [t for t in _git("log", f"{base}..{tip}", "--format=%B%x1e", cwd=cwd).split("\x1e") if t.strip()]
        delivered = deliveries(messages)
        if not delivered:
            continue
        paths = [p for p in _git("diff", "--name-only", base, m["sha"], cwd=cwd).splitlines() if p]
        for t, solution in delivered.items():
            out.setdefault(t, []).append((m, merge_object(m, solution, paths)))
    return out


def _gather_gates(candidates: dict, tickets: dict, bc, cfg) -> tuple:
    """(probes, incident, rfd_blockers, delivery_blockers): the gate verdicts for the candidate tickets. A verdict that cannot be read becomes a REASON (the move waits), never a pass."""
    probes, incident, rfd_b, del_b = {}, {}, {}, {}
    in_progress = [a for a in candidates if tickets[a]["status"] == "IN_PROGRESS"]
    ready = [a for a in candidates if tickets[a]["status"] == "READY_FOR_DEPLOY"]
    for a in in_progress:
        try:
            rfd_b[a] = [blocker_text(b) for b in bc.gate_blockers(a, "READY_FOR_DEPLOY", False)]
        except Exception as exc:                                             # noqa: BLE001
            rfd_b[a] = [f"the delivery gate could not be read ({type(exc).__name__})"]
    if ready:
        for a in ready:
            try:
                del_b[a] = [blocker_text(b) for b in bc.gate_blockers(a, "CLOSED", True)]
            except Exception as exc:                                         # noqa: BLE001
                del_b[a] = [f"the delivery gate could not be read ({type(exc).__name__})"]
        try:
            present, incidents = rel.read_incident_facts()
            incident = rel.incident_reasons(ready, incidents, present, bool(cfg.AUDIT_INCIDENT_LOOP_ENABLED))
        except Exception as exc:                                             # noqa: BLE001 -- cannot verify = cannot close
            incident = {a: [f"the audit-incident store of `fondos` could not be read ({type(exc).__name__}); a closure waits"] for a in ready}
        stored = bc.close_conditions() or {}
        wanted = {a: stored[a] for a in ready if stored.get(a)}
        if wanted:
            try:
                import rfd_matrix_probe as probe
                for tid, _verdict, results in probe.evaluate(wanted):
                    probes[tid] = [(ok is True, d) for ok, d in results]       # a manual condition (None) is not a pass: a person closes that ticket
            except Exception as exc:                                         # noqa: BLE001
                probes = {a: [(False, f"the closing conditions could not be evaluated ({type(exc).__name__})")] for a in wanted}
    return probes, incident, rfd_b, del_b


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="record the MERGE facts and act according to the mode (default: dry run)")
    ap.add_argument("--rev", default="origin/master", help="the history whose merges are read (default origin/master)")
    ap.add_argument("--since-days", type=int, default=45, help="how far back the merges are read (default 45)")
    ap.add_argument("--mode", choices=sorted(_MODE_RANK), help="lower the configured BACKLOG_AUTOPILOT_MODE for this run (never raises it)")
    ap.add_argument("--ticket", action="append", help="only this ticket (repeatable); for a first, careful run")
    ap.add_argument("--quiet", action="store_true", help="print only a one-line summary (the git hook)")
    args = ap.parse_args(argv)
    os.environ["FONDOS_ACTOR"] = ACTOR        # one process, one identity: this process IS the autopilot, whoever started it
    import shared.config as cfg
    from shared import backlog_client as bc
    out = (lambda *a, **k: None) if args.quiet else print
    mode = effective_mode(cfg.BACKLOG_AUTOPILOT_MODE, args.mode)
    try:
        by_ticket = collect_merges(args.rev, args.since_days)
        if args.ticket:
            by_ticket = {t: v for t, v in by_ticket.items() if t in set(args.ticket)}
        tickets = {t["action_point_id"]: {"status": t["status"], "release_version": t["release_version"]} for t in bc.list_tickets(ids=sorted(by_ticket))} if by_ticket else {}
    except (RuntimeError, bc.BacklogUnavailable) as exc:
        print(f"error: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return RC_USAGE
    unknown = sorted(set(by_ticket) - set(tickets))
    by_ticket = {t: v for t, v in by_ticket.items() if t in tickets}
    candidates = {t: v for t, v in by_ticket.items() if tickets[t]["status"] in ("IN_PROGRESS", "READY_FOR_DEPLOY")}
    out(f"{args.rev}: {sum(len(v) for v in by_ticket.values())} delivery fact(s) for {len(by_ticket)} ticket(s) in the last {args.since_days} days; mode {mode}"
        + (f"; not in the backlog: {', '.join(unknown)}" if unknown else ""))

    try:
        deploys = {r for _ref, r in bc.ops_event_keys("DEPLOY")} if bc.autonomy_store_present() else set()
        cycles = {r for _ref, r in bc.ops_event_keys("CYCLE")} if bc.autonomy_store_present() else set()
        probes, incident, rfd_b, del_b = _gather_gates(candidates, tickets, bc, cfg)
    except Exception as exc:                                                 # noqa: BLE001
        print(f"error: could not read the facts: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return RC_USAGE
    facts = ap_mod.Facts(tickets=tickets, merges={t: [mo for _m, mo in v] for t, v in candidates.items()}, deploys=deploys, cycles=cycles, probes=probes, incident=incident,
                         rfd_blockers=rfd_b, delivery_blockers=del_b)
    decisions = ap_mod.decide(facts)
    for d in decisions:
        out(f"  {d.ap}: {d.action} {d.from_status} -> {d.to_status} ({d.evidence_kind} {d.evidence_ref[:12]})" + (f"  WAITS: {'; '.join(d.reasons)}" if d.reasons else ""))
    if not decisions:
        out("  nothing to decide")
    if not args.apply or mode == "OFF":
        out("DRY RUN: nothing written." if not args.apply else "mode OFF: nothing written.")
        if args.quiet:
            print(f"autopilot: {len(decisions)} decision(s), mode {mode}, nothing written")
        return RC_OK

    if bc.autopilot_operation(ACTOR) is None:
        print(f"REFUSED: --apply writes only through the login backlog_autopilot, and {bc._AUTOPILOT_DSN_ENV_VAR} is not set (owner action O-V3-3). Nothing was written.", file=sys.stderr)
        return RC_USAGE
    try:
        if not bc.autonomy_store_present():
            print("REFUSED: the W3 DDL is not applied (owner: python scripts/ops/migrate_backlog_v2.py --phase autonomy). Nothing was written.", file=sys.stderr)
            return RC_USAGE
        new = 0
        for t, rows in by_ticket.items():
            for m, mo in rows:
                payload = {"pr": mo.pr, "paths": list(mo.paths), "solution": mo.solution, "subject": m["subject"][:200]}
                new += bool(bc.record_ops_event("MERGE", m["sha"], t, "REFS", payload, m["author_ts"]))
        out(f"MERGE facts recorded: {new} new")
        notes: dict = {}
        for d in decisions:
            if d.ap not in notes:
                notes[d.ap] = [r["comment"] for r in bc.list_logs(ids=[d.ap])]
        res = act(decisions, mode, bc, notes, bc.make_session_id("AUTOPILOT", "SYNC"), out=out)
    except bc.BacklogUnavailable as exc:                                     # includes LiveWriteForbidden: no login, no connection
        print(f"REFUSED: {type(exc).__name__}: {str(exc)[:240]}. Nothing more was written.", file=sys.stderr)
        return RC_USAGE
    except Exception as exc:                                                 # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return RC_FAILED
    stale = ap_mod.stale_in_progress({t["action_point_id"]: t["update_ts"].date() for t in bc.list_tickets(open_only=True) if t["status"] == "IN_PROGRESS" and t.get("update_ts")}, date.today())
    summary = (f"autopilot {mode}: moved {res['moved']}, closed {res['closed']}, noted {res['noted']}, already noted {res['skipped']}, refused by the database {res['refused']}; "
               f"{len(stale)} IN_PROGRESS ticket(s) idle for 14 days or more")
    print(summary)
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
