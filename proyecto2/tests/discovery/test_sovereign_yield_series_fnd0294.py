"""FND-0294 step 2: the sovereign 10y yields reach series_macro through the existing loaders, with a declared stamp and freshness limit.

Pure: no network, no database. The monitor-only contract is pinned too: nothing that scores or builds a portfolio may read the new indicator.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from proyecto2.src.discovery import macro_discovery as md  # noqa: E402
from shared import macro_freshness as mf  # noqa: E402
from shared import period_key_contract as pkc  # noqa: E402
from shared import sovereign_stress as ss  # noqa: E402


def _legs():
    ecb = {c["geography"]: (k, c) for k, c in md._BCE_SERIES.items() if c["indicator"] == ss.YIELD_INDICATOR}
    fred = {c["geography"]: (k, c) for k, c in md._FRED_SERIES.items() if c["indicator"] == ss.YIELD_INDICATOR}
    return ecb, fred


def test_every_country_a_rule_reads_has_exactly_one_feed():
    ecb, fred = _legs()
    needed = {ss.BASE, ss.CORE, *ss.PERIPHERY, *ss.SHOCK_COUNTRIES}
    assert set(ecb) | set(fred) == needed
    assert not set(ecb) & set(fred), "a country must come from one source only"


def test_euro_legs_are_the_ecb_irs_10y_series_and_the_rest_the_oecd_mirror():
    ecb, fred = _legs()
    assert set(ecb) == {"DE", "FR", "IT", "ES"} and set(fred) == {"US", "GB", "JP"}
    for cc, (_, cfg) in ecb.items():
        assert cfg["series_key"] == f"IRS/M.{cc}.L.L40.CI.0000.EUR.N.Z"      # the same key the evidence run used
    for cc, (series_id, _) in fred.items():
        assert series_id == f"IRLTLT01{cc}M156N"


def test_legs_are_percent_monthly_month_start_and_not_inflation():
    for registry in _legs():
        for _, cfg in registry.values():
            assert cfg["unit"] == "pct" and cfg.get("write_inflation") is False
            assert cfg.get("stamp") != "month_end"                           # month START, as declared in the stamp contract


def test_stamp_and_freshness_limits_are_declared():
    assert pkc.EXPECTED_STAMPS[ss.YIELD_INDICATOR] == pkc.MONTH_START
    limit, source = mf.ttl_for(ss.YIELD_INDICATOR, "DE")
    assert source == "explicit" and 60 < limit <= 100


def test_a_month_old_stamp_is_alive_and_a_quarter_old_one_is_stale():
    from datetime import date
    today = date(2026, 10, 24)
    ok = mf.assess({(ss.YIELD_INDICATOR, "DE"): date(2026, 9, 1)}, today)[0]          # what the feeds carried on this date
    dead = mf.assess({(ss.YIELD_INDICATOR, "DE"): date(2026, 6, 1)}, today)[0]
    assert not ok.stale and dead.stale


def test_monitor_only_nothing_that_scores_reads_the_indicator():
    """The portfolio never depends on the sovereign yields before the FND-0193 validation (owner rule, FND-0294)."""
    offenders = []
    for folder in ("proyecto3/src", "proyecto3/config"):
        for path in (_ROOT / folder).rglob("*"):
            if path.suffix in (".py", ".yaml", ".yml") and ("yield_10y_gov" in path.read_text(encoding="utf-8", errors="replace")
                                                            or "sovereign_stress" in path.read_text(encoding="utf-8", errors="replace")):
                offenders.append(path.relative_to(_ROOT).as_posix())
    assert not offenders, f"P3 must not read the monitor-only sovereign yields: {offenders}"
