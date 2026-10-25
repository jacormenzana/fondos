#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_hygiene.py -- read-only registry-hygiene checks of the action-point backlog (FND-0323; NORMAS_BACKLOG.md section 4.1). Information, exit 0.

    python scripts/audit/backlog_hygiene.py --premise-sweep d5397af^..d5397af [--include-closed] [--path P ...]
    python scripts/audit/backlog_hygiene.py --stale-status [--days 14]

--premise-sweep REV[..REV]  (section 4.1 rule 3) At a release or promotion: the UPPER_SNAKE identifiers (switches, parameters) whose lines changed between the two
    revisions in the config files (default shared/config.py and proyecto3/config/scorer_params.yaml), and the PENDING tickets whose text or logs cite one of them: their
    premise may have changed. A ticket that cites NO changed identifier is not found (measured 2026-10-25 on d5397af: it finds FND-0258 and FND-0235, misses FND-0257).
--stale-status  (rule 2) IN_PROGRESS / BLOCKED / READY_FOR_DEPLOY tickets with no log for --days days. Idle time is a weak signal: it finds a ticket nobody touched, not
    a ticket whose closing condition is already met (FND-0243 was idle 7 days when it was already done).

No SQL here: tickets and logs come from shared/backlog_client (the only door to the gestion database). Nothing is written.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_PATHS = ("shared/config.py", "proyecto3/config/scorer_params.yaml")
IDENT = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
SAFE_REV = re.compile(r"^[\w.^~/@{}-]+$")
PENDING = ("OPEN", "TODO", "IN_PROGRESS", "READY_FOR_DEPLOY", "BLOCKED", "DEFERRED")
WATCHED = ("IN_PROGRESS", "BLOCKED", "READY_FOR_DEPLOY")


def changed_identifiers(diff_text: str) -> list:
    """Sorted UPPER_SNAKE identifiers on the added or removed lines of a unified diff (headers excluded). Pure."""
    found = set()
    for line in diff_text.splitlines():
        if line[:1] in "+-" and not line.startswith(("+++", "---")):
            found.update(IDENT.findall(line[1:]))
    return sorted(found)


def tickets_citing(identifiers: list, tickets: list, logs: list, include_closed: bool = False) -> list:
    """[{id, status, found}] for the tickets whose title, description or logs contain a changed identifier (whole token). Pending ones only unless include_closed. Pure."""
    texts: dict = {}
    for t in tickets:
        texts[t["action_point_id"]] = [t.get("title") or "", t.get("scenario_description") or ""]
    for lg in logs:
        texts.setdefault(lg["action_point_id"], []).append(lg.get("comment") or "")
    out = []
    for t in tickets:
        if t["status"] not in PENDING and not include_closed:
            continue
        blob = " ".join(texts.get(t["action_point_id"], []))
        tokens = set(IDENT.findall(blob))
        hit = [i for i in identifiers if i in tokens]
        if hit:
            out.append({"id": t["action_point_id"], "status": t["status"], "found": hit})
    return sorted(out, key=lambda r: r["id"])


def stale_status(tickets: list, logs: list, today: date, days: int) -> list:
    """[{id, status, idle_days, last_log}] for the WATCHED tickets whose newest log is `days` or more days old, most idle first. A ticket with no log counts from its
    creation. Pure."""
    last: dict = {}
    for lg in logs:
        ts = lg["log_ts"]
        if lg["action_point_id"] not in last or ts > last[lg["action_point_id"]]:
            last[lg["action_point_id"]] = ts
    out = []
    for t in tickets:
        if t["status"] not in WATCHED:
            continue
        ts = last.get(t["action_point_id"]) or t.get("creation_ts")
        if ts is None:
            continue
        d = ts.date() if isinstance(ts, datetime) else ts
        idle = (today - d).days
        if idle >= days:
            out.append({"id": t["action_point_id"], "status": t["status"], "idle_days": idle, "last_log": d.isoformat()})
    return sorted(out, key=lambda r: (-r["idle_days"], r["id"]))


def git_diff(rev_range: str, paths: list) -> str:
    """`git diff A B -- paths` for 'A..B' or 'A' (= A..HEAD). The revisions are checked against a whitelist: no option can be injected."""
    a, _, b = rev_range.partition("..")
    b = b or "HEAD"
    for rev in (a, b):
        if not rev or rev.startswith("-") or not SAFE_REV.match(rev):
            raise ValueError(f"bad revision {rev!r}")
    res = subprocess.run(["git", "-C", str(ROOT), "diff", a, b, "--", *paths], capture_output=True, text=True, encoding="utf-8", check=False)
    if res.returncode != 0:
        raise RuntimeError(f"git diff failed: {res.stderr.strip()[:200]}")
    return res.stdout


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--premise-sweep", metavar="REV[..REV]")
    g.add_argument("--stale-status", action="store_true")
    ap.add_argument("--include-closed", action="store_true", help="--premise-sweep: also list closed tickets (validation of the sweep)")
    ap.add_argument("--path", action="append", default=None, help="--premise-sweep: file to diff (repeatable; default the two config files)")
    ap.add_argument("--days", type=int, default=14, help="--stale-status: days without a log (default 14)")
    args = ap.parse_args(argv)

    from shared import config  # noqa: F401  (loads the repo .env: FONDOS_BACKLOG_PG_DSN)
    from shared import backlog_client as bc
    tickets, logs = bc.list_tickets(), bc.list_logs()
    if args.premise_sweep:
        ids = changed_identifiers(git_diff(args.premise_sweep, args.path or list(DEFAULT_PATHS)))
        print(f"{len(ids)} identifiers changed in {args.premise_sweep}")
        hits = tickets_citing(ids, tickets, logs, include_closed=args.include_closed)
        print(f"{len(hits)} {'tickets' if args.include_closed else 'pending tickets'} cite one of them (their premise may have changed: log 'premisa cambiada' or close):")
        for h in hits:
            print(f"  {h['id']} [{h['status']}] {', '.join(h['found'][:6])}{' ...' if len(h['found']) > 6 else ''}")
    else:
        rows = stale_status(tickets, logs, date.today(), args.days)
        print(f"{len(rows)} of {sum(t['status'] in WATCHED for t in tickets)} watched tickets (IN_PROGRESS / BLOCKED / READY_FOR_DEPLOY) have no log for {args.days}+ days:")
        for r in rows:
            print(f"  {r['id']} [{r['status']}] idle {r['idle_days']} d (last log {r['last_log']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
