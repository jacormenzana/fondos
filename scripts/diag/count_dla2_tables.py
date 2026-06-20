# -*- coding: utf-8 -*-
"""
count_dla2_tables.py — test the multiple-table hypothesis.

For each fund with a stored DLA2_Table_Text, count how many distinct
'total-cost' rows and 'ACI' rows the grid contains. A fund with >=2
total rows is a live instance of the multiple-DLA2-table scenario where
the parser's first-match-wins selection could bind to the wrong table.

Run (Windows, conda 'des'):
  set PYTHONPATH=C:\\desarrollo\\fondos\\proyecto1;C:\\desarrollo\\fondos\\proyecto1\\core;C:\\desarrollo\\fondos\\shared
  python -X utf8 count_dla2_tables.py
"""
import sqlite3
import sys

sys.path.insert(0, r'C:\desarrollo\fondos\proyecto1')
sys.path.insert(0, r'C:\desarrollo\fondos\proyecto1\core')
sys.path.insert(0, r'C:\desarrollo\fondos\shared')

from core.cost_table_parser import TOTAL_COSTS_ROW, ACI_ROW  # noqa

DB = r'C:\desarrollo\fondos\db\fondos.sqlite'
SEP = chr(124) * 3   # '|||'


def first_cell(line):
    """Extract the label cell (cell 0) from a DLA2 '|||'-delimited line."""
    parts = line.split(SEP)
    # drop leading/trailing empties produced by the |||...||| framing
    cells = [p for p in parts]
    if cells and cells[0].strip() == '':
        cells = cells[1:]
    return cells[0].strip() if cells else ''


def main():
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT ISIN, DLA2_Table_Text FROM fund_kiid_metadata "
        "WHERE DLA2_Table_Text IS NOT NULL AND length(DLA2_Table_Text) > 200"
    ).fetchall()
    conn.close()

    multi = []
    for isin, grid in rows:
        lines = grid.split('\n')
        total_n = sum(1 for l in lines if TOTAL_COSTS_ROW.match(first_cell(l)))
        aci_n = sum(1 for l in lines if ACI_ROW.search(l))
        marker = '  <-- MULTI' if total_n >= 2 or aci_n >= 2 else ''
        print(f'{isin}  total_rows={total_n}  aci_rows={aci_n}{marker}')
        if total_n >= 2 or aci_n >= 2:
            multi.append(isin)

    print()
    print(f'Funds evaluated: {len(rows)}')
    print(f'Funds with >=2 total OR >=2 ACI rows (multi-table candidates): {len(multi)}')
    if multi:
        print('Multi-table ISINs:', multi)


if __name__ == '__main__':
    main()
