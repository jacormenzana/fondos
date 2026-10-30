# tests/test_open_pr_runner.py
# -*- coding: utf-8 -*-
"""Acceptance 1, 3, 5-of-section-5, 7, 8, 9, 11, 13 and 14 of CICD_SERVICE_ACCOUNT_SPEC.md (FND-0353) for the runner open_pr.py. Written BEFORE the code (owner order, 2026-10-30).
The GitHub API, the key-file ACL and the clock are fakes; the key is a real RSA key generated per session (never a literal in this repository).

Contract under test (scripts/ops/open_pr.py):
    EXIT_OK=0  EXIT_KEY=10  EXIT_MANIFEST=11  EXIT_STARTUP=12  EXIT_SECURITY=13  EXIT_API=14
    enabled_from_env(env: dict) -> bool                                   FONDOS_OPEN_PR_ENABLED, default off
    load_app_key(path, read_acl, allowed) -> bytes                         KeyProblem(code, message); ACL checked BEFORE the content is read
    make_jwt(app_id, pem: bytes, now: int) -> str                          RS256, 10 minutes
    startup_check(token_response: dict, installation_repos: dict) -> None  StartupMismatch(rule)
    Config(inbox, outbox, quarantine, alerts, key_file, schema_dir, manifest_root, app_id, installation_id, allowed_principals, enabled, repo="jacormenzana/fondos", base="master", review_login="jacormenzana")
    run(cfg, api, read_acl, now=time.time, log=print) -> int
    api.request(method, path, *, bearer, body=None) -> (status: int, data: dict)
"""
from __future__ import annotations

import base64
import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

_OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
if str(_OPS) not in sys.path:
    sys.path.insert(0, str(_OPS))

import db_owner_manifest as manifest  # noqa: E402
import open_pr as op  # noqa: E402
import open_pr_schema as sch  # noqa: E402

REPO = "jacormenzana/fondos"
TOKEN = "ghs_FAKEINSTALLATIONTOKEN0123456789"
GOOD_PERMS = {"pull_requests": "write", "checks": "read", "metadata": "read"}
RUNNER_SID = "S-1-5-21-111-222-333-1005"
ALLOWED = frozenset({RUNNER_SID, "S-1-5-18", "S-1-5-32-544"})


@pytest.fixture(scope="session")
def rsa_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    return key, pem


class FakeApi:
    """Scripted GitHub. Records every call; never sees the key. `bearer` is what the runner presented."""

    def __init__(self, *, perms=None, selection="selected", repos=(REPO,), total=None, branches=None, token_status=201, pr_status=201, pr_message="", reviewer_status=201, head_sha="a" * 40):
        self.perms = dict(GOOD_PERMS) if perms is None else perms
        self.selection, self.repos, self.total = selection, list(repos), total
        self.branches = {"feat/x": 2} if branches is None else branches            # branch -> commits ahead of master
        self.token_status, self.pr_status, self.pr_message, self.reviewer_status, self.head_sha = token_status, pr_status, pr_message, reviewer_status, head_sha
        self.calls: list = []

    def request(self, method, path, *, bearer, body=None):
        self.calls.append((method, path, bearer, body))
        if method == "POST" and path.endswith("/access_tokens"):
            if self.token_status != 201:
                return self.token_status, {"message": "Bad credentials"}
            return 201, {"token": TOKEN, "permissions": self.perms, "repository_selection": self.selection, "repositories": [{"full_name": r} for r in self.repos]}
        if method == "GET" and path == "/installation/repositories":
            return 200, {"total_count": len(self.repos) if self.total is None else self.total, "repositories": [{"full_name": r} for r in self.repos]}
        if method == "GET" and path.startswith(f"/repos/{REPO}/branches/"):
            b = path.rsplit("/branches/", 1)[1]
            return (200, {"name": b}) if b in self.branches else (404, {"message": "Branch not found"})
        if method == "GET" and "/compare/" in path:
            b = path.split("...", 1)[1]
            return 200, {"ahead_by": self.branches.get(b, 0)}
        if method == "POST" and path == f"/repos/{REPO}/pulls":
            if self.pr_status != 201:
                return self.pr_status, {"message": self.pr_message or "Resource not accessible by integration"}
            n = 7
            return 201, {"number": n, "html_url": f"https://github.com/{REPO}/pull/{n}", "head": {"sha": self.head_sha}, "base": {"ref": "master"}, "state": "open"}
        if method == "POST" and path.endswith("/requested_reviewers"):
            return self.reviewer_status, {}
        return 404, {"message": "unexpected call in the test"}

    def writes(self):
        return [c for c in self.calls if c[0] == "POST" and not c[1].endswith("/access_tokens") and not c[1].endswith("/requested_reviewers")]


