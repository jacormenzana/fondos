"""FND-0445: the baseline shadow stream `P3_BASELINE_SHADOW` (doc/backlog/PKG_M_ac_adoption.md section 2).

Arm L = the live scorer with the levers of L (L4 off) and the builder at band 0, recorded at every build so R24, R36 and the revert trigger keep a baseline once AC is live.
Invariants pinned: (1) the live DRAFT is never touched and only `ingestion_log` rows are written; (2) the arm gets NO incumbents, even with the live hysteresis switch ON, and the L4 penalty is
off even with the live lever ON; (3) the chain reads its own previous row; the first one starts from the last DRAFT before adoption (the `off` of the latest AC row of an earlier build);
(4) while the live arm IS L, the baseline must equal the live DRAFT exactly and a mismatch writes one ERROR row; ties are ordered as the live query ordered them; after adoption nothing is compared;
(5) every row records the live flags of its build; (6) a failure leaves the build whole and writes one ERROR row; (7) the switch ships OFF.
No database (R-7): the glue takes the builder, the connection and the scoring functions as arguments."""
import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import baseline_shadow as bl  # noqa: E402
from proyecto3.src import fee_penalty_shadow as fp  # noqa: E402
from proyecto3.src import hysteresis_shadow as hs  # noqa: E402
from proyecto3.src import portfolio_builder as pb  # noqa: E402
from proyecto3.src import ranking_levers as RL  # noqa: E402

SRC = ROOT / "proyecto3" / "src" / "baseline_shadow.py"
SCRIPT = ROOT / "scripts" / "launch" / "p3_build_portfolio.py"
COLS = ["block", "isin", "score_total", "fund_name", "fund_nature", "management_company", "fund_family_id"]

L_FLAGS = {"PORTFOLIO_HYSTERESIS_ENABLED": False, "P3_L4_FEE_PENALTY_ENABLED": False, "P3_L4_H_YEARS": 3.0}
AC_FLAGS = {"PORTFOLIO_HYSTERESIS_ENABLED": True, "P3_L4_FEE_PENALTY_ENABLED": True, "P3_L4_H_YEARS": 0.58}
B_FLAGS = {"PORTFOLIO_HYSTERESIS_ENABLED": True, "P3_L4_FEE_PENALTY_ENABLED": False, "P3_L4_H_YEARS": 3.0}


def _f(isin, sub, w, score=1.0):
    return {"isin": isin, "subportfolio": sub, "master_weight": w, "score": score}


def _pf(sid, funds):
    subs = {}
    for f in funds:
        subs.setdefault(f["subportfolio"], []).append({"isin": f["isin"]})
    return SimpleNamespace(scenario_id=sid, all_funds=funds, sub_portfolios=[SimpleNamespace(name=n, funds=v) for n, v in subs.items()])


