#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_modules.py -- derives which modules each open ticket is about, keeps the MODULE clusters, and measures them (Backlog v3 W4b, FND-0360; the rules are in shared/backlog_modules.py and
shared/backlog_clusters.py).

    python scripts/audit/backlog_modules.py            # DRY RUN: counts what it would record and the cluster events it would write; writes nothing
    python scripts/audit/backlog_modules.py --apply    # records the new facts through backlog.sp_record_module and keeps the clusters (the autopilot's login)
    python scripts/audit/backlog_modules.py --clusters # READ-ONLY: the clusters ranked by open members x priority, with why each ticket is in one
    python scripts/audit/backlog_modules.py --measure  # READ-ONLY: precision and recall against the OVERLAPS decisions on record, the top 10 clusters, and what the T0 collision rule would gain

Sources, all automatic: TOUCHED from git (the files of every commit that cites a ticket: ops_event COMMIT facts carry only the subject, so the files are read with `git diff-tree`), and CITED
(proposals only) from the title, the description, the progress logs and the object name. A test maps to the module it tests; registries, documents and configuration never count (see the rules).
The autopilot run (scripts/audit/backlog_autopilot.py --apply) calls this at the end of every run, so nobody starts it. It is idempotent: a fact already current is not sent, and the database
refuses to write again a proposal that an agent or a person retracted. It writes ONLY through FONDOS_BACKLOG_PG_DSN_AUTOPILOT (role backlog_autopilot) and does nothing, with exit 0, before the
`modules` phase is deployed.

The clusters follow BACKLOG_MODULE_CLUSTERS_MODE (shared/config.py): OFF never, ON always, AUTO only while the clusters prove themselves (>= 20 decided pairs, precision >= 70 %, recall above the
text baseline), measured at most once a day and cached in out/backlog_modules/proof.json. Until the proof holds nothing is written and no "module mates" appear.
Exit codes: 0 ok, 2 git or the backlog could not be read.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from shared import backlog_clusters as cl  # noqa: E402
from shared import backlog_modules as bm  # noqa: E402

ACTOR = "system:autopilot"
RC_OK, RC_USAGE = 0, 2
PROOF_CACHE = ROOT / "out" / "backlog_modules" / "proof.json"


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


def sources(bc, git=_git, open_only: bool = True) -> dict:
    """What the sources say now: {'desired': {ticket: [fact]}, 'tickets', 'files': {sha: [paths]}, 'commits_of': {ticket: [sha]}, 'tracked', 'by_base'}. Reads the backlog and git only."""
    tracked, by_base = bm.index_tracked(git("ls-files").splitlines())
    tickets = {t["action_point_id"]: t for t in bc.list_tickets(open_only=open_only)}
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
    return {"desired": desired, "tickets": tickets, "files": files, "commits_of": commits_of, "tracked": tracked, "by_base": by_base}


def gather(bc, git=_git) -> dict:
    """{ticket: [fact]} the sources say now, for the OPEN tickets. Reads the backlog and git only."""
    return sources(bc, git)["desired"]


def as_rows(desired: dict) -> list:
    """The facts of `gather`, in the shape of the rows of backlog.v_ticket_module. Pure."""
    return [{"action_point_id": ap, "module_path": f["module"], "role": f["role"], "symbol": None, "source": f["source"]} for ap, fs in desired.items() for f in fs]


# ---------------------------------------------------------------- the facts

def facts_step(bc, apply: bool, git, out) -> dict:
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


# ---------------------------------------------------------------- the proof

