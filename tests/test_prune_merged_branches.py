# tests/test_prune_merged_branches.py
# -*- coding: utf-8 -*-
"""scripts/gov/delivery/prune_merged_branches.py and .github/workflows/branch-cleanup.yml (Backlog v3.1 housekeeping, FND-0371): the selection rules (pure), and the deletion against a real throwaway repository with a
real bare remote. No network, no GitHub, no database (R-7)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "gov" / "delivery")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import prune_merged_branches as pm  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is needed")
TIPS = {"feat/merged": "a1", "feat/moved-on": "b2", "feat/never-merged": "c3", "feat/open": "d4", "feat/base-of-open": "e5", "fix/merged": "f6", "docs/merged": "g7",
        "publish/x": "h8", "master": "m0", "main": "m1", "wip/merged": "i9"}
MERGED = {"feat/merged", "feat/moved-on", "feat/open", "feat/base-of-open", "fix/merged", "docs/merged", "publish/x", "master", "main", "wip/merged"}
PR_TIPS = {"feat/merged": {"a1"}, "feat/moved-on": {"zz"}, "fix/merged": {"f6"}, "docs/merged": {"g7", "old"}, "feat/open": {"d4"}, "feat/base-of-open": {"e5"}, "publish/x": {"h8"}, "wip/merged": {"i9"}}
OPEN = {"feat/open", "feat/base-of-open"}


def names(rule, **kw):
    return [n for n, _why in pm.select_branches(TIPS, rule=rule, merged=MERGED, merged_pr_tips=PR_TIPS, open_heads=OPEN, **kw)]


def test_the_pr_rule_needs_the_tip_in_master_and_a_merged_pr_that_ended_on_exactly_that_commit():
    assert names("pr") == ["docs/merged", "feat/merged", "fix/merged"]
    # feat/moved-on: its PR was merged, but the branch was pushed again afterwards (tip b2, merged PR ended on zz): not deleted
    # feat/never-merged: not in master. feat/open and feat/base-of-open: an open pull request uses them. publish/x, wip/merged: outside the prefixes. master and main: never.
    assert "feat/moved-on" not in names("pr") and "master" not in names("pr")


def test_the_ancestor_rule_is_a_persons_tool_it_needs_only_the_tip_in_master_and_still_never_touches_open_prs_or_other_names():
    assert names("ancestor") == ["docs/merged", "feat/merged", "feat/moved-on", "fix/merged"]
    assert names("ancestor", prefixes=pm.PREFIXES + ("publish/",)) == ["docs/merged", "feat/merged", "feat/moved-on", "fix/merged", "publish/x"]
    assert names("ancestor", keep=("feat/merged",)) == ["docs/merged", "feat/moved-on", "fix/merged"]
    assert not {"master", "main", "feat/open", "feat/base-of-open", "feat/never-merged"} & set(names("ancestor", prefixes=("",)))        # not even an empty prefix reaches the protected ones


def test_an_unknown_rule_is_refused_and_nothing_is_selected_without_data():
    with pytest.raises(ValueError):
        pm.select_branches(TIPS, rule="all", merged=MERGED, merged_pr_tips={}, open_heads=set())
    assert pm.select_branches({}, rule="pr", merged=set(), merged_pr_tips={}, open_heads=set()) == []
    assert pm.select_branches(TIPS, rule="pr", merged=MERGED, merged_pr_tips={}, open_heads=set()) == []                    # no merged pull request known: nothing


def test_the_parsers_read_git_and_gh_output():
    assert pm.parse_tips("feat/a abc123\nfix/b def456\nHEAD 000\n\n") == {"feat/a": "abc123", "fix/b": "def456"}
    opened = json.dumps([{"headRefName": "feat/x", "baseRefName": "master"}, {"headRefName": "feat/y", "baseRefName": "feat/x"}, {"headRefName": "feat/z"}])
    merged = json.dumps([{"headRefName": "feat/a", "headRefOid": "1"}, {"headRefName": "feat/a", "headRefOid": "2"}, {"headRefName": "feat/b", "headRefOid": "3"}])
    heads, prs = pm.parse_pr_lists(opened, merged)
    assert heads == {"feat/x", "feat/y", "master", "feat/z"} and prs == {"feat/a": {"1", "2"}, "feat/b": {"3"}}       # a base is in use too: deleting it would close the stacked PR
    assert pm.parse_pr_lists("", "") == (set(), {})
    assert pm.chunks(list(range(5)), 2) == [[0, 1], [2, 3], [4]] and pm.chunks([], 20) == []


# ---------------------------------------------------------------- a real repository with a real bare remote

class Rig:
    def __init__(self, tmp_path):
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        self.remote, self.work = tmp_path / "origin.git", tmp_path / "work"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(self.remote)], check=True, env=self.env)
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.work)], check=True, capture_output=True, env=self.env)
        self.git("checkout", "-q", "-b", "master")
        self.commit("README", "x", "init")
        self.git("push", "-q", "origin", "master")

    def git(self, *a):
        p = subprocess.run(["git", *a], cwd=self.work, capture_output=True, text=True, encoding="utf-8", env=self.env)
        assert p.returncode == 0, (a, p.stderr)
        return p.stdout.strip()

    def commit(self, name, text, message):
        (self.work / name).write_text(text, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", message)

    def merged_branch(self, branch):
        """A branch with one commit, merged into master with a merge commit, both pushed (what an auto-merged PR leaves behind)."""
        self.git("checkout", "-q", "-b", branch, "master")
        self.commit(branch.replace("/", "_"), branch, f"work on {branch}")
        self.git("push", "-q", "origin", branch)
        tip = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "master")
        self.git("merge", "-q", "--no-ff", branch, "-m", f"Merge pull request from {branch}")
        self.git("push", "-q", "origin", "master")
        return tip

    def unmerged_branch(self, branch):
        self.git("checkout", "-q", "-b", branch, "master")
        self.commit(branch.replace("/", "_"), branch, f"unfinished {branch}")
        self.git("push", "-q", "origin", branch)
        self.git("checkout", "-q", "master")

    def remote_branches(self):
        return sorted(l.split("refs/heads/")[1] for l in subprocess.run(["git", "ls-remote", "--heads", str(self.remote)], capture_output=True, text=True, env=self.env).stdout.splitlines())

    def run(self, argv, gh=None):
        """main() against the work clone: real git, and a fake gh (the real one needs GitHub)."""
        def run(cmd):
            if cmd[0] == "gh":
                return gh(cmd)
            p = subprocess.run(cmd, cwd=self.work, capture_output=True, text=True, encoding="utf-8", env=self.env)
            if p.returncode != 0:
                raise RuntimeError(f"{cmd[0]} {cmd[1]} failed: {p.stderr.strip()[:200]}")
            return p.stdout
        return pm.main(argv, cwd=self.work, run=run)


@needs_git
def test_the_ancestor_rule_deletes_merged_branches_from_a_real_remote_and_leaves_everything_else(tmp_path, capsys):
    r = Rig(tmp_path)
    r.merged_branch("feat/done")
    r.merged_branch("fix/also-done")
    r.merged_branch("publish/keep-me")
    r.unmerged_branch("feat/in-progress")
    assert r.run(["--rule", "ancestor"]) == 0
    assert r.remote_branches() == ["feat/done", "feat/in-progress", "fix/also-done", "master", "publish/keep-me"]            # a dry run deletes nothing
    assert "feat/done" in capsys.readouterr().out
    assert r.run(["--rule", "ancestor", "--apply", "--keep", "fix/also-done"]) == 0
    assert r.remote_branches() == ["feat/in-progress", "fix/also-done", "master", "publish/keep-me"]
    assert r.run(["--rule", "ancestor", "--apply", "--prefix", "publish/", "--keep", "fix/also-done"]) == 0
    assert r.remote_branches() == ["feat/in-progress", "fix/also-done", "master"]


@needs_git
def test_the_pr_rule_deletes_only_a_branch_whose_merged_pull_request_ended_on_its_tip_and_never_an_open_one(tmp_path):
    r = Rig(tmp_path)
    tip_done = r.merged_branch("feat/done")
    r.merged_branch("feat/no-pr")                                            # merged by hand, no pull request ever: the pr rule leaves it
    old = r.merged_branch("feat/moved-on")
    r.git("checkout", "-q", "feat/moved-on")
    r.git("merge", "-q", "master")                                           # the branch is pushed again after its merge
    r.git("push", "-q", "origin", "feat/moved-on")
    r.git("checkout", "-q", "master")
    r.merged_branch("feat/reopened")
    tip_reopened = r.git("rev-parse", "origin/feat/reopened")

    def gh(cmd):
        if "open" in cmd:
            return json.dumps([{"headRefName": "feat/reopened", "baseRefName": "master"}])
        return json.dumps([{"headRefName": "feat/done", "headRefOid": tip_done}, {"headRefName": "feat/moved-on", "headRefOid": old}, {"headRefName": "feat/reopened", "headRefOid": tip_reopened}])

    assert r.run(["--rule", "pr", "--apply"], gh=gh) == 0
    assert r.remote_branches() == ["feat/moved-on", "feat/no-pr", "feat/reopened", "master"]


@needs_git
def test_when_gh_fails_the_pr_rule_deletes_nothing_and_says_so(tmp_path, capsys):
    r = Rig(tmp_path)
    r.merged_branch("feat/done")

    def gh(cmd):
        raise RuntimeError("gh pr failed: HTTP 401")

    assert r.run(["--rule", "pr", "--apply"], gh=gh) == pm.RC_ENV
    assert r.remote_branches() == ["feat/done", "master"] and "error:" in capsys.readouterr().err


@needs_git
def test_the_local_scope_deletes_merged_local_branches_never_the_current_one_or_an_unmerged_one(tmp_path):
    r = Rig(tmp_path)
    r.merged_branch("feat/done")
    r.unmerged_branch("feat/in-progress")
    r.git("checkout", "-q", "feat/in-progress")
    assert r.run(["--scope", "local", "--apply"]) == 0
    local = sorted(l.strip("* ").strip() for l in r.git("branch").splitlines())
    assert local == ["feat/in-progress", "master"]                                    # feat/done went; the current branch and master stay (and so would anything unmerged)


@needs_git
def test_the_local_scope_keeps_the_branch_that_is_checked_out_even_when_it_is_merged(tmp_path):
    r = Rig(tmp_path)
    r.merged_branch("feat/done")
    r.merged_branch("feat/also-done")
    r.git("checkout", "-q", "feat/done")                                              # merged AND checked out
    assert r.run(["--scope", "local", "--apply"]) == 0                                # git would refuse it anyway; the tool must not even try (a refusal would be exit 3)
    assert sorted(l.strip("* ").strip() for l in r.git("branch").splitlines()) == ["feat/done", "master"]


# ---------------------------------------------------------------- the workflow is pinned by reading it

WORKFLOW = _ROOT / ".github" / "workflows" / "branch-cleanup.yml"


def load():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_workflow_has_exactly_the_permissions_it_needs_and_three_triggers_and_never_a_pull_request_one():
    wf = load()
    assert wf["permissions"] == {"contents": "write", "pull-requests": "read"}
    on = wf.get("on", wf.get(True))
    assert set(on) == {"push", "schedule", "workflow_dispatch"} and on["push"] == {"branches": ["master"]}
    assert "pull_request" not in on and "pull_request_target" not in on


def test_the_workflow_runs_the_pr_rule_with_the_workflow_token_and_nothing_else():
    wf = load()
    steps = wf["jobs"]["prune"]["steps"]
    assert steps[0]["uses"].startswith("actions/checkout@") and steps[0]["with"] == {"fetch-depth": 0}                 # the whole history, or "contained in master" cannot be known
    run = steps[1]
    assert run["run"] == "python3 scripts/gov/delivery/prune_merged_branches.py --rule pr --apply" and run["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    assert len(steps) == 2 and "ancestor" not in WORKFLOW.read_text(encoding="utf-8").split("jobs:")[1]
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_the_script_never_deletes_the_protected_names_by_construction():
    assert {"master", "main"} <= set(pm.NEVER) and pm.PREFIXES == ("feat/", "fix/", "chore/", "docs/")
    src = (_ROOT / "scripts" / "gov" / "delivery" / "prune_merged_branches.py").read_text(encoding="utf-8")
    assert src.count('"push", "origin", "--delete"') == 1 and "--force" not in src
    assert src.count('"branch", "-D"') == 1 and '"--merged", "origin/master"' in src                 # -D only ever follows the --merged origin/master listing


# ---------------------------------------------------------------- FND-0371: stacked branches, --no-fetch, the lock, the hook that starts it

def test_stacked_bases_are_the_merged_branches_an_unmerged_branch_was_cut_from():
    tips = {"feat/base": "b1", "feat/other": "o1", "feat/unmerged": "u1", "master": "m1"}
    assert pm.stacked_bases(tips, {"feat/base", "feat/other", "master"}, {"b1", "zz"}) == {"feat/base"}
    assert pm.stacked_bases(tips, {"feat/base"}, {"u1"}) == set()                          # a base that is itself unmerged is not "merged": nothing to protect
    assert pm.stacked_bases(tips, {"feat/base"}, set()) == set()


@needs_git
def test_the_ancestor_rule_keeps_a_merged_branch_that_a_stacked_unmerged_branch_was_cut_from(tmp_path):
    r = Rig(tmp_path)
    r.merged_branch("feat/base")
    r.merged_branch("feat/alone")
    r.git("checkout", "-q", "-b", "feat/stacked", "origin/feat/base")                   # a stacked pull request: its base is feat/base
    r.commit("stacked", "s", "work on the stack")
    r.git("push", "-q", "origin", "feat/stacked")
    r.git("checkout", "-q", "master")
    assert r.run(["--rule", "ancestor", "--apply"]) == 0
    assert r.remote_branches() == ["feat/base", "feat/stacked", "master"]               # feat/alone went; deleting feat/base would close the stacked pull request


@needs_git
def test_a_branch_with_no_common_history_with_master_does_not_stop_the_run(tmp_path):
    r = Rig(tmp_path)
    r.merged_branch("feat/done")
    r.git("checkout", "-q", "--orphan", "feat/orphan")
    r.commit("orphan", "o", "unrelated history")
    r.git("push", "-q", "origin", "feat/orphan")
    r.git("checkout", "-q", "master")
    assert r.run(["--rule", "ancestor", "--apply"]) == 0                                 # `git merge-base` exits 1 for it: not an error of the run
    assert r.remote_branches() == ["feat/orphan", "master"]


@needs_git
def test_no_fetch_skips_the_fetch_and_the_default_still_fetches(tmp_path):
    r = Rig(tmp_path)
    r.merged_branch("feat/done")
    seen = []

    def spy(cmd):
        seen.append(cmd[:2])
        return subprocess.run(cmd, cwd=r.work, capture_output=True, text=True, encoding="utf-8", env=r.env, check=True).stdout

    assert pm.main(["--rule", "ancestor", "--no-fetch"], cwd=r.work, run=spy) == 0
    assert ["git", "fetch"] not in seen
    assert pm.main(["--rule", "ancestor"], cwd=r.work, run=spy) == 0
    assert ["git", "fetch"] in seen


def test_the_lock_is_taken_once_and_a_dead_runs_lock_is_taken_over(tmp_path):
    lock = tmp_path / "out" / "audit" / "x.lock"
    assert pm.acquire_lock(lock) is True and lock.exists()
    assert pm.acquire_lock(lock) is False                                               # a second run while one holds it
    assert pm.acquire_lock(lock, now=lock.stat().st_mtime + pm.LOCK_STALE_S - 1) is False
    assert pm.acquire_lock(lock, now=lock.stat().st_mtime + pm.LOCK_STALE_S + 1) is True      # older than the limit: the run that made it is dead


@needs_git
def test_apply_does_nothing_while_another_run_holds_the_lock_and_releases_it_afterwards(tmp_path, capsys):
    r = Rig(tmp_path)
    r.merged_branch("feat/done")
    lock = r.work / pm.LOCK_REL
    assert pm.acquire_lock(lock)
    assert r.run(["--rule", "ancestor", "--apply"]) == 0
    assert r.remote_branches() == ["feat/done", "master"] and "another prune holds" in capsys.readouterr().out
    lock.unlink()
    assert r.run(["--rule", "ancestor", "--apply"]) == 0
    assert r.remote_branches() == ["master"] and not lock.exists()                      # done, and the lock is gone
    assert r.run(["--rule", "ancestor"]) == 0 and not lock.exists()                     # a dry run never takes it


@needs_git
def test_a_failed_run_still_releases_the_lock(tmp_path):
    r = Rig(tmp_path)
    r.merged_branch("feat/done")

    def boom(cmd):
        raise RuntimeError("git for-each-ref failed")

    assert pm.main(["--rule", "ancestor", "--apply", "--no-fetch"], cwd=r.work, run=boom) == pm.RC_ENV
    assert not (r.work / pm.LOCK_REL).exists()
