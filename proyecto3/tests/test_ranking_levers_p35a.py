"""Package L step F (doc/decisiones/PREREG_P3_5A.md): the four ranking-layer levers of proyecto3/src/ranking_levers.py and their wiring into the scorer and the PIT. Synthetic frames only (R-7).
Invariants pinned: (1) every lever ships OFF and an all-off configuration changes NOTHING (scores, eligibility, columns, PIT output, PIT cache keys); (2) L1 excludes the worst share q of a cell on the
rolling 60-month drawdown, falls back to the since-inception drawdown for a fund without one, keeps the absolute filter in a cell below 15 funds, leaves Defensiva alone and never lets a missing
metric exclude; (3) L4 only touches the score: `check_hard_filters` reads the RAW return, so L1 and L4 are orthogonal and the eligible set is identical with L4 on or off; (4) L2 multiplies the score
of the over-extended funds of Equilibrada by 0.80 and nothing else; (5) L3's state is a function of the pool series only, its constants equal the ones frozen by hash in the preregistration, and
k = 1 is the identity; (6) the live loader asks P2 only for metrics P2 stores; (7) the PIT passes the same inputs to the same scorer function and a lever that is ON without its inputs is an error,
never a silent no-op."""
import inspect
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _scoring_fixture import make_scoring_frame  # noqa: E402
from proyecto3.src import fund_scorer as fs  # noqa: E402
from proyecto3.src import ranking_levers as L  # noqa: E402

OFF = L.LeverConfig()


def lever_frame(n: int = 420, seed: int = 5) -> pd.DataFrame:
    f = make_scoring_frame(n=n, seed=seed)
    rng = np.random.default_rng(seed + 1)
    f["fund_family_id"] = None                                                         # every class is its own family: the deduplication cannot hide a score change
    f["return_ann_real"] = rng.normal(0.03, 0.02, len(f))                              # no real-return filter hit unless a test asks for one
    f["max_dd__rolling_5y"] = -np.abs(rng.normal(0.18, 0.10, len(f))).clip(0.0, 0.55)  # never below the Equilibrada backstop (-0.60)
    f["return_ann__rolling_3y"] = rng.normal(0.06, 0.12, len(f))
    f["Entry_Fee_Pct"] = rng.choice([0.0, 0.0, 0.02, 0.05, np.nan], len(f))
    f["Exit_Fee_Pct"] = rng.choice([0.0, 0.0, 0.01], len(f))
    return f


def score(frame, levers=None, recovery_k=None, regime="Expansion"):
    return fs.score_funds_from_df(frame.copy(), regime, verbose=False, levers=levers, recovery_k=recovery_k)


# ================================================================ configuration =====================================================================================
def test_every_lever_switch_ships_off_and_the_default_config_is_all_off():
    import shared.config as C
    for name in ("P3_L1_PEER_FILTER_ENABLED", "P3_L2_OVEREXTENSION_ENABLED", "P3_L3_RECOVERY_MODULATION_ENABLED", "P3_L4_FEE_PENALTY_ENABLED"):
        assert getattr(C, name) is False, name
    cfg = L.LeverConfig.from_config()
    assert cfg == OFF and not cfg.any_on and L.metric_columns(cfg) == ()


def test_from_config_reads_the_switches_and_their_values(monkeypatch):
    import shared.config as C
    monkeypatch.setattr(C, "P3_L1_PEER_FILTER_ENABLED", True)
    monkeypatch.setattr(C, "P3_L1_PEER_CUT", 0.35)
    monkeypatch.setattr(C, "P3_L4_FEE_PENALTY_ENABLED", True)
    cfg = L.LeverConfig.from_config()
    assert cfg == L.LeverConfig(l1_q=0.35, l4_h_years=C.P3_L4_H_YEARS) and cfg.any_on
    assert set(L.metric_columns(cfg)) == {L.DD60_COLUMN, "Entry_Fee_Pct", "Exit_Fee_Pct"}


def test_the_l3_constants_equal_the_ones_frozen_by_hash_in_the_preregistration():
    from proyecto3.src import pit_p35a_prep as P
    p = P.L3_PARAMS
    assert L.L3_TRAILING_MONTHS == int(p["trailing_window_months"]) and L.L3_STRESS_THRESHOLD == float(p["stress_threshold"])
    assert L.L3_RECOVERY_RETURN_MONTHS == int(p["recovery_return_window_months"]) and L.L3_RECOVERY_CONFIRMATIONS == int(p["recovery_confirmation_month_ends"])
    assert L.L3_MIN_REBOUND == float(p["recovery_min_rebound_from_low"])


