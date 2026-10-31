# tests/test_backlog_phase_c_acceptance.py
# -*- coding: utf-8 -*-
"""Phase C acceptance verifier (FND-0334), no database: scripts/audit/backlog_phase_c_acceptance.py judges a catalogue of facts. A catalogue that matches the design passes every check, and each
way a scoped login or a routine can drift from it fails exactly the check that names it."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts" / "audit"), str(ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_phase_c_acceptance as acc  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402

ROLES = ("backlog_gate", "backlog_adjudicator", "backlog_writer", "backlog_human", "backlog_owner")


def routine(execs, secdef=True, owner="backlog_owner", pinned=True, public=False):
    return {"owner": owner, "secdef": secdef, "search_path_pinned": pinned, "public": public, "executes": sorted(execs)}


def table(append_only=True, owner="backlog_owner", select=("backlog_writer",), write=()):
    return {"owner": owner, "append_only": append_only,
            "select": {r: r in select for r in ROLES}, "insert": {r: r in write for r in ROLES}, "update": {r: r in write for r in ROLES}, "delete": {r: r in write for r in ROLES}}


def role(**over):
    r = {"login": True, "super": False, "createrole": False, "createdb": False, "connlimit": -1, "config": [], "member_of": []}
    r.update(over)
    return r


def good():
    """A catalogue exactly as the three phases leave it."""
    tables = {t: table() for p in ("hook", "collision", "attest") for t in mig.PHASES[p]["tables"]}
    views = {v: True for p in ("hook", "collision", "attest") for v in mig.PHASES[p]["views"]}
    gate = {"sp_gate_check", "sp_gate_forward", "fn_gate_statuses", "sp_gate_collision", "fn_collision_state", "fn_gate_undecided"}
    grants = {"sp_gate_check": {"backlog_gate", "backlog_writer"}, "sp_record_hook_bypass": {"backlog_writer"}, "sp_gate_forward": {"backlog_gate"}, "fn_gate_statuses": {"backlog_gate"},
              "sp_gate_collision": {"backlog_gate"}, "fn_collision_state": {"backlog_gate", "backlog_writer"}, "fn_gate_undecided": {"backlog_gate", "backlog_adjudicator", "backlog_writer"},
              "fn_gate_share": {"backlog_writer"}, "fn_audit_start": {"backlog_writer"}, "sp_set_collision_mode": {"backlog_writer", "backlog_human"}, "fn_collision_pairs": set(),
              "sp_adjudicate_overlap": {"backlog_adjudicator"}, "fn_mode_rank": set(), "sp_record_gate_protection": {"backlog_writer", "backlog_human"}}
    routines = {n: routine(grants.get(n, set()), secdef=(n != "fn_mode_rank"), pinned=(n != "fn_mode_rank")) for p in ("hook", "collision", "attest") for n in mig.PHASES[p]["routines"]}
    for n in acc.PRIVILEGED:
        routines.setdefault(n, routine(set()))
    assert gate and routines
    return {"tables": tables, "views": views, "routines": routines,
            "scoped": {"backlog_gate": {"read": [], "write": []}, "backlog_adjudicator": {"read": ["v_gate_queue"], "write": []}},
            "roles": {"backlog_gate": role(connlimit=8, config=["statement_timeout=2500ms"]), "backlog_adjudicator": role(), "backlog_writer": role(login=False), "backlog_human": role(login=False)},
            "violations_view": "SELECT ... FROM backlog.v_violations_c2 UNION ALL SELECT ... FROM backlog.v_violations_c3"}


def evaluate(cat, phase="all"):
    spec = lambda p: mig.PHASES[p]
    out = {}
    if phase in ("hook", "all"):
        out["hook"] = acc.evaluate_hook(cat, spec("hook"), collision_applied=True)
    if phase in ("collision", "all"):
        out["collision"] = acc.evaluate_collision(cat, spec("collision"))
    if phase in ("attest", "all"):
        out["attest"] = acc.evaluate_attest(cat, spec("attest"))
    return out


def failed(results):
    return {name for rs in results.values() for name, ok, _ in rs if not ok}


def test_the_design_passes_every_check():
    res = evaluate(good())
    assert failed(res) == set() and all(len(rs) >= 5 for rs in res.values())


def test_the_helper_lists_are_the_migration_phases():
    """One source of truth: the verifier reads the tables, views and routines of scripts/ops/migrate_backlog_v2.py."""
    res = evaluate(good())
    names = " ".join(n for rs in res.values() for n, _, _ in rs)
    for p in ("hook", "collision", "attest"):
        for t in mig.PHASES[p]["tables"]:
            assert f"table {t} " in names
        for r in mig.PHASES[p]["routines"]:
            assert f"routine {r}" in names


def mutate(fn, phase="all"):
    cat = good()
    fn(cat)
    return failed(evaluate(cat, phase))


def test_a_missing_or_loose_object_fails_its_check():
    assert any(n.startswith("table gate_check ") for n in mutate(lambda c: c["tables"].pop("gate_check")))
    assert any(n.startswith("table hook_bypass ") for n in mutate(lambda c: c["tables"]["hook_bypass"].update(append_only=False)))
    assert any(n.startswith("table gate_state ") for n in mutate(lambda c: c["tables"]["gate_state"].update(owner="fondos_owner")))
    assert any(n.startswith("view v_gate_queue") for n in mutate(lambda c: c["views"].update(v_gate_queue=False)))
    assert any(n.startswith("routine sp_gate_forward:") for n in mutate(lambda c: c["routines"]["sp_gate_forward"].update(secdef=False)))
    assert any(n.startswith("routine sp_gate_forward:") for n in mutate(lambda c: c["routines"]["sp_gate_forward"].update(search_path_pinned=False)))
    assert any(n.startswith("routine sp_gate_forward:") for n in mutate(lambda c: c["routines"]["sp_gate_forward"].update(public=True)))
    assert any(n.startswith("routine sp_gate_forward:") for n in mutate(lambda c: c["routines"]["sp_gate_forward"].update(owner="fondos_owner")))
    assert any(n.startswith("routine fn_mode_rank (pure helper)") for n in mutate(lambda c: c["routines"]["fn_mode_rank"].update(public=True)))


def test_a_pure_helper_need_not_be_security_definer_but_a_privileged_routine_must():
    assert failed(evaluate(good())) == set()                                            # fn_mode_rank is neither definer nor pinned in the good catalogue
    assert mutate(lambda c: c["routines"]["sp_set_collision_mode"].update(secdef=False))


def test_every_way_the_hook_login_can_drift_fails_a_check():
    base = "backlog_gate"
    assert any("can log in" in n for n in mutate(lambda c: c["roles"].pop(base)))
    assert any("no superuser" in n for n in mutate(lambda c: c["roles"][base].update(super=True)))
    assert any("no superuser" in n for n in mutate(lambda c: c["roles"][base].update(createrole=True)))
    assert any("belongs to no other role" in n for n in mutate(lambda c: c["roles"][base].update(member_of=["backlog_writer"])))
    assert any("connection limit" in n for n in mutate(lambda c: c["roles"][base].update(connlimit=-1)))
    assert any("statement timeout" in n for n in mutate(lambda c: c["roles"][base].update(config=[])))
    assert any("executes exactly" in n for n in mutate(lambda c: c["routines"]["sp_transition"]["executes"].append(base)))        # a privileged routine
    assert any("executes exactly" in n for n in mutate(lambda c: c["routines"]["sp_record_hook_bypass"]["executes"].append(base)))
    assert any("executes exactly" in n for n in mutate(lambda c: c["routines"]["sp_gate_check"]["executes"].remove(base)))           # a routine of its design taken away
    assert any("no privilege on ANY table" in n for n in mutate(lambda c: c["scoped"]["backlog_gate"]["read"].append("backlog")))          # a table OUTSIDE the phase
    assert any("no privilege on ANY table" in n for n in mutate(lambda c: c["scoped"]["backlog_gate"]["write"].append("gate_check")))
    assert any("no privilege on ANY table" in n for n in mutate(lambda c: c["scoped"].pop("backlog_gate")))                               # not read: never a silent pass


def test_every_way_the_adjudicator_can_drift_fails_a_check():
    base = "backlog_adjudicator"
    assert any("backlog_adjudicator exists" in n for n in mutate(lambda c: c["roles"].pop(base), "collision"))                       # the owner has not created it
    assert any("no superuser" in n for n in mutate(lambda c: c["roles"][base].update(super=True), "collision"))
    assert any("executes exactly" in n for n in mutate(lambda c: c["routines"]["sp_set_collision_mode"]["executes"].append(base), "collision"))
    assert any("executes exactly" in n for n in mutate(lambda c: c["routines"]["sp_adjudicate_overlap"]["executes"].remove(base), "collision"))
    assert any("no write privilege" in n for n in mutate(lambda c: c["scoped"]["backlog_adjudicator"]["write"].append("backlog_logs"), "collision"))
    assert any("no write privilege" in n for n in mutate(lambda c: c["scoped"].pop("backlog_adjudicator"), "collision"))


def test_the_hook_login_cannot_adjudicate_or_set_a_mode_and_the_writer_cannot_adjudicate():
    assert any("cannot adjudicate or set a mode" in n for n in mutate(lambda c: c["routines"]["sp_adjudicate_overlap"]["executes"].append("backlog_gate"), "collision"))
    assert any("cannot adjudicate or set a mode" in n for n in mutate(lambda c: c["routines"]["sp_set_collision_mode"]["executes"].append("backlog_gate"), "collision"))
    assert any("cannot adjudicate" in n for n in mutate(lambda c: c["routines"]["sp_adjudicate_overlap"]["executes"].append("backlog_writer"), "collision"))
    assert any("cannot adjudicate" in n for n in mutate(lambda c: c["routines"]["sp_set_collision_mode"]["executes"].remove("backlog_writer"), "collision"))


def test_the_writer_must_not_write_the_tables_directly():
    for t in ("gate_check", "gate_adjudication", "gate_protection_event"):
        assert any("writes none directly" in n or "writes no table directly" in n for n in mutate(lambda c, t=t: c["tables"][t]["insert"].update(backlog_writer=True)))


def test_the_catalogue_view_must_contain_each_phases_violations_and_attest_needs_its_routine_grants():
    assert any("contains v_violations_c2" in n for n in mutate(lambda c: c.update(violations_view="SELECT 1"), "hook"))
    assert any("contains v_violations_c3" in n for n in mutate(lambda c: c.update(violations_view=None), "attest"))
    assert any("executable by the shared writer" in n for n in mutate(lambda c: c["routines"]["sp_record_gate_protection"]["executes"].append("backlog_gate"), "attest"))
    assert any("executable by the shared writer" in n for n in mutate(lambda c: c["routines"]["sp_record_gate_protection"].update(executes=[]), "attest"))


def test_a_phase_that_is_not_applied_fails_every_existence_check_not_silently():
    cat = good()
    for t in mig.PHASES["attest"]["tables"]:
        cat["tables"].pop(t)
    for v in mig.PHASES["attest"]["views"]:
        cat["views"][v] = False
    for r in mig.PHASES["attest"]["routines"]:
        cat["routines"].pop(r)
    cat["violations_view"] = "SELECT 1"
    bad = failed(evaluate(cat, "attest"))
    assert len(bad) >= 5 and all(not n.startswith("PASS") for n in bad)
    assert copy.deepcopy(good()) == good()
