# -*- coding: utf-8 -*-
"""
backlog_autopilot.py -- the rule table of the Backlog v3 autopilot (W3, FND-0358; doc/especificacion/ACTION_PLAN_BACKLOG_V3.md).

A ticket moves on EVIDENCE the system recorded, not on a declaration. This module is the pure half: it takes plain facts (the tickets, the merges that deliver them, the deploy and
cycle events, the closing-condition probes, the gate verdicts) and returns decisions. No database, no git, no I/O: scripts/audit/backlog_sync.py gathers the facts and applies the
decisions (a later slice), and the tests in tests/test_backlog_autopilot.py pin every edge.

The edges (a ticket in any other status is never moved here; BLOCKED and DEFERRED are never moved by a machine, and OPEN / TODO move on a `Refs:` commit through the commit-msg hook):

    IN_PROGRESS -> READY_FOR_DEPLOY   a merge on master delivers the ticket (a commit with `Closes: FND-nnnn`); the delivery gate for READY_FOR_DEPLOY must be clear.
    READY_FOR_DEPLOY -> CLOSED        CODE_FIX (default) or DATA_FIX (`Solution: DATA_FIX`), only when ALL of: the merge is on master; a change under db/ has its DEPLOY event (the DDL
                                      is live); every closing condition passes (DATA_FIX needs at least one, and a CYCLE event after the merge); the audit-incident gate (FND-0264) is
                                      clear; the delivery gate for a closure is clear; the release label is valid.

Anything short of that is a WAIT with the reasons, so the ticket stays visible (V-AUTO-REFUSED, W6) and nothing is closed on a hunch. REJECTED, DUPLICATED and WORKAROUND are never
automatic.

Backlog v3.1 (owner directive 2026-11-01, remove the administrative tasks) adds the events that had no trigger, so nobody has to move these tickets by hand:

    BLOCKED -> IN_PROGRESS            the ticket it is structurally blocked on is CLOSED (UNBLOCK).
    DEFERRED -> TODO                  its revisit date has been reached (UNDEFER).
    CLOSED -> IN_PROGRESS (REOPEN)    a merge on master AFTER the closure carries a commit that cites the ticket (`Refs:` or `Closes:`) and changes code or test files: more work was needed, so the
                                      delivery was incomplete. Only a delivery closure (CODE_FIX, DATA_FIX, WORKAROUND) reopens: a REJECTED or DUPLICATED ticket is not "delivered".
Reopening is the one move that is safe in every direction (it adds work and hides nothing), which is why a rule, an agent or a person may all record it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from shared import backlog_release as rel

MODES = ("OFF", "SHADOW", "ON")
AUTOMATIC_SOLUTIONS = ("CODE_FIX", "DATA_FIX")
DB_PREFIXES = ("db/",)                 # a change under these paths is not live until its DDL is applied: the closure waits for a DEPLOY event
MOVE, CLOSE, WAIT = "MOVE", "CLOSE", "WAIT"
REOPEN, UNBLOCK, UNDEFER = "REOPEN", "UNBLOCK", "UNDEFER"
DELIVERY_SOLUTIONS = ("CODE_FIX", "DATA_FIX", "WORKAROUND")
NON_CODE_PREFIXES = ("doc/", "docs/", "out/", "deployments/", "backlog_gate/")       # a change that touches only these (or a .md/.txt/.rst file) is not more work on the code
NON_CODE_SUFFIXES = (".md", ".txt", ".rst")
_PR = re.compile(r"^Merge pull request #(\d+)\b")


@dataclass(frozen=True)
class Merge:
    """A merge commit on master that delivers a ticket: it contains a commit whose message says `Closes: <ticket>`."""
    ref: str                           # the merge commit sha
    pr: Optional[int]                  # the pull request number, when the subject says so
    paths: tuple                       # the files the merge changed
    year: int
    month: int
    solution: Optional[str] = None     # the Solution: trailer of the delivering commit, upper-cased; None = CODE_FIX


@dataclass(frozen=True)
class Citation:
    """A merge on master whose branch has a commit citing a ticket (`Refs:` or `Closes:`): when it happened and which files it changed."""
    ref: str                           # the merge commit sha
    ts: datetime                       # the merge's author time (aware)
    paths: tuple                       # the files the merge changed


@dataclass
class Facts:
    tickets: dict                                        # {ap: {"status": str, "release_version": Optional[str]}}
    merges: dict = field(default_factory=dict)           # {ap: [Merge, ...]} oldest first
    deploys: set = field(default_factory=set)            # tickets with a DEPLOY event (their DDL is live)
    cycles: set = field(default_factory=set)             # tickets with a CYCLE event after their merge
    probes: dict = field(default_factory=dict)           # {ap: [(ok, text), ...]}: the active closing conditions, evaluated
    incident: dict = field(default_factory=dict)         # {ap: [reason]}: the audit-incident gate (shared.backlog_release.incident_reasons)
    rfd_blockers: dict = field(default_factory=dict)     # {ap: [str]}: what stops READY_FOR_DEPLOY (backlog_client.gate_blockers)
    delivery_blockers: dict = field(default_factory=dict)  # {ap: [str]}: what stops a delivery closure
    citations: dict = field(default_factory=dict)          # {ap: [Citation, ...]}: merges that cite the ticket (any status): the facts a reopening is decided from
    closed_at: dict = field(default_factory=dict)          # {ap: datetime}: closure_ts of the CLOSED tickets (aware)
    blockers: dict = field(default_factory=dict)           # {ap: blocker ap}: the structured blocker of a BLOCKED ticket (its latest wait)
    revisits: dict = field(default_factory=dict)           # {ap: date}: the revisit_by of a DEFERRED ticket (its latest wait)
    today: Optional[date] = None


@dataclass(frozen=True)
class Decision:
    ap: str
    action: str                        # MOVE | CLOSE | WAIT
    from_status: str
    to_status: Optional[str]
    evidence_kind: str = "MERGE"
    evidence_ref: str = ""
    solution_type: Optional[str] = None
    release: Optional[str] = None
    reasons: tuple = ()

    @property
    def key(self) -> tuple:
        """What makes a decision the same decision on a later run (the note of a SHADOW run is written once per key)."""
        return (self.ap, self.action, self.to_status, self.evidence_ref)


def pr_number(subject: str) -> Optional[int]:
    """The pull request number of a `Merge pull request #n from ...` subject, else None. Pure."""
    m = _PR.match(subject or "")
    return int(m.group(1)) if m else None


def merge_release(m: Merge) -> Optional[str]:
    """The release label of a delivery by this merge: R<yyyy>.<mm>-PR<n>, or R<yyyy>.<mm>-M<sha7> when the merge is not a pull request. Pure."""
    tag = f"PR{m.pr}" if m.pr else f"M{m.ref[:7].upper()}"
    return rel.release_label(m.year, m.month, tag)


def _touches_db(m: Merge) -> bool:
    return any(str(p).startswith(DB_PREFIXES) for p in m.paths)


def decide_one(ap: str, facts: Facts) -> Optional[Decision]:
    """The decision for one ticket, or None when no rule applies. Pure."""
    t = facts.tickets.get(ap)
    merges = facts.merges.get(ap) or []
    if not t or not merges:
        return None
    status, m = t["status"], merges[-1]                 # the latest merge is the delivery that counts
    if status not in ("IN_PROGRESS", "READY_FOR_DEPLOY"):
        return None
    solution = m.solution or "CODE_FIX"
    base = dict(ap=ap, from_status=status, evidence_kind="MERGE", evidence_ref=m.ref)
    if solution not in AUTOMATIC_SOLUTIONS:
        return Decision(action=WAIT, to_status="READY_FOR_DEPLOY" if status == "IN_PROGRESS" else "CLOSED", **base,
                        reasons=(f"Solution: {solution} is not automatic (only {', '.join(AUTOMATIC_SOLUTIONS)}): a person closes it",))
    if status == "IN_PROGRESS":
        blockers = tuple(facts.rfd_blockers.get(ap) or ())
        if blockers:
            return Decision(action=WAIT, to_status="READY_FOR_DEPLOY", **base, reasons=tuple(f"delivery gate: {b}" for b in blockers))
        return Decision(action=MOVE, to_status="READY_FOR_DEPLOY", **base)
    reasons: list = []
    if _touches_db(m) and ap not in facts.deploys:
        reasons.append("the merge changes db/**: it is not live until its DDL is applied (no DEPLOY event yet)")
    probes = facts.probes.get(ap) or []
    if solution == "DATA_FIX":
        if not probes:
            reasons.append("DATA_FIX needs at least one closing condition (close_condition) to prove the data is right")
        if ap not in facts.cycles:
            reasons.append("DATA_FIX needs a cycle that ran after the merge (no CYCLE event yet)")
    reasons += [f"closing condition fails: {text}" for ok, text in probes if not ok]
    reasons += [f"incident gate: {r}" for r in facts.incident.get(ap) or ()]
    reasons += [f"delivery gate: {b}" for b in facts.delivery_blockers.get(ap) or ()]
    release = t.get("release_version") or merge_release(m)
    if not release:
        reasons.append("no valid release label can be derived from the merge")
    if reasons:
        return Decision(action=WAIT, to_status="CLOSED", **base, solution_type=solution, release=release, reasons=tuple(reasons))
    return Decision(action=CLOSE, to_status="CLOSED", **base, solution_type=solution, release=release)


def is_code_path(path: str) -> bool:
    """True for a file whose change is more work on the code or its tests (not documentation, reports or the attestation). Pure."""
    p = str(path).replace("\\", "/")
    return not (p.startswith(NON_CODE_PREFIXES) or p.lower().endswith(NON_CODE_SUFFIXES))


def decide_event(ap: str, facts: Facts) -> Optional[Decision]:
    """The decision of a ticket that is not being delivered: REOPEN, UNBLOCK or UNDEFER, or None. Pure."""
    t = facts.tickets.get(ap)
    if not t:
        return None
    status = t["status"]
    if status == "CLOSED":
        closed = facts.closed_at.get(ap)
        later = [c for c in facts.citations.get(ap, []) if closed and c.ts > closed and any(is_code_path(p) for p in c.paths)]
        if later and t.get("solution_type") in DELIVERY_SOLUTIONS:
            c = max(later, key=lambda x: x.ts)
            return Decision(ap=ap, action=REOPEN, from_status="CLOSED", to_status="IN_PROGRESS", evidence_kind="COMMIT", evidence_ref=c.ref)
        return None
    if status == "BLOCKED":
        blocker = facts.blockers.get(ap)
        if blocker and (facts.tickets.get(blocker) or {}).get("status") == "CLOSED":
            return Decision(ap=ap, action=UNBLOCK, from_status="BLOCKED", to_status="IN_PROGRESS", evidence_kind="BLOCKER", evidence_ref=blocker)
        return None
    if status == "DEFERRED":
        when = facts.revisits.get(ap)
        if when and facts.today and when <= facts.today:
            return Decision(ap=ap, action=UNDEFER, from_status="DEFERRED", to_status="TODO", evidence_kind="REVISIT", evidence_ref=when.isoformat())
    return None


def decide(facts: Facts) -> list:
    """Every decision of a run (a delivery, or a reopen / unblock / undefer), ordered by ticket id. Pure."""
    aps = sorted(set(facts.merges) | set(facts.citations) | set(facts.blockers) | set(facts.revisits))
    out = []
    for ap in aps:
        d = decide_one(ap, facts) or decide_event(ap, facts)
        if d is not None:
            out.append(d)
    return out


def shadow_note(d: Decision, mode: str = "SHADOW") -> str:
    """The text of the note a SHADOW run writes instead of acting, and the audit text of an applied decision. One line; carries the key so a rerun can recognise it. Pure."""
    head = f"[autopilot {mode}] {d.action} {d.ap}: {d.from_status} -> {d.to_status}" if d.to_status else f"[autopilot {mode}] {d.action} {d.ap}"
    extra = ""
    if d.action == CLOSE:
        extra = f" as {d.solution_type}, release {d.release}"
    why = ("; refused because: " + "; ".join(d.reasons)) if d.reasons else ""
    return f"{head}{extra}. Evidence: {d.evidence_kind} {d.evidence_ref[:12]}{why} (key {d.action}:{d.to_status}:{d.evidence_ref[:12]})"


def note_key(text: str) -> Optional[tuple]:
    """The (action, to_status, evidence ref prefix) of a note written by shadow_note, or None. Pure."""
    m = re.search(r"\(key (\w+):(\w+|None):([^)\s]+)\)", text or "")
    return (m.group(1), m.group(2), m.group(3)) if m else None


def already_noted(d: Decision, notes: list) -> bool:
    """True when one of the existing log texts of the ticket is the shadow note of this same decision. Pure."""
    want = (d.action, str(d.to_status), d.evidence_ref[:12])
    return any(note_key(n) == want for n in notes)


def stale_in_progress(last_activity: dict, today: date, days: int = 14) -> list:
    """[(ap, idle_days)] of the IN_PROGRESS tickets with no activity (commit, log or status event) for `days` or more, most idle first. `last_activity` = {ap: date}. Pure."""
    out = [(ap, (today - d).days) for ap, d in last_activity.items() if (today - d).days >= days]
    return sorted(out, key=lambda x: (-x[1], x[0]))
