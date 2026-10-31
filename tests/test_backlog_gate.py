# tests/test_backlog_gate.py
# -*- coding: utf-8 -*-
"""Phase C slice 2 (FND-0334), no database: shared/backlog_gate.py (message parsing and the verdict of a commit), scripts/audit/commit_msg_gate.py (the hook with a fake
backlog) and the installed hooks in a throwaway git repository (commit-msg, pre-rebase). The hook never blocks a commit except for a malformed ticket token."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import commit_msg_gate as hook  # noqa: E402
from shared import backlog_gate as g  # noqa: E402

NONCE = "ab" * 16
TREE = "1" * 40


# ---------------------------------------------------------------- the message

def test_parse_message_reads_subject_refs_and_gate_and_ignores_comments_and_the_body():
    m = g.parse_message("feat(x): FND-0001 and FND-0002 done\n\nbody mentions FND-0099 and fnd0098 but is prose\n\nRefs: FND-0003, FND-0004\n"
                        "Backlog-Gate: " + NONCE + "\n# a comment FND-0777\n")
    assert m.subject.startswith("feat(x)") and m.refs == ["FND-0003", "FND-0004"]
    assert m.mentions == ["FND-0001", "FND-0002", "FND-0003", "FND-0004"] and m.malformed == [] and m.gate == NONCE


def test_malformed_tokens_are_found_in_the_subject_and_the_refs_only():
    m = g.parse_message("fix: fnd0193 and FND_0194\n\nbody fnd0195\n\nRefs: FND-193\n")
    assert m.malformed == ["FND-193", "FND_0194", "fnd0193"]
    assert g.parse_message("docs: no ticket\n").mentions == [] and g.parse_message("").subject == ""


def test_a_one_line_message_has_no_trailer_block():
    assert g.parse_message("Refs: FND-0001").refs == []                 # a lone line is the subject, not a trailer


# ---------------------------------------------------------------- the verdict

def test_classify_every_case():
    ok = {NONCE: TREE}
    assert g.classify(NONCE, TREE, 1, ok) == (g.CHECKED, "nonce and tree match")
    assert g.classify(None, TREE, 1, ok)[0] == g.BYPASS and g.classify(None, TREE, 1, ok)[1] == g.REASON_MISSING
    assert g.classify("cd" * 16, TREE, 1, ok) == (g.BYPASS, g.REASON_UNKNOWN)
    assert g.classify(NONCE, "2" * 40, 1, ok) == (g.BYPASS, g.REASON_TREE)
    assert g.classify("offline", TREE, 1, ok)[0] == g.PENDING
    assert g.classify(None, TREE, 2, ok)[0] == g.EXEMPT                    # a merge commit


def commit(sha, ts, mentions, gate_value=None, tree=TREE, parents=1):
    return {"sha": sha, "author_ts": datetime.fromisoformat(ts), "mentions": mentions, "gate": gate_value, "tree": tree, "parents": parents}


def test_verdicts_judge_only_commits_after_the_epoch_that_cite_a_known_ticket():
    epoch = g.parse_epoch("2026-11-02T09:00:00+01:00")
    cs = [commit("a" * 40, "2026-11-01T10:00:00+01:00", ["FND-0001"]),                    # before the epoch
          commit("b" * 40, "2026-11-03T10:00:00+01:00", ["FND-0001", "FND-0002"]),         # bypass on two tickets
          commit("c" * 40, "2026-11-03T11:00:00+01:00", ["FND-0001"], NONCE),              # checked
          commit("d" * 40, "2026-11-03T12:00:00+01:00", ["FND-0999"]),                     # unknown ticket: not judged
          commit("e" * 40, "2026-11-03T13:00:00+01:00", [])]                               # cites nothing
    out = g.verdicts(cs, {NONCE: TREE}, epoch, {"FND-0001", "FND-0002"})
    assert [(c["sha"][0], ap, v) for c, ap, v, _ in out] == [("a", "FND-0001", g.EXEMPT), ("b", "FND-0001", g.BYPASS), ("b", "FND-0002", g.BYPASS), ("c", "FND-0001", g.CHECKED)]
    assert all(v == g.EXEMPT for _, _, v, _ in g.verdicts(cs, {}, None, {"FND-0001"}))     # not armed: nothing is judged


def test_epoch_needs_an_offset_and_empty_means_not_armed():
    assert g.parse_epoch("") is None and g.parse_epoch("  ") is None
    assert g.parse_epoch("2026-11-02T09:00:00+01:00").utcoffset() is not None
    with pytest.raises(ValueError):
        g.parse_epoch("2026-11-02T09:00:00")
    with pytest.raises(ValueError):
        g.parse_epoch("tomorrow")


def test_the_sync_reads_the_gate_trailer_tree_and_parents_from_git_log():
    sys.path.insert(0, str(ROOT / "scripts" / "audit"))
    import backlog_sync as bs
    us, rs = "\x1f", "\x1e"
    log = f"{'f' * 40}{us}2026-11-03T10:00:00+01:00{us}feat: FND-0001 x{us}{us}{TREE}{us}{'p' * 40} {'q' * 40}{us}{NONCE}{rs}\n"
    c = bs.parse_log(log)[0]
    assert (c["tree"], c["parents"], c["gate"]) == (TREE, 2, NONCE)
    bare = bs.parse_log(f"{'f' * 40}{us}2026-11-03T10:00:00+01:00{us}docs: x{us}{us}{us}{us}{rs}")[0]
    assert bare["gate"] is None and bare["parents"] == 0


# ---------------------------------------------------------------- the hook, with a fake backlog

class FakeStore:
    forward_enabled = True

    def __init__(self, statuses, fail=None, delay=0.0):
        self._statuses, self.fail, self.delay = statuses, fail, delay
        self.moved, self.checks = [], []

    def statuses(self, ids):
        time.sleep(self.delay)
        if self.fail:
            raise self.fail
        return {a: s for a, s in self._statuses.items() if a in ids}

    def forward(self, ap, comment):
        self.moved.append((ap, comment))

    def gate_check(self, tree, aps):
        self.checks.append((tree, list(aps)))
        return NONCE


class Err:
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s

    def flush(self):
        pass


def run_hook(tmp_path, message, store, **kw):
    f = tmp_path / "MSG"
    f.write_text(message, encoding="utf-8")
    err = Err()
    rc = hook.run(str(f), store_factory=lambda: store, tree_fn=lambda: TREE, err=err, **kw)
    return rc, f.read_text(encoding="utf-8"), err.text


def have_git():
    return shutil.which("git") is not None


def test_the_manual_fallback_replaces_and_appends_without_git_interpret_trailers(tmp_path, monkeypatch):
    def no_git(*a, **k):
        return subprocess.CompletedProcess(a, 1, "", "not a git command")
    monkeypatch.setattr(hook.subprocess, "run", no_git)
    f = tmp_path / "MSG"
    f.write_text("feat: FND-0001 x\n\nRefs: FND-0001\nBacklog-Gate: cdcd\n", encoding="utf-8")
    hook.write_trailer(f, "Backlog-Gate: offline")
    assert f.read_text(encoding="utf-8") == "feat: FND-0001 x\n\nRefs: FND-0001\nBacklog-Gate: offline\n"
    f.write_text("feat: FND-0001 x\n\nbody prose\n", encoding="utf-8")
    hook.write_trailer(f, "Backlog-Gate: offline")
    assert f.read_text(encoding="utf-8") == "feat: FND-0001 x\n\nbody prose\n\nBacklog-Gate: offline\n"


@pytest.mark.skipif(not have_git(), reason="git is needed for interpret-trailers")
def test_hook_moves_a_refs_ticket_forward_and_stamps_the_nonce(tmp_path):
    store = FakeStore({"FND-0001": "TODO", "FND-0002": "OPEN", "FND-0003": "BLOCKED", "FND-0004": "TODO"})
    rc, text, err = run_hook(tmp_path, "feat: FND-0004 mentioned\n\nRefs: FND-0001, FND-0002, FND-0003, FND-0777\n", store)
    assert rc == 0 and text.rstrip().endswith(f"Backlog-Gate: {NONCE}")
    assert [a for a, _ in store.moved] == ["FND-0001", "FND-0002"]          # not 0003 (BLOCKED), not 0004 (subject mention), not 0777 (unknown)
    assert store.checks == [(TREE, ["FND-0001", "FND-0002", "FND-0003", "FND-0004", "FND-0777"])]
    assert "FND-0777" in err and "not in the backlog" in err


@pytest.mark.skipif(not have_git(), reason="git is needed for interpret-trailers")
def test_hook_with_the_switch_off_moves_nothing_but_still_checks(tmp_path):
    store = FakeStore({"FND-0001": "TODO"})
    store.forward_enabled = False
    rc, text, _ = run_hook(tmp_path, "feat: x\n\nRefs: FND-0001\n", store)
    assert rc == 0 and store.moved == [] and NONCE in text


@pytest.mark.skipif(not have_git(), reason="git is needed for interpret-trailers")
def test_hook_replaces_an_old_trailer_as_after_an_amend(tmp_path):
    old = "cd" * 16
    rc, text, _ = run_hook(tmp_path, f"feat: FND-0001 x\n\nRefs: FND-0001\nBacklog-Gate: {old}\n", FakeStore({"FND-0001": "IN_PROGRESS"}))
    assert rc == 0 and old not in text and text.count("Backlog-Gate:") == 1 and NONCE in text


@pytest.mark.skipif(not have_git(), reason="git is needed for interpret-trailers")
@pytest.mark.parametrize("store", [FakeStore({}, fail=ConnectionRefusedError("down")), FakeStore({"FND-0001": "OPEN"}, delay=1.0)], ids=["unreachable", "too slow"])
def test_hook_fails_open_with_offline(tmp_path, store):
    rc, text, err = run_hook(tmp_path, "feat: FND-0001 x\n", store, budget=0.2)
    assert rc == 0 and text.rstrip().endswith("Backlog-Gate: offline") and ("offline" in err or "continuing" in err)
    assert store.moved == [] and store.checks == []


def test_hook_rejects_a_malformed_token_and_leaves_the_message_alone(tmp_path):
    store = FakeStore({"FND-0001": "TODO"})
    rc, text, err = run_hook(tmp_path, "fix: fnd0193 broke it\n", store)
    assert rc == hook.REJECTED_RC and "fnd0193" in err and text == "fix: fnd0193 broke it\n" and store.checks == []


def test_hook_leaves_a_commit_without_a_ticket_alone_and_never_calls_the_backlog(tmp_path):
    def boom():
        raise AssertionError("the backlog must not be touched")
    f = tmp_path / "MSG"
    f.write_text("docs: nothing to see\n", encoding="utf-8")
    assert hook.run(str(f), store_factory=boom, tree_fn=boom) == 0 and f.read_text(encoding="utf-8") == "docs: nothing to see\n"


def test_main_never_blocks_on_a_usage_error_or_an_internal_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", sys.stdin)            # main() detaches stdin and declares its own identity: both are restored afterwards
    monkeypatch.setenv("FONDOS_ACTOR", "agent:claude-opus-5-5")
    assert hook.main([]) == 0
    assert hook.main([str(tmp_path / "does-not-exist")]) == 0
    assert "internal error" in capsys.readouterr().err


# ---------------------------------------------------------------- the installed hooks, in a throwaway repository

BASH = shutil.which("bash")


@pytest.fixture
def repo(tmp_path):
    if not (BASH and have_git()):
        pytest.skip("bash and git are needed")
    r = tmp_path / "r"
    (r / "scripts" / "audit").mkdir(parents=True)
    (r / "shared").mkdir()
    shutil.copy(ROOT / "scripts" / "audit" / "commit_msg_gate.py", r / "scripts" / "audit")
    shutil.copy(ROOT / "shared" / "backlog_gate.py", r / "shared")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    env.pop("FONDOS_BACKLOG_PG_DSN", None)

    def git(*a, ok=True):
        p = subprocess.run(["git", *a], cwd=r, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        assert (p.returncode == 0) == ok, (a, p.stdout, p.stderr)
        return p
    git("init", "-q", "-b", "master")
    p = subprocess.run([BASH, str(ROOT / "scripts" / "audit" / "commit-msg.sh")], cwd=r, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    (r / "f.txt").write_text("1")
    git("add", "f.txt")
    git("commit", "-q", "--no-verify", "-m", "init")
    return git, r


def message(git, rev="HEAD"):
    return git("log", "-1", "--format=%B", rev).stdout


def test_installed_commit_msg_hook_stamps_offline_when_the_backlog_is_unreachable(repo):
    git, r = repo
    (r / "f.txt").write_text("2")
    git("commit", "-q", "-a", "-m", "feat: FND-0001 first", "-m", "Refs: FND-0001")
    assert message(git).rstrip().endswith("Backlog-Gate: offline") and "Refs: FND-0001" in message(git)
    git("commit", "-q", "--amend", "--no-edit")                                    # an amend must not stack a second trailer
    assert message(git).count("Backlog-Gate:") == 1


def test_installed_hook_rejects_a_malformed_token_and_skips_commits_without_a_ticket(repo):
    git, r = repo
    (r / "f.txt").write_text("3")
    git("commit", "-q", "-a", "-m", "fix: fnd0193 oops", ok=False)
    git("commit", "-q", "-a", "-m", "docs: plain")
    assert "Backlog-Gate" not in message(git)


def test_no_verify_leaves_no_trailer(repo):
    git, r = repo
    (r / "f.txt").write_text("4")
    git("commit", "-q", "-a", "--no-verify", "-m", "feat: FND-0001 sneaky")
    assert "Backlog-Gate" not in message(git)


def test_pre_rebase_refuses_master_and_allows_a_branch(repo):
    git, r = repo
    git("checkout", "-q", "-b", "work")
    (r / "g.txt").write_text("x")
    git("add", "g.txt")
    git("commit", "-q", "--no-verify", "-m", "on work")
    git("rebase", "master")                                                         # a branch rebases freely
    git("checkout", "-q", "master")
    git("rebase", "work", ok=False)                                                  # master does not
    git("rebase", "master", "work")                                                  # rebasing the branch from master is fine


# ---------------------------------------------------------------- strict traceability (BACKLOG_REQUIRE_TICKET_ENABLED, off by default)

@pytest.mark.parametrize("subject, branch, merging, why", [
    ("Merge branch 'feat/x'", "master", False, "merge"),
    ("fix: something", "master", True, "merge"),
    ('Revert "feat: FND-0001 x"', "master", False, "revert"),
    ("chore: bump the pin", "master", False, "chore"),
    ("chore(deps)!: drop python 3.12", "master", False, "chore"),
    ("Chore: capitalised", "master", False, "chore"),
    ("docs: a normal commit on a chore branch", "chore/env-path-des", False, "chore branch"),
    ("feat: a normal commit", "feat/x", False, None),
    ("chorex: not a chore", "master", False, None),
    ("fix: chore: inside the subject", "master", False, None),
])
def test_ticket_exemptions(subject, branch, merging, why):
    assert g.ticket_exemption(subject, branch, merging) == why


def run_strict(tmp_path, message, branch="master", merging=False, require=True):
    f = tmp_path / "MSG"
    f.write_text(message, encoding="utf-8")
    err = Err()

    def boom():
        raise AssertionError("no ticket cited: the backlog must not be touched")
    rc = hook.run(str(f), store_factory=boom, tree_fn=boom, err=err, require_ticket=require, context_fn=lambda: (branch, merging))
    return rc, f.read_text(encoding="utf-8"), err.text


def test_strict_mode_rejects_a_commit_without_a_ticket_unless_exempt(tmp_path):
    rc, text, err = run_strict(tmp_path, "feat: no ticket here\n")
    assert rc == hook.REJECTED_RC and "No ticket cited" in err and text == "feat: no ticket here\n"
    assert run_strict(tmp_path, "chore: bump\n")[0] == 0
    assert run_strict(tmp_path, "Merge branch 'x'\n")[0] == 0
    assert run_strict(tmp_path, 'Revert "feat: y"\n')[0] == 0
    assert run_strict(tmp_path, "docs: z\n", branch="chore/x")[0] == 0
    assert run_strict(tmp_path, "fix: z\n", merging=True)[0] == 0


def test_strict_mode_is_off_by_default_and_never_touches_a_commit_that_cites_a_ticket(tmp_path):
    assert run_strict(tmp_path, "feat: no ticket here\n", require=False)[0] == 0
    import shared.config as cfg
    assert cfg.BACKLOG_REQUIRE_TICKET_ENABLED is False
    rc, text, _ = run_hook(tmp_path, "feat: FND-0001 cited\n", FakeStore({"FND-0001": "IN_PROGRESS"}), require_ticket=True)
    assert rc == 0 and NONCE in text


# ---------------------------------------------------------------- the live store (no database: the client is replaced)

def test_live_store_without_its_scoped_login_is_offline_never_the_general_writer(monkeypatch):
    store = hook.LiveStore()
    monkeypatch.setenv("FONDOS_ACTOR", hook.ACTOR)
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN_GATE", raising=False)
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", "postgresql://writer@127.0.0.1:1/gestion")      # the general login must NOT be used
    for call in (lambda: store.statuses(["FND-0001"]), lambda: store.gate_check(TREE, ["FND-0001"]), lambda: store.forward("FND-0001", "a comment long enough")):
        with pytest.raises(hook.GateOffline):
            call()


def test_live_store_passes_valid_arguments_to_the_scoped_client_functions(monkeypatch):
    store = hook.LiveStore()
    seen = {}
    monkeypatch.setattr(store.bc, "gate_forward", lambda ap, sid, comment, actor=None, conn=None: seen.update(ap=ap, sid=sid, actor=actor) or 1)
    monkeypatch.setattr(store.bc, "gate_check", lambda tree, aps, actor=None, conn=None: seen.update(tree=tree, aps=aps, actor2=actor) or NONCE)
    monkeypatch.setattr(store.bc, "gate_statuses", lambda ids, conn=None: {i: "OPEN" for i in ids})
    store.forward("FND-0001", "commit-msg gate: work started")
    assert seen["ap"] == "FND-0001" and re.fullmatch(r"FND_\d{8}_SYNC_BACKLOG_GATE", seen["sid"]) and seen["actor"] == "system:gate"       # a real session id: the first version raised ValueError here
    assert store.gate_check(TREE, ["FND-0001"]) == NONCE and seen["actor2"] == "system:gate"
    assert store.statuses(["FND-0001"]) == {"FND-0001": "OPEN"}
