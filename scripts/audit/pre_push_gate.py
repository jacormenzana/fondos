#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pre_push_gate.py -- the pre-push hook of the collision gate (Backlog v2 Phase C slice 3, FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.4; NORMAS_BACKLOG.md 4.6).
Installed by `bash scripts/audit/commit-msg.sh` as .git/hooks/pre-push; git calls it with the remote name and url and writes one line per ref to stdin:
`<local ref> <local sha> <remote ref> <remote sha>`.

It matters only in BLOCK mode (the effective mode: the lower of BACKLOG_COLLISION_GATE_MODE and the latest backlog.gate_state event). The commits the push carries are listed
(`git rev-list <local> --not <remote sha>`; for a new branch, `--not --remotes=<remote>`), the tickets their subjects and `Refs:` trailers cite are collected, and the push is REFUSED
while any collision pair touching those tickets is undecided: the exact shas and pairs are printed, and the owner adjudicates them (scripts/audit/gate_adjudicate.py). In AUDIT mode it
only warns; with the gate OFF it does nothing and does not even ask the database.

Backlog unreachable, or any error of this script: the push goes through with a warning (the CI attestation of slice 4 repeats the check; an outage never stalls a publication). The only
exit code that refuses a push is REJECTED_RC; the installed shim turns anything else into success. Deleting a ref is never refused.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTOR = "system:gate"
REJECTED_RC = 3
ZEROS = "0" * 40


def parse_push(text: str) -> list:
    """[(local_ref, local_sha, remote_ref, remote_sha)] of the stdin git writes; blank or short lines are skipped. Pure."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4:
            out.append(tuple(parts))
    return out


def rev_list_args(local_sha: str, remote_sha: str, remote_name: str) -> list:
    """The `git rev-list` arguments listing the commits a ref update carries; [] for a deletion (nothing is published). Pure."""
    if local_sha == ZEROS:
        return []
    if remote_sha == ZEROS:
        return [local_sha, "--not", f"--remotes={remote_name}"]
    return [local_sha, "--not", remote_sha]


def tickets_of(messages: dict) -> dict:
    """{sha: [ticket ids cited by its subject or Refs trailer]} for {sha: full message}; commits that cite none are left out. Pure."""
    from shared import backlog_gate as g
    out = {}
    for sha, text in messages.items():
        cited = g.parse_message(text).mentions
        if cited:
            out[sha] = cited
    return out


def refusal(by_commit: dict, undecided: list) -> list:
    """The lines printed when the push is refused: every commit that cites a ticket of an undecided pair, and the pairs. Pure."""
    touched = {t for p in undecided for t in (p["a"], p["b"])}
    lines = ["pre-push gate [BLOCK]: the push carries commits on tickets with an UNDECIDED collision pair; publication is refused until the owner adjudicates them."]
    for sha, tickets in sorted(by_commit.items()):
        hit = sorted(set(tickets) & touched)
        if hit:
            lines.append(f"  {sha[:12]}  cites {', '.join(hit)}")
    for p in undecided:
        lines.append(f"  pair {p['a']} / {p['b']}  undecided for {p.get('age_hours', '?')} h")
    lines.append("  decide them with: python scripts/audit/gate_adjudicate.py --list   (then --decide <a> <b> <OVERLAPS|RELATED|DEPENDS_ON|NONE|REVERT> --reason ...)")
    return lines


def evaluate(by_commit: dict, ceiling: str, state_fn, undecided_fn) -> tuple:
    """(exit code, lines). `state_fn(ceiling)` -> {'mode', ...}; `undecided_fn(tickets)` -> [{'a', 'b', 'age_hours'}]. Pure apart from those two callables."""
    if ceiling == "OFF" or not by_commit:
        return 0, []
    mode = state_fn(ceiling)["mode"]
    if mode == "OFF":
        return 0, []
    tickets = sorted({t for ts in by_commit.values() for t in ts})
    undecided = undecided_fn(tickets)
    if not undecided:
        return 0, []
    if mode == "BLOCK":
        return REJECTED_RC, refusal(by_commit, undecided)
    return 0, [f"pre-push gate [{mode}]: {len(undecided)} undecided collision pair(s) on the tickets of this push (not refused in {mode}): "
               + "; ".join(f"{p['a']} / {p['b']}" for p in undecided)]


def git(*args) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20)
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed")
    return r.stdout


def messages_of_push(updates: list, remote_name: str) -> dict:
    out = {}
    for _, local_sha, _, remote_sha in updates:
        args = rev_list_args(local_sha, remote_sha, remote_name)
        if not args:
            continue
        for sha in git("rev-list", *args).split():
            if sha not in out:
                out[sha] = git("log", "-1", "--format=%B", sha)
    return out


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    remote_name = argv[0] if argv else "origin"
    try:
        stdin_text = sys.stdin.read() if sys.stdin is not None else ""
        sys.path.insert(0, str(ROOT))
        os.environ["FONDOS_ACTOR"] = ACTOR
        sys.stdin = None                                   # non-interactive, like the commit-msg hook
        import shared.config as cfg
        ceiling = cfg.BACKLOG_COLLISION_GATE_MODE if cfg.BACKLOG_COLLISION_GATE_MODE in ("OFF", "AUDIT", "BLOCK") else "OFF"
        if ceiling == "OFF":
            return 0
        by_commit = tickets_of(messages_of_push(parse_push(stdin_text), remote_name))
        from shared import backlog_client as bc
        rc, lines = evaluate(by_commit, ceiling, lambda c: bc.collision_state(c, scoped=True), lambda ts: bc.gate_undecided(ts, scoped=True))
        for line in lines:
            print(line, file=sys.stderr)
        return rc
    except Exception as exc:                              # noqa: BLE001  (fail open: the CI attestation repeats the check)
        print(f"pre-push gate: could not evaluate ({type(exc).__name__}); the push goes through.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
