#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FND-0177 one-shot cleanup: delete the 218 provably bad daily NAV rows of three funds from bronze.fund_nav_daily.

    python scripts/ops/fnd0177_delete_bad_daily_rows.py            # DRY RUN: counts + CSV backup only
    python scripts/ops/fnd0177_delete_bad_daily_rows.py --apply    # backup, then delete in ONE transaction

Rows (verified 2026-10-03; the nav_discovery quality gate _filter_daily_anomalies flags exactly these):
  IE00B3L10570  5   nav < 50 (prints of 1.0 among ~100)
  LU0052474979  1   2001-05-18, nav 9.1562 (among ~41)
  LU1291108998  212 date < 2016-12-30 (scale 127,500-133,200 before the seam; the monthly series starts at the seam)
The delete asserts the exact count per fund and rolls back on any mismatch. The CSV backup goes to
C:/data/fondos/audit/. Uses FONDOS_PG_DSN (fondos_app, DML). Exit 0 ok, 1 refused/mismatch.
"""
import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.db import get_connection  # noqa: E402

RULES = [("IE00B3L10570", "nav < 50", 5),
         ("LU0052474979", "date = '2001-05-18' AND nav < 20", 1),
         ("LU1291108998", "date < '2016-12-30'", 212)]
OUT = Path(r"C:\data\fondos\audit")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = get_connection(backend="postgres")
    rows = []
    for isin, cond, n in RULES:
        r = conn.execute(f"SELECT * FROM bronze.fund_nav_daily WHERE isin = %s AND {cond}", [isin]).fetchall()
        print(f"{isin}: {len(r)} rows match (expected {n})")
        if len(r) != n:
            print("REFUSED: count mismatch - the data changed since the 2026-10-03 verification"); return 1
        rows += r
    cols = [d.name for d in conn.execute("SELECT * FROM bronze.fund_nav_daily LIMIT 0").description]
    OUT.mkdir(parents=True, exist_ok=True)
    bak = OUT / f"fnd0177_deleted_daily_rows_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(bak, "w", newline="", encoding="utf8") as fh:
        w = csv.writer(fh); w.writerow(cols); w.writerows(rows)
    print(f"backup: {len(rows)} rows -> {bak}")
    if not a.apply:
        print("DRY RUN: nothing deleted. Re-run with --apply."); return 0
    with conn.transaction():
        for isin, cond, n in RULES:
            d = conn.execute(f"DELETE FROM bronze.fund_nav_daily WHERE isin = %s AND {cond}", [isin]).rowcount
            if d != n:
                raise RuntimeError(f"{isin}: deleted {d}, expected {n} - rolled back")
    conn.commit()        # the SELECTs above opened an implicit transaction: transaction() was only a SAVEPOINT in it
    conn.close()
    chk = get_connection(backend="postgres")          # re-read from a FRESH connection: never trust the same session
    left = sum(chk.execute(f"SELECT count(*) FROM bronze.fund_nav_daily WHERE isin = %s AND {cond}", [isin]).fetchone()[0]
               for isin, cond, _ in RULES)
    chk.close()
    if left:
        print(f"FAILED: {left} bad rows still present after the commit"); return 1
    print(f"DELETED {len(rows)} rows (verified from a fresh connection).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
