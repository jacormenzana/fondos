# tests/test_backlog_gate_pg.py
# -*- coding: utf-8 -*-
"""Phase C slice 2 of Backlog v2 (FND-0334): db/pg_backlog/80_hook.sql applied by scripts/ops/migrate_backlog_v2.py --phase hook, the client functions and
scripts/audit/backlog_sync.py judging commits by their Backlog-Gate trailer. Hermetic: the throwaway container inside a rolled-back savepoint (pg_conn); never the live backlog.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_gate_pg.py
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

import backlog_sync as bs  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from shared import backlog_gate as g  # noqa: E402
from shared.testing.sandbox import acting_as  # noqa: E402
from test_backlog_groups_pg import violations  # noqa: E402
from test_backlog_transitions_pg import FORCE_REASON, ME, _base, _mk, refused, tr  # noqa: E402

SYNC, GATE = "system:sync", "system:gate"
TREE = "1" * 40


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    for phase in ("relations", "groups", "sync", "hook"):
        mig.migrate(pg_conn, phase)
    return pg_conn


def bypass(sha, ap, reason, ts, *, conn):
    with acting_as(SYNC):
        return bc.record_hook_bypass(sha, ap, reason, ts, conn=conn)


def gate_check(tree, aps, *, conn):
    with acting_as(GATE):
        return bc.gate_check(tree, aps, conn=conn)


def now(conn):
    return conn.execute("SELECT clock_timestamp()").fetchone()[0]


def holds(conn, ap):
    return conn.execute("SELECT violation_code, reason FROM backlog.v_open_hold WHERE action_point_id = %s ORDER BY 1", (ap,)).fetchall()


# ---------------------------------------------------------------- migration

def test_hook_needs_the_earlier_phases(pg_conn):
    _base(pg_conn)
    mig.migrate(pg_conn, "relations")
    with pytest.raises(RuntimeError, match="v_violations_c"):
        mig.migrate(pg_conn, "hook")


def test_hook_is_idempotent_and_joins_the_catalogue_view(db):
    mig.migrate(db, "hook")
    have = mig.existing(db, "hook")
    spec = mig.PHASES["hook"]
    assert have["tables"] == sorted(spec["tables"]) and have["views"] == sorted(spec["views"]) and have["routines"] == sorted(spec["routines"])
    a = _mk(db)
    bypass("a" * 40, a, "TRAILER_MISSING", now(db), conn=db)
    assert violations(db, "V-HOOK-BYPASS", a) == [("V-HOOK-BYPASS", a)]
    mig.migrate(db, "sync")                                            # re-applying slice 1 keeps the slice 2 code in the union
    mig.migrate(db, "groups")
    assert violations(db, "V-HOOK-BYPASS", a) == [("V-HOOK-BYPASS", a)]


def test_rollback_keeps_history_and_holds_and_removes_the_code(db):
    a = _mk(db)
    nonce = gate_check(TREE, [a], conn=db)
    bypass("b" * 40, a, "UNKNOWN_NONCE", now(db), conn=db)
    mig.migrate(db, "hook", down=True)
    have = mig.existing(db, "hook")
    assert have["views"] == [] and have["routines"] == [] and have["tables"] == ["gate_check", "hook_bypass"]
    assert db.execute("SELECT count(*) FROM backlog.v_backlog_violations WHERE code = 'V-HOOK-BYPASS'").fetchone()[0] == 0
    assert len(holds(db, a)) == 1                                       # a rollback never releases a hold
    mig.migrate(db, "hook")
    assert violations(db, "V-HOOK-BYPASS", a) == [("V-HOOK-BYPASS", a)]
    assert bc.gate_checks([nonce], conn=db) == {nonce: TREE}


# ---------------------------------------------------------------- gate_check

def test_gate_check_returns_a_nonce_bound_to_the_tree(db):
    a, b = _mk(db), _mk(db)
    n1 = gate_check(TREE, [a, b], conn=db)
    n2 = gate_check("2" * 40, [a], conn=db)
    assert g.NONCE.match(n1) and g.NONCE.match(n2) and n1 != n2
    assert bc.gate_checks([n1, n2, "00" * 16], conn=db) == {n1: TREE, n2: "2" * 40}
    assert bc.gate_checks([], conn=db) == {}
    assert db.execute("SELECT tickets, actor FROM backlog.gate_check WHERE nonce = %s", (n1,)).fetchone() == ([a, b], GATE)


def test_gate_check_refuses_a_bad_actor_a_bad_tree_and_any_update(db):
    refused(db, "not canonical", lambda c: c.execute("SELECT backlog.sp_gate_check(%s, '{}', 'gate')", (TREE,)))
    refused(db, "tree_hash", lambda c: gate_check("not-a-tree", [], conn=c))
    n = gate_check(TREE, [], conn=db)
    refused(db, "append-only", lambda c: c.execute("UPDATE backlog.gate_check SET tree_hash = %s WHERE nonce = %s", ("3" * 40, n)))
    refused(db, "append-only", lambda c: c.execute("DELETE FROM backlog.gate_check"))


# ---------------------------------------------------------------- hook_bypass and the hold

def test_a_new_bypass_raises_one_hold_and_a_repeat_raises_none(db):
    a = _mk(db)
    assert bypass("c" * 40, a, "TRAILER_MISSING", now(db), conn=db) is True
    assert bypass("c" * 40, a, "TRAILER_MISSING", now(db), conn=db) is False
    h = holds(db, a)
    assert len(h) == 1 and h[0][0] == "V-HOOK-BYPASS" and "cccccccccccc" in h[0][1]
    assert bypass("d" * 40, a, "TREE_MISMATCH", now(db), conn=db) is True       # another commit, another hold
    assert len(holds(db, a)) == 2


def test_a_bypass_on_a_closed_ticket_is_recorded_without_a_hold(db):
    a = _mk(db)
    tr(db, a, "CLOSED", solution_type="REJECTED", force=True, comment=FORCE_REASON)
    assert bypass("e" * 40, a, "UNKNOWN_NONCE", now(db) + timedelta(hours=1), conn=db) is True
    assert holds(db, a) == [] and violations(db, "V-HOOK-BYPASS", a) == [("V-HOOK-BYPASS", a)]


def test_bypass_refusals(db):
    a = _mk(db)
    with pytest.raises(ValueError):
        bypass("f" * 40, a, "BECAUSE", now(db), conn=db)
    refused(db, "does not exist", lambda c: bypass("f" * 40, "FND-0001", "TRAILER_MISSING", now(c), conn=c))
    refused(db, "not canonical", lambda c: c.execute("SELECT backlog.sp_record_hook_bypass(%s, %s, 'TRAILER_MISSING', now(), 'sync')", ("f" * 40, a)))
    refused(db, "hook_bypass_sha_check", lambda c: bypass("short", a, "TRAILER_MISSING", now(c), conn=c))
    bypass("8" * 40, a, "TRAILER_MISSING", now(db), conn=db)
    refused(db, "append-only", lambda c: c.execute("UPDATE backlog.hook_bypass SET reason = 'TREE_MISMATCH'"))
    refused(db, "append-only", lambda c: c.execute("DELETE FROM backlog.hook_bypass"))


def test_the_hold_is_released_only_by_a_human(db):
    a = _mk(db)
    bypass("9" * 40, a, "TRAILER_MISSING", now(db), conn=db)
    hold_id = db.execute("SELECT hold_id FROM backlog.v_open_hold WHERE action_point_id = %s", (a,)).fetchone()[0]
    refused(db, "only a human", lambda c: c.execute("SELECT backlog.sp_release_hold(%s, %s, 'released by an agent, not allowed')", (hold_id, ME)))


def test_privileges(db):
    for t in mig.PHASES["hook"]["tables"]:
        assert db.execute("SELECT has_table_privilege('backlog_writer', %s, 'INSERT')", (f"backlog.{t}",)).fetchone()[0] is False
        assert db.execute("SELECT has_table_privilege('backlog_writer', %s, 'SELECT')", (f"backlog.{t}",)).fetchone()[0] is True
    for sig in ("backlog.sp_gate_check(text,text[],text)", "backlog.sp_record_hook_bypass(text,text,text,timestamptz,text)"):
        assert db.execute("SELECT has_function_privilege('backlog_writer', %s, 'EXECUTE')", (sig,)).fetchone()[0] is True
        assert db.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (sig,)).fetchone()[0] is False


def test_store_presence_probe(pg_conn):
    _base(pg_conn)
    assert bc.hook_store_present(conn=pg_conn) is False
    for phase in ("relations", "groups", "sync", "hook"):
        mig.migrate(pg_conn, phase)
    assert bc.hook_store_present(conn=pg_conn) is True


# ---------------------------------------------------------------- backlog_sync judging a log

def log_line(sha, ts, subject, refs="", tree=TREE, parents="p" * 40, gate=""):
    return f"{sha}\x1f{ts.isoformat()}\x1f{subject}\x1f{refs}\x1f{tree}\x1f{parents}\x1f{gate}\x1e\n"


def test_sync_records_only_the_bypasses_after_the_epoch_and_holds_the_ticket_once(db):
    a, b, c, d = _mk(db), _mk(db), _mk(db), _mk(db)
    t0 = now(db)
    good = gate_check(TREE, [a], conn=db)
    wrong_tree = gate_check("2" * 40, [b], conn=db)
    log = (log_line("1" * 40, t0 - timedelta(days=2), f"old: {a}")                                        # before the epoch
           + log_line("2" * 40, t0 + timedelta(minutes=1), f"feat: {a}", gate=good)                       # checked
           + log_line("3" * 40, t0 + timedelta(minutes=2), f"feat: {b}", gate=wrong_tree)                 # tree mismatch
           + log_line("4" * 40, t0 + timedelta(minutes=3), f"feat: {c}")                                  # trailer missing
           + log_line("5" * 40, t0 + timedelta(minutes=4), f"feat: {d}", gate="offline")                  # pending: no penalty
           + log_line("6" * 40, t0 + timedelta(minutes=5), f"Merge {c}", parents=("p" * 40) + " " + ("q" * 40))   # merge: exempt
           + log_line("7" * 40, t0 + timedelta(minutes=6), f"feat: {d} again", gate="ab" * 16))           # unknown nonce
    commits = bs.parse_log(log)
    statuses = {t["action_point_id"]: t["status"] for t in bc.list_tickets(conn=db)}
    epoch = g.parse_epoch((t0 - timedelta(hours=1)).isoformat())
    vs = g.verdicts(commits, bc.gate_checks([c_["gate"] for c_ in commits if c_["gate"] and c_["gate"] != "offline"], conn=db), epoch, set(statuses))
    verdict = {(c_["sha"][0], ap): (v, why) for c_, ap, v, why in vs}
    assert verdict[("1", a)][0] == g.EXEMPT and verdict[("2", a)][0] == g.CHECKED and verdict[("5", d)][0] == g.PENDING and verdict[("6", c)][0] == g.EXEMPT
    assert verdict[("3", b)] == (g.BYPASS, g.REASON_TREE) and verdict[("4", c)] == (g.BYPASS, g.REASON_MISSING) and verdict[("7", d)] == (g.BYPASS, g.REASON_UNKNOWN)
    p = bs.plan(commits, statuses, set(), forward_enabled=False)
    p["bypass"] = [(c_, ap, why) for c_, ap, v, why in vs if v == g.BYPASS]
    with acting_as(SYNC):
        done = bs.apply_plan(p, {"written": 0, "moved": 0, "bypass": 0}, conn=db)
    assert done["bypass"] == 3
    assert {r[0] for r in db.execute("SELECT action_point_id FROM backlog.hook_bypass").fetchall()} == {b, c, d}
    assert len(holds(db, b)) == 1 and len(holds(db, c)) == 1 and len(holds(db, d)) == 1 and holds(db, a) == []
    with acting_as(SYNC):
        again = bs.apply_plan(p, {"written": 0, "moved": 0, "bypass": 0}, conn=db)                          # idempotent: no second record, no second hold
    assert again["bypass"] == 0 and len(holds(db, c)) == 1


def test_the_report_line(db):
    t0 = now(db)
    commits = bs.parse_log(log_line("4" * 40, t0, "feat: FND-9001"))
    assert "NOT ARMED" in bs.gate_report(g.verdicts(commits, {}, None, {"FND-9001"}), None, True)
    line = bs.gate_report(g.verdicts(commits, {}, g.parse_epoch((t0 - timedelta(hours=1)).isoformat()), {"FND-9001"}), g.parse_epoch((t0 - timedelta(hours=1)).isoformat()), False)
    assert "BYPASS 1" in line and "not applied" in line
