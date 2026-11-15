"""FND-0158: the monthly hysteresis SHADOW (doc/decisiones/DESIGN_FND-0158_hysteresis_shadow.md, section 4). No database, no `pipeline` import: the module is pure at its
top and its glue takes the builder and the connection as arguments, so fakes drive it.

Invariants pinned (numbers in the design): (1) the live DRAFT is never touched; (2) the live flag is never read and the live arm has no incumbents; (3) a failure
leaves the build whole and writes one ERROR row; (4) turnover and diff are symmetric where they must be, with a hand-computed case; (5) the new SQL is literal."""
import ast
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "proyecto3" / "src"))
sys.path.insert(0, str(ROOT))
import hysteresis_shadow as hs  # noqa: E402

SRC = ROOT / "proyecto3" / "src" / "hysteresis_shadow.py"
SCRIPT = ROOT / "scripts" / "launch" / "p3_build_portfolio.py"


def _f(isin, sub, w, score=1.0):
    return {"isin": isin, "subportfolio": sub, "master_weight": w, "score": score}


def _pf(sid, funds):
    """What the builder reads from a Portfolio: `all_funds` (master weights) and `sub_portfolios[].name / .funds[]["isin"]` (the incumbents)."""
    subs = {}
    for f in funds:
        subs.setdefault(f["subportfolio"], []).append({"isin": f["isin"]})
    return SimpleNamespace(scenario_id=sid, all_funds=funds, sub_portfolios=[SimpleNamespace(name=n, funds=v) for n, v in subs.items()])


