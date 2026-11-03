# shared/backlog_proposals.py
# -*- coding: utf-8 -*-
"""backlog_proposals.py -- what opening a ticket proposes (FND-0378; plan ACTION_PLAN_BACKLOG_REFACTORING, Revision 2, step 2.8a; owner requirement: "open ticket: a single call; the system proposes relationships
and groupings"). Pure rules; the reads live in shared/backlog_client.open_ticket. A proposal is information, never a gate: nothing is recorded as a link (the text ranking has no threshold that separates a neighbour
from a non-neighbour, scripts/audit/backlog_links.py NEW_FLOOR), nobody approves anything, and a person or agent decides later with record_link_events / record_modules."""
from __future__ import annotations

from typing import Optional

TOP_NEIGHBOURS = 3
MAX_MODULES = 8
MAX_SAME_MODULE = 6
ROLES = ("TARGET", "TOUCHED")          # the roles that put a ticket "on" a module (CITED is only a proposal and never forms a cluster)


def same_module_tickets(modules: set, rows: list, open_ids: set, exclude: Optional[str] = None) -> list:
    """The open tickets that change one of `modules` (their TARGET or TOUCHED facts, `rows` = backlog.v_ticket_module), the one sharing the most modules first, then by id. `exclude` is the new ticket. Pure."""
    shared: dict = {}
    for r in rows:
        a = r.get("action_point_id")
        if a == exclude or a not in open_ids or r.get("role") not in ROLES or r.get("module_path") not in modules:
            continue
        shared.setdefault(a, set()).add(r["module_path"])
    return [a for a, _m in sorted(shared.items(), key=lambda kv: (-len(kv[1]), kv[0]))][:MAX_SAME_MODULE]


def note_text(neighbours: list, modules: list, same_module: list) -> Optional[str]:
    """The one log line written on a new ticket: the closest open tickets by text (a rank, not a verdict), the modules its text cites and the open tickets that change the same modules. None when there is nothing to
    say. `neighbours` = [(score, ticket id, status, title)]. Pure."""
    parts = []
    if neighbours:
        parts.append("closest open tickets by text (a rank, not a verdict): " + "; ".join(f"{t} [{s}] {str(title)[:60]} ({score:.2f})" for score, t, s, title in neighbours[:TOP_NEIGHBOURS]))
    if modules:
        parts.append("modules its text cites: " + ", ".join(sorted(modules)[:MAX_MODULES]))
    if same_module:
        parts.append("open tickets that change the same module(s): " + ", ".join(same_module[:MAX_SAME_MODULE]) + " (a MOD.* group forms when the clusters prove their precision)")
    if not parts:
        return None
    return "[proposals at opening] " + " | ".join(parts) + ". Informational: confirm or retract with record_link_events / record_modules; nothing was recorded as a link."
