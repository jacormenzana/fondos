"""
tests/test_p1p2_cycle_report.py -- the post-cycle report of P1_P2_Complete.bat.

The decisions (what deserves attention, the comparison with the previous report, the rendering) are
pure functions tested here with no database. `collect()` is exercised against a fake connection that
returns canned rows per statement, which proves the wiring (which parameters each query gets, how rows
become the JSON shape); the SQL itself is EXPLAINed against the real DDL by
tests/test_sql_explain_sweep_pg.py (every statement in scripts/launch is swept).
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "p1p2_cycle_report", Path(__file__).resolve().parent.parent / "scripts" / "launch" / "p1p2_cycle_report.py")
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)


def _report(**over):
    r = {
        "stamp": "20261004_100000", "since": "2026-10-04", "today": "2026-10-04",
        "universe": {"active": 1000, "inactive": 50, "total": 1050},
        "nature": {"Renta Variable": 600, "Mixtos": 400},
        "kiid_status": {"OK": 995, "WRONG_DOC": 5},
        "reliability": {"srri_null": 6, "profile_null": 0, "dqf_warn": 70, "dqf_missing": 1},
        "wrong_doc_stale": [], "low_confidence_nature": 3,
        "dq_cycle": [{"check_code": "INFO_A", "level": "INFO", "n": 900},
                     {"check_code": "WARN_B", "level": "WARN", "n": 7}],
        "nav_stale": [], "nav_sources": {"OK/OK": 990, "NOT_FOUND/OK": 10},
        "real_orphans": {}, "coverage": {"sharpe": 980, "return_ann": 980, "momentum_rank": 950},
        "alerts": {"OK": 90, "WARN": 8, "ALARM": 2},
    }
    r.update(over)
    return r


# ─── coverage ────────────────────────────────────────────────────────────────────────────────────

def test_coverage_pct_is_a_share_of_the_active_universe():
    assert cr.coverage_pct(_report()) == {"sharpe": 98.0, "return_ann": 98.0, "momentum_rank": 95.0}


def test_coverage_pct_without_an_active_universe_is_empty_not_a_zero_division():
    assert cr.coverage_pct(_report(universe={"active": 0, "inactive": 0, "total": 0})) == {}


# ─── attention items ─────────────────────────────────────────────────────────────────────────────

def test_a_clean_first_report_has_nothing_to_flag():
    assert cr.attention_items(_report(), None) == []


def test_coverage_drop_beyond_the_threshold_is_flagged_and_a_small_one_is_not():
    prev = _report(coverage={"sharpe": 980, "return_ann": 980, "momentum_rank": 950})
    cur = _report(coverage={"sharpe": 940, "return_ann": 975, "momentum_rank": 950})   # -4.0 pp and -0.5 pp
    items = cr.attention_items(cur, prev)
    assert len(items) == 1 and "sharpe" in items[0] and "4.0 pp" in items[0]


def test_a_metric_that_disappeared_entirely_is_flagged():
    prev = _report()
    cur = _report(coverage={"sharpe": 980, "return_ann": 980})
    assert any("momentum_rank" in i and "ningun fondo" in i for i in cr.attention_items(cur, prev))


def test_coverage_gains_never_flag():
    prev = _report(coverage={"sharpe": 900})
    assert cr.attention_items(_report(), prev) == []


def test_wrong_doc_funds_with_a_non_warn_flag_are_flagged():
    cur = _report(wrong_doc_stale=[{"isin": "LU1", "fund_nature": "Mixtos", "flag": "-"}])
    assert any("WRONG_DOC" in i and "1 fondos" in i for i in cr.attention_items(cur, None))


def test_frozen_navs_are_expected_but_other_stale_navs_are_flagged():
    cur = _report(nav_stale=[{"isin": "A", "newest": "2025-01-31", "data_status": "STALE_FROZEN"}])
    assert cr.attention_items(cur, None) == []
    cur = _report(nav_stale=[{"isin": "A", "newest": "2025-01-31", "data_status": "STALE_FROZEN"},
                             {"isin": "B", "newest": "2026-03-12", "data_status": "OK"}])
    items = cr.attention_items(cur, None)
    assert len(items) == 1 and "1 fondos activos" in items[0]


def test_real_nominal_pairing_gaps_are_flagged_with_the_metric_names():
    items = cr.attention_items(_report(real_orphans={"sharpe": 2, "max_dd": 1}), None)
    assert len(items) == 1 and "3 metricas" in items[0] and "sharpe=2" in items[0]


def test_a_calc_version_backfill_that_ran_no_ols_is_flagged():
    """2026-10-05: the CALC_VERSION bump left every fund 'OLS-fresh' (ols_funds=0) and the beta gate failed."""
    items = cr.attention_items(_report(ols_zero_backfills=["P2-20261005_020156-771354"]), None)
    assert len(items) == 1
    assert "ols_funds=0" in items[0] and "P2-20261005_020156-771354" in items[0] and "beta_shift_audit" in items[0]


def test_no_zero_ols_backfill_flags_nothing_and_old_reports_without_the_key_are_tolerated():
    assert cr.attention_items(_report(ols_zero_backfills=[]), None) == []
    assert "ols_zero_backfills" not in _report()                    # a report written before this check existed
    assert cr.attention_items(_report(), None) == []


def test_the_zero_ols_query_only_matches_drift_backfills_with_an_ols_funds_field():
    sql = cr.Q_OLS_ZERO_BACKFILL
    assert "'RUN_SUMMARY'" in sql and "'BACKFILL_START'" in sql
    assert "ols_funds=0( |$)" in sql                               # 'ols_funds=0' only, never 'ols_funds=05' / '=10'
    assert "starts_with(b.message, 'CALC_VERSION drift')" in sql   # a deliberate --force backfill is not a defect


def test_universe_change_is_reported_with_its_sign():
    prev = _report(universe={"active": 1010, "inactive": 40, "total": 1050})
    assert any("-10" in i for i in cr.attention_items(_report(), prev))


# ─── previous report lookup ──────────────────────────────────────────────────────────────────────

def test_find_previous_is_the_newest_other_report(tmp_path):
    for s in ("20261001_000000", "20261003_000000", "20261004_000000"):
        (tmp_path / f"P1_P2_cycle_report_{s}.json").write_text("{}")
    (tmp_path / "unrelated.json").write_text("{}")
    prev = cr.find_previous(tmp_path, "P1_P2_cycle_report_20261004_000000.json")
    assert prev.name == "P1_P2_cycle_report_20261003_000000.json"


def test_find_previous_is_none_for_the_first_ever_report(tmp_path):
    assert cr.find_previous(tmp_path, "P1_P2_cycle_report_20261004_000000.json") is None
    (tmp_path / "P1_P2_cycle_report_20261004_000000.json").write_text("{}")
    assert cr.find_previous(tmp_path, "P1_P2_cycle_report_20261004_000000.json") is None


def test_load_report_survives_a_missing_or_corrupt_file(tmp_path):
    assert cr.load_report(None) is None
    assert cr.load_report(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert cr.load_report(bad) is None


# ─── rendering ───────────────────────────────────────────────────────────────────────────────────

def test_render_lists_attention_items_with_the_marker_the_launcher_greps_for():
    cur = _report(real_orphans={"sharpe": 2})
    text = cr.render_text(cur, None, cr.attention_items(cur, None))
    assert any(line.startswith("[ATENCION] ") for line in text.splitlines())
    assert "Universo: activos=1000" in text


def test_render_without_findings_says_so_and_has_no_marker_line():
    text = cr.render_text(_report(), None, [])
    assert "Sin puntos de atencion." in text
    assert not any(line.startswith("[ATENCION]") for line in text.splitlines())


def test_render_shows_deltas_against_the_previous_report():
    prev = _report(nature={"Renta Variable": 590, "Mixtos": 400})
    text = cr.render_text(_report(), prev, [])
    assert "(+10)" in text


def test_render_orders_dq_issues_by_actionability_not_by_volume():
    text = cr.render_text(_report(), None, [])
    assert text.index("WARN_B") < text.index("INFO_A")


# ─── collect(): wiring against a fake connection ─────────────────────────────────────────────────

class _FakeConn:
    def __init__(self, rows_by_sql):
        self.rows_by_sql = rows_by_sql
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        rows = self.rows_by_sql[sql]
        return type("Cur", (), {"fetchall": lambda s: rows})()

    def close(self):
        pass


def _canned():
    return {
        cr.Q_UNIVERSE: [(1000, 50, 1050)],
        cr.Q_NATURE: [("Renta Variable", 600), ("Mixtos", 400)],
        cr.Q_KIID_STATUS: [("OK", 995)],
        cr.Q_RELIABILITY: [(6, 0, 70, 1)],
        cr.Q_WRONG_DOC_STALE: [("LU1", "Mixtos", "-")],
        cr.Q_LOW_CONFIDENCE: [(3,)],
        cr.Q_DQ_CYCLE: [("WARN_B", "WARN", 7)],
        cr.Q_NAV_STALE: [("LU2", date(2026, 3, 12), "OK")],
        cr.Q_NAV_SOURCES: [("OK", "OK", 990)],
        cr.Q_REAL_ORPHANS: [],
        cr.Q_COVERAGE: [("sharpe", 980)],
        cr.Q_ALERTS: [("ALARM", 2)],
        cr.Q_OLS_ZERO_BACKFILL: [("P2-20261005_020156-771354",)],
    }


def test_collect_builds_the_json_shape_and_passes_the_cycle_dates_to_the_queries():
    conn = _FakeConn(_canned())
    rep = cr.collect(conn, since=date(2026, 10, 1), today=date(2026, 10, 4))
    assert rep["universe"] == {"active": 1000, "inactive": 50, "total": 1050}
    assert rep["wrong_doc_stale"] == [{"isin": "LU1", "fund_nature": "Mixtos", "flag": "-"}]
    assert rep["nav_stale"] == [{"isin": "LU2", "newest": "2026-03-12", "data_status": "OK"}]
    assert rep["coverage"] == {"sharpe": 980} and rep["alerts"] == {"ALARM": 2}
    params = {sql: p for sql, p in conn.calls if p}
    assert params[cr.Q_DQ_CYCLE] == (date(2026, 10, 1),)
    assert params[cr.Q_LOW_CONFIDENCE] == (date(2026, 10, 1),)
    assert params[cr.Q_NAV_STALE] == (date(2026, 8, 5),)            # today - NAV_STALE_DAYS (60)
    assert rep["ols_zero_backfills"] == ["P2-20261005_020156-771354"]
    assert params[cr.Q_OLS_ZERO_BACKFILL] == (date(2026, 10, 1),)
    json.dumps(rep)                                                 # must be JSON-serialisable


def test_every_query_is_a_static_literal_with_only_percent_s_placeholders():
    names = [n for n in dir(cr) if n.startswith("Q_")]
    assert len(names) == len(_canned()), "a query was added without a canned row set in these tests"
    for name in names:
        sql = getattr(cr, name)
        assert isinstance(sql, str) and "{" not in sql and "?" not in sql, name


def test_the_p3_consumed_metric_list_matches_what_the_scorer_loads():
    scorer = (Path(__file__).resolve().parent.parent / "proyecto3" / "src" / "fund_scorer.py").read_text(encoding="utf-8")
    for metric in ("return_ann", "sharpe", "max_dd", "alpha_persistence", "capture_ratio", "momentum_rank",
                   "fx_contribution_pct", "srri_nav"):
        assert f'"{metric}"' in scorer and f"'{metric}'" in cr.Q_COVERAGE, metric


# ─── main() ──────────────────────────────────────────────────────────────────────────────────────

def _patch_db(monkeypatch, conn=None, boom=False):
    import shared.db

    def get_connection():
        if boom:
            raise ConnectionError("db down")
        return conn

    monkeypatch.setattr(shared.db, "get_connection", get_connection)


def test_main_writes_the_json_and_the_next_run_compares_against_it(tmp_path, monkeypatch, capsys):
    _patch_db(monkeypatch, _FakeConn(_canned()))
    assert cr.main(["--stamp", "20261004_100000", "--since", "2026-10-01", "--out-dir", str(tmp_path)]) == 0
    first = tmp_path / "P1_P2_cycle_report_20261004_100000.json"
    assert json.loads(first.read_text(encoding="utf-8"))["stamp"] == "20261004_100000"
    assert "nada (primer informe)" in capsys.readouterr().out

    assert cr.main(["--stamp", "20261004_110000", "--since", "2026-10-01", "--out-dir", str(tmp_path)]) == 0
    assert "comparado con: 20261004_100000" in capsys.readouterr().out


def test_main_with_an_unreadable_database_exits_1_and_writes_nothing(tmp_path, monkeypatch, capsys):
    _patch_db(monkeypatch, boom=True)
    assert cr.main(["--stamp", "s", "--out-dir", str(tmp_path)]) == cr.RC_DB_UNREADABLE == 1
    assert list(tmp_path.iterdir()) == []
    assert "cycle report skipped" in capsys.readouterr().out


def test_main_rejects_a_bad_since_date_with_rc_4(tmp_path):
    assert cr.main(["--since", "04/10/2026", "--out-dir", str(tmp_path)]) == 4
