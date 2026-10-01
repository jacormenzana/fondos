# proyecto3/tests/test_fund_scorer_persist_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression test for
src/fund_scorer.py::_persist_scores(). Commits internally, so this uses
pg_conn_module_schema/pg_session_conn -- never bare pg_conn/SAVEPOINT -- same discipline as every
other commit-owning function in this migration. This module had zero test coverage for the
persistence path before this port (test_fund_scorer_normalize.py only covers the pure-function
normalization helpers).

gold.fund_scores.score_detail is jsonb in the target schema (db/pg/30_gold.sql) -- the ported SQL
casts the json.dumps()'d text with ::jsonb.
"""
from __future__ import annotations

import contextlib

import pandas as pd

from proyecto3.src.fund_scorer import _persist_scores


def _make_fund_scores(conn):
    conn.execute("""
        CREATE TABLE fund_scores (
            isin              varchar(12) NOT NULL,
            block             text        NOT NULL,
            score_version     text        NOT NULL DEFAULT 'v1',
            regime            text        NOT NULL,
            as_of_date        date        NOT NULL,
            score_total       double precision,
            score_base        double precision,
            multiplier        double precision,
            score_detail      jsonb,
            eligible          smallint    NOT NULL DEFAULT 0,
            exclusion_reason  text,
            calculated_at     date        NOT NULL,
            notes             text,
            PRIMARY KEY (isin, block, score_version, regime, as_of_date)
        )
    """)


def _df(isin="ES0001", score_final=0.75, eligible=True, exclusion_reason=None):
    return pd.DataFrame([{
        "isin": isin, "subportfolio": "Defensiva",
        "score_final": score_final, "score_base": 0.70, "multiplier": 1.05,
        "detail": {"return_ann_real": 0.03, "sharpe": 1.1},
        "eligible": eligible, "exclusion_reason": exclusion_reason,
    }])


def test_persist_scores_upsert_and_jsonb_roundtrip(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_scores(conn)

    _persist_scores(conn, _df(score_final=0.75), regime="Expansion", score_version="v1")
    row = conn.execute(
        "SELECT score_total, eligible, score_detail FROM fund_scores "
        "WHERE isin='ES0001' AND block='Defensiva' AND regime='Expansion'"
    ).fetchone()
    assert row[0] == 0.75
    assert row[1] == 1
    assert row[2] == {"return_ann_real": 0.03, "sharpe": 1.1}

    # Same PK, rerun with a revised score -> must overwrite (ON CONFLICT DO UPDATE), not duplicate.
    _persist_scores(conn, _df(score_final=0.82, eligible=False, exclusion_reason="max_drawdown"),
                     regime="Expansion", score_version="v1")
    rows = conn.execute(
        "SELECT score_total, eligible, exclusion_reason FROM fund_scores "
        "WHERE isin='ES0001' AND block='Defensiva' AND regime='Expansion'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0] == (0.82, 0, "max_drawdown")

    # A different regime is a different PK member -> coexists, not overwritten.
    _persist_scores(conn, _df(score_final=0.60), regime="Crisis_Financiera", score_version="v1")
    count = conn.execute("SELECT COUNT(*) FROM fund_scores WHERE isin='ES0001'").fetchone()[0]
    assert count == 2


# ---- FND-0172: a scoring run is authoritative for its (version, regime, as_of_date) ----------

@contextlib.contextmanager
def _transactional(conn):
    """pg_conn_module_schema leaves the connection in AUTOCOMMIT, where each statement commits by
    itself and no code can be atomic. Production (shared.db.get_connection) keeps psycopg's default
    autocommit=False, so run these tests the same way, and hand the fixture back what it expects."""
    conn.commit()
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.autocommit = True


def _df_many(*isins, score_final=0.75):
    return pd.concat([_df(isin=i, score_final=score_final) for i in isins], ignore_index=True)


def _seed_old_row(conn, isin, as_of, regime="Expansion"):
    conn.execute(
        "INSERT INTO fund_scores (isin, block, score_version, regime, as_of_date, score_total, "
        "eligible, calculated_at) VALUES (%s, 'Defensiva', 'v1', %s, %s, 0.9, 1, %s)",
        (isin, regime, as_of, as_of),
    )
    conn.commit()


def test_rerun_same_day_drops_funds_the_new_run_no_longer_scores(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_scores(conn)
    _seed_old_row(conn, "OLDDAY", "2026-03-21")                      # another date: history
    _seed_old_row(conn, "OTHERREG", "2026-10-01", regime="Crisis_Financiera")  # another regime

    with _transactional(conn):
        _persist_scores(conn, _df_many("ES0001", "ES0002", "ES0003"), regime="Expansion", score_version="v1")
        today = conn.execute("SELECT MAX(as_of_date) FROM fund_scores WHERE regime='Expansion'").fetchone()[0]

        # Same day rerun: ES0002/ES0003 no longer get scored (e.g. their metrics were cleared).
        _persist_scores(conn, _df_many("ES0001", score_final=0.5), regime="Expansion", score_version="v1")
        rows = conn.execute(
            "SELECT isin, score_total FROM fund_scores WHERE regime='Expansion' AND as_of_date=%s", (today,)
        ).fetchall()
        assert [tuple(r) for r in rows] == [("ES0001", 0.5)]
        # History and other regimes are untouched.
        assert conn.execute("SELECT COUNT(*) FROM fund_scores WHERE isin='OLDDAY'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fund_scores WHERE isin='OTHERREG'").fetchone()[0] == 1


def test_failed_insert_rolls_back_and_keeps_the_previous_run(pg_session_conn, pg_conn_module_schema):
    import pytest
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_scores(conn)

    with _transactional(conn):
        _persist_scores(conn, _df_many("ES0001", "ES0002"), regime="Expansion", score_version="v1")

        bad = _df_many("ES0001").astype({"score_final": object})
        bad.loc[0, "score_final"] = "not-a-number"
        with pytest.raises(Exception):
            _persist_scores(conn, bad, regime="Expansion", score_version="v1")

        # The DELETE ran in the same transaction as the failed INSERT: both are rolled back.
        assert conn.execute("SELECT COUNT(*) FROM fund_scores WHERE regime='Expansion'").fetchone()[0] == 2


def test_empty_input_deletes_nothing(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_scores(conn)

    with _transactional(conn):
        _persist_scores(conn, _df_many("ES0001", "ES0002"), regime="Expansion", score_version="v1")

        _persist_scores(conn, _df_many("ES0001").iloc[0:0], regime="Expansion", score_version="v1")
        assert conn.execute("SELECT COUNT(*) FROM fund_scores WHERE regime='Expansion'").fetchone()[0] == 2