PROD = _pf("prod", [_f("A", "Defensiva", 0.5), _f("B", "Defensiva", 0.5)])
OFF = _pf("draft", [_f("A", "Defensiva", 0.5), _f("C", "Defensiva", 0.5, 2.0)])      # OFF rotated B out, C in
ON = _pf("draft", [_f("A", "Defensiva", 0.5), _f("B", "Defensiva", 0.5)])            # hysteresis kept B


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Conn:
    """Records every statement; stores the ingestion_log rows it is given and answers the reads from them."""

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
            rows = [(m,) for (_i, step, _st, m) in self.log if step == params[0]]
            return _Cur(rows[-1:])
        if s.startswith("SELECT id, message FROM ingestion_log"):
            rows = [(i, m) for (i, step, _st, m) in self.log if step == params[0]]
            return _Cur(list(reversed(rows)))
        if "FROM fund_metrics" in s:
            return _Cur([("20261112", 2)])
        raise AssertionError(f"unexpected SQL: {s}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Builder:
    """`load_previous` = PRODUCTION; `build` returns ON when it is given incumbents and records what it was asked."""

    def __init__(self, production=PROD, on=ON, raises=None):
        self.production, self.on, self.raises, self.calls = production, on, raises, []

    def load_previous(self, exclude_scenario_id=None):
        return self.production

    def build(self, regime, scenario_id, dry_run=False, previous_portfolio=None, **kw):
        self.calls.append({"dry_run": dry_run, "previous": previous_portfolio})
        assert dry_run is True, "the shadow must never persist"
        if self.raises:
            raise self.raises
        return self.on


def _run(conn=None, builder=None, live=OFF, enabled=True, **kw):
    conn = conn or Conn()
    builder = builder or Builder()
    line = hs.shadow_after_build(conn, builder, object(), "draft", live, enabled=enabled, band=0.10, **kw)
    return conn, builder, line


# ---- invariant 4: numbers -------------------------------------------------------------------------------------------------------------------------
def test_traded_notional_and_turnover_hand_computed():
    base, new = {"A": 0.5, "B": 0.5}, {"A": 0.5, "C": 0.5}
    assert hs.traded_notional(new, base) == 1.0                 # B out 0.5, C in 0.5
    assert hs.one_way_turnover(new, base) == 0.5
    assert abs(hs.cost_drag(1.0) - 0.0025) < 1e-12              # 25 bp on the traded notional (PIT convention)


def test_two_equal_portfolios_have_zero_turnover_and_an_empty_diff():
    rows = hs.fund_rows(PROD.all_funds)
    assert hs.traded_notional(hs.weights_of(rows), hs.weights_of(rows)) == 0.0
    d = hs.selection_diff(rows, rows, {"A", "B"})
    assert d == {"only_off": [], "only_on": [], "kept_by_hysteresis": []}


def test_the_diff_names_what_hysteresis_kept_and_what_it_kept_out():
    off, on = hs.fund_rows(OFF.all_funds), hs.fund_rows(ON.all_funds)
    d = hs.selection_diff(off, on, {"A", "B"})
    assert [r["isin"] for r in d["only_off"]] == ["C"] and d["only_off"][0]["score"] == 2.0
    assert [r["isin"] for r in d["only_on"]] == ["B"]
    assert [r["isin"] for r in d["kept_by_hysteresis"]] == ["B"]        # B is an incumbent that OFF would have rotated out
    assert hs.selection_diff(off, on, set())["kept_by_hysteresis"] == []


def test_a_fund_in_two_subportfolios_adds_up():
    assert hs.weights_of([{"isin": "A", "weight": 0.1}, {"isin": "A", "weight": 0.2}]) == {"A": 0.30000000000000004}


def test_the_rotation_cost_estimate_pairs_exits_and_entries_inside_a_subportfolio():
    pairs = hs.pair_rotations([{"isin": "B", "subportfolio": "D", "weight": 0.5}, {"isin": "X", "subportfolio": "E", "weight": 0.1}],
                              [{"isin": "C", "subportfolio": "D", "weight": 0.5}])
    assert pairs == [("B", "C", 0.5)]                                    # X has no entry in its sub-portfolio: not priced
    m = hs.stream_metrics(hs.fund_rows(OFF.all_funds), hs.fund_rows(PROD.all_funds), hs.fund_rows(OFF.all_funds), {"A", "B"}, cost_fn=lambda o, i: 0.02)
    assert m["rotation_cost_estimate"] == 0.01 and m["funds_in"] == ["C"] and m["funds_out"] == ["B"]
    assert hs.stream_metrics(hs.fund_rows(OFF.all_funds), hs.fund_rows(PROD.all_funds), [], set(), cost_fn=lambda o, i: 1 / 0)["rotation_cost_estimate"] is None


# ---- the row ----------------------------------------------------------------------------------------------------------------------------------------
def test_a_successful_run_appends_exactly_one_row_with_both_streams():
    conn, builder, line = _run(identity={"git_commit": "abc", "git_clean": True, "config_hash": "h"})
    shadow_rows = [r for r in conn.log if r[1] == hs.STEP]
    assert len(shadow_rows) == 1 and shadow_rows[0][2] == "SHADOW" and not [r for r in conn.log if r[1] == hs.STEP_ERROR]
    row = hs.parse_row(shadow_rows[0][3])
    assert row["scenario_id"] == "draft" and row["band"] == 0.10 and row["baseline_scenario"] == "prod" and row["calc_versions"] == {"20261112": 2}
    assert row["off"]["one_way_turnover"] == 0.5 and row["one_step"]["one_way_turnover"] == 0.0 and row["one_step"]["funds_out"] == []
    assert [r["isin"] for r in row["one_step"]["diff_vs_off"]["kept_by_hysteresis"]] == ["B"]
    assert row["build_identity"] == {"git_commit": "abc", "git_clean": True, "config_hash": "h"}      # recorded as given, never compared
    assert row["chain_base_scenario"] == "prod"                  # the first month starts from PRODUCTION
    assert "1 month(s)" in line and "OFF 50.0% / ON 0.0%" in line


def test_the_chain_starts_from_the_previous_rows_on_portfolio():
    conn, builder, _ = _run()
    first = json.loads(conn.log[0][3])
    first["chain"]["weights"] = [{"isin": "Z", "subportfolio": "Defensiva", "weight": 1.0, "score": 1.0}]       # last month's ON portfolio held only Z
    conn2 = Conn(stored=[(1, hs.STEP, "SHADOW", json.dumps(first))])
    b2 = Builder()
    hs.shadow_after_build(conn2, b2, object(), "draft2", OFF, enabled=True, band=0.10)
    one_step_call, chain_call = b2.calls
    assert {f["isin"] for sp in one_step_call["previous"].sub_portfolios for f in sp.funds} == {"A", "B"}      # PRODUCTION
    assert {f["isin"] for sp in chain_call["previous"].sub_portfolios for f in sp.funds} == {"Z"}              # its own history
    assert hs.parse_row(conn2.log[-1][3])["chain_base_scenario"] == "draft"


def test_bad_json_in_the_previous_row_restarts_the_chain_from_production():
    conn = Conn(stored=[(1, hs.STEP, "SHADOW", "{not json")])
    b = Builder()
    line = hs.shadow_after_build(conn, b, object(), "draft", OFF, enabled=True, band=0.10)
    assert {f["isin"] for sp in b.calls[1]["previous"].sub_portfolios for f in sp.funds} == {"A", "B"}
    assert [r[1] for r in conn.log] == [hs.STEP, hs.STEP] and line.startswith("hysteresis shadow")      # the bad row stays as history, the new one is good
    assert len(hs.read_rows(conn)) == 1                                                                   # and the bad one is skipped when reading


# ---- invariant 3: failures --------------------------------------------------------------------------------------------------------------------------
def test_no_production_scenario_writes_one_error_row_and_does_not_raise():
    conn, _b, line = _run(builder=Builder(production=None))
    assert [r[1] for r in conn.log] == [hs.STEP_ERROR] and "no PRODUCTION" in conn.log[0][3] and line.startswith("AVISO")


def test_empty_candidates_write_one_error_row():
    conn, _b, _l = _run(live=_pf("draft", []))
    assert [r[1] for r in conn.log] == [hs.STEP_ERROR] and "empty candidates" in conn.log[0][3]


def test_a_builder_failure_is_one_error_row_not_an_exception():
    conn, _b, line = _run(builder=Builder(raises=RuntimeError("selection blew up")))
    assert [r[1] for r in conn.log] == [hs.STEP_ERROR] and "selection blew up" in conn.log[0][3] and conn.rollbacks >= 1 and "skipped" in line


def test_if_even_the_error_row_cannot_be_written_the_hook_still_returns():
    conn, _b, line = _run(conn=Conn(fail_insert=True))
    assert conn.log == [] and line.startswith("AVISO") and "could not be written" in line


# ---- invariants 1 and 2 -----------------------------------------------------------------------------------------------------------------------------
def test_the_live_portfolio_is_not_modified_and_nothing_is_persisted():
    live = copy.deepcopy(OFF)
    conn, builder, _ = _run(live=live)
    assert live.all_funds == OFF.all_funds and live.scenario_id == OFF.scenario_id
    assert all(c["dry_run"] is True for c in builder.calls) and len(builder.calls) == 2
    touched = " ".join(" ".join(s.split()).upper() for s in conn.sql)
    assert "PORTFOLIO_" not in touched and "FUND_SCORES" not in touched          # the only writes are the ingestion_log row


def test_the_switch_off_does_nothing_at_all():
    conn, builder, line = _run(enabled=False)
    assert line is None and conn.sql == [] and builder.calls == []


def test_the_module_never_reads_the_live_flag():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not [n for n in imports if getattr(n, "module", "") and n.module.startswith(("shared", "proyecto3"))]
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "PORTFOLIO_HYSTERESIS_ENABLED" not in names                           # a docstring may name it; no code may read it


def test_the_hook_runs_after_the_draft_is_persisted_and_is_fail_soft():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    src = SCRIPT.read_text(encoding="utf-8").splitlines()
    build_line = next(n.lineno for n in ast.walk(main) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "build")
    hook = next(n for n in ast.walk(main) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_hysteresis_shadow")
    assert hook.lineno > build_line                                              # builder.build persists the DRAFT before the shadow starts
    guards = [n for n in ast.walk(main) if isinstance(n, ast.If) and any(c is hook for c in ast.walk(n))]
    guard = max(guards, key=lambda n: n.lineno)                                  # the innermost `if` around the call
    assert "not dry_run" in src[guard.lineno - 1]                                # real runs only
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_hysteresis_shadow")
    body = fn.body[-1]
    assert isinstance(body, ast.Try) and any(h.type is not None and getattr(h.type, "id", "") == "Exception" for h in body.handlers)


# ---- reporting and invariant 5 ----------------------------------------------------------------------------------------------------------------------
def test_the_summary_line_of_no_rows_and_of_many():
    assert hs.summary_line([]) == "hysteresis shadow: 0 months accumulated"
    conn, _b, _l = _run()
    assert hs.summary_line(hs.read_rows(conn)).startswith("hysteresis shadow (band 0.10, switch OFF): 1 month(s)")


def test_every_statement_of_the_module_is_a_literal_with_placeholders():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    sql = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "execute"]
    assert sql and all(c.args and isinstance(c.args[0], ast.Constant) and isinstance(c.args[0].value, str) for c in sql)    # no f-string, no concatenation
