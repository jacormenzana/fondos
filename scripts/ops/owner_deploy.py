#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
owner_deploy.py -- applies the pending backlog DDL on the owner's CLICK, with no console (Backlog v3, owner decision 2026-11-01: "reduce the administrative tasks, no console").

    python scripts/ops/owner_deploy.py check              # is any DDL phase of master not live? prints the phases; exit 0 none, 1 pending
    python scripts/ops/owner_deploy.py init               # once: verify that the live backlog matches master NOW (the drift tool) and remember that commit
    python scripts/ops/owner_deploy.py watch              # the watcher: every N minutes fetch origin; when master brings DDL, show a window and ask
    python scripts/ops/owner_deploy.py install-startup    # run the watcher at your Windows logon (a .cmd in your Startup folder, no admin rights)
    python scripts/ops/owner_deploy.py remove-startup

What the window shows. The commits since the last deployed master, the phases to apply, and THE DDL DIFF itself (db/pg_backlog), because what you approve runs with the owner role and came through
an automatic merge: a click on a bare question would be a rubber stamp. Buttons: Apply now / Remind me in 4 h / Ignore this version.

What "Apply now" does (the same steps as AUTO, without the restore point and the rollback). Exports origin/master to a temporary folder (never your working tree), runs each pending phase of scripts/ops/migrate_backlog_v2.py --apply in order with the owner string taken
from the owner vault (shared/owner_vault.py) into THAT child process only, then runs the drift tool (scripts/ops/backlog_drift.py): only `no drift` counts as success and only then is the commit
remembered. Each phase is one transaction, so a failing phase leaves nothing half-applied; a later phase is not attempted after a failure. A receipt (no secrets) is kept in
%LOCALAPPDATA%\\fondos\\deploy_receipts. Phases that need a decision of yours (`revoke`, `iam`) are never offered here.

