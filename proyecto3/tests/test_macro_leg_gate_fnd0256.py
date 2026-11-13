# proyecto3/tests/test_macro_leg_gate_fnd0256.py
# -*- coding: utf-8 -*-
"""
FND-0256: the P3 freshness gate judges every leg of a composite regime input (ipc_yoy_avg = mean of the ES and EU CPI legs).

R-7: no DB, no pipeline imports (a fake connection stands in for the loader).

Run from repo root:
    python -m pytest proyecto3/tests/test_macro_leg_gate_fnd0256.py -v
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import proyecto3.src.data_freshness as df  # noqa: E402
from shared import config  # noqa: E402
from shared.macro_freshness import COMPOSITE_INPUTS  # noqa: E402

TODAY = date(2026, 11, 13)
FRESH = date(2026, 9, 1)            # CPI of September: 73 days old, inside the 100-day limit of an ipc_index
DEAD = date(2025, 12, 1)            # the ECB series that ended in 2025-12


def _latest(es=FRESH, eu=FRESH):
    out = {}
    if es is not None:
        out[("ipc_index", "ES")] = es
    if eu is not None:
        out[("ipc_index", "EU")] = eu
    return out


def _only(checks):
    assert len(checks) == len(COMPOSITE_INPUTS)
    return {c.name: c for c in checks}["macro_legs:ipc_yoy_avg"]


def test_two_live_legs_pass_in_both_modes():
    for enforce in (False, True):
        c = _only(df.macro_leg_checks(_latest(), TODAY, enforce))
        assert c.ok and c.detail == "OK: ES=OK, EU=OK" and not c.show_age


def test_a_dead_leg_is_reported_but_does_not_block_in_shadow():
    c = _only(df.macro_leg_checks(_latest(eu=DEAD), TODAY, enforce=False))
    assert c.ok                                                    # SHADOW: the gate does not refuse
    assert "DEGRADED" in c.detail and "EU=STALE" in c.detail and "SHADOW: would block" in c.detail


def test_a_dead_leg_blocks_when_the_switch_is_on():
    c = _only(df.macro_leg_checks(_latest(eu=DEAD), TODAY, enforce=True))
    assert not c.ok and "DEGRADED" in c.detail and "SHADOW" not in c.detail
    assert df.stale_checks([c]) == [c]


def test_no_live_leg_is_blind_and_blocks_when_on():
    c = _only(df.macro_leg_checks(_latest(es=DEAD, eu=DEAD), TODAY, enforce=True))
    assert not c.ok and c.detail.startswith("BLIND")


def test_a_leg_missing_from_the_table_counts_as_not_alive():
    c = _only(df.macro_leg_checks(_latest(eu=None), TODAY, enforce=True))
    assert not c.ok and "EU=MISSING" in c.detail and c.detail.startswith("DEGRADED")


def test_the_limit_is_the_series_own_limit_not_a_single_number():
    just_inside = TODAY - timedelta(days=100)                      # the ipc_index limit is 100 days
    just_outside = TODAY - timedelta(days=101)
    assert _only(df.macro_leg_checks(_latest(eu=just_inside), TODAY, True)).ok
    assert not _only(df.macro_leg_checks(_latest(eu=just_outside), TODAY, True)).ok


def test_text_and_timestamp_dates_are_accepted_like_in_the_rest_of_the_gate():
    c = _only(df.macro_leg_checks({("ipc_index", "ES"): "2026-09-01", ("ipc_index", "EU"): "2026-09-01"}, TODAY, True))
    assert c.ok


def test_the_shipped_default_is_shadow():
    assert config.P3_MACRO_LEG_CHECK_ENABLED is False


# ---------------- loader and wiring ----------------

class _Conn:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, None, None

    def execute(self, sql, params=()):
        self.sql, self.params = sql, params
        return SimpleNamespace(fetchall=lambda: self.rows)


def test_the_loader_reads_only_the_composite_legs():
    conn = _Conn([("ipc_index", "ES", FRESH), ("ipc_index", "EU", FRESH), ("ipc_index", "US", FRESH)])   # US is not a leg
    got = df.load_macro_leg_latest(conn)
    assert set(got) == {("ipc_index", "ES"), ("ipc_index", "EU")}
    assert "series_macro" in conn.sql and conn.params == (["ipc_index"],)


class _Clf:
    def input_last_dates(self):
        return {}


def _stub_the_rest(monkeypatch, latest):
    monkeypatch.setattr(df, "load_freshness_inputs", lambda conn: ([], None, []))
    monkeypatch.setattr(df, "evaluate_freshness", lambda *a, **k: [])
    import shared.family_refresh as _fr
    monkeypatch.setattr(_fr, "pending_family_refresh", lambda conn, active_only=True: [])
    monkeypatch.setattr(df, "load_macro_leg_latest", lambda conn: latest)
    monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", False, raising=False)


def test_the_gate_always_reports_the_leg_check_and_only_the_switch_makes_it_block(monkeypatch):
    _stub_the_rest(monkeypatch, _latest(eu=DEAD))
    monkeypatch.setattr(config, "P3_MACRO_LEG_CHECK_ENABLED", False, raising=False)
    off = {c.name: c for c in df.check_universe_freshness(None, _Clf(), today=TODAY)}
    assert "macro_legs:ipc_yoy_avg" in off and off["macro_legs:ipc_yoy_avg"].ok          # reported, not blocking
    assert not df.stale_checks(list(off.values()))
    monkeypatch.setattr(config, "P3_MACRO_LEG_CHECK_ENABLED", True, raising=False)
    on = df.check_universe_freshness(None, _Clf(), today=TODAY)
    assert [c.name for c in df.stale_checks(on)] == ["macro_legs:ipc_yoy_avg"]
    assert "macro_legs:ipc_yoy_avg" in df.format_report(on) and "STALE" in df.format_report(on)
