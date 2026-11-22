"""Layer 1 (selection value) feasibility: `scripts/audit/p3/p3_l1_feasibility.py`. Invariants: (1) BLIND by construction: the result carries standard errors, counts and MDEs, never a mean or a difference
of the champion against the pool (a test pins the keys and scans the source); (2) the SE is `pit_p35a_prep.paired_se_annual_pp` of champion minus the mean pool draw; (3) the MDE and the months needed for a
floor follow from the SE; (4) a date missing in the pool is dropped and counted, never filled. Synthetic frames only (R-7)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts" / "audit" / "p3"))

import p3_l1_feasibility as F  # noqa: E402
from proyecto3.src import pit_p35a_prep as P  # noqa: E402

SCRIPT = _ROOT / "scripts" / "audit" / "p3" / "p3_l1_feasibility.py"


def _data(n=120, draws=5, seed=1, shift=0.0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2010-01-31", periods=n, freq="ME")
    champ = pd.Series(rng.normal(0.004, 0.02, n) + shift, index=dates)
    pool = pd.DataFrame([{"date": d, "draw": k, "gross": float(rng.normal(0.004, 0.02))} for d in dates for k in range(draws)])
    return champ, pool


def test_result_carries_standard_errors_only():
    champ, pool = _data()
    res = F.layer1_se(champ, pool)
    assert set(res) == {"se_pp", "se_single_draw_pp", "n", "dropped", "draws"}
    assert res["n"] == 120 and res["draws"] == 5 and res["dropped"] == 0


def test_the_se_does_not_depend_on_a_constant_shift_of_the_champion():
    champ, pool = _data()
    a, b = F.layer1_se(champ, pool), F.layer1_se(champ + 0.05, pool)
    assert a["se_pp"] == pytest.approx(b["se_pp"], rel=1e-9)               # a point estimate cannot leak through the SE: shifting the champion by 5 %/month changes nothing


def test_the_se_is_the_paired_block_bootstrap_of_champion_minus_the_mean_draw():
    champ, pool = _data()
    mean_draw = pool.groupby("date")["gross"].mean()
    ref = P.paired_se_annual_pp((champ - mean_draw.reindex(champ.index)).to_numpy())
    assert F.layer1_se(champ, pool)["se_pp"] == pytest.approx(ref)


def test_a_date_missing_in_the_pool_is_dropped_and_counted_never_filled():
    champ, pool = _data()
    pool = pool[pool["date"] != champ.index[7]]
    res = F.layer1_se(champ, pool)
    assert res["dropped"] == 1 and res["n"] == 119


def test_mde_and_the_months_needed_follow_from_the_se():
    out = F.feasibility(se_pp=0.52, n=261, floor_pp=0.5, alpha=0.05)
    assert out["mde_pp"] == pytest.approx(P.mde_pp(0.52, 0.05))
    assert out["se_needed_pp"] == pytest.approx(0.5 / P.mde_pp(1.0, 0.05))
    assert out["months_needed"] == pytest.approx(261 * (0.52 / out["se_needed_pp"]) ** 2)
    assert out["testable_at_floor"] is False
    assert F.feasibility(se_pp=0.15, n=261, floor_pp=0.5, alpha=0.05)["testable_at_floor"] is True       # MDE 0.37 pp <= the floor


def test_forward_horizon_scales_with_the_square_root_of_months():
    f = F.forward_mde(se_pp=0.52, n=261, months=(36, 144), alpha=0.05)
    assert f[36] == pytest.approx(P.mde_pp(0.52 * np.sqrt(261 / 36), 0.05)) and f[144] < f[36]


def test_the_script_never_takes_a_mean_of_the_champion_difference():
    src = SCRIPT.read_text(encoding="utf-8")
    import ast
    ast.parse(src)
    assert "paired_se_annual_pp" in src
    for bad in ("diff.mean", "diff.sum", ".mean() *", "np.mean(diff", "np.mean(d)"):
        assert bad not in src, bad
