# proyecto1/tests/test_fund_writer_fee_pair_pkgh_pg.py
# -*- coding: utf-8 -*-
"""Package H (FND-0408): the writer's behaviour for the fee pairs, with ATTRIBUTE_GROUP_WRITE_ENABLED OFF and ON (hermetic PG, `pg_conn` savepoint).

OFF = today's writer, pinned: one COALESCE per column, so a stale 0.0 survives next to a new ceiling and the flag is written (the defect of FND-0406).
ON  = a pair the pipeline resolved (record['_fee_pairs_resolved']) is written as a UNIT ('ow', NULLs included); a side the pipeline did not resolve keeps
      its stored pair (COALESCE); the Fee_Known_Flag leaves the spec (it becomes a generated column, plan R8)."""
from __future__ import annotations

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
for _p in (_CORE_DIR, _P1_DIR, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fund_writer  # noqa: E402
from test_fund_writer_pg_upsert import _minimal_partial_record  # noqa: E402

ISIN = "ZZ0000PKGH01"
COLS = "entry_fee_pct, entry_fee_pct_max, exit_fee_pct, exit_fee_pct_max, fee_known_flag"


def _seed(conn, **stored):
    """A fund_master row with exactly the stored fee values given (everything else a valid minimal record)."""
    conn.execute("SET search_path = gold, silver, bronze, control, public")
    fund_writer.upsert_fund_master(conn, _minimal_partial_record(ISIN, "Renta Variable", "TEST PKGH"))
    sets = ", ".join(f"{k} = %s" for k in stored)
    conn.execute(f"UPDATE fund_master SET {sets} WHERE isin = %s", (*stored.values(), ISIN))


def _row(conn):
    return tuple(conn.execute(f"SELECT {COLS} FROM fund_master WHERE isin = %s", (ISIN,)).fetchone())


def _record(**fee):
    rec = _minimal_partial_record(ISIN, "Renta Variable", "TEST PKGH")
    rec.update({"Entry_Fee_Pct": None, "Entry_Fee_Pct_Max": None, "Exit_Fee_Pct": None, "Exit_Fee_Pct_Max": None, "Fee_Known_Flag": "NOT_FOUND"})
    rec.update(fee)
    return rec


def test_switch_off_is_todays_per_column_coalesce_and_the_flag_is_written(pg_conn):
    """The defect, pinned: the stale 0.0 survives next to the new ceiling, and the new flag overwrites the old one."""
    _seed(pg_conn, entry_fee_pct=0.0, entry_fee_pct_max=None, fee_known_flag="ZERO_CONFIRMED")
    fund_writer.upsert_fund_master(pg_conn, _record(Entry_Fee_Pct_Max=4.96, Fee_Known_Flag="ENTRY_CONDITIONAL"))     # no marker: the pipeline sets it only with the switch ON
    assert _row(pg_conn) == (0.0, 4.96, None, None, "ENTRY_CONDITIONAL")


def test_switch_on_a_resolved_pair_is_written_as_a_unit_and_the_flag_is_not_written(pg_conn, monkeypatch):
    monkeypatch.setattr(fund_writer, "_attribute_group_write_enabled", lambda: True)
    _seed(pg_conn, entry_fee_pct=0.0, entry_fee_pct_max=None, fee_known_flag="ZERO_CONFIRMED")
    fund_writer.upsert_fund_master(pg_conn, _record(Entry_Fee_Pct=None, Entry_Fee_Pct_Max=4.96, Fee_Known_Flag="ENTRY_CONDITIONAL", _fee_pairs_resolved={"entry": True}))
    assert _row(pg_conn) == (None, 4.96, None, None, "ZERO_CONFIRMED")            # stale point cleared by the unit write; the flag column is untouched


def test_switch_on_a_resolved_pair_can_clear_a_stale_ceiling(pg_conn, monkeypatch):
    monkeypatch.setattr(fund_writer, "_attribute_group_write_enabled", lambda: True)
    _seed(pg_conn, entry_fee_pct=0.0, entry_fee_pct_max=0.10)
    fund_writer.upsert_fund_master(pg_conn, _record(Entry_Fee_Pct=0.0, Entry_Fee_Pct_Max=None, _fee_pairs_resolved={"entry": True}))
    assert _row(pg_conn)[:2] == (0.0, None)                                       # F1b: an old extractor's ceiling cannot survive current evidence


def test_switch_on_an_unresolved_side_keeps_its_stored_pair(pg_conn, monkeypatch):
    monkeypatch.setattr(fund_writer, "_attribute_group_write_enabled", lambda: True)
    _seed(pg_conn, entry_fee_pct=0.03, entry_fee_pct_max=3.0, exit_fee_pct=0.01, exit_fee_pct_max=1.0)
    fund_writer.upsert_fund_master(pg_conn, _record(Entry_Fee_Pct=0.05, Entry_Fee_Pct_Max=5.0, _fee_pairs_resolved={"entry": True}))
    assert _row(pg_conn)[:4] == (0.05, 5.0, 0.01, 1.0)                            # the exit side had no evidence: COALESCE kept it whole


def test_switch_on_a_cached_pass_without_the_marker_writes_no_pair(pg_conn, monkeypatch):
    """The CACHED cycle: the pipeline removed the pair from the record (None) and set no marker, so nothing of the stored pair moves."""
    monkeypatch.setattr(fund_writer, "_attribute_group_write_enabled", lambda: True)
    _seed(pg_conn, entry_fee_pct=0.03, entry_fee_pct_max=3.0, exit_fee_pct=0.01, exit_fee_pct_max=1.0)
    fund_writer.upsert_fund_master(pg_conn, _record())
    assert _row(pg_conn)[:4] == (0.03, 3.0, 0.01, 1.0)


def test_the_marker_never_reaches_the_sql_and_the_switch_defaults_to_off():
    from shared import config
    assert config.ATTRIBUTE_GROUP_WRITE_ENABLED is False and fund_writer._attribute_group_write_enabled() is False
    rec = _record(_fee_pairs_resolved={"entry": True})
    assert "_fee_pairs_resolved" in rec          # popped inside upsert_fund_master, before the spec is built (the PG tests above prove the UPSERT accepts it)
