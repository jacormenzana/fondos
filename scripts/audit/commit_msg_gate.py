#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
commit_msg_gate.py -- the commit-msg hook (Backlog v2 Phase C slice 2, FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.1; NORMAS_BACKLOG.md 4.5).
Installed by `bash scripts/audit/commit-msg.sh` as .git/hooks/commit-msg; git calls it with the path of the message file.

In order, for a commit whose subject or `Refs:` trailer cites a ticket (a commit that cites none is left alone, with no database call):
  1. tokens: a malformed id (`fnd0193`, `FND_0193`, `FND-193`) is REJECTED (exit 1, nothing else happens: it is a typing mistake that fixes itself); an id that is not in the
     backlog is WARNED on stderr;
  2. collision check: a stub until slice 3 (PROPUESTA 6.4.2);
  3. a `Refs: FND-nnnn` trailer on a ticket in OPEN or TODO moves it to IN_PROGRESS as system:gate (switch BACKLOG_AUTO_FORWARD_ENABLED); never from BLOCKED or DEFERRED,
     never backward, never from a mention in the subject;
  4. a backlog.gate_check row (sp_gate_check) and the trailer `Backlog-Gate: <nonce>`, the nonce bound to the staged tree (`git write-tree`; an old trailer, as after
     `git commit --amend`, is replaced).

The hook never blocks a commit except for step 1 and, when the switch BACKLOG_REQUIRE_TICKET_ENABLED is on (off by default), a commit that cites no ticket and is not a merge, a
revert, a `chore` commit or on a `chore/` branch. Backlog unreachable, more than BUDGET_S seconds, any error: FAIL OPEN with `Backlog-Gate: offline`; backlog_sync.py does the
move and the check later (pending, no penalty).

Live writes. The hook is non-interactive (it detaches stdin, so nothing ever prompts) and writes ONLY through its scoped login (owner authorization of 2026-10-31, the allow-listed
operation `gate`): FONDOS_BACKLOG_PG_DSN_GATE, the role backlog_gate, which can execute fn_gate_statuses, sp_gate_forward and sp_gate_check and nothing else. Without that
variable there is no fallback to another login: the commit simply carries `offline`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTOR = "system:gate"
BUDGET_S = 3.0
FORWARD_FROM = ("OPEN", "TODO")


class GateOffline(Exception):
    """The backlog could not be reached or may not be written from here: the commit gets `Backlog-Gate: offline`."""


def collision_check(msg, tree: str) -> list:
    """Slice 3 (PROPUESTA 6.4.2): the pairs this commit would collide on. A stub until then: no pair."""
    return []


class LiveStore:
    """The backlog as the hook sees it, through shared.backlog_client. Raises GateOffline when the backlog cannot be reached or written to."""

    def __init__(self):
        sys.path.insert(0, str(ROOT))
        import shared.config as cfg                    # loads .env (FONDOS_BACKLOG_PG_DSN)
        from shared import backlog_client as bc
        self.bc = bc
        self.forward_enabled = bool(cfg.BACKLOG_AUTO_FORWARD_ENABLED)

    def _guard(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except self.bc.BacklogUnavailable as exc:      # no DSN, no driver, live write refused
            raise GateOffline(type(exc).__name__) from exc
        except OSError as exc:
            raise GateOffline(type(exc).__name__) from exc
        except Exception as exc:                       # noqa: BLE001  (driver errors: connection refused, timeouts)
            if type(exc).__module__.startswith("psycopg") and "Operational" in type(exc).__name__:
                raise GateOffline(type(exc).__name__) from exc
            raise

    def statuses(self, ids: list) -> dict:
        return self._guard(self.bc.gate_statuses, list(ids))

    def forward(self, ap_id: str, comment: str) -> None:
        self._guard(self.bc.gate_forward, ap_id, self.bc.make_session_id("BACKLOG_GATE", "SYNC"), comment, actor=ACTOR)

    def gate_check(self, tree: str, ap_ids: list) -> str:
        return self._guard(self.bc.gate_check, tree, list(ap_ids), actor=ACTOR)


def staged_tree() -> str:
    """The tree git is about to commit (honours GIT_INDEX_FILE, so `git commit -a` / `-- paths` are covered)."""
    r = subprocess.run(["git", "write-tree"], capture_output=True, text=True, encoding="utf-8", timeout=2)
    if r.returncode != 0 or not r.stdout.strip():
        raise GateOffline("git write-tree failed")
    return r.stdout.strip()


def write_trailer(path: Path, line: str) -> None:
    """Set the Backlog-Gate trailer of the message file, replacing an earlier one (as after --amend)."""
    key, value = line.split(": ", 1)
    r = subprocess.run(["git", "interpret-trailers", "--in-place", "--if-exists", "replace", "--trailer", f"{key}: {value}", str(path)],
                       capture_output=True, text=True, encoding="utf-8", timeout=2)
    if r.returncode != 0:                              # git without interpret-trailers: do it by hand
        from shared import backlog_gate as g
        lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").rstrip("\n").split("\n") if not ln.lower().startswith(f"{key.lower()}:")]
        body = "\n".join(lines).rstrip("\n")
        path.write_text(f"{body}\n{line}\n" if g._trailer_block(body.split("\n")) else f"{body}\n\n{line}\n", encoding="utf-8")


def git_context() -> tuple:
    """(current branch, merge in progress) for the strict-traceability exemptions; ("", False) when git cannot say."""
    try:
        b = subprocess.run(["git", "symbolic-ref", "--short", "-q", "HEAD"], capture_output=True, text=True, encoding="utf-8", timeout=2)
        m = subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], capture_output=True, text=True, encoding="utf-8", timeout=2)
        return b.stdout.strip(), m.returncode == 0
    except Exception:                                  # noqa: BLE001
        return "", False


