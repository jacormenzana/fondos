"""
tests/test_db_numeric_loader_pg.py — get_connection(backend="postgres") returns Python floats for
computed numerics, as SQLite did.

Postgres returns AVG()/SUM() over integers (and any ::numeric expression) as decimal.Decimal.
Decimal breaks float arithmetic (`Decimal * float` -> TypeError), silently changes pandas dtypes and
is written to Excel as text. shared/db.py registers psycopg's float loader for `numeric` on every
connection it hands out. The connection under test is built THROUGH get_connection() — the shared
test fixtures use a bare psycopg.connect() and would not see the loader.
"""
from __future__ import annotations

import os

import pytest

psycopg = pytest.importorskip("psycopg")

_DSN = os.environ.get("FONDOS_TEST_PG_DSN")

pytestmark = pytest.mark.skipif(not _DSN, reason="FONDOS_TEST_PG_DSN not set")


@pytest.fixture()
def conn(monkeypatch):
    from shared.db import get_connection
    monkeypatch.setenv("FONDOS_PG_DSN", _DSN)
    c = get_connection(backend="postgres")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def test_avg_over_integers_is_a_float_not_decimal(conn):
    row = conn.execute("SELECT AVG(x) AS a, SUM(x) AS s FROM (VALUES (1), (2), (4)) AS t(x)").fetchone()
    assert isinstance(row[0], float) and row[0] == pytest.approx(7 / 3)
    # SUM() of integers is a bigint (a Python int) on both engines: unchanged, as in SQLite
    assert isinstance(row[1], int) and row[1] == 7


def test_sum_over_numeric_values_is_a_float(conn):
    v = conn.execute("SELECT SUM(x) FROM (VALUES (1.5::numeric), (2.25::numeric)) AS t(x)").fetchone()[0]
    assert isinstance(v, float) and v == pytest.approx(3.75)


def test_explicit_numeric_cast_is_a_float(conn):
    v = conn.execute("SELECT 2.5::numeric").fetchone()[0]
    assert isinstance(v, float) and v == 2.5


def test_float_arithmetic_on_a_computed_average_does_not_raise(conn):
    avg = conn.execute("SELECT AVG(x) FROM (VALUES (10), (20)) AS t(x)").fetchone()[0]
    assert avg * 1.5 == pytest.approx(22.5)     # Decimal * float would raise TypeError


def test_double_precision_and_integers_are_untouched(conn):
    r = conn.execute("SELECT 1.25::float8, 7::int, 3::bigint, 'x'::text").fetchone()
    assert r[0] == 1.25 and isinstance(r[0], float)
    assert r[1] == 7 and isinstance(r[1], int)
    assert r[2] == 3 and isinstance(r[2], int)
    assert r[3] == "x"


def test_null_numeric_stays_none(conn):
    assert conn.execute("SELECT NULL::numeric").fetchone()[0] is None
