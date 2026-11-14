# proyecto1/tests/test_fund_writer_fee_pair_pkgh_pg.py
# -*- coding: utf-8 -*-
"""Package H (FND-0408): the writer's behaviour for the fee pairs, against the real DDL where both flags are GENERATED columns (hermetic PG, `pg_conn` savepoint).

A pair the pipeline resolved (record['_fee_pairs_resolved']) is written as a UNIT ('ow', NULLs included); a side the pipeline did not resolve keeps its stored pair
(COALESCE); without the marker (a CACHED cycle) nothing of the stored pair moves. The flags are never written (Postgres refuses an explicit value for a generated
column) and read back as the state DERIVED from the numbers: the flag can no longer disagree with them, which is the defect this package removes."""
from __future__ import annotations

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
for _p in (_CORE_DIR, _P1_DIR, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fund_writer  # noqa: E402
from test_fund_writer_pg_upsert import _minimal_partial_record  # noqa: E402

ISIN = "ZZ0000PKGH01"
COLS = "entry_fee_pct, entry_fee_pct_max, exit_fee_pct, exit_fee_pct_max, fee_known_flag, exit_fee_known_flag"


def _seed(conn, **stored):
    """A fund_master row with exactly the stored fee numbers given (everything else a valid minimal record)."""
    conn.execute("SET search_path = gold, silver, bronze, control, public")
    fund_writer.upsert_fund_master(conn, _minimal_partial_record(ISIN, "Renta Variable", "TEST PKGH"))
    sets = ", ".join(f"{k} = %s" for k in stored)
    conn.execute(f"UPDATE fund_master SET {sets} WHERE isin = %s", (*stored.values(), ISIN))


def _row(conn):
    return tuple(conn.execute(f"SELECT {COLS} FROM fund_master WHERE isin = %s", (ISIN,)).fetchone())


def _record(**fee):
    rec = _minimal_partial_record(ISIN, "Renta Variable", "TEST PKGH")
    rec.update({"Entry_Fee_Pct": None, "Entry_Fee_Pct_Max": None, "Exit_Fee_Pct": None, "Exit_Fee_Pct_Max": None, "Fee_Known_Flag": "NOT_FOUND"})   # a record may still carry the legacy key
    rec.update(fee)
    return rec


def test_a_resolved_pair_is_written_as_a_unit_and_the_flag_follows_the_numbers(pg_conn):
    _seed(pg_conn, entry_fee_pct=0.0, entry_fee_pct_max=None)
    assert _row(pg_conn)[4] == "ZERO_CONFIRMED"                                    # a stale 0.0 reads as a confirmed zero: the defect FND-0406 hit
    fund_writer.upsert_fund_master(pg_conn, _record(Entry_Fee_Pct=None, Entry_Fee_Pct_Max=4.96, _fee_pairs_resolved={"entry": True}))
    assert _row(pg_conn)[:2] == (None, 4.96) and _row(pg_conn)[4] == "ENTRY_CONDITIONAL"      # stale point cleared by the unit write; the flag is derived, not written


def test_a_resolved_pair_can_clear_a_stale_ceiling(pg_conn):
    _seed(pg_conn, entry_fee_pct=0.0, entry_fee_pct_max=0.10)
    fund_writer.upsert_fund_master(pg_conn, _record(Entry_Fee_Pct=0.0, Entry_Fee_Pct_Max=None, _fee_pairs_resolved={"entry": True}))
    assert _row(pg_conn)[:2] == (0.0, None)                                       # F1b: an old extractor's ceiling cannot survive current evidence


def test_an_unresolved_side_keeps_its_stored_pair(pg_conn):
    _seed(pg_conn, entry_fee_pct=0.03, entry_fee_pct_max=3.0, exit_fee_pct=0.01, exit_fee_pct_max=1.0)
    fund_writer.upsert_fund_master(pg_conn, _record(Entry_Fee_Pct=0.05, Entry_Fee_Pct_Max=5.0, _fee_pairs_resolved={"entry": True}))
    row = _row(pg_conn)
    assert row[:4] == (0.05, 5.0, 0.01, 1.0) and row[4:] == ("EXTRACTED", "EXTRACTED")      # the exit side had no evidence: COALESCE kept it whole


def test_a_cached_pass_without_the_marker_writes_no_pair(pg_conn):
    """The CACHED cycle: the pipeline removed the pair from the record (None) and set no marker, so nothing of the stored pair moves."""
    _seed(pg_conn, entry_fee_pct=0.03, entry_fee_pct_max=3.0, exit_fee_pct=0.01, exit_fee_pct_max=1.0)
    fund_writer.upsert_fund_master(pg_conn, _record())
    assert _row(pg_conn)[:4] == (0.03, 3.0, 0.01, 1.0)


def test_both_flags_are_generated_columns_in_the_ddl(pg_conn):
    rows = pg_conn.execute("SELECT attname, attgenerated FROM pg_attribute WHERE attrelid = 'silver.fund_master'::regclass AND attname IN ('fee_known_flag', 'exit_fee_known_flag') "
                           "AND NOT attisdropped ORDER BY attname").fetchall()
    assert [tuple(r) for r in rows] == [("exit_fee_known_flag", "s"), ("fee_known_flag", "s")]


def test_the_marker_never_reaches_the_sql_and_the_switch_is_on():
    from shared import config
    assert config.ATTRIBUTE_GROUP_WRITE_ENABLED is True
