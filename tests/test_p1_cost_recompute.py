"""
tests/test_p1_cost_recompute.py -- the unattended cost recompute orchestrator (owner directive 2026-11-04, D-0).

Everything runs against an injected fake runner and tmp paths: no sub-process, no DB, no live write.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "launch"))

import p1_cost_recompute as P  # noqa: E402

GOOD_LOG = ("[12:00:00] NATURE_FIRST LU0000000001 (1/2)\n  [CACHED] 200ms\nBloque/modo NATURE_FIRST procesado. Registros publicados: 2947\n"
            "[GLOBAL_NORM] {'rows_affected': 3763}\n[UNIVERSE-RECON] 2943 en universo actual, 811 huerfanos\n")
LIST_OUT = ("5 incident(s)\n"
            "  #7 OPEN      cost_attributes|ANNUAL_EQUALS_TOTAL_AT_1Y|X ap=FND-0278 fix=CODE isins=14 opened=r1 certified=-\n"
            "  #8 OPEN      cost_attributes|ANNUAL_LE_ACCUMULATED|X ap=FND-0279 fix=CODE isins=26 opened=r1 certified=-\n"
            "  #9 CERTIFIED cost_attributes|SOMETHING|X ap=FND-0283 fix=CODE isins=1 opened=r1 certified=r2\n"
            "  #10 OPEN     p2_metrics|SORTINO_BOUND|X ap=FND-0383 fix=- isins=11 opened=r1 certified=-\n"
            "  #11 CLOSED   cost_attributes|OLD|X ap=FND-0100 fix=CODE isins=2 opened=r0 certified=r0\n")


def test_the_log_parser_reads_the_publication_count_the_reconcile_and_the_errors():
    assert P.parse_recompute_log(GOOD_LOG) == {"published": 2947, "reconciled": True, "errors": 0}
    bad = GOOD_LOG + "Traceback (most recent call last):\n  File x\nboom\n[ERROR-123] ISIN fund not persisted\n"
    assert P.parse_recompute_log(bad)["errors"] == 2
    assert P.parse_recompute_log("") == {"published": None, "reconciled": False, "errors": 0}


def test_the_verdict_needs_rc_zero_a_publication_count_and_no_error_lines():
    ok = P.parse_recompute_log(GOOD_LOG)
    assert P.recompute_verdict(0, ok) == P.EXIT_OK
    assert P.recompute_verdict(1, ok) == P.EXIT_RECOMPUTE
    assert P.recompute_verdict(0, {"published": None, "reconciled": False, "errors": 0}) == P.EXIT_LOG        # it exited 0 but published nothing
    assert P.recompute_verdict(0, {"published": 0, "reconciled": True, "errors": 0}) == P.EXIT_LOG
    assert P.recompute_verdict(0, {"published": 5, "reconciled": True, "errors": 1}) == P.EXIT_LOG


def test_only_open_cost_incidents_are_candidates_for_certification():
    items = P.parse_incident_list(LIST_OUT)
    assert [x["id"] for x in items] == [7, 8, 9, 10, 11] and items[0]["ap"] == "FND-0278"
    assert [x["id"] for x in P.open_cost_incidents(items)] == [7, 8]


def test_the_watch_set_decides_whether_a_range_triggers_a_recompute():
    assert P.changed_watch_files(["README.md", "proyecto1/core/cost_table_parser.py", "proyecto1/core/fund_writer.py"]) == ["proyecto1/core/cost_table_parser.py"]
    assert P.changed_watch_files(["docs/a.md"]) == []
    for f in P.WATCH_FILES:
        assert (ROOT / f).exists(), f"stale watch entry {f}"


def test_the_final_exit_is_not_certified_when_any_incident_is_refused():
    assert P.final_exit({}) == P.EXIT_OK and P.final_exit({7: 0, 8: 0}) == P.EXIT_OK
    assert P.final_exit({7: 0, 8: 1}) == P.EXIT_NOT_CERTIFIED


def _fake(responses):
    calls = []

    def run(cmd, log_path=None, timeout=0):
        calls.append(cmd)
        key = next(k for k in responses if k in " ".join(map(str, cmd)))
        rc, out = responses[key]
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(out, encoding="utf-8")
        return rc, out
    return run, calls


def test_the_full_sequence_recomputes_audits_and_certifies_the_open_cost_incidents(tmp_path):
    run, calls = _fake({"run_block.py": (0, GOOD_LOG),
                        "run_statistical_audit.py": (0, "=== Statistical audit run_id=post_S_costs ===\nPersisted: 10 rows (run_id=post_S_costs)\n"),
                        "--list": (0, LIST_OUT),
                        "--certify 7": (0, "OK\nCERTIFIED"), "--certify 8": (1, "REFUSED\n  - 3 ISIN(s) still violate")})
    rec = P.sequence("py", "S", run=run, log_dir=tmp_path)
    assert rec["exit"] == P.EXIT_NOT_CERTIFIED and rec["audit_run_id"] == "post_S_costs"
    assert rec["steps"]["recompute"]["published"] == 2947 and (tmp_path / "cost_recompute_S.log").exists()
    assert rec["steps"]["certify"]["7"]["verdict"] == "CERTIFIED" and rec["steps"]["certify"]["8"]["verdict"] == "NOT CERTIFIABLE"
    order = [Path(str(c[3])).name for c in calls]            # [python, -X, utf8, script, ...]
    assert order[0] == "run_block.py" and order[1] == "run_statistical_audit.py" and order[2] == "audit_incident.py"
    assert "--recompute-costs" in calls[0] and "--persist" in calls[1] and "post_S_costs" in calls[1]
    assert all("--apply" in c for c in calls[3:])


def test_a_failed_recompute_stops_the_chain_before_the_audit(tmp_path):
    run, calls = _fake({"run_block.py": (1, "Traceback (most recent call last):\nboom")})
    rec = P.sequence("py", "S", run=run, log_dir=tmp_path)
    assert rec["exit"] == P.EXIT_RECOMPUTE and len(calls) == 1 and "audit" not in rec["steps"]


def test_an_rc_zero_recompute_that_published_nothing_stops_the_chain_too(tmp_path):
    run, calls = _fake({"run_block.py": (0, "nothing useful\n")})
    assert P.sequence("py", "S", run=run, log_dir=tmp_path)["exit"] == P.EXIT_LOG and len(calls) == 1


def test_an_audit_that_does_not_print_its_run_id_is_a_failed_audit(tmp_path):
    run, calls = _fake({"run_block.py": (0, GOOD_LOG), "run_statistical_audit.py": (0, "report without persistence")})
    assert P.sequence("py", "S", run=run, log_dir=tmp_path)["exit"] == P.EXIT_AUDIT and len(calls) == 2


def test_no_open_incident_means_a_clean_run(tmp_path):
    run, _ = _fake({"run_block.py": (0, GOOD_LOG), "run_statistical_audit.py": (0, "run_id=post_S_costs"), "--list": (0, "0 incident(s)\n")})
    rec = P.sequence("py", "S", run=run, log_dir=tmp_path)
    assert rec["exit"] == P.EXIT_OK and "certify" not in rec["steps"]


def test_the_lock_is_exclusive_and_a_stale_lock_is_taken_over(tmp_path):
    lock = tmp_path / "x.lock"
    assert P.acquire_lock(lock) is True and P.acquire_lock(lock) is False
    old = datetime.now() - timedelta(hours=P.LOCK_STALE_HOURS + 1)
    import os
    os.utime(lock, (old.timestamp(), old.timestamp()))
    assert P.acquire_lock(lock) is True


def test_a_commit_is_processed_once_and_the_receipt_is_appended(tmp_path):
    state = tmp_path / "state.json"
    assert P.already_processed(state, "abc") is False
    P.mark_processed(state, "abc")
    assert P.already_processed(state, "abc") is True and P.already_processed(state, "def") is False
    rp = tmp_path / "r.jsonl"
    P.write_receipt({"exit": 0, "stamp": "S"}, rp)
    P.write_receipt({"exit": 130, "stamp": "T"}, rp)
    assert [json.loads(x)["exit"] for x in rp.read_text(encoding="utf-8").splitlines()] == [0, 130]


def test_the_automatic_trigger_ships_off_and_the_script_has_a_usage_header():
    import shared.config as cfg
    assert cfg.COST_RECOMPUTE_AUTO_ENABLED is False
    assert "Exit codes" in P.__doc__ and "--if-changed" in P.__doc__
