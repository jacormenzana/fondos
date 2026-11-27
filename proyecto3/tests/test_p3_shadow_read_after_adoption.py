"""FND-0445: the reader's post-adoption branch (`scripts/audit/p3/p3_shadow_read.py --after-adoption`), doc/backlog/PKG_M_ac_adoption.md section 2 and PREREG_P3_7 sections 13-14.

Series: before adoption as today (AC = `chain` and L = `off` of the AC rows); from adoption on the baseline stream (adopted arm = `off`, the DRAFT; L = `chain`). A row of month m earns the return of
month m+1. R12 is an alarm, R24 / R36 and the revert trigger can say REVERT; nothing is shown before 12 post-adoption months. Synthetic rows only (R-7): no database."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts" / "audit" / "p3"))

import p3_shadow_read as R  # noqa: E402

L_FLAGS = {"PORTFOLIO_HYSTERESIS_ENABLED": False, "P3_L4_FEE_PENALTY_ENABLED": False, "P3_L4_H_YEARS": 3.0}
AC_FLAGS = {"PORTFOLIO_HYSTERESIS_ENABLED": True, "P3_L4_FEE_PENALTY_ENABLED": True, "P3_L4_H_YEARS": 0.58}
B_FLAGS = {"PORTFOLIO_HYSTERESIS_ENABLED": True, "P3_L4_FEE_PENALTY_ENABLED": False, "P3_L4_H_YEARS": 3.0}

MONTHS = [str(p) for p in pd.period_range("2027-01", periods=40, freq="M")]


def _w(*pairs):
    return [{"isin": i, "subportfolio": "Defensiva", "weight": w, "score": 1.0} for i, w in pairs]


def _a(i):                                   # the adopted arm holds still
    return _w(("A", 0.5), ("B", 0.5))


def _l(i):                                   # L rotates half of the portfolio every month
    return _w(("A", 0.5), ("C" if i % 2 else "D", 0.5))


def _ac_row(m, i):
    return {"v": 1, "scenario_id": f"s{m}", "built_at": f"{m}-15T10:00:00+00:00", "off": {"weights": _l(i)}, "chain": {"weights": _a(i)}}


def _base_row(m, i, flags=AC_FLAGS, adopted=True):
    off = _a(i) if adopted else _l(i)
    return {"v": 1, "scenario_id": f"s{m}", "built_at": f"{m}-15T10:00:00+00:00", "off": {"weights": off}, "chain": {"weights": _l(i)}, "one_step": {"weights": _l(i)},
            "live_flags": flags, "identity_check": "NOT_APPLICABLE" if adopted else "MATCH", "band": 0.0, "arm": "L"}


def _streams(n, adopt_at=6, flags=AC_FLAGS):
    ac = [_ac_row(m, i) for i, m in enumerate(MONTHS[:adopt_at])]
    base = [_base_row(m, i, flags=flags if i >= adopt_at else L_FLAGS, adopted=i >= adopt_at) for i, m in enumerate(MONTHS[:n])]
    return ac, base


def _returns(n, **monthly):
    """{return month: Series by ISIN}: constant monthly returns, 0 unless given."""
    idx = [str(p) for p in pd.period_range("2027-01", periods=n, freq="M")]
    return pd.DataFrame({i: [monthly.get(i, 0.0)] * n for i in "ABCD"}, index=idx)


def _fees(entry=0.02, exit_=0.0):
    df = pd.DataFrame({"Entry_Fee_Pct": [entry] * 4, "Exit_Fee_Pct": [exit_] * 4, "Entry_Fee_Pct_Max": [np.nan] * 4, "Exit_Fee_Pct_Max": [np.nan] * 4, "Fund_Nature": ["Mixtos"] * 4},
                      index=pd.Index(list("ABCD"), name="isin"))
    return df


# ---- adoption and the series --------------------------------------------------------------------------------------------------------------------------------------
def test_the_adoption_month_and_arm_come_from_the_flags_of_the_rows_alone():
    _ac, base = _streams(20, adopt_at=6)
    assert R.adoption(R.last_per_month(base)) == (MONTHS[6], "AC")
    _ac, base = _streams(20, adopt_at=6, flags=B_FLAGS)
    assert R.adoption(R.last_per_month(base)) == (MONTHS[6], "B")
    _ac, base = _streams(5, adopt_at=9)
    assert R.adoption(R.last_per_month(base)) is None


def test_the_series_splices_the_old_shadow_before_adoption_and_the_baseline_stream_after():
    ac, base = _streams(10, adopt_at=6)
    arm, l = R.spliced_series(R.last_per_month(ac), R.last_per_month(base), MONTHS[6])
    assert arm[MONTHS[2]] == _a(2) and l[MONTHS[2]] == _l(2)                  # before: chain of the old shadow against its `off`
    assert arm[MONTHS[7]] == _a(7) and l[MONTHS[7]] == _l(7)                  # after: the DRAFT against the baseline chain
    assert sorted(arm) == MONTHS[:10]


def test_monthly_returns_use_closed_months_only():
    nav = pd.DataFrame({"A": [100.0, 110.0, 121.0, 130.0]}, index=pd.to_datetime(["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30"]))
    r = R.monthly_returns(nav, today="2027-04-15")
    assert list(r.index) == ["2027-02", "2027-03"] and r.loc["2027-02", "A"] == pytest.approx(0.10)


def test_a_fund_without_a_return_is_renormalised_away_and_counted_and_cash_earns_nothing():
    ret = pd.Series({"A": 0.10, "B": np.nan})
    gross, missing = R.portfolio_return(_w(("A", 0.4), ("B", 0.4)), ret)          # 20 % cash
    assert gross == pytest.approx(0.08) and missing == pytest.approx(0.5)


def test_a_row_of_month_m_earns_month_m_plus_1_net_of_its_own_trades():
    ac, base = _streams(8, adopt_at=6)
    arm, l = R.spliced_series(R.last_per_month(ac), R.last_per_month(base), MONTHS[6])
    ret = _returns(9, C=0.04, D=0.02)
    obs = R.observations(arm, l, ret, _fees())
    assert obs[0]["month"] == MONTHS[1]                                        # the first row has no predecessor
    first = obs[0]                                                             # month 2027-02 (i = 1): L swaps D for C and earns C's return of 2027-03
    assert first["l_cost"] == pytest.approx(0.5 * 0.02) and first["l_net"] == pytest.approx(0.5 * 0.04 - 0.01)
    assert first["arm_cost"] == 0.0 and first["arm_net"] == 0.0 and first["l_turnover"] == pytest.approx(0.5) and first["arm_turnover"] == 0.0


def test_max_drawdown_counts_from_the_start_value():
    assert R.max_drawdown([-0.10, 0.0]) == pytest.approx(0.10) and R.max_drawdown([0.10, -0.05]) == pytest.approx(0.05) and R.max_drawdown([0.01]) == 0.0


# ---- the rules ----------------------------------------------------------------------------------------------------------------------------------------------------
def _obs(n, arm_net=0.0, l_net=0.0, arm_to=0.10, l_to=0.50):
    f = (lambda x, i: x[i % len(x)] if isinstance(x, list) else x)
    return [{"month": MONTHS[i % 40], "arm_net": f(arm_net, i), "l_net": f(l_net, i), "arm_turnover": arm_to, "l_turnover": l_to} for i in range(n)]


def test_r12_is_an_alarm_on_a_return_gap_beyond_half_a_point_and_never_a_pass():
    assert R.alarm_r12(_obs(11, arm_net=-0.001))["state"] == "NOT DUE"
    assert R.alarm_r12(_obs(12, arm_net=-0.001))["state"] == "ALARM"           # -1.2 pp a year
    assert R.alarm_r12(_obs(12))["state"] == "no alarm (not a pass)"


def test_r12_drawdown_alarm_needs_an_informative_l_drawdown():
    quiet = _obs(12, arm_net=[-0.025] + [0.003] * 11, l_net=[0.0] * 12)
    assert R.alarm_r12(quiet)["state"] == "no alarm (not a pass)"              # L's drawdown 0 %: the drawdown guard does not count
    live = _obs(12, arm_net=[-0.07] + [0.0027] * 11, l_net=[-0.04] + [0.0] * 11)          # 7 % against 4 %, the same return
    res = R.alarm_r12(live)
    assert res["state"] == "ALARM" and res["causes"] == ["drawdown"]


def test_r24_passes_when_not_inferior_and_the_drawdown_is_informative_and_not_deeper():
    obs = _obs(24, arm_net=[-0.04, 0.012] + [0.012] * 10, l_net=[-0.04, 0.010] + [0.010] * 10)
    res = R.check_return(obs, 24)
    assert res["return"] == "PASS" and res["drawdown"] == "PASS" and res["verdict"] == "PASS"


def test_r24_fails_the_return_test_when_inferior():
    obs = _obs(24, arm_net=[-0.04] + [0.005] * 11, l_net=[-0.04] + [0.010] * 11)
    res = R.check_return(obs, 24)
    assert res["return"] == "FAIL" and res["verdict"] == "FAIL"


def test_r24_uses_the_first_24_months_only():
    obs = _obs(24, arm_net=0.01, l_net=0.01) + _obs(12, arm_net=-0.2, l_net=0.01)
    assert R.check_return(obs, 24)["return"] == "PASS"
    assert R.check_return(obs, 36)["return"] == "FAIL"
    assert R.check_return(obs[:23], 24)["verdict"] == "NOT DUE"


def test_the_drawdown_guard_is_not_informative_under_three_percent_never_a_pass():
    res = R.check_return(_obs(24, arm_net=0.01, l_net=0.01), 24)
    assert res["drawdown"] == "NOT INFORMATIVE" and res["verdict"] == "NOT INFORMATIVE"


@pytest.mark.parametrize("l_dd, arm_dd, want", [
    (0.05, 0.059, "PASS"), (0.05, 0.061, "FAIL"),                # within 1.0 pp of L
    (0.145, 0.152, "FAIL"),                                      # within 1 pp but over the 15 % cap
    (0.20, 0.199, "PASS"), (0.20, 0.205, "FAIL"),                # L itself over the cap: not worse
    (0.029, 0.029, "NOT INFORMATIVE"),
])
def test_the_drawdown_guard(l_dd, arm_dd, want):
    assert R.drawdown_guard(arm_dd, l_dd) == want


def test_the_revert_trigger_needs_12_months_and_any_of_three_causes():
    assert R.revert_trigger(_obs(11, arm_net=-0.01), "AC")["state"] == "NOT DUE"
    assert R.revert_trigger(_obs(12), "AC")["state"] == "not triggered"
    assert R.revert_trigger(_obs(12, arm_net=-0.001), "AC")["causes"] == ["return"]
    deep = _obs(12, arm_net=[-0.03] + [0.006] * 11, l_net=[0.0] * 11 + [0.036])
    assert "drawdown" in R.revert_trigger(deep, "AC")["causes"]
    slow = R.revert_trigger(_obs(12, arm_to=0.40, l_to=0.50), "AC")           # reduction 20 % < half of 58 %
    assert slow["causes"] == ["turnover"] and slow["state"] == "REVERT"
    assert R.revert_trigger(_obs(12, arm_to=0.34, l_to=0.50), "AC")["state"] == "not triggered"          # 32 % >= 29.2 %
    assert R.revert_trigger(_obs(12, arm_to=0.36, l_to=0.50), "B")["state"] == "not triggered"           # B's PIT reduction 54 %: floor 27 %


def test_the_revert_turnover_floor_comes_from_the_pit_values():
    assert R.turnover_floor("AC") == pytest.approx(0.5 * (32.2 - 13.4) / 32.2) and R.turnover_floor("B") == pytest.approx(0.5 * (32.2 - 14.8) / 32.2)


# ---- the program ----------------------------------------------------------------------------------------------------------------------------------------------------
def _run(n, adopt_at=6, ret=None, argv=("--after-adoption",), tmp=None, flags=AC_FLAGS):
    ac, base = _streams(n, adopt_at=adopt_at, flags=flags)
    argv = list(argv) + (["--snapshot", str(tmp / "snap")] if tmp else [])
    return R.run(argv, rows_ac=ac, rows_b=[], n_errors=0, fees=_fees(), rows_base=base, returns=ret if ret is not None else _returns(n + 1))


def test_refused_without_an_adoption_and_before_12_post_adoption_months_showing_nothing(tmp_path):
    rc, text = _run(10, adopt_at=20, tmp=tmp_path)
    assert rc == 3 and "no adoption" in text
    rc, text = _run(17, adopt_at=6, tmp=tmp_path)                              # 11 post-adoption rows, 10 with a closed next month and more
    assert rc == 3 and "NOT READY" in text
    for word in ("pp", "turnover reduction", "REVERT"):
        assert word not in text, word
    assert not (tmp_path / "snap").exists()


def test_the_report_after_adoption_states_each_rule(tmp_path):
    rc, text = _run(30, adopt_at=6, tmp=tmp_path)
    assert rc == 0, text
    for part in ("adopted arm AC", "R12", "R24", "R36: NOT DUE", "revert trigger", "optimistic", "verdict"):
        assert part in text, part
    assert (tmp_path / "snap").exists()


def test_a_return_gap_gives_a_revert_verdict(tmp_path):
    rc, text = _run(30, adopt_at=6, ret=_returns(31, B=-0.02, C=0.01, D=0.01), tmp=tmp_path)
    assert rc == 0 and "verdict: REVERT" in text and "owner" in text


def test_the_default_view_adds_the_baseline_integrity_without_comparison_values():
    ac, base = _streams(3, adopt_at=9)
    for r in base:
        r["one_step"] = {"weights": _l(1), "one_way_turnover": 0.31337, "cost_drag": 0.00042}
    rc, text = R.run([], rows_ac=[], rows_b=[], n_errors=0, rows_base=base, n_base_errors=1)
    assert rc == 0 and "P3_BASELINE_SHADOW: 3 SHADOW row(s)" in text and "1 ERROR row(s)" in text and "identity MATCH" in text and "adoption: none" in text
    for forbidden in ("0.31337", "0.00042"):
        assert forbidden not in text


def test_the_default_view_is_unchanged_without_a_baseline_row():
    rc, text = R.run([], rows_ac=[], rows_b=[], n_errors=0)
    assert rc == 0 and "BASELINE" not in text


def test_the_adoption_month_trades_from_the_draft_really_held_not_from_the_arms_own_shadow():
    ac, base = _streams(10, adopt_at=6)
    arm, l = R.spliced_series(R.last_per_month(ac), R.last_per_month(base), MONTHS[6])
    obs = {o["month"]: o for o in R.observations(arm, l, _returns(11), _fees(), adopted_month=MONTHS[6])}
    assert obs[MONTHS[6]]["transition"] and obs[MONTHS[6]]["arm_turnover"] == pytest.approx(0.5)          # from L's {A, D} / {A, C} into {A, B}
    assert not obs[MONTHS[7]]["transition"] and obs[MONTHS[7]]["arm_turnover"] == 0.0
    slow = [dict(o, arm_turnover=0.9, transition=True) for o in _obs(1)] + _obs(11)
    assert R.revert_trigger(slow, "AC")["state"] == "not triggered"


def test_the_band_alone_takes_its_months_before_adoption_from_the_band_stream():
    ac, base = _streams(10, adopt_at=6, flags=B_FLAGS)
    b = [{"built_at": f"{m}-15T10:00:00+00:00", "chain": {"weights": _w(("C", 1.0))}} for m in MONTHS[:6]]
    arm, l = R.spliced_series(R.last_per_month(ac), R.last_per_month(base), MONTHS[6], b_by=R.last_per_month(b))
    assert arm[MONTHS[2]] == _w(("C", 1.0)) and l[MONTHS[2]] == _l(2) and arm[MONTHS[8]] == _a(8)


def test_months_after_a_revert_are_not_read(tmp_path):
    ac, base = _streams(30, adopt_at=6)
    base += [_base_row(m, i, flags=L_FLAGS, adopted=False) for i, m in enumerate(MONTHS[:34]) if i >= 30]
    res = R.review_after_adoption(ac, [], base, _returns(35), _fees())
    assert res["changed_month"] == MONTHS[30] and res["months"][-1] < MONTHS[30]


# ---- three edge cases (audit of 2026-11-27) -----------------------------------------------------------------------------------------------------------------------
def test_a_penalty_at_another_h_is_not_the_preregistered_candidate():
    from shared import config as cfg
    assert R.AC_H_YEARS == cfg.P3_FEE_PENALTY_SHADOW_H_YEARS
    _ac, base = _streams(20, adopt_at=6, flags={**AC_FLAGS, "P3_L4_H_YEARS": 3.0})          # both flags flipped, H left at the old value
    with pytest.raises(R.NotReady, match="not a candidate"):
        R.adoption(R.last_per_month(base))


def test_rows_of_one_month_from_different_builds_are_reported_never_silent(tmp_path):
    ac, base = _streams(30, adopt_at=6)
    same = R.review_after_adoption(ac, [], base, _returns(31), _fees())
    assert same["mixed_builds"] == []
    base[2] = dict(base[2], scenario_id="another-build")                                     # the baseline row of 2027-03 comes from an older build than the AC row
    res = R.review_after_adoption(ac, [], base, _returns(31), _fees())
    assert res["mixed_builds"] == [MONTHS[2]] and res["checks"] == same["checks"]            # reported only: no verdict changes
    text = R.render_after_adoption(res, "0" * 64)
    assert "different builds" in text and MONTHS[2] in text
    assert "different builds" not in R.render_after_adoption(same, "0" * 64)


def test_the_review_reports_a_band_only_row_from_another_build():
    ac = {m: {"scenario_id": f"s{m}"} for m in MONTHS[:4]}
    b = {m: {"scenario_id": f"s{m}"} for m in MONTHS[:4]}
    b[MONTHS[1]] = {"scenario_id": "older"}
    b[MONTHS[3]] = {}                                                                        # a row without an id says nothing
    assert R.mixed_builds(ac, b, MONTHS[:4]) == [MONTHS[1]]


def test_a_funds_latest_observed_month_is_provisional_and_is_not_read():
    # the panel is already on month-end labels: a NAV of 04-26 is labelled 04-30 and is replaced by a later load
    idx = pd.to_datetime(["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30"])
    nav = pd.DataFrame({"A": [100.0, 110.0, 121.0, 130.0]}, index=idx)
    assert list(R.monthly_returns(nav, today="2027-05-10").index) == ["2027-02", "2027-03"]
    later = pd.DataFrame({"A": [100.0, 110.0, 121.0, 130.0, 131.0]}, index=idx.append(pd.to_datetime(["2027-05-24"])))
    assert list(R.monthly_returns(later, today="2027-06-02").index) == ["2027-02", "2027-03", "2027-04"]          # a later observation closes April


def test_a_month_with_no_covered_fund_in_a_series_is_not_an_observation():
    ac, base = _streams(8, adopt_at=6)
    arm, low = R.spliced_series(R.last_per_month(ac), R.last_per_month(base), MONTHS[6])
    ret = _returns(9)
    ret.loc[MONTHS[3]] = np.nan                                                              # nothing held has a return that month
    months = [o["month"] for o in R.observations(arm, low, ret, _fees())]
    assert MONTHS[2] not in months and MONTHS[3] in months
