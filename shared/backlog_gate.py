# -*- coding: utf-8 -*-
"""
backlog_gate.py -- the pure half of the commit-msg gate (Backlog v2 Phase C slice 2, FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.1 and 6.4.6; NORMAS_BACKLOG.md 4.5).

Shared by the hook (scripts/audit/commit_msg_gate.py, at commit time) and the sync (scripts/audit/backlog_sync.py, afterwards), so both read a commit message the same way
(P#11). No database, no git, no I/O here: every function takes and returns plain values.

* Tokens. `FND-nnnn` (four digits) is a ticket id. A loose spelling of the same thing (`fnd0193`, `FND_0193`, `FND-193`) is MALFORMED. Only the subject and the `Refs:`
  trailer are read; the body is prose and is never scanned.
* Trailer. `Backlog-Gate: <nonce>` is what the hook leaves: a 32-hex nonce bound to the staged tree, or the word `offline` when the backlog could not be reached.
* Verdict. For a commit that cites a ticket: CHECKED (its nonce is in gate_check and the tree it names is the commit's tree), PENDING (`offline`: the sync catches up later, no
  penalty), BYPASS (trailer missing, unknown nonce, or tree mismatch: `--no-verify`, another clone, a forged trailer), EXEMPT (a merge commit, or older than the epoch).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

ID = re.compile(r"\bFND-(\d{4})\b")
LOOSE = re.compile(r"(?i)\bfnd[-_ ]?\d{1,5}\b")
_TRAILER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):[ \t]*(.*)$")
GATE_KEY = "Backlog-Gate"
OFFLINE = "offline"
NONCE = re.compile(r"^[0-9a-f]{32}$")

CHECKED, PENDING, BYPASS, EXEMPT = "CHECKED", "PENDING", "BYPASS", "EXEMPT"
REASON_MISSING, REASON_UNKNOWN, REASON_TREE = "TRAILER_MISSING", "UNKNOWN_NONCE", "TREE_MISMATCH"


def well_formed(text: str) -> list:
    return sorted({f"FND-{m}" for m in ID.findall(text)})


def malformed(text: str) -> list:
    """Loose spellings of a ticket id that are not `FND-nnnn` (sorted, unique)."""
    return sorted({t for t in LOOSE.findall(text) if not re.fullmatch(r"FND-\d{4}", t)})


@dataclass
class Message:
    subject: str = ""
    refs: list = field(default_factory=list)          # ids of the Refs: trailer(s): this commit says it is work on them
    mentions: list = field(default_factory=list)      # refs + ids of the subject
    malformed: list = field(default_factory=list)
    gate: Optional[str] = None                        # value of the Backlog-Gate trailer, if any
    closes: list = field(default_factory=list)        # ids of the Closes: trailer(s): this commit delivers them (Backlog v3 W3); they are ALSO in `refs` (finishing a ticket is work on it)
    solution: Optional[str] = None                    # the value of the Solution: trailer upper-cased (CODE_FIX | DATA_FIX are the only automatic ones; anything else is kept raw so the autopilot can refuse it)


def _trailer_block(lines: list) -> list:
    """The last paragraph when every line of it is a `Key: value` trailer (git's rule, simplified); else []."""
    para: list = []
    for line in reversed(lines):
        if not line.strip():
            if para:
                break
            continue
        para.append(line)
    para.reverse()
    return para if para and all(_TRAILER_LINE.match(ln) for ln in para) and len(para) < len(lines) else []


def parse_message(text: str) -> Message:
    """Subject, Refs, Closes, Solution and Backlog-Gate trailers of a commit message file. Comment lines (`#`) are ignored; the body is not read for ids."""
    lines = [ln.rstrip("\r") for ln in text.split("\n") if not ln.startswith("#")]
    while lines and not lines[0].strip():
        lines.pop(0)
    subject = lines[0] if lines else ""
    refs_text, closes_text, gate, solution = "", "", None, None
    for ln in _trailer_block(lines):
        key, value = _TRAILER_LINE.match(ln).groups()
        if key.lower() == "refs":
            refs_text += f" {value}"
        elif key.lower() == "closes":
            closes_text += f" {value}"
        elif key.lower() == "solution":
            solution = value.strip().upper() or None
        elif key.lower() == GATE_KEY.lower():
            gate = value.strip() or None
    closes = well_formed(closes_text)
    refs = sorted(set(well_formed(refs_text)) | set(closes))
    return Message(subject=subject, refs=refs, mentions=sorted(set(refs) | set(well_formed(subject))),
                   malformed=sorted(set(malformed(subject)) | set(malformed(refs_text)) | set(malformed(closes_text))), gate=gate, closes=closes, solution=solution)


def classify(gate: Optional[str], tree: str, parents: int, nonces: dict) -> tuple:
    """(verdict, reason) of ONE commit that cites a ticket. `gate`: its Backlog-Gate trailer value (None = absent); `tree`: its tree hash; `parents`: how many
    parents it has; `nonces`: {nonce: tree_hash} read from backlog.gate_check."""
    if parents > 1:
        return EXEMPT, "merge commit"
    if not gate:
        return BYPASS, REASON_MISSING
    if gate == OFFLINE:
        return PENDING, "the hook could not reach the backlog"
    if gate not in nonces:
        return BYPASS, REASON_UNKNOWN
    if nonces[gate] != tree:
        return BYPASS, REASON_TREE
    return CHECKED, "nonce and tree match"


def parse_epoch(value: str) -> Optional[datetime]:
    """BACKLOG_HOOK_EPOCH ('' = the gate is not armed) as an aware datetime, or None. Raises ValueError for a non-empty value that is not an ISO timestamp with offset."""
    value = (value or "").strip()
    if not value:
        return None
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        raise ValueError(f"BACKLOG_HOOK_EPOCH {value!r} has no UTC offset (e.g. 2026-11-02T09:00:00+01:00)")
    return ts


def verdicts(commits: list, nonces: dict, epoch: Optional[datetime], known: set) -> list:
    """[(commit, ap_id, verdict, reason)] for every commit of `commits` that cites a ticket in `known`. Before the epoch (or with no epoch) a commit is EXEMPT: the gate
    judges only what was committed after the hook was meant to be installed. `commits`: dicts of backlog_sync.parse_log (needs 'mentions', 'gate', 'tree', 'parents', 'author_ts')."""
    out = []
    for c in commits:
        cited = [ap for ap in c["mentions"] if ap in known]
        if not cited:
            continue
        if epoch is None or c["author_ts"] < epoch:
            v, why = EXEMPT, "before the epoch" if epoch else "gate not armed"
        else:
            v, why = classify(c.get("gate"), c.get("tree", ""), int(c.get("parents", 1)), nonces)
        out += [(c, ap, v, why) for ap in cited]
    return out


_CHORE = re.compile(r"^chore(\([^)]*\))?!?:", re.I)


def ticket_exemption(subject: str, branch: str = "", merging: bool = False) -> Optional[str]:
    """Why a commit may carry no ticket when BACKLOG_REQUIRE_TICKET_ENABLED is on (NORMAS_BACKLOG.md 4.5, owner policy 2026-10-31): a merge, a revert, a `chore` commit
    (conventional subject `chore:` / `chore(scope):`) or any commit on a `chore/` branch. None = not exempt."""
    if merging or subject.startswith("Merge "):
        return "merge"
    if subject.startswith("Revert "):
        return "revert"
    if _CHORE.match(subject):
        return "chore"
    if branch.startswith("chore/"):
        return "chore branch"
    return None


def trailer_for(nonce: Optional[str]) -> str:
    """The trailer line the hook appends: the nonce, or `offline` when there is none."""
    return f"{GATE_KEY}: {nonce if nonce else OFFLINE}"
