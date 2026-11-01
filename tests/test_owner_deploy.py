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
    assert od.read_state(f) == {"deployed_ref": None, "snoozed_until": {}, "ignored": [], "auto_failed": [], "revoke_failed": False}
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


def test_there_is_no_command_line_way_to_answer_the_window_by_itself():
    """The command line has no auto-approve switch and the only askers are the window and a console prompt. The one unattended path is the AUTO mode of shared/config.py (owner decision
    2026-11-01), tested below: an apply without a window happens only through `mode == "AUTO"` and its gates, never by an argument."""
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


# ---------------------------------------------------------------- Backlog v3.1: the AUTO mode (no window, automatic gates)

class AutoRunner(Runner):
    """Like Runner, and it knows the restore point (python -c ...) and the rollbacks (--down)."""

    def __call__(self, cmd, env, cwd):
        if cmd[1] == "-c":
            key = "restore"
        elif "--down" in cmd:
            key = "down:" + cmd[cmd.index("--phase") + 1]
        else:
            key = "drift" if cmd[1].endswith("backlog_drift.py") else cmd[cmd.index("--phase") + 1]
        self.calls.append((key, dict(env), list(cmd)))
        return self.rcs.get(key, 0), self.tails.get(key, f"{key} done")


def auto_apply(tmp_path, runner, pending=("relations", "autonomy"), vault=None):
    return od.apply_pending(HEAD, list(pending), vault=vault or FakeVault(), state_file=tmp_path / "s.json", run=runner, export=lambda ref, dest: None, python="py",
                            env_file=tmp_path / ".env", receipts_dir=tmp_path / "receipts", now=lambda: NOW, auto=True)


def keys(r):
    return [c[0] for c in r.calls]


def test_the_mode_is_auto_or_ask_and_anything_else_is_ask():
    assert [od.deploy_mode(v) for v in ("AUTO", "auto", " ASK ", "ASK", "", None, "yes", "ON")] == ["AUTO", "AUTO", "ASK", "ASK", "ASK", "ASK", "ASK", "ASK"]
    from shared import config
    assert od.deploy_mode(config.BACKLOG_AUTO_DEPLOY_MODE) in ("AUTO", "ASK")


def test_an_auto_apply_creates_a_restore_point_first_then_phases_in_order_then_verifies(tmp_path):
    r = AutoRunner()
    out = auto_apply(tmp_path, r)
    assert out.ok and out.auto and out.applied == ["relations", "autonomy"] and out.rolled_back == [] and not out.fallback
    assert keys(r) == ["restore", "relations", "autonomy", "drift"]
    restore = r.calls[0]
    assert restore[1][od.OWNER_VAR] == SECRET and restore[2][2] == od.RESTORE_POINT_CODE and restore[2][3].startswith("backlog_deploy_bbbbbbbbbb_20261101T120000Z")
    assert SECRET not in " ".join(restore[2]) and len(restore[2][3]) <= 63                                       # the string never rides on a command line; a restore point name is 63 bytes at most
    assert od.OWNER_VAR not in r.calls[-1][1] and od.read_state(tmp_path / "s.json")["deployed_ref"] == HEAD


def test_without_a_restore_point_nothing_is_applied_and_a_person_is_asked(tmp_path):
    r = AutoRunner(rcs={"restore": 1}, tails={"restore": f"permission denied for function pg_create_restore_point ({SECRET})"})
    out = auto_apply(tmp_path, r)
    assert not out.ok and out.stage == "restore_point" and out.fallback and out.applied == [] and SECRET not in out.detail
    assert keys(r) == ["restore"] and od.read_state(tmp_path / "s.json")["deployed_ref"] is None


def test_a_missing_vault_string_in_auto_mode_asks_a_person_instead_of_failing_silently(tmp_path):
    out = auto_apply(tmp_path, AutoRunner(), vault=FakeVault(value=None))
    assert not out.ok and out.stage == "vault" and out.fallback and "owner_vault.py set" in out.detail


