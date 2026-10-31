#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_sync.py -- record the commits that cite backlog tickets as operational facts, and move a ticket forward when a commit's `Refs:` trailer says
work on it has started (Backlog v2 Phase C slice 1, FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.1 and 6.4.8; NORMAS_BACKLOG.md 4.4).

    python scripts/audit/backlog_sync.py                     # DRY RUN over origin/master: what would be recorded and moved; writes nothing
    python scripts/audit/backlog_sync.py --apply             # records the new commit events (idempotent) and runs the auto-forward
    python scripts/audit/backlog_sync.py --rev HEAD --apply  # another revision (default origin/master: the published history)

Citations. A `Refs: FND-nnnn` trailer (several ids may be comma separated, several trailers allowed) is source REFS: the commit says it is work on the
ticket. An id in the subject line is source SUBJECT: a mention (it cannot tell work from a reference). The body is not read. Ids that are not in the
backlog (e.g. the documented gap FND-0339..FND-0349) are reported and skipped; malformed tokens (`fnd0193`, `FND_0193`, `FND-193`) are reported (the
commit-msg hook of slice 2 rejects them at commit time).

Facts. One backlog.ops_event row per (commit, ticket), kind COMMIT, actor system:sync, fact time = the author time. Recording is idempotent: a rerun
records nothing twice, so the whole history is scanned every time (no watermark to lose). The violations V-RETRO and V-WORK-ON-CLOSED are computed by
the view from these rows (backlog.v_violations_c).

Auto-forward (switch BACKLOG_AUTO_FORWARD_ENABLED). For a commit recorded for the FIRST time whose REFS cite a ticket in OPEN or TODO: transition to
IN_PROGRESS through shared.backlog_client.transition() as system:sync, the commit as evidence. Never from BLOCKED or DEFERRED, never backward, never
from a SUBJECT mention, never again for a commit already recorded (a ticket a person moved back stays where they put it). Closures stay manual.

Commit-msg gate (slice 2, NORMAS_BACKLOG.md 4.5). Each commit made after BACKLOG_HOOK_EPOCH that cites a ticket is judged by its `Backlog-Gate:` trailer (shared/backlog_gate.py):
checked (the nonce is in backlog.gate_check and names the commit's tree), pending (`offline`: the hook could not reach the backlog, no penalty) or BYPASS (trailer missing, unknown
nonce, tree mismatch). A new bypass is recorded in backlog.hook_bypass (violation V-HOOK-BYPASS) and raises a hold on the ticket. With the epoch empty the gate is not armed:
the run only reports the trailers it sees. Merge commits are exempt.

Exit codes: 0 ok (or dry run), 1 a write failed (what was written before it stays: every write is idempotent), 2 usage error (git failed, no backlog DSN,
or --apply before the owner applied `migrate_backlog_v2.py --phase sync`).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTOR = "system:sync"
RC_OK, RC_FAILED, RC_USAGE = 0, 1, 2
FORWARD_FROM = ("OPEN", "TODO")

sys.path.insert(0, str(ROOT))
from shared import backlog_gate as gate  # noqa: E402  (the pure half of the commit-msg gate: one reading of a commit message for the hook and for this sync)

_ID, _LOOSE = gate.ID, gate.LOOSE
_FMT = ("%H%x1f%aI%x1f%s%x1f%(trailers:key=Refs,valueonly,separator=%x2C)%x1f%T%x1f%P%x1f"
        "%(trailers:key=Backlog-Gate,valueonly,separator=%x2C)%x1e")


# ── pure ──────────────────────────────────────────────────────────────────────────────────────────

def parse_log(text: str) -> list:
    """Commits of `git log --format=_FMT` output: [{'sha', 'author_ts', 'subject', 'refs', 'mentions', 'malformed', 'tree', 'parents', 'gate'}] (newest first, as git prints).
    'gate' is the value of the last Backlog-Gate trailer (None = absent); 'parents' is the number of parents."""
    out = []
    for rec in text.split("\x1e"):
        rec = rec.strip("\r\n")
        if not rec:
            continue
        sha, ts, subject, refs, tree, parents, gates = (rec.split("\x1f") + [""] * 7)[:7]
        well = {f"FND-{m}" for m in _ID.findall(refs)} | {f"FND-{m}" for m in _ID.findall(subject)}
        malformed = sorted({t for t in _LOOSE.findall(f"{subject} {refs}") if not re.fullmatch(r"FND-\d{4}", t)})
        out.append({"sha": sha.strip(), "author_ts": datetime.fromisoformat(ts.strip()), "subject": subject,
                    "refs": sorted({f"FND-{m}" for m in _ID.findall(refs)}), "mentions": sorted(well), "malformed": malformed,
                    "tree": tree.strip(), "parents": len(parents.split()), "gate": gates.split(",")[-1].strip() or None})
    return out


