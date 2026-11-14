#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
apply_fmts_pivot.py — pivot real_flag out of gold.fund_metric_timeseries (v27, 2026-09-27).

    python scripts/ops/apply_fmts_pivot.py                  # PREVIEW: live stats + Superset pre-check, no writes
    python scripts/ops/apply_fmts_pivot.py --apply           # build + verify + swap + COMMIT
    python scripts/ops/apply_fmts_pivot.py --drop-old        # drop *_long_old, only after a clean post-swap P2 run

See the pivot plan (harmonic-marinating-balloon.md, 3 review rounds) for the full design. Summary:
`real_flag` (smallint, part of the key) is dropped from gold.fund_metric_timeseries; `value`/
`ref_value` pivot into `value_nominal`/`value_real`, with `has_real` distinguishing "no real row"
from "real row, NULL value" (lossless). `gold.v_fund_metric_timeseries_long` (db/pg/40_matviews.sql)
reconstructs the pre-pivot long shape for every reader that still needs it (Superset, the
statistical audit). `gold.fund_metrics` is NOT touched (sparse 59/41 split, ~24 readers in P3 —
out of scope, see the plan).

Build-then-swap: --apply builds a full parallel copy of the table (`_new` suffix), verifies it
EXACTLY reproduces the live data (row counts, a full anti-join via EXCEPT, matview parity), and only
then swaps names — all inside ONE transaction. The build holds only a SHARE lock (blocks P2 writers,
not BI readers); the swap itself needs ACCESS EXCLUSIVE, held only for the renames. If that lock
can't be acquired quickly (a live analytical query holding a read lock), the swap retries for ~3
minutes (12 x 5s lock_timeout, via a SAVEPOINT so the build is never lost) rather than giving up.
A verification failure or an exhausted retry ROLLS BACK the entire transaction — the live objects
are untouched either way.

DDL is read from db/pg/30_gold.sql and db/pg/40_matviews.sql between their `APPLY_FMTS_PIVOT:
BEGIN/END` sentinel comments (never duplicated here) and substituted to `_new` staging names.

Runs with FONDOS_PG_DSN_OWNER (DDL needs ownership; fondos_app has none, by design). Grants to
fondos_app/fondos_ro/superset_ro are automatic (ALTER DEFAULT PRIVILEGES FOR ROLE fondos_owner,
db/pg/00_roles_schemas.sql + db/pg_ops/readonly_roles.sql) as long as this script runs as the owner
— verified, not (re-)granted, after the swap.

Exit codes: 0 ok (preview, or a completed --apply/--drop-old), 1 verification/guard failed,
2 usage/connection/lock error.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLD_DDL_FILE = ROOT / "db" / "pg" / "30_gold.sql"
MATVIEWS_DDL_FILE = ROOT / "db" / "pg" / "40_matviews.sql"

TABLE = "gold.fund_metric_timeseries"
TABLE_NEW = "gold.fund_metric_timeseries_new"
TABLE_OLD = "gold.fund_metric_timeseries_long_old"
APP_ROLE = "fondos_app"
RO_ROLES = ("fondos_ro", "superset_ro")

# Partitions + PK/FK constraint + indexes + the extended-statistics object that the sentinel-marked
# 30_gold.sql block creates for gold.fund_metric_timeseries. Renamed in both directions during the
# swap (live -> *_long_old, then *_new -> live).
_PARTITIONS = ("fmts_p_vol_ann", "fmts_p_max_dd", "fmts_p_return_ann", "fmts_p_sharpe",
               "fmts_p_sortino", "fmts_p_default")