def test_a_failing_phase_rolls_back_what_this_run_applied_newest_first(tmp_path):
    r = AutoRunner(rcs={"attest": 1})
    out = auto_apply(tmp_path, r, pending=["relations", "autonomy", "attest"])
    assert not out.ok and out.stage == "attest" and out.applied == ["relations", "autonomy"] and out.rolled_back == ["autonomy", "relations"] and not out.fallback
    assert keys(r) == ["restore", "relations", "autonomy", "attest", "down:autonomy", "down:relations"]
    assert all("--apply" in c[2] for c in r.calls if c[0].startswith("down:")) and od.read_state(tmp_path / "s.json")["deployed_ref"] is None


def test_drift_after_the_apply_rolls_it_back_but_an_unreadable_drift_check_does_not(tmp_path):
    r = AutoRunner(rcs={"drift": 1})
    out = auto_apply(tmp_path, r)
    assert not out.ok and out.stage == "drift" and out.rolled_back == ["autonomy", "relations"] and keys(r)[-2:] == ["down:autonomy", "down:relations"]
    r2 = AutoRunner(rcs={"drift": 2})
    out2 = auto_apply(tmp_path, r2)
    assert not out2.ok and out2.stage == "drift" and out2.rolled_back == [] and not any(k.startswith("down:") for k in keys(r2))      # cannot verify: not judged wrong, not undone


def test_a_rollback_that_fails_stops_the_rollback_and_says_what_was_actually_undone(tmp_path):
    r = AutoRunner(rcs={"drift": 1, "down:autonomy": 1})
    out = auto_apply(tmp_path, r)
    assert out.rolled_back == [] and keys(r)[-1] == "down:autonomy"                                              # relations is left alone: the order matters and a person must look
    r2 = AutoRunner(rcs={"drift": 1, "down:relations": 1})
    assert auto_apply(tmp_path, r2).rolled_back == ["autonomy"]


def test_the_click_path_neither_creates_a_restore_point_nor_rolls_back(tmp_path):
    r = AutoRunner(rcs={"autonomy": 1})
    out = apply(tmp_path, r)                                                                                     # auto is False by default
    assert not out.ok and out.rolled_back == [] and not out.auto and keys(r) == ["relations", "autonomy"]


# -- the DEPLOY facts and the incident

LOG = ("feat(backlog): a\n\nRefs: FND-0355\nCo-Authored-By: t <t@t>\n\x1e" "feat(backlog): b\n\nCloses: FND-0358\nCo-Authored-By: t <t@t>\n\x1e" "chore: c\n\nno trailer\n\x1e")


class FactsVault:
    def __init__(self, facts="postgresql://backlog_facts@127.0.0.1:1/gestion"):
        self.facts = facts

    def get(self, name):
        return {od.FACTS_VAR: self.facts, od.OWNER_VAR: SECRET}.get(name)


def test_the_tickets_of_the_ddl_commits_come_from_their_trailers():
    assert od.ddl_tickets(LOG) == ["FND-0355", "FND-0358"] and od.ddl_tickets("") == []


def test_deploy_facts_are_recorded_per_ticket_with_the_scoped_string_only_during_the_calls(tmp_path):
    seen, env = [], {}

    def record(ref, ap, payload, ts):
        seen.append((ref, ap, payload, env.get("FONDOS_BACKLOG_PG_DSN_FACTS")))
        return ap != "FND-0358"                                                                                  # the second one was recorded before: not new

    line = od.record_facts(HEAD, OLD, ["autonomy"], vault=FactsVault(), ddl_log=lambda a, b: LOG, record=record, environ=env, now=lambda: NOW)
    assert [(r, a) for r, a, _p, _d in seen] == [("deploy-" + HEAD[:12], "FND-0355"), ("deploy-" + HEAD[:12], "FND-0358")] and all(d.endswith("/gestion") for *_x, d in seen)
    assert seen[0][2] == {"head": HEAD, "phases": ["autonomy"]} and "1 new for 2 ticket(s)" in line and "FAILED" not in line
    assert "FONDOS_BACKLOG_PG_DSN_FACTS" not in env                                                              # the string does not stay in the environment


