# tests/test_backlog_ci.py
# -*- coding: utf-8 -*-
"""Backlog v3 W4 (FND-0359): `tests_pass`, the closing condition "CI green". The pure verdict of shared/backlog_ci.py, its HTTP reading with a fake opener (no network), and the probe in
scripts/audit/rfd_matrix_probe.py with the git and the GitHub read injected. R-7: no database, no git, no network."""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rfd_matrix_probe as probe  # noqa: E402
from shared import backlog_ci as ci  # noqa: E402

REQUIRED = ["Full test suite", "PG fixture smoke test"]
SHA = "a" * 40


def run(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion}


GREEN = [run("Full test suite"), run("PG fixture smoke test"), run("backlog-gate")]


# ---------------------------------------------------------------- the verdict

def test_green_needs_every_required_check_run_and_no_run_failed_or_going():
    ok, why = ci.verdict(GREEN, REQUIRED)
    assert ok is True and "3 check run(s) all green" in why and "2 required" in why


@pytest.mark.parametrize("runs, needle", [
    ([], "no check runs"),
    (GREEN[:1], "required check(s) missing on the commit: PG fixture smoke test"),
    (GREEN + [run("lint", conclusion="failure")], "check(s) failed: lint"),
    (GREEN + [run("lint", conclusion="cancelled")], "check(s) failed: lint"),
    (GREEN + [run("lint", conclusion="timed_out")], "check(s) failed: lint"),
    (GREEN + [run("slow", status="in_progress", conclusion=None)], "not finished yet: slow"),
    (GREEN + [run("queued", status="queued", conclusion=None)], "not finished yet: queued"),
    (GREEN + [run("odd", conclusion=None)], "without a passing conclusion: odd")])
def test_anything_less_than_green_is_not_green_and_says_why(runs, needle):
    ok, why = ci.verdict(runs, REQUIRED)
    assert ok is False and needle in why


def test_skipped_and_neutral_runs_count_as_passed_as_github_does():
    ok, _why = ci.verdict(GREEN + [run("docs", conclusion="skipped"), run("hint", conclusion="neutral")], REQUIRED)
    assert ok is True


def test_a_failure_wins_over_a_pending_run_so_the_reason_is_the_failure():
    ok, why = ci.verdict(GREEN + [run("lint", conclusion="failure"), run("slow", status="in_progress", conclusion=None)], REQUIRED)
    assert ok is False and why.startswith("check(s) failed")


def test_the_evidence_is_the_pull_requests_head_which_is_the_second_parent_of_a_merge():
    assert ci.evidence_commit("m", ["p1", "p2"]) == "p2" and ci.evidence_commit("c", ["p1"]) == "c" and ci.evidence_commit("c", []) == "c"


def test_the_required_checks_come_from_the_branch_object_and_an_unprotected_branch_says_none():
    body = {"protection": {"required_status_checks": {"contexts": ["B", "A"], "checks": [{"context": "C"}, {"context": "A"}]}}}
    assert ci.required_checks(200, body) == ["A", "B", "C"]
    assert ci.required_checks(200, {"protected": False}) == [] and ci.required_checks(404, None) == [] and ci.required_checks(200, "x") == []


# ---------------------------------------------------------------- reading GitHub (fake opener)

class Resp:
    def __init__(self, status, body):
        self.status, self._raw = status, json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._raw


def opener_for(routes, seen=None, boom=None):
    def op(req, timeout=10):
        if seen is not None:
            seen.append((req.full_url, dict(req.header_items())))
        if boom:
            raise boom
        for needle, (status, body) in routes.items():
            if needle in req.full_url:
                if status >= 400:
                    raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(json.dumps(body).encode("utf-8")))
                return Resp(status, body)
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"{}"))
    return op


BRANCH = (200, {"protection": {"required_status_checks": {"contexts": REQUIRED}}})
CHECKS = (200, {"total_count": 3, "check_runs": GREEN})


def read(routes, token=None, seen=None, boom=None):
    return ci.read_ci("o/r", "master", SHA, token, opener_for(routes, seen, boom))


def test_a_green_commit_is_true_and_the_two_urls_are_the_check_runs_and_the_branch():
    seen = []
    ok, detail = read({"/check-runs": CHECKS, "/branches/master": BRANCH}, seen=seen)
    assert ok is True and detail.startswith(SHA[:12] + ":") and [u.split("/repos/o/r/")[1][:11] for u, _h in seen] == ["commits/aaa", "branches/ma"]


def test_a_failing_commit_is_false_and_an_unreadable_one_is_none_never_a_pass():
    bad = (200, {"total_count": 3, "check_runs": GREEN[:2] + [run("backlog-gate", conclusion="failure")]})
    assert read({"/check-runs": bad, "/branches/master": BRANCH})[0] is False
    ok, detail = read({"/check-runs": (403, {"message": "rate limit"}), "/branches/master": BRANCH})
    assert ok is None and "HTTP 403" in detail and "rate limit" in detail                        # the refusal says what it most likely is
    assert read({"/check-runs": (500, {}), "/branches/master": BRANCH})[0] is None
    assert read({"/branches/master": BRANCH}, boom=OSError("down"))[0] is None
    assert read({"/check-runs": (200, "garbage"), "/branches/master": BRANCH})[0] is None


