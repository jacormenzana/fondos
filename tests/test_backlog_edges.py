# tests/test_backlog_edges.py
# -*- coding: utf-8 -*-
"""Backlog v3.1 (FND-0355): the state machine as events. Every legal edge of backlog.fn_transition_legal has a declared trigger that is a RULE (the system moves it on evidence) or an EVENT (an actor
records it); none is an approval. The table in shared/backlog_autopilot.py (EDGES) must equal the DDL, what the rule engine actually produces and the table printed in NORMAS_BACKLOG.md. No database
(R-7)."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from shared import backlog_autopilot as ap

_ROOT = Path(__file__).resolve().parents[1]
SHA = "0123456789abcdef0123456789abcdef01234567"


def ddl_edges() -> set:
    """The legal moves of fn_transition_legal (30_transitions.sql) plus the reopening of a CLOSED ticket, which sp_transition handles apart from the legality table."""
    sql = (_ROOT / "db" / "pg_backlog" / "30_transitions.sql").read_text(encoding="utf-8")
    body = sql[sql.index("fn_transition_legal"):]
    out = set()
    for frm, tos in re.findall(r"WHEN '(\w+)'\s+THEN p_to IN \(([^)]*)\)", body):
        out |= {(frm, to) for to in re.findall(r"'(\w+)'", tos)}
    assert len(out) >= 20, out                                                      # the pattern found the table, not a fragment of it
    return out | {("CLOSED", "OPEN"), ("CLOSED", "TODO"), ("CLOSED", "IN_PROGRESS")}


def test_the_edge_table_is_the_ddls_legal_moves_plus_the_reopenings_and_nothing_else():
    assert set(ap.EDGES) == ddl_edges()


def test_every_edge_is_a_rule_or_an_event_with_a_trigger_and_none_is_an_approval():
    for edge, (kind, why) in ap.EDGES.items():
        assert kind in (ap.RULE, ap.EVENT), edge
        assert len(why) >= 10, edge
        assert not re.search(r"approv|sign[- ]?off|authori[sz]ed by|owner decides|confirmed by", why, re.I), (edge, why)


def test_the_rule_engine_produces_only_edges_declared_as_rules():
    t = {"FND-0001": {"status": "IN_PROGRESS", "release_version": None}}
    mv = ap.Merge(ref=SHA, pr=17, paths=("shared/x.py",), year=2026, month=11)
    made = [ap.decide_one("FND-0001", ap.Facts(tickets=t, merges={"FND-0001": [mv]})),                                                # MOVE
            ap.decide_one("FND-0001", ap.Facts(tickets={"FND-0001": {"status": "READY_FOR_DEPLOY", "release_version": None}}, merges={"FND-0001": [mv]}))]   # CLOSE
    closed_at = datetime(2026, 10, 30, tzinfo=timezone.utc)
    cite = ap.Citation(ref=SHA, ts=datetime(2026, 10, 31, tzinfo=timezone.utc), paths=("shared/x.py",))
    made.append(ap.decide_event("FND-0001", ap.Facts(tickets={"FND-0001": {"status": "CLOSED", "solution_type": "CODE_FIX"}}, citations={"FND-0001": [cite]}, closed_at={"FND-0001": closed_at})))   # REOPEN
    made.append(ap.decide_event("FND-0001", ap.Facts(tickets={"FND-0001": {"status": "BLOCKED"}, "FND-0002": {"status": "CLOSED"}}, blockers={"FND-0001": "FND-0002"})))                              # UNBLOCK
    made.append(ap.decide_event("FND-0001", ap.Facts(tickets={"FND-0001": {"status": "DEFERRED"}}, revisits={"FND-0001": date(2026, 11, 1)}, today=date(2026, 11, 1))))                              # UNDEFER
    assert [d.action for d in made] == [ap.MOVE, ap.CLOSE, ap.REOPEN, ap.UNBLOCK, ap.UNDEFER]
    for d in made:
        assert ap.EDGES[(d.from_status, d.to_status)][0] == ap.RULE, (d.action, d.from_status, d.to_status)


def test_every_rule_edge_is_made_by_the_engine_or_the_commit_hook():
    by_engine = {("IN_PROGRESS", "READY_FOR_DEPLOY"), ("READY_FOR_DEPLOY", "CLOSED"), ("CLOSED", "IN_PROGRESS"), ("BLOCKED", "IN_PROGRESS"), ("DEFERRED", "TODO")}
    by_hook = {("OPEN", "IN_PROGRESS"), ("TODO", "IN_PROGRESS")}                    # commit-msg hook: a `Refs:` trailer on an OPEN / TODO ticket (scripts/audit/commit_msg_gate.py)
    assert {e for e, (k, _w) in ap.EDGES.items() if k == ap.RULE} == by_engine | by_hook
    hook = (_ROOT / "scripts" / "audit" / "commit_msg_gate.py").read_text(encoding="utf-8")
    assert "IN_PROGRESS" in hook and "BACKLOG_AUTO_FORWARD_ENABLED" in hook


def test_normas_backlog_prints_the_same_table():
    text = (_ROOT / "doc" / "reglas" / "NORMAS_BACKLOG.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r"<!-- EDGES:BEGIN -->\n(.*?)\n<!-- EDGES:END -->", text, re.S)
    assert m, "the edge table markers are missing from NORMAS_BACKLOG.md"
    assert m.group(1) == ap.edge_table_markdown(), "regenerate the table: python -c \"from shared import backlog_autopilot as a; print(a.edge_table_markdown())\""


@pytest.mark.parametrize("current, decisions, contradicted, reopened, expected", [
    ("SHADOW", 20, 0, 0, "ON"), ("SHADOW", 19, 0, 0, None), ("SHADOW", 20, 1, 0, None), ("SHADOW", 25, 0, 1, None),         # promotion needs 20, none contradicted, no reopened closure
    ("ON", 0, 0, 1, None), ("ON", 0, 0, 2, "SHADOW"), ("ON", 99, 0, 3, "SHADOW"),                                           # demotion at two reopened closures
    ("OFF", 50, 0, 0, None), ("OFF", 0, 0, 5, None), ("ON", 50, 0, 0, None), ("SHADOW", 0, 0, 5, None)])                    # OFF is a person's: never touched
def test_the_mode_follows_the_track_record(current, decisions, contradicted, reopened, expected):
    change = ap.mode_change(current, decisions, contradicted, reopened)
    assert (change[0] if change else None) == expected
    if change:
        assert len(change[1]) >= 10                                                  # the database requires a reason of 10+ characters
