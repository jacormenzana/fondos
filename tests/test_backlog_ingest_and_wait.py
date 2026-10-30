# tests/test_backlog_ingest_and_wait.py
# -*- coding: utf-8 -*-
"""Pure tests of the post-deployment automation (FND-0354): the strict parser and the exactly-once plan of backlog_ingest_pending_logs.py, and the wait -> verify -> ingest
state machine of backlog_wait_for_deployment.py. No database, no clock, no sleeping."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_ingest_pending_logs as ing  # noqa: E402
import backlog_wait_for_deployment as wf  # noqa: E402

GOOD = """# PENDING_FND_LOGS

intro text that is not an entry

## FND-0354

### FND-0354 / 01 - First decision
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner review

Body of the first entry,
on two lines.

### FND-0354 / 02 - Second decision
actor: agent:claude-sonnet-5-5
date: 2026-10-30
source: commit abc

Body two.

## FND-0331

### FND-0331 / 01 - Other ticket
actor: system:release
date: 2026-10-29
source: x

Third body.
"""


# --- the parser --------------------------------------------------------------------------------------------------------------------------------

def test_the_good_file_is_parsed_into_entries_in_order():
    entries = ing.parse(GOOD)
    assert [(e.ticket, e.seq) for e in entries] == [("FND-0354", "01"), ("FND-0354", "02"), ("FND-0331", "01")]
    first = entries[0]
    assert first.title == "First decision" and first.actor == "agent:claude-sonnet-5-5" and first.date == "2026-10-29" and first.body == "Body of the first entry,\non two lines."
    assert first.marker == "[PENDING_FND_LOGS FND-0354/01]"
    assert first.text.startswith("[PENDING_FND_LOGS FND-0354/01] First decision (decision date 2026-10-29; source: owner review)\n\nBody of the first entry")
    assert ing.parse(GOOD.replace("\n", "\r\n")) == entries                                          # a Windows file reads the same


@pytest.mark.parametrize("broken, message", [
    (GOOD.replace("actor: agent:claude-sonnet-5-5\ndate: 2026-10-29", "date: 2026-10-29\nactor: agent:claude-sonnet-5-5", 1), "expected `actor"),     # fields out of order
    (GOOD.replace("actor: agent:claude-sonnet-5-5", "actor: claude", 1), "is not agent:<model> or system:<name>"),
    (GOOD.replace("actor: agent:claude-sonnet-5-5", "actor: human:someone", 1), "is not agent:<model> or system:<name>"),                                # a human appends with his own role
    (GOOD.replace("date: 2026-10-29", "date: 29/10/2026", 1), "is not YYYY-MM-DD"),
    (GOOD.replace("source: owner review\n\nBody of the first entry,\non two lines.\n", "source: owner review\n\n", 1), "empty or longer"),
    (GOOD.replace("### FND-0354 / 02 - Second decision", "### FND-0354 / 01 - Again", 1), "appears twice"),
    (GOOD.replace("### FND-0354 / 02 - Second decision", "### FND-354 / 2 - Bad heading", 1), "heading that does not follow"),
    (GOOD.replace("source: owner review\n\n", "source: owner review\ntext right after the fields\n", 1), "expected a blank line"),
    (GOOD.replace("source: owner review", "source: ", 1), "expected `source"),
    ("# only an intro\n", "no entry found"),
    (GOOD.replace("Third body.", "x" * (ing.MAX_BODY + 1)), "empty or longer"),
])
def test_anything_malformed_makes_the_whole_parse_fail(broken, message):
    with pytest.raises(ing.PendingLogsError, match=message):
        ing.parse(broken)


def test_the_real_pending_file_of_the_repository_parses_when_it_is_present():
    path = _ROOT / "PENDING_FND_LOGS.md"
    if not path.exists():
        pytest.skip("the file is removed after a complete ingestion")
    entries = ing.parse(path.read_text(encoding="utf-8"))
    assert len(entries) >= 1 and all(e.actor.startswith("agent:") for e in entries) and len({(e.ticket, e.seq) for e in entries}) == len(entries)


# --- the plan and the ingestion ------------------------------------------------------------------------------------------------------------------

def test_the_plan_skips_what_is_already_there_and_flags_a_missing_ticket():
    entries = ing.parse(GOOD)
    existing = {"FND-0354": ["unrelated note", entries[0].text]}                                    # the first entry is already in the log of its ticket
    planned = ing.plan(entries, {"FND-0354"}, existing)
    assert [s for _, s in planned] == ["ALREADY", "TO_APPEND", "MISSING_TICKET"]


def test_apply_appends_each_missing_entry_once_and_a_second_run_appends_nothing():
    entries = ing.parse(GOOD)
    store: dict = {}
    calls = []

    def append(ticket, actor, text):
        calls.append((ticket, actor, text[:36]))
        store.setdefault(ticket, []).append(text)
        return 100 + len(calls)

    first = ing.ingest(entries, ticket_ids={"FND-0354", "FND-0331"}, existing_comments=dict(store), append=append, apply=True)
    assert [s for _, s, _ in first] == ["APPENDED"] * 3 and [i for _, _, i in first] == [101, 102, 103]
    assert calls[0] == ("FND-0354", "agent:claude-sonnet-5-5", "[PENDING_FND_LOGS FND-0354/01] First")
    second = ing.ingest(entries, ticket_ids={"FND-0354", "FND-0331"}, existing_comments=dict(store), append=append, apply=True)
    assert [s for _, s, _ in second] == ["ALREADY"] * 3 and len(calls) == 3                          # nothing appended twice
    assert ing.render(second, "H", True).splitlines()[-1] == "RESULT: COMPLETE: nothing left to ingest"


def test_a_dry_run_writes_nothing_and_says_so():
    entries = ing.parse(GOOD)
    out = ing.ingest(entries, ticket_ids={"FND-0354", "FND-0331"}, existing_comments={}, append=lambda *a: pytest.fail("a dry run must not append"), apply=False)
    assert [s for _, s, _ in out] == ["TO_APPEND"] * 3
    assert ing.render(out, "H", False).splitlines()[-1] == "RESULT: INCOMPLETE: 3 not ingested (dry run: nothing was written)"


def test_a_failure_stops_the_run_records_what_was_done_and_the_next_run_resumes():
    entries = ing.parse(GOOD)
    store: dict = {}

    def flaky(ticket, actor, text):
        if text.startswith("[PENDING_FND_LOGS FND-0354/02]"):
            raise RuntimeError("the database went away")
        store.setdefault(ticket, []).append(text)
        return 7

    out = ing.ingest(entries, ticket_ids={"FND-0354", "FND-0331"}, existing_comments={}, append=flaky, apply=True)
    assert [s.split(":")[0] for _, s, _ in out] == ["APPENDED", "FAILED"] and len(out) == 2           # stopped at the failure: the third was not even tried
    resumed = ing.ingest(entries, ticket_ids={"FND-0354", "FND-0331"}, existing_comments=dict(store), append=lambda t, a, c: 8, apply=True)
    assert [s for _, s, _ in resumed] == ["ALREADY", "APPENDED", "APPENDED"]


def test_the_command_line_fails_cleanly_on_a_missing_or_malformed_file(tmp_path, capsys):
    assert ing.main(["--file", str(tmp_path / "nope.md")]) == ing.RC_USAGE
    bad = tmp_path / "bad.md"
    bad.write_text("### FND-0354 / 01 - x\nactor: claude\n", encoding="utf-8")
    assert ing.main(["--file", str(bad)]) == ing.RC_FAIL and "nothing was ingested" in capsys.readouterr().err


# --- wait -> verify -> ingest ----------------------------------------------------------------------------------------------------------------------

ALL = {"override_column": True, "actor_login_event": True, "sp_append_log": True, "iam_roles": True}


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def run_wait(flags_seq, *, timeout_s=3600.0, stable=2):
    t, it, logged = FakeTime(), iter(flags_seq), []

    def probe_fn():
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    ok = wf.wait(probe_fn, interval=15, timeout_s=timeout_s, stable=stable, sleep=t.sleep, clock=t.clock, log=logged.append)
    return ok, t, logged


def test_the_wait_needs_all_four_facts_on_two_consecutive_polls():
    half = dict(ALL, sp_append_log=False, iam_roles=False)                                           # step relations applied, step iam not yet: the case waiting for the column alone gets wrong
    ok, t, _ = run_wait([{k: False for k in ALL}, half, half, ALL, ALL])
    assert ok is True and t.slept == [15, 15, 15, 15]
    assert not wf.ready(half) and wf.ready(ALL) and not wf.ready({})


def test_a_flapping_read_resets_the_streak_and_a_connection_error_is_just_not_ready():
    ok, t, _ = run_wait([ALL, dict(ALL, iam_roles=False), ALL, RuntimeError("db restarting"), ALL, ALL])
    assert ok is True and len(t.slept) == 5                                                          # the single ready polls never counted; the last two did


def test_the_wait_gives_up_at_the_timeout():
    ok, t, logged = run_wait([{k: False for k in ALL}] * 100, timeout_s=60)
    assert ok is False and t.now >= 60 and any("poll 1" in line for line in logged)


def test_nothing_is_ingested_unless_the_verification_passed():
    events = []
    rc = wf.run(lambda: True, lambda: events.append("verify") or 0, lambda: events.append("ingest") or 0, attempts=3, retry_s=60, sleep=lambda s: None, log=lambda m: None)
    assert rc == wf.RC_OK and events == ["verify", "ingest"]
    events.clear()
    rc = wf.run(lambda: True, lambda: events.append("verify") or 1, lambda: events.append("ingest") or 0, attempts=3, retry_s=60, sleep=lambda s: events.append("sleep"), log=lambda m: None)
    assert rc == wf.RC_FAIL and events == ["verify", "sleep", "verify", "sleep", "verify"]            # retried, never ingested


def test_a_verification_that_passes_on_a_retry_ingests_and_a_failed_ingestion_is_a_failure():
    results = iter([1, 1, 0])
    events = []
    rc = wf.run(lambda: True, lambda: next(results), lambda: events.append("ingest") or 0, attempts=5, retry_s=1, sleep=lambda s: None, log=lambda m: None)
    assert rc == wf.RC_OK and events == ["ingest"]
    assert wf.run(lambda: True, lambda: 0, lambda: 1, attempts=1, retry_s=1, sleep=lambda s: None, log=lambda m: None) == wf.RC_FAIL


def test_a_timeout_while_waiting_verifies_and_ingests_nothing():
    events = []
    rc = wf.run(lambda: False, lambda: events.append("verify") or 0, lambda: events.append("ingest") or 0, attempts=3, retry_s=1, sleep=lambda s: None, log=lambda m: None)
    assert rc == wf.RC_TIMEOUT and events == []
