# tests/test_backlog_ingest_and_wait_pg.py
# -*- coding: utf-8 -*-
"""The post-deployment automation against the real DDL (hermetic throwaway PostgreSQL, rolled-back savepoint): the four-fact probe, and the exactly-once ingestion into real tickets.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_ingest_and_wait_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_ingest_pending_logs as ing  # noqa: E402
import backlog_wait_for_deployment as wf  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from test_backlog_iam_pg import mk_login  # noqa: E402,F401
from test_backlog_transitions_pg import _base, _mk  # noqa: E402

TEXT = """### FND-9001 / 01 - First
actor: agent:claude-sonnet-5-5
date: 2026-10-30
source: a test

First body.

### FND-9001 / 02 - Second
actor: agent:claude-sonnet-5-5
date: 2026-10-30
source: a test

Second body.
"""


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    return pg_conn


def test_the_probe_sees_each_step_as_it_is_applied(db):
    assert wf.probe(db) == {"override_column": False, "actor_login_event": False, "sp_append_log": False, "iam_roles": False}
    mig.migrate(db, "transitions")
    mig.migrate(db, "relations")
    half = wf.probe(db)
    assert half["override_column"] is True and half["sp_append_log"] is False and not wf.ready(half)       # step relations alone is NOT enough to start verifying
    mig.migrate(db, "iam")
    assert wf.probe(db) == {"override_column": True, "actor_login_event": True, "sp_append_log": True, "iam_roles": True} and wf.ready(wf.probe(db))
    mig.migrate(db, "iam", down=True)
    assert wf.probe(db)["actor_login_event"] is True and wf.probe(db)["sp_append_log"] is False and not wf.ready(wf.probe(db))        # the rollback keeps the table, drops the routine and roles


def test_the_ingestion_into_real_tickets_is_exactly_once(db):
    mig.migrate(db, "transitions")
    mig.migrate(db, "relations")
    mig.migrate(db, "iam")
    db.execute("SELECT set_config('backlog.via_proc', 'on', true)")
    db.execute("INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority, session_id) VALUES ('FND-9001', 't', 'FND', 'BUG', 'N/A', 'OPEN', 'LOW', 'FND_20261030_DEV_X')")
    entries = ing.parse(TEXT)

    def state():
        ids = {t["action_point_id"] for t in bc.list_tickets(ids=["FND-9001", "FND-9002"], conn=db)}
        existing: dict = {}
        for row in bc.list_logs(ids=sorted(ids), conn=db):
            existing.setdefault(row["action_point_id"], []).append(row["comment"])
        return ids, existing

    ids, existing = state()
    assert ids == {"FND-9001"}
    out = ing.ingest(entries, ticket_ids=ids, existing_comments=existing, append=lambda t, a, c: bc.append_log(t, a, c, conn=db), apply=True)
    assert [s for _, s, _ in out] == ["APPENDED", "APPENDED"] and all(isinstance(i, int) for _, _, i in out)
    ids, existing = state()
    again = ing.ingest(entries, ticket_ids=ids, existing_comments=existing, append=lambda t, a, c: pytest.fail("appended twice"), apply=True)
    assert [s for _, s, _ in again] == ["ALREADY", "ALREADY"]
    notes = [r["comment"] for r in bc.list_logs(ids=["FND-9001"], conn=db) if r["comment"].startswith("[PENDING_FND_LOGS")]
    assert len(notes) == 2 and notes[0].startswith("[PENDING_FND_LOGS FND-9001/01] First (decision date 2026-10-30; source: a test)\n\nFirst body.")
    assert {r["username"] for r in bc.list_logs(ids=["FND-9001"], conn=db) if r["comment"].startswith("[PENDING")} == {"agent:claude-sonnet-5-5"}


def test_an_entry_for_a_ticket_that_does_not_exist_is_reported_not_written(db):
    entries = ing.parse(TEXT.replace("FND-9001 / 02", "FND-9002 / 01"))
    ids = {t["action_point_id"] for t in bc.list_tickets(ids=["FND-9001", "FND-9002"], conn=db)}
    out = ing.ingest(entries, ticket_ids=ids, existing_comments={}, append=lambda *a: pytest.fail("must not append"), apply=True)
    assert [s for _, s, _ in out] == ["MISSING_TICKET", "MISSING_TICKET"]


def test_the_probe_reads_pg_catalog_so_a_login_without_table_privileges_still_sees_the_column(db):
    """The staging watch uses a READ-ONLY login: information_schema would hide gate_switch.override from a login with no privilege on the table."""
    mig.migrate(db, "transitions")
    mig.migrate(db, "relations")
    db.execute("CREATE ROLE staging_probe_ro LOGIN")
    db.execute("SET SESSION AUTHORIZATION staging_probe_ro")
    try:
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'backlog' AND table_name = 'gate_switch'").fetchone()[0] == 0     # hidden from this login
        assert wf.probe(db)["override_column"] is True                                                                                                        # but not from the probe
        sig = wf.staging_signature(db)
        assert set(sig) == set(wf.REQUIRED) | {"routine_rows_xmin", "binding_events_inserted"} and isinstance(sig["routine_rows_xmin"], int)
    finally:
        db.execute("RESET SESSION AUTHORIZATION")


def test_reapplying_the_ddl_changes_the_signature_even_when_nothing_else_does(db):
    """CREATE OR REPLACE rewrites the routines' catalog rows: an idempotent re-application on the staging replica is visible without any privilege."""
    mig.migrate(db, "transitions")
    mig.migrate(db, "relations")
    mig.migrate(db, "iam")
    with db.transaction():
        db.execute("SELECT 1")
    first = wf.staging_signature(db)["routine_rows_xmin"]
    with db.transaction():
        mig.migrate(db, "iam")                      # the same step again: no object is new
    second = wf.staging_signature(db)["routine_rows_xmin"]
    assert second != first
    assert wf.misfire({k: False for k in wf.REQUIRED}, wf.staging_signature(db), dict(wf.staging_signature(db), routine_rows_xmin=first)) is True
