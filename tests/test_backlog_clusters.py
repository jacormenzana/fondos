# tests/test_backlog_clusters.py
# -*- coding: utf-8 -*-
"""Backlog v3 W4b slice 2 (FND-0360): the pure cluster rules of shared/backlog_clusters.py. No database, no git, no network (R-7)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import backlog_clusters as bc  # noqa: E402

NAME_RX = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,63}$")


def row(ap, module, role="TOUCHED", symbol=None):
    return {"action_point_id": ap, "module_path": module, "role": role, "symbol": symbol, "source": "COMMIT" if role == "TOUCHED" else "DECLARED"}


def ids(prefix, n):
    return [f"{prefix}-{i:04d}" for i in range(1, n + 1)]


def cluster_of(members, principal="a.py"):
    return {"members": tuple(sorted(members)), "hotspot": False, "principal": principal, "modules": [(principal, 1)], "why": {m: f"shares {principal} with x" for m in members}}


# ---------------------------------------------------------------- edges, hubs and what counts

def test_the_signals_are_weighted_and_the_strongest_is_the_score():
    rows = [row("A", "m.py", "TARGET", "f"), row("B", "m.py", "TARGET", "f"),            # same symbol 1.0
            row("A", "n.py", "TARGET"), row("B", "n.py", "TARGET"),                       # same target module 0.8
            row("C", "o.py", "TARGET"), row("D", "o.py"),                                 # target vs touched 0.7
            row("E", "p.py"), row("F", "p.py")]                                           # both touched 0.6
    e = bc.build_edges(bc.holdings(rows, set("ABCDEF")), set())
    assert e[("A", "B")]["score"] == 1.0 and e[("A", "B")]["modules"] == {"m.py": 1.0, "n.py": 0.8}
    assert e[("C", "D")]["score"] == 0.7 and e[("E", "F")]["score"] == 0.6


def test_a_cited_module_never_makes_an_edge_and_only_target_and_touched_count():
    rows = [row("A", "m.py", "CITED"), row("B", "m.py", "CITED")]
    assert bc.build_edges(bc.holdings(rows, {"A", "B"}), set()) == {}
    assert bc.derive(rows, {"A", "B"}) == []
    assert len(bc.derive(rows, {"A", "B"}, roles=("CITED",))) == 1                    # the text baseline runs through the same rules


def test_the_edge_floor_drops_weak_edges():
    rows = [row("A", "m.py"), row("B", "m.py")]
    assert bc.derive(rows, {"A", "B"}, edge_min=0.7) == [] and len(bc.derive(rows, {"A", "B"})) == 1


def test_a_hub_needs_both_the_share_and_the_absolute_floor():
    small = [row(a, "m.py") for a in ids("S", 4)]                                    # 4 of 10 tickets: 40 % but not more than 5 tickets
    assert bc.hub_modules(bc.holdings(small, set(ids("S", 10))), 10) == set()
    big = [row(a, "m.py") for a in ids("S", 6)]                                      # 6 of 100: more than 5 tickets but only 6 %
    assert bc.hub_modules(bc.holdings(big, set(ids("S", 100))), 100) == set()
    both = [row(a, "m.py") for a in ids("S", 6)]                                     # 6 of 20: more than 5 tickets and 30 %
    assert bc.hub_modules(bc.holdings(both, set(ids("S", 20))), 20) == {"m.py"}


def test_a_hub_links_two_tickets_only_through_a_shared_symbol():
    rows = [row(a, "hub.py") for a in ids("S", 6)] + [row("S-0001", "hub.py", "TARGET", "f"), row("S-0002", "hub.py", "TARGET", "f")]
    cl = bc.derive(rows, set(ids("S", 20)))
    assert [c["members"] for c in cl] == [("S-0001", "S-0002")]


# ---------------------------------------------------------------- clusters

def test_files_shared_by_the_same_two_tickets_are_one_cluster_not_eight():
    mods = [f"scripts/launch/L{i}.bat" for i in range(8)]
    rows = [row(t, m) for t in ("FND-0239", "FND-0308") for m in mods]
    cl = bc.derive(rows, {"FND-0239", "FND-0308"})
    assert len(cl) == 1 and cl[0]["members"] == ("FND-0239", "FND-0308") and len(cl[0]["modules"]) == 8
    assert cl[0]["why"]["FND-0239"].startswith("shares scripts/launch/L0.bat (+7 more) with FND-0308")


def test_nine_tickets_on_one_module_stay_one_hotspot_and_are_never_cut():
    t = ids("F", 9)
    cl = bc.derive([row(a, "scripts/audit/audit_incident.py") for a in t], set(t) | set(ids("G", 90)))
    assert len(cl) == 1 and cl[0]["hotspot"] is True and len(cl[0]["members"]) == 9


def test_a_big_component_on_several_modules_is_cut_at_its_weakest_link_and_nothing_shatters():
    a, b = ids("A", 5), ids("B", 5)
    rows = [row(x, "ma.py") for x in a] + [row(x, "mb.py") for x in b] + [row("A-0001", "mc.py"), row("B-0001", "mc.py")]
    cl = bc.derive(rows, set(a + b) | set(ids("G", 90)))
    assert sorted(c["members"] for c in cl) == [tuple(a), tuple(b)] and not any(c["hotspot"] for c in cl)


def test_a_single_bridge_is_cut_alone_and_a_part_left_alone_is_no_cluster():
    a = ids("A", 9)
    rows = [row(x, "ma.py") for x in a[:8]] + [row("A-0008", "mb.py"), row("A-0009", "mb.py")]
    cl = bc.derive(rows, set(a) | set(ids("G", 90)))
    assert [c["members"] for c in cl] == [tuple(a[:8])]                                    # one edge cut; A-0009 is alone and belongs to no cluster


def test_with_no_bridge_edges_go_weakest_first_and_the_cut_stops_at_the_first_split():
    a, b = ids("A", 5), ids("B", 5)
    rows = ([row(x, "ma.py") for x in a] + [row(x, "mb.py") for x in b]
            + [row("A-0001", "mc.py"), row("B-0001", "mc.py"), row("A-0002", "md.py"), row("B-0002", "md.py")])     # two links between the halves: no single edge splits them
    cl = bc.derive(rows, set(a + b) | set(ids("G", 90)))
    assert sorted(c["members"] for c in cl) == [tuple(a), tuple(b)]


def test_the_size_rule_counts_only_the_tickets_given_which_are_the_open_ones():
    t = ids("F", 12)
    rows = [row(a, "m.py") for a in t]
    open_only = set(t[:6]) | set(ids("G", 90))
    cl = bc.derive(rows, open_only)
    assert len(cl) == 1 and len(cl[0]["members"]) == 6 and cl[0]["hotspot"] is False       # the six closed tickets are not in the universe


def test_fifty_closed_tickets_do_not_count_toward_the_cap_of_eight_open_ones():
    closed, opened = ids("C", 50), ids("O", 8)
    rows = [row(x, "ma.py") for x in closed + opened] + [row("O-0001", "mb.py"), row("C-0001", "mb.py")]      # two modules, so a cut would be allowed if the size were over the cap
    cl = bc.derive(rows, set(closed + opened) | set(ids("G", 400)), open_ids=set(opened))
    assert len(cl) == 1 and len(cl[0]["members"]) == 58 and cl[0]["hotspot"] is False
    over = bc.derive(rows, set(closed + opened) | set(ids("G", 400)), open_ids=set(closed + opened))           # counted as open they would be over the cap
    assert len(over[0]["members"]) < 58 or over[0]["hotspot"] is True


def test_the_group_state_comes_from_the_events_and_only_a_non_system_remove_is_a_rejection():
    ev = [{"group_id": 1, "name": "MOD.A", "action_point_id": "T-1", "op": "ADD", "actor": "system:autopilot"},
          {"group_id": 1, "name": "MOD.A", "action_point_id": "T-2", "op": "ADD", "actor": "system:autopilot"},
          {"group_id": 1, "name": "MOD.A", "action_point_id": "T-2", "op": "REMOVE", "actor": "agent:claude-opus-5-5"},
          {"group_id": 1, "name": "MOD.A", "action_point_id": "T-3", "op": "ADD", "actor": "system:autopilot"},
          {"group_id": 1, "name": "MOD.A", "action_point_id": "T-3", "op": "REMOVE", "actor": "system:autopilot"},
          {"group_id": 2, "name": "MOD.B", "action_point_id": None, "op": None, "actor": None}]
    groups, current, rejected = bc.group_state(ev, {"T-1": "CLOSED", "T-2": "OPEN", "T-3": "OPEN"})
    assert current == {"MOD.A": {"T-1"}} and rejected == {("MOD.A", "T-2")}
    assert groups == {"MOD.A": {"id": 1, "open": set()}, "MOD.B": {"id": 2, "open": set()}}


def test_clusters_are_ranked_by_open_members_times_priority_and_closed_tickets_do_not_count():
    c1, c2 = cluster_of(ids("A", 3), "a.py"), cluster_of(ids("B", 2), "b.py")
    info = {**{a: {"status": "OPEN", "priority": "LOW"} for a in ids("A", 3)}, **{a: {"status": "OPEN", "priority": "HIGH"} for a in ids("B", 2)}}
    info["A-0003"]["status"] = "CLOSED"
    ranked = bc.rank([c1, c2], info)
    assert [r[0]["principal"] for r in ranked] == ["b.py", "a.py"] and ranked[1][1] == ["A-0001", "A-0002"]


# ---------------------------------------------------------------- identity and events

def test_a_new_group_is_named_after_its_module_and_fits_the_name_rule_even_when_long():
    assert bc.group_name("shared/backlog_client.py") == "MOD.SHARED.BACKLOG_CLIENT"
    long = bc.group_name("proyecto1/" + "very_long_directory_name/" * 6 + "module.py")
    assert len(long) <= 64 and NAME_RX.match(long) and bc.group_name(".github/x.py") and NAME_RX.match(bc.group_name(".github/x.py"))
    assert bc.group_name("a/b.py", salt="x") != bc.group_name("a/b.py") and NAME_RX.match(bc.group_name("a/b.py", salt="x"))


def test_a_cluster_inherits_the_group_that_holds_most_of_its_open_tickets():
    groups = {"MOD.OLD": {"id": 1, "open": {"A-0001", "A-0002", "A-0003"}}, "MOD.OTHER": {"id": 2, "open": {"A-0003"}}}
    out = bc.assign_identity([cluster_of(["A-0001", "A-0002", "A-0003", "A-0004"])], groups)
    assert out[0][1:] == ("MOD.OLD", False)


def test_a_group_is_inherited_once_the_larger_cluster_takes_it_and_the_other_gets_a_new_one():
    groups = {"MOD.OLD": {"id": 1, "open": {"A-0001", "A-0002", "A-0003", "A-0004"}}}
    big, small = cluster_of(["A-0001", "A-0002", "A-0003"], "big.py"), cluster_of(["A-0004", "A-0005"], "small.py")
    out = bc.assign_identity([big, small], groups)
    assert out[0][1:] == ("MOD.OLD", False) and out[1][1:] == ("MOD.SMALL", True)


def test_a_cluster_with_no_ticket_in_any_group_is_new_and_a_taken_name_gets_a_hash():
    out = bc.assign_identity([cluster_of(["A-0001", "A-0002"], "m.py")], {"MOD.M": {"id": 1, "open": {"Z-0001"}}})
    assert out[0][2] is True and out[0][1] != "MOD.M" and NAME_RX.match(out[0][1])


def test_a_tie_between_groups_goes_to_the_lowest_id():
    groups = {"MOD.B": {"id": 2, "open": {"A-0001"}}, "MOD.A": {"id": 1, "open": {"A-0002"}}}
    assert bc.assign_identity([cluster_of(["A-0001", "A-0002"])], groups)[0][1] == "MOD.A"


def test_the_plan_creates_adds_and_removes_and_a_rejection_stands():
    c = cluster_of(["A-0001", "A-0002", "A-0003"], "m.py")
    ops = bc.plan_memberships([(c, "MOD.M", True)], {}, {("MOD.M", "A-0003")}, {})
    assert ops[0][0] == "CREATE" and len(ops[0][2]) >= 10
    assert [o[:3] for o in ops[1:]] == [("ADD", "MOD.M", "A-0001"), ("ADD", "MOD.M", "A-0002")] and all(len(o[3]) >= 10 for o in ops[1:])


def test_an_open_ticket_with_no_edge_left_is_removed_and_a_closed_one_stays_a_member():
    c = cluster_of(["A-0001", "A-0002"], "m.py")
    ops = bc.plan_memberships([(c, "MOD.M", False)], {"MOD.M": {"A-0001", "A-0002", "A-0009", "A-0010"}}, set(), {"A-0009": "OPEN", "A-0010": "CLOSED"})
    assert [o[:3] for o in ops] == [("REMOVE", "MOD.M", "A-0009")]


def test_a_rerun_with_everything_in_place_plans_nothing():
    c = cluster_of(["A-0001", "A-0002"], "m.py")
    assert bc.plan_memberships([(c, "MOD.M", False)], {"MOD.M": {"A-0001", "A-0002"}}, set(), {}) == []


# ---------------------------------------------------------------- proof and switch

def clusters_of_pairs(n):
    return [cluster_of([f"P{i}-1", f"P{i}-2"], f"m{i}.py") for i in range(n)]


def pair(i):
    return frozenset((f"P{i}-1", f"P{i}-2"))


def universe_of(n):
    return {t for i in range(n) for t in tuple(pair(i))}


def test_precision_recall_and_the_text_baseline_are_measured_on_the_decided_pairs():
    cl, tx = clusters_of_pairs(25), clusters_of_pairs(5)
    pos = {pair(i) for i in range(22)} | {frozenset(("Q-1", "Q-2"))}
    neg = {pair(i) for i in range(22, 25)}
    ev = bc.evaluate(cl, tx, pos, neg, universe_of(25) | {"Q-1", "Q-2"})
    assert ev["sample"] == 25 and ev["precision"] == pytest.approx(22 / 25) and ev["recall"] == pytest.approx(22 / 23) and ev["recall_text"] == pytest.approx(5 / 23)
    assert bc.proves(ev) is True


@pytest.mark.parametrize("n_pos, n_neg, text, ok", [
    (10, 0, 0, False),             # sample too small: unproven, so off
    (20, 0, 20, True),             # no better than the text baseline: recall is reported, never a bar (owner directive 2026-11-01)
    (14, 6, 0, True),              # 70 % exactly on 20 pairs
    (13, 7, 0, False)])            # 65 %
def test_the_proof_needs_the_sample_the_precision_and_a_gain_over_the_text(n_pos, n_neg, text, ok):
    n = n_pos + n_neg
    ev = bc.evaluate(clusters_of_pairs(n), clusters_of_pairs(text), {pair(i) for i in range(n_pos)}, {pair(i) for i in range(n_pos, n)}, universe_of(n))
    assert bc.proves(ev) is ok


def test_the_switch_modes_on_forces_off_never_and_auto_waits_for_the_proof():
    small = {"sample": 3, "precision": 1.0, "recall": 1.0, "recall_text": 0.0, "positives": 3}
    good = {"sample": 30, "precision": 0.8, "recall": 0.6, "recall_text": 0.2, "positives": 40}
    assert bc.active("ON", None)[0] is True and bc.active("OFF", good)[0] is False and bc.active("BOGUS", good)[0] is False
    on, why = bc.active("AUTO", small)
    assert on is False and "unproven" in why
    assert bc.active("AUTO", None)[0] is False and bc.active("AUTO", good)[0] is True
    assert bc.active("AUTO", {**good, "precision": 0.5})[0] is False


# ---------------------------------------------------------------- the T0 simulation

def test_the_t0_simulation_counts_the_pairs_only_the_facts_find_and_how_many_are_decided():
    held = bc.holdings([row("U1", "m.py"), row("U2", "n.py"), row("U3", "m.py")], {"U1", "U2", "U3"})
    cited = {"U1": {"m.py"}, "U2": set(), "U3": set()}                                  # the text of U1 cites m.py; U2 and U3 do not
    commits = [{"sha": "s1", "tickets": ["T1"], "modules": {"m.py", "n.py"}}]
    sim = bc.simulate_t0(commits, cited, held, {"U1", "U2", "U3"}, {frozenset(("T1", "U3"))}, {frozenset(("T1", "U2"))})
    assert sim["text_pairs"] == 1 and sim["fact_pairs"] == 3 and sim["added"] == 2
    assert (sim["added_confirmed"], sim["added_rejected"], sim["added_undecided"]) == (1, 1, 0)


def test_the_proof_ignores_recall_so_a_worse_recall_than_the_text_never_blocks_the_switch():
    ev = {"sample": 25, "precision": 0.8, "recall": 0.05, "recall_text": 0.9, "positives": 100}
    assert bc.proves(ev) is True and bc.active("AUTO", ev)[0] is True and "recall" not in bc.active("AUTO", ev)[1]
    assert bc.active("AUTO", {**ev, "precision": 0.6})[0] is False


@pytest.mark.parametrize("confirmed, rejected, undecided, ready", [
    (5, 0, 279, False),            # today's real figures: 5 decided of 284 added pairs; undecided is unknown, not wrong
    (14, 6, 300, True),            # 70 % of 20 decided
    (13, 7, 0, False),             # 65 %
    (19, 0, 0, False)])            # a perfect share on too small a sample
def test_t0_is_touched_only_when_the_pairs_the_facts_add_are_mostly_confirmed_among_the_decided(confirmed, rejected, undecided, ready):
    sim = {"added": confirmed + rejected + undecided, "added_confirmed": confirmed, "added_rejected": rejected, "added_undecided": undecided}
    on, why = bc.t0_ready(sim)
    assert on is ready and why.startswith("T0 ")


def test_the_t0_simulation_keeps_the_hub_rule_of_the_text_rule():
    cited = {f"U{i}": {"hub.py"} for i in range(5)}                                     # cited by five open tickets: a hub for the text rule
    sim = bc.simulate_t0([{"sha": "s", "tickets": ["T1"], "modules": {"hub.py"}}], cited, {}, set(cited), set(), set())
    assert sim["text_pairs"] == 0
