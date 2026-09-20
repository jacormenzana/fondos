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
