"""FND-0303 report generator: the pre-registered rules applied to synthetic runs (no DB, no pipeline imports)."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "audit"))

import pit_variants_report as pr   # noqa: E402

DATES = pd.date_range("2005-01-31", "2026-08-31", freq="ME")


def _nav():
    rng = np.random.default_rng(3)
    cols = {}
    for i in range(24):
        r = rng.normal(0.004, 0.02, len(DATES))
        # common shocks in 2008-10..2009-02, 2020-03 and 2022: they give the reference real troughs
        for lo, hi, sh in (("2008-10-31", "2009-02-28", -0.05), ("2020-03-31", "2020-03-31", -0.12), ("2022-02-28", "2022-09-30", -0.015)):
            r[(DATES >= lo) & (DATES <= hi)] += sh
        cols[f"F{i:02d}"] = 100.0 * np.cumprod(1.0 + r)
    nav = pd.DataFrame(cols, index=DATES)
    natures = ["Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible", "Mixtos", "Renta Variable", "Alternativo"]
    attrs = pd.DataFrame({"Fund_Nature": [natures[i % 6] for i in range(24)], "Management_Company": [f"M{i % 12}" for i in range(24)]},
                         index=nav.columns)
    return nav, attrs


def _table(returns, cash_weight=0.0, turnover=0.14):
    return pd.DataFrame({"net_turnover_1m": returns, "cash_ret_1m": 0.0008, "cash_weight": cash_weight, "turnover": turnover,
                         "target_1m": 0.005}, index=DATES)


def _run(root: Path, name: str, table: pd.DataFrame, nav=None, attrs=None, inputs_hash="same", status="success"):
    d = root / name / "run"
    d.mkdir(parents=True, exist_ok=True)
    table.to_csv(d / "pit_backtest_table.csv", index_label="date")
    if nav is not None:
        (d / "inputs").mkdir(exist_ok=True)
        nav.to_parquet(d / "inputs" / "nav.parquet")
        attrs.to_parquet(d / "inputs" / "attrs.parquet")
    if name != "V0_gate_A":
        (root / name / "variant_status.json").write_text(json.dumps(
            {"variant": name, "status": status, "out_dir": str(d), "inputs_sha256": inputs_hash, "overlay_sha256": "x"}), encoding="utf-8")
    return d


@pytest.fixture
def world(tmp_path):
    rng = np.random.default_rng(11)
    base = rng.normal(0.0035, 0.012, len(DATES))
    nav, attrs = _nav()
    d0 = _run(tmp_path, "V0_gate_A", _table(base), nav, attrs)
    (tmp_path / "gate.json").write_text(json.dumps({"status": "PASS", "v0_inputs_sha256": "same", "v0_run_dir": str(d0), "git": {"commit": "abc"},
                                                    "v0_series_stats_sha256": "h"}), encoding="utf-8")
    return tmp_path, base, nav, attrs


def _report(root):
    return pr.build(root, n_boot_sharpe=300, n_boot_dd=300)


def test_an_identical_variant_passes_every_computable_rule(world):
    root, base, *_ = world
    _run(root, "V1", _table(base))
    data = _report(root)
    r = data["variants"]["V1"]
    assert r["status"] == "evaluated" and r["rules"] == {"R1": True, "R2": True, "R3": True, "R4": True, "R5": None, "R6": True}
    assert data["decision"]["outcome"] == "CANDIDATE" and data["decision"]["variant"] == "V1"
    assert "Rule 5" in data["decision"]["note"]


def test_a_deeper_drawdown_fails_rule_1_and_a_lower_sharpe_fails_rule_2(world):
    root, base, *_ = world
    bad = base.copy()
    bad[(DATES >= "2009-01-31") & (DATES <= "2009-06-30")] -= 0.06          # a 6-month crash: drawdown far beyond 15 %
    _run(root, "V3", _table(bad))
    assert _report(root)["variants"]["V3"]["rules"]["R1"] is False
    _run(root, "V1", _table(base - 0.004))                                    # a lower mean: lower Sharpe and lower return
    r1 = _report(root)["variants"]["V1"]
    assert r1["rules"]["R2"] is False or r1["rules"]["R3"] is False


def test_turnover_rule_4(world):
    root, base, *_ = world
    _run(root, "V1", _table(base, turnover=0.14 + 0.06))
    assert _report(root)["variants"]["V1"]["rules"]["R4"] is False


def test_cash_timing_rule_6_uses_variant_induced_cash_only(world):
    root, base, nav, attrs = world
    d = root / "V0_gate_A" / "run"                                            # V0 holds 8 % cash everywhere
    t = pd.read_csv(d / "pit_backtest_table.csv", index_col=0, parse_dates=True)
    t["cash_weight"] = 0.08
    t.to_csv(d / "pit_backtest_table.csv", index_label="date")
    _run(root, "V1", _table(base, cash_weight=0.08))                          # the same cash induces none: must still pass
    assert _report(root)["variants"]["V1"]["rules"]["R6"] is True
    cw = np.full(len(DATES), 0.08)
    cw[100] = 0.33                                                            # 25 pp of induced cash in one month breaks C2 (<= 20 %)
    _run(root, "V3", _table(base, cash_weight=cw))
    assert _report(root)["variants"]["V3"]["rules"]["R6"] is False


def test_incomplete_and_incomparable_variants_are_not_evaluated(world):
    root, base, *_ = world
    _run(root, "V1", _table(base), status="timeout")
    _run(root, "V3", _table(base), inputs_hash="other")
    data = _report(root)
    assert data["variants"]["V1"]["status"] == "INCOMPLETE" and data["variants"]["V3"]["status"] == "INCOMPARABLE"
    assert data["decision"]["outcome"] == "NOT_EVALUATED"


def test_the_simplest_passing_variant_wins(world):
    root, base, *_ = world
    for n in ("V3", "V1", "V123"):
        _run(root, n, _table(base))
    assert _report(root)["decision"]["variant"] == "V1"


def test_rule_7_fallback_when_v0_itself_breaches_15_percent(tmp_path):
    rng = np.random.default_rng(5)
    base = rng.normal(0.0035, 0.012, len(DATES))
    crash = base.copy()
    crash[(DATES >= "2009-01-31") & (DATES <= "2009-06-30")] -= 0.06           # V0 itself breaches
    nav, attrs = _nav()
    d0 = _run(tmp_path, "V0_gate_A", _table(crash), nav, attrs)
    (tmp_path / "gate.json").write_text(json.dumps({"status": "PASS", "v0_inputs_sha256": "same", "v0_run_dir": str(d0), "git": {}}), encoding="utf-8")
    _run(tmp_path, "V1", _table(crash))                                          # the same breach: fails Rule 1, passes 2-4 and 6
    data = _report(tmp_path)
    assert data["v0_rule1"] is False and data["decision"]["outcome"] == "RULE_7_FALLBACK" and data["decision"]["variant"] == "V1"


def test_stress_reference_episodes_are_variant_independent_and_sensible():
    nav, attrs = _nav()
    ref = pr.stress_reference(nav, attrs["Fund_Nature"])
    eps = {e["episode"]: e for e in pr.episode_windows(ref)}
    assert pd.Timestamp("2008-09-30") <= eps["2008-09..2009-03"]["trough"] <= pd.Timestamp("2009-03-31")
    assert eps["2020-02..2020-03"]["trough"] == pd.Timestamp("2020-03-31")
    for e in eps.values():
        assert e["recovery_end"] >= e["trough"] and e["months"] <= pr.RECOVERY_CAP_MONTHS


def test_recovery_is_capped_and_flagged_censored():
    idx = pd.date_range("2007-01-31", periods=120, freq="ME")
    s = pd.Series(np.r_[np.linspace(100, 100, 24), np.linspace(100, 60, 12), np.linspace(60, 70, 84)], index=idx)   # never regains 100
    e = pr.episode_windows(s, episodes=(("x", "2009-01-01", "2009-12-31"),))[0]
    assert e["censored"] is True and e["months"] == pr.RECOVERY_CAP_MONTHS


def test_v2_eligibility_gates_flag_a_small_pool():
    nav, attrs = _nav()
    q = pr.v2_eligibility(nav, attrs, window=60, first="2012-01-31")
    assert q["shortfall_months"] > 0 and q["data_starved"] is True            # 24 funds over 3 sub-portfolios: fewer than 30 per sub


def test_the_markdown_report_renders_the_pre_registered_sections(world):
    root, base, *_ = world
    _run(root, "V1", _table(base))
    md = pr.render(_report(root))
    for needle in ("## V0 (live configuration)", "## Stress episodes", "## Rules matrix", "### V1", "## Pre-registered outcome", "PENDING"):
        assert needle in md
