# proyecto3/tests/test_fx_coverage_gate_fnd0243.py
# -*- coding: utf-8 -*-
"""FND-0243 step 5: the P3 gate refuses to build while a non-EUR share-class currency cannot be converted up to its newest NAV,
and the cycle report names the active funds whose class currency is unknown (excluded from EUR scoring, never assumed EUR).
Pure functions and fakes (R-7)."""
import sys
from datetime import date
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "scripts" / "launch")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared import config  # noqa: E402
from proyecto3.src import data_freshness as df  # noqa: E402
import p1p2_cycle_report as cr  # noqa: E402


# ---------------- fx_coverage_check (pure) ----------------

def test_rates_up_to_the_newest_nav_pass():
    c = df.fx_coverage_check({"USD": (date(2026, 10, 7), date(2026, 10, 7), 605), "GBP": (date(2026, 9, 30), date(2026, 10, 2), 18)},
                             unknown_ccy=14, max_gap_days=7)
    assert c.ok and c.name == "fx_coverage"
    assert "GBP:18" in c.detail and "USD:605" in c.detail and "14 active funds with unknown class currency" in c.detail


def test_a_weekend_or_holiday_gap_within_the_limit_passes():
    assert df.fx_coverage_check({"JPY": (date(2026, 10, 5), date(2026, 10, 2), 6)}, 0, 7).ok


def test_rates_lagging_the_nav_fail():
    c = df.fx_coverage_check({"USD": (date(2026, 10, 7), date(2026, 9, 25), 605)}, 0, 7)
    assert not c.ok and "USD: rates end 2026-09-25, NAV up to 2026-10-07" in c.detail and "macro_discovery --source bce" in c.detail


def test_a_currency_without_rates_fails():
    c = df.fx_coverage_check({"CHF": (date(2026, 10, 7), None, 5)}, 0, 7)
    assert not c.ok and "CHF: no ECB rates (5 funds)" in c.detail


def test_unknown_currency_funds_never_block_the_build():
    assert df.fx_coverage_check({}, unknown_ccy=99, max_gap_days=7).ok


# ---------------- wiring into the gate ----------------

class _Clf:
    def input_last_dates(self):
        return {}


def test_the_gate_adds_fx_coverage_only_with_the_eur_view(monkeypatch):
    monkeypatch.setattr(df, "load_freshness_inputs", lambda conn: ([], None, []))
    monkeypatch.setattr(df, "evaluate_freshness", lambda *a, **k: [])
    import shared.family_refresh as _fr
    monkeypatch.setattr(_fr, "pending_family_refresh", lambda conn, active_only=True: [])
    monkeypatch.setattr(df, "load_fx_canary_pairs", lambda conn: [])
    monkeypatch.setattr(df, "load_fx_coverage_inputs",
                        lambda conn: ({"USD": (date(2026, 10, 7), date(2026, 9, 1), 3)}, 2))
    monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", False, raising=False)
    off = df.check_universe_freshness(None, _Clf(), today=date(2026, 10, 9))
    assert "fx_coverage" not in [c.name for c in off]
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", True, raising=False)
    on = {c.name: c for c in df.check_universe_freshness(None, _Clf(), today=date(2026, 10, 9))}
    assert "fx_coverage" in on and not on["fx_coverage"].ok


# ---------------- cycle report ----------------

def _report(unknown, eur_on):
    return {"universe": {"active": 10, "inactive": 0, "total": 10}, "wrong_doc_stale": [], "nav_stale": [], "real_orphans": {},
            "class_ccy_unknown": unknown, "eur_view_on": eur_on}


@pytest.mark.parametrize("eur_on, fate", [(False, "puntuados hoy como si su NAV fuera EUR"), (True, "EXCLUIDOS del scoring EUR")])
def test_cycle_report_names_unknown_class_currency_funds(eur_on, fate):
    items = cr.attention_items(_report(["IE00B8J38129", "LU0170475585"], eur_on), None)
    line = next(i for i in items if "sin divisa de clase" in i)
    assert line.startswith("2 fondos activos") and fate in line and "IE00B8J38129" in line


def test_cycle_report_is_silent_without_unknown_currency_and_on_old_reports():
    assert not [i for i in cr.attention_items(_report([], True), None) if "divisa de clase" in i]
    old = _report([], False)
    del old["class_ccy_unknown"], old["eur_view_on"]
    assert not [i for i in cr.attention_items(old, None) if "divisa de clase" in i]
