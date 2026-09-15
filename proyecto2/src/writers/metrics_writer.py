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

import sqlite3
import time
from datetime import date


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


def write_metrics(
    conn: sqlite3.Connection,
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
    sql = """
        INSERT OR REPLACE INTO fund_metrics
            (isin, metric, horizon, value, real_flag,
             calculation_date, metric_version, benchmark_id, source_rows,
             algorithm_version, batch_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
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
    if conn.in_transaction:
        conn.executemany(sql, rows)
        return len(rows)
    for attempt in range(5):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.executemany(sql, rows)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return len(rows)
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) and attempt < 4:
                time.sleep(2 ** attempt)
            else:
                raise
    return 0


def write_timeseries(
    conn: sqlite3.Connection,
    rows: list[dict],
    dry_run: bool,
    *,
    algorithm_version: str,
    batch_id: str,
) -> int:
    """Escribe filas en fund_metric_timeseries con upsert (P0, 2026-09-15).

    Root cause fix: antes usaba INSERT OR IGNORE, lo que hacia que un
    --force silenciosamente no-opeara sobre filas ya existentes -- un
    cambio de formula (ej. la canonicalizacion de sortino/sharpe, 2b45416)
    nunca se propagaba a esta tabla aunque el recalculo se ejecutara, y
    algorithm_version/batch_id quedaban en first-write-wins. El UPDATE
    condicional mantiene la operacion idempotente a nivel de pagina: una
    re-ejecucion con valores identicos no escribe paginas nuevas, por lo
    que las ejecuciones incrementales normales no ven crecimiento de WAL.

    Devuelve el n. de filas insertadas O actualizadas (0 si no habia
    cambios reales o dry_run). NOTA: antes de este fix el valor devuelto
    contaba solo inserciones; ahora tambien cuenta actualizaciones -- ver
    el log [TIMESERIES] en el llamador.

    EFF-2: skips own BEGIN/COMMIT when caller already has an open transaction.
    """
    if not rows or dry_run:
        return 0
    sql = """
        INSERT INTO fund_metric_timeseries
            (isin, metric, window, date, value, real_flag,
             ref_type, ref_value, source_rows,
             algorithm_version, batch_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(isin, metric, window, date, real_flag) DO UPDATE SET
            value             = excluded.value,
            ref_type          = excluded.ref_type,
            ref_value         = excluded.ref_value,
            source_rows       = excluded.source_rows,
            algorithm_version = excluded.algorithm_version,
            batch_id          = excluded.batch_id
        WHERE fund_metric_timeseries.value IS NOT excluded.value
           OR fund_metric_timeseries.algorithm_version IS NOT excluded.algorithm_version
    """
    data = [
        (
            r["isin"], r["metric"], r["window"], r["date"],
            r["value"],
            r["real_flag"],
            r.get("ref_type"),
            r.get("ref_value"),
            r.get("source_rows"),
            algorithm_version,
            batch_id,
        )
        for r in rows
    ]
    # EFF-2: skip own transaction when the caller batches for us
    if conn.in_transaction:
        cur = conn.executemany(sql, data)
        return cur.rowcount if cur.rowcount >= 0 else len(data)
    for attempt in range(5):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.executemany(sql, data)
                conn.execute("COMMIT")
                return cur.rowcount if cur.rowcount >= 0 else len(data)
            except Exception:
                conn.execute("ROLLBACK")
                raise
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) and attempt < 4:
                time.sleep(2 ** attempt)
            else:
                raise
    return 0


def replace_beta_set(
    conn: sqlite3.Connection,
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
        "WHERE isin=? AND metric LIKE 'beta_%' AND horizon=? AND metric_version=?"
    )
    sql_ins = """
        INSERT OR REPLACE INTO fund_metrics
            (isin, metric, horizon, value, real_flag,
             calculation_date, metric_version, benchmark_id, source_rows,
             algorithm_version, batch_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
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
    if conn.in_transaction:
        conn.execute(sql_del, (isin, horizon, metric_version))
        conn.executemany(sql_ins, rows)
        return len(rows)
    for attempt in range(5):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(sql_del, (isin, horizon, metric_version))
                conn.executemany(sql_ins, rows)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return len(rows)
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) and attempt < 4:
                time.sleep(2 ** attempt)
            else:
                raise
    return 0
