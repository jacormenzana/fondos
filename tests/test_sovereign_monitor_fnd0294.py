"""FND-0294 step 5: the sovereign-stress monitor line of the cycle report (monitor-only, [ATENCION], owner decision 2026-10-24).

Pure: synthetic yield series, a fake connection, no database and no network. The thresholds are NOT re-tested here (test_sovereign_stress_fnd0294.py
pins them and the evidence run): this file pins that the monitor evaluates exactly those rules, in the right month, and that missing data is loud.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from shared import sovereign_stress as ss

_ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cr = _load("p1p2_cycle_report_sovereign_t", "scripts/launch/p1p2_cycle_report.py")


def _months(first="2016-01", last="2026-09"):
    out, (y, m) = [], map(int, first.split("-"))
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


BASE_LEVEL = {"DE": 3.0, "IT": 3.9, "ES": 3.5, "FR": 3.4, "US": 4.5, "GB": 4.8, "JP": 1.0}      # calm: IT-DE 0.9, ES-DE 0.5, FR-DE 0.4


def _y(last="2026-09", **override):
    """{country: {month: yield}}: flat calm levels; override[country] = f(month) -> yield."""
    ms = _months(last=last)
    return {c: {m: (override[c](m) if c in override else lvl) for m in ms} for c, lvl in BASE_LEVEL.items()}


def _lift(country, last_months, deltas):
    """A yield that sits at its calm level and, in the last len(deltas) months of the sample, is raised by `deltas`."""
    tail = dict(zip(last_months, deltas))
    return lambda m: BASE_LEVEL[country] + tail.get(m, 0.0)


def test_a_calm_market_has_every_rule_off_and_no_attention_line():
    res = ss.monitor(_y())
    assert res["state"] == "OK" and res["month"] == "2026-09"
    assert set(res["rules"]) == set(ss.build_rules(_y())) and not any(res["rules"].values())
    assert ss.attention_lines(res) == []
    line = ss.status_line(res)
    assert "2026-09" in line and "R1=off" in line and "R3=off" in line and "IT-DE 0.90pp" in line and "FR-DE 0.40pp" in line


def test_periphery_level_rule_r1_raises_an_attention_line_with_the_readings():
    last = ["2026-08", "2026-09"]
    res = ss.monitor(_y(IT=_lift("IT", last, [1.3, 1.4])))                         # IT-DE = 0.9 + 1.4 = 2.3pp
    assert res["rules"]["R1"] is True
    lines = ss.attention_lines(res)
    assert any(ln.startswith("ESTRES SOBERANO R1 activa (2026-09)") and "IT-DE 2.30pp" in ln for ln in lines)
    assert all("Solo monitor" in ln for ln in lines)


def test_global_yield_shock_r5_fires_at_075_and_r4_needs_a_full_point():
    last = ["2026-07", "2026-08", "2026-09"]
    res = ss.monitor(_y(US=_lift("US", last, [0.2, 0.5, 0.8])))                    # +0.8pp over three months
    assert res["rules"]["R5"] is True and res["rules"]["R4"] is False
    assert res["readings"]["max_shock_3m"] == 0.8 or abs(res["readings"]["max_shock_3m"] - 0.8) < 1e-9
    assert any("R5" in ln and "0.80pp" in ln for ln in ss.attention_lines(res))
    res4 = ss.monitor(_y(US=_lift("US", last, [0.3, 0.7, 1.1])))
    assert res4["rules"]["R4"] is True


def test_core_spread_watch_r3_is_reported_as_a_vigilance_line_not_a_stress_line():
    last = ["2026-06", "2026-07", "2026-08", "2026-09"]
    res = ss.monitor(_y(FR=_lift("FR", last, [0.0, 0.2, 0.4, 0.6])))               # FR-DE 0.4 -> 1.0: top decile and +0.6pp in 3 months
    assert res["rules"]["R3"] is True
    lines = ss.attention_lines(res)
    r3 = [ln for ln in lines if "R3" in ln]
    assert r3 and r3[0].startswith("VIGILANCIA estres soberano R3") and "1 de 6 episodios" in r3[0] and "FR-DE 1.00pp" in r3[0]
    assert not any(ln.startswith("ESTRES SOBERANO R3") for ln in lines)


def test_the_month_is_the_newest_one_every_country_has():
    y = _y()
    del y["JP"]["2026-09"]                                                         # one feed is a month behind
    res = ss.monitor(y)
    assert res["month"] == "2026-08" and res["state"] == "OK"
    assert ss.monitor({c: dict(s) for c, s in _y(last="2026-08").items()})["month"] == "2026-08"


def test_missing_data_is_a_state_with_a_line_never_a_silent_all_clear():
    none = ss.monitor({})
    assert none["state"] == "NO_DATA" and none["rules"] == {}
    assert "macro_discovery" in ss.attention_lines(none)[0] and ss.status_line(none).endswith("NO_DATA")
    y = _y()
    del y["GB"], y["JP"]
    part = ss.monitor(y)
    assert part["state"] == "INCOMPLETE" and part["missing"] == ["GB", "JP"]
    assert "GB, JP" in ss.attention_lines(part)[0]
    y2 = _y()
    y2["IT"] = {}                                                                  # a country present but empty is also missing
    assert ss.monitor(y2)["state"] == "INCOMPLETE"


def test_rows_from_series_macro_become_month_keyed_yields_and_nulls_are_dropped():
    rows = [(date(2026, 9, 1), "DE", 3.47), (date(2026, 8, 1), "DE", None), ("2026-09-01", "IT", 4.36)]
    assert ss.yields_from_rows(rows) == {"DE": {"2026-09": 3.47}, "IT": {"2026-09": 4.36}}


# ---------------------------------------------------------------- the cycle report section

class _Conn:
    def __init__(self, rows):
        self._rows, self.calls = rows, []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return self._rows

    def rollback(self):
        pass


def _rows(y):
    return [(date(int(m[:4]), int(m[5:]), 1), c, v) for c, s in y.items() for m, v in s.items()]


def _report(section):
    return {"universe": {"active": 1, "inactive": 0, "total": 1}, "wrong_doc_stale": [], "nav_stale": [], "real_orphans": {},
            "sovereign_stress": section}


def test_the_collector_reads_the_declared_indicator_and_feeds_the_attention_items():
    last = ["2026-07", "2026-08", "2026-09"]
    conn = _Conn(_rows(_y(US=_lift("US", last, [0.2, 0.5, 0.8]))))
    out = cr.collect_sovereign_stress(conn)
    assert conn.calls[0][1] == (ss.YIELD_INDICATOR,) and "%s" in conn.calls[0][0]       # parametrised literal SQL (the EXPLAIN sweep resolves it)
    assert out["state"] == "OK" and out["rules"]["R5"] is True and "R5=ON" in out["status"]
    items = cr.attention_items(_report(out), None)
    assert any("ESTRES SOBERANO R5" in i for i in items)


def test_an_empty_table_is_an_attention_item_until_the_series_are_loaded():
    out = cr.collect_sovereign_stress(_Conn([]))
    assert out["state"] == "NO_DATA"
    assert any("yield_10y_gov" in i for i in cr.attention_items(_report(out), None))


def test_the_collector_never_raises_and_the_failure_is_reported():
    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

        def rollback(self):
            pass
    out = cr.collect_sovereign_stress(Boom())
    assert "RuntimeError" in out["error"]
    assert any("monitor de estres soberano" in i and "RuntimeError" in i for i in cr.attention_items(_report(out), None))


def test_a_calm_market_adds_no_attention_item_but_the_status_line_is_in_the_text_report():
    out = cr.collect_sovereign_stress(_Conn(_rows(_y())))
    assert out["lines"] == []
    assert not any("soberano" in i.lower() for i in cr.attention_items(_report(out), None))
    assert "Estres soberano (FND-0294, solo monitor) 2026-09" in out["status"]