@pytest.fixture
def env(tmp_path, rsa_key):
    key, pem = rsa_key
    root = tmp_path
    for d in ("inbox", "outbox", "quarantine", "alerts", "keys", "schemas"):
        (root / d).mkdir()
    for n in ("open_pr_request.schema.json", "open_pr_receipt.schema.json"):
        shutil.copy(_OPS / n, root / "schemas" / n)
    (root / "keys" / "app.pem").write_bytes(pem)
    cfg = op.Config(inbox=root / "inbox", outbox=root / "outbox", quarantine=root / "quarantine", alerts=root / "alerts", key_file=root / "keys" / "app.pem",
                    schema_dir=root / "schemas", manifest_root=None, app_id="837492", installation_id="49201834", allowed_principals=ALLOWED, enabled=True)
    logs: list = []
    return cfg, logs, root, key, pem


def put_request(cfg, **over):
    req = {"v": 1, "id": str(uuid.uuid4()), "branch": "feat/x", "ticket": "FND-0353", "title": "docs(backlog): FND-0353 - first pull request", "body": "Body with `code`.\n"}
    req.update(over)
    (cfg.inbox / f"{req['id']}.json").write_bytes(json.dumps(req).encode("utf-8"))
    return req


def go(cfg, logs, api, acl=("S-1-5-18", "S-1-5-32-544", RUNNER_SID)):
    return op.run(cfg, api, lambda p: list(acl), now=lambda: 1_700_000_000.0, log=logs.append)


def receipts(cfg):
    out = {}
    for f in cfg.outbox.glob("*.result.json"):
        out[f.name.removesuffix(".result.json")] = json.loads(f.read_text(encoding="utf-8"))
    return out


# --- the kill switch and the key (acceptance 1, 7) -----------------------------------------------------------------------------------------------

def test_the_runner_is_off_unless_the_environment_turns_it_on():
    assert op.enabled_from_env({}) is False
    assert op.enabled_from_env({"FONDOS_OPEN_PR_ENABLED": "0"}) is False
    assert op.enabled_from_env({"FONDOS_OPEN_PR_ENABLED": "1"}) is True
    assert op.enabled_from_env({"FONDOS_OPEN_PR_ENABLED": "on"}) is True
    assert op.enabled_from_env({"FONDOS_OPEN_PR_ENABLED": "maybe"}) is False


def test_without_the_key_file_the_runner_writes_a_distinct_failure_and_opens_nothing(env):
    cfg, logs, root, *_ = env
    put_request(cfg)
    cfg.key_file.unlink()
    api = FakeApi()
    assert go(cfg, logs, api) == op.EXIT_KEY
    assert api.calls == [] and receipts(cfg) == {} and any("KEY_MISSING" in line for line in logs)


@pytest.mark.parametrize("content,code", [(b"", "KEY_EMPTY"), (b"just text", "KEY_NOT_SINGLE_PEM")])
def test_an_empty_or_non_pem_key_is_refused_with_its_own_code(env, content, code):
    cfg, logs, *_ = env
    cfg.key_file.write_bytes(content)
    assert go(cfg, logs, FakeApi()) == op.EXIT_KEY and any(code in line for line in logs)


