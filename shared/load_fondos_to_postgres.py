"""
Load all tables from the local SQLite fondos DB into a PostgreSQL database.

Run on the Windows host (where the SQLite file lives), AFTER:
  1. Publishing the Postgres port to the host (compose: db ports "5433:5432")
  2. Creating the target database:  CREATE DATABASE fondos;
  3. pip install pandas sqlalchemy psycopg2-binary

Each table is REPLACED on load (idempotent re-runs). Safe to run repeatedly.
"""

import sqlite3
import pandas as pd
from sqlalchemy import create_engine

# ----------------------------------------------------------------------
# CONFIG  -- adjust only if your paths / credentials differ
# ----------------------------------------------------------------------
SQLITE_PATH = r"C:\desarrollo\fondos\db\fondos.sqlite"

# Loader connects from the Windows HOST -> published port 5433
PG_URL = "postgresql+psycopg2://superset:superset@localhost:5433/fondos"

# Read each SQLite table fully into memory (DB is ~383 MB, fine),
# then insert in batches sized to stay under Postgres' 65535-param cap.
PARAM_BUDGET = 60000  # safety margin below 65535
# ----------------------------------------------------------------------


def main():
    print(f"Source : {SQLITE_PATH}")
    print(f"Target : {PG_URL}\n")

    src = sqlite3.connect(SQLITE_PATH)
    dst = create_engine(PG_URL)

    # Discover user tables (skip internal sqlite_* tables)
    tables = pd.read_sql(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name",
        src,
    )["name"].tolist()

    if not tables:
        print("No tables found. Check SQLITE_PATH.")
        return

    print(f"Found {len(tables)} tables: {', '.join(tables)}\n")

    for t in tables:
        df = pd.read_sql(f'SELECT * FROM "{t}"', src)
        ncols = max(len(df.columns), 1)

        # Postgres TEXT cannot store NUL (0x00). SQLite can. Strip it from
        # every string value (common in text scraped out of PDFs).
        obj_cols = df.select_dtypes(include="object").columns
        for c in obj_cols:
            df[c] = df[c].map(
                lambda v: v.replace("\x00", "") if isinstance(v, str) else v
            )

        # Cap rows-per-insert so (rows * cols) stays under the param limit.
        safe_chunk = max(1, PARAM_BUDGET // ncols)

        df.to_sql(
            t,
            dst,
            if_exists="replace",   # drop + recreate each run
            index=False,
            method="multi",        # batched multi-row INSERT (fast)
            chunksize=safe_chunk,
        )
        print(f"  {t:<28} {len(df):>8,} rows  ({ncols} cols)  loaded")

    src.close()
    dst.dispose()
    print("\nAll tables loaded into 'fondos'.")


if __name__ == "__main__":
    main()
