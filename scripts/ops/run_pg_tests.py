#!/usr/bin/env python
"""Hermetic Postgres-backed test run: throwaway trust-auth container -> load the
migration DDL -> pytest -> tear down. Needs no credentials and never touches the
shared test server or live data.

    python scripts/ops/run_pg_tests.py                      # P1 + P3 + root + P2 suites
    python scripts/ops/run_pg_tests.py -k statistical_audit # extra args go to one pytest run (repo root)
    python scripts/ops/run_pg_tests.py --port 5440 --image postgres:17

The port defaults to the first free one from 5437 up (a port left half-open by a killed run is
skipped), stale `pg-test-*` containers from earlier killed runs are removed before starting, and the
container is stopped on SIGTERM/Ctrl-C too. A killed run used to leave a stale WSL port-forward and
the NEXT run then hung silently at its first DB test (2026-09-26).

Uses `docker` directly when it works, else `docker` inside WSL (Ubuntu). Under WSL the
distro idle-shuts-down and kills containers unless a wsl process stays attached, so a
`sleep infinity` session is held for the duration (see CLAUDE.md, WSL2-hosted Postgres).
"""
from __future__ import annotations

import argparse
import random
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DDL_FILES = ("00_roles_schemas", "10_bronze", "20_silver", "30_gold", "35_control", "40_matviews")
DB_NAME = "fondos_test"


def _docker_prefix(distro: str) -> list[str]:
    if shutil.which("docker"):
        probe = subprocess.run(["docker", "info"], capture_output=True)
        if probe.returncode == 0:
            return ["docker"]
    if shutil.which("wsl"):
        return ["wsl", "-d", distro, "--", "docker"]
    raise SystemExit("No usable docker (neither native nor via WSL)")


