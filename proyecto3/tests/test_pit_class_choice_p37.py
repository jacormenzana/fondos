"""Package L, P3-7 (doc/decisiones/PREREG_P3_7.md section 8): the fee-aware class choice in the PIT (`proyecto3/src/pit_class_choice.py`), parameterised PER TRIAL (no global switch is read) and sharing ONE rule
function with the live scorer (R3: `fund_scorer._fee_aware_pick`).
Invariants pinned: (1) with no `ClassChoice` nothing changes, byte for byte, through `candidates_by_sub`, `build_weights` and `prepare_pit_backtest` (the default of every trial that does not use B);
(2) within a (family, sub-portfolio) the class with the lowest entry + exit fees replaces the best-score class only when its score is within the tolerance, its Ongoing_Charge is not higher and its one-off
fees are strictly lower; a higher-TER class is NEVER chosen; (3) an unknown (NaN / null / negative) Ongoing_Charge, Entry_Fee_Pct or Exit_Fee_Pct in the candidate OR in the best class keeps the best, each
field and each side; (4) a fund with no family id is its own family and a family with one class is untouched; (5) the PIT pass and the live `deduplicate_by_family(fee_aware=True)` keep the SAME class on the
same data; (6) the engine ends up with exactly one class per family, the chosen one. Synthetic frames only (R-7)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import fund_scorer as fs  # noqa: E402
from proyecto3.src.pit_backtest import build_weights, candidates_by_sub  # noqa: E402
from proyecto3.src.pit_class_choice import ClassChoice, apply_class_choice  # noqa: E402

T = pd.Timestamp("2020-06-30")


def scores(rows, sub="Equilibrada"):
    """rows: (isin, family, score) -> one date of scores for one sub-portfolio, all eligible."""
    return pd.DataFrame([dict(as_of=T, regime="Expansion", isin=i, subportfolio=sub, fund_name=i, fund_nature="Renta Variable", fund_family_id=f, score_base=s, multiplier=1.0, score_final=s,
                              eligible=True, exclusion_reason=None) for i, f, s in rows])


def attrs(isins):
    return pd.DataFrame({"Management_Company": [f"M{k}" for k in range(len(isins))]}, index=pd.Index(isins, name="isin"))


def choice(fees: dict, ongoing: dict, tol=0.02):
    """fees: isin -> (entry, exit); ongoing: isin -> ongoing charge."""
    f = pd.DataFrame({"Entry_Fee_Pct": {i: v[0] for i, v in fees.items()}, "Exit_Fee_Pct": {i: v[1] for i, v in fees.items()}})
    f.index.name = "isin"
    return ClassChoice(fees=f, ongoing=pd.Series(ongoing, dtype=float), tolerance=tol)


def kept(cands, sub="Equilibrada"):
    return list(cands[sub]["isin"])


BASE = [("A_best", "F1", 0.80), ("A_cheap", "F1", 0.79), ("B_only", "F2", 0.70)]
FEES = {"A_best": (0.05, 0.01), "A_cheap": (0.00, 0.00), "B_only": (0.03, 0.0)}
OC = {"A_best": 0.012, "A_cheap": 0.012, "B_only": 0.010}


# ================================================================ default: nothing changes ===========================================================================
def test_without_a_class_choice_the_candidates_are_identical():
    sc = scores(BASE)
    a = candidates_by_sub(sc, attrs(sc["isin"]))
    b = candidates_by_sub(sc, attrs(sc["isin"]), class_choice=None)
    for sub in a:
        pd.testing.assert_frame_equal(a[sub], b[sub])
    assert kept(a) == ["A_best", "A_cheap", "B_only"]                              # the engine, not this function, removes the second class of a family


def test_without_a_class_choice_build_weights_is_byte_identical():
    rows = [(f"I{k}", f"F{k // 2}", 0.9 - 0.02 * k) for k in range(16)]
    sc = scores(rows)
    hist = pd.DataFrame({"regime": "Expansion", "weight_defensive": 0.2, "weight_balanced": 0.5, "weight_dynamic": 0.3}, index=pd.DatetimeIndex([T - pd.offsets.MonthEnd(1)]))
    at = pd.DatetimeIndex([T])
    W1, c1 = build_weights(sc, attrs([r[0] for r in rows]), hist, at)
    W2, c2 = build_weights(sc, attrs([r[0] for r in rows]), hist, at, class_choice=None)
    pd.testing.assert_frame_equal(W1, W2)
    pd.testing.assert_series_equal(c1, c2)


# ================================================================ the rule ============================================================================================
def test_the_cheaper_class_replaces_the_best_one_when_the_rule_holds():
    sc = scores(BASE)
    out = apply_class_choice(candidates_by_sub(sc, attrs(sc["isin"])), choice(FEES, OC))
    assert kept(out) == ["A_cheap", "B_only"]                                      # F1 keeps its cheaper class (score within 0.02, same TER, one-off fees 0 < 0.06), F2 has one class


def test_a_score_gap_above_the_tolerance_keeps_the_best_class():
    sc = scores([("A_best", "F1", 0.80), ("A_cheap", "F1", 0.77)])
    out = apply_class_choice(candidates_by_sub(sc, attrs(sc["isin"])), choice(FEES, OC, tol=0.02))
    assert kept(out) == ["A_best"]
    out = apply_class_choice(candidates_by_sub(sc, attrs(sc["isin"])), choice(FEES, OC, tol=0.05))
    assert kept(out) == ["A_cheap"]


def test_a_higher_ongoing_charge_class_is_never_chosen():
    sc = scores(BASE)
    oc = dict(OC, A_cheap=0.013)                                                   # cheaper one-off fees but a higher recurring charge
    out = apply_class_choice(candidates_by_sub(sc, attrs(sc["isin"])), choice(FEES, oc))
    assert kept(out) == ["A_best", "B_only"]
    rng = np.random.default_rng(3)
    for _ in range(40):                                                            # property: whatever the fees, the kept class never has a higher TER than the best-score class
        n = 5
        rows = [(f"X{k}", "FAM", 0.80 - 0.004 * k) for k in range(n)]
        f = {r[0]: (float(rng.choice([0, 0.01, 0.03, 0.05])), float(rng.choice([0, 0.01]))) for r in rows}
        o = {r[0]: float(rng.choice([0.008, 0.01, 0.012, 0.015])) for r in rows}
        s2 = scores(rows)
        got = kept(apply_class_choice(candidates_by_sub(s2, attrs(s2["isin"])), choice(f, o)))
        assert len(got) == 1 and o[got[0]] <= o["X0"] and (got[0] == "X0" or f[got[0]][0] + f[got[0]][1] < f["X0"][0] + f["X0"][1])


def test_equal_one_off_fees_keep_the_best_class():
    sc = scores(BASE)
    fees = dict(FEES, A_cheap=(0.05, 0.01))                                        # not STRICTLY lower
    out = apply_class_choice(candidates_by_sub(sc, attrs(sc["isin"])), choice(fees, OC))
    assert kept(out) == ["A_best", "B_only"]


@pytest.mark.parametrize("field", ["entry", "exit", "ongoing"])
@pytest.mark.parametrize("side", ["candidate", "best"])
@pytest.mark.parametrize("bad", [np.nan, -0.01])
def test_an_unknown_fee_in_either_class_keeps_the_best_class(field, side, bad):
    sc = scores(BASE)
    fees, oc = dict(FEES), dict(OC)
    target = "A_cheap" if side == "candidate" else "A_best"
    if field == "entry":
        fees[target] = (bad, fees[target][1])
    elif field == "exit":
        fees[target] = (fees[target][0], bad)
    else:
        oc[target] = bad
    out = apply_class_choice(candidates_by_sub(sc, attrs(sc["isin"])), choice(fees, oc))
    assert kept(out) == ["A_best", "B_only"]


def test_a_class_absent_from_the_fee_frame_counts_as_unknown():
    sc = scores(BASE)
    fees = {k: v for k, v in FEES.items() if k != "A_cheap"}
    out = apply_class_choice(candidates_by_sub(sc, attrs(sc["isin"])), choice(fees, OC))
    assert kept(out) == ["A_best", "B_only"]


def test_a_fund_with_no_family_is_its_own_family_and_a_single_class_family_is_untouched():
    sc = scores([("S1", None, 0.9), ("S2", "", 0.85), ("S3", "F3", 0.8), ("N1", None, 0.7)])
    fees = {i: (0.0, 0.0) for i in ["S1", "S2", "S3", "N1"]}
    out = apply_class_choice(candidates_by_sub(sc, attrs(sc["isin"])), choice(fees, {i: 0.01 for i in fees}))
    assert kept(out) == ["S1", "S2", "S3", "N1"]


def test_the_order_of_the_candidates_is_kept_and_the_other_sub_portfolios_are_untouched():
    a = scores(BASE, sub="Equilibrada")
    b = scores([("D1", "G1", 0.9), ("D2", "G1", 0.89)], sub="Defensiva")
    sc = pd.concat([a, b], ignore_index=True)
    fees = dict(FEES, D1=(0.03, 0.0), D2=(0.0, 0.0))
    oc = dict(OC, D1=0.01, D2=0.01)
    base = candidates_by_sub(sc, attrs(sc["isin"]))
    out = apply_class_choice(base, choice(fees, oc))
    assert kept(out, "Equilibrada") == ["A_cheap", "B_only"] and kept(out, "Defensiva") == ["D2"]
    assert out["Dinamica"].empty and list(out["Equilibrada"]["score_total"]) == sorted(out["Equilibrada"]["score_total"], reverse=True)


# ================================================================ one rule for live and PIT (R3) ======================================================================
def test_the_pit_pass_and_the_live_deduplication_keep_the_same_class():
    rng = np.random.default_rng(11)
    rows, fees, oc = [], {}, {}
    for fam in range(30):
        n_cls = int(rng.integers(1, 5))
        perm = rng.permutation(n_cls)                                              # distinct scores inside a family: a tie is broken by an unstable sort in the live function, not by the rule
        for k in range(n_cls):
            i = f"F{fam}_{k}"
            rows.append((i, f"FAM{fam}", round(0.70 + 0.008 * float(perm[k]), 4)))
            fees[i] = (float(rng.choice([0.0, 0.01, 0.03, 0.05, np.nan])), float(rng.choice([0.0, 0.0, 0.01])))
            oc[i] = float(rng.choice([0.008, 0.01, 0.012, np.nan]))
    sc = scores(rows)
    pit = set(kept(apply_class_choice(candidates_by_sub(sc, attrs(sc["isin"])), choice(fees, oc, tol=0.02))))
    live_in = sc[["isin", "fund_family_id", "subportfolio", "score_final", "eligible"]].copy()
    live_in["_fee_ongoing"] = live_in["isin"].map(oc)
    live_in["_fee_entry"] = live_in["isin"].map(lambda i: fees[i][0])
    live_in["_fee_exit"] = live_in["isin"].map(lambda i: fees[i][1])
    live = fs.deduplicate_by_family(live_in, verbose=False, fee_aware=True, tolerance=0.02)
    assert pit == set(live["isin"])


# ================================================================ the engine sees one class per family ================================================================
def test_the_engine_holds_the_chosen_class_and_the_default_holds_the_best_one():
    rows = [(f"I{k}", f"FAM{k // 2}", 0.90 - 0.003 * k) for k in range(24)]       # 12 families of 2 classes; the odd class is the cheaper one
    fees = {f"I{k}": ((0.05, 0.01) if k % 2 == 0 else (0.0, 0.0)) for k in range(24)}
    oc = {f"I{k}": 0.01 for k in range(24)}
    sc = scores(rows)
    hist = pd.DataFrame({"regime": "Expansion", "weight_defensive": 0.0, "weight_balanced": 1.0, "weight_dynamic": 0.0}, index=pd.DatetimeIndex([T - pd.offsets.MonthEnd(1)]))
    at = pd.DatetimeIndex([T])
    W0, _ = build_weights(sc, attrs([r[0] for r in rows]), hist, at)
    W1, _ = build_weights(sc, attrs([r[0] for r in rows]), hist, at, class_choice=choice(fees, oc))
    held0, held1 = set(W0.loc[T].dropna().index), set(W1.loc[T].dropna().index)
    assert all(int(i[1:]) % 2 == 0 for i in held0)                                 # best-score class of each family = the even one (higher score), the expensive one
    assert all(int(i[1:]) % 2 == 1 for i in held1)                                 # the chosen one is the cheap class
    assert len({int(i[1:]) // 2 for i in held1}) == len(held1)                     # one class per family
