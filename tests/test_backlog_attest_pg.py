# tests/test_backlog_attest_pg.py
# -*- coding: utf-8 -*-
"""Phase C slice 4 of Backlog v2 (FND-0334): db/pg_backlog/95_attest.sql applied by scripts/ops/migrate_backlog_v2.py --phase attest: the branch-protection facts, the three V-GATE-* violations,
the 30-day expiry of an owner verification, the client functions and gate_pairs. Hermetic: the throwaway container inside a rolled-back savepoint; never the live backlog.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_attest_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from shared.testing.sandbox import acting_as  # noqa: E402
from test_backlog_groups_pg import violations  # noqa: E402
from test_backlog_transitions_pg import ME, _base, _mk, refused  # noqa: E402

SYNC, OWNER = "system:sync", "human:jacor"
PHASES = ("relations", "groups", "sync", "hook", "collision", "attest")


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    for phase in PHASES:
        mig.migrate(pg_conn, phase)
    _mk(pg_conn)                                                          # FND-0334 is the ticket the repository-level violations belong to: make it exist in the fixture
    pg_conn.execute("INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority, session_id) "
                    "VALUES ('FND-0334', 'the gate', 'FND', 'BUG', 'N/A', 'IN_PROGRESS', 'LOW', 'FND_20261031_DEV_TEST') ON CONFLICT DO NOTHING")
    return pg_conn


def api(conn, kind, reason="read from the GitHub API in a test"):
    with acting_as(SYNC):
        return bc.record_gate_protection(kind, reason, actor=SYNC, conn=conn)


def owner(conn, kind, reason="written by the owner in a test"):
    with acting_as(OWNER):
        return bc.record_gate_protection(kind, reason, actor=OWNER, conn=conn)


def codes(conn):
    return sorted(c for c, _ in violations(conn) if c.startswith("V-GATE-") and c != "V-GATE-OVERRIDE")


# ---------------------------------------------------------------- migration

def test_attest_needs_the_hook_phase(pg_conn):
    _base(pg_conn)
    for phase in ("relations", "groups", "sync"):
        mig.migrate(pg_conn, phase)
    with pytest.raises(RuntimeError, match="v_violations_c2"):
        mig.migrate(pg_conn, "attest")


def test_attest_is_idempotent_joins_the_catalogue_and_the_rollback_keeps_history(db):
    mig.migrate(db, "attest")
    have, spec = mig.existing(db, "attest"), mig.PHASES["attest"]
    assert have["tables"] == sorted(spec["tables"]) and have["views"] == sorted(spec["views"]) and have["routines"] == sorted(spec["routines"])
    api(db, "UNPROTECTED")
    assert codes(db) == ["V-GATE-UNPROTECTED"]
    mig.migrate(db, "hook")                                                # re-applying an earlier phase keeps the slice 4 codes in the union
    assert codes(db) == ["V-GATE-UNPROTECTED"]
    mig.migrate(db, "attest", down=True)
    have = mig.existing(db, "attest")
    assert have["views"] == [] and have["routines"] == [] and have["tables"] == ["gate_protection_event"]
    assert codes(db) == []
    mig.migrate(db, "attest")
    assert codes(db) == ["V-GATE-UNPROTECTED"]                              # the fact was kept


# ---------------------------------------------------------------- the facts and the violations

def test_an_api_reading_is_recorded_only_when_it_changes_or_is_a_day_old(db):
    assert api(db, "UNVERIFIED") is True and api(db, "UNVERIFIED") is False        # the same reading: not recorded again
    assert api(db, "UNPROTECTED") is True                                          # it changed
    db.execute("INSERT INTO backlog.gate_protection_event (kind, source, reason, actor, event_ts) VALUES ('UNPROTECTED', 'API', 'an older reading in a test', 'system:sync', now() - interval '30 hours')")
    assert api(db, "UNPROTECTED") is True                                          # the latest reading is 30 hours old: a daily sync records it again
    assert db.execute("SELECT count(*) FROM backlog.gate_protection_event WHERE source = 'API'").fetchone()[0] == 4


def test_unprotected_and_the_two_ways_to_be_unverified(db):
    assert codes(db) == []
    api(db, "PROTECTED")
    assert codes(db) == []                                                         # backlog-gate is a required check: nothing to report
    api(db, "UNVERIFIED")
    assert codes(db) == ["V-GATE-UNVERIFIED"]                                       # a missing token never passes silently
    api(db, "UNPROTECTED")
    assert codes(db) == ["V-GATE-UNPROTECTED"]                                      # only the latest reading counts
    api(db, "PROTECTED")
    assert codes(db) == []


def test_an_owner_verification_clears_unverified_for_30_days_but_never_unprotected(db):
    api(db, "UNVERIFIED")
    db.execute("INSERT INTO backlog.gate_protection_event (kind, source, reason, actor, event_ts) VALUES ('VERIFIED', 'OWNER', 'an old verification in a test', 'human:jacor', now() - interval '31 days')")
    assert codes(db) == ["V-GATE-UNVERIFIED"]                                       # 31 days old: it expired
    assert owner(db, "VERIFIED") is True
    assert codes(db) == []                                                         # a fresh one clears it
    refused(db, "append-only", lambda c: c.execute("UPDATE backlog.gate_protection_event SET reason = 'edited in a test'"))
    api(db, "UNPROTECTED")
    assert codes(db) == ["V-GATE-UNPROTECTED"]                                      # a verification never clears an UNPROTECTED reading


def test_each_emergency_push_is_a_violation_that_stays(db):
    owner(db, "EMERGENCY", "pushed with the rule disabled because CI was down")
    owner(db, "EMERGENCY", "a second emergency push in a test, new key in 48 h")
    assert codes(db) == ["V-GATE-EMERGENCY", "V-GATE-EMERGENCY"]
    api(db, "PROTECTED")
    assert codes(db) == ["V-GATE-EMERGENCY", "V-GATE-EMERGENCY"]                    # protection restored: the emergencies remain on the record


def test_who_may_write_what(db):
    refused(db, "written by a person", lambda c: owner_as(c, "agent:claude-opus-5-5", "VERIFIED"))
    refused(db, "written by a person", lambda c: owner_as(c, "system:sync", "EMERGENCY"))
    refused(db, "an API reading is written by a system actor", lambda c: owner_as(c, OWNER, "UNPROTECTED"))
    refused(db, "an API reading is written by a system actor", lambda c: owner_as(c, ME, "PROTECTED"))
    refused(db, "is not PROTECTED", lambda c: c.execute("SELECT backlog.sp_record_gate_protection('MAYBE', 'a reason long enough here', 'system:sync')"))
    refused(db, "not canonical", lambda c: c.execute("SELECT backlog.sp_record_gate_protection('PROTECTED', 'a reason long enough here', 'sync')"))
    refused(db, "gate_protection_event_reason_check", lambda c: owner_as(c, OWNER, "VERIFIED", "short"))
    with pytest.raises(ValueError):
        bc.record_gate_protection("MAYBE", "a reason long enough here", actor=SYNC, conn=db)


def owner_as(conn, actor, kind, reason="a reason long enough for the check"):
    return conn.execute("SELECT backlog.sp_record_gate_protection(%s, %s, %s)", (kind, reason, actor)).fetchone()[0]


# ---------------------------------------------------------------- the client

def test_state_and_presence(pg_conn):
    _base(pg_conn)
    for phase in PHASES[:-1]:
        mig.migrate(pg_conn, phase)
    assert bc.attest_store_present(conn=pg_conn) is False
    mig.migrate(pg_conn, "attest")
    assert bc.attest_store_present(conn=pg_conn) is True
    assert bc.gate_protection_state(conn=pg_conn) == {"api": None, "verified": None, "emergencies": 0}
    api(pg_conn, "UNVERIFIED", "no token in a test, the API cannot be read")
    owner(pg_conn, "VERIFIED")
    owner(pg_conn, "EMERGENCY", "pushed with the rule disabled, in a test")
    st = bc.gate_protection_state(conn=pg_conn)
    assert st["api"][0] == "UNVERIFIED" and st["api"][1].startswith("no token") and st["verified"] is not None and st["emergencies"] == 1


def test_gate_pairs_lists_undecided_and_decided_pairs_of_the_tickets(db):
    from datetime import datetime  # noqa: F401
    a, b, c, d = _mk(db), _mk(db), _mk(db), _mk(db)
    for x, y in ((a, b), (c, d)):
        db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username, origin) VALUES (%s, %s, 'OVERLAPS', 'PROPOSED', 'collision gate in a test', 'system:gate', 'GATE')",
                   (min(x, y), max(x, y)))
    db.execute("INSERT INTO backlog.gate_adjudication (a, b, decision, reason, actor) VALUES (%s, %s, 'NONE', 'decided earlier by the owner', 'human:jacor')", (min(c, d), max(c, d)))
    db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username, origin) VALUES (%s, %s, 'OVERLAPS', 'REJECTED', 'adjudicated NONE: in a test', 'human:jacor', 'GATE')",
               (min(c, d), max(c, d)))
    got = bc.gate_pairs([a, c], conn=db)
    assert [(r["a"], r["b"], r["decided"]) for r in got] == sorted([(min(a, b), max(a, b), False), (min(c, d), max(c, d), True)])
    assert bc.gate_pairs([], conn=db) == []


def test_the_writer_and_the_owner_login_may_execute_and_nobody_else(db):
    sig = "backlog.sp_record_gate_protection(text,text,text)"
    for role in ("backlog_writer",):
        assert db.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig)).fetchone()[0] is True
    for role in ("backlog_gate", "public"):
        assert db.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig)).fetchone()[0] is False
    assert db.execute("SELECT has_table_privilege('backlog_writer', 'backlog.gate_protection_event', 'INSERT')").fetchone()[0] is False
