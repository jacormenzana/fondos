# tests/test_auto_publish.py
# -*- coding: utf-8 -*-
"""FND-0376: the automatic publication of master (scripts/audit/auto_publish.py, scripts/audit/post-commit.sh). The pure rules (what waits, what is retried, when to alert), and the git side against a REAL bare
repository standing in for `origin` (pushes, a delivery that waits for its receipt, a diverged history that is never forced, a failed push that is retried by itself and alerted). R-7: no database, no network."""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import auto_publish as ap  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
TRAILER = "\nCo-Authored-By: t <t@t>"


# ── pure rules ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("! [rejected] master -> master (non-fast-forward)", "diverged"),
    ("error: failed to push some refs\nhint: Updates were rejected because the remote contains work", "diverged"),
    ("remote: Permission to x denied\nfatal: Authentication failed for 'https://github.com/x'", "auth"),
    ("fatal: could not read Username for 'https://github.com': terminal prompts disabled", "auth"),
    ("fatal: unable to access 'https://github.com/x/': Could not resolve host: github.com", "network"),
    ("timed out", "network"),
    ("something nobody has seen", "unknown"),
    ("", "unknown"),
])
def test_push_errors_are_classified(text, expected):
    assert ap.classify_push_error(text) == expected


def test_only_network_and_unknown_failures_are_worth_retrying():
    assert ap.retriable("network") and ap.retriable("unknown")
    assert not ap.retriable("diverged") and not ap.retriable("auth")


def test_backoff_is_one_two_five_ten_then_fifteen_minutes():
    assert [ap.backoff_s(n) for n in (1, 2, 3, 4, 5, 6, 40)] == [60, 120, 300, 600, 900, 900, 900]
    assert ap.backoff_s(0) == 60


def commit_info(closes=False, minutes_ago=1, sha="a" * 40):
    return {"sha": sha, "closes": closes, "ts": T0 - timedelta(minutes=minutes_ago)}


def test_plan_publishes_plain_commits_at_once_and_makes_a_delivery_wait_for_its_receipt():
    assert ap.plan("master", [commit_info()], None)[0] == "push"
    assert ap.plan("master", [commit_info(closes=True)], None)[0] == "wait"
    assert ap.plan("master", [commit_info(closes=True)], False)[0] == "wait"
    assert ap.plan("master", [commit_info(closes=True)], True)[0] == "push"
    assert ap.plan("master", [commit_info(), commit_info(closes=True)], None)[0] == "wait"        # a delivery holds back everything pushed with it
    assert ap.plan("master", [commit_info(closes=True)], None, force=True)[0] == "push"            # the hotfix path
    assert ap.plan("master", [], None)[0] == "nothing"
    assert ap.plan("feature", [commit_info()], None)[0] == "nothing"
    assert ap.plan("", [commit_info()], None)[0] == "nothing"


def failing(first=T0, attempts=1, cls="network", alerted=False):
    return {"pending_sha": "b" * 40, "first_failure_ts": first.isoformat(), "attempts": attempts, "error_class": cls, "last_error": "boom", "alerted": alerted}


def test_a_retriable_failure_alerts_after_fifteen_minutes_and_a_person_failure_at_once():
    assert ap.failure_alert(failing(), T0 + timedelta(minutes=14)) is None
    assert "NOT published" in ap.failure_alert(failing(), T0 + timedelta(minutes=15))
    assert "a person must act" in ap.failure_alert(failing(cls="diverged"), T0)
    assert "a person must act" in ap.failure_alert(failing(cls="auth"), T0)
    assert ap.failure_alert(failing(alerted=True), T0 + timedelta(hours=3)) is None               # said once
    assert ap.failure_alert({}, T0) is None


def test_a_delivery_waiting_thirty_minutes_for_its_receipt_alerts_once_per_head():
    stuck = [commit_info(closes=True, minutes_ago=31)]
    assert "delivery is stuck" in ap.stuck_delivery_alert(stuck, T0, None)
    assert ap.stuck_delivery_alert(stuck, T0, stuck[-1]["sha"]) is None
    assert ap.stuck_delivery_alert([commit_info(closes=True, minutes_ago=29)], T0, None) is None
    assert ap.stuck_delivery_alert([commit_info(minutes_ago=300)], T0, None) is None              # a plain commit is never "stuck": it is published


def test_a_success_clears_the_failure_and_a_failure_counts_attempts_from_its_first_time():
    fail = {"status": "failed", "detail": "x", "error_class": "network"}
    s1 = ap.apply_result({}, fail, "h" * 40, T0)
    s2 = ap.apply_result(s1, fail, "h" * 40, T0 + timedelta(minutes=5))
    assert s1["attempts"] == 1 and s2["attempts"] == 2 and s2["first_failure_ts"] == s1["first_failure_ts"]
    assert ap.apply_result(s2, {"status": "pushed", "detail": ""}, "h" * 40, T0) == {}
    assert ap.apply_result({**s2, "stuck_alerted_for": "z"}, {"status": "waiting", "detail": ""}, "h" * 40, T0) == {"stuck_alerted_for": "z"}


