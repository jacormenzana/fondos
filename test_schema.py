# test_schema.py
import sys
sys.path.insert(0, '.')
from proyecto2.src.db import get_connection
from shared.schema_checks import assert_schema_alignment

conn = get_connection()
try:
    assert_schema_alignment(conn, scope="all")
    print("Schema OK -- todas las tablas alineadas")
except AssertionError as e:
    print(f"ERROR: {e}")