# The 5 named metric values from 30_gold.sql's FOR VALUES IN (...); fmts_p_default catches anything
# else. Used to scope the anti-join verification per partition (see _verify) instead of one
# all-at-once UNION ALL -- rehearsed against a live-scale (32M row) restored copy on 2026-09-27 and
# OOM-killed the whole Postgres instance (shared_buffers=4GB on a 7.7GB WSL2 VM, plus parallel-worker
# hash memory on top, for one unbounded bidirectional EXCEPT over the full table). Never widen this
# back to a single unscoped query without re-rehearsing at live scale first.
_NAMED_METRICS = ("vol_ann", "max_dd", "return_ann", "sharpe", "sortino")
_RELATION_RENAMES = {  # canonical name -> _new / _long_old suffix pieces, longest-first for safety
    "gold.fund_metric_timeseries": ("_new", "_long_old"),
}
# idx_fmts_batch dropped from the live table (FND-0115, 2026-09-27, before this pivot's first live
# --apply attempt) but this script's DDL substitution / rename lists still named it, so
# _rename_old_to_long_old's ALTER INDEX failed with UndefinedTable against live (rehearsal never
# caught this: the rehearsal was restored from a dump taken before the FND-0115 drop). Fixed in step
# with removing idx_fmts_batch's CREATE INDEX from 30_gold.sql -- there is now only one index here.
_INDEX_RENAMES = ("idx_fmts_bi",)
_STATS_RENAME = "stx_fmts"
_PKEY_NAME = "fmts_pkey"
_FK_NAME = "fmts_isin_fk"

# Views/matviews rebuilt in the second sentinel block, oldest-dependency-first for the DROP order
# (matviews depend on the view) and reverse for CREATE — the DDL file already orders them correctly.
_VIEW = "gold.v_fund_metric_timeseries_long"
_MATVIEWS = ("gold.mv_fmts_peer_stats", "gold.mv_fmts_latest", "gold.mv_fund_coverage")
_MATVIEW_INDEXES = ("idx_mv_fmts_peer_stats", "idx_mv_fmts_latest", "idx_mv_fund_coverage")

_SWAP_STEP = "FMTS_PIVOT_SWAP"
_MAX_LOCK_ATTEMPTS = 12
_LOCK_RETRY_DELAY_S = 10


def _extract_sentinel_block(sql_text: str, label: str) -> str:
    pattern = re.compile(
        rf"-- APPLY_FMTS_PIVOT: BEGIN {re.escape(label)}.*?\n(.*?)-- APPLY_FMTS_PIVOT: END {re.escape(label)}",
        re.S,
    )
    m = pattern.search(sql_text)
    if not m:
        raise ValueError(f"APPLY_FMTS_PIVOT sentinel block {label!r} not found — DDL file drifted")
    return m.group(1)


def _to_staging(sql: str) -> str:
    """canonical names -> `_new` staging names. Longer/more-specific identifiers first so a
    single-pass str.replace() never corrupts a name that is a substring of another (verified: none
    of the identifiers below is a substring of another once `gold.` prefixes are accounted for)."""
    out = sql
    out = out.replace("gold.v_fund_metric_timeseries_long", "gold.v_fund_metric_timeseries_long_new")
    out = out.replace("gold.fund_metric_timeseries", TABLE_NEW)
    out = out.replace("gold.mv_fmts_peer_stats", "gold.mv_fmts_peer_stats_new")
    out = out.replace("gold.mv_fmts_latest", "gold.mv_fmts_latest_new")
    out = out.replace("gold.mv_fund_coverage", "gold.mv_fund_coverage_new")
    for idx in _MATVIEW_INDEXES:
        out = out.replace(idx, f"{idx}_new")
    for idx in _INDEX_RENAMES:
        out = out.replace(idx, f"{idx}_new")
    out = out.replace(_STATS_RENAME, f"{_STATS_RENAME}_new")
    out = out.replace(_PKEY_NAME, f"{_PKEY_NAME}_new")
    for p in _PARTITIONS:
        out = out.replace(p, p.replace("fmts_p_", "fmts_new_p_"))
    return out


def build_staging_ddl() -> tuple[str, str]:
    """(table_ddl, views_ddl), both with canonical names substituted to their `_new` staging
    counterparts. Raises if either sentinel block is missing (DDL file drifted since this script
    was written) rather than silently building against a stale extraction."""
    table_block = _extract_sentinel_block(
        GOLD_DDL_FILE.read_text(encoding="utf-8"),
        "base table DDL",
    )
    views_block = _extract_sentinel_block(
        MATVIEWS_DDL_FILE.read_text(encoding="utf-8"),
        "long view + matviews DDL",
    )
    return _to_staging(table_block), _to_staging(views_block)


