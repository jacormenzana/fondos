# scripts/ops/open_pr_request.py
# -*- coding: utf-8 -*-
"""Agent side of the pull-request pipeline (FND-0353, CICD_SERVICE_ACCOUNT_SPEC.md sections 5 and 6). Holds NO secret: it writes a request into the inbox, starts the scheduled task, and
appends the ticket log ONLY after a receipt that GitHub itself confirms, exactly once. Contract: tests/test_open_pr_request.py (written first).

    python scripts/ops/open_pr_request.py send --branch B --ticket FND-nnnn --title T --body-file F     write the request, start the task, print the request id
    python scripts/ops/open_pr_request.py log --id <request id>                                         confirm with GitHub and append the ticket log (once)
    python scripts/ops/open_pr_request.py --reconcile                                                    log every confirmed receipt whose log is missing
    python scripts/ops/open_pr_request.py --check-isolation                                              PASS only if this process is denied the key and the UAC values hold
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from open_pr_schema import StrictJsonError, load_strict_json, validate  # noqa: E402

REPO = "jacormenzana/fondos"
BOT_LOGIN = "fondos-pr-opener[bot]"
ROOT = Path(r"C:\ProgramData\fondos\ci_runner")
KEY_DIR = ROOT / "github_app_keys"
KEY_FILE = KEY_DIR / "fondos-pr-opener.pem"
STATE = Path(os.environ.get("LOCALAPPDATA", ".")) / "fondos" / "open_pr_state"
LOG_ACTOR = "agent:claude-sonnet-5-5"
REQUIRED_UAC = {"EnableLUA": 1, "ConsentPromptBehaviorAdmin": 1, "PromptOnSecureDesktop": 1}


class ReceiptInvalid(Exception):
    pass


def new_request(branch: str, ticket: str, title: str, body: str) -> dict:
    return {"v": 1, "id": str(uuid.uuid4()), "branch": branch, "ticket": ticket, "title": title, "body": body}


def write_request(inbox: Path, req: dict, schema: dict) -> Path:
    """Checks the request against the locked schema (the runner would quarantine it otherwise), then creates <id>.json atomically and never over an existing file."""
    errors = validate(req, schema)
    if errors:
        raise ValueError("the request would be rejected by the runner: " + "; ".join(errors[:3]))
    final = Path(inbox) / f"{req['id']}.json"
    if final.exists():
        raise FileExistsError(str(final))
    part = final.with_name(final.name + ".part")             # the runner ignores *.part, so it never reads a half-written file
    with open(part, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(req))
    os.replace(part, final)
    return final


def read_receipt(outbox: Path, request_id: str, schema: dict):
    f = Path(outbox) / f"{request_id}.result.json"
    if not f.is_file():
        return None
    try:
        data = load_strict_json(f.read_bytes())
    except StrictJsonError as exc:
        raise ReceiptInvalid(f"strict json: {exc}") from None
    errors = validate(data, schema)
    if errors:
        raise ReceiptInvalid("schema: " + "; ".join(errors[:3]))
    if data["request_id"] != request_id:
        raise ReceiptInvalid("the receipt is for another request")
    return data


def confirm_pull(receipt: dict, fetch_pull) -> tuple:
    """GitHub, not a file, is the source of truth (spec section 6)."""
    p = fetch_pull(receipt["pr_number"])
    if p.get("state") != "open":
        return False, f"open: GitHub reports the pull request as {p.get('state')!r}"
    if (p.get("head") or {}).get("sha") != receipt["head_sha"]:
        return False, "head: the head sha on GitHub differs from the receipt"
    if (p.get("base") or {}).get("ref") != receipt["base"]:
        return False, f"base: GitHub reports base {(p.get('base') or {}).get('ref')!r}"
    if (p.get("user") or {}).get("login") != BOT_LOGIN:
        return False, f"author: the pull request was opened by {(p.get('user') or {}).get('login')!r}, not {BOT_LOGIN}"
    if p.get("html_url") != receipt["pr_url"]:
        return False, "url: the URL on GitHub differs from the receipt"
    return True, "confirmed"


def log_if_confirmed(ticket: str, request_id: str, outbox: Path, state_dir: Path, sink, fetch_pull, schema: dict) -> str:
    marker = Path(state_dir) / f"{request_id}.logged"
    if marker.exists():
        return "ALREADY_LOGGED"
    try:
        receipt = read_receipt(outbox, request_id, schema)
    except ReceiptInvalid as exc:
        return f"NOT_LOGGED: the receipt is not trustworthy ({exc})"
    if receipt is None:
        return "NOT_LOGGED: no receipt yet"
    if receipt["status"] != "PR_OPENED":
        return f"NOT_LOGGED: status {receipt['status']}"
    try:
        ok, why = confirm_pull(receipt, fetch_pull)
    except Exception as exc:                                  # the log waits; it is never written on trust
        return f"NOT_LOGGED: GitHub unreachable ({type(exc).__name__})"
    if not ok:
        return f"NOT_LOGGED: {why}"
    if any(request_id in text for text in sink.existing_logs(ticket)):
        marker.write_text(request_id + "\n", encoding="utf-8")
        return "ALREADY_LOGGED"
    sink.append_log(ticket, f"Pull request #{receipt['pr_number']} opened by fondos-pr-opener and confirmed by GitHub: {receipt['pr_url']} (head {receipt['head_sha'][:7]}, request {request_id})")
    marker.write_text(request_id + "\n", encoding="utf-8")
    return "LOGGED"


def reconcile(outbox: Path, state_dir: Path, sink, fetch_pull, schema: dict, ticket_of) -> list:
    """Every PR_OPENED receipt without a .logged marker (a crash between the pull request and the log, or between the log and the marker), logged idempotently."""
    done = []
    for f in sorted(Path(outbox).glob("*.result.json")):
        rid = f.name.removesuffix(".result.json")
        if (Path(state_dir) / f"{rid}.logged").exists():
            continue
        try:
            receipt = read_receipt(outbox, rid, schema)
        except ReceiptInvalid:
            continue
        if receipt is None or receipt["status"] != "PR_OPENED":
            continue
        out = log_if_confirmed(ticket_of(rid), rid, outbox, state_dir, sink, fetch_pull, schema)
        if out in ("LOGGED", "ALREADY_LOGGED"):
            done.append(f"{rid}: {out}")
    return done


def check_isolation(try_read_key, try_list_dir, uac: dict) -> tuple:
    """PASS only when reading the key AND listing its directory raise PermissionError (a missing file is NOT a denial) and the three UAC values are set."""
    lines, ok = [], True
    for name, probe, target in (("read the key", try_read_key, KEY_FILE), ("list the key directory", try_list_dir, KEY_DIR)):
        try:
            probe(target)
            lines.append(f"FAIL {name}: it succeeded")
            ok = False
        except PermissionError:
            lines.append(f"PASS {name}: access is denied")
        except Exception as exc:
            lines.append(f"FAIL {name}: {type(exc).__name__} is not a denial")
            ok = False
    for k, want in REQUIRED_UAC.items():
        good = uac.get(k) == want
        lines.append(f"{'PASS' if good else 'FAIL'} UAC {k} = {uac.get(k)!r} (required {want})")
        ok = ok and good
    return ok, lines


# --- command line (thin: every rule above is tested without it) -------------------------------------------------------------------------------------------

def _fetch_pull(number: int) -> dict:
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/pulls/{number}", headers={"Accept": "application/vnd.github+json", "User-Agent": "fondos-open-pr-request"})
    with urllib.request.urlopen(req, timeout=30) as resp:        # public repository: no credential
        return json.loads(resp.read().decode("utf-8"))


class _BacklogSink:
    def existing_logs(self, ticket):
        from shared import backlog_client as bc
        return [r["comment"] for r in bc.list_logs(ids=[ticket])]

    def append_log(self, ticket, text):
        from shared import backlog_client as bc
        bc.append_log(ticket, LOG_ACTOR, text)


def _schemas():
    d = Path(__file__).resolve().parent
    return (json.loads((d / "open_pr_request.schema.json").read_text(encoding="utf-8")), json.loads((d / "open_pr_receipt.schema.json").read_text(encoding="utf-8")))


def _uac() -> dict:
    import winreg
    out = {}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System") as k:
        for name in REQUIRED_UAC:
            try:
                out[name] = winreg.QueryValueEx(k, name)[0]
            except OSError:
                out[name] = None
    return out


def main(argv=None) -> int:
    a = sys.argv[1:] if argv is None else argv
    request_schema, receipt_schema = _schemas()
    STATE.mkdir(parents=True, exist_ok=True)
    if "--check-isolation" in a:
        ok, lines = check_isolation(lambda p: open(p, "rb").close(), lambda p: os.listdir(p), _uac())
        print("\n".join(lines) + f"\nISOLATION {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    if "--reconcile" in a:
        out = reconcile(ROOT / "outbox", STATE, _BacklogSink(), _fetch_pull, receipt_schema, lambda rid: (STATE / f"{rid}.ticket").read_text(encoding="utf-8").strip())
        print("\n".join(out) or "nothing to reconcile")
        return 0
    cmd = a[0] if a else ""
    opts = {a[i]: a[i + 1] for i in range(1, len(a) - 1, 2)}
    if cmd == "send":
        body = Path(opts["--body-file"]).read_text(encoding="utf-8")
        req = new_request(opts["--branch"], opts["--ticket"], opts["--title"], body)
        write_request(ROOT / "inbox", req, request_schema)
        (STATE / f"{req['id']}.ticket").write_text(req["ticket"] + "\n", encoding="utf-8")
        import subprocess
        subprocess.run(["schtasks", "/Run", "/TN", "fondos_open_pr"], check=False)
        print(req["id"])
        return 0
    if cmd == "log":
        rid = opts["--id"]
        ticket = (STATE / f"{rid}.ticket").read_text(encoding="utf-8").strip()
        out = log_if_confirmed(ticket, rid, ROOT / "outbox", STATE, _BacklogSink(), _fetch_pull, receipt_schema)
        print(out)
        return 0 if out in ("LOGGED", "ALREADY_LOGGED") else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
