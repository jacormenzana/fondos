# tests/test_backlog_overlap.py
# -*- coding: utf-8 -*-
"""shared/backlog_overlap.py (text similarity + the two-tier incident policy) and its calibration fixture.

Pure: no database, no pipeline import (R-7). The behaviour of report_incident against the real DDL is in
test_backlog_incident_overlap_pg.py; the historical replay is `scripts/audit/backlog_links_calibration.py`.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared import backlog_overlap as O  # noqa: E402
from shared import config  # noqa: E402
import backlog_links_calibration as C  # noqa: E402

FIXTURE = json.loads((_ROOT / "tests" / "fixtures" / "backlog_incident_cases.json").read_text(encoding="utf-8"))


# --- similarity ---------------------------------------------------------------------------------------------------------

def test_tokens_drop_stopwords_short_words_and_bare_numbers():
    assert O.tokens("The RC=2 failure in run_pipeline after 120 seconds") == ["failure", "run_pipeline", "after", "seconds"]


def test_cosine_to_query_ranks_the_closer_document_first_and_unshared_terms_lower_it():
    docs = {"A": "psycopg undefined column real_flag in fund_metric_timeseries writer",
            "B": "morningstar chart token expired during nav load"}
    cos = O.cosine_to_query("undefined column real_flag fund_metric_timeseries", docs)
    assert cos["A"] > 0.5 > cos["B"] == 0.0
    longer = O.cosine_to_query("undefined column real_flag fund_metric_timeseries plus many other unrelated words here", docs)
    assert longer["A"] < cos["A"]                       # a text with much unshared content is less similar


def test_pairwise_cosine_matches_the_query_form():
    docs = {"A": "alpha beta gamma delta", "B": "alpha beta gamma epsilon", "C": "zeta eta theta iota"}
    pairs = O.tfidf_cosine(docs, min_cos=0.0)
    assert pairs[("A", "B")] > 0.4 and ("A", "C") not in pairs and ("B", "C") not in pairs


def test_exit_codes_are_identity():
    assert O.exit_codes("failed with RC=101 after 4s") == {"101"}
    assert O.exit_codes("process exited with code 3") == {"3"}
    assert O.exit_codes("no code here") == set()


# --- policy -------------------------------------------------------------------------------------------------------------

HIGH, MEDIUM = 0.70, 0.45


def _tier(case):
    return O.classify_incident(scenario=case["incident"]["scenario"], object_name=case["incident"]["object_name"],
                               candidates=case["open"], high_cosine=HIGH, medium_cosine=MEDIUM)


def test_no_candidates_is_none():
    assert O.classify_incident(scenario="x", object_name="y", candidates=[], high_cosine=HIGH, medium_cosine=MEDIUM)["tier"] == "NONE"


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[c["name"] for c in FIXTURE["cases"]])
def test_synthetic_fixture_case(case):
    res = _tier(case)
    if case.get("never_high"):
        assert res["tier"] != "HIGH", f"false merge into {res['ticket']}: {res['reason']}"
    if "expect" in case:
        assert res["tier"] == case["expect"], res


def test_released_thresholds_pass_the_calibration_gate_on_the_fixture():
    # the switch-on gate (D-1) for part (b): the thresholds in shared/config.py, not the local test constants
    assert C.run_fixture(FIXTURE["cases"], high=config.BACKLOG_INCIDENT_HIGH_COSINE,
                         medium=config.BACKLOG_INCIDENT_MEDIUM_COSINE) == []


def test_the_switch_ships_off():
    assert config.BACKLOG_INCIDENT_AUTO_APPEND_ENABLED is False


# --- every incident reporter has a fixture case (enforced, not only listed) -------------------------------------------------

_REPORTERS = {"report_incident", "capture_exceptions", "install_excepthook"}
_SKIP_DIRS = {"tests", ".claude", ".git", "__pycache__", "out", "node_modules", "venv", ".venv"}


def _constant_prefix(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):                       # f"P1_P2_Complete.bat PASO {step} ..." -> the text before the first {}
        head = ""
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                head += part.value
            else:
                break
        return head or None
    return None


def _reporter_object_names() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for top in ("shared", "proyecto1", "proyecto2", "proyecto3", "scripts"):
        for path in (_ROOT / top).rglob("*.py"):
            if _SKIP_DIRS & set(path.relative_to(_ROOT).parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                if fn not in _REPORTERS:
                    continue
                for kw in node.keywords:
                    if kw.arg == "object_name":
                        name = _constant_prefix(kw.value)
                        if name:
                            found.setdefault(name, []).append(str(path.relative_to(_ROOT)).replace("\\", "/"))
    return found


def test_every_incident_reporter_has_a_fixture_case():
    prefixes = FIXTURE["object_name_prefixes"]
    found = _reporter_object_names()
    assert found, "the AST scan found no reporter at all: the scan itself is broken"
    uncovered = {n: f for n, f in found.items() if not any(n.startswith(p) for p in prefixes)}
    assert not uncovered, (
        "a component reports incidents automatically but has no case in tests/fixtures/backlog_incident_cases.json "
        "(add a hard-negative/positive case and list its object_name in object_name_prefixes): " + repr(uncovered))


def test_fixture_prefixes_do_not_rot():
    found = _reporter_object_names()
    stale = [p for p in FIXTURE["object_name_prefixes"] if not any(n.startswith(p) for n in found)]
    assert not stale, f"fixture lists reporters that no longer exist: {stale}"