_POPULATE_SQL = f"""
    INSERT INTO {TABLE_NEW}
        (isin, metric, window_label, date, value_nominal, value_real, has_real, source_rows,
         algorithm_version, batch_id)
    SELECT isin, metric, window_label, date,
           MAX(value) FILTER (WHERE real_flag = 0)            AS value_nominal,
           MAX(value) FILTER (WHERE real_flag = 1)            AS value_real,
           bool_or(real_flag = 1)                              AS has_real,
           MAX(source_rows)                                    AS source_rows,
           MAX(algorithm_version) FILTER (WHERE real_flag = 0) AS algorithm_version,
           MAX(batch_id) FILTER (WHERE real_flag = 0)          AS batch_id
    FROM {TABLE}
    GROUP BY isin, metric, window_label, date
    ORDER BY isin, window_label, date
"""

_FK_SQL = f"""
    ALTER TABLE {TABLE_NEW}
      ADD CONSTRAINT {_FK_NAME}_new FOREIGN KEY (isin) REFERENCES silver.fund_master (isin) ON DELETE CASCADE
"""

_LOCK_BLOCKERS_SQL = f"""
    SELECT l.pid, a.usename, a.state, a.query, a.query_start
    FROM pg_locks l
    JOIN pg_stat_activity a ON a.pid = l.pid
    WHERE l.relation = '{TABLE}'::regclass
      AND l.granted
      AND l.pid <> pg_backend_pid()
"""


def _partitioned_size_pretty(conn, qualified_table: str) -> str:
    """pg_total_relation_size() on a declaratively-partitioned PARENT returns ~0 (the parent has no
    physical storage of its own, per 30_gold.sql's comment) -- sum over its partitions instead."""
    (size,) = conn.execute(
        "SELECT pg_size_pretty(sum(pg_total_relation_size(c.oid))) "
        "FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = %s::regclass",
        (qualified_table,),
    ).fetchone()
    return size or "0 bytes"


def _verify(conn) -> list[str]:
    """Runs every check from the pivot plan's "Verify" step. Returns a list of failure messages
    (empty = all passed). Never raises on a data mismatch -- that's the caller's job, so every
    check runs and reports, instead of stopping at the first failure."""
    failures: list[str] = []

    (old_distinct_keys,) = conn.execute(
        f"SELECT count(*) FROM (SELECT DISTINCT isin, metric, window_label, date FROM {TABLE}) s"
    ).fetchone()
    (new_rows,) = conn.execute(f"SELECT count(*) FROM {TABLE_NEW}").fetchone()
    if new_rows != old_distinct_keys:
        failures.append(f"row count: new={new_rows} != old_distinct_keys={old_distinct_keys}")

    # EXCEPT treats two NULLs as equal for row comparison (same semantics the plan calls for via
    # "IS NOT DISTINCT FROM"), so this is a like-for-like anti-join in both directions. Scoped one
    # metric partition at a time (not one UNION ALL over the whole table) and run with parallelism
    # off and work_mem capped -- see _NAMED_METRICS' comment for why: an unscoped version of this
    # query OOM-killed the whole Postgres instance during the 2026-09-27 rehearsal at live scale.
    conn.execute("SET LOCAL max_parallel_workers_per_gather = 0")
    conn.execute("SET LOCAL work_mem = '128MB'")
    metric_filters = [("= %s", (m,)) for m in _NAMED_METRICS]
    metric_filters.append(("!= ALL (%s)", (list(_NAMED_METRICS),)))
    for op, params in metric_filters:
        (diff_count,) = conn.execute(f"""
            SELECT count(*) FROM (
                (SELECT isin, metric, window_label, real_flag, date, value
                 FROM gold.v_fund_metric_timeseries_long_new WHERE metric {op}
                 EXCEPT
                 SELECT isin, metric, window_label, real_flag, date, value FROM {TABLE} WHERE metric {op})
                UNION ALL
                (SELECT isin, metric, window_label, real_flag, date, value FROM {TABLE} WHERE metric {op}
                 EXCEPT
                 SELECT isin, metric, window_label, real_flag, date, value
                 FROM gold.v_fund_metric_timeseries_long_new WHERE metric {op})
            ) diff
        """, params * 4).fetchone()
        if diff_count != 0:
            failures.append(f"anti-join (metric {op} {params[0]!r}): {diff_count} differing rows between old and new")

    (bad_ref,) = conn.execute(
        f"SELECT count(*) FROM {TABLE} WHERE ref_type IS NOT NULL OR ref_value IS NOT NULL"
    ).fetchone()
    if bad_ref != 0:
        failures.append(f"ref_type/ref_value: {bad_ref} non-NULL rows on the live table (pivot would lose data)")

    for mv in ("mv_fmts_peer_stats", "mv_fmts_latest"):
        (old_n,) = conn.execute(f"SELECT count(*) FROM gold.{mv}").fetchone()
        (new_n,) = conn.execute(f"SELECT count(*) FROM gold.{mv}_new").fetchone()
        if old_n != new_n:
            failures.append(f"{mv}: old={old_n} != new={new_n} (should be unchanged by the pivot)")

    # mv_fund_coverage is DELIBERATELY not compared for equality: it drops the real_flag dimension
    # by design (see 40_matviews.sql's comment on it), so its count roughly halves. Just confirm it
    # built with data at all.
    (cov_n,) = conn.execute("SELECT count(*) FROM gold.mv_fund_coverage_new").fetchone()
    if cov_n == 0:
        failures.append("mv_fund_coverage_new: 0 rows (expected roughly half of the live mv_fund_coverage)")

    return failures


