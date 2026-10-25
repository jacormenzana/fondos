"""FND-0317: context readings next to the sovereign-stress line (real 10y yield, debt/GDP). Readings only: no thresholds, no rule, no [ATENCION] item.

Pure: synthetic series and a fake connection that answers by indicator. Uses only series already in series_macro (ipc_index, debt_gdp_pct, yield_10y_gov).
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from shared import sovereign_stress as ss

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("p1p2_cycle_report_ctx_t", _ROOT / "scripts/launch/p1p2_cycle_report.py")
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)


def _ipc(first_year_index=100.0, yoy=0.038, last="2026-09", geo="EU", months=30):
    """Monthly index growing so that the 12-month change is exactly `yoy` at every month."""
    out, (y, m) = {}, (2024, 1)
    g = (1 + yoy) ** (1 / 12)
    v = first_year_index
    while f"{y:04d}-{m:02d}" <= last:
        out[f"{y:04d}-{m:02d}"] = v
        v *= g
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return {geo: out}


def test_real_yield_is_the_nominal_ten_year_minus_the_twelve_month_inflation():
    ctx = ss.context_readings({"DE": {"2026-09": 3.47}}, _ipc(yoy=0.038), {}, "2026-09")
    r = ctx["real_yield"]["DE"]
    assert r["month"] == "2026-09" and r["nominal"] == pytest.approx(3.47)
    assert r["inflation"] == pytest.approx(3.8, abs=1e-6) and r["real"] == pytest.approx(3.47 - 3.8, abs=1e-6)


def test_the_euro_area_index_is_the_inflation_reference_of_the_bund():
    assert ss.CONTEXT_INFLATION["DE"] == "EU" and ss.CONTEXT_INFLATION["US"] == "US" and ss.CONTEXT_INFLATION["JP"] == "JP"


def test_a_cpi_one_month_behind_uses_its_latest_month_and_says_which():
    y = {"JP": {"2026-08": 2.9, "2026-09": 3.05}}
    ctx = ss.context_readings(y, _ipc(yoy=0.02, last="2026-08", geo="JP"), {}, "2026-09")
    assert ctx["real_yield"]["JP"]["month"] == "2026-08" and ctx["real_yield"]["JP"]["nominal"] == pytest.approx(2.9)


def test_a_cpi_more_than_three_months_behind_is_not_available_never_a_stale_number():
    y = {"JP": {m: 3.0 for m in ("2026-05", "2026-06", "2026-07", "2026-08", "2026-09")}}
    ctx = ss.context_readings(y, _ipc(yoy=0.02, last="2026-04", geo="JP"), {}, "2026-09")
    assert ctx["real_yield"]["JP"] is None
    assert "JP n/d" in ss.context_line(ctx)


def test_debt_to_gdp_is_the_latest_published_year_per_geography():
    ctx = ss.context_readings({}, {}, {"ES": {"2024": 101.4, "2025": 100.5}, "EU": {"2025": 87.7}}, "2026-09")
    assert ctx["debt_gdp"] == {"ES": ("2025", 100.5), "EU": ("2025", 87.7)}


def test_the_context_line_states_that_it_has_no_thresholds_and_survives_missing_data():
    empty = ss.context_line(ss.context_readings({}, {}, {}, "2026-09"))
    assert "sin umbrales" in empty and "n/d" in empty
    full = ss.context_line(ss.context_readings({"DE": {"2026-09": 3.47}}, _ipc(), {"ES": {"2025": 100.5}}, "2026-09"))
    assert "DE" in full and "-0.33" in full and "ES 100.5% (2025)" in full


def test_rows_from_series_macro_become_month_and_year_keyed_dictionaries():
    rows = [(date(2026, 9, 1), "EU", 128.5), (date(2026, 8, 1), "EU", None), ("2026-08-01", "US", 320.1)]
    assert ss.ipc_from_rows(rows) == {"EU": {"2026-09": 128.5}, "US": {"2026-08": 320.1}}
    assert ss.debt_from_rows([(date(2025, 1, 1), "ES", 100.5), (date(2024, 1, 1), "ES", 101.4)]) == {"ES": {"2025": 100.5, "2024": 101.4}}


# ---------------------------------------------------------------- the cycle report collector

class _Conn:
    """Answers each SELECT by the indicator passed as parameter."""

    def __init__(self, by_indicator):
        self._by, self._cur = by_indicator, []
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        self._cur = self._by.get(params[0], []) if params else []
        return self

    def fetchall(self):
        return self._cur

    def rollback(self):
        pass


def _yield_rows():
    months = [f"2025-{m:02d}" for m in range(1, 13)] + [f"2026-{m:02d}" for m in range(1, 10)]
    base = {"DE": 3.0, "IT": 3.9, "ES": 3.5, "FR": 3.4, "US": 4.5, "GB": 4.8, "JP": 1.0}
    return [(date(int(m[:4]), int(m[5:]), 1), c, v) for c, v in base.items() for m in months]


def _ipc_rows():
    return [(date(int(m[:4]), int(m[5:]), 1), geo, v) for geo, s in _ipc(yoy=0.038).items() for m, v in s.items()]


def test_the_collector_adds_the_context_line_without_touching_the_rules_or_the_attention_items():
    conn = _Conn({ss.YIELD_INDICATOR: _yield_rows(), "ipc_index": _ipc_rows(), "debt_gdp_pct": [(date(2025, 1, 1), "ES", 100.5)]})
    out = cr.collect_sovereign_stress(conn)
    assert out["state"] == "OK" and "Contexto soberano" in out["context"] and "ES 100.5% (2025)" in out["context"]
    assert out["lines"] == []                                                       # a calm market stays silent: context is never an item
    for sql, params in conn.calls:
        assert "%s" in sql and params                                               # parametrised literal SQL (the EXPLAIN sweep resolves it)
    assert [p[0] for _, p in conn.calls] == [ss.YIELD_INDICATOR, "ipc_index", "debt_gdp_pct"]


def test_a_failing_context_never_hides_the_monitor():
    class Half(_Conn):
        def execute(self, sql, params=()):
            if params and params[0] == "ipc_index":
                raise RuntimeError("ipc down")
            return super().execute(sql, params)
    out = cr.collect_sovereign_stress(Half({ss.YIELD_INDICATOR: _yield_rows()}))
    assert out["state"] == "OK" and "R1=off" in out["status"]
    assert "context_error" in out and "RuntimeError" in out["context_error"]
