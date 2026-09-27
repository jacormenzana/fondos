# proyecto2/src/writers/metrics_writer.py
# -*- coding: utf-8 -*-
"""
Escritores de fund_metrics / fund_metric_timeseries (P0, 2026-09-15).

Extraidos de run_pipeline.py para que sean testables bajo R-7 (sin importar
pipeline.py) -- antes vivian como funciones de modulo dentro de
run_pipeline.py leyendo CALC_VERSION/RUN_BATCH_ID como globals implicitos;
aqui se reciben como parametros explicitos (algorithm_version, batch_id).

Reemplaza la version legacy de este fichero (write_metrics(engine, ...) via
to_sql, no llamada por run_pipeline.py, no audit-column-aware) -- ver
historial git para el contenido anterior.
"""

import sys
from datetime import date
from pathlib import Path

try:
    from shared.db import in_transaction, begin_immediate
except ModuleNotFoundError:
    _shared_root = Path(__file__).resolve().parents[3]
    if str(_shared_root) not in sys.path:
        sys.path.insert(0, str(_shared_root))
    from shared.db import in_transaction, begin_immediate


def _executemany(conn, sql: str, data: list) -> "psycopg.Cursor":
    """executemany that returns the cursor on both dialects (psycopg3's Connection has no
    executemany of its own -- see shared/db.py's own executemany() docstring for the same gap;
    this local variant returns the cursor because write_timeseries needs cur.rowcount)."""
    cur = conn.cursor()
    cur.executemany(sql, data)
    return cur


def rows_from_metric_tuples(
    metric_tuples: list[tuple],
    source_rows: int,
) -> list[dict]:
    """Convierte la salida estandar list[(metric, value, real_flag)] de los
    modulos de src.calculations al formato dict que esperan write_metrics /
    replace_beta_set."""
    return [
        {"metric": m, "value": v, "real_flag": rf, "source_rows": source_rows}
        for m, v, rf in metric_tuples
    ]


# One statement per dialect, shared by write_metrics and write_metrics_batch (module constants so the
# EXPLAIN sweep in tests/test_sql_explain_sweep_pg.py still resolves and verifies the Postgres one).
_METRICS_UPSERT_PG = """
            INSERT INTO fund_metrics
                (isin, metric, horizon, value, real_flag,
                 calculation_date, metric_version, benchmark_id, source_rows,
                 algorithm_version, batch_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s)
            ON CONFLICT (isin, metric, horizon, real_flag, metric_version) DO UPDATE SET
                value = excluded.value, calculation_date = excluded.calculation_date,
                benchmark_id = excluded.benchmark_id, source_rows = excluded.source_rows,
                algorithm_version = excluded.algorithm_version, batch_id = excluded.batch_id,
                load_ts = DEFAULT
        """


def _metric_row_tuples(isin, metrics, horizon, today, metric_version, algorithm_version, batch_id) -> list:
    return [
        (
            isin,
            m["metric"],
            horizon,
            m["value"] if not (isinstance(m["value"], float) and
                                m["value"] != m["value"]) else None,  # NaN -> NULL
            m["real_flag"],
            today,
            metric_version,
            m.get("source_rows"),
            algorithm_version,
            batch_id,
        )
        for m in metrics
    ]


def write_metrics_batch(
    conn: "psycopg.Connection",
    items: list,
    dry_run: bool,
    *,
    algorithm_version: str,
    batch_id: str,
    metric_version: str,
    chunk: int = 5000,
) -> int:
    """Writes many (isin, horizon, metrics) groups to fund_metrics in ONE transaction (FND-0092).

    `items` = [(isin, horizon, [metric dicts]), ...]. Same rows/SQL as write_metrics (shared
    helpers), but a single BEGIN/COMMIT and chunked executemany instead of one transaction per
    (isin, horizon): the whole-universe rolling category write was ~17k commits ≈ 15 min on
    Postgres. If the caller already holds a transaction the function appends to it (EFF-2) and
    leaves the commit to the caller; on error the transaction it opened is rolled back whole.
    """
    if not items or dry_run:
        return 0
    today = date.today().isoformat()
    sql = _METRICS_UPSERT_PG
    rows: list = []
    for isin, horizon, metrics in items:
        rows.extend(_metric_row_tuples(isin, metrics, horizon, today, metric_version,
                                       algorithm_version, batch_id))
    if not rows:
        return 0
    own_txn = not in_transaction(conn)
    if own_txn:
        begin_immediate(conn)
    try:
        for i in range(0, len(rows), chunk):
            _executemany(conn, sql, rows[i:i + chunk])
        if own_txn:
            conn.execute("COMMIT")
    except Exception:
        if own_txn:
            conn.execute("ROLLBACK")
        raise
    return len(rows)


