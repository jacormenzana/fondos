# tests/test_owner_deploy.py
# -*- coding: utf-8 -*-
"""scripts/ops/owner_deploy.py: the pending-DDL decision, the apply-and-verify orchestration (with a fake runner: no database, no git, no window) and the watcher's branches. The secret used here is a
throwaway string (R-7)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import owner_deploy as od  # noqa: E402
from shared import owner_vault as ov  # noqa: E402

SECRET = "postgresql://fondos_owner:throwaway-secret@127.0.0.1:1/gestion"
HEAD, OLD = "b" * 40, "a" * 40
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
DRIFT_TEXT = ("DRIFT: master's DDL defines things the live backlog does not have (or has differently):\n"
              "  missing in live: routine fn_actor_bound(p_actor text)  (phase relations)\n"
              "  -> the owner re-applies it: python scripts/ops/migrate_backlog_v2.py --phase relations   (dry run first, then --apply; phases are idempotent)\n"
              "  -> the owner re-applies it: python scripts/ops/migrate_backlog_v2.py --phase autonomy   (dry run first)\n")


class FakeVault:
    def __init__(self, value=SECRET, error=None):
        self.value, self.error = value, error

    def get(self, name):
        assert name == od.OWNER_VAR
        if self.error:
            raise ov.VaultError(self.error)
        return self.value


# ---------------------------------------------------------------- pure decisions

def test_a_change_to_a_phases_ddl_file_pends_that_phase_and_the_shared_file_pends_both_in_order():
    assert od.pending_phases(["db/pg_backlog/97_autonomy.sql"]) == ["autonomy"]
    assert od.pending_phases(["db/pg_backlog/30_transitions.sql"]) == ["transitions", "relations"]          # relations carries the A-1 block
    assert od.pending_phases(["db/pg_backlog/97_autonomy.sql", "db/pg_backlog/40_relations_v2.sql"]) == ["relations", "autonomy"]    # application order, not alphabetical
    assert od.pending_phases(["scripts/ops/migrate_backlog_v2.py", "doc/x.md", "db/pg_backlog/down/97_autonomy_down.sql"]) == []        # the tool, docs and rollbacks are not DDL to apply


def test_only_the_phases_a_click_may_apply_are_offered_never_revoke_or_iam():
    offered = set(od.files_by_phase())
    assert "revoke" not in offered and "iam" not in offered and "autonomy" in offered
    assert all(files for files in od.files_by_phase().values())


def test_the_phases_to_apply_are_read_from_the_drift_tools_own_advice():
    assert od.phases_from_drift_output(DRIFT_TEXT) == ["relations", "autonomy"]
    assert od.phases_from_drift_output("no drift: the live backlog has everything") == [] and od.phases_from_drift_output("") == []
    assert od.phases_from_drift_output("--phase revoke --phase attest") == ["attest"]                         # an unknown-to-the-tool phase is never offered


def test_the_state_round_trips_and_a_missing_or_damaged_file_is_the_empty_state(tmp_path):
    f = tmp_path / "s.json"
    assert od.read_state(f) == {"deployed_ref": None, "snoozed_until": {}, "ignored": []}
    od.write_state(f, {"deployed_ref": OLD, "snoozed_until": {HEAD: NOW.isoformat()}, "ignored": [OLD]})
    assert od.read_state(f)["deployed_ref"] == OLD and od.read_state(f)["ignored"] == [OLD]
    f.write_text("{not json", encoding="utf-8")
    assert od.read_state(f)["deployed_ref"] is None


def test_the_prompt_decision_covers_nothing_pending_ignored_snoozed_and_ask():
    s = {"snoozed_until": {HEAD: (NOW + timedelta(hours=1)).isoformat()}, "ignored": []}
    assert od.prompt_action(s, HEAD, [], NOW) == "NONE"
    assert od.prompt_action(s, HEAD, ["autonomy"], NOW) == "WAIT"
    assert od.prompt_action(s, HEAD, ["autonomy"], NOW + timedelta(hours=2)) == "ASK"                          # the snooze expired
    assert od.prompt_action({"snoozed_until": {}, "ignored": [HEAD]}, HEAD, ["autonomy"], NOW) == "NONE"
    assert od.prompt_action(s, "c" * 40, ["autonomy"], NOW) == "ASK"                                          # another version is asked again


def test_the_window_shows_the_phases_the_commits_and_the_ddl_diff_and_caps_a_long_one():
    diff = "\n".join(f"+line {i}" for i in range(od.DIFF_MAX_LINES + 50))
    text = od.summary(HEAD, OLD, ["relations", "autonomy"], [("c" * 40, "feat: add the routine", "A. Person")], diff)
    assert "relations, autonomy" in text and "cccccccc  feat: add the routine  (A. Person)" in text and "+line 0" in text
    assert f"+line {od.DIFF_MAX_LINES - 1}" in text and f"+line {od.DIFF_MAX_LINES}" not in text and "50 more lines" in text
    first = od.summary(HEAD, None, ["relations"], [], "", DRIFT_TEXT)
    assert "no textual diff available" in first and "missing in live: routine fn_actor_bound" in first


def test_a_secret_is_scrubbed_from_any_text_kept():
    assert od.scrub(f"connection to {SECRET} failed", [SECRET, None, ""]) == "connection to *** failed"


def test_the_startup_launcher_is_generated_outside_the_repo_and_starts_the_watcher():
    text = od.startup_cmd_text(r"C:\py\pythonw.exe", r"C:\repo\scripts\ops\owner_deploy.py")
    assert 'start "" /min "C:\\py\\pythonw.exe" "C:\\repo\\scripts\\ops\\owner_deploy.py" watch' in text and text.endswith("\r\n")
    p = od.startup_cmd_path({"APPDATA": r"C:\Users\x\AppData\Roaming"})
    assert p.name == "fondos_owner_deploy.cmd" and "Startup" in p.parts


def test_install_and_remove_startup_touch_only_the_startup_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert od.main(["install-startup"]) == 0
    p = od.startup_cmd_path()
    assert p.is_file() and str(tmp_path) in str(p) and "owner_deploy.py" in p.read_text(encoding="utf-8")
    assert od.main(["remove-startup"]) == 0 and not p.exists()
    assert od.main(["remove-startup"]) == 0


def test_there_is_no_way_to_answer_the_window_by_itself():
    """Nothing is applied without a click: the command line has no auto-approve switch and the only askers are the window and a console prompt."""
    import re
    src = (_ROOT / "scripts" / "ops" / "owner_deploy.py").read_text(encoding="utf-8")
    declared = re.findall(r"add_argument\(\s*[\"'](--[\w-]+)", src) + re.findall(r"add_parser\(\s*[\"']([\w-]+)", src)
    assert sorted(declared) == sorted(["--interval-min", "--once", "--console", "check", "init", "watch", "install-startup", "remove-startup"]), declared       # no --yes / --answer / --apply / apply
    assert "ask_window" in src and "ask_console" in src
    assert "def apply_pending" in src and "ask(" in src.split("def watch_once")[1].split("apply(head, pending)")[0]       # the apply in the watcher is reached only after ask() returned


# ---------------------------------------------------------------- apply and verify

class Runner:
    """Records every command with the environment it got, and answers from a script of return codes."""

    def __init__(self, rcs=None, tails=None):
        self.calls, self.rcs, self.tails = [], rcs or {}, tails or {}

    def __call__(self, cmd, env, cwd):
        key = "drift" if cmd[1].endswith("backlog_drift.py") else cmd[cmd.index("--phase") + 1]
        self.calls.append((key, dict(env)))
        return self.rcs.get(key, 0), self.tails.get(key, f"{key} done")


def apply(tmp_path, runner, pending=("relations", "autonomy"), vault=None, export=None):
    return od.apply_pending(HEAD, list(pending), vault=vault or FakeVault(), state_file=tmp_path / "s.json", run=runner, export=export or (lambda ref, dest: None),
                            python="py", env_file=tmp_path / ".env", receipts_dir=tmp_path / "receipts", now=lambda: NOW)


def test_a_clean_apply_runs_each_phase_in_order_then_the_drift_tool_and_remembers_the_commit(tmp_path, monkeypatch):
    monkeypatch.setenv(od.OWNER_VAR, "inherited-owner-string")                                                   # must never reach the drift child
    r = Runner()
    out = apply(tmp_path, r)
    assert out.ok and out.stage == "applied" and out.applied == ["relations", "autonomy"]
    assert [k for k, _ in r.calls] == ["relations", "autonomy", "drift"]
    envs = dict(r.calls)
    assert envs["relations"][od.OWNER_VAR] == SECRET and envs["autonomy"][od.OWNER_VAR] == SECRET and od.OWNER_VAR not in envs["drift"]
    assert od.read_state(tmp_path / "s.json")["deployed_ref"] == HEAD
    receipt = next((tmp_path / "receipts").glob("*.json")).read_text(encoding="utf-8")
    assert json.loads(receipt)["ok"] is True and SECRET not in receipt


def test_a_failing_phase_stops_everything_and_remembers_nothing(tmp_path):
    r = Runner(rcs={"autonomy": 1}, tails={"autonomy": f"could not connect to {SECRET}"})
    out = apply(tmp_path, r, pending=["relations", "autonomy", "attest"])
    assert not out.ok and out.stage == "autonomy" and out.applied == ["relations"]
    assert [k for k, _ in r.calls] == ["relations", "autonomy"]                                                  # attest and the drift tool never ran
    assert SECRET not in out.detail and "***" in out.detail
    assert od.read_state(tmp_path / "s.json")["deployed_ref"] is None
    assert json.loads(next((tmp_path / "receipts").glob("*.json")).read_text(encoding="utf-8"))["ok"] is False


def test_drift_left_after_the_apply_is_a_failure_and_the_commit_is_not_remembered(tmp_path):
    out = apply(tmp_path, Runner(rcs={"drift": 1}, tails={"drift": "DRIFT: still missing a view"}))
    assert not out.ok and out.stage == "drift" and out.applied == ["relations", "autonomy"] and "still missing" in out.detail
    assert od.read_state(tmp_path / "s.json")["deployed_ref"] is None
    assert not apply(tmp_path, Runner(rcs={"drift": 2})).ok                                                       # could not verify is not a success either


def test_without_the_owner_string_in_the_vault_nothing_runs_and_the_message_says_what_to_do(tmp_path):
    for vault in (FakeVault(value=None), FakeVault(error="the vault file is damaged")):
        r = Runner()
        out = apply(tmp_path, r, vault=vault)
        assert not out.ok and out.stage == "vault" and r.calls == []
    assert "owner_vault.py set FONDOS_PG_DSN_OWNER" in apply(tmp_path, Runner(), vault=FakeVault(value=None)).detail


def test_an_export_failure_is_reported_and_nothing_runs(tmp_path):
    def boom(ref, dest):
        raise RuntimeError("git archive failed")
    r = Runner()
    out = apply(tmp_path, r, export=boom)
    assert not out.ok and out.stage == "export" and r.calls == []


# ---------------------------------------------------------------- the watcher

class Watch:
    def __init__(self, tmp_path, *, state=None, changed=("db/pg_backlog/97_autonomy.sql",), answer="apply", ok=True, drift=(0, ""), fetch_error=False):
        self.state_file = tmp_path / "s.json"
        if state is not None:
            od.write_state(self.state_file, state)
        self.asked, self.applied, self.notified = [], [], []
        self.changed, self.answer, self.ok, self.drift, self.fetch_error = list(changed), answer, ok, drift, fetch_error

    def once(self, now=NOW):
        def fetch():
            if self.fetch_error:
                raise RuntimeError("offline")

        def ask(text):
            self.asked.append(text)
            return self.answer

        def apply(h, pend):
            self.applied.append((h, list(pend)))
            return od.Outcome(self.ok, "applied" if self.ok else pend[0], list(pend) if self.ok else [], "")

        return od.watch_once(state_file=self.state_file, fetch=fetch, head_fn=lambda: HEAD, changed=lambda a, b: self.changed, commits=lambda a, b: [("c" * 40, "feat: ddl", "A")],
                             diff=lambda a, b: "+ddl line", drift=lambda h: self.drift, ask=ask, apply=apply, notify=self.notified.append, now=lambda: now)


def test_a_new_ddl_change_asks_and_a_click_on_apply_applies_the_phases_and_notifies(tmp_path):
    w = Watch(tmp_path, state={"deployed_ref": OLD})
    assert w.once() == "applied" and w.applied == [(HEAD, ["autonomy"])] and len(w.asked) == 1 and "autonomy" in w.asked[0] and "+ddl line" in w.asked[0] and len(w.notified) == 1


def test_a_failed_apply_is_reported_as_failed(tmp_path):
    w = Watch(tmp_path, state={"deployed_ref": OLD}, ok=False)
    assert w.once() == "failed" and not w.notified[0].ok


def test_remind_me_later_snoozes_for_four_hours_then_asks_again(tmp_path):
    w = Watch(tmp_path, state={"deployed_ref": OLD}, answer="later")
    assert w.once() == "later" and w.applied == []
    assert w.once(NOW + timedelta(hours=1)) == "wait" and len(w.asked) == 1                                       # snoozed: no window
    assert w.once(NOW + timedelta(hours=5)) == "later" and len(w.asked) == 2                                      # expired: asked again


def test_ignore_this_version_stops_asking_about_it_but_a_newer_master_asks_again(tmp_path):
    w = Watch(tmp_path, state={"deployed_ref": OLD}, answer="ignore")
    assert w.once() == "ignored" and w.once() == "none" and len(w.asked) == 1 and HEAD in od.read_state(w.state_file)["ignored"]


def test_nothing_pending_never_opens_a_window(tmp_path):
    w = Watch(tmp_path, state={"deployed_ref": OLD}, changed=[])
    assert w.once() == "none" and w.asked == []


def test_a_failing_fetch_gives_an_error_and_never_a_window(tmp_path):
    w = Watch(tmp_path, state={"deployed_ref": OLD}, fetch_error=True)
    assert w.once().startswith("error") and w.asked == []


def test_the_first_pass_uses_the_drift_tool_as_judge_remembers_a_matching_master_and_asks_about_drift(tmp_path):
    ok = Watch(tmp_path, drift=(0, "no drift"))
    assert ok.once() == "none" and od.read_state(ok.state_file)["deployed_ref"] == HEAD and ok.asked == []
    drifted = Watch(tmp_path / "other", drift=(1, DRIFT_TEXT)) if (tmp_path / "other").mkdir() is None else None
    assert drifted.once() == "applied" and drifted.applied == [(HEAD, ["relations", "autonomy"])]
    assert "missing in live: routine fn_actor_bound" in drifted.asked[0]


# ---------------------------------------------------------------- the lock

def test_one_watcher_at_a_time_and_a_dead_ones_lock_is_taken_over(tmp_path):
    lock = tmp_path / "w.lock"
    assert od.acquire_lock(lock, pid=100, alive=lambda p: True)
    assert od.acquire_lock(lock, pid=100, alive=lambda p: True)                                                   # the same process again
    assert not od.acquire_lock(lock, pid=200, alive=lambda p: p == 100)                                           # another live watcher holds it
    assert od.acquire_lock(lock, pid=200, alive=lambda p: False) and lock.read_text(encoding="utf-8") == "200"     # the holder died: taken over
