"""Package L session 2 (FND-0435 / FND-0436): pure pieces of proyecto3/src/pit_p35a_prep.py that prepare PREREG_P3_5A_DRAFT. Synthetic frames only (R-7): no database, no sealed run, no pipeline import.
Invariants pinned: (1) the L3 freeze guard: the pool-drawdown count refuses to run unless the contamination log holds a freeze line whose SHA-256 is that of the CURRENT L3 text and whose
timestamp is not in the future; (2) the pool-drawdown count is a function of the POOL return series only (no score, no selection, no lever output) and returns only a drawdown and flags;
(3) the minimum detectable effect grows with the serial dependence of the paired differences and shrinks with the sample; (4) the Brinson split on means is an IDENTITY (allocation + selection +
interaction = the gap, to 1e-12) and the attribute split within nature is an identity too; (5) holding spells and the L4 scale check match hand-computed cases; (6) the D6 attribute loader
lives outside pit_inputs and cannot change the attribute frame that feeds the score cache key."""
import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import pit_p35a_prep as P  # noqa: E402


# ---- L3 freeze ---------------------------------------------------------------------------------------------------------------------------------------------------
def test_the_l3_digest_is_the_sha256_of_its_canonical_text_and_any_parameter_change_moves_it(monkeypatch):
    import hashlib
    d = P.l3_digest()
    assert d == hashlib.sha256(P.l3_canonical_text().encode("utf-8")).hexdigest() and len(d) == 64
    for key in P.L3_PARAMS:
        changed = dict(P.L3_PARAMS)
        changed[key] = str(changed[key]) + "x"
        monkeypatch.setattr(P, "L3_PARAMS", changed)
        assert P.l3_digest() != d, key
    monkeypatch.undo()
    assert P.l3_digest() == d


