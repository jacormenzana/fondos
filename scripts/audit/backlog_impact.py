#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_impact.py -- puts the open tickets a merge may affect on notice, with ONE note each (Backlog v3 W4, FND-0359; the rules are in shared/backlog_impact.py).

    python scripts/audit/backlog_impact.py --replay [--days 45]   # READ-ONLY: how many notes each recent merge would have produced (the fatigue guard), against today's open tickets

The autopilot run (scripts/audit/backlog_autopilot.py --apply) calls `run` after the module facts, with the merges it just read, so nobody starts it. The note is appended as the autopilot through the
scoped routine (a NOTE, never a review flag: a flag blocks the ticket's delivery until someone acknowledges it, which would be an approval queue). It does nothing, with no error, while
BACKLOG_IMPACT_NOTES_ENABLED is False or the `modules` phase is not deployed. A merge older than IMPACT_WINDOW_DAYS is not noted, a rerun writes nothing (the note carries the merge as its key),
and a run writes at most MAX_NOTES_PER_RUN notes. Exit codes: 0 ok, 2 git or the backlog could not be read.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "audit"))
from shared import backlog_clusters as cl  # noqa: E402
from shared import backlog_impact as ib  # noqa: E402
from shared import backlog_modules as bm  # noqa: E402

ACTOR = "system:autopilot"
RC_OK, RC_USAGE = 0, 2


def _git(*args, cwd: Path = ROOT) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def _enabled() -> bool:
    try:
        from shared import config
        return bool(getattr(config, "BACKLOG_IMPACT_NOTES_ENABLED", False))
    except Exception:                                                        # noqa: BLE001 -- no configuration, no notes
        return False


def _inputs(bc, git, mode, cache, desired):
    """(open ids, confirmed links as (from, to, type), module rows, facts_ok): what the rules need, read once. Read-only."""
    import backlog_modules as modules_run
    open_ids = {t["action_point_id"] for t in bc.list_tickets(open_only=True)}
    links = [(l["from_id"], l["to_id"], l["link_type"]) for l in bc.list_links(status="CONFIRMED") if l["link_type"] in ib.LINK_TYPES]
    rows = bc.list_ticket_modules()
    if desired is not None:
        rows = bm.refresh_evidence(rows, desired)
    mode = (mode or modules_run._config_mode()).upper()
    facts_ok, _why = cl.active(mode, modules_run.proof(bc, git, cache) if mode == "AUTO" else None)
    return open_ids, links, rows, facts_ok


