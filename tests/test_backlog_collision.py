# tests/test_backlog_collision.py
# -*- coding: utf-8 -*-
"""Phase C slice 3 (FND-0334), no database: the collision step of the commit-msg hook, the pre-push gate, the adjudication tool, the queue lines of the pre-flight and the installed hooks in a
throwaway repository (a crash of a hook script never blocks; only the exit status 3 refuses)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import commit_msg_gate as hook  # noqa: E402
import gate_adjudicate as adj  # noqa: E402
import pre_push_gate as pp  # noqa: E402
import preflight_check as pre  # noqa: E402

NONCE = "ab" * 16
TREE = "1" * 40


# ---------------------------------------------------------------- the commit-msg hook and the rule

class Store:
    forward_enabled = False

    def __init__(self, result=None, fail=False):
        self.result, self.fail, self.calls = result, fail, []

    def statuses(self, ids):
        return {i: "IN_PROGRESS" for i in ids}

    def forward(self, ap, comment):
        raise AssertionError("not here")

    def gate_check(self, tree, aps):
        return NONCE

    def collision(self, files, aps, ceiling, nonce):
        self.calls.append((list(files), list(aps), ceiling, nonce))
        if self.fail:
            raise ConnectionRefusedError("down")
        return self.result


class Err:
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s

    def flush(self):
        pass


def run(tmp_path, store, ceiling, message="feat: FND-0002 work\n"):
    f = tmp_path / "MSG"
    f.write_text(message, encoding="utf-8")
    err = Err()
    rc = hook.run(str(f), store_factory=lambda: store, tree_fn=lambda: TREE, files_fn=lambda: ["shared/foo.py", "doc/x.md"], err=err, ceiling=ceiling)
    return rc, f.read_text(encoding="utf-8"), err.text


PAIR = {"mode": "AUDIT", "tier": "T0", "new": [{"a": "FND-0001", "b": "FND-0002", "shared": ["shared/foo.py"]}], "undecided": [{"a": "FND-0001", "b": "FND-0002"}], "held": []}


def test_collision_lines_warn_and_never_decide():
    assert hook.collision_lines({}) == [] and hook.collision_lines({"mode": "OFF"}) == []
    [line] = hook.collision_lines(PAIR)
    assert "AUDIT T0" in line and "FND-0001 and FND-0002 collide on shared/foo.py (new pair)" in line and "gate_adjudicate.py" in line
    old = {**PAIR, "new": []}
    assert "have an undecided collision pair" in hook.collision_lines(old)[0]
    blocked = hook.collision_lines({**PAIR, "mode": "BLOCK", "held": ["FND-0002"]})
    assert len(blocked) == 2 and "hold was raised on FND-0002" in blocked[1] and "`git push` will be refused" in blocked[1]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is needed for interpret-trailers")
def test_the_hook_evaluates_the_rule_after_the_nonce_and_warns_without_blocking(tmp_path):
    store = Store(PAIR)
    rc, text, err = run(tmp_path, store, "AUDIT")
    assert rc == 0 and NONCE in text and "collide on shared/foo.py" in err
    assert store.calls == [(["shared/foo.py", "doc/x.md"], ["FND-0002"], "AUDIT", NONCE)]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is needed for interpret-trailers")
def test_with_the_gate_off_the_database_is_not_asked_for_the_rule(tmp_path):
    store = Store(PAIR)
    rc, text, err = run(tmp_path, store, "OFF")
    assert rc == 0 and NONCE in text and store.calls == [] and err == ""


@pytest.mark.skipif(shutil.which("git") is None, reason="git is needed for interpret-trailers")
def test_a_failure_of_the_rule_never_costs_the_nonce(tmp_path):
    rc, text, err = run(tmp_path, Store(fail=True), "BLOCK")
    assert rc == 0 and text.rstrip().endswith(f"Backlog-Gate: {NONCE}") and "could not be evaluated (ConnectionRefusedError)" in err


# ---------------------------------------------------------------- pre-push

ZERO = "0" * 40


def test_parse_push_and_the_range_of_each_update():
    text = f"refs/heads/x {'a' * 40} refs/heads/x {'b' * 40}\nrefs/heads/new {'c' * 40} refs/heads/new {ZERO}\nrefs/heads/gone {ZERO} refs/heads/gone {'d' * 40}\n\nbroken line\n"
    ups = pp.parse_push(text)
    assert len(ups) == 3
    assert pp.rev_list_args("a" * 40, "b" * 40, "origin") == ["a" * 40, "--not", "b" * 40]
    assert pp.rev_list_args("c" * 40, ZERO, "origin") == ["c" * 40, "--not", "--remotes=origin"]
    assert pp.rev_list_args(ZERO, "d" * 40, "origin") == []                                      # deleting a ref publishes nothing


def test_tickets_of_the_commits_and_the_refusal_text():
    by = pp.tickets_of({"a" * 40: "feat: FND-0001 x\n\nRefs: FND-0003\n", "b" * 40: "docs: nothing\n", "c" * 40: "fix: FND-0009\n"})
    assert by == {"a" * 40: ["FND-0001", "FND-0003"], "c" * 40: ["FND-0009"]}
    und = [{"a": "FND-0001", "b": "FND-0002", "age_hours": 5}]
    lines = pp.refusal(by, und)
    assert "BLOCK" in lines[0] and any(l.startswith("  aaaaaaaaaaaa  cites FND-0001") for l in lines) and not any("cccccccccccc" in l for l in lines)
    assert any("FND-0001 / FND-0002" in l for l in lines)


def test_evaluate_refuses_only_in_block_with_an_undecided_pair():
    by = {"a" * 40: ["FND-0001"]}
    pair = [{"a": "FND-0001", "b": "FND-0002", "age_hours": 3}]
    state = lambda mode: (lambda c: {"mode": mode})
    assert pp.evaluate(by, "BLOCK", state("BLOCK"), lambda ts: pair)[0] == pp.REJECTED_RC
    rc, lines = pp.evaluate(by, "BLOCK", state("AUDIT"), lambda ts: pair)
    assert rc == 0 and "not refused in AUDIT" in lines[0]                                     # the latest event lowered the mode
    assert pp.evaluate(by, "BLOCK", state("OFF"), lambda ts: pair) == (0, [])                    # retired by the calibrator
    assert pp.evaluate(by, "BLOCK", state("BLOCK"), lambda ts: []) == (0, [])                    # nothing undecided
    assert pp.evaluate(by, "OFF", lambda c: pytest.fail("asked"), lambda ts: pytest.fail("asked")) == (0, [])
    assert pp.evaluate({}, "BLOCK", lambda c: pytest.fail("asked"), lambda ts: pytest.fail("asked")) == (0, [])


# ---------------------------------------------------------------- the adjudication tool

def test_queue_csv_round_trip_and_the_decisions_it_accepts():
    rows = [{"a": "FND-0001", "b": "FND-0002", "age_hours": 5, "level": "OK", "evidence": "shared: x.py"},
            {"a": "FND-0003", "b": "FND-0004", "age_hours": 60, "level": "ATENCION", "evidence": "shared: y.py"}]
    text = adj.queue_csv(rows)
    assert text.splitlines()[0] == ",".join(adj.COLUMNS)
    undecided = {("FND-0001", "FND-0002"), ("FND-0003", "FND-0004")}
    assert adj.read_decisions(text, undecided) == ([], [])                                        # nothing filled in: nothing decided
    filled = text.replace("shared: x.py,,,", 'shared: x.py,NONE,,"different functions of the file"').replace("shared: y.py,,,", f"shared: y.py,revert,,the commit was reverted by {'f' * 40}")
    ok, bad = adj.read_decisions(filled, undecided)
    assert ok == [("FND-0001", "FND-0002", "NONE", None, "different functions of the file"), ("FND-0003", "FND-0004", "REVERT", None, f"the commit was reverted by {'f' * 40}")] and bad == []


def test_decisions_the_tool_refuses_before_the_database_does():
    undecided = {("FND-0001", "FND-0002")}
    csv_text = ("a,b,decision,absorbs,reason\n"
                "FND-0001,FND-0002,MAYBE,,a reason long enough\n"
                "FND-0001,FND-0009,NONE,,a reason long enough\n"
                "FND-0002,FND-0001,NONE,,short\n"
                "FND-0001,FND-0002,REVERT,,reverted but no sha is given\n"
                "FND-0002,FND-0001,OVERLAPS,the criteria,a reason long enough\n")
    ok, bad = adj.read_decisions(csv_text, undecided)
    assert [n for n, _ in bad] == [2, 3, 4, 5] and ok == [("FND-0002", "FND-0001", "OVERLAPS", "the criteria", "a reason long enough")]   # either order of the pair is accepted
    assert "is not one of" in bad[0][1] and "not in the undecided queue" in bad[1][1] and "reason of 10" in bad[2][1] and "40-character sha" in bad[3][1]


def test_list_format_marks_the_ages():
    assert adj.format_queue([]) == ["no undecided collision pair"]
    lines = adj.format_queue([{"a": "A", "b": "B", "age_hours": 100, "level": "ALERTA", "evidence": "e1"}, {"a": "C", "b": "D", "age_hours": 50, "level": "ATENCION", "evidence": "e2"},
                              {"a": "E", "b": "F", "age_hours": 1, "level": "OK", "evidence": "e3"}])
    assert lines[0].startswith("3 undecided") and "[ALERTA] A / B" in lines[1] and "[ATENCION] C / D" in lines[2] and "[" not in lines[3]
    assert adj.csv_cell('say "hi"') == '"say ""hi"""'