def write_metrics(
    conn: "psycopg.Connection",
    isin: str,
    metrics: list[dict],
    horizon: str,
    dry_run: bool,
    *,
    algorithm_version: str,
    batch_id: str,
    metric_version: str,
) -> int:
    """Persiste lista de metricas en fund_metrics. Devuelve n. escritas.

    metric_version: version de metrica (ej. shared.config.METRIC_VERSION
    'v1', o METRIC_VERSION_SHORT 'd1' para series cortas) -- mantiene las
    series cortas separadas de las mensuales en la clave compuesta.

    Txn batching (EFF-2): if the caller already opened a transaction
    (conn.in_transaction=True), this function skips its own BEGIN/COMMIT and
    executes the DML directly inside the caller's transaction. Otherwise it
    manages its own retried transaction as before.
    """
    if not metrics or dry_run:
        return 0

    today = date.today().isoformat()
    sql = _METRICS_UPSERT_PG
    rows = _metric_row_tuples(isin, metrics, horizon, today, metric_version, algorithm_version, batch_id)
    # EFF-2: skip own transaction when the caller batches for us
    if in_transaction(conn):
        _executemany(conn, sql, rows)
        return len(rows)
    begin_immediate(conn)
    try:
        _executemany(conn, sql, rows)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(rows)


# v27 pivot (2026-09-27): the resulting row is built from the SAME expressions in SET and WHERE
# (row-wise IS DISTINCT FROM against the identical CASE/OR expressions) -- "write only if the
# resulting row differs from the stored one" -- so the two can't drift apart the way two
# independently-written predicates could (a real risk flagged in review of the pivot plan; see
# harmonic-marinating-balloon.md "Review round 3"). ref_type/ref_value DROPPED (v27): always NULL
# on every live row pre-pivot (confirmed against production), never written here again.
_TIMESERIES_UPSERT_PG = """
    INSERT INTO fund_metric_timeseries
        (isin, metric, window_label, date, value_nominal, value_real, has_real, source_rows,
         algorithm_version, batch_id)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (isin, metric, window_label, date) DO UPDATE SET
        value_nominal     = excluded.value_nominal,
        value_real        = CASE WHEN excluded.has_real THEN excluded.value_real
                                  ELSE fund_metric_timeseries.value_real END,
        has_real          = fund_metric_timeseries.has_real OR excluded.has_real,
        source_rows       = excluded.source_rows,
        algorithm_version = excluded.algorithm_version,
        batch_id          = excluded.batch_id
    WHERE (fund_metric_timeseries.value_nominal, fund_metric_timeseries.value_real,
           fund_metric_timeseries.has_real, fund_metric_timeseries.algorithm_version)
          IS DISTINCT FROM
          (excluded.value_nominal,
           CASE WHEN excluded.has_real THEN excluded.value_real ELSE fund_metric_timeseries.value_real END,
           fund_metric_timeseries.has_real OR excluded.has_real,
           excluded.algorithm_version)
"""


def _group_timeseries_pairs(rows: list[dict]) -> dict[tuple, dict]:
    """Groups the long-format rows (one dict per real_flag, as produced by
    rolling_stats.compute_rolling_rows) by (isin, metric, window, date) into the pivoted shape
    write_timeseries needs. A real_flag=1 row with no matching real_flag=0 row for the same key is
    structurally impossible (compute_rolling_rows always emits the nominal variant first) and
    raises -- the caller (run_pipeline.py) confines that failure to the one fund being processed."""
    grouped: dict[tuple, dict] = {}
    for r in rows:
        key = (r["isin"], r["metric"], r["window"], r["date"])
        slot = grouped.setdefault(key, {})
        if r["real_flag"] == 0:
            slot["value_nominal"] = r["value"]
            slot["source_rows"] = r.get("source_rows")
        elif r["real_flag"] == 1:
            slot["has_real"] = True
            slot["value_real"] = r["value"]
        else:
            raise ValueError(f"write_timeseries: unexpected real_flag={r['real_flag']!r} for key={key}")
    for key, slot in grouped.items():
        if "value_nominal" not in slot:
            raise ValueError(f"write_timeseries: real_flag=1 row with no real_flag=0 row for key={key}")
    return grouped


