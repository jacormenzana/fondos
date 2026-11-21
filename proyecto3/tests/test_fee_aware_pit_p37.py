"""Package L, P3-7 (doc/decisiones/PREREG_P3_7.md section 4, arm B): the fee-aware class choice in the PIT, per trial, THROUGH THE SCORER's own family de-duplication.
Why this file exists: the first build put the choice in the engine (`pit_backtest.candidates_by_sub`), where it was a NO-OP, because the PIT scorer (`score_funds_from_df` -> `deduplicate_by_family`) already
keeps one class per family before the engine sees a candidate: arm B came out identical to the baseline and AB identical to A1 (found by the integrity check before any performance number was read). The rule
is the scorer's (`fund_scorer._fee_aware_pick`, the "rule of session 1"), so the per-trial parameter goes there.
Invariants pinned: (1) with the parameter absent / False / None under an OFF switch the PIT output and the cache key are byte-identical to what they were; (2) ON, the representative of a family is the cheaper
class where the rule says so, and it is NOT the best-score class (the test fails if the lever is a no-op); (3) ON without the fee frame is an ERROR, never a silent no-op; (4) the lever enters the score cache
key only when ON, with its tolerance; (5) the parameter reaches `compute_pit_scores` and `score_funds_from_df`. Synthetic frames only (R-7)."""
import inspect
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from proyecto3.src import fund_scorer as fs  # noqa: E402
from proyecto3.src import ranking_levers as L  # noqa: E402
from test_ranking_levers_p35a import _lever_inputs, _pit, _world  # noqa: E402


@pytest.fixture(scope="module")
def fam_world():
    """The synthetic universe with its funds paired into two-class families, one recurring charge for everybody and a cheap and an expensive class in each family."""
    panel, nature, attrs, regimes, risk, peers, mom, AT = _world()
    attrs = attrs.copy()
    isins = list(attrs.index)
    attrs["fund_family_id"] = [f"FAM{k // 2}" for k in range(len(isins))]
    attrs["Ongoing_Charge"] = 0.01
    fees = pd.DataFrame({"Entry_Fee_Pct": [0.05 if k % 2 == 0 else 0.0 for k in range(len(isins))], "Exit_Fee_Pct": 0.0}, index=isins)   # the even class is the expensive one
    li = L.LeverInputs(dd60=None, ret36=None, fees=fees, nav_m=None)
    return (panel, nature, attrs, regimes, risk, peers, mom, AT), li


def reps(scores):
    return scores.groupby(["as_of", "fund_family_id", "subportfolio"])["isin"].apply(frozenset)


def test_the_parameter_off_absent_or_none_leaves_the_pit_output_byte_identical(fam_world):
    w, li = fam_world
    a, ua = _pit(w)
    for kw in ({"fee_aware": False}, {"fee_aware": None}, {"fee_aware": False, "fee_tolerance": 0.5}):
        b, ub = _pit(w, lever_inputs=li, **kw)
        pd.testing.assert_frame_equal(a, b)
        pd.testing.assert_frame_equal(ua, ub)


def test_on_the_cheaper_class_represents_the_family_and_it_is_not_a_no_op(fam_world):
    w, li = fam_world
    off, _ = _pit(w)
    on, _ = _pit(w, lever_inputs=li, fee_aware=True, fee_tolerance=1.0)           # a tolerance of 1.0: any score gap is allowed, so only the fee rule decides
    assert not on.equals(off), "arm B must change the representatives: identical output would be the no-op this file exists to catch"
    cheap = {i for i in li.fees.index if li.fees.loc[i, "Entry_Fee_Pct"] == 0.0}
    both = off.merge(on, on=["as_of", "subportfolio", "isin"], how="outer", indicator=True, suffixes=("_off", "_on"))
    gained = both[both["_merge"] == "right_only"]
    lost = both[both["_merge"] == "left_only"]
    assert len(gained) > 0 and set(gained["isin"]) <= cheap                          # what enters is always a cheap-entry class
    assert set(lost["isin"]) <= set(li.fees.index) - cheap                           # what leaves is the expensive class of the same family


def test_on_it_keeps_one_row_per_family_and_sub_portfolio_and_the_eligibility_of_the_kept_rows(fam_world):
    w, li = fam_world
    on, _ = _pit(w, lever_inputs=li, fee_aware=True, fee_tolerance=1.0)
    assert not on.duplicated(["as_of", "fund_family_id", "subportfolio"]).any()


def test_the_tolerance_is_honoured(fam_world):
    w, li = fam_world
    tight, _ = _pit(w, lever_inputs=li, fee_aware=True, fee_tolerance=0.0)
    loose, _ = _pit(w, lever_inputs=li, fee_aware=True, fee_tolerance=1.0)
    off, _ = _pit(w)
    assert len(tight.merge(off, on=["as_of", "subportfolio", "isin"])) >= len(loose.merge(off, on=["as_of", "subportfolio", "isin"]))   # the looser the tolerance the more families change class


def test_on_without_the_fee_frame_is_an_error_not_a_silent_noop(fam_world):
    w, li = fam_world
    with pytest.raises(ValueError, match="fee"):
        _pit(w, fee_aware=True)
    with pytest.raises(ValueError, match="fee"):
        _pit(w, lever_inputs=L.LeverInputs(dd60=None, ret36=None, fees=None, nav_m=None), fee_aware=True)


def test_the_cache_key_changes_only_when_the_lever_is_on_and_with_its_tolerance():
    from proyecto3.src.pit_run import fee_aware_key_parts
    assert fee_aware_key_parts(False, 0.02) == [] and fee_aware_key_parts(None, 0.02) == []        # None = the global switch, OFF: nothing is added, the warm caches stay valid
    on, on2 = fee_aware_key_parts(True, 0.02), fee_aware_key_parts(True, 0.05)
    assert on and on != on2 and on == fee_aware_key_parts(True, 0.02)


def test_the_global_switch_when_on_also_enters_the_key(monkeypatch):
    import shared.config as C
    from proyecto3.src.pit_run import fee_aware_key_parts
    monkeypatch.setattr(C, "P3_FEE_AWARE_CLASS_ENABLED", True)
    assert fee_aware_key_parts(None, None) == fee_aware_key_parts(True, C.P3_FEE_AWARE_CLASS_SCORE_TOLERANCE)     # a switch that changes the scores can never hit a stale cache


def test_the_parameter_reaches_the_scorer_and_the_pit_entry_points():
    from proyecto3.src.pit_candidates import pit_scores
    from proyecto3.src.pit_run import compute_pit_scores
    for fn in (fs.score_funds_from_df, pit_scores, compute_pit_scores):
        params = inspect.signature(fn).parameters
        assert "fee_aware" in params and "fee_tolerance" in params and params["fee_aware"].default is None, fn.__name__
