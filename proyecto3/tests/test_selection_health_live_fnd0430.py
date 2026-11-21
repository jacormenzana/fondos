"""FND-0430 / P3-1.6 step 2: the LIVE `selection_health` block (proyecto3/src/selection_health.py, wired in scripts/launch/p3_build_portfolio.py).

Stub frames and a fake connection only (R-7): no database, no `pipeline` import. Invariants pinned: (1) the block is REPORT-ONLY and fail-soft, nothing in it can raise out of
`run_live` or change a DRAFT / RC; (2) the kill switch silences it completely; (3) the change monitors compare the current run with the latest PERSISTED run strictly BEFORE today
(a real build has just persisted today's rows, so "latest persisted" would be the run itself); (4) concentration is measured on the CONSTRUCTED portfolio; (5) the thresholds are the
named constant of the calibration of the sealed run; (6) the new SQL is literal; (7) the script calls the block after the build, inside its own fail-soft wrapper."""
import ast
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import selection_health as H  # noqa: E402

SCRIPT = ROOT / "scripts" / "launch" / "p3_build_portfolio.py"
LIMITS = {("Defensiva", "eligible_change"): 0.179, ("Defensiva", "top_turnover"): 0.30, ("Defensiva", "top_manager_max"): 3.0}


def frame(n=30, drop=0, shift=0, sub="Defensiva", reason=None):
    """`n` funds scored 1.00, 0.99, ...; `drop` funds ineligible at the tail; `shift` moves the score ranking by that many places."""
    rows = []
    for k in range(n):
        rows.append({"subportfolio": sub, "isin": f"ES{k:04d}", "fund_nature": ["Monetario", "Mixtos", "Renta Variable"][k % 3],
                     "eligible": k < n - drop, "score_final": (1.0 - 0.01 * ((k + shift) % n)) if k < n - drop else 0.0,
                     "exclusion_reason": None if k < n - drop else (reason or "hard filter")})
    return pd.DataFrame(rows)


def test_a_stable_run_raises_no_alert_and_reports_zero_change():
    rep = H.live_report(frame(), frame(), LIMITS)
    row = rep["monitors"].loc["Defensiva"]
    assert row["eligible"] == 30 and row["eligible_change"] == 0 and row["top_turnover"] == 0
    assert rep["alerts"] == []


def test_a_partial_run_makes_eligible_change_alert():
    """E-7: the current run scored only part of the universe: a third of the eligible set is gone."""
    rep = H.live_report(frame(drop=10), frame(), LIMITS)
    assert [a[:2] for a in rep["alerts"] if a[1] == "eligible_change"] == [("Defensiva", "eligible_change")]
    assert rep["monitors"].loc["Defensiva", "eligible_change"] == pytest.approx(10 / 30)


def test_a_reshuffled_top_makes_top_turnover_alert():
    rep = H.live_report(frame(shift=15), frame(), LIMITS)
    assert any(a[:2] == ("Defensiva", "top_turnover") for a in rep["alerts"])


def test_without_a_previous_run_the_change_monitors_are_unavailable_and_never_alert():
    for prev in (None, pd.DataFrame()):
        rep = H.live_report(frame(), prev, LIMITS)
        assert np.isnan(rep["monitors"].loc["Defensiva", "eligible_change"]) and rep["alerts"] == []
        assert "no previous run" in H.format_block(rep)


def test_raw_top_manager_concentration_uses_the_manager_series():
    mgr = pd.Series({f"ES{k:04d}": "ONE" if k < 5 else f"M{k}" for k in range(30)})
    rep = H.live_report(frame(), frame(), LIMITS, manager=mgr)
    assert rep["monitors"].loc["Defensiva", "top_manager_max"] == 5
    assert ("Defensiva", "top_manager_max", 5.0, 3.0) in rep["alerts"]