def test_a_failed_fact_is_reported_and_never_raises_and_a_missing_string_says_what_to_do():
    def record(ref, ap, payload, ts):
        raise RuntimeError("permission denied")

    env = {"FONDOS_BACKLOG_PG_DSN_FACTS": "previous"}
    line = od.record_facts(HEAD, OLD, [], vault=FactsVault(), ddl_log=lambda a, b: LOG, record=record, environ=env)
    assert "FAILED for FND-0355 (RuntimeError), FND-0358 (RuntimeError)" in line and env["FONDOS_BACKLOG_PG_DSN_FACTS"] == "previous"
    none = od.record_facts(HEAD, OLD, [], vault=FactsVault(facts=None), ddl_log=lambda a, b: LOG, record=record, environ={})
    assert "NOT recorded" in none and "owner_vault.py set FONDOS_BACKLOG_PG_DSN_FACTS" in none
    assert "no previously deployed" in od.record_facts(HEAD, None, [], vault=FactsVault(), ddl_log=lambda a, b: LOG, record=record, environ={})
    assert "no ticket cites" in od.record_facts(HEAD, OLD, [], vault=FactsVault(), ddl_log=lambda a, b: "chore\n\x1e", record=record, environ={})
    assert "not recorded" in od.record_facts(HEAD, OLD, [], vault=FactsVault(), ddl_log=lambda a, b: (_ for _ in ()).throw(RuntimeError("git")), record=record, environ={})


def test_auto_deploy_records_the_facts_only_after_a_verified_success(tmp_path):
    calls = []
    ok = od.auto_deploy(HEAD, ["autonomy"], OLD, vault=FactsVault(), state_file=tmp_path / "s.json", ddl_log=lambda a, b: LOG, record=lambda *a: calls.append(a) or True,
                        apply=lambda *a, **kw: od.Outcome(True, "applied", ["autonomy"], "no drift", auto=True))
    assert ok.ok and "2 new" in ok.facts and len(calls) == 2
    calls.clear()
    bad = od.auto_deploy(HEAD, ["autonomy"], OLD, vault=FactsVault(), state_file=tmp_path / "s.json", ddl_log=lambda a, b: LOG, record=lambda *a: calls.append(a) or True,
                         apply=lambda *a, **kw: od.Outcome(False, "drift", ["autonomy"], "DRIFT", auto=True))
    assert not bad.ok and bad.facts == "" and calls == []                                                        # a deployment that did not verify declares nothing live


def test_the_incident_names_the_failure_and_never_raises():
    got = {}
    o = od.Outcome(False, "autonomy", ["relations"], "boom", rolled_back=["relations"], auto=True)
    ticket = od.report_failure(o, HEAD, lambda **kw: got.update(kw) or "FND-0400")
    assert ticket == "FND-0400" and "failed at autonomy" in got["title"] and "Rolled back: relations" in got["scenario_description"] and got["object_type"] == "JOB"
    assert od.report_failure(o, HEAD, lambda **kw: (_ for _ in ()).throw(RuntimeError("no db"))) is None


# -- the watcher in AUTO mode

class AutoWatch(Watch):
    def __init__(self, tmp_path, outcome, **kw):
        super().__init__(tmp_path, **kw)
        self.outcome, self.auto_calls, self.reports = outcome, [], []

    def once(self, now=NOW, mode="AUTO"):
        def apply_auto(h, pend, deployed):
            self.auto_calls.append((h, list(pend), deployed))
            return self.outcome

        return od.watch_once(state_file=self.state_file, fetch=lambda: None, head_fn=lambda: HEAD, changed=lambda a, b: self.changed, commits=lambda a, b: [], diff=lambda a, b: "+ddl line",
                             drift=lambda h: self.drift, ask=lambda t: self.asked.append(t) or self.answer, apply=lambda h, p: pytest.fail("the click path must not run"),
                             notify=self.notified.append, now=lambda: now, mode=mode, apply_auto=apply_auto, report=lambda **kw: self.reports.append(kw) or "FND-0400")


