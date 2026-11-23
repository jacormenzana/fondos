"""Package L, P3-9 (doc/decisiones/PREREG_P3_9_LAYER3.md section 8): the two layer-3 levers of proyecto3/src/ranking_levers.py and their wiring. Synthetic frames only (R-7).
Invariants pinned: (1) both ship OFF and an all-off configuration changes NOTHING: the cache key of every configuration that does not use them is byte-identical to what it was before they
existed; (2) L5 (nature-bonus scale k) turns every bonus b into 1 + (b - 1) k in all three sub-portfolios and nothing else, k = 1 is the identity and k = 0 removes the bonus; (3) L6 (chasing
control f) multiplies the weights of `return_ann_real` and `alpha_persistence` by f in Equilibrada ONLY, rescales the others proportionally so the total stays 1, and f = 1 is the identity;
(4) a lever that is ON moves the key; (5) the PIT needs no extra inputs for them and the AC shadow refuses to mix arms when either is ON."""
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
from proyecto3.src import fee_penalty_shadow as fp  # noqa: E402
from proyecto3.src import fund_scorer as fs  # noqa: E402
from proyecto3.src import ranking_levers as L  # noqa: E402

OFF = L.LeverConfig()


def _frame(n=420, seed=5):
    f = make_scoring_frame(n=n, seed=seed)
    f["fund_family_id"] = None
    f["return_ann_real"] = np.random.default_rng(seed + 1).normal(0.03, 0.02, len(f))
    return f


def _base(frame, sub, levers=None):
    mask = frame["Fund_Nature"].isin(fs.SUBPORTFOLIO_MAPPING[sub])
    return fs.compute_base_scores(frame[mask], subportfolio=sub, levers=levers)


# ================================================================ configuration =====================================================================================
def test_both_switches_ship_off_and_default_config_is_all_off():
    import shared.config as C
    for name in ("P3_L5_NATURE_SCALE_ENABLED", "P3_L6_CHASE_CONTROL_ENABLED"):
        assert getattr(C, name) is False, name
    cfg = L.LeverConfig.from_config()
    assert cfg == OFF and not cfg.any_on and cfg.l5_nature_k is None and cfg.l6_chase_f is None
    assert (C.P3_L5_NATURE_K, C.P3_L6_CHASE_F) == (0.5, 0.5)                     # the milder preregistered value is the default; k 0 and f 0 are the other


def test_from_config_reads_the_two_switches_and_their_values(monkeypatch):
    import shared.config as C
    monkeypatch.setattr(C, "P3_L5_NATURE_SCALE_ENABLED", True)
    monkeypatch.setattr(C, "P3_L5_NATURE_K", 0.0)
    monkeypatch.setattr(C, "P3_L6_CHASE_CONTROL_ENABLED", True)
    monkeypatch.setattr(C, "P3_L6_CHASE_F", 0.5)
    cfg = L.LeverConfig.from_config()
    assert cfg == L.LeverConfig(l5_nature_k=0.0, l6_chase_f=0.5) and cfg.any_on
    assert not cfg.uses_inputs and L.metric_columns(cfg) == ()                  # neither reads a metric the frame does not already carry


def test_the_key_of_a_config_that_does_not_use_them_is_what_it_was_before():
    base = L.LeverConfig(l1_q=0.2, l2_z0=2.0, l3_k=0.5, l4_h_years=3.0)
    legacy = {"l1_q": 0.2, "l2_z0": 2.0, "l3_k": 0.5, "l4_h_years": 3.0,
              "const": {"l1_dd_backstop": L.L1_DD_BACKSTOP, "l1_ret_delta": L.L1_RET_BACKSTOP_DELTA, "min_cell": L.MIN_CELL, "l2_mult": L.L2_MULT, "mad": L.MAD_SCALE,
                        "l3": [L.L3_TRAILING_MONTHS, L.L3_STRESS_THRESHOLD, L.L3_RECOVERY_RETURN_MONTHS, L.L3_RECOVERY_CONFIRMATIONS, L.L3_MIN_REBOUND]}}
    assert base.key() == legacy                                                   # the warm score caches of P3-5A / P3-7 stay valid, byte for byte


def test_the_key_moves_with_each_value_of_the_new_levers():
    keys = [L.LeverConfig(l5_nature_k=0.5).key(), L.LeverConfig(l5_nature_k=0.0).key(), L.LeverConfig(l6_chase_f=0.5).key(), L.LeverConfig(l6_chase_f=0.0).key(),
            L.LeverConfig(l5_nature_k=0.5, l6_chase_f=0.5).key(), L.LeverConfig(l4_h_years=0.58).key(), L.LeverConfig(l4_h_years=0.58, l5_nature_k=0.5).key()]
    assert len({repr(sorted(k.items(), key=str)) for k in keys}) == len(keys)
    from proyecto3.src.pit_run import lever_key_parts
    assert lever_key_parts(OFF, None) == [] and lever_key_parts(L.LeverConfig(l5_nature_k=0.5), None)


# ================================================================ pure functions ===================================================================================
def test_scale_weights_identity_sum_and_proportional_rescale():
    w = {"return_ann_real": 0.25, "sharpe": 0.20, "max_dd": 0.20, "alpha_persistence": 0.15, "capture_ratio": 0.10, "momentum_rank": 0.10}
    assert L.scale_weights(w, L.L6_METRICS, 1.0) == pytest.approx(w)
    half = L.scale_weights(w, L.L6_METRICS, 0.5)
    assert sum(half.values()) == pytest.approx(1.0)
    assert half["return_ann_real"] == pytest.approx(0.125) and half["alpha_persistence"] == pytest.approx(0.075)
    others = [m for m in w if m not in L.L6_METRICS]
    ratios = {half[m] / w[m] for m in others}
    assert len(ratios) == 1 or max(ratios) - min(ratios) < 1e-12                  # the others are scaled by ONE factor
    assert next(iter(ratios)) == pytest.approx(0.8 / 0.6)
    zero = L.scale_weights(w, L.L6_METRICS, 0.0)
    assert zero["return_ann_real"] == 0.0 and zero["alpha_persistence"] == 0.0 and sum(zero.values()) == pytest.approx(1.0)