AUTO mode (Backlog v3.1, owner decision 2026-11-01: "no console, no click"; switch BACKLOG_AUTO_DEPLOY_MODE in shared/config.py, AUTO | ASK, default AUTO). The window above is the ASK mode and the
fallback. In AUTO a pending phase is applied with no window, behind gates that are all automatic: master only carries what the required CI checks passed (hermetic migration and rollback tests);
a restore point is created first (pg_create_restore_point; if it cannot be, nothing is applied and the window asks); each phase is one transaction; the drift tool must say `no drift`; on a
phase failure or on drift, the phases applied in this run are rolled back (`--down --apply`, newest first), an incident ticket is opened (shared.backlog_client.report_incident) and the window
asks for that version from then on. A success is a self-closing notice and a receipt. The DEPLOY fact (what lets the autopilot close tickets whose merge changed db/**) is recorded for the tickets
of the DDL commits through the scoped login backlog_facts (FONDOS_BACKLOG_PG_DSN_FACTS, owner vault). A change that the tests cannot see (a valid but wrong additive DDL) is NOT caught here: that
is the accepted risk of AUTO, and `ASK` is the switch back.
Step A-2 (`revoke`, which removes the direct write privileges of backlog_writer) is NOT part of the pending phases above. With BACKLOG_AUTO_REVOKE_ENABLED = True (off by default: it is the owner's
decision) the watcher also applies it, alone, through the same gates, once the shadow stage shows 50 non-system transitions since the last direct write, nothing else is pending and the privilege
is still live; a failed attempt is reported as an incident and never repeated by itself (`revoke_failed` in the state).
Honest limit: a guard, not a security boundary (NORMAS_BACKLOG 9.2 and 9.3): code running as your Windows user could read the vault and run the migration without this tool.
Exit codes: 0 ok / nothing pending, 1 pending (check) or drift remains (init), 2 an apply failed, 3 usage or environment error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backlog_drift as bd  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import owner_vault as ov  # noqa: E402

RC_OK, RC_PENDING, RC_FAILED, RC_USAGE = 0, 1, 2, 3
OWNER_VAR = "FONDOS_PG_DSN_OWNER"
FACTS_VAR = "FONDOS_BACKLOG_PG_DSN_FACTS"
# Creates the restore point in a CHILD process that holds the owner string (pg_create_restore_point needs the owner role; the name is argv[1], the string never appears on a command line).
RESTORE_POINT_CODE = ("import os, sys, psycopg\n"
                      "c = psycopg.connect(os.environ[" + repr(OWNER_VAR) + "], connect_timeout=10, autocommit=True)\n"      # built from the constant: this process never reads the string from its own environment
                      "print('RESTORE_POINT', c.execute('SELECT pg_create_restore_point(%s)', (sys.argv[1],)).fetchone()[0])\n")
SNOOZE_HOURS = 4
DIFF_MAX_LINES = 400
DDL_DIR = "db/pg_backlog/"
_PHASE_IN_DRIFT = re.compile(r"--phase (\w+)")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ── pure ────────────────────────────────────────────────────────────────────────

def files_by_phase() -> dict:
    """{phase: {repo path of each DDL file}} for the phases the drift tool compares (the ones a click may apply). Pure."""
    return {ph: {DDL_DIR + f for f, _ in mig._blocks(mig.PHASES[ph]["up"])} for ph in bd.PHASES_IN_ORDER}


def pending_phases(changed_files: list) -> list:
    """The phases (in application order) whose DDL files are among `changed_files`. A change to the migration tool alone is not DDL. Pure."""
    changed = set(changed_files)
    return [ph for ph in bd.PHASES_IN_ORDER if files_by_phase()[ph] & changed]


def phases_from_drift_output(text: str) -> list:
    """The phases the drift tool tells the owner to re-apply (`--phase X`), in application order, without duplicates. Pure."""
    found = set(_PHASE_IN_DRIFT.findall(text or ""))
    return [ph for ph in bd.PHASES_IN_ORDER if ph in found]


def state_path(environ: Optional[dict] = None) -> Path:
    env = os.environ if environ is None else environ
    if env.get("FONDOS_DEPLOY_STATE"):
        return Path(env["FONDOS_DEPLOY_STATE"])
    return Path(env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")) / "fondos" / "deploy_state.json"


def read_state(path: Path) -> dict:
    """{'deployed_ref': sha | None, 'snoozed_until': {sha: iso}, 'ignored': [sha], 'auto_failed': [sha]}; a missing or damaged file is the empty state. `auto_failed` = the versions whose
    automatic apply failed: they are offered in the window from then on, never retried by themselves."""
    base = {"deployed_ref": None, "snoozed_until": {}, "ignored": [], "auto_failed": [], "revoke_failed": False}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {**base, **{k: data[k] for k in base if k in data}}
    except (OSError, ValueError):
        return base


def write_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def prompt_action(state: dict, head: str, pending: list, now: datetime) -> str:
    """NONE (nothing pending, or the owner chose to ignore this version), WAIT (snoozed) or ASK. Pure."""
    if not pending or head in state.get("ignored", []):
        return "NONE"
    until = state.get("snoozed_until", {}).get(head)
    if until and datetime.fromisoformat(until) > now:
        return "WAIT"
    return "ASK"


def summary(head: str, deployed: Optional[str], pending: list, commits: list, diff_text: str, drift_text: str = "") -> str:
    """The text of the window: what master brings, the commits and the DDL diff (capped), so the click is an informed one. Pure."""
    lines = [f"origin/master {head[:10]} has DDL that the live backlog does not have.", "", f"Phases to apply, in this order: {', '.join(pending)}", ""]
    if deployed:
        lines.append(f"Commits since the last deployed master ({deployed[:10]}):")
        lines += [f"  {sha[:8]}  {subject}  ({author})" for sha, subject, author in commits] or ["  (none listed)"]
        lines.append("")
    if drift_text:
        lines += ["What the drift tool reports (live backlog compared with master's DDL):", drift_text.strip(), ""]
    body = diff_text.splitlines()
    lines.append(f"DDL changes (db/pg_backlog), first {DIFF_MAX_LINES} lines of {len(body)}:" if body else "DDL changes: (no textual diff available: first deployment of this tool)")
    lines += body[:DIFF_MAX_LINES]
    if len(body) > DIFF_MAX_LINES:
        lines.append(f"... {len(body) - DIFF_MAX_LINES} more lines: `git diff {(deployed or '')[:10]} {head[:10]} -- db/pg_backlog`")
    lines += ["", "Apply now runs each phase as one transaction with the owner role, then verifies with the drift tool."]
    return "\n".join(lines)


def deploy_mode(value) -> str:
    """AUTO or ASK from the configured BACKLOG_AUTO_DEPLOY_MODE; anything else (a typo, a missing value) is ASK, the safe one: a person decides. Pure."""
    v = str(value).strip().upper()
    return v if v in ("AUTO", "ASK") else "ASK"


def restore_point_name(head: str, now: datetime) -> str:
    """The name of the restore point of a deployment (a PostgreSQL name is at most 63 bytes). Pure."""
    return f"backlog_deploy_{head[:10]}_{now.strftime('%Y%m%dT%H%M%SZ')}"


def ddl_tickets(log_text: str) -> list:
    """The tickets (`Refs:` / `Closes:` trailers) of the commits in `log_text` (git log --format=%B%x1e of the DDL commits), sorted. These are the tickets whose DDL is live after a deployment. Pure."""
    from shared import backlog_gate as gate
    found = set()
    for msg in log_text.split("\x1e"):
        if msg.strip():
            found |= set(gate.parse_message(msg).refs)
    return sorted(found)


def scrub(text: str, secrets: list) -> str:
    """Replaces every secret value in `text` with *** (a tool might echo a connection string in an error). Pure."""
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def startup_cmd_path(environ: Optional[dict] = None) -> Path:
    env = os.environ if environ is None else environ
    appdata = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "fondos_owner_deploy.cmd"


def startup_cmd_text(pythonw: str, script: str) -> str:
    """The launcher placed in the user's Startup folder (outside the repository): starts the watcher minimised. Pure."""
    return ("@echo off\r\n"
            "rem Generated by scripts/ops/owner_deploy.py install-startup. Remove it with: python scripts/ops/owner_deploy.py remove-startup\r\n"
            f'start "" /min "{pythonw}" "{script}" watch\r\n')


# ── I/O ────────────────────────────────────────────────────────────────────────

def _git(*args, cwd: Path = ROOT, timeout: int = 120) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=timeout, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def git_head(ref: str = "origin/master") -> str:
    return _git("rev-parse", ref).strip()


def git_changed(a: str, b: str) -> list:
    return [p for p in _git("diff", "--name-only", a, b, "--", "db/pg_backlog").splitlines() if p]


def git_commits(a: str, b: str) -> list:
    out = _git("log", f"{a}..{b}", "--format=%H%x1f%s%x1f%an", "--", "db/pg_backlog")
    return [tuple(line.split("\x1f")) for line in out.splitlines() if line.count("\x1f") == 2]


def git_diff(a: str, b: str) -> str:
    return _git("diff", a, b, "--", "db/pg_backlog")


def git_ddl_log(a: str, b: str) -> str:
    """The full messages (separated by \\x1e) of the commits between a and b that touch the backlog DDL."""
    return _git("log", f"{a}..{b}", "--format=%B%x1e", "--", "db/pg_backlog")


def git_fetch() -> None:
    _git("fetch", "-q", "origin", timeout=90)


def export_tree(ref: str, dest: Path) -> None:
    """`git archive` of `ref` extracted into `dest`: the migration runs from a clean copy of master, never from the working tree (which may be on any branch or dirty)."""
    r = subprocess.run(["git", "archive", "--format=tar", ref], cwd=ROOT, capture_output=True, timeout=180, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"git archive failed: {r.stderr.decode('utf-8', 'replace')[:200]}")
    import io
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
        tar.extractall(dest)


def run_cmd(cmd: list, env: dict, cwd: Path) -> tuple:
    """(returncode, last 18 lines of the combined output). No console window."""
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, creationflags=_NO_WINDOW)
    tail = "\n".join((r.stdout + "\n" + r.stderr).strip().splitlines()[-18:])
    return r.returncode, tail


def python_exe() -> str:
    """python.exe next to the running interpreter (the watcher itself runs under pythonw)."""
    exe = Path(sys.executable)
    cand = exe.with_name("python.exe")
    return str(cand if cand.exists() else exe)


@dataclass
class Outcome:
    ok: bool
    stage: str                                   # "applied", a phase name, "drift", "vault", "export", "restore_point"
    applied: list = field(default_factory=list)
    detail: str = ""
    rolled_back: list = field(default_factory=list)   # AUTO: the phases undone after a failure
    fallback: bool = False                       # AUTO: nothing was applied and a person should be asked (no restore point, no vault string)
    auto: bool = False
    facts: str = ""                              # AUTO: what happened with the DEPLOY facts


def _roll_back(applied: list, python: str, tree: Path, env: dict, run: Callable, log: list, secret: str) -> list:
    """Undo the phases applied in this run, newest first (`--down --apply`, one transaction each). Stops at the first rollback that fails and returns the phases actually undone."""
    undone: list = []
    for ph in reversed(applied):
        rc, tail = run([python, str(tree / "scripts" / "ops" / "migrate_backlog_v2.py"), "--phase", ph, "--down", "--apply"], env, tree)
        log.append({"step": f"rollback {ph}", "rc": rc, "tail": scrub(tail, [secret])})
        if rc != 0:
            break
        undone.append(ph)
    return undone


def apply_pending(head: str, pending: list, *, vault, state_file: Path, run: Callable = run_cmd, export: Callable = export_tree, python: Optional[str] = None,
                  env_file: Optional[Path] = None, receipts_dir: Optional[Path] = None, now: Callable = lambda: datetime.now(timezone.utc), auto: bool = False,
                  remember: bool = True) -> Outcome:
    """Apply `pending` phases of `head` in order and verify with the drift tool. Success only when the drift tool says `no drift`; only then the state remembers `head`. The owner string goes into the
    environment of the migration children only (never the drift child, never this process's environment) and is scrubbed from any output kept.
    auto=True (AUTO mode) adds the automatic gates: a restore point before anything is applied (if it cannot be created NOTHING is applied and the Outcome asks for a person: fallback=True), and, when a
    phase fails or the drift tool reports drift, the rollback of the phases applied in this run. If the drift tool could not read the live backlog the apply is NOT rolled back (it cannot be judged
    wrong): it is reported as unverified and the version is not remembered."""
    python = python or python_exe()
    try:
        secret = vault.get(OWNER_VAR)
    except ov.VaultError as exc:
        return Outcome(False, "vault", detail=str(exc), fallback=auto, auto=auto)
    if not secret:
        return Outcome(False, "vault", detail=f"{OWNER_VAR} is not in the owner vault. Store it once: python scripts/ops/owner_vault.py set {OWNER_VAR}", fallback=auto, auto=auto)
    base_env = {**os.environ, "FONDOS_ENV_FILE": str(env_file or (ROOT / ".env")), "PYTHONIOENCODING": "utf-8"}
    base_env.pop(OWNER_VAR, None)
    owner_env = {**base_env, OWNER_VAR: secret}
    applied: list = []
    rolled: list = []
    with tempfile.TemporaryDirectory(prefix="fondos_deploy_") as tmp:
        tree = Path(tmp)
        try:
            export(head, tree)
        except Exception as exc:                                              # noqa: BLE001
            return Outcome(False, "export", detail=f"{type(exc).__name__}: {str(exc)[:200]}", auto=auto)
        log: list = []
        if auto:
            rc, tail = run([python, "-c", RESTORE_POINT_CODE, restore_point_name(head, now())], owner_env, tree)
            log.append({"step": "restore point", "rc": rc, "tail": scrub(tail, [secret])})
            if rc != 0:
                _receipt(receipts_dir, now, head, pending, applied, log, False)
                return Outcome(False, "restore_point", detail=scrub(tail, [secret]), fallback=True, auto=True)
        for ph in pending:
            rc, tail = run([python, str(tree / "scripts" / "ops" / "migrate_backlog_v2.py"), "--phase", ph, "--apply"], owner_env, tree)
            log.append({"step": f"apply {ph}", "rc": rc, "tail": scrub(tail, [secret])})
            if rc != 0:
                if auto:
                    rolled = _roll_back(applied, python, tree, owner_env, run, log, secret)
                _receipt(receipts_dir, now, head, pending, applied, log, False)
                return Outcome(False, ph, applied, scrub(tail, [secret]), rolled_back=rolled, auto=auto)
            applied.append(ph)
        rc, tail = run([python, str(tree / "scripts" / "ops" / "backlog_drift.py")], base_env, tree)
        log.append({"step": "drift", "rc": rc, "tail": scrub(tail, [secret])})
        if rc == 1 and auto:                                                  # drift present after the apply: the apply is wrong, undo it
            rolled = _roll_back(applied, python, tree, owner_env, run, log, secret)
    ok = rc == 0
    _receipt(receipts_dir, now, head, pending, applied, log, ok)
    if not ok:
        return Outcome(False, "drift", applied, scrub(tail, [secret]), rolled_back=rolled, auto=auto)
    if remember:                                                               # a phase applied on its own (revoke) says nothing about the rest of master
        state = read_state(state_file)
        state.update(deployed_ref=head, snoozed_until={}, ignored=[], auto_failed=[])
        write_state(state_file, state)
    return Outcome(True, "applied", applied, scrub(tail, [secret]), auto=auto)


def _receipt(receipts_dir, now, head, pending, applied, log, ok) -> None:
    """A JSON receipt of what ran (no secret: the tails are scrubbed). Best effort."""
    try:
        d = Path(receipts_dir) if receipts_dir else state_path().parent / "deploy_receipts"
        d.mkdir(parents=True, exist_ok=True)
        stamp = now().strftime("%Y%m%dT%H%M%SZ")
        (d / f"{stamp}_{head[:8]}.json").write_text(json.dumps({"head": head, "pending": pending, "applied": applied, "ok": ok, "steps": log}, indent=1), encoding="utf-8")
    except OSError:
        pass


def verify_now(head: str, *, run: Callable = run_cmd, export: Callable = export_tree, python: Optional[str] = None, env_file: Optional[Path] = None) -> tuple:
    """(rc, output tail) of the drift tool run from a clean export of `head`: 0 no drift, 1 drift, 2 could not read. Read-only; needs no owner string."""
    python = python or python_exe()
    env = {**os.environ, "FONDOS_ENV_FILE": str(env_file or (ROOT / ".env")), "PYTHONIOENCODING": "utf-8"}
    env.pop(OWNER_VAR, None)
    with tempfile.TemporaryDirectory(prefix="fondos_drift_") as tmp:
        export(head, Path(tmp))
        return run([python, str(Path(tmp) / "scripts" / "ops" / "backlog_drift.py")], env, Path(tmp))


def record_facts(head: str, deployed: Optional[str], applied: list, *, vault, ddl_log: Callable, record: Callable, environ=None, now: Callable = lambda: datetime.now(timezone.utc)) -> str:
    """Record the DEPLOY fact of every ticket cited by the DDL commits between `deployed` and `head`, through the scoped login (FACTS_VAR from the owner vault, put into `environ` for these calls
    only). It returns one line for the receipt and never raises: a missing string or a failed write leaves the autopilot waiting for the fact, which is visible and harmless."""
    environ = os.environ if environ is None else environ
    if not deployed:
        return "DEPLOY facts not recorded: no previously deployed commit to list the DDL tickets from"
    try:
        tickets = ddl_tickets(ddl_log(deployed, head))
        dsn = vault.get(FACTS_VAR)
    except Exception as exc:                                                   # noqa: BLE001
        return f"DEPLOY facts not recorded: {type(exc).__name__}: {str(exc)[:120]}"
    if not tickets:
        return "DEPLOY facts: no ticket cites the DDL commits"
    if not dsn:
        return f"DEPLOY facts NOT recorded for {', '.join(tickets)}: {FACTS_VAR} is not in the owner vault (once: python scripts/ops/owner_vault.py set {FACTS_VAR})"
    from shared import backlog_client as bc
    new, failed = 0, []
    previous = environ.get(bc._FACTS_DSN_ENV_VAR)
    environ[bc._FACTS_DSN_ENV_VAR] = dsn
    try:
        for t in tickets:
            try:
                new += bool(record(f"deploy-{head[:12]}", t, {"head": head, "phases": applied}, now()))
            except Exception as exc:                                           # noqa: BLE001
                failed.append(f"{t} ({type(exc).__name__})")
    finally:
        if previous is None:
            environ.pop(bc._FACTS_DSN_ENV_VAR, None)
        else:
            environ[bc._FACTS_DSN_ENV_VAR] = previous
    return f"DEPLOY facts: {new} new for {len(tickets)} ticket(s)" + (f"; FAILED for {', '.join(failed)}" if failed else "")


def report_failure(o: "Outcome", head: str, report: Callable) -> Optional[str]:
    """Open (or add to) an incident ticket for a failed AUTOMATIC deployment, so the failure has an owner in the backlog and nobody has to notice a window. Never raises. Returns the ticket id."""
    text = (f"The automatic backlog DDL deployment of master {head[:10]} failed at {o.stage}. Applied in this run: {', '.join(o.applied) or 'nothing'}. Rolled back: "
            f"{', '.join(o.rolled_back) or 'nothing'}. The version is offered in the approval window from now on (it is not retried by itself). Output: {o.detail[:700]}")
    try:
        return report(object_name="scripts/ops/owner_deploy.py", scenario_description=text, object_type="JOB", title=f"Automatic backlog DDL deploy failed at {o.stage} ({head[:10]})")
    except Exception:                                                          # noqa: BLE001
        return None


def auto_deploy(head: str, pending: list, deployed: Optional[str], *, vault, state_file: Path, ddl_log: Callable, record: Callable, apply: Callable = apply_pending, **kw) -> Outcome:
    """The whole AUTO deployment: the gated apply (restore point, phases, drift, rollback) and, after a verified success, the DEPLOY facts. Returns the Outcome (`facts` carries the facts line)."""
    o = apply(head, pending, vault=vault, state_file=state_file, auto=True, **kw)
    if o.ok:
        o.facts = record_facts(head, deployed, o.applied, vault=vault, ddl_log=ddl_log, record=record)
    return o


REVOKE_PHASE = "revoke"


def revoke_due(enabled: bool, progress, privilege_live, deployed_ref, head: str, required: int = 50) -> tuple:
    """(due, why) for step A-2 (`revoke`) in AUTO mode. Due only when the switch is on, everything else is deployed (the remembered commit is master's), the writer still holds the direct UPDATE
    privilege (so it is not applied yet) and the shadow stage shows `required` non-system transitions since the last direct write (the migrate tool refuses it before that anyway). Pure."""
    if not enabled:
        return False, "BACKLOG_AUTO_REVOKE_ENABLED is off"
    if deployed_ref != head:
        return False, "other DDL of master is not deployed yet: revoke waits so the drift check judges only itself"
    if privilege_live is not True:
        return False, "the direct write privilege is already revoked (or unknown)"
    done = (progress or {}).get("live_non_system_since_last_v00")
    if done is None or done < required:
        return False, f"the shadow stage shows {done} of {required} non-system transitions"
    return True, f"the shadow stage shows {done} of {required} non-system transitions and nothing else is pending"


def revoke_pass(*, state_file: Path, enabled: bool, head_fn: Callable, progress_fn: Callable, privilege_fn: Callable, apply: Callable, notify: Callable, report: Optional[Callable] = None) -> str:
    """One pass of the A-2 trigger of the watcher: none | skipped | applied | failed | error. A failed attempt is remembered (`revoke_failed`) and never repeated by itself: a person looks at it."""
    if not enabled:
        return "none"
    state = read_state(state_file)
    if state.get("revoke_failed"):
        return "skipped"
    try:
        head = head_fn()
        due, _why = revoke_due(enabled, progress_fn(), privilege_fn(), state.get("deployed_ref"), head)
    except Exception as exc:                                                   # noqa: BLE001 -- unreadable state is not a reason to revoke anything
        return f"error: {type(exc).__name__}"
    if not due:
        return "none"
    outcome = apply(head, [REVOKE_PHASE])
    if not outcome.ok and not outcome.fallback:
        state = read_state(state_file)
        state["revoke_failed"] = True
        write_state(state_file, state)
        if report is not None:
            report_failure(outcome, head, report)
    if outcome.fallback:                                                       # no restore point, no vault string: nothing was applied, try again next pass
        return "skipped"
    notify(outcome)
    return "applied" if outcome.ok else "failed"


# ── the watcher ───────────────────────────────────────────────────────────────

def decide_pending(state: dict, head: str, *, changed: Callable, drift: Callable) -> tuple:
    """(pending phases, drift text, deployed ref). With a remembered deployed commit the phases are the DDL files that changed since it; without one the drift tool is the judge (read-only)."""
    deployed = state.get("deployed_ref")
    if deployed:
        return pending_phases(changed(deployed, head)), "", deployed
    rc, text = drift(head)
    if rc == 0:
        return [], "", None                                                    # live matches master: the caller remembers `head`
    return phases_from_drift_output(text), text, None


def watch_once(*, state_file: Path, fetch: Callable, head_fn: Callable, changed: Callable, commits: Callable, diff: Callable, drift: Callable, ask: Callable,
               apply: Callable, notify: Callable, now: Callable = lambda: datetime.now(timezone.utc), mode: str = "ASK", apply_auto: Optional[Callable] = None,
               report: Optional[Callable] = None) -> str:
    """One pass of the watcher. Returns what happened: none | wait | later | ignored | applied | failed | error. `ask(text)` must return 'apply' | 'later' | 'ignore' from a PERSON.
    mode AUTO (with `apply_auto(head, pending, deployed)` -> Outcome): a pending version is applied with no window; a failure is reported (`report`, an incident), remembered in the state
    (`auto_failed`) and the version is offered in the window from then on; an Outcome with fallback=True (no restore point, no vault string) applied nothing and goes straight to the window."""
    try:
        fetch()
        head = head_fn()
        state = read_state(state_file)
        pending, drift_text, deployed = decide_pending(state, head, changed=changed, drift=drift)
        if not deployed and not pending:
            state.update(deployed_ref=head)
            write_state(state_file, state)
            return "none"
    except Exception as exc:                                                   # noqa: BLE001 -- a failing fetch or git must never produce a window
        return f"error: {type(exc).__name__}"
    action = prompt_action(state, head, pending, now())
    if action == "NONE":
        return "none"
    if action == "WAIT":
        return "wait"
    if mode == "AUTO" and apply_auto is not None and head not in state.get("auto_failed", []):
        outcome = apply_auto(head, pending, deployed)
        if not outcome.fallback:
            if not outcome.ok:
                state = read_state(state_file)
                state["auto_failed"] = sorted(set(state.get("auto_failed", [])) | {head})
                write_state(state_file, state)
                if report is not None:
                    report_failure(outcome, head, report)
            notify(outcome)
            return "applied" if outcome.ok else "failed"
        drift_text = f"THE AUTOMATIC DEPLOYMENT DID NOT RUN ({outcome.stage}): {outcome.detail}\n\n" + drift_text      # the window says why a person is asked
    answer = ask(summary(head, deployed, pending, commits(deployed, head) if deployed else [], diff(deployed, head) if deployed else "", drift_text))
    if answer == "ignore":
        state["ignored"] = sorted(set(state.get("ignored", [])) | {head})
        write_state(state_file, state)
        return "ignored"
    if answer != "apply":
        state.setdefault("snoozed_until", {})[head] = (now() + timedelta(hours=SNOOZE_HOURS)).isoformat()
        write_state(state_file, state)
        return "later"
    outcome = apply(head, pending)
    notify(outcome)
    return "applied" if outcome.ok else "failed"


# ── windows (thin; the logic above is what the tests cover) ───────────────────────

def ask_window(text: str) -> str:
    """The approval window: the summary in a scrollable box and three buttons. Closing the window means 'later'."""
    import tkinter as tk
    from tkinter import scrolledtext
    root = tk.Tk()
    root.title("Fondos: backlog DDL pending")
    root.geometry("980x640")
    root.attributes("-topmost", True)
    box = scrolledtext.ScrolledText(root, wrap="none", font=("Consolas", 9))
    box.insert("1.0", text)
    box.configure(state="disabled")
    box.pack(fill="both", expand=True)
    answer = {"v": "later"}

    def done(v: str) -> None:
        answer["v"] = v
        root.destroy()

    bar = tk.Frame(root)
    bar.pack(fill="x")
    tk.Button(bar, text="Apply now", width=16, command=lambda: done("apply")).pack(side="left", padx=8, pady=8)
    tk.Button(bar, text=f"Remind me in {SNOOZE_HOURS} h", width=18, command=lambda: done("later")).pack(side="left")
    tk.Button(bar, text="Ignore this version", width=18, command=lambda: done("ignore")).pack(side="left", padx=8)
    root.protocol("WM_DELETE_WINDOW", lambda: done("later"))
    root.mainloop()
    return answer["v"]


def work_window(work: Callable) -> "Outcome":
    """Runs `work()` (the apply, about two minutes) in a thread under a progress window, then returns its Outcome."""
    import threading
    import tkinter as tk
    root = tk.Tk()
    root.title("Fondos: applying the backlog DDL")
    root.geometry("460x110")
    root.attributes("-topmost", True)
    tk.Label(root, text="Applying the pending phases and verifying with the drift tool.\nThis takes about two minutes; do not close this window.", pady=20).pack()
    box: dict = {}

    def target() -> None:
        try:
            box["o"] = work()
        except Exception as exc:                                               # noqa: BLE001
            box["o"] = Outcome(False, "error", detail=f"{type(exc).__name__}: {str(exc)[:200]}")

    t = threading.Thread(target=target, daemon=True)
    t.start()

    def poll() -> None:
        if t.is_alive():
            root.after(500, poll)
        else:
            root.destroy()

    root.after(500, poll)
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    root.mainloop()
    return box["o"]


def result_window(o: "Outcome") -> None:
    import tkinter as tk
    from tkinter import scrolledtext
    root = tk.Tk()
    root.title("Fondos: backlog DDL " + ("applied" if o.ok else "FAILED"))
    root.geometry("820x420")
    root.attributes("-topmost", True)
    head = (f"Applied: {', '.join(o.applied) or 'nothing'}.\nThe live backlog matches master (no drift)." if o.ok
            else f"FAILED at {o.stage}. Applied before the failure: {', '.join(o.applied) or 'nothing'}."
                 + (f"\nRolled back automatically: {', '.join(o.rolled_back) or 'nothing'}. An incident ticket was opened; this version is offered in the approval window from now on." if o.auto else "")
                 + "\nEach phase is one transaction, so nothing is half-applied. Output:")
    box = scrolledtext.ScrolledText(root, wrap="word", font=("Consolas", 9))
    box.insert("1.0", head + "\n\n" + o.detail)
    box.configure(state="disabled")
    box.pack(fill="both", expand=True)
    tk.Button(root, text="OK", width=12, command=root.destroy).pack(pady=8)
    root.mainloop()


def toast_window(o: "Outcome", seconds: int = 20) -> None:
    """A small notice that closes by itself: the automatic deployment succeeded and nobody has to do anything."""
    import tkinter as tk
    root = tk.Tk()
    root.title("Fondos: backlog DDL applied automatically")
    root.geometry("520x150+40+40")
    root.attributes("-topmost", True)
    tk.Label(root, text=f"Applied: {', '.join(o.applied) or 'nothing'}.\nThe live backlog matches master (no drift).\n{o.facts}", justify="left", wraplength=480, pady=14).pack()
    root.after(seconds * 1000, root.destroy)
    root.mainloop()


def ask_console(text: str) -> str:
    """Fallback when no window can be shown (needs a person at a console)."""
    print(text)
    a = input("\nApply now? [y = apply / l = later / i = ignore this version] > ").strip().lower()
    return {"y": "apply", "i": "ignore"}.get(a, "later")


# ── single instance ─────────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, creationflags=_NO_WINDOW)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(path: Path, pid: Optional[int] = None, alive: Callable = _pid_alive) -> bool:
    """True when this process now holds the watcher lock; False when another live watcher does. A lock of a dead process is taken over."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pid = pid or os.getpid()
    try:
        old = int(path.read_text(encoding="utf-8").strip())
        if old != pid and alive(old):
            return False
    except (OSError, ValueError):
        pass
    path.write_text(str(pid), encoding="utf-8")
    return True


# ── commands ─────────────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("init")
    w = sub.add_parser("watch")
    w.add_argument("--interval-min", type=float, default=10.0, help="minutes between passes (default 10)")
    w.add_argument("--once", action="store_true", help="a single pass, then exit")
    w.add_argument("--console", action="store_true", help="ask in this console instead of a window")
    sub.add_parser("install-startup")
    sub.add_parser("remove-startup")
    args = ap.parse_args(argv)
    sf = state_path()

    if args.cmd == "install-startup":
        p = startup_cmd_path()
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(startup_cmd_text(str(pythonw if pythonw.exists() else sys.executable), str(Path(__file__).resolve())), encoding="utf-8", newline="")
        print(f"startup launcher written: {p}\nThe watcher starts at your next Windows logon (or run it now: python scripts/ops/owner_deploy.py watch).")
        return RC_OK
    if args.cmd == "remove-startup":
        p = startup_cmd_path()
        if p.exists():
            p.unlink()
            print(f"startup launcher removed: {p}")
        else:
            print("there was no startup launcher")
        return RC_OK

    try:
        git_fetch()
        head = git_head()
    except Exception as exc:                                                   # noqa: BLE001
        print(f"error: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return RC_USAGE

    if args.cmd == "init":
        rc, text = verify_now(head)
        print(text)
        if rc == 0:
            st = read_state(sf)
            st["deployed_ref"] = head
            write_state(sf, st)
            print(f"\nthe live backlog matches origin/master {head[:10]}: remembered. The watcher will ask only when master brings new DDL.")
            return RC_OK
        print(f"\nnot remembered: the drift tool returned {rc}. Pending phases: {', '.join(phases_from_drift_output(text)) or '(see above)'}")
        return RC_PENDING
    if args.cmd == "check":
        st = read_state(sf)
        if not st.get("deployed_ref"):
            print("no deployed commit remembered yet: run `python scripts/ops/owner_deploy.py init` once.")
            return RC_USAGE
        pend = pending_phases(git_changed(st["deployed_ref"], head))
        print(f"deployed {st['deployed_ref'][:10]}, master {head[:10]}: " + (f"PENDING phases: {', '.join(pend)}" if pend else "nothing pending"))
        return RC_PENDING if pend else RC_OK

    lock = sf.with_name("owner_deploy.lock")
    if not acquire_lock(lock):
        print("another watcher is running (lock held).", file=sys.stderr)
        return RC_USAGE
    ask = ask_console if args.console else ask_window
    vault = ov.Vault()
    from shared import config as cfg                                           # also loads .env (the incident DSN) -- after the lock and the git checks, never for `check` or `init`
    from shared import backlog_client as bc
    mode = deploy_mode(getattr(cfg, "BACKLOG_AUTO_DEPLOY_MODE", "ASK"))

    def do_apply(h: str, pend: list) -> Outcome:
        job = lambda: apply_pending(h, pend, vault=vault, state_file=sf)       # noqa: E731
        return job() if args.console else work_window(job)

    def do_apply_auto(h: str, pend: list, deployed: Optional[str]) -> Outcome:
        return auto_deploy(h, pend, deployed, vault=vault, state_file=sf, ddl_log=git_ddl_log, record=bc.record_deploy_fact)

    def notify(o: Outcome) -> None:
        extra = f" | rolled back: {', '.join(o.rolled_back)}" if o.rolled_back else ""
        print((("APPLIED: " + ", ".join(o.applied) + (f" | {o.facts}" if o.facts else "")) if o.ok else f"FAILED at {o.stage}: {o.detail}{extra}"))
        if args.console:
            return
        if o.ok and o.auto:
            toast_window(o)                                                    # an unattended success closes by itself: nothing to click
        else:
            result_window(o)

    revoke_enabled = mode == "AUTO" and bool(getattr(cfg, "BACKLOG_AUTO_REVOKE_ENABLED", False))

    def do_apply_revoke(h: str, pend: list) -> Outcome:
        return apply_pending(h, pend, vault=vault, state_file=sf, auto=True, remember=False)

    print(f"owner_deploy watcher: mode {mode}" + ("; A-2 revoke will be applied by itself when its gate holds" if revoke_enabled else ""))
    while True:
        res = watch_once(state_file=sf, fetch=git_fetch, head_fn=git_head, changed=git_changed, commits=git_commits, diff=git_diff, drift=lambda h: verify_now(h),
                         ask=ask, apply=do_apply, notify=notify, mode=mode, apply_auto=do_apply_auto, report=bc.report_incident)
        if revoke_enabled and res in ("none", "applied"):
            rres = revoke_pass(state_file=sf, enabled=True, head_fn=git_head, progress_fn=bc.shadow_progress, privilege_fn=bc.raw_update_privilege_live, apply=do_apply_revoke, notify=notify,
                               report=bc.report_incident)
            if rres not in ("none", "skipped"):
                print(f"revoke: {rres}")
        if args.once:
            print(res)
            return RC_FAILED if res == "failed" else RC_OK
        time.sleep(max(1.0, args.interval_min * 60))


if __name__ == "__main__":
    sys.exit(main())