def test_in_auto_mode_a_pending_version_is_applied_with_no_window_and_notified(tmp_path):
    w = AutoWatch(tmp_path, od.Outcome(True, "applied", ["autonomy"], "no drift", auto=True), state={"deployed_ref": OLD})
    assert w.once() == "applied" and w.auto_calls == [(HEAD, ["autonomy"], OLD)] and w.asked == [] and len(w.notified) == 1 and w.reports == []


def test_in_ask_mode_the_auto_path_is_never_taken(tmp_path):
    w = AutoWatch(tmp_path, od.Outcome(True, "applied", ["autonomy"], "", auto=True), state={"deployed_ref": OLD}, answer="later")
    assert w.once(mode="ASK") == "later" and w.auto_calls == [] and len(w.asked) == 1


def test_a_failed_automatic_apply_opens_an_incident_remembers_the_failure_and_the_next_pass_asks_a_person(tmp_path):
    bad = od.Outcome(False, "autonomy", ["relations"], "boom", rolled_back=["relations"], auto=True)
    w = AutoWatch(tmp_path, bad, state={"deployed_ref": OLD}, answer="later")
    assert w.once() == "failed" and len(w.reports) == 1 and "failed at autonomy" in w.reports[0]["title"] and w.notified == [bad]
    assert HEAD in od.read_state(w.state_file)["auto_failed"]
    assert w.once() == "later" and len(w.auto_calls) == 1 and len(w.asked) == 1                                  # not retried by itself: the window asks, once


def test_without_a_restore_point_the_window_asks_and_says_why(tmp_path):
    w = AutoWatch(tmp_path, od.Outcome(False, "restore_point", [], "permission denied for pg_create_restore_point", fallback=True, auto=True), state={"deployed_ref": OLD}, answer="later")
    assert w.once() == "later" and len(w.asked) == 1 and "DID NOT RUN (restore_point)" in w.asked[0] and "permission denied" in w.asked[0]
    assert w.reports == [] and w.notified == [] and HEAD not in od.read_state(w.state_file).get("auto_failed", [])        # nothing was applied: not a failure, not an incident


def test_an_ignored_version_is_not_applied_automatically(tmp_path):
    ignored = AutoWatch(tmp_path, od.Outcome(True, "applied", [], "", auto=True), state={"deployed_ref": OLD, "ignored": [HEAD]})
    assert ignored.once() == "none" and ignored.auto_calls == []


def test_the_auto_branch_is_reached_only_through_the_mode_and_the_config_default_is_documented():
    src = (_ROOT / "scripts" / "ops" / "owner_deploy.py").read_text(encoding="utf-8")
    assert src.split("def watch_once")[1].split("# ── windows")[0].count('mode == "AUTO"') == 1 and "deploy_mode(getattr(cfg" in src       # the apply path of watch_once
    assert src.count('mode == "AUTO"') == 2 and 'revoke_enabled = mode == "AUTO" and bool(getattr(cfg, "BACKLOG_AUTO_REVOKE_ENABLED", False))' in src      # the other one is the A-2 switch, which is also off by default
    cfg = (_ROOT / "shared" / "config.py").read_text(encoding="utf-8")
    assert "BACKLOG_AUTO_DEPLOY_MODE: str =" in cfg and "Rollback of this decision" in cfg


# -- step A-2 (`revoke`) applied by the watcher when its gate holds (BACKLOG_AUTO_REVOKE_ENABLED, off by default)

PROGRESS_OK = {"live_non_system_since_last_v00": 50, "v00_total": 0, "last_v00_ts": None}


@pytest.mark.parametrize("enabled, progress, live, deployed, expected", [
    (True, PROGRESS_OK, True, HEAD, True), (True, {**PROGRESS_OK, "live_non_system_since_last_v00": 61}, True, HEAD, True),
    (False, PROGRESS_OK, True, HEAD, False),                                                         # the switch
    (True, {**PROGRESS_OK, "live_non_system_since_last_v00": 49}, True, HEAD, False),                # the gate: 49 of 50
    (True, None, True, HEAD, False), (True, {"live_non_system_since_last_v00": None}, True, HEAD, False),
    (True, PROGRESS_OK, False, HEAD, False), (True, PROGRESS_OK, None, HEAD, False),                  # already revoked, or unknown
    (True, PROGRESS_OK, True, OLD, False), (True, PROGRESS_OK, True, None, False)])                    # other DDL of master still pending: the drift check could not judge revoke alone
