# proyecto3/tests/test_pit_value.py
# -*- coding: utf-8 -*-
"""
Decision machinery of the P3 model-value study (proyecto3/src/pit_value.py) -- FND-0193. Synthetic tables with a known answer; the
verdict must follow the pre-registered rules mechanically. No DB (R-7).

    python -m pytest proyecto3/tests/test_pit_value.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import pit_value as pv

IDX = pd.date_range("2005-01-31", periods=260, freq=pd.offsets.MonthEnd())


def _table(mean, vol=0.012, seed=0, turnover=0.1):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"net_turnover_1m": rng.normal(mean, vol, len(IDX)), "cash_ret_1m": 0.001, "turnover": turnover,
                         "regime": "Expansion"}, index=IDX)


def _tabs(model_mean, base_mean):
    tabs = {pv.MODEL: _table(model_mean, seed=1)}
    for i, n in enumerate([*pv.PRIMARY, *pv.SECONDARY]):
        tabs[n] = _table(base_mean, seed=10 + i)
    return tabs


def test_ledger_lock_accepts_the_committed_content_and_aborts_on_any_edit(tmp_path):
    locked = b'{"N_scan_upper": 437,\n "N_scan_x2": 874}\n'
    f = tmp_path / "ledger.json"
    f.write_bytes(locked.replace(b"\n", b"\r\n"))                              # Windows line endings are not an edit
    assert len(pv.verify_ledger_lock(f, blob_reader=lambda: locked)) == 64
    f.write_bytes(b'{"N_scan_upper": 400,\n "N_scan_x2": 800}\n')               # a trial "removed"
    with pytest.raises(pv.LedgerLockError, match="locked at the pre-registration"):
        pv.verify_ledger_lock(f, blob_reader=lambda: locked)
    with pytest.raises(pv.LedgerLockError, match="cannot read"):
        pv.verify_ledger_lock(f, repo=tmp_path, tag="no-such-tag")               # a missing tag is a failure, never a pass


def test_the_committed_ledger_matches_the_real_tag():
    import subprocess
    root = Path(__file__).resolve().parents[2]
    if subprocess.run(["git", "-C", str(root), "rev-parse", "-q", "--verify", f"refs/tags/{pv.PREREG_TAG}"], capture_output=True).returncode != 0:
        pytest.skip("the pre-registration tag exists only in the study's repository")
    path = root / pv.LEDGER_RELPATH
    assert pv.verify_ledger_lock(path, repo=root)
    assert pv.load_ledger(path)["N_scan_upper"] == 437


def test_family_keys_group_multiplier_components():
    detail = pd.DataFrame({"as_of": [IDX[0]], "isin": ["A"], "m_alpha_persistence_bonus": [1.15], "m_fx_malus": [0.8],
                           "m_regime_return_bonus": [1.1], "m_regime_coverage_damp": [0.5], "m_crisis_mdd_deep_malus": [0.9],
                           "m_sharpe_slope_improving_bonus": [1.05], "m_roll_vol_high_pctile_malus": [0.9], "m_weird": [1.0], "x_sharpe": [0.3]})
    fam = pv.family_keys(detail)
    assert fam["alpha"] == ["alpha_persistence_bonus"] and fam["fx"] == ["fx_malus"]
    assert sorted(fam["regime"]) == ["regime_coverage_damp", "regime_return_bonus"]
    assert fam["crisis"] == ["crisis_mdd_deep_malus"] and fam["slope"] == ["sharpe_slope_improving_bonus"]
    assert fam["roll"] == ["roll_vol_high_pctile_malus"] and fam["other"] == ["weird"]


def test_e1_full_holm_flags_a_clearly_better_model_and_a_clearly_worse_one():
    better = pv.e1_full(_tabs(0.010, 0.002), n_boot=400)
    assert better.loc[list(pv.PRIMARY), "holm_M_better"].all() and not better.loc[list(pv.PRIMARY), "holm_B_better"].any()
    worse = pv.e1_full(_tabs(0.002, 0.010), n_boot=400)
    assert worse.loc[list(pv.PRIMARY), "holm_B_better"].all() and not worse.loc[list(pv.PRIMARY), "holm_M_better"].any()
    same = pv.e1_full(_tabs(0.005, 0.005), n_boot=400)
    assert not same.loc[list(pv.PRIMARY), "holm_M_better"].all()


def test_subperiods_and_loeo_follow_the_sign_of_the_edge():
    tabs = _tabs(0.012, 0.002)
    sub, loeo = pv.e1_subperiods(tabs, with_early=True), pv.e1_loeo(tabs)
    assert list(sub.columns) == ["2005-2010", "2011-2016", "2017-2021", "2022-2026", "early"]
    assert sub.loc[list(pv.PRIMARY), ["2005-2010", "2011-2016", "2017-2021"]].gt(0).all().all()
    assert np.isnan(sub.loc["B-POOL", "early"])                       # no months before 2005 in this table
    assert (loeo.loc[list(pv.PRIMARY)] > 0).all().all()
    assert list(loeo.columns) == ["without 2005-11..2007-08", "without 2008", "without 2020", "without 2022"]


def _verdict_inputs(model_mean, base_mean, dsr=0.95, pbo=0.1, worst_dd=-0.10, p_model=0.0, p_pool=0.01, early=True):
    tabs = _tabs(model_mean, base_mean)
    e1, sub, loeo = pv.e1_full(tabs, n_boot=400), pv.e1_subperiods(tabs), pv.e1_loeo(tabs)
    wf_vs = pd.DataFrame({"diff": {n: (model_mean - base_mean) * 100 for n in pv.PRIMARY}})
    return e1, sub, loeo, wf_vs, {"dsr_2N": dsr}, {"pbo": pbo}, {"worst_max_dd": worst_dd, "model_prob_36m": p_model, "pool_prob_36m": p_pool}, early


def test_verdict_keep_simplify_replace_and_the_breakers():
    assert pv.verdict(*_verdict_inputs(0.012, 0.002))["outcome"] == "KEEP"
    assert pv.verdict(*_verdict_inputs(0.012, 0.002, dsr=0.5))["outcome"] == "SIMPLIFY"             # DSR at 2N below 0.90
    assert pv.verdict(*_verdict_inputs(0.012, 0.002, pbo=0.45))["outcome"] == "SIMPLIFY"            # overfitting
    assert pv.verdict(*_verdict_inputs(0.012, 0.002, worst_dd=-0.16))["outcome"] == "SIMPLIFY"      # mandate breached in one evaluation
    assert pv.verdict(*_verdict_inputs(0.012, 0.002, p_model=0.05, p_pool=0.01))["outcome"] == "SIMPLIFY"
    assert pv.verdict(*_verdict_inputs(0.012, 0.002, early=False))["outcome"] == "SIMPLIFY"         # sign fails in the early sub-period
    assert pv.verdict(*_verdict_inputs(0.002, 0.012))["outcome"] == "REPLACE_PROPOSAL"              # a baseline wins with a CI excluding 0
    v = pv.verdict(*_verdict_inputs(0.012, 0.002))
    assert all(c["pass"] for c in v["criteria"].values()) and len(v["criteria"]) == 6


def test_layer_verdict_keeps_a_layer_only_when_removing_it_hurts():
    m = _table(0.008, seed=1)
    strong_loss = _table(0.002, seed=2)                               # removing the layer collapses the return: it earns its place
    gain = _table(0.012, seed=3)                                      # removing the layer is better: it does not
    deeper = _table(0.008, seed=1)
    deeper.iloc[100:110, 0] = -0.03                                   # same Sharpe-ish but a much deeper drawdown
    out = pv.layer_verdicts({pv.MODEL: m, "ABL_noMult": strong_loss, "ABL_eqW": gain, "ABL_staticMix": deeper}, n_boot=400)
    assert bool(out.loc["ABL_noMult", "layer_earns_its_place"]) is True
    assert bool(out.loc["ABL_eqW", "layer_earns_its_place"]) is False
    assert bool(out.loc["ABL_staticMix", "layer_earns_its_place"]) is True and out.loc["ABL_staticMix", "dd_worsens_pp"] > 2.0
