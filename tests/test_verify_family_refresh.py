"""
tests/test_verify_family_refresh.py -- the closure check of the family-nature refresh (FND-0244 follow-up). R-7: the analysis is pure.

Three situations decide the exit code: a homogeneous family (0), a legitimately multi-class family whose derived attribute differs (2, with
the full dump so the owner can decide without another query) and a fund that is still pending (1).
"""
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location("verify_family_refresh", _ROOT / "scripts" / "audit" / "verify_family_refresh.py")
v = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v)


def _row(isin, family="FAM_1", nature="Mixtos", active=1, hedging="Unhedged", ccy="EUR", **attrs):
    base = {"isin": isin, "fund_name": f"NAME {isin}", "fund_nature": nature, "fund_family_id": family, "in_current_universe": active,
            "hedging_policy": hedging, "fund_currency": ccy}
    base.update({a: f"{a}_v" for a in v.DERIVED_ATTRS})
    base.update(attrs)
    return base


# ---------------------------------------------------------------- the three decisive cases
def test_a_homogeneous_family_is_clean_exit_0():
    rows = [_row("A"), _row("B", ccy="USD"), _row("C", hedging="Hedged")]
    res = v.analyze(rows, ["A"], [])
    assert v.exit_code(res) == v.RC_CLEAN == 0 and res["divergences"] == [] and res["checked"] == 1
    assert "RESULT: clean" in v.render(res)


def test_a_legitimately_multi_class_family_is_soft_exit_2_with_the_full_dump():
    """Two classes of one fund hold the same nature but a secondary strategy differs: informational, never a block."""
    rows = [_row("A", alt_strategy="Long/Short"), _row("B", alt_strategy="Global Macro", ccy="USD")]
    res = v.analyze(rows, ["A"], [])
    assert v.exit_code(res) == v.RC_SOFT == 2
    (d,) = res["divergences"]
    assert (d["isin"], d["sibling"], d["family"], d["attr"], d["value"], d["sibling_value"]) == ("A", "B", "FAM_1", "alt_strategy", "Long/Short", "Global Macro")
    text = v.render(res)
    for needle in ("A [NAME A]", "B [NAME B]", "FAM_1", "alt_strategy", "'Long/Short'", "'Global Macro'", "per attribute: alt_strategy=1",
                   "informational", "Mixtos / Mixtos"):
        assert needle in text, needle


def test_a_pending_fund_is_hard_exit_1_and_names_it():
    res = v.analyze([_row("A"), _row("B")], ["A"], ["A"])
    assert v.exit_code(res) == v.RC_HARD == 1
    text = v.render(res)
    assert "[FAIL] family_refresh_pending: 1 active funds: A" in text and "HARD findings" in text


def test_an_active_multi_nature_family_is_hard_but_a_retired_member_is_not():
    rows = [_row("A", nature="Mixtos"), _row("B", nature="Renta Variable", ccy="USD")]
    assert v.exit_code(v.analyze(rows, [], [])) == v.RC_HARD
    rows[1]["in_current_universe"] = 0
    assert v.exit_code(v.analyze(rows, [], [])) == v.RC_CLEAN


# ---------------------------------------------------------------- sibling choice and comparison
def test_siblings_prefer_the_same_hedging_class_then_hedging_then_any_of_the_same_nature():
    me = _row("ME", hedging="Hedged", ccy="EUR")
    same_class = _row("S1", hedging="Hedged", ccy="EUR")
    same_hedging = _row("S2", hedging="Hedged", ccy="USD")
    other = _row("S3", hedging="Unhedged", ccy="USD")
    wrong_nature = _row("S4", nature="Renta Variable", hedging="Hedged", ccy="EUR")
    sibs, used = v.pick_siblings(me, [me, same_class, same_hedging, other, wrong_nature])
    assert [s["isin"] for s in sibs] == ["S1"] and used.startswith("hedging+currency")
    sibs, used = v.pick_siblings(me, [me, same_hedging, other, wrong_nature])
    assert [s["isin"] for s in sibs] == ["S2"] and used.startswith("hedging:")
    sibs, used = v.pick_siblings(me, [me, other, wrong_nature])
    assert [s["isin"] for s in sibs] == ["S3"] and used.startswith("any")


def test_a_fund_without_a_same_nature_sibling_is_reported_not_failed():
    res = v.analyze([_row("A"), _row("B", nature="Renta Variable")], ["A"], [])
    assert res["no_sibling"] == ["A"] and res["checked"] == 0 and v.exit_code(res) == v.RC_HARD   # (the two natures are an active multi-nature family)
    res = v.analyze([_row("A", family="F1"), _row("B", family="F2")], ["A"], [])
    assert res["no_sibling"] == ["A"] and v.exit_code(res) == v.RC_CLEAN


def test_none_versus_a_value_is_a_divergence_and_none_versus_none_is_not():
    assert v.compare_attributes({"profile": None, "credit_quality": None}, {"profile": "Balanced", "credit_quality": None},
                                attrs=("profile", "credit_quality")) == [("profile", None, "Balanced")]


def test_cost_and_class_attributes_are_not_compared():
    for banned in ("ongoing_charge_recurrent", "management_fee_pct", "hedging_policy", "fund_currency", "accumulation_policy", "aci_rhp"):
        assert banned not in v.DERIVED_ATTRS


def test_every_corrected_isin_is_checked_once_even_if_corrected_twice():
    rows = [_row("A", profile="x"), _row("B")]
    res = v.analyze(rows, ["A", "A"], [])
    assert res["corrected"] == 1 and len(res["divergences"]) == 1
