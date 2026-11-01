# -*- coding: utf-8 -*-
"""
backlog_impact.py -- which open tickets a merge may affect, and the one note each gets (Backlog v3 W4, FND-0359; doc/especificacion/ACTION_PLAN_BACKLOG_V3.md section W4).

Pure rules; the runner (scripts/audit/backlog_impact.py) reads git and the backlog and appends the notes as the autopilot. No database, no git, no network here.

A merge that cites tickets (`Refs:` / `Closes:`) changes some modules. An OPEN ticket that the merge does not cite gets ONE note when the change may touch what it is about, so whoever works on it
re-checks whether it is resolved or regressed. Decided here and nowhere else (P#11):
  1. the signals, strongest first: LINK (a confirmed IMPACTS / OVERLAPS link with a ticket the merge cites), TARGET (the ticket declared that module as the one it will change), PATH (its text cites a changed
     module: the rule the collision gate already uses, with the same hub rule) and TOUCHED (a commit of the ticket changed it). TARGET and TOUCHED are module FACTS and count only while the clusters have
     proved their precision (`facts_ok`), the same bar as the MODULE groups: an unproven signal must not make noise;
  2. the fatigue guard: a sweeping merge (more than MERGE_SWEEP_MODULES modules) notes nothing, a merge notes at most MAX_NOTES_PER_MERGE tickets (strongest first) and a run at most MAX_NOTES_PER_RUN;
  3. the note and its key, which makes a rerun write nothing.
It is a NOTE, never a review flag: a flag blocks the ticket's delivery until someone acknowledges it, which would turn every correlation into an approval queue. Nothing here is a gate.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from shared import backlog_clusters as cl
from shared import backlog_modules as bm

IMPACT_WINDOW_DAYS = 2            # a merge older than this is not noted (the first run must not flood the backlog with history)
MERGE_SWEEP_MODULES = 12          # a merge that changed more modules than this says nothing about any one of them (calibrated by --replay)
MAX_NOTES_PER_MERGE = 5
MAX_WEAK_NOTES_PER_MERGE = 1      # of those, at most this many may rest on the weakest signal (TOUCHED)
MAX_NOTES_PER_RUN = 25            # tickets noted in one run
IMPACT_COOLDOWN_HOURS = 24        # a ticket is told at most once in this time, however many merges touch what it is about
SCORE = {"LINK": 3, "TARGET": 2, "PATH": 2, "TOUCHED": 1}
LINK_TYPES = ("IMPACTS", "OVERLAPS")


def merges_from(cited: dict, since: datetime) -> list:
    """[{'sha', 'ts', 'paths', 'tickets'}] the merges that cite tickets, from the autopilot's `cited` = {ticket: [Citation(ref, ts, paths)]}, newer than `since`, oldest first. Pure."""
    by: dict = {}
    for ticket, cites in cited.items():
        for c in cites:
            if c.ts < since:
                continue
            m = by.setdefault(c.ref, {"sha": c.ref, "ts": c.ts, "paths": tuple(c.paths), "tickets": set()})
            m["tickets"].add(ticket)
    return sorted(by.values(), key=lambda m: (m["ts"], m["sha"]))


def merge_modules(paths, tracked: set, by_base: dict) -> set:
    """The modules a merge changed (tests fold into their module; registries and documents are left out). Pure."""
    return {m for p in paths if (m := bm.logical_module(p, tracked, by_base))}


def key_of(sha: str) -> str:
    return f"IMPACT:{sha[:12]}"


def already(sha: str, logs: list) -> bool:
    """True when a note of this merge is already in the ticket's logs. Pure."""
    k = key_of(sha)
    return any(k in (t or "") for t in logs)


