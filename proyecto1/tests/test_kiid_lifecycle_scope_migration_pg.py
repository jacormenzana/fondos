# -*- coding: utf-8 -*-
"""Rehearsal of db/migrations/20261003_kiid_lifecycle_retire_scope.sql on the OLD table shape.

The hermetic test database is built from db/pg/*.sql, so silver.kiid_lifecycle already has the
column; the test drops it (and its CHECK) to recreate the shape the live server has today.
"""
from __future__ import annotations

import os

_MIG = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "db", "migrations",
    "20261003_kiid_lifecycle_retire_scope.sql"))

GONE, PART, LIVE, BACK, STRAY = (
    "LU0000GONE01", "LU0000PART01", "LU0000LIVE01", "LU0000BACK01", "LU0000GONE01_copia")


def test_migration_adds_column_backfills_and_is_idempotent(pg_conn):
    pg_conn.execute("ALTER TABLE silver.kiid_lifecycle DROP COLUMN retire_scope")  # OLD shape
    pg_conn.execute("""
        INSERT INTO bronze.db_document_catalogue
            (harvest_ts, gestora_label, cod_db, link_label, href, isin, cod_sus)
        VALUES ('20260101_000000','G','F1','KIID','https://x/1',%s,'KIID'),
               ('20260201_000000','G','F2','LIIC','https://x/2',%s,'LIIC'),
               ('20260201_000000','G','F3','KIID','https://x/3',%s,'KIID')
    """, (GONE, PART, BACK))
    pg_conn.execute("""
        INSERT INTO silver.kiid_lifecycle (isin, start_date, end_date, status) VALUES
            (%s,'2026-01-01','2026-02-01','retired'),
            (%s,'2026-01-01','2026-02-01','retired'),
            (%s,'2026-01-01',NULL,'commercializing'),
            (%s,'2026-01-01','2026-02-01','retired'),
            (%s,'2026-03-01',NULL,'commercializing'),
            (%s,'2026-01-01','2026-02-01','retired')
    """, (GONE, PART, LIVE, BACK, BACK, STRAY))
    sql = open(_MIG, encoding="utf-8").read()
    pg_conn.execute(sql)
    pg_conn.execute(sql)  # idempotent
    got = {(r[0], r[1]): r[2] for r in pg_conn.execute(
        "SELECT isin, status, retire_scope FROM silver.kiid_lifecycle").fetchall()}
    assert got == {
        (GONE, "retired"): "FULL",
        (PART, "retired"): "PARTIAL",
        (LIVE, "commercializing"): None,
        (BACK, "retired"): None,           # retired in the past, back in the catalogue
        (BACK, "commercializing"): None,
        (STRAY, "retired"): None,          # malformed key = stray archived PDF copy, not a fund
    }