def test_the_config_key_moves_with_every_value_and_the_all_off_key_is_never_used():
    base = L.LeverConfig(l1_q=0.2, l2_z0=2.0, l3_k=0.5, l4_h_years=3.0)
    for changed in (L.LeverConfig(l1_q=0.35, l2_z0=2.0, l3_k=0.5, l4_h_years=3.0), L.LeverConfig(l1_q=0.2, l2_z0=1.5, l3_k=0.5, l4_h_years=3.0),
                    L.LeverConfig(l1_q=0.2, l2_z0=2.0, l3_k=0.0, l4_h_years=3.0), L.LeverConfig(l1_q=0.2, l2_z0=2.0, l3_k=0.5, l4_h_years=2.0)):
        assert changed.key() != base.key()
    from proyecto3.src.pit_run import lever_key_parts
    assert lever_key_parts(OFF, None) == []                                       # an all-off run adds NOTHING to a cache key: the warm caches stay valid
    assert lever_key_parts(base, None) and lever_key_parts(base, None) != lever_key_parts(L.LeverConfig(l1_q=0.35), None)


# ================================================================ L1 ============================================================================================
def l1_cell_frame(n=60, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"Fund_Nature": "Renta Variable", "max_dd": -np.abs(rng.normal(0.2, 0.1, n)).clip(0, 0.5), L.DD60_COLUMN: -np.abs(rng.normal(0.15, 0.08, n)).clip(0, 0.5),
                         "return_ann_real": rng.normal(0.03, 0.02, n)}, index=[f"F{i:03d}" for i in range(n)])


def test_l1_excludes_the_worst_share_of_a_cell_on_the_rolling_drawdown():
    df = l1_cell_frame(60)
    r = L.l1_peer_reasons(df, "Equilibrada", 0.20, -0.30, -0.02)
    peer = r[r.fillna("").str.startswith("l1_peer_drawdown")].index
    assert len(peer) == 12                                                        # 20 % of 60
    assert df.loc[peer, L.DD60_COLUMN].max() <= df.drop(index=peer)[L.DD60_COLUMN].min() + 1e-12      # exactly the 12 worst
    assert not r.fillna("").str.startswith("l1_backstop").any()
    r35 = L.l1_peer_reasons(df, "Equilibrada", 0.35, -0.30, -0.02)
    assert r35.fillna("").str.startswith("l1_peer_drawdown").sum() == 21


def test_l1_uses_the_since_inception_drawdown_for_a_fund_without_a_rolling_one():
    df = l1_cell_frame(60)
    worst = df["max_dd"].idxmin()
    df.loc[worst, L.DD60_COLUMN] = np.nan
    df.loc[worst, "max_dd"] = -0.99                                               # worst by far on the fallback
    r = L.l1_peer_reasons(df, "Dinamica", 0.20, -0.40, 0.0)
    assert isinstance(r[worst], str) and r[worst].startswith("l1_backstop")       # -0.99 is below the Dinamica backstop (-0.80) on the fallback value


def test_l1_backstops_are_twice_the_drawdown_limit_and_the_return_limit_minus_5_pp():
    df = l1_cell_frame(60)
    df.loc["F000", L.DD60_COLUMN] = -0.61
    df.loc["F001", L.DD60_COLUMN] = -0.79
    df.loc["F002", "return_ann_real"] = -0.08
    df.loc["F003", "return_ann_real"] = -0.065
    eq = L.l1_peer_reasons(df, "Equilibrada", 0.01, -0.30, -0.02)               # q tiny: only backstops can fire
    assert eq["F000"].startswith("l1_backstop") and eq["F002"].startswith("l1_backstop") and not (isinstance(eq["F003"], str) and eq["F003"].startswith("l1_backstop"))
    din = L.l1_peer_reasons(df, "Dinamica", 0.01, -0.40, 0.0)
    assert not (isinstance(din["F000"], str) and din["F000"].startswith("l1_backstop")) and not (isinstance(din["F001"], str) and din["F001"].startswith("l1_backstop"))   # -0.61 / -0.79 are above -0.80
    assert din["F003"].startswith("l1_backstop")                                  # -0.065 < 0.0 - 0.05


