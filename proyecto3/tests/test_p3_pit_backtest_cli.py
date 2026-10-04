# proyecto3/tests/test_p3_pit_backtest_cli.py
# -*- coding: utf-8 -*-
"""
CLI of the PIT backtest (scripts/launch/p3_pit_backtest.py) and Backtester.for_pit -- FND-0159.

The Backtester is replaced by a stub: these tests pin the CLI contract (arguments forwarded, artifact set and
contents, dry-run writes nothing, empty result -> exit 2) and that for_pit never loads the legacy NAV matrix.
No DB (R-7).

    python -m pytest proyecto3/tests/test_p3_pit_backtest_cli.py -v
"""

import importlib.util
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location("p3_pit_backtest_cli", _ROOT / "scripts" / "launch" / "p3_pit_backtest.py")
cli = importlib.util.module_from_spec(_spec)
sys.modules["p3_pit_backtest_cli"] = cli
_spec.loader.exec_module(cli)

from proyecto3.src import backtesting
from proyecto3.src.pit_run import PitRun
from shared.config import REGIME_PUBLICATION_LAG_MONTHS


# ---------------- stubs ----------------

IDX = pd.date_range("2010-01-31", periods=24, freq=pd.offsets.MonthEnd())


def _table():
    rng = np.random.default_rng(0)
    t = pd.DataFrame({"regime": "Expansion", "n_funds": 12, "cash_weight": 0.1,
                      "cash_ret_1m": 0.001, "ret_1m": rng.normal(0.004, 0.02, 24), "bench_1m": rng.normal(0.005, 0.02, 24),
                      "net_turnover_1m": rng.normal(0.004, 0.02, 24), "turnover": 0.1}, index=IDX)
    t.index.name = "date"
    t.attrs["tx_cost_bps"] = 25.0
    return t


class _StubBacktester:
    calls = {}

    def __init__(self, table=None):
        self._table = _table() if table is None else table
        scores = pd.DataFrame({"as_of": IDX[:2], "regime": "Expansion", "isin": ["A", "B"], "subportfolio": "Defensiva",
                               "score_final": [0.5, 0.4], "eligible": [True, True]})
        universe = pd.DataFrame({"regime": "Expansion", "entered": 10, "stale": 1, "young": 0, "scored": 10, "eligible": 8},
                                index=pd.DatetimeIndex(IDX[:2], name="as_of"))
        cov = pd.DataFrame({"funds_with_daily": [5, 6], "evaluable_6m": [4, 5]}, index=IDX[:2])
        self.last_pit_run = PitRun(scores=scores, universe=universe, timings={"risk": 0.1, "scoring": 2.0, "total": 2.1},
                                   cache_hits={"risk": False}, short_coverage=cov)

    @classmethod
    def for_pit(cls, conn):
        return cls._instance

    def run_pit(self, **kwargs):
        type(self).calls["run_pit"] = kwargs
        return self._table

    def pit_cost_sensitivity(self, bps=(0.0, 25.0, 50.0), entry_sides=1):
        type(self).calls["grid"] = (tuple(bps), entry_sides)
        return pd.DataFrame({"bps_per_side": list(bps), "window_m": 1, "mean_ret": 0.01})

    def pit_hysteresis_experiment(self, bands=(0.0, 0.05)):
        type(self).calls["hysteresis"] = tuple(bands)
        return pd.DataFrame({"hysteresis_band": list(bands), "mean_turnover": 0.2, "annual_cost_drag": 0.006, "ann_return": 0.04,
                             "sharpe": 0.6, "max_drawdown": -0.15, "mean_excess_12m": 0.01})

    def summary(self, table):
        return "BACKTESTING P3 -- stub summary"


class _Conn:
    def __init__(self, count=3737):
        self.sql, self.count = [], count

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))
        outer = self

        class _R:
            def fetchall(self_inner):
                return [("S1",), ("S2",)]

            def fetchone(self_inner):
                return (outer.count,)
        return _R()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "LOG_DIR", tmp_path / "log")
    _StubBacktester.calls = {}
    _StubBacktester._instance = _StubBacktester()
    monkeypatch.setattr(cli, "Backtester", _StubBacktester)
    return tmp_path


# ---------------- arguments ----------------

def test_defaults_match_the_documented_contract():
    a = cli.build_parser().parse_args([])
    assert (a.start, a.end, a.bps_main, a.entry_sides, a.max_stale_days) == ("2005-01-31", None, 25.0, 1, 45)
    assert a.hysteresis_band == 0.0 and a.hysteresis_grid == "0,0.05,0.10,0.20,0.40"
    assert a.grid_bps == "0,25,50" and not a.no_cache and not a.dry_run and not a.current_universe_only
    assert a.isin_file is None and a.isins is None and a.sample_per_nature is None


