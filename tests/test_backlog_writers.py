# tests/test_backlog_writers.py
# -*- coding: utf-8 -*-
"""FND-0370: the register of automatic writers and the rule that a new one writes only after a proof (shared/backlog_writers.py), its place in the client (a write to a real connection by a system:*
identity is judged first), the measures the dry run reports (duplications, volume, concentration per ticket) and the CLI's listing. The proof on a real scratch database is
tests/test_backlog_writer_proof_pg.py. R-7: no database, no network."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared import backlog_client as bc
from shared import backlog_writer_proof as wp
from shared import backlog_writers as bw

_ROOT = Path(__file__).resolve().parents[1]
W = bw.Write


# ── measures: duplications, volume, concentration ───────────────────────────────

def test_metrics_count_volume_tickets_concentration_and_duplicates():
    m = bw.metrics([W("FND-1", "LOG", "a"), W("FND-1", "LOG", "b"), W("FND-1", "LOG", "a"), W("FND-2", "LOG", "a"), W("FND-3", "LINK", "", "text"), W("FND-3", "LINK", "", "text")])
    assert m["total"] == 6 and m["tickets"] == 3 and m["max_per_ticket"] == 3 and m["busiest_ticket"] == "FND-1" and m["top_share"] == 0.5
    assert m["duplicates"] == 2 and any("FND-1 LOG a" in s for s in m["duplicate_samples"])           # same ticket, kind and key; and, with no key, the same text
    assert bw.metrics([]) == {"total": 0, "tickets": 0, "max_per_ticket": 0, "busiest_ticket": None, "top_share": 0.0, "duplicates": 0, "duplicate_samples": []}


def test_the_same_key_on_different_tickets_or_kinds_is_not_a_duplicate():
    assert bw.metrics([W("FND-1", "LOG", "k"), W("FND-2", "LOG", "k"), W("FND-1", "LINK", "k")])["duplicates"] == 0


CAPS = bw.Caps(max_writes_per_run=10, max_writes_per_ticket=2, max_top_ticket_share=0.4, min_writes_for_share=5, max_duplicates=0)


def test_breaches_name_volume_concentration_share_and_duplication_each_with_its_cap():
    assert bw.breaches(bw.metrics([W(f"FND-{i}", "LOG", "k") for i in range(10)]), CAPS) == []
    assert any(b.startswith("VOLUME") and "11" in b and "10" in b for b in bw.breaches(bw.metrics([W(f"FND-{i}", "LOG", "k") for i in range(11)]), CAPS))
    assert any(b.startswith("CONCENTRATION") and "FND-1" in b and "cap per ticket is 2" in b for b in bw.breaches(bw.metrics([W("FND-1", "LOG", str(i)) for i in range(3)]), CAPS))
    spread = [W("FND-1", "LOG", "a"), W("FND-1", "LOG", "b")] + [W(f"FND-{i}", "LOG", "x") for i in range(2, 6)]                          # 6 writes, the busiest has 2: 33 %
    assert bw.breaches(bw.metrics(spread), CAPS) == []
    heavy = [W("FND-1", "LOG", "a"), W("FND-1", "LOG", "b")] + [W(f"FND-{i}", "LOG", "x") for i in range(2, 4)]                           # 4 writes: below min_writes_for_share, the share is not judged
    assert bw.breaches(bw.metrics(heavy), CAPS) == []
    skewed = [W("FND-1", "LOG", "a"), W("FND-1", "LOG", "b")] + [W(f"FND-{i}", "LOG", "x") for i in range(2, 5)]                          # 5 writes, 40 %: at the cap, allowed
    assert bw.breaches(bw.metrics(skewed), CAPS) == []
    too = bw.Caps(max_writes_per_run=10, max_writes_per_ticket=9, max_top_ticket_share=0.3, min_writes_for_share=5, max_duplicates=0)
    assert any("takes" in b and "cap is 30%" in b for b in bw.breaches(bw.metrics(skewed), too))
    assert any(b.startswith("DUPLICATION") for b in bw.breaches(bw.metrics([W("FND-1", "LOG", "a"), W("FND-1", "LOG", "a")]), CAPS))


# ── the digest and the proof status ─────────────────────────────────────────────

def spec(tmp_path, **kw):
    (tmp_path / "w.py").write_text("x = 1\n", encoding="utf-8")
    base = dict(name="newbie", writes=("LOG",), switch="NEWBIE_ENABLED", files=("w.py",), caps=CAPS, entry="newbie_entry")
    base.update(kw)
    return bw.WriterSpec(**base)


def test_the_code_digest_follows_the_content_and_a_missing_file_is_not_an_error(tmp_path):
    s = spec(tmp_path)
    d1 = bw.code_digest(s, tmp_path)
    assert d1 == bw.code_digest(s, tmp_path)
    (tmp_path / "w.py").write_text("x = 2\n", encoding="utf-8")
    assert bw.code_digest(s, tmp_path) != d1
    assert len(bw.code_digest(spec(tmp_path, files=("w.py", "gone.py")), tmp_path)) == 64


def proof(writer="newbie", digest="d1", verdict="PASS", **kw):
    return {"writer": writer, "code_digest": digest, "verdict": verdict, "ts": "2026-11-03T12:00:00+00:00", **kw}


def test_a_new_writer_is_proven_only_by_a_passing_proof_for_exactly_its_current_code(tmp_path):
    s = spec(tmp_path)
    assert bw.proof_status(s, [], "d1")[0] == "PENDING" and "writer_proof.py --writer newbie" in bw.proof_status(s, [], "d1")[1]
    assert bw.proof_status(s, [proof()], "d1")[0] == "PROVEN"
    st, why = bw.proof_status(s, [proof(verdict="FAIL", failures=["scratch: kill switch OFF writes nothing: x"])], "d1")
    assert st == "PENDING" and "did not pass" in why and "kill switch" in why
    assert bw.proof_status(s, [proof()], "d2")[0] == "PENDING" and "code changed" in bw.proof_status(s, [proof()], "d2")[1]
    assert bw.proof_status(s, [proof(digest="d2"), proof(verdict="FAIL")], "d2")[0] == "PENDING"            # the NEWEST proof counts: a later failure withdraws an earlier pass
    assert bw.proof_status(s, [proof(writer="other")], "d1")[0] == "PENDING"                                  # another writer's proof is not mine
    assert bw.proof_status(bw.REGISTRY["autopilot"], [], "x")[0] == bw.GRANDFATHERED


# ── the gate ────────────────────────────────────────────────────────────────────

def test_only_system_identities_are_judged():
    for who in (None, "", "human:jacor", "agent:claude-sonnet-5-5"):
        bw.assert_may_write(who, proofs=[])                                                                   # out of scope: no error


def test_an_unregistered_system_writer_is_refused_with_the_way_forward():
    with pytest.raises(bw.WriterRefused) as e:
        bw.assert_may_write("system:brand-new", proofs=[])
    assert "not a registered automatic writer" in str(e.value) and "writer_proof.py" in str(e.value) and "FND-0370" in str(e.value)


def test_a_grandfathered_writer_may_write_without_a_proof():
    for name in bw.REGISTRY:
        bw.assert_may_write(f"system:{name}", proofs=[])


def test_a_new_registered_writer_writes_only_while_proven_for_its_current_code(tmp_path):
    s = spec(tmp_path)
    reg = {"newbie": s}
    with pytest.raises(bw.WriterRefused, match="may not write yet"):
        bw.assert_may_write("system:newbie", proofs=[], root=tmp_path, registry=reg)
    good = proof(digest=bw.code_digest(s, tmp_path))
    bw.assert_may_write("system:newbie", proofs=[good], root=tmp_path, registry=reg)
    (tmp_path / "w.py").write_text("x = 'changed'\n", encoding="utf-8")                                       # its code changes: the proof no longer vouches for it
    with pytest.raises(bw.WriterRefused, match="code changed"):
        bw.assert_may_write("system:newbie", proofs=[good], root=tmp_path, registry=reg)


# ── static checks of a new writer ───────────────────────────────────────────────

def test_a_new_writer_must_declare_caps_an_entry_files_and_a_kill_switch_that_ships_off(tmp_path):
    assert bw.static_problems(spec(tmp_path), SimpleNamespace(NEWBIE_ENABLED=False), tmp_path) == []
    assert bw.static_problems(spec(tmp_path), SimpleNamespace(NEWBIE_ENABLED="OFF"), tmp_path) == []
    bad = bw.static_problems(spec(tmp_path, caps=None, entry=None, files=("nope.py",), switch=None), None, tmp_path)
    assert {"no caps declared", "no plan/apply entry for the proof harness", "listed file nope.py does not exist", "no kill switch"} <= set(bad)
    assert any("does not ship OFF" in p for p in bw.static_problems(spec(tmp_path), SimpleNamespace(NEWBIE_ENABLED=True), tmp_path))
    assert any("is not in shared/config.py" in p for p in bw.static_problems(spec(tmp_path), SimpleNamespace(), tmp_path))
    assert bw.static_problems(bw.REGISTRY["autopilot"]) == []


# ── the register is complete and honest ─────────────────────────────────────────

def test_every_system_identity_in_the_code_is_registered_so_a_new_writer_cannot_land_unregistered():
    pat = re.compile(r"system:([a-z][a-z0-9_-]*)")
    found: dict = {}
    for top in ("shared", "scripts", "proyecto1", "proyecto2", "proyecto3"):
        for path in (_ROOT / top).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for m in pat.finditer(path.read_text(encoding="utf-8", errors="replace")):
                found.setdefault(m.group(1), set()).add(str(path.relative_to(_ROOT)).replace("\\", "/"))
    assert found, "the scan found no system identity at all: the scan itself is broken"
    missing = {n: sorted(f)[:3] for n, f in found.items() if n not in bw.REGISTRY}
    assert not missing, ("a system:<name> identity is used in the code but is not in shared/backlog_writers.py: register it (status REGISTERED: caps, kill switch OFF, files, entry) and prove it with "
                         "scripts/audit/writer_proof.py (FND-0370): " + repr(missing))


def test_the_register_is_well_formed_and_its_files_exist():
    for name, s in bw.REGISTRY.items():
        assert s.name == name and s.actor == f"system:{name}" and re.fullmatch(r"[a-z][a-z0-9-]*", name)
        assert set(s.writes) <= set(bw.KINDS) and s.writes and s.status in (bw.GRANDFATHERED, bw.REGISTERED)
        for rel in s.files:
            assert (_ROOT / rel).exists(), (name, rel)
        if s.status == bw.REGISTERED:
            assert bw.static_problems(s) == [], (name, bw.static_problems(s))                                   # a registered writer must already be fit to be proven
    assert wp.COUNT_TABLES.keys() == set(bw.KINDS)                                                              # every kind of write can be counted by the harness


# ── the gate inside the client ──────────────────────────────────────────────────

def test_append_log_by_an_unregistered_system_identity_is_refused_before_any_connection(monkeypatch):
    monkeypatch.setattr(bc, "_session", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a connection was opened")))
    with pytest.raises(bw.WriterRefused):
        bc.append_log("FND-0001", "system:brand-new", "a note")
    with pytest.raises(bw.WriterRefused):
        bc.record_link_events([dict(from_id="FND-0001", to_id="FND-0002", link_type="RELATED", status="PROPOSED", evidence="x", username="system:brand-new")])


def test_a_write_session_judges_the_identity_the_process_declares(monkeypatch):
    monkeypatch.setenv(bc._DSN_ENV_VAR, "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setattr(bc, "confirm_live_write", lambda *a, **k: None)
    reached = []

    class Boom(RuntimeError):
        pass

    def connect(*a, **k):
        reached.append(1)
        raise Boom("the connection was reached: the gate let the writer through")
    monkeypatch.setattr(bc.psycopg, "connect", connect)
    monkeypatch.setenv("FONDOS_ACTOR", "system:brand-new")
    with pytest.raises(bw.WriterRefused):
        with bc._session(None, write=True):
            pass
    assert reached == []
    monkeypatch.setenv("FONDOS_ACTOR", "system:autopilot")                                                     # a grandfathered writer passes the gate (and then fails at the fake connection)
    with pytest.raises(Boom):
        with bc._session(None, write=True):
            pass
    monkeypatch.setenv("FONDOS_ACTOR", "agent:claude-sonnet-5-5")                                              # an agent is not judged here either
    with pytest.raises(Boom):
        with bc._session(None, write=True):
            pass
    monkeypatch.delenv("FONDOS_ACTOR", raising=False)                                                          # no identity declared: the call that needs one raises its own error, the gate stays out
    with pytest.raises(Boom):
        with bc._session(None, write=True):
            pass


def test_a_call_with_an_injected_connection_is_not_judged(monkeypatch):
    class Conn:
        def execute(self, *a, **k):
            return SimpleNamespace(fetchone=lambda: (1,))
    assert bc.append_log("FND-0001", "system:brand-new", "a note in a hermetic transaction", conn=Conn()) == 1


# ── the proof records and the CLI ───────────────────────────────────────────────

def test_a_failed_proof_is_recorded_with_every_reason_and_a_pass_needs_all_three_parts(tmp_path):
    s = bw.WriterSpec(name="newbie", writes=("LOG",), switch="X", files=("a.py",), caps=CAPS, entry="e")
    from datetime import datetime, timezone
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    dry_ok = {"completed": True, "planned": 5, "metrics": {}, "breaches": []}
    scratch_ok = {"failures": [], "checks": []}
    assert wp.build_record(s, "d", dry_ok, scratch_ok, None, [], now)["verdict"] == "PASS"
    r = wp.build_record(s, "d", {"completed": False, "error": "boom"}, None, None, ["no kill switch"], now)
    assert r["verdict"] == "FAIL" and any("static: no kill switch" in f for f in r["failures"]) and any("dry run did not complete: boom" in f for f in r["failures"]) and any("scratch proof was not run" in f for f in r["failures"])
    r = wp.build_record(s, "d", {**dry_ok, "breaches": ["VOLUME: 300 writes"]}, {"failures": ["kill switch OFF writes nothing: x"]}, None, [], now)
    assert r["verdict"] == "FAIL" and "dry run: VOLUME: 300 writes" in r["failures"] and "scratch: kill switch OFF writes nothing: x" in r["failures"]
    path = wp.append_record(r, tmp_path / "audit" / "proofs.jsonl")
    wp.append_record(wp.build_record(s, "d", dry_ok, scratch_ok, None, [], now), path)
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [x["verdict"] for x in lines] == ["FAIL", "PASS"] and bw.read_proofs(path)[-1]["verdict"] == "PASS"


def test_the_dry_run_measures_a_plan_and_reports_a_plan_that_raises():
    ok = SimpleNamespace(plan=lambda bc_, now: [W("FND-1", "LOG", "a"), W("FND-1", "LOG", "a"), W("FND-1", "LOG", "b")])
    r = wp.dry_run(ok, None, CAPS, None)
    assert r["completed"] and r["planned"] == 3 and r["metrics"]["duplicates"] == 1 and any(b.startswith("DUPLICATION") for b in r["breaches"]) and any(b.startswith("CONCENTRATION") for b in r["breaches"])

    def boom(bc_, now):
        raise KeyError("a column that is not there")
    r = wp.dry_run(SimpleNamespace(plan=boom), None, CAPS, None)
    assert r["completed"] is False and "KeyError" in r["error"] and r["breaches"] == []


def test_the_bound_client_adds_the_connection_only_to_calls_that_take_one():
    seen = []
    client = SimpleNamespace(with_conn=lambda x, conn=None: seen.append(("with", x, conn)), without=lambda x: seen.append(("without", x)), CONSTANT=7)
    bound = wp.BoundClient(client, "THE-CONN")
    bound.with_conn(1)
    bound.without(2)
    assert seen == [("with", 1, "THE-CONN"), ("without", 2)] and bound.CONSTANT == 7


def test_the_listing_shows_every_writer_with_its_status():
    sys.path.insert(0, str(_ROOT / "scripts" / "audit"))
    import writer_proof as cli
    lines = cli.list_lines([])
    assert len(lines) == len(bw.REGISTRY) and any(ln.startswith("system:autopilot") and "GRANDFATHERED" in ln for ln in lines)