def _rename_old_to_long_old(conn) -> None:
    conn.execute(f"ALTER TABLE {TABLE} RENAME TO fund_metric_timeseries_long_old")
    for p in _PARTITIONS:
        conn.execute(f"ALTER TABLE gold.{p} RENAME TO {p.replace('fmts_p_', 'fmts_long_old_p_')}")
    for idx in _INDEX_RENAMES:
        conn.execute(f"ALTER INDEX gold.{idx} RENAME TO {idx}_long_old")
    conn.execute(f"ALTER STATISTICS gold.{_STATS_RENAME} RENAME TO {_STATS_RENAME}_long_old")
    # Constraint names are scoped per-table in Postgres, so this rename is cosmetic (avoids a name
    # that reads as "the pkey" on what is now the retired copy) rather than required for correctness.
    conn.execute(f"ALTER TABLE {TABLE_OLD} RENAME CONSTRAINT {_PKEY_NAME} TO {_PKEY_NAME}_long_old")
    conn.execute(f"ALTER TABLE {TABLE_OLD} RENAME CONSTRAINT {_FK_NAME} TO {_FK_NAME}_long_old")


def _rename_new_to_live(conn) -> None:
    conn.execute(f"ALTER TABLE {TABLE_NEW} RENAME TO fund_metric_timeseries")
    for p in _PARTITIONS:
        new_p = p.replace("fmts_p_", "fmts_new_p_")
        conn.execute(f"ALTER TABLE gold.{new_p} RENAME TO {p}")
    for idx in _INDEX_RENAMES:
        conn.execute(f"ALTER INDEX gold.{idx}_new RENAME TO {idx}")
    conn.execute(f"ALTER STATISTICS gold.{_STATS_RENAME}_new RENAME TO {_STATS_RENAME}")
    conn.execute(f"ALTER TABLE {TABLE} RENAME CONSTRAINT {_PKEY_NAME}_new TO {_PKEY_NAME}")
    conn.execute(f"ALTER TABLE {TABLE} RENAME CONSTRAINT {_FK_NAME}_new TO {_FK_NAME}")


