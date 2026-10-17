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
