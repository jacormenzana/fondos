# proyecto1/tests/test_statistical_audit_accepted_residuals_pg.py
# -*- coding: utf-8 -*-
"""FND-0234(e): the accepted-residual table, its writer and loader, and the audit runner's tolerance of a missing table.
Hermetic Postgres container (python scripts/ops/run_pg_tests.py). emit_accepted_residuals commits internally, so the
tests use pg_session_conn + a throwaway schema (same discipline as test_statistical_audit_persistence_pg.py)."""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date, timedelta

import psycopg
import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit.accepted_residuals import apply_accepted, load_accepted  # noqa: E402
from shared.statistical_audit.persistence import emit_accepted_residuals  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "run_statistical_audit_pg", os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"))
runner = importlib.util.module_from_spec(_spec)
sys.modules["run_statistical_audit_pg"] = runner
_spec.loader.exec_module(runner)

DOMAIN = "cost_attributes"
RULE = "OC_NOT_CONTAMINATED"


def _make_table(conn):
    conn.execute("""CREATE TABLE audit_accepted_finding (
        domain text NOT NULL, rule_id text NOT NULL, isin varchar(12) NOT NULL, ap_id text NOT NULL,
        reason text NOT NULL, accepted_at date NOT NULL DEFAULT current_date, accepted_by text,
        expires_at date NOT NULL, PRIMARY KEY (domain, rule_id, isin),
        CONSTRAINT audit_accepted_finding_expiry_ck CHECK (expires_at > accepted_at))""")


def test_emit_then_load_roundtrip_and_renewal(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_table(conn)
    soon, later = date.today() + timedelta(days=30), date.today() + timedelta(days=120)

    assert emit_accepted_residuals(conn, DOMAIN, RULE, ["B", "A", "A"], "FND-0034", "copied from ACI row", soon, "me") == 2
    assert load_accepted(conn, DOMAIN) == {RULE: {"A": ("FND-0034", soon), "B": ("FND-0034", soon)}}

    emit_accepted_residuals(conn, DOMAIN, RULE, ["A"], "FND-0099", "renewed under another AP", later, "me")   # renewal
    got = load_accepted(conn, DOMAIN)[RULE]
    assert got["A"] == ("FND-0099", later) and got["B"] == ("FND-0034", soon)

    assert emit_accepted_residuals(conn, DOMAIN, RULE, [], "FND-0034", "nothing to do", soon) == 0
    assert load_accepted(conn, "p2_metrics") == {}                       # domains are separate


def test_expired_acceptances_are_not_loaded(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_table(conn)
    conn.execute("INSERT INTO audit_accepted_finding (domain, rule_id, isin, ap_id, reason, accepted_at, expires_at) "
                 "VALUES (%s, %s, 'OLD', 'FND-0034', 'was accepted', current_date - 30, current_date - 1), "
                 "(%s, %s, 'LIVE', 'FND-0034', 'still accepted', current_date, current_date + 10)",
                 (DOMAIN, RULE, DOMAIN, RULE))
    conn.commit()
    assert set(load_accepted(conn, DOMAIN)[RULE]) == {"LIVE"}


def test_end_to_end_a_finding_is_downgraded_by_what_the_table_holds(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_table(conn)
    emit_accepted_residuals(conn, DOMAIN, RULE, ["A", "B"], "FND-0034", "copied from ACI row",
                            date.today() + timedelta(days=30))
    run = runner.AuditRun(DOMAIN)
    run.findings = [{"block": "BLOCK5", "rule_id": RULE, "rule_class": "HARD_INVARIANT", "severity": "ALARM",
                     "group_key": RULE, "distance": 3.0, "evidence": "3/10", "violating_isins": ("A", "B", "N")}]
    runner._apply_accepted_residuals(conn, run)
    assert run.findings[0]["severity"] == "ALARM" and run.findings[0]["violating_isins"] == ("N",)
    assert run.accepted_notes and "1 NEW" in run.accepted_notes[0]


def test_a_missing_table_means_no_accepted_residuals_and_leaves_the_connection_usable(
        pg_session_conn, pg_conn_module_schema, capsys):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")           # throwaway schema: the table does not exist here
    conn.autocommit = False
    run = runner.AuditRun(DOMAIN)
    run.findings = [{"block": "BLOCK5", "rule_id": RULE, "rule_class": "HARD_INVARIANT", "severity": "ALARM",
                     "group_key": RULE, "distance": 1.0, "evidence": "1/10", "violating_isins": ("A",)}]
    before = [dict(f) for f in run.findings]
    runner._apply_accepted_residuals(conn, run)
    assert run.findings == before and run.accepted_notes == []
    assert "none applied" in capsys.readouterr().err
    assert conn.execute("SELECT 1").fetchone()[0] == 1                   # the aborted transaction was rolled back
    conn.rollback()
    conn.autocommit = True                                               # the state pg_conn_module_schema's teardown expects


def test_real_ddl_has_the_columns_and_rejects_a_non_future_expiry(pg_session_conn):
    """db/pg/35_control.sql, as loaded into the container."""
    conn = pg_session_conn
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='control' "
        "AND table_name='audit_accepted_finding'").fetchall()}
    assert cols == {"domain", "rule_id", "isin", "ap_id", "reason", "accepted_at", "accepted_by", "expires_at"}
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO control.audit_accepted_finding (domain, rule_id, isin, ap_id, reason, expires_at) "
                     "VALUES ('d', 'r', 'X', 'FND-0001', 'why', current_date)")
    conn.rollback()
