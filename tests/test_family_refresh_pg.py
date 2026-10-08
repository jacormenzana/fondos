"""
tests/test_family_refresh_pg.py -- FND-0244 follow-up on a real Postgres (the real db/pg DDL, hermetic container):
the pending selection, the atomic FAMILY_REFRESH_DONE row, the audited P3 bypass and its DDL block.

pg_app_conn = a SAVEPOINT connection that is rolled back, with the application's search_path. Run: python scripts/ops/run_pg_tests.py
"""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "proyecto1" / "core"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared import family_refresh as fr  # noqa: E402
from shared import gate_bypass as gb  # noqa: E402

_spec = importlib.util.spec_from_file_location("migrate_cycle_telemetry_fr", _ROOT / "scripts" / "ops" / "migrate_cycle_telemetry.py")
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

_spec2 = importlib.util.spec_from_file_location("migrate_audit_finding_p3_gate_t", _ROOT / "scripts" / "ops" / "migrate_audit_finding_p3_gate.py")
mig_gate = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(mig_gate)


def _fund(conn, isin, nature="Mixtos", active=1, wrong_doc=False):
    conn.execute("INSERT INTO fund_master (isin, fund_name, fund_nature, heuristic_block, heuristic_core, in_current_universe) "
                 "VALUES (%s, %s, %s, 'mixtos', 1, %s)", (isin, f"FUND {isin}", nature, active))
    if wrong_doc:
        conn.execute("INSERT INTO fund_kiid_metadata (isin, kiid_class, kiid_status) VALUES (%s, 1, 'WRONG_DOC')", (isin,))


def _log(conn, isin, step, ts="2026-10-07T23:01:00+00:00"):
    conn.execute("INSERT INTO ingestion_log (isin, step, status, message, created_at) VALUES (%s, %s, 'OK', 'x', %s)", (isin, step, ts))


def _correct(conn, isin, ts="2026-10-07T23:01:00+00:00"):
    _log(conn, isin, fr.STEP_CORRECTION, ts)


def _done(conn, isin, ts="2026-10-07T23:01:00+00:00"):
    _log(conn, isin, fr.STEP_DONE, ts)


# ---------------------------------------------------------------- pending selection
def test_a_correction_without_a_done_row_is_pending(pg_app_conn):
    c = pg_app_conn
    _fund(c, "FR0000000A1")
    _correct(c, "FR0000000A1")
    assert fr.pending_family_refresh(c) == ["FR0000000A1"]


def test_a_later_done_row_closes_it_and_a_newer_correction_reopens_it(pg_app_conn):
    c = pg_app_conn
    _fund(c, "FR0000000A1")
    _correct(c, "FR0000000A1")
    _done(c, "FR0000000A1")
    assert fr.pending_family_refresh(c) == []
    _correct(c, "FR0000000A1")                         # the builder rewrote the nature again
    assert fr.pending_family_refresh(c) == ["FR0000000A1"]
    _done(c, "FR0000000A1")
    assert fr.pending_family_refresh(c) == []


def test_ordering_is_by_id_not_by_timestamp(pg_app_conn):
    """Second-resolution timestamps tie (and a clock step could invert them); the identity does not."""
    c = pg_app_conn
    _fund(c, "FR0000000A1")
    _correct(c, "FR0000000A1", ts="2026-10-07T23:01:00+00:00")
    _done(c, "FR0000000A1", ts="2026-10-07T23:01:00+00:00")           # same second
    assert fr.pending_family_refresh(c) == []
    _correct(c, "FR0000000A1", ts="2026-10-07T23:01:00+00:00")        # same second again, but a later id
    assert fr.pending_family_refresh(c) == ["FR0000000A1"]


def test_only_active_funds_that_can_be_refreshed_block(pg_app_conn):
    c = pg_app_conn
    _fund(c, "FR0000000A1")                                     # active: pending
    _fund(c, "FR0000000R1", active=0)                           # retired: not reachable by the classifier, feeds nothing
    _fund(c, "FR0000000W1", wrong_doc=True)                     # WRONG_DOC: excluded from every block, could never be refreshed
    for i in ("FR0000000A1", "FR0000000R1", "FR0000000W1"):
        _correct(c, i)
    assert fr.pending_family_refresh(c) == ["FR0000000A1"]
    assert fr.pending_family_refresh(c, active_only=False) == ["FR0000000A1", "FR0000000R1", "FR0000000W1"]


def test_other_steps_and_null_isins_never_count(pg_app_conn):
    c = pg_app_conn
    _fund(c, "FR0000000A1")
    _log(c, "FR0000000A1", "PUBLISH_FUND")
    _log(c, None, fr.STEP_CORRECTION)
    assert fr.pending_family_refresh(c) == []


def test_the_gate_check_over_real_rows(pg_app_conn):
    from proyecto3.src.data_freshness import family_refresh_check
    c = pg_app_conn
    _fund(c, "FR0000000A1")
    _correct(c, "FR0000000A1")
    assert not family_refresh_check(fr.pending_family_refresh(c)).ok
    _done(c, "FR0000000A1")
    assert family_refresh_check(fr.pending_family_refresh(c)).ok


