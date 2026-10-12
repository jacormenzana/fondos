# proyecto1/tests/test_statistical_audit_exception_events_pg.py
# -*- coding: utf-8 -*-
"""FND-0263 on the hermetic Postgres container (python scripts/ops/run_pg_tests.py): the event writers keep
control.audit_accepted_finding equal to its history in one transaction, the trigger makes the history append-only, and the owner
migration backfills today's residuals and is idempotent. The writers commit, so they run in a throwaway schema with PRODUCTION
transaction semantics (autocommit off); the trigger and the migration run on the real control schema inside a rolled-back savepoint."""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts", "ops"))

import migrate_audit_governance as mig  # noqa: E402
from shared.statistical_audit import accepted_residuals as ar  # noqa: E402
from shared.statistical_audit import persistence as ps  # noqa: E402

D = "p2_metrics"
RULE = "WINDOW_FISHER_IDENTITY"
UNTIL = date.today() + timedelta(days=60)


@pytest.fixture()
def tx_conn(pg_session_conn, pg_conn_module_schema):
    """Throwaway schema with copies of the real tables (constraints, defaults, identity; no FK, no trigger), autocommit OFF like
    shared.db.get_connection(); restored before the schema fixture's teardown."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    for t in ("audit_accepted_finding", "audit_exception_event"):
        conn.execute(f"CREATE TABLE {t} (LIKE control.{t} INCLUDING ALL)")
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.autocommit = True


def _table(conn):
    return sorted(tuple(r) for r in conn.execute(
        "SELECT rule_id, isin, ap_id, accepted_by, expires_at FROM audit_accepted_finding WHERE domain = %s", (D,)).fetchall())


def test_grant_is_pending_and_approve_activates_it_in_one_transaction(tx_conn):
    conn = tx_conn
    gid = ps.grant_exception(conn, D, RULE, ["B", "A", "A"], "FND-0250", "a reason long enough", UNTIL, "ana")
    assert _table(conn) == []                                                             # nothing accepted before the approval
    ps.approve_exception(conn, gid, "bob")
    assert _table(conn) == [(RULE, "A", "FND-0250", "bob", UNTIL), (RULE, "B", "FND-0250", "bob", UNTIL)]
    assert ar.verify_governed(conn, D)["drift"] == []
    kinds = [r[0] for r in conn.execute("SELECT event_type FROM audit_exception_event ORDER BY event_id").fetchall()]
    assert kinds == ["GRANT", "APPROVE"]


def test_a_grant_cannot_be_approved_twice_nor_after_a_full_revoke_nor_once_lapsed(tx_conn):
    conn = tx_conn
    gid = ps.grant_exception(conn, D, RULE, ["A"], "FND-0250", "a reason long enough", UNTIL, "ana")
    ps.approve_exception(conn, gid, "ana")
    with pytest.raises(ValueError, match="APPROVED"):
        ps.approve_exception(conn, gid, "ana")
    g2 = ps.grant_exception(conn, D, RULE, ["B"], "FND-0250", "a reason long enough", UNTIL, "ana")
    ps.revoke_exception(conn, g2, "ana", "OWNER", "withdrawn before approval")
    with pytest.raises(ValueError, match="REVOKED"):
        ps.approve_exception(conn, g2, "ana")
    g3 = ps.grant_exception(conn, D, RULE, ["C"], "FND-0250", "a reason long enough", date.today() + timedelta(days=1), "ana")
    with pytest.raises(ValueError, match="expired"):
        ps.approve_exception(conn, g3, "ana", today=date.today() + timedelta(days=2))
    assert [r[1] for r in _table(conn)] == ["A"]


def test_a_revoke_removes_only_its_isins_and_needs_a_known_cause(tx_conn):
    conn = tx_conn
    gid = ps.grant_exception(conn, D, RULE, ["A", "B", "C"], "FND-0250", "a reason long enough", UNTIL, "ana")
    ps.approve_exception(conn, gid, "ana")
    with pytest.raises(ValueError, match="cause"):
        ps.revoke_exception(conn, gid, "ana", "BECAUSE", "a reason long enough")
    with pytest.raises(ValueError, match="not in grant"):
        ps.revoke_exception(conn, gid, "ana", "RESOLVED", "a reason long enough", ["Z"])
    ps.revoke_exception(conn, gid, "ana", "RESOLVED", "fixed, re-audit clean", ["B"])
    assert [r[1] for r in _table(conn)] == ["A", "C"]
    assert ar.verify_governed(conn, D)["drift"] == []


def test_a_renewal_replaces_the_expiry_and_ap_and_a_revoke_of_the_old_grant_leaves_it(tx_conn):
    conn = tx_conn
    later = UNTIL + timedelta(days=20)
    g1 = ps.grant_exception(conn, D, RULE, ["A", "B"], "FND-0250", "a reason long enough", UNTIL, "ana")
    ps.approve_exception(conn, g1, "ana")
    g2 = ps.grant_exception(conn, D, RULE, ["A"], "FND-0300", "renewed after review", later, "ana", renew=True)
    ps.approve_exception(conn, g2, "ana")
    ps.revoke_exception(conn, g1, "ana", "AP_CLOSED", "FND-0250 closed, B no longer accepted")
    assert _table(conn) == [(RULE, "A", "FND-0300", "ana", later)]
    assert conn.execute("SELECT event_type FROM audit_exception_event WHERE event_id = %s", (g2,)).fetchone()[0] == "RENEW"


def test_a_projection_edited_behind_the_history_is_detected(tx_conn):
    conn = tx_conn
    gid = ps.grant_exception(conn, D, RULE, ["A"], "FND-0250", "a reason long enough", UNTIL, "ana")
    ps.approve_exception(conn, gid, "ana")
    conn.execute("UPDATE audit_accepted_finding SET expires_at = expires_at + 365 WHERE isin = 'A'")   # the old UPSERT path
    conn.execute("INSERT INTO audit_accepted_finding (domain, rule_id, isin, ap_id, reason, expires_at) "
                 "VALUES (%s, %s, 'Z', 'FND-9999', 'no history', %s)", (D, RULE, UNTIL))
    conn.commit()
    assert {(d[0], d[1]) for d in ar.verify_governed(conn, D)["drift"]} == {(RULE, "A"), (RULE, "Z")}


def test_a_failed_refresh_leaves_no_orphan_event(tx_conn, monkeypatch):
    conn = tx_conn
    gid = ps.grant_exception(conn, D, RULE, ["A"], "FND-0250", "a reason long enough", UNTIL, "ana")

    def boom(*a, **k):
        raise RuntimeError("projection write failed")

    monkeypatch.setattr(ps, "_refresh_projection", boom)
    with pytest.raises(RuntimeError):
        ps.approve_exception(conn, gid, "ana")
    assert conn.execute("SELECT count(*) FROM audit_exception_event").fetchone()[0] == 1   # only the GRANT: the APPROVE rolled back


# --- the real control schema: trigger, shape CHECK and the owner migration ------------------------------------------------------------
def test_the_history_is_append_only_and_shape_checked(pg_app_conn):
    conn = pg_app_conn
    (eid,) = conn.execute("INSERT INTO control.audit_exception_event (event_type, domain, rule_id, isins, ap_id, reason, expires_at, actor) "
                          "VALUES ('GRANT', %s, %s, ARRAY['A'], 'FND-0250', 'why', %s, 'ana') RETURNING event_id", (D, RULE, UNTIL)).fetchone()
    for sql in ("UPDATE control.audit_exception_event SET actor = 'mallory' WHERE event_id = %s",
                "DELETE FROM control.audit_exception_event WHERE event_id = %s"):
        conn.execute("SAVEPOINT t")
        with pytest.raises(Exception, match="append-only"):
            conn.execute(sql, (eid,))
        conn.execute("ROLLBACK TO SAVEPOINT t")
    conn.execute("SAVEPOINT t")
    with pytest.raises(Exception, match="append-only"):
        conn.execute("TRUNCATE control.audit_exception_event CASCADE")
    conn.execute("ROLLBACK TO SAVEPOINT t")
    for bad in ("INSERT INTO control.audit_exception_event (event_type, domain, rule_id, actor) VALUES ('GRANT', 'p2_metrics', 'R', 'a')",
                "INSERT INTO control.audit_exception_event (event_type, domain, rule_id, actor) VALUES ('APPROVE', 'p2_metrics', 'R', 'a')",
                "INSERT INTO control.audit_exception_event (event_type, domain, rule_id, ref_event_id, reason, actor) "
                "VALUES ('REVOKE', 'p2_metrics', 'R', 1, 'why', 'a')"):                      # no isins / no ref / no cause
        conn.execute("SAVEPOINT t")
        with pytest.raises(Exception):
            conn.execute(bad)
        conn.execute("ROLLBACK TO SAVEPOINT t")


def test_the_migration_backfills_current_residuals_and_is_idempotent(pg_app_conn):
    conn = pg_app_conn
    for isin in ("A", "B"):
        conn.execute("INSERT INTO control.audit_accepted_finding (domain, rule_id, isin, ap_id, reason, accepted_by, accepted_at, expires_at) "
                     "VALUES (%s, %s, %s, 'FND-0250', 'accepted before FND-0263', 'jacor', current_date - 1, %s)", (D, RULE, isin, UNTIL))
    n, drift = mig.migrate(conn)
    assert n == 2 and drift == {D: []}
    rows = conn.execute("SELECT event_type, actor, reason, isins FROM control.audit_exception_event WHERE domain = %s ORDER BY event_id",
                        (D,)).fetchall()
    assert [r[0] for r in rows] == ["GRANT", "APPROVE"] and rows[0][1] == "jacor"
    assert rows[0][2].startswith(mig.BACKFILL_TAG) and sorted(rows[0][3]) == ["A", "B"]
    n2, drift2 = mig.migrate(conn)                                                        # a re-run writes nothing
    assert n2 == 0 and drift2 == {D: []}


def test_the_migration_refuses_a_projection_it_cannot_explain(pg_app_conn):
    conn = pg_app_conn
    conn.execute("INSERT INTO control.audit_exception_event (event_type, domain, rule_id, isins, ap_id, reason, expires_at, actor) "
                 "VALUES ('GRANT', %s, %s, ARRAY['A'], 'FND-0250', 'pending only', %s, 'ana')", (D, RULE, UNTIL))
    conn.execute("INSERT INTO control.audit_accepted_finding (domain, rule_id, isin, ap_id, reason, expires_at) "
                 "VALUES (%s, %s, 'A', 'FND-0250', 'written behind a pending grant', %s)", (D, RULE, UNTIL))
    with pytest.raises(RuntimeError, match="projection != history"):                     # the pair has an event: no backfill, drift stays
        mig.migrate(conn)