def test_two_pems_or_a_non_rsa_or_a_short_rsa_key_are_refused(env, rsa_key):
    cfg, logs, root, key, pem = env
    cfg.key_file.write_bytes(pem + pem)
    assert go(cfg, logs, FakeApi()) == op.EXIT_KEY and any("KEY_NOT_SINGLE_PEM" in line for line in logs)
    from cryptography.hazmat.primitives.asymmetric import ec
    ec_pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    cfg.key_file.write_bytes(ec_pem)
    logs.clear()
    assert go(cfg, logs, FakeApi()) == op.EXIT_KEY and any("KEY_NOT_RSA" in line for line in logs)
    short = rsa.generate_private_key(public_exponent=65537, key_size=1024).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    cfg.key_file.write_bytes(short)
    logs.clear()
    assert go(cfg, logs, FakeApi()) == op.EXIT_KEY and any("KEY_NOT_RSA" in line for line in logs)


def test_a_key_readable_by_an_extra_principal_is_refused_naming_it_and_the_content_is_never_read(env):
    cfg, logs, *_ = env
    reads = []
    original = Path.read_bytes
    Path.read_bytes = lambda self: (reads.append(self), original(self))[1]
    try:
        with pytest.raises(op.KeyProblem) as e:
            op.load_app_key(cfg.key_file, lambda p: ["S-1-5-18", "S-1-5-21-111-222-333-1001"], ALLOWED)
    finally:
        Path.read_bytes = original
    assert e.value.code == "KEY_ACL" and "S-1-5-21-111-222-333-1001" in e.value.message and cfg.key_file not in reads
    api = FakeApi()
    put_request(cfg)
    assert go(cfg, logs, api, acl=("S-1-5-18", "S-1-5-32-544", RUNNER_SID, "S-1-5-21-111-222-333-1001")) == op.EXIT_KEY
    assert api.calls == [] and any("S-1-5-21-111-222-333-1001" in line for line in logs)
    assert go(cfg, logs, api) == op.EXIT_OK                                                       # with the restricted ACL it proceeds


def test_the_jwt_is_rs256_for_ten_minutes_and_verifies_with_the_public_key(rsa_key):
    key, pem = rsa_key
    tok = op.make_jwt("837492", pem, 1_700_000_000)
    h, p, s = tok.split(".")
    b64 = lambda x: base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))        # noqa: E731
    assert json.loads(b64(h)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(b64(p))
    assert claims["iss"] == "837492" and claims["exp"] - claims["iat"] <= 660 and claims["exp"] > 1_700_000_000
    key.public_key().verify(b64(s), f"{h}.{p}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())


# --- the startup check (acceptance 14, decision D8) ------------------------------------------------------------------------------------------

def token_response(**over):
    r = {"token": TOKEN, "permissions": dict(GOOD_PERMS), "repository_selection": "selected", "repositories": [{"full_name": REPO}]}
    r.update(over)
    return r


def installation(**over):
    r = {"total_count": 1, "repositories": [{"full_name": REPO}]}
    r.update(over)
    return r


def test_the_exact_permission_set_and_one_repository_pass():
    op.startup_check(token_response(), installation())


@pytest.mark.parametrize("tr,inst,needle", [
    (dict(permissions={**GOOD_PERMS, "contents": "write"}), {}, "permissions"),
    (dict(permissions={"pull_requests": "write", "checks": "read"}), {}, "permissions"),
    (dict(permissions={**GOOD_PERMS, "pull_requests": "read"}), {}, "permissions"),
    (dict(permissions={**GOOD_PERMS, "checks": "write"}), {}, "permissions"),
    (dict(permissions={**GOOD_PERMS, "x": "read"}), {}, "permissions"),
    (dict(repository_selection="all"), {}, "repository_selection"),
    (dict(repositories=[{"full_name": REPO}, {"full_name": "jacormenzana/other"}]), {}, "repositories"),
    (dict(repositories=[{"full_name": "jacormenzana/other"}]), {}, "repositories"),
    ({}, dict(total_count=2, repositories=[{"full_name": REPO}, {"full_name": "jacormenzana/other"}]), "installation"),
    ({}, dict(total_count=1, repositories=[{"full_name": "other/x"}]), "installation"),
    ({}, dict(total_count=0, repositories=[]), "installation"),
])
def test_any_other_permission_or_repository_set_is_a_startup_mismatch(tr, inst, needle):
    with pytest.raises(op.StartupMismatch) as e:
        op.startup_check(token_response(**tr), installation(**inst))
    assert needle in e.value.rule


@pytest.mark.parametrize("api_kwargs", [dict(perms={**GOOD_PERMS, "contents": "write"}), dict(perms={"pull_requests": "write"}), dict(selection="all"), dict(repos=(REPO, "jacormenzana/other")),
                                          dict(total=2)])
def test_a_startup_mismatch_stops_the_runner_before_the_inbox_is_opened(env, api_kwargs):
    cfg, logs, root, *_ = env
    req = put_request(cfg)
    api = FakeApi(**api_kwargs)
    assert go(cfg, logs, api) == op.EXIT_STARTUP
    assert (cfg.inbox / f"{req['id']}.json").exists() and receipts(cfg) == {} and api.writes() == []                    # the request stays unread, nothing was written to GitHub
    assert "CRITICAL" in (cfg.alerts / "security.log").read_text(encoding="utf-8")


def test_the_token_is_requested_down_scoped(env):
    cfg, logs, *_ = env
    api = FakeApi()
    go(cfg, logs, api)
    method, path, bearer, body = api.calls[0]
    assert (method, path) == ("POST", "/app/installations/49201834/access_tokens") and bearer.count(".") == 2
    assert body == {"permissions": {"pull_requests": "write", "checks": "read"}, "repositories": ["fondos"]}


# --- the ordinary rules (acceptance 9) -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("branch,branches,reason", [
    ("master", {"master": 5}, "master"),
    ("feat/none", {"feat/none": 0}, "ahead"),
    ("feat/ghost", {}, "origin"),
])
def test_an_ordinary_refusal_is_a_refusal_not_a_security_event(env, branch, branches, reason):
    cfg, logs, *_ = env
    req = put_request(cfg, branch=branch)
    api = FakeApi(branches=branches)
    assert go(cfg, logs, api) == op.EXIT_OK
    r = receipts(cfg)[req["id"]]
    assert r["status"] == "REFUSED" and reason in r["reason"] and "pr_url" not in r
    assert api.writes() == [] and not (cfg.alerts / "security.log").exists() and list(cfg.quarantine.iterdir()) == []


