"""
pg_seed.py — One-shot SQLite -> PostgreSQL seed loader for the `fondos` migration.

Part of the SQLite -> PostgreSQL migration (plan: role-you-are-keen-ladybug.md, §P3 "Data
Ingestion & Table Population"). Replaces `shared/load_fondos_to_postgres.py` outright — that
script's incremental batch_id sync exists only because SQLite was the system of record and
Postgres a downstream copy; once PG is authoritative, that machinery is actively dangerous (a
stale run would delete freshly-computed PG rows and overwrite them from SQLite). This script has
no incremental mode. It is a ONE-SHOT SEED, run exactly once against an empty (freshly-DDL'd)
Postgres database.

Prerequisites (NOT done by this script):
    1. Postgres is up with db/pg/00_roles_schemas.sql .. 40_matviews.sql already applied — either
       via the docker-compose.yml auto-init path (first-ever container start) or the manual
       `psql -f` loop documented at the top of docker/docker-compose.yml.
    2. The server is running with the LOAD-TIME config (`wal_level = minimal`,
       `max_wal_size = 16GB`, `archive_mode = off`, `max_wal_senders = 0`) — a config swap +
       container restart, done by the operator, not this script (see plan §P3 "WAL strategy").
       A filesystem snapshot of the PGDATA volume should exist before this script runs — PITR is
       off during the load window.
    3. shared/config.py's ROLLING_TIMESERIES_METRICS still matches the 5 partitions declared in
       db/pg/30_gold.sql (fmts_p_vol_ann/max_dd/return_ann/sharpe/sortino) — the CI test
       `test_partition_covers_every_rolling_metric` (§5a) is the ongoing guard for this once the
       test harness exists; this script does a lighter runtime check of the same fact (see
       `_assert_partition_metrics_match`).

What this script does NOT do (left for the operator / a separate step):
    - Restart Postgres into/out of load-time config (steps 1 and 14 of the plan's execution
      order) — a container restart, not a SQL operation.
    - Run the reconciliation gate (scripts/mig/pg_reconcile.py, §Verification) — run that
      separately once this script reports success.

Design note on index-stripping (a documented, deliberate simplification vs. the plan's idealized
ordering): the plan's execution order calls for building every table bare (NOT NULL only, no
PK/index), COPYing, then adding constraints/indexes afterward — index-build-after-load is
materially faster than loading into an already-indexed table. This script applies that discipline
ONLY to `bronze.fund_nav_daily` (13.9M rows, a plain non-partitioned table — trivial to strip and
rebuild). It does NOT attempt the same for `gold.fund_metric_timeseries`'s partitions: stripping a
partitioned parent's PK requires DETACH/re-ATTACH maneuvers per partition, and that complexity and
risk was judged not worth it for a one-time load — COPYing into the already-PK-indexed partitions
is slower than the idealized path but not by an amount that justifies the added risk. If this
script's measured wall-clock for the fmts load exceeds ~2x the plan's estimate, revisit.

Usage:
    python -X utf8 scripts/mig/pg_seed.py                  # run (resumable — see control.seed_progress)
    python -X utf8 scripts/mig/pg_seed.py --dry-run         # print the unit plan, touch nothing
    python -X utf8 scripts/mig/pg_seed.py --only bronze.fund_nav_daily   # single unit (debug)
    # Refuses (exit 3) a target that already passed the reconciliation gate, or whose tables hold rows
    # control.seed_progress does not record as loaded. --force-reseed bypasses only the first refusal.

Environment:
    FONDOS_SQLITE_PATH   default: db/fondos.sqlite (relative to repo root)
    FONDOS_PG_DSN        e.g. "postgresql://fondos_owner@localhost:5432/fondos"
                          Password via PGPASSWORD env var or a .pgpass file — never on the command
                          line or hardcoded (same discipline as the pgBackRest bucket key, §P1
                          "Credential handling").
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML is required (pip install pyyaml)", file=sys.stderr)
    raise

REPO_ROOT = Path(__file__).resolve().parents[2]
RENAME_MAP_PATH = REPO_ROOT / "db" / "pg" / "rename_map.yaml"
DEFAULT_SQLITE_PATH = REPO_ROOT / "db" / "fondos.sqlite"

MADRID = ZoneInfo("Europe/Madrid")
UTC = dt.timezone.utc

FETCH_CHUNK = 50_000    # per SQLite fetchmany() — the SQLite-side fetch is chunked; the COPY
                        # stream to Postgres is NOT (one COPY per unit, never restarted per chunk)

FMTS_METRICS = ["vol_ann", "max_dd", "return_ann", "sharpe", "sortino"]  # must match
    # shared/config.py ROLLING_TIMESERIES_METRICS and db/pg/30_gold.sql's 5 named partitions —
    # checked at runtime by _assert_partition_metrics_match(), enforced permanently by the CI
    # test in §5a once the test harness exists.

EXCLUDED_TABLES = {
    "_piloto_snapshot", "fund_master_20260430", "fund_kiid_metadata_20260430",
    "fund_cost_schedule_snapshot_20260824", "fund_cost_schedule_snapshot_20260824b",
}


# =============================================================================
# Coercion — the highest-risk boundary in this migration (plan §P3 "Coercion in Python").
# Every function here RAISES on an unparseable non-NULL input. None ever return None for input
# that fails to parse — silently NULLing an unparseable date/timestamp is the single highest-risk
# failure mode of this migration (it would survive both row-count and hash reconciliation checks).
# =============================================================================

class CoercionError(ValueError):
    """Raised when a source value cannot be safely coerced. Carries enough context to find the
    offending row without re-scanning the source table."""


def coerce_date(raw: Optional[str], *, table: str, column: str) -> Optional[dt.date]:
    if raw is None:
        return None
    try:
        return dt.date.fromisoformat(raw.strip())
    except (ValueError, AttributeError) as exc:
        raise CoercionError(f"{table}.{column}: unparseable date {raw!r}") from exc


_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S",       # fund_master.Created_At style (naive, space-separated)
    "%Y-%m-%dT%H:%M:%S%z",     # fund_master.Updated_At style (ISO-8601, tz-aware)
    "%Y-%m-%dT%H:%M:%S",       # fund_benchmarks.extracted_at style (ISO-8601, no offset)
)


def coerce_timestamptz(raw: Optional[str], *, table: str, column: str) -> Optional[dt.datetime]:
    """Three incompatible text timestamp formats exist in the live SQLite DB (plan §P2 "Type
    mapping"). Try each explicitly; a naive value is localized as Europe/Madrid, then converted
    to UTC for storage. Raises on anything matching none of the three."""
    if raw is None:
        return None
    raw = raw.strip()
    # SQLite's CURRENT_TIMESTAMP default sometimes yields a trailing 'Z' or fractional seconds —
    # normalize the common variants before trying the exact formats.
    candidate = raw[:19] if len(raw) > 19 and raw[19] in (".", "Z") else raw
    for fmt in _TS_FORMATS:
        try:
            parsed = dt.datetime.strptime(candidate, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=MADRID)
        return parsed.astimezone(UTC)
    raise CoercionError(f"{table}.{column}: unparseable timestamp {raw!r}")


def coerce_smallint(raw: Any, *, table: str, column: str) -> Optional[int]:
    if raw is None:
        return None
    try:
        v = int(raw)
    except (ValueError, TypeError) as exc:
        raise CoercionError(f"{table}.{column}: not an integer {raw!r}") from exc
    if not (-32768 <= v <= 32767):
        raise CoercionError(f"{table}.{column}: {v} out of smallint range")
    return v


def coerce_int(raw: Any, *, table: str, column: str) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError) as exc:
        raise CoercionError(f"{table}.{column}: not an integer {raw!r}") from exc


def coerce_float(raw: Any, **_kw) -> Optional[float]:
    if raw is None:
        return None
    return float(raw)


def coerce_text(raw: Any, **_kw) -> Optional[str]:
    """NUL bytes are illegal in Postgres text of any format (carried forward from
    shared/load_fondos_to_postgres.py's `_clean_nul`, now applied row-streaming instead of over a
    whole DataFrame — plan §P3)."""
    if raw is None:
        return None
    return raw.replace("\x00", "") if isinstance(raw, str) else raw


def coerce_jsonb(raw: Any, *, table: str, column: str) -> Optional[str]:
    """Load-time validation, not blind cast: a malformed JSON value here would otherwise abort
    the whole COPY stream with no row identity in the error (plan §P2 "Type mapping" — 'load all
    three as text, then convert after a validation pass'). We validate eagerly per-row instead,
    since we're already streaming row-by-row."""
    if raw is None:
        return None
    try:
        json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CoercionError(f"{table}.{column}: malformed JSON {raw!r}") from exc
    return raw  # psycopg adapts a Python str to jsonb when the target column type is jsonb


COERCERS: dict[str, Callable[..., Any]] = {
    "text": coerce_text,
    "float": coerce_float,
    "int": coerce_int,
    "smallint": coerce_smallint,
    "date": coerce_date,
    "timestamptz": coerce_timestamptz,
    "jsonb": coerce_jsonb,
}

_SQLITE_TYPE_DEFAULT = {
    "TEXT": "text", "REAL": "float", "INTEGER": "int", "DATE": "date", "TIMESTAMP": "timestamptz",
}

# Overrides where the live SQLite declared type does NOT match the intended PG kind — derived
# directly from db/pg/{10,20,30,35}.sql while writing that DDL. Keyed (source_table, dst_column)
# using POST-RENAME column names (i.e. what rename_map.yaml produces), so this stays readable
# against the DDL files rather than the SQLite originals.
COERCION_OVERRIDES: dict[tuple[str, str], str] = {
    # fund_master: 0/1 flags and small bounded integers declared INTEGER in SQLite -> smallint
    ("fund_master", "heuristic_core"): "smallint",
    ("fund_master", "in_current_universe"): "smallint",
    ("fund_master", "srri"): "smallint",
    ("fund_master", "sfdr_article"): "smallint",
    ("fund_master", "recommended_holding_period"): "smallint",
    # fund_kiid_metadata
    ("fund_kiid_metadata", "kiid_class"): "smallint",
    ("fund_kiid_metadata", "srri"): "smallint",
    ("fund_kiid_metadata", "srri_visual"): "smallint",
    ("fund_kiid_metadata", "srri_textual"): "smallint",
    ("fund_kiid_metadata", "kiid_published_date"): "date",        # declared TEXT in SQLite
    ("fund_kiid_metadata", "kiid_downloaded_at"): "timestamptz",  # declared TEXT in SQLite
    # processing_breakdown: NO override — despite the name, this is NOT JSON. Confirmed 2026-09-18
    # against the full live non-null population (3,726/3,726 rows): pipe-delimited timing string,
    # e.g. 'kiid_fetch:0ms|kiid_parse:72ms|classify:2ms'. Falls through to the SQLite-declared-type
    # default (TEXT -> "text"), which is correct here. Caught by this script's own coercion
    # validation pass against real data — see db/pg/10_bronze.sql for the DDL-side correction.
    # fund_cost_schedule
    ("fund_cost_schedule", "is_rhp"): "smallint",
    ("fund_cost_schedule", "created_at"): "timestamptz",   # declared TEXT w/ datetime('now') default
    ("fund_cost_schedule", "updated_at"): "timestamptz",
    # fund_cost_corrections — old_value/new_value declared REAL in SQLite but hold text for the
    # rare non-numeric correction (Performance_Fee_Basis etc.) — confirmed 2026-09-19 against a
    # live run: 1/6,077 rows is text ('RATE_ON_OUTPERFORMANCE'), crashing coerce_float outright.
    # Column type changed to `text` in db/pg/20_silver.sql to match; override the default
    # REAL->"float" mapping here to "text" so both the numeric majority and the categorical
    # exception pass through unchanged rather than being forced through a numeric cast.
    ("fund_cost_corrections", "old_value"): "text",
    ("fund_cost_corrections", "new_value"): "text",
    ("fund_cost_corrections", "corrected_at"): "timestamptz",
    # fund_families
    ("fund_families", "updated_at"): "timestamptz",
    # fund_benchmarks
    ("fund_benchmarks", "extracted_at"): "timestamptz",
    # fund_data_quality_issues
    ("fund_data_quality_issues", "detected_at"): "timestamptz",
    # kiid_lifecycle
    ("kiid_lifecycle", "start_date"): "date",
    ("kiid_lifecycle", "end_date"): "date",
    # fund_metrics
    ("fund_metrics", "real_flag"): "smallint",
    # fund_metric_state — calculated_at declared TEXT in SQLite (like fund_metric_timeseries.date
    # above) but DATE in the target DDL. Missing entirely until caught by a systematic
    # loader-vs-live-column-type cross-check 2026-09-19 (not by a run crash this time — this one
    # was caught proactively, before it could fail mid-migration on this table, which comes late
    # in TABLE_SPECS order, after the two big tables). Verified: 0/3,683 live values fail
    # date.fromisoformat.
    ("fund_metric_state", "calculated_at"): "date",
    # fund_metric_timeseries — `date` is declared TEXT in the live SQLite DB (NOT DATE, despite
    # the column's semantics), and window (-> window_label) needs no override (stays text)
    ("fund_metric_timeseries", "date"): "date",
    ("fund_metric_timeseries", "real_flag"): "smallint",
    # fund_nav_daily / fund_nav_monthly
    ("fund_nav_daily", "is_estimated"): "smallint",
    ("fund_nav_monthly", "is_estimated"): "smallint",
    # fund_scores
    ("fund_scores", "eligible"): "smallint",
    ("fund_scores", "score_detail"): "jsonb",
    # ingestion_log — created_at declared TEXT, not TIMESTAMP
    ("ingestion_log", "created_at"): "timestamptz",
    # rotation_costs
    ("rotation_costs", "redemption_days"): "smallint",
    ("rotation_costs", "min_holding_days"): "smallint",
}

# Fully-qualified (schema, target_table) for every migrated unit, and the source SQLite table.
# `partition_col` marks fund_metric_timeseries's 5 per-partition passes (plan §P3 "Five
# per-partition passes"). `fk_isin_check` marks tables whose isin column is pre-scanned for
# orphans against fund_master before COPY (plan §P3 step 3 / §35_control.sql quarantine tables) —
# the same 9 tables control.quarantine_<table>_orphans was created for.
@dataclass(frozen=True)
class TableSpec:
    sqlite_table: str
    schema: str
    table: str
    fk_isin_check: bool = False
    partition_metrics: Optional[list[str]] = None  # set only for fund_metric_timeseries


TABLE_SPECS: list[TableSpec] = [
    # fund_families MUST load before fund_master — fund_master.fund_family_id carries an FK to
    # fund_families(family_id) (db/pg/20_silver.sql), same dependency-ordering constraint already
    # handled in the DDL itself (that FK is deferred to an ALTER TABLE for exactly this reason).
    # Confirmed against a live server 2026-09-19: loading fund_master first raises
    # ForeignKeyViolation on the first row carrying a non-null fund_family_id.
    TableSpec("fund_families", "silver", "fund_families"),
    TableSpec("fund_master", "silver", "fund_master"),
    TableSpec("fund_benchmarks", "silver", "fund_benchmarks"),
    TableSpec("fund_cost_schedule", "silver", "fund_cost_schedule"),
    TableSpec("fund_cost_corrections", "silver", "fund_cost_corrections"),
    TableSpec("fund_data_quality_issues", "silver", "fund_data_quality_issues"),
    TableSpec("kiid_lifecycle", "silver", "kiid_lifecycle"),
    TableSpec("fund_nav_daily", "bronze", "fund_nav_daily", fk_isin_check=True),
    TableSpec("fund_nav_monthly", "bronze", "fund_nav_monthly", fk_isin_check=True),
    TableSpec("series_macro", "bronze", "series_macro"),
    TableSpec("series_benchmark", "bronze", "series_benchmark"),
    TableSpec("series_inflation", "bronze", "series_inflation"),
    TableSpec("fund_kiid_metadata", "bronze", "fund_kiid_metadata"),
    TableSpec("db_document_catalogue", "bronze", "db_document_catalogue"),
    TableSpec("fund_metrics", "gold", "fund_metrics", fk_isin_check=True),
    TableSpec("fund_metric_timeseries", "gold", "fund_metric_timeseries",
              fk_isin_check=True, partition_metrics=FMTS_METRICS),
    TableSpec("fund_metric_alerts", "gold", "fund_metric_alerts", fk_isin_check=True),
    TableSpec("fund_scores", "gold", "fund_scores", fk_isin_check=True),
    TableSpec("portfolio_scenarios", "gold", "portfolio_scenarios"),
    TableSpec("portfolio_weights", "gold", "portfolio_weights", fk_isin_check=True),
    TableSpec("rotation_costs", "gold", "rotation_costs"),
    # Added 2026-09-20 — rename_map.yaml's "NEW, no SQLite equivalent exists or ever will" claim
    # was wrong: the table exists live (P3 Phase 3b), matching column set, PK (date,
    # classifier_version). Currently 0 rows so this closes the gap before it can lose anything, not
    # after. No isin column -> no fk_isin_check.
    TableSpec("regime_history", "gold", "regime_history"),
    TableSpec("fund_metric_state", "control", "fund_metric_state", fk_isin_check=True),
    TableSpec("p2_pipeline_log", "control", "p2_pipeline_log"),
    TableSpec("ingestion_log", "control", "ingestion_log"),
    TableSpec("nav_sources", "control", "nav_sources"),
    TableSpec("audit_statistic", "control", "audit_statistic"),
    TableSpec("audit_finding", "control", "audit_finding", fk_isin_check=True),
]


# =============================================================================
# Rename map / column planning
# =============================================================================

def load_rename_map() -> dict:
    with open(RENAME_MAP_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def sqlite_columns(sconn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    """Returns [(column_name, sqlite_declared_type), ...] in table-definition order."""
    rows = sconn.execute(f"PRAGMA table_info({table})").fetchall()
    return [(r[1], (r[2] or "TEXT").upper()) for r in rows]


@dataclass
class ColumnPlan:
    src: str
    dst: str
    kind: str


def build_column_plan(sconn: sqlite3.Connection, spec: TableSpec, rename_map: dict) -> list[ColumnPlan]:
    col_map = rename_map["columns"].get(spec.sqlite_table, {})
    plan = []
    for src_col, sqlite_type in sqlite_columns(sconn, spec.sqlite_table):
        dst_col = col_map.get(src_col, src_col)
        override = COERCION_OVERRIDES.get((spec.sqlite_table, dst_col))
        kind = override or _SQLITE_TYPE_DEFAULT.get(sqlite_type, "text")
        plan.append(ColumnPlan(src=src_col, dst=dst_col, kind=kind))
    return plan


# =============================================================================
# control.seed_progress bookkeeping (plan §P3 "Restart semantics" — resume at the failed unit,
# not the whole 55-90 minute run, on a crash mid-load).
# =============================================================================

def seed_unit_done(pconn: psycopg.Connection, unit: str) -> bool:
    row = pconn.execute(
        "SELECT status FROM control.seed_progress WHERE target_table = %s", (unit,)
    ).fetchone()
    return bool(row and row[0] == "done")


def seed_unit_start(pconn: psycopg.Connection, unit: str) -> None:
    pconn.execute(
        "INSERT INTO control.seed_progress (target_table, status) VALUES (%s, 'pending') "
        "ON CONFLICT (target_table) DO NOTHING",
        (unit,),
    )
    pconn.commit()


def seed_unit_mark_done(pconn: psycopg.Connection, unit: str) -> None:
    pconn.execute(
        "INSERT INTO control.seed_progress (target_table, status, completed_at) "
        "VALUES (%s, 'done', now()) "
        "ON CONFLICT (target_table) DO UPDATE SET status = 'done', completed_at = now()",
        (unit,),
    )
    pconn.commit()


# =============================================================================
# Orphan pre-scan (plan §P3 step 3 — BEFORE any COPY runs, not discovered at VALIDATE CONSTRAINT
# time after a 40-minute load).
# =============================================================================

def orphan_isins(sconn: sqlite3.Connection, sqlite_table: str, isin_col: str) -> set[str]:
    """ISINs present in `sqlite_table` with no matching row in fund_master. Cheap: fund_master is
    3,726 rows, held entirely in a Python set for the membership test."""
    master_isins = {r[0] for r in sconn.execute("SELECT ISIN FROM fund_master")}
    rows = sconn.execute(
        f"SELECT DISTINCT {isin_col} FROM {sqlite_table} WHERE {isin_col} IS NOT NULL"
    ).fetchall()
    return {r[0] for r in rows if r[0] not in master_isins}


def _has_incoming_fk(pconn: psycopg.Connection, schema: str, table: str) -> bool:
    """True if some OTHER table has a foreign key pointing AT (schema, table) — e.g.
    silver.fund_master, referenced by gold.fund_metrics/fund_scores/etc. PostgreSQL refuses a bare
    TRUNCATE on such a table ("cannot truncate a table referenced in a foreign key constraint"),
    EVEN when every table involved is empty — confirmed against a live server 2026-09-19, not
    something the dry-run or unit-level coercion tests could ever have caught, since neither
    exercises TRUNCATE against a real multi-table schema. Checked dynamically (not a hardcoded
    table list) so this stays correct if the DDL grows more FKs later."""
    row = pconn.execute(
        "SELECT 1 FROM pg_constraint c "
        "JOIN pg_class rel ON rel.oid = c.confrelid "
        "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
        "WHERE c.contype = 'f' AND ns.nspname = %s AND rel.relname = %s LIMIT 1",
        (schema, table),
    ).fetchone()
    return row is not None


def _clear_table(pconn: psycopg.Connection, target: sql.Composed, schema: str, table: str) -> None:
    """TRUNCATE where safe (fast, resets storage); DELETE where the table has incoming FK
    references (TRUNCATE would be refused outright — see _has_incoming_fk's docstring). DELETE
    against an empty table (the common case: a fresh seed run) costs nothing; the only tables this
    actually affects (fund_master, fund_families, portfolio_scenarios) are small (thousands of
    rows, not millions) even at full production scale, so DELETE's per-row cost vs. TRUNCATE is
    negligible here — this is not applied to the two big tables (fund_metric_timeseries,
    fund_nav_daily), which have no incoming FKs and keep the fast TRUNCATE path."""
    if _has_incoming_fk(pconn, schema, table):
        pconn.execute(sql.SQL("DELETE FROM {}").format(target))
    else:
        pconn.execute(sql.SQL("TRUNCATE {}").format(target))


def _isin_column_for(sqlite_table: str) -> str:
    # Matches the live schema exactly — some tables spell it ISIN, others isin (see rename_map.yaml).
    upper_isin_tables = {
        "fund_nav_daily", "fund_nav_monthly", "fund_benchmarks", "fund_cost_schedule",
        "fund_cost_corrections", "fund_data_quality_issues", "fund_kiid_metadata", "fund_master",
        "ingestion_log",
    }
    return "ISIN" if sqlite_table in upper_isin_tables else "isin"


# =============================================================================
# COPY execution
# =============================================================================
# (The actual per-row COPY logic lives inline in load_unit() below, since it has to interleave
# each row between the main-table copy stream and the quarantine copy stream depending on whether
# that row's ISIN is a pre-scanned orphan — a single reusable "_copy_rows" helper would need the
# same branching passed in anyway, so it was cut in favor of the one inline implementation.)


def load_unit(
    sconn: sqlite3.Connection,
    pconn: psycopg.Connection,
    spec: TableSpec,
    columns: list[ColumnPlan],
    *,
    metric_filter: Optional[str] = None,
    target_table_override: Optional[str] = None,
) -> None:
    target_table = target_table_override or spec.table
    unit_name = f"{spec.schema}.{target_table}"
    if seed_unit_done(pconn, unit_name):
        print(f"  [skip] {unit_name} already done")
        return
    seed_unit_start(pconn, unit_name)

    t0 = time.monotonic()
    src_cols_sql = ", ".join(c.src for c in columns)
    where = " WHERE metric = ?" if metric_filter else ""
    query = f"SELECT {src_cols_sql} FROM {spec.sqlite_table}{where}"
    params = (metric_filter,) if metric_filter else ()

    orphans: set[str] = set()
    isin_col = None
    if spec.fk_isin_check:
        isin_col = _isin_column_for(spec.sqlite_table)
        orphans = orphan_isins(sconn, spec.sqlite_table, isin_col)
        if orphans:
            print(f"  [orphans] {unit_name}: {len(orphans)} ISIN(s) not in fund_master -> quarantine")

    target = sql.SQL("{}.{}").format(sql.Identifier(spec.schema), sql.Identifier(target_table))
    quarantine_target = (
        sql.SQL("control.{}").format(sql.Identifier(f"quarantine_{spec.table}_orphans"))
        if spec.fk_isin_check else None
    )

    with pconn.transaction():
        _clear_table(pconn, target, spec.schema, target_table)
        cur = sconn.execute(query, params)
        col_idents = sql.SQL(", ").join(sql.Identifier(c.dst) for c in columns)
        main_copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(target, col_idents)
        n_main = n_quarantine = 0
        idx_of_isin = next((i for i, c in enumerate(columns) if c.src == isin_col), None) if isin_col else None

        # Orphan rows are buffered in memory, NOT written via a second simultaneously-open COPY
        # stream — a single psycopg3/PostgreSQL connection can only have one COPY operation in
        # flight at a time. Two overlapping `.copy()` contexts on the same connection (the original
        # design here) doesn't error cleanly; it silently deadlocks — confirmed against a live
        # server 2026-09-19: `pg_stat_progress_copy` showed 0 bytes/tuples processed for 14+
        # minutes on `bronze.fund_nav_daily` (the first table in TABLE_SPECS where fk_isin_check
        # actually triggers this path), while a single-stream 1M-row COPY over the identical
        # connection completed cleanly in 27s — isolating the bug to the dual-stream structure,
        # not the network path or data volume. Orphan counts are small by design ("cheap
        # insurance" — see the plan) so buffering them is safe; this is not appropriate for a
        # table where a large fraction of rows are expected to be orphaned.
        quarantine_rows: list[list] = []

        with pconn.cursor().copy(main_copy_sql) as main_cp:
            while batch := cur.fetchmany(FETCH_CHUNK):
                for row in batch:
                    is_orphan = idx_of_isin is not None and row[idx_of_isin] in orphans
                    values = [
                        COERCERS[c.kind](row[i], table=spec.sqlite_table, column=c.dst)
                        for i, c in enumerate(columns)
                    ]
                    if is_orphan and quarantine_target is not None:
                        quarantine_rows.append(values)
                        n_quarantine += 1
                    else:
                        main_cp.write_row(values)
                        n_main += 1

        if quarantine_rows:
            q_copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(quarantine_target, col_idents)
            with pconn.cursor().copy(q_copy_sql) as q_cp:
                for values in quarantine_rows:
                    q_cp.write_row(values)

    seed_unit_mark_done(pconn, unit_name)
    elapsed = time.monotonic() - t0
    print(f"  [done] {unit_name}: {n_main} rows"
          + (f" (+{n_quarantine} quarantined)" if n_quarantine else "")
          + f" in {elapsed:.1f}s")


def load_fmts_partitions(sconn: sqlite3.Connection, pconn: psycopg.Connection, columns: list[ColumnPlan]) -> None:
    """Five per-partition passes, WHERE metric = ? -> gold.fmts_p_<metric> directly — skips tuple
    routing and makes each partition independently restartable (plan §P3)."""
    for metric in FMTS_METRICS:
        spec = TableSpec("fund_metric_timeseries", "gold", "fund_metric_timeseries", fk_isin_check=True)
        load_unit(sconn, pconn, spec, columns, metric_filter=metric,
                  target_table_override=f"fmts_p_{metric}")


# =============================================================================
# gold.fund_scores — special-cased loader, NOT the generic column-plan path.
#
# P3 Phase 3a (proyecto3 plan `i-am-sharing-an-piped-reddy.md` §"Phase 3 — Persistence and
# keys", coordinated via memory/project_p3_scoring_regime_optimization_20260916.md, landed in
# db/pg/30_gold.sql 2026-09-19). Target columns `regime`, `as_of_date`, `exclusion_reason` have
# NO 1:1 SQLite source column — they're DERIVED from `notes` (regex parse) and `calculated_at`
# (copy), which the generic `ColumnPlan`/`build_column_plan()` machinery (one dst_col per exactly
# one src_col) cannot express. Rather than bend that machinery to fit one table, fund_scores gets
# its own loader — same seed_progress/orphan-quarantine/COPY conventions as `load_unit()`, applied
# to a hand-built row transform instead of a generic column plan.
# =============================================================================

# Confirmed against the FULL live non-null population (5,884/5,884 rows, 2026-09-19), not a
# sample: every row matches one of exactly two shapes —
#   'regime=Shock_Energetico'
#   'regime=Shock_Energetico | excluido: max_drawdown=-0.36 < -0.2 (Defensiva)'
_FUND_SCORES_NOTES_RE = re.compile(
    r"^regime=(?P<regime>[^\s|]+)(?:\s*\|\s*excluido:\s*(?P<exclusion_reason>.+))?$"
)


def load_fund_scores_unit(sconn: sqlite3.Connection, pconn: psycopg.Connection) -> None:
    unit_name = "gold.fund_scores"
    if seed_unit_done(pconn, unit_name):
        print(f"  [skip] {unit_name} already done")
        return
    seed_unit_start(pconn, unit_name)

    t0 = time.monotonic()
    isin_col = _isin_column_for("fund_scores")  # "isin" — already lower_snake in SQLite
    orphans = orphan_isins(sconn, "fund_scores", isin_col)
    if orphans:
        print(f"  [orphans] {unit_name}: {len(orphans)} ISIN(s) not in fund_master -> quarantine")

    dst_cols = ["isin", "block", "score_version", "score_total", "score_base", "multiplier",
                "eligible", "calculated_at", "as_of_date", "regime", "score_detail",
                "exclusion_reason", "notes"]
    target = sql.Identifier("gold", "fund_scores")
    quarantine_target = sql.Identifier("control", "quarantine_fund_scores_orphans")
    col_idents = sql.SQL(", ").join(sql.Identifier(c) for c in dst_cols)
    main_copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(target, col_idents)

    with pconn.transaction():
        # Nothing currently references gold.fund_scores via FK, so plain TRUNCATE is safe today —
        # routed through _clear_table() anyway (not a bare TRUNCATE) so this stays correct
        # automatically if that ever changes, rather than needing this call site remembered too.
        _clear_table(pconn, target, "gold", "fund_scores")
        # score_base / multiplier: SQLite gained real columns for these in v27 (P3 Phase 3a, the
        # same day this loader was written) and P3 now populates them, so a hard NULL here
        # silently DISCARDED populated data — found 2026-09-23 by an A/B of the same P3 run on
        # SQLite vs Postgres: 4,475 seeded rows had them NULL in Postgres and populated in
        # SQLite (recoverable from score_detail, but the denormalised columns disagreed, and
        # reconcile check (j) excluded them so the gate could not see it). Copy them when the
        # source has the columns; an older (pre-v27) SQLite file keeps the NULL backfill.
        src_cols = {r[1].lower() for r in sconn.execute("PRAGMA table_info(fund_scores)")}
        sel_base = "score_base" if "score_base" in src_cols else "NULL"
        sel_mult = "multiplier" if "multiplier" in src_cols else "NULL"
        cur = sconn.execute(
            f"SELECT isin, block, score_version, score_total, {sel_base}, {sel_mult}, eligible, "
            f"calculated_at, score_detail, notes FROM fund_scores"
        )
        n_main = n_quarantine = 0
        # Orphan rows buffered, not written via a second simultaneous COPY stream — same fix, same
        # reason, as load_unit()'s identical bug: a single connection can't have two overlapping
        # COPY operations in flight (silently deadlocks, doesn't error). See load_unit()'s comment.
        quarantine_rows: list[list] = []
        with pconn.cursor().copy(main_copy_sql) as main_cp:
            while batch := cur.fetchmany(FETCH_CHUNK):
                for isin, block, score_version, score_total, score_base, multiplier, eligible, \
                        calculated_at, score_detail, notes in batch:
                    m = _FUND_SCORES_NOTES_RE.match(notes or "")
                    if m is None:
                        raise CoercionError(
                            f"fund_scores: notes {notes!r} for isin={isin} block={block} "
                            f"doesn't match the expected 'regime=X' / 'regime=X | excluido: ...' "
                            f"shape — cannot derive `regime` (a NOT NULL PK column in the target "
                            f"schema). Fix the parser or the source data before re-running; do "
                            f"not silently default regime to a sentinel."
                        )
                    regime = m.group("regime")
                    exclusion_reason = m.group("exclusion_reason")  # None if not excluded
                    as_of_date_val = coerce_date(calculated_at, table="fund_scores", column="as_of_date")

                    values = [
                        coerce_text(isin, table="fund_scores", column="isin"),
                        coerce_text(block, table="fund_scores", column="block"),
                        coerce_text(score_version, table="fund_scores", column="score_version"),
                        coerce_float(score_total),
                        coerce_float(score_base),   # NULL only if the SQLite source lacks the column
                        coerce_float(multiplier),   # (pre-v27) or the row itself predates Phase 3a
                        coerce_smallint(eligible, table="fund_scores", column="eligible"),
                        coerce_date(calculated_at, table="fund_scores", column="calculated_at"),
                        as_of_date_val,
                        coerce_text(regime, table="fund_scores", column="regime"),
                        coerce_jsonb(score_detail, table="fund_scores", column="score_detail"),
                        coerce_text(exclusion_reason, table="fund_scores", column="exclusion_reason"),
                        coerce_text(notes, table="fund_scores", column="notes"),
                    ]
                    if isin in orphans:
                        quarantine_rows.append(values)
                        n_quarantine += 1
                    else:
                        main_cp.write_row(values)
                        n_main += 1

        if quarantine_rows:
            q_copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(quarantine_target, col_idents)
            with pconn.cursor().copy(q_copy_sql) as q_cp:
                for values in quarantine_rows:
                    q_cp.write_row(values)

    seed_unit_mark_done(pconn, unit_name)
    elapsed = time.monotonic() - t0
    print(f"  [done] {unit_name}: {n_main} rows"
          + (f" (+{n_quarantine} quarantined)" if n_quarantine else "")
          + f" in {elapsed:.1f}s")


def _assert_partition_metrics_match(sconn: sqlite3.Connection) -> None:
    """Runtime guard mirroring the CI test in §5a (test_partition_covers_every_rolling_metric) —
    if a metric was added to fund_metric_timeseries that isn't in FMTS_METRICS, it would silently
    land in fmts_p_default via the generic load_unit path with no metric_filter. Since this
    script routes fmts explicitly via load_fmts_partitions (only the 5 named metrics), any metric
    present in SQLite outside that set would simply never be loaded at all — fail loudly instead."""
    present = {r[0] for r in sconn.execute("SELECT DISTINCT metric FROM fund_metric_timeseries")}
    unexpected = present - set(FMTS_METRICS)
    if unexpected:
        raise RuntimeError(
            f"fund_metric_timeseries has metric(s) not in FMTS_METRICS: {unexpected}. "
            f"Add the corresponding partition to db/pg/30_gold.sql and this script's "
            f"FMTS_METRICS before seeding, or these rows will be silently skipped."
        )


# =============================================================================
# bronze.fund_nav_daily — the one table where index-stripping around COPY is actually applied
# (plain, non-partitioned; safe and cheap to drop/rebuild — see module docstring).
# =============================================================================

def strip_nav_daily_indexes(pconn: psycopg.Connection) -> None:
    with pconn.transaction():
        pconn.execute("ALTER TABLE bronze.fund_nav_daily DROP CONSTRAINT IF EXISTS fund_nav_daily_pkey")
        pconn.execute("DROP INDEX IF EXISTS bronze.idx_nav_daily_date")
        pconn.execute("DROP INDEX IF EXISTS bronze.idx_nav_daily_ingested_at")
        pconn.execute("ALTER TABLE bronze.fund_nav_daily SET (autovacuum_enabled = false)")


def rebuild_nav_daily_indexes(pconn: psycopg.Connection) -> None:
    with pconn.transaction():
        pconn.execute(
            "ALTER TABLE bronze.fund_nav_daily "
            "ADD CONSTRAINT fund_nav_daily_pkey PRIMARY KEY (isin, date)"
        )
    pconn.execute("ANALYZE bronze.fund_nav_daily")
    corr = pconn.execute(
        "SELECT correlation FROM pg_stats WHERE schemaname='bronze' "
        "AND tablename='fund_nav_daily' AND attname='isin'"
    ).fetchone()
    if corr is None or corr[0] is None or corr[0] < 0.9:
        print("  [cluster] bronze.fund_nav_daily correlation < 0.9 — clustering")
        pconn.execute("CLUSTER bronze.fund_nav_daily USING fund_nav_daily_pkey")
        pconn.execute("ANALYZE bronze.fund_nav_daily")
    with pconn.transaction():
        pconn.execute("CREATE INDEX idx_nav_daily_date ON bronze.fund_nav_daily (date)")
        pconn.execute(
            "CREATE INDEX idx_nav_daily_ingested_at ON bronze.fund_nav_daily "
            "USING brin (ingested_at) WITH (pages_per_range = 128)"
        )
        pconn.execute(
            "ALTER TABLE bronze.fund_nav_daily SET (autovacuum_enabled = true)"
        )


# =============================================================================
# Post-load: sequences, FK on fund_metric_timeseries, VACUUM FREEZE
# =============================================================================

def reset_identity_sequences(pconn: psycopg.Connection) -> None:
    """setval() every identity sequence — skipping this is the #1 post-migration outage (plan
    §P3 step 11): the first post-cutover insert would raise a duplicate-key error otherwise."""
    targets = [
        ("control.ingestion_log", "id"),
        ("control.p2_pipeline_log", "id"),
        ("silver.fund_data_quality_issues", "id"),
        ("control.audit_finding", "id"),
        ("control.migration_state", "id"),
    ]
    for table, col in targets:
        pconn.execute(
            sql.SQL(
                "SELECT setval(pg_get_serial_sequence({}, {}), "
                "coalesce((SELECT max({}) FROM {}), 1))"
            ).format(sql.Literal(table), sql.Literal(col), sql.Identifier(col), sql.SQL(table))
        )
    pconn.commit()
    print("  [sequences] reset")


def add_fmts_fk(pconn: psycopg.Connection) -> None:
    """Step 10 of the plan's execution order: add the FK VALID directly (not NOT VALID), because
    the orphan pre-scan/quarantine split already guaranteed there are no orphans left in the
    table by the time this runs."""
    pconn.execute(
        "ALTER TABLE gold.fund_metric_timeseries "
        "ADD CONSTRAINT fmts_isin_fk FOREIGN KEY (isin) REFERENCES silver.fund_master (isin) "
        "ON DELETE CASCADE"
    )
    pconn.commit()
    print("  [fk] gold.fund_metric_timeseries.isin -> silver.fund_master added VALID")


def vacuum_freeze_all(pconn: psycopg.Connection) -> None:
    """FREEZE is not optional (plan §P3 step 13): freezing now prevents an anti-wraparound
    autovacuum storm 200M transactions from now, on a box with no maintenance window. Must run
    outside a transaction block (VACUUM cannot run inside one) — psycopg autocommit."""
    pconn.autocommit = True
    try:
        pconn.execute("VACUUM (ANALYZE, FREEZE)")
    finally:
        pconn.autocommit = False
    print("  [vacuum] VACUUM (ANALYZE, FREEZE) complete")


# =============================================================================
# Orchestration
# =============================================================================

# =============================================================================
# Preflight: refuse a target this loader was not designed for.
#
# pg_seed is a ONE-SHOT loader for an EMPTY database (plan §P3). Found 2026-09-23: pointed at an
# already-seeded database it dies half-way with a ForeignKeyViolation clearing silver.fund_families
# (fund_master_family_fk was added after the original load), having already cleared other tables.
# Worse, at the other extreme, nothing stopped it from TRUNCATE-ing a database that has since become
# the live primary — the plan flagged exactly that ("needs a guard before Postgres is trusted as
# primary"). So it fails FAST and CLEARLY instead of auto-purging: an auto-wipe fix would make the
# seeder able to destroy live data, which is the worse failure of the two.
# =============================================================================

def _target_units() -> list[tuple[str, str, str]]:
    """(seed_progress unit name, schema, table) for every table this loader writes."""
    units: list[tuple[str, str, str]] = []
    for spec in TABLE_SPECS:
        if spec.partition_metrics:
            units += [(f"gold.fmts_p_{m}", "gold", f"fmts_p_{m}") for m in spec.partition_metrics]
        else:
            units.append((f"{spec.schema}.{spec.table}", spec.schema, spec.table))
    return units


def gate_already_passed(pconn: psycopg.Connection) -> bool:
    """A control.migration_state row is written only when pg_reconcile's full gate went green: the
    database has been certified and may be the live primary."""
    return bool(pconn.execute("SELECT EXISTS (SELECT 1 FROM control.migration_state)").fetchone()[0])


def populated_but_not_recorded(pconn: psycopg.Connection, units: list[tuple[str, str, str]]) -> list[str]:
    """Units whose target table holds rows that control.seed_progress does NOT record as loaded —
    i.e. data this loader did not put there (or a progress table that was reset)."""
    found = []
    for unit, schema, table in units:
        if seed_unit_done(pconn, unit):
            continue
        has_rows = pconn.execute(
            sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(sql.Identifier(schema, table))
        ).fetchone()[0]
        if has_rows:
            found.append(unit)
    return found


def preflight(pconn: psycopg.Connection, *, force_reseed: bool, only: Optional[str]) -> Optional[str]:
    """Error message if it is unsafe to proceed, else None."""
    if gate_already_passed(pconn) and not force_reseed:
        return ("control.migration_state has a row: this database already passed the reconciliation "
                "gate and may be the live primary. Re-seeding TRUNCATEs tables and would overwrite "
                "live data, so it is refused. Pass --force-reseed only for a database you know is "
                "disposable.")
    if not only:
        bad = populated_but_not_recorded(pconn, _target_units())
        if bad:
            shown = ", ".join(bad[:5]) + (f" (+{len(bad) - 5} more)" if len(bad) > 5 else "")
            return (f"these tables already contain rows that control.seed_progress does not record as "
                    f"loaded: {shown}. pg_seed is a one-shot loader for an EMPTY database — on a "
                    f"populated one it fails mid-load with a ForeignKeyViolation. Recreate the "
                    f"database from db/pg/00_roles_schemas.sql .. 40_matviews.sql and seed that. (To "
                    f"resume a crashed load, re-run without touching control.seed_progress.)")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the unit plan, touch nothing")
    parser.add_argument("--only", help="load a single unit, e.g. bronze.fund_nav_daily (debug)")
    parser.add_argument("--force-reseed", action="store_true",
                        help="bypass the refusal to seed a database that already passed the "
                             "reconciliation gate (only for a database you know is disposable)")
    args = parser.parse_args()

    sqlite_path = Path(os.environ.get("FONDOS_SQLITE_PATH", str(DEFAULT_SQLITE_PATH)))
    pg_dsn = os.environ.get("FONDOS_PG_DSN")
    if not pg_dsn and not args.dry_run:
        print("FONDOS_PG_DSN is required (e.g. postgresql://fondos_owner@localhost:5432/fondos). "
              "Password via PGPASSWORD env var or .pgpass — never on the command line.",
              file=sys.stderr)
        return 2

    rename_map = load_rename_map()
    sconn = sqlite3.connect(f"file:{sqlite_path}?mode=ro&immutable=1", uri=True)
    sconn.execute("PRAGMA cache_size = -524288")  # 512MB page cache for the read side

    _assert_partition_metrics_match(sconn)

    if args.dry_run:
        print(f"Would seed {len(TABLE_SPECS)} tables from {sqlite_path} "
              f"(fund_metric_timeseries expands to {len(FMTS_METRICS)} partition passes):")
        for spec in TABLE_SPECS:
            label = f"{spec.schema}.{spec.table}"
            if spec.partition_metrics:
                label += f"  [{len(spec.partition_metrics)} partitions: {', '.join(spec.partition_metrics)}]"
            if spec.fk_isin_check:
                label += "  [orphan pre-scan]"
            print(f"  - {label}")
        return 0

    with psycopg.connect(pg_dsn) as pconn:
        problem = preflight(pconn, force_reseed=args.force_reseed, only=args.only)
        if problem:
            print(f"ERROR: {problem}", file=sys.stderr)
            return 3
        for spec in TABLE_SPECS:
            unit_label = f"{spec.schema}.{spec.table}"
            if args.only and args.only != unit_label:
                continue

            if spec.partition_metrics:
                columns = build_column_plan(sconn, spec, rename_map)
                print(f"[unit] {unit_label} (partitioned, {len(spec.partition_metrics)} passes)")
                load_fmts_partitions(sconn, pconn, columns)
                continue

            print(f"[unit] {unit_label}")
            if spec.table == "fund_nav_daily":
                columns = build_column_plan(sconn, spec, rename_map)
                strip_nav_daily_indexes(pconn)
                load_unit(sconn, pconn, spec, columns)
                rebuild_nav_daily_indexes(pconn)
            elif spec.table == "fund_scores":
                # Special-cased: regime/as_of_date/exclusion_reason are derived from `notes`, not
                # a 1:1 column mapping — see load_fund_scores_unit()'s own docstring/header comment.
                load_fund_scores_unit(sconn, pconn)
            else:
                columns = build_column_plan(sconn, spec, rename_map)
                load_unit(sconn, pconn, spec, columns)

        if not args.only:
            print("[post-load] resetting identity sequences")
            reset_identity_sequences(pconn)
            print("[post-load] adding gold.fund_metric_timeseries FK (orphan-free by construction)")
            add_fmts_fk(pconn)
            print("[post-load] VACUUM (ANALYZE, FREEZE)")
            vacuum_freeze_all(pconn)

    print("\nSeed load complete. Next steps:")
    print("  1. Restart Postgres with production config (wal_level=replica) — see docker/postgresql.conf")
    print("  2. Run scripts/mig/pg_reconcile.py against both the source SQLite file and this database")
    print("  3. On green: filesystem snapshot, then chmod 444 the source SQLite file (90-day retention)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
