# proyecto3/tests/test_pit_exposure.py
# -*- coding: utf-8 -*-
"""
The PIT backtest builds portfolios under the SAME currency-exposure construction constraint as the live builder (owner review 2026-10-11):
flags travel in PitInputs.fx_exposed (not in `attrs`, which feeds the score cache keys), candidates_by_sub adds the `fx_exposed` column and
build_weights applies score_candidates.exposure_constrained when flags are supplied. Pure (R-7: no DB).
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import proyecto3.src.score_candidates as sc  # noqa: E402
from proyecto3.src.pit_backtest import build_weights, candidates_by_sub  # noqa: E402
from proyecto3.src.pit_inputs import load_fx_exposed  # noqa: E402
from proyecto3.src.pit_run import PitInputs  # noqa: E402

T = pd.Timestamp("2020-06-30")
SUB_W = 0.2                                              # Defensiva regime weight used below
BUDGET = 0.30


def _scores(n_usd=8, n_eur=12):
    rows = []
    for k in range(n_usd):
        rows.append(("USD%02d" % k, 0.95 - 0.01 * k))
    for k in range(n_eur):
        rows.append(("EUR%02d" % k, 0.80 - 0.01 * k))
    return pd.DataFrame([dict(as_of=T, regime="Expansion", isin=i, subportfolio="Defensiva", fund_name=i,
                              fund_nature=f"N{n % 4}", fund_family_id=f"F{i}", score_base=s, multiplier=1.0, score_final=s,
                              eligible=True, exclusion_reason=None) for n, (i, s) in enumerate(rows)])


def _attrs(isins):
    return pd.DataFrame({"Management_Company": [f"M{i}" for i in range(len(isins))]}, index=pd.Index(isins, name="isin"))


def _hist():
    return pd.DataFrame({"regime": "Expansion", "weight_defensive": SUB_W, "weight_balanced": 0.5, "weight_dynamic": 0.3},
                        index=pd.DatetimeIndex([T - pd.offsets.MonthEnd(1)]))


def _flags(scores):
    return pd.Series({i: i.startswith("USD") for i in scores["isin"]}, dtype=bool)


def _exposed_master(W, flags):
    row = W.loc[T].dropna()
    return float(sum(w for i, w in row.items() if flags.get(i, False)))


def test_candidates_carry_the_flag_column_only_when_flags_are_supplied():
    s = _scores(2, 2)
    plain = candidates_by_sub(s, _attrs(s["isin"]))
    assert "fx_exposed" not in plain["Defensiva"].columns
    flagged = candidates_by_sub(s, _attrs(s["isin"]), pd.Series({"USD00": True, "USD01": True}, dtype=bool))
    col = flagged["Defensiva"].set_index("isin")["fx_exposed"]
    assert col["USD00"] and col["USD01"] and not col["EUR00"] and not col["EUR01"]     # absent from the Series = not exposed
    assert col.dtype == bool


def test_flags_make_the_pit_portfolio_respect_the_budget_that_the_unconstrained_one_breaks():
    s = _scores()
    at = pd.DatetimeIndex([T])
    flags = _flags(s)
    free, _ = build_weights(s, _attrs(s["isin"]), _hist(), at)                                 # previous behaviour
    bound, _ = build_weights(s, _attrs(s["isin"]), _hist(), at, fx_exposed=flags)
    assert _exposed_master(free, flags) > BUDGET * SUB_W + 1e-3                                # the failure being fixed
    assert 0 < _exposed_master(bound, flags) <= BUDGET * SUB_W + 1e-3                          # constrained, not eliminated


def test_the_switch_off_keeps_the_pit_path_unconstrained(monkeypatch):
    monkeypatch.setattr(sc, "FX_EXPOSURE_CONSTRAINT_ENABLED", False)
    s = _scores()
    at = pd.DatetimeIndex([T])
    a, _ = build_weights(s, _attrs(s["isin"]), _hist(), at)
    b, _ = build_weights(s, _attrs(s["isin"]), _hist(), at, fx_exposed=_flags(s))
    assert a.loc[T].dropna().to_dict() == b.loc[T].dropna().to_dict()


def test_pit_inputs_default_has_no_flags_and_loader_follows_the_gate_rule(monkeypatch):
    assert PitInputs(nav=pd.DataFrame(), attrs=pd.DataFrame(), ipc=None, rate=None).fx_exposed is None

    class Conn:
        def execute(self, sql, params=()):
            rows = [("A", "USD", "Unhedged", "USD"), ("B", "EUR", "Hedged", "USD"), ("C", "EUR", "Unhedged", "MCY")]
            return type("Cur", (), {"fetchall": lambda s: rows})()

    out = load_fx_exposed(Conn(), ["A", "B", "C", "D"])
    assert out.to_dict() == {"A": True, "B": False, "C": False, "D": False} and out.dtype == bool and out.index.name == "isin"
    monkeypatch.setattr(sc, "FX_EXPOSURE_CONSTRAINT_ENABLED", False)
    assert load_fx_exposed(Conn(), ["A"]) is None