def test_with_the_kill_switch_off_a_valid_request_is_refused_and_no_token_is_requested(env):
    cfg, logs, *_ = env
    cfg.enabled = False
    req = put_request(cfg)
    api = FakeApi()
    assert go(cfg, logs, api) == op.EXIT_OK
    assert receipts(cfg)[req["id"]]["status"] == "REFUSED" and "FONDOS_OPEN_PR_ENABLED" in receipts(cfg)[req["id"]]["reason"] and api.calls == []


def test_a_good_request_opens_a_pull_request_requests_the_owner_and_writes_a_confirmed_receipt(env):
    cfg, logs, *_ = env
    req = put_request(cfg)
    api = FakeApi()
    assert go(cfg, logs, api) == op.EXIT_OK
    r = receipts(cfg)[req["id"]]
    assert r == {"v": 1, "request_id": req["id"], "status": "PR_OPENED", "pr_number": 7, "pr_url": f"https://github.com/{REPO}/pull/7", "head_sha": "a" * 40, "base": "master",
                 "opened_at": "2023-11-14T22:13:20Z"}
    pr = [c for c in api.calls if c[0] == "POST" and c[1] == f"/repos/{REPO}/pulls"][0]
    assert pr[3]["head"] == "feat/x" and pr[3]["base"] == "master" and pr[3]["title"] == req["title"]
    assert "FND-0353" in pr[3]["body"] and req["id"] in pr[3]["body"] and pr[3]["body"].startswith(req["body"])        # the ticket and the request id are in the pull request
    assert any(c[1].endswith("/pulls/7/requested_reviewers") and c[3] == {"reviewers": ["jacormenzana"]} for c in api.calls)
    assert not (cfg.inbox / f"{req['id']}.json").exists()                                                             # consumed
    assert sch.validate(r, json.loads((cfg.schema_dir / "open_pr_receipt.schema.json").read_text(encoding="utf-8"))) == []


