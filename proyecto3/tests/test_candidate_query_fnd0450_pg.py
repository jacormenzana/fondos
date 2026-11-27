# proyecto3/tests/test_candidate_query_fnd0450_pg.py
# -*- coding: utf-8 -*-
"""FND-0450 (owner order 2026-11-27): the rewrite of score_candidates.load_current_candidates is accepted only if it returns 100 % identical candidates and is measurably
faster. The old query (a SELECT MAX(as_of_date) correlated subquery evaluated per row of the score history) is kept HERE as the specification: over a toy history with
several runs, regimes, versions, blocks, tied scores, ineligible and out-of-universe funds, the new function must return the same rows in the same order, and its plan
must hold no per-row SubPlan. Postgres (run_pg_tests.py)."""
from __future__ import annotations

import pytest

from proyecto3.src.score_candidates import CANDIDATE_COLUMNS, load_current_candidates

OLD_SQL = """
    SELECT fs.block, fs.isin, fs.score_total,
           fm.Fund_Name, fm.Fund_Nature, fm.Management_Company,
           fm.fund_family_id
    FROM fund_scores fs
    JOIN fund_master fm ON fm.ISIN = fs.isin
    WHERE fs.score_version = %s
      AND fs.regime = %s
      AND (%s::text IS NULL OR fs.block = %s::text)
      AND fs.as_of_date = (
          SELECT MAX(cur.as_of_date) FROM fund_scores cur
          WHERE cur.block = fs.block
            AND cur.score_version = fs.score_version
            AND cur.regime = fs.regime)
      AND fs.eligible = 1
      AND fs.score_total > 0
      AND fm.In_Current_Universe = 1
    ORDER BY fs.block, fs.score_total DESC
"""
_SCHEMA = """
CREATE TABLE fund_master (ISIN TEXT PRIMARY KEY, Fund_Name TEXT, Fund_Nature TEXT, Management_Company TEXT, fund_family_id TEXT, In_Current_Universe SMALLINT DEFAULT 1);
CREATE TABLE fund_scores (isin TEXT, block TEXT, score_version TEXT, regime TEXT, as_of_date DATE, score_total REAL, score_detail TEXT, eligible INTEGER,
                          PRIMARY KEY (isin, block, score_version, regime, as_of_date));
CREATE INDEX idx_toy_block ON fund_scores (block, eligible);
"""
BLOCKS = ("Defensiva", "Equilibrada", "Dinamica")


@pytest.fixture
def conn(pg_conn):
    pg_conn.execute(_SCHEMA)
    isins = [f"F{i:03d}" for i in range(40)]
    for i, isin in enumerate(isins):
        pg_conn.execute("INSERT INTO fund_master VALUES (%s, %s, 'Mixtos', %s, %s, %s)",
                        (isin, f"Fund {i}", f"M{i % 7}", None if i % 11 == 0 else f"FAM{i}", 0 if i % 13 == 5 else 1))
    rows = []
    for block_no, block in enumerate(BLOCKS):
        # three runs for (v1, A): the latest differs per block; another regime and another version with LATER dates that must not leak in
        for run_no, date in enumerate(("2026-09-01", "2026-10-01", "2026-11-0%d" % (block_no + 1))):
            for i, isin in enumerate(isins):
                if run_no == 2 and i % 9 == 0:
                    continue                                                          # scored before, not in the latest run: never a candidate
                score = None if i == 3 else (0.0 if i == 4 else round(((i * 7 + run_no + block_no) % 12) / 10.0, 2))    # many ties
                rows.append((isin, block, "v1", "A", date, score, 0 if i % 10 == 1 else 1))
        for i, isin in enumerate(isins[:10]):
            rows.append((isin, block, "v1", "B", "2026-11-20", 1.5, 1))
            rows.append((isin, block, "v2", "A", "2026-11-21", 1.4, 1))
    for r in rows:
        pg_conn.execute("INSERT INTO fund_scores (isin, block, score_version, regime, as_of_date, score_total, eligible) VALUES (%s, %s, %s, %s, %s, %s, %s)", r)
    pg_conn.execute("ANALYZE fund_scores")
    return pg_conn


def _old(conn, version, regime, sub):
    return [tuple(r) for r in conn.execute(OLD_SQL, (version, regime, sub, sub)).fetchall()]


def _new(conn, version, regime, sub):
    frame = load_current_candidates(conn, version, regime, sub)
    assert list(frame.columns) == CANDIDATE_COLUMNS
    return [tuple(r) for r in frame.itertuples(index=False, name=None)]


@pytest.mark.parametrize("version, regime", [("v1", "A"), ("v1", "B"), ("v2", "A"), ("v1", "nothing")])
@pytest.mark.parametrize("sub", [None, *BLOCKS, "NoSuchBlock"])
def test_the_new_query_returns_exactly_the_rows_of_the_old_one(conn, version, regime, sub):
    """Same rows, and the same (block, score) sequence. The order AMONG funds with equal scores was defined by neither query (it follows the plan). Verified on the live
    data on 2026-11-27: identical ordered output for each sub-portfolio (what the builder asks), 49 positions swapped among equal scores in the all-blocks form (the
    backtester), and identical selections and weights from both."""
    new, old = _new(conn, version, regime, sub), _old(conn, version, regime, sub)
    assert sorted(new, key=str) == sorted(old, key=str)
    assert [(r[0], r[2]) for r in new] == [(r[0], r[2]) for r in old]


def test_the_toy_history_exercises_what_it_claims(conn):
    got = _old(conn, "v1", "A", None)
    assert len(got) > 30 and {r[0] for r in got} == set(BLOCKS)
    scores = [(r[0], r[2]) for r in got]
    assert len(scores) > len(set(scores))                                                 # ties are present
    assert not [r for r in got if r[1] in ("F000", "F009", "F003", "F004", "F005")]      # not in the latest run, NULL / zero score, out of universe


class _Spy:
    """Capture the statement the function runs, to ask Postgres for its plan."""
    def __init__(self, conn): self._c, self.sql, self.params = conn, None, None
    def execute(self, sql, params=()):
        self.sql, self.params = sql, params
        return self._c.execute(sql, params)


def test_the_latest_run_is_resolved_once_not_per_row(conn):
    spy = _Spy(conn)
    load_current_candidates(spy, "v1", "A", "Defensiva")
    plan = "\n".join(str(r[0]) for r in conn.execute("EXPLAIN " + spy.sql, spy.params).fetchall())
    assert "SubPlan" not in plan, plan
