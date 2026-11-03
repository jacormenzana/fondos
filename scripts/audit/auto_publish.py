#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
auto_publish.py -- publishes master by itself (FND-0376; plan ACTION_PLAN_BACKLOG_REFACTORING, Revision 2, R2.7; Directive D-0: no recurring manual `git push`).

    python scripts/audit/auto_publish.py              # what the post-commit hook runs, detached: publish now, retry with backoff for up to 24 h
    python scripts/audit/auto_publish.py --retry      # one attempt (the deploy watcher calls this on every pass); prints an alert text when one is newly due
    python scripts/audit/auto_publish.py --now        # a hotfix: publish at once, in the foreground, without waiting for a test receipt; exit 0 published / nothing to do, 1 failed, 2 needs a person
    python scripts/audit/auto_publish.py --status     # the pending state, for a person or the preflight

What it does. It pushes `master` to `origin`, never forced, whenever master is ahead of origin/master. A commit that carries `Closes:` is a delivery: it waits until HEAD has a passing local test receipt
(scripts/audit/delivery_attest.py), which publishes by itself when it finishes; every other commit goes at once, which is the off-box copy. The push moves origin/master, which fires the autopilot hook.
A failed push NEVER waits for the next commit: this process retries with backoff (1, 2, 5, 10, then every 15 minutes, for up to 24 h), the deploy watcher retries on each pass (it survives a reboot), and
the next commit retries too. State is kept in out/audit/auto_publish_state.json. Nothing is silent: after 15 minutes of failure (at once for a diverged history or an authentication error, which no retry
fixes) it opens an incident through report_incident and the watcher shows a window; a delivery that waits for its receipt for 30 minutes raises the same alert; `preflight_check.py` prints an UNPUBLISHED line.
Exit codes: 0 ok, 1 the push failed (retriable), 2 a person must act (diverged history, authentication).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import backlog_gate as gate  # noqa: E402

RC_OK, RC_FAILED, RC_PERSON = 0, 1, 2
STATE = ROOT / "out" / "audit" / "auto_publish_state.json"
LOCK = ROOT / "out" / "audit" / "auto_publish.lock"
BACKOFF_S = (60, 120, 300, 600, 900)                      # then every 15 minutes
GIVE_UP_S = 24 * 3600
ALERT_AFTER_S = 15 * 60
STUCK_AFTER_S = 30 * 60
LOCK_STALE_S = 30 * 60                                     # the loop refreshes the lock on every turn; one turn sleeps at most 15 minutes
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_DETACHED = 0x00000008 | 0x00000200                        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


# ── pure ────────────────────────────────────────────────────────────────────────

def classify_push_error(text: str) -> str:
    """`diverged` (the remote has commits we lack: nobody forces), `auth` (credentials, permission), `network` (unreachable, timeout) or `unknown`; only network and unknown are worth retrying. Pure."""
    t = (text or "").lower()
    if any(s in t for s in ("non-fast-forward", "fetch first", "[rejected]", "updates were rejected", "diverged")):
        return "diverged"
    if any(s in t for s in ("authentication failed", "could not read username", "permission denied", "invalid username", "403", "repository not found", "access denied")):
        return "auth"
    if any(s in t for s in ("could not resolve host", "timed out", "timeout", "unable to access", "connection", "network is unreachable", "early eof", "remote end hung up")):
        return "network"
    return "unknown"


def retriable(error_class: str) -> bool:
    """Only a network or unknown failure heals by trying again. Pure."""
    return error_class in ("network", "unknown")


def backoff_s(attempts: int) -> int:
    """Seconds to wait after `attempts` failed attempts: 60, 120, 300, 600, then 900. Pure."""
    return BACKOFF_S[min(max(attempts, 1), len(BACKOFF_S)) - 1]


