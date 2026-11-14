# tests/test_lineage_fnd0306_pg.py
# -*- coding: utf-8 -*-
"""
FND-0306 slice 1, hermetic Postgres: the loader SQL of shared/governance/lineage.py against a TEMP fund_metrics (it shadows any real table for the
session, and the savepoint of `pg_conn` rolls it back), with the three events that must and must not move the digest.

    python scripts/gov/delivery/run_pg_tests.py -- tests/test_lineage_fnd0306_pg.py
"""
from shared.governance import lineage as ln

RUN_1, RUN_2 = "P2-20261112_194930-2984d0", "P2-20261113_090000-bbbbbb"


def _seed(conn):
    conn.execute("CREATE TEMP TABLE fund_metrics (isin text, metric text, horizon text, algorithm_version text, batch_id text)")
    for isin, av, batch, n in [("IE0001", "20261112", RUN_1, 3), ("IE0001", "20261112.20261019", RUN_1, 2),
                               ("LU0002", "20261112", RUN_1, 3), ("ES0003", "20261112", RUN_1, 4)]:
        for k in range(n):
            conn.execute("INSERT INTO fund_metrics VALUES (%s, %s, 'since_inception', %s, %s)", (isin, f"m{k}_{av}", av, batch))


def test_the_loader_counts_rows_per_fund_and_pair(pg_conn):
    _seed(pg_conn)
    got = sorted(ln.load_lineage_rows(pg_conn))
    assert got == [("ES0003", "20261112", RUN_1, 4), ("IE0001", "20261112", RUN_1, 3), ("IE0001", "20261112.20261019", RUN_1, 2),
                   ("LU0002", "20261112", RUN_1, 3)]
    assert len(ln.load_lineage_rows(pg_conn, ["IE0001"])) == 2


def test_a_recompute_of_a_scenario_fund_moves_the_scoped_digest_and_one_outside_does_not(pg_conn):
    _seed(pg_conn)
    scope = ["IE0001", "LU0002"]
    before_scope, before_all = ln.compute(pg_conn, scope), ln.compute(pg_conn)
    pg_conn.execute("UPDATE fund_metrics SET batch_id = %s WHERE isin = 'ES0003'", (RUN_2,))      # a fund NOT in the scenario
    assert ln.compute(pg_conn, scope) == before_scope and ln.compute(pg_conn).sha256 != before_all.sha256
    pg_conn.execute("UPDATE fund_metrics SET batch_id = %s WHERE isin = 'LU0002'", (RUN_2,))      # a fund of the scenario
    state, detail = ln.verdict(before_scope, ln.compute(pg_conn, scope))
    assert state == ln.MISMATCH and "pairs 3 -> 3" in detail


def test_a_deletion_without_a_new_stamp_is_seen(pg_conn):
    _seed(pg_conn)
    before = ln.compute(pg_conn, ["IE0001"])
    pg_conn.execute("DELETE FROM fund_metrics WHERE isin = 'IE0001' AND metric = 'm0_20261112'")   # what delete_unproduced does
    state, detail = ln.verdict(before, ln.compute(pg_conn, ["IE0001"]))
    assert state == ln.MISMATCH and "rows 5 -> 4" in detail


def test_the_digest_is_the_same_in_two_computations_over_unchanged_data(pg_conn):
    _seed(pg_conn)
    assert ln.compute(pg_conn) == ln.compute(pg_conn) and ln.verdict(ln.compute(pg_conn, ["IE0001"]), ln.compute(pg_conn, ["IE0001"]))[0] == ln.MATCH
