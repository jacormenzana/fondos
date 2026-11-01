# tests/test_backlog_autopilot.py
# -*- coding: utf-8 -*-
"""Backlog v3 W3 (FND-0358), the pure half: the rule table of shared/backlog_autopilot.py, the shared release gate of shared/backlog_release.py and the Closes: / Solution: trailers of
shared/backlog_gate.py. No database, no git, no network (R-7)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from shared import backlog_autopilot as ap
from shared import backlog_gate as gate
from shared import backlog_release as rel

SHA = "0123456789abcdef0123456789abcdef01234567"


def merge(paths=("shared/x.py",), pr=17, solution=None, ref=SHA, year=2026, month=11):
    return ap.Merge(ref=ref, pr=pr, paths=tuple(paths), year=year, month=month, solution=solution)


def facts(status="READY_FOR_DEPLOY", merges=None, **kw):
    t = {"FND-0001": {"status": status, "release_version": kw.pop("release_version", None)}}
    return ap.Facts(tickets=t, merges={"FND-0001": merges if merges is not None else [merge()]}, **kw)


def one(f):
    return ap.decide_one("FND-0001", f)


# ---------------------------------------------------------------- IN_PROGRESS -> READY_FOR_DEPLOY

def test_a_merge_that_delivers_an_in_progress_ticket_moves_it_to_ready_for_deploy_with_the_merge_as_evidence():
    d = one(facts("IN_PROGRESS"))
    assert (d.action, d.from_status, d.to_status, d.evidence_kind, d.evidence_ref) == (ap.MOVE, "IN_PROGRESS", "READY_FOR_DEPLOY", "MERGE", SHA)
    assert d.solution_type is None and d.reasons == ()


def test_an_open_delivery_gate_makes_the_move_a_wait_with_the_blockers():
    d = one(facts("IN_PROGRESS", rfd_blockers={"FND-0001": ["prerequisite FND-0009 is still open"]}))
    assert d.action == ap.WAIT and d.to_status == "READY_FOR_DEPLOY" and d.reasons == ("delivery gate: prerequisite FND-0009 is still open",)


# ---------------------------------------------------------------- READY_FOR_DEPLOY -> CLOSED

def test_a_clear_ready_for_deploy_ticket_closes_as_code_fix_with_the_pull_request_release_label():
    d = one(facts())
    assert (d.action, d.to_status, d.solution_type, d.release, d.reasons) == (ap.CLOSE, "CLOSED", "CODE_FIX", "R2026.11-PR17", ())


def test_a_merge_that_changes_db_waits_for_the_deploy_event():
    f = facts(merges=[merge(paths=("db/pg_backlog/97_autonomy.sql", "tests/t.py"))])
    d = one(f)
    assert d.action == ap.WAIT and "not live until its DDL is applied" in d.reasons[0]
    f.deploys.add("FND-0001")
    assert one(f).action == ap.CLOSE


def test_a_failing_closing_condition_blocks_the_closure_and_names_the_condition():
    d = one(facts(probes={"FND-0001": [(True, "pushed abc"), (False, "flag X is still off")]}))
    assert d.action == ap.WAIT and d.reasons == ("closing condition fails: flag X is still off",)
    assert one(facts(probes={"FND-0001": [(True, "a"), (True, "b")]})).action == ap.CLOSE


def test_the_audit_incident_gate_and_the_delivery_gate_each_block_a_closure():
    d = one(facts(incident={"FND-0001": ["an incident is still open"]}, delivery_blockers={"FND-0001": ["an unacknowledged review flag"]}))
    assert d.action == ap.WAIT and d.reasons == ("incident gate: an incident is still open", "delivery gate: an unacknowledged review flag")


def test_data_fix_needs_a_closing_condition_and_a_cycle_after_the_merge():
    d = one(facts(merges=[merge(solution="DATA_FIX")]))
    assert d.action == ap.WAIT and len(d.reasons) == 2 and d.solution_type == "DATA_FIX"
    ok = facts(merges=[merge(solution="DATA_FIX")], probes={"FND-0001": [(True, "share is 0")]}, cycles={"FND-0001"})
    d = one(ok)
    assert (d.action, d.solution_type) == (ap.CLOSE, "DATA_FIX")


@pytest.mark.parametrize("solution", ["WORKAROUND", "REJECTED", "DUPLICATED", "CODE-FIX", "DATAFIX"])
@pytest.mark.parametrize("status", ["IN_PROGRESS", "READY_FOR_DEPLOY"])
def test_only_code_fix_and_data_fix_are_automatic_so_a_typo_never_closes_anything(status, solution):
    d = one(facts(status, merges=[merge(solution=solution)]))
    assert d.action == ap.WAIT and "is not automatic" in d.reasons[0]


@pytest.mark.parametrize("status", ["OPEN", "TODO", "BLOCKED", "DEFERRED", "CLOSED"])
def test_a_ticket_in_any_other_status_is_never_moved_by_the_autopilot(status):
    assert one(facts(status)) is None


def test_no_merge_no_decision_and_an_unknown_ticket_no_decision():
    assert ap.decide(ap.Facts(tickets={"FND-0001": {"status": "IN_PROGRESS", "release_version": None}})) == []
    assert ap.decide_one("FND-9999", facts()) is None


def test_the_latest_merge_is_the_delivery_that_counts():
    old, new = merge(ref="a" * 40, pr=3, paths=("db/x.sql",)), merge(ref="b" * 40, pr=9)
    d = one(facts(merges=[old, new]))
    assert d.action == ap.CLOSE and d.evidence_ref == "b" * 40 and d.release == "R2026.11-PR9"


def test_a_release_label_already_on_the_ticket_is_kept_and_a_merge_that_is_no_pull_request_gets_a_sha_tag():
    assert one(facts(release_version="R2026.10-GOV-05")).release == "R2026.10-GOV-05"
    assert one(facts(merges=[merge(pr=None)])).release == "R2026.11-M0123456"
    d = one(facts(merges=[merge(month=13)]))
    assert d.action == ap.WAIT and "no valid release label" in d.reasons[0]


def test_decide_orders_by_ticket_and_returns_one_decision_per_ticket():
    f = ap.Facts(tickets={"FND-0002": {"status": "IN_PROGRESS", "release_version": None}, "FND-0001": {"status": "READY_FOR_DEPLOY", "release_version": None}},
                 merges={"FND-0002": [merge()], "FND-0001": [merge()]})
    assert [(d.ap, d.action) for d in ap.decide(f)] == [("FND-0001", ap.CLOSE), ("FND-0002", ap.MOVE)]


# ---------------------------------------------------------------- v3.1 events: REOPEN, UNBLOCK, UNDEFER

CLOSED_AT = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
BEFORE = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)


def cite(ts=AFTER, paths=("shared/x.py",), ref=SHA):
    return ap.Citation(ref=ref, ts=ts, paths=tuple(paths))


def closed(solution="CODE_FIX", citations=None):
    t = {"FND-0001": {"status": "CLOSED", "release_version": "R1", "solution_type": solution}}
    return ap.Facts(tickets=t, citations={"FND-0001": citations if citations is not None else [cite()]}, closed_at={"FND-0001": CLOSED_AT})


def event(f, a="FND-0001"):
    return ap.decide_event(a, f)


def test_a_code_merge_citing_a_ticket_after_its_closure_reopens_it_as_the_commit_evidence():
    d = event(closed())
    assert (d.action, d.from_status, d.to_status, d.evidence_kind, d.evidence_ref) == (ap.REOPEN, "CLOSED", "IN_PROGRESS", "COMMIT", SHA)


@pytest.mark.parametrize("citations", [[], [cite(ts=BEFORE)], [cite(paths=("doc/reglas/X.md",))], [cite(paths=("README.md", "out/report.txt"))]])
def test_a_citation_before_the_closure_or_one_that_touches_no_code_never_reopens(citations):
    assert event(closed(citations=citations)) is None


@pytest.mark.parametrize("solution", ["REJECTED", "DUPLICATED", None])
def test_only_a_delivery_closure_reopens(solution):
    assert event(closed(solution=solution)) is None


def test_the_newest_qualifying_citation_is_the_evidence_and_a_doc_only_one_is_skipped():
    f = closed(citations=[cite(ref="a" * 40), cite(ts=AFTER.replace(day=1, month=11), ref="b" * 40), cite(ts=AFTER.replace(day=2, month=11), paths=("doc/x.md",), ref="c" * 40)])
    assert event(f).evidence_ref == "b" * 40


def test_a_blocked_ticket_is_unblocked_when_its_blocker_is_closed_and_not_before():
    t = {"FND-0001": {"status": "BLOCKED"}, "FND-0002": {"status": "CLOSED"}}
    d = event(ap.Facts(tickets=t, blockers={"FND-0001": "FND-0002"}))
    assert (d.action, d.to_status, d.evidence_kind, d.evidence_ref) == (ap.UNBLOCK, "IN_PROGRESS", "BLOCKER", "FND-0002")
    t["FND-0002"] = {"status": "IN_PROGRESS"}
    assert event(ap.Facts(tickets=t, blockers={"FND-0001": "FND-0002"})) is None
    assert event(ap.Facts(tickets=t)) is None                                       # no structured blocker: a person's block, left alone


def test_a_deferred_ticket_returns_to_todo_on_its_revisit_date_and_not_before():
    t = {"FND-0001": {"status": "DEFERRED"}}
    d = event(ap.Facts(tickets=t, revisits={"FND-0001": date(2026, 11, 1)}, today=date(2026, 11, 1)))
    assert (d.action, d.to_status, d.evidence_kind, d.evidence_ref) == (ap.UNDEFER, "TODO", "REVISIT", "2026-11-01")
    assert event(ap.Facts(tickets=t, revisits={"FND-0001": date(2026, 11, 2)}, today=date(2026, 11, 1))) is None
    assert event(ap.Facts(tickets=t, today=date(2026, 11, 1))) is None


def test_decide_includes_the_events_and_a_delivery_wins_over_an_event_for_the_same_ticket():
    t = {"FND-0001": {"status": "CLOSED", "solution_type": "CODE_FIX"}, "FND-0002": {"status": "DEFERRED"}, "FND-0003": {"status": "IN_PROGRESS", "release_version": None}}
    f = ap.Facts(tickets=t, citations={"FND-0001": [cite()]}, closed_at={"FND-0001": CLOSED_AT}, revisits={"FND-0002": date(2026, 10, 1)}, today=date(2026, 11, 1),
                 merges={"FND-0003": [merge()]})
    assert [(d.ap, d.action) for d in ap.decide(f)] == [("FND-0001", ap.REOPEN), ("FND-0002", ap.UNDEFER), ("FND-0003", ap.MOVE)]


def test_an_event_note_is_recognised_on_the_next_run_even_when_the_evidence_is_a_date_or_a_ticket():
    d = event(ap.Facts(tickets={"FND-0001": {"status": "DEFERRED"}}, revisits={"FND-0001": date(2026, 11, 1)}, today=date(2026, 11, 1)))
    text = ap.shadow_note(d)
    assert ap.note_key(text) == ("UNDEFER", "TODO", "2026-11-01") and ap.already_noted(d, [text])


@pytest.mark.parametrize("path,expected", [("shared/x.py", True), ("tests/test_x.py", True), ("db/pg_backlog/98_events.sql", True), (".github/workflows/ci.yml", True),
                                           ("doc/reglas/X.md", False), ("README.md", False), ("out/r.txt", False), ("deployments/x.yaml", False)])
def test_what_counts_as_a_change_to_the_code(path, expected):
    assert ap.is_code_path(path) is expected


# ---------------------------------------------------------------- notes (SHADOW mode) and staleness

def test_a_shadow_note_is_one_line_names_the_evidence_and_is_recognised_on_the_next_run():
    d = one(facts(incident={"FND-0001": ["still open"]}))
    text = ap.shadow_note(d)
    assert "\n" not in text and text.startswith("[autopilot SHADOW] WAIT FND-0001") and "MERGE 0123456789ab" in text and "refused because: incident gate: still open" in text
    assert ap.already_noted(d, ["an unrelated note", text]) and not ap.already_noted(d, ["an unrelated note"])
    other = one(facts(merges=[merge(ref="f" * 40)], incident={"FND-0001": ["still open"]}))
    assert not ap.already_noted(other, [text])                                  # another merge is another decision
    assert "as CODE_FIX, release R2026.11-PR17" in ap.shadow_note(one(facts()), "ON")


def test_pull_request_numbers_come_from_the_merge_subject_only():
    assert ap.pr_number("Merge pull request #16 from jacormenzana/feat/x") == 16
    assert ap.pr_number("Merge branch 'feat/x'") is None and ap.pr_number("feat: Merge pull request #4") is None


def test_stale_in_progress_lists_the_most_idle_first():
    today = date(2026, 11, 1)
    got = ap.stale_in_progress({"FND-0001": date(2026, 10, 1), "FND-0002": date(2026, 10, 25), "FND-0003": date(2026, 10, 10), "FND-0004": date(2026, 10, 20)}, today)
    assert got == [("FND-0001", 31), ("FND-0003", 22)]                          # FND-0002 (7 days) and FND-0004 (12 days) are under the 14-day limit


# ---------------------------------------------------------------- the shared release gate

def test_release_labels_follow_the_norm_and_a_bad_tag_gives_none():
    assert rel.release_label(2026, 11, "pr17") == "R2026.11-PR17" and rel.release_label(2026, 1, "GOV-05") == "R2026.01-GOV-05"
    assert rel.release_label(2026, 13, "X") is None and rel.release_label(2026, 11, "-bad") is None and rel.release_label(2026, 11, "") is None


def test_the_incident_gate_fails_closed_when_the_store_is_missing_and_the_switch_is_on():
    assert rel.incident_reasons(["FND-0001", "FND-0002"], [], False, True).keys() == {"FND-0001", "FND-0002"}
    assert rel.incident_reasons(["FND-0001"], [], False, False) == {}            # no store and the loop switch OFF: nothing to enforce
    assert rel.incident_reasons(["FND-0001"], [], True, True) == {}              # a store with no incident on the ticket: clear


def test_a_release_closes_only_in_progress_or_ready_tickets_that_carry_no_release_yet():
    rows = {"FND-0001": ("IN_PROGRESS", None), "FND-0002": ("OPEN", None), "FND-0003": ("READY_FOR_DEPLOY", "R2026.10-X")}
    why = rel.closable_by_release(rows, ["FND-0001", "FND-0002", "FND-0003", "FND-0009"])
    assert len(why) == 3 and any("FND-0002 is OPEN" in w for w in why) and any("already carries release" in w for w in why) and any("does not exist" in w for w in why)


# ---------------------------------------------------------------- the Closes: and Solution: trailers

def test_a_closes_trailer_is_also_a_refs_so_the_forward_move_needs_no_change():
    m = gate.parse_message("feat: deliver it\n\nbody text\n\nCloses: FND-0357, FND-0358\nSolution: data_fix\nRefs: FND-0001\nCo-Authored-By: a <a@b.c>\n")
    assert m.closes == ["FND-0357", "FND-0358"] and m.refs == ["FND-0001", "FND-0357", "FND-0358"] and m.mentions == m.refs
    assert m.solution == "DATA_FIX" and m.malformed == []


def test_a_closes_trailer_outside_the_last_paragraph_is_ignored_like_refs():
    m = gate.parse_message("feat: x\n\nCloses: FND-0357\n\nCo-Authored-By: a <a@b.c>\n")
    assert m.closes == [] and m.refs == [] and m.solution is None


def test_a_malformed_closes_token_is_reported_so_the_hook_rejects_the_commit():
    m = gate.parse_message("feat: x\n\nCloses: fnd0357\nCo-Authored-By: a <a@b.c>\n")
    assert m.closes == [] and m.malformed == ["fnd0357"]
