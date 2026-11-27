"""P3-7 forward shadow of the AC arm (band 0.10 + one-off fee penalty, H = 7 months): doc/decisiones/PREREG_P3_7.md and the owner's decision of 2026-11-22.

The stream mirrors the FND-0158 hysteresis shadow (doc/decisiones/DESIGN_FND-0158_hysteresis_shadow.md, section 4). Invariants pinned here:
(1) the live DRAFT is never touched and nothing but one `ingestion_log` row is written; (2) the live flags are never read, the live arm has no incumbents and no penalty;
(3) a failure leaves the build whole and writes one ERROR row; (4) the arm is "the live levers + L4 at H", never another combination; (5) the stream is its own (its chain reads
its own previous row, never the hysteresis rows); (6) the new SQL is literal; (7) the builder seam changes nothing while it is not used.
No database: the glue takes the builder, the connection and the scoring functions as arguments, so fakes drive it."""
import ast
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import fee_penalty_shadow as fp  # noqa: E402
from proyecto3.src import hysteresis_shadow as hs  # noqa: E402
from proyecto3.src import portfolio_builder as pb  # noqa: E402
from proyecto3.src import ranking_levers as RL  # noqa: E402

SRC = ROOT / "proyecto3" / "src" / "fee_penalty_shadow.py"
SCRIPT = ROOT / "scripts" / "launch" / "p3_build_portfolio.py"
CANDIDATE_COLUMNS = ["block", "isin", "score_total", "fund_name", "fund_nature", "management_company", "fund_family_id"]


def _f(isin, sub, w, score=1.0):
    return {"isin": isin, "subportfolio": sub, "master_weight": w, "score": score}


def _pf(sid, funds):
    subs = {}
    for f in funds:
        subs.setdefault(f["subportfolio"], []).append({"isin": f["isin"]})
    return SimpleNamespace(scenario_id=sid, all_funds=funds, sub_portfolios=[SimpleNamespace(name=n, funds=v) for n, v in subs.items()])


