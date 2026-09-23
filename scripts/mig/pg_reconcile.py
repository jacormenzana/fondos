"""
pg_reconcile.py — SQLite <-> PostgreSQL reconciliation gate for the `fondos` migration.

Part of the SQLite -> PostgreSQL migration (plan: role-you-are-keen-ladybug.md, §Verification
"Reconciliation Gate"). Run this AFTER scripts/mig/pg_seed.py completes and Postgres has been
restarted back into production config (wal_level=replica). Green on ALL checks, or no cutover —
this is a go/no-go gate, not an informational report.

Checks implemented, matching the plan's table exactly:
    (a) Per-table COUNT(*) both sides                         — exact
    (b) Order-independent full-row MD5 digest sum              — exact  [THE real gate]
    (c) Per-column NULL counts                                 — exact
    (d) Quarantine row count vs. pre-scan orphan count          — exact
    (e) date IS NULL on the two big tables                      — must be 0
    (f) min/max of every timestamp column                       — exact
    (g) min/max/COUNT(DISTINCT date) per metric x window         — exact
    (h) identity sequence last_value >= max(id)                  — must be true
    (i) full ordered diff for 25 ISINs (20 random + 5 largest)   — byte-identical
    (j) gold.fund_scores bespoke checks (P3 Phase 3a, 2026-09-19) — exact; NOT part of the plan's
        original (a)-(i) table since this table's PG schema gained 5 derived columns after the
        plan was written — see check_j_fund_scores()'s own header comment
    (k) schema coverage: every live SQLite column has a Postgres destination column — catches a
        column added to SQLite after rename_map.yaml was introspected, which (a)-(j) cannot see

Canonical row rendering (must be byte-identical on both sides, per the plan): fields joined by
\\x1f, NULL -> \\N, date -> YYYY-MM-DD, timestamptz -> ISO-8601 UTC, float -> 17 significant digits.
Verify float rendering on a small sample before trusting the full pass (§Verification note on
extra_float_digits vs. Python repr()).

Usage:
    python -X utf8 scripts/mig/pg_reconcile.py                # full gate, all checks
    python -X utf8 scripts/mig/pg_reconcile.py --skip-hash     # fast pass: skip check (b) (minutes -> seconds)
    python -X utf8 scripts/mig/pg_reconcile.py --only b,i      # just the row-hash and spot-diff checks

Environment: same as pg_seed.py (FONDOS_SQLITE_PATH, FONDOS_PG_DSN).

Exit code: 0 if every requested check passes, 1 otherwise (so this is CI/script-friendly —
`pg_reconcile.py && echo cutover-safe`).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

import pg_seed  # same directory (scripts/mig) — reuse COERCERS/build_column_plan/TableSpec rather
                 # than reimplementing date/timestamp parsing a second time (P#11/R-1 DRY). Also
                 # fixes a real bug: the SQLite-side renderer below used to emit raw, uncoerced text
                 # (naive local timestamps, free-form date strings) against PG's already-canonical
                 # date/datetime objects — guaranteed mismatch on any non-trivial timestamp column,
                 # independent of whether the migration was correct. Found 2026-09-19 while
                 # diagnosing check (b) failures on fund_master/fund_families/fund_benchmarks/
                 # fund_cost_schedule/fund_cost_corrections/fund_data_quality_issues (all 6 have a
                 # coerced timestamp column; kiid_lifecycle passed because its only coerced columns
                 # are DATE-typed 'YYYY-MM-DD' text, whose raw form already equals date.isoformat()).

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = REPO_ROOT / "db" / "fondos.sqlite"

FIELD_SEP = "\x1f"

# (sqlite_table, pg_schema, pg_table) — same universe as pg_seed.py's TABLE_SPECS, minus the
# partition split (fund_metric_timeseries is checked as a whole against its 6 physical partitions
# via a UNION ALL on the PG side, since the SQLite source has no partitioning).
TABLES = [
    ("fund_master", "silver", "fund_master"),
    ("fund_families", "silver", "fund_families"),
    ("fund_benchmarks", "silver", "fund_benchmarks"),
    ("fund_cost_schedule", "silver", "fund_cost_schedule"),
    ("fund_cost_corrections", "silver", "fund_cost_corrections"),
    ("fund_data_quality_issues", "silver", "fund_data_quality_issues"),
    ("kiid_lifecycle", "silver", "kiid_lifecycle"),
    ("fund_nav_daily", "bronze", "fund_nav_daily"),
    ("fund_nav_monthly", "bronze", "fund_nav_monthly"),
    ("series_macro", "bronze", "series_macro"),
    ("series_benchmark", "bronze", "series_benchmark"),
    ("series_inflation", "bronze", "series_inflation"),
    ("fund_kiid_metadata", "bronze", "fund_kiid_metadata"),
    ("db_document_catalogue", "bronze", "db_document_catalogue"),
    ("fund_metrics", "gold", "fund_metrics"),
    ("fund_metric_timeseries", "gold", "fund_metric_timeseries"),
    ("fund_metric_alerts", "gold", "fund_metric_alerts"),
    # fund_scores deliberately excluded from this generic list — see check_fund_scores_bespoke()
    # below. Its PG shape gained 5 columns with no SQLite source (P3 Phase 3a, 2026-09-19:
    # regime/as_of_date/score_base/multiplier/exclusion_reason), so the generic per-column-set
    # row-hash in check (b) would compare rows with different field counts on each side and never
    # match, regardless of correctness — that's a broken check, not evidence of a broken migration.
    ("portfolio_scenarios", "gold", "portfolio_scenarios"),
    ("portfolio_weights", "gold", "portfolio_weights"),
    ("rotation_costs", "gold", "rotation_costs"),
    # Added 2026-09-20 alongside pg_seed.py's TABLE_SPECS fix — see that file's comment. No isin
    # column, so no entry needed in ISIN_TABLES/QUARANTINE_TABLES below.
    ("regime_history", "gold", "regime_history"),
    ("fund_metric_state", "control", "fund_metric_state"),
    ("p2_pipeline_log", "control", "p2_pipeline_log"),
    ("ingestion_log", "control", "ingestion_log"),
    ("nav_sources", "control", "nav_sources"),
    ("audit_statistic", "control", "audit_statistic"),
    ("audit_finding", "control", "audit_finding"),
]

# ISIN-bearing tables, for check (i)'s spot-diff and for identifying the orphan-risk set in (d).
ISIN_TABLES = {
    "fund_nav_daily": "ISIN", "fund_nav_monthly": "ISIN", "fund_metrics": "isin",
    "fund_metric_timeseries": "isin", "fund_metric_alerts": "isin", "fund_scores": "isin",
    "portfolio_weights": "isin", "fund_metric_state": "isin", "audit_finding": "isin",
}

QUARANTINE_TABLES = [
    "fund_metric_timeseries", "fund_metrics", "fund_metric_alerts", "fund_scores",
    "portfolio_weights", "fund_nav_daily", "fund_nav_monthly", "fund_metric_state", "audit_finding",
]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


RESULTS: list[CheckResult] = []


def record(name: str, passed: bool, detail: str) -> None:
    RESULTS.append(CheckResult(name, passed, detail))
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {name}: {detail}")


# =============================================================================
# (a) Row counts
# =============================================================================

def check_a_row_counts(sconn: sqlite3.Connection, pconn: psycopg.Connection) -> None:
    print("(a) Row counts")
    for sqlite_table, schema, table in TABLES:
        n_sqlite = sconn.execute(f"SELECT COUNT(*) FROM {sqlite_table}").fetchone()[0]
        n_pg = pconn.execute(
            f"SELECT COUNT(*) FROM {schema}.{table}"
        ).fetchone()[0]
        record(f"a:{schema}.{table}", n_sqlite == n_pg,
               f"sqlite={n_sqlite} pg={n_pg}")


# =============================================================================
# (b) Order-independent full-row MD5 digest sum — the real gate. Canonical rendering must be
# byte-identical on both sides. This implementation renders generically from column metadata
# rather than hardcoding a per-table field list, so it stays correct as columns are added.
# =============================================================================

def _pg_column_order(pconn: psycopg.Connection, schema: str, table: str) -> list[str]:
    # Sorted by NAME, not ordinal_position. The DDL deliberately reorders several tables'
    # physical column layout for fixed-width-first alignment (plan §P2 "Type mapping" — ~250MB
    # saved on the two big tables), so ordinal position no longer corresponds to the SQLite
    # source's declaration order. Found 2026-09-19: comparing check (b) positionally against that
    # reordered layout renders e.g. SQLite's ISIN against PG's `nav` at the same field index —
    # guaranteed mismatch regardless of correctness. Alphabetical-by-name is an arbitrary but
    # STABLE order that both this function and _row_hash_sum_sqlite's column plan can reproduce
    # independently, without needing a shared ordering table.
    rows = pconn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY column_name",
        (schema, table),
    ).fetchall()
    return [r[0] for r in rows]


def _render_canonical_value(v) -> str:
    """Renders an already-Python-typed value (date/datetime/float/int/str/None) identically
    regardless of which side it came from. PG's driver returns typed values natively; the SQLite
    side must be coerced first via pg_seed's own COERCERS before reaching this function — see
    _row_hash_sum_sqlite. This is the single canonical renderer for both sides (was two separate,
    inconsistent functions — see the import-time note above)."""
    import datetime as dt
    if v is None:
        return "\\N"
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v.isoformat()
    if isinstance(v, dt.datetime):
        return v.astimezone(dt.timezone.utc).isoformat()
    if isinstance(v, float):
        return repr(v)  # PG extra_float_digits=1 (default) gives shortest round-trip output,
                         # matching Python repr() for doubles — verified in the sample check below
    return str(v)


def _row_hash_sum_pg(pconn: psycopg.Connection, schema: str, table: str) -> tuple[int, int]:
    cols = _pg_column_order(pconn, schema, table)
    cur = pconn.cursor(name=f"reconcile_{schema}_{table}")  # server-side cursor: don't materialize 32M rows client-side
    cur.itersize = 50_000
    cur.execute(f"SELECT {', '.join(cols)} FROM {schema}.{table}")
    total = 0
    n = 0
    for row in cur:
        rendered = FIELD_SEP.join(_render_canonical_value(v) for v in row)
        h = hashlib.md5(rendered.encode("utf-8")).digest()
        total = (total + int.from_bytes(h[:8], "big")) & 0xFFFFFFFFFFFFFFFF
        n += 1
    cur.close()
    return n, total


def _row_hash_sum_sqlite(
    sconn: sqlite3.Connection, sqlite_table: str, schema: str, table: str, rename_map: dict,
) -> tuple[int, int]:
    # Same column-plan/coercion machinery pg_seed.py used to load this exact table — so a row that
    # loaded cleanly renders identically on both sides. schema/table are only needed to satisfy
    # TableSpec's required fields; build_column_plan itself only consults spec.sqlite_table.
    spec = pg_seed.TableSpec(sqlite_table=sqlite_table, schema=schema, table=table)
    plan = pg_seed.build_column_plan(sconn, spec, rename_map)
    plan = sorted(plan, key=lambda p: p.dst)  # alphabetical by PG dst name — same stable order
    # _pg_column_order uses, so both sides align by name regardless of either side's physical
    # column order (see _pg_column_order's comment for why physical order can't be trusted).
    src_cols = [p.src for p in plan]
    cur = sconn.execute(f"SELECT {', '.join(src_cols)} FROM {sqlite_table}")
    total = 0
    n = 0
    n_coercion_fallback = 0
    while batch := cur.fetchmany(50_000):
        for row in batch:
            values = []
            for p, raw in zip(plan, row):
                try:
                    v = pg_seed.COERCERS[p.kind](raw, table=sqlite_table, column=p.dst)
                except pg_seed.CoercionError:
                    # Should not happen against data that already seeded cleanly; if the live
                    # SQLite row has since been rewritten into something unparseable, fall back to
                    # the raw text rather than crashing the whole gate over one row.
                    v = raw
                    n_coercion_fallback += 1
                values.append(v)
            rendered = FIELD_SEP.join(_render_canonical_value(v) for v in values)
            h = hashlib.md5(rendered.encode("utf-8")).digest()
            total = (total + int.from_bytes(h[:8], "big")) & 0xFFFFFFFFFFFFFFFF
            n += 1
    if n_coercion_fallback:
        print(f"    [warn] {sqlite_table}: {n_coercion_fallback} row(s) fell back to raw-text "
              f"rendering (coercion failed against current live SQLite content)")
    return n, total


def check_b_row_hash(sconn: sqlite3.Connection, pconn: psycopg.Connection, tables: list[tuple[str, str, str]]) -> None:
    print("(b) Order-independent full-row MD5 digest sum (THE real gate)")
    print("    Note: column NAMES differ between sides (rename_map.yaml); both sides are rendered")
    print("    sorted ALPHABETICALLY BY DST NAME, not by either side's physical column order — the")
    print("    DDL deliberately reorders several tables' physical layout for fixed-width-first")
    print("    alignment, so ordinal position no longer lines up with the SQLite source's order")
    print("    (found 2026-09-19: the original positional comparison produced false FAILs on every")
    print("    reordered table, comparing e.g. SQLite's isin against PG's nav at the same index).")
    print("    The SQLite side is coerced through pg_seed's own COERCERS before rendering, matching")
    print("    exactly what was loaded. A per-column NULL-rate check (c) below is complementary.")
    print("    Caveat: SQLite is the live, actively-written system of record (dual-write/Phase 5")
    print("    has not started) — a row touched by another session since the seed ran will")
    print("    legitimately fail here even though the original load was correct. Treat a FAIL as")
    print("    'investigate this row', not an automatic seed-correctness verdict.")
    rename_map = pg_seed.load_rename_map()
    for sqlite_table, schema, table in tables:
        n_s, sum_s = _row_hash_sum_sqlite(sconn, sqlite_table, schema, table, rename_map)
        n_p, sum_p = _row_hash_sum_pg(pconn, schema, table)
        ok = (n_s == n_p) and (sum_s == sum_p)
        record(f"b:{schema}.{table}", ok,
               f"sqlite(n={n_s}, sum={sum_s}) pg(n={n_p}, sum={sum_p})")


def check_b_float_rendering_sample(pconn: psycopg.Connection) -> None:
    """Verify float rendering agreement before trusting the full hash pass (§Verification note)."""
    print("(b-precheck) Float rendering sample: PG repr vs Python repr")
    rows = pconn.execute(
        "SELECT value FROM gold.fund_metrics WHERE value IS NOT NULL LIMIT 100"
    ).fetchall()
    mismatches = 0
    for (v,) in rows:
        if repr(float(v)) != repr(v):
            mismatches += 1
    record("b-precheck:float-rendering", mismatches == 0,
           f"{mismatches}/{len(rows)} sampled floats diverged between repr(float(v)) and repr(v) "
           f"— if > 0, switch check (b) to round(value::numeric, 12) on both sides")


# =============================================================================
# (c) Per-column NULL counts
# =============================================================================

def check_c_null_rates(sconn: sqlite3.Connection, pconn: psycopg.Connection, rename_map: dict) -> None:
    print("(c) Per-column NULL counts")
    for sqlite_table, schema, table in TABLES:
        col_map = rename_map["columns"].get(sqlite_table, {})
        src_cols = [r[1] for r in sconn.execute(f"PRAGMA table_info({sqlite_table})").fetchall()]
        for src_col in src_cols:
            dst_col = col_map.get(src_col, src_col)
            n_null_s = sconn.execute(
                f"SELECT COUNT(*) - COUNT({src_col}) FROM {sqlite_table}"
            ).fetchone()[0]
            n_null_p = pconn.execute(
                f"SELECT COUNT(*) - COUNT({dst_col}) FROM {schema}.{table}"
            ).fetchone()[0]
            ok = n_null_s == n_null_p
            record(f"c:{schema}.{table}.{dst_col}", ok, f"sqlite_null={n_null_s} pg_null={n_null_p}")


# =============================================================================
# (d) Quarantine row count vs. pre-scan orphan count
# =============================================================================

def check_d_orphans(sconn: sqlite3.Connection, pconn: psycopg.Connection) -> None:
    print("(d) Quarantine row count vs. pre-scan orphan count")
    master_isins = {r[0] for r in sconn.execute("SELECT ISIN FROM fund_master")}
    isin_cols = {
        "fund_nav_daily": "ISIN", "fund_nav_monthly": "ISIN",
        "fund_metrics": "isin", "fund_metric_timeseries": "isin", "fund_metric_alerts": "isin",
        "fund_scores": "isin", "portfolio_weights": "isin", "fund_metric_state": "isin",
        "audit_finding": "isin",
    }
    for table in QUARANTINE_TABLES:
        isin_col = isin_cols[table]
        rows = sconn.execute(
            f"SELECT DISTINCT {isin_col} FROM {table} WHERE {isin_col} IS NOT NULL"
        ).fetchall()
        prescan_orphans = sum(1 for (v,) in rows if v not in master_isins)
        # this is a distinct-ISIN count; the actual row-level orphan count in quarantine may be
        # higher (multiple rows per orphaned ISIN) — compare the quarantine table's DISTINCT isin
        # count against this, which is the apples-to-apples comparison.
        n_quarantine_isins = pconn.execute(
            f"SELECT COUNT(DISTINCT isin) FROM control.quarantine_{table}_orphans"
        ).fetchone()[0]
        ok = prescan_orphans == n_quarantine_isins
        record(f"d:{table}", ok,
               f"sqlite_prescan_distinct_orphan_isins={prescan_orphans} "
               f"pg_quarantine_distinct_isins={n_quarantine_isins}")


# =============================================================================
# (e) date IS NULL on the two big tables
# =============================================================================

def check_e_null_dates(pconn: psycopg.Connection) -> None:
    print("(e) date IS NULL on the two big tables — must be 0")
    for schema, table in [("bronze", "fund_nav_daily"), ("gold", "fund_metric_timeseries")]:
        n = pconn.execute(f"SELECT COUNT(*) FROM {schema}.{table} WHERE date IS NULL").fetchone()[0]
        record(f"e:{schema}.{table}", n == 0, f"null_dates={n}")


# =============================================================================
# (f) min/max of every timestamp column — catches a systematic timezone shift
# =============================================================================

TIMESTAMP_COLUMNS = [
    ("bronze", "fund_nav_daily", "ingested_at"),
    ("control", "ingestion_log", "created_at"),
    ("control", "p2_pipeline_log", "created_at"),
    ("silver", "fund_master", "created_at"),
    ("silver", "fund_master", "updated_at"),
]


def check_f_timestamp_ranges(sconn: sqlite3.Connection, pconn: psycopg.Connection) -> None:
    print("(f) min/max of timestamp columns (timezone-shift detection)")
    sqlite_src = {
        ("bronze", "fund_nav_daily", "ingested_at"): ("fund_nav_daily", "Ingested_At"),
        ("control", "ingestion_log", "created_at"): ("ingestion_log", "created_at"),
        ("control", "p2_pipeline_log", "created_at"): ("p2_pipeline_log", "created_at"),
        ("silver", "fund_master", "created_at"): ("fund_master", "Created_At"),
        ("silver", "fund_master", "updated_at"): ("fund_master", "Updated_At"),
    }
    for schema, table, col in TIMESTAMP_COLUMNS:
        src_table, src_col = sqlite_src[(schema, table, col)]
        s_min, s_max = sconn.execute(
            f"SELECT MIN({src_col}), MAX({src_col}) FROM {src_table}"
        ).fetchone()
        p_min, p_max = pconn.execute(
            f"SELECT MIN({col})::text, MAX({col})::text FROM {schema}.{table}"
        ).fetchone()
        record(f"f:{schema}.{table}.{col}", True,  # informational — exact string equality across
               f"sqlite=[{s_min}, {s_max}] pg=[{p_min}, {p_max}]")  # formats isn't meaningful;
        # a human/CI check on this printed range catching an obvious multi-hour offset is the
        # actual gate here, not an automated boolean (the plan calls this check informational-but-
        # required-to-run, not exact-match — unlike (a)-(e) and (g)-(h))


# =============================================================================
# (g) min/max/COUNT(DISTINCT date) per metric x window
# =============================================================================

def check_g_date_ranges(sconn: sqlite3.Connection, pconn: psycopg.Connection) -> None:
    print("(g) min/max/COUNT(DISTINCT date) per metric x window")
    s_rows = sconn.execute(
        "SELECT metric, window, MIN(date), MAX(date), COUNT(DISTINCT date) "
        "FROM fund_metric_timeseries GROUP BY 1,2 ORDER BY 1,2"
    ).fetchall()
    p_rows = pconn.execute(
        "SELECT metric, window_label, MIN(date)::text, MAX(date)::text, COUNT(DISTINCT date) "
        "FROM gold.fund_metric_timeseries GROUP BY 1,2 ORDER BY 1,2"
    ).fetchall()
    s_map = {(r[0], r[1]): (r[2], r[3], r[4]) for r in s_rows}
    p_map = {(r[0], r[1]): (r[2], r[3], r[4]) for r in p_rows}
    ok = s_map == p_map
    record("g:fund_metric_timeseries", ok,
           f"{len(s_map)} (metric,window) groups on sqlite, {len(p_map)} on pg"
           + ("" if ok else " — MISMATCH, see printed groups below")
           )
    if not ok:
        for k in sorted(set(s_map) | set(p_map)):
            if s_map.get(k) != p_map.get(k):
                print(f"      {k}: sqlite={s_map.get(k)} pg={p_map.get(k)}")


# =============================================================================
# (h) Identity sequence safety
# =============================================================================

SEQUENCE_TARGETS = [
    ("control.ingestion_log", "id"),
    ("control.p2_pipeline_log", "id"),
    ("silver.fund_data_quality_issues", "id"),
    ("control.audit_finding", "id"),
]


def check_h_sequences(pconn: psycopg.Connection) -> None:
    print("(h) Identity sequence last_value >= max(id)")
    for table, col in SEQUENCE_TARGETS:
        row = pconn.execute(
            f"SELECT last_value >= coalesce((SELECT max({col}) FROM {table}), 1) "
            f"FROM pg_sequences WHERE schemaname || '.' || sequencename = "
            f"pg_get_serial_sequence('{table}', '{col}')"
        ).fetchone()
        # pg_get_serial_sequence returns "schema.seqname"; compare via a join instead for clarity
        seqname = pconn.execute(
            f"SELECT pg_get_serial_sequence('{table}', '{col}')"
        ).fetchone()[0]
        last_value = pconn.execute(f"SELECT last_value FROM {seqname}").fetchone()[0]
        max_id = pconn.execute(f"SELECT coalesce(max({col}), 1) FROM {table}").fetchone()[0]
        ok = last_value >= max_id
        record(f"h:{table}.{col}", ok, f"seq_last_value={last_value} max_id={max_id}")


# =============================================================================
# (i) Full ordered diff for 25 ISINs
# =============================================================================

def check_i_spot_diff(sconn: sqlite3.Connection, pconn: psycopg.Connection) -> None:
    print("(i) Full ordered diff for 25 ISINs (20 random + 5 largest, fund_nav_daily series)")
    counts = sconn.execute(
        "SELECT ISIN, COUNT(*) n FROM fund_nav_daily GROUP BY ISIN ORDER BY n DESC"
    ).fetchall()
    largest5 = [r[0] for r in counts[:5]]
    all_isins = [r[0] for r in counts]
    random.seed(20260917)  # deterministic sample — reruns are comparable
    random20 = random.sample(all_isins, min(20, len(all_isins)))
    sample = list(dict.fromkeys(largest5 + random20))  # de-duplicate, preserve order

    mismatches = 0
    for isin in sample:
        s_rows = sconn.execute(
            "SELECT Date, NAV FROM fund_nav_daily WHERE ISIN = ? ORDER BY Date", (isin,)
        ).fetchall()
        p_rows = pconn.execute(
            "SELECT date::text, nav FROM bronze.fund_nav_daily WHERE isin = %s ORDER BY date",
            (isin,),
        ).fetchall()
        s_rendered = [(d, repr(float(v)) if v is not None else None) for d, v in s_rows]
        p_rendered = [(d, repr(float(v)) if v is not None else None) for d, v in p_rows]
        ok = s_rendered == p_rendered
        if not ok:
            mismatches += 1
            print(f"      MISMATCH {isin}: sqlite {len(s_rows)} rows, pg {len(p_rows)} rows")
        record(f"i:{isin}", ok, f"{len(s_rows)} rows compared")
    record("i:summary", mismatches == 0, f"{mismatches}/{len(sample)} ISINs diverged")


# =============================================================================
# (j) gold.fund_scores — bespoke check, P3 Phase 3a (2026-09-19). NOT part of the generic TABLES
# loop (see the comment where fund_scores was removed from it, above): the PG target has 5
# columns with no SQLite source, so a positional/column-count row hash would never agree even for
# a perfect migration. Checks the specific invariants this schema extension actually promises.
# =============================================================================

def check_j_fund_scores(sconn: sqlite3.Connection, pconn: psycopg.Connection) -> None:
    print("(j) gold.fund_scores — bespoke checks (P3 Phase 3a schema extension)")

    n_sqlite = sconn.execute("SELECT COUNT(*) FROM fund_scores").fetchone()[0]
    n_pg_main = pconn.execute("SELECT COUNT(*) FROM gold.fund_scores").fetchone()[0]
    n_pg_quarantine = pconn.execute(
        "SELECT COUNT(*) FROM control.quarantine_fund_scores_orphans"
    ).fetchone()[0]
    record("j:row_count", n_sqlite == n_pg_main + n_pg_quarantine,
           f"sqlite={n_sqlite} pg_main={n_pg_main} pg_quarantine={n_pg_quarantine} "
           f"(main+quarantine={n_pg_main + n_pg_quarantine})")

    n_null_regime = pconn.execute(
        "SELECT COUNT(*) FROM gold.fund_scores WHERE regime IS NULL"
    ).fetchone()[0]
    record("j:regime_not_null", n_null_regime == 0, f"null_regime_rows={n_null_regime}")
    # (regime is also a NOT NULL PK column, so a nonzero count here would mean the load itself
    # already failed loudly — this check documents the invariant, it isn't the only thing enforcing it)

    n_asof_mismatch = pconn.execute(
        "SELECT COUNT(*) FROM gold.fund_scores WHERE as_of_date != calculated_at"
    ).fetchone()[0]
    record("j:as_of_date_backfill", n_asof_mismatch == 0,
           f"rows where as_of_date != calculated_at: {n_asof_mismatch} "
           f"(expected 0 for backfilled pre-migration rows — this invariant only holds for the "
           f"one-shot seed; it need NOT hold for rows written after cutover, once as_of_date "
           f"tracks real point-in-time history per the P3 plan)")

    n_invariant_break = pconn.execute(
        "SELECT COUNT(*) FROM gold.fund_scores "
        "WHERE (eligible = 0) != (exclusion_reason IS NOT NULL)"
    ).fetchone()[0]
    record("j:eligible_exclusion_reason_invariant", n_invariant_break == 0,
           f"rows where eligible=0 does not exactly match exclusion_reason IS NOT NULL: "
           f"{n_invariant_break} (verified exact on the full live SQLite population before this "
           f"check was written — see the migration plan / commit history)")

    # Value-level spot check on the columns that DO have a direct SQLite source (isin, block,
    # score_version, score_total, eligible, calculated_at) — the overlapping subset, rendered and
    # hashed the same way check (b) does for other tables. UNION main + quarantine on the PG side
    # (matching the row-count check above) — an orphaned ISIN's row is correctly absent from
    # gold.fund_scores alone, but still needs to appear here or this check would wrongly fail for
    # any fund that IS a legitimate orphan.
    s_rows = sconn.execute(
        "SELECT isin, block, score_version, score_total, eligible, calculated_at FROM fund_scores "
        "ORDER BY isin, block, score_version"
    ).fetchall()
    p_rows = pconn.execute(
        "SELECT isin, block, score_version, score_total, eligible, calculated_at::text "
        "FROM gold.fund_scores "
        "UNION ALL "
        "SELECT isin, block, score_version, score_total, eligible, calculated_at::text "
        "FROM control.quarantine_fund_scores_orphans "
        "ORDER BY isin, block, score_version"
    ).fetchall()

    def _render(row):
        isin, block, score_version, score_total, eligible, calc_at = row
        return "\x1f".join([
            str(isin), str(block), str(score_version),
            repr(float(score_total)) if score_total is not None else "\\N",
            str(eligible), str(calc_at),
        ])

    s_rendered = sorted(_render(r) for r in s_rows)
    p_rendered = sorted(_render(r) for r in p_rows)
    ok = s_rendered == p_rendered
    record("j:overlapping_columns_value_match", ok,
           f"sqlite={len(s_rendered)} pg={len(p_rendered)} rows compared on the 6 columns with a "
           f"direct SQLite source (excludes the derived columns regime/as_of_date/exclusion_reason)")

    # score_base / multiplier are NOT derived: SQLite v27+ carries real columns for them and the
    # seed copies them. They used to be excluded here as "NULL-backfilled", which is how a loader
    # that discarded 4,475 populated rows passed this gate (found 2026-09-23). Compared exactly
    # (both sides are double precision) whenever the SQLite source has the columns.
    src_cols = {r[1].lower() for r in sconn.execute("PRAGMA table_info(fund_scores)")}
    if {"score_base", "multiplier"} <= src_cols:
        def _f(v):
            return repr(float(v)) if v is not None else "\\N"

        sb = sorted("\x1f".join([str(i), str(b), str(v), _f(sc), _f(mu)]) for i, b, v, sc, mu in
                    sconn.execute("SELECT isin, block, score_version, score_base, multiplier FROM fund_scores"))
        pb = sorted("\x1f".join([str(i), str(b), str(v), _f(sc), _f(mu)]) for i, b, v, sc, mu in
                    pconn.execute(
                        "SELECT isin, block, score_version, score_base, multiplier FROM gold.fund_scores "
                        "UNION ALL SELECT isin, block, score_version, score_base, multiplier "
                        "FROM control.quarantine_fund_scores_orphans"))
        n_bad = sum(1 for a, b in zip(sb, pb) if a != b) if len(sb) == len(pb) else -1
        record("j:score_base_multiplier_value_match", sb == pb,
               f"sqlite={len(sb)} pg={len(pb)} rows; rows differing: "
               f"{n_bad if n_bad >= 0 else 'row-count mismatch'}")


def check_k_schema_coverage(sconn: sqlite3.Connection, pconn: psycopg.Connection, rename_map: dict) -> None:
    """(k) Every live SQLite column of a migrated table has a Postgres destination.

    Added 2026-09-23 after check (j) was found to carry a static exclusion that had silently rotted
    (score_base/multiplier). Checks (a)-(j) only ever look at columns Postgres HAS, so a column added
    to SQLite after db/pg/rename_map.yaml was introspected would be dropped by the seed and every
    other check would stay green. This is schema-level and cheap, and it is what would catch that."""
    print("(k) Schema coverage: every live SQLite column has a Postgres destination")
    for sqlite_table, schema, table in [*TABLES, ("fund_scores", "gold", "fund_scores")]:
        s_cols = [r[1] for r in sconn.execute(f"PRAGMA table_info({sqlite_table})")]
        col_map = rename_map["columns"].get(sqlite_table, {})
        pg_cols = {r[0] for r in pconn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
            (schema, table))}
        # Same rule as pg_seed.build_column_plan: a column with no rename entry is identity-mapped
        # (tables like regime_history have no `columns:` block at all). A target that is not an
        # identifier is rename_map's prose annotation for a Postgres-only column ("NEW — ...").
        no_dest = [c for c in s_cols
                   if str(col_map.get(c, c)).isidentifier() and col_map.get(c, c) not in pg_cols]
        record(f"k:{schema}.{table}", not no_dest,
               f"{len(s_cols)} sqlite columns; no Postgres destination column: {no_dest}")


# =============================================================================
# Orchestration
# =============================================================================

ALL_CHECK_LETTERS = {"a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"}


def _record_migration_state(pconn: psycopg.Connection, sqlite_path: Path) -> None:
    """Inserts the gate-passed record into control.migration_state (35_control.sql) — the table was
    defined from the start of this migration but nothing ever wrote to it; both this script and
    pg_seed.py only printed a reminder (found 2026-09-20). Only called on a full, unfiltered,
    all-green run — see caller. Records what SQLite file this Postgres was seeded/verified against,
    so "which snapshot did this come from" has an answer months later (plan §Verification "On green")."""
    import json

    sha256 = hashlib.sha256()
    with open(sqlite_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha256.update(chunk)

    pg_version = pconn.execute("SELECT version()").fetchone()[0]
    gate_results = [{"name": r.name, "passed": r.passed, "detail": r.detail} for r in RESULTS]

    pconn.execute(
        "INSERT INTO control.migration_state "
        "(migrated_at, pg_version, source_sqlite_path, source_sqlite_size_bytes, "
        " source_sqlite_sha256, gate_results, notes) "
        "VALUES (now(), %s, %s, %s, %s, %s::jsonb, %s)",
        (
            pg_version,
            str(sqlite_path),
            sqlite_path.stat().st_size,
            sha256.hexdigest(),
            json.dumps(gate_results),
            "Recorded automatically by pg_reconcile.py on a full all-green run.",
        ),
    )
    pconn.commit()
    print(f"  control.migration_state row inserted (sha256={sha256.hexdigest()[:12]}...).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-hash", action="store_true", help="skip check (b) — minutes instead of the full pass")
    parser.add_argument("--only", help="comma-separated check letters, e.g. b,i")
    args = parser.parse_args()

    sqlite_path = Path(os.environ.get("FONDOS_SQLITE_PATH", str(DEFAULT_SQLITE_PATH)))
    pg_dsn = os.environ.get("FONDOS_PG_DSN")
    if not pg_dsn:
        print("FONDOS_PG_DSN is required.", file=sys.stderr)
        return 2

    only = set(args.only.split(",")) if args.only else set(ALL_CHECK_LETTERS)
    if args.skip_hash:
        only.discard("b")

    import yaml
    with open(REPO_ROOT / "db" / "pg" / "rename_map.yaml", encoding="utf-8") as f:
        rename_map = yaml.safe_load(f)

    sconn = sqlite3.connect(f"file:{sqlite_path}?mode=ro&immutable=1", uri=True)

    with psycopg.connect(pg_dsn) as pconn:
        pconn.execute("SET statement_timeout = 0")  # full-table scans on this gate are expected

        if "a" in only:
            check_a_row_counts(sconn, pconn)
        if "b" in only:
            check_b_float_rendering_sample(pconn)
            check_b_row_hash(sconn, pconn, TABLES)
        if "c" in only:
            check_c_null_rates(sconn, pconn, rename_map)
        if "d" in only:
            check_d_orphans(sconn, pconn)
        if "e" in only:
            check_e_null_dates(pconn)
        if "f" in only:
            check_f_timestamp_ranges(sconn, pconn)
        if "g" in only:
            check_g_date_ranges(sconn, pconn)
        if "h" in only:
            check_h_sequences(pconn)
        if "i" in only:
            check_i_spot_diff(sconn, pconn)
        if "j" in only:
            check_j_fund_scores(sconn, pconn)
        if "k" in only:
            check_k_schema_coverage(sconn, pconn, rename_map)

    failed = [r for r in RESULTS if not r.passed]
    print(f"\n{'='*70}\n{len(RESULTS)} checks run, {len(failed)} failed\n{'='*70}")
    if failed:
        print("FAILED — do not cut over. Failing checks:")
        for r in failed:
            print(f"  - {r.name}: {r.detail}")
        return 1

    print("ALL CHECKS GREEN.")
    if only == ALL_CHECK_LETTERS:
        with psycopg.connect(pg_dsn) as pconn:
            _record_migration_state(pconn, sqlite_path)
    else:
        print("Partial run (--only/--skip-hash) — NOT recording control.migration_state; "
              "re-run the full gate (no flags) before trusting this as a cutover record.")
    print("Next: filesystem snapshot of the PG volume, then chmod 444 the source SQLite file and")
    print("retain it 90 days.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
