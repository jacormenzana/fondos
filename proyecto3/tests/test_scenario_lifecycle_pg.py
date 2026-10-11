# proyecto3/tests/test_scenario_lifecycle_pg.py
# -*- coding: utf-8 -*-
"""
Scenario lifecycle on Postgres (owner decision 2026-10-11): the promotion transaction and the report resolution, run on toy tables carrying
the migration's own DDL block (see scenario_pg_support.py). The DDL itself is covered by test_scenario_status_ddl_pg.py and the builder
rules by test_scenario_builder_pg.py.

    python scripts/ops/run_pg_tests.py -- proyecto3/tests/test_scenario_lifecycle_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenario_pg_support import db, statuses  # noqa: E402,F401  (db is a fixture)

from proyecto3.src import scenario_lifecycle as lc  # noqa: E402


def test_promotion_is_atomic_demotes_the_previous_and_leaves_an_audit_row(db):
    db.execute("UPDATE portfolio_scenarios SET status = 'PRODUCTION' WHERE scenario_id = 'legacy_b'")
    db.commit()
    prev = lc.promote(db, "legacy_c", "owner", "non-EUR exposure 12.0%")
    assert prev == "legacy_b"
    assert statuses(db) == {"legacy_a": "ARCHIVED", "legacy_b": "ARCHIVED", "legacy_c": "PRODUCTION"}
    row = db.execute("SELECT promoted_by, supersedes, promoted_at IS NOT NULL FROM portfolio_scenarios WHERE scenario_id='legacy_c'").fetchone()
    assert tuple(row) == ("owner", "legacy_b", True)
    log = db.execute("SELECT step, status, message FROM ingestion_log").fetchall()
    assert len(log) == 1 and log[0][0] == "P3_SCENARIO_PROMOTED" and "supersedes legacy_b" in log[0][2] and "12.0%" in log[0][2]


def test_a_failed_promotion_changes_nothing(db):
    db.execute("UPDATE portfolio_scenarios SET status = 'PRODUCTION' WHERE scenario_id = 'legacy_b'")
    db.commit()
    with pytest.raises(lc.ScenarioLifecycleError):
        lc.promote(db, "legacy_a", "owner")         # ARCHIVED, not a DRAFT: refused AFTER the demotion statement ran
    assert statuses(db) == {"legacy_a": "ARCHIVED", "legacy_b": "PRODUCTION", "legacy_c": "DRAFT"}
    assert db.execute("SELECT COUNT(*) FROM ingestion_log").fetchone()[0] == 0
    with pytest.raises(lc.ScenarioLifecycleError):
        lc.promote(db, "does_not_exist", "owner")
    assert statuses(db)["legacy_b"] == "PRODUCTION"


def test_report_resolution_on_real_queries(db):
    with pytest.raises(lc.NoProductionScenario, match="legacy_c"):                  # a draft exists, nothing in production
        lc.resolve_report_scenario(db)
    lc.promote(db, "legacy_c", "owner")
    assert lc.resolve_report_scenario(db) == ("legacy_c", True)
    assert lc.resolve_report_scenario(db, "legacy_a") == ("legacy_a", False)
    assert lc.production_scenario_id(db, exclude_id="legacy_c") is None


def test_newest_draft_is_ordered_by_built_at_not_by_id_or_calendar_day(db):
    for sid in ("zzz_regime_b1", "aaa_regime_b2"):                                  # ids that sort AGAINST the build order, same day
        db.execute("INSERT INTO portfolio_scenarios (scenario_id, profile, created_at) VALUES (%s, 'Defensiva', '2026-10-11')", (sid,))
    db.execute("UPDATE portfolio_scenarios SET built_at = '2026-10-11 09:00+00' WHERE scenario_id = 'zzz_regime_b1'")
    db.execute("UPDATE portfolio_scenarios SET built_at = '2026-10-11 17:30+00' WHERE scenario_id = 'aaa_regime_b2'")
    db.commit()
    assert lc.latest_drafts(db, 5) == ["aaa_regime_b2", "zzz_regime_b1", "legacy_c"]
    assert lc.latest_draft_id(db) == "aaa_regime_b2"


def test_a_refused_promotion_leaves_exactly_one_audit_row_and_changes_no_status(db):
    before = statuses(db)
    lc.record_blocked(db, "legacy_c", "owner", ["exposicion no-EUR 79.0% excede el presupuesto 30%", "liquidez estructural 12.0%"])
    rows = db.execute("SELECT step, status, message FROM ingestion_log").fetchall()
    assert len(rows) == 1 and rows[0][0] == "P3_SCENARIO_PROMOTION_BLOCKED" and rows[0][1] == "BLOCKED"
    assert "legacy_c" in rows[0][2] and "79.0%" in rows[0][2] and "owner" in rows[0][2]
    assert statuses(db) == before