def test_l1_small_cell_keeps_the_absolute_filter_and_a_missing_metric_never_excludes():
    df = l1_cell_frame(10)
    df[L.DD60_COLUMN], df["max_dd"], df["return_ann_real"] = -0.10, -0.10, 0.03
    df.loc["F000", L.DD60_COLUMN] = -0.45
    df.loc["F002", [L.DD60_COLUMN, "max_dd"]] = np.nan
    r = L.l1_peer_reasons(df, "Equilibrada", 0.35, -0.30, -0.02)
    assert r["F000"] == "drawdown=-0.45 < -0.3 (Equilibrada, cell below 15)"      # the absolute limit applies in a cell below MIN_CELL
    assert pd.isna(r["F001"]) and pd.isna(r["F002"])                              # a fund inside the limit and a fund with no drawdown at all are never excluded
    assert list(r.dropna().index) == ["F000"]


def test_l1_leaves_defensiva_and_an_invalid_q_alone():
    df = l1_cell_frame(60)
    assert L.l1_peer_reasons(df, "Defensiva", 0.35, -0.20, -0.05).isna().all()
    assert L.l1_peer_reasons(df, "Equilibrada", 0.0, -0.30, -0.02).isna().all() and L.l1_peer_reasons(df, "Equilibrada", 1.0, -0.30, -0.02).isna().all()


def test_l1_cells_are_by_nature():
    a, b = l1_cell_frame(40, 1), l1_cell_frame(40, 2)
    b["Fund_Nature"] = "Mixtos"
    b.index = [f"G{i:03d}" for i in range(40)]
    df = pd.concat([a, b])
    r = L.l1_peer_reasons(df, "Equilibrada", 0.25, -0.30, -0.02)
    for nat in ("Renta Variable", "Mixtos"):
        assert r[df["Fund_Nature"] == nat].fillna("").str.startswith("l1_peer_drawdown").sum() == 10          # 25 % of each cell of 40, not of the 80


# ================================================================ L2 ============================================================================================
def test_l2_penalises_the_robust_z_above_the_threshold_in_equilibrada_only():
    rng = np.random.default_rng(3)
    x = rng.normal(0.05, 0.05, 80)
    df = pd.DataFrame({"Fund_Nature": "Mixtos", L.RET36_COLUMN: x}, index=[f"M{i:02d}" for i in range(80)])
    med = np.median(x)
    scale = 1.4826 * np.median(np.abs(x - med))
    z = (x - med) / scale
    got = L.l2_multiplier(df, "Equilibrada", 1.5)
    assert ((got == 0.80) == (z > 1.5)).all() and set(got.unique()) <= {0.80, 1.0} and (got == 0.80).any()
    assert (L.l2_multiplier(df, "Dinamica", 1.5) == 1.0).all() and (L.l2_multiplier(df, "Defensiva", 1.5) == 1.0).all()
    assert (L.l2_multiplier(df, "Equilibrada", 2.0) == 0.80).sum() <= (got == 0.80).sum()                    # a higher threshold penalises fewer


def test_l2_leaves_a_small_cell_a_zero_mad_a_missing_metric_and_a_missing_column_alone():
    small = pd.DataFrame({"Fund_Nature": "Mixtos", L.RET36_COLUMN: np.r_[np.zeros(13), [5.0]]}, index=range(14))
    assert (L.l2_multiplier(small, "Equilibrada", 1.0) == 1.0).all()                                         # 14 < 15
    flat = pd.DataFrame({"Fund_Nature": "Mixtos", L.RET36_COLUMN: np.r_[np.zeros(30), [5.0]]}, index=range(31))
    assert (L.l2_multiplier(flat, "Equilibrada", 1.0) == 1.0).all()                                           # MAD 0: no scale
    holes = pd.DataFrame({"Fund_Nature": "Mixtos", L.RET36_COLUMN: np.r_[np.random.default_rng(0).normal(0, 1, 30), [np.nan]]}, index=range(31))
    assert L.l2_multiplier(holes, "Equilibrada", 0.5).iloc[-1] == 1.0
    assert (L.l2_multiplier(holes.drop(columns=L.RET36_COLUMN), "Equilibrada", 0.5) == 1.0).all()


# ================================================================ L3 ============================================================================================
RECOVERY = [0.0] * 4 + [-0.05] * 3 + [0.02] * 5
STRESS_ONLY = [0.0] * 4 + [-0.05] * 3 + [0.02] + [-0.01] * 4
NORMAL = [0.01] * 12