def _psql(docker: list[str], name: str, args: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess:
    cmd = docker + ["exec", "-i", name, "psql", "-U", "postgres", "-d", DB_NAME, "-v", "ON_ERROR_STOP=1", "-q"] + args
    return subprocess.run(cmd, input=stdin, capture_output=True)


def _free_port(start: int = 5437, tries: int = 100) -> int:
    """A port in [start, start+tries) that nothing on this host is listening on. The scan starts at a
    RANDOM offset: WSL2 keeps a just-released forward alive for a while, and reusing the port of the
    previous run made the next one hang silently (2026-09-26)."""
    offset = random.randrange(tries)
    for k in range(tries):
        port = start + (offset + k) % tries
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:          # nothing accepts connections there
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as b:
                        b.bind(("127.0.0.1", port))             # and we can actually claim it
                    return port
                except OSError:
                    continue
    raise SystemExit(f"no free port in {start}..{start + tries - 1}")


def _remove_stale_containers(docker: list[str], keep: str) -> None:
    """Throwaway `pg-test-*` containers older than 30 min are leftovers of a killed run."""
    out = subprocess.run(docker + ["ps", "-a", "--filter", "name=pg-test-", "--format", "{{.Names}}|{{.RunningFor}}"],
                         capture_output=True, text=True).stdout.splitlines()
    for line in out:
        name, _, age = line.partition("|")
        name = name.strip()
        if not name or name == keep:
            continue
        minutes = int(age.split()[0]) if age.split() and age.split()[0].isdigit() and "minute" in age else None
        if minutes is None or minutes >= 30:      # hours/days/"About an hour"/"Exited" -> stale
            print(f"[pg-test] removing stale container {name} ({age.strip()})", flush=True)
            subprocess.run(docker + ["rm", "-f", name], capture_output=True)


def _check_host_reachable(port: int, attempts: int = 3) -> None:
    """Fail fast (with a clear message) if the database is not reachable FROM THE HOST through the
    published port - a dead WSL port-forward otherwise makes pytest stall silently at its first DB test."""
    import psycopg
    last = None
    for _ in range(attempts):
        try:
            with psycopg.connect(f"postgresql://postgres@127.0.0.1:{port}/{DB_NAME}", connect_timeout=8) as c:
                c.execute("select 1")
            return
        except Exception as exc:                                  # noqa: BLE001
            last = exc
            time.sleep(2)
    raise SystemExit(f"[pg-test] 127.0.0.1:{port} is not reachable from the host ({type(last).__name__}: {last}). "
                     "Stale WSL port-forward? Re-run (a new random port is chosen).")


def _wait_ready(docker: list[str], name: str, timeout_s: int = 60) -> None:
    # The image restarts the server once after initdb; require two consecutive successes.
    deadline, ok = time.time() + timeout_s, 0
    while time.time() < deadline:
        ok = ok + 1 if _psql(docker, name, ["-c", "select 1"]).returncode == 0 else 0
        if ok >= 2:
            return
        time.sleep(1.5)
    raise SystemExit(f"Postgres in {name} not ready after {timeout_s}s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="default: first free port from 5437")
    ap.add_argument("--image", default="postgres:15")
    ap.add_argument("--distro", default="Ubuntu")
    ap.add_argument("pytest_args", nargs="*", help="passed to a single pytest run from the repo root")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    from shared.env_guard import require_db_driver
    require_db_driver("run_pg_tests.py")        # RC 106 before any docker work: the suites cannot connect without it

    docker = _docker_prefix(args.distro)
    if not args.port:
        args.port = _free_port()
    name = f"pg-test-{args.port}"
    _remove_stale_containers(docker, keep=name)
    subprocess.run(docker + ["rm", "-f", name], capture_output=True)       # same-name leftover
    # `timeout`/task killers send SIGTERM, which skips `finally` unless converted to an exit.
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, lambda *_: sys.exit(143))
    keepalive = None
    if docker[0] == "wsl":
        keepalive = subprocess.Popen(["wsl", "-d", args.distro, "--", "sleep", "infinity"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rc = 1
    try:
        run = subprocess.run(docker + ["run", "-d", "--rm", "--name", name,
                                       "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", f"POSTGRES_DB={DB_NAME}",
                                       "-p", f"127.0.0.1:{args.port}:5432", args.image], capture_output=True, text=True)
        if run.returncode != 0:
            print(run.stderr.strip(), file=sys.stderr)
            return 1
        _wait_ready(docker, name)
        for stem in DDL_FILES:
            res = _psql(docker, name, [], stdin=(ROOT / "db" / "pg" / f"{stem}.sql").read_bytes())
            if res.returncode != 0:
                print(f"DDL {stem}.sql failed:\n{res.stderr.decode(errors='replace')}", file=sys.stderr)
                return 1
        print(f"[pg-test] {args.image} on 127.0.0.1:{args.port}, DDL loaded", flush=True)
        _check_host_reachable(args.port)

        import os
        env = dict(os.environ, FONDOS_TEST_PG_DSN=f"postgresql://postgres@127.0.0.1:{args.port}/{DB_NAME}",
                   PGCONNECT_TIMEOUT="10")           # a dead forward must fail, not hang
        if args.pytest_args:
            runs = [(ROOT, args.pytest_args)]
        else:
            runs = [(ROOT, ["proyecto1/tests"]), (ROOT, ["proyecto3/tests"]), (ROOT, ["tests"]),
                    (ROOT / "proyecto2", ["tests", "--ignore=tests/discovery/test_historia.py"])]  # test_historia needs the network
        rc = 0
        for cwd, targets in runs:
            # stdin=DEVNULL: a child that inherits the caller's stdin (a tool/CI pipe) can block forever waiting on it
            # (found 2026-09-26: identical runs hung with an inherited stdin and passed with /dev/null).
            rc |= subprocess.run([sys.executable, "-m", "pytest", "-q", *targets], cwd=cwd, env=env,
                                 stdin=subprocess.DEVNULL).returncode
        return rc
    finally:
        subprocess.run(docker + ["stop", name], capture_output=True)
        if keepalive:
            keepalive.terminate()


if __name__ == "__main__":
    sys.exit(main())
