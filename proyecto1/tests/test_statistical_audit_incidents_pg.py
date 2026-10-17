# proyecto1/tests/test_statistical_audit_incidents_pg.py
# -*- coding: utf-8 -*-
"""FND-0264 on the hermetic Postgres container (python scripts/ops/run_pg_tests.py): the incident writers, the partial unique index
("one live incident per finding key"), the state CHECKs of the REAL DDL, and the plan's exit criterion end to end -- a recompute alone never
closes a CRITICAL defect. The writers commit, so they run in a throwaway schema with PRODUCTION transaction semantics (autocommit off)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts", "ops"))

import migrate_audit_incident as mig  # noqa: E402
from shared.statistical_audit import incidents as inc  # noqa: E402
from shared.statistical_audit import persistence as ps  # noqa: E402

D = "cost_attributes"
KEY = inc.finding_key(D, "MGMT_LE_TOTAL", "MGMT_LE_TOTAL")
CAND = inc.Candidate(D, "MGMT_LE_TOTAL", "MGMT_LE_TOTAL", ("LU1", "LU2"), "73/2836 rows violate", "run_a")


@pytest.fixture()
def tx_conn(pg_session_conn, pg_conn_module_schema):
    """Throwaway schema holding a copy of the REAL control.audit_incident (columns, CHECKs, identity and the partial unique index),
    autocommit OFF like shared.db.get_connection(); restored before the schema fixture's teardown."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute("CREATE TABLE audit_incident (LIKE control.audit_incident INCLUDING ALL)")
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.autocommit = True


def _row(conn, incident_id):
    return conn.execute("SELECT state, ap_id, fix_kind, certified_run_id, closed_at IS NOT NULL, isins FROM audit_incident WHERE incident_id = %s",
                        (incident_id,)).fetchone()


def test_open_records_the_snapshot_and_a_second_live_incident_of_the_same_key_is_refused(tx_conn):
    conn = tx_conn
    iid = ps.open_incident(conn, CAND, ap_id="FND-0900", affected_calc_version="20261011")
    assert tuple(_row(conn, iid)) == ("OPEN", "FND-0900", None, None, False, ["LU1", "LU2"])
    with pytest.raises(ValueError, match="live incident already exists"):                              # idempotent: a re-run never duplicates
        ps.open_incident(conn, CAND)
    assert conn.execute("SELECT count(*) FROM audit_incident").fetchone()[0] == 1


def test_the_full_lifecycle_open_certify_close_and_the_key_can_open_again_afterwards(tx_conn):
    conn = tx_conn
    iid = ps.open_incident(conn, CAND, ap_id="FND-0900")
    ps.annotate_incident(conn, iid, root_cause="extractor binds the wrong row", corrective_action="fix + recompute-costs", fix_kind="CODE")
    ps.certify_incident(conn, iid, "run_b")
    assert tuple(_row(conn, iid))[:5] == ("CERTIFIED", "FND-0900", "CODE", "run_b", False)
    assert inc.closable("FND-0900", inc.load_incidents(conn)).ok
    ps.close_incident(conn, iid)
    assert _row(conn, iid)[0] == "CLOSED" and _row(conn, iid)[4] is True
    again = ps.open_incident(conn, CAND)                                                                # CLOSED frees the key
    assert again != iid and _row(conn, again)[0] == "OPEN"


def test_an_open_incident_blocks_the_closure_of_its_ticket_and_only_certification_unblocks_it(tx_conn):
    # the plan's exit criterion: a recompute alone (state still OPEN, nothing certified) cannot close the ticket
    conn = tx_conn
    iid = ps.open_incident(conn, CAND, ap_id="FND-0900")
    ps.annotate_incident(conn, iid, fix_kind="RECOMPUTE")
    gate = inc.closable("FND-0900", inc.load_incidents(conn))
    assert not gate.ok and "--certify" in gate.reasons[0]
    ps.certify_incident(conn, iid, "run_b")
    assert inc.closable("FND-0900", inc.load_incidents(conn)).ok


