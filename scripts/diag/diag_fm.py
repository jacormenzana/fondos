import sys
sys.path.insert(0, '.')
from shared.db import get_connection

conn = get_connection()

print('=== INDICES fund_master ===')
for r in conn.execute(
    "SELECT name, sql FROM sqlite_master "
    "WHERE type='index' AND tbl_name='fund_master'"
).fetchall():
    print(r[0], '->', r[1])

print()
print('=== TRIGGERS fund_master ===')
for r in conn.execute(
    "SELECT name, sql FROM sqlite_master "
    "WHERE type='trigger' AND tbl_name='fund_master'"
).fetchall():
    print(r[0])

print()
print('=== SQL completo fund_master ===')
print(conn.execute(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='fund_master'"
).fetchone()[0])

conn.close()
