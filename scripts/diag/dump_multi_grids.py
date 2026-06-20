# -*- coding: utf-8 -*-
"""
dump_multi_grids.py — inspect the actual grid rows for the 3 patterns found.

Prints every total-cost and ACI row (with their full cell content) for one
representative fund per pattern, so we can see whether duplicate/multiple
rows carry the SAME or DIFFERENT values — the deciding factor for the fix.
"""
import sqlite3
import sys

sys.path.insert(0, r'C:\desarrollo\fondos\proyecto1')
sys.path.insert(0, r'C:\desarrollo\fondos\proyecto1\core')
sys.path.insert(0, r'C:\desarrollo\fondos\shared')

from core.cost_table_parser import TOTAL_COSTS_ROW, ACI_ROW  # noqa

DB = r'C:\desarrollo\fondos\db\fondos.sqlite'
SEP = chr(124) * 3

# one representative per pattern:
#   A duplicate-OT, B multiple-ACI, C zero-total
TARGETS = ['FR0013296332', 'LU1873128687', 'LU0006277684', 'LU0083138064']


def first_cell(line):
    cells = line.split(SEP)
    if cells and cells[0].strip() == '':
        cells = cells[1:]
    return cells[0].strip() if cells else ''


def main():
    conn = sqlite3.connect(DB)
    for isin in TARGETS:
        row = conn.execute(
            "SELECT DLA2_Table_Text FROM fund_kiid_metadata WHERE ISIN=?",
            (isin,)
        ).fetchone()
        print('=' * 60)
        print(isin)
        print('=' * 60)
        if not row or not row[0]:
            print('  (no DLA2_Table_Text)')
            continue
        for l in row[0].split('\n'):
            fc = first_cell(l)
            is_total = bool(TOTAL_COSTS_ROW.match(fc))
            is_aci = bool(ACI_ROW.search(l))
            if is_total or is_aci:
                tag = 'TOTAL' if is_total else 'ACI  '
                print(f'  [{tag}] {l!r}')
        print()
    conn.close()


if __name__ == '__main__':
    main()
