# tests/test_backlog_modules.py
# -*- coding: utf-8 -*-
"""Backlog v3 W4b (FND-0360): the pure rules of shared/backlog_modules.py and the runner scripts/audit/backlog_modules.py with a fake client and a fake git. No database, no git, no network (R-7)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_modules as run  # noqa: E402
from shared import backlog_modules as bm  # noqa: E402

FILES = ["shared/backlog_client.py", "shared/config.py", "scripts/audit/audit_incident.py", "scripts/audit/backlog_links.py", "proyecto1/core/pipeline.py", "proyecto2/core/pipeline.py",
         "tests/test_backlog_client.py", "tests/test_audit_incident.py", "tests/test_pipeline.py", "tests/conftest.py", "AGENTS.md", "doc/reglas/NORMAS_BACKLOG.md", "db/pg_backlog/99_modules.sql",
         "scripts/ops/migrate_backlog_v2.py", "scripts/launch/run.bat", ".github/workflows/pg-tests.yml", "data/x.csv", "proyecto1/__init__.py"]
TRACKED, BY_BASE = bm.index_tracked(FILES)


def lm(path):
    return bm.logical_module(path, TRACKED, BY_BASE)


# ---------------------------------------------------------------- what a module is

@pytest.mark.parametrize("path, expected", [
    ("shared/backlog_client.py", "shared/backlog_client.py"), ("db/pg_backlog/99_modules.sql", "db/pg_backlog/99_modules.sql"), ("scripts/launch/run.bat", "scripts/launch/run.bat"),
    ("./shared/backlog_client.py", "shared/backlog_client.py"), ("shared\\backlog_client.py", "shared/backlog_client.py"),
    ("tests/test_backlog_client.py", "shared/backlog_client.py"), ("tests/test_audit_incident.py", "scripts/audit/audit_incident.py"),       # a test is the module it tests
    ("tests/test_pipeline.py", None),                                                                       # two files named pipeline.py: a test with no unique target stands for nothing
    ("tests/conftest.py", None), ("proyecto1/__init__.py", None),
    ("AGENTS.md", None), ("doc/reglas/NORMAS_BACKLOG.md", None), ("shared/config.py", None), ("scripts/ops/migrate_backlog_v2.py", None),     # registries and documents never form a cluster
    (".github/workflows/pg-tests.yml", None), ("data/x.csv", None), ("shared/deleted_long_ago.py", None)])                                 # not tracked, not code
def test_a_path_stands_for_a_module_or_for_none(path, expected):
    assert lm(path) == expected


def test_registries_are_recognised_by_prefix_name_and_suffix():
    assert all(bm.is_registry(p) for p in ("doc/x.py", "deployments/a/b.sql", "out/r.txt", "AGENTS.md", "shared/config.py", "notes.md", "a/b.yaml", ".github/x.py"))
    assert not any(bm.is_registry(p) for p in ("shared/backlog_client.py", "db/pg/35_control.sql", "scripts/ops/x.ps1"))


# ---------------------------------------------------------------- what text can claim

def test_citations_are_tracked_paths_or_unique_bare_names_normalised_and_nothing_else():
    text = ("fix shared/backlog_client.py and tests/test_backlog_client.py; see audit_incident.py and pipeline.py (ambiguous), AGENTS.md, doc/reglas/NORMAS_BACKLOG.md, "
            "shared\\config.py, nothing_here.py, https://example.com/page.md and db/pg_backlog/99_modules.sql.")
    assert bm.extract_citations(text, TRACKED, BY_BASE) == {"shared/backlog_client.py", "scripts/audit/audit_incident.py", "db/pg_backlog/99_modules.sql"}
    assert bm.extract_citations("", TRACKED, BY_BASE) == set() and bm.extract_citations(None, TRACKED, BY_BASE) == set()


def test_an_object_name_counts_only_when_it_is_a_tracked_file():
    assert bm.object_module("scripts/audit/audit_incident.py", TRACKED, BY_BASE) == "scripts/audit/audit_incident.py"
    assert bm.object_module("fund_master.Fund_Nature", TRACKED, BY_BASE) is None and bm.object_module("N/A", TRACKED, BY_BASE) is None and bm.object_module(None, TRACKED, BY_BASE) is None
    assert bm.object_module("a b.py", TRACKED, BY_BASE) is None


def test_text_proposes_only_cited_with_its_source_and_where_it_was_first_seen():
    t = {"title": "fix the parser in shared/backlog_client.py", "scenario_description": "also audit_incident.py", "object_name": "scripts/audit/backlog_links.py"}
    facts = bm.text_facts(t, ["a log naming proyecto1/core/pipeline.py and shared/backlog_client.py"], TRACKED, BY_BASE)
    assert {(f["module"], f["source"], f["evidence"]) for f in facts} == {
        ("shared/backlog_client.py", "TEXT", "named in the title"), ("scripts/audit/audit_incident.py", "TEXT", "named in the description"),
        ("proyecto1/core/pipeline.py", "TEXT", "named in a progress log"), ("scripts/audit/backlog_links.py", "OBJECT_NAME", "the ticket's object name")}
    assert all(f["role"] == "CITED" for f in facts)                                                       # a mention never makes a member


def test_commits_make_touched_per_module_with_the_first_commit_as_evidence_and_tests_fold_into_their_module():
    files = {"bbbbbbbbbbbbbbbb": ["shared/backlog_client.py", "doc/reglas/NORMAS_BACKLOG.md", "AGENTS.md"], "aaaaaaaaaaaaaaaa": ["tests/test_backlog_client.py", "scripts/audit/audit_incident.py", "tests/test_pipeline.py"]}
    got = {f["module"]: (f["role"], f["source"], f["evidence"]) for f in bm.commit_facts(files, TRACKED, BY_BASE)}
    assert got == {"shared/backlog_client.py": ("TOUCHED", "COMMIT", "commit aaaaaaaaaaaa"), "scripts/audit/audit_incident.py": ("TOUCHED", "COMMIT", "commit aaaaaaaaaaaa")}


def test_only_what_is_not_current_is_sent():
    f1 = {"module": "a.py", "role": "TOUCHED", "source": "COMMIT", "evidence": "x"}
    f2 = {"module": "b.py", "role": "CITED", "source": "TEXT", "evidence": "y"}
    current = {("FND-1", "a.py", None, "TOUCHED", "COMMIT")}
    assert bm.new_facts({"FND-1": [f1, f2], "FND-0": [f1]}, current) == [("FND-0", f1), ("FND-1", f2)]
    assert bm.new_facts({}, current) == [] and bm.new_facts({"FND-1": [f1]}, current) == []


# ---------------------------------------------------------------- the runner, with a fake client and a fake git

class FakeBc:
    def __init__(self, present=True, current=()):
        self.present, self.current, self.recorded = present, list(current), []

    def modules_store_present(self):
        return self.present

    def list_tickets(self, open_only=False):
        return [{"action_point_id": "FND-1", "title": "fix shared/backlog_client.py", "scenario_description": "", "object_name": None},
                {"action_point_id": "FND-2", "title": "nothing to see", "scenario_description": "", "object_name": None}]

    def list_logs(self, ids=None):
        return [{"action_point_id": "FND-2", "comment": "worked on scripts/audit/audit_incident.py"}]

    def ops_event_keys(self, kind):
        return {("c" * 40, "FND-1"), ("d" * 40, "FND-2"), ("e" * 40, "FND-3")}                           # FND-3 is not open; "e..." is not in the clone

    def list_ticket_modules(self):
        return self.current

    def record_modules(self, facts, actor=None):
        self.recorded += facts
        return {"recorded": len(facts) - 1, "unchanged": 1}


def fake_git(*args):
    if args[0] == "ls-files":
        return "\n".join(FILES)
    sha = args[-1]
    if sha == "e" * 40:
        raise RuntimeError("git diff-tree failed: bad object")
    return {"c" * 40: "shared/backlog_client.py\nAGENTS.md\n", "d" * 40: "tests/test_audit_incident.py\n"}[sha]


def test_the_runner_derives_text_and_commit_facts_for_open_tickets_and_a_dry_run_records_nothing():
    bc = FakeBc()
    res = run.run(bc, apply=False, git=fake_git, out=lambda *_: None)
    assert res["present"] and res["desired"] == 4 and res["new"] == 4 and res["recorded"] == 0 and bc.recorded == []
    res = run.run(bc, apply=True, git=fake_git, out=lambda *_: None)
    assert res["recorded"] == 3 and res["unchanged"] == 1
    assert {(f["ap_id"], f["module"], f["role"], f["source"]) for f in bc.recorded} == {
        ("FND-1", "shared/backlog_client.py", "CITED", "TEXT"), ("FND-1", "shared/backlog_client.py", "TOUCHED", "COMMIT"),
        ("FND-2", "scripts/audit/audit_incident.py", "CITED", "TEXT"), ("FND-2", "scripts/audit/audit_incident.py", "TOUCHED", "COMMIT")}
    assert all(set(f) == {"ap_id", "module", "role", "source", "evidence"} for f in bc.recorded)          # nothing else is sent: never an actor or a symbol the runner did not derive


def test_a_current_fact_is_not_sent_again_and_before_the_phase_the_runner_does_nothing():
    bc = FakeBc(current=[{"action_point_id": "FND-1", "module_path": "shared/backlog_client.py", "symbol": None, "role": "TOUCHED", "source": "COMMIT"}])
    res = run.run(bc, apply=True, git=fake_git, out=lambda *_: None)
    assert res["new"] == 3 and ("FND-1", "shared/backlog_client.py", "TOUCHED", "COMMIT") not in {(f["ap_id"], f["module"], f["role"], f["source"]) for f in bc.recorded}
    early = FakeBc(present=False)
    assert run.run(early, apply=True, git=fake_git, out=lambda *_: None) == {"present": False, "desired": 0, "new": 0, "recorded": 0, "unchanged": 0} and early.recorded == []


def test_a_commit_that_is_not_in_the_clone_is_skipped_not_fatal():
    assert run.commit_files(["c" * 40, "e" * 40], git=fake_git) == {"c" * 40: ["shared/backlog_client.py", "AGENTS.md"]}


def test_the_autopilot_run_calls_the_modules_step_last_best_effort_and_the_actor_is_the_autopilots():
    src = (_ROOT / "scripts" / "audit" / "backlog_autopilot.py").read_text(encoding="utf-8")
    block = src.split("import backlog_modules as modules_run")[1].split("import backlog_digest")[0]
    assert "modules_run.run(bc, apply=True" in block and "except Exception" in block
    assert run.ACTOR == "system:autopilot"


def test_clean_path_strips_only_a_leading_dot_slash_never_the_dot_of_a_hidden_directory():
    assert bm.clean_path("./a/b.py") == "a/b.py" and bm.clean_path("././a.py") == "a.py" and bm.clean_path("a" + chr(92) + "b.py") == "a/b.py"
    assert bm.clean_path(".github/workflows/x.yml") == ".github/workflows/x.yml" and bm.clean_path("../x.py") == "../x.py" and bm.is_registry(".github/x.py")
