# tests/test_backlog_wait_staging_watch.py
# -*- coding: utf-8 -*-
"""The misfire detector of the deployment poller (owner order 2026-10-30): "DDL MISFIRE DETECTED ON STAGING PORT 5433". Pure logic: no database, no clock."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_wait_for_deployment as wf  # noqa: E402

NOTHING = {"override_column": False, "actor_login_event": False, "sp_append_log": False, "iam_roles": False}
EVERYTHING = {k: True for k in NOTHING}


def sig(facts=EVERYTHING, xmin=100, inserted=0):
    return dict(facts, routine_rows_xmin=xmin, binding_events_inserted=inserted)


def test_the_alert_text_is_the_one_the_owner_asked_for():
    assert wf.MISFIRE_ALERT == "DDL MISFIRE DETECTED ON STAGING PORT 5433." and wf.STAGING_TARGET == "127.0.0.1:5433/des"


def test_misfire_needs_production_not_ready_and_a_change_on_staging_that_carries_the_payload():
    start = sig(xmin=100)
    assert wf.misfire(NOTHING, sig(xmin=101), start) is True                                         # re-applied on staging while production is untouched
    assert wf.misfire(NOTHING, sig(xmin=100, inserted=3), sig(xmin=100, inserted=0)) is True          # new binding events on staging
    assert wf.misfire(NOTHING, start, start) is False                                                # staging merely holds the payload from the first attempt: not news
    assert wf.misfire(EVERYTHING, sig(xmin=101), start) is False                                     # production is deployed: nothing to alert
    assert wf.misfire(NOTHING, sig(xmin=101), {}) is False                                           # no baseline yet
    assert wf.misfire(NOTHING, sig(NOTHING, xmin=101), sig(NOTHING, xmin=100)) is False              # a change on staging that carries none of the payload is unrelated


def watch(signatures, alerts, logs):
    it = iter(signatures)

    def signature_fn():
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item
    return wf.StagingWatch(signature_fn, lambda now, start: alerts.append((now, start)), logs.append)


def test_without_a_staging_connection_the_watch_says_so_once_and_never_raises():
    logs, alerts = [], []
    w = watch([wf.StagingNotConfigured("x")] * 3, alerts, logs)
    for _ in range(3):
        w(NOTHING)
    assert len(logs) == 1 and "staging watch OFF" in logs[0] and wf.STAGING_ENV in logs[0] and "READ-ONLY" in logs[0] and alerts == []


def test_the_first_read_is_the_baseline_and_a_change_raises_one_critical_alert_per_state():
    logs, alerts = [], []
    start, changed, changed_again = sig(xmin=100), sig(xmin=105), sig(xmin=110)
    w = watch([start, start, changed, changed, changed_again], alerts, logs)
    for _ in range(5):
        w(NOTHING)
    critical = [line for line in logs if line.startswith("CRITICAL: " + wf.MISFIRE_ALERT)]
    assert len(critical) == 2 and len(alerts) == 2                                                    # once per distinct changed state, not once per poll
    assert alerts[0] == (changed, start) and "routine_rows_xmin" in critical[0]
    assert any("already carries the payload from an earlier attempt" in line for line in logs)         # the baseline line, not an alert


def test_no_alert_once_production_is_ready():
    logs, alerts = [], []
    w = watch([sig(xmin=1), sig(xmin=2)], alerts, logs)
    w(EVERYTHING)
    w(EVERYTHING)
    assert alerts == [] and not any("CRITICAL" in line for line in logs)


def test_an_unreadable_staging_is_logged_once_per_kind_and_a_failing_alert_record_does_not_hide_the_alert():
    logs = []
    w = wf.StagingWatch(lambda: (_ for _ in ()).throw(ConnectionError("down")), lambda *a: None, logs.append)
    w(NOTHING)
    w(NOTHING)
    assert len([line for line in logs if "cannot read" in line]) == 1
    logs2, it = [], iter([sig(xmin=1), sig(xmin=2)])

    def broken(now, start):
        raise RuntimeError("disk full")
    w2 = wf.StagingWatch(lambda: next(it), broken, logs2.append)
    w2(NOTHING)
    w2(NOTHING)
    assert any(line.startswith("CRITICAL: " + wf.MISFIRE_ALERT) for line in logs2) and any("alert record could not be committed" in line for line in logs2)


def test_the_poll_loop_ignores_whatever_the_watch_raises():
    seen, logs = [], []

    def boom(flags):
        seen.append(flags)
        raise RuntimeError("watch bug")
    now = {"t": 0.0}
    ok = wf.wait(lambda: EVERYTHING, interval=1, timeout_s=10, stable=2, sleep=lambda s: now.update(t=now["t"] + s), clock=lambda: now["t"], log=logs.append, on_poll=boom)
    assert ok is True and len(seen) == 2 and any("watch error (ignored)" in line for line in logs)       # the wait reached ready in spite of the watch
