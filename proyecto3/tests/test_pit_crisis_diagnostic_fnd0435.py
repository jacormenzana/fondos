"""FND-0435: pure pieces of the turning-point diagnostic (proyecto3/src/pit_crisis_diagnostic.py). Synthetic frames and a throw-away git repo only (R-7): no database, no sealed run.
Invariants pinned: (1) the R9 date lock refuses every locked stress episode until PREREG_P3_5A.md is in git HEAD (a working-tree copy does not count; unreadable git = locked), design and
descriptive dates are never refused; (2) the hindsight bounds of D0 match hand-computed cases; (3) the module reads no database and fills no missing value silently."""
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import pit_crisis_diagnostic as D  # noqa: E402

SRC = ROOT / "proyecto3" / "src" / "pit_crisis_diagnostic.py"
IDX = pd.DatetimeIndex(["2007-12-31", "2008-01-31", "2008-02-29"])


def pools(din=(0.02, -0.20, 0.30), idx=IDX):
    return pd.DataFrame({"Defensiva": [0.01, 0.01, 0.01], "Equilibrada": [-0.005] * 3, "Dinamica": list(din)}, index=idx)


# ---- date lock ----------------------------------------------------------------------------------------------------------------

def test_design_and_descriptive_dates_are_never_locked():
    for d in D.DESIGN_TOP + D.DESIGN_BOTTOM + D.DESCRIPTIVE_2020:
        assert D.locked_episode(d) is None
    assert len(D.DESIGN_BOTTOM) == 9 and D.DESIGN_BOTTOM[0] == "2008-10-31" and D.DESIGN_BOTTOM[-1] == "2009-06-30"
    D.assert_dates_allowed(list(D.DESIGN_TOP) + list(D.DESIGN_BOTTOM) + list(D.DESCRIPTIVE_2020), prereg_committed=False)


@pytest.mark.parametrize("date,episode", [("2015-08-31", "2015-07/08"), ("2015-12-31", "2015-11..2016-01"), ("2018-11-30", "2018-11"), ("2022-06-30", "2022-01..2023-01"),
                                          ("2023-01-31", "2022-01..2023-01"), ("2025-03-31", "2025-03"), ("2026-02-28", "2026-02")])
def test_every_locked_episode_is_refused_until_the_prereg_is_committed(date, episode):
    assert D.locked_episode(date) == episode
    with pytest.raises(D.LockedEpisode, match=episode.replace("..", r"\.\.")):
        D.assert_dates_allowed(["2008-06-30", date], prereg_committed=False)
    D.assert_dates_allowed(["2008-06-30", date], prereg_committed=True)


def test_prereg_counts_only_when_committed_in_head(tmp_path):
    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "doc").mkdir()
    (tmp_path / "doc" / "other.md").write_text("x")
    git("add", "."); git("commit", "-qm", "base")
    assert D.prereg_in_git_head(tmp_path) is False
    (tmp_path / "doc" / D.PREREG_NAME).write_text("draft")
    assert D.prereg_in_git_head(tmp_path) is False                          # a working-tree copy is not a preregistration
    git("add", "."); git("commit", "-qm", "prereg")
    assert D.prereg_in_git_head(tmp_path) is True


def test_unreadable_git_means_locked(tmp_path):
    assert D.prereg_in_git_head(tmp_path / "missing") is False
    assert D.prereg_in_git_head(tmp_path) is False                          # not a repository


# ---- lever lock: the recovery months (Package L step C0, FND-0436 plan rev. 3.1, issue I6) -----------------------------------------------------------
# The baseline lock covers the drawdown windows only. A LEVER (relative drawdown filter, recovery modulation) acts in the months after the trough, so a lever output is also locked for
# `LEVER_RECOVERY_MONTHS` after the end of each episode (a superset of trough + 12 months: the trough lies inside the episode). Baseline reads keep the narrower lock.

@pytest.mark.parametrize("date,episode", [("2016-06-30", "2015-07/08"), ("2019-11-30", "2018-11"), ("2023-06-30", "2022-01..2023-01"), ("2025-09-30", "2025-03"), ("2026-12-31", "2026-02")])
def test_a_recovery_month_is_open_to_the_baseline_but_locked_to_a_lever(date, episode):
    assert D.locked_episode(date) is None                                        # the baseline lock does not cover the recovery
    assert episode in D.lever_locked_episodes(date)
    with pytest.raises(D.LockedEpisode, match="lever"):
        D.assert_lever_dates_allowed(["2008-06-30", date], prereg_committed=False)
    D.assert_lever_dates_allowed(["2008-06-30", date], prereg_committed=True)


def test_the_lever_lock_ends_twelve_months_after_the_episode_and_contains_the_baseline_lock():
    assert D.LEVER_RECOVERY_MONTHS == 12
    assert D.lever_locked_episodes("2019-11-30") == ["2018-11"] and D.lever_locked_episodes("2019-12-31") == []        # inclusive end, then open
    for name, (lo, hi) in D.LOCKED_EPISODES.items():
        for d in (lo, hi):
            assert name in D.lever_locked_episodes(d)                              # every date the baseline refuses, the lever lock refuses too


def test_design_and_descriptive_dates_are_never_lever_locked():
    for d in D.DESIGN_TOP + D.DESIGN_BOTTOM + D.DESCRIPTIVE_2020:
        assert D.lever_locked_episodes(d) == []
    D.assert_lever_dates_allowed(list(D.DESIGN_TOP) + list(D.DESIGN_BOTTOM) + list(D.DESCRIPTIVE_2020), prereg_committed=False)


# ---- D0 hindsight bounds -----------------------------------------------------------------------------------------------------