def test_concentration_is_measured_on_the_constructed_portfolio():
    funds = [{"subportfolio": "Defensiva", "fund_nature": "Monetario", "gestora": "A"}, {"subportfolio": "Defensiva", "fund_nature": "Monetario", "gestora": "B"},
             {"subportfolio": "Defensiva", "fund_nature": "Mixtos", "gestora": "A"}, {"subportfolio": "Dinamica", "fund_nature": "Mixtos", "gestora": "A"}]
    out = H.constructed_concentration(funds)
    assert out["Defensiva"] == {"funds": 3, "nature_max": 2, "manager_max": 2}
    assert out["Dinamica"] == {"funds": 1, "nature_max": 1, "manager_max": 1}


def test_holds_and_suspicion_flags_are_counted_by_reason_prefix():
    held = frame(n=3, sub="Dinamica").assign(eligible=False, exclusion_reason="data_hold: FND-1 until 2026-12-01 (x)")
    suspect = frame(n=2, sub="Dinamica").assign(eligible=False, isin=["X1", "X2"], exclusion_reason="data_suspect: flagged")
    cur = pd.concat([frame(drop=0), held, suspect], ignore_index=True)
    assert H.exclusion_counts(cur) == {"data_hold": 3, "data_suspect": 2}
    assert H.exclusion_counts(frame()) == {"data_hold": 0, "data_suspect": 0}


def test_nan_weight_by_subportfolio_is_hand_computed():
    """Defensiva weights 0.6 / 0.4: fund A has both, B lacks the 0.4 metric, C lacks both (but is ineligible: not counted)."""
    metrics = pd.DataFrame({"m1": [1.0, 1.0, np.nan], "m2": [1.0, np.nan, np.nan]}, index=["A", "B", "C"])
    cur = pd.DataFrame({"subportfolio": "Defensiva", "isin": ["A", "B", "C"], "eligible": [True, True, False]})
    out = H.nan_weight_by_subportfolio(metrics, cur, {"Defensiva": {"m1": 0.6, "m2": 0.4}})
    assert out["Defensiva"]["funds_with_gap"] == 1 and out["Defensiva"]["mean"] == pytest.approx(0.2)


def test_the_block_is_compact_and_states_it_is_report_only():
    text = H.format_block(H.live_report(frame(drop=10), frame(), LIMITS, holds={"data_hold": 1, "data_suspect": 0}))
    assert "selection_health" in text and "report-only" in text and "ALERT" in text
    assert text.count("\n") < 25


# ---- loaders on a fake connection ------------------------------------------------------------------------------------------

class _Res:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return _Res(self.rows)


def test_previous_run_is_read_strictly_before_today_without_the_regime_filter_and_through_a_runs_join():
    conn = _Conn([("Defensiva", "ES1", 0.5, 1, dt.date(2026, 11, 3)), ("Defensiva", "ES2", 0.0, 0, dt.date(2026, 11, 3))])
    prev = H.load_previous_run(conn, "v1", dt.date(2026, 11, 21))
    sql, params = conn.calls[0]
    assert "as_of_date <" in sql and "regime" in sql and "WITH runs AS" in sql
    assert "regime = %s" not in sql and "MAX(" not in sql.upper()                                  # no regime filter, no correlated MAX (it took > 40 s)
    assert params[0] == "v1" and params[1] == dt.date(2026, 11, 21)
    assert list(prev["eligible"]) == [True, False] and set(prev["subportfolio"]) == {"Defensiva"}
    assert H.load_previous_run(_Conn([]), "v1", dt.date(2026, 11, 21)).empty


def test_managers_are_loaded_for_the_raw_top_only():
    conn = _Conn([("ES0000", "Alpha"), ("ES0001", "Beta")])
    mgr = H.load_managers(conn, ["ES0000", "ES0001"])
    assert mgr.to_dict() == {"ES0000": "Alpha", "ES0001": "Beta"} and H.load_managers(_Conn([]), []).empty


