#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backfill_geography_source.py -- FND-0441 Step 3: fill fund_master.Geography_Source for every stored row (retired funds included). Writes ONLY that column.

    python scripts/mig/fondos/backfill_geography_source.py            # DRY RUN: read-only, prints the distribution it would write
    python scripts/mig/fondos/backfill_geography_source.py --apply    # writes it in one transaction, then verifies that every Geography has a source

The source is classify_utils.derive_geography_source, the function the P1 pipeline uses. Rows without Geography get no source. Idempotent (a second run changes nothing).
Runs with the pipeline role (FONDOS_PG_DSN, DML only). Never prints a DSN.
Exit codes: 0 ok, 1 failed (rolled back), 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RC_OK, RC_FAILED, RC_REFUSED = 0, 1, 3

ROWS_SQL = """
    SELECT fm.isin, fm.geography, fm.geography_source, fm.fund_name, fm.benchmark_declared, fm.fund_nature, fm.theme, km.raw_kiid_text, km.kiid_status
    FROM silver.fund_master fm
    LEFT JOIN bronze.fund_kiid_metadata km ON km.isin = fm.isin AND km.kiid_class = 1
    ORDER BY fm.isin
"""


def plan(rows, derive) -> list:
    """[(isin, geography, old_source, new_source)] for every row; `derive` is derive_geography_source. Pure."""
    return [(isin, geo, old, derive(geo, text, name, bench, nature, theme, status)) for isin, geo, old, name, bench, nature, theme, text, status in rows]


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the column (default: dry run)")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts" / "mig" / "fondos"))
    from shared.env_guard import require_db_driver
    require_db_driver()
    from shared.db import get_connection
    from proyecto1.core.classify_utils import derive_geography_source
    from migrate_fund_metric_state_ols_calc_version import LOCK_FILE, lock_file_held

    conn = get_connection()
    try:
        todo = plan([tuple(r) for r in conn.execute(ROWS_SQL).fetchall()], derive_geography_source)
        changes = [(isin, new) for isin, _geo, old, new in todo if old != new]
        groups = Counter(("none" if not geo else "Global" if geo == "Global" else "region", new or "-") for _isin, geo, _old, new in todo)
        print(f"{len(todo)} funds; rows by (Geography, source): {dict(sorted(groups.items()))}")
        print(f"rows to change: {len(changes)}")
        if not args.apply:
            conn.rollback()
            print("DRY RUN: nothing written.")
            return RC_OK
        if lock_file_held():
            conn.rollback()
            print(f"REFUSED: the launcher cycle lock is held ({LOCK_FILE}); a cycle is running.", file=sys.stderr)
            return RC_REFUSED
        conn.cursor().executemany("UPDATE silver.fund_master SET geography_source = %s WHERE isin = %s", [(new, isin) for isin, new in changes])
        missing = conn.execute("SELECT count(*) FROM silver.fund_master WHERE geography IS NOT NULL AND geography_source IS NULL").fetchone()[0]
        if missing:
            conn.rollback()
            print(f"FAILED, rolled back: {missing} rows with a Geography and no source.", file=sys.stderr)
            return RC_FAILED
        conn.commit()
        print(f"APPLIED: {len(changes)} rows written; rows with a Geography and no source: 0.")
        return RC_OK
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
