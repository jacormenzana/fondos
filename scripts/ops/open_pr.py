# scripts/ops/open_pr.py
# -*- coding: utf-8 -*-
"""The pull-request runner (FND-0353, CICD_SERVICE_ACCOUNT_SPEC.md). Runs ONLY as the local account `fondos_ci_runner`, from the read-only deployed copy, started by a scheduled task.
It is the only module that touches the GitHub App's private key. Standard library plus `cryptography`; no process calls, no dynamic execution, no credential store, no token cache (spec D7).

The request that crosses from the agent is UNTRUSTED input: validate_request() is the whole gate (spec section 5). Contract of this module: tests/test_open_pr_*.py (written first).
Order of run(): integrity (manifest, pinned schemas) -> quarantine empty -> key (ACL before content) -> kill switch -> JWT -> installation token -> startup check D8 (permissions and
repositories GitHub actually issued) -> only then the inbox.
"""
from __future__ import annotations

import base64
import dataclasses
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import db_owner_manifest as manifest
from open_pr_schema import StrictJsonError, load_strict_json, sha256_file, validate

# --- pinned artefacts (spec acceptance 13): changing a schema without redeploying makes the runner exit ------------------------------------------------------------
SCHEMA_SHA256 = {
    "open_pr_request.schema.json": "cd3ca852aa40135a83d3e9d91d3ec21412213b3bc625a3e0b354ce50f13667a1",
    "open_pr_receipt.schema.json": "cfb094bcddfbc4e1fd0ece6a92c90be16a3c519b8640525d9396be8204677d9e",
}
EXIT_OK, EXIT_KEY, EXIT_MANIFEST, EXIT_STARTUP, EXIT_SECURITY, EXIT_API = 0, 10, 11, 12, 13, 14
MAX_REQUEST_BYTES = 64 * 1024
EXPECTED_PERMISSIONS = {"pull_requests": "write", "checks": "read", "metadata": "read"}
TOKEN_PERMISSIONS = {"pull_requests": "write", "checks": "read"}          # requested down-scoped; metadata: read is implicit
PROCESSED_IDS = ".processed_ids"
FOOTER = "\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)"
_REQUEST_NAME = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class HardFail(Exception):
    """A security-relevant deviation in a request file: quarantine, REJECTED_SECURITY, critical alert, exit, stop processing the inbox (spec section 5)."""

    def __init__(self, rule: str):
        super().__init__(rule)
        self.rule = rule


