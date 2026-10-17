# tests/test_publish_master.py
# -*- coding: utf-8 -*-
"""FND-0273: the decision logic of scripts/ops/publish_master.py (pure; no git, no network, no pipeline imports -- R-7), plus one test
against a real throwaway repository for the git plumbing (range, ancestry)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_spec = importlib.util.spec_from_file_location("publish_master", os.path.join(_ROOT, "scripts", "ops", "publish_master.py"))
pm = importlib.util.module_from_spec(_spec)
sys.modules["publish_master"] = pm
_spec.loader.exec_module(pm)


def C(sha, tickets, subject="s"):
    return pm.Commit(sha * 7, "me", subject, tuple(tickets))


def test_a_clean_range_of_named_tickets_passes():
    v = pm.evaluate([C("a", ["FND-0273"]), C("b", ["FND-0275"])], ancestor_ok=True, tickets={"FND-0273", "FND-0275"})
    assert v.ok and not v.refusals


def test_behind_or_diverged_is_refused():
    v = pm.evaluate([C("a", ["FND-0273"])], ancestor_ok=False, tickets={"FND-0273"})
    assert not v.ok and "ancestor" in v.refusals[0]


def test_a_commit_naming_no_ticket_is_refused_even_with_allow_foreign():
    v = pm.evaluate([C("a", [], "wip stuff")], ancestor_ok=True, tickets={"FND-0273"}, allow_foreign=True)
    assert not v.ok and "no FND-nnnn" in v.refusals[0]


def test_a_foreign_commit_rides_along_only_by_explicit_opt_in():
    commits = [C("a", ["FND-0273"]), C("b", ["FND-0270"])]
    refused = pm.evaluate(commits, ancestor_ok=True, tickets={"FND-0273"})
    assert not refused.ok and "FND-0270" in refused.refusals[0]
    assert pm.evaluate(commits, ancestor_ok=True, tickets={"FND-0273"}, allow_foreign=True).ok


def test_a_commit_naming_a_wanted_and_an_unwanted_ticket_is_not_foreign():
    assert pm.evaluate([C("a", ["FND-0273", "FND-0270"])], ancestor_ok=True, tickets={"FND-0273"}).ok


def test_an_empty_range_is_refused():
    v = pm.evaluate([], ancestor_ok=True, tickets={"FND-0273"})
    assert not v.ok and "empty" in v.refusals[0]


def test_every_refusal_is_reported_not_only_the_first():
    v = pm.evaluate([C("a", [])], ancestor_ok=False, tickets={"FND-0273"})
    assert len(v.refusals) == 2


def test_tickets_are_read_from_the_whole_message_and_parse_log_keeps_the_order():
    raw = ("a" * 40 + "\x1f" + "Ann" + "\x1f" + "feat: thing (FND-0273)\n\nalso touches FND-0265\x1e"
           "\n" + "b" * 40 + "\x1f" + "Bob" + "\x1f" + "fix: no ticket here\x1e")
    got = pm.parse_log(raw)
    assert [c.sha[0] for c in got] == ["a", "b"]
    assert got[0].tickets == ("FND-0265", "FND-0273") and got[0].subject == "feat: thing (FND-0273)" and got[1].tickets == ()


def _run(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def test_the_range_and_the_ancestry_check_on_a_real_repository(tmp_path, monkeypatch):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "master", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        _run(work, "config", k, v)
    (work / "f").write_text("1")
    _run(work, "add", "f")
    _run(work, "commit", "-m", "base (FND-0001)")
    _run(work, "push", "origin", "HEAD:master")
    (work / "f").write_text("2")
    _run(work, "commit", "-am", "change (FND-0273)")
    monkeypatch.setattr(pm, "ROOT", work)
    head = _run(work, "rev-parse", "HEAD")
    assert pm.main([head, "--tickets", "FND-0273"]) == pm.RC_OK                    # dry run: nothing pushed
    assert _run(work, "rev-parse", "origin/master") != head
    assert pm.main([head, "--tickets", "FND-0999"]) == pm.RC_REFUSED               # the commit is for another ticket
    assert pm.main([head, "--tickets", "nonsense"]) == pm.RC_USAGE