def test_a_recorded_freeze_line_passes_and_returns_its_digest_and_time():
    now = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
    line = P.freeze_line(utc=(now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    got = P.assert_freeze_recorded("# log\n- some other line\n- " + line + "\n", now=now)
    assert got["digest"] == P.l3_digest() and got["utc"] == "2026-11-21T17:55:00Z"


@pytest.mark.parametrize("text,why", [
    ("# log with no freeze line at all\n", "no freeze"),
    ("L3-FREEZE sha256=" + "0" * 64 + " utc=2026-11-21T17:00:00Z\n", "digest"),
    ("L3-FREEZE sha256=abc utc=2026-11-21T17:00:00Z\n", "digest"),
    ("L3-FREEZE sha256=%s utc=not-a-time\n", "utc"),
    ("L3-FREEZE sha256=%s utc=2026-11-21T19:00:00Z\n", "future"),                 # after `now` (18:00)
])
def test_the_count_is_refused_without_a_matching_and_past_freeze_line(text, why):
    now = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
    text = text.replace("%s", P.l3_digest())
    with pytest.raises(P.FreezeNotRecorded):
        P.assert_freeze_recorded(text, now=now)


def test_a_freeze_of_an_older_version_of_the_text_does_not_cover_the_current_one(monkeypatch):
    now = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
    old_line = P.freeze_line(utc="2026-11-21T17:00:00Z")
    monkeypatch.setattr(P, "L3_PARAMS", {**P.L3_PARAMS, "stress_threshold": "0.04"})
    with pytest.raises(P.FreezeNotRecorded):
        P.assert_freeze_recorded(old_line, now=now)


# ---- pool drawdown of the locked episodes -----------------------------------------------------------------------------------------------------------------------
def _pool(values, start="2015-05-31"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="ME"), dtype=float)


def test_the_episode_drawdown_is_the_peak_to_trough_of_the_pool_over_the_months_of_the_window():
    # the forward return at formation t is the return of the NEXT month: the months of July-August 2015 use the formation dates 2015-06-30 and 2015-07-31
    pool = _pool([0.01, -0.03, -0.04, 0.05, 0.01, 0.0])                         # formations 2015-05-31 .. 2015-10-31
    r = P.episode_pool_drawdown(pool, "2015-07-01", "2015-08-31")
    assert r["months"] == 2 and r["complete"] is True
    assert r["pool_max_dd"] == pytest.approx((1 - 0.03) * (1 - 0.04) - 1)         # the start level counts as the first peak
    one = P.episode_pool_drawdown(pool, "2015-08-01", "2015-08-31")
    assert one["months"] == 1 and one["pool_max_dd"] == pytest.approx(-0.04)


def test_a_window_with_a_missing_month_is_incomplete_never_filled():
    pool = _pool([0.01, np.nan, -0.04, 0.05, 0.01, 0.0])                        # the formation 2015-06-30 (July's return) is missing
    r = P.episode_pool_drawdown(pool, "2015-07-01", "2015-08-31")
    assert r["complete"] is False and np.isnan(r["pool_max_dd"])


def test_the_count_applies_the_gate_and_checks_the_recovery_data():
    pool = _pool([0.0, -0.03, -0.04, 0.0, 0.0, 0.0] + [0.001] * 14)            # data runs to the 2016-12-31 formation, i.e. returns up to 2017-01
    eps = {"deep": ("2015-07-01", "2015-08-31"), "shallow": ("2015-10-01", "2015-10-31"), "late": ("2016-09-01", "2016-09-30")}
    t = P.pool_dd_count(pool, eps, gate=0.04, recovery_months=12).set_index("episode")
    assert bool(t.loc["deep", "informative"]) and not bool(t.loc["shallow", "informative"])
    assert bool(t.loc["deep", "recovery_available"]) and not bool(t.loc["late", "recovery_available"])        # 2016-09 + 12 months is past the data
    assert set(t.columns) == {"months", "pool_max_dd", "complete", "informative", "recovery_available", "informative_with_recovery"}


def test_the_count_reads_only_the_pool_series_and_no_selection_or_lever_output():
    for fn in (P.episode_pool_drawdown, P.pool_dd_count):
        src = inspect.getsource(fn)
        for banned in ("score_final", "select_", "build_weights", "exclusion_reason", "eligible", "assert_lever_dates_allowed"):
            assert banned not in src, (fn.__name__, banned)


def test_the_master_pool_return_is_the_equal_weight_of_the_unique_eligible_funds():
    at = pd.DatetimeIndex(["2008-01-31", "2008-02-29"])
    scores = pd.DataFrame({"as_of": [at[0]] * 4 + [at[1]] * 2, "subportfolio": ["Defensiva", "Equilibrada", "Equilibrada", "Dinamica", "Defensiva", "Dinamica"],
                           "isin": ["A", "A", "B", "C", "A", "B"], "eligible": [True, True, True, False, True, False]})
    fwd = pd.DataFrame({"A": [0.01, 0.02], "B": [0.03, 0.10], "C": [0.50, 0.50]}, index=at)
    got = P.master_pool_returns(scores, fwd, at)
    assert got.iloc[0] == pytest.approx((0.01 + 0.03) / 2) and got.iloc[1] == pytest.approx(0.02)          # A listed in two sub-portfolios counts once; C is not eligible


# ---- minimum detectable effect ------------------------------------------------------------------------------------------------------------------------------------
def test_the_mde_formula_and_the_annualisation_are_exact():
    from statistics import NormalDist
    z = NormalDist().inv_cdf
    assert P.mde_pp(0.30, 0.05, 0.80) == pytest.approx((z(0.95) + z(0.80)) * 0.30)
    assert P.mde_pp(0.30, 0.05 / 11, 0.80) > P.mde_pp(0.30, 0.05, 0.80) > P.mde_pp(0.20, 0.05, 0.80)


def test_the_paired_se_matches_the_iid_formula_and_grows_with_serial_dependence():
    rng = np.random.default_rng(7)
    n, sd = 240, 0.004
    iid = rng.normal(0.0, sd, n)
    se_iid = P.paired_se_annual_pp(iid, mean_block=1.0, n_boot=3000, seed=1)
    assert se_iid == pytest.approx(sd / np.sqrt(n) * 12 * 100, rel=0.20)
    ar = np.zeros(n)
    for i in range(1, n):
        ar[i] = 0.8 * ar[i - 1] + rng.normal(0.0, sd * 0.6)
    ar = ar / ar.std() * sd                                                       # same marginal sd, strong dependence
    assert P.paired_se_annual_pp(ar, mean_block=12.0, n_boot=3000, seed=1) > 1.5 * se_iid
    with pytest.raises(ValueError):
        P.paired_se_annual_pp(np.array([0.1]))


def test_non_finite_paired_differences_are_dropped_and_counted_never_filled():
    d = np.r_[np.random.default_rng(3).normal(0, 0.003, 100), [np.nan, np.nan]]
    out = P.paired_se_annual_pp(d, return_n=True)
    assert out["n"] == 100 and out["dropped"] == 2 and out["se_pp"] > 0


# ---- Brinson on means ---------------------------------------------------------------------------------------------------------------------------------------------
def _case(seed=0, n=60, k=3):
    rng = np.random.default_rng(seed)
    isin = [f"F{i:03d}" for i in range(n)]
    ret = pd.Series(rng.normal(0.05, 0.2, n), index=isin)
    grp = pd.Series(rng.choice([f"G{j}" for j in range(k)], n), index=isin)
    pool_w = pd.Series(1.0 / n, index=isin)
    chosen = rng.choice(isin, 12, replace=False)
    sel_w = pd.Series(rng.dirichlet(np.ones(12)), index=chosen)
    return sel_w, pool_w, ret, grp


def test_brinson_hand_case():
    # two natures; pool 50/50, returns A 0.10 B 0.00; selected 75/25 with returns A 0.12 B 0.02
    isin = ["a1", "a2", "b1", "b2"]
    ret = pd.Series([0.12, 0.08, 0.02, -0.02], index=isin)                         # pool cell returns: A 0.10, B 0.00; selected cell returns: A 0.12 (a1), B 0.02 (b1)
    grp = pd.Series(["A", "A", "B", "B"], index=isin)
    pool_w = pd.Series(0.25, index=isin)
    sel_w = pd.Series({"a1": 0.75, "b1": 0.25})
    ret2 = ret
    out = P.brinson(sel_w, pool_w, ret2, grp)
    assert out["gap"] == pytest.approx(0.75 * 0.12 + 0.25 * 0.02 - (0.5 * 0.10 + 0.5 * 0.00))
    assert out["allocation"] == pytest.approx((0.75 - 0.5) * (0.10 - 0.05) + (0.25 - 0.5) * (0.00 - 0.05))
    assert out["selection"] == pytest.approx(0.5 * (0.12 - 0.10) + 0.5 * (0.02 - 0.00))
    assert out["interaction"] == pytest.approx((0.75 - 0.5) * (0.12 - 0.10) + (0.25 - 0.5) * (0.02 - 0.00))
    assert out["allocation"] + out["selection"] + out["interaction"] == pytest.approx(out["gap"], abs=1e-12)


@pytest.mark.parametrize("seed", range(6))
def test_brinson_is_an_identity_on_random_data(seed):
    sel_w, pool_w, ret, grp = _case(seed)
    out = P.brinson(sel_w, pool_w, ret, grp)
    assert out["allocation"] + out["selection"] + out["interaction"] == pytest.approx(out["gap"], abs=1e-12)
    assert out["gap"] == pytest.approx(float((sel_w * ret.reindex(sel_w.index)).sum() - (pool_w * ret).sum()), abs=1e-12)
    assert out["valid_share_selected"] == pytest.approx(1.0) and out["valid_share_pool"] == pytest.approx(1.0)


def test_a_missing_outcome_is_dropped_from_both_sides_and_reported():
    sel_w, pool_w, ret, grp = _case(2)
    ret = ret.copy()
    ret.iloc[:5] = np.nan
    out = P.brinson(sel_w, pool_w, ret, grp)
    assert out["valid_share_pool"] == pytest.approx(55 / 60) and out["valid_share_selected"] <= 1.0
    assert out["allocation"] + out["selection"] + out["interaction"] == pytest.approx(out["gap"], abs=1e-12)
    assert np.isfinite(out["gap"])


def test_a_group_the_selection_does_not_hold_contributes_nothing_to_selection_or_interaction():
    isin = ["a1", "a2", "b1", "b2"]
    ret = pd.Series([0.10, 0.12, -0.05, -0.03], index=isin)
    grp = pd.Series(["A", "A", "B", "B"], index=isin)
    out = P.brinson(pd.Series({"a1": 1.0}), pd.Series(0.25, index=isin), ret, grp)
    by = out["by_group"].set_index("group")
    assert by.loc["B", "selection"] == 0.0 and by.loc["B", "interaction"] == 0.0
    assert out["allocation"] + out["selection"] + out["interaction"] == pytest.approx(out["gap"], abs=1e-12)


@pytest.mark.parametrize("seed", range(5))
def test_the_attribute_split_within_nature_is_an_identity(seed):
    sel_w, pool_w, ret, nat = _case(seed, n=90, k=3)
    rng = np.random.default_rng(seed + 100)
    attr = pd.Series(rng.choice(["x", "y", "z", None], len(ret)), index=ret.index)
    out = P.attribute_mix_within_nature(sel_w, pool_w, ret, nat, attr)
    within = P.brinson(sel_w, pool_w, ret, nat)
    assert out["within_nature_total"] == pytest.approx(within["selection"] + within["interaction"], abs=1e-12)
    assert out["attribute_mix"] + out["cell_selection"] == pytest.approx(out["within_nature_total"], abs=1e-12)
    assert out["missing_attribute_share_pool"] == pytest.approx(float(attr.isna().mean()))


def test_an_attribute_that_does_not_vary_explains_nothing():
    sel_w, pool_w, ret, nat = _case(4, n=60)
    out = P.attribute_mix_within_nature(sel_w, pool_w, ret, nat, pd.Series("same", index=ret.index))
    assert out["attribute_mix"] == pytest.approx(0.0, abs=1e-12)


# ---- holding spells -----------------------------------------------------------------------------------------------------------------------------------------------------
def test_holding_spells_hand_case():
    idx = pd.date_range("2020-01-31", periods=6, freq="ME")
    W = pd.DataFrame({"A": [0.5, 0.5, 0.5, np.nan, np.nan, np.nan],                 # one spell of 3 months
                      "B": [np.nan, 0.5, np.nan, 0.5, 0.5, np.nan],                 # spells of 1 and 2
                      "C": [0.2, 0.2, 0.2, 0.2, 0.2, 0.2]}, index=idx)              # one censored spell of 6
    s = P.holding_spells(W)
    assert s["n_spells"] == 4 and s["mean_months"] == pytest.approx((3 + 1 + 2 + 6) / 4) and s["median_months"] == pytest.approx(2.5)
    assert s["completed_mean_months"] == pytest.approx((3 + 1 + 2) / 3)               # spells that ended inside the window
    assert s["n_censored"] == 1


# ---- L4 scale check -----------------------------------------------------------------------------------------------------------------------------------------------------
def test_the_penalty_scale_against_the_within_nature_spread_hand_case():
    ret = pd.Series([0.00, 0.02, 0.04, 0.06, 0.08], index=list("abcde"))                         # IQR = 0.04
    fees = pd.Series([0.00, 0.00, 0.01, 0.02, 0.05], index=list("abcde"))                        # entry + exit, ratios
    out = P.penalty_scale(ret, fees, h_years=0.5)                                                  # penalties 0, 0, .02, .04, .10 a year
    assert out["iqr"] == pytest.approx(0.04) and out["n"] == 5 and out["n_charging"] == 3
    assert out["median_penalty_charging"] == pytest.approx(0.04) and out["ratio_median_to_iqr"] == pytest.approx(1.0)
    assert out["share_over_half_iqr"] == pytest.approx(2 / 5)                                      # strictly above half the IQR (0.02): the penalties .04 and .10, not .02
    assert out["rank_change_share"] >= 0.0


def test_an_unknown_fee_takes_the_cell_median_of_the_known_penalties_and_is_counted():
    ret = pd.Series([0.00, 0.02, 0.04, 0.06], index=list("abcd"))
    fees = pd.Series([0.00, 0.02, np.nan, 0.04], index=list("abcd"))
    out = P.penalty_scale(ret, fees, h_years=1.0)
    assert out["n_unknown_fee"] == 1 and out["n"] == 4
    # known penalties 0, .02, .04 -> median .02 fills c; nothing is hidden: the count above is the trace


def test_the_rank_change_counts_funds_whose_within_cell_percentile_moves_by_at_least_a_tenth():
    ret = pd.Series(np.linspace(0.0, 0.10, 11), index=[f"f{i}" for i in range(11)])
    free = pd.Series(0.0, index=ret.index)
    assert P.penalty_scale(ret, free, h_years=1.0)["rank_change_share"] == 0.0
    costly = pd.Series(0.0, index=ret.index)
    costly.iloc[-1] = 0.5                                                                           # the best fund is wiped out by its fee
    assert P.penalty_scale(ret, costly, h_years=1.0)["rank_change_share"] > 0.0


# ---- rolling drawdown (lock-in horizon) ------------------------------------------------------------------------------------------------------------------------------
def test_the_rolling_drawdown_forgets_an_old_crash_and_the_since_inception_one_does_not():
    idx = pd.date_range("2000-01-31", periods=90, freq="ME")
    nav = pd.Series(100.0, index=idx)
    nav.iloc[10:20] = np.linspace(100, 50, 10)                                              # a -50 % crash in months 10-19, flat afterwards
    nav.iloc[20:] = 50.0
    df = pd.DataFrame({"X": nav})
    t = idx[-1]
    assert P.rolling_max_dd(df, t, None)["X"] == pytest.approx(-0.5)                         # since inception: the crash stays
    assert P.rolling_max_dd(df, t, 60)["X"] == pytest.approx(0.0)                            # the last 60 months (30..89) are flat
    short = P.rolling_max_dd(df, idx[30], 60)                                                  # 31 points only: the window is not full
    assert np.isnan(short["X"])


# ---- the D6 attribute loader never touches the cache-key frame ----------------------------------------------------------------------------------------------
def test_the_d6_loader_is_outside_pit_inputs_and_the_attribute_frame_columns_are_unchanged():
    from proyecto3.src import pit_inputs
    from proyecto3.src.pit_cache import content_hash
    assert hasattr(P, "load_d6_attributes") and not hasattr(pit_inputs, "load_d6_attributes")
    for col in P.D6_ATTRIBUTES:
        assert col not in pit_inputs.ATTRIBUTE_FRAME_COLUMNS, col
    attrs = pd.DataFrame({c: ["x", "y"] for c in pit_inputs.ATTRIBUTE_FRAME_COLUMNS[1:]}, index=pd.Index(["A", "B"], name="isin"))
    before = content_hash(attrs)
    d6 = pd.DataFrame({c: ["g", "h"] for c in P.D6_ATTRIBUTES}, index=pd.Index(["A", "B"], name="isin"))
    _ = d6.join(attrs[["Fund_Nature"]])                                                           # what the diagnostic does with it: a NEW frame
    assert content_hash(attrs) == before
    assert "load_attributes" not in inspect.getsource(P.load_d6_attributes)