def test_an_incomplete_page_and_an_unreadable_branch_are_unverifiable_not_green():
    assert read({"/check-runs": (200, {"total_count": 150, "check_runs": GREEN}), "/branches/master": BRANCH})[0] is None
    ok, detail = read({"/check-runs": CHECKS, "/branches/master": (404, {})})
    assert ok is None and "requires could not be read" in detail


def test_the_token_only_goes_in_the_authorization_header_and_never_in_a_detail():
    seen = []
    ok, detail = read({"/check-runs": CHECKS, "/branches/master": BRANCH}, token="ghp_SECRETSECRET", seen=seen)
    assert all(h.get("Authorization") == "Bearer ghp_SECRETSECRET" for _u, h in seen) and "SECRET" not in detail
    seen.clear()
    read({"/check-runs": CHECKS, "/branches/master": BRANCH}, token=None, seen=seen)
    assert all("Authorization" not in h for _u, h in seen)                                       # a public repository needs no secret


# ---------------------------------------------------------------- the probe

def fake_git(parents_line, rc=0):
    return lambda cmd: (rc, parents_line)


def test_the_probe_reads_the_ci_on_the_second_parent_of_the_delivering_merge():
    got = []
    out = probe.probe_tests_pass("FND-1", evidence={"FND-1": "m" * 40}, git=fake_git(f"{'m' * 40} {'1' * 40} {'2' * 40}"), read=lambda repo, branch, sha, token: got.append((repo, branch, sha, token)) or (True, "all green"))
    assert out == (True, "tests_pass: all green") and got[0][2] == "2" * 40 and got[0][0] and got[0][1]
    assert got[0][3] is None                                                                  # the autopilot holds no secret: the read is unauthenticated, whatever the environment has


def test_the_probe_never_reads_a_vault_variable_even_when_the_environment_has_one(monkeypatch):
    monkeypatch.setenv("BACKLOG_GATE_GITHUB_TOKEN", "ghp_SHOULD_NEVER_BE_USED")
    seen = []
    probe.probe_tests_pass("FND-1", evidence={"FND-1": "c" * 40}, git=fake_git(f"{'c' * 40}"), read=lambda repo, branch, sha, token: seen.append(token) or (True, "ok"))
    assert seen == [None]
    assert "BACKLOG_GATE_GITHUB_TOKEN" not in (_ROOT / "scripts" / "audit" / "rfd_matrix_probe.py").read_text(encoding="utf-8").replace("BACKLOG_GATE_GITHUB_REPO", "").replace("BACKLOG_GATE_GITHUB_BRANCH", "")


def test_a_plain_commit_or_an_unresolvable_one_is_read_as_itself_and_an_explicit_sha_wins():
    got = []
    reader = lambda repo, branch, sha, token: got.append(sha) or (True, "ok")                     # noqa: E731
    probe.probe_tests_pass("FND-1", evidence={"FND-1": "c" * 40}, git=fake_git(f"{'c' * 40} {'p' * 40}"), read=reader)
    probe.probe_tests_pass("FND-1", evidence={"FND-1": "c" * 40}, git=fake_git("", rc=128), read=reader)
    probe.probe_tests_pass("FND-1", sha="e" * 40, evidence={"FND-1": "c" * 40}, git=fake_git(f"{'e' * 40} {'p' * 40}"), read=reader)
    assert got == ["c" * 40, "c" * 40, "e" * 40]


def test_no_delivering_merge_is_not_yet_false_and_the_verdicts_map_to_the_three_answers():
    assert probe.probe_tests_pass("FND-1", evidence={}, read=lambda *a: (True, "x"))[0] is False and probe.probe_tests_pass("FND-1")[0] is False
    for answer in (True, False, None):
        got = probe.probe_tests_pass("FND-1", evidence={"FND-1": "c" * 40}, git=fake_git(f"{'c' * 40}"), read=lambda *a, _a=answer: (_a, "why"))
        assert got[0] is answer and got[1] == "tests_pass: why"


def test_the_kind_is_known_everywhere_it_must_be():
    assert "tests_pass" in probe.PROBE_KINDS
    src = (_ROOT / "scripts" / "audit" / "rfd_matrix_probe.py").read_text(encoding="utf-8")
    assert 'elif kind == "tests_pass":' in src and "probe_tests_pass(tid, *args, evidence=evidence)" in src
    ddl = (_ROOT / "db" / "pg_backlog" / "99_probes.sql").read_text(encoding="utf-8")
    assert "'tests_pass'" in ddl and "owner directive 2026-11-01" in ddl
    auto = (_ROOT / "scripts" / "audit" / "backlog_autopilot.py").read_text(encoding="utf-8")
    assert "probe.evaluate(wanted, evidence=" in auto