def run(msg_path: str, *, store_factory=LiveStore, tree_fn=staged_tree, budget: float = BUDGET_S, err=None, require_ticket: bool = False, context_fn=git_context) -> int:
    """The hook. Returns the process exit code: 1 for a malformed ticket token, or (switch BACKLOG_REQUIRE_TICKET_ENABLED) for a commit that cites no ticket and is not
    exempt; otherwise 0."""
    from shared import backlog_gate as g
    err = err or sys.stderr
    path = Path(msg_path)
    msg = g.parse_message(path.read_text(encoding="utf-8", errors="replace"))
    if msg.malformed:
        print("commit-msg gate: REJECTED. Ticket token(s) not in the form FND-nnnn (four digits): "
              + ", ".join(msg.malformed) + ". Fix the message and commit again.", file=err)
        return 1
    if not msg.mentions:
        if require_ticket and g.ticket_exemption(msg.subject, *context_fn()) is None:
            print("commit-msg gate: REJECTED. No ticket cited (BACKLOG_REQUIRE_TICKET_ENABLED is on): put FND-nnnn in the subject or a `Refs: FND-nnnn` trailer. "
                  "Exempt: merge, revert, `chore:` commits and `chore/` branches.", file=err)
            return 1
        return 0
    box: dict = {}

    def work():
        try:
            tree = tree_fn()
            store = store_factory()
            statuses = store.statuses(msg.mentions)
            box["unknown"] = [a for a in msg.mentions if a not in statuses]
            box["pairs"] = collision_check(msg, tree)
            if store.forward_enabled:
                for ap in msg.refs:
                    if statuses.get(ap) in FORWARD_FROM:
                        store.forward(ap, f"commit-msg gate: work started (Refs: trailer) - {msg.subject[:160]}")
                        box.setdefault("moved", []).append(ap)
            box["nonce"] = store.gate_check(tree, msg.mentions)
        except BaseException as exc:                   # noqa: BLE001  (fail open, whatever it was)
            box["error"] = exc

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(budget)
    nonce = None if t.is_alive() or "error" in box else box.get("nonce")
    if t.is_alive():
        print(f"commit-msg gate: more than {budget:g} s, continuing offline (backlog_sync.py catches up).", file=err)
    elif "error" in box:
        print(f"commit-msg gate: offline ({type(box['error']).__name__}); backlog_sync.py catches up.", file=err)
    if box.get("unknown") and nonce:
        print("commit-msg gate: not in the backlog: " + ", ".join(box["unknown"]), file=err)
    try:
        write_trailer(path, g.trailer_for(nonce))
    except Exception as exc:                           # noqa: BLE001  (never block on the trailer itself)
        print(f"commit-msg gate: could not write the trailer ({type(exc).__name__}); the sync will see the commit as unchecked.", file=err)
    return 0


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: commit_msg_gate.py <commit-message-file>", file=sys.stderr)
        return 0                                       # never block on a usage error
    sys.path.insert(0, str(ROOT))
    os.environ["FONDOS_ACTOR"] = ACTOR                 # one process, one identity (owner order 2026-10-28): this process IS the gate
    sys.stdin = None                                   # non-interactive: a live write is refused, never prompted (see the module doc)
    try:
        try:
            import shared.config as cfg
            require = bool(cfg.BACKLOG_REQUIRE_TICKET_ENABLED)
        except Exception:                              # noqa: BLE001  (no config: the strict switch is off)
            require = False
        return run(argv[0], require_ticket=require)
    except Exception as exc:                          # noqa: BLE001
        print(f"commit-msg gate: internal error ({type(exc).__name__}); commit allowed.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
