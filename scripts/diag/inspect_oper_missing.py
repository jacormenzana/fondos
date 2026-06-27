# -*- coding: utf-8 -*-
"""Inspect why operación % is missing: grid oper row + raw-text oper sentence."""
import sqlite3
import re

DB = r'C:\desarrollo\fondos\db\fondos.sqlite'
SEP = chr(124) * 3
TARGETS = ['FR0007435920', 'FR0010251660', 'FR0010836163', 'LU0996100000']
# note: last may not exist; replaced below with a real LU09961 member at runtime

OPER = re.compile(r'operaci[oó]n|transacci[oó]n|transaction', re.I)


def main():
    conn = sqlite3.connect(DB)
    # resolve a real LU09961 member
    lu = conn.execute(
        "SELECT ISIN FROM fund_kiid_metadata WHERE ISIN LIKE 'LU09961%' LIMIT 1"
    ).fetchone()
    targets = ['FR0007435920', 'FR0010251660', 'FR0010836163']
    if lu:
        targets.append(lu[0])

    for isin in targets:
        row = conn.execute(
            "SELECT DLA2_Table_Text, Raw_KIID_Text FROM fund_kiid_metadata WHERE ISIN=?",
            (isin,)
        ).fetchone()
        print('=' * 60)
        print(isin)
        print('=' * 60)
        if not row:
            print('  (not found)')
            continue
        grid, raw = row[0] or '', row[1] or ''

        print('-- GRID oper rows --')
        for l in grid.split('\n'):
            if OPER.search(l):
                print('   ', repr(l[:160]))

        print('-- RAW oper sentence --')
        # find the operación description sentence
        m = re.search(r'.{0,30}(?:operaci[oó]n|transacci[oó]n)[^.]{0,180}', raw, re.I)
        if m:
            print('   ', repr(m.group(0)))
        else:
            print('    (no operación sentence found in raw)')
        print()
    conn.close()


if __name__ == '__main__':
    main()
