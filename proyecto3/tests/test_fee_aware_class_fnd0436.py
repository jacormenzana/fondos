"""FND-0436 / Package L Track 1 (T1b): the fee-aware class choice of `fund_scorer.deduplicate_by_family` (switch P3_FEE_AWARE_CLASS_ENABLED, ships OFF). Synthetic frames only (R-7).
The rule (plan rev. 3.1, section 4): within a family and sub-portfolio let B be the class with the best `score_final`; choose instead the class C with the lowest Entry_Fee_Pct + Exit_Fee_Pct
only if ALL hold: C is eligible and so is B; score_final(C) >= score_final(B) - tolerance (0.02); Ongoing_Charge(C) <= Ongoing_Charge(B); C's one-off fees are strictly lower than B's. An unknown
(NaN / null / absent) Ongoing_Charge, Entry_Fee_Pct or Exit_Fee_Pct in C OR in B keeps B. Invariants pinned: (1) a higher-TER class is never chosen; (2) with the switch OFF the representatives are
exactly the unchanged rule's (the shadow only counts); (3) an unknown fee, in either class and in any of the three fields, keeps B; (4) an ineligible class is never chosen; (5) the private fee columns
never reach the result of `score_funds_from_df`."""
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

OC, EN, EX = fs.FEE_AWARE_COLUMNS


def fam(rows, family="F1", sub="Defensiva"):
    """rows: (isin, score_final, ongoing, entry, exit[, eligible]) -> the frame deduplicate_by_family receives (RangeIndex, one family, one sub-portfolio)."""
    recs = [{"isin": r[0], "fund_family_id": family, "subportfolio": sub, "score_final": r[1], OC: r[2], EN: r[3], EX: r[4], "eligible": (r[5] if len(r) > 5 else True)} for r in rows]
    return pd.DataFrame(recs)


def kept(df, **kw):
    return sorted(fs.deduplicate_by_family(df.copy(), verbose=False, **kw)["isin"])


B_ROW = ("B", 0.80, 0.010, 0.05, 0.00)                 # the best score, a 5 % entry fee
CHEAP = ("C", 0.79, 0.010, 0.00, 0.00)                 # within tolerance, same TER, no one-off fee: the case the rule exists for


def test_the_default_rule_is_off_and_the_tolerance_is_the_reviewers_value():
    assert fs._shared_config.P3_FEE_AWARE_CLASS_ENABLED is False
    assert fs._shared_config.P3_FEE_AWARE_CLASS_SCORE_TOLERANCE == pytest.approx(0.02)


def test_on_chooses_the_class_with_the_lower_one_off_fees_within_the_tolerance():
    df = fam([B_ROW, CHEAP])
    assert kept(df, fee_aware=True) == ["C"]
    assert kept(df, fee_aware=False) == ["B"]


def test_a_class_with_a_higher_ongoing_charge_is_never_chosen_even_with_no_one_off_fee():
    df = fam([B_ROW, ("C", 0.80, 0.0101, 0.00, 0.00)])     # TER a hair above B's
    assert kept(df, fee_aware=True) == ["B"]
    assert kept(fam([B_ROW, ("C", 0.80, 0.010, 0.00, 0.00)]), fee_aware=True) == ["C"]       # equal TER is allowed


def test_the_score_tolerance_is_inclusive_and_a_lower_score_keeps_the_best_class():
    assert kept(fam([B_ROW, ("C", 0.78, 0.010, 0.0, 0.0)]), fee_aware=True) == ["C"]          # exactly B - 0.02
    assert kept(fam([B_ROW, ("C", 0.7799, 0.010, 0.0, 0.0)]), fee_aware=True) == ["B"]
    assert kept(fam([B_ROW, ("C", 0.7799, 0.010, 0.0, 0.0)]), fee_aware=True, tolerance=0.05) == ["C"]


def test_one_off_fees_must_be_strictly_lower():
    assert kept(fam([B_ROW, ("C", 0.80, 0.010, 0.05, 0.00)]), fee_aware=True) == ["B"]        # equal entry + exit: no change
    assert kept(fam([B_ROW, ("C", 0.80, 0.010, 0.02, 0.02)]), fee_aware=True) == ["C"]        # 4 % < 5 %


@pytest.mark.parametrize("field", [OC, EN, EX])
@pytest.mark.parametrize("missing", [np.nan, None])
def test_an_unknown_fee_in_the_candidate_keeps_the_best_class(field, missing):
    df = fam([B_ROW, CHEAP])
    df[field] = df[field].astype(object)
    df.loc[df["isin"] == "C", field] = missing
    assert kept(df, fee_aware=True) == ["B"]


@pytest.mark.parametrize("field", [OC, EN, EX])
@pytest.mark.parametrize("missing", [np.nan, None])
def test_an_unknown_fee_in_the_best_class_keeps_it(field, missing):
    df = fam([B_ROW, CHEAP])
    df[field] = df[field].astype(object)
    df.loc[df["isin"] == "B", field] = missing
    assert kept(df, fee_aware=True) == ["B"]


@pytest.mark.parametrize("field", [OC, EN, EX])
def test_a_negative_fee_is_unknown_never_a_bargain(field):
    df = fam([B_ROW, CHEAP])
    df.loc[df["isin"] == "C", field] = -0.01
    assert kept(df, fee_aware=True) == ["B"]