def test_the_pool_state_machine_normal_stress_recovery():
    assert L.pool_state(pd.Series(NORMAL)) == "normal"
    assert L.pool_state(pd.Series(STRESS_ONLY)) == "stress"
    assert L.pool_state(pd.Series(RECOVERY)) == "recovery"
    mild = pd.Series([0.0] * 4 + [-0.05] * 3 + [0.03] * 5)                        # 1.03^5 brings the level back above 0.97 of the high: not stress
    assert L.pool_state(mild) == "normal"


def test_recovery_needs_every_condition_and_missing_data_is_normal():
    three_month_return_negative = pd.Series([0.0] * 4 + [-0.05] * 6 + [0.0] + [0.01])              # a rebound month, but the 3-month return at t is still negative
    assert L.pool_state(three_month_return_negative) == "stress"
    weak_rebound = pd.Series([0.0] * 3 + [-0.05] * 4 + [0.0] + [0.01] * 4)                         # both 3-month returns are positive (3.0 %) but the level is only 4.1 % above its low
    assert L.pool_state(weak_rebound) == "stress"
    enough_rebound = pd.Series([0.0] * 3 + [-0.05] * 4 + [0.0] + [0.013] * 4)                      # the same shape with a 5.3 % rebound from the low
    assert L.pool_state(enough_rebound) == "recovery"
    assert L.pool_state(pd.Series(RECOVERY[:-1])) == "normal"                    # fewer than 12 returns
    gap = pd.Series(RECOVERY)
    gap.iloc[5] = np.nan
    assert L.pool_state(gap) == "normal"                                          # a missing month is never filled
    assert L.pool_state(pd.Series([0.0] * 5 + RECOVERY)) == "recovery"            # only the last 12 returns count


def test_the_state_depends_on_the_pool_series_only():
    src = inspect.getsource(L.pool_state)
    assert "regime" not in src.lower() and "nature" not in src.lower()


def test_pool_returns_use_month_ends_only_and_never_fill_a_hole():
    idx = pd.date_range("2020-01-31", periods=14, freq="ME")
    nav = pd.DataFrame({"A": 100.0 * 1.01 ** np.arange(14), "B": 100.0 * 1.03 ** np.arange(14)}, index=idx)
    nav.loc[idx[6], "B"] = np.nan                                                 # a hole: B has no return in months 6 and 7
    stray = pd.DataFrame({"A": [50.0], "B": [50.0]}, index=pd.DatetimeIndex(["2020-06-13"]))
    dirty = pd.concat([nav, stray]).sort_index()
    clean = L.pool_returns_from_nav(L.month_end_rows(nav), ["A", "B"], idx[-1])
    got = L.pool_returns_from_nav(L.month_end_rows(dirty), ["A", "B"], idx[-1])
    pd.testing.assert_series_equal(got, clean)
    assert len(got) == 12 and got.iloc[0] == pytest.approx((0.01 + 0.03) / 2)
    assert clean.loc[idx[6]] == pytest.approx(0.01) and clean.loc[idx[7]] == pytest.approx(0.01)       # only A has a return in months 6 and 7


def test_modulation_k_one_is_the_identity_k_zero_removes_the_bonus_and_the_drawdown_weight():
    w = dict(fs.SUBPORTFOLIO_WEIGHTS["Equilibrada"])
    assert all(math.isclose(L.modulate_weights(w, 1.0)[m], w[m]) for m in w)
    half = L.modulate_weights(w, 0.5)
    assert half["max_dd"] == pytest.approx(w["max_dd"] * 0.5) and sum(half.values()) == pytest.approx(sum(w.values()))
    zero = L.modulate_weights(w, 0.0)
    assert zero["max_dd"] == 0.0 and sum(zero.values()) == pytest.approx(sum(w.values()))
    assert zero["sharpe"] == pytest.approx(w["sharpe"] / (1.0 - w["max_dd"]))      # the others scaled by the same factor
    b = fs.NATURE_PROFILE_BONUS["Defensiva"]
    assert all(math.isclose(L.modulate_bonus(b, 1.0)[n], b[n]) for n in b)
    assert L.modulate_bonus(b, 0.0)["Monetario"] == 1.0 and L.modulate_bonus(b, 0.5)["Monetario"] == pytest.approx(1.125)


