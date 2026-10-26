# tests/test_backlog_client_a1_pg.py
# -*- coding: utf-8 -*-
"""Client side of the Phase A transition kernel (FND-0331; shared/backlog_client.py against db/pg_backlog/30_transitions.sql).

Hermetic (pg_conn, rolled-back savepoint). Covers open_ticket / transition / shadow_progress, the explicit-actor rule (D5), the feature detection that
keeps the legacy path until the owner applies A-1, and report_incident opening its tickets through sp_open_ticket. The legacy path of
report_incident is covered by tests/test_backlog_incident_overlap_pg.py (base schema only), which must keep passing unchanged.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_client_a1_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_links as links  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402

SCHEMA_SQL = (_ROOT / "db" / "pg_backlog" / "00_schema.sql").read_text(encoding="utf-8")
LEGACY_RELEASE = links.ddl_block(_ROOT / "db" / "pg_backlog" / "10_kpis_and_release.sql", "backlog_sp_close_release")
ME = "agent:claude-opus-5-5"
SESSION = "FND_20261026_DEV_TEST"


class _Borrowed:
    def __init__(self, conn):
        self._c = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._c.cursor()

    def commit(self):
        pass


def _base(pg_conn):
    pg_conn.execute(SCHEMA_SQL)
    links.migrate(pg_conn)
    mig.migrate(pg_conn, "alias")
    pg_conn.execute(LEGACY_RELEASE)
    return pg_conn


@pytest.fixture
def pre(pg_conn):
    return _base(pg_conn)


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    mig.migrate(pg_conn, "transitions")
    return pg_conn


@pytest.fixture
def incident_db(db, monkeypatch):
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", "postgresql://unused")
    monkeypatch.setattr(bc, "psycopg", SimpleNamespace(connect=lambda *a, **k: _Borrowed(db)))
    monkeypatch.setattr(bc, "_overlap_settings", lambda: None)
    return db


def _open(conn, **kw):
    a = dict(title="t", nature="BUG", object_type="N/A", priority="LOW", session_id=SESSION, description="d", first_log="first log of it", actor=ME)
    a.update(kw)
    return bc.open_ticket(conn=conn, **a)


# --- pure ----------------------------------------------------------------------------------------------------------------------------------

def test_make_session_id_follows_the_norm_and_cleans_the_topic():
    from datetime import datetime, timezone
    day = datetime(2026, 10, 26, tzinfo=timezone.utc)
    assert bc.make_session_id("backlog v2", "WKSH", today=day) == "FND_20261026_WKSH_BACKLOG_V2"
    assert bc.make_session_id("  Auto-detect!  ", "REVB", today=day) == "FND_20261026_REVB_AUTO-DETECT"
    assert bc.make_session_id("x", today=day) == "FND_20261026_DEV_X"
    for bad in (("topic", "XXXX"), ("", "DEV"), ("!!!", "DEV")):
        with pytest.raises(ValueError):
            bc.make_session_id(*bad, today=day)


def test_resolve_actor_is_explicit_first_then_the_environment_and_never_a_guess(monkeypatch):
    monkeypatch.setenv("FONDOS_ACTOR", "human:jacor")
    assert bc.resolve_actor("agent:claude-opus-5-5") == "agent:claude-opus-5-5"            # the explicit one wins
    assert bc.resolve_actor() == "human:jacor"                                              # the validated alternative
    with pytest.raises(bc.ActorUnknown):
        bc.resolve_actor("claude")                                                           # an explicit bad one is not rescued by the environment
    monkeypatch.delenv("FONDOS_ACTOR")
    with pytest.raises(bc.ActorUnknown):
        bc.resolve_actor()


def test_sp_signatures_the_client_probes_match_the_ddl():
    ddl = mig.ddl_block("transitions")
    assert "FUNCTION backlog.sp_open_ticket(" in ddl and "FUNCTION backlog.sp_transition(" in ddl
    assert bc._SIG_OPEN.count(",") == 10 and bc._SIG_TRANSITION.count(",") == 13            # 11 and 14 arguments


# --- feature detection -------------------------------------------------------------------------------------------------------------------------

def test_the_store_is_reported_absent_before_a1_and_present_after(pre):
    assert bc.transitions_store_present(conn=pre) is False
    mig.migrate(pre, "transitions")
    assert bc.transitions_store_present(conn=pre) is True


def test_the_write_api_fails_loudly_before_a1(pre):
    with pytest.raises(psycopg.errors.UndefinedFunction):
        with pre.transaction():
            _open(pre)


# --- open_ticket / transition -----------------------------------------------------------------------------------------------------------------

def test_open_ticket_and_transition_round_trip_with_an_explicit_actor(db):
    ap = _open(db, title="a ticket", nature="REQ")
    assert db.execute("SELECT reporter, status FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone() == (ME, "OPEN")
    ev = bc.transition(ap, "IN_PROGRESS", session_id=SESSION, comment="starting the work now", actor="human:jacor", assignee=ME, conn=db)
    assert isinstance(ev, int)
    assert db.execute("SELECT status, assignee FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone() == ("IN_PROGRESS", ME)
    assert db.execute("SELECT actor FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()[0] == "human:jacor"
    bc.transition(ap, "READY_FOR_DEPLOY", session_id=SESSION, comment="developed and tested", actor=ME, release_version="R2026.10-CLI-01", conn=db)
    bc.transition(ap, "CLOSED", session_id=SESSION, comment="deployed and verified", actor=ME, solution_type="CODE_FIX", conn=db)
    assert db.execute("SELECT status, solution_type, release_version FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone() == (
        "CLOSED", "CODE_FIX", "R2026.10-CLI-01")


def test_the_actor_comes_from_the_environment_when_not_given_and_is_never_guessed(db, monkeypatch):
    monkeypatch.delenv("FONDOS_ACTOR", raising=False)
    with pytest.raises(bc.ActorUnknown):
        bc.open_ticket(title="t", nature="BUG", object_type="N/A", priority="LOW", session_id=SESSION, description="d", first_log="first log", conn=db)
    monkeypatch.setenv("FONDOS_ACTOR", "system:release")
    ap = bc.open_ticket(title="t", nature="BUG", object_type="N/A", priority="LOW", session_id=SESSION, description="d", first_log="first log", conn=db)
    assert db.execute("SELECT reporter FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone()[0] == "system:release"


def test_the_database_refusals_surface_as_exceptions(db):
    ap = _open(db)
    for kw, match in (({"to_status": "READY_FOR_DEPLOY"}, "not a legal move"), ({"to_status": "BLOCKED"}, "not a legal move")):
        with pytest.raises(psycopg.errors.Error, match=match):
            with db.transaction():
                bc.transition(ap, kw["to_status"], session_id=SESSION, comment="an illegal move on purpose", actor=ME, conn=db)
    with pytest.raises(psycopg.errors.Error, match="session id"):
        with db.transaction():
            bc.transition(ap, "TODO", session_id="free text", comment="a bad session id", actor=ME, conn=db)
    assert db.execute("SELECT status FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone()[0] == "OPEN"


def test_shadow_progress_reads_the_stage(db):
    assert bc.shadow_progress(conn=db) == {"last_v00_ts": None, "v00_total": 0, "live_non_system_since_last_v00": 0}
    ap = _open(db)
    bc.transition(ap, "TODO", session_id=SESSION, comment="triaged by the owner", actor="human:jacor", conn=db)
    assert bc.shadow_progress(conn=db)["live_non_system_since_last_v00"] == 1


# --- report_incident through the routine --------------------------------------------------------------------------------------------------------

def test_report_incident_opens_through_the_routine_with_a_canonical_reporter(incident_db):
    apid = bc.report_incident(object_name="run_pipeline.py", scenario_description="ValueError: boom in the P2 writer", schema_name="silver")
    row = incident_db.execute("SELECT status, nature, priority, reporter, object_type, object_name, schema_name, session_id FROM backlog.BACKLOG "
                              "WHERE action_point_id = %s", (apid,)).fetchone()
    assert row[:4] == ("OPEN", "BUG", "HIGH", "system:autodetect") and row[4:7] == ("JOB", "run_pipeline.py", "silver")
    assert row[7].startswith("FND_") and "_REVB_AUTODETECT" in row[7]
    logs = incident_db.execute("SELECT username, comment FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s", (apid,)).fetchall()
    assert logs == [("system:autodetect", "Auto-opened: ValueError: boom in the P2 writer")]
    assert incident_db.execute("SELECT from_status, to_status, actor FROM backlog.status_event WHERE action_point_id = %s", (apid,)).fetchone() == (
        None, "OPEN", "system:autodetect")
    assert incident_db.execute("SELECT count(*) FROM backlog.raw_update_event").fetchone()[0] == 0


def test_report_incident_reincidence_and_the_suggestion_text_are_unchanged(incident_db, monkeypatch):
    first = bc.report_incident(object_name="run_pipeline.py", scenario_description="ValueError: boom")
    again = bc.report_incident(object_name="run_pipeline.py", scenario_description="ValueError: boom again")
    assert again == first
    assert [r[0] for r in incident_db.execute("SELECT comment FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s ORDER BY log_id", (first,)).fetchall()][-1] == (
        "Reincidence detected: ValueError: boom again")
    monkeypatch.setattr(bc, "_overlap_settings", lambda: (0.99, 0.0))
    related = bc.report_incident(object_name="other.py", scenario_description="ValueError: boom")
    assert related != first
    assert "Possibly related (NOT merged): " + first in incident_db.execute(
        "SELECT comment FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s ORDER BY log_id LIMIT 1", (related,)).fetchone()[0]


def test_report_incident_still_works_without_a1_and_with_the_legacy_reporter(pre, monkeypatch):
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", "postgresql://unused")
    monkeypatch.setattr(bc, "psycopg", SimpleNamespace(connect=lambda *a, **k: _Borrowed(pre)))
    monkeypatch.setattr(bc, "_overlap_settings", lambda: None)
    apid = bc.report_incident(object_name="run_pipeline.py", scenario_description="ValueError: boom")
    assert pre.execute("SELECT reporter, status FROM backlog.BACKLOG WHERE action_point_id = %s", (apid,)).fetchone() == ("SYSTEM_AUTODETECT", "OPEN")
    assert pre.execute("SELECT to_regclass('backlog.status_event')").fetchone()[0] is None                       # nothing of A-1 was needed


def test_report_incident_never_raises_when_the_routine_itself_fails(incident_db, monkeypatch, tmp_path):
    monkeypatch.setenv("FONDOS_BACKLOG_FALLBACK_LOG", str(tmp_path / "fallback.jsonl"))
    apid = bc.report_incident(object_name="x.py", scenario_description="boom", object_type="NOT_A_TYPE")             # violates the object_type CHECK inside the routine
    assert apid is None and (tmp_path / "fallback.jsonl").exists()                                                  # recorded to the local fallback, pipeline unharmed
