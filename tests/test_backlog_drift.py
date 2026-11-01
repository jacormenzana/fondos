# tests/test_backlog_drift.py
# -*- coding: utf-8 -*-
"""Pure tests of scripts/ops/backlog_drift.py: the comparison of the live catalog with master's DDL fingerprint (Backlog v2, FND-0351 family). No database."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_drift as bd  # noqa: E402


def fp(funcs=None, cols=None, views=None):
    return {"funcs": funcs or {}, "cols": cols or {}, "views": views or {}}


def test_identical_catalogs_have_no_drift():
    a = fp({"sp_x(p_a text)": "h1"}, {"t": "a,b"}, {"v": "h2"})
    d = bd.diff(a, a)
    assert not bd.has_drift(d)
    assert "no drift" in bd.report_lines(d)[0]


def test_a_routine_missing_in_live_is_reported():
    d = bd.diff(fp(), fp({"sp_set_gate(p_a text)": "h"}))
    assert d["missing"] == [("routine", "sp_set_gate(p_a text)")] and bd.has_drift(d)


def test_a_changed_signature_shows_as_missing_and_a_changed_body_as_changed():
    live = fp({"sp_set_gate(p_a text)": "old"})
    master = fp({"sp_set_gate(p_a text, p_b text)": "new"})
    assert bd.diff(live, master)["missing"] == [("routine", "sp_set_gate(p_a text, p_b text)")]
    assert bd.diff(live, master)["live_only"] == [("routine", "sp_set_gate(p_a text)")]       # the old signature is informational
    assert bd.diff(fp({"f()": "1"}), fp({"f()": "2"}))["changed"] == [("routine", "f()")]


def test_columns_missing_in_live_are_reported_and_extra_ones_are_not_drift_on_their_own():
    d = bd.diff(fp(cols={"gate_switch": "a,b"}), fp(cols={"gate_switch": "a,b,override"}))
    assert d["columns"] == [("gate_switch", ["override"], [])] and bd.has_drift(d)
    d2 = bd.diff(fp(cols={"t": "a,b,c"}), fp(cols={"t": "a,b"}))
    assert d2["columns"] == [("t", [], ["c"])]


def test_a_missing_view_is_reported_once_not_also_as_a_table():
    d = bd.diff(fp(), fp(cols={"v_new": "a"}, views={"v_new": "h"}))
    assert d["missing"] == [("view", "v_new")]


def test_a_missing_table_is_reported():
    assert bd.diff(fp(), fp(cols={"t_new": "a"}))["missing"] == [("table", "t_new")]


def test_the_known_live_only_view_is_not_listed():
    d = bd.diff(fp(views={"vw_backlog_kpis": "h"}), fp())
    assert d["live_only"] == [] and not bd.has_drift(d)


def test_owning_phase_is_the_last_phase_that_mentions_the_object():
    blocks = {ph: "" for ph in bd.PHASES_IN_ORDER}
    blocks["transitions"] = "CREATE FUNCTION backlog.fn_actor_bound()"
    blocks["relations"] = "CREATE OR REPLACE FUNCTION backlog.fn_actor_bound() ... backlog.sp_set_gate"
    assert bd.owning_phase("fn_actor_bound()", blocks=blocks) == "relations"
    assert bd.owning_phase("sp_set_gate(p_a text)", blocks=blocks) == "relations"
    assert bd.owning_phase("nothing_here()", blocks=blocks) == ""


def test_owning_phase_prefers_the_phase_that_defines_over_one_that_only_calls():
    blocks = {ph: "" for ph in bd.PHASES_IN_ORDER}
    blocks["relations"] = "CREATE OR REPLACE FUNCTION backlog.fn_actor_authorized(p text)"
    blocks["attest"] = "IF NOT backlog.fn_actor_authorized(p_actor) THEN"
    assert bd.owning_phase("fn_actor_authorized(p_actor text)", blocks=blocks) == "relations"


def test_phases_in_order_are_all_real_phases():
    import migrate_backlog_v2 as mig
    for ph in bd.PHASES_IN_ORDER:
        assert ph in mig.PHASES, ph
    assert "iam" not in bd.PHASES_IN_ORDER and "revoke" not in bd.PHASES_IN_ORDER


def test_every_applied_phase_that_creates_objects_is_in_the_order_so_the_tool_is_never_blind_to_it():
    """The tool attributes a live-only object to no phase and cannot detect drift of a phase it does not list: the autonomy phase (W2 and W3) was missing once."""
    import migrate_backlog_v2 as mig
    assert "autonomy" in bd.PHASES_IN_ORDER
    order = {ph: i for i, ph in enumerate(bd.PHASES_IN_ORDER)}
    for needed in mig.PHASES["autonomy"].get("requires_routines", ()):
        owner = next((ph for ph in bd.PHASES_IN_ORDER if ph != "autonomy" and needed in mig.PHASES[ph].get("routines", ())), None)
        assert owner is None or order[owner] < order["autonomy"], (needed, owner)       # whatever autonomy needs is applied before it
    assert all(order[p] < order["autonomy"] for p in ("relations", "groups", "sync"))