def test_absent_fee_columns_keep_the_best_class_exactly_as_before():
    df = fam([B_ROW, CHEAP]).drop(columns=[OC, EN, EX])
    assert kept(df, fee_aware=True) == kept(df, fee_aware=False) == ["B"]


def test_an_ineligible_class_is_never_chosen_and_an_ineligible_best_keeps_the_rule_off():
    ineligible_cheap = fam([B_ROW, ("C", 0.79, 0.010, 0.0, 0.0, False)])
    assert kept(ineligible_cheap, fee_aware=True) == ["B"]
    ineligible_best = fam([("B", 0.0, 0.010, 0.05, 0.0, False), ("C", 0.0, 0.010, 0.0, 0.0, True)])      # an excluded best scores 0: its siblings tie at 0
    assert kept(ineligible_best, fee_aware=True) == kept(ineligible_best, fee_aware=False)


def test_among_several_candidates_the_lowest_one_off_wins_then_the_higher_score():
    df = fam([B_ROW, ("C1", 0.79, 0.010, 0.02, 0.0), ("C2", 0.785, 0.009, 0.01, 0.0), ("C3", 0.79, 0.008, 0.01, 0.0)])
    assert kept(df, fee_aware=True) == ["C3"]                                                    # C2 and C3 tie at 1 %; C3 has the higher score


def test_a_singleton_family_and_rows_without_a_family_are_untouched():
    df = pd.concat([fam([B_ROW]), fam([("S", 0.5, 0.01, 0.05, 0.0)], family="F2"), fam([("N1", 0.5, 0.01, 0.05, 0.0)], family=None), fam([("N2", 0.5, 0.01, 0.0, 0.0)], family="")],
                   ignore_index=True)
    assert kept(df, fee_aware=True) == kept(df, fee_aware=False) == ["B", "N1", "N2", "S"]


def test_the_shadow_counts_the_would_be_switches_even_when_the_switch_is_off():
    df = pd.concat([fam([B_ROW, CHEAP]), fam([B_ROW, ("C", 0.5, 0.01, 0.0, 0.0)], family="F2"), fam([B_ROW], family="F3")], ignore_index=True)
    off = fs.deduplicate_by_family(df.copy(), verbose=False, fee_aware=False)
    on = fs.deduplicate_by_family(df.copy(), verbose=False, fee_aware=True)
    assert off.attrs["fee_aware_shadow"] == {"enabled": False, "families": 2, "would_switch": 1}      # F1 would switch; F2's cheap class is below the tolerance; F3 is a singleton
    assert on.attrs["fee_aware_shadow"] == {"enabled": True, "families": 2, "would_switch": 1}
    assert sorted(off["isin"]) == ["B", "B", "B"] and sorted(on["isin"]) == ["B", "B", "C"]            # the same isin label repeats across families in this synthetic frame


def test_the_switch_is_read_from_the_config_when_not_passed(monkeypatch):
    df = fam([B_ROW, CHEAP])
    assert kept(df) == ["B"]
    monkeypatch.setattr(fs._shared_config, "P3_FEE_AWARE_CLASS_ENABLED", True)
    assert kept(df) == ["C"]


# ---- through the pure scoring core --------------------------------------------------------------------------------------------------------------------------
def _with_fees(frame, seed=3):
    rng = np.random.default_rng(seed)
    out = frame.copy()
    out["Entry_Fee_Pct"] = rng.choice([0.0, 0.01, 0.03, 0.05, np.nan], size=len(out))
    out["Exit_Fee_Pct"] = rng.choice([0.0, 0.0, 0.01], size=len(out))
    return out


def test_with_the_switch_off_fee_columns_in_the_input_change_nothing_and_never_reach_the_result():
    frame = make_scoring_frame()
    plain = fs.score_funds_from_df(frame.copy(), "Expansion", verbose=False)
    with_fees = fs.score_funds_from_df(_with_fees(frame), "Expansion", verbose=False)
    pd.testing.assert_frame_equal(plain.reset_index(drop=True), with_fees.reset_index(drop=True), check_exact=True)
    assert not any(c in with_fees.columns for c in fs.FEE_AWARE_COLUMNS)


def test_with_the_switch_on_the_result_keeps_one_class_per_family_and_no_private_column(monkeypatch):
    frame = _with_fees(make_scoring_frame())
    off = fs.score_funds_from_df(frame.copy(), "Expansion", verbose=False)
    monkeypatch.setattr(fs._shared_config, "P3_FEE_AWARE_CLASS_ENABLED", True)
    on = fs.score_funds_from_df(frame.copy(), "Expansion", verbose=False)
    assert not any(c in on.columns for c in fs.FEE_AWARE_COLUMNS)
    assert len(on) == len(off) and set(on.columns) == set(off.columns)
    key = ["fund_family_id", "subportfolio"]
    has_fam = lambda d: d[d["fund_family_id"].notna() & (d["fund_family_id"].astype(str) != "")]       # noqa: E731  (rows without a family are their own family)
    assert has_fam(on).groupby(key).size().max() == 1
    m = has_fam(off).merge(has_fam(on), on=key, suffixes=("_off", "_on"))
    assert len(m) == len(has_fam(off))
    assert (m["score_final_on"] >= m["score_final_off"] - 0.02 - 1e-9).all()                               # whatever changed stayed within the tolerance ...
    switched = m[m["isin_off"] != m["isin_on"]]
    assert switched["eligible_on"].all()                                                                     # ... and onto an eligible class