PROD = _pf("prod", [_f("A", "Defensiva", 0.5), _f("B", "Defensiva", 0.5)])
OFF = _pf("draft", [_f("A", "Defensiva", 0.5), _f("C", "Defensiva", 0.5, 2.0)])          # the live arm rotated B out and C in
AC = _pf("draft", [_f("A", "Defensiva", 0.5), _f("B", "Defensiva", 0.5)])                # band + penalty kept B
CANDS = pd.DataFrame([["Defensiva", "A", 0.9, "Fund A", "Mixtos", "M1", None]], columns=CANDIDATE_COLUMNS)
INFO = {"n_scored": 10, "n_candidates": 1, "n_fee_unknown": 3}


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
            return _Cur([("A", "M1"), ("B", "M2")])
        raise AssertionError(f"unexpected SQL: {s}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Builder:
    """`load_previous` = PRODUCTION; `build` returns AC and records what it was asked."""

    def __init__(self, production=PROD, ac=AC, raises=None):
        self.production, self.ac, self.raises, self.calls = production, ac, raises, []

    def load_previous(self, exclude_scenario_id=None):
        return self.production

    def build(self, regime, scenario_id, dry_run=False, previous_portfolio=None, candidates=None, **kw):
        self.calls.append({"dry_run": dry_run, "previous": previous_portfolio, "candidates": candidates})
        assert dry_run is True, "the shadow must never persist"
        if self.raises:
            raise self.raises
        return self.ac


class Rescore:
    def __init__(self, cands=CANDS, info=INFO, raises=None):
        self.cands, self.info, self.raises, self.n = cands, info, raises, 0

    def __call__(self):
        self.n += 1
        if self.raises:
            raise self.raises
        return self.cands, dict(self.info)


def _run(conn=None, builder=None, live=OFF, enabled=True, rescore=None, **kw):
    conn, builder, rescore = conn or Conn(), builder or Builder(), rescore or Rescore()
    line = fp.shadow_after_build(conn, builder, SimpleNamespace(regime="Shock_Energetico"), "draft", live, enabled=enabled, band=0.10, h_years=0.58, rescore=rescore, **kw)
    return conn, builder, line


# ---- invariant 4: the arm is the live levers + L4 at H ----------------------------------------------------------------------------------------------------
def test_the_arm_is_the_live_levers_plus_the_penalty_and_nothing_else():
    cfg = fp.shadow_levers(RL.LeverConfig(), 0.58)
    assert cfg == RL.LeverConfig(l4_h_years=0.58)
    assert cfg.l1_q is None and cfg.l2_z0 is None and cfg.l3_k is None
    assert fp.other_levers_on(RL.LeverConfig(l4_h_years=3.0)) is False       # the live L4 (OFF in config) is the one the shadow replaces
    assert fp.other_levers_on(RL.LeverConfig(l2_z0=2.0)) is True


def test_the_constants_are_the_preregistered_ones():
    assert fp.ARM == "AC" and fp.STEP == "P3_FEE_PENALTY_SHADOW" and fp.STEP_ERROR == "P3_FEE_PENALTY_SHADOW_ERROR"
    from shared import config as cfg
    assert cfg.P3_FEE_PENALTY_SHADOW_H_YEARS == 0.58                          # 7 months (PREREG_P3_7 section 4)
    assert cfg.P3_L4_FEE_PENALTY_ENABLED is False                            # the live lever stays OFF: this stream only measures


def test_the_candidate_columns_are_the_live_query_s():
    from proyecto3.src import score_candidates
    assert fp.CANDIDATE_COLUMNS == score_candidates.CANDIDATE_COLUMNS == CANDIDATE_COLUMNS


# ---- the candidates of the penalty arm ----------------------------------------------------------------------------------------------------------------------
def _scores():
    return pd.DataFrame({
        "isin": ["A", "B", "C", "D", "E"], "subportfolio": ["Defensiva", "Defensiva", "Equilibrada", "Equilibrada", "Defensiva"],
        "fund_name": ["a", "b", "c", "d", "e"], "fund_nature": ["Mixtos"] * 5, "fund_family_id": [None, "F1", None, None, None],
        "score_final": [0.5, 0.9, 0.7, 0.0, 0.8], "eligible": [True, True, True, True, False]})


def test_candidates_keep_only_eligible_positive_scores_sorted_like_the_live_query():
    c = fp.candidates_from_scores(_scores(), {"A": "M1", "B": "M2", "C": "M3"})
    assert list(c.columns) == CANDIDATE_COLUMNS
    assert c["isin"].tolist() == ["B", "A", "C"]                              # D has score 0, E is ineligible; (block, score DESC)
    assert c["block"].tolist() == ["Defensiva", "Defensiva", "Equilibrada"]
    assert c["management_company"].tolist() == ["M2", "M1", "M3"] and c["score_total"].tolist() == [0.9, 0.5, 0.7]


def test_candidates_of_nothing_are_an_empty_frame_with_the_columns():
    c = fp.candidates_from_scores(_scores().iloc[0:0], {})
    assert c.empty and list(c.columns) == CANDIDATE_COLUMNS


def test_rescore_calls_the_scorer_with_the_penalty_in_memory_and_counts_unknown_fees():
    seen = {}

    def load(conn, regime=None):
        seen["load"] = regime
        return pd.DataFrame({"x": [1, 2]}, index=["A", "B"])

    def score(df, regime, verbose=True, levers=None, **kw):
        seen["score"] = (regime, verbose, levers)
        return _scores()

    conn = Conn()
    c, info = fp.rescore_candidates(conn, "Shock_Energetico", 0.58, load_metrics=load, score_from_df=score, base_levers=RL.LeverConfig(),
                                    count_unknown_fees=lambda df, h: (None, 7))
    assert seen["load"] == "Shock_Energetico" and seen["score"] == ("Shock_Energetico", False, RL.LeverConfig(l4_h_years=0.58))
    assert info == {"n_scored": 5, "n_candidates": 3, "n_fee_unknown": 7} and len(c) == 3
    assert all("INSERT" not in s.upper() and "UPDATE" not in s.upper() for s in conn.sql)          # only the management-company read


def test_rescore_refuses_when_another_lever_is_on_instead_of_mixing_arms():
    with pytest.raises(fp.ShadowSkip, match="another ranking lever"):
        fp.rescore_candidates(Conn(), "R", 0.58, load_metrics=lambda c, regime=None: pd.DataFrame({"x": [1]}), score_from_df=lambda *a, **k: _scores(),
                              base_levers=RL.LeverConfig(l1_q=0.2))


def test_rescore_of_an_empty_metrics_frame_is_a_skip():
    with pytest.raises(fp.ShadowSkip, match="no metrics"):
        fp.rescore_candidates(Conn(), "R", 0.58, load_metrics=lambda c, regime=None: pd.DataFrame(), score_from_df=lambda *a, **k: _scores(), base_levers=RL.LeverConfig())


def test_the_real_scorer_is_wired_so_that_the_penalty_reaches_the_candidate_scores():
    """End to end with the live scorer function on a synthetic frame: the AC pool differs from the penalty-free pool, equals the scorer run with L4 at H, and the unknown fees are counted."""
    import numpy as np
    sys.path.insert(0, str(ROOT / "proyecto3" / "tests"))
    from _scoring_fixture import make_scoring_frame
    from proyecto3.src import fund_scorer as fs
    frame = make_scoring_frame(n=420, seed=5)
    rng = np.random.default_rng(6)
    frame["fund_family_id"] = None
    frame["return_ann_real"] = rng.normal(0.03, 0.02, len(frame))
    frame["Entry_Fee_Pct"] = rng.choice([0.0, 0.0, 0.02, 0.05, np.nan], len(frame))
    frame["Exit_Fee_Pct"] = rng.choice([0.0, 0.0, 0.01], len(frame))

    class AllFunds(Conn):
        def execute(self, sql, params=()):
            if "FROM fund_master" in " ".join(sql.split()):
                self.sql.append(sql)
                return _Cur([(i, "M") for i in params[0]])
            return super().execute(sql, params)

    conn = AllFunds()
    cands, info = fp.rescore_candidates(conn, "Expansion", 0.58, load_metrics=lambda c, regime=None: frame.copy(), score_from_df=fs.score_funds_from_df,
                                        base_levers=RL.LeverConfig(), count_unknown_fees=RL.l4_penalty)
    expected = fs.score_funds_from_df(frame.copy(), "Expansion", verbose=False, levers=RL.LeverConfig(l4_h_years=0.58))
    plain = fp.candidates_from_scores(fs.score_funds_from_df(frame.copy(), "Expansion", verbose=False, levers=RL.LeverConfig()), {})
    assert not cands.empty and cands["score_total"].round(4).tolist() == fp.candidates_from_scores(expected, {})["score_total"].round(4).tolist()
    assert cands["score_total"].round(4).tolist() != plain["score_total"].round(4).tolist()       # the penalty moved the scores
    assert info["n_fee_unknown"] > 0 and info["n_candidates"] == len(cands) and info["n_scored"] == len(expected)
    assert (cands["management_company"] == "M").all() and not [s for s in conn.sql if "INSERT" in s.upper()]


# ---- the row ------------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_successful_run_appends_exactly_one_row_with_both_streams_and_the_penalty_facts():
    conn, builder, line = _run(identity={"git_commit": "abc", "git_clean": True, "config_hash": "h"})
    rows = [r for r in conn.log if r[1] == fp.STEP]
    assert len(rows) == 1 and rows[0][2] == "SHADOW" and not [r for r in conn.log if r[1] == fp.STEP_ERROR] and len(conn.log) == 1
    row = hs.parse_row(rows[0][3])
    assert row["arm"] == "AC" and row["l4_h_years"] == 0.58 and row["band"] == 0.10 and row["scenario_id"] == "draft" and row["baseline_scenario"] == "prod"
    assert row["n_fee_unknown"] == 3 and row["n_candidates"] == 1 and row["n_scored"] == 10
    assert row["off"]["one_way_turnover"] == 0.5 and row["one_step"]["one_way_turnover"] == 0.0
    assert [r["isin"] for r in row["one_step"]["diff_vs_off"]["kept_by_hysteresis"]] == ["B"]
    assert row["chain_base_scenario"] == "prod" and row["build_identity"] == {"git_commit": "abc", "git_clean": True, "config_hash": "h"}
    assert "1 month(s), 1 row(s)" in line and "withheld until the first review" in line and "%" not in line      # no comparison value (2026-11-27)


def test_both_arms_are_built_in_memory_from_the_penalty_candidates():
    _c, builder, _l = _run()
    assert len(builder.calls) == 2 and all(c["dry_run"] is True for c in builder.calls)
    assert all(c["candidates"] is CANDS for c in builder.calls)               # the same penalty-ranked pool for both streams
    one_step, chain = builder.calls
    assert {f["isin"] for sp in one_step["previous"].sub_portfolios for f in sp.funds} == {"A", "B"}      # PRODUCTION


def test_the_chain_reads_its_own_previous_row_never_the_hysteresis_stream():
    conn, _b, _l = _run()
    first = json.loads(conn.log[0][3])
    first["chain"]["weights"] = [{"isin": "Z", "subportfolio": "Defensiva", "weight": 1.0, "score": 1.0}]
    hyst = json.loads(conn.log[0][3])
    hyst["chain"]["weights"] = [{"isin": "H", "subportfolio": "Defensiva", "weight": 1.0, "score": 1.0}]
    conn2 = Conn(stored=[(1, fp.STEP, "SHADOW", json.dumps(first)), (2, hs.STEP, "SHADOW", json.dumps(hyst))])
    b2 = Builder()
    fp.shadow_after_build(conn2, b2, SimpleNamespace(regime="R"), "draft2", OFF, enabled=True, band=0.10, h_years=0.58, rescore=Rescore())
    assert {f["isin"] for sp in b2.calls[1]["previous"].sub_portfolios for f in sp.funds} == {"Z"}
    assert hs.parse_row(conn2.log[-1][3])["chain_base_scenario"] == "draft"
    assert len(fp.read_rows(conn2)) == 2 and len(hs.read_rows(conn2)) == 1       # each stream reads only its own step


def test_bad_json_in_the_previous_row_restarts_the_chain_from_production():
    conn = Conn(stored=[(1, fp.STEP, "SHADOW", "{not json")])
    b = Builder()
    fp.shadow_after_build(conn, b, SimpleNamespace(regime="R"), "draft", OFF, enabled=True, band=0.10, h_years=0.58, rescore=Rescore())
    assert {f["isin"] for sp in b.calls[1]["previous"].sub_portfolios for f in sp.funds} == {"A", "B"}
    assert len(fp.read_rows(conn)) == 1


# ---- invariant 3: failures ----------------------------------------------------------------------------------------------------------------------------------
def test_no_production_scenario_writes_one_error_row_and_does_not_raise():
    conn, _b, line = _run(builder=Builder(production=None))
    assert [r[1] for r in conn.log] == [fp.STEP_ERROR] and "no PRODUCTION" in conn.log[0][3] and line.startswith("AVISO")


def test_empty_live_build_and_empty_penalty_candidates_are_one_error_row_each():
    conn, _b, _l = _run(live=_pf("draft", []))
    assert [r[1] for r in conn.log] == [fp.STEP_ERROR] and "empty candidates" in conn.log[0][3]
    conn2, _b2, _l2 = _run(rescore=Rescore(cands=CANDS.iloc[0:0]))
    assert [r[1] for r in conn2.log] == [fp.STEP_ERROR] and "no eligible candidates" in conn2.log[0][3]


def test_a_scorer_or_builder_failure_is_one_error_row_not_an_exception():
    conn, _b, line = _run(rescore=Rescore(raises=RuntimeError("scorer blew up")))
    assert [r[1] for r in conn.log] == [fp.STEP_ERROR] and "scorer blew up" in conn.log[0][3] and conn.rollbacks >= 1 and "skipped" in line
    conn2, _b2, _l2 = _run(builder=Builder(raises=RuntimeError("selection blew up")))
    assert [r[1] for r in conn2.log] == [fp.STEP_ERROR] and "selection blew up" in conn2.log[0][3]


def test_if_even_the_error_row_cannot_be_written_the_hook_still_returns():
    conn, _b, line = _run(conn=Conn(fail_insert=True))
    assert conn.log == [] and line.startswith("AVISO") and "could not be written" in line


# ---- invariants 1 and 2 -------------------------------------------------------------------------------------------------------------------------------------
def test_the_live_portfolio_is_not_modified_and_the_only_write_is_the_log_row():
    live = copy.deepcopy(OFF)
    conn, builder, _ = _run(live=live)
    assert live.all_funds == OFF.all_funds and live.scenario_id == OFF.scenario_id
    touched = " ".join(" ".join(s.split()).upper() for s in conn.sql)
    assert "PORTFOLIO_" not in touched and "FUND_SCORES" not in touched
    writes = [s for s in conn.sql if " ".join(s.split()).upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert len(writes) == 1 and "ingestion_log" in writes[0]


def test_the_switch_off_does_nothing_at_all():
    rescore = Rescore()
    conn, builder, line = _run(enabled=False, rescore=rescore)
    assert line is None and conn.sql == [] and builder.calls == [] and rescore.n == 0


def test_the_module_never_reads_a_live_flag_and_never_persists_scores():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"PORTFOLIO_HYSTERESIS_ENABLED", "P3_L4_FEE_PENALTY_ENABLED", "_persist_scores", "_persist", "score_funds"}
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    forbidden = ("shared", "pipeline", "core", "proyecto3.src.portfolio_builder", "proyecto3.src.fund_scorer")
    assert not [n for n in imports if (getattr(n, "module", "") or "").startswith(forbidden)]          # R-7: the scorer and the builder are arguments


def test_every_statement_of_the_module_is_a_literal_with_placeholders():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    sql = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "execute"]
    assert sql and all(c.args and isinstance(c.args[0], ast.Constant) and isinstance(c.args[0].value, str) for c in sql)


