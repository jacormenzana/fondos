#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gate_adjudicate.py -- the owner's tool to read and decide the collision queue (Backlog v2 Phase C slice 3, FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.3; NORMAS_BACKLOG.md 4.6).

    python scripts/audit/gate_adjudicate.py --list                                   # the undecided pairs, oldest first, with their age and the files they share (read-only)
    python scripts/audit/gate_adjudicate.py --queue-out pairs.csv                    # the same as a CSV with empty decision / absorbs / reason columns to fill in (read-only)
    python scripts/audit/gate_adjudicate.py --decide FND-0001 FND-0002 NONE --reason "the two tickets touch different functions of the file"
    python scripts/audit/gate_adjudicate.py --decide FND-0001 FND-0002 OVERLAPS --reason "..." --absorbs "criteria of FND-0002 copied into FND-0001"
    python scripts/audit/gate_adjudicate.py --apply-decisions pairs.csv              # DRY RUN of a filled CSV: which rows would be decided and which are refused
    python scripts/audit/gate_adjudicate.py --apply-decisions pairs.csv --apply      # decides them

Decisions: OVERLAPS (the two really overlap; a CONFIRMED link, `absorbs` names what one ticket takes over), RELATED, DEPENDS_ON (the first ticket depends on the second), NONE (no
real collision) and REVERT (the colliding commit was reverted: the 40-character sha of the revert goes in the reason). A decision closes the proposal, writes a log on both tickets and
releases the collision hold of a ticket that has no undecided pair left. A pair that was decided is never proposed again.

Who. Only the role backlog_adjudicator decides, with its own login: FONDOS_BACKLOG_PG_DSN_ADJUDICATOR, created by the owner and kept OUTSIDE the repository .env (type it in this terminal;
never export it in a shell that also launches agents). The deciding identity is FONDOS_ACTOR, which must be a human:<login>. A write to the live backlog asks for the usual typed
confirmation. The queue is read with the ordinary login.

Exit codes: 0 ok (or dry run), 1 a write failed or was refused, 2 usage error (no database, the collision store is not applied, a bad CSV).
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RC_OK, RC_FAILED, RC_USAGE = 0, 1, 2
DECISIONS = ("OVERLAPS", "RELATED", "DEPENDS_ON", "NONE", "REVERT")
COLUMNS = ["a", "b", "age_hours", "level", "evidence", "decision", "absorbs", "reason"]


def format_queue(rows: list) -> list:
    """One line per undecided pair for --list. Pure."""
    if not rows:
        return ["no undecided collision pair"]
    out = [f"{len(rows)} undecided collision pair(s), oldest first:"]
    for r in rows:
        mark = {"ALERTA": "[ALERTA] ", "ATENCION": "[ATENCION] "}.get(r["level"], "")
        out.append(f"  {mark}{r['a']} / {r['b']}  {r['age_hours']} h  {r.get('evidence', '')}")
    return out


