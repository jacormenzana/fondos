# tests/test_backlog_gate_override_pg.py
# -*- coding: utf-8 -*-
"""FND-0350: backlog.sp_set_gate takes a governed EXECUTIVE OVERRIDE (specification approved by the owner on 2026-10-28). Hermetic (pg_conn): a human actor can put the delivery gate in
ENFORCE without a clear audit window by passing the flag with a reason of 80+ characters; an agent can never pass it; the row is flagged and listed; nothing needs a raw insert any more.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_gate_override_pg.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from shared.testing.sandbox import acting_as  # noqa: E402
from test_backlog_transitions_pg import ME, OWNER, _base, refused  # noqa: E402

DIRECTIVE = "Owner directive of 2026-10-29: the audit window is waived for this deployment, the executive accepts the risk of enforcing on day one (a reason that cites the written directive)"
assert len(DIRECTIVE) >= 80


@pytest.fixture
def dba(pg_conn):
    """Phase B as deployed: the gate is in AUDIT, the window is not clear."""
    _base(pg_conn)
    mig.migrate(pg_conn, "relations")
    return pg_conn


def set_gate(conn, mode, reason, actor=OWNER, override=None):
    if override is None:
        return conn.execute("SELECT backlog.sp_set_gate('DELIVERY', %s, %s, %s)", (mode, actor, reason)).fetchone()[0]
    return conn.execute("SELECT backlog.sp_set_gate('DELIVERY', %s, %s, %s, %s)", (mode, actor, reason, override)).fetchone()[0]


def test_the_four_argument_form_still_works_and_there_is_exactly_one_function(dba):
    assert dba.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'backlog'::regnamespace AND proname = 'sp_set_gate'").fetchone()[0] == 1       # no ambiguous overload
    set_gate(dba, "OFF", "the old four-argument call, unchanged behaviour")
    assert bc.gate_mode(conn=dba) == "OFF"
    set_gate(dba, "AUDIT", "back to the audit mode with the old call", override=False)
    assert bc.gate_mode(conn=dba) == "AUDIT"


def test_an_agent_can_never_pass_the_flag_whatever_the_reason(dba):
    refused(dba, "only a human actor", lambda c: set_gate(c, "ENFORCE", DIRECTIVE, actor=ME, override=True))
    refused(dba, "only a human actor", lambda c: set_gate(c, "ENFORCE", DIRECTIVE, actor="system:owner-waiver", override=True))
    assert bc.gate_mode(conn=dba) == "AUDIT"


@pytest.mark.parametrize("reason", ["", "short", "x" * 79])
def test_the_override_needs_a_reason_of_eighty_characters(dba, reason):
    refused(dba, "80\\+ characters", lambda c: set_gate(c, "ENFORCE", reason, override=True))
    assert bc.gate_mode(conn=dba) == "AUDIT"


def test_a_reason_of_exactly_eighty_characters_is_enough(dba):
    set_gate(dba, "ENFORCE", "y" * 80, override=True)
    assert bc.gate_mode(conn=dba) == "ENFORCE"


@pytest.mark.parametrize("mode", ["AUDIT", "OFF"])
def test_the_override_applies_only_to_enforce(dba, mode):
    refused(dba, "applies only to ENFORCE", lambda c: set_gate(c, mode, DIRECTIVE, override=True))


def test_an_unknown_mode_is_still_refused_with_the_override(dba):
    refused(dba, "must be OFF, AUDIT or ENFORCE", lambda c: set_gate(c, "WHATEVER", DIRECTIVE, override=True))


def test_the_override_reaches_enforce_with_a_window_that_is_not_clear_and_leaves_a_flagged_row(dba):
    assert bc.gate_audit_window(conn=dba)["clear"] is False and bc.gate_audit_window(conn=dba)["last_override_ts"] is None
    refused(dba, "executive override", lambda c: set_gate(c, "ENFORCE", DIRECTIVE))                # the ordinary path stays closed, and the error names the way out
    eid = set_gate(dba, "ENFORCE", DIRECTIVE, override=True)
    assert bc.gate_mode(conn=dba) == "ENFORCE"
    rows = dba.execute("SELECT event_id, mode, actor, override, reason FROM backlog.gate_switch ORDER BY event_id").fetchall()
    assert [(r[1], r[2], r[3]) for r in rows] == [("AUDIT", "system:deploy", False), ("ENFORCE", OWNER, True)] and rows[1][0] == eid and rows[1][4] == DIRECTIVE
    assert [(r[0], r[1], r[2]) for r in dba.execute("SELECT event_id, mode, actor FROM backlog.v_gate_override_event").fetchall()] == [(eid, "ENFORCE", OWNER)]
    assert bc.gate_audit_window(conn=dba)["last_override_ts"] is not None


def test_going_back_to_audit_and_enforcing_again_needs_a_clear_window_or_a_new_override(dba):
    set_gate(dba, "ENFORCE", DIRECTIVE, override=True)
    set_gate(dba, "AUDIT", "back to the audit mode after the first day of enforcement")
    refused(dba, "needs a clear audit window", lambda c: set_gate(c, "ENFORCE", "enforcing again without the flag"))
    set_gate(dba, "ENFORCE", DIRECTIVE + " (second override)", override=True)
    assert dba.execute("SELECT count(*) FROM backlog.v_gate_override_event").fetchone()[0] == 2


def test_the_ordinary_path_to_enforce_is_unchanged_when_the_window_is_clear(dba):
    dba.execute("ALTER TABLE backlog.gate_switch DISABLE TRIGGER append_only")
    dba.execute("UPDATE backlog.gate_switch SET event_ts = event_ts - interval '49 hours'")
    dba.execute("ALTER TABLE backlog.gate_switch ENABLE TRIGGER append_only")
    for n in range(5):                                                                              # five different tickets with a gated move inside the window
        dba.execute("SELECT set_config('backlog.via_proc', 'on', true)")
        ap = f"FND-{9100 + n}"
        dba.execute("INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority, session_id) VALUES (%s, 't', 'FND', 'BUG', 'N/A', 'IN_PROGRESS', 'LOW', 'FND_20261026_DEV_TEST')", (ap,))
        dba.execute("SELECT backlog.sp_transition(%s, 'READY_FOR_DEPLOY', %s, 'FND_20261029_DEV_TEST', 'ready for deploy in a test', NULL, 'R2026.10-OVR')", (ap, ME))
    assert bc.gate_audit_window(conn=dba)["clear"] is True
    eid = set_gate(dba, "ENFORCE", "the owner enforces after a clear window, no override")
    assert dba.execute("SELECT override FROM backlog.gate_switch WHERE event_id = %s", (eid,)).fetchone()[0] is False


def test_applying_the_phase_again_adds_the_column_with_false_for_the_rows_written_before(dba):
    """The precedent of 2026-10-28 (event 2, a direct row by the owner role) predates the column: it must read false and stay untouched when the phase is applied again."""
    dba.execute("ALTER TABLE backlog.gate_switch DROP COLUMN override CASCADE")                       # the state of the live database before FND-0350
    dba.execute("INSERT INTO backlog.gate_switch (gate, mode, actor, reason) VALUES ('DELIVERY', 'ENFORCE', 'system:owner-waiver', 'a direct row written before the column existed')")
    mig.migrate(dba, "relations")
    mig.migrate(dba, "relations")                                                                       # idempotent
    assert dba.execute("SELECT mode, actor, override FROM backlog.gate_switch ORDER BY event_id").fetchall() == [("AUDIT", "system:deploy", False), ("ENFORCE", "system:owner-waiver", False)]
    assert dba.execute("SELECT count(*) FROM backlog.v_gate_override_event").fetchone()[0] == 0


def test_the_rollback_removes_the_function_and_the_view(dba):
    mig.migrate(dba, "relations", down=True)
    assert dba.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'backlog'::regnamespace AND proname = 'sp_set_gate'").fetchone()[0] == 0
    assert dba.execute("SELECT to_regclass('backlog.v_gate_override_event') IS NULL").fetchone()[0] is True


def test_the_shared_writer_can_execute_the_function_but_an_agent_identity_is_refused_inside_it(dba):
    dba.execute("SET LOCAL ROLE backlog_writer")
    try:
        assert dba.execute("SELECT count(*) FROM backlog.v_gate_override_event").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="only a human actor"):
            with dba.transaction():
                set_gate(dba, "ENFORCE", DIRECTIVE, actor=ME, override=True)
    finally:
        dba.execute("RESET ROLE")


def test_the_client_passes_the_flag_for_a_human_and_refuses_it_for_an_agent_before_the_database(dba):
    with acting_as(OWNER):
        bc.set_gate("ENFORCE", DIRECTIVE, executive_override=True, conn=dba)
    assert bc.gate_mode(conn=dba) == "ENFORCE" and dba.execute("SELECT count(*) FROM backlog.v_gate_override_event").fetchone()[0] == 1
    with pytest.raises(bc.ActorUnknown, match="only a human"):                                       # the environment identity of the tests is an agent
        bc.set_gate("ENFORCE", DIRECTIVE, executive_override=True, conn=dba)


def test_the_catalog_and_the_acceptance_know_the_override_column(dba):
    import backlog_phase_a_acceptance as acc
    assert bc.phase_b_catalog(conn=dba)["override_column"] is True
    after = bc.history_fingerprint(conn=dba)
    results = acc.evaluate_relations(0, after, after, bc.phase_b_catalog(conn=dba))
    assert [n for n, ok, _ in results if not ok] == []
    assert any("executive-override column" in n for n, _, _ in results)


# --- the rule that makes the override necessary in the first place: nothing inserts into gate_switch by hand ----------------------------------------------------------------------

_RAW_GATE = re.compile(r"INSERT\s+INTO\s+backlog\.gate_switch", re.IGNORECASE)
ALLOWED_GATE_INSERTS = {
    "db/pg_backlog/40_relations_v2.sql": "the DDL: the AUDIT seed row of the deployment and sp_set_gate itself",
    "scripts/audit/backlog_phase_b_replay.py": "FND-0333 the replay sets the gate mode of its THROWAWAY scratch database; it refuses any live target (assert_scratch)",
}


def test_no_repository_code_inserts_into_gate_switch_outside_the_ddl_the_scratch_replay_and_the_tests():
    offenders = []
    for path in _ROOT.rglob("*"):
        rel = path.relative_to(_ROOT).as_posix()
        if (not path.is_file() or path.suffix not in (".py", ".sql") or rel.startswith(("tests/", "out/", ".git/", "db/pg_backlog/down/")) or "/node_modules/" in rel):
            continue
        if _RAW_GATE.search(path.read_text(encoding="utf-8", errors="replace")) and rel not in ALLOWED_GATE_INSERTS:
            offenders.append(rel)
    assert not offenders, f"a raw INSERT INTO backlog.gate_switch outside the allow-list (use backlog.sp_set_gate, with executive_override when the window is waived): {offenders}"
    stale = [r for r in ALLOWED_GATE_INSERTS if not _RAW_GATE.search((_ROOT / r).read_text(encoding="utf-8"))]
    assert not stale, f"allow-listed files that no longer insert into gate_switch (remove them): {stale}"