def _swap_views(conn) -> None:
    # Views/matviews are DROPPED (not renamed) on the old side: unlike the base table, there is no
    # value in keeping a "_long_old" copy of a view — it's pure SQL, recoverable from git if ever
    # needed, and dropping it (rather than juggling a third name) is what frees the canonical name
    # for the already-built, already-verified staging one.
    for mv in _MATVIEWS:
        conn.execute(f"DROP MATERIALIZED VIEW {mv}")
    # IF EXISTS: unlike the 3 matviews (which already exist pre-pivot, built directly on the base
    # table), this view is NET NEW -- introduced by the pivot itself to give the wide table a long
    # shape. On a first-ever --apply (rehearsed 2026-09-27) it never existed yet; an unconditional
    # DROP VIEW failed with UndefinedTable there. Idempotent for a second run too.
    conn.execute(f"DROP VIEW IF EXISTS {_VIEW}")

    conn.execute(f"ALTER VIEW {_VIEW}_new RENAME TO v_fund_metric_timeseries_long")
    for mv in _MATVIEWS:
        conn.execute(f"ALTER MATERIALIZED VIEW {mv}_new RENAME TO {mv.split('.', 1)[1]}")
    for idx in _MATVIEW_INDEXES:
        conn.execute(f"ALTER INDEX gold.{idx}_new RENAME TO {idx}")


def _check_privileges(conn) -> dict[str, dict[str, bool | None]]:
    """None (not False) means the role itself doesn't exist on this server -- reported, not fatal:
    a role is provisioned by a separate ops script (db/pg_ops/readonly_roles.sql), not the DDL this
    migration depends on, so its absence here is an environment gap, not proof the swap misbehaved."""
    import psycopg

    result: dict[str, dict[str, bool | None]] = {}
    result[APP_ROLE] = {
        p: bool(conn.execute(
            "SELECT has_table_privilege(%s, %s, %s)", (APP_ROLE, TABLE, p)
        ).fetchone()[0])
        for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }
    for role in RO_ROLES:
        try:
            with conn.transaction():  # SAVEPOINT: an UndefinedObject must not abort the outer txn
                ok = bool(conn.execute(
                    "SELECT has_table_privilege(%s, %s, 'SELECT')", (role, TABLE)
                ).fetchone()[0])
            result[role] = {"SELECT": ok}
        except psycopg.errors.UndefinedObject:
            print(f"[PRIVS] role {role!r} does not exist on this server — skipping (see db/pg_ops/readonly_roles.sql)",
                  file=sys.stderr)
            result[role] = {"SELECT": None}
    return result


class _DryRunComplete(Exception):
    pass


