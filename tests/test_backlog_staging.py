# tests/test_backlog_staging.py
# -*- coding: utf-8 -*-
"""FND-0351, no database: the pure parts of scripts/ops/backlog_staging.py and the shared guard shared/backlog_scratch.py. The command's promise -- the live backlog is only ever READ, and it is proved --
rests on `sequence_diff`; the guard's promise -- a live target is refused whatever its name -- rests on `scratch_verdict`."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts" / "ops"), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_phase_b_replay as rp  # noqa: E402
import backlog_staging as st  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_scratch as sc  # noqa: E402

LIVE = {("local", "5436", "gestion"), ("local", "5436", "fondos")}


def test_scenario_names_map_to_tests_and_unknown_names_are_refused():
    assert st.k_expression([]) == ""
    assert st.k_expression(["gate_blocked", "a2_revoke"]) == "test_scenario_gate_blocked or test_scenario_a2_revoke_rehearsal"
    with pytest.raises(ValueError, match="unknown scenario"):
        st.k_expression(["gate_blocked", "nope"])
    import re
    text = (ROOT / "tests" / "test_backlog_staging_pg.py").read_text(encoding="utf-8")
    for name, test in st.SCENARIOS.items():
        assert re.search(rf"^def {test}\(", text, re.M), (name, test)                       # every scenario the command names exists


def test_phases_are_validated_against_the_migration_script():
    assert st.parse_phases("transitions, relations", mig.PHASES) == ["transitions", "relations"]
    assert st.parse_phases(st.DEFAULT_PHASES, mig.PHASES) == ["transitions", "relations"]
    for bad in ("", " , ", "transitions,nope"):
        with pytest.raises(ValueError):
            st.parse_phases(bad, mig.PHASES)


def test_a_moved_live_sequence_is_reported_and_an_untouched_one_is_not():
    before = {"seq_backlog_fnd": 354, "backlog_logs_log_id_seq": 1701, "status_event_event_id_seq": 410}
    assert st.sequence_diff(before, dict(before)) == []
    assert st.sequence_diff(before, {**before, "backlog_logs_log_id_seq": 1702}) == [("backlog_logs_log_id_seq", 1701, 1702)]
    assert st.sequence_diff(before, {k: v for k, v in before.items() if k != "seq_backlog_fnd"}) == [("seq_backlog_fnd", 354, None)]
    assert st.sequence_diff({}, {"new_seq": 1}) == [("new_seq", None, 1)]


def test_the_guard_refuses_every_live_target_whatever_its_name_and_accepts_only_the_allow_list():
    assert sc.scratch_verdict("127.0.0.1", 5436, "gestion", LIVE)[0] is False
    assert sc.scratch_verdict("localhost", 5436, "backlog_staging_x", LIVE)[0] is False        # a staging NAME on the live SERVER: scratch never shares a server with live data
    ok, why = sc.scratch_verdict("::1", 5436, "fondos_test", LIVE)
    assert ok is False and "SAME SERVER" in why
    assert sc.scratch_verdict("db.example.com", 5436, "backlog_staging_x", LIVE)[0] is True    # another machine: a different server
    assert sc.scratch_verdict("127.0.0.1", 5440, "fondos_test", LIVE) == (True, "scratch")
    assert sc.scratch_verdict("127.0.0.1", 5440, "backlog_staging_20261031", LIVE)[0] is True
    for name in ("gestion", "fondos", "production", "postgres", "", "backlog_staging", "x_backlog_staging_"):
        assert sc.scratch_verdict("127.0.0.1", 5440, name, LIVE)[0] is False, name


def test_the_replay_tool_keeps_the_names_it_always_exported():
    for n in ("assert_scratch", "scratch_verdict", "live_targets", "SCRATCH_DBNAMES", "SCRATCH_DBPREFIXES", "_norm_host"):
        assert hasattr(rp, n), n
    assert rp.scratch_verdict is sc.scratch_verdict and rp.live_targets is sc.live_targets


def test_every_script_that_calls_the_real_routines_on_a_snapshot_calls_the_guard():
    """FND-0351 (3): the guard is programmatic. A tool that loads a snapshot or replays history must call assert_scratch before its first statement."""
    for path, needle in ((ROOT / "scripts" / "audit" / "backlog_phase_b_replay.py", "assert_scratch(conn)"), (ROOT / "tests" / "test_backlog_staging_pg.py", "assert_scratch(pg_conn, who=WHO)")):
        assert needle in path.read_text(encoding="utf-8"), path.name
    fixture = (ROOT / "tests" / "test_backlog_staging_pg.py").read_text(encoding="utf-8").split("def staged(pg_conn):", 1)[1].split("def clean_in_progress", 1)[0]
    assert fixture.index("assert_scratch") < fixture.index("_base(pg_conn)") < fixture.index("load_snapshot")      # the guard is the FIRST statement
