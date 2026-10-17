# proyecto2/tests/discovery/test_source_chain_fnd0256.py
# -*- coding: utf-8 -*-
"""
FND-0256: the ECB `ICP` dataset was discontinued with the Feb-2026 HICP overhaul (last point 2025-12) and Eurostat's
prc_hicp_minr (unit I15 = 2015=100) continues the same index. source_chain proves a fallback is the SAME measure before it
continues a series; macro_discovery._apply_ipc_eu_chain applies it to ipc_index/EU. Fixtures are the real values probed on
2026-10-17 (ECB ICP.M.U2.N.000000.4.INX vs Eurostat prc_hicp_minr I15 EA); no network.

R-7: no pipeline / core.io imports.
"""
import sys
from pathlib import Path

import pytest

_P2_ROOT = Path(__file__).resolve().parent.parent.parent
_REPO = _P2_ROOT.parent
for _p in (str(_P2_ROOT), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto2.src.discovery import macro_discovery as md  # noqa: E402
from proyecto2.src.discovery import source_chain as sc  # noqa: E402

ECB = {"2025-09-01": 129.43, "2025-10-01": 129.70, "2025-11-01": 129.33, "2025-12-01": 129.54}
EUROSTAT = {"2025-10-01": 129.70, "2025-11-01": 129.33, "2025-12-01": 129.56, "2026-01-01": 130.1, "2026-09-01": 134.31}


def _ecb_rows():
    macro = [{"date": d, "indicator": "ipc_index", "geography": "EU", "value": v, "unit": "index", "source": "BCE"}
             for d, v in ECB.items()]
    infl = [{"date": d, "geography": "EU", "ipc_index": v, "source": "BCE"} for d, v in ECB.items()]
    return infl, macro


# ---------------------------------------------------------------- splice_check
def test_the_real_ecb_vs_eurostat_overlap_passes_the_hicp_tolerance():
    r = sc.splice_check(ECB, EUROSTAT, **md._HICP_EA_SPLICE)
    assert r.ok and r.n_overlap == 3 and r.max_abs_diff == pytest.approx(0.02)


def test_a_different_measure_is_refused():
    shifted = {d: v + 1.0 for d, v in EUROSTAT.items()}                    # e.g. a different base year
    r = sc.splice_check(ECB, shifted, **md._HICP_EA_SPLICE)
    assert not r.ok and "abs_tol" in r.reason


def test_too_short_an_overlap_is_refused():
    r = sc.splice_check(ECB, {"2025-12-01": 129.56, "2026-01-01": 130.1}, **md._HICP_EA_SPLICE)
    assert not r.ok and r.n_overlap == 1


def test_a_tolerance_is_mandatory():
    with pytest.raises(ValueError):
        sc.splice_check(ECB, EUROSTAT)


def test_relative_tolerance_covers_scaled_levels():
    assert sc.splice_check({"a": 1000.0, "b": 2000.0, "c": 3000.0}, {"a": 1000.5, "b": 2000.5, "c": 3000.5}, rel_tol=0.001).ok
    assert not sc.splice_check({"a": 1000.0, "b": 2000.0, "c": 3000.0}, {"a": 1100.0, "b": 2000.0, "c": 3000.0}, rel_tol=0.001).ok


def test_only_the_last_window_of_shared_months_is_compared():
    primary = {f"2020-{m:02d}-01": 100.0 for m in range(1, 13)} | {f"2021-{m:02d}-01": 100.0 for m in range(1, 13)}
    fallback = dict(primary)
    fallback["2020-01-01"] = 999.0                                         # an old disagreement, outside window=12
    assert sc.splice_check(primary, fallback, abs_tol=0.1, window=12).ok
    assert not sc.splice_check(primary, fallback, abs_tol=0.1, window=24).ok


# ---------------------------------------------------------------- extend_series
def test_extension_emits_only_months_after_the_primary_and_never_overrides_it():
    rows = sc.extend_series(ECB, EUROSTAT, indicator="ipc_index", geography="EU", unit="index", source="EUROSTAT_MINR")
    assert [r["date"] for r in rows] == ["2026-01-01", "2026-09-01"]
    assert all(r["source"] == "EUROSTAT_MINR" for r in rows)


def test_extension_scales_the_fallback_unit():
    rows = sc.extend_series({"2019-08-01": 1.0}, {"2019-08-01": 5.0, "2019-09-01": 2.0}, indicator="m2_level", geography="CN",
                            unit="cny", source="OECD", scale=1e6)
    assert rows == [{"date": "2019-09-01", "indicator": "m2_level", "geography": "CN", "value": 2.0e6, "unit": "cny", "source": "OECD"}]


# ---------------------------------------------------------------- JSON-stat parsing
def _jsonstat(sizes, periods, values):
    return {"id": ["freq", "unit", "coicop18", "geo", "time"], "size": sizes,
            "dimension": {"time": {"category": {"index": {p: i for i, p in enumerate(periods)}}}}, "value": values}


def test_jsonstat_series_parses_a_single_series_and_skips_gaps():
    d = _jsonstat([1, 1, 1, 1, 3], ["2026-07", "2026-08", "2026-09"], {"0": 132.93, "2": 134.31})
    assert sc.jsonstat_series(d) == {"2026-07-01": 132.93, "2026-09-01": 134.31}


def test_jsonstat_series_refuses_an_unfiltered_response():
    with pytest.raises(ValueError):
        sc.jsonstat_series(_jsonstat([1, 2, 1, 1, 3], ["2026-07", "2026-08", "2026-09"], {"0": 1.0}))


# ---------------------------------------------------------------- the loader wiring
def test_the_chain_extends_ipc_index_eu_and_series_inflation(capsys):
    infl, macro = _ecb_rows()
    md._apply_ipc_eu_chain(infl, macro, "2000-01", fetch=lambda desde: dict(EUROSTAT))
    assert [r["date"] for r in macro if r["source"] == "EUROSTAT_MINR"] == ["2026-01-01", "2026-09-01"]
    assert [r["date"] for r in infl if r["source"] == "EUROSTAT_MINR"] == ["2026-01-01", "2026-09-01"]
    assert [r for r in infl if r["source"] == "EUROSTAT_MINR"][-1]["ipc_index"] == 134.31
    assert "[CHAIN] ipc_index/EU fallback ACTIVE" in capsys.readouterr().out


def test_a_failed_splice_leaves_the_series_untouched_and_says_so(capsys):
    infl, macro = _ecb_rows()
    n = len(macro)
    md._apply_ipc_eu_chain(infl, macro, "2000-01", fetch=lambda desde: {d: v + 5 for d, v in EUROSTAT.items()})
    assert len(macro) == n and "REFUSED" in capsys.readouterr().out


def test_an_unreachable_fallback_does_not_abort_the_ecb_loader(capsys):
    def boom(desde):
        raise ConnectionError("down")
    infl, macro = _ecb_rows()
    md._apply_ipc_eu_chain(infl, macro, "2000-01", fetch=boom)
    assert len(macro) == len(ECB) and "UNAVAILABLE" in capsys.readouterr().out


def test_when_the_ecb_resumes_the_fallback_adds_nothing():
    infl, macro = _ecb_rows()
    macro.append({"date": "2026-09-01", "indicator": "ipc_index", "geography": "EU", "value": 134.3, "unit": "index", "source": "BCE"})
    md._apply_ipc_eu_chain(infl, macro, "2000-01", fetch=lambda desde: dict(EUROSTAT))
    assert not [r for r in macro if r["source"] == "EUROSTAT_MINR"]


def test_load_bce_series_calls_the_chain_once(monkeypatch):
    calls = []
    monkeypatch.setattr(md, "_BCE_SERIES", {})                              # no ECB request at all
    monkeypatch.setattr(md, "_apply_ipc_eu_chain", lambda infl, macro, desde: calls.append(desde))
    md.load_bce_series("2000-01")
    assert calls == ["2000-01"]


# ================================================================ FND-0293: OECD G20 CPI for JP (continue) and CN (replace)
def _oecd_cn(n=40, start=100.0, step=0.003):
    """A synthetic monthly CN index 2023-01.. with a constant monthly growth."""
    out, v = {}, start
    for i in range(n):
        y, m = 2023 + i // 12, i % 12 + 1
        out[f"{y}-{m:02d}-01"] = round(v, 3)
        v *= 1 + step
    return out


def _fred_mom_ref(index, upto, jitter=0.0):
    mom = sc.pct_change(index, 1)
    return {d: round(v, 1) + jitter for d, v in mom.items() if d <= upto}


class _Ref:                                           # a [date, value] frame like _fred_fetch returns
    def __init__(self, ref):
        import pandas as pd
        self._df = pd.DataFrame({"date": pd.to_datetime(list(ref)), "value": list(ref.values())})

    def __getitem__(self, k):
        return self._df[k]


def test_pct_change_monthly_and_yearly():
    s = {"2024-12-01": 100.0, "2025-01-01": 101.0, "2025-12-01": 105.0}
    assert sc.pct_change(s, 1) == {"2025-01-01": pytest.approx(1.0)}
    assert sc.pct_change(s, 12) == {"2025-12-01": pytest.approx(5.0)}


def test_oecd_csv_series_parses_areas_and_skips_empty_values():
    txt = "REF_AREA,TIME_PERIOD,OBS_VALUE\nCHN,2026-08,116.2862\nCHN,2026-09,116.5106\nJPN,2026-08,116.4783\nJPN,2026-09,\n"
    out = sc.oecd_csv_series(txt)
    assert out == {"CHN": {"2026-08-01": 116.2862, "2026-09-01": 116.5106}, "JPN": {"2026-08-01": 116.4783}}


def test_oecd_csv_series_refuses_a_changed_period_layout():
    with pytest.raises(ValueError):
        sc.oecd_csv_series("REF_AREA,TIME_PERIOD,OBS_VALUE\nCHN,2026-Q3,1.0\n")


def _chain(macro, oecd, ref=None, ref_exc=None):
    def fetch_ref():
        if ref_exc:
            raise ref_exc
        return _Ref(ref)
    md._apply_oecd_cpi_chain(macro, "2023-01", None, fetch=lambda desde: oecd, fetch_ref=fetch_ref)


def test_japan_is_continued_after_the_last_fred_month_only(capsys):
    jp_fred = {"2024-10-01": 100.0, "2024-11-01": 100.2, "2024-12-01": 100.4}
    macro = [{"date": d, "indicator": "ipc_index", "geography": "JP", "value": v, "unit": "index", "source": "FRED"} for d, v in jp_fred.items()]
    oecd = {"JPN": {**jp_fred, "2025-01-01": 100.7, "2025-02-01": 101.0}, "CHN": {}}
    _chain(macro, oecd, ref={})
    new = [m for m in macro if m["source"] == "OECD_G20" and m["geography"] == "JP"]
    assert [m["date"] for m in new] == ["2025-01-01", "2025-02-01"]
    assert "[CHAIN] ipc_index/JP fallback ACTIVE" in capsys.readouterr().out


def test_japan_on_a_different_base_is_refused(capsys):
    macro = [{"date": f"2024-{m:02d}-01", "indicator": "ipc_index", "geography": "JP", "value": 100.0 + m, "unit": "index", "source": "FRED"}
             for m in range(1, 7)]
    oecd = {"JPN": {f"2024-{m:02d}-01": 130.0 + m for m in range(1, 9)}, "CHN": {}}
    n = len(macro)
    _chain(macro, oecd, ref={})
    assert len(macro) == n and "ipc_index/JP fallback REFUSED" in capsys.readouterr().out


def test_china_index_replaces_the_series_once_it_reproduces_the_old_mom_rates(capsys):
    cn = _oecd_cn()
    ref = _fred_mom_ref(cn, upto="2024-03-01")                      # the old FRED 657 series, 1-decimal MoM %
    macro = []
    _chain(macro, {"JPN": {}, "CHN": cn}, ref=ref)
    rows = [m for m in macro if m["geography"] == "CN"]
    assert len(rows) == len(cn) and all(m["indicator"] == "ipc_index" and m["source"] == "OECD_G20" for m in rows)
    assert "ipc_index/CN REPLACED" in capsys.readouterr().out


def test_china_is_refused_when_the_old_series_is_not_the_same_measure(capsys):
    cn = _oecd_cn()
    wrong = {d: v + 1.0 for d, v in _fred_mom_ref(cn, upto="2024-03-01").items()}     # e.g. a YoY series, an order of magnitude off
    macro = []
    _chain(macro, {"JPN": {}, "CHN": cn}, ref=wrong)
    assert not [m for m in macro if m["geography"] == "CN"] and "ipc_index/CN OECD index REFUSED" in capsys.readouterr().out


def test_china_is_refused_when_a_month_of_the_old_series_would_survive_the_upsert(capsys):
    cn = _oecd_cn()
    ref = _fred_mom_ref(cn, upto="2024-03-01")
    ref["2022-06-01"] = 0.3                                          # a stored month the OECD index does not cover
    macro = []
    _chain(macro, {"JPN": {}, "CHN": cn}, ref=ref)
    out = capsys.readouterr().out
    assert not [m for m in macro if m["geography"] == "CN"] and "absent from OECD" in out


def test_an_unreachable_oecd_or_reference_loads_nothing_and_never_raises(capsys):
    macro = []
    md._apply_oecd_cpi_chain(macro, "2023-01", None, fetch=lambda d: (_ for _ in ()).throw(ConnectionError("x")))
    assert macro == [] and "OECD UNAVAILABLE" in capsys.readouterr().out
    _chain(macro, {"JPN": {}, "CHN": _oecd_cn()}, ref_exc=ConnectionError("x"))
    assert macro == [] and "reference series unavailable" in capsys.readouterr().out


def test_the_mislabelled_fred_china_entry_is_gone():
    assert "CPALTT01CNM657N" not in md._FRED_SERIES
    assert md._CN_FRED_MOM_ID == "CPALTT01CNM657N"                   # kept only as the validation reference


def test_load_fred_series_calls_the_oecd_chain_once(monkeypatch):
    calls = []
    monkeypatch.setattr(md, "_FRED_SERIES", {})
    monkeypatch.setattr(md, "_apply_oecd_cpi_chain", lambda macro, desde, key, **kw: calls.append(desde))
    md.load_fred_series("2000-01", api_key=None)
    assert calls == ["2000-01"]