class KeyProblem(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


class StartupMismatch(Exception):
    def __init__(self, rule: str):
        super().__init__(rule)
        self.rule = rule


def enabled_from_env(env) -> bool:
    return str(env.get("FONDOS_OPEN_PR_ENABLED", "")).strip().lower() in ("1", "true", "on", "yes")


@dataclasses.dataclass
class Config:
    inbox: Path
    outbox: Path
    quarantine: Path
    alerts: Path
    key_file: Path
    schema_dir: Path
    manifest_root: object            # Path of the deployed tree whose MANIFEST.sha256 is checked, or None
    app_id: str
    installation_id: str
    allowed_principals: frozenset
    enabled: bool
    repo: str = "jacormenzana/fondos"
    base: str = "master"
    review_login: str = "jacormenzana"


# --- the request gate (spec section 5, steps 1 to 3) ------------------------------------------------------------------------------------------------------------

def validate_request(raw: bytes, filename: str, seen_ids, schema: dict) -> dict:
    """Returns the request; raises HardFail(rule) on any deviation."""
    if len(raw) > MAX_REQUEST_BYTES:
        raise HardFail(f"size: request file is {len(raw)} bytes, the limit is {MAX_REQUEST_BYTES}")
    m = _REQUEST_NAME.fullmatch(filename)
    if not m:
        raise HardFail("file name is not <uuid4>.json")
    try:
        data = load_strict_json(raw)
    except StrictJsonError as exc:
        raise HardFail(f"strict json: {exc}") from None
    errors = validate(data, schema)
    if errors:
        raise HardFail("schema: " + "; ".join(errors[:3]))
    if data["id"] != m.group(1):
        raise HardFail("file name does not equal the request id")
    if data["id"] in seen_ids:
        raise HardFail("request id already processed")
    return data


# --- the key (spec section 4) -------------------------------------------------------------------------------------------------------------------------------------

def load_app_key(path: Path, read_acl, allowed) -> bytes:
    """The PEM bytes, after: the file exists; nobody outside `allowed` can read it (the ACL is checked BEFORE the content is read); it is exactly one PEM block holding an RSA 2048 key."""
    path = Path(path)
    if not path.is_file():
        raise KeyProblem("KEY_MISSING", "the key file does not exist")
    extra = sorted(set(read_acl(path)) - set(allowed))
    if extra:
        raise KeyProblem("KEY_ACL", "the key file can also be read by " + ", ".join(extra))
    data = path.read_bytes()
    if not data.strip():
        raise KeyProblem("KEY_EMPTY", "the key file is empty")
    if data.count(b"-----BEGIN ") != 1:
        raise KeyProblem("KEY_NOT_SINGLE_PEM", "the key file is not exactly one PEM block")
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except Exception:
        raise KeyProblem("KEY_NOT_RSA", "the PEM block is not a loadable private key") from None
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048:
        raise KeyProblem("KEY_NOT_RSA", "the key is not an RSA 2048-bit private key")
    return data


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_jwt(app_id, pem: bytes, now: int) -> str:
    """RS256 JWT valid for 10 minutes (iat 60 s in the past for clock skew)."""
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps({"iat": int(now) - 60, "exp": int(now) + 540, "iss": str(app_id)}, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(pem, password=None)
    sig = key.sign(f"{head}.{body}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(sig)}"


def windows_read_acl(path) -> list:
    """SIDs (strings) granted any access by the file's DACL. Win32 through ctypes: no process call."""
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p)]
    adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    adv.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    adv.GetAclInformation.restype = wintypes.BOOL
    adv.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    adv.GetAce.restype = wintypes.BOOL
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = wintypes.BOOL
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree.restype = ctypes.c_void_p
    dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    rc = adv.GetNamedSecurityInfoW(str(path), 1, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(sd))     # SE_FILE_OBJECT, DACL_SECURITY_INFORMATION
    if rc != 0:
        raise OSError(rc, "GetNamedSecurityInfoW failed")
    try:
        if not dacl.value:
            return ["NULL-DACL(everyone)"]
        info = (wintypes.DWORD * 3)()                                  # AceCount, AclBytesInUse, AclBytesFree
        if not adv.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            raise OSError(ctypes.get_last_error(), "GetAclInformation failed")
        sids = []
        for i in range(info[0]):
            ace = ctypes.c_void_p()
            if not adv.GetAce(dacl, i, ctypes.byref(ace)):
                raise OSError(ctypes.get_last_error(), "GetAce failed")
            ace_type = ctypes.c_ubyte.from_address(ace.value).value
            mask = wintypes.DWORD.from_address(ace.value + 4).value
            if ace_type != 0 or mask == 0:                                # ACCESS_ALLOWED_ACE_TYPE only
                continue
            text = wintypes.LPWSTR()
            if not adv.ConvertSidToStringSidW(ctypes.c_void_p(ace.value + 8), ctypes.byref(text)):
                raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
            sids.append(text.value)
            k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        return sids
    finally:
        k32.LocalFree(sd)


# --- what GitHub issued (decision D8) --------------------------------------------------------------------------------------------------------------------------

def startup_check(token_response: dict, installation_repos: dict, repo: str = "jacormenzana/fondos") -> None:
    if token_response.get("permissions") != EXPECTED_PERMISSIONS:
        raise StartupMismatch(f"permissions: GitHub issued {token_response.get('permissions')!r}, expected exactly {EXPECTED_PERMISSIONS!r}")
    if token_response.get("repository_selection") != "selected":
        raise StartupMismatch(f"repository_selection: {token_response.get('repository_selection')!r}, expected 'selected'")
    names = sorted(r.get("full_name") for r in token_response.get("repositories", []))
    if names != [repo]:
        raise StartupMismatch(f"repositories of the token: {names!r}, expected exactly [{repo!r}]")
    inst = sorted(r.get("full_name") for r in installation_repos.get("repositories", []))
    if installation_repos.get("total_count") != 1 or inst != [repo]:
        raise StartupMismatch(f"installation: GET /installation/repositories returned {installation_repos.get('total_count')} repositories {inst!r}, expected exactly [{repo!r}]")


# --- GitHub over HTTPS (the only network code) ------------------------------------------------------------------------------------------------------------------

class UrllibApi:
    def __init__(self, base: str = "https://api.github.com", timeout: int = 30):
        self.base, self.timeout = base, timeout

    def request(self, method, path, *, bearer, body=None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "fondos-pr-opener"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:      # nosec: fixed https host
                raw, status = resp.read(), resp.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        try:
            return status, (json.loads(raw.decode("utf-8")) if raw else {})
        except ValueError:
            return status, {"message": "non-JSON response"}


# --- files ------------------------------------------------------------------------------------------------------------------------------------------------------------------

def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _clean(text: str, limit: int = 300) -> str:
    return _CONTROL.sub(" ", str(text))[:limit].strip() or "no detail"


def _alert(cfg: Config, now, line: str) -> None:
    cfg.alerts.mkdir(parents=True, exist_ok=True)
    with open(cfg.alerts / "security.log", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"CRITICAL {_iso(now())} {_clean(line, 500)}\n")


def _write_receipt(cfg: Config, receipt_schema: dict, receipt: dict) -> None:
    errors = validate(receipt, receipt_schema)
    if errors:
        raise RuntimeError("the runner built an invalid receipt: " + "; ".join(errors[:3]))
    cfg.outbox.mkdir(parents=True, exist_ok=True)
    final = cfg.outbox / f"{receipt['request_id']}.result.json"
    tmp = final.with_name(final.name + ".tmp")
    tmp.write_text(json.dumps(receipt, separators=(",", ":")), encoding="utf-8", newline="\n")
    os.replace(tmp, final)


def _processed(cfg: Config) -> set:
    f = cfg.outbox / PROCESSED_IDS
    return set(f.read_text(encoding="utf-8").split()) if f.is_file() else set()


def _mark_processed(cfg: Config, rid: str) -> None:
    with open(cfg.outbox / PROCESSED_IDS, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(rid + "\n")


# --- one request (spec section 5 steps 4 and 5) ------------------------------------------------------------------------------------------------------------------

def _refused(rid, why):
    return {"v": 1, "request_id": rid, "status": "REFUSED", "reason": _clean(why)}


def _failed(rid, why):
    return {"v": 1, "request_id": rid, "status": "FAILED", "reason": _clean(why)}


def handle_request(cfg: Config, api, token, req: dict, now) -> tuple:
    """(receipt, api_failed). `token` is None when the runner is off: the request is refused without any network call."""
    rid, repo = req["id"], cfg.repo
    if not cfg.enabled:
        return _refused(rid, "FONDOS_OPEN_PR_ENABLED is off in the runner's environment"), False
    if req["branch"] == cfg.base:
        return _refused(rid, f"the head branch is {cfg.base}"), False
    branch = quote(req["branch"], safe="/")
    status, _ = api.request("GET", f"/repos/{repo}/branches/{branch}", bearer=token)
    if status == 404:
        return _refused(rid, "the branch is not on origin"), False
    if status != 200:
        return _failed(rid, f"GitHub returned HTTP {status} when reading the branch"), True
    status, data = api.request("GET", f"/repos/{repo}/compare/{cfg.base}...{branch}", bearer=token)
    if status != 200:
        return _failed(rid, f"GitHub returned HTTP {status} when comparing the branch with {cfg.base}"), True
    if not data.get("ahead_by"):
        return _refused(rid, f"the branch has no commits ahead of {cfg.base}"), False
    body = f"{req['body']}\n\nRefs: {req['ticket']}\nRequest: {rid}\n\n{FOOTER}\n"
    status, pr = api.request("POST", f"/repos/{repo}/pulls", bearer=token, body={"title": req["title"], "head": req["branch"], "base": cfg.base, "body": body})
    if status in (401, 403, 404):
        return _failed(rid, f"GitHub refused the write (HTTP {status}): the App lacks 'Pull requests: Read and write' on {repo}"), True
    if status != 201:
        return _failed(rid, f"GitHub returned HTTP {status}: {pr.get('message', '')}"), True
    number, sha = pr.get("number"), (pr.get("head") or {}).get("sha")
    url = f"https://github.com/{repo}/pull/{number}"
    if pr.get("html_url") != url or not isinstance(number, int) or not re.fullmatch(r"[0-9a-f]{40}", str(sha)):
        return _failed(rid, "GitHub's answer for the new pull request is not in the expected shape"), True
    api.request("POST", f"/repos/{repo}/pulls/{number}/requested_reviewers", bearer=token, body={"reviewers": [cfg.review_login]})      # best effort: the PR already exists
    return {"v": 1, "request_id": rid, "status": "PR_OPENED", "pr_number": number, "pr_url": url, "head_sha": sha, "base": cfg.base, "opened_at": _iso(now())}, False


def process_inbox(cfg: Config, api, token, schemas: tuple, now, log) -> int:
    request_schema, receipt_schema = schemas
    seen = _processed(cfg)
    api_failed = False
    for path in sorted(p for p in cfg.inbox.iterdir() if p.is_file() and not p.name.endswith(".part")):
        try:
            req = validate_request(path.read_bytes() if path.stat().st_size <= MAX_REQUEST_BYTES else b"\0" * (MAX_REQUEST_BYTES + 1), path.name, seen, request_schema)
        except HardFail as exc:
            m = _REQUEST_NAME.fullmatch(path.name)
            rid = m.group(1) if m and not (cfg.outbox / f"{m.group(1)}.result.json").exists() else str(uuid.uuid4())
            cfg.quarantine.mkdir(parents=True, exist_ok=True)
            os.replace(path, cfg.quarantine / path.name)
            _write_receipt(cfg, receipt_schema, {"v": 1, "request_id": rid, "status": "REJECTED_SECURITY", "reason": _clean(exc.rule)})
            _alert(cfg, now, f"{path.name} {exc.rule}")
            log(f"HARD FAIL {path.name}: {exc.rule}; the inbox is not processed further")
            return EXIT_SECURITY
        receipt, failed = handle_request(cfg, api, token, req, now)
        _write_receipt(cfg, receipt_schema, receipt)
        _mark_processed(cfg, req["id"])
        path.unlink()
        api_failed = api_failed or failed
        log(f"{req['id']}: {receipt['status']}")
    return EXIT_API if api_failed else EXIT_OK


# --- the run ------------------------------------------------------------------------------------------------------------------------------------------------------------------

def run(cfg: Config, api, read_acl, now=time.time, log=print) -> int:
    problems = []
    if cfg.manifest_root is not None:
        problems += manifest.verify(Path(cfg.manifest_root))[1]
    for name, want in SCHEMA_SHA256.items():
        p = Path(cfg.schema_dir) / name
        if not p.is_file() or sha256_file(p) != want:
            problems.append(f"SCHEMA {name} differs from the pinned hash")
    if problems:
        log("INTEGRITY: " + "; ".join(problems[:10]))
        _alert(cfg, now, "INTEGRITY " + "; ".join(problems[:5]))
        return EXIT_MANIFEST
    request_schema = json.loads((Path(cfg.schema_dir) / "open_pr_request.schema.json").read_text(encoding="utf-8"))
    receipt_schema = json.loads((Path(cfg.schema_dir) / "open_pr_receipt.schema.json").read_text(encoding="utf-8"))
    if cfg.quarantine.is_dir() and any(cfg.quarantine.iterdir()):
        log("QUARANTINE is not empty: nothing is processed until the owner clears it")
        return EXIT_SECURITY
    try:
        pem = load_app_key(cfg.key_file, read_acl, cfg.allowed_principals)
    except KeyProblem as exc:
        log(str(exc))
        _alert(cfg, now, str(exc))
        return EXIT_KEY
    schemas = (request_schema, receipt_schema)
    if not cfg.enabled:
        return process_inbox(cfg, api, None, schemas, now, log)
    status, tr = api.request("POST", f"/app/installations/{cfg.installation_id}/access_tokens", bearer=make_jwt(cfg.app_id, pem, int(now())),
                             body={"permissions": TOKEN_PERMISSIONS, "repositories": [cfg.repo.split("/", 1)[1]]})
    pem = None
    if status != 201 or not isinstance(tr.get("token"), str):
        log(f"TOKEN: GitHub returned HTTP {status} for the installation token")
        return EXIT_API
    token = tr["token"]
    status, inst = api.request("GET", "/installation/repositories", bearer=token)
    if status != 200:
        log(f"STARTUP: GitHub returned HTTP {status} for /installation/repositories")
        return EXIT_API
    try:
        startup_check(tr, inst, cfg.repo)
    except StartupMismatch as exc:
        log(f"STARTUP MISMATCH: {exc.rule}")
        _alert(cfg, now, f"STARTUP {exc.rule}")
        return EXIT_STARTUP
    try:
        return process_inbox(cfg, api, token, schemas, now, log)
    finally:
        token = None


# --- entry point (scheduled task) ------------------------------------------------------------------------------------------------------------------------------

def load_config(root: Path, env) -> Config:
    """<root>\\app\\config.json holds only identifiers (no secret): {"app_id", "installation_id", "runner_sid", "review_login"}."""
    root = Path(root)
    cfg = json.loads((root / "app" / "config.json").read_text(encoding="utf-8"))
    if set(cfg) - {"app_id", "installation_id", "runner_sid", "review_login"} or not {"app_id", "installation_id", "runner_sid"} <= set(cfg):
        raise ValueError("config.json must hold app_id, installation_id, runner_sid and optionally review_login, nothing else")
    return Config(inbox=root / "inbox", outbox=root / "outbox", quarantine=root / "quarantine", alerts=root / "alerts", key_file=root / "github_app_keys" / "fondos-pr-opener.pem",
                  schema_dir=root / "app", manifest_root=root, app_id=str(cfg["app_id"]), installation_id=str(cfg["installation_id"]),
                  allowed_principals=frozenset({cfg["runner_sid"], "S-1-5-18", "S-1-5-32-544"}), enabled=enabled_from_env(env), review_login=cfg.get("review_login", "jacormenzana"))


def main(argv=None, env=None) -> int:
    import sys
    args = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    root = Path(args[args.index("--root") + 1]) if "--root" in args else Path(r"C:\ProgramData\fondos\ci_runner")
    cfg = load_config(root, env)
    if "--check-key" in args:
        try:
            load_app_key(cfg.key_file, windows_read_acl, cfg.allowed_principals)
        except KeyProblem as exc:
            print(str(exc))
            return EXIT_KEY
        print("key OK (ACL restricted, one RSA 2048 PEM); nothing printed from it")
        return EXIT_OK
    return run(cfg, UrllibApi(), windows_read_acl)


if __name__ == "__main__":
    import sys
    sys.exit(main())
