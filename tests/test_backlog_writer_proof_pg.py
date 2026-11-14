# tests/test_backlog_writer_proof_pg.py
# -*- coding: utf-8 -*-
"""FND-0370: the proof harness of shared/backlog_writer_proof.py on a SCRATCH Postgres with the real DDL and a snapshot loaded (the same machinery as the staging scenarios, FND-0351).

Two parts:
  * the harness proves ITSELF with a demo writer: a good one passes; each of six bad ones (ignores the kill switch, ignores the caps, is not idempotent, concentrates its writes on one ticket, duplicates a
    write, writes a kind it did not declare) fails for the right reason. This runs in every hermetic suite.
  * `test_prove_the_real_writer` runs only when scripts/gov/backlog/writer_proof.py asks for it (FONDOS_PROOF_WRITER, FONDOS_PROOF_SNAPSHOT, FONDOS_PROOF_RESULT): it loads a REAL snapshot, runs the real writer's
    entry for real in the scratch database and writes the result for the CLI to record.
Every scratch check calls assert_scratch first (shared/backlog_scratch.py): it refuses any live target, whatever its name."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "gov" / "backlog"), str(_ROOT / "scripts" / "mig" / "backlog"), str(_ROOT / "scripts" / "mig" / "backlog"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_phase_b_replay as rp  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from shared import backlog_writer_proof as wp  # noqa: E402
from shared import backlog_writers as bw  # noqa: E402
from shared.backlog_scratch import assert_scratch  # noqa: E402
from test_backlog_staging_pg import synthetic_snapshot  # noqa: E402
from test_backlog_transitions_pg import _base  # noqa: E402

WHO = "backlog_writer_proof"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
DEMO = bw.WriterSpec(name="demo-writer", writes=("LOG",), switch="DEMO_SWITCH", files=("shared/backlog_writers.py",), entry="demo",
                     caps=bw.Caps(max_writes_per_run=5, max_writes_per_ticket=1, max_top_ticket_share=0.5, min_writes_for_share=8, max_duplicates=0))
PHASES = "transitions,relations"


def scratch_guard(conn):
    assert_scratch(conn, who=WHO)


@pytest.fixture
def scratch(pg_conn):
    scratch_guard(pg_conn)
    _base(pg_conn)
    for phase in PHASES.split(","):
        mig.migrate(pg_conn, phase)
    rp.load_snapshot(pg_conn, synthetic_snapshot())
    return pg_conn


# ── a demo writer, good and bad ─────────────────────────────────────────────────

def demo_entry(*, honours_switch=True, honours_caps=True, idempotent=True, concentrated=False, duplicated=False, undeclared=False, chatty=False):
    TEXT = "[demo-writer] note on {}"
    state = {"run": 0}

    def plan(bc_, now):
        ids = [t["action_point_id"] for t in bc_.list_tickets(open_only=True)][:6]
        if concentrated:
            return [bw.Write(ids[0], "LOG", f"k{i}", TEXT.format(ids[0]) + f" #{i}") for i in range(4)]
        out = [bw.Write(t, "LOG", t, TEXT.format(t)) for t in ids]
        if duplicated:
            out.append(bw.Write(ids[0], "LOG", ids[0], TEXT.format(ids[0])))
        return out

    def apply(bc_, planned, *, caps, enabled):
        if honours_switch and not enabled:
            return []
        made = []
        run = state["run"]
        for w in planned:
            if honours_caps and len(made) >= caps.max_writes_per_run:
                break
            if chatty:                                                         # a different note on every run: never a duplicate, never quiet
                w = bw.Write(w.ticket, "LOG", f"{w.key}-run{run}", f"{w.text} (run {run})")
            elif idempotent and any(r["comment"] == w.text for r in bc_.list_logs(ids=[w.ticket])):
                continue
            bc_.append_log(w.ticket, "system:demo-writer", w.text)
            made.append(w)
        state["run"] += 1
        if undeclared and made:
            bc_.record_link_event(from_id=made[0].ticket, to_id=planned[-1].ticket if planned[-1].ticket != made[0].ticket else planned[1].ticket, link_type="RELATED",
                                  status="PROPOSED", evidence="an undeclared kind of write", username="system:demo-writer")
        return made

    return SimpleNamespace(plan=plan, apply=apply)


def prove(scratch, **bad):
    return wp.scratch_proof(scratch, DEMO, demo_entry(**bad), wp.BoundClient(bc, scratch), NOW, scratch_guard)


def failed_names(result):
    return {c["name"] for c in result["checks"] if not c["ok"]}


def test_a_good_writer_passes_every_check_and_the_measures_are_reported(scratch):
    r = prove(scratch)
    assert r["failures"] == [], r["failures"]
    assert {c["name"] for c in r["checks"]} >= {"the plan has work to prove", "kill switch OFF writes nothing", "the caps stop it", "what it reports equals what the database counted",
                                                "it writes only the kinds it declares", "a second run writes nothing (idempotent)"}
    assert r["metrics"]["total"] >= 2 and r["metrics"]["duplicates"] == 0 and r["metrics"]["max_per_ticket"] == 1


def test_a_writer_that_ignores_its_kill_switch_is_caught(scratch):
    assert "kill switch OFF writes nothing" in failed_names(prove(scratch, honours_switch=False))


def test_a_writer_that_ignores_its_caps_is_caught(scratch):
    r = prove(scratch, honours_caps=False)
    assert "the caps stop it" in failed_names(r)


def test_a_writer_that_is_not_idempotent_is_caught(scratch):
    r = prove(scratch, idempotent=False)
    assert "a second run writes nothing (idempotent)" in failed_names(r) or any("DUPLICATION" in f for f in r["failures"])


def test_a_writer_that_writes_again_on_every_run_is_caught_by_the_second_run_alone(scratch):
    r = prove(scratch, chatty=True)
    assert failed_names(r) == {"a second run writes nothing (idempotent)"}, r["failures"]            # distinct notes each time: no duplication, no cap breach, only the idempotency check sees it


def test_a_writer_that_concentrates_its_writes_on_one_ticket_is_caught(scratch):
    r = prove(scratch, concentrated=True)
    assert any("CONCENTRATION" in f for f in r["failures"]), r["failures"]


def test_a_writer_that_duplicates_a_write_is_caught_by_the_measures(scratch):
    r = prove(scratch, duplicated=True, idempotent=False)
    assert any("DUPLICATION" in f for f in r["failures"]), r["failures"]


def test_a_writer_that_writes_a_kind_it_did_not_declare_is_caught(scratch):
    r = prove(scratch, undeclared=True)
    assert "it writes only the kinds it declares" in failed_names(r), r["failures"]


def test_a_plan_with_nothing_to_do_proves_nothing(scratch):
    entry = SimpleNamespace(plan=lambda bc_, now: [], apply=lambda *a, **k: [])
    r = wp.scratch_proof(scratch, DEMO, entry, wp.BoundClient(bc, scratch), NOW, scratch_guard)
    assert "the plan has work to prove" in failed_names(r) and "fewer than two" in " ".join(r["failures"])


def test_the_proof_refuses_a_live_looking_database_before_anything_runs():
    class Info:
        host, port, dbname = "localhost", 5436, "gestion"

    class Conn:
        info = Info()

        def execute(self, *a, **k):
            raise AssertionError("nothing may run against a live-looking database")
    with pytest.raises(RuntimeError, match="refuses to run"):
        wp.scratch_proof(Conn(), DEMO, demo_entry(), None, NOW, lambda c: assert_scratch(c, {"FONDOS_BACKLOG_PG_DSN": "postgresql://u:p@127.0.0.1:5436/gestion"}, who=WHO))


def test_the_counts_are_per_identity_and_a_missing_table_counts_zero(scratch):
    scratch.execute("DROP TABLE IF EXISTS backlog.probe_event CASCADE")                    # a phase this database does not have
    before = wp.count_writes(scratch, "system:demo-writer")
    assert before["PROBE"] == 0 and before["LOG"] == 0
    bc.append_log("FND-0100", "system:demo-writer", "one", conn=scratch)
    bc.append_log("FND-0100", "agent:someone-else", "two", conn=scratch)
    assert wp.count_writes(scratch, "system:demo-writer")["LOG"] == 1


# ── the real writer (only when the CLI asks) ────────────────────────────────────

REAL = os.environ.get("FONDOS_PROOF_WRITER")


@pytest.mark.skipif(not REAL, reason="scripts/gov/backlog/writer_proof.py runs this one, with FONDOS_PROOF_WRITER and FONDOS_PROOF_SNAPSHOT")
def test_prove_the_real_writer(pg_conn):
    spec = bw.REGISTRY[REAL]
    scratch_guard(pg_conn)
    _base(pg_conn)
    import backlog_drift as bd
    for phase in bd.PHASES_IN_ORDER:
        mig.migrate(pg_conn, phase)
    snap = json.loads(Path(os.environ["FONDOS_PROOF_SNAPSHOT"]).read_text(encoding="utf-8"))
    rp.load_snapshot(pg_conn, snap)
    wp.load_extra_tables(pg_conn, snap.get("extra") or {})
    entry = wp.load_entry(spec)
    result = wp.scratch_proof(pg_conn, spec, entry, wp.BoundClient(bc, pg_conn), datetime.now(timezone.utc), scratch_guard)
    Path(os.environ["FONDOS_PROOF_RESULT"]).write_text(json.dumps(result, default=str), encoding="utf-8")
    assert result["failures"] == [], result["failures"]