# ================================================================ L4 ============================================================================================
def test_l4_penalty_hand_case_unknown_fee_takes_the_cell_median_and_is_counted():
    df = pd.DataFrame({"Entry_Fee_Pct": [0.0, 0.02, np.nan, 0.05, -0.01], "Exit_Fee_Pct": [0.0, 0.0, 0.0, 0.01, 0.0]}, index=list("abcde"))
    pen, n_unknown = L.l4_penalty(df, 2.0)
    assert pen["a"] == 0.0 and pen["b"] == pytest.approx(0.01) and pen["d"] == pytest.approx(0.03)
    assert n_unknown == 2                                                         # the NaN and the negative fee
    assert pen["c"] == pen["e"] == pytest.approx(0.01)                            # median of 0, 0.01, 0.03
    none_known, n = L.l4_penalty(pd.DataFrame({"Entry_Fee_Pct": [np.nan], "Exit_Fee_Pct": [np.nan]}), 3.0)
    assert none_known.isna().all() and n == 1
    absent, n = L.l4_penalty(pd.DataFrame(index=[1, 2]), 3.0)
    assert absent.isna().all() and n == 2


# ================================================================ scorer integration ==================================================================================
def test_an_all_off_config_changes_nothing_and_extra_lever_columns_are_inert():
    f = lever_frame()
    plain = score(f.drop(columns=[L.DD60_COLUMN, L.RET36_COLUMN, "Entry_Fee_Pct", "Exit_Fee_Pct"]))
    pd.testing.assert_frame_equal(score(f, levers=OFF).reset_index(drop=True), plain.reset_index(drop=True), check_exact=True)
    pd.testing.assert_frame_equal(score(f).reset_index(drop=True), plain.reset_index(drop=True), check_exact=True)            # levers=None reads the (OFF) configuration
    assert not any(c in plain.columns for c in (L.DD60_COLUMN, L.RET36_COLUMN, "l1_on", "l1_reason"))


def test_l1_changes_only_equilibrada_and_dinamica_and_only_through_l1_reasons():
    f = lever_frame()
    base, on = score(f), score(f, levers=L.LeverConfig(l1_q=0.20))
    key = ["subportfolio", "isin"]
    m = base.merge(on, on=key, suffixes=("_b", "_l"))
    assert len(m) == len(base) == len(on)
    defensiva = m[m["subportfolio"] == "Defensiva"]
    assert (defensiva["eligible_b"] == defensiva["eligible_l"]).all() and (defensiva["exclusion_reason_b"].fillna("") == defensiva["exclusion_reason_l"].fillna("")).all()
    changed = m[(m["eligible_b"] != m["eligible_l"]) | (m["exclusion_reason_b"].fillna("") != m["exclusion_reason_l"].fillna(""))]
    assert len(changed) and set(changed["subportfolio"]) <= {"Equilibrada", "Dinamica"}
    newly_out = m[m["eligible_b"] & ~m["eligible_l"]]
    assert len(newly_out) and newly_out["exclusion_reason_l"].str.startswith("l1_").all() and (newly_out["score_final_l"] == 0.0).all()
    assert (m["score_base_b"] == m["score_base_l"]).all()                         # L1 is a FILTER: no base score moves


def test_l1_excludes_the_worst_fifth_of_each_nature_cell_through_the_scorer():
    f = lever_frame()
    f["return_ann_real"] = 0.03 + np.linspace(0, 0.01, len(f))                  # no real-return exclusion
    on = score(f, levers=L.LeverConfig(l1_q=0.20))
    for sub in ("Equilibrada", "Dinamica"):
        blk = on[on["subportfolio"] == sub]
        for nat, g in blk.groupby("fund_nature"):
            n = int(g.shape[0])
            peer = int(g["exclusion_reason"].fillna("").str.startswith("l1_peer_drawdown").sum())
            assert abs(peer - 0.20 * n) < 1.0 + 1e-9, (sub, nat, n, peer)


def test_l4_never_changes_eligibility_and_only_the_base_score_and_never_the_raw_return():
    f = lever_frame()
    base, on = score(f), score(f, levers=L.LeverConfig(l4_h_years=3.0))
    key = ["subportfolio", "isin"]
    m = base.merge(on, on=key, suffixes=("_b", "_l"))
    assert (m["eligible_b"] == m["eligible_l"]).all() and (m["exclusion_reason_b"].fillna("") == m["exclusion_reason_l"].fillna("")).all()       # L1 / L4 orthogonality
    assert (m["score_base_b"] != m["score_base_l"]).any()
    ret_b = pd.Series([d["metrics"]["return_ann_real"] for d in base["detail"]], index=base["isin"] + "|" + base["subportfolio"])
    ret_l = pd.Series([d["metrics"]["return_ann_real"] for d in on["detail"]], index=on["isin"] + "|" + on["subportfolio"])
    pd.testing.assert_series_equal(ret_b.sort_index(), ret_l.sort_index())        # the persisted metric is the RAW one
    assert not any(c in on.columns for c in ("Entry_Fee_Pct", "Exit_Fee_Pct"))


