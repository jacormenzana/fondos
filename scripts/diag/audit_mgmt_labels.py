# -*- coding: utf-8 -*-
"""
audit_mgmt_labels.py — find management-row label variants the current
COMPOSITION_ROW_LABELS['management'] pattern misses.

For every Class-1 fund, scan DLA2_Table_Text (and Raw_KIID_Text fallback) for
lines that look like a management-fee row (contain 'gesti' near a %/EUR in the
cost section) but do NOT match the current mgmt label pattern. Cluster the
unmatched label heads so we can extend the pattern from evidence, corpus-wide.

Run (Windows, conda 'des'):
  set PYTHONPATH=C:\\desarrollo\\fondos\\proyecto1;C:\\desarrollo\\fondos\\proyecto1\\core;C:\\desarrollo\\fondos\\shared
  python -X utf8 audit_mgmt_labels.py
"""
import re
import sqlite3
import collections

DB = r'C:\desarrollo\fondos\db\fondos.sqlite'
SEP = chr(124) * 3

# Current deployed mgmt pattern (keep in sync with cost_table_parser.py L145)
MGMT_CUR = re.compile(r'comisiones?\s*de\s*gesti[oó]n|management\s*(?:fee|cost)s?', re.I)

# Broad "this row is about management" signal: a gestión/gastos-de-gestion token.
MGMT_BROAD = re.compile(r'gesti[oó]n|management', re.I)
# Exclude obvious non-mgmt rows that also contain 'gestion' (e.g. sociedad gestora prose)
NONROW = re.compile(r'sociedad\s+gestora|gestora\s+|reclamaci|dep[oó]sito', re.I)


def first_cell(line):
    cells = line.split(SEP)
    if cells and cells[0].strip() == '':
        cells = cells[1:]
    return cells[0].strip() if cells else ''


def main():
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT ISIN, DLA2_Table_Text FROM fund_kiid_metadata WHERE KIID_Class=1"
    ).fetchall()
    conn.close()

    unmatched_heads = collections.Counter()
    affected = []
    for isin, grid in rows:
        if not grid:
            continue
        for l in grid.split('\n'):
            fc = first_cell(l)
            if not fc:
                continue
            # candidate mgmt row: broad gestión signal, not a prose exclusion,
            # and NOT already matched by current pattern
            if (MGMT_BROAD.search(fc) and not NONROW.search(fc)
                    and not MGMT_CUR.search(fc)):
                head = fc[:60]
                unmatched_heads[head] += 1
                affected.append(isin)
                break

    print('=== Management-label variants NOT matched by current pattern ===')
    print(f'Funds with an unmatched mgmt-like row: {len(set(affected))}')
    print()
    print('Distinct unmatched label heads (top 25):')
    for head, cnt in unmatched_heads.most_common(25):
        print(f'  x{cnt:>4}  {head!r}')


if __name__ == '__main__':
    main()