# ---- the hook ---------------------------------------------------------------------------------------------------------------------------------------------------
def test_the_hook_runs_after_the_draft_is_persisted_in_real_runs_only_and_is_fail_soft():
    text = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(text)
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    src = text.splitlines()
    build_line = next(n.lineno for n in ast.walk(main) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "build")
    hook = next(n for n in ast.walk(main) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_fee_penalty_shadow")
    assert hook.lineno > build_line
    guard = max((n for n in ast.walk(main) if isinstance(n, ast.If) and any(c is hook for c in ast.walk(n))), key=lambda n: n.lineno)
    assert "not dry_run" in src[guard.lineno - 1]
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_fee_penalty_shadow")
    last = fn.body[-1]
    assert isinstance(last, ast.Try) and any(h.type is not None and getattr(h.type, "id", "") == "Exception" for h in last.handlers)
    assert "P3_FEE_PENALTY_SHADOW_ENABLED" in ast.get_source_segment(text, fn) and "P3_FEE_PENALTY_SHADOW_H_YEARS" in ast.get_source_segment(text, fn)


def test_the_summary_line_of_no_rows_and_of_many():
    assert fp.summary_line([]) == "fee-penalty shadow: 0 months accumulated"
    conn, _b, _l = _run()
    hidden = fp.summary_line(fp.read_rows(conn))
    assert hidden.startswith("fee-penalty shadow (AC: band 0.10 + penalty H 0.58 y, live arm OFF): 1 month(s), 1 row(s)")
    for field in ("turnover", "cost drag", "unknown fee", "%"):                   # PREREG_P3_7 section 13: not displayed before the first review
        assert field not in hidden, field
    shown = fp.summary_line(fp.read_rows(conn), show_comparisons=True)
    assert "OFF 50.0% / AC 0.0%" in shown and "cost drag" in shown


# ---- invariant 7: the builder seam -------------------------------------------------------------------------------------------------------------------------
def _frame():
    return pd.DataFrame([
        ["Defensiva", f"D{i}", 1.0 - i * 0.01, f"d{i}", "Mixtos", f"M{i}", None] for i in range(6)
    ] + [["Equilibrada", f"E{i}", 0.8 - i * 0.01, f"e{i}", "Mixtos", f"N{i}", None] for i in range(6)], columns=CANDIDATE_COLUMNS)


def test_the_builder_takes_an_explicit_candidate_frame_and_the_default_path_is_unchanged(monkeypatch):
    frame = _frame()
    calls = []
    monkeypatch.setattr(pb, "flag_fx_exposed", lambda conn, c: c)

    def live_query(conn, version, regime, sub):
        calls.append((version, regime, sub))
        return frame[frame["block"] == sub].reset_index(drop=True)

    monkeypatch.setattr(pb, "load_current_candidates", live_query)
    via_db = pb._select_funds_for_subportfolio(None, "Defensiva", "v1", "R")
    assert calls == [("v1", "R", "Defensiva")]                                          # default: the database query, exactly as before
    via_frame = pb._select_funds_for_subportfolio(None, "Defensiva", "v1", "R", candidates=frame)
    assert calls == [("v1", "R", "Defensiva")]                                          # an explicit frame never touches the query
    assert via_frame["isin"].tolist() == via_db["isin"].tolist() and via_frame["isin"].str.startswith("D").all()      # and only its own block's rows count


def test_an_explicit_frame_without_the_block_selects_nothing(monkeypatch):
    monkeypatch.setattr(pb, "flag_fx_exposed", lambda conn, c: c)
    monkeypatch.setattr(pb, "load_current_candidates", lambda *a: (_ for _ in ()).throw(AssertionError("the query must not run")))
    assert pb._select_funds_for_subportfolio(None, "Dinamica", "v1", "R", candidates=_frame()).empty


def test_build_threads_the_frame_to_every_subportfolio_and_defaults_to_none():
    tree = ast.parse((ROOT / "proyecto3" / "src" / "portfolio_builder.py").read_text(encoding="utf-8"))
    build = next(n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "PortfolioBuilder" for n in c.body if isinstance(n, ast.FunctionDef) and n.name == "build")
    args = {a.arg: d for a, d in zip(build.args.args[-len(build.args.defaults):], build.args.defaults)}
    assert "candidates" in args and isinstance(args["candidates"], ast.Constant) and args["candidates"].value is None
    call = next(n for n in ast.walk(build) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_select_funds_for_subportfolio")
    assert any(k.arg == "candidates" and getattr(k.value, "id", "") == "candidates" for k in call.keywords)


def test_hysteresis_shadow_io_helpers_take_a_step_and_default_to_their_own():
    conn, _b, _l = _run()
    row = conn.log[0][3]
    other = Conn(stored=[(1, "OTHER_STEP", "SHADOW", row)])
    assert hs.read_rows(other) == [] and hs._last_row(other) is None                    # the default is the hysteresis stream's own step
    assert len(hs.read_rows(other, step="OTHER_STEP")) == 1 and hs._last_row(other, step="OTHER_STEP")["arm"] == "AC"
    hs._write(other, "OTHER_STEP", "SHADOW", row)
    assert [r[1] for r in other.log] == ["OTHER_STEP", "OTHER_STEP"]
