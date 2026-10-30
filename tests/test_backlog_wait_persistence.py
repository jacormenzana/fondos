# tests/test_backlog_wait_persistence.py
# -*- coding: utf-8 -*-
"""Persistence of the deployment records (owner order 2026-10-30): every record is COMMITTED in the repository's own working tree right after it is produced and before the next write.
Real git, in a throwaway repository under tmp_path (no network, no hooks, no database)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_wait_for_deployment as wf  # noqa: E402

BRANCH = "chore/deployment-records-final"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", BRANCH)
    git(r, "config", "user.email", "t@example.org")
    git(r, "config", "user.name", "t")
    git(r, "config", "commit.gpgsign", "false")
    (r / "deployments").mkdir()
    (r / "deployments" / "README.md").write_text("rule\n", encoding="utf-8")
    (r / "PENDING_FND_LOGS.md").write_text("pending\n", encoding="utf-8")
    (r / "other.txt").write_text("unrelated tracked file\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    return r


def record(repo, name="2026-10-30_verification_X.txt", text="PASS\n"):
    (repo / "deployments" / name).write_text(text, encoding="utf-8")


def test_the_new_record_is_committed_alone_on_the_branch_and_nothing_else_is(repo):
    before = wf.listing(repo / "deployments")
    record(repo)
    (repo / "other.txt").write_text("a dirty change that is NOT ours\n", encoding="utf-8")                  # another session's edit must stay out of the commit
    (repo / "stray.txt").write_text("untracked and not ours\n", encoding="utf-8")
    commit = wf.persist(repo, repo / "deployments", before, "docs(ops): FND-0354 - a record\n", branch=BRANCH, push=False, log=lambda m: None)
    assert git(repo, "rev-parse", "--short", "HEAD") == commit
    assert git(repo, "show", "--name-only", "--format=", "HEAD").splitlines() == ["deployments/2026-10-30_verification_X.txt"]
    assert sorted(line.strip() for line in git(repo, "status", "--porcelain").splitlines()) == ["?? stray.txt", "M other.txt"]       # the others are still exactly as they were


def test_it_refuses_on_another_branch_and_when_no_record_was_produced(repo):
    before = wf.listing(repo / "deployments")
    record(repo)
    git(repo, "switch", "-q", "-c", "master")
    with pytest.raises(wf.RecordError, match="not on chore/deployment-records-final"):
        wf.persist(repo, repo / "deployments", before, "m\n", branch=BRANCH, push=False, log=lambda m: None)
    git(repo, "switch", "-q", BRANCH)
    with pytest.raises(wf.RecordError, match="no record was produced"):
        wf.persist(repo, repo / "deployments", wf.listing(repo / "deployments"), "m\n", branch=BRANCH, push=False, log=lambda m: None)


def test_the_outbox_is_removed_in_the_same_commit_as_the_ingestion_record(repo):
    before = wf.listing(repo / "deployments")
    record(repo, "2026-10-30_pending_logs_ingestion_X.txt", "RESULT: COMPLETE\n")
    wf.persist(repo, repo / "deployments", before, "m\n", branch=BRANCH, remove=["PENDING_FND_LOGS.md"], push=False, log=lambda m: None)
    changed = git(repo, "show", "--name-status", "--format=", "HEAD").splitlines()
    assert sorted(changed) == ["A\tdeployments/2026-10-30_pending_logs_ingestion_X.txt", "D\tPENDING_FND_LOGS.md"]
    assert not (repo / "PENDING_FND_LOGS.md").exists()


def test_a_failed_push_is_only_logged_the_commit_stands(repo):
    before = wf.listing(repo / "deployments")
    record(repo)
    logged = []
    wf.persist(repo, repo / "deployments", before, "m\n", branch=BRANCH, push=True, log=logged.append)               # the throwaway repo has no remote
    assert any(line.startswith("push failed (the commit stands)") for line in logged) and git(repo, "log", "--format=%s", "-1") == "m"


def test_a_hook_that_refuses_the_commit_is_an_error_and_hooks_are_never_skipped(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho refused by the hook >&2\nexit 1\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    before = wf.listing(repo / "deployments")
    record(repo)
    with pytest.raises(wf.RecordError, match="git commit failed"):
        wf.persist(repo, repo / "deployments", before, "m\n", branch=BRANCH, push=False, log=lambda m: None)


# --- the sequence ------------------------------------------------------------------------------------------------------------------------------

def run(events, *, verify_rcs, persist_fail_at=None, ingest_rc=0):
    rcs = iter(verify_rcs)

    def persist_verify(attempt, rc):
        events.append(f"persist_verify({attempt},{rc})")
        if persist_fail_at == attempt:
            raise wf.RecordError("the commit failed")

    def persist_ingest(rc):
        events.append(f"persist_ingest({rc})")

    return wf.run(lambda: True, lambda: events.append("verify") or next(rcs), lambda: events.append("ingest") or ingest_rc,
                  attempts=3, retry_s=0, persist_verify=persist_verify, persist_ingest=persist_ingest, sleep=lambda s: None, log=lambda m: None)


def test_every_record_is_committed_before_the_next_write():
    events = []
    assert run(events, verify_rcs=[1, 0]) == wf.RC_OK
    assert events == ["verify", "persist_verify(1,1)", "verify", "persist_verify(2,0)", "ingest", "persist_ingest(0)"]       # the verification record is committed BEFORE the ingestion


def test_nothing_is_ingested_when_the_verification_record_cannot_be_committed():
    events = []
    assert run(events, verify_rcs=[0], persist_fail_at=1) == wf.RC_FAIL
    assert events == ["verify", "persist_verify(1,0)"]


def test_an_incomplete_ingestion_is_still_recorded_and_fails_the_run():
    events = []
    assert run(events, verify_rcs=[0], ingest_rc=1) == wf.RC_FAIL
    assert events[-2:] == ["ingest", "persist_ingest(1)"]


def test_a_failure_to_commit_the_ingestion_record_is_a_failure():
    def boom(rc):
        raise wf.RecordError("no space")
    rc = wf.run(lambda: True, lambda: 0, lambda: 0, attempts=1, retry_s=0, persist_ingest=boom, sleep=lambda s: None, log=lambda m: None)
    assert rc == wf.RC_FAIL