def test_scale_weights_ignores_absent_metrics_and_a_set_that_is_the_whole_total():
    w = {"sharpe": 0.6, "max_dd": 0.4}
    assert L.scale_weights(w, L.L6_METRICS, 0.0) == pytest.approx(w)             # none of the metrics is weighted: nothing happens
    only = {"return_ann_real": 1.0}
    assert L.scale_weights(only, L.L6_METRICS, 0.5) == pytest.approx(only)       # nothing to rescale into: unchanged, never a total of 0.5


def test_the_constants_are_the_preregistered_ones():
    assert L.L6_SUBS == ("Equilibrada",) and set(L.L6_METRICS) == {"return_ann_real", "alpha_persistence"}


# ================================================================ the scorer ========================================================================================
def test_l5_zero_removes_every_bonus_in_all_three_sub_portfolios():
    f = _frame()
    for sub, bonus in fs.NATURE_PROFILE_BONUS.items():
        on = _base(f, sub, L.LeverConfig(l5_nature_k=0.0))
        off = _base(f, sub)
        nat = f.loc[off.index, "Fund_Nature"]
        factor = nat.map(bonus).fillna(1.0)
        assert (on * factor - off).abs().max() < 1e-12, sub                      # no bonus x bonus = the baseline score
        assert not np.allclose(on.to_numpy(), off.to_numpy())                     # and the lever does something


def test_l5_half_halves_the_excess_of_the_bonus_and_one_is_the_identity():
    f = _frame()
    off = _base(f, "Defensiva")
    assert (_base(f, "Defensiva", L.LeverConfig(l5_nature_k=1.0)) - off).abs().max() < 1e-12
    nat = f.loc[off.index, "Fund_Nature"]
    b = nat.map(fs.NATURE_PROFILE_BONUS["Defensiva"]).fillna(1.0)
    half = _base(f, "Defensiva", L.LeverConfig(l5_nature_k=0.5))
    assert (half * (1 + (b - 1) * 1.0) - off * (1 + (b - 1) * 0.5)).abs().max() < 1e-12      # score_k / (1 + (b - 1) k) is the same for every k


def test_l6_changes_equilibrada_only_and_f_one_is_the_identity():
    f = _frame()
    cfg = L.LeverConfig(l6_chase_f=0.0)
    for sub in ("Defensiva", "Dinamica"):
        pd.testing.assert_series_equal(_base(f, sub, cfg), _base(f, sub))
    assert not np.allclose(_base(f, "Equilibrada", cfg).to_numpy(), _base(f, "Equilibrada").to_numpy())
    pd.testing.assert_series_equal(_base(f, "Equilibrada", L.LeverConfig(l6_chase_f=1.0)), _base(f, "Equilibrada"))


def test_l6_zero_ignores_a_change_of_the_two_metrics():
    f = _frame()
    g = f.copy()
    g["alpha_persistence"] = np.random.default_rng(99).uniform(0, 1, len(g))      # a different chasing signal
    g["return_ann_real"] = np.random.default_rng(98).normal(0.03, 0.05, len(g))
    cfg = L.LeverConfig(l6_chase_f=0.0)
    mask = f["Fund_Nature"].isin(fs.SUBPORTFOLIO_MAPPING["Equilibrada"])
    a, b = _base(f, "Equilibrada", cfg), _base(g, "Equilibrada", cfg)
    pd.testing.assert_series_equal(a[mask[mask].index], b[mask[mask].index])      # with f = 0 the two metrics carry no weight: the score cannot depend on them


def test_the_whole_scorer_with_both_levers_off_equals_none_and_with_them_on_differs():
    f = _frame()
    a = fs.score_funds_from_df(f.copy(), "Expansion", verbose=False)
    b = fs.score_funds_from_df(f.copy(), "Expansion", verbose=False, levers=OFF)
    pd.testing.assert_frame_equal(a, b)
    c = fs.score_funds_from_df(f.copy(), "Expansion", verbose=False, levers=L.LeverConfig(l5_nature_k=0.0, l6_chase_f=0.0))
    assert not np.allclose(a["score_final"].to_numpy(dtype=float), c["score_final"].to_numpy(dtype=float))
    assert set(a["isin"]) == set(c["isin"]) and list(a.columns) == list(c.columns)


# ================================================================ the PIT and the AC shadow ==========================================================================
def test_the_pit_runs_the_new_levers_without_lever_inputs():
    from test_ranking_levers_p35a import _pit, _world
    world = _world()
    a, _ = _pit(world)
    b, _ = _pit(world, levers=L.LeverConfig(l5_nature_k=0.0, l6_chase_f=0.0))      # no lever_inputs: neither lever reads one
    assert len(a) == len(b) and not np.allclose(a["score_final"].to_numpy(dtype=float), b["score_final"].to_numpy(dtype=float))
    c, _ = _pit(world, levers=OFF)
    pd.testing.assert_frame_equal(a, c)


def test_the_ac_shadow_refuses_to_mix_arms_when_a_new_lever_is_on():
    assert fp.other_levers_on(OFF) is False and fp.other_levers_on(L.LeverConfig(l4_h_years=0.58)) is False
    assert fp.other_levers_on(L.LeverConfig(l5_nature_k=0.5)) is True and fp.other_levers_on(L.LeverConfig(l6_chase_f=0.0)) is True