def run(bc, cited: dict, apply: bool = False, git=_git, out=print, now: datetime = None, enabled: bool = None, desired: dict = None, mode: str = None, cache: Path = None) -> dict:
    """Note the open tickets that the merges in `cited` ({ticket: [Citation]}) may affect. Returns {'enabled', 'present', 'merges', 'planned', 'written', 'skipped'}."""
    res = {"enabled": _enabled() if enabled is None else enabled, "present": True, "merges": 0, "planned": 0, "written": 0, "skipped": 0}
    if not res["enabled"]:
        return res
    if not bc.modules_store_present():
        res["present"] = False
        return res
    now = now or datetime.now(timezone.utc)
    merges = ib.merges_from(cited, now - timedelta(days=ib.IMPACT_WINDOW_DAYS))
    res["merges"] = len(merges)
    if not merges:
        return res
    open_ids, links, rows, facts_ok = _inputs(bc, git, mode, cache, desired)
    tracked, by_base = bm.index_tracked(git("ls-files").splitlines())
    plans = []
    for m in merges:
        mods = ib.merge_modules(m["paths"], tracked, by_base)
        plans += [(m, mods, t, sig, ev) for t, sig, ev, _held in ib.plan_notes(m, mods, open_ids, links, rows, facts_ok)]
    by_ticket: dict = {}
    for m, mods, t, sig, ev in plans:
        by_ticket.setdefault(t, []).append((m, mods, sig, ev))
    res.update(planned=len(plans), tickets=len(by_ticket), cooldown=0)
    logs: dict = {}
    for row in bc.list_logs(ids=sorted(by_ticket)) if by_ticket else []:
        logs.setdefault(row["action_point_id"], []).append(row)
    noted = 0
    for t in sorted(by_ticket):
        items = [i for i in by_ticket[t] if not ib.already(i[0]["sha"], [r["comment"] for r in logs.get(t, [])])]
        res["skipped"] += len(by_ticket[t]) - len(items)
        if not items:
            continue
        if ib.recently_noted(logs.get(t, []), now):
            res["cooldown"] += 1
            continue
        if noted >= ib.MAX_NOTES_PER_RUN:
            break
        text = ib.combined_text(items)
        if apply:
            try:
                bc.append_log(t, ACTOR, text)
            except bc.BacklogUnavailable as exc:                             # no login, no connection or the live-write guard (LiveWriteForbidden): a configuration problem; nothing more is written
                out(f"impact: nothing more written ({type(exc).__name__})")
                res["unavailable"] = True
                break
            logs.setdefault(t, []).append({"comment": text, "log_ts": now})
            res["written"] += 1
        noted += 1
        out(f"  {t}: impact note for {len(items)} merge(s), strongest signal {max(items, key=lambda i: ib.SCORE[i[2]])[2]}" + ("" if apply else " -- dry run"))
    out(f"impact: {res['merges']} merge(s) in the last {ib.IMPACT_WINDOW_DAYS} days affect {res['tickets']} ticket(s); {res['written']} note(s) written, {res['skipped']} merge(s) already noted, "
        f"{res['cooldown']} ticket(s) told within {ib.IMPACT_COOLDOWN_HOURS} h")
    return res


def replay(bc, cited: dict, git=_git, mode: str = None, cache: Path = None, out=print) -> dict:
    """Read-only: the notes each merge in `cited` WOULD get against today's open tickets (no window, no dedup), and their distribution: the fatigue guard. Today's open tickets overstate the past, so
    the figures are an upper bound."""
    open_ids, links, rows, facts_ok = _inputs(bc, git, mode, cache, None)
    tracked, by_base = bm.index_tracked(git("ls-files").splitlines())
    merges = ib.merges_from(cited, datetime(1970, 1, 1, tzinfo=timezone.utc))
    counts, by_signal, quiet = [], {}, 0
    for m in merges:
        mods = ib.merge_modules(m["paths"], tracked, by_base)
        notes = ib.plan_notes(m, mods, open_ids, links, rows, facts_ok)
        counts.append(len(notes))
        quiet += 1 if len(mods) > ib.MERGE_SWEEP_MODULES else 0
        for _t, sig, _e, _h in notes:
            by_signal[sig] = by_signal.get(sig, 0) + 1
    dist = ib.distribution(counts)
    out(f"replay: {dist['merges']} merge(s) that cite tickets; notes per merge: median {dist['median']}, p90 {dist['p90']}, max {dist['max']}; {dist['silent']:.0%} of the merges note nothing "
        f"({quiet} sweeping, over {ib.MERGE_SWEEP_MODULES} modules); module facts {'ON' if facts_ok else 'off (clusters unproven)'}; by signal {dict(sorted(by_signal.items()))}")
    return {"dist": dist, "by_signal": by_signal, "sweeping": quiet, "facts_ok": facts_ok}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", action="store_true", required=True, help="read-only: the notes each recent merge would produce")
    ap.add_argument("--days", type=int, default=45, help="how far back the merges are read (default 45)")
    args = ap.parse_args(argv)
    os.environ.setdefault("FONDOS_ACTOR", ACTOR)
    import shared.config  # noqa: F401 -- loads .env
    from shared import backlog_client as bc
    try:
        import backlog_autopilot as autopilot
        _delivered, cited = autopilot.scan_merges("origin/master", args.days)
        replay(bc, cited)
    except Exception as exc:                                  # noqa: BLE001 -- never echo a DSN
        print(f"error: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return RC_USAGE
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
