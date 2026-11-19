"""FND-0430 / roadmap v4 step 2: the cost check (proyecto3/src/pit_cost_check.py) and the baseline seal (proyecto3/src/pit_seal.py). No database, synthetic series (R-7).

Pins: net = gross - bps x turnover and equals the chained convention of pit_backtest.assemble_table; fund-by-fund fee cost enters from cash, charges purchases at the entry fee and sales at the exit fee and
COUNTS the funds that took the default; the break-even cost is exact on a constructed pair, 0 when the low-turnover variant already wins, None when it never does; the seal refuses a changed or missing file."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import pit_cost_check as C  # noqa: E402
from proyecto3.src import pit_seal as S  # noqa: E402
from proyecto3.src.pit_backtest import monthly_turnover  # noqa: E402

IDX = pd.date_range("2020-01-31", periods=24, freq="ME")


def test_net_series_is_gross_minus_cost_times_turnover():
    g = pd.Series(0.01, index=IDX)
    t = pd.Series(0.3, index=IDX)
    n = C.net_series(g, t, 50.0)
    assert np.allclose(n, 0.01 - 0.005 * 0.3)
    assert np.allclose(C.net_series(g, t, 0.0), g)


def test_grid_stats_return_falls_as_cost_rises():
    rng = np.random.default_rng(0)
    g = pd.Series(rng.normal(0.004, 0.01, len(IDX)), index=IDX)
    t = pd.Series(0.3, index=IDX)
    cash = pd.Series(0.0, index=IDX)
    st = C.grid_stats(g, t, cash)
    assert list(st) == [25.0, 50.0, 100.0, 200.0]
    rets = [st[b]["ann_return"] for b in st]
    assert rets == sorted(rets, reverse=True)


def test_fee_cost_series_enters_from_cash_and_prices_buys_and_sells():
    W = pd.DataFrame({"A": [0.5, 0.5, np.nan], "B": [0.5, np.nan, 0.5]}, index=IDX[:3])
    entry = pd.Series({"A": 0.02, "B": 0.01})
    exit_ = pd.Series({"A": 0.0, "B": 0.005})
    cost, miss = C.fee_cost_series(W, entry, exit_, default_entry=0.03, default_exit=0.0)
    assert cost.iloc[0] == pytest.approx(0.5 * 0.02 + 0.5 * 0.01)             # entering from cash: buys only
    assert cost.iloc[1] == pytest.approx(0.5 * 0.005)                          # B sold at its exit fee, A unchanged
    assert cost.iloc[2] == pytest.approx(0.5 * 0.01 + 0.5 * 0.0)               # B bought back, A sold at its exit fee 0
    assert miss == {"funds": 2, "missing_entry": 0, "missing_exit": 0}


def test_fee_cost_series_counts_funds_that_took_the_default():
    W = pd.DataFrame({"A": [1.0, 1.0], "B": [np.nan, np.nan]}, index=IDX[:2])
    cost, miss = C.fee_cost_series(W, pd.Series({"A": np.nan}), pd.Series({"A": 0.01}), default_entry=0.02, default_exit=0.0)
    assert cost.iloc[0] == pytest.approx(0.02)
    assert miss["missing_entry"] == 2 and miss["missing_exit"] == 1             # entry: A is NaN and B is absent; exit: only B is absent
    assert cost.iloc[1] == pytest.approx(0.0)


def test_fee_cost_series_matches_turnover_at_a_flat_fee():
    rng = np.random.default_rng(1)
    W = pd.DataFrame(rng.dirichlet(np.ones(5), size=len(IDX)), index=IDX, columns=list("ABCDE"))
    W = W.where(W > 0.1)
    flat = pd.Series(0.0025, index=W.columns)
    cost, _ = C.fee_cost_series(W, flat, flat, 0.0025, 0.0025)
    assert np.allclose(cost.dropna(), (0.0025 * monthly_turnover(W)).dropna())


def _pair():
    """A churns (turnover 0.4) with gross 0.0100; B is calm (turnover 0.1) with gross 0.0090: B overtakes A when 0.3 x bps/1e4 >= 0.0010, i.e. at 33.3 bp."""
    ga, ta = pd.Series(0.0100, index=IDX), pd.Series(0.4, index=IDX)
    gb, tb = pd.Series(0.0090, index=IDX), pd.Series(0.1, index=IDX)
    return ga, ta, gb, tb


def test_break_even_is_exact_on_a_constructed_pair():
    ga, ta, gb, tb = _pair()
    cash = pd.Series(0.0, index=IDX)
    # the lower-turnover variant is B; the baseline is A. With flat series the annual return is monotone, so the scan hits the first whole bp at or above 33.33
    be = C.break_even_bps(gb, tb, ga, ta, cash, metric="ann_return", max_bps=200.0, step=1.0)
    assert be == 34.0


def test_break_even_zero_when_the_variant_wins_at_no_cost_and_none_when_it_never_does():
    ga, ta, gb, tb = _pair()
    cash = pd.Series(0.0, index=IDX)
    assert C.break_even_bps(ga, tb, gb, ta, cash, max_bps=50.0) == 0.0          # higher gross AND lower turnover
    assert C.break_even_bps(gb, ta, ga, tb, cash, max_bps=300.0) is None        # lower gross AND higher turnover: never
    with pytest.raises(ValueError):
        C.break_even_bps(ga, ta, gb, tb, cash, metric="nope")


def test_seal_accepts_matching_files_and_refuses_changed_or_missing(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"alpha")
    (tmp_path / "b.txt").write_bytes(b"beta")
    seal = {"a.txt": S.sha256_of(tmp_path / "a.txt"), "b.txt": S.sha256_of(tmp_path / "b.txt")}
    assert set(S.verify_seal(tmp_path, seal)) == {"a.txt", "b.txt"}
    (tmp_path / "b.txt").write_bytes(b"beta!")
    with pytest.raises(S.SealMismatch, match="b.txt"):
        S.verify_seal(tmp_path, seal)
    (tmp_path / "a.txt").unlink()
    with pytest.raises(S.SealMismatch, match="a.txt: missing"):
        S.verify_seal(tmp_path, seal)


def test_the_recorded_seal_covers_every_artifact_the_diagnostics_read():
    assert S.RUN_STAMP == "20261119_202509"
    assert {"manifest.json", "scores_long.parquet", "pit_backtest_table.csv"} <= set(S.SEAL)
    assert all(len(h) == 64 for h in S.SEAL.values())
    assert sum(k.startswith("inputs/") for k in S.SEAL) == 6
