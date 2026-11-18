"""FND-0430 / P3-1.7: the decision rule of scripts/audit/p3/nav_rows_eligibility_diff.py (pure part, no database, R-7)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "audit" / "p3"))
sys.path.insert(0, str(ROOT))
import nav_rows_eligibility_diff as D  # noqa: E402

ROWS = [("Renta Variable", 100, 2, 3, 5), ("Mixtos", 50, 0, 1, 1), ("Monetario", 10, 0, 0, 0)]    # nature, eligible, no NAV, under min, excluded


def test_the_summary_computes_shares_per_nature_and_in_total():
    s = D.summarise(ROWS, None)
    assert s["eligible"] == 160 and s["excluded"] == 6 and s["share"] == 6 / 160
    assert s["worst_nature"] == "Renta Variable" and s["worst_share"] == 0.05
    assert s["recommendation"] is None                                # no threshold, no recommendation


def test_the_rule_recommends_on_only_when_no_nature_loses_more_than_the_share():
    assert D.summarise(ROWS, 0.05)["recommendation"] == "ON"          # worst nature loses exactly 5 %
    assert D.summarise(ROWS, 0.04)["recommendation"] == "NOT ON"


def test_nothing_excluded_is_on_and_an_empty_result_does_not_fail():
    assert D.summarise([("Mixtos", 10, 0, 0, 0)], 0.0)["recommendation"] == "ON"
    s = D.summarise([], 0.05)
    assert s["eligible"] == 0 and s["worst_nature"] is None and s["recommendation"] == "ON"
