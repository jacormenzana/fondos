# -*- coding: utf-8 -*-
"""Dump full composition section (unfiltered) for mgmt-MISSING funds."""
import sqlite3

DB = r'C:\desarrollo\fondos\db\fondos.sqlite'
TARGETS = ['DE000DWS17J0', 'FR0012903276', 'LU0048579097']


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
        grid = (row or [''])[0] or ''
        lines = grid.split('\n')
        # find composition header, dump 12 lines from there
        idx = next((i for i, l in enumerate(lines)
                    if 'composici' in l.lower()), None)
        if idx is None:
            print('  (no composition header found)')
            print('  total grid lines:', len(lines))
            # show first 6 lines for context
            for l in lines[:6]:
                print('   ', repr(l))
        else:
            for l in lines[idx:idx + 12]:
                print('   ', repr(l))
        print()
    conn.close()


if __name__ == '__main__':
    main()