def test_scope_options_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--isins", "A", "--sample-per-nature", "4"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--entry-sides", "3"])


def test_isin_file_ignores_blanks_and_comments(tmp_path):
    f = tmp_path / "isins.txt"
    f.write_text("# sample\nLU001\n\n  LU002  # inline\nLU003\n", encoding="utf-8")
    assert cli.read_isin_file(str(f)) == ["LU001", "LU002", "LU003"]


def test_resolve_isins_variants():
    args = cli.build_parser().parse_args([])
    assert cli.resolve_isins(_Conn(), args) is None                                  # no scope = whole universe
    assert cli.resolve_isins(_Conn(), cli.build_parser().parse_args(["--isins", "A, B ,,C"])) == ["A", "B", "C"]
    conn = _Conn()
    sample = cli.resolve_isins(conn, cli.build_parser().parse_args(["--sample-per-nature", "4"]))
    assert sample == ["S1", "S2"] and conn.sql[0][1] == (4,)                         # parametrised, 4 per nature
    assert "md5(fm.isin)" in conn.sql[0][0] and "in_current_universe = 1" in conn.sql[0][0]


# ---------------- run behaviour ----------------

def test_dry_run_computes_and_writes_nothing(env):
    out = env / "out"
    rc = cli.main(["--dry-run", "--isins", "A,B", "--out-dir", str(out)], conn=_Conn())
    assert rc == 0 and not out.exists() and _StubBacktester.calls == {}


def test_full_universe_run_logs_the_expected_duration_and_announces_scope(env):
    cli.main(["--dry-run"], conn=_Conn(count=3737))
    text = next((env / "log").glob("log_P3_pitBacktest_*.log")).read_text(encoding="utf-8")   # main() owns the handlers
    assert "FULL UNIVERSE" in text and "3737" in text and "20-25 min" in text


def test_run_forwards_arguments_and_writes_the_artifact_set(env):
    out = env / "out"
    rc = cli.main(["--isins", "A,B", "--start", "2011-01-31", "--end", "2011-12-31", "--bps-main", "50", "--grid-bps", "0,10",
                   "--entry-sides", "2", "--max-stale-days", "60", "--current-universe-only", "--no-cache",
                   "--hysteresis-band", "0.1", "--hysteresis-grid", "0,0.3",
                   "--cache-dir", str(env / "cache"), "--out-dir", str(out)], conn=_Conn())
    assert rc == 0
    kw = _StubBacktester.calls["run_pit"]
    assert kw["isins"] == ["A", "B"] and kw["start_date"] == "2011-01-31" and kw["end_date"] == "2011-12-31"
    assert kw["tx_cost_bps"] == 50 and kw["entry_sides"] == 2 and kw["max_stale_days"] == 60
    assert kw["current_universe_only"] is True and kw["use_cache"] is False and kw["cache_dir"] == str(env / "cache")
    assert kw["hysteresis_band"] == 0.1
    assert _StubBacktester.calls["grid"] == ((0.0, 10.0), 2) and _StubBacktester.calls["hysteresis"] == (0.0, 0.3)
    expected = {"summary.txt", "pit_backtest_table.csv", "pit_backtest_table.parquet", "cost_sensitivity.csv", "hysteresis_experiment.csv",
                "series_stats.json",
                "universe_by_date.csv", "short_gate_coverage.csv", "scores_long.parquet", "timings.json", "manifest.json"}
    assert {p.name for p in out.iterdir()} == expected


def test_artifact_contents(env):
    out = env / "out"
    cli.main(["--isins", "A,B", "--out-dir", str(out)], conn=_Conn())
    pd.testing.assert_frame_equal(pd.read_parquet(out / "pit_backtest_table.parquet"), _table(), check_freq=False)
    assert len(pd.read_csv(out / "pit_backtest_table.csv")) == 24
    assert set(pd.read_csv(out / "cost_sensitivity.csv")["bps_per_side"]) == {0.0, 25.0, 50.0}
    stats = json.loads((out / "series_stats.json").read_text(encoding="utf-8"))
    assert set(stats) == {"portfolio", "benchmark"} and {"sharpe", "ann_return", "max_drawdown"} <= set(stats["portfolio"])
    assert (out / "summary.txt").read_text(encoding="utf-8") == "BACKTESTING P3 -- stub summary"
    timings = json.loads((out / "timings.json").read_text(encoding="utf-8"))
    assert timings["seconds"]["total"] == 2.1 and timings["cache_hits"] == {"risk": False}
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scope"] == "2 ISINs" and manifest["dates"] == ["2010-01-31", "2011-12-31", 24]
    assert manifest["regime_publication_lags_months"] == REGIME_PUBLICATION_LAG_MONTHS and manifest["git_commit"]
    assert "manifest.json" in manifest["artifacts"] and manifest["args"]["bps_main"] == 25.0