# ---------------------------------------------------------------- the DONE row commits with the fund, or not at all
def _record(isin, nature="Mixtos"):
    return {"ISIN": isin, "Fund_Name": f"FUND {isin}", "Fund_Nature": nature, "Heuristic_Block": "mixtos", "Heuristic_Core": 1}


def test_publish_fund_writes_the_done_row_in_the_same_transaction(pg_app_conn):
    from fund_writer import publish_fund
    c = pg_app_conn
    _fund(c, "FR0000000A1", nature="Renta Variable")
    _correct(c, "FR0000000A1")
    publish_fund(c, _record("FR0000000A1"), None, None, extra_log=[(fr.STEP_DONE, "OK", "nature=Mixtos own_evidence=Renta Variable")])
    assert fr.pending_family_refresh(c) == []
    assert c.execute("SELECT fund_nature FROM fund_master WHERE isin = 'FR0000000A1'").fetchone()[0] == "Mixtos"
    assert c.execute("SELECT count(*) FROM ingestion_log WHERE isin = 'FR0000000A1' AND step = %s", (fr.STEP_DONE,)).fetchone()[0] == 1


def test_a_failing_done_row_rolls_back_the_fund_too(pg_app_conn, monkeypatch):
    """Upsert + DONE row are one unit: if the strict DONE insert fails, the fund is NOT left half refreshed and stays pending."""
    import fund_writer
    c = pg_app_conn
    _fund(c, "FR0000000A1", nature="Renta Variable")
    _correct(c, "FR0000000A1")
    real = fund_writer.log_ingestion

    def failing_on_strict(conn, isin, step, status, message, strict=False):
        if strict:
            raise RuntimeError("DONE row could not be written")
        return real(conn, isin, step, status, message, strict=False)

    monkeypatch.setattr(fund_writer, "log_ingestion", failing_on_strict)
    with pytest.raises(RuntimeError, match="DONE row"):
        fund_writer.publish_fund(c, _record("FR0000000A1"), None, None, extra_log=[(fr.STEP_DONE, "OK", "x")])
    monkeypatch.setattr(fund_writer, "log_ingestion", real)
    assert c.execute("SELECT fund_nature FROM fund_master WHERE isin = 'FR0000000A1'").fetchone()[0] == "Renta Variable"   # upsert rolled back
    assert fr.pending_family_refresh(c) == ["FR0000000A1"]                                                              # and it stays pending


def test_publish_fund_without_extra_log_is_unchanged(pg_app_conn):
    from fund_writer import publish_fund
    c = pg_app_conn
    _fund(c, "FR0000000A1")
    publish_fund(c, _record("FR0000000A1"), None, None)
    assert c.execute("SELECT count(*) FROM ingestion_log WHERE isin = 'FR0000000A1' AND step = 'PUBLISH_FUND'").fetchone()[0] == 1
    assert c.execute("SELECT count(*) FROM ingestion_log WHERE isin = 'FR0000000A1' AND step = %s", (fr.STEP_DONE,)).fetchone()[0] == 0


# ---------------------------------------------------------------- the audited bypass and its DDL
STALE = [("family_refresh_pending", "1 pending: FR0000000A1")]
WHEN = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)


def test_the_ddl_block_is_idempotent_and_admits_p3_gate(pg_app_conn):
    c = pg_app_conn
    block = mig.ddl_block(name="p3_gate_bypass")
    assert block.startswith("-- BEGIN p3_gate_bypass") and block.rstrip().endswith("-- END p3_gate_bypass")
    c.execute(block)
    c.execute(block)                                                       # re-applying completes nothing and fails nothing
    assert mig_gate.existing(c) == {"check": True, "view": True}
    assert gb.audit_finding_admits_p3_gate(c) is True


def _pre_migration(conn):
    """The live state before the owner migration: the two-value CHECK and no view."""
    conn.execute("DROP VIEW IF EXISTS control.v_p3_gate_bypass")
    conn.execute("ALTER TABLE control.audit_finding DROP CONSTRAINT audit_finding_domain_check")
    conn.execute("ALTER TABLE control.audit_finding ADD CONSTRAINT audit_finding_domain_check CHECK (domain IN ('p2_metrics', 'cost_attributes'))")


def test_before_the_migration_the_bypass_goes_to_ingestion_log_only(pg_app_conn):
    c = pg_app_conn
    _pre_migration(c)
    assert gb.audit_finding_admits_p3_gate(c) is False
    assert mig_gate.existing(c) == {"check": False, "view": False}
    _fund(c, "FR0000000A1")
    out = gb.record_gate_bypass(c, STALE, "cartera_x_202610", ["FR0000000A1"], user="owner", host="h1", now=WHEN)
    assert out["ingestion_log"] == 2 and out["audit_finding"] == 0
    rows = c.execute("SELECT isin, status, message FROM ingestion_log WHERE step = 'P3_STALE_BYPASS' ORDER BY id").fetchall()
    assert [r[0] for r in rows] == [None, "FR0000000A1"] and all(r[1] == "ALARM" for r in rows)
    assert "owner@h1" in rows[0][2] and "cartera_x_202610" in rows[0][2]
    assert c.execute("SELECT count(*) FROM audit_finding WHERE domain IN ('p2_metrics','cost_attributes')").fetchone()[0] >= 0   # table intact