def citations(commit: dict) -> list:
    """[(ap_id, source)] of one commit: REFS for a trailer id (it wins over a subject mention of the same id), SUBJECT for the rest."""
    refs = set(commit["refs"])
    return [(ap, "REFS" if ap in refs else "SUBJECT") for ap in commit["mentions"]]


def plan(commits: list, statuses: dict, recorded: set, forward_enabled: bool = True) -> dict:
    """What a run does, without doing it. `statuses`: {ap_id: status} of the backlog; `recorded`: {(sha, ap_id)} already in ops_event.
    {'new': [(commit, ap, source)], 'already': n, 'unknown': {ids}, 'malformed': [(sha, token)], 'forward': [(ap, commit)]}."""
    out = {"new": [], "already": 0, "unknown": set(), "malformed": [], "forward": []}
    forwarded = set()
    for c in sorted(commits, key=lambda c: c["author_ts"]):               # oldest first: the first commit is the one that moves the ticket
        out["malformed"] += [(c["sha"], t) for t in c["malformed"]]
        for ap, source in citations(c):
            if ap not in statuses:
                out["unknown"].add(ap)
                continue
            if (c["sha"], ap) in recorded:
                out["already"] += 1
                continue
            out["new"].append((c, ap, source))
            if forward_enabled and source == "REFS" and statuses[ap] in FORWARD_FROM and ap not in forwarded:
                out["forward"].append((ap, c))
                forwarded.add(ap)
    return out


# ── I/O ───────────────────────────────────────────────────────────────────────────────────────────