def test_hysteresis_grid_can_be_skipped_and_is_written_by_default(env):
    out = env / "out"
    cli.main(["--isins", "A", "--out-dir", str(out)], conn=_Conn())
    exp = pd.read_csv(out / "hysteresis_experiment.csv")
    assert list(exp["hysteresis_band"]) == [0.0, 0.05, 0.1, 0.2, 0.4] and "mean_turnover" in exp.columns
    assert _StubBacktester.calls["run_pit"]["hysteresis_band"] == 0.0
    out2 = env / "out2"
    _StubBacktester.calls = {}
    cli.main(["--isins", "A", "--out-dir", str(out2), "--hysteresis-grid", ""], conn=_Conn())
    assert not (out2 / "hysteresis_experiment.csv").exists() and "hysteresis" not in _StubBacktester.calls
    assert "hysteresis_experiment.csv" not in json.loads((out2 / "manifest.json").read_text(encoding="utf-8"))["artifacts"]


def test_no_scores_flag_skips_the_large_file(env):
    out = env / "out"
    cli.main(["--isins", "A", "--no-scores", "--out-dir", str(out)], conn=_Conn())
    assert not (out / "scores_long.parquet").exists() and (out / "manifest.json").exists()


def test_empty_result_exits_with_code_2_and_writes_no_artifacts(env):
    _StubBacktester._instance = _StubBacktester(table=pd.DataFrame())
    out = env / "out"
    rc = cli.main(["--isins", "A", "--out-dir", str(out)], conn=_Conn())
    assert rc == cli.EXIT_NOTHING_TO_EVALUATE == 2 and not out.exists()


def test_a_log_file_is_created_per_run(env):
    cli.main(["--dry-run", "--isins", "A"], conn=_Conn())
    logs = list((env / "log").glob("log_P3_pitBacktest_*.log"))
    assert len(logs) == 1 and "P3 PIT backtest" in logs[0].read_text(encoding="utf-8")


# ---------------- Backtester.for_pit ----------------

def test_for_pit_does_not_touch_the_database_or_load_the_legacy_matrix(monkeypatch):
    seen = {}

    class _Clf:
        def __init__(self, conn, publication_lags=None):
            seen["lags"] = publication_lags

    class _NoDb:
        def execute(self, *a, **k):
            raise AssertionError("for_pit must not query the DB")

    monkeypatch.setattr(backtesting, "RegimeClassifier", _Clf)
    bt = backtesting.Backtester.for_pit(_NoDb())
    assert bt._nav is None and bt._cash is None and bt._selection_cache == {}
    assert seen["lags"] == REGIME_PUBLICATION_LAG_MONTHS
    backtesting.Backtester.for_pit(_NoDb(), publication_lags={})
    assert seen["lags"] == {}                                                       # explicit {} disables the lag


# ---------------- --flag overrides (attribute an effect to one P2 fix) ----------------

def test_flag_override_sets_only_the_named_flags_and_returns_the_effective_state(monkeypatch):
    from shared import config
    for name in config.P2_BUNDLE_FLAGS:
        monkeypatch.setattr(config, name, True)
    state = cli.apply_flag_overrides(["CAPTURE_MONTH_END_ENABLED=0", "PERSISTENCE_FIRST_LAST_NAV_ENABLED=true"])
    assert state["CAPTURE_MONTH_END_ENABLED"] is False and config.CAPTURE_MONTH_END_ENABLED is False
    assert state["PERSISTENCE_FIRST_LAST_NAV_ENABLED"] is True and config.MACRO_VIF_ITERATIVE_ENABLED is True
    assert set(state) == set(config.P2_BUNDLE_FLAGS)


def test_flag_override_without_specs_changes_nothing(monkeypatch):
    from shared import config
    before = {n: getattr(config, n) for n in config.P2_BUNDLE_FLAGS}
    assert cli.apply_flag_overrides([]) == before and cli.apply_flag_overrides(None) == before


@pytest.mark.parametrize("spec", ["NOT_A_FLAG=1", "CAPTURE_MONTH_END_ENABLED=maybe", "CAPTURE_MONTH_END_ENABLED", "ROLLING_STATS_ENABLED=0"])
def test_flag_override_rejects_unknown_names_and_bad_values(spec):
    with pytest.raises(SystemExit):
        cli.apply_flag_overrides([spec])


def test_the_parser_collects_repeated_flags():
    args = cli.build_parser().parse_args(["--flag", "CAPTURE_MONTH_END_ENABLED=0", "--flag", "PERSISTENCE_FIRST_LAST_NAV_ENABLED=1"])
    assert args.flag == ["CAPTURE_MONTH_END_ENABLED=0", "PERSISTENCE_FIRST_LAST_NAV_ENABLED=1"]