def plan_notes(merge: dict, modules: set, open_ids: set, links: list, rows: list, facts_ok: bool, max_notes: int = MAX_NOTES_PER_MERGE) -> list:
    """[(ticket, signal, evidence, changed modules it holds)] the open tickets a merge may affect, strongest first, at most `max_notes`. `links` = [(from, to, type)] confirmed; `rows` = the current
    module facts. The tickets the merge cites are never noted, and neither are closed ones. Pure."""
    if not modules or len(modules) > MERGE_SWEEP_MODULES:
        return []
    own = set(merge["tickets"])
    cand: dict = {}

    def offer(ticket: str, signal: str, evidence: str, mod: Optional[str]) -> None:
        if ticket in own or ticket not in open_ids:
            return
        cur = cand.setdefault(ticket, {"signal": signal, "evidence": evidence, "mods": set()})
        if SCORE[signal] > SCORE[cur["signal"]]:
            cur["signal"], cur["evidence"] = signal, evidence
        if mod:
            cur["mods"].add(mod)

    for a, b, t in links:
        if t in LINK_TYPES:
            if a in own:
                offer(b, "LINK", f"{t} link with {a}", None)
            if b in own:
                offer(a, "LINK", f"{t} link with {b}", None)
    cited_by: dict = defaultdict(set)
    for r in rows:
        if r["role"] == "CITED" and r["action_point_id"] in open_ids:
            cited_by[r["module_path"]].add(r["action_point_id"])
    for m in sorted(modules):
        if len(cited_by.get(m, ())) < cl.TEXT_HUB_TICKETS:
            for t in sorted(cited_by.get(m, ())):
                offer(t, "PATH", f"its text cites {m}", m)
    if facts_ok:
        held = cl.holdings(rows, open_ids)
        hubs = cl.hub_modules(held, len(open_ids))
        for t in sorted(held):
            for m in sorted(modules & set(held[t]) - hubs):
                offer(t, "TARGET" if held[t][m]["role"] == "TARGET" else "TOUCHED", f"{'declared as its target' if held[t][m]['role'] == 'TARGET' else 'changed by its commits'}: {m}", m)
    ranked = sorted(cand.items(), key=lambda kv: (-SCORE[kv[1]["signal"]], -len(kv[1]["mods"]), kv[0]))
    out, weak = [], 0
    for t, v in ranked:
        if len(out) >= max_notes:
            break
        if v["signal"] == "TOUCHED":                                         # the weakest signal fills at most MAX_WEAK_NOTES_PER_MERGE places: it is the one that would flood
            if weak >= MAX_WEAK_NOTES_PER_MERGE:
                continue
            weak += 1
        out.append((t, v["signal"], v["evidence"], sorted(v["mods"])))
    return out


def note_text(merge: dict, modules: set, ticket: str, signal: str, evidence: str) -> str:
    """The one note a ticket gets for a merge; it carries the key that makes a rerun write nothing. Pure."""
    first = sorted(modules)[0]
    more = f" (+{len(modules) - 1} more)" if len(modules) > 1 else ""
    return (f"[impact] merge {merge['sha'][:12]} for {', '.join(sorted(merge['tickets']))} changed {first}{more}: re-check whether this ticket is resolved or regressed. "
            f"Signal {signal}: {evidence}. ({key_of(merge['sha'])})")


def combined_text(items: list) -> str:
    """The ONE note a ticket gets from a run, however many merges may have affected it: `items` = [(merge, modules, signal, evidence)] oldest first. The note names the merges, the first changed module,
    the strongest signal and carries the key of EVERY merge it covers, so a rerun writes nothing for any of them. Pure."""
    if len(items) == 1:
        merge, modules, signal, evidence = items[0]
        return note_text(merge, modules, "", signal, evidence)
    mods = set().union(*[i[1] for i in items])
    _m, _mods, signal, evidence = max(items, key=lambda i: SCORE[i[2]])
    first = sorted(mods)[0]
    more = f" (+{len(mods) - 1} more)" if len(mods) > 1 else ""
    tickets = sorted(set().union(*[i[0]["tickets"] for i in items]))
    return (f"[impact] {len(items)} merges ({', '.join(i[0]['sha'][:12] for i in items)}) for {', '.join(tickets)} changed {first}{more}: re-check whether this ticket is resolved or regressed. "
            f"Strongest signal {signal}: {evidence}. ({') ('.join(key_of(i[0]['sha']) for i in items)})")


def recently_noted(rows: list, now: datetime) -> bool:
    """True when the ticket already got an [impact] note in the last IMPACT_COOLDOWN_HOURS: a ticket is told once a day, not once per merge. `rows` = its log rows ({'comment', 'log_ts'}). Pure."""
    since = now - timedelta(hours=IMPACT_COOLDOWN_HOURS)
    return any((r.get("comment") or "").startswith("[impact]") and r.get("log_ts") is not None and r["log_ts"] >= since for r in rows)


def distribution(counts: list) -> dict:
    """{'merges', 'median', 'p90', 'max', 'silent'}: notes per merge over a replay (silent = the share of merges that note nothing). Pure."""
    s = sorted(counts)
    if not s:
        return {"merges": 0, "median": 0, "p90": 0, "max": 0, "silent": 0.0}
    return {"merges": len(s), "median": s[len(s) // 2], "p90": s[min(len(s) - 1, int(len(s) * 0.9))], "max": s[-1], "silent": sum(1 for n in s if n == 0) / len(s)}
