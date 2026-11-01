# -*- coding: utf-8 -*-
"""
backlog_clusters.py -- clusters of tickets that change the same module, derived (Backlog v3 W4b slice 2, FND-0360; doc/especificacion/ACTION_PLAN_BACKLOG_V3.md section W4b).

Pure rules; the runner (scripts/audit/backlog_modules.py) reads the module facts and the groups and writes through backlog.sp_create_group / sp_group_member. No database, no git, no network here.

The facts come from shared/backlog_modules.py (slice 1): TARGET (declared) and TOUCHED (a commit changed it) make a cluster; CITED (text) only proposes and never does. Decided here and nowhere else (P#11):
  1. the edge between two tickets: one weighted signal per shared module, explainable (the modules are kept as evidence);
  2. the hub rule: a module that more than HUB_SHARE of the tickets AND more than HUB_MIN_TICKETS tickets hold is shared infrastructure, not shared logic;
  3. the clusters: connected components of the edges, split by removing the fewest weakest edges when more than MAX_OPEN_MEMBERS OPEN tickets are in one (closed tickets never count);
     a component that rests on ONE module is never split: it is a hotspot (the nine tickets of audit_incident.py are one unit of work);
  4. the identity of a group: anchored on the open tickets it already holds, never renamed;
  5. the plan of ADD / REMOVE events, where a rejection by an agent or a person stands;
  6. the proof that the clusters are worth turning on: precision against the OVERLAPS decisions already on record. Mode AUTO forms clusters only while that proof holds.
Nothing here is a gate and nothing needs a person's approval.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from itertools import combinations
from typing import Optional

# Signal weights (the strongest signal of a pair is its score). Calibrated by scripts/audit/backlog_modules.py --measure (NORMAS_BACKLOG.md section 6 rule).
W_SYMBOL, W_TARGET, W_MIXED, W_TOUCHED = 1.0, 0.8, 0.7, 0.6
EDGE_MIN = 0.6                    # an edge below this score does not link two tickets
MAX_OPEN_MEMBERS = 8              # OPEN tickets of one cluster; closed members never count
HUB_SHARE = 0.15                  # a hub is held by MORE than this share of the tickets ...
HUB_MIN_TICKETS = 5               # ... AND by MORE than this many distinct tickets
TEXT_HUB_TICKETS = 5              # the existing T0 rule: a file cited by this many open tickets or more is a hub (db/pg_backlog/90_collision.sql)
NAME_MAX = 64
MIN_SAMPLE = 20                   # decided pairs inside the clusters needed before the precision means anything
MIN_PRECISION = 0.70
PRIORITY_WEIGHT = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
MODES = ("OFF", "AUTO", "ON")
SYSTEM = "system:"


# ---------------------------------------------------------------- the facts a ticket holds

def holdings(rows: list, universe: set, roles: tuple = ("TARGET", "TOUCHED")) -> dict:
    """{ticket: {module: {'role': 'TARGET'|'TOUCHED', 'symbols': set}}} from the current module facts of the tickets in `universe`. Only the given roles count; any role other than
    TARGET is held as TOUCHED, so the text baseline (roles=('CITED',)) runs through the same rules. Symbols come from TARGET rows only. Pure."""
    out: dict = defaultdict(dict)
    for r in rows:
        ap, role = r["action_point_id"], r["role"]
        if ap not in universe or role not in roles:
            continue
        cur = out[ap].setdefault(r["module_path"], {"role": "TOUCHED", "symbols": set()})
        if role == "TARGET":
            cur["role"] = "TARGET"
            if r.get("symbol"):
                cur["symbols"].add(r["symbol"])
    return dict(out)


def hub_modules(held: dict, size: int) -> set:
    """The modules held by more than HUB_SHARE of `size` tickets AND by more than HUB_MIN_TICKETS tickets. Pure."""
    count = Counter(m for mods in held.values() for m in mods)
    return {m for m, c in count.items() if c > HUB_MIN_TICKETS and c > HUB_SHARE * size}


def signal(x: dict, y: dict, is_hub: bool) -> Optional[float]:
    """The weight of one shared module between two tickets, or None. A hub counts only when both declared the same symbol of it. Pure."""
    if x["symbols"] & y["symbols"]:
        return W_SYMBOL
    if is_hub:
        return None
    if x["role"] == "TARGET" and y["role"] == "TARGET":
        return W_TARGET
    if "TARGET" in (x["role"], y["role"]):
        return W_MIXED
    return W_TOUCHED


def build_edges(held: dict, hubs: set, edge_min: float = EDGE_MIN) -> dict:
    """{(a, b): {'score': best weight, 'modules': {module: weight}}} for every pair of tickets (a < b) that share a module at `edge_min` or more. Pure."""
    by_module: dict = defaultdict(list)
    for ap, mods in held.items():
        for m in mods:
            by_module[m].append(ap)
    edges: dict = {}
    for m, aps in by_module.items():
        for a, b in combinations(sorted(aps), 2):
            w = signal(held[a][m], held[b][m], m in hubs)
            if w is None or w < edge_min:
                continue
            e = edges.setdefault((a, b), {"score": 0.0, "modules": {}})
            e["modules"][m] = w
            e["score"] = max(e["score"], w)
    return edges


# ---------------------------------------------------------------- clusters

def components(nodes: set, edges: dict) -> list:
    """The connected components of `nodes` over `edges`, each a set, ordered by their lowest ticket. Pure."""
    adj: dict = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    seen: set = set()
    out = []
    for n in sorted(nodes):
        if n in seen:
            continue
        comp, stack = set(), [n]
        while stack:
            cur = stack.pop()
            if cur in comp:
                continue
            comp.add(cur)
            stack.extend(adj[cur] - comp)
        seen |= comp
        out.append(comp)
    return out


def split(comp: set, edges: dict, open_ids: Optional[set] = None) -> list:
    """[(members, hotspot)]: `comp` as one cluster when it has MAX_OPEN_MEMBERS OPEN tickets or fewer (`open_ids` = the open ones; None = every ticket given is open; closed tickets never count),
    else cut by removing the fewest edges. A component whose edges all rest on ONE common module is never cut (hotspot). Otherwise a single edge that splits it on its own (a bridge) is cut: the one that
    leaves the smallest largest part, then the weakest. With no bridge, edges go one at a time, the weakest first and the ones at the busiest tickets first among equals, and the cut stops at the FIRST
    split: nothing shatters. A part left alone is no cluster. Pure."""
    live = {e: v for e, v in edges.items() if e[0] in comp and e[1] in comp}

    def size(c: set) -> int:
        return len(c) if open_ids is None else len(c & open_ids)

    if size(comp) <= MAX_OPEN_MEMBERS:
        return [(comp, False)]
    if live and set.intersection(*[set(v["modules"]) for v in live.values()]):
        return [(comp, True)]

    def cut(parts: list, rest: dict) -> list:
        out = []
        for p in parts:
            if len(p) >= 2:
                out += split(p, rest, open_ids)
        return out

    bridges, total = find_bridges(comp, live, size)
    if bridges:
        e = min(bridges, key=lambda t: (max(t[0], total - t[0]), live[t[1]]["score"], t[1]))[1]
        rest = {k: v for k, v in live.items() if k != e}
        return cut(components(comp, rest), rest)
    deg: Counter = Counter()
    for a, b in live:
        deg[a] += 1
        deg[b] += 1
    order = sorted(live, key=lambda k: (live[k]["score"], -(deg[k[0]] + deg[k[1]]), k))

    def rest_after(n: int) -> dict:                                                  # the edges left once the first n of `order` are gone
        gone = set(order[:n])
        return {k: v for k, v in live.items() if k not in gone}

    lo, hi = 1, len(order)                                                           # removing more edges never joins anything: the first split is found by bisection
    while lo < hi:
        mid = (lo + hi) // 2
        if len(components(comp, rest_after(mid))) > 1:
            hi = mid
        else:
            lo = mid + 1
    rest = rest_after(lo)
    return cut(components(comp, rest), rest) if len(components(comp, rest)) > 1 else []


def find_bridges(comp: set, live: dict, size) -> tuple:
    """([(size of the part below the bridge, (a, b))], size of comp): the edges whose removal alone splits the connected `comp`, in one linear pass (lowpoints). `size(set)` counts the open tickets. Pure."""
    adj: dict = defaultdict(list)
    for a, b in live:
        adj[a].append(b)
        adj[b].append(a)
    start = min(comp)
    disc, low, below = {start: 0}, {start: 0}, {start: size({start})}
    out, clock = [], 1
    stack = [(start, None, iter(sorted(adj[start])))]
    while stack:
        u, parent, nxt = stack[-1]
        for v in nxt:
            if v == parent:
                continue
            if v in disc:
                low[u] = min(low[u], disc[v])
            else:
                disc[v] = low[v] = clock
                clock += 1
                below[v] = size({v})
                stack.append((v, u, iter(sorted(adj[v]))))
                break
        else:
            stack.pop()
            if parent is not None:
                low[parent] = min(low[parent], low[u])
                below[parent] += below[u]
                if low[u] > disc[parent]:
                    out.append((below[u], (min(u, parent), max(u, parent))))
    return out, below[start]


def make_cluster(members: set, hotspot: bool, edges: dict) -> dict:
    """{'members': sorted tuple, 'hotspot', 'principal': the module most pairs share, 'modules': [(module, pairs)], 'why': {ticket: 'shares ... with ...'}}. Pure."""
    inside = {e: v for e, v in edges.items() if e[0] in members and e[1] in members}
    count: Counter = Counter(m for v in inside.values() for m in v["modules"])
    ranked = sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))
    why: dict = {}
    for ap in sorted(members):
        mine = [(e, v) for e, v in inside.items() if ap in e]
        (a, b), best = sorted(mine, key=lambda kv: (-kv[1]["score"], kv[0]))[0]
        mods = sorted(best["modules"], key=lambda m: (-best["modules"][m], m))
        why[ap] = f"shares {mods[0]}{f' (+{len(mods) - 1} more)' if len(mods) > 1 else ''} with {b if a == ap else a}"
    return {"members": tuple(sorted(members)), "hotspot": hotspot, "principal": ranked[0][0], "modules": ranked, "why": why}


def derive(rows: list, universe: set, roles: tuple = ("TARGET", "TOUCHED"), edge_min: float = EDGE_MIN, open_ids: Optional[set] = None) -> list:
    """The clusters of the tickets in `universe`, largest first. `open_ids` = which of them are open for the size rule (None: all of them). Pure."""
    held = holdings(rows, universe, roles)
    edges = build_edges(held, hub_modules(held, len(universe)), edge_min)
    out = []
    for comp in components({n for e in edges for n in e}, edges):
        if len(comp) >= 2:
            out += [make_cluster(m, hot, edges) for m, hot in split(comp, edges, open_ids)]
    return sorted(out, key=lambda c: (-len(c["members"]), c["principal"], c["members"]))


def rank(clusters: list, info: dict) -> list:
    """[(cluster, open members, weight)] by open members times priority, the biggest unit of work first. `info` = {ticket: {'status', 'priority'}}. Pure."""
    out = []
    for c in clusters:
        opens = [a for a in c["members"] if info.get(a, {}).get("status", "CLOSED") != "CLOSED"]
        out.append((c, opens, sum(PRIORITY_WEIGHT.get(info[a].get("priority"), 1) for a in opens)))
    return sorted(out, key=lambda t: (-len(t[1]) * t[2], -len(t[1]), t[0]["principal"]))


# ---------------------------------------------------------------- the identity of a group and the events that keep it

def group_name(module: str, salt: str = "") -> str:
    """MOD.<module path, upper-cased, dots for slashes, no extension>, within the 64 characters and the character set of group_tag.name; cut with a hash when too long or when `salt` is given. Pure."""
    base = re.sub(r"\.[A-Za-z0-9]+$", "", module)
    name = "MOD." + re.sub(r"[^A-Z0-9_.-]", "-", base.upper().replace("/", "."))
    if salt or len(name) > NAME_MAX:
        tag = hashlib.sha1((module + salt).encode("utf-8")).hexdigest()[:8].upper()
        name = name[:NAME_MAX - 9] + "-" + tag
    return name


def group_state(events: list, status: dict) -> tuple:
    """(groups, current, rejected) from the MODULE group events [{'group_id', 'name', 'action_point_id' (None for a group with no event), 'op', 'actor'}] in event order, and {ticket: status}:
    `groups` = {name: {'id', 'open': its OPEN members now}}, `current` = {name: all its members now}, `rejected` = {(name, ticket)} whose latest event is a REMOVE by an agent or a person. Pure."""
    ids: dict = {}
    last: dict = {}
    for e in events:
        ids[e["name"]] = e["group_id"]
        if e.get("action_point_id"):
            last[(e["name"], e["action_point_id"])] = (e["op"], e["actor"])
    current: dict = defaultdict(set)
    rejected: set = set()
    for (name, ap), (op, actor) in last.items():
        if op == "ADD":
            current[name].add(ap)
        elif not actor.startswith(SYSTEM):
            rejected.add((name, ap))
    groups = {n: {"id": i, "open": {a for a in current.get(n, set()) if status.get(a, "CLOSED") != "CLOSED"}} for n, i in ids.items()}
    return groups, dict(current), rejected


def assign_identity(clusters: list, groups: dict) -> list:
    """[(cluster, group name, is_new)]. `groups` = {name: {'id': int, 'open': set of the OPEN tickets it holds now}}. A cluster inherits the group that holds most of its tickets (ties: the lowest id)
    and a group is inherited once (the largest cluster takes it); a cluster with no ticket in any group gets a new one. Nobody is moved out of a group that nobody inherits. Pure."""
    claimed: set = set()
    taken = set(groups)
    out = []
    for c in clusters:
        mine = set(c["members"])
        best = sorted(((len(mine & g["open"]), -g["id"], n) for n, g in groups.items() if n not in claimed and mine & g["open"]), reverse=True)
        if best:
            claimed.add(best[0][2])
            out.append((c, best[0][2], False))
            continue
        name = group_name(c["principal"])
        if name in taken:
            name = group_name(c["principal"], salt=",".join(c["members"]))
        taken.add(name)
        claimed.add(name)
        out.append((c, name, True))
    return out


def plan_memberships(assigned: list, current: dict, rejected: set, status: dict) -> list:
    """The events that make each group match its cluster: ('CREATE', name, description), ('ADD', name, ticket, reason), ('REMOVE', name, ticket, reason).
    `current` = {group: set of all its members now}; `rejected` = {(group, ticket)} whose latest event is a REMOVE by an agent or a person (it stands); `status` = {ticket: status}.
    A ticket that closes stays a member (the group can reach READY_TO_CLOSE); only an OPEN ticket with no edge left into the cluster is removed. Pure."""
    ops = []
    for c, name, is_new in assigned:
        if is_new:
            n = len(c["modules"])
            ops.append(("CREATE", name, f"tickets that change {c['principal']}" + (f" (+{n - 1} more shared modules)" if n > 1 else "") + (": a hotspot" if c["hotspot"] else "")))
        have, want = current.get(name, set()), set(c["members"])
        ops += [("ADD", name, ap, c["why"][ap]) for ap in sorted(want - have) if (name, ap) not in rejected]
        ops += [("REMOVE", name, ap, "no module shared with the other members any more") for ap in sorted(have - want) if status.get(ap, "CLOSED") != "CLOSED"]
    return ops


# ---------------------------------------------------------------- is it good enough to be on

def pairs_of(clusters: list) -> set:
    """Every pair of tickets that share a cluster, as frozensets. Pure."""
    return {frozenset(p) for c in clusters for p in combinations(c["members"], 2)}


def evaluate(clusters: list, text_clusters: list, positives: set, negatives: set, universe: set) -> dict:
    """{'sample', 'precision', 'recall', 'recall_text', 'positives'}: precision = decided pairs the clusters put together that are CONFIRMED OVERLAPS, over all decided pairs they put together
    (sample = how many); recall = confirmed OVERLAPS (both tickets in `universe`) the clusters put together, and the same for the text-only baseline. None where nothing can be divided. Pure."""
    pos = {p for p in positives if p <= universe}
    cl, tx = pairs_of(clusters), pairs_of(text_clusters)
    tp, fp = cl & pos, cl & negatives
    n = len(tp) + len(fp)
    return {"sample": n, "precision": len(tp) / n if n else None, "positives": len(pos),
            "recall": len(tp) / len(pos) if pos else None, "recall_text": len(tx & pos) / len(pos) if pos else None}


def proves(ev: dict) -> bool:
    """True when the sample is big enough and the precision reaches MIN_PRECISION. Precision is the only bar (owner directive 2026-11-01): it is what prevents false "module mates"; the recall and the text
    baseline are reported but never gate, because the confirmed OVERLAPS were themselves found from ticket text, so that comparison favours text. Pure."""
    return bool(ev["sample"] >= MIN_SAMPLE and ev["precision"] is not None and ev["precision"] >= MIN_PRECISION)


def active(mode: str, ev: Optional[dict]) -> tuple:
    """(on?, why) for the switch BACKLOG_MODULE_CLUSTERS_MODE: ON forms clusters, OFF never does, AUTO only while the clusters prove themselves against the decisions on record. Pure."""
    if mode == "ON":
        return True, "forced on"
    if mode != "AUTO":
        return False, "off"
    if ev is None:
        return False, "auto: not measured"
    if ev["sample"] < MIN_SAMPLE:
        return False, f"auto: sample {ev['sample']} of {MIN_SAMPLE} decided pairs; precision unproven"
    if not proves(ev):
        return False, f"auto: precision {ev['precision']:.0%} on {ev['sample']} decided pairs is under the {MIN_PRECISION:.0%} bar"
    return True, f"auto: precision {ev['precision']:.0%} on {ev['sample']} decided pairs"


def t0_ready(sim: dict) -> tuple:
    """(ready?, why): whether the facts are good enough to change the collision rule T0. They are only when the pairs the facts ADD to the text rule, among those already decided, are mostly CONFIRMED
    OVERLAPS (at least MIN_SAMPLE decided and MIN_PRECISION confirmed): undecided pairs are unknown, not wrong, and a gate that fires on them would only add noise. Until then T0 is not touched. Pure."""
    decided = sim["added_confirmed"] + sim["added_rejected"]
    if decided < MIN_SAMPLE:
        return False, f"T0 stays as it is: {decided} of {MIN_SAMPLE} decided added pairs ({sim['added_undecided']} undecided)"
    share = sim["added_confirmed"] / decided
    if share < MIN_PRECISION:
        return False, f"T0 stays as it is: {share:.0%} of {decided} decided added pairs are confirmed, under the {MIN_PRECISION:.0%} bar"
    return True, f"T0 could take the facts: {share:.0%} of {decided} decided added pairs are confirmed"


# ---------------------------------------------------------------- what the T0 collision rule would gain (simulation only)

def simulate_t0(commits: list, cited: dict, held: dict, in_progress: set, positives: set, negatives: set) -> dict:
    """What the cluster facts would add to the collision rule T0. `commits` = [{'sha', 'tickets', 'modules'}]; `cited` = {ticket: modules its text cites} (the rule today, with its hub rule of
    TEXT_HUB_TICKETS), `held` = holdings() of TARGET / TOUCHED. For each commit, a pair is a ticket of the commit with ANOTHER ticket in progress that shares a changed module. Returns the pairs
    each rule finds, the ones only the facts find, and how many of those are CONFIRMED OVERLAPS or REJECTED on record. Pure."""
    text_count = Counter(m for mods in cited.values() for m in mods)
    held_hubs = hub_modules(held, max(len(in_progress), 1))
    text_pairs, fact_pairs = set(), set()
    for c in commits:
        tickets = set(c["tickets"])
        for u in in_progress - tickets:
            t_mods = {m for m in cited.get(u, ()) if text_count[m] < TEXT_HUB_TICKETS}
            f_mods = {m for m in held.get(u, {}) if m not in held_hubs}
            for t in tickets:
                if c["modules"] & t_mods:
                    text_pairs.add(frozenset((t, u)))
                if c["modules"] & f_mods:
                    fact_pairs.add(frozenset((t, u)))
    added = fact_pairs - text_pairs
    return {"commits": len(commits), "text_pairs": len(text_pairs), "fact_pairs": len(fact_pairs), "added": len(added),
            "added_confirmed": len(added & positives), "added_rejected": len(added & negatives), "added_undecided": len(added - positives - negatives)}
