# -*- coding: utf-8 -*-
"""
Withdrawal marking in harvest.p1_kiid_sync — FULL vs PARTIAL documentation withdrawal
(FND-0183 follow-up): classify_withdrawals, _mark_withdrawn, _reactivate_returned and the
--retire-orphans command.

Helper functions do not commit (bare pg_conn SAVEPOINT fixture); cmd_retire_orphans commits
internally, so it uses pg_session_conn/pg_conn_module_schema like cmd_backfill_lifecycle.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_HARVEST_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "harvest"))

_spec = importlib.util.spec_from_file_location(
    "p1_kiid_sync_withdrawal_test", os.path.join(_HARVEST_DIR, "p1_kiid_sync.py")
)
_pks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pks)
classify_withdrawals = _pks.classify_withdrawals
_mark_withdrawn = _pks._mark_withdrawn
_reactivate_returned = _pks._reactivate_returned
_lifecycle_activate = _pks._lifecycle_activate
cmd_retire_orphans = _pks.cmd_retire_orphans


def _make_schema(conn):
    conn.execute("""
        CREATE TABLE kiid_lifecycle (
            isin TEXT NOT NULL, start_date DATE NOT NULL, end_date DATE,
            status TEXT NOT NULL DEFAULT 'commercializing', href TEXT, retire_dir TEXT,
            retire_scope TEXT CHECK (retire_scope IS NULL OR retire_scope IN ('FULL','PARTIAL')),
            PRIMARY KEY (isin, start_date))
    """)
    conn.execute("CREATE TABLE fund_master (isin TEXT PRIMARY KEY, in_current_universe INTEGER NOT NULL DEFAULT 1)")
    conn.execute("CREATE TABLE fund_kiid_metadata (isin TEXT, kiid_class INTEGER, kiid_status TEXT)")
    conn.execute("""
        CREATE TABLE db_document_catalogue (
            harvest_ts text NOT NULL, gestora_label text NOT NULL, cod_db text NOT NULL,
            isin varchar(12), link_label text NOT NULL, href text NOT NULL, cod_sus text,
            PRIMARY KEY (harvest_ts, cod_db, href))
    """)
    # latest harvest: OKKIID1 has a KIID row; PARTIAL1 only a LIIC row; FULL1 is absent
    conn.execute("""
        INSERT INTO db_document_catalogue (harvest_ts, gestora_label, cod_db, link_label, href, isin, cod_sus)
        VALUES ('20260101_000000','G','F0','KIID','https://x/0','OLDHARV1','KIID'),
               ('20260201_000000','G','F1','KIID','https://x/1','OKKIID1','KIID'),
               ('20260201_000000','G','F2','LIIC','https://x/2','PARTIAL1','LIIC')
    """)
    for isin in ("OKKIID1", "PARTIAL1", "FULL1"):
        conn.execute("INSERT INTO fund_master (isin) VALUES (%s)", (isin,))
        conn.execute("INSERT INTO fund_kiid_metadata VALUES (%s, 1, 'OK')", (isin,))


def test_classify_withdrawals_full_vs_partial(pg_conn):
    _make_schema(pg_conn)
    res = classify_withdrawals(pg_conn, {"OKKIID1", "PARTIAL1", "FULL1"})
    assert res == {"PARTIAL1": "PARTIAL", "FULL1": "FULL"}


def test_classify_withdrawals_refuses_empty_kiid_harvest(pg_conn):
    _make_schema(pg_conn)
    pg_conn.execute("DELETE FROM db_document_catalogue WHERE cod_sus = 'KIID'")
    with pytest.raises(RuntimeError):
        classify_withdrawals(pg_conn, {"FULL1"})


def test_mark_withdrawn_sets_all_three_marks_and_is_idempotent(pg_conn):
    _make_schema(pg_conn)
    assert _mark_withdrawn(pg_conn, "PARTIAL1", "PARTIAL", "2026-10-03", None) is True
    assert _mark_withdrawn(pg_conn, "PARTIAL1", "PARTIAL", "2026-10-03", None) is False

    assert pg_conn.execute("SELECT in_current_universe FROM fund_master WHERE isin='PARTIAL1'").fetchone()[0] == 0
    assert pg_conn.execute("SELECT kiid_status FROM fund_kiid_metadata WHERE isin='PARTIAL1'").fetchone()[0] == "RETIRED"
    rows = pg_conn.execute("SELECT status, retire_scope, retire_dir FROM kiid_lifecycle WHERE isin='PARTIAL1'").fetchall()
    assert [tuple(r) for r in rows] == [("retired", "PARTIAL", None)]
    assert pg_conn.execute("SELECT in_current_universe FROM fund_master WHERE isin='OKKIID1'").fetchone()[0] == 1


def test_mark_withdrawn_scope_upgrade_partial_to_full_updates_same_row(pg_conn):
    _make_schema(pg_conn)
    _mark_withdrawn(pg_conn, "FULL1", "PARTIAL", "2026-10-03", None)
    _mark_withdrawn(pg_conn, "FULL1", "FULL", "2026-10-10", None)
    rows = pg_conn.execute("SELECT retire_scope FROM kiid_lifecycle WHERE isin='FULL1'").fetchall()
    assert [r[0] for r in rows] == ["FULL"]


def test_mark_withdrawn_closes_active_period_with_scope(pg_conn):
    _make_schema(pg_conn)
    _lifecycle_activate(pg_conn, "FULL1", "https://x/f1", "2026-01-01")
    _mark_withdrawn(pg_conn, "FULL1", "FULL", "2026-10-03", "20261003")
    row = pg_conn.execute(
        "SELECT status, end_date, retire_dir, retire_scope FROM kiid_lifecycle WHERE isin='FULL1'"
    ).fetchone()
    assert (row[0], str(row[1]), row[2], row[3]) == ("retired", "2026-10-03", "20261003", "FULL")


def test_reactivate_returned_flips_retired_back_to_force_refresh(pg_conn):
    _make_schema(pg_conn)
    _mark_withdrawn(pg_conn, "PARTIAL1", "PARTIAL", "2026-10-03", None)
    _mark_withdrawn(pg_conn, "FULL1", "FULL", "2026-10-03", None)
    assert _reactivate_returned(pg_conn, {"PARTIAL1", "OKKIID1"}) == 1
    st = {r[0]: r[1] for r in pg_conn.execute("SELECT isin, kiid_status FROM fund_kiid_metadata").fetchall()}
    assert st["PARTIAL1"] == "FORCE_REFRESH" and st["FULL1"] == "RETIRED" and st["OKKIID1"] == "OK"
    assert _reactivate_returned(pg_conn, set()) == 0


def test_cmd_retire_orphans_marks_funds_without_local_pdf(
    pg_session_conn, pg_conn_module_schema, tmp_path,
):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_schema(conn)
    with patch.object(_pks, "KIID_DIR", tmp_path / "kiid"), \
         patch.object(_pks, "KIID_RETIRED_BASE", tmp_path / "retired"):
        cmd_retire_orphans(None, conn=conn)
        cmd_retire_orphans(None, conn=conn)  # idempotent second pass
    scopes = {r[0]: r[1] for r in conn.execute("SELECT isin, retire_scope FROM kiid_lifecycle").fetchall()}
    assert scopes == {"PARTIAL1": "PARTIAL", "FULL1": "FULL"}
    assert conn.execute("SELECT COUNT(*) FROM fund_master WHERE in_current_universe = 0").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM fund_kiid_metadata WHERE kiid_status = 'RETIRED'").fetchone()[0] == 2
