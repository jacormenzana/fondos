# shared/migrate_schema_v27.py
# -*- coding: utf-8 -*-
"""
Migracion idempotente a schema v27 -- P3 optimization plan, Phase 3a.

Aplica a `db/fondos.sqlite` el mismo diseno de esquema ya validado y
comprometido en `db/pg/30_gold.sql` (equipo de migracion a Postgres,
2026-09-19), de modo que los 4 consumidores P3
(portfolio_builder.py/backtesting.py/monthly_report.py/fund_scorer.py)
puedan portarse a la nueva forma HOY, contra una base de datos real y
consultable -- no existe ningun Postgres desplegado y alcanzable todavia
(verificado: sin Docker, sin psql en PATH; la memoria del equipo de
migracion confirma "never executed against a live PostgreSQL instance").
SQLite sigue siendo la unica base de datos operativa real hasta el cutover.

Cambios v27:
  - fund_scores: PK extendida de (isin, block, score_version) a
    (isin, block, score_version, regime, as_of_date). SQLite no soporta
    ALTER TABLE ... DROP/ADD de columnas PK, asi que se reconstruye la
    tabla (patron estandar: crear _new con el esquema objetivo, copiar y
    transformar datos, DROP + RENAME). Nuevas columnas: regime (backfill:
    parseado de `notes` via la MISMA regex validada por
    scripts/mig/pg_seed.py::_FUND_SCORES_NOTES_RE contra la poblacion
    completa de 5.884 filas), as_of_date (backfill := calculated_at),
    score_base/multiplier (backfill NULL -- nunca se persistieron como
    columnas propias historicamente, solo dentro del JSON de score_detail;
    mismo backfill que eligio el equipo de Postgres, por consistencia entre
    ambos sistemas), exclusion_reason (backfill: parte 'excluido: ...' de
    `notes`, NULL si el fondo era elegible).
  - regime_history (tabla nueva): persiste lo que
    proyecto3/src/regime_classifier.py::classify_historical() computa.
    Columnas identicas a RegimeResult + classifier_version + membership_json
    (reservado, "fold into v27" segun el plan). Arranca vacia -- el lado de
    ESCRITURA (que RegimeClassifier persista aqui) es una pieza deliberada y
    explicitamente distinta, no incluida en este alcance (ver el plan, Fase
    3b "persistence half").

Cada paso comprueba su propia precondicion antes de actuar (columna/tabla ya
existe -> skip), por lo que ejecutar este script mas de una vez es seguro.

Uso (una sola vez, contra la BD de produccion):
    cd c:/desarrollo/fondos
    C:\\data\\envs\\des\\python.exe -m shared.migrate_schema_v27

Precaucion:
  - Hacer una copia de seguridad de db/fondos.sqlite antes de ejecutar en
    produccion (convencion ya establecida en este repo).
  - fund_scores se reconstruye por completo -- verificar recuento de filas
    antes/despues (debe conservarse exactamente).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from shared.db import get_connection


# Misma regex validada por scripts/mig/pg_seed.py::_FUND_SCORES_NOTES_RE,
# corrida contra la poblacion completa de fund_scores (5.884/5.884 filas,
# 0 fallos de parseo) -- replicada aqui verbatim, no reinventada.
_FUND_SCORES_NOTES_RE = re.compile(
    r"^regime=(?P<regime>[^\s|]+)(?:\s*\|\s*excluido:\s*(?P<exclusion_reason>.+))?$"
)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _migrate_fund_scores(conn) -> dict:
    """
    Reconstruye fund_scores con la PK extendida. Idempotente: si `regime`
    ya es una columna real (no solo texto en `notes`), asume que la
    migracion ya corrio y no hace nada.
    """
    if _column_exists(conn, "fund_scores", "regime"):
        print("  SKIP  fund_scores -- ya migrada (columna 'regime' presente)")
        return {"skipped": True}

    n_before = conn.execute("SELECT COUNT(*) FROM fund_scores").fetchone()[0]

    conn.execute("""
        CREATE TABLE fund_scores_new (
            isin              TEXT    NOT NULL,
            block             TEXT    NOT NULL,
            score_version     TEXT    NOT NULL DEFAULT 'v1',
            regime            TEXT    NOT NULL,
            as_of_date        DATE    NOT NULL,
            score_total       REAL,
            score_base        REAL,
            multiplier        REAL,
            score_detail      TEXT,
            eligible          INTEGER NOT NULL DEFAULT 0 CHECK (eligible IN (0,1)),
            exclusion_reason  TEXT,
            calculated_at     DATE    NOT NULL,
            notes             TEXT,
            PRIMARY KEY (isin, block, score_version, regime, as_of_date),
            FOREIGN KEY (isin) REFERENCES fund_master (ISIN) ON DELETE CASCADE
        )
    """)

    rows = conn.execute("""
        SELECT isin, block, score_version, score_total, score_detail,
               eligible, calculated_at, notes
        FROM fund_scores
    """).fetchall()

    insert_sql = """
        INSERT INTO fund_scores_new
            (isin, block, score_version, regime, as_of_date, score_total,
             score_base, multiplier, score_detail, eligible,
             exclusion_reason, calculated_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
    """
    to_insert = []
    parse_failures = []
    for isin, block, score_version, score_total, score_detail, eligible, calculated_at, notes in rows:
        m = _FUND_SCORES_NOTES_RE.match(notes or "")
        if m is None:
            parse_failures.append((isin, block, notes))
            continue
        regime = m.group("regime")
        exclusion_reason = m.group("exclusion_reason")
        as_of_date = calculated_at  # backfill: unica fecha disponible historicamente
        to_insert.append((
            isin, block, score_version, regime, as_of_date, score_total,
            score_detail, eligible, exclusion_reason, calculated_at, notes,
        ))

    if parse_failures:
        conn.execute("DROP TABLE fund_scores_new")
        raise ValueError(
            f"fund_scores: {len(parse_failures)} fila(s) con `notes` que no "
            f"encaja en el patron 'regime=X' / 'regime=X | excluido: ...' -- "
            f"no se puede derivar `regime` (columna PK NOT NULL). Primeras: "
            f"{parse_failures[:5]}. No continuar sin resolver esto -- no "
            f"asignar un regime sentinela por defecto."
        )

    conn.executemany(insert_sql, to_insert)

    n_after = conn.execute("SELECT COUNT(*) FROM fund_scores_new").fetchone()[0]
    if n_after != n_before:
        conn.execute("DROP TABLE fund_scores_new")
        raise ValueError(
            f"fund_scores: recuento de filas no coincide tras la migracion "
            f"(antes={n_before}, despues={n_after}) -- abortado sin aplicar."
        )

    conn.execute("DROP TABLE fund_scores")
    conn.execute("ALTER TABLE fund_scores_new RENAME TO fund_scores")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scores_block ON fund_scores (block, eligible)"
    )
    # Mismo proposito que gold.idx_scores_latest en la DDL de Postgres: los 4
    # consumidores leen "ultimo score para este isin/block/regimen".
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scores_latest "
        "ON fund_scores (isin, block, regime, as_of_date DESC)"
    )
    conn.commit()

    print(f"  MIGRATE fund_scores -- {n_after} filas reconstruidas, "
          f"PK extendida a (isin, block, score_version, regime, as_of_date)")
    return {"migrated": True, "rows": n_after}


def _create_regime_history(conn) -> dict:
    """
    Crea regime_history si no existe. Vacia al crearse -- el lado de
    escritura (RegimeClassifier persistiendo aqui) es alcance de una fase
    posterior, deliberadamente distinta de esta migracion de esquema.
    """
    if _table_exists(conn, "regime_history"):
        print("  SKIP  regime_history -- ya existe")
        return {"skipped": True}

    conn.execute("""
        CREATE TABLE regime_history (
            date                 DATE    NOT NULL,
            classifier_version   TEXT    NOT NULL,
            regime               TEXT    NOT NULL,
            weight_defensive     REAL    NOT NULL,
            weight_balanced      REAL    NOT NULL,
            weight_dynamic       REAL    NOT NULL,
            oil_yoy              REAL,
            ipc_yoy_avg          REAL,
            cli_eu               REAL,
            rate_deposit         REAL,
            d_rate_3m            REAL,
            spread_hy            REAL,
            vix_yoy              REAL,
            term_spread          REAL,
            membership_json      TEXT,
            PRIMARY KEY (date, classifier_version)
        )
    """)
    conn.commit()
    print("  CREATE regime_history -- tabla nueva (vacia)")
    return {"created": True}


def migrate(conn=None) -> dict:
    """Aplica las migraciones v27. Si conn es None abre la conexion de produccion."""
    close = conn is None
    if conn is None:
        conn = get_connection()

    result = {}
    try:
        result["fund_scores"] = _migrate_fund_scores(conn)
        result["regime_history"] = _create_regime_history(conn)
    finally:
        if close:
            conn.close()

    return result


if __name__ == "__main__":
    print("=== Migracion schema v27 (P3 Phase 3a) ===")
    result = migrate()
    print()
    print("Resultado:", result)
    print("=== OK ===")