def test_after_the_migration_audit_finding_rows_appear_by_themselves_and_the_view_reads_both(pg_app_conn):
    c = pg_app_conn
    _pre_migration(c)
    c.execute(mig.ddl_block(name="p3_gate_bypass"))                         # the owner applies the migration
    _fund(c, "FR0000000A1")
    out = gb.record_gate_bypass(c, STALE + [("nav_p10", "old")], "cartera_x_202610", ["FR0000000A1"], user="owner", host="h1", now=WHEN)
    assert out["ingestion_log"] == 3 and out["audit_finding"] == 2          # one finding per bypassed check, none per fund
    af = c.execute("SELECT domain, block, rule_id, rule_class, severity, group_key, isin FROM audit_finding WHERE domain = 'p3_gate' "
                   "ORDER BY group_key").fetchall()
    assert [tuple(r) for r in af] == [("p3_gate", "GATE", "P3_STALE_BYPASS", "HARD_INVARIANT", "ALARM", "family_refresh_pending", None),
                                      ("p3_gate", "GATE", "P3_STALE_BYPASS", "HARD_INVARIANT", "ALARM", "nav_p10", None)]
    by_source = dict(c.execute("SELECT source, count(*) FROM control.v_p3_gate_bypass GROUP BY source").fetchall())
    assert by_source == {"ingestion_log": 3, "audit_finding": 2}


def test_the_view_works_before_the_check_is_extended(pg_app_conn):
    """The view needs no CHECK change: applying it first is safe."""
    c = pg_app_conn
    _pre_migration(c)
    c.execute("CREATE OR REPLACE VIEW control.v_p3_gate_bypass AS "
              "SELECT 'ingestion_log'::text AS source, created_at AS event_ts, isin, message AS detail FROM control.ingestion_log "
              "WHERE step = 'P3_STALE_BYPASS' UNION ALL SELECT 'audit_finding'::text, detected_at, isin, evidence FROM control.audit_finding "
              "WHERE domain = 'p3_gate'")
    gb.record_gate_bypass(c, STALE, "s", [], user="u", host="h", now=WHEN)
    assert c.execute("SELECT count(*) FROM control.v_p3_gate_bypass").fetchone()[0] == 1


def test_an_unauditable_bypass_raises(pg_app_conn):
    """The caller (p3_build_portfolio) turns this into a refusal: an override that cannot be recorded does not run."""
    c = pg_app_conn
    c.execute("ALTER TABLE control.ingestion_log RENAME TO ingestion_log_gone")
    with pytest.raises(Exception):
        gb.record_gate_bypass(c, STALE, "s", [], user="u", host="h", now=WHEN)


# ---------------------------------------------------------------- the closure script against the real DDL
def _closure():
    spec = importlib.util.spec_from_file_location("verify_family_refresh_pg", _ROOT / "scripts" / "audit" / "verify_family_refresh.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_closure_script_reads_the_real_tables_and_follows_the_three_exit_codes(pg_app_conn, capsys):
    v = _closure()
    c = pg_app_conn
    c.execute("INSERT INTO fund_families (family_id, family_name, fund_nature, n_funds) VALUES ('FAM_T1', 'T1', 'Mixtos', 2)")
    for isin in ("FR0000000A1", "FR0000000B2"):
        _fund(c, isin)
        c.execute("UPDATE fund_master SET fund_family_id = 'FAM_T1', hedging_policy = 'Unhedged', fund_currency = 'EUR', "
                  "profile = 'Balanced' WHERE isin = %s", (isin,))
    _correct(c, "FR0000000A1")
    assert v.main(conn=c) == v.RC_HARD                                  # pending: the DONE row is missing
    assert "family_refresh_pending: 1 active funds: FR0000000A1" in capsys.readouterr().out
    _done(c, "FR0000000A1")
    assert v.main(conn=c) == v.RC_CLEAN                                 # refreshed and equal to its sibling
    c.execute("UPDATE fund_master SET profile = 'Aggressive' WHERE isin = 'FR0000000A1'")
    assert v.main(conn=c) == v.RC_SOFT                                  # a divergent derived attribute: information only
    out = capsys.readouterr().out
    assert "FR0000000A1" in out and "FR0000000B2" in out and "profile" in out and "'Aggressive'" in out and "'Balanced'" in out
    c.execute("UPDATE fund_master SET fund_nature = 'Renta Variable' WHERE isin = 'FR0000000B2'")
    assert v.main(conn=c) == v.RC_HARD                                  # an active family with two natures
