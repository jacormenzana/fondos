# scripts/mig/fondos/p2_rollback.py
# -*- coding: utf-8 -*-
"""
FND-0426 / FND-0427 (plan WP1.6): the rollback of the full P2 recompute that flips ROLLING_WINDOW_COVERAGE_ENABLED and YTD_METRIC_ENABLED.

  export     COPY every row of gold.fund_metrics and gold.fund_metric_timeseries to <out>/*.csv.gz (sorted by isin, ONE repeatable-read snapshot, read-only), with a
             manifest.json: row counts and an order-independent hash per group (fund_metrics by horizon, timeseries by metric / window). A FULL export, not the date subset of
             the plan: a recompute can also prune rows outside it (a continuity gap), and the table is 15.9M rows, about 0.5 GB compressed.
  verify     recompute the group counts and hashes of a database and compare them with the manifest (zero differences = the database equals the export).
  restore    put the export back, fund by fund: one transaction per batch of --batch-funds funds does DELETE of their rows and COPY of the exported ones. Refuses the live port
             without --i-am-restoring-live. --max-funds stops early (rehearsal).
  rehearse   start a throwaway postgres:17 (the harness of run_pg_tests.py), load the export as the "before" state, then for each batch size damage the first K funds and restore
             them, measuring time per batch, WAL written and dead tuples, and verifying the result against the manifest. Prints the largest batch that keeps every transaction
             under --target-batch-seconds. Never touches a live or shared database.

Never prints a DSN. The live database is only ever READ by `export` / `verify`.
"""
from __future__ import annotations

import argparse
import gzip
import itertools
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "gov" / "delivery"))

TABLES = {
    "fund_metrics": {
        "cols": ["isin", "metric", "horizon", "real_flag", "metric_version", "value", "calculation_date", "load_ts", "source_rows", "benchmark_id", "algorithm_version", "batch_id"],
        "order": "isin, metric, horizon, real_flag, metric_version",
        "group": "horizon",
    },
    "fund_metric_timeseries": {
        "cols": ["isin", "metric", "window_label", "date", "value_nominal", "value_real", "source_rows", "has_real", "algorithm_version", "batch_id"],
        "order": "isin, metric, window_label, date",
        "group": "metric || '|' || window_label",
    },
}
LIVE_PORT = 5436


def _connect(dsn: str):
    import psycopg
    conn = psycopg.connect(dsn, autocommit=False)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.commit()                                # leave no transaction open: the caller may set the isolation level
    return conn


def _live_dsn() -> str:
    import shared.config  # noqa: F401  (loads .env)
    dsn = os.environ.get("FONDOS_PG_DSN")
    if not dsn:
        sys.exit("FONDOS_PG_DSN is not set")
    return dsn


def _port_of(dsn: str) -> int:
    import psycopg
    return int(psycopg.conninfo.conninfo_to_dict(dsn).get("port") or 5432)


def fingerprint(conn, table: str) -> dict:
    """{group: [count, hash]} of one table, over the export's column list (so the result does not depend on the physical column order)."""
    spec = TABLES[table]
    row = ", ".join(spec["cols"])
    rows = conn.execute(
        f"SELECT {spec['group']} AS g, COUNT(*), COALESCE(SUM(hashtextextended(ROW({row})::text, 0)::numeric), 0)::text FROM gold.{table} GROUP BY 1 ORDER BY 1").fetchall()
    return {str(g): [int(n), h] for g, n, h in rows}