def test_a_failed_review_request_does_not_fail_the_pull_request(env):
    cfg, logs, *_ = env
    req = put_request(cfg)
    assert go(cfg, logs, FakeApi(reviewer_status=422)) == op.EXIT_OK
    assert receipts(cfg)[req["id"]]["status"] == "PR_OPENED"


def test_an_app_without_pull_requests_write_fails_at_the_first_write_and_names_the_permission(env):
    cfg, logs, *_ = env
    req = put_request(cfg)
    api = FakeApi(pr_status=403)
    assert go(cfg, logs, api) == op.EXIT_API
    r = receipts(cfg)[req["id"]]
    assert r["status"] == "FAILED" and "Pull requests: Read and write" in r["reason"] and "pr_url" not in r


def test_an_existing_pull_request_is_a_failure_that_says_so(env):
    cfg, logs, *_ = env
    req = put_request(cfg)
    assert go(cfg, logs, FakeApi(pr_status=422, pr_message="A pull request already exists for jacormenzana:feat/x.")) == op.EXIT_API
    assert "already exists" in receipts(cfg)[req["id"]]["reason"]


# --- hard fail (acceptance 6, runner side) ----------------------------------------------------------------------------------------------------

def test_a_hard_fail_quarantines_the_file_alerts_and_stops_processing_the_inbox(env):
    cfg, logs, *_ = env
    bad_id = "00000000-0000-4000-8000-000000000000"                                                      # sorts first
    (cfg.inbox / f"{bad_id}.json").write_bytes(json.dumps({"v": 1, "id": bad_id, "branch": "a;b", "ticket": "FND-0353", "title": "t", "body": ""}).encode())
    ok = put_request(cfg, id="ffffffff-ffff-4fff-bfff-ffffffffffff")
    api = FakeApi()
    assert go(cfg, logs, api) == op.EXIT_SECURITY
    assert (cfg.quarantine / f"{bad_id}.json").exists() and not (cfg.inbox / f"{bad_id}.json").exists()
    assert (cfg.inbox / f"{ok['id']}.json").exists() and ok["id"] not in receipts(cfg)                      # nothing more from the inbox is processed
    r = receipts(cfg)[bad_id]
    assert r["status"] == "REJECTED_SECURITY" and "schema" in r["reason"] and "pr_url" not in r
    line = (cfg.alerts / "security.log").read_text(encoding="utf-8").strip().splitlines()[-1]
    assert line.startswith("CRITICAL") and bad_id in line and "schema" in line
    assert api.writes() == []
    # until the owner clears quarantine, a later run processes nothing
    logs.clear()
    assert go(cfg, logs, FakeApi()) == op.EXIT_SECURITY and ok["id"] not in receipts(cfg)
    shutil.rmtree(cfg.quarantine); cfg.quarantine.mkdir()
    assert go(cfg, logs, FakeApi()) == op.EXIT_OK and receipts(cfg)[ok["id"]]["status"] == "PR_OPENED"


def test_a_repeated_id_is_a_hard_fail(env):
    cfg, logs, *_ = env
    req = put_request(cfg)
    assert go(cfg, logs, FakeApi()) == op.EXIT_OK
    (cfg.inbox / f"{req['id']}.json").write_bytes(json.dumps(req).encode())                           # the same id again
    assert go(cfg, logs, FakeApi()) == op.EXIT_SECURITY
    assert (cfg.quarantine / f"{req['id']}.json").exists()


def test_a_file_whose_name_is_not_its_id_is_quarantined_with_a_fresh_receipt(env):
    cfg, logs, *_ = env
    req = {"v": 1, "id": str(uuid.uuid4()), "branch": "feat/x", "ticket": "FND-0353", "title": "t", "body": ""}
    (cfg.inbox / "notes.json").write_bytes(json.dumps(req).encode())
    assert go(cfg, logs, FakeApi()) == op.EXIT_SECURITY
    assert (cfg.quarantine / "notes.json").exists()
    assert len(receipts(cfg)) == 1 and next(iter(receipts(cfg).values()))["status"] == "REJECTED_SECURITY"


# --- nothing secret is ever written anywhere (acceptance 11) -----------------------------------------------------------------------------------

