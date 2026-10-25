"""FND-0317: fallback legs for the sovereign 10y yields (monitor-only).

Pure: injected fetchers, no network, no database. The contract mirrors the HICP / M2 chains (FND-0256 / FND-0293): the primary always wins where it has
data, the fallback only supplies the months AFTER the primary's last one, and only when the splice check over the shared months passes.
Measured 2026-10-25 on the live series: ECB vs the FRED/OECD mirror max |diff| DE 0.003, FR 0.055, IT 0.000, ES 0.009 pp; US IRLTLT01 vs the DGS10
monthly mean max 0.005 pp over 321 months; hence abs_tol 0.10 with the strict default min_share (every shared month inside the tolerance).
"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from proyecto2.src.discovery import macro_discovery as md  # noqa: E402
from shared import sovereign_stress as ss  # noqa: E402

MONTHS = [f"2025-{m:02d}-01" for m in range(1, 13)] + [f"2026-{m:02d}-01" for m in range(1, 8)]          # 19 months, 2025-01..2026-07
VALUE = {m: 3.0 + 0.01 * i for i, m in enumerate(MONTHS)}


def _row(geo, month, value, source="BCE"):
    return {"date": month, "indicator": ss.YIELD_INDICATOR, "geography": geo, "value": value, "unit": "pct", "source": source}


def _rows(geo, months):
    return [_row(geo, m, VALUE[m]) for m in months]


def _fb(months, shift=0.0):
    return {m: VALUE[m] + shift for m in months}


def test_the_fallback_supplies_only_the_months_after_the_primarys_last():
    rows = _rows("DE", MONTHS[:-2])                                       # primary ends 2026-05
    md._extend_yield(rows, "DE", lambda: _fb(MONTHS), "FRED_OECD")
    added = [r for r in rows if r["source"] == "FRED_OECD"]
    assert [r["date"] for r in added] == MONTHS[-2:]                      # 2026-06, 2026-07 only
    assert all(r["indicator"] == ss.YIELD_INDICATOR and r["geography"] == "DE" and r["unit"] == "pct" for r in added)
    assert len([r for r in rows if r["date"] in MONTHS[:-2]]) == len(MONTHS) - 2      # the primary's months are untouched, none duplicated


def test_the_primary_wins_where_it_has_data_even_if_the_fallback_differs_slightly():
    rows = _rows("DE", MONTHS[:-1])
    md._extend_yield(rows, "DE", lambda: _fb(MONTHS, shift=0.04), "FRED_OECD")      # 4bp apart: inside the 0.10 tolerance
    prim = {r["date"]: r["value"] for r in rows if r["source"] == "BCE"}
    assert prim == {m: VALUE[m] for m in MONTHS[:-1]}


def test_a_current_primary_gets_nothing_added(capsys):
    rows = _rows("DE", MONTHS)
    md._extend_yield(rows, "DE", lambda: _fb(MONTHS), "FRED_OECD")
    assert len(rows) == len(MONTHS)


def test_a_fallback_that_disagrees_with_the_primary_is_refused(capsys):
    rows = _rows("DE", MONTHS[:-2])
    md._extend_yield(rows, "DE", lambda: _fb(MONTHS, shift=0.25), "FRED_OECD")       # 25bp apart: not the same measure
    assert len(rows) == len(MONTHS) - 2
    assert "REFUSED" in capsys.readouterr().out


def test_an_unavailable_fallback_never_aborts_the_loader(capsys):
    rows = _rows("DE", MONTHS[:-2])

    def boom():
        raise RuntimeError("fred down")
    md._extend_yield(rows, "DE", boom, "FRED_OECD")
    assert len(rows) == len(MONTHS) - 2
    out = capsys.readouterr().out
    assert "UNAVAILABLE" in out and "RuntimeError" in out


def test_an_empty_primary_is_refused_not_filled_from_the_fallback(capsys):
    """Documented limit: the chain compares against the rows fetched in the SAME run. A run whose primary fetch returned nothing loads nothing from the
    fallback (no overlap to prove it is the same measure); the freshness TTL and the monitor lag warning cover that case."""
    rows = []
    md._extend_yield(rows, "DE", lambda: _fb(MONTHS), "FRED_OECD")
    assert rows == [] and "REFUSED" in capsys.readouterr().out


def test_the_euro_chain_covers_exactly_the_euro_countries_and_tags_the_source():
    rows = [r for cc in ss.EURO_YIELD_COUNTRIES for r in _rows(cc, MONTHS[:-1])] + _rows("US", MONTHS[:-1])
    asked = []

    def fetch(cc, desde, key):
        asked.append(cc)
        return _fb(MONTHS)
    md._apply_euro_yield_chain(rows, "2000-01", api_key="k", fetch=fetch)
    assert asked == list(ss.EURO_YIELD_COUNTRIES)                          # DE FR IT ES: never the shock-only countries
    added = [r for r in rows if r["source"] == "FRED_OECD"]
    assert {r["geography"] for r in added} == set(ss.EURO_YIELD_COUNTRIES) and len(added) == len(ss.EURO_YIELD_COUNTRIES)
    assert not [r for r in rows if r["geography"] == "US" and r["source"] != "BCE"]


def test_the_us_chain_continues_irltlt01us_with_the_dgs10_monthly_mean():
    rows = [_row("US", m, VALUE[m], source="FRED") for m in MONTHS[:-1]]
    md._apply_us_yield_chain(rows, "2000-01", api_key="k", fetch=lambda desde, key: _fb(MONTHS))
    added = [r for r in rows if r["source"] == "FRED_DGS10"]
    assert [r["date"] for r in added] == [MONTHS[-1]] and added[0]["geography"] == "US"


def test_dgs10_monthly_mean_excludes_the_month_in_progress():
    days = pd.date_range("2026-08-03", "2026-10-16", freq="B")
    daily = pd.DataFrame({"date": days, "value": [4.0 if d.month == 8 else 4.5 if d.month == 9 else 9.9 for d in days]})
    out = md._dgs10_monthly_mean(daily, today=date(2026, 10, 16))
    assert out == {"2026-08-01": 4.0, "2026-09-01": 4.5}                   # October is still open: a partial mean is not a month


def test_no_new_indicator_and_no_shock_only_country_gets_a_fallback():
    assert set(ss.EURO_YIELD_COUNTRIES) == {"DE", "FR", "IT", "ES"}
    # GB and JP have no verified free alternative: they are deliberately absent from the chains
    assert not hasattr(md, "_apply_gb_yield_chain") and not hasattr(md, "_apply_jp_yield_chain")
