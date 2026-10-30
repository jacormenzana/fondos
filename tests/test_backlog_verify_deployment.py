# tests/test_backlog_verify_deployment.py
# -*- coding: utf-8 -*-
"""Zero-input verification of the Backlog v2 deployment (scripts/audit/backlog_verify_deployment.py): nobody supplies an exit code, the schema is read and judged.
Pure parts here; the hermetic PostgreSQL part is tests/test_backlog_verify_deployment_pg.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_verify_deployment as vd  # noqa: E402


def test_the_newest_baseline_is_found_by_its_stamp_and_none_when_there_is_none(tmp_path):
    assert vd.newest_baseline(tmp_path) is None and vd.newest_baseline(tmp_path / "missing") is None
    for stamp in ("20261029T224146Z", "20261028T212330Z", "20261030T081500Z"):
        (tmp_path / f"fnd0331_baseline_{stamp}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "fnd0331_acceptance_iam_20261031T000000Z.txt").write_text("x", encoding="utf-8")       # other files in the directory are not baselines
    assert vd.newest_baseline(tmp_path).name == "fnd0331_baseline_20261030T081500Z.json"


def test_the_missing_exit_code_criterion_becomes_an_explicit_not_needed_and_only_that_one():
    results = [("migration exit code is 0", False, "reported rc=1"), ("something else", False, "kept as it is"), ("migration exit code mentioned later", True, "x")]
    out = vd.drop_exit_code_criterion(results)
    assert out[0] == vd.NOT_NEEDED and out[0][1] is True and out[1:] == results[1:]
    assert vd.drop_exit_code_criterion([("only the state", True, "d")]) == [("only the state", True, "d")]          # an evaluator without that criterion is untouched
    assert vd.drop_exit_code_criterion([]) == []


def test_render_says_verified_only_when_every_criterion_passes():
    ok = [("target", "a", True, "d"), ("iam", "b", True, "d")]
    bad = ok + [("relations", "c", False, "the override column is missing")]
    assert vd.render(ok, ["a line"], ["HEADER"]).splitlines()[-1] == "RESULT: DEPLOYED AND VERIFIED (all criteria met)"
    text = vd.render(bad, [], ["HEADER"])
    assert text.splitlines()[0] == "HEADER" and "FAIL - [relations] c: the override column is missing" in text and text.splitlines()[-1] == "RESULT: NOT VERIFIED"
    assert "INFO - a line" in vd.render(ok, ["a line"], ["HEADER"])


def test_the_command_line_needs_a_baseline_and_says_how_to_get_one(tmp_path, capsys):
    assert vd.main(["--baseline-dir", str(tmp_path)]) == vd.RC_USAGE
    assert "no baseline found" in capsys.readouterr().err
    assert vd.main(["--baseline-file", str(tmp_path / "nope.json")]) == vd.RC_USAGE


def test_the_defaults_are_the_production_target_and_the_repo_directories():
    assert vd.PRODUCTION_TARGET == "127.0.0.1:5436/gestion"
    assert vd.DEFAULT_RECORD_DIR == _ROOT / "deployments" and vd.DEFAULT_BASELINE_DIR == _ROOT / "out" / "audit"


def test_no_connection_string_in_the_environment_is_a_usage_error_not_a_crash(tmp_path, monkeypatch, capsys):
    baseline = tmp_path / "fnd0331_baseline_20261029T224146Z.json"
    baseline.write_text(json.dumps({"max_log_id": 1, "max_event_id": 1, "logs": 1, "logs_md5": "a", "events": 1, "events_md5": "b"}), encoding="utf-8")
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN", raising=False)
    monkeypatch.setenv("FONDOS_ENV_FILE", str(tmp_path / "no.env"))
    from shared import backlog_client as bc
    monkeypatch.setattr(bc, "_dsn", lambda: None)
    assert vd.main(["--baseline-file", str(baseline), "--record-dir", ""]) == vd.RC_USAGE
    assert "FONDOS_BACKLOG_PG_DSN is not set" in capsys.readouterr().err