def test_the_key_the_jwt_and_the_token_appear_in_no_body_log_receipt_or_alert(env):
    cfg, logs, root, key, pem = env
    put_request(cfg)
    bad = str(uuid.uuid4())
    (cfg.inbox / f"{bad}.json").write_bytes(b'{"v": 1, "v": 2}')
    api = FakeApi()
    go(cfg, logs, api)
    jwt = next(c[2] for c in api.calls if c[1].endswith("/access_tokens"))
    secrets = [pem.decode("ascii"), pem.decode("ascii").splitlines()[1], jwt, jwt.split(".")[2], TOKEN]
    haystacks = [json.dumps(c[1]) + json.dumps(c[3]) for c in api.calls] + logs
    for d in (cfg.outbox, cfg.alerts, cfg.quarantine):
        haystacks += [f.read_text(encoding="utf-8", errors="replace") for f in d.rglob("*") if f.is_file()]
    for secret in secrets:
        assert not any(secret in h for h in haystacks)
    assert all(c[2] is None or c[2] == jwt or c[2] == TOKEN for c in api.calls)                       # the credentials only travel as the bearer


# --- the pinned manifest and schemas (acceptance 3, 13) --------------------------------------------------------------------------------------

def test_a_changed_deployed_file_or_schema_makes_the_runner_exit_before_anything_else(env):
    cfg, logs, root, *_ = env
    tree = root / "tree"
    (tree / "app").mkdir(parents=True)
    (tree / "app" / "open_pr.py").write_text("x = 1\n", encoding="utf-8")
    (tree / manifest.MANIFEST_NAME).write_text(manifest.build(tree, "c" * 40), encoding="utf-8", newline="\n")
    cfg.manifest_root = tree
    put_request(cfg)
    api = FakeApi()
    assert go(cfg, logs, api) == op.EXIT_OK                                                                    # untouched tree: runs
    (tree / "app" / "open_pr.py").write_text("x = 2\n", encoding="utf-8")
    put_request(cfg)
    api = FakeApi()
    assert go(cfg, logs, api) == op.EXIT_MANIFEST and api.calls == [] and any("CHANGED app/open_pr.py" in line for line in logs)
    (tree / "app" / "open_pr.py").write_text("x = 1\n", encoding="utf-8")
    (cfg.schema_dir / "open_pr_request.schema.json").write_text('{"type": "object"}\n', encoding="utf-8")        # the schema changed without redeploying
    logs.clear()
    api = FakeApi()
    assert go(cfg, logs, api) == op.EXIT_MANIFEST and api.calls == [] and any("open_pr_request.schema.json" in line for line in logs)


# --- the real Windows ACL reader and the identifier-only config ----------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Win32 ACL")
def test_windows_read_acl_lists_the_sids_the_dacl_allows(tmp_path):
    f = tmp_path / "k.pem"
    f.write_bytes(b"x")
    sids = op.windows_read_acl(f)
    assert sids and all(s.startswith("S-1-") for s in sids)
    assert "S-1-5-18" in sids or "S-1-5-32-544" in sids or any(s.startswith("S-1-5-21-") for s in sids)       # the inherited grants of a normal temp folder


def test_the_config_holds_identifiers_only_and_nothing_else(tmp_path):
    (tmp_path / "app").mkdir()
    cfgfile = tmp_path / "app" / "config.json"
    cfgfile.write_text(json.dumps({"app_id": 837492, "installation_id": 49201834, "runner_sid": RUNNER_SID}), encoding="utf-8")
    cfg = op.load_config(tmp_path, {"FONDOS_OPEN_PR_ENABLED": "1"})
    assert cfg.app_id == "837492" and cfg.installation_id == "49201834" and cfg.enabled is True and RUNNER_SID in cfg.allowed_principals
    assert cfg.key_file == tmp_path / "github_app_keys" / "fondos-pr-opener.pem" and op.load_config(tmp_path, {}).enabled is False
    for bad in ({"app_id": 1, "installation_id": 2}, {"app_id": 1, "installation_id": 2, "runner_sid": RUNNER_SID, "private_key": "x"}):
        cfgfile.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError):
            op.load_config(tmp_path, {})