def test_accept_and_close_follow_the_state_machine_and_refuse_anything_else(tx_conn):
    conn = tx_conn
    iid = ps.open_incident(conn, CAND, ap_id="FND-0900")
    with pytest.raises(ValueError, match="neither CERTIFIED nor ACCEPTED"):
        ps.close_incident(conn, iid)                                                                    # an OPEN incident does not close
    ps.accept_incident(conn, iid)
    with pytest.raises(ValueError, match="is not OPEN"):
        ps.certify_incident(conn, iid, "run_b")                                                         # nor does ACCEPTED certify
    with pytest.raises(ValueError, match="not OPEN"):
        ps.annotate_incident(conn, iid, root_cause="late note")
    ps.close_incident(conn, iid)
    assert _row(conn, iid)[0] == "CLOSED"


def test_annotate_refuses_an_unknown_fix_kind_and_keeps_fields_it_is_not_given(tx_conn):
    conn = tx_conn
    iid = ps.open_incident(conn, CAND, ap_id="FND-0900")
    with pytest.raises(ValueError, match="fix_kind"):
        ps.annotate_incident(conn, iid, fix_kind="MAGIC")
    ps.annotate_incident(conn, iid, root_cause="why")
    ps.annotate_incident(conn, iid, fix_kind="DATA")
    row = conn.execute("SELECT ap_id, root_cause, fix_kind FROM audit_incident WHERE incident_id = %s", (iid,)).fetchone()
    assert tuple(row) == ("FND-0900", "why", "DATA")


def test_the_real_ddl_refuses_a_certified_incident_without_its_run_and_unknown_states_and_domains(pg_app_conn):
    conn = pg_app_conn
    base = "INSERT INTO control.audit_incident (finding_key, domain, rule_id, group_key, opened_run_id, {cols}) VALUES ('k', 'cost_attributes', 'r', 'g', 'run', {vals})"
    for cols, vals in (("state", "'CERTIFIED'"),                                        # CERTIFIED needs certified_run_id + certified_at
                       ("state", "'CLOSED'"),                                           # CLOSED needs closed_at
                       ("state", "'FIXED'"),                                            # unknown state
                       ("fix_kind", "'MAGIC'")):                                        # unknown fix kind
        conn.execute("SAVEPOINT t")
        with pytest.raises(Exception):
            conn.execute(base.format(cols=cols, vals=vals))
        conn.execute("ROLLBACK TO SAVEPOINT t")
    conn.execute("SAVEPOINT t")
    with pytest.raises(Exception):
        conn.execute("INSERT INTO control.audit_incident (finding_key, domain, rule_id, group_key, opened_run_id) "
                     "VALUES ('k', 'costs', 'r', 'g', 'run')")                          # domain outside the audit domains
    conn.execute("ROLLBACK TO SAVEPOINT t")


def test_the_migration_block_is_idempotent_and_creates_the_live_index(pg_app_conn):
    conn = pg_app_conn
    conn.execute("DROP TABLE control.audit_incident")
    assert mig.existing(conn) == {"table": False, "live_index": False}
    mig.migrate(conn)
    mig.migrate(conn)                                                                     # re-run: no error, nothing duplicated
    assert mig.existing(conn) == {"table": True, "live_index": True}
    ps_cand = inc.Candidate(D, "R", "G", ("LU1",), "e", "run")
    conn.execute("INSERT INTO control.audit_incident (finding_key, domain, rule_id, group_key, opened_run_id) VALUES (%s, %s, 'R', 'G', 'run')",
                 (ps_cand.key, D))
    conn.execute("SAVEPOINT t")
    with pytest.raises(Exception):
        conn.execute("INSERT INTO control.audit_incident (finding_key, domain, rule_id, group_key, opened_run_id) VALUES (%s, %s, 'R', 'G', 'run2')",
                     (ps_cand.key, D))
    conn.execute("ROLLBACK TO SAVEPOINT t")


def test_certification_end_to_end_a_fixed_defect_certifies_and_a_still_violating_one_does_not(tx_conn):
    conn = tx_conn
    iid = ps.open_incident(conn, CAND, ap_id="FND-0900")
    (incident,) = [r for r in inc.load_incidents(conn) if r["incident_id"] == iid]
    later = datetime.now(timezone.utc)
    still = inc.Reaudit("run_b", D, "FULL", "FAIL", later, {KEY: frozenset({"LU2"})})
    fixed = inc.Reaudit("run_c", D, "FULL", "PASS_WITH_WARNINGS", later, {})
    assert not inc.certify(incident, still).ok
    assert inc.certify(incident, fixed).ok
    ps.certify_incident(conn, iid, fixed.run_id)
    assert _row(conn, iid)[0] == "CERTIFIED"
