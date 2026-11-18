"""FND-0430 / P3-1.6: the selection_health monitors (proyecto3/src/selection_health.py). No database, synthetic scores (R-7)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import selection_health as H  # noqa: E402

DATES = pd.date_range("2020-01-31", periods=8, freq="ME")


def world(n_funds=30, mutate=None):
    rows = []
    for t in DATES:
        for k in range(n_funds):
            rows.append((t, "Defensiva", f"ES{k:04d}", ["Monetario", "Mixtos", "Renta Variable"][k % 3], True, 1.0 - 0.01 * k))
    df = pd.DataFrame(rows, columns=["as_of", "subportfolio", "isin", "fund_nature", "eligible", "score_final"])
    return mutate(df) if mutate else df


def managers(n_funds=30, per=5):
    return pd.Series({f"ES{k:04d}": f"M{k // per}" for k in range(n_funds)})


def test_a_stable_world_has_zero_change_and_known_concentration():
    h = H.health_series(world(), managers(), n=10)
    s = h.xs("Defensiva", level="subportfolio")
    assert s["eligible"].eq(30).all()
    assert s["eligible_change"].dropna().eq(0).all() and s["top_turnover"].dropna().eq(0).all()
    assert pd.isna(s["eligible_change"].iloc[0]) and pd.isna(s["top_turnover"].iloc[0])            # the first date has no previous one
    assert s["top_nature_max"].eq(4).all()                                                           # top 10 of the 3-nature cycle: 4 / 3 / 3
    assert s["top_manager_max"].eq(5).all()                                                          # funds 0-4 share manager M0, 5-9 share M1


def test_a_partial_scoring_run_makes_the_eligible_set_change_jump():
    """E-7: the latest run covers only part of the universe. The eligible set loses a third of its funds in one month."""
    def partial(df):
        late = (df["as_of"] == DATES[5]) & (df["isin"].str[2:].astype(int) % 3 == 0)
        return df[~late]
    s = H.health_series(world(mutate=partial), managers(), n=10).xs("Defensiva", level="subportfolio")
    assert s["eligible_change"].iloc[5] == pytest.approx(10 / 30) and s["eligible"].iloc[5] == 20
    assert s["eligible_change"].iloc[6] == pytest.approx(10 / 20)                                    # and the return of the funds shows as a change too
    assert s["eligible_change"].drop(s.index[[5, 6]]).dropna().eq(0).all()


def test_a_reshuffled_ranking_moves_the_top_turnover():
    def shuffle(df):
        late = df["as_of"] >= DATES[4]
        df = df.copy()
        df.loc[late, "score_final"] = 1.0 - 0.01 * (29 - df.loc[late, "isin"].str[2:].astype(int))     # reverses the ranking from month 4
        return df
    s = H.health_series(world(mutate=shuffle), managers(), n=10).xs("Defensiva", level="subportfolio")
    assert s["top_turnover"].iloc[4] == 1.0 and s["top_turnover"].iloc[3] == 0.0 and s["top_turnover"].iloc[5] == 0.0


def test_ineligible_and_zero_score_funds_are_never_in_the_top_set():
    def spoil(df):
        df = df.copy()
        df.loc[df["isin"] == "ES0000", "eligible"] = False
        df.loc[df["isin"] == "ES0001", "score_final"] = 0.0
        return df
    top = H._top_set(world(mutate=spoil).query("as_of == @DATES[0]"), 3)
    assert top == ["ES0002", "ES0003", "ES0004"]


def test_without_managers_the_manager_monitor_is_nan_and_the_rest_works():
    h = H.health_series(world(), None, n=10)
    assert h["top_manager_max"].isna().all() and h["top_nature_max"].notna().all()


def test_calibration_thresholds_and_the_historical_alert_rate():
    rng = np.random.default_rng(0)
    idx = pd.MultiIndex.from_product([pd.date_range("2000-01-31", periods=200, freq="ME"), ["Defensiva"]], names=["as_of", "subportfolio"])
    h = pd.DataFrame({m: rng.random(200) for m in H.MONITORS}, index=idx)
    cal = H.calibrate(h)
    assert cal.loc[("Defensiva", "top_turnover"), "n"] == 200 and 0.9 < cal.loc[("Defensiva", "top_turnover"), "q95"] < 1.0
    lim = H.thresholds(cal, 0.95)
    assert ("Defensiva", "eligible") not in lim and ("Defensiva", "eligible_change") in lim              # a level has no threshold, its change does
    rate = H.historical_alert_rate(h, lim)
    assert (rate - 0.05).abs().max() < 0.03                                                          # about 1 - q by construction
    row = pd.Series({m: 0.0 for m in H.MONITORS})
    row["top_turnover"] = 10.0
    assert [m for m, _v, _t in H.evaluate(row, "Defensiva", lim)] == ["top_turnover"]                   # only the monitor above its threshold is reported
    assert H.evaluate(pd.Series({m: 0.0 for m in H.MONITORS}), "Defensiva", lim) == []


def test_a_monitor_pinned_at_its_ceiling_cannot_fire_and_gets_no_threshold():
    idx = pd.MultiIndex.from_product([pd.date_range("2000-01-31", periods=100, freq="ME"), ["Dinamica"]], names=["as_of", "subportfolio"])
    rng = np.random.default_rng(1)
    h = pd.DataFrame({m: rng.random(100) for m in H.MONITORS}, index=idx)
    h["top_nature_max"] = 10.0                                                                       # the raw top 10 is always one nature
    cal = H.calibrate(h)
    assert H.degenerate(cal) == [("Dinamica", "top_nature_max")]
    assert ("Dinamica", "top_nature_max") not in H.thresholds(cal) and ("Dinamica", "top_turnover") in H.thresholds(cal)
    assert ("Dinamica", "top_nature_max") not in H.historical_alert_rate(h, H.thresholds(cal)).index


def test_the_nan_weight_share_counts_the_weight_on_missing_metrics():
    m = pd.DataFrame({"return_ann_real": [1.0, 1.0, 1.0], "sharpe": [1.0, 1.0, np.nan], "alpha_persistence": [1.0, np.nan, np.nan]}, index=["A", "B", "C"])
    w = {"return_ann_real": 0.5, "sharpe": 0.3, "alpha_persistence": 0.2, "capture_ratio": 0.0}          # capture_ratio has no column and no weight
    s = H.nan_weight_share(m, w)
    assert s.round(6).to_dict() == {"A": 0.0, "B": 0.2, "C": 0.5}
    assert H.nan_weight_share(m, {"return_ann_real": 0.5, "capture_ratio": 0.5}).to_dict() == {"A": 0.5, "B": 0.5, "C": 0.5}   # a metric with no column is missing for everyone
