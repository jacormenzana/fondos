#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
publish_master.py -- the gated push to origin/master (FND-0273). The owner's push, with the checks a bare `git push` does not make.

    python scripts/ops/publish_master.py <hash> --tickets FND-0273,FND-0275            # DRY RUN: every check, nothing pushed
    python scripts/ops/publish_master.py <hash> --tickets FND-0273,FND-0275 --apply    # the same checks, then `git push origin <hash>:master`

What it refuses (exit 1, the message says which):
  * origin/master is not an ancestor of <hash> (the branch is behind or diverged: rebase first);
  * a commit in origin/master..<hash> whose message names no FND-nnnn ticket;
  * a commit that names none of --tickets (a foreign commit riding along: seen 2026-10-12, a push of one branch would have carried four
    commits of other sessions); --allow-foreign lets them through knowingly;
  * a <hash> that is not a commit, or an empty range (nothing to publish).
It pushes an EXPLICIT hash, never a branch name, and prints origin/master before and after. The tip predicate after the push is the shared
one (shared/governance/run_identity.code_integrity: HEAD == origin/master and a clean tracked tree, decision D1), so the promotion gate and
this script never disagree (P#11).

The decision logic is pure (`evaluate`) so it is tested without git. Exit codes: 0 ok (or dry run), 1 refused, 2 usage / git error.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIP = "origin/master"
TICKET_RE = re.compile(r"\bFND-\d{4}\b")
RC_OK, RC_REFUSED, RC_USAGE = 0, 1, 2


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    subject: str
    tickets: tuple = ()


@dataclass
class Verdict:
    ok: bool
    refusals: list = field(default_factory=list)


def tickets_of(text: str) -> tuple:
    return tuple(sorted(set(TICKET_RE.findall(text or ""))))


def evaluate(commits: list, *, ancestor_ok: bool, tickets: set, allow_foreign: bool = False) -> Verdict:
    """Pure decision. `commits` = origin/master..<hash> (oldest first); `tickets` = the tickets this push is for."""
    refusals = []
    if not ancestor_ok:
        refusals.append(f"{TIP} is not an ancestor of the target: the branch is behind or diverged (rebase on {TIP} first)")
    if not commits:
        refusals.append("nothing to publish: the range is empty")
    unnamed = [c for c in commits if not c.tickets]
    if unnamed:
        refusals.append("commit(s) that name no FND-nnnn ticket: " + ", ".join(f"{c.sha[:7]} {c.subject[:50]!r}" for c in unnamed))
    if not allow_foreign:
        foreign = [c for c in commits if c.tickets and not set(c.tickets) & tickets]
        if foreign:
            refusals.append("commit(s) for tickets not named in --tickets (foreign work riding along; --allow-foreign to push them): "
                            + ", ".join(f"{c.sha[:7]} {','.join(c.tickets)}" for c in foreign))
    return Verdict(not refusals, refusals)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60)


def parse_log(raw: str) -> list:
    """`git log --format=%H%x1f%an%x1f%B%x1e` output -> [Commit] (oldest first is the caller's `--reverse`). Pure."""
    out = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        sha, author, body = (rec.split("\x1f") + ["", ""])[:3]
        out.append(Commit(sha.strip(), author, body.strip().splitlines()[0] if body.strip() else "", tickets_of(body)))
    return out


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="the commit to publish (a hash; a branch name is resolved to its hash and the HASH is pushed)")
    ap.add_argument("--tickets", required=True, help="comma-separated FND-nnnn this push is for")
    ap.add_argument("--allow-foreign", action="store_true", help="publish commits that name other tickets too")
    ap.add_argument("--apply", action="store_true", help="push (default: dry run)")
    args = ap.parse_args(argv)
    tickets = {t.strip().upper() for t in args.tickets.split(",") if t.strip()}
    if not tickets or not all(TICKET_RE.fullmatch(t) for t in tickets):
        print("error: --tickets needs FND-nnnn values", file=sys.stderr)
        return RC_USAGE
    fetch = _git("fetch", "--quiet", "origin")
    if fetch.returncode != 0:
        print(f"error: git fetch failed: {fetch.stderr.strip()}", file=sys.stderr)
        return RC_USAGE
    target = _git("rev-parse", "--verify", "--quiet", f"{args.target}^{{commit}}")
    before = _git("rev-parse", "--verify", "--quiet", TIP)
    if target.returncode != 0 or before.returncode != 0:
        print(f"error: cannot resolve {args.target!r} or {TIP}", file=sys.stderr)
        return RC_USAGE
    sha, tip = target.stdout.strip(), before.stdout.strip()
    ancestor_ok = _git("merge-base", "--is-ancestor", tip, sha).returncode == 0
    log = _git("log", "--reverse", "--format=%H%x1f%an%x1f%B%x1e", f"{tip}..{sha}")
    commits = parse_log(log.stdout)
    print(f"{TIP} = {tip[:7]}; publishing {sha[:7]} ({len(commits)} commit(s)):")
    for c in commits:
        print(f"  {c.sha[:7]} {c.author}  [{','.join(c.tickets) or 'NO TICKET'}]  {c.subject[:70]}")
    verdict = evaluate(commits, ancestor_ok=ancestor_ok, tickets=tickets, allow_foreign=args.allow_foreign)
    if not verdict.ok:
        for r in verdict.refusals:
            print(f"REFUSED: {r}")
        return RC_REFUSED
    if not args.apply:
        print("DRY RUN: all checks pass. --apply pushes this hash.")
        return RC_OK
    push = _git("push", "origin", f"{sha}:master")
    print(push.stdout.strip() + push.stderr.strip())
    if push.returncode != 0:
        return RC_USAGE
    _git("fetch", "--quiet", "origin")
    after = _git("rev-parse", TIP).stdout.strip()
    sys.path.insert(0, str(ROOT))
    from shared.governance.run_identity import code_integrity
    state = code_integrity(ROOT)
    print(f"{TIP}: {tip[:7]} -> {after[:7]}; code integrity here: {'OK' if state['ok'] else 'NOT at the tip: ' + str(state['detail'])}")
    return RC_OK if after == sha else RC_USAGE


if __name__ == "__main__":
    sys.exit(main())
