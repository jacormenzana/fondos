# tests/test_backlog_sync.py
# -*- coding: utf-8 -*-
"""Phase C slice 1 (FND-0334), pure half: scripts/audit/backlog_sync.py parses `git log`, finds the citations and plans the events and the
auto-forward; scripts/audit/rfd_matrix_probe.py chooses its source of closing conditions and plans the seed. No database, no git."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "audit"))

import backlog_sync as bs  # noqa: E402
import rfd_matrix_probe as rfd  # noqa: E402

US, RS = "\x1f", "\x1e"


def rec(sha, ts, subject, refs=""):
    return f"{sha}{US}{ts}{US}{subject}{US}{refs}{RS}\n"


LOG = (rec("c3" * 20, "2026-10-30T12:00:00+01:00", "feat: FND-0002 more work", "FND-0002") +
       rec("c2" * 20, "2026-10-30T11:00:00+01:00", "fix: FND-0001 and FND-0003 (see fnd0193, FND_0194)", "FND-0001,FND-0004") +
       rec("c1" * 20, "2026-10-30T10:00:00+01:00", "docs: no ticket here") +
       rec("c0" * 20, "2026-10-30T09:00:00+01:00", "feat(x): FND-0002 first", "FND-0002, FND-0339"))


def test_parse_log_reads_every_field():
    cs = bs.parse_log(LOG)
    assert [c["sha"][:2] for c in cs] == ["c3", "c2", "c1", "c0"]
    c2 = cs[1]
    assert c2["refs"] == ["FND-0001", "FND-0004"] and c2["mentions"] == ["FND-0001", "FND-0003", "FND-0004"]
    assert c2["malformed"] == ["FND_0194", "fnd0193"] and cs[2]["mentions"] == [] and cs[3]["refs"] == ["FND-0002", "FND-0339"]
    assert cs[0]["author_ts"].isoformat() == "2026-10-30T12:00:00+01:00"


def test_a_trailer_id_is_refs_and_wins_over_the_subject():
    c = bs.parse_log(LOG)[1]
    assert dict(bs.citations(c)) == {"FND-0001": "REFS", "FND-0003": "SUBJECT", "FND-0004": "REFS"}


def test_plan_records_new_facts_and_forwards_once_from_the_oldest_refs_commit():
    statuses = {"FND-0001": "TODO", "FND-0002": "OPEN", "FND-0003": "OPEN", "FND-0004": "BLOCKED"}
    p = bs.plan(bs.parse_log(LOG), statuses, recorded=set())
    assert sorted((c["sha"][:2], ap, s) for c, ap, s in p["new"]) == [
        ("c0", "FND-0002", "REFS"), ("c2", "FND-0001", "REFS"), ("c2", "FND-0003", "SUBJECT"), ("c2", "FND-0004", "REFS"), ("c3", "FND-0002", "REFS")]
    assert p["unknown"] == {"FND-0339"}
    assert [(ap, c["sha"][:2]) for ap, c in p["forward"]] == [("FND-0002", "c0"), ("FND-0001", "c2")]   # never 0003 (subject) nor 0004 (BLOCKED)
    assert len(p["malformed"]) == 2


def test_plan_never_forwards_an_already_recorded_commit_or_a_moved_ticket():
    cs = bs.parse_log(LOG)
    recorded = {("c0" * 20, "FND-0002"), ("c3" * 20, "FND-0002")}
    p = bs.plan(cs, {"FND-0001": "IN_PROGRESS", "FND-0002": "TODO", "FND-0003": "OPEN", "FND-0004": "OPEN"}, recorded)
    assert p["already"] == 2
    assert [ap for ap, _ in p["forward"]] == ["FND-0004"]       # 0002 was moved back by a person: its commits are recorded, nothing re-forwards
    for st in ("BLOCKED", "DEFERRED", "IN_PROGRESS", "READY_FOR_DEPLOY", "CLOSED"):
        assert bs.plan(cs, {"FND-0001": st, "FND-0002": st, "FND-0003": st, "FND-0004": st}, set())["forward"] == []


def test_the_switch_off_records_but_moves_nothing():
    p = bs.plan(bs.parse_log(LOG), {"FND-0001": "OPEN", "FND-0002": "OPEN"}, set(), forward_enabled=False)
    assert p["new"] and p["forward"] == []


def test_rfd_probe_reads_the_store_when_it_exists():
    assert rfd.choose_spec(None)[0] is rfd.SPEC
    stored = {"FND-0001": [("pushed", "abc")]}
    assert rfd.choose_spec(stored) == (stored, "backlog.close_condition")


def test_rfd_seed_copies_only_ready_tickets_and_never_duplicates():
    spec = {"FND-0001": [("pushed", "abc"), ("flag", "X_ENABLED", True)], "FND-0002": [("manual", "owner action")]}
    add, skip = rfd.seed_rows(spec, {"FND-0001": "READY_FOR_DEPLOY", "FND-0002": "CLOSED"}, {"FND-0001": [("pushed", "abc")]})
    assert add == [("FND-0001", "flag", ["X_ENABLED", True])]
    assert skip == [("FND-0002", "status CLOSED")]


def test_store_probe_kinds_match_the_probe():
    """The kinds the table accepts are those of the sync phase plus the ones a later phase adds (`tests_pass`, 99_probes.sql widens the check); together they are exactly the kinds the probe runs."""
    import re
    ddl = (ROOT / "db" / "pg_backlog" / "70_sync.sql").read_text(encoding="utf-8")
    block = ddl.split("probe_kind       TEXT NOT NULL CHECK (probe_kind IN (", 1)[1].split("))", 1)[0]
    original = set(re.findall(r"'([a-z_]+)'", block))
    later = (ROOT / "db" / "pg_backlog" / "99_probes.sql").read_text(encoding="utf-8")
    widened = set(re.findall(r"'([a-z_]+)'", later.split("CHECK (probe_kind IN (", 1)[1].split("))", 1)[0]))
    assert widened == rfd.PROBE_KINDS and original < widened and widened - original == {"tests_pass"}


# ---------------------------------------------------------------- REVERT adjudications verified by the sync (FND-0334 slice 3)

def test_revert_checks_verify_the_sha_named_in_the_reason():
    sha_ok, sha_plain, sha_gone = "a" * 40, "b" * 40, "c" * 40
    reverts = [{"a": "FND-0001", "b": "FND-0002", "reason": f"the colliding commit was reverted by {sha_ok}"},
               {"a": "FND-0003", "b": "FND-0004", "reason": f"reverted by {sha_plain}, see the thread"},
               {"a": "FND-0005", "b": "FND-0006", "reason": f"reverted by {sha_gone}"},
               {"a": "FND-0007", "b": "FND-0008", "reason": "reverted, but no sha is written here"},
               {"a": "FND-0009", "b": "FND-0010", "reason": f"short sha {sha_ok[:12]} is not enough"}]
    messages = {sha_ok: 'Revert "feat: FND-0001 the colliding work"', sha_plain: "feat: FND-0003 a normal commit"}
    got = bs.revert_checks(reverts, messages)
    assert [(a, v) for a, _, _, v in got] == [("FND-0001", "OK"), ("FND-0003", "NOT_A_REVERT"), ("FND-0005", "NOT_FOUND"), ("FND-0007", "NO_SHA"), ("FND-0009", "NO_SHA")]
    assert bs.revert_checks([{"a": "A", "b": "B", "reason": f"x {sha_plain}"}], {sha_plain: "chore: This reverts commit 123"})[0][3] == "OK"       # git's own wording also counts
    lines = bs.revert_lines(got)
    assert len(lines) == 4 and all(l.startswith("[ATENCION] REVERT adjudication") for l in lines)
    assert "not a revert" in lines[0] and "not in the history" in lines[1] and "names no 40-character sha" in lines[2]
    assert bs.revert_lines(bs.revert_checks(reverts[:1], messages)) == []                                  # a verified one prints nothing