# ---------------------------------------------------------------- the pre-flight queue lines

def test_preflight_queue_lines_and_the_alerta_exit():
    assert pre.queue_lines([]) == ([], False)
    assert pre.queue_lines([{"a": "A", "b": "B", "age_hours": 5, "level": "OK"}]) == (["collision queue: 1 undecided pair(s), none older than 48 h"], False)
    lines, alerta = pre.queue_lines([{"a": "A", "b": "B", "age_hours": 100, "level": "ALERTA"}, {"a": "C", "b": "D", "age_hours": 50, "level": "ATENCION"}])
    assert alerta and lines[0].startswith("[ALERTA] 1 collision pair(s) undecided for 96 h") and lines[1].startswith("[ATENCION] 1 collision pair(s) undecided for 48 h")
    base = {"driver": {}, "git": {"dirty": {}}, "processes": {}, "db": {"error": "skipped"}, "memory": {"error": "x"}, "verdict": {"level": "CLEAR", "cautions": [], "notes": []}}
    text = pre.render({**base, "backlog": {"open": 1, "new": [], "since_hours": 24, "queue": [{"a": "A", "b": "B", "age_hours": 100, "level": "ALERTA"}]}})
    assert text.splitlines()[0].startswith("[ALERTA]") and "=== PRE-FLIGHT" in text.splitlines()[1]       # the first line of the report
    assert not pre.render({**base, "backlog": {"open": 1, "new": [], "since_hours": 24, "queue": []}}).startswith("[ALERTA]")


