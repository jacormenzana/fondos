# tests/test_open_pr_schema.py
# -*- coding: utf-8 -*-
"""Acceptance 6 and 13 of CICD_SERVICE_ACCOUNT_SPEC.md (FND-0353): the inbox request is untrusted input. Written BEFORE the code (owner order, 2026-10-30): the tests state what the
specification demands; open_pr_schema.py / open_pr.py are what has to satisfy them.

Contract under test (scripts/ops/open_pr_schema.py and open_pr.py):
    load_strict_json(raw: bytes) -> object          UTF-8 only, no BOM, no duplicate key, no NaN/Infinity; raises StrictJsonError
    validate(instance, schema: dict) -> list[str]   [] when valid, one message per violation; a small interpreter of the schema subset the two locked schemas use
    open_pr.validate_request(raw: bytes, filename: str, seen_ids: set, schema: dict) -> dict     raises open_pr.HardFail(rule) on ANY deviation
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

import open_pr  # noqa: E402
import open_pr_schema as sch  # noqa: E402

REQUEST_SCHEMA = json.loads((_OPS / "open_pr_request.schema.json").read_text(encoding="utf-8"))
RECEIPT_SCHEMA = json.loads((_OPS / "open_pr_receipt.schema.json").read_text(encoding="utf-8"))


def good(**over):
    req = {"v": 1, "id": str(uuid.uuid4()), "branch": "chore/ci-credential-spec-final", "ticket": "FND-0353", "title": "docs(backlog): FND-0353 - first pull request",
           "body": "Body with `backticks`, $dollars; semicolons | pipes & ampersands\nand a second line\tand a tab.\n"}
    req.update(over)
    return req


def raw(req, **kw):
    return json.dumps(req, **kw).encode("utf-8")


def check(req_or_raw, *, name=None, seen=()):
    data = req_or_raw if isinstance(req_or_raw, bytes) else raw(req_or_raw)
    if name is None:
        name = json.loads(data.decode("utf-8", "replace")).get("id", "x") + ".json" if data.startswith(b"{") else "x.json"
    return open_pr.validate_request(data, name, set(seen), REQUEST_SCHEMA)


def rejected(req_or_raw, **kw):
    with pytest.raises(open_pr.HardFail) as e:
        check(req_or_raw, **kw)
    return e.value.rule


# --- a well-formed request passes every step --------------------------------------------------------------------------------------------------

def test_a_well_formed_request_passes_and_keeps_its_markdown_body():
    r = good()
    assert check(r) == r
    assert check(good(body="")) ["body"] == ""


# --- strict JSON --------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("data", [
    b"\xef\xbb\xbf" + raw(good()),                                         # BOM
    raw(good()).replace(b"FND-0353", b"FND-\xff353"),                      # invalid UTF-8
    b'{"v": 1, "v": 1, "id": "x"}',                                        # duplicate key
    b'{"v": NaN}', b'{"v": Infinity}', b'{"v": -Infinity}',
    b"", b"[]", b"null", b"{", b'"a string"',
])
def test_strict_json_failures_are_hard_fails(data):
    rule = rejected(data, name=str(uuid.uuid4()) + ".json")
    assert "strict json" in rule or "schema" in rule                       # a valid file name: the failure is in the content, not the name


def test_load_strict_json_names_its_failure():
    for bad in (b"\xef\xbb\xbf{}", b'{"a":1,"a":2}', b"NaN", b"\xff"):
        with pytest.raises(sch.StrictJsonError):
            sch.load_strict_json(bad)
    assert sch.load_strict_json(b'{"a": [1, 2.5, "x"]}') == {"a": [1, 2.5, "x"]}


# --- structure ----------------------------------------------------------------------------------------------------------------------------------

def test_extra_missing_and_wrongly_typed_keys_are_hard_fails():
    base = good()
    rejected({**base, "extra": 1})                                             # extra key at the top level
    rejected({**base, "title": {"nested": "object", "extra": True}})           # an object where a string belongs (nested position)
    for key in ("v", "id", "branch", "ticket", "title", "body"):
        rejected({k: v for k, v in base.items() if k != key}, name=base["id"] + ".json")
    for key, wrong in (("v", "1"), ("v", 1.0), ("v", True), ("v", 2), ("id", 5), ("branch", ["a"]), ("ticket", 353), ("title", None), ("body", 12)):
        rejected({**base, key: wrong}, name=base["id"] + ".json")


@pytest.mark.parametrize("branch", [
    "a/../b", "a//b", "-leading-dash", "x@{1}", "topic.lock", "has space", "a;b", "$(id)", "`id`", "a\nb", "a|b", "a&b", "a>b", "a\\b", "a'b", 'a"b', "a*b", "a?b", "a~b", "a^b", "a:b", "",
    "x" * 101,
    "ｍａｓｔｅｒ",           # full-width Latin look-alike
    "mаster",                 # Cyrillic a
    "ma​ster",           # zero-width space
])
def test_a_branch_outside_the_git_safe_alphabet_is_a_hard_fail(branch):
    rejected(good(branch=branch))


@pytest.mark.parametrize("ticket", ["FND-353", "FND-03533", "fnd-0353", "FND-0353\n", "FND-０３５３", "ABC-0353", " FND-0353"])
def test_a_ticket_that_is_not_fnd_and_four_ascii_digits_is_a_hard_fail(ticket):
    rejected(good(ticket=ticket))


@pytest.mark.parametrize("title", ["a;b", "a|b", "a&b", "a`b", "a$b", "a<b", "a>b", "a\\b", 'a"b', "a!b", "a*b", "a?b", "a{b", "a}b", "a[b", "a]b", "a~b", "a\nb", "a\tb", "", "x" * 201, "ｔｉｔｌｅ"])
def test_a_title_with_a_shell_or_markup_metacharacter_is_a_hard_fail(title):
    rejected(good(title=title))


def test_body_is_free_markdown_but_has_no_control_characters_and_a_size_limit():
    assert check(good(body="`code` $(not a shell) ; | & <b> \\ \" ' ~ ! * ? {}\n\ttab"))["body"].startswith("`code`")
    for bad in ("nul\x00", "bell\x07", "esc\x1b[31m", "del\x7f", "vt\x0b", "ff\x0c"):
        rejected(good(body=bad))
    assert check(good(body="a" * 20000))
    rejected(good(body="a" * 20001))


# --- identity and size ------------------------------------------------------------------------------------------------------------------------

def test_the_id_must_be_a_uuid4_equal_to_the_file_name_and_never_seen_before():
    r = good()
    rejected(r, name="somethingelse.json")
    rejected(r, name=r["id"] + ".JSON")
    rejected(r, name="../" + r["id"] + ".json")
    rejected(r, name=r["id"] + ".json", seen=[r["id"]])
    rejected(good(id=str(uuid.uuid1())))                                       # version 1, not 4
    rejected(good(id=str(uuid.uuid4()).upper()), name="x.json")                # upper case
    rejected(good(id="not-a-uuid"), name="not-a-uuid.json")


def test_a_file_over_64_kb_is_refused_before_it_is_parsed():
    big = good(body="a" * 20000)
    data = raw(big) + b" " * 70000
    assert len(data) > 65536
    assert "size" in rejected(data, name=big["id"] + ".json").lower()


def test_every_hard_fail_carries_the_rule_that_failed():
    assert rejected(good(branch="a..b")).strip() != ""


# --- the locked schemas (acceptance 13) ----------------------------------------------------------------------------------------------------------

def test_the_schemas_are_pinned_by_hash_so_a_changed_schema_cannot_be_used_silently():
    assert sch.sha256_file(_OPS / "open_pr_request.schema.json") == open_pr.SCHEMA_SHA256["open_pr_request.schema.json"]
    assert sch.sha256_file(_OPS / "open_pr_receipt.schema.json") == open_pr.SCHEMA_SHA256["open_pr_receipt.schema.json"]


def test_the_receipt_schema_separates_a_pull_request_from_every_other_status():
    rid = str(uuid.uuid4())
    opened = {"v": 1, "request_id": rid, "status": "PR_OPENED", "pr_number": 7, "pr_url": "https://github.com/jacormenzana/fondos/pull/7", "head_sha": "a" * 40, "base": "master",
              "opened_at": "2026-10-30T16:00:00Z"}
    assert sch.validate(opened, RECEIPT_SCHEMA) == []
    for st in ("REFUSED", "REJECTED_SECURITY", "FAILED"):
        assert sch.validate({"v": 1, "request_id": rid, "status": st, "reason": "no commits ahead"}, RECEIPT_SCHEMA) == []
        assert sch.validate({"v": 1, "request_id": rid, "status": st, "reason": "x", "pr_url": opened["pr_url"]}, RECEIPT_SCHEMA) != []        # a refusal never carries a URL
    assert sch.validate({**opened, "pr_url": "https://evil.example/pull/7"}, RECEIPT_SCHEMA) != []
    assert sch.validate({**opened, "pr_url": "https://github.com/other/repo/pull/7"}, RECEIPT_SCHEMA) != []
    assert sch.validate({**opened, "base": "main"}, RECEIPT_SCHEMA) != []
    assert sch.validate({**opened, "head_sha": "A" * 40}, RECEIPT_SCHEMA) != []
    assert sch.validate({**opened, "extra": 1}, RECEIPT_SCHEMA) != []
    assert sch.validate({**opened, "pr_number": 7.0}, RECEIPT_SCHEMA) != [] and sch.validate({**opened, "pr_number": True}, RECEIPT_SCHEMA) != []      # a number that is not an integer
    assert sch.validate({**opened, "pr_number": 0}, RECEIPT_SCHEMA) != []
    assert sch.validate({"v": 1, "request_id": rid, "status": "SUCCESS", "reason": "x"}, RECEIPT_SCHEMA) != []
