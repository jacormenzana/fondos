# tests/test_fx_exposure.py
"""shared/fx_exposure.py: the net non-EUR exposure rule of the portfolio gate (pure, R-7)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import fx_exposure as fx  # noqa: E402


@pytest.mark.parametrize("fc, hp, ac, expected", [
    ("USD", "Unhedged", "USD", "USD"),       # foreign class, unhedged
    ("USD", "Hedged", "JPY", "USD"),         # USD class hedged JPY->USD: the EUR investor still holds USD
    ("USD", "Hedged", None, "USD"),
    ("GBP", None, "EUR", "GBP"),             # foreign class even when the assets are EUR
    ("EUR", "Hedged", "USD", None),          # EUR class hedged to EUR: no exposure
    ("EUR", "Unhedged", "USD", "USD"),       # EUR class of US assets, unhedged: exposed through the assets
    ("EUR", None, "USD", "USD"),
    ("EUR", "Partially Hedged", "USD", "USD"),   # hedged share unknown: counted as exposed
    ("EUR", "Unhedged", "MCY", "MCY"),       # multi-currency: reported apart
    ("EUR", "Unhedged", "EUR", None),
    ("EUR", None, None, None),
    (None, None, None, None),
    ("usd", "hedged", None, "USD"),          # case-insensitive
])
def test_fund_exposure_currency(fc, hp, ac, expected):
    assert fx.fund_exposure_currency(fc, hp, ac) == expected


def test_the_release_portfolio_numbers():
    """20 USD classes carrying 79.0% of the master weight (2026-10-11 build) -> breach of the 30% budget."""
    holdings = [(0.0395, "USD", "Unhedged", "USD")] * 20 + [(0.021, "EUR", "Unhedged", None)] * 10
    s = fx.summarize(fx.exposure_by_currency(holdings), 0.30)
    assert s["non_eur"] == pytest.approx(0.79) and s["over_budget"] and list(s["by_currency"]) == ["USD"]
    assert "EXCEDE" in fx.format_line(s) and "79.0%" in fx.format_line(s)


def test_mcy_is_reported_apart_and_does_not_count_against_the_budget():
    s = fx.summarize(fx.exposure_by_currency([(0.25, "EUR", "Unhedged", "MCY"), (0.2, "USD", None, None)]), 0.30)
    assert s["non_eur"] == pytest.approx(0.2) and s["mcy"] == pytest.approx(0.25) and not s["over_budget"]
    assert "MCY 25.0%" in fx.format_line(s) and "dentro de" in fx.format_line(s)


def test_budget_edge_and_empty_portfolio():
    assert not fx.summarize({"USD": 0.30}, 0.30)["over_budget"]
    assert fx.summarize({"USD": 0.3001}, 0.30)["over_budget"]
    empty = fx.summarize({}, 0.30)
    assert empty["non_eur"] == 0 and "ninguna" in fx.format_line(empty)


def test_zero_weights_are_ignored():
    assert fx.exposure_by_currency([(0, "USD", None, None), (None, "GBP", None, None)]) == {}


def test_exposed_flags_follow_the_same_rule_as_the_summary_and_treat_unknown_isins_as_not_exposed():
    attrs = {"A": ("USD", "Unhedged", "USD"), "B": ("EUR", "Hedged", "USD"), "C": ("EUR", "Unhedged", "MCY"),
             "D": ("EUR", "Unhedged", "USD")}
    flags = fx.exposed_flags(attrs, ["A", "B", "C", "D", "NOPE"])
    assert flags == {"A": True, "B": False, "C": False, "D": True, "NOPE": False}   # MCY is reported apart, not budgeted
    holdings = [(0.25, *attrs[i]) for i in ("A", "B", "C", "D")]
    summary = fx.summarize(fx.exposure_by_currency(holdings), 0.30)
    assert summary["non_eur"] == sum(0.25 for i in ("A", "D"))                  # the flag and the KPI agree: this is what the gate sees
