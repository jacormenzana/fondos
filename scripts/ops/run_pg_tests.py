#!/usr/bin/env python
"""Hermetic Postgres-backed test run: throwaway trust-auth container -> load the
migration DDL -> pytest -> tear down. Needs no credentials and never touches the
shared test server or live data.

    python scripts/ops/run_pg_tests.py                      # P1 + P3 + root + P2 suites
    python scripts/ops/run_pg_tests.py -k statistical_audit # extra args go to one pytest run (repo root)
    python scripts/ops/run_pg_tests.py --port 5440 --image postgres:17
    python scripts/ops/run_pg_tests.py --suite-files plan.json  # the SAME container, DDL and four runs, each restricted to the files of its suite (FND-0385)
    python scripts/ops/run_pg_tests.py --shards auto            # the SAME container and DDL, the full suite as concurrent pytest shards balanced by the stored per-file times (FND-0386)

`--suite-files` takes a JSON object {"proyecto1"|"proyecto3"|"tests"|"proyecto2": ["repo-relative test path[::test]", ...]}; a suite without entries is skipped, the proyecto2 paths lose their
`proyecto2/` prefix because that run starts in proyecto2/. Without the flag nothing changes.

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
import json
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


def default_runs(root: Path) -> list:
    """The four pytest runs of a full suite: (cwd, targets). test_historia needs the network."""
    return [(root, ["proyecto1/tests"]), (root, ["proyecto3/tests"]), (root, ["tests"]),
            (root / "proyecto2", ["tests", "--ignore=tests/discovery/test_historia.py"])]


SHARD_DURATIONS = Path("out") / "audit" / "test_durations.json"       # {"per_file_seconds": {repo-relative posix path: seconds}} written by the FND-0385 baseline
DEFAULT_FILE_SECONDS = 1.0                                              # a file with no recorded time weighs one second


def collect_test_files(root: Path) -> list:
    """Every test file of the four default runs as (cwd, path relative to that cwd, path relative to the repo root), in a stable order; the same files `default_runs` collects
    (test_historia needs the network and is left out). Pure apart from reading the directory tree."""
    out = []
    for cwd, rel in ((root, "proyecto1/tests"), (root, "proyecto3/tests"), (root, "tests"), (root / "proyecto2", "tests")):
        base = cwd / rel
        files = sorted(set(base.rglob("test_*.py")) | set(base.rglob("*_test.py")))
        for f in files:
            if "__pycache__" in f.parts or f.name == "test_historia.py":
                continue
            out.append((cwd, f.relative_to(cwd).as_posix(), f.relative_to(root).as_posix()))
    return out


def suite_of(cwd: Path, rel: str) -> str:
    """The suite a test file belongs to: proyecto1 / proyecto3 / tests (all from the repo root) or proyecto2 (its own cwd). The suites cannot share a pytest process: proyecto2 and proyecto3 each have their
    own `src` package and the root conftest differs, so a shard runs ONE process per suite it holds (run 1 of 2026-11-10 mixed them: 1,800 collection errors). Pure."""
    return cwd.name if cwd.name == "proyecto2" else rel.split("/", 1)[0]


def shard_plan(weights: dict, n: int) -> list:
    """Longest-processing-time-first balancing: {item: seconds} -> at most `n` lists of items whose planned seconds are as even as a greedy split allows. The heaviest item sets a floor
    (one file cannot be split). Deterministic. Pure."""
    n = max(1, int(n))
    bins = [[0.0, []] for _ in range(n)]
    for item, w in sorted(weights.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        target = min(bins, key=lambda b: b[0])
        target[0] += float(w)
        target[1].append(item)
    return [b[1] for b in bins if b[1]]


def auto_shards(cpu_count: int | None = None) -> int:
    """One shard per CPU but one, at most 5 (the hermetic Postgres is shared by the shards and the launcher end-to-end files are subprocess-heavy). Pure."""
    return max(1, min(5, (cpu_count or 2) - 1))


def build_shards(root: Path, n: int, durations: dict | None = None) -> list:
    """The full suite as `n` shards, each a list of (cwd, relative path) grouped per cwd by the caller. `durations` = {repo-relative path: seconds}."""
    durations = durations or {}
    files = collect_test_files(root)
    weights = {(cwd, rel): float(durations.get(rr, DEFAULT_FILE_SECONDS)) for cwd, rel, rr in files}
    return shard_plan(weights, n)


def build_plan_shards(plan_runs: list, root: Path, n: int, durations: dict | None = None) -> list:
    """An IMPACT run (`--suite-files`) as `n` shards: each entry of the plan (a test file or a `file::test` node) is a unit weighing the recorded seconds of its file (a node weighs one second), balanced
    like the full suite. `plan_runs` = suite_runs(...) = [(cwd, [entry])]. Returns shards of (cwd, entry); every entry lands in exactly one. Pure. FND-0387 follow-up: an impact run was sequential, so a
    selection that is a third of the suite cost more than the sharded full run."""
    durations = durations or {}
    weights = {}
    for cwd, entries in plan_runs:
        for e in entries:
            repo_rel = (cwd.relative_to(root).as_posix() + "/" if cwd != root else "") + e.split("::", 1)[0]
            weights[(cwd, e)] = DEFAULT_FILE_SECONDS if "::" in e else float(durations.get(repo_rel, DEFAULT_FILE_SECONDS))
    return shard_plan(weights, n)


def load_durations(root: Path, path: Path | None = None) -> dict:
    """{repo-relative path: seconds} used only to BALANCE the shards (never to select or skip a test). `path` = the file to read (a clean clone has no out/ directory: the receipt passes the real repository's
    out/audit/test_durations.json); default root/out/audit/test_durations.json. A missing or unreadable file means every file weighs one second: a worse balance, the same tests."""
    try:
        return {str(k).replace("\\", "/"): float(v) for k, v in json.loads((path or root / SHARD_DURATIONS).read_text(encoding="utf-8"))["per_file_seconds"].items()}
    except (OSError, ValueError, KeyError):
        return {}


def _pytest_once(cwd: Path, files: list, env: dict, extra: list | None = None):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files, *(extra or [])], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def _failed_files(text: str) -> list:
    """The test FILES named by the FAILED / ERROR lines of a pytest -q output (first occurrence order). Pure."""
    seen, out = set(), []
    for line in str(text).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("FAILED", "ERROR"):
            f = parts[1].split("::")[0].replace("\\", "/")
            if f not in seen:
                seen.add(f)
                out.append(f)
    return out


def run_sharded(root: Path, n: int, env: dict, durations_path: Path | None = None, plan_runs: list | None = None) -> int:
    """FND-0386: the full suite as `n` concurrent pytest processes against the one hermetic container (each module has its own schema). Output is printed per shard, in order, so the receipt
    parser sums the pytest summaries exactly as for the four sequential runs. A file that FAILS under parallelism is re-run ALONE, sequentially (the owner's one exception to batching: error isolation),
    to CLASSIFY the failure: still failing alone = a real failure; passing alone = a parallel-only failure (a test-isolation defect). Both fail the run (rc 1): a parallel-only failure is never excused,
    it is a defect of the test or of the runner to fix before the sharded run is trusted."""
    from concurrent.futures import ThreadPoolExecutor
    durations = load_durations(root, durations_path)
    shards = build_plan_shards(plan_runs, root, n, durations) if plan_runs is not None else build_shards(root, n, durations)
    print(f"[pg-test] sharded run: {sum(len(s) for s in shards)} test {'entries' if plan_runs is not None else 'files'} in {len(shards)} concurrent shards", flush=True)

    def work(shard):
        groups: dict = {}                                   # (suite, cwd) -> files: one pytest process per suite
        for cwd, rel in shard:
            groups.setdefault((suite_of(cwd, rel), cwd), []).append(rel)
        text, rc = "", 0
        for (_suite, cwd), files in groups.items():
            p = _pytest_once(cwd, files, env)
            text += p.stdout + (p.stderr or "")
            rc |= p.returncode
        return rc, text, {cwd: files for (_s, cwd), files in groups.items()}

    started = time.time()
    with ThreadPoolExecutor(max_workers=len(shards)) as pool:
        results = list(pool.map(work, shards))
    rc = 0
    for i, (src, text, _groups) in enumerate(results):
        print(f"===== shard {i + 1}/{len(results)} (rc {src}) =====", flush=True)
        print(text, flush=True)
        rc |= src
    print(f"[pg-test] shards finished in {time.time() - started:.0f}s", flush=True)
    if rc:
        for i, (src, text, groups) in enumerate(results):
            if not src:
                continue
            for f in _failed_files(text):
                cwd = next((c for c in groups if (c / f).exists() or (c / f.split("/", 1)[-1]).exists()), root)
                rel = f if (cwd / f).exists() else f.split("/", 1)[-1]
                alone = _pytest_once(cwd, [rel], env)
                tail = alone.stdout.strip().splitlines()[-1] if alone.stdout.strip() else ""
                if alone.returncode == 0:
                    print(f"[pg-test] PARALLEL-ONLY failure: {f} fails in a shard but passes alone ({tail}); a test-isolation defect, the run FAILS", flush=True)
                else:
                    print(f"[pg-test] CONFIRMED failure alone: {f} ({tail})\n{alone.stdout}", flush=True)
    return rc


def suite_runs(plan: dict, root: Path) -> list:
    """The same runs as `default_runs`, in the same order, restricted to the entries of `plan` ({suite: [path or path::test]}). A suite with no entries is skipped; proyecto2 entries are made relative to
    proyecto2/ (its run starts there). An unknown suite name is an error: a typo must never silently run less. Pure."""
    known = ("proyecto1", "proyecto3", "tests", "proyecto2")
    unknown = sorted(set(plan) - set(known))
    if unknown:
        raise ValueError(f"unknown suite(s) in the suite file: {unknown}; expected a subset of {list(known)}")
    runs = []
    for suite in known:
        entries = [str(e).replace("\\", "/") for e in plan.get(suite) or []]
        if not entries:
            continue
        if suite == "proyecto2":
            runs.append((root / "proyecto2", [e[len("proyecto2/"):] if e.startswith("proyecto2/") else e for e in entries]))
        else:
            runs.append((root, entries))
    return runs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="default: first free port from 5437")
    ap.add_argument("--image", default="postgres:15")
    ap.add_argument("--distro", default="Ubuntu")
    ap.add_argument("--suite-files", metavar="JSON", help="run only the listed tests, grouped by suite (see above)")
    ap.add_argument("--junit-dir", metavar="DIR", help="also write one JUnit XML per pytest run into DIR (per-test verdicts and durations)")
    ap.add_argument("--durations", metavar="JSON", help="with --shards: the per-file seconds used to balance the shards (default out/audit/test_durations.json of this tree)")
    ap.add_argument("--shards", metavar="N|auto", help="run the full suite (or, with --suite-files, the listed entries) as N concurrent pytest shards (FND-0386); not combined with pytest arguments")
    ap.add_argument("pytest_args", nargs="*", help="passed to a single pytest run from the repo root")
    args = ap.parse_args()
    suite_plan = None
    n_shards = 1
    if args.shards:
        if args.pytest_args or args.junit_dir:
            ap.error("--shards is not combined with --junit-dir or pytest arguments")
        import os as _os
        n_shards = auto_shards(_os.cpu_count()) if args.shards == "auto" else max(1, int(args.shards))
    if args.suite_files:
        if args.pytest_args:
            ap.error("--suite-files and pytest arguments are mutually exclusive")
        try:
            suite_plan = suite_runs(json.loads(Path(args.suite_files).read_text(encoding="utf-8")), ROOT)
        except (OSError, ValueError) as exc:                    # before any docker work: a bad plan must not start a container
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not suite_plan:
            print("error: the suite file selects no test", file=sys.stderr)
            return 2

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
        if n_shards > 1:
            return run_sharded(ROOT, n_shards, env, Path(args.durations) if args.durations else None, plan_runs=suite_plan)
        if args.pytest_args:
            runs = [(ROOT, args.pytest_args)]
        elif suite_plan is not None:
            runs = suite_plan
        else:
            runs = default_runs(ROOT)
        rc = 0
        junit_dir = Path(args.junit_dir).resolve() if args.junit_dir else None
        if junit_dir:
            junit_dir.mkdir(parents=True, exist_ok=True)
        for n, (cwd, targets) in enumerate(runs):
            extra = [f"--junitxml={junit_dir / f'run{n}_{cwd.name}.xml'}"] if junit_dir else []      # per-test verdicts and durations (FND-0385: baseline, F5 data, order-dependence comparison)
            # stdin=DEVNULL: a child that inherits the caller's stdin (a tool/CI pipe) can block forever waiting on it
            # (found 2026-09-26: identical runs hung with an inherited stdin and passed with /dev/null).
            rc |= subprocess.run([sys.executable, "-m", "pytest", "-q", *targets, *extra], cwd=cwd, env=env,
                                 stdin=subprocess.DEVNULL).returncode
        return rc
    finally:
        subprocess.run(docker + ["stop", name], capture_output=True)
        if keepalive:
            keepalive.terminate()


if __name__ == "__main__":
    sys.exit(main())
