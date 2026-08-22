"""
Load all tables from the local SQLite fondos DB into a PostgreSQL database.

Run on the Windows host (where the SQLite file lives), AFTER:
  1. Publishing the Postgres port to the host (compose: db ports "5433:5432")
  2. Creating the target database:  CREATE DATABASE fondos;
  3. pip install pandas sqlalchemy psycopg2-binary

All tables use full replace on each run EXCEPT fund_metric_timeseries (16.6M rows),
which uses incremental sync keyed on batch_id to avoid unsustainable full reloads.
Pass --full to force a complete reload of ALL tables.
"""

import sys
import sqlite3
import pandas as pd
from sqlalchemy import create_engine, text

# ----------------------------------------------------------------------
# CONFIG  -- adjust only if your paths / credentials differ
# ----------------------------------------------------------------------
SQLITE_PATH = r"C:\desarrollo\fondos\db\fondos.sqlite"

# Loader connects from the Windows HOST -> published port 5433
PG_URL = "postgresql+psycopg2://superset:superset@localhost:5433/fondos"

# Read each SQLite table fully into memory (DB is ~383 MB, fine),
# then insert in batches sized to stay under Postgres' 65535-param cap.
PARAM_BUDGET = 60000  # safety margin below 65535

# Large table that uses incremental sync instead of full replace.
_INCREMENTAL_TABLE = "fund_metric_timeseries"
_WATERMARK_COL     = "batch_id"          # lexicographically sortable run-id (YYYYMMDD_HHMMSS)
# ----------------------------------------------------------------------


def _clean_nul(df: pd.DataFrame) -> pd.DataFrame:
    """Strip NUL bytes from string columns (common in PDF-extracted text)."""
    for c in df.select_dtypes(include="object").columns:
        df[c] = df[c].map(lambda v: v.replace("\x00", "") if isinstance(v, str) else v)
    return df


def _safe_chunk(ncols: int) -> int:
    return max(1, PARAM_BUDGET // ncols)


def _load_full(src, dst, t: str) -> None:
    df = _clean_nul(pd.read_sql(f'SELECT * FROM "{t}"', src))
    ncols = max(len(df.columns), 1)
    df.to_sql(t, dst, if_exists="replace", index=False,
              method="multi", chunksize=_safe_chunk(ncols))
    print(f"  {t:<28} {len(df):>8,} rows  ({ncols} cols)  loaded")


def _sync_incremental(src, dst, t: str) -> None:
    """Incremental sync for fund_metric_timeseries using batch_id watermark.

    Finds ISINs with a batch_id newer than the PG watermark, deletes the
    stale PG rows for those ISINs, then inserts the new SQLite rows.
    Falls back to full replace on any error (e.g. first run / table absent).
    """
    try:
        # Get current PG watermark
        max_bid = None
        try:
            row = pd.read_sql(
                f"SELECT MAX({_WATERMARK_COL}) AS wm FROM {t}", dst
            )
            max_bid = row.iloc[0]["wm"]
        except Exception:
            pass  # table absent in PG → full load below

        if max_bid is None:
            df = _clean_nul(pd.read_sql(f'SELECT * FROM "{t}"', src))
            ncols = max(len(df.columns), 1)
            df.to_sql(t, dst, if_exists="replace", index=False,
                      method="multi", chunksize=_safe_chunk(ncols))
            print(f"  {t:<28} {len(df):>8,} rows  ({ncols} cols)  initial full load")
            return

        # ISINs updated since last sync
        new_isins = pd.read_sql(
            f'SELECT DISTINCT isin FROM "{t}" WHERE {_WATERMARK_COL} > ?',
            src, params=(max_bid,)
        )["isin"].tolist()

        if not new_isins:
            print(f"  {t:<28}        0 rows  incremental — no new batches (watermark={max_bid})")
            return

        # Delete stale PG rows for updated ISINs
        with dst.begin() as con:
            con.execute(
                text(f"DELETE FROM {t} WHERE isin = ANY(:isins)"),
                {"isins": new_isins}
            )

        # Load new rows from SQLite for those ISINs (parameterised)
        placeholders = ",".join("?" * len(new_isins))
        df = _clean_nul(pd.read_sql(
            f'SELECT * FROM "{t}" WHERE isin IN ({placeholders})',
            src, params=new_isins
        ))
        ncols = max(len(df.columns), 1)
        df.to_sql(t, dst, if_exists="append", index=False,
                  method="multi", chunksize=_safe_chunk(ncols))
        new_wm = df[_WATERMARK_COL].max() if _WATERMARK_COL in df.columns else "?"
        print(
            f"  {t:<28} {len(df):>8,} rows  ({ncols} cols)  "
            f"incremental ({len(new_isins)} ISINs, watermark {max_bid}→{new_wm})"
        )

    except Exception as exc:
        print(f"  [WARN] {t}: incremental sync failed ({exc}), falling back to full replace")
        _load_full(src, dst, t)


def main() -> None:
    full_reload = "--full" in sys.argv
    print(f"Source : {SQLITE_PATH}")
    print(f"Target : {PG_URL}")
    mode_label = "full reload (--full)" if full_reload else f"incremental for {_INCREMENTAL_TABLE}"
    print(f"[MODE] {mode_label}\n")

    src = sqlite3.connect(SQLITE_PATH)
    dst = create_engine(PG_URL)

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
        if t == _INCREMENTAL_TABLE and not full_reload:
            _sync_incremental(src, dst, t)
        else:
            _load_full(src, dst, t)

    src.close()
    dst.dispose()
    print("\nAll tables synced to 'fondos'.")


if __name__ == "__main__":
    main()