def queue_csv(rows: list) -> str:
    """The queue as a CSV to fill in (decision, absorbs, reason empty). Pure."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({"a": r["a"], "b": r["b"], "age_hours": r["age_hours"], "level": r["level"], "evidence": r.get("evidence", ""), "decision": "", "absorbs": "", "reason": ""})
    return buf.getvalue()


def read_decisions(text: str, undecided: set) -> tuple:
    """([(a, b, decision, absorbs, reason)] to apply, [(line, why)] refused) from a filled CSV; rows with an empty decision are skipped silently. `undecided`: {(a, b)} canonical pairs. Pure."""
    ok, bad = [], []
    for n, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        decision = (row.get("decision") or "").strip().upper()
        if not decision:
            continue
        a, b = (row.get("a") or "").strip(), (row.get("b") or "").strip()
        reason, absorbs = (row.get("reason") or "").strip(), (row.get("absorbs") or "").strip() or None
        if decision not in DECISIONS:
            bad.append((n, f"decision {decision!r} is not one of {', '.join(DECISIONS)}"))
        elif (min(a, b), max(a, b)) not in undecided:
            bad.append((n, f"the pair {a} / {b} is not in the undecided queue"))
        elif len(reason) < 10:
            bad.append((n, "a reason of 10 or more characters is required"))
        elif decision == "REVERT" and not any(len(w) == 40 and all(c in "0123456789abcdef" for c in w) for w in reason.replace(",", " ").split()):
            bad.append((n, "REVERT needs the 40-character sha of the revert commit in the reason"))
        else:
            ok.append((a, b, decision, absorbs, reason))
    return ok, bad


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="print the undecided pairs")
    g.add_argument("--queue-out", metavar="CSV", help="write the undecided pairs as a CSV to fill in")
    g.add_argument("--decide", nargs=3, metavar=("A", "B", "DECISION"), help="decide one pair")
    g.add_argument("--apply-decisions", metavar="CSV", help="decide the rows of a filled CSV (dry run unless --apply)")
    ap.add_argument("--reason", help="why (10 or more characters; for REVERT it carries the revert sha)")
    ap.add_argument("--absorbs", help="for OVERLAPS or RELATED: what one ticket takes over from the other")
    ap.add_argument("--apply", action="store_true", help="with --apply-decisions: write (default: dry run)")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import shared.config  # noqa: F401  (loads .env: the ordinary read login)
    from shared import backlog_client as bc

    try:
        if not bc.collision_store_present():
            print("the collision store is not applied (owner: python scripts/ops/migrate_backlog_v2.py --phase collision)", file=sys.stderr)
            return RC_USAGE
        rows = bc.gate_queue()
    except Exception as exc:                                  # never echo a DSN
        print(f"backlog unreachable: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    if args.list:
        print("\n".join(format_queue(rows)))
        return RC_OK
    if args.queue_out:
        Path(args.queue_out).write_text(queue_csv(rows), encoding="utf-8", newline="")
        print(f"{len(rows)} pair(s) written to {args.queue_out}; fill in decision (and absorbs, reason) and run --apply-decisions")
        return RC_OK

    undecided = {(r["a"], r["b"]) for r in rows}
    if args.decide:
        a, b, decision = args.decide[0], args.decide[1], args.decide[2].upper()
        csv_text = f"a,b,decision,absorbs,reason\n{a},{b},{decision},{csv_cell(args.absorbs)},{csv_cell(args.reason)}\n"
        todo, refused = read_decisions(csv_text, undecided)
        dry = False
    else:
        todo, refused = read_decisions(Path(args.apply_decisions).read_text(encoding="utf-8"), undecided)
        dry = not args.apply
    for n, why in refused:
        print(f"refused (line {n}): {why}", file=sys.stderr)
    for a, b, decision, absorbs, reason in todo:
        print(f"{'would decide' if dry else 'deciding'} {a} / {b}: {decision}" + (f" (absorbs: {absorbs})" if absorbs else ""))
    if refused or not todo:
        print("nothing is decided." if not todo else "some rows are refused: fix them first; nothing is decided.", file=sys.stderr)
        return RC_USAGE if refused else RC_OK
    if dry:
        print("\nDRY RUN: nothing written. Add --apply to decide these pairs.")
        return RC_OK
    dsn = os.environ.get(bc._ADJUDICATOR_DSN_ENV_VAR)
    refusal = bc.require_live_write_cli(dsn)
    if refusal is not None:
        return refusal
    done = 0
    try:
        for a, b, decision, absorbs, reason in todo:
            bc.adjudicate_overlap(a, b, decision, reason, absorbs=absorbs)
            done += 1
    except Exception as exc:
        print(f"FAILED after {done} decision(s): {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return RC_FAILED
    print(f"\nDECIDED: {done} pair(s).")
    return RC_OK


def csv_cell(value) -> str:
    """One CSV field (quoted, quotes doubled) for the single-row --decide path. Pure."""
    text = (value or "").replace('"', '""')
    return f'"{text}"'


if __name__ == "__main__":
    sys.exit(main())