def measure(bc, git=_git) -> dict:
    """The clusters of ALL tickets (closed ones carry the decisions on record) from the sources in memory, and how good they are: {'ev', 'clusters', 'tickets', 'rows', 'sim'}. Read-only."""
    src = sources(bc, git, open_only=False)
    tickets = src["tickets"]
    open_ids = {a for a, t in tickets.items() if t["status"] != "CLOSED"}
    rows = as_rows(src["desired"])
    if bc.modules_store_present():
        rows += [r for r in bc.list_ticket_modules() if r["source"] == "DECLARED"]
    universe = set(tickets)
    clusters = cl.derive(rows, universe, open_ids=open_ids)
    text = cl.derive(rows, universe, roles=("CITED",), open_ids=open_ids)
    links = bc.list_links()
    pos = {frozenset((l["from_id"], l["to_id"])) for l in links if l["link_type"] == "OVERLAPS" and l["status"] == "CONFIRMED"}
    neg = {frozenset((l["from_id"], l["to_id"])) for l in links if l["link_type"] == "OVERLAPS" and l["status"] == "REJECTED"}
    ev = cl.evaluate(clusters, text, pos, neg, universe)
    by_sha: dict = defaultdict(list)
    for ap, shas in src["commits_of"].items():
        for sha in shas:
            by_sha[sha].append(ap)
    commits = [{"sha": sha, "tickets": sorted(ts), "modules": {m for p in src["files"][sha] if (m := bm.logical_module(p, src["tracked"], src["by_base"]))}}
               for sha, ts in sorted(by_sha.items()) if sha in src["files"]]
    in_progress = {a for a, t in tickets.items() if t["status"] == "IN_PROGRESS"}
    cited = {ap: {f["module"] for f in fs if f["role"] == "CITED"} for ap, fs in src["desired"].items()}
    sim = cl.simulate_t0(commits, cited, cl.holdings(rows, in_progress), in_progress, pos, neg)
    return {"ev": ev, "clusters": clusters, "tickets": tickets, "rows": rows, "sim": sim}


def proof(bc, git=_git, cache: Path = None) -> dict:
    """The evidence of `measure`, at most once a day (cached in out/backlog_modules/proof.json): the cost is one `git diff-tree` per commit on record. A broken cache is measured again."""
    cache = cache or PROOF_CACHE
    today = date.today().isoformat()
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("day") == today:
            return c["ev"]
    except (OSError, ValueError, KeyError):
        pass
    ev = measure(bc, git)["ev"]
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"day": today, "ev": ev}), encoding="utf-8")
    except OSError:
        pass
    return ev


def _config_mode() -> str:
    try:
        from shared import config
        return str(getattr(config, "BACKLOG_MODULE_CLUSTERS_MODE", "OFF")).upper()
    except Exception:                                                        # noqa: BLE001 -- no configuration, no clusters
        return "OFF"


# ---------------------------------------------------------------- the clusters

def cluster_step(bc, apply: bool, git=_git, out=print, mode: str = None, cache: Path = None) -> dict:
    """Keep the MODULE groups equal to the clusters of the OPEN tickets, while the mode allows it. Returns {'active', 'why', 'clusters', 'planned', 'written', 'refused'}."""
    mode = (mode or _config_mode()).upper()
    on, why = cl.active(mode, proof(bc, git, cache) if mode == "AUTO" else None)
    out(f"clusters: {why}")
    res = {"active": on, "why": why, "clusters": 0, "planned": 0, "written": 0, "refused": 0}
    if not on:
        return res
    tickets = {t["action_point_id"]: t for t in bc.list_tickets()}
    status = {a: t["status"] for a, t in tickets.items()}
    open_ids = {a for a, s in status.items() if s != "CLOSED"}
    clusters = cl.derive(bc.list_ticket_modules(), open_ids, open_ids=open_ids)
    groups, current, rejected = cl.group_state(bc.list_module_group_events(), status)
    ops = cl.plan_memberships(cl.assign_identity(clusters, groups), current, rejected, status)
    res.update(clusters=len(clusters), planned=len(ops))
    out(f"clusters: {len(clusters)} cluster(s) of open tickets; {len(ops)} group event(s) planned")
    if not apply:
        return res
    skipped: set = set()
    for op in ops:
        if op[1] in skipped:
            continue
        try:
            if op[0] == "CREATE":
                bc.create_group(op[1], "MODULE", op[2], actor=ACTOR)
            else:
                bc.group_member(op[1], op[2], op[0], op[3], actor=ACTOR)
            res["written"] += 1
        except Exception as exc:                                             # noqa: BLE001 -- never echo a DSN; a refused event leaves the rest running
            res["refused"] += 1
            out(f"clusters: {op[0]} {op[1]} refused ({type(exc).__name__})")
            if op[0] == "CREATE":
                skipped.add(op[1])
    out(f"clusters: wrote {res['written']}, refused {res['refused']}")
    return res


