# tests/test_receipt_scope_control_fnd0388.py
# -*- coding: utf-8 -*-
"""FND-0388, control T-RECEIPT-SCOPE (owner ruling 2026-11-15): a CODE_FIX / DATA_FIX closure needs a receipt whose scope covers every changed path and that ran at least one test; a documents-only delivery
is not a code or data fix. The pure rule (shared/delivery_receipt.closure_blockers), its two consumers (the autopilot decision and the autopilot's fact collector; close_release's gate) and the catalogue entry.
The incident: FND-0280 was closed as CODE_FIX with the receipt `0 passed ... scope docs_only`. R-7: no database, no git, no network."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "gov" / "backlog")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared import backlog_autopilot as ap  # noqa: E402
from shared import delivery_receipt as dr  # noqa: E402
from shared.governance import controls as gc  # noqa: E402
import backlog_autopilot as script  # noqa: E402
import close_release as cr  # noqa: E402

SHA = "a" * 40
TREE = "b" * 40
CODE = ["shared/x.py"]
DOCS = ["doc/backlog/NOTE.md", "out/audit/x.json"]


def receipt(scope="full", passed=10, result="pass", covers=(SHA,), tree=TREE, sha=SHA, tamper=False):
    r = dr.build_receipt(sha=sha, tree=tree, base="c" * 40, covers=list(covers), changed=CODE, required="full", run_scope=scope, files=[], counts={"passed": passed, "failed": 0, "errors": 0, "skipped": 0},
                         rc=0, seconds=1.0, python="py", command=[], result=result, detail="d", ts="2026-11-15T00:00:00+00:00")
    if tamper:
        r["seconds"] = 999.0
    return r


def tree_of(sha):
    return TREE


def blockers(solution="CODE_FIX", paths=CODE, receipts=None, commit=SHA, tree=tree_of):
    return dr.closure_blockers(solution, commit, paths, [receipt()] if receipts is None else receipts, tree)


# ---- the pure rule ----------------------------------------------------------------------------------------------------------------------------------
def test_a_full_receipt_that_ran_tests_vouches_for_a_code_fix():
    assert blockers() == []
    assert blockers("DATA_FIX") == []


def test_a_documents_only_delivery_is_not_a_code_fix_the_fnd_0280_incident():
    why = blockers("CODE_FIX", DOCS, [receipt("docs_only", passed=0)])
    assert len(why) == 1 and "documents or records" in why[0]
    assert blockers("DATA_FIX", DOCS) != []


@pytest.mark.parametrize("solution", ["WORKAROUND", "REJECTED", "DUPLICATED", None, "CODE_FIX/DATA_FIX"])
def test_other_solutions_are_not_decided_here(solution):
    assert blockers(solution, DOCS, []) == []


def test_no_receipt_for_the_commit_blocks():
    assert "no local test receipt covers" in blockers(receipts=[])[0]
    assert "no local test receipt covers" in blockers(receipts=[receipt(covers=("d" * 40,))])[0]


def test_a_scope_weaker_than_the_changed_paths_need_blocks_even_if_the_receipt_says_it_required_less():
    why = blockers(receipts=[receipt("area")])                         # shared/ needs full; the receipt's own `required` field says full too, but the rule recomputes it from the paths
    assert "weaker than the full" in why[0]
    assert "weaker than the full" in blockers(receipts=[receipt("docs_only")])[0]


def test_an_area_receipt_is_enough_for_a_change_outside_the_runtime_prefixes():
    assert blockers(paths=["scripts/gov/x.py"], receipts=[receipt("area")]) == []


def test_an_impact_receipt_stands_for_full():
    assert blockers(receipts=[receipt("impact")]) == []


def test_a_receipt_that_ran_no_test_blocks_even_at_full_scope():
    assert "ran no test" in blockers(receipts=[receipt("full", passed=0)])[0]


@pytest.mark.parametrize("rec,text", [(receipt(result="fail"), "is fail"), (receipt(tamper=True), "digest"), (receipt(tree="e" * 40), "no longer gives tree")])
def test_a_failed_tampered_or_stale_receipt_blocks(rec, text):
    assert text in blockers(receipts=[rec])[0]


def test_an_older_passing_receipt_stands_when_a_newer_one_failed():
    assert blockers(receipts=[receipt(), receipt(result="fail")]) == []            # same rule as `evaluate`: any intact passing receipt for the commit


# ---- the autopilot ----------------------------------------------------------------------------------------------------------------------------------
def _facts(receipt_blockers=None, solution=None):
    m = ap.Merge(ref=SHA, pr=None, paths=tuple(CODE), year=2026, month=11, solution=solution)
    return ap.Facts(tickets={"FND-0001": {"status": "READY_FOR_DEPLOY", "release_version": None}}, merges={"FND-0001": [m]}, receipt_blockers=receipt_blockers or {})


def test_the_decision_waits_with_the_control_named_when_the_receipt_does_not_vouch():
    d = ap.decide_one("FND-0001", _facts({"FND-0001": ["the receipt ran no test"]}))
    assert d.action == ap.WAIT and any("T-RECEIPT-SCOPE" in r and "ran no test" in r for r in d.reasons)


def test_the_decision_closes_when_nothing_blocks():
    assert ap.decide_one("FND-0001", _facts()).action == ap.CLOSE


def _cands(paths=CODE, solution=None, status="READY_FOR_DEPLOY"):
    m = ap.Merge(ref=SHA, pr=None, paths=tuple(paths), year=2026, month=11, solution=solution)
    return {"FND-0001": [({"sha": SHA}, m)]}, {"FND-0001": {"status": status}}


def test_the_collector_reads_the_receipts_and_reports_the_blockers_of_a_ready_ticket():
    c, t = _cands(DOCS)
    out = script._receipt_blockers(c, t, SimpleNamespace(BACKLOG_GITHUB_EVIDENCE_ENABLED=False), read=lambda: [], tree_of=tree_of)
    assert list(out) == ["FND-0001"] and "documents or records" in out["FND-0001"][0]
    c, t = _cands(CODE)
    assert script._receipt_blockers(c, t, SimpleNamespace(BACKLOG_GITHUB_EVIDENCE_ENABLED=False), read=lambda: [receipt()], tree_of=tree_of) == {}


def test_the_collector_is_silent_in_github_mode_and_for_tickets_not_ready():
    c, t = _cands(DOCS)
    assert script._receipt_blockers(c, t, SimpleNamespace(BACKLOG_GITHUB_EVIDENCE_ENABLED=True), read=lambda: [], tree_of=tree_of) == {}
    c, t = _cands(DOCS, status="IN_PROGRESS")
    assert script._receipt_blockers(c, t, SimpleNamespace(BACKLOG_GITHUB_EVIDENCE_ENABLED=False), read=lambda: [], tree_of=tree_of) == {}


def test_unreadable_receipts_fail_closed_for_a_code_fix_only():
    def boom():
        raise OSError("no file")
    c, t = _cands(CODE)
    out = script._receipt_blockers(c, t, SimpleNamespace(BACKLOG_GITHUB_EVIDENCE_ENABLED=False), read=boom, tree_of=tree_of)
    assert "could not be read" in out["FND-0001"][0]
    c, t = _cands(CODE, solution="WORKAROUND")
    assert script._receipt_blockers(c, t, SimpleNamespace(BACKLOG_GITHUB_EVIDENCE_ENABLED=False), read=boom, tree_of=tree_of) == {}


# ---- close_release ----------------------------------------------------------------------------------------------------------------------------------
LOG = (f"{'1' * 40}\x1fdocs: handoff\n\nRefs: FND-0001\x1e"
       f"{'2' * 40}\x1ffeat: the code\n\nRefs: FND-0001\nCo-Authored-By: x\x1e"
       f"{'3' * 40}\x1funrelated\n\nRefs: FND-0009\x1e")
FILES = {"1" * 40: ["doc/backlog/H.md"], "2" * 40: ["shared/x.py"], "3" * 40: ["shared/y.py"]}


def test_the_delivering_commit_is_the_newest_citing_commit_that_changed_code():
    f = cr.delivery_facts(LOG, ["FND-0001", "FND-0002"], FILES.get)
    assert f["FND-0001"] == ("2" * 40, ["doc/backlog/H.md", "shared/x.py"]) and "FND-0002" not in f


def test_a_ticket_cited_only_by_documents_has_no_delivering_commit():
    f = cr.delivery_facts(LOG, ["FND-0001"], lambda sha: ["doc/backlog/H.md"])
    assert f["FND-0001"][0] is None
    why = cr.receipt_scope_gate(["FND-0001"], f, [], tree_of)
    assert len(why) == 1 and why[0].startswith("FND-0001: ") and "documents or records" in why[0]


def test_close_release_refuses_without_a_covering_receipt_and_accepts_with_one():
    f = cr.delivery_facts(LOG, ["FND-0001"], FILES.get)
    assert "no local test receipt covers" in cr.receipt_scope_gate(["FND-0001"], f, [receipt()], tree_of)[0]
    ok = receipt(covers=("2" * 40,), sha="2" * 40)
    assert cr.receipt_scope_gate(["FND-0001"], f, [ok], tree_of) == []


def test_a_ticket_no_commit_cites_is_not_evaluated_by_close_release():
    assert cr.receipt_scope_gate(["FND-0002"], cr.delivery_facts(LOG, ["FND-0002"], FILES.get), [], tree_of) == []


# ---- the catalogue ----------------------------------------------------------------------------------------------------------------------------------
def test_the_control_is_registered_enforced_and_blocking():
    c = gc.CONTROLS_BY_ID["T-RECEIPT-SCOPE"]
    assert c.pillar == gc.PILLAR_TECHNICAL and c.enforced and c.consequence == gc.BLOCK and c.deploy_consequence is None
    assert c.source == "CODE:shared.delivery_receipt.closure_blockers"
