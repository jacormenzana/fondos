# tests/test_backlog_collision_pg.py
# -*- coding: utf-8 -*-
"""Phase C slice 3 of Backlog v2 (FND-0334): db/pg_backlog/90_collision.sql applied by scripts/ops/migrate_backlog_v2.py --phase collision: the rule (T0 / T1), the pairs recorded as
PROPOSED OVERLAPS events of origin GATE, the modes, the adjudication procedure, the queue and the privileges. Hermetic: the throwaway container inside a rolled-back savepoint; never the
live backlog.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_collision_pg.py
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_v2 as mig  # noqa: E402
from test_backlog_groups_pg import as_proc  # noqa: E402
from test_backlog_transitions_pg import ME, _base, _mk, refused, tr  # noqa: E402

GATE, CAL, OWNER = "system:gate", "system:calibrator", "human:jacor"
NONCE = "ab" * 16
PHASES = ("relations", "groups", "sync", "hook")


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    for phase in PHASES:
        mig.migrate(pg_conn, phase)
    pg_conn.execute("CREATE ROLE backlog_adjudicator LOGIN")             # the owner creates it in production; here before the phase so the grants are made
    mig.migrate(pg_conn, "collision")
    return pg_conn


def note(conn, ap, text):
    as_proc(conn, "INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, %s, %s)", (ap, ME, text))


def wip(conn, text=""):
    """A ticket IN_PROGRESS whose log cites `text`."""
    ap = _mk(conn)
    tr(conn, ap, "IN_PROGRESS")
    if text:
        note(conn, ap, text)
    return ap


def pairs(conn, files, tickets, tier="T0"):
    return conn.execute("SELECT a, b, shared FROM backlog.fn_collision_pairs(%s, %s, %s) ORDER BY 1, 2", (files, tickets, tier)).fetchall()


def gate(conn, files, tickets, ceiling="AUDIT", nonce=NONCE, actor=GATE):
    return conn.execute("SELECT backlog.sp_gate_collision(%s, %s, %s, %s, %s)", (files, tickets, ceiling, nonce, actor)).fetchone()[0]


def adjudicate(conn, a, b, decision="NONE", reason="decided in a test, a reason of ten or more", absorbs=None, actor=OWNER):
    return conn.execute("SELECT backlog.sp_adjudicate_overlap(%s, %s, %s, %s, %s, %s)", (a, b, decision, absorbs, reason, actor)).fetchone()[0]


def as_adjudicator(conn):
    conn.execute("SET LOCAL SESSION AUTHORIZATION backlog_adjudicator")


def reset(conn):
    conn.execute("RESET SESSION AUTHORIZATION")


def holds(conn, ap):
    return conn.execute("SELECT violation_code FROM backlog.v_open_hold WHERE action_point_id = %s", (ap,)).fetchall()


def events(conn, a, b):
    return conn.execute("SELECT link_type, status, origin, username FROM backlog.backlog_link_event WHERE least(from_id, to_id) = %s AND greatest(from_id, to_id) = %s ORDER BY event_id",
                        (a, b)).fetchall()


# ---------------------------------------------------------------- migration

def test_collision_needs_the_hook_phase(pg_conn):
    _base(pg_conn)
    for phase in ("relations", "groups", "sync"):
        mig.migrate(pg_conn, phase)
    with pytest.raises(RuntimeError, match="gate_check"):
        mig.migrate(pg_conn, "collision")


def test_collision_is_idempotent_and_the_rollback_keeps_history(db):
    mig.migrate(db, "collision")
    have, spec = mig.existing(db, "collision"), mig.PHASES["collision"]
    assert have["tables"] == sorted(spec["tables"]) and have["views"] == sorted(spec["views"]) and have["routines"] == sorted(spec["routines"])
    a, b = wip(db, "shared/x_mod.py"), wip(db)
    gate(db, ["shared/x_mod.py"], [b])
    mig.migrate(db, "collision", down=True)
    have = mig.existing(db, "collision")
    assert have["views"] == [] and have["routines"] == [] and have["tables"] == ["gate_adjudication", "gate_collision", "gate_state"]
    assert events(db, *sorted((a, b)))[0][2] == "GATE"                                   # the link event stays
    mig.migrate(db, "collision")
    assert db.execute("SELECT count(*) FROM backlog.v_gate_queue").fetchone()[0] == 1


# ---------------------------------------------------------------- the modes

def test_the_effective_mode_is_the_lower_of_the_ceiling_and_the_latest_event(db):
    st = lambda c: db.execute("SELECT mode, tier FROM backlog.fn_collision_state(%s)", (c,)).fetchone()
    assert st("BLOCK") == ("BLOCK", "T0") and st("OFF") == ("OFF", "T0")                 # no event: the ceiling
    db.execute("INSERT INTO backlog.gate_state (mode, tier, actor, reason) VALUES ('AUDIT', 'T1', 'human:jacor', 'the owner starts the audit')")
    assert st("BLOCK") == ("AUDIT", "T1") and st("AUDIT") == ("AUDIT", "T1") and st("OFF") == ("OFF", "T1")
    refused(db, "not OFF, AUDIT or BLOCK", lambda c: c.execute("SELECT * FROM backlog.fn_collision_state('LOUD')"))


def set_mode(conn, mode, tier="T0", actor=OWNER, ceiling="BLOCK", reason="a reason of ten or more characters"):
    return conn.execute("SELECT backlog.sp_set_collision_mode(%s, %s, %s, %s, %s)", (mode, tier, actor, reason, ceiling)).fetchone()[0]


def test_a_person_sets_audit_but_block_needs_14_days_and_a_share_of_10_percent_or_less(db):
    set_mode(db, "AUDIT")
    refused(db, "14 days of AUDIT", lambda c: set_mode(c, "BLOCK"))
    db.execute("INSERT INTO backlog.gate_state (mode, tier, actor, reason, event_ts) VALUES ('AUDIT', 'T0', 'human:jacor', 'an earlier audit start of the owner', now() - interval '20 days')")
    refused(db, "share of commits", lambda c: set_mode(c, "BLOCK"))                       # no evaluation at all: no share
    for i in range(10):                                                                   # 2 of 10 introduce a pair = 20 %
        db.execute("INSERT INTO backlog.gate_collision (nonce, mode, tier, files_n, new_pairs) VALUES (%s, 'AUDIT', 'T0', 3, %s)", (f"{i:032x}", 1 if i < 2 else 0))
    refused(db, "10 % or less", lambda c: set_mode(c, "BLOCK"))
    for i in range(10, 30):
        db.execute("INSERT INTO backlog.gate_collision (nonce, mode, tier, files_n, new_pairs) VALUES (%s, 'AUDIT', 'T0', 3, 0)", (f"{i:032x}",))
    assert float(db.execute("SELECT backlog.fn_gate_share(3)").fetchone()[0]) == pytest.approx(0.0667, abs=1e-3)
    assert set_mode(db, "BLOCK") > 0
    assert db.execute("SELECT mode FROM backlog.fn_collision_state('BLOCK')").fetchone()[0] == "BLOCK"


def test_the_calibrator_only_retires_or_tightens_and_never_lowers_block_or_loosens(db):
    set_mode(db, "AUDIT", ceiling="AUDIT")
    assert set_mode(db, "AUDIT", "T1", actor=CAL, ceiling="AUDIT") > 0                    # tighten T0 -> T1 at the mode in force
    refused(db, "may only retire", lambda c: set_mode(c, "AUDIT", "T0", actor=CAL, ceiling="AUDIT"))   # never loosens
    refused(db, "may only retire", lambda c: set_mode(c, "BLOCK", "T1", actor=CAL, ceiling="AUDIT"))   # never raises
    assert set_mode(db, "OFF", "T1", actor=CAL, ceiling="AUDIT") > 0                      # retires
    assert db.execute("SELECT mode, tier FROM backlog.fn_collision_state('BLOCK')").fetchone() == ("OFF", "T1")
    # BLOCK in force: lowering to AUDIT is refused, retiring is allowed
    db.execute("INSERT INTO backlog.gate_state (mode, tier, actor, reason) VALUES ('BLOCK', 'T0', 'human:jacor', 'the owner re-enables the gate')")
    refused(db, "may only retire", lambda c: set_mode(c, "AUDIT", "T0", actor=CAL, ceiling="BLOCK"))
    refused(db, "only a person or system:calibrator", lambda c: set_mode(c, "OFF", actor=ME))
    refused(db, "append-only", lambda c: c.execute("UPDATE backlog.gate_state SET mode = 'OFF'"))


# ---------------------------------------------------------------- the rule

def test_t0_flags_a_code_file_cited_by_another_ticket_in_progress(db):
    a = wip(db, "the fix is in shared/foo_mod.py and its test tests/test_foo.py; see also doc/NOTES.md")
    b = wip(db)
    out = pairs(db, ["shared/foo_mod.py", "doc/NOTES.md", "README.txt"], [b])
    assert out == [(*sorted((a, b)), ["shared/foo_mod.py"])]                              # the .md and .txt files never count
    assert pairs(db, ["shared/other.py"], [b]) == []
    assert pairs(db, ["SHARED/FOO_MOD.PY"], [b]) == [(*sorted((a, b)), ["SHARED/FOO_MOD.PY"])]   # the match is case-insensitive


def test_only_tickets_in_progress_count_and_the_committing_ticket_never_pairs_with_itself(db):
    a = _mk(db)                                                                           # OPEN
    note(db, a, "shared/foo_mod.py")
    b = wip(db)
    assert pairs(db, ["shared/foo_mod.py"], [b]) == []
    tr(db, a, "IN_PROGRESS")
    assert len(pairs(db, ["shared/foo_mod.py"], [b])) == 1
    assert pairs(db, ["shared/foo_mod.py"], [a]) == []                                    # the only ticket citing it is the committing one


def test_a_hub_file_cited_by_five_open_tickets_is_ignored(db):
    citing = [wip(db, "touches shared/hub_mod.py") for _ in range(4)]
    b = wip(db)
    assert len(pairs(db, ["shared/hub_mod.py"], [b])) == 4
    wip(db, "also shared/hub_mod.py")                                                     # the fifth open ticket citing it
    assert pairs(db, ["shared/hub_mod.py"], [b]) == []
    assert citing


def test_a_confirmed_link_or_an_earlier_decision_skips_the_pair(db):
    a, b, c = wip(db, "shared/foo_mod.py"), wip(db), wip(db)
    db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) VALUES (%s, %s, 'RELATED', 'CONFIRMED', 'known relation in a test', %s)", (b, a, ME))
    assert pairs(db, ["shared/foo_mod.py"], [b]) == []
    assert len(pairs(db, ["shared/foo_mod.py"], [c])) == 1
    db.execute("INSERT INTO backlog.gate_adjudication (a, b, decision, reason, actor) VALUES (%s, %s, 'NONE', 'decided earlier by the owner', 'human:jacor')", tuple(sorted((a, c))))
    assert pairs(db, ["shared/foo_mod.py"], [c]) == []


def test_t1_needs_two_shared_files(db):
    a = wip(db, "shared/one_mod.py only")
    b = wip(db)
    assert len(pairs(db, ["shared/one_mod.py"], [b], "T0")) == 1 and pairs(db, ["shared/one_mod.py"], [b], "T1") == []
    note(db, a, "and shared/two_mod.py as well")
    assert pairs(db, ["shared/one_mod.py", "shared/two_mod.py"], [b], "T1") == [(*sorted((a, b)), ["shared/one_mod.py", "shared/two_mod.py"])]


# ---------------------------------------------------------------- recording

def test_off_records_nothing(db):
    wip(db, "shared/foo_mod.py")
    b = wip(db)
    out = gate(db, ["shared/foo_mod.py"], [b], ceiling="OFF")
    assert out["mode"] == "OFF" and out["new"] == []
    assert db.execute("SELECT count(*) FROM backlog.gate_collision").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM backlog.backlog_link_event WHERE origin = 'GATE'").fetchone()[0] == 0


def test_audit_records_the_pair_in_canonical_order_once_and_blocks_nothing(db):
    a = wip(db, "shared/foo_mod.py")
    b = wip(db)
    out = gate(db, ["shared/foo_mod.py", "tests/test_foo.py"], [b])
    x, y = sorted((a, b))
    assert out["mode"] == "AUDIT" and out["new"] == [{"a": x, "b": y, "shared": ["shared/foo_mod.py"]}] and out["undecided"] == [{"a": x, "b": y}] and out["held"] == []
    ev = events(db, x, y)
    assert ev == [("OVERLAPS", "PROPOSED", "GATE", GATE)]
    assert db.execute("SELECT from_id, to_id FROM backlog.backlog_link_event WHERE origin = 'GATE'").fetchone() == (x, y)
    again = gate(db, ["shared/foo_mod.py"], [b], nonce="cd" * 16)
    assert again["new"] == [] and again["undecided"] == [{"a": x, "b": y}]               # still undecided, not proposed twice
    assert len(events(db, x, y)) == 1 and holds(db, b) == [] and holds(db, a) == []     # AUDIT raises no hold
    assert db.execute("SELECT new_pairs, mode, tier, files_n FROM backlog.gate_collision ORDER BY collision_id").fetchall() == [(1, "AUDIT", "T0", 2), (0, "AUDIT", "T0", 1)]


def test_block_raises_one_hold_on_the_committing_ticket_only(db):
    a = wip(db, "shared/foo_mod.py")
    b = wip(db)
    out = gate(db, ["shared/foo_mod.py"], [b], ceiling="BLOCK")
    assert out["mode"] == "BLOCK" and out["held"] == [b]
    assert holds(db, b) == [("V-COLLISION",)] and holds(db, a) == []
    assert gate(db, ["shared/foo_mod.py"], [b], ceiling="BLOCK", nonce="cd" * 16)["held"] == []   # not twice
    assert len(holds(db, b)) == 1


def test_the_hook_login_actor_is_fixed_and_a_bad_actor_is_refused(db):
    b = wip(db)
    refused(db, "not canonical", lambda c: gate(c, [], [b], actor="gate"))
    reset(db)


# ---------------------------------------------------------------- adjudication

def collide(conn, ceiling="BLOCK"):
    """Two tickets with an undecided pair; returns (a, b) in canonical order."""
    x = wip(conn, "shared/foo_mod.py")
    y = wip(conn)
    gate(conn, ["shared/foo_mod.py"], [y], ceiling=ceiling)
    return tuple(sorted((x, y)))


def test_only_the_adjudicator_role_adjudicates(db):
    a, b = collide(db)
    db.execute("CREATE ROLE someone_else LOGIN")                                          # even with the right to EXECUTE, a login that is not the adjudicator (or a member) is refused
    db.execute("GRANT USAGE ON SCHEMA backlog TO someone_else")
    db.execute("GRANT EXECUTE ON FUNCTION backlog.sp_adjudicate_overlap(text, text, text, text, text, text) TO someone_else")
    db.execute("SET LOCAL SESSION AUTHORIZATION someone_else")
    refused(db, "only the role backlog_adjudicator", lambda c: adjudicate(c, a, b))
    reset(db)
    db.execute("SET LOCAL SESSION AUTHORIZATION backlog_writer")                          # the shared writer has no EXECUTE at all
    refused(db, "permission denied", lambda c: adjudicate(c, a, b))
    reset(db)
    as_adjudicator(db)
    assert adjudicate(db, b, a) > 0                                                       # either order
    reset(db)


@pytest.mark.parametrize("decision, absorbs, expected", [
    ("OVERLAPS", "the criteria of the other ticket", [("OVERLAPS", "PROPOSED"), ("OVERLAPS", "CONFIRMED")]),
    ("NONE", None, [("OVERLAPS", "PROPOSED"), ("OVERLAPS", "REJECTED")]),
    ("RELATED", None, [("OVERLAPS", "PROPOSED"), ("OVERLAPS", "REJECTED"), ("RELATED", "CONFIRMED")]),
    ("DEPENDS_ON", None, [("OVERLAPS", "PROPOSED"), ("OVERLAPS", "REJECTED"), ("DEPENDS_ON", "CONFIRMED")]),
    ("REVERT", None, [("OVERLAPS", "PROPOSED"), ("OVERLAPS", "CONFIRMED")]),
])
def test_each_decision_writes_its_events_and_closes_the_queue(db, decision, absorbs, expected):
    a, b = collide(db)
    reason = "the commit was reverted by " + "f" * 40 if decision == "REVERT" else "decided in a test, a reason of ten or more"
    as_adjudicator(db)
    adjudicate(db, a, b, decision, reason, absorbs)
    reset(db)
    assert [(t, s) for t, s, *_ in events(db, a, b)] == expected
    assert db.execute("SELECT count(*) FROM backlog.v_gate_queue").fetchone()[0] == 0
    assert db.execute("SELECT decision, absorbs, actor FROM backlog.gate_adjudication WHERE a = %s AND b = %s", (a, b)).fetchone() == (decision, absorbs, OWNER)
    assert db.execute("SELECT count(*) FROM backlog.BACKLOG_LOGS WHERE comment LIKE 'Collision pair%%adjudicated%%' AND action_point_id IN (%s, %s)", (a, b)).fetchone()[0] == 2
    assert pairs(db, ["shared/foo_mod.py"], [b]) == []                                    # decided: a later commit does not propose it again
    assert gate(db, ["shared/foo_mod.py"], [b], ceiling="BLOCK")["new"] == []


def test_holds_go_only_when_the_last_undecided_pair_of_the_ticket_is_decided(db):
    a1, a2 = wip(db, "shared/foo_mod.py"), wip(db, "shared/bar_mod.py")
    b = wip(db)
    gate(db, ["shared/foo_mod.py", "shared/bar_mod.py"], [b], ceiling="BLOCK")
    assert holds(db, b) == [("V-COLLISION",)]
    as_adjudicator(db)
    adjudicate(db, a1, b, "NONE")
    reset(db)
    assert holds(db, b) == [("V-COLLISION",)]                                             # one pair is still undecided
    as_adjudicator(db)
    adjudicate(db, a2, b, "NONE")
    reset(db)
    assert holds(db, b) == []
    assert db.execute("SELECT count(*) FROM backlog.hold_event WHERE kind = 'RELEASE' AND actor = %s", (OWNER,)).fetchone()[0] == 1


def test_adjudication_refusals(db):
    a, b = collide(db)
    c = wip(db)
    as_adjudicator(db)
    refused(db, "must be a canonical human", lambda cn: adjudicate(cn, a, b, actor="agent:claude-opus-5-5"))
    refused(db, "must be a canonical human", lambda cn: adjudicate(cn, a, b, actor="system:calibrator"))
    refused(db, "is not OVERLAPS, RELATED", lambda cn: adjudicate(cn, a, b, "MAYBE"))
    refused(db, "reason of 10 or more", lambda cn: adjudicate(cn, a, b, "NONE", "short"))
    refused(db, "sha of the revert commit", lambda cn: adjudicate(cn, a, b, "REVERT", "reverted, but no sha is given here"))
    refused(db, "is not undecided", lambda cn: adjudicate(cn, a, c))                      # a pair nobody proposed
    adjudicate(db, a, b)
    refused(db, "is not undecided", lambda cn: adjudicate(cn, a, b))                      # twice
    reset(db)


# ---------------------------------------------------------------- the queue and the privileges

def test_the_queue_levels_follow_the_age_of_the_proposal(db):
    ids = [wip(db) for _ in range(6)]
    for (x, y), hours in zip([(ids[0], ids[1]), (ids[2], ids[3]), (ids[4], ids[5])], (10, 50, 100)):
        db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username, origin, event_ts) VALUES (%s, %s, 'OVERLAPS', 'PROPOSED', "
                   "'collision gate in a test', %s, 'GATE', now() - make_interval(hours => %s))", (min(x, y), max(x, y), GATE, hours))
    lv = db.execute("SELECT level FROM backlog.v_gate_queue ORDER BY age_hours").fetchall()
    assert lv == [("OK",), ("ATENCION",), ("ALERTA",)]
    db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) VALUES (%s, %s, 'OVERLAPS', 'PROPOSED', 'a REVIEW proposal in a test', %s)", (min(ids[0], ids[2]), max(ids[0], ids[2]), ME))
    assert db.execute("SELECT count(*) FROM backlog.v_gate_queue").fetchone()[0] == 3     # only the gate's own proposals are in the queue


def test_the_undecided_pairs_of_the_tickets_a_push_carries(db):
    a, b = collide(db)
    c = wip(db)
    assert db.execute("SELECT a, b FROM backlog.fn_gate_undecided(%s)", ([b],)).fetchall() == [(a, b)]
    assert db.execute("SELECT a, b FROM backlog.fn_gate_undecided(%s)", ([c],)).fetchall() == []


def test_the_hook_login_reaches_the_rule_and_nothing_of_the_adjudication(db):
    a, b = collide(db, ceiling="AUDIT")
    c = wip(db, "shared/c_mod.py")
    d = wip(db)
    db.execute("SET LOCAL SESSION AUTHORIZATION backlog_gate")
    assert db.execute("SELECT mode FROM backlog.fn_collision_state('AUDIT')").fetchone()[0] == "AUDIT"
    out = db.execute("SELECT backlog.sp_gate_collision(%s, %s, 'AUDIT', %s, 'system:gate')", (["shared/c_mod.py"], [d], "cd" * 16)).fetchone()[0]
    assert len(out["new"]) == 1
    assert db.execute("SELECT count(*) FROM backlog.fn_gate_undecided(%s)", ([a],)).fetchone()[0] == 1
    refused(db, "backlog_gate login writes only as system:gate", lambda cn: gate(cn, [], [d], actor=ME))
    refused(db, "permission denied", lambda cn: cn.execute("SELECT * FROM backlog.fn_collision_pairs('{}', '{}', 'T0')"))
    refused(db, "permission denied", lambda cn: adjudicate(cn, a, b))
    refused(db, "permission denied", lambda cn: set_mode(cn, "OFF"))
    refused(db, "permission denied", lambda cn: cn.execute("SELECT * FROM backlog.v_gate_queue"))
    reset(db)
    assert c


def test_privileges_of_the_other_roles(db):
    for sig in ("backlog.sp_adjudicate_overlap(text,text,text,text,text,text)", "backlog.fn_gate_undecided(text[])"):
        assert db.execute("SELECT has_function_privilege('backlog_adjudicator', %s, 'EXECUTE')", (sig,)).fetchone()[0] is True
    for sig in ("backlog.sp_gate_collision(text[],text[],text,text,text)", "backlog.sp_set_collision_mode(text,text,text,text,text,jsonb)"):
        assert db.execute("SELECT has_function_privilege('backlog_adjudicator', %s, 'EXECUTE')", (sig,)).fetchone()[0] is False
        assert db.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (sig,)).fetchone()[0] is False
    assert db.execute("SELECT has_function_privilege('backlog_writer', 'backlog.sp_adjudicate_overlap(text,text,text,text,text,text)', 'EXECUTE')").fetchone()[0] is False
    assert db.execute("SELECT has_function_privilege('backlog_writer', 'backlog.sp_set_collision_mode(text,text,text,text,text,jsonb)', 'EXECUTE')").fetchone()[0] is True
    for t in mig.PHASES["collision"]["tables"]:
        assert db.execute("SELECT has_table_privilege('backlog_writer', %s, 'INSERT')", (f"backlog.{t}",)).fetchone()[0] is False


# ---------------------------------------------------------------- the measurements the calibrator reads

def test_gate_measurements_count_the_window_from_the_latest_event(db):
    import shared.backlog_client as bc
    from datetime import datetime, timezone
    m0 = bc.gate_measurements("AUDIT", conn=db)
    assert m0["state"] == {"mode": "AUDIT", "tier": "T0"} and m0["audit_start"] is None and m0["anchor"] is None and m0["share_3d"] is None
    assert m0["window"] == {"adjudicated": 0, "positive": 0, "confirmed": 0, "flagged": 0}
    # three pairs proposed by the gate, adjudicated NONE / OVERLAPS / DEPENDS_ON
    ids = [wip(db, "shared/m_mod.py") for _ in range(3)]
    t = wip(db)
    gate(db, ["shared/m_mod.py"], [t])
    as_adjudicator(db)
    for x, decision in zip(sorted(ids), ("NONE", "OVERLAPS", "DEPENDS_ON")):
        adjudicate(db, x, t, decision)
    reset(db)
    # two overlaps a person confirmed by ANOTHER route: one the gate had proposed (the pair of ids[0] and t was), one it had not
    z1, z2 = wip(db), wip(db)
    db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username, origin, event_ts) VALUES (%s, %s, 'OVERLAPS', 'CONFIRMED', 'confirmed in a review', %s, 'REVIEW', clock_timestamp())",
               (min(z1, z2), max(z1, z2), ME))
    a0 = sorted(ids)[0]
    db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username, origin, event_ts) VALUES (%s, %s, 'OVERLAPS', 'CONFIRMED', 'confirmed in a review', %s, 'REVIEW', clock_timestamp())",
               (min(a0, t), max(a0, t), ME))
    m = bc.gate_measurements("AUDIT", conn=db)
    assert m["window"] == {"adjudicated": 3, "positive": 2, "confirmed": 2, "flagged": 1}          # the gate's own OVERLAPS adjudication is not counted as a confirmation
    assert m["share_3d"] == pytest.approx(1.0) and m["audit_start"] is not None and m["anchor"] is None
    db.execute("INSERT INTO backlog.gate_state (mode, tier, actor, reason) VALUES ('AUDIT', 'T1', 'system:calibrator', 'tightened by the calibrator in a test')")
    m = bc.gate_measurements("AUDIT", conn=db)
    assert m["anchor"] is not None and m["state"] == {"mode": "AUDIT", "tier": "T1"}
    assert m["window"] == {"adjudicated": 0, "positive": 0, "confirmed": 0, "flagged": 0}          # a new window starts at the latest event
    with pytest.raises(ValueError):
        bc.gate_measurements("LOUD", conn=db)
    assert isinstance(m["now"], datetime) and m["now"].tzinfo is not None and datetime.now(timezone.utc)


def test_the_show_lines_say_what_block_still_needs(db):
    import gate_mode
    import shared.backlog_client as bc
    from datetime import timedelta as td
    m = bc.gate_measurements("AUDIT", conn=db)
    lines = gate_mode.show_lines("AUDIT", m, [])
    assert "ceiling AUDIT" in lines[0] and "not started" in lines[1] and "BLOCK needs: 14 days of AUDIT (not started); a share of 10 % or less (no data)" in lines[-1]
    m["audit_start"], m["share_3d"] = m["now"] - td(days=20), 0.04
    lines = gate_mode.show_lines("BLOCK", {**m, "state": {"mode": "AUDIT", "tier": "T0"}}, [{"level": "ALERTA"}, {"level": "ATENCION"}, {"level": "OK"}])
    assert "undecided pairs: 3 (1 over 96 h, 1 over 48 h)" in lines[2] and "the database would accept it" in lines[-1]
    assert not gate_mode.show_lines("BLOCK", {**m, "state": {"mode": "BLOCK", "tier": "T0"}}, [])[-1].startswith("  BLOCK needs")