def git_log(rev: str) -> str:
    r = subprocess.run(["git", "log", rev, f"--format={_FMT}"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"git log {rev} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rev", default="origin/master", help="revision whose history is read (default origin/master)")
    ap.add_argument("--apply", action="store_true", help="record the events and run the auto-forward (default: dry run)")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    os.environ["FONDOS_ACTOR"] = ACTOR      # one process, one identity (owner order 2026-10-28): this process IS the system sync, whoever started it
    import shared.config as cfg  # noqa: F401  (loads .env: FONDOS_BACKLOG_PG_DSN)
    from shared import backlog_client as bc

    try:
        commits = parse_log(git_log(args.rev))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return RC_USAGE
    try:
        statuses = {t["action_point_id"]: t["status"] for t in bc.list_tickets()}
        applied = bc.sync_store_present()
        recorded = {(r, a) for r, a in bc.ops_event_keys("COMMIT")} if applied else set()
    except Exception as exc:                                        # never echo a DSN
        print(f"backlog unreachable: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    p = plan(commits, statuses, recorded, cfg.BACKLOG_AUTO_FORWARD_ENABLED)
    try:
        epoch = gate.parse_epoch(cfg.BACKLOG_HOOK_EPOCH)
        hook_applied = bc.hook_store_present()
        wanted = [c["gate"] for c in commits if c["gate"] and c["gate"] != gate.OFFLINE]
        vs = gate.verdicts(commits, bc.gate_checks(wanted) if hook_applied else {}, epoch, set(statuses))
    except Exception as exc:                                        # a bad epoch, an unreachable store
        print(f"commit-msg gate: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return RC_USAGE
    p["bypass"] = [(c, ap, why) for c, ap, v, why in vs if v == gate.BYPASS] if hook_applied else []
    n_refs = sum(1 for _, _, s in p["new"] if s == "REFS")
    print(f"{args.rev}: {len(commits)} commits, {sum(1 for c in commits if c['mentions'])} cite a ticket; "
          f"new events {len(p['new'])} ({n_refs} REFS, {len(p['new']) - n_refs} SUBJECT), already recorded {p['already']}")
    if p["unknown"]:
        print(f"ids not in the backlog (skipped): {', '.join(sorted(p['unknown']))}")
    if p["malformed"]:
        print(f"malformed ticket tokens: {len(p['malformed'])} (e.g. {', '.join(f'{s[:8]} {t!r}' for s, t in p['malformed'][:5])})")
    print(f"auto-forward ({'ON' if cfg.BACKLOG_AUTO_FORWARD_ENABLED else 'OFF'}): "
          + (", ".join(f"{a} {statuses[a]} -> IN_PROGRESS by {c['sha'][:8]}" for a, c in p["forward"]) or "nothing to move"))
    print(gate_report(vs, epoch, hook_applied))
    if not applied:
        print("the sync store is not applied (owner: python scripts/ops/migrate_backlog_v2.py --phase sync): every event above is new.")
    if not args.apply:
        print("\nDRY RUN: nothing written. --apply records the events, runs the auto-forward and records the bypassed commits (hold on the ticket).")
        return RC_OK
    if not applied:
        print("REFUSED: --apply needs the sync store (migrate_backlog_v2.py --phase sync). Nothing was written.", file=sys.stderr)
        return RC_USAGE
    refusal = bc.require_live_write_cli()                           # owner order 2026-10-29: only a person at a terminal writes to the live backlog; nothing is written before this
    if refusal is not None:
        return refusal
    done = {"written": 0, "moved": 0, "bypass": 0}
    try:
        apply_plan(p, done)
    except Exception as exc:
        print(f"FAILED after {done['written']} event(s), {done['moved']} move(s) and {done['bypass']} bypass(es): {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return RC_FAILED
    print(f"\nAPPLIED: {done['written']} event(s) recorded, {done['moved']} ticket(s) moved to IN_PROGRESS, {done['bypass']} new bypass(es) recorded.")
    return RC_OK


def gate_report(vs: list, epoch, hook_applied: bool) -> str:
    """One line on the commit-msg gate: the verdicts per commit (a commit citing several tickets counts once, by its worst verdict)."""
    if epoch is None:
        seen = sum(1 for c in {c["sha"]: c for c, _, _, _ in vs}.values() if c.get("gate"))
        return f"commit-msg gate: NOT ARMED (BACKLOG_HOOK_EPOCH is empty): {seen} commit(s) carry a Backlog-Gate trailer; no bypass is recorded."
    worst: dict = {}
    order = {gate.EXEMPT: 0, gate.CHECKED: 1, gate.PENDING: 2, gate.BYPASS: 3}
    for c, _, v, _ in vs:
        if order[v] >= order.get(worst.get(c["sha"]), -1):
            worst[c["sha"]] = v
    n = {v: sum(1 for x in worst.values() if x == v) for v in order}
    tail = "" if hook_applied else " -- the hook store is not applied (owner: migrate_backlog_v2.py --phase hook): nothing is recorded"
    return (f"commit-msg gate (armed since {epoch.isoformat()}): checked {n[gate.CHECKED]}, pending (offline) {n[gate.PENDING]}, "
            f"BYPASS {n[gate.BYPASS]}, exempt {n[gate.EXEMPT]}{tail}")


def apply_plan(p: dict, done: dict, conn=None) -> dict:
    """Write a plan: the events first (idempotent), then the forward moves. `done` counts as it goes, so a failure says how far it got."""
    from shared import backlog_client as bc
    for c, ap_id, source in p["new"]:
        done["written"] += bc.record_ops_event("COMMIT", c["sha"], ap_id, source, {"subject": c["subject"][:300]}, c["author_ts"],
                                               actor=ACTOR, conn=conn)
    session = bc.make_session_id("BACKLOG_SYNC", "SYNC")
    for ap_id, c in p["forward"]:
        bc.transition(ap_id, "IN_PROGRESS", session_id=session, actor=ACTOR, conn=conn,
                      comment=f"auto-forward: commit {c['sha'][:12]} cites this ticket in a Refs: trailer ({c['subject'][:160]})")
        done["moved"] += 1
    for c, ap_id, reason in p.get("bypass", []):                    # a new one raises a hold on the ticket (sp_record_hook_bypass)
        done["bypass"] = done.get("bypass", 0) + bc.record_hook_bypass(c["sha"], ap_id, reason, c["author_ts"], actor=ACTOR, conn=conn)
    return done


if __name__ == "__main__":
    sys.exit(main())
