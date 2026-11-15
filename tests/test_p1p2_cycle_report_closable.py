"""
tests/test_p1p2_cycle_report_closable.py -- FND-0423: the post-cycle sweep of closing conditions in the cycle report (pure functions plus the collector with stubs, no database).

An open ticket whose cause disappeared never closed by itself: the autopilot only evaluates IN_PROGRESS / READY_FOR_DEPLOY tickets with a delivery. The report now runs the closing conditions of the open tickets
that carry one beyond the default `tests_pass` and lists the ones where ALL of them hold. It is read-only: closing stays an event.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "p1p2_cycle_report_closable", Path(__file__).resolve().parent.parent / "scripts" / "launch" / "p1p2_cycle_report.py")
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)

DB = ("db_check", "table_rows", "series_benchmark", "==", 0)
TP = ("tests_pass",)


def _rows():
    return [("FND-1", "READY", [(True, "table series_benchmark: 0 row(s)")]),         # closable: a real condition and it holds
            ("FND-2", "READY", [(True, "tests_pass: receipt")]),                          # only the default condition: the autopilot's, not a finding
            ("FND-3", "MANUAL", [(True, "a"), (None, "owner confirms")]),                 # waits for a person
            ("FND-4", "BLOCKED", [(False, "flag off")]),
            ("FND-5", "READY", [(True, "x")]),                                            # already CLOSED: never listed
            ("FND-6", "READY", [(True, "y")])]                                            # not an open ticket in the status map


def _spec():
    return {"FND-1": [DB, TP], "FND-2": [TP], "FND-3": [("manual", "owner confirms")], "FND-4": [("flag", "X_ENABLED", True)], "FND-5": [DB], "FND-6": [DB]}


STATUSES = {"FND-1": "DEFERRED", "FND-2": "IN_PROGRESS", "FND-3": "TODO", "FND-4": "OPEN", "FND-5": "CLOSED"}


def test_only_an_open_ticket_with_a_real_condition_that_fully_holds_is_closable():
    out = cr.select_closable(_rows(), _spec(), STATUSES)
    assert [c["ticket"] for c in out["closable"]] == ["FND-1"]
    assert out["closable"][0]["status"] == "DEFERRED" and out["closable"][0]["kinds"] == ["db_check", "tests_pass"]
    assert out["closable"][0]["evidence"] == ["table series_benchmark: 0 row(s)"]
    assert out["manual"] == ["FND-3"] and out["blocked"] == 1 and out["evaluated"] == 6


def test_a_ticket_with_only_the_default_condition_is_left_to_the_autopilot_and_a_closed_one_never_listed():
    out = cr.select_closable([("FND-2", "READY", [(True, "r")]), ("FND-5", "READY", [(True, "r")])], _spec(), STATUSES)
    assert out["closable"] == [] and out["manual"] == [] and out["blocked"] == 0


def test_the_line_and_the_attention_for_each_state():
    ok = {"applied": True, **cr.select_closable(_rows(), _spec(), STATUSES)}
    assert cr.closable_line(ok) == "Tickets cerrables por evidencia: 1 de 6 evaluados (FND-1 (DEFERRED)); 1 esperan una condicion manual, 1 con alguna condicion sin cumplir"
    item, = cr.closable_attention(ok)
    assert "1 ticket(s) abiertos con TODAS sus condiciones de cierre cumplidas (FND-1)" in item and "es un evento" in item
    none = {"applied": True, "closable": [], "manual": [], "blocked": 0, "evaluated": 0}
    assert "ninguno" in cr.closable_line(none) and cr.closable_attention(none) == []
    assert "sin aplicar" in cr.closable_line({"applied": False}) and cr.closable_attention({"applied": False}) == []
    assert "no disponible (boom)" in cr.closable_line({"error": "boom"}) and cr.closable_attention({"error": "boom"}) == []
    assert cr.closable_attention(None) == []


def test_the_report_text_carries_the_line_and_an_old_report_without_the_key_still_renders():
    base = {"stamp": "s", "since": "2026-11-01", "today": "2026-11-15", "universe": {"active": 1000, "inactive": 50, "total": 1050}, "nature": {"Mixtos": 1},
            "kiid_status": {"OK": 1}, "reliability": {"srri_null": 0, "profile_null": 0, "dqf_warn": 0, "dqf_missing": 0}, "wrong_doc_stale": [], "low_confidence_nature": 0,
            "dq_cycle": [], "nav_stale": [], "nav_sources": {"OK/OK": 1}, "real_orphans": {}, "coverage": {"sharpe": 1}, "alerts": {"OK": 1}}
    rep = {**base, "closable_on_evidence": {"applied": True, **cr.select_closable(_rows(), _spec(), STATUSES)}}
    text = cr.render_text(rep, None, cr.attention_items(rep, None))
    assert "Tickets cerrables por evidencia: 1 de 6" in text and "[ATENCION] 1 ticket(s) abiertos con TODAS" in text
    assert "Tickets cerrables" not in cr.render_text(base, None, cr.attention_items(base, None))


class _Stubs:
    """Replaces shared.backlog_client's three reads and the probe module, and remembers what the probe was asked to evaluate."""
    def __init__(self, monkeypatch, stored, tickets, rows=None, boom=False):
        import shared.backlog_client as bc
        self.asked = None
        stub = types.ModuleType("rfd_matrix_probe")

        def evaluate(spec, evidence=None):
            self.asked = dict(spec)
            if boom:
                raise RuntimeError("no database")
            return rows or []
        stub.evaluate = evaluate
        monkeypatch.setitem(sys.modules, "rfd_matrix_probe", stub)
        monkeypatch.setattr(bc, "close_conditions", lambda **k: stored)
        monkeypatch.setattr(bc, "list_tickets", lambda **k: tickets)


def test_the_collector_evaluates_only_open_tickets_with_a_condition_beyond_the_default(monkeypatch):
    stored = {"FND-1": [DB, TP], "FND-2": [TP], "FND-5": [DB]}                 # FND-5 is closed, FND-2 has only the default
    tickets = [{"action_point_id": "FND-1", "status": "DEFERRED"}, {"action_point_id": "FND-2", "status": "IN_PROGRESS"}]
    stubs = _Stubs(monkeypatch, stored, tickets, rows=[("FND-1", "READY", [(True, "ok")])])
    out = cr.collect_closable_on_evidence()
    assert stubs.asked == {"FND-1": [DB, TP]}
    assert out["applied"] is True and [c["ticket"] for c in out["closable"]] == ["FND-1"]


def test_the_collector_with_nothing_to_evaluate_does_not_run_the_probes(monkeypatch):
    stubs = _Stubs(monkeypatch, {"FND-2": [TP]}, [{"action_point_id": "FND-2", "status": "IN_PROGRESS"}], boom=True)
    out = cr.collect_closable_on_evidence()
    assert stubs.asked is None and out == {"applied": True, "closable": [], "manual": [], "blocked": 0, "evaluated": 0}


def test_the_collector_reports_an_unapplied_store_and_never_raises(monkeypatch):
    _Stubs(monkeypatch, None, [])
    assert cr.collect_closable_on_evidence() == {"applied": False}
    _Stubs(monkeypatch, {"FND-1": [DB]}, [{"action_point_id": "FND-1", "status": "OPEN"}], boom=True)
    assert "RuntimeError" in cr.collect_closable_on_evidence()["error"]
