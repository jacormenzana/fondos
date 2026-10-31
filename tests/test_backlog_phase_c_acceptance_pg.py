# tests/test_backlog_phase_c_acceptance_pg.py
# -*- coding: utf-8 -*-
"""Phase C acceptance verifier (FND-0334) against a real catalogue: the three phases applied to the hermetic container pass every check, and each deliberate drift of the DDL (an extra grant, a
dropped trigger, an elevated login) is caught by the check that names it. Never the live backlog.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_phase_c_acceptance_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_phase_c_acceptance as acc  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from test_backlog_transitions_pg import _base  # noqa: E402

PHASES = ("relations", "groups", "sync", "hook", "collision", "attest")


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    for phase in PHASES[:4]:
        mig.migrate(pg_conn, phase)
    pg_conn.execute("CREATE ROLE backlog_adjudicator LOGIN")                 # the owner creates it in production; before the phase so the grants are made
    pg_conn.execute("CREATE ROLE backlog_human NOLOGIN")
    for phase in PHASES[4:]:
        mig.migrate(pg_conn, phase)
    return pg_conn


def verdict(conn, phase="all"):
    phases = ("hook", "collision", "attest") if phase == "all" else (phase,)
    out = {}
    for p in phases:
        spec = mig.PHASES[p]
        everything = lambda key: [x for q in ("hook", "collision", "attest") for x in mig.PHASES[q][key]]
        cat = bc.phase_c_catalog(list(spec["tables"]) + everything("tables"), list(spec["views"]), list(spec["routines"]) + everything("routines") + list(acc.PRIVILEGED), acc.ROLES, conn=conn)
        out[p] = {"hook": lambda: acc.evaluate_hook(cat, spec, collision_applied="sp_gate_collision" in cat["routines"]),
                  "collision": lambda: acc.evaluate_collision(cat, spec), "attest": lambda: acc.evaluate_attest(cat, spec)}[p]()
    return out


def failed(res):
    return sorted(name for rs in res.values() for name, ok, _ in rs if not ok)


def test_the_three_phases_as_the_ddl_leaves_them_pass_every_check(db):
    res = verdict(db)
    assert failed(res) == [] and sum(len(rs) for rs in res.values()) > 40


def test_each_phase_can_be_verified_on_its_own(db):
    for phase in ("hook", "collision", "attest"):
        assert failed(verdict(db, phase)) == [], phase


def test_before_the_attest_phase_is_applied_its_checks_fail_and_the_others_still_pass(pg_conn):
    _base(pg_conn)
    for phase in PHASES[:4]:
        mig.migrate(pg_conn, phase)
    pg_conn.execute("CREATE ROLE backlog_adjudicator LOGIN")
    mig.migrate(pg_conn, "collision")
    res = verdict(pg_conn)
    assert failed({"hook": res["hook"], "collision": res["collision"]}) == []
    assert any("table gate_protection_event" in n for n in failed({"attest": res["attest"]}))


def test_without_the_adjudicator_role_the_collision_phase_fails_on_that_check(pg_conn):
    _base(pg_conn)
    for phase in PHASES[:5]:
        mig.migrate(pg_conn, phase)                                           # the owner has not created backlog_adjudicator
    bad = failed(verdict(pg_conn, "collision"))
    assert bad == ["role backlog_adjudicator exists, can log in"]


@pytest.mark.parametrize("sql, phase, fragment", [
    ("GRANT SELECT ON backlog.BACKLOG TO backlog_gate", "hook", "holds no privilege on ANY table"),
    ("GRANT EXECUTE ON FUNCTION backlog.sp_transition(text,text,text,text,text,text,text,text,text,date,text,text,text,boolean) TO backlog_gate", "hook", "executes exactly its routines"),
    ("GRANT EXECUTE ON FUNCTION backlog.sp_record_hook_bypass(text,text,text,timestamptz,text) TO backlog_gate", "hook", "executes exactly its routines"),
    ("ALTER ROLE backlog_gate SUPERUSER", "hook", "no superuser"),
    ("ALTER ROLE backlog_gate CREATEROLE", "hook", "no superuser"),
    ("GRANT backlog_writer TO backlog_gate", "hook", "belongs to no other role"),
    ("ALTER ROLE backlog_gate CONNECTION LIMIT 100", "hook", "connection limit"),
    ("ALTER ROLE backlog_gate RESET statement_timeout", "hook", "statement timeout"),
    ("GRANT EXECUTE ON FUNCTION backlog.sp_gate_forward(text,text,text,text) TO PUBLIC", "hook", "routine sp_gate_forward:"),
    ("ALTER TABLE backlog.hook_bypass DISABLE TRIGGER append_only", "hook", "table hook_bypass"),
    ("ALTER FUNCTION backlog.sp_gate_check(text,text[],text) SECURITY INVOKER", "hook", "routine sp_gate_check:"),
    ("ALTER FUNCTION backlog.sp_gate_check(text,text[],text) RESET ALL", "hook", "routine sp_gate_check:"),
    ("GRANT INSERT ON backlog.hook_bypass TO backlog_writer", "hook", "writes none directly"),
    ("GRANT EXECUTE ON FUNCTION backlog.sp_adjudicate_overlap(text,text,text,text,text,text) TO backlog_gate", "collision", "cannot adjudicate or set a mode"),
    ("GRANT EXECUTE ON FUNCTION backlog.sp_set_collision_mode(text,text,text,text,text,jsonb) TO backlog_adjudicator", "collision", "executes exactly its routines"),
    ("GRANT EXECUTE ON FUNCTION backlog.sp_adjudicate_overlap(text,text,text,text,text,text) TO backlog_writer", "collision", "cannot adjudicate"),
    ("ALTER ROLE backlog_adjudicator SUPERUSER", "collision", "no superuser"),
    ("GRANT INSERT ON backlog.gate_adjudication TO backlog_adjudicator", "collision", "no write privilege"),
    ("REVOKE EXECUTE ON FUNCTION backlog.sp_gate_collision(text[],text[],text,text,text) FROM backlog_gate", "collision", "executes the rule"),
    ("GRANT EXECUTE ON FUNCTION backlog.sp_record_gate_protection(text,text,text) TO backlog_gate", "attest", "executable by the shared writer"),
    ("REVOKE EXECUTE ON FUNCTION backlog.sp_record_gate_protection(text,text,text) FROM backlog_writer", "attest", "executable by the shared writer"),
    ("ALTER TABLE backlog.gate_protection_event DISABLE TRIGGER append_only", "attest", "table gate_protection_event"),
])
def test_each_drift_of_the_ddl_is_caught_by_the_check_that_names_it(db, sql, phase, fragment):
    assert failed(verdict(db, phase)) == []
    db.execute(sql)
    bad = failed(verdict(db, phase))
    assert bad and any(fragment in n for n in bad), (sql, bad)