def write_timeseries(
    conn: "psycopg.Connection",
    rows: list[dict],
    dry_run: bool,
    *,
    algorithm_version: str,
    batch_id: str,
) -> int:
    """Escribe filas en fund_metric_timeseries con upsert (P0, 2026-09-15; pivoted v27, 2026-09-27).

    Root cause fix: antes usaba INSERT OR IGNORE, lo que hacia que un
    --force silenciosamente no-opeara sobre filas ya existentes -- un
    cambio de formula (ej. la canonicalizacion de sortino/sharpe, 2b45416)
    nunca se propagaba a esta tabla aunque el recalculo se ejecutara, y
    algorithm_version/batch_id quedaban en first-write-wins. El UPDATE
    condicional mantiene la operacion idempotente a nivel de pagina: una
    re-ejecucion con valores identicos no escribe paginas nuevas, por lo
    que las ejecuciones incrementales normales no ven crecimiento de WAL.

    v27: la tabla ya no tiene real_flag en su clave (pivotado a value_nominal/value_real/has_real,
    ver db/pg/30_gold.sql) -- `rows` sigue llegando en formato largo (un dict por real_flag); esta
    funcion los agrupa por (isin, metric, window, date) y escribe una sola fila por clave. Un
    write nominal-only (sin IPC disponible) nunca toca value_real/has_real de una fila existente.

    Devuelve el n. de filas insertadas O actualizadas (0 si no habia
    cambios reales o dry_run). NOTA: antes de este fix el valor devuelto
    contaba solo inserciones; ahora tambien cuenta actualizaciones -- ver
    el log [TIMESERIES] en el llamador.

    EFF-2: skips own BEGIN/COMMIT when caller already has an open transaction.
    """
    if not rows or dry_run:
        return 0
    grouped = _group_timeseries_pairs(rows)
    data = [
        (
            isin, metric, window, dt,
            slot["value_nominal"],
            slot.get("value_real"),
            slot.get("has_real", False),
            slot.get("source_rows"),
            algorithm_version,
            batch_id,
        )
        for (isin, metric, window, dt), slot in grouped.items()
    ]
    # EFF-2: skip own transaction when the caller batches for us
    if in_transaction(conn):
        cur = _executemany(conn, _TIMESERIES_UPSERT_PG, data)
        return cur.rowcount if cur.rowcount >= 0 else len(data)
    begin_immediate(conn)
    try:
        cur = _executemany(conn, _TIMESERIES_UPSERT_PG, data)
        conn.execute("COMMIT")
        return cur.rowcount if cur.rowcount >= 0 else len(data)
    except Exception:
        conn.execute("ROLLBACK")
        raise


def replace_beta_set(
    conn: "psycopg.Connection",
    isin: str,
    metrics: list[dict],
    horizon: str,
    dry_run: bool,
    *,
    algorithm_version: str,
    batch_id: str,
    metric_version: str,
) -> int:
    """Atomic delete+insert for OLS macro-sensitivity metrics.

    When OLS actually runs for a fund, this replaces the *full* beta set in a
    single transaction: first deletes all existing beta_* rows, then inserts
    the new survivors. This prevents orphan rows for factors that were
    present in a previous run but are now excluded by the VIF filter.

    Use this instead of write_metrics for macro-sensitivity metrics.
    write_metrics is still used for all other metric families.

    EFF-2: skips own BEGIN/COMMIT when caller already has an open transaction.
    The delete+insert pair is still atomic because both DML statements run in
    the same (caller-owned) transaction.
    """
    if not metrics or dry_run:
        return 0

    today = date.today().isoformat()
    sql_del = (
        "DELETE FROM fund_metrics "
        "WHERE isin=%s AND metric LIKE 'beta_%%' AND horizon=%s AND metric_version=%s"
    )
    sql_ins = """
        INSERT INTO fund_metrics
            (isin, metric, horizon, value, real_flag,
             calculation_date, metric_version, benchmark_id, source_rows,
             algorithm_version, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s)
        ON CONFLICT (isin, metric, horizon, real_flag, metric_version) DO UPDATE SET
            value = excluded.value, calculation_date = excluded.calculation_date,
            benchmark_id = excluded.benchmark_id, source_rows = excluded.source_rows,
            algorithm_version = excluded.algorithm_version, batch_id = excluded.batch_id,
            load_ts = DEFAULT
    """
    rows = [
        (
            isin,
            m["metric"],
            horizon,
            m["value"] if not (isinstance(m["value"], float) and
                                m["value"] != m["value"]) else None,  # NaN -> NULL
            m["real_flag"],
            today,
            metric_version,
            m.get("source_rows"),
            algorithm_version,
            batch_id,
        )
        for m in metrics
    ]
    # EFF-2: skip own transaction when the caller batches for us
    if in_transaction(conn):
        conn.execute(sql_del, (isin, horizon, metric_version))
        _executemany(conn, sql_ins, rows)
        return len(rows)
    begin_immediate(conn)
    try:
        conn.execute(sql_del, (isin, horizon, metric_version))
        _executemany(conn, sql_ins, rows)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(rows)
