"""
tests/test_sovereign_stress_fnd0294.py -- the sovereign-stress rules (shared/sovereign_stress.py) and their backtest script.

The rules are tested on synthetic yield paths with exact threshold edges; the declared thresholds and episodes are PINNED, so tuning one is a
deliberate edit and not a side effect. No network (R-7): the script's fetchers are not called.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from shared import sovereign_stress as ss

_SPEC = importlib.util.spec_from_file_location("sovereign_stress_backtest_t", Path(__file__).resolve().parent.parent / "scripts" / "audit"
                                               / "sovereign_stress_backtest.py")
bt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bt)


def months(start="2000-01", n=120):
    out, m = [], start
    for _ in range(n):
        out.append(m)
        m = ss.back(m, -1)
    return out


def flat(value, ms):
    return {m: value for m in ms}


def yields(ms, **over):
    """Every country flat at 3.0% unless overridden with a {month: value} dict (or a callable of the month)."""
    y = {c: flat(3.0, ms) for c in ("DE", "FR", "IT", "ES", "PT", "GR", "US", "GB", "JP")}
    for c, v in over.items():
        y[c] = {m: (v(m) if callable(v) else v.get(m, y[c][m])) for m in ms}
    return y


# ---------------------------------------------------------------- pinned declarations
def test_the_declared_thresholds_are_pinned():
    assert ss.RULE_PARAMS == {"periphery_level_pp": 2.0, "periphery_widening_pp": 0.5, "widening_months": 3, "core_percentile": 90.0,
                              "core_percentile_years": 10, "core_widening_pp": 0.2, "core_min_months": 60, "shock_strict_pp": 1.0,
                              "shock_pp": 0.75, "shock_months": 3}
    assert ss.PERIPHERY == ("IT", "ES") and ss.EURO_ERA_START == "1999-01"


def test_the_labelled_episodes_are_pinned():
    assert list(ss.EPISODES) == ["euro sovereign crisis", "taper tantrum", "Italy BTP spike", "Covid dash for cash",
                                 "UK gilt / global yield surge", "US yield spike (term premium)"]
    assert ss.EPISODES["euro sovereign crisis"] == ("2010-05", "2012-09")


# ---------------------------------------------------------------- month helpers
def test_back_crosses_year_boundaries_and_goes_forward_with_a_negative_k():
    assert ss.back("2026-03", 3) == "2025-12" and ss.back("2026-01", 1) == "2025-12" and ss.back("2026-12", -1) == "2027-01"
    assert ss.back("2000-01", 12) == "1999-01"


def test_within_includes_both_slack_edges_and_nothing_beyond():
    assert ss.within("2010-02", "2010-05", "2012-09") and ss.within("2012-12", "2010-05", "2012-09")
    assert not ss.within("2010-01", "2010-05", "2012-09") and not ss.within("2013-01", "2010-05", "2012-09")


def test_percentile_rank():
    ms = months(n=24)
    rising = {m: float(i) for i, m in enumerate(ms)}
    falling = {m: -float(i) for i, m in enumerate(ms)}
    assert ss.percentile_rank(ms[-1], rising, 10) == 100.0                               # a new maximum
    assert ss.percentile_rank(ms[-1], falling, 10) == pytest.approx(100.0 / 24)           # a new minimum: only itself is <= it
    assert ss.percentile_rank(ms[0], falling, 10) == 100.0                                # a one-point history is its own maximum
    assert ss.percentile_rank(ms[-1], rising, 1) == 100.0                                 # the window argument is in years (12 months here)
    old_peak = {m: (100.0 if i == 0 else 1.0) for i, m in enumerate(ms)}
    assert ss.percentile_rank(ms[-1], old_peak, 10) == pytest.approx(100.0 * 23 / 24)     # beaten only by the first month


# ---------------------------------------------------------------- R1 .. R5 at their exact edges
M = months()


def rule(rid, y):
    return ss.build_rules(y)[rid][2]


def test_r1_periphery_level_edge_uses_the_larger_of_it_and_es():
    at = M[60]
    assert rule("R1", yields(M, IT={at: 5.0}))(at) is True                            # IT-DE = 2.00 exactly
    assert rule("R1", yields(M, IT={at: 4.99}))(at) is False
    assert rule("R1", yields(M, ES={at: 5.0}))(at) is True
    assert rule("R1", yields(M, GR={at: 12.0}, PT={at: 9.0}))(at) is False           # Greece / Portugal alone never trigger it


def test_r2_periphery_widening_edge_over_three_months():
    at = M[60]
    assert rule("R2", yields(M, IT={at: 3.5}))(at) is True                            # +0.50 vs three months earlier
    assert rule("R2", yields(M, IT={at: 3.49}))(at) is False
    assert rule("R2", yields(M, IT={ss.back(at, 1): 3.5, at: 3.5}))(at) is True       # the window is 3 months: the level still differs from t-3


def test_r2_is_false_not_an_error_without_history():
    y = yields(M[:2])
    assert rule("R2", y)(M[1]) is False


def test_r3_needs_five_years_of_history_a_top_decile_spread_and_a_widening():
    base = {m: 0.5 for m in M}
    at = M[100]
    wide = dict(base)
    wide[at] = 0.8                                                                       # top of its history and +0.3 vs 3 months earlier
    y = yields(M, FR={m: 3.0 + wide[m] for m in M})
    assert rule("R3", y)(at) is True
    flat_high = {m: 3.0 + 0.8 for m in M}                                                # high but not widening
    assert rule("R3", yields(M, FR=flat_high))(at) is False
    assert rule("R3", y)(M[10]) is False                                                 # fewer than 60 months of history
    slight = dict(base)
    slight[at] = 0.69                                                                    # +0.19: below the widening edge
    assert rule("R3", yields(M, FR={m: 3.0 + slight[m] for m in M}))(at) is False


@pytest.mark.parametrize("rid,threshold", [("R4", 1.0), ("R5", 0.75)])
def test_r4_r5_shock_edges_for_any_of_de_us_gb_jp(rid, threshold):
    at = M[60]
    for c in ("DE", "US", "GB", "JP"):
        assert rule(rid, yields(M, **{c: {at: 3.0 + threshold}}))(at) is True
        assert rule(rid, yields(M, **{c: {at: 3.0 + threshold - 0.01}}))(at) is False
    assert rule(rid, yields(M, IT={at: 9.0}))(at) is False                              # a euro-periphery move is not a global rate shock


def test_r4_is_stricter_than_r5():
    at = M[60]
    y = yields(M, US={at: 3.9})
    assert rule("R5", y)(at) is True and rule("R4", y)(at) is False


# ---------------------------------------------------------------- scoring
def test_score_counts_episode_months_and_false_alarms_outside_every_window():
    sample = months("1999-01", 336)
    flags = {"2011-06", "2011-07", "2022-09", "2005-03", "2005-04"}                      # 3 inside episodes, 2 outside every window
    s = ss.score(flags, sample)
    assert s["per_episode"]["euro sovereign crisis"] == 2 and s["per_episode"]["UK gilt / global yield surge"] == 1
    assert s["detected"] == 2 and s["false_months"] == ["2005-03", "2005-04"] and s["false_pct"] == pytest.approx(100 * 2 / 336)


def test_score_ignores_flags_outside_the_evaluation_sample():
    s = ss.score({"1995-01", "2011-06"}, months("1999-01", 336))
    assert s["false_months"] == [] and s["flagged"] == 1


def test_a_persistent_state_rule_is_visible_as_many_months_in_one_episode():
    sample = months("1999-01", 336)
    s = ss.score({m for m in sample if "2010-01" <= m <= "2014-12"}, sample)
    assert s["per_episode"]["taper tantrum"] > 0 and s["detected"] >= 2                  # the property the first, withdrawn run overlooked


# ---------------------------------------------------------------- evaluate() and the script
def test_evaluate_restricts_to_the_euro_era_and_reports_the_latest_month():
    ms = months("1990-01", 420)
    y = yields(ms, IT={m: 6.0 for m in ms[-3:]})                                         # IT-DE = 3.0pp over the last three months
    r = ss.evaluate(y)
    assert r["months"][0] == "1999-01" and r["months"][-1] == ms[-1]
    assert set(r["rules"]) == {"R1", "R2", "R3", "R4", "R5"} and r["rules"]["R1"]["now"] is True and r["rules"]["R5"]["now"] is False


def test_evaluate_with_a_missing_country_returns_no_months_instead_of_failing():
    y = yields(M)
    del y["JP"]
    assert ss.evaluate(y)["months"] == []


def test_the_script_renders_every_rule_and_the_state():
    ms = months("1990-01", 420)
    text = bt.render(ss.evaluate(yields(ms)))
    for rid in ("R1", "R2", "R3", "R4", "R5"):
        assert f"\n{rid} " in text or text.startswith(rid)
    assert "euro era:" in text and "false-alarm months" in text
