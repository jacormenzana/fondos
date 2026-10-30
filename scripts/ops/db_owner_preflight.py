# scripts/ops/db_owner_preflight.py
# -*- coding: utf-8 -*-
"""Hard-stop pre-flight and launcher of the Backlog v2 DDL (FND-0354, Step 3). Runs ONLY as the local account `fondos_db_owner`,
started by the owner through run_db_owner.cmd (`runas /user:%COMPUTERNAME%\\fondos_db_owner`), from the read-only copy under
C:\\ProgramData\\fondos\\db_owner\\app with the interpreter of ...\\runtime. Nothing here runs from the repository checkout.

Order, every step a hard stop (exit 1, the DSN is never printed):
  1. the process account is `fondos_db_owner`;
  2. MANIFEST.sha256 of the whole tree (code AND interpreter) matches byte for byte;
  3. the vault holds exactly one line FONDOS_PG_DSN_OWNER=postgresql://...@127.0.0.1:5436/gestion;
  4. the first output line is `target: host=127.0.0.1 port=5436 dbname=gestion`;
  5. the SERVER is the real one, not a stand-in answering on the same port: system_identifier 7688429679929688102, database `gestion` with oid 34094, PostgreSQL 17
     (read through a read-only session; the host and port are fixed by the DSN pattern of step 3);
  6. only for --apply: the owner types the confirmation sentence (a non-interactive process cannot);
  7. migrate_backlog_v2.py runs in a child process that receives the DSN through its environment and nothing else of ours.
The whole output is also written to <root>\\receipts\\<utc>_<phase>_<mode>.txt (readable by the owner's daily user, no secret in it).

    run_db_owner.cmd --check                       steps 1-5 only
    run_db_owner.cmd --phase relations             dry run of a phase
    run_db_owner.cmd --phase relations --apply     DDL (typed confirmation)
    run_db_owner.cmd --phase iam --down --apply    staged rollback
Exit codes: 0 ok, 1 hard stop or failed DDL, 2 usage.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db_owner_manifest as manifest  # noqa: E402

ACCOUNT = "fondos_db_owner"
PHASES = ("relations", "iam")                       # the two steps of the Backlog v2 deployment, in this order
EXPECTED = {"host": "127.0.0.1", "port": "5436", "dbname": "gestion",
            "system_identifier": 7688429679929688102, "database_oid": 34094, "major": 17}
VAULT_KEY = "FONDOS_PG_DSN_OWNER"
_DSN = re.compile(r"^postgresql://[^:@/\s]+:[^@\s]+@127\.0\.0\.1:5436/gestion$")
TARGET_LINE = "target: host={host} port={port} dbname={dbname}".format(**EXPECTED)


class HardStop(Exception):
    pass


def current_account() -> str:
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(256)
        size = ctypes.c_uint32(256)
        if ctypes.windll.advapi32.GetUserNameW(buf, ctypes.byref(size)):
            return buf.value
    except Exception:       # not Windows
        pass
    import getpass
    return getpass.getuser()


def parse_vault(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) != 1 or not lines[0].startswith(VAULT_KEY + "="):
        raise HardStop(f"the vault must hold exactly one line {VAULT_KEY}=...")
    dsn = lines[0][len(VAULT_KEY) + 1:]
    if not _DSN.match(dsn):
        raise HardStop("the vault DSN does not target exactly 127.0.0.1:5436/gestion")
    return dsn


def read_fingerprint(conn) -> dict:
    row = conn.execute("SELECT (SELECT system_identifier FROM pg_control_system()), "
                       "(SELECT oid::bigint FROM pg_database WHERE datname = current_database()), "
                       "current_database(), current_setting('server_version_num')::int / 10000").fetchone()
    return {"system_identifier": int(row[0]), "database_oid": int(row[1]), "dbname": row[2], "major": int(row[3])}


def fingerprint_problems(read: dict) -> list:
    """One line per fact that differs from EXPECTED (pure)."""
    return [f"{k}: read {read.get(k)!r}, expected {EXPECTED[k]!r}" for k in ("system_identifier", "database_oid", "dbname", "major") if read.get(k) != EXPECTED[k]]


def confirmation_sentence(phase: str, down: bool) -> str:
    return f"APPLY {'ROLLBACK ' if down else ''}{phase} ON {EXPECTED['dbname']} {EXPECTED['host']}:{EXPECTED['port']}"


def run(argv, *, root: Path, account=current_account, connect=None, ask=input, child=None, log=print, now=lambda: datetime.now(timezone.utc)) -> int:
    ap = argparse.ArgumentParser(prog="run_db_owner.cmd", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="steps 1-5 only")
    ap.add_argument("--phase", choices=PHASES)
    ap.add_argument("--down", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if args.check == bool(args.phase) or (args.check and (args.down or args.apply)) or (args.down and not args.phase):
        ap.error("use --check, or --phase <relations|iam> [--down] [--apply]")

    lines = []

    def out(msg: str) -> None:
        lines.append(msg)
        log(msg)

    mode = "check" if args.check else ("apply" if args.apply else "dry")
    receipt = root / "receipts" / f"{now():%Y%m%dT%H%M%SZ}_{args.phase or 'check'}{'_down' if args.down else ''}_{mode}.txt"
    secret = None
    rc = 1
    try:
        who = account()
        if who.lower() != ACCOUNT:
            raise HardStop(f"runs only as {ACCOUNT}, not as {who}")
        commit, problems = manifest.verify(root)
        if problems:
            raise HardStop("MANIFEST.sha256 does not match the tree: " + "; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""))
        secret = parse_vault((root / "vault" / "owner_dsn.conf").read_text(encoding="utf-8"))
        out(TARGET_LINE)
        out(f"deployed commit {commit}; manifest verified")
        if connect is None:
            import psycopg
            connect = lambda dsn: psycopg.connect(dsn, connect_timeout=10, options="-c default_transaction_read_only=on")   # noqa: E731
        try:
            conn = connect(secret)
        except Exception as exc:            # never echo the DSN
            raise HardStop(f"connection failed: {type(exc).__name__}") from None
        try:
            read = read_fingerprint(conn)
            conn.rollback()
        finally:
            conn.close()
        bad = fingerprint_problems(read)
        if bad:
            raise HardStop("WRONG SERVER. " + "; ".join(bad))
        out(f"server verified: system_identifier {read['system_identifier']}, database {read['dbname']} oid {read['database_oid']}, PostgreSQL {read['major']}")
        if args.check:
            rc = 0
        else:
            if args.apply:
                sentence = confirmation_sentence(args.phase, args.down)
                out(f"type exactly: {sentence}")
                if ask("> ").strip() != sentence:
                    raise HardStop("confirmation not typed: nothing was changed")
            cmd = [sys.executable, "-I", "-B", str(root / "app" / "scripts" / "ops" / "migrate_backlog_v2.py"), "--phase", args.phase]
            cmd += (["--down"] if args.down else []) + (["--apply"] if args.apply else [])
            env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"), "FONDOS_PG_DSN_OWNER": secret,
                   "FONDOS_ENV_FILE": str(root / "no.env"), "PYTHONDONTWRITEBYTECODE": "1"}
            rc = (child or _run_child)(cmd, env, out)
    except HardStop as exc:
        out(f"HARD STOP: {exc}")
        rc = 1
    finally:
        text = "\n".join(lines) + "\n"
        if secret:
            text = text.replace(secret, "***")
        try:
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(text, encoding="utf-8", newline="\n")
            log(f"receipt: {receipt}")
        except OSError as exc:
            log(f"receipt NOT written: {exc}")
    return rc


def _run_child(cmd: list, env: dict, out) -> int:
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    for line in p.stdout:
        out(line.rstrip("\n"))
    return p.wait()


def main() -> int:
    root = Path(__file__).resolve().parents[3]          # <root>\app\scripts\ops\this file
    rc = run(sys.argv[1:], root=root)
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            input("Press Enter to close this window...")
        except EOFError:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