# ---------------------------------------------------------------- the installed hooks, in a throwaway repository

BASH = shutil.which("bash")
STUB_CONFIG = 'BACKLOG_COLLISION_GATE_MODE = "BLOCK"\nBACKLOG_REQUIRE_TICKET_ENABLED = False\n'
STUB_CLIENT = ('def collision_state(ceiling, scoped=False):\n    return {"mode": "BLOCK"}\n\n'
               'def gate_undecided(tickets, scoped=False):\n    return [{"a": "FND-0001", "b": "FND-0002", "age_hours": 7}] if "FND-0001" in tickets else []\n')


@pytest.fixture
def repo(tmp_path):
    if not (BASH and shutil.which("git")):
        pytest.skip("bash and git are needed")
    r, remote = tmp_path / "r", tmp_path / "remote.git"
    (r / "scripts" / "audit").mkdir(parents=True)
    (r / "shared").mkdir()
    for src, dst in (("scripts/audit/commit_msg_gate.py", "scripts/audit"), ("scripts/audit/pre_push_gate.py", "scripts/audit"), ("shared/backlog_gate.py", "shared")):
        shutil.copy(ROOT / src, r / dst)
    (r / "shared" / "config.py").write_text(STUB_CONFIG, encoding="utf-8")
    (r / "shared" / "backlog_client.py").write_text(STUB_CLIENT, encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    env.pop("FONDOS_BACKLOG_PG_DSN", None)

    def git(*a, ok=True, cwd=r):
        p = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        assert (p.returncode == 0) == ok, (a, p.stdout, p.stderr)
        return p
    git("init", "-q", "--bare", str(remote), cwd=tmp_path)
    git("init", "-q", "-b", "master")
    git("remote", "add", "origin", str(remote))
    p = subprocess.run([BASH, str(ROOT / "scripts" / "audit" / "commit-msg.sh")], cwd=r, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert p.returncode == 0 and "pre-push hook installed" in p.stdout, p.stderr
    (r / "f.txt").write_text("1")
    git("add", "f.txt")
    git("commit", "-q", "--no-verify", "-m", "init")
    git("push", "-q", "--no-verify", "origin", "master")
    return git, r


def commit(git, r, name, message):
    (r / name).write_text(name)
    git("add", name)
    git("commit", "-q", "--no-verify", "-m", message)


def test_pre_push_refuses_a_push_that_carries_a_ticket_with_an_undecided_pair(repo):
    git, r = repo
    commit(git, r, "a.txt", "feat: FND-0001 touches the contested file")
    p = git("push", "origin", "master", ok=False)
    assert "UNDECIDED collision pair" in p.stderr and "cites FND-0001" in p.stderr and "FND-0001 / FND-0002" in p.stderr
    assert git("ls-remote", "origin", "master").stdout.split()[0] != git("rev-parse", "HEAD").stdout.strip()      # nothing was published


def test_pre_push_lets_other_tickets_and_deletions_through(repo):
    git, r = repo
    git("checkout", "-q", "-b", "side")
    commit(git, r, "b.txt", "feat: FND-0009 unrelated work")
    git("push", "-q", "origin", "side")
    git("push", "-q", "origin", "--delete", "side")                                                # a deletion is never refused


def test_a_crash_of_either_script_never_blocks_a_commit_or_a_push(repo):
    git, r = repo
    for name in ("commit_msg_gate.py", "pre_push_gate.py"):
        (r / "scripts" / "audit" / name).write_text("this is not python (\n", encoding="utf-8")
    commit(git, r, "c.txt", "feat: FND-0001 still pushed")
    (r / "d.txt").write_text("d")
    git("add", "d.txt")
    git("commit", "-q", "-m", "feat: FND-0001 the broken hook must not block this")                  # the commit-msg hook runs, crashes, and lets it through
    git("push", "-q", "origin", "master")                                                           # the pre-push hook crashes too and lets it through
