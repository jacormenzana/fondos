# test_schema.py
# Diagnostic script, not a pytest test (lives outside any tests/ dir on purpose). Fixed 2026-09-20:
# `proyecto2.src.db` no longer exists (superseded by shared/db.py — see that module's docstring),
# and assert_schema_alignment() never accepted a `scope` kwarg, so this script raised unconditionally
# before either fix. `--backend postgres` lets this double as a quick manual schema-alignment check
# during the read-path port (plan §Addendum).
import argparse
import sys

sys.path.insert(0, '.')
from shared.db import get_connection
from shared.schema_checks import assert_schema_alignment

parser = argparse.ArgumentParser()
parser.add_argument("--backend", choices=["sqlite", "postgres"], default="sqlite")
args = parser.parse_args()

conn = get_connection(backend=args.backend)
try:
    assert_schema_alignment(conn)
    print(f"Schema OK -- todas las tablas alineadas ({args.backend})")
except AssertionError as e:
    print(f"ERROR: {e}")