"""FND-0239: shared/cycle_telemetry.py -- the pure parts and the failure-isolation guarantees (no database; R-7).

The contract that matters most: a telemetry problem must never change a launcher's outcome. So: opt-in, never raises (Exception only),
bounded connect / statement timeouts, a JSONL fallback that is replayed in order and idempotently.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import cycle_telemetry as ct  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv(ct.ENV_CYCLE_ID, raising=False)
    monkeypatch.setenv(ct.ENV_FALLBACK, str(tmp_path / "fallback.jsonl"))
    monkeypatch.delenv(ct.ENV_DSN, raising=False)
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN", raising=False)
    return tmp_path / "fallback.jsonl"


# ---------------------------------------------------------------- pure functions
def test_baseline_is_the_median_of_the_last_five_and_needs_three_values():
    assert ct.median_baseline([]) is None and ct.median_baseline([10.0, 20.0]) is None            # warm-up
    assert ct.median_baseline([10.0, 20.0, 30.0]) == 20.0
    assert ct.median_baseline([1, 2, 3, 4, 5, 1000, 1000]) == 3.0                                  # only the 5 most recent count
    assert ct.median_baseline([None, float("nan"), 5.0, 6.0, 7.0]) == 6.0                          # non-finite values are ignored


def test_ratio_is_undefined_without_a_positive_baseline():
    assert ct.ratio_of(30.0, 10.0) == 3.0
    assert ct.ratio_of(30.0, None) is None and ct.ratio_of(30.0, 0.0) is None and ct.ratio_of(None, 5.0) is None


def test_judge_hard_ceiling_works_from_the_first_cycle_and_ratios_need_a_baseline():
    assert ct.judge(40000, None, 1.5, 3.0, hard_max=32400) == "HARD"                  # no baseline at all: the ceiling still fires
    assert ct.judge(100, None, 1.5, 3.0) == "OK"                                       # warm-up: no ratio judgement
    assert ct.judge(100, 100, 1.5, 3.0) == "OK"
    assert ct.judge(160, 100, 1.5, 3.0) == "WARN"
    assert ct.judge(300, 100, 1.5, 3.0) == "ALARM"
    assert ct.judge(5, 100, hard_min=10) == "HARD"
    assert ct.judge(None) == "OK" and ct.judge(float("nan"), 1, 1.5, 3.0) == "OK"


# ---------------------------------------------------------------- opt-in
@pytest.mark.parametrize("call", [
    lambda: ct.begin_cycle("L"), lambda: ct.end_cycle("OK", 0), lambda: ct.step_begin("P2_CALC"),
    lambda: ct.step_end("P2_CALC", 0), lambda: ct.record_metric("P2_CALC", "ols_funds", 1.0),
    lambda: ct.raise_flag("X", "WARN"), lambda: ct.record_audit_run("p2_metrics", "pre", "r1"),
])
def test_without_a_cycle_id_every_call_is_a_no_op_that_touches_nothing(call, _isolated):
    assert call() is False and not _isolated.exists()


# ---------------------------------------------------------------- failure isolation
def test_an_unreachable_database_never_raises_and_lands_in_the_fallback(monkeypatch, _isolated):
    monkeypatch.setenv(ct.ENV_CYCLE_ID, "20261007_120000")
    monkeypatch.setenv(ct.ENV_DSN, "postgresql://u:p@127.0.0.1:1/none")             # nothing listens: refused at once
    t0 = time.monotonic()
    assert ct.step_begin("P2_CALC", log_path="x.log") is False
    assert ct.record_metric("P2_CALC", "ols_funds", 0.0) is False
    assert time.monotonic() - t0 < 2 * ct.CONNECT_TIMEOUT_S + 1
    events = [json.loads(l) for l in _isolated.read_text(encoding="utf-8").splitlines()]
    assert [e["op"] for e in events] == ["step_begin", "metric"] and all(e["cycle_id"] == "20261007_120000" for e in events)


def test_connections_are_bounded_by_connect_and_statement_timeouts(monkeypatch):
    seen = {}

    def fake_connect(dsn, **kw):
        seen.update(kw)
        raise RuntimeError("down")

    monkeypatch.setattr(ct.psycopg, "connect", fake_connect)
    monkeypatch.setenv(ct.ENV_DSN, "postgresql://u:p@h/db")
    with pytest.raises(RuntimeError):
        ct._connect()
    assert seen["connect_timeout"] == ct.CONNECT_TIMEOUT_S == 3
    assert f"statement_timeout={ct.STATEMENT_TIMEOUT_MS}" in seen["options"] and ct.STATEMENT_TIMEOUT_MS == 3000


def test_ctrl_c_and_system_exit_are_not_swallowed(monkeypatch):
    """Exception only: a hung cycle must stay killable."""
    monkeypatch.setenv(ct.ENV_CYCLE_ID, "20261007_120000")
    for exc in (KeyboardInterrupt, SystemExit):
        monkeypatch.setattr(ct, "_apply", lambda conn, ev, e=exc: (_ for _ in ()).throw(e()))
        with pytest.raises(exc):
            ct.emit({"op": "metric", "cycle_id": "c"}, conn=object())


def test_an_unwritable_fallback_location_does_not_break_either(monkeypatch, tmp_path):
    """The fallback directory cannot be created because its parent is a regular FILE (portable, writes nothing anywhere)."""
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv(ct.ENV_FALLBACK, str(blocker / "sub" / "f.jsonl"))
    monkeypatch.setenv(ct.ENV_CYCLE_ID, "c")
    assert ct.step_begin("P2_CALC") is False and ct.pending_fallback_bytes() == 0


# ---------------------------------------------------------------- fallback replay
class _Conn:
    """Records the events _apply receives; fails on demand."""
    def __init__(self, fail_at=None):
        self.fail_at, self.applied = fail_at, []


def _fake_apply(monkeypatch, conn):
    def apply(c, ev):
        if conn.fail_at is not None and len(conn.applied) == conn.fail_at:
            raise RuntimeError("db went away")
        conn.applied.append(ev["op"])
    monkeypatch.setattr(ct, "_apply", apply)


def _events(n):
    return [{"op": f"op{i}", "cycle_id": "c", "ts": "2026-10-07T10:00:00+00:00"} for i in range(n)]


def test_replay_applies_in_order_and_rotates_the_file(monkeypatch, _isolated):
    _isolated.write_text("".join(json.dumps(e) + "\n" for e in _events(3)), encoding="utf-8")
    conn = _Conn()
    _fake_apply(monkeypatch, conn)
    assert ct.replay_fallback(conn) == 3 and conn.applied == ["op0", "op1", "op2"]
    assert not _isolated.exists() and _isolated.with_name(_isolated.name + ".done").exists()
    assert ct.pending_fallback_bytes() == 0 and ct.replay_fallback(conn) == 0                    # nothing left, still safe to call


def test_replay_that_fails_midway_keeps_only_the_unapplied_events(monkeypatch, _isolated):
    _isolated.write_text("".join(json.dumps(e) + "\n" for e in _events(4)), encoding="utf-8")
    conn = _Conn(fail_at=2)
    _fake_apply(monkeypatch, conn)
    assert ct.replay_fallback(conn) == 2
    assert [json.loads(l)["op"] for l in _isolated.read_text(encoding="utf-8").splitlines()] == ["op2", "op3"]
    assert ct.pending_fallback_bytes() > 0


# ---------------------------------------------------------------- the backlog link
def test_lookup_without_a_dsn_or_on_outage_is_lookup_failed_never_a_missing_ticket(monkeypatch):
    from shared import backlog_client as bc
    assert ct.lookup_ap_status("FND-0001") == "LOOKUP_FAILED"                       # no DSN
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", "postgresql://x")
    monkeypatch.setattr(bc.psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert ct.lookup_ap_status("FND-0001") == "LOOKUP_FAILED"                       # outage / timeout


def test_lookup_distinguishes_a_real_status_from_a_missing_ticket(monkeypatch):
    from shared import backlog_client as bc

    class _Conn:
        def __init__(self, row): self.row = row
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            row = self.row
            class R:
                def fetchone(s): return row
            return R()

    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", "postgresql://x")
    monkeypatch.setattr(bc.psycopg, "connect", lambda *a, **k: _Conn(("CLOSED",)))
    assert ct.lookup_ap_status("FND-1") == "CLOSED"
    monkeypatch.setattr(bc.psycopg, "connect", lambda *a, **k: _Conn(None))
    assert ct.lookup_ap_status("FND-404") == "NOT_FOUND"                            # the backlog answered: there is no such AP


def test_raise_flag_reads_the_ap_status_at_write_time(monkeypatch, _isolated):
    monkeypatch.setenv(ct.ENV_CYCLE_ID, "c")
    sent = []
    monkeypatch.setattr(ct, "emit", lambda ev, conn=None: sent.append(ev) or True)
    ct.raise_flag("BACKFILL_NO_WORK", "HIGH", ap_id="FND-0229", lookup=lambda a: "CLOSED")
    ct.raise_flag("OTHER", "WARN", lookup=lambda a: pytest.fail("no AP, no lookup"))
    ct.raise_flag("BOOM", "WARN", ap_id="FND-9", lookup=lambda a: (_ for _ in ()).throw(RuntimeError("x")))
    assert [(e["flag_code"], e["ap_id"], e["ap_status"]) for e in sent] == [
        ("BACKFILL_NO_WORK", "FND-0229", "CLOSED"), ("OTHER", None, None), ("BOOM", "FND-9", "LOOKUP_FAILED")]


def test_refresh_resolves_only_the_unverified_flags(monkeypatch):
    updates = []

    class C:
        def transaction(self):
            import contextlib
            return contextlib.nullcontext()

        def execute(self, sql, params=None):
            class R:
                def fetchall(s):
                    return [("c1", "A", "", "FND-1"), ("c1", "B", "", "FND-2")]
            if sql.startswith("UPDATE"):
                updates.append(params)
            return R()

    n = ct.refresh_unverified_flags(C(), lookup=lambda ap: "CLOSED" if ap == "FND-1" else "LOOKUP_FAILED")
    assert n == 1 and updates == [("CLOSED", "c1", "A", "")]


# ---------------------------------------------------------------- DDL not applied is not an outage
class _Undefined(Exception):
    sqlstate = "42P01"


def test_missing_telemetry_tables_are_a_configuration_absence_and_leave_no_fallback_debt(monkeypatch, _isolated):
    monkeypatch.setattr(ct, "_connect", lambda *a, **k: (_ for _ in ()).throw(_Undefined("relation control.cycle_run does not exist")))
    assert ct.emit({"op": "metric", "cycle_id": "c"}) is False and not _isolated.exists()
    monkeypatch.setattr(ct, "_connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("server closed the connection")))
    assert ct.emit({"op": "metric", "cycle_id": "c"}) is False and _isolated.exists()          # a real outage still spills


# ---------------------------------------------------------------- the command line the .bat files call
@pytest.fixture
def cli(monkeypatch, _isolated):
    sent = []
    monkeypatch.setattr(ct, "emit", lambda ev, conn=None: sent.append(ev) or True)
    monkeypatch.setattr(ct, "replay_fallback", lambda conn=None: 0)
    return sent


def test_cli_without_a_cycle_id_does_nothing_and_returns_zero(cli):
    for argv in (["begin", "--launcher", "L"], ["step-begin", "P2_CALC"], ["step-end", "P2_CALC", "0"], ["end", "--status", "OK", "--rc", "0"]):
        assert ct.main(argv) == 0
    assert cli == []


def test_cli_carries_the_step_start_between_two_processes(monkeypatch, cli):
    monkeypatch.setenv(ct.ENV_CYCLE_ID, "20261007_120000")
    assert ct.main(["begin", "--launcher", "P1_P2_P3", "--option", "force=1", "--resume-from", "3"]) == 0
    assert ct.main(["step-begin", "P2_CALC", "--log", "x.log"]) == 0
    assert ct.main(["step-end", "P2_CALC", "0", "--warn", "4", "--error", "0"]) == 0
    assert ct.main(["end", "--status", "OK", "--rc", "0", "--regime", "Expansion"]) == 0
    ops = {e["op"]: e for e in cli}
    assert list(ops) == ["cycle_begin", "step_begin", "step_end", "cycle_end"]
    assert ops["cycle_begin"]["options"] == {"force": "1"} and ops["cycle_begin"]["resume_from"] == 3
    assert ops["step_end"]["started_at"] == ops["step_begin"]["ts"] and ops["step_end"]["warn_count"] == 4 and ops["step_end"]["rc"] == 0
    assert ops["cycle_end"]["regime"] == "Expansion"


def test_cli_never_fails_the_launcher(monkeypatch, cli):
    monkeypatch.setenv(ct.ENV_CYCLE_ID, "c")
    assert ct.main(["no-such-command"]) == 0 and ct.main([]) == 0 and ct.main(["step-end", "X", "not-a-number"]) == 0
    monkeypatch.setattr(ct, "emit", lambda ev, conn=None: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ct.main(["step-begin", "P2_CALC"]) == 0


def test_cli_pending_reports_the_fallback_size(monkeypatch, _isolated, capsys):
    _isolated.write_text("x" * 12, encoding="utf-8")
    assert ct.main(["pending"]) == 0 and capsys.readouterr().out.strip() == "12"