def plan(branch: str, ahead: list, head_verified: Optional[bool], force: bool = False, want: str = "master") -> tuple:
    """(action, why) with action `nothing` | `wait` | `push`. `ahead` = the commits of origin/master..HEAD as {'sha', 'closes': bool, 'ts': datetime}; `head_verified` = whether HEAD has a passing local test receipt
    (None when it was not asked). A commit that carries `Closes:` is a delivery and waits for the receipt unless `force` (the hotfix path); anything else is published at once. Pure."""
    if branch != want:
        return "nothing", f"the branch is {branch or '(detached)'}, not {want}"
    if not ahead:
        return "nothing", "nothing to publish"
    if any(c["closes"] for c in ahead) and not force and head_verified is not True:
        return "wait", "a commit with Closes: waits for its local test receipt (python scripts/audit/delivery_attest.py)"
    return "push", f"{len(ahead)} commit(s) ahead of origin"


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def failure_alert(state: dict, now: datetime) -> Optional[str]:
    """The alert text for a failing publication, once: at once for a diverged history or an authentication error, after ALERT_AFTER_S of failure otherwise. None when there is nothing to say or it was said. Pure."""
    if not state.get("pending_sha") or state.get("alerted") or not state.get("first_failure_ts"):
        return None
    cls = state.get("error_class") or "unknown"
    age = (now - _parse(state["first_failure_ts"])).total_seconds()
    if retriable(cls) and age < ALERT_AFTER_S:
        return None
    what = "a person must act (nothing is forced)" if not retriable(cls) else "it keeps retrying"
    return (f"master is NOT published: {state.get('attempts', 0)} attempt(s) since {state['first_failure_ts'][:19]}, {cls}: {str(state.get('last_error'))[:200]}; {what}. "
            f"After fixing the cause run: python scripts/audit/auto_publish.py --now")


def stuck_delivery_alert(ahead: list, now: datetime, already: Optional[str]) -> Optional[str]:
    """The alert text for a delivery (a `Closes:` commit) that has waited STUCK_AFTER_S for its test receipt, once per head. Pure."""
    old = [c for c in ahead if c["closes"] and (now - c["ts"]).total_seconds() >= STUCK_AFTER_S]
    if not old or (ahead and ahead[-1]["sha"] == already):
        return None
    return (f"a delivery is stuck: commit {old[0]['sha'][:12]} (Closes:) has waited {int((now - old[0]['ts']).total_seconds() // 60)} minutes for a local test receipt, so it is not published and its "
            f"ticket is not closed. Run: python scripts/audit/delivery_attest.py")


# ── git ─────────────────────────────────────────────────────────────────────────

def _git(root: Path, *args: str, timeout: int = 120) -> tuple:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    return r.returncode, r.stdout or "", r.stderr or ""


def ahead_commits(root: Path, remote_ref: str) -> list:
    """The commits of remote_ref..HEAD, oldest first: {'sha', 'closes': bool, 'ts'}."""
    rc, out, _ = _git(root, "log", "--reverse", f"{remote_ref}..HEAD", "--format=%H%x1f%aI%x1f%B%x1e")
    items = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\r\n")
        if not rec:
            continue
        sha, ts, msg = (rec.split("\x1f") + ["", ""])[:3]
        items.append({"sha": sha.strip(), "ts": datetime.fromisoformat(ts.strip()), "closes": bool(gate.parse_message(msg).closes)})
    return items