def test_l4_with_no_fee_columns_or_a_free_cell_is_the_identity():
    f = lever_frame()
    pd.testing.assert_frame_equal(score(f.drop(columns=["Entry_Fee_Pct", "Exit_Fee_Pct"]), levers=L.LeverConfig(l4_h_years=3.0)).reset_index(drop=True),
                                  score(f.drop(columns=["Entry_Fee_Pct", "Exit_Fee_Pct"])).reset_index(drop=True), check_exact=True)
    free = f.copy()
    free["Entry_Fee_Pct"], free["Exit_Fee_Pct"] = 0.0, 0.0
    pd.testing.assert_frame_equal(score(free, levers=L.LeverConfig(l4_h_years=3.0)).reset_index(drop=True), score(free).reset_index(drop=True), check_exact=True)


def test_l4_lowers_the_rank_of_a_costly_fund_inside_its_nature():
    f = lever_frame()
    f["Entry_Fee_Pct"], f["Exit_Fee_Pct"] = 0.0, 0.0
    victim = f.index[(f["Fund_Nature"] == "Renta Variable")][0]
    f.loc[victim, "return_ann_real"] = f["return_ann_real"].max() + 0.01          # the best raw return of the whole frame
    f.loc[victim, "Entry_Fee_Pct"] = 0.30                                         # a 30 % entry fee over 2 years: a 15 pp a year penalty, far more than any spread between two funds
    base = score(f).set_index(["subportfolio", "isin"])["score_base"]
    on = score(f, levers=L.LeverConfig(l4_h_years=2.0)).set_index(["subportfolio", "isin"])["score_base"]
    sub_rows = [i for i in base.index if i[1] == victim]
    assert sub_rows and all(on[i] < base[i] for i in sub_rows)


def test_l2_multiplies_the_final_score_of_equilibrada_only():
    f = lever_frame()
    base, on = score(f), score(f, levers=L.LeverConfig(l2_z0=1.5))
    m = base.merge(on, on=["subportfolio", "isin"], suffixes=("_b", "_l"))
    assert (m["eligible_b"] == m["eligible_l"]).all() and (m["score_base_b"] == m["score_base_l"]).all()
    elig = m[m["eligible_b"] & (m["score_final_b"] > 0)]
    ratio = elig["score_final_l"] / elig["score_final_b"]                          # scores are persisted to 4 decimals: compare with a tolerance
    eq = elig["subportfolio"] == "Equilibrada"
    penalised = ratio < 0.9
    assert (ratio[penalised] - 0.80).abs().max() < 2e-3 and (ratio[~penalised] - 1.0).abs().max() < 1e-12
    assert penalised[eq].any() and not penalised[~eq].any()                        # only Equilibrada, and some funds of it


def test_l3_recovery_k_one_is_the_identity_and_k_zero_removes_the_bonus_and_the_drawdown_weight():
    f = lever_frame()
    pd.testing.assert_frame_equal(score(f, levers=L.LeverConfig(l3_k=1.0), recovery_k=1.0).reset_index(drop=True), score(f).reset_index(drop=True), check_exact=True)
    pd.testing.assert_frame_equal(score(f, levers=L.LeverConfig(l3_k=0.5), recovery_k=None).reset_index(drop=True), score(f).reset_index(drop=True), check_exact=True)   # state not recovery: nothing moves
    base, zero = score(f), score(f, levers=L.LeverConfig(l3_k=0.0), recovery_k=0.0)
    m = base.merge(zero, on=["subportfolio", "isin"], suffixes=("_b", "_l"))
    assert (m["eligible_b"] == m["eligible_l"]).all() and (m["score_base_b"] != m["score_base_l"]).any()