def test_new_sql_is_literal():
    tree = ast.parse((ROOT / "proyecto3" / "src" / "selection_health.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "execute":
            assert isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)


# ---- run_live: kill switch, fail-soft, report-only ------------------------------------------------------------------------

def _portfolio():
    from types import SimpleNamespace
    return SimpleNamespace(all_funds=[{"subportfolio": "Defensiva", "fund_nature": "Mixtos", "gestora": "A"}])


def _loaders(prev=None, boom=None):
    def load_previous(conn, version, before):
        if boom:
            raise boom
        return frame() if prev is None else prev
    return {"load_previous": load_previous, "load_managers": lambda conn, isins: pd.Series(dtype=object), "load_metrics": lambda conn: None}


def test_kill_switch_off_prints_nothing_and_reads_nothing():
    calls = []
    out = H.run_live(None, frame(), _portfolio(), enabled=False, limits=LIMITS, today=dt.date(2026, 11, 21), weights={},
                     load_previous=lambda *a: calls.append(a), load_managers=lambda *a: calls.append(a), load_metrics=lambda *a: calls.append(a))
    assert out is None and calls == []


def test_a_failing_loader_never_raises_and_gives_one_aviso_line():
    out = H.run_live(None, frame(), _portfolio(), enabled=True, limits=LIMITS, today=dt.date(2026, 11, 21), weights={},
                     **_loaders(boom=RuntimeError("db down")))
    assert out.startswith("AVISO: selection_health not run (RuntimeError: db down") and "\n" not in out


def test_run_live_returns_the_block_and_does_not_mutate_its_inputs():
    cur = frame(drop=10)
    before = cur.copy(deep=True)
    out = H.run_live(None, cur, _portfolio(), enabled=True, limits=LIMITS, today=dt.date(2026, 11, 21), weights={}, **_loaders())
    assert "ALERT" in out and "eligible_change" in out
    pd.testing.assert_frame_equal(cur, before)


def test_a_failing_nan_weight_read_degrades_that_line_only():
    loaders = _loaders()
    loaders["load_metrics"] = lambda conn: (_ for _ in ()).throw(RuntimeError("no metrics"))
    out = H.run_live(None, frame(), _portfolio(), enabled=True, limits=LIMITS, today=dt.date(2026, 11, 21), weights={"Defensiva": {"m": 1.0}}, **loaders)
    assert out.startswith("selection_health") and "nan_weight n/a" in out


# ---- wiring and constants ---------------------------------------------------------------------------------------------------

def test_the_script_calls_the_block_after_the_build_inside_a_fail_soft_wrapper():
    src = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = sorted((c.lineno, c.func.id) for c in ast.walk(main) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name))
    order = [name for _, name in calls]
    assert "_selection_health" in order and order.index("_selection_health") > order.index("score_funds")
    assert order.count("_selection_health") == 1
    wrapper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_selection_health")
    tries = [n for n in ast.walk(wrapper) if isinstance(n, ast.Try)]
    assert tries and any(getattr(h.type, "id", "") == "Exception" for t in tries for h in t.handlers)
    assert isinstance(wrapper.returns, ast.Constant) and wrapper.returns.value is None                # `-> None`: it can never become the RC
    assert any(isinstance(n, ast.Attribute) and n.attr == "transaction" for n in ast.walk(wrapper))   # every read runs in its own savepoint
    assert not any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("commit", "rollback") for n in ast.walk(wrapper))   # never touches the build's transaction


def test_constants_carry_the_calibration_of_the_sealed_run():
    import shared.config as C
    assert C.P3_SELECTION_HEALTH_ENABLED is True
    lim = C.P3_SELECTION_HEALTH_LIMITS
    assert lim[("Defensiva", "eligible_change")] == pytest.approx(0.179) and lim[("Dinamica", "eligible_change")] == pytest.approx(0.341)
    assert lim[("Equilibrada", "eligible_change")] == pytest.approx(0.297)
    assert lim[("Defensiva", "top_turnover")] == pytest.approx(0.30) and lim[("Dinamica", "top_turnover")] == pytest.approx(0.40)
    assert lim[("Defensiva", "top_manager_max")] == 3 and lim[("Dinamica", "top_manager_max")] == 3
    assert not any(m in ("top_nature_max",) for (_s, m) in lim) and ("Equilibrada", "top_manager_max") not in lim     # degenerate monitors cannot fire
    assert "20261119_210053" in C.P3_SELECTION_HEALTH_LIMITS_PROVENANCE