def run(bc, apply: bool = False, git=_git, out=print, clusters: bool = False, mode: str = None, cache: Path = None) -> dict:
    """Gather, subtract what is current and (with apply) record the rest; with `clusters`, then keep the MODULE groups. Returns {'present', 'desired', 'new', 'recorded', 'unchanged'} and, with
    `clusters`, 'clusters' (see cluster_step); 'present' is False before the phase is deployed."""
    if not bc.modules_store_present():
        out("the modules phase is not deployed yet: nothing to do")
        return {"present": False, "desired": 0, "new": 0, "recorded": 0, "unchanged": 0}
    res = facts_step(bc, apply, git, out)
    if clusters:
        try:
            res["clusters"] = cluster_step(bc, apply, git, out, mode, cache)
        except Exception as exc:                                             # noqa: BLE001 -- the clusters never take the facts down with them
            out(f"clusters: not kept ({type(exc).__name__})")
            res["clusters"] = {"active": False, "why": f"error {type(exc).__name__}"}
    return res


# ---------------------------------------------------------------- the reports (read-only)

def show_clusters(bc, git=_git, out=print, top: int = 15) -> None:
    """The clusters of the open tickets from the recorded facts (or from the sources when the phase is not deployed), the biggest unit of work first."""
    tickets = {t["action_point_id"]: t for t in bc.list_tickets(open_only=True)}
    open_ids = set(tickets)
    rows = bc.list_ticket_modules() if bc.modules_store_present() else as_rows(gather(bc, git))
    for c, opens, weight in cl.rank(cl.derive(rows, open_ids, open_ids=open_ids), tickets)[:top]:
        out(f"{c['principal']}: {len(opens)} open ticket(s), weight {weight}{', HOTSPOT' if c['hotspot'] else ''}, {len(c['modules'])} shared module(s)")
        for a in opens:
            out(f"    {a} [{tickets[a]['status']}] {str(tickets[a]['title'])[:60]} -- {c['why'][a]}")


def show_measure(bc, git=_git, out=print) -> dict:
    res = measure(bc, git)
    ev, sim = res["ev"], res["sim"]
    open_n = sum(1 for t in res["tickets"].values() if t["status"] != "CLOSED")
    out(f"measure: {len(res['tickets'])} ticket(s), {open_n} open; {len(res['clusters'])} cluster(s) from {len(res['rows'])} fact(s)")
    fmt = lambda v: "n/a" if v is None else f"{v:.0%}"                      # noqa: E731
    out(f"precision {fmt(ev['precision'])} on {ev['sample']} decided pair(s) (bar {cl.MIN_PRECISION:.0%} on {cl.MIN_SAMPLE}); recall {fmt(ev['recall'])} of {ev['positives']} confirmed OVERLAPS, text baseline {fmt(ev['recall_text'])}")
    out(f"mode AUTO would be: {cl.active('AUTO', ev)[1]}")
    out("top 10 clusters (information only):")
    for c in res["clusters"][:10]:
        opens = sum(1 for a in c["members"] if res["tickets"][a]["status"] != "CLOSED")
        out(f"    {len(c['members'])} ticket(s), {opens} open, on {c['principal']}{' (hotspot)' if c['hotspot'] else ''}: {', '.join(c['members'][:6])}{' ...' if len(c['members']) > 6 else ''}")
    out(f"T0 simulation over {sim['commits']} recorded commit(s) against the tickets in progress now: the text rule finds {sim['text_pairs']} pair(s), the facts {sim['fact_pairs']}; "
        f"{sim['added']} only the facts find ({sim['added_confirmed']} confirmed OVERLAPS, {sim['added_rejected']} rejected, {sim['added_undecided']} undecided)")
    return res


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="record the new facts and keep the clusters (default: only count them)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--clusters", action="store_true", help="read-only: the clusters of the open tickets, ranked")
    g.add_argument("--measure", action="store_true", help="read-only: precision, recall, the top 10 clusters and the T0 simulation")
    args = ap.parse_args(argv)
    os.environ["FONDOS_ACTOR"] = ACTOR if args.apply else os.environ.get("FONDOS_ACTOR", ACTOR)
    import shared.config  # noqa: F401 -- loads .env
    from shared import backlog_client as bc
    try:
        if args.clusters:
            show_clusters(bc)
        elif args.measure:
            show_measure(bc)
        else:
            run(bc, apply=args.apply, clusters=True)
    except Exception as exc:                                  # noqa: BLE001 -- never echo a DSN
        print(f"error: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return RC_USAGE
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
