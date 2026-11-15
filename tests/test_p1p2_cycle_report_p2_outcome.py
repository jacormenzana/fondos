"""
tests/test_p1p2_cycle_report_p2_outcome.py -- FND-0327: the P2 outcome line of the post-cycle report (pure functions, no database).

An all-cache P2 run used to be indistinguishable from a P2 that did nothing: the console showed only Inicio / Fin OK. The report now says what P2 did in THIS cycle
(recomputed / cache only / unreadable summary / not run, and for "not run" whether P2 started at all), reading the runs since the cycle's own start instant.
The Postgres side (a cycle crossing midnight, read in a UTC session) is in tests/test_p1p2_cycle_report_p2_outcome_pg.py.
"""
from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "p1p2_cycle_report_p2_pure", Path(__file__).resolve().parent.parent / "scripts" / "launch" / "p1p2_cycle_report.py")
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)

_SUMMARY = ("run_id=20261115_001739 processed={p} skipped={s} errors={e} warnings=4 defl_skipped=0 written=2115 quarantined=3 ols_funds=0 "
            "cond_guard_funds=0 betas_nulled=0 elapsed=360s")


def _row(p=122, s=3621, e=0, status="OK"):
    return (status, _SUMMARY.format(p=p, s=s, e=e), datetime(2026, 11, 14, 23, 23, 33, tzinfo=timezone.utc))


def _report(**over):
    r = {
        "stamp": "20261004_100000", "since": "2026-10-04", "today": "2026-10-04",
        "universe": {"active": 1000, "inactive": 50, "total": 1050},
        "nature": {"Renta Variable": 600, "Mixtos": 400},
        "kiid_status": {"OK": 995, "WRONG_DOC": 5},
        "reliability": {"srri_null": 6, "profile_null": 0, "dqf_warn": 70, "dqf_missing": 1},
        "wrong_doc_stale": [], "low_confidence_nature": 3,
        "dq_cycle": [], "nav_stale": [], "nav_sources": {"OK/OK": 990}, "real_orphans": {},
        "coverage": {"sharpe": 980}, "alerts": {"OK": 90},
    }
    r.update(over)
    return r


def test_the_cycle_start_is_an_aware_instant_in_the_stamp_zone_and_a_missing_stamp_falls_back_to_midnight_of_since():
    start, from_stamp = cr.cycle_start_from_stamp("20261114_235743", date(2026, 11, 14))
    assert from_stamp is True and start.tzinfo is not None
    assert start.isoformat() == "2026-11-14T23:57:43+01:00"                                  # CET in November: 22:57:43 UTC
    start, from_stamp = cr.cycle_start_from_stamp(None, date(2026, 11, 14))
    assert from_stamp is False and start.isoformat() == "2026-11-14T00:00:00+01:00"
    start, from_stamp = cr.cycle_start_from_stamp("not-a-stamp", date(2026, 7, 1))
    assert from_stamp is False and start.isoformat() == "2026-07-01T00:00:00+02:00", "CEST in July: the offset comes from the zone, not from a constant"


def test_the_cycle_start_does_not_depend_on_the_process_zone(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    start, _ = cr.cycle_start_from_stamp("20261114_235743", date(2026, 11, 14))
    assert start.utcoffset().total_seconds() == 3600


def test_the_zone_fallback_is_still_aware_when_the_tz_database_is_missing(monkeypatch):
    def _boom(_name):
        raise KeyError("no tzdata")
    monkeypatch.setattr(cr, "ZoneInfo", _boom)
    start, from_stamp = cr.cycle_start_from_stamp("20261114_235743", date(2026, 11, 14))
    assert from_stamp is True and start.tzinfo is not None


def test_a_summary_with_processed_funds_is_recomputed_and_one_with_none_is_cache_only():
    out = cr.classify_p2_outcome(_row(), True)
    assert out["state"] == "recomputed" and (out["processed"], out["skipped"], out["written"], out["errors"]) == (122, 3621, 2115, 0)
    out = cr.classify_p2_outcome(_row(p=0, s=3743), True)
    assert out["state"] == "cache_only" and out["skipped"] == 3743


def test_an_unreadable_summary_is_visible_never_skipped():
    out = cr.classify_p2_outcome(("OK", "garbage from last month", None), True)
    assert out["state"] == "unparsed" and out["problems"]
    assert any("ilegible" in i for i in cr.p2_outcome_attention(out))
    assert "ilegible" in cr.p2_outcome_line(out)


def test_no_summary_is_not_run_and_started_tells_an_aborted_p2_from_a_cycle_without_p2():
    assert cr.classify_p2_outcome(None, False) == {"state": "not_run", "started": False}
    assert cr.classify_p2_outcome(None, True) == {"state": "not_run", "started": True}
    aborted = {**cr.classify_p2_outcome(None, True), "cycle_start": "2026-11-14T23:57:43+01:00", "window_from_stamp": True}
    p1_only = {**cr.classify_p2_outcome(None, False), "cycle_start": "2026-11-14T23:57:43+01:00", "window_from_stamp": True}
    assert any("se interrumpio" in i for i in cr.p2_outcome_attention(aborted))
    assert cr.p2_outcome_attention(p1_only) == [], "a P1-only cycle has no P2 to report: no [ATENCION]"
    assert "no corrio" in cr.p2_outcome_line(p1_only) and "empezo pero no dejo RUN_SUMMARY" in cr.p2_outcome_line(aborted)


def test_the_outcome_line_and_the_attention_for_each_state():
    rec = cr.classify_p2_outcome(_row(), True)
    assert cr.p2_outcome_line(rec) == "P2 del ciclo: 122 fondos recalculados, 3621 servidos de cache, 2115 filas escritas, 0 errores, 4 avisos"
    cache = cr.classify_p2_outcome(_row(p=0, s=3743), True)
    assert "ningun fondo recalculado (todo en cache" in cr.p2_outcome_line(cache)
    assert cr.p2_outcome_attention(rec) == [] and cr.p2_outcome_attention(cache) == []
    assert any("3 errores" in i for i in cr.p2_outcome_attention(cr.classify_p2_outcome(_row(e=3), True)))
    assert cr.p2_outcome_attention(None) == [] and cr.p2_outcome_attention({"error": "x"}) == []


def test_a_manual_run_without_a_stamp_says_the_window_is_midnight_of_since():
    p2 = {**cr.classify_p2_outcome(None, False), "cycle_start": "2026-11-14T00:00:00+01:00", "window_from_stamp": False}
    assert "medianoche de --since" in cr.p2_outcome_line(p2)


def test_the_text_report_carries_the_line_and_an_old_report_without_it_still_renders():
    rep = _report(p2_outcome={**cr.classify_p2_outcome(_row(), True), "cycle_start": "x", "window_from_stamp": True})
    text = cr.render_text(rep, None, cr.attention_items(rep, None))
    assert "P2 del ciclo: 122 fondos recalculados" in text
    old = _report()                                                                      # no p2_outcome key: a report written before FND-0327
    assert "P2 del ciclo" not in cr.render_text(old, None, cr.attention_items(old, None))


def test_collect_p2_outcome_is_fail_soft_and_rolls_back():
    class _Boom:
        rolled = False

        def execute(self, *a, **k):
            raise RuntimeError("db gone")

        def rollback(self):
            _Boom.rolled = True
    out = cr.collect_p2_outcome(_Boom(), datetime(2026, 11, 14, 22, 57, tzinfo=timezone.utc), True)
    assert "RuntimeError" in out["error"] and _Boom.rolled is True