@pytest.mark.parametrize("k", [0.0, 0.5, 1.0])
def test_l3_base_score_equals_an_independent_hand_computation_for_a_monetario_cell(k):
    """Defensiva, Monetario only: score = sum_m w'_m * percentile(m) * b', with w' and b' the modulated weights and bonus (rebuilt here from the definitions, not from the module)."""
    f = lever_frame()
    cell = f[f["Fund_Nature"] == "Monetario"]
    w = dict(fs.SUBPORTFOLIO_WEIGHTS["Defensiva"])
    new_dd = w["max_dd"] * k
    scale = (sum(w.values()) - new_dd) / (sum(w.values()) - w["max_dd"])
    w2 = {m: (new_dd if m == "max_dd" else v * scale) for m, v in w.items()}
    bonus = 1.0 + (fs.NATURE_PROFILE_BONUS["Defensiva"]["Monetario"] - 1.0) * k
    expected = pd.Series(0.0, index=cell.index)
    for metric, weight in w2.items():
        if metric in cell.columns and cell[metric].notna().any():
            expected = expected.add(cell[metric].dropna().rank(pct=True, na_option="bottom") * weight, fill_value=0)
    expected = expected * bonus
    got = fs.compute_base_scores(cell, subportfolio="Defensiva", levers=L.LeverConfig(l3_k=k), recovery_k=k)
    pd.testing.assert_series_equal(got.sort_index(), expected.sort_index(), check_names=False, atol=1e-12)
    if k == 1.0:
        pd.testing.assert_series_equal(got.sort_index(), fs.compute_base_scores(cell, subportfolio="Defensiva").sort_index(), check_exact=True)


def test_baseline_eligible_isins_is_the_eligible_set_with_the_levers_off():
    f = lever_frame()
    elig = set(fs.baseline_eligible_isins(f))
    res = score(f)
    assert elig == set(res.loc[res["eligible"], "isin"])
    with_l1 = f.copy()
    assert set(fs.baseline_eligible_isins(with_l1)) == elig                       # it never reads a lever


# ================================================================ live loader ======================================================================================
def test_the_live_loader_asks_p2_only_for_the_metrics_of_the_levers_that_are_on():
    assert fs.lever_metric_requests(OFF) == []
    both = fs.lever_metric_requests(L.LeverConfig(l1_q=0.2, l2_z0=2.0, l3_k=0.5, l4_h_years=3.0))
    assert set(both) == {("max_dd", "rolling_5y", 0), ("return_ann", "rolling_3y", 0)}
    from shared.config import ROLLING_WINDOWS
    assert "rolling_5y" in ROLLING_WINDOWS and "rolling_3y" in ROLLING_WINDOWS
    assert fs.lever_metric_requests(L.LeverConfig(l4_h_years=3.0)) == [] and fs.lever_metric_requests(L.LeverConfig(l1_q=0.2)) == [("max_dd", "rolling_5y", 0)]
    assert fs.scoring_metric_requests("Expansion") == fs.scoring_metric_requests("Expansion")       # the default request list is untouched


# ================================================================ PIT wiring ===========================================================================================
def _world():
    """test_pit_candidates' synthetic universe, but WITH a CPI series: without it every real metric (return_ann_real) is NaN and L1 / L4 would have nothing to act on."""
    from test_pit_candidates import AT, DATES, _universe
    from proyecto3.src.pit_metrics import expanding_risk_metrics
    from proyecto3.src.pit_peer_metrics import alpha_persistence, capture_ratios, momentum_rank
    panel, nature, attrs, regimes = _universe()
    ipc = pd.DataFrame({"date": DATES, "ipc_index": 100.0 * 1.002 ** np.arange(len(DATES))})
    risk = expanding_risk_metrics(panel, ipc, None)
    peers = {**alpha_persistence(panel, nature), **capture_ratios(panel, nature)}
    mom = momentum_rank(risk["return_ann"], nature, AT, max_stale_days=45)
    return panel, nature, attrs, regimes, risk, peers, mom, AT


def _pit(world, **kw):
    from proyecto3.src.pit_candidates import pit_scores
    panel, nature, attrs, regimes, risk, peers, mom, AT = world
    return pit_scores(AT, risk, peers, attrs, regimes, momentum=mom, **kw)


def _lever_inputs(world, with_fees=True):
    panel, nature, attrs, regimes, risk, peers, mom, AT = world
    rng = np.random.default_rng(11)
    dd60 = pd.DataFrame(-np.abs(rng.normal(0.15, 0.08, panel.shape)), index=panel.index, columns=panel.columns)
    ret36 = pd.DataFrame(rng.normal(0.05, 0.1, panel.shape), index=panel.index, columns=panel.columns)
    fees = pd.DataFrame({"Entry_Fee_Pct": rng.choice([0.0, 0.03, 0.05], len(panel.columns)), "Exit_Fee_Pct": 0.0}, index=panel.columns) if with_fees else None
    return L.LeverInputs(dd60=dd60, ret36=ret36, fees=fees, nav_m=L.month_end_rows(panel))


@pytest.fixture(scope="module")
def world():
    return _world()


