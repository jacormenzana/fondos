"""
tests/test_macro_freshness_fnd0256.py -- per-series staleness of series_macro (shared/macro_freshness.py, scripts/audit/macro_freshness_check.py,
the cycle-report section).

Why it exists: on 2026-10-17 six macro series were found frozen for months or years and nothing had noticed. The centrepiece replays the REAL
inventory of that day (52 series) and demands that exactly those six are flagged -- no silent staleness, no false alarm on annual / quarterly series
(stamped at the start of the period) or on the lagged OECD / IMF feeds. R-7: pure modules and a fake connection, no database.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from shared import macro_freshness as mf

_ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = _load("macro_freshness_check_t", "scripts/audit/macro_freshness_check.py")
cr = _load("p1p2_cycle_report_t2", "scripts/launch/p1p2_cycle_report.py")

TODAY = date(2026, 10, 17)
# (indicator, geography, newest stamp) -- bronze.series_macro as read on 2026-10-17, BEFORE the source-chain fixes.
REAL_INVENTORY = [
    ("m2_level", "JP", "2017-02-01"), ("m2_level", "CN", "2019-08-01"), ("ipc_index", "JP", "2021-06-01"), ("ipc_index", "CN", "2024-03-01"),
    ("debt_gdp_pct", "ES", "2025-01-01"), ("debt_gdp_pct", "EU", "2025-01-01"), ("deficit_gdp_pct", "ES", "2025-01-01"),
    ("deficit_gdp_pct", "EU", "2025-01-01"), ("rate_policy", "CN", "2025-06-01"), ("ipc_index", "EU", "2025-12-01"),
    ("gdp_nom_eur", "EU", "2026-04-01"), ("copper", "GLOB", "2026-07-01"),
    ("cli", "CN", "2026-08-01"), ("cli", "ES", "2026-08-01"), ("cli", "EU", "2026-08-01"), ("cli", "JP", "2026-08-01"), ("cli", "US", "2026-08-01"),
    ("gold", "GLOB", "2026-08-01"), ("ipc_index", "ES", "2026-08-01"), ("ipc_index", "US", "2026-08-01"), ("m2_level", "EU", "2026-08-01"),
    ("m2_level", "US", "2026-08-01"), ("m2_yoy", "EU", "2026-08-01"), ("m3_level", "EU", "2026-08-01"), ("m3_yoy", "EU", "2026-08-01"),
    ("rate_policy", "JP", "2026-08-01"), ("m2_yoy", "US", "2026-08-31"),
    ("fx_cny_usd", "GLOB", "2026-09-01"), ("fx_jpy_usd", "GLOB", "2026-09-01"), ("fx_usd_eur", "GLOB", "2026-09-01"),
    ("fx_usd_gbp", "GLOB", "2026-09-01"), ("rate_policy", "US", "2026-09-01"), ("spread_ig", "GLOB", "2026-09-01"),
    ("term_spread", "US", "2026-09-01"), ("unemployment", "US", "2026-09-01"),
    ("fx_eom_eur", "CHF", "2026-09-30"), ("fx_eom_eur", "GBP", "2026-09-30"), ("fx_eom_eur", "JPY", "2026-09-30"), ("fx_eom_eur", "USD", "2026-09-30"),
    ("m2_global_yoy", "GLOB", "2026-09-30"), ("m2_yoy", "CN", "2026-09-30"), ("m2_yoy", "JP", "2026-09-30"),
    ("dxy", "GLOB", "2026-10-01"), ("oil_wti", "GLOB", "2026-10-01"), ("rate_deposit", "EU", "2026-10-01"), ("rate_policy", "EU", "2026-10-01"),
    ("spread_hy", "GLOB", "2026-10-01"), ("vix", "GLOB", "2026-10-01"),
    ("fx_d_eur", "CHF", "2026-10-09"), ("fx_d_eur", "GBP", "2026-10-09"), ("fx_d_eur", "JPY", "2026-10-09"), ("fx_d_eur", "USD", "2026-10-09"),
]
FROZEN = {("m2_level", "JP"), ("m2_level", "CN"), ("ipc_index", "JP"), ("ipc_index", "CN"), ("rate_policy", "CN"), ("ipc_index", "EU")}


def _latest(rows=REAL_INVENTORY):
    return {(i, g): date.fromisoformat(d) for i, g, d in rows}


def test_the_real_inventory_flags_exactly_the_six_frozen_series():
    assert len(REAL_INVENTORY) == 52
    st = mf.assess(_latest(), TODAY)
    assert {(s.indicator, s.geography) for s in st if s.stale} == FROZEN
    assert all(s.limit_source == "explicit" for s in st)                         # no series falls back to the default limit


def test_annual_and_quarterly_series_stamped_at_period_start_are_not_false_alarms():
    st = {(s.indicator, s.geography): s for s in mf.assess(_latest(), TODAY)}
    assert st[("debt_gdp_pct", "ES")].age_days == 654 and not st[("debt_gdp_pct", "ES")].stale        # year 2025 stamped 2025-01-01
    assert not st[("gdp_nom_eur", "EU")].stale and not st[("copper", "GLOB")].stale and not st[("rate_policy", "JP")].stale


def test_most_stale_series_come_first_and_future_stamps_have_age_zero():
    st = mf.assess({("vix", "GLOBAL"): date(2026, 11, 1), ("m2_level", "JP"): date(2017, 2, 1), ("cli", "EU"): date(2026, 8, 1)}, TODAY)
    assert st[0].indicator == "m2_level" and st[0].stale
    assert [s for s in st if s.indicator == "vix"][0].age_days == 0


def test_ttl_lookup_prefers_the_specific_geography_then_the_wildcard_then_the_default():
    assert mf.ttl_for("m2_level", "CN") == (140, "explicit") and mf.ttl_for("m2_level", "JP") == (75, "explicit")
    assert mf.ttl_for("ipc_index", "XX") == (100, "explicit")
    assert mf.ttl_for("brand_new_indicator", "EU") == (mf.DEFAULT_TTL_DAYS, "default")


def test_a_series_just_inside_and_just_outside_its_limit():
    limit, _ = mf.ttl_for("ipc_index", "EU")
    from datetime import timedelta
    ok = mf.assess({("ipc_index", "EU"): TODAY - timedelta(days=limit)}, TODAY)[0]
    bad = mf.assess({("ipc_index", "EU"): TODAY - timedelta(days=limit + 1)}, TODAY)[0]
    assert not ok.stale and bad.stale


def test_every_indicator_the_loaders_write_has_an_explicit_limit():
    import sys
    sys.path.insert(0, str(_ROOT))
    from proyecto2.src.discovery import macro_discovery as md
    written = {cfg["indicator"] for cfg in md._BCE_SERIES.values()} | {cfg["indicator"] for cfg in md._FRED_SERIES.values()}
    written |= {"ipc_index", "gdp_nom_eur", "debt_gdp_pct", "deficit_gdp_pct", "m2_yoy", "m2_global_yoy", "m2_level", "rate_policy"}
    missing = sorted(i for i in written if not any(k[0] == i for k in mf.MACRO_TTL_DAYS))
    assert not missing, f"indicators without a freshness limit: {missing}"


# ---------------------------------------------------------------- composite regime inputs
def _st(**kw):
    base = {("ipc_index", "ES"): date(2026, 9, 1), ("ipc_index", "EU"): date(2026, 9, 1)}
    base.update({("ipc_index", k): v for k, v in kw.items()})
    return mf.assess(base, TODAY)


def test_ipc_yoy_avg_is_degraded_when_one_leg_is_dead_even_though_the_average_looks_fresh():
    c = mf.composite_legs(_st(EU=date(2025, 12, 1)))["ipc_yoy_avg"]
    assert c["state"] == "DEGRADED" and c["legs"] == {"ES": "OK", "EU": "STALE"}


def test_ipc_yoy_avg_ok_blind_and_missing():
    assert mf.composite_legs(_st())["ipc_yoy_avg"]["state"] == "OK"
    assert mf.composite_legs(_st(ES=date(2025, 1, 1), EU=date(2025, 1, 1)))["ipc_yoy_avg"]["state"] == "BLIND"
    only_es = mf.assess({("ipc_index", "ES"): date(2026, 9, 1)}, TODAY)
    c = mf.composite_legs(only_es)["ipc_yoy_avg"]
    assert c["state"] == "DEGRADED" and c["legs"]["EU"] == "MISSING"


def test_the_real_inventory_reports_ipc_yoy_avg_as_degraded():
    assert mf.composite_legs(mf.assess(_latest(), TODAY))["ipc_yoy_avg"]["state"] == "DEGRADED"


# ---------------------------------------------------------------- attention lines, CLI, cycle report
def test_attention_lines_name_the_stale_series_the_degraded_input_and_the_default_limits():
    st = mf.assess({**_latest(), ("novel", "X"): date(2026, 9, 1)}, TODAY)
    lines = mf.attention_lines(st, mf.composite_legs(st))
    text = " | ".join(lines)
    assert "6 series macro caducadas" in text and "ipc_index/EU 2025-12" in text and "m2_level/JP 2017-02" in text
    assert "ipc_yoy_avg DEGRADED" in text and "ES=OK, EU=STALE" in text
    assert "sin limite de frescura propio" in text and "novel/X" in text


def test_no_lines_when_everything_is_fresh():
    fresh = {(i, g): TODAY for (i, g) in _latest()}
    st = mf.assess(fresh, TODAY)
    assert mf.attention_lines(st, mf.composite_legs(st)) == []


def test_the_cli_table_and_its_stale_filter():
    st, comp = check.run(_latest(), TODAY)
    full, only = check.render(st, comp, False), check.render(st, comp, True)
    assert sum(1 for line in only.splitlines() if line.rstrip().endswith("STALE")) == 6 and "52 series, 6 stale" in only
    assert len(full.splitlines()) > len(only.splitlines())
    assert "composite input ipc_yoy_avg: DEGRADED" in only


class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        return self

    def fetchall(self):
        return self._rows

    def rollback(self):
        pass


def test_the_cycle_report_section_summarises_and_feeds_the_attention_items():
    rows = [(i, g, date.fromisoformat(d)) for i, g, d in REAL_INVENTORY]
    out = cr.collect_macro_freshness(_Conn(rows), TODAY)
    assert out["series"] == 52 and len(out["stale"]) == 6 and out["composites"] == {"ipc_yoy_avg": "DEGRADED"}
    assert any("series macro caducadas" in line for line in out["lines"])
    items = cr.attention_items({"universe": {"active": 1, "inactive": 0, "total": 1}, "wrong_doc_stale": [], "nav_stale": [],
                                "real_orphans": {}, "macro_freshness": out}, None)
    assert any("ipc_yoy_avg DEGRADED" in i for i in items)


def test_the_cycle_report_section_never_raises():
    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

        def rollback(self):
            pass
    assert "RuntimeError" in cr.collect_macro_freshness(Boom(), TODAY)["error"]