def test_best_static_mix_under_the_drawdown_limit_is_hand_computed():
    """Defensiva +1 %/month, Equilibrada -0.5 %, Dinamica [+2 %, -20 %, +30 %]. Drawdown of a mix with Dinamica weight w is 0.01 - 0.21 w (the loss month right after the peak):
    w <= 0.76 keeps it above -15 %, w = 0.7 on the 0.1 grid; its three-month growth 1.017 * 0.863 * 1.213 beats w = 0.6 (1.0634) and the all-Defensiva mix (1.0303)."""
    best = D.best_static_mix(pools())
    assert (best["Defensiva"], best["Equilibrada"], best["Dinamica"]) == (0.3, 0.0, 0.7)
    assert best["max_dd"] == pytest.approx(0.003 - 0.14, abs=1e-9)
    assert best["ann"] == pytest.approx((1.017 * 0.863 * 1.213) ** 4 - 1, abs=1e-9)
    assert D.best_static_mix(pools(), dd_limit=-0.05)["Dinamica"] <= 0.2          # a tighter limit shrinks the risky weight (0.01 - 0.21 w >= -0.05)


def test_no_mix_reaching_the_limit_gives_nan_not_an_error():
    S = pools(din=(0.0, -0.5, 0.0)).assign(Defensiva=[0.0, -0.4, 0.0], Equilibrada=[0.0, -0.4, 0.0])
    assert np.isnan(D.best_static_mix(S)["ann"])


def test_remove_window_and_halve_negatives():
    S = pd.DataFrame({"Defensiva": [0.01, -0.10, 0.02, -0.04], "Equilibrada": 0.0, "Dinamica": 0.0}, index=pd.DatetimeIndex(["2007-10-31", "2008-02-29", "2008-03-31", "2009-04-30"]))
    assert list(D.remove_window(S).index) == [pd.Timestamp("2007-10-31"), pd.Timestamp("2009-04-30")]
    h = D.halve_negatives(S)
    assert list(h["Defensiva"]) == [0.01, -0.05, 0.02, -0.04]                  # only the negative month INSIDE the window is halved; positives and outside months untouched
    assert S["Defensiva"].iloc[1] == -0.10                                      # the input is not mutated


def test_ceiling_variants_lists_the_three_bounds_and_removal_never_lowers_the_ceiling():
    idx = pd.date_range("2007-06-30", periods=30, freq="ME")
    rng = np.random.default_rng(3)
    S = pd.DataFrame(rng.normal(0.004, 0.02, (30, 3)), index=idx, columns=list(D.SUBPORTFOLIOS))
    rows = D.ceiling_variants(S)
    assert [r["variant"] for r in rows] == ["all months", "crash window removed (hindsight)", "crash losses halved (hindsight)"]
    assert rows[1]["ann"] >= rows[0]["ann"] - 1e-12 or np.isnan(rows[1]["ann"])


# ---- pools and eligibility ---------------------------------------------------------------------------------------------------

def test_pool_returns_averages_only_the_eligible_funds_of_each_date():
    sc = pd.DataFrame({"as_of": [IDX[0]] * 3, "subportfolio": ["Defensiva"] * 3, "isin": ["A", "B", "C"], "eligible": [True, True, False]})
    R = pd.DataFrame({"A": [0.02], "B": [0.04], "C": [0.50]}, index=[IDX[0]])
    out = D.pool_returns(sc, R, IDX[:1], subs=("Defensiva",))
    assert out["Defensiva"].iloc[0] == pytest.approx(0.03)


def test_first_reason_and_eligibility_by_nature_are_hand_computed():
    t = pd.Timestamp("2008-10-31")
    sc = pd.DataFrame({"as_of": [t] * 6, "subportfolio": ["Defensiva"] * 6, "fund_nature": ["Monetario"] * 4 + ["Mixtos"] * 2,
                       "isin": list("ABCDEF"), "eligible": [True, False, False, False, True, False],
                       "exclusion_reason": [None, "max_drawdown: -20% below -15%", "nav_stale: 70 days", "max_drawdown: x", None, "return_ann_real: y"]})
    assert list(D.first_reason(sc)) == ["eligible", "max_drawdown", "nav_stale", "max_drawdown", "eligible", "return_ann_real"]
    hy = sc.assign(exclusion_reason=[None, None, "Credit_Quality=High Yield excluido de Defensiva", None, None, None], eligible=[True, True, False, True, True, True])
    assert D.first_reason(hy).iloc[2] == "Credit_Quality"                       # a capitalised reason is a reason, not 'eligible' (the first version of this module got this wrong)
    out_hy = D.eligibility_by_nature(hy, [t], "Defensiva", ["Monetario"]).iloc[0]
    assert out_hy["eligible"] == 0.75 and out_hy["other"] == 0.25 and out_hy["max_drawdown"] == 0.0     # the share comes from the `eligible` column
    out = D.eligibility_by_nature(sc, [t], "Defensiva", ["Monetario", "Mixtos", "Alternativo"]).set_index("nature")
    assert out.loc["Monetario", ["n", "eligible", "max_drawdown", "other"]].tolist() == [4, 0.25, 0.5, 0.25]
    assert out.loc["Mixtos", ["n", "eligible", "max_drawdown", "other"]].tolist() == [2, 0.5, 0.0, 0.5]
    assert out.loc["Alternativo", "n"] == 0 and np.isnan(out.loc["Alternativo", "eligible"])      # an empty nature is reported as empty, never as 0 % or 100 %


# ---- hygiene -----------------------------------------------------------------------------------------------------------------

def test_the_module_reads_no_database_and_fills_nothing_silently():
    text = SRC.read_text(encoding="utf-8")
    for banned in ("psycopg", "get_connection", "shared.db", ".fillna(", "to_markdown"):
        assert banned not in text, banned