# ── git, against a real bare origin ─────────────────────────────────────────────

def git(repo, *args, check=True):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def commit(repo, name, message="c"):
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(bare)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True, capture_output=True)
    git(work, "checkout", "-q", "-B", "master")
    commit(work, "README", "init")
    git(work, "push", "-q", "-u", "origin", "master")
    return bare, work


def remote_head(bare):
    return git(bare, "rev-parse", "master")


@needs_git
def test_a_plain_commit_is_published_at_once(world):
    bare, work = world
    sha = commit(work, "a.py")
    res = ap.try_publish(work)
    assert res["status"] == "pushed" and remote_head(bare) == sha
    assert ap.try_publish(work)["status"] == "nothing"


@needs_git
def test_a_delivery_waits_for_its_receipt_and_goes_when_it_has_one_or_on_the_hotfix_path(world):
    bare, work = world
    before = remote_head(bare)
    sha = commit(work, "a.py", "feat\n\nCloses: FND-0001" + TRAILER)
    res = ap.try_publish(work, verify=lambda r, s, p: (False, "no receipt"), receipts_path=work / "r.jsonl")
    assert res["status"] == "waiting" and remote_head(bare) == before and res["ahead"][0]["closes"] is True
    asked = []
    res = ap.try_publish(work, verify=lambda r, s, p: asked.append(s) or (True, "ok"), receipts_path=work / "r.jsonl")
    assert res["status"] == "pushed" and remote_head(bare) == sha and asked == [sha]                  # the receipt is asked for HEAD, the commit being pushed


@needs_git
def test_the_hotfix_path_publishes_a_delivery_without_a_receipt(world):
    bare, work = world
    sha = commit(work, "a.py", "feat\n\nCloses: FND-0001" + TRAILER)
    assert ap.try_publish(work, force=True)["status"] == "pushed" and remote_head(bare) == sha


@needs_git
def test_a_delivery_holds_back_a_plain_commit_pushed_with_it(world):
    bare, work = world
    before = remote_head(bare)
    commit(work, "a.py", "feat\n\nCloses: FND-0001" + TRAILER)
    commit(work, "b.py", "chore: plain")
    res = ap.try_publish(work, verify=lambda r, s, p: (False, "none"), receipts_path=work / "r.jsonl")
    assert res["status"] == "waiting" and remote_head(bare) == before


@needs_git
def test_a_diverged_history_is_never_forced_and_needs_a_person(world, tmp_path):
    bare, work = world
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True, capture_output=True)
    git(other, "checkout", "-q", "-B", "master")
    theirs = commit(other, "theirs.py")
    git(other, "push", "-q", "origin", "master")
    commit(work, "mine.py")
    res = ap.try_publish(work)
    assert res["status"] == "failed" and res["error_class"] == "diverged" and remote_head(bare) == theirs and not ap.retriable("diverged")


@needs_git
def test_nothing_is_done_off_master_or_when_head_is_already_on_origin(world):
    bare, work = world
    git(work, "checkout", "-q", "-b", "feature")
    commit(work, "f.py")
    assert ap.try_publish(work)["status"] == "nothing"
    git(work, "checkout", "-q", "master")
    assert ap.try_publish(work)["status"] == "nothing"


@needs_git
def test_an_unreachable_origin_is_a_failure_that_can_be_retried(world):
    bare, work = world
    commit(work, "a.py")
    git(work, "remote", "set-url", "origin", str(bare) + "_gone")
    res = ap.try_publish(work)
    assert res["status"] == "failed" and ap.retriable(res["error_class"])


@needs_git
def test_a_failed_push_is_retried_by_itself_and_never_waits_for_the_next_commit(world, tmp_path):
    bare, work = world
    sha = commit(work, "a.py")
    real_url = git(work, "remote", "get-url", "origin")
    git(work, "remote", "set-url", "origin", real_url + "_down")                                        # the network is down at commit time
    sleeps, alerts = [], []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            git(work, "remote", "set-url", "origin", real_url)                                           # ... and comes back during the second wait

    res = ap.publish_loop(work, sleep=sleep, state_path=tmp_path / "state.json", lock=tmp_path / "pub.lock", alert=alerts.append)
    assert res["status"] == "pushed" and remote_head(bare) == sha
    assert sleeps == [60, 120] and alerts == []                                                           # one minute, then two; far below the alert threshold
    assert ap.read_state(tmp_path / "state.json") == {} and not (tmp_path / "pub.lock").exists()