def do_apply(conn) -> int:
    import psycopg

    table_ddl, views_ddl = build_staging_ddl()

    with conn.transaction():
        conn.execute(f"LOCK TABLE {TABLE} IN SHARE MODE")
        print("[BUILD] SHARE lock held (blocks P2 writers, not BI readers) — creating staging table")
        conn.execute(table_ddl)
        print("[BUILD] populating staging table from the live one (one pass, partition-routed)")
        conn.execute(_POPULATE_SQL)
        conn.execute(_FK_SQL)
        print("[BUILD] creating staging view + matviews")
        conn.execute(views_ddl)

        print("[VERIFY] running the pivot plan's verification checks")
        failures = _verify(conn)
        if failures:
            for f in failures:
                print(f"  FAILED: {f}", file=sys.stderr)
            raise RuntimeError(f"{len(failures)} verification check(s) failed — rolling back, nothing changed")
        print("[VERIFY] all checks passed")

        before = _partitioned_size_pretty(conn, TABLE)
        after = _partitioned_size_pretty(conn, TABLE_NEW)
        print(f"[SIZE] live={before} staging={after}")

        swapped = False
        for attempt in range(1, _MAX_LOCK_ATTEMPTS + 1):
            try:
                with conn.transaction():  # nested -> SAVEPOINT; a failure here never loses the build
                    conn.execute("SET LOCAL lock_timeout = '5s'")
                    conn.execute(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE")
                    print(f"[SWAP] attempt {attempt}: lock acquired — renaming")
                    _rename_old_to_long_old(conn)
                    _rename_new_to_live(conn)
                    _swap_views(conn)
                    conn.execute(
                        "INSERT INTO control.p2_pipeline_log (isin, step, status, horizon, metric_version, "
                        "message, batch_id) VALUES (NULL, %s, 'OK', NULL, NULL, %s, NULL)",
                        (_SWAP_STEP, f"live={before} staging={after}"),
                    )
                swapped = True
                break
            except psycopg.errors.LockNotAvailable:
                blockers = conn.execute(_LOCK_BLOCKERS_SQL).fetchall()
                print(f"[SWAP] attempt {attempt}/{_MAX_LOCK_ATTEMPTS}: lock_timeout — blocked by: "
                      f"{[dict(zip(('pid', 'user', 'state', 'query', 'since'), b)) for b in blockers]}",
                      file=sys.stderr)
                if attempt < _MAX_LOCK_ATTEMPTS:
                    time.sleep(_LOCK_RETRY_DELAY_S)

        if not swapped:
            raise RuntimeError(
                f"swap could not acquire ACCESS EXCLUSIVE after {_MAX_LOCK_ATTEMPTS} attempts "
                "— run off-peak. The build was rolled back; nothing changed; safe to retry."
            )

        privs = _check_privileges(conn)
        print(f"[PRIVS] {privs}")
        if not privs[APP_ROLE]["SELECT"] or not privs[APP_ROLE]["INSERT"]:
            raise RuntimeError(f"{APP_ROLE} is missing expected privileges post-swap: {privs[APP_ROLE]}")
        for role in RO_ROLES:
            if privs[role]["SELECT"] is False:
                raise RuntimeError(f"{role} is missing SELECT post-swap: {privs[role]}")

    print("[DONE] committed. Next: refresh Superset dataset columns for bi_compat.fund_metric_timeseries "
          "and gold.mv_fmts_latest (both dropped ref_type/ref_value), then let the next P2 cycle run before "
          "--drop-old.")
    return 0


def do_preview(conn) -> int:
    (n,) = conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()
    size = _partitioned_size_pretty(conn, TABLE)
    print(f"live {TABLE}: {n} rows, {size}")
    table_ddl, views_ddl = build_staging_ddl()
    print(f"staging DDL extracted OK: {len(table_ddl)} + {len(views_ddl)} chars "
          "(re-run with --apply to build, verify and swap)")
    return 0


def do_drop_old(conn) -> int:
    rows = conn.execute(
        f"SELECT created_at FROM control.p2_pipeline_log WHERE step = '{_SWAP_STEP}' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchall()
    if not rows:
        print("REFUSED: no recorded swap (control.p2_pipeline_log has no FMTS_PIVOT_SWAP row) "
              "— nothing to drop.", file=sys.stderr)
        return 1
    swap_ts = rows[0][0]
    (ok_after,) = conn.execute(
        "SELECT count(*) FROM control.p2_pipeline_log "
        "WHERE step = 'RUN_SUMMARY' AND status = 'OK' AND created_at > %s",
        (swap_ts,),
    ).fetchone()
    if ok_after == 0:
        print(f"REFUSED: no successful P2 run (RUN_SUMMARY/OK) since the swap at {swap_ts} — "
              "let the next scheduled P2 cycle finish clean first.", file=sys.stderr)
        return 1
    with conn.transaction():
        conn.execute(f"DROP TABLE IF EXISTS {TABLE_OLD} CASCADE")
    print(f"[DONE] dropped {TABLE_OLD} (swap was {swap_ts}, verified {ok_after} clean P2 run(s) since).")
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="build, verify, swap, commit")
    g.add_argument("--drop-old", action="store_true", help="drop the retired *_long_old copy")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    import os
    import shared.config  # noqa: F401  (autoloads .env: FONDOS_PG_DSN_OWNER)
    import psycopg

    dsn = os.environ.get("FONDOS_PG_DSN_OWNER")
    if not dsn:
        print("FONDOS_PG_DSN_OWNER is not set (it is in .env); DDL needs the owner role.", file=sys.stderr)
        return 2
    from psycopg.conninfo import conninfo_to_dict
    d = conninfo_to_dict(dsn)
    print(f"target: host={d.get('host')} port={d.get('port')} dbname={d.get('dbname')} user={d.get('user')}")

    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            if args.drop_old:
                return do_drop_old(conn)
            if args.apply:
                try:
                    return do_apply(conn)
                except _DryRunComplete:
                    return 0
            return do_preview(conn)
    except RuntimeError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