def test_revoke_is_due_only_with_the_switch_on_the_gate_met_the_privilege_live_and_nothing_else_pending(enabled, progress, live, deployed, expected):
    due, why = od.revoke_due(enabled, progress, live, deployed, HEAD)
    assert due is expected and why


class RevokeRig:
    def __init__(self, tmp_path, *, enabled=True, progress=PROGRESS_OK, live=True, state=None, outcome=None, raises=False):
        self.sf = tmp_path / "s.json"
        od.write_state(self.sf, state if state is not None else {"deployed_ref": HEAD})
        self.kw = dict(enabled=enabled)
        self.progress, self.live, self.raises = progress, live, raises
        self.outcome = outcome or od.Outcome(True, "applied", ["revoke"], "no drift", auto=True)
        self.applied, self.notified, self.reports = [], [], []

    def run(self):
        def progress():
            if self.raises:
                raise RuntimeError("v_shadow_stage does not exist")
            return self.progress

        return od.revoke_pass(state_file=self.sf, enabled=self.kw["enabled"], head_fn=lambda: HEAD, progress_fn=progress, privilege_fn=lambda: self.live,
                              apply=lambda h, p: self.applied.append((h, list(p))) or self.outcome, notify=self.notified.append, report=lambda **kw: self.reports.append(kw) or "FND-0400")


def test_a_due_revoke_is_applied_alone_with_the_automatic_gates_and_reported(tmp_path):
    r = RevokeRig(tmp_path)
    assert r.run() == "applied" and r.applied == [(HEAD, ["revoke"])] and len(r.notified) == 1 and r.reports == []


def test_nothing_happens_when_the_switch_is_off_the_gate_is_short_or_the_state_cannot_be_read(tmp_path):
    for rig in (RevokeRig(tmp_path / "a", enabled=False), RevokeRig(tmp_path / "b", progress={"live_non_system_since_last_v00": 20}), RevokeRig(tmp_path / "c", live=False),
                RevokeRig(tmp_path / "d", state={"deployed_ref": OLD})):
        (tmp_path / rig.sf.parent.name).mkdir(exist_ok=True)
        assert rig.run() == "none" and rig.applied == []
    bad = RevokeRig(tmp_path / "e", raises=True)
    assert bad.run().startswith("error:") and bad.applied == []


def test_a_failed_revoke_opens_an_incident_is_remembered_and_never_retried_by_itself(tmp_path):
    failed = od.Outcome(False, "drift", ["revoke"], "DRIFT", rolled_back=["revoke"], auto=True)
    r = RevokeRig(tmp_path, outcome=failed)
    assert r.run() == "failed" and len(r.reports) == 1 and "failed at drift" in r.reports[0]["title"] and r.notified == [failed]
    assert od.read_state(r.sf)["revoke_failed"] is True
    assert r.run() == "skipped" and len(r.applied) == 1                                              # the second pass does not touch it


def test_without_a_restore_point_revoke_is_not_applied_nor_marked_failed(tmp_path):
    r = RevokeRig(tmp_path, outcome=od.Outcome(False, "restore_point", [], "denied", fallback=True, auto=True))
    assert r.run() == "skipped" and od.read_state(r.sf)["revoke_failed"] is False and r.reports == [] and r.notified == []


def test_a_revoke_apply_does_not_remember_master_as_deployed(tmp_path):
    state_file = tmp_path / "s.json"
    od.write_state(state_file, {"deployed_ref": OLD})
    out = od.apply_pending(HEAD, ["revoke"], vault=FakeVault(), state_file=state_file, run=AutoRunner(), export=lambda ref, dest: None, python="py", env_file=tmp_path / ".env",
                           receipts_dir=tmp_path / "receipts", now=lambda: NOW, auto=True, remember=False)
    assert out.ok and od.read_state(state_file)["deployed_ref"] == OLD                                # the other DDL of master is not deployed because revoke was
