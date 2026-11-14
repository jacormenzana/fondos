# -*- coding: utf-8 -*-
"""Package H, Slice 1: the generated-column expression equals `derive_fee_state` on a real Postgres (hermetic, the `pg_conn` fixture).

The flags become GENERATED columns, so the SQL text and the Python function are two renderings of one definition; this test is what keeps
them from drifting. It builds a scratch TEMP table (nothing of the live schema is touched) with the generated expressions for both
sides and compares every row of the full truth table, negative, NaN and infinite values included (PG orders NaN above every number,
hence the explicit guard in the expression).
"""
from __future__ import annotations

import itertools
import math

from shared.fee_state import derive_fee_state, fee_state_sql

VALUES = (None, 0.0, -0.0, 0.0001, 0.0525, 0.05, 5.25, 25.0, -0.01, -5.0, float("nan"), float("inf"))


def _build(conn):
    conn.execute(
        "CREATE TEMP TABLE fee_state_truth ("
        "  id serial PRIMARY KEY, p double precision, c double precision,"
        f"  entry_state text GENERATED ALWAYS AS ({fee_state_sql('p', 'c', 'entry')}) STORED,"
        f"  exit_state  text GENERATED ALWAYS AS ({fee_state_sql('p', 'c', 'exit')}) STORED) ON COMMIT DROP")
    for p, c in itertools.product(VALUES, VALUES):
        conn.execute("INSERT INTO fee_state_truth (p, c) VALUES (%s, %s)", (p, c))


def test_generated_expression_equals_the_python_definition_on_the_full_truth_table(pg_conn):
    _build(pg_conn)
    rows = pg_conn.execute("SELECT p, c, entry_state, exit_state FROM fee_state_truth").fetchall()
    assert len(rows) == len(VALUES) ** 2
    for p, c, entry_state, exit_state in rows:
        py_p = None if p is None else float(p)
        py_c = None if c is None else float(c)
        assert entry_state == derive_fee_state(py_p, py_c, "entry"), (p, c)
        assert exit_state == derive_fee_state(py_p, py_c, "exit"), (p, c)


def test_every_state_of_both_vocabularies_is_reachable_in_the_generated_column(pg_conn):
    _build(pg_conn)
    entry = {r[0] for r in pg_conn.execute("SELECT DISTINCT entry_state FROM fee_state_truth").fetchall()}
    exit_ = {r[0] for r in pg_conn.execute("SELECT DISTINCT exit_state FROM fee_state_truth").fetchall()}
    assert entry == {"EXTRACTED", "ZERO_CONFIRMED", "ENTRY_CONDITIONAL", "NOT_FOUND"}
    assert exit_ == {"EXTRACTED", "ZERO_CONFIRMED", "EXIT_CONDITIONAL", "NOT_FOUND"}


def test_a_generated_column_refuses_an_explicit_value(pg_conn):
    """The reason the writer must stop writing the flags before the migration (plan R8): PostgreSQL rejects an explicit value."""
    import psycopg
    import pytest
    _build(pg_conn)
    pg_conn.execute("SAVEPOINT explicit_value")
    with pytest.raises(psycopg.errors.GeneratedAlways):
        pg_conn.execute("INSERT INTO fee_state_truth (p, c, entry_state) VALUES (0.05, 5.0, 'EXTRACTED')")
    pg_conn.execute("ROLLBACK TO SAVEPOINT explicit_value")
    assert math.isclose(pg_conn.execute("SELECT count(*) FROM fee_state_truth").fetchone()[0], len(VALUES) ** 2)
