# -*- coding: utf-8 -*-
"""Dump OT (costs-over-time) section for ACI-missing funds."""
import sqlite3
import re

DB = r'C:\desarrollo\fondos\db\fondos.sqlite'
OT = re.compile(r'largo del tiempo|incidencia|costes totales|salida después', re.I)

# both-missing (FR collapsed-grid family) + need a 504 RHP-only member resolved at runtime
TARGETS_BOTH = ['FR0007435920', 'FR0010251660', 'FR0013439478', 'FR0014008W22']


def main():
    conn = sqlite3.connect(DB)
    for isin in TARGETS_BOTH:
        row = conn.execute(
            "SELECT DLA2_Table_Text FROM fund_kiid_metadata WHERE ISIN=?", (isin,)
        ).fetchone()
        print('=' * 60)
        print(isin, '(both aci1+acirhp missing)')
        print('=' * 60)
        grid = (row or [''])[0] or ''
        idx = grid.lower().find('largo del tiempo')
        if idx >= 0:
            for l in grid[idx:idx+600].split('\n')[:14]:
                print('   ', repr(l[:150]))
        else:
            print('   no OT header in grid; total len', len(grid))
            # show any incidencia/costes rows
            for l in grid.split('\n'):
                if OT.search(l):
                    print('    [match]', repr(l[:150]))
        print()
    conn.close()


if __name__ == '__main__':
    main()