# ------------------------------------------------------------------ export
def cmd_export(args) -> int:
    import psycopg
    out = Path(args.out) if args.out else ROOT / "out" / "pkg_i" / f"rollback_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    conn = _connect(_live_dsn())
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "tables": {}}
    try:
        manifest["git_sha"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        manifest["git_sha"] = None
    for table, spec in TABLES.items():
        t0 = time.time()
        path = out / f"{table}.csv.gz"
        cols = ", ".join(spec["cols"])
        nbytes = 0
        with conn.cursor() as cur, gzip.open(path, "wb", compresslevel=3) as fh:
            with cur.copy(f"COPY (SELECT {cols} FROM gold.{table} ORDER BY {spec['order']}) TO STDOUT (FORMAT csv)") as copy:
                for chunk in copy:
                    nbytes += len(chunk)
                    fh.write(bytes(chunk))
        groups = fingerprint(conn, table)
        total = sum(v[0] for v in groups.values())
        manifest["tables"][table] = {"file": path.name, "rows": total, "csv_bytes": nbytes, "gz_bytes": path.stat().st_size, "columns": spec["cols"], "groups": groups}
        print(f"[export] {table}: {total:,} rows, {nbytes / 1e6:,.0f} MB csv -> {path.stat().st_size / 1e6:,.0f} MB gz in {time.time() - t0:,.0f}s", flush=True)
    conn.rollback()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"[export] manifest: {out / 'manifest.json'}")
    return 0


# ------------------------------------------------------------------ verify
def _verify(conn, manifest: dict, tables=None) -> list:
    diffs = []
    for table, meta in manifest["tables"].items():
        if tables and table not in tables:
            continue
        now = fingerprint(conn, table)
        for g in sorted(set(now) | set(meta["groups"])):
            if now.get(g) != meta["groups"].get(g):
                diffs.append((table, g, meta["groups"].get(g), now.get(g)))
    conn.rollback()
    return diffs


def cmd_verify(args) -> int:
    manifest = json.loads((Path(args.export) / "manifest.json").read_text(encoding="utf-8"))
    conn = _connect(args.dsn or _live_dsn())
    diffs = _verify(conn, manifest)
    for table, g, want, got in diffs[:40]:
        print(f"DIFF {table} [{g}]: export {want} | database {got}")
    print(f"[verify] {len(diffs)} group(s) differ" if diffs else "[verify] the database equals the export (0 groups differ)")
    return 1 if diffs else 0


# ------------------------------------------------------------------ restore
def _isin_groups(path: Path):
    """Yield (isin, [lines]) from a csv.gz sorted by isin whose FIRST column is the isin (an ISIN never holds a comma or a quote)."""
    cur, lines = None, []
    with gzip.open(path, "rb") as fh:
        for line in fh:
            isin = line.split(b",", 1)[0]
            if isin != cur:
                if cur is not None:
                    yield cur.decode(), lines
                cur, lines = isin, []
            lines.append(line)
    if cur is not None:
        yield cur.decode(), lines


def restore_table(conn, export: Path, table: str, batch: int, max_funds: int = 0) -> dict:
    """Restore `table` fund by fund in batches of `batch` funds; one transaction per batch. Returns timing / WAL stats."""
    spec = TABLES[table]
    cols = ", ".join(spec["cols"])
    stats = {"batches": 0, "funds": 0, "rows": 0, "seconds": [], "wal_bytes": 0}
    pending_isins, pending_lines = [], []

    def flush():
        if not pending_isins:
            return
        t0 = time.time()
        lsn0 = conn.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
        with conn.transaction():
            conn.execute(f"DELETE FROM gold.{table} WHERE isin = ANY(%s)", (list(pending_isins),))
            with conn.cursor() as cur, cur.copy(f"COPY gold.{table} ({cols}) FROM STDIN (FORMAT csv)") as cp:
                cp.write(b"".join(pending_lines))
        lsn1 = conn.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
        conn.commit()
        stats["wal_bytes"] += int(conn.execute("SELECT pg_wal_lsn_diff(%s::pg_lsn, %s::pg_lsn)", (lsn1, lsn0)).fetchone()[0])
        conn.commit()
        stats["seconds"].append(time.time() - t0)
        stats["batches"] += 1
        stats["funds"] += len(pending_isins)
        stats["rows"] += len(pending_lines)
        pending_isins.clear()
        pending_lines.clear()

    for isin, lines in _isin_groups(export / f"{table}.csv.gz"):
        pending_isins.append(isin)
        pending_lines.extend(lines)
        if len(pending_isins) >= batch:
            flush()
        if max_funds and stats["funds"] + len(pending_isins) >= max_funds:
            break
    flush()
    return stats