def try_publish(root: Path, *, force: bool = False, verify: Optional[Callable] = None, receipts_path: Optional[Path] = None, remote: str = "origin", branch: str = "master") -> dict:
    """One publication attempt: {'status': pushed | nothing | waiting | failed, 'detail', 'error_class', 'ahead'}. Fetches first, never forces, never pushes a history that is not a fast-forward."""
    rc, out, _ = _git(root, "branch", "--show-current")
    cur = out.strip()
    if cur != branch:
        return {"status": "nothing", "detail": f"the branch is {cur or '(detached)'}, not {branch}", "ahead": []}
    rc, _o, err = _git(root, "fetch", "-q", remote, timeout=90)
    if rc != 0:
        return {"status": "failed", "detail": err.strip()[:300], "error_class": classify_push_error(err), "ahead": []}
    remote_ref = f"{remote}/{branch}"
    head = _git(root, "rev-parse", "HEAD")[1].strip()
    if _git(root, "merge-base", "--is-ancestor", remote_ref, "HEAD")[0] != 0:
        if _git(root, "merge-base", "--is-ancestor", "HEAD", remote_ref)[0] == 0:
            return {"status": "nothing", "detail": "HEAD is already contained in origin", "ahead": []}
        return {"status": "failed", "detail": f"{remote_ref} has commits this master lacks and this master has its own: the history diverged (nothing is forced)", "error_class": "diverged", "ahead": []}
    ahead = ahead_commits(root, remote_ref)
    verified = None
    if any(c["closes"] for c in ahead) and not force:
        if verify is None:
            import delivery_attest as da
            verify = lambda r, sha, p: da.verify(r, sha, p)                                   # noqa: E731
            receipts_path = receipts_path or da.RECEIPTS
        verified = verify(root, head, receipts_path)[0] is True
    action, why = plan(cur, ahead, verified, force, branch)
    if action != "push":
        return {"status": "waiting" if action == "wait" else "nothing", "detail": why, "ahead": ahead}
    rc, _o, err = _git(root, "push", remote, branch, timeout=180)
    if rc != 0:
        return {"status": "failed", "detail": err.strip()[:300], "error_class": classify_push_error(err), "ahead": ahead}
    return {"status": "pushed", "detail": f"{len(ahead)} commit(s) published to {remote_ref}", "ahead": ahead}


# ── state, lock, loop ───────────────────────────────────────────────────────────

def read_state(path: Path = STATE) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(state: dict, path: Path = STATE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, sort_keys=True, indent=1), encoding="utf-8")


def apply_result(state: dict, res: dict, head: str, now: datetime) -> dict:
    """The new state after an attempt (pure): a failure keeps its first time and counts attempts; anything else clears the failure and keeps only the alert bookkeeping of a waiting delivery."""
    if res["status"] == "failed":
        s = dict(state) if state.get("pending_sha") else {}
        s.setdefault("first_failure_ts", now.isoformat(timespec="seconds"))
        s.update(pending_sha=head, attempts=int(s.get("attempts", 0)) + 1, last_error=res["detail"], error_class=res.get("error_class", "unknown"), last_attempt_ts=now.isoformat(timespec="seconds"))
        s.setdefault("alerted", False)
        return s
    keep = {k: state[k] for k in ("stuck_alerted_for",) if k in state and res["status"] == "waiting"}
    return keep


def acquire(lock: Path = LOCK, stale_s: int = LOCK_STALE_S) -> bool:
    """Take the loop's lock (created exclusively); a lock nobody refreshed for `stale_s` belongs to a dead process."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > stale_s:
            lock.unlink()
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    os.write(fd, f"{os.getpid()}\n".encode())
    os.close(fd)
    return True


def release(lock: Path = LOCK) -> None:
    try:
        lock.unlink()
    except OSError:
        pass


def lock_alive(lock: Path = LOCK, stale_s: int = LOCK_STALE_S) -> bool:
    try:
        return time.time() - lock.stat().st_mtime <= stale_s
    except OSError:
        return False


def raise_alert(text: str) -> None:
    """The incident for an alert: through report_incident (a no-op without the backlog login; it never raises and never masks the problem). The window is the watcher's."""
    try:
        import shared.config  # noqa: F401  (loads .env)
        from shared import backlog_client as bc
        bc.report_incident(object_name="auto_publish.py", scenario_description=text[:900], title="auto_publish: master is not being published")
    except Exception:                                                                       # noqa: BLE001
        pass


def attempt(root: Path, *, state_path: Path = STATE, force: bool = False, now: Callable = lambda: datetime.now(timezone.utc), try_fn: Callable = try_publish, alert: Callable = raise_alert, **kw) -> tuple:
    """One attempt with its bookkeeping: (result, alert text or None). Updates the state file; opens the incident when an alert is newly due."""
    res = try_fn(root, force=force, **kw)
    t = now()
    head = _git(root, "rev-parse", "HEAD")[1].strip()
    state = apply_result(read_state(state_path), res, head, t)
    text = None
    if res["status"] == "failed":
        text = failure_alert(state, t)
        if text:
            state["alerted"] = True
    elif res["status"] == "waiting":
        text = stuck_delivery_alert(res.get("ahead", []), t, state.get("stuck_alerted_for"))
        if text:
            state["stuck_alerted_for"] = res["ahead"][-1]["sha"]
    write_state(state, state_path)
    if text:
        alert(text)
    return res, text