PROD = _pf("prod", [_f("A", "Defensiva", 0.5), _f("B", "Defensiva", 0.5)])
DRAFT = _pf("draft", [_f("A", "Defensiva", 0.5, 0.9), _f("C", "Defensiva", 0.5, 0.8)])
SAME = _pf("draft", [_f("A", "Defensiva", 0.5, 0.9), _f("C", "Defensiva", 0.5, 0.8)])
OTHER = _pf("draft", [_f("A", "Defensiva", 0.5, 0.9), _f("D", "Defensiva", 0.5, 0.8)])
CANDS = pd.DataFrame([["Defensiva", "A", 0.9, "Fund A", "Mixtos", "M1", None], ["Defensiva", "C", 0.8, "Fund C", "Mixtos", "M2", None]], columns=COLS)
INFO = {"n_scored": 10, "n_candidates": 2}


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Conn:
    """Records every statement; keeps the ingestion_log rows it is given and answers the reads from them."""

    def __init__(self, stored=None, fail_insert=False):
        self.sql, self.log, self.fail_insert, self.commits, self.rollbacks = [], list(stored or []), fail_insert, 0, 0

    def execute(self, sql, params=()):
        self.sql.append(sql)
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO ingestion_log"):
            if self.fail_insert:
                raise RuntimeError("insert refused")
            self.log.append((len(self.log) + 1, params[0], params[1], params[2]))
            return _Cur([])
        if s.startswith("SELECT message FROM ingestion_log"):
            return _Cur([(m,) for (_i, step, _st, m) in self.log if step == params[0]][-1:])
        if s.startswith("SELECT id, message FROM ingestion_log"):
            return _Cur(list(reversed([(i, m) for (i, step, _st, m) in self.log if step == params[0]])))
        if "FROM fund_metrics" in s:
            return _Cur([("20261116", 2)])
        if "FROM fund_master" in s:
            return _Cur([(i, "M") for i in params[0]])
        raise AssertionError(f"unexpected SQL: {s}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Builder:
    def __init__(self, production=PROD, built=SAME, raises=None):
        self.production, self.built, self.raises, self.calls = production, built, raises, []

    def load_previous(self, exclude_scenario_id=None):
        return self.production

    def build(self, regime, scenario_id, dry_run=False, previous_portfolio=None, candidates=None, **kw):
        self.calls.append({"dry_run": dry_run, "previous": previous_portfolio, "candidates": candidates})
        assert dry_run is True, "the shadow must never persist"
        if self.raises:
            raise self.raises
        return self.built


def _rescore(cands=CANDS, info=INFO):
    return lambda: (cands, dict(info))


def _run(conn=None, builder=None, live=DRAFT, enabled=True, flags=L_FLAGS, rescore=None, sid="draft", **kw):
    conn, builder = conn or Conn(), builder or Builder()
    line = bl.shadow_after_build(conn, builder, SimpleNamespace(regime="Shock_Energetico"), sid, live, enabled=enabled, live_flags=flags, rescore=rescore or _rescore(), **kw)
    return conn, builder, line


def _stored(step, scenario, off=None, chain=None):
    row = {"v": 1, "scenario_id": scenario, "off": {"weights": off or []}, "chain": {"weights": chain or []}}
    return (1, step, "SHADOW", json.dumps(row))


def _rows_of(conn, step):
    return [r for r in conn.log if r[1] == step]


def _ids(portfolio):
    return {f["isin"] for sp in portfolio.sub_portfolios for f in sp.funds}


# ---- the arm ------------------------------------------------------------------------------------------------------------------------------------------------------
def test_the_constants_and_the_switch_ships_off():
    from shared import config as cfg
    assert bl.ARM == "L" and bl.STEP == "P3_BASELINE_SHADOW" and bl.STEP_ERROR == "P3_BASELINE_SHADOW_ERROR"
    assert cfg.P3_BASELINE_SHADOW_ENABLED is False


def test_the_arm_switches_the_penalty_off_and_keeps_the_other_levers():
    assert bl.baseline_levers(RL.LeverConfig(l4_h_years=0.58)) == RL.LeverConfig()
    assert bl.baseline_levers(RL.LeverConfig()) == RL.LeverConfig()


def test_rescore_calls_the_scorer_without_the_penalty_even_when_the_live_lever_is_on():
    seen = {}

    def score(df, regime, verbose=True, levers=None, **kw):
        seen["levers"] = levers
        return pd.DataFrame({"isin": ["A"], "subportfolio": ["Defensiva"], "fund_name": ["a"], "fund_nature": ["Mixtos"], "fund_family_id": [None], "score_final": [0.5], "eligible": [True]})

    conn = Conn()
    c, info = bl.rescore_candidates(conn, "R", load_metrics=lambda c, regime=None: pd.DataFrame({"x": [1]}), score_from_df=score, base_levers=RL.LeverConfig(l4_h_years=0.58))
    assert seen["levers"] == RL.LeverConfig() and list(c.columns) == COLS and info == {"n_scored": 1, "n_candidates": 1}
    assert not [s for s in conn.sql if "INSERT" in s.upper()]


def test_rescore_refuses_when_another_lever_is_on():
    with pytest.raises(bl.ShadowSkip, match="another ranking lever"):
        bl.rescore_candidates(Conn(), "R", load_metrics=lambda c, regime=None: pd.DataFrame({"x": [1]}), score_from_df=lambda *a, **k: None, base_levers=RL.LeverConfig(l2_z0=2.0))


def test_the_baseline_is_built_once_in_memory_with_no_incumbents():
    _c, builder, _l = _run()
    assert len(builder.calls) == 1
    call = builder.calls[0]
    assert call["dry_run"] is True and call["previous"] is not None and _ids(call["previous"]) == set()      # not None: build() would load PRODUCTION when hysteresis is ON


def test_the_real_builder_gives_no_incumbents_to_the_baseline_even_with_hysteresis_on(monkeypatch):
    seen = []
    monkeypatch.setattr(pb, "PORTFOLIO_HYSTERESIS_ENABLED", True)
    monkeypatch.setattr(pb, "ROTATION_COST_GATE_ENABLED", False)
    monkeypatch.setattr(pb, "_duplicate_twins", lambda *a: {})
    monkeypatch.setattr(pb, "_select_funds_for_subportfolio", lambda *a, **k: (seen.append(k["incumbent_isins"]), pd.DataFrame())[1])
    builder = pb.PortfolioBuilder(Conn())
    monkeypatch.setattr(builder, "load_previous", lambda **k: pytest.fail("the baseline must never load incumbents"))
    regime = SimpleNamespace(regime="R", weights={"Defensiva": 1.0}, oil_yoy=0, ipc_yoy_avg=0, cli_eu=100, rate_deposit=2, spread_hy=0, vix_yoy=0)
    builder.build(regime, scenario_id="s", dry_run=True, previous_portfolio=bl.no_incumbents(), candidates=CANDS)
    assert seen == [frozenset()]


# ---- the row ------------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_successful_run_appends_exactly_one_row_with_the_live_flags():
    conn, _b, line = _run(identity={"git_commit": "abc"})
    assert len(conn.log) == 1 and conn.log[0][1] == bl.STEP and conn.log[0][2] == "SHADOW"
    row = hs.parse_row(conn.log[0][3])
    assert row["arm"] == "L" and row["band"] == 0.0 and row["scenario_id"] == "draft" and row["baseline_scenario"] == "prod"
    assert row["live_flags"] == L_FLAGS and row["identity_check"] == bl.MATCH and row["n_candidates"] == 2 and row["n_scored"] == 10
    assert row["build_identity"] == {"git_commit": "abc"}
    assert row["one_step"]["weights"] == row["chain"]["weights"] and row["one_step"]["weights"] == hs.fund_rows(SAME.all_funds)
    assert "1 month(s), 1 row(s)" in line and "identity check MATCH" in line and "%" not in line and "turnover" not in line


def test_one_step_turnover_is_against_production_and_chain_against_the_previous_baseline():
    conn = Conn(stored=[_stored(bl.STEP, "old", chain=hs.fund_rows(SAME.all_funds))])
    _run(conn=conn)
    row = hs.parse_row(conn.log[-1][3])
    assert row["one_step"]["one_way_turnover"] == 0.5 and row["chain"]["one_way_turnover"] == 0.0 and row["chain_base_scenario"] == "old"


def test_the_first_chain_row_starts_from_the_draft_of_the_latest_earlier_ac_row():
    earlier = hs.fund_rows(SAME.all_funds)
    stored = [_stored(fp.STEP, "draft_before", off=earlier, chain=[{"isin": "Z", "subportfolio": "Defensiva", "weight": 1.0}]),
              _stored(fp.STEP, "draft", off=[{"isin": "Y", "subportfolio": "Defensiva", "weight": 1.0}])]       # this build's own AC row: not "before"
    conn = Conn(stored=stored)
    _run(conn=conn)
    row = hs.parse_row(conn.log[-1][3])
    assert row["chain_base_scenario"] == "draft_before" and row["chain"]["one_way_turnover"] == 0.0


def test_without_any_earlier_row_the_chain_starts_from_production():
    conn, _b, _l = _run()
    assert hs.parse_row(conn.log[0][3])["chain_base_scenario"] == "prod"


# ---- the identity check -------------------------------------------------------------------------------------------------------------------------------------------
def test_a_mismatch_with_the_live_draft_writes_the_row_and_one_error_row():
    conn, _b, line = _run(builder=Builder(built=OTHER))
    assert [r[1] for r in conn.log] == [bl.STEP, bl.STEP_ERROR] and conn.log[1][2] == "ERROR"
    assert hs.parse_row(conn.log[0][3])["identity_check"] == bl.MISMATCH
    err = json.loads(conn.log[1][3])
    assert err["scenario_id"] == "draft" and "identity" in err["reason"] and err["only_live"] == ["Defensiva:C"] and err["only_baseline"] == ["Defensiva:D"]
    assert "MISMATCH" in line


@pytest.mark.parametrize("funds", [
    [_f("A", "Defensiva", 0.6, 0.9), _f("C", "Defensiva", 0.4, 0.8)],       # same funds, other weights
    [_f("A", "Defensiva", 0.5, 0.9), _f("C", "Defensiva", 0.5, 0.7)],       # same weights, another score
])
def test_a_weight_or_score_difference_is_a_mismatch(funds):
    conn, _b, _l = _run(builder=Builder(built=_pf("draft", funds)))
    assert [r[1] for r in conn.log] == [bl.STEP, bl.STEP_ERROR] and json.loads(conn.log[1][3])["changed"]


def test_after_adoption_nothing_is_compared():
    for flags in (AC_FLAGS, B_FLAGS):
        conn, _b, line = _run(builder=Builder(built=OTHER), flags=flags, live_order=lambda: pytest.fail("the live pool is AC's after adoption"))
        assert [r[1] for r in conn.log] == [bl.STEP]
        assert hs.parse_row(conn.log[0][3])["identity_check"] == bl.NOT_APPLICABLE and "adopted" in line


def test_ties_follow_the_live_query_order_and_other_ranks_are_untouched():
    mem = pd.DataFrame([["D", "A", 0.9], ["D", "B", 0.9], ["D", "C", 0.8], ["D", "E", 0.9], ["Q", "B", 0.5], ["Q", "A", 0.5]], columns=COLS[:3])
    live = pd.DataFrame([["D", "B", 0.9], ["D", "A", 0.9], ["D", "C", 0.8], ["Q", "A", 0.5], ["Q", "B", 0.5]], columns=COLS[:3])
    out = bl.align_ties(mem, live)
    assert out["isin"].tolist() == ["B", "A", "E", "C", "A", "B"]          # E is unknown to the live pool: after the known ties, never above a higher score
    assert list(out.columns) == list(mem.columns) and len(out) == len(mem)


def test_the_live_order_is_applied_while_the_live_arm_is_l():
    tied = pd.DataFrame([["Defensiva", "A", 0.9, "a", "Mixtos", "M1", None], ["Defensiva", "C", 0.9, "c", "Mixtos", "M2", None]], columns=COLS)
    _c, builder, _l = _run(rescore=_rescore(tied), live_order=lambda: tied.iloc[::-1])
    assert builder.calls[0]["candidates"]["isin"].tolist() == ["C", "A"]


# ---- failures and the switch --------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kw, text", [
    (dict(builder=Builder(production=None)), "no PRODUCTION"),
    (dict(live=_pf("draft", [])), "empty candidates"),
    (dict(rescore=_rescore(CANDS.iloc[0:0])), "no eligible candidates"),
    (dict(builder=Builder(raises=RuntimeError("selection blew up"))), "selection blew up"),
])
def test_a_failure_is_one_error_row_not_an_exception(kw, text):
    conn, _b, line = _run(**kw)
    assert [r[1] for r in conn.log] == [bl.STEP_ERROR] and text in conn.log[0][3] and line.startswith("AVISO")