@needs_git
def test_a_failure_that_lasts_raises_one_alert_and_a_diverged_history_alerts_at_once_without_retrying(world, tmp_path):
    bare, work = world
    commit(work, "a.py")
    git(work, "remote", "set-url", "origin", git(work, "remote", "get-url", "origin") + "_down")
    clock = {"now": T0}
    alerts = []
    state = tmp_path / "state.json"
    kw = dict(state_path=state, now=lambda: clock["now"], alert=alerts.append)
    ap.attempt(work, **kw)
    assert alerts == []
    clock["now"] = T0 + timedelta(minutes=16)
    _res, text = ap.attempt(work, **kw)
    assert text and "NOT published" in text and len(alerts) == 1 and ap.read_state(state)["alerted"] is True
    clock["now"] = T0 + timedelta(minutes=40)
    ap.attempt(work, **kw)
    assert len(alerts) == 1                                                                               # said once

    calls = []
    res = ap.publish_loop(work, sleep=lambda s: calls.append(s), state_path=tmp_path / "s2.json", lock=tmp_path / "l2.lock",
                          try_fn=lambda root, force=False, **k: {"status": "failed", "detail": "rejected", "error_class": "diverged", "ahead": []}, alert=alerts.append)
    assert res["error_class"] == "diverged" and calls == [] and len(alerts) == 2                          # no retry, an alert at once


def test_the_loop_gives_up_after_a_day_and_one_loop_runs_at_a_time(tmp_path):
    ticks = iter(range(0, 10 ** 7, 20000))
    calls = []
    res = ap.publish_loop(_ROOT, sleep=lambda s: None, clock=lambda: next(ticks), state_path=tmp_path / "s.json", lock=tmp_path / "l.lock", alert=lambda t: None,
                          try_fn=lambda root, force=False, **k: calls.append(1) or {"status": "failed", "detail": "x", "error_class": "network", "ahead": []})
    assert res["status"] == "failed" and 2 <= len(calls) < 12                                              # about 24 h at 20000 s per turn, then it stops
    assert ap.acquire(tmp_path / "x.lock") and not ap.acquire(tmp_path / "x.lock")
    assert ap.publish_loop(_ROOT, lock=tmp_path / "x.lock", state_path=tmp_path / "s3.json")["detail"] == "another publisher is running"


@needs_git
def test_a_commit_that_arrives_while_the_loop_holds_the_lock_is_still_published(world, tmp_path):
    bare, work = world
    commit(work, "a.py")
    late = {}

    def try_fn(root, force=False, **k):
        res = ap.try_publish(root, **k)
        if not late:                                                                                      # the second commit's hook found the lock taken and returned
            late["sha"] = commit(work, "late.py")
        return res

    res = ap.publish_loop(work, state_path=tmp_path / "s.json", lock=tmp_path / "l.lock", try_fn=try_fn, alert=lambda t: None)
    assert res["status"] == "pushed" and remote_head(bare) == late["sha"]


@needs_git
def test_a_stuck_delivery_alerts_once_and_the_watcher_pass_is_silent_while_the_loop_is_alive(world, tmp_path):
    bare, work = world
    commit(work, "a.py", "feat\n\nCloses: FND-0001" + TRAILER)
    alerts = []
    kw = dict(state_path=tmp_path / "state.json", now=lambda: datetime.now(timezone.utc) + timedelta(minutes=45), alert=alerts.append,
              verify=lambda r, s, p: (False, "no receipt"), receipts_path=work / "r.jsonl")
    _r, text = ap.attempt(work, **kw)
    assert text and "delivery is stuck" in text
    _r, again = ap.attempt(work, **kw)
    assert again is None and len(alerts) == 1
    held = tmp_path / "held.lock"
    assert ap.acquire(held)
    assert ap.lock_alive(held) and ap.watcher_pass(work, lock=held, **{**kw, "state_path": tmp_path / "w.json"}) is None
    ap.release(held)
    assert not ap.lock_alive(held) and "delivery is stuck" in ap.watcher_pass(work, lock=held, **{**kw, "state_path": tmp_path / "w2.json"})


@needs_git
def test_status_says_what_is_unpublished_and_why(world, tmp_path):
    bare, work = world
    assert ap.status_lines(work, tmp_path / "none.json") == ["publication: nothing unpublished"]
    commit(work, "a.py", "feat\n\nCloses: FND-0001" + TRAILER)
    lines = ap.status_lines(work, tmp_path / "none.json")
    assert lines[0].startswith("UNPUBLISHED: 1 commit(s)") and "waiting for its test receipt" in lines[0]
    ap.write_state(failing(attempts=3), tmp_path / "s.json")
    assert any("publication failing since" in ln and "3 attempt(s)" in ln for ln in ap.status_lines(work, tmp_path / "s.json"))


# ── no forced push, ever ────────────────────────────────────────────────────────

def test_the_publisher_never_builds_a_forced_push():
    code = (_ROOT / "scripts" / "audit" / "auto_publish.py").read_text(encoding="utf-8")
    pushes = [ln for ln in code.splitlines() if "_git(" in ln and '"push"' in ln]
    assert len(pushes) == 1 and 'root, "push", remote, branch' in pushes[0]                               # one push, a plain refspec
    for forbidden in ('"--force"', '"-f"', "force-with-lease", '"+'):
        assert forbidden not in code, forbidden