def test_pit_with_levers_off_or_absent_is_byte_identical(world):
    a, ua = _pit(world)
    b, ub = _pit(world, levers=OFF)
    c, uc = _pit(world, levers=OFF, lever_inputs=_lever_inputs(world))
    for x, ux in ((b, ub), (c, uc)):
        pd.testing.assert_frame_equal(a, x)
        pd.testing.assert_frame_equal(ua, ux)


def test_pit_with_a_lever_on_and_no_inputs_is_an_error_not_a_silent_noop(world):
    for cfg in (L.LeverConfig(l1_q=0.2), L.LeverConfig(l2_z0=2.0), L.LeverConfig(l3_k=0.5), L.LeverConfig(l4_h_years=3.0)):
        with pytest.raises(ValueError, match="lever"):
            _pit(world, levers=cfg)
    with pytest.raises(ValueError, match="fee"):
        _pit(world, levers=L.LeverConfig(l4_h_years=3.0), lever_inputs=_lever_inputs(world, with_fees=False))


def test_pit_l4_changes_scores_and_not_eligibility_and_flows_through_the_scorers_own_function(world):
    base, _ = _pit(world)
    on, _ = _pit(world, levers=L.LeverConfig(l4_h_years=2.0), lever_inputs=_lever_inputs(world))
    m = base.merge(on, on=["as_of", "subportfolio", "isin"], suffixes=("_b", "_l"))
    assert len(m) == len(base) and (m["eligible_b"] == m["eligible_l"]).all() and (m["score_base_b"] != m["score_base_l"]).any()


def test_pit_l1_excludes_through_the_scorers_filter(world):
    on, _ = _pit(world, levers=L.LeverConfig(l1_q=0.30), lever_inputs=_lever_inputs(world))
    base, _ = _pit(world)
    assert set(on["subportfolio"]) == set(base["subportfolio"])
    hit = on[on["exclusion_reason"].fillna("").str.startswith("l1_")]
    assert not hit["eligible"].any() and (hit["score_final"] == 0.0).all() and set(hit["subportfolio"]) <= {"Equilibrada", "Dinamica"}


def test_pit_l3_reads_the_pool_state_per_date_and_applies_k_only_in_recovery(world, monkeypatch):
    base, _ = _pit(world)
    cfg = L.LeverConfig(l3_k=0.0)
    monkeypatch.setattr(L, "pool_state", lambda returns: "normal")
    normal, _ = _pit(world, levers=cfg, lever_inputs=_lever_inputs(world))
    pd.testing.assert_frame_equal(base, normal)                                    # k never applies outside recovery
    monkeypatch.setattr(L, "pool_state", lambda returns: "recovery")
    rec, _ = _pit(world, levers=cfg, lever_inputs=_lever_inputs(world))
    assert (rec["score_base"].to_numpy() != base["score_base"].to_numpy()).any() and (rec["eligible"] == base["eligible"]).all()


def test_pit_universe_records_the_l3_state_per_date_only_when_l3_is_on(world, monkeypatch):
    _s, u_off = _pit(world)
    assert "l3_state" not in u_off.columns                                          # an all-off run keeps today's frame, column for column
    _s, u_l1 = _pit(world, levers=L.LeverConfig(l1_q=0.3), lever_inputs=_lever_inputs(world))
    assert "l3_state" not in u_l1.columns
    states = iter(["normal", "stress", "recovery", "normal", "recovery", "stress"])
    monkeypatch.setattr(L, "pool_state", lambda returns: next(states))
    _s, u_on = _pit(world, levers=L.LeverConfig(l3_k=0.5), lever_inputs=_lever_inputs(world))
    assert list(u_on["l3_state"]) == ["normal", "stress", "recovery", "normal", "recovery", "stress"]


def test_pit_l3_pool_is_the_baseline_eligible_set_and_never_reads_a_lever(world, monkeypatch):
    seen = []
    real = L.pool_returns_from_nav

    def spy(nav_m, isins, t, months=12):
        seen.append((pd.Timestamp(t), frozenset(isins)))
        return real(nav_m, isins, t, months)
    monkeypatch.setattr(L, "pool_returns_from_nav", spy)
    base, _ = _pit(world)
    _pit(world, levers=L.LeverConfig(l1_q=0.5, l3_k=0.5), lever_inputs=_lever_inputs(world))
    assert len(seen) == len(set(base["as_of"]))
    for t, isins in seen:                                                          # the pool is the BASELINE eligible set, even with L1 on (no circularity)
        assert isins == set(base.loc[(base["as_of"] == t) & base["eligible"], "isin"])