def test_if_even_the_error_row_cannot_be_written_the_hook_still_returns():
    conn, _b, line = _run(conn=Conn(fail_insert=True))
    assert conn.log == [] and line.startswith("AVISO") and "could not be written" in line


def test_the_switch_off_does_nothing_at_all():
    conn, builder, line = _run(enabled=False, rescore=lambda: pytest.fail("not rescored"))
    assert line is None and conn.sql == [] and builder.calls == []


def test_the_only_writes_are_log_rows():
    conn, _b, _l = _run()
    writes = [s for s in conn.sql if " ".join(s.split()).upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert len(writes) == 1 and "ingestion_log" in writes[0]


def test_the_module_is_pure_and_its_sql_literal():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    forbidden = ("shared", "pipeline", "core", "proyecto3.src.portfolio_builder", "proyecto3.src.fund_scorer")
    assert not [n for n in imports if (getattr(n, "module", "") or "").startswith(forbidden)]          # R-7
    sql = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "execute"]
    assert all(c.args and isinstance(c.args[0], ast.Constant) for c in sql)


# ---- the hook -----------------------------------------------------------------------------------------------------------------------------------------------------
def test_the_hook_runs_last_in_real_builds_only_and_is_fail_soft():
    text = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(text)
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = {getattr(n.func, "id", ""): n.lineno for n in ast.walk(main) if isinstance(n, ast.Call)}
    assert calls["_baseline_shadow"] > calls["_fee_penalty_shadow"]          # the first chain row reads the AC row of an EARLIER build; this one's is skipped by id
    guard = next(n for n in ast.walk(main) if isinstance(n, ast.If) and "_baseline_shadow" in ast.get_source_segment(text, n) and "dry_run" in ast.get_source_segment(text, n.test))
    assert "not dry_run" in ast.get_source_segment(text, guard.test)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_baseline_shadow")
    assert isinstance(fn.body[-1], ast.Try) and any(getattr(h.type, "id", "") == "Exception" for h in fn.body[-1].handlers)
    seg = ast.get_source_segment(text, fn)
    for name in ("P3_BASELINE_SHADOW_ENABLED", "PORTFOLIO_HYSTERESIS_ENABLED", "P3_L4_FEE_PENALTY_ENABLED", "P3_L4_H_YEARS", "load_current_candidates"):
        assert name in seg, name


def test_the_summary_line_of_no_rows():
    assert bl.summary_line([]) == "baseline shadow: 0 months accumulated"
