#!/usr/bin/env python
"""Hermetic Postgres-backed test run: throwaway trust-auth container -> load the
migration DDL -> pytest -> tear down. Needs no credentials and never touches the
shared test server or live data.

    python scripts/ops/run_pg_tests.py                      # P1 + P3 + root + P2 suites
    python scripts/ops/run_pg_tests.py -k statistical_audit # extra args go to one pytest run (repo root)
    python scripts/ops/run_pg_tests.py --port 5440 --image postgres:17

Uses `docker` directly when it works, else `docker` inside WSL (Ubuntu). Under WSL the
distro idle-shuts-down and kills containers unless a wsl process stays attached, so a
`sleep infinity` session is held for the duration (see CLAUDE.md, WSL2-hosted Postgres).
"""
from __future__ import annotations

import argparse
import shutil
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
    ap.add_argument("--port", type=int, default=5437)
    ap.add_argument("--image", default="postgres:15")
    ap.add_argument("--distro", default="Ubuntu")
    ap.add_argument("pytest_args", nargs="*", help="passed to a single pytest run from the repo root")
    args = ap.parse_args()

    docker = _docker_prefix(args.distro)
    name = f"pg-test-{args.port}"
    keepalive = None
    if docker[0] == "wsl":
        keepalive = subprocess.Popen(["wsl", "-d", args.distro, "--", "sleep", "infinity"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

        import os
        env = dict(os.environ, FONDOS_TEST_PG_DSN=f"postgresql://postgres@127.0.0.1:{args.port}/{DB_NAME}")
        if args.pytest_args:
            runs = [(ROOT, args.pytest_args)]
        else:
            runs = [(ROOT, ["proyecto1/tests"]), (ROOT, ["proyecto3/tests"]), (ROOT, ["tests"]),
                    (ROOT / "proyecto2", ["tests", "--ignore=tests/discovery/test_historia.py"])]  # test_historia needs the network
        rc = 0
        for cwd, targets in runs:
            rc |= subprocess.run([sys.executable, "-m", "pytest", "-q", *targets], cwd=cwd, env=env).returncode
        return rc
    finally:
        subprocess.run(docker + ["stop", name], capture_output=True)
        if keepalive:
            keepalive.terminate()


if __name__ == "__main__":
    sys.exit(main())