def cmd_restore(args) -> int:
    dsn = args.dsn or _live_dsn()
    if _port_of(dsn) == LIVE_PORT and not args.i_am_restoring_live:
        sys.exit(f"refused: the target is the live port {LIVE_PORT}; pass --i-am-restoring-live (owner order) to restore the live database")
    conn = _connect(dsn)
    for table in TABLES:
        s = restore_table(conn, Path(args.export), table, args.batch_funds, args.max_funds)
        print(f"[restore] {table}: {s['funds']} funds, {s['rows']:,} rows, {s['batches']} batches, max {max(s['seconds'] or [0]):.1f}s, WAL {s['wal_bytes'] / 1e6:,.0f} MB", flush=True)
    return 0


# ------------------------------------------------------------------ rehearse
def cmd_rehearse(args) -> int:
    import run_pg_tests as H                                          # the harness of the hermetic Postgres tests (scripts/gov/delivery)
    export = Path(args.export)
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    docker = H._docker_prefix("Ubuntu")
    port = H._free_port()
    name = f"pg-rollback-{port}"
    subprocess.run(docker + ["rm", "-f", name], capture_output=True)
    keepalive = None
    if docker[0] == "wsl":
        keepalive = subprocess.Popen(["wsl", "-d", "Ubuntu", "--", "sleep", "infinity"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = []
    try:
        run = subprocess.run(docker + ["run", "-d", "--rm", "--name", name, "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", f"POSTGRES_DB={H.DB_NAME}",
                                       "-p", f"127.0.0.1:{port}:5432", args.image], capture_output=True, text=True)
        if run.returncode != 0:
            print(run.stderr.strip(), file=sys.stderr)
            return 1
        H._wait_ready(docker, name)
        for stem in H.DDL_FILES:
            res = H._psql(docker, name, [], stdin=(ROOT / "db" / "pg" / f"{stem}.sql").read_bytes())
            if res.returncode != 0:
                print(f"DDL {stem}.sql failed:\n{res.stderr.decode(errors='replace')}", file=sys.stderr)
                return 1
        H._check_host_reachable(port)
        ver = subprocess.run(docker + ["exec", name, "postgres", "--version"], capture_output=True, text=True).stdout.strip()
        print(f"[rehearse] {ver} on 127.0.0.1:{port}, DDL loaded", flush=True)
        dsn = f"postgresql://postgres@127.0.0.1:{port}/{H.DB_NAME}"
        conn = _connect(dsn)
        # 0. fund_metrics has a foreign key to silver.fund_master (ON DELETE CASCADE): seed one minimal row per exported fund so the restore pays the same FK checks as live
        seed = sorted({line.split(b",", 1)[0].decode() for line in gzip.open(export / "fund_metrics.csv.gz", "rb")})
        with conn.cursor() as cur, cur.copy("COPY silver.fund_master (isin, fund_name, fund_nature, heuristic_block, heuristic_core) FROM STDIN") as cp:
            for isin in seed:
                cp.write_row((isin, "rehearsal", "Mixtos", "rehearsal", 0))
        conn.commit()
        print(f"[rehearse] seeded silver.fund_master with {len(seed)} funds", flush=True)
        # 1. the "before" state = the export, loaded in big batches
        t0 = time.time()
        for table in TABLES:
            s = restore_table(conn, export, table, 400)
            print(f"[rehearse] loaded {table}: {s['rows']:,} rows in {time.time() - t0:,.0f}s", flush=True)
        diffs = _verify(conn, manifest)
        print(f"[rehearse] initial load equals the export: {'YES' if not diffs else f'NO ({len(diffs)} groups)'}", flush=True)
        if diffs:
            return 1
        # 2. damage the first K funds, restore them with each batch size
        for batch in args.batch_sizes:
            # the funds damaged are exactly the first K of each export file (the ones restore_table(max_funds=K) puts back), whatever the collation of this database
            firsts = {tb: [i for i, _ in itertools.islice(_isin_groups(export / f"{tb}.csv.gz"), args.funds)] for tb in TABLES}
            conn.execute("UPDATE gold.fund_metrics SET value = value + 1 WHERE isin = ANY(%s)", (firsts["fund_metrics"],))
            conn.execute("DELETE FROM gold.fund_metric_timeseries WHERE isin = ANY(%s)", (firsts["fund_metric_timeseries"],))
            conn.commit()
            dead0 = conn.execute("SELECT COALESCE(SUM(n_dead_tup), 0) FROM pg_stat_user_tables WHERE relname LIKE 'fund_metric%' OR relname LIKE 'fmts_p_%'").fetchone()[0]
            size0 = conn.execute("SELECT SUM(pg_total_relation_size(c.oid)) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='gold' AND (c.relname LIKE 'fund_metric%' OR c.relname LIKE 'fmts_p_%') AND c.relkind IN ('r','p')").fetchone()[0]
            conn.commit()
            row = {"batch": batch}
            t0 = time.time()
            secs, wal, rows = [], 0, 0
            for table in TABLES:
                s = restore_table(conn, export, table, batch, args.funds)
                secs += s["seconds"]
                wal += s["wal_bytes"]
                rows += s["rows"]
            dead1 = conn.execute("SELECT COALESCE(SUM(n_dead_tup), 0) FROM pg_stat_user_tables WHERE relname LIKE 'fund_metric%' OR relname LIKE 'fmts_p_%'").fetchone()[0]
            size1 = conn.execute("SELECT SUM(pg_total_relation_size(c.oid)) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='gold' AND (c.relname LIKE 'fund_metric%' OR c.relname LIKE 'fmts_p_%') AND c.relkind IN ('r','p')").fetchone()[0]
            conn.commit()
            diffs = _verify(conn, manifest)
            row.update(total_s=round(time.time() - t0, 1), batches=len(secs), max_batch_s=round(max(secs), 1), rows=rows, wal_mb=round(wal / 1e6), dead_tuples_added=int(dead1 - dead0),
                       size_growth_mb=round((float(size1) - float(size0)) / 1e6), equals_export=not diffs)
            results.append(row)
            print(f"[rehearse] batch {batch}: {row}", flush=True)
            conn.autocommit = True                                      # VACUUM cannot run inside a transaction block
            for tb in TABLES:
                conn.execute(f"VACUUM gold.{tb}")                       # between trials, as autovacuum would
            conn.autocommit = False
    finally:
        subprocess.run(docker + ["stop", name], capture_output=True)
        if keepalive:
            keepalive.terminate()
    ok = [r for r in results if r["equals_export"] and r["max_batch_s"] <= args.target_batch_seconds]
    best = max(ok, key=lambda r: r["batch"]) if ok else None
    per_fund_wal = (results[-1]["wal_mb"] / args.funds) if results else 0
    print(json.dumps({"image": args.image, "funds_per_trial": args.funds, "trials": results,
                      "recommended_batch_funds": best["batch"] if best else None, "target_batch_seconds": args.target_batch_seconds,
                      "wal_mb_per_fund": round(per_fund_wal, 2)}, indent=1))
    return 0 if best else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="export the two tables + manifest (read-only on the live database)")
    e.add_argument("--out", help="default: out/pkg_i/rollback_<stamp>")
    e.set_defaults(fn=cmd_export)
    v = sub.add_parser("verify", help="compare a database with the manifest")
    v.add_argument("--export", required=True)
    v.add_argument("--dsn", help="default: the live FONDOS_PG_DSN (read-only)")
    v.set_defaults(fn=cmd_verify)
    r = sub.add_parser("restore", help="restore the export, fund by fund, in batches")
    r.add_argument("--export", required=True)
    r.add_argument("--dsn", help="default: the live FONDOS_PG_DSN (refused on the live port without --i-am-restoring-live)")
    r.add_argument("--batch-funds", type=int, default=200)
    r.add_argument("--max-funds", type=int, default=0)
    r.add_argument("--i-am-restoring-live", action="store_true")
    r.set_defaults(fn=cmd_restore)
    h = sub.add_parser("rehearse", help="restore rehearsal on a throwaway postgres:17")
    h.add_argument("--export", required=True)
    h.add_argument("--image", default="postgres:17")
    h.add_argument("--funds", type=int, default=600, help="funds damaged and restored per trial")
    h.add_argument("--batch-sizes", type=int, nargs="+", default=[50, 150, 300, 600])
    h.add_argument("--target-batch-seconds", type=float, default=15.0)
    h.set_defaults(fn=cmd_rehearse)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
