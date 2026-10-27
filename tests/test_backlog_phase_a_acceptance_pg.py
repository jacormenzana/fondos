# tests/test_backlog_phase_a_acceptance_pg.py
# -*- coding: utf-8 -*-
"""The Phase A acceptance checker (scripts/audit/backlog_phase_a_acceptance.py) against the REAL DDL (FND-0331). Hermetic (pg_conn): the checker must
accept the database after step A-1 and after step A-2, and reject the states before them, using the same catalog facts the live run reads.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_phase_a_acceptance_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_phase_a_acceptance as acc  # noqa: E402
import migrate_backlog_links as links  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402

SCHEMA_SQL = (_ROOT / "db" / "pg_backlog" / "00_schema.sql").read_text(encoding="utf-8")
LEGACY_RELEASE = links.ddl_block(_ROOT / "db" / "pg_backlog" / "10_kpis_and_release.sql", "backlog_sp_close_release")


@pytest.fixture
def pre(pg_conn):
    pg_conn.execute(SCHEMA_SQL)
    links.migrate(pg_conn)
    mig.migrate(pg_conn, "alias")
    pg_conn.execute(LEGACY_RELEASE)
    pg_conn.execute("CREATE ROLE backlog_writer NOLOGIN")
    pg_conn.execute("GRANT USAGE ON SCHEMA backlog TO backlog_writer")
    pg_conn.execute("GRANT SELECT, INSERT, UPDATE ON backlog.BACKLOG TO backlog_writer")
    pg_conn.execute("GRANT SELECT, INSERT ON backlog.BACKLOG_LOGS, backlog.backlog_link_event TO backlog_writer")
    pg_conn.execute("GRANT SELECT ON backlog.v_backlog_link, backlog.actor_alias TO backlog_writer")
    pg_conn.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA backlog TO backlog_writer")
    for i in (1, 2, 3):
        pg_conn.execute("INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority) "
                        "VALUES (%s, 't', 'FND', 'BUG', 'N/A', 'OPEN', 'LOW')", (f"FND-900{i}",))
    pg_conn.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES ('FND-9001', 'claude', 'old note')")
    return pg_conn


def _baseline(conn) -> dict:
    return dict(bc.history_fingerprint(conn=conn), tickets=bc.phase_a_catalog(conn=conn)["tickets"])


def _verify(conn, baseline, step, rc=0):
    after = bc.history_fingerprint(max_log_id=baseline["max_log_id"], max_event_id=baseline["max_event_id"], conn=conn)
    return (acc.evaluate_a1 if step == "a1" else acc.evaluate_a2)(rc, baseline, after, bc.phase_a_catalog(conn=conn))


def _failed(results):
    return [n for n, ok, _ in results if not ok]


def test_the_checker_accepts_the_database_after_a1_and_rejects_it_before(pre):
    base = _baseline(pre)
    before = _failed(_verify(pre, base, "a1"))
    assert len(before) >= 4 and "backlog_owner exists, is not a superuser and cannot log in" in before            # nothing applied: rejected
    mig.migrate(pre, "transitions")
    assert _failed(_verify(pre, base, "a1")) == []
    assert _failed(_verify(pre, base, "a1", rc=1)) == ["migration exit code is 0"]


def test_the_checker_accepts_the_database_after_a2_and_rejects_it_before(pre):
    base = _baseline(pre)
    mig.migrate(pre, "transitions")
    assert len(_failed(_verify(pre, base, "a2"))) >= 2                                                             # A-1 only: the writer can still write
    mig.migrate(pre, "revoke")
    assert _failed(_verify(pre, base, "a2")) == []
    assert _failed(_verify(pre, base, "a1")) == ["A-1 is additive: the writer lost nothing and cannot write the audit tables"]     # A-1 criteria no longer hold: expected


def test_the_checker_detects_a_rewritten_log(pre):
    base = _baseline(pre)
    mig.migrate(pre, "transitions")
    pre.execute("UPDATE backlog.BACKLOG_LOGS SET comment = 'rewritten' WHERE action_point_id = 'FND-9001'")
    assert "history up to the baseline ids is identical" in _failed(_verify(pre, base, "a1"))


def _verify_new(conn, baseline, step, rc=0):
    after = bc.history_fingerprint(max_log_id=baseline["max_log_id"], max_event_id=baseline["max_event_id"], conn=conn)
    ev = acc.evaluate_human if step == "human" else acc.evaluate_history
    return ev(rc, baseline, after, bc.phase_a_catalog(conn=conn), bc.identity_history_catalog(conn=conn))


def test_a1_still_verifies_after_tickets_were_opened_through_the_routines(pre):
    """Found by the live rehearsal: tickets opened AFTER A-1 carry a LIVE event, not a BACKFILL one; re-verifying A-1 (as after its re-application) must not fail for that."""
    base = _baseline(pre)
    mig.migrate(pre, "transitions")
    for _ in range(2):
        bc.open_ticket(title="later", nature="BUG", object_type="N/A", priority="LOW", session_id="FND_20261027_DEV_TEST", description="d", first_log="first log", actor="agent:claude-opus-5-5", conn=pre)
    cat = bc.phase_a_catalog(conn=pre)
    assert cat["tickets"] == 5 and cat["backfill_events"] == 3 and cat["tickets_without_status_event"] == 0
    assert _failed(_verify(pre, base, "a1")) == []


def test_the_checker_accepts_the_owner_role_step_after_it_and_rejects_the_state_before(pre):
    base = _baseline(pre)
    mig.migrate(pre, "transitions")
    assert _failed(_verify(pre, base, "a1")) == []                                              # A-1 as re-applied with FND-0337 still passes, now with fn_actor_authorized hardened
    assert len(_failed(_verify_new(pre, base, "human"))) >= 1                                   # no role yet
    mig.migrate(pre, "human")
    assert _failed(_verify_new(pre, base, "human")) == []
    assert _failed(_verify_new(pre, base, "human", rc=1)) == ["migration exit code is 0"]


def test_the_checker_accepts_the_history_step_after_it_and_rejects_the_state_before(pre):
    base = _baseline(pre)
    mig.migrate(pre, "transitions")
    assert len(_failed(_verify_new(pre, base, "history"))) >= 4                                 # nothing applied
    mig.migrate(pre, "history")
    assert _failed(_verify_new(pre, base, "history")) == []
    pre.execute("UPDATE backlog.BACKLOG_LOGS SET comment = 'rewritten' WHERE action_point_id = 'FND-9001'")
    assert "history up to the baseline ids is identical" in _failed(_verify_new(pre, base, "history"))


def test_the_identity_catalog_reports_what_the_checks_rely_on(pre):
    mig.migrate(pre, "transitions")
    mig.migrate(pre, "human")
    mig.migrate(pre, "history")
    mig.migrate(pre, "revoke")
    ident = bc.identity_history_catalog(conn=pre)
    assert ident["human_role"] == {"superuser": False, "can_login": True, "member_of_writer": True} and ident["authorization_function"] is True
    assert ident["calls_authorization"] == {"sp_open_ticket": True, "sp_transition": True, "trg_actor_known": True}     # the A-2 author trigger carries the rule too
    h = ident["history"]
    assert h["owner"] == "backlog_owner" and h["append_only"] is True and h["view"] is True and h["writer_can_insert"] is False
    assert all(col in h["trigger_def"] for col in bc.DESCRIPTIVE_FIELDS)


def test_the_identity_catalog_is_empty_but_safe_before_the_steps(pre):
    ident = bc.identity_history_catalog(conn=pre)
    assert ident["human_role"] is None and ident["authorization_function"] is False and ident["history"] is None and ident["calls_authorization"] == {}


def test_the_catalog_reports_the_hardening_the_checker_relies_on(pre):
    mig.migrate(pre, "transitions")
    cat = bc.phase_a_catalog(conn=pre)
    assert cat["owner_role"] == {"superuser": False, "can_login": False}
    assert {r["name"] for r in cat["routines"]} >= set(acc.HARDENED)
    assert cat["backfill_events"] == 3 and cat["tickets"] == 3 and cat["max_ticket_id"] == "FND-9003"
    assert "governed_writes" in cat["triggers"] and cat["writer"]["can_execute_transition"] is True