def publish_loop(root: Path, *, sleep: Callable = time.sleep, clock: Callable = time.time, state_path: Path = STATE, lock: Path = LOCK, **kw) -> dict:
    """The detached process: attempt, and on a retriable failure sleep (backoff) and attempt again for up to GIVE_UP_S. Returns the last result. One loop at a time (the lock, refreshed on every turn)."""
    res: dict = {"status": "nothing", "detail": "another publisher is running", "ahead": []}
    for _round in range(3):
        if not acquire(lock):
            return res
        started = clock()
        try:
            while True:
                res, _text = attempt(root, state_path=state_path, **kw)
                if res["status"] != "failed" or not retriable(res.get("error_class", "unknown")) or clock() - started > GIVE_UP_S:
                    break
                sleep(backoff_s(int(read_state(state_path).get("attempts", 1))))
                try:
                    os.utime(lock)
                except OSError:
                    pass
        finally:
            release(lock)
        # a commit that arrived while this loop held the lock was refused by its own hook (the lock was taken): look once more now that it is free
        if res["status"] in ("pushed", "nothing") and ahead_commits(root, "origin/master"):
            continue
        return res
    return res


def watcher_pass(root: Path = ROOT, lock: Path = LOCK, **kw) -> Optional[str]:
    """One pass for the deploy watcher: nothing while the detached loop is alive, else one attempt. Returns the alert text when one is newly due (the watcher shows it), else None."""
    if lock_alive(lock):
        return None
    _res, text = attempt(root, **kw)
    return text


def spawn_detached(root: Path = ROOT) -> None:
    """Start the publisher as a detached process (used by the receipt command, whose passing receipt releases a waiting delivery). Never raises."""
    try:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=_DETACHED if os.name == "nt" else 0, start_new_session=(os.name != "nt"))
    except OSError:
        pass


def status_lines(root: Path = ROOT, state_path: Path = STATE, now: Optional[datetime] = None) -> list:
    """The lines a person or the preflight reads: what is unpublished and why. Reads git and the state file only."""
    now = now or datetime.now(timezone.utc)
    ahead = ahead_commits(root, "origin/master")
    state = read_state(state_path)
    if not ahead and not state.get("pending_sha"):
        return ["publication: nothing unpublished"]
    lines = []
    if ahead:
        age = int((now - ahead[0]["ts"]).total_seconds() // 60)
        lines.append(f"UNPUBLISHED: {len(ahead)} commit(s), oldest {age} min" + (" (includes a delivery waiting for its test receipt)" if any(c["closes"] for c in ahead) else ""))
    if state.get("pending_sha"):
        lines.append(f"publication failing since {str(state.get('first_failure_ts'))[:19]}: {state.get('error_class')} after {state.get('attempts')} attempt(s): {str(state.get('last_error'))[:160]}")
    return lines


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--retry", action="store_true", help="one attempt; prints an alert text when one is newly due")
    g.add_argument("--now", action="store_true", help="a hotfix: publish at once without waiting for a test receipt")
    g.add_argument("--status", action="store_true", help="print the pending state")
    args = ap.parse_args(argv)
    if args.status:
        print("\n".join(status_lines()))
        return RC_OK
    if args.retry:
        text = watcher_pass()
        if text:
            print(text)
        return RC_OK
    if args.now:
        res, text = attempt(ROOT, force=True)
        print(f"{res['status'].upper()}: {res['detail']}")
        if text:
            print(text)
        return RC_OK if res["status"] in ("pushed", "nothing") else RC_PERSON if not retriable(res.get("error_class", "unknown")) else RC_FAILED
    res = publish_loop(ROOT)
    print(f"{datetime.now().isoformat(timespec='seconds')} {res['status']}: {res['detail']}")
    return RC_OK if res["status"] != "failed" else RC_PERSON if not retriable(res.get("error_class", "unknown")) else RC_FAILED


if __name__ == "__main__":
    sys.exit(main())
