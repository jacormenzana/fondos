# tests/test_open_pr_request.py
# -*- coding: utf-8 -*-
"""Acceptance 2 (classification part), 6 (agent self-check) and 10 of CICD_SERVICE_ACCOUNT_SPEC.md (FND-0353) for the agent side open_pr_request.py: a request goes into the inbox, the
ticket log is written ONLY after a receipt that GitHub itself confirms, exactly once. Written BEFORE the code (owner order, 2026-10-30). The runner is a fake that drops receipt files.

Contract under test (scripts/ops/open_pr_request.py):
    new_request(branch, ticket, title, body) -> dict                                      v=1, a fresh uuid4
    write_request(inbox: Path, req: dict, schema: dict) -> Path                            validates against the locked request schema, exclusive create <id>.json
    read_receipt(outbox: Path, request_id: str, schema: dict) -> dict | None               None when absent; ReceiptInvalid when it does not match the receipt schema
    confirm_pull(receipt: dict, fetch_pull) -> tuple[bool, str]                            GitHub is the source of truth
    log_if_confirmed(ticket, request_id, outbox, state_dir, sink, fetch_pull, schema) -> str   'LOGGED' | 'ALREADY_LOGGED' | 'NOT_LOGGED: <why>'
    reconcile(outbox, state_dir, sink, fetch_pull, schema, ticket_of) -> list[str]
    check_isolation(try_read_key, try_list_dir, uac: dict) -> tuple[bool, list[str]]
    sink.existing_logs(ticket) -> list[str];  sink.append_log(ticket, text) -> None;  fetch_pull(number) -> dict (the GitHub pull-request object)
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

_OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
if str(_OPS) not in sys.path:
    sys.path.insert(0, str(_OPS))

import open_pr_request as rq  # noqa: E402
import open_pr_schema as sch  # noqa: E402

REQ_SCHEMA = json.loads((_OPS / "open_pr_request.schema.json").read_text(encoding="utf-8"))
RCP_SCHEMA = json.loads((_OPS / "open_pr_receipt.schema.json").read_text(encoding="utf-8"))
BOT = "fondos-pr-opener[bot]"
SHA = "a" * 40


class Sink:
    def __init__(self):
        self.logs: dict = {}

    def existing_logs(self, ticket):
        return list(self.logs.get(ticket, []))

    def append_log(self, ticket, text):
        self.logs.setdefault(ticket, []).append(text)


def pull(**over):
    p = {"number": 7, "state": "open", "html_url": "https://github.com/jacormenzana/fondos/pull/7", "head": {"sha": SHA}, "base": {"ref": "master"}, "user": {"login": BOT}}
    p.update(over)
    return p


def opened(rid, **over):
    r = {"v": 1, "request_id": rid, "status": "PR_OPENED", "pr_number": 7, "pr_url": "https://github.com/jacormenzana/fondos/pull/7", "head_sha": SHA, "base": "master",
         "opened_at": "2026-10-30T16:00:00Z"}
    r.update(over)
    return r


def drop(outbox, rid, receipt):
    (outbox / f"{rid}.result.json").write_text(receipt if isinstance(receipt, str) else json.dumps(receipt), encoding="utf-8")


@pytest.fixture
def box(tmp_path):
    outbox, state = tmp_path / "outbox", tmp_path / "state"
    outbox.mkdir(); state.mkdir()
    return outbox, state, Sink(), str(uuid.uuid4())


# --- writing the request ------------------------------------------------------------------------------------------------------------------------

def test_a_request_is_built_checked_against_the_locked_schema_and_written_once(tmp_path):
    inbox = tmp_path / "inbox"; inbox.mkdir()
    req = rq.new_request("feat/x", "FND-0353", "docs(backlog): FND-0353 - a title", "Body\n")
    assert req["v"] == 1 and sch.validate(req, REQ_SCHEMA) == []
    path = rq.write_request(inbox, req, REQ_SCHEMA)
    assert path == inbox / f"{req['id']}.json" and json.loads(path.read_text(encoding="utf-8")) == req
    with pytest.raises(FileExistsError):
        rq.write_request(inbox, req, REQ_SCHEMA)                                   # exclusive create: never overwrites
    assert rq.new_request("feat/x", "FND-0353", "t", "b")["id"] != req["id"]


@pytest.mark.parametrize("kwargs", [dict(branch="a;b"), dict(ticket="FND-1"), dict(title="a|b"), dict(body="nul\x00")])
def test_a_request_the_runner_would_quarantine_is_never_written(tmp_path, kwargs):
    inbox = tmp_path / "inbox"; inbox.mkdir()
    base = dict(branch="feat/x", ticket="FND-0353", title="a title", body="b")
    base.update(kwargs)
    req = rq.new_request(**base)
    with pytest.raises(ValueError):
        rq.write_request(inbox, req, REQ_SCHEMA)
    assert list(inbox.iterdir()) == []


# --- receipts: nothing is logged unless GitHub confirms (acceptance 10) ----------------------------------------------------------------------------

@pytest.mark.parametrize("receipt", [
    lambda rid: {"v": 1, "request_id": rid, "status": "REFUSED", "reason": "no commits ahead of master"},
    lambda rid: {"v": 1, "request_id": rid, "status": "REJECTED_SECURITY", "reason": "schema: unexpected key"},
    lambda rid: {"v": 1, "request_id": rid, "status": "FAILED", "reason": "GitHub refused"},
])
def test_refused_rejected_and_failed_never_produce_a_database_log(box, receipt):
    outbox, state, sink, rid = box
    drop(outbox, rid, receipt(rid))
    fetched = []
    out = rq.log_if_confirmed("FND-0353", rid, outbox, state, sink, lambda n: fetched.append(n) or pull(), RCP_SCHEMA)
    assert out.startswith("NOT_LOGGED: status ") and sink.logs == {} and fetched == []         # refused on the status itself, and not even a GitHub call: there is no pull request to confirm


def test_no_receipt_yet_a_timeout_is_not_logged(box):
    outbox, state, sink, rid = box
    assert rq.read_receipt(outbox, rid, RCP_SCHEMA) is None
    assert rq.log_if_confirmed("FND-0353", rid, outbox, state, sink, lambda n: pull(), RCP_SCHEMA).startswith("NOT_LOGGED") and sink.logs == {}


@pytest.mark.parametrize("content", ["{not json", "[]", '{"v": 1, "v": 1}', json.dumps(opened("x")), "", json.dumps({**opened("00000000-0000-4000-8000-000000000000"), "extra": 1})])
def test_a_malformed_receipt_is_never_trusted(box, content):
    outbox, state, sink, rid = box
    drop(outbox, rid, content)
    with pytest.raises(rq.ReceiptInvalid):
        rq.read_receipt(outbox, rid, RCP_SCHEMA)
    assert rq.log_if_confirmed("FND-0353", rid, outbox, state, sink, lambda n: pull(), RCP_SCHEMA).startswith("NOT_LOGGED") and sink.logs == {}


def test_a_receipt_for_another_request_is_not_accepted(box):
    outbox, state, sink, rid = box
    drop(outbox, rid, opened(str(uuid.uuid4())))
    assert rq.log_if_confirmed("FND-0353", rid, outbox, state, sink, lambda n: pull(), RCP_SCHEMA).startswith("NOT_LOGGED") and sink.logs == {}


@pytest.mark.parametrize("fetched,why", [
    (pull(head={"sha": "b" * 40}), "head"),
    (pull(state="closed"), "open"),
    (pull(user={"login": "jacormenzana"}), "author"),
    (pull(html_url="https://github.com/jacormenzana/fondos/pull/8"), "url"),
    (pull(base={"ref": "main"}), "base"),
])
def test_a_pull_request_that_github_does_not_confirm_is_never_logged(box, fetched, why):
    outbox, state, sink, rid = box
    drop(outbox, rid, opened(rid))
    out = rq.log_if_confirmed("FND-0353", rid, outbox, state, sink, lambda n: fetched, RCP_SCHEMA)
    assert out.startswith("NOT_LOGGED") and why in out and sink.logs == {}


def test_an_unreachable_github_waits_it_never_logs_on_trust(box):
    outbox, state, sink, rid = box
    drop(outbox, rid, opened(rid))

    def down(n):
        raise ConnectionError("no route")
    out = rq.log_if_confirmed("FND-0353", rid, outbox, state, sink, down, RCP_SCHEMA)
    assert out.startswith("NOT_LOGGED") and "unreachable" in out and sink.logs == {} and not (state / f"{rid}.logged").exists()


def test_a_confirmed_pull_request_is_logged_exactly_once_with_the_request_id(box):
    outbox, state, sink, rid = box
    drop(outbox, rid, opened(rid))
    assert rq.log_if_confirmed("FND-0353", rid, outbox, state, sink, lambda n: pull(), RCP_SCHEMA) == "LOGGED"
    assert len(sink.logs["FND-0353"]) == 1 and rid in sink.logs["FND-0353"][0] and "pull/7" in sink.logs["FND-0353"][0]
    assert (state / f"{rid}.logged").exists()
    assert rq.log_if_confirmed("FND-0353", rid, outbox, state, sink, lambda n: pull(), RCP_SCHEMA) == "ALREADY_LOGGED"
    assert len(sink.logs["FND-0353"]) == 1


def test_reconcile_writes_the_log_a_crash_left_missing_and_never_a_duplicate(tmp_path, box):
    outbox, state, sink, rid = box
    rid2, rid3 = str(uuid.uuid4()), str(uuid.uuid4())
    drop(outbox, rid, opened(rid))                                                          # crash between the pull request and the log: no log, no marker
    drop(outbox, rid2, opened(rid2, pr_number=8, pr_url="https://github.com/jacormenzana/fondos/pull/8"))
    sink.append_log("FND-0353", f"Pull request #8 opened ... (request {rid2})")             # crash between the log and the marker: log exists, no marker
    drop(outbox, rid3, {"v": 1, "request_id": rid3, "status": "REFUSED", "reason": "x"})
    fetch = lambda n: pull(number=n, html_url=f"https://github.com/jacormenzana/fondos/pull/{n}")       # noqa: E731
    done = rq.reconcile(outbox, state, sink, fetch, RCP_SCHEMA, lambda request_id: "FND-0353")
    assert sorted(done) == sorted([f"{rid}: LOGGED", f"{rid2}: ALREADY_LOGGED"])
    assert len([t for t in sink.logs["FND-0353"] if rid2 in t]) == 1 and len([t for t in sink.logs["FND-0353"] if rid in t]) == 1
    assert (state / f"{rid}.logged").exists() and (state / f"{rid2}.logged").exists() and not (state / f"{rid3}.logged").exists()
    assert rq.reconcile(outbox, state, sink, fetch, RCP_SCHEMA, lambda request_id: "FND-0353") == []        # a second pass changes nothing


# --- the isolation self-check (acceptance 2) ------------------------------------------------------------------------------------------------

GOOD_UAC = {"EnableLUA": 1, "ConsentPromptBehaviorAdmin": 1, "PromptOnSecureDesktop": 1}


def deny(_):
    raise PermissionError("Access is denied")


def missing(_):
    raise FileNotFoundError("no such file")


def test_isolation_passes_only_on_access_denied_with_the_three_uac_values():
    ok, lines = rq.check_isolation(deny, deny, GOOD_UAC)
    assert ok is True
    assert rq.check_isolation(lambda p: b"key", deny, GOOD_UAC)[0] is False                  # the key was readable
    assert rq.check_isolation(deny, lambda p: ["file"], GOOD_UAC)[0] is False                 # the directory was listable
    assert rq.check_isolation(missing, deny, GOOD_UAC)[0] is False                             # a missing file is not a denial
    for k in GOOD_UAC:
        assert rq.check_isolation(deny, deny, {**GOOD_UAC, k: 0})[0] is False
    assert rq.check_isolation(deny, deny, {k: v for k, v in GOOD_UAC.items() if k != "EnableLUA"})[0] is False
