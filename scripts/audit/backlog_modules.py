#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_modules.py -- derives which modules each open ticket is about and records what is new (Backlog v3 W4b, FND-0360; the rules are in shared/backlog_modules.py).

    python scripts/audit/backlog_modules.py            # DRY RUN: counts what it would record; writes nothing
    python scripts/audit/backlog_modules.py --apply    # records the new facts through backlog.sp_record_module (the autopilot's login)

Sources, all automatic: TOUCHED from git (the files of every commit that cites an open ticket: ops_event COMMIT facts carry only the subject, so the files are read with `git diff-tree`), and CITED
(proposals only) from the title, the description, the progress logs and the object name. A test maps to the module it tests; registries, documents and configuration never count (see the rules).
The autopilot run (scripts/audit/backlog_autopilot.py --apply) calls this at the end of every run, so nobody starts it. It is idempotent: a fact already current is not sent, and the database
refuses to write again a proposal that an agent or a person retracted. It writes ONLY through FONDOS_BACKLOG_PG_DSN_AUTOPILOT (role backlog_autopilot) and does nothing, with exit 0, before the
`modules` phase is deployed. Exit codes: 0 ok, 2 git or the backlog could not be read.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from shared import backlog_modules as bm  # noqa: E402

ACTOR = "system:autopilot"
RC_OK, RC_USAGE = 0, 2


def _git(*args, cwd: Path = ROOT) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def commit_files(shas: list, git=_git) -> dict:
    """{sha: [paths]} for the commits that exist in this clone; a commit that does not exist (rewritten, never fetched) is skipped. Not pure: it calls git."""
    out = {}
    for sha in shas:
        try:
            out[sha] = [p for p in git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines() if p]
        except RuntimeError:
            continue
    return out


def gather(bc, git=_git) -> dict:
    """{ticket: [fact]} the sources say now, for the OPEN tickets. Reads the backlog and git only."""
    tracked, by_base = bm.index_tracked(git("ls-files").splitlines())
    tickets = {t["action_point_id"]: t for t in bc.list_tickets(open_only=True)}
    logs: dict = {}
    for row in bc.list_logs(ids=sorted(tickets)):
        logs.setdefault(row["action_point_id"], []).append(row["comment"])
    commits_of: dict = {}
    for sha, ap in sorted(bc.ops_event_keys("COMMIT")):
        if ap in tickets:
            commits_of.setdefault(ap, []).append(sha)
    files = commit_files(sorted({s for shas in commits_of.values() for s in shas}), git)
    desired: dict = {}
    for ap, t in tickets.items():
        facts = bm.text_facts(t, logs.get(ap, []), tracked, by_base)
        facts += bm.commit_facts({s: files[s] for s in commits_of.get(ap, []) if s in files}, tracked, by_base)
        if facts:
            desired[ap] = facts
    return desired


def run(bc, apply: bool = False, git=_git, out=print) -> dict:
    """Gather, subtract what is current and (with apply) record the rest. Returns {'present', 'desired', 'new', 'recorded', 'unchanged'}; 'present' is False before the phase is deployed."""
    if not bc.modules_store_present():
        out("the modules phase is not deployed yet: nothing to do")
        return {"present": False, "desired": 0, "new": 0, "recorded": 0, "unchanged": 0}
    desired = gather(bc, git)
    current = {(r["action_point_id"], r["module_path"], r["symbol"], r["role"], r["source"]) for r in bc.list_ticket_modules()}
    new = bm.new_facts(desired, current)
    res = {"present": True, "desired": sum(len(v) for v in desired.values()), "new": len(new), "recorded": 0, "unchanged": 0}
    out(f"modules: {res['desired']} fact(s) from the sources of {len(desired)} open ticket(s); {res['new']} new")
    if apply and new:
        r = bc.record_modules([{"ap_id": ap, "module": f["module"], "role": f["role"], "source": f["source"], "evidence": f["evidence"]} for ap, f in new], actor=ACTOR)
        res.update(r)
        out(f"modules: recorded {r['recorded']}, unchanged {r['unchanged']}")
    return res


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="record the new facts (default: only count them)")
    args = ap.parse_args(argv)
    os.environ["FONDOS_ACTOR"] = ACTOR
    import shared.config  # noqa: F401 -- loads .env
    from shared import backlog_client as bc
    try:
        run(bc, apply=args.apply)
    except Exception as exc:                                  # noqa: BLE001 -- never echo a DSN
        print(f"error: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return RC_USAGE
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
