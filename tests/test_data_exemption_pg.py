# tests/test_data_exemption_pg.py
# -*- coding: utf-8 -*-
"""FND-0394: the one database read of the verified-genuine exemptions (shared.data_exemption.load_build_dates) on a real Postgres: it reads the scenario creation dates, and a FAILED read leaves the caller's transaction usable
(SAVEPOINT, tests/test_dialect_coverage.py) and gives no dates, which keeps an expired exemption in its grace build instead of lapsing it unseen.

    python scripts/ops/run_pg_tests.py -- tests/test_data_exemption_pg.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.data_exemption import exemption_state, load_build_dates  # noqa: E402

ENTRY = {"ap": "FND-0403", "since": "2026-11-08", "until": "2027-05-07", "quantities": ["sortino|crisis_2022"]}


def test_the_build_dates_are_the_scenario_creation_dates_and_drive_the_grace(pg_conn):
    pg_conn.execute("INSERT INTO gold.portfolio_scenarios (scenario_id, profile, created_at) VALUES ('t_pre', 'Defensiva', '2027-03-01'), ('t_grace', 'Defensiva', '2027-05-10')")
    dates = load_build_dates(pg_conn)
    assert date(2027, 3, 1) in dates and date(2027, 5, 10) in dates
    assert exemption_state(ENTRY, date(2027, 5, 10), dates) == "GRACE"             # the first build after the expiry
    assert exemption_state(ENTRY, date(2027, 5, 11), dates) == "LAPSED"            # the next one


def test_a_failed_read_gives_no_dates_and_leaves_the_transaction_usable(pg_conn):
    class _Flaky:
        autocommit = pg_conn.autocommit

        def execute(self, sql, *a):
            return pg_conn.execute("SELECT 1/0" if "portfolio_scenarios" in sql else sql, *a)

    assert load_build_dates(_Flaky()) == []
    assert pg_conn.execute("SELECT 41 + 1").fetchone()[0] == 42                      # not InFailedSqlTransaction: the SAVEPOINT was rolled back
