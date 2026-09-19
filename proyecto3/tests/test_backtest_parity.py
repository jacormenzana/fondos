# proyecto3/tests/test_backtest_parity.py
# -*- coding: utf-8 -*-
"""
Test de paridad Fase 4 (P3 optimization plan, 2026-09-18) -- v2.0 release
cut line.

Antes de la Fase 4, Backtester._build_weights_for_regime() era una segunda
implementacion independiente de "construir una cartera" (top-10 por score,
pesos proporcionales, SIN ningun limite -- ni tope del 20%, ni suelo del
3%, ni max_same_nature, ni limite por gestora, ni dedup por familia, ni
histeresis, ni coste de rotacion). El backtest nunca probaba la cartera que
PortfolioBuilder.build() realmente construye y persiste, lo que hacia
inutilizable cualquier medicion de las Fases 5-7 contra ese backtest.

Este test es el invariante que hace la paridad verificable: dados los
MISMOS candidatos (fund_scores JOIN fund_master) y el MISMO vector de pesos
de regimen, PortfolioBuilder.build() (el camino real, con toda su
maquinaria -- Portfolio/SubPortfolioAllocation, persist condicional) y
Backtester._select_for_regime() + _blend_to_master() (el camino del
backtest) deben producir exactamente el mismo mapa {isin: peso_master}.

R-7: importa solo proyecto3.src.{portfolio_builder,backtesting,
portfolio_engine,regime_classifier} -- no pipeline.py, no core.io.
Run from repo root:
    python -m pytest proyecto3/tests/test_backtest_parity.py -v
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]  # c:\desarrollo\fondos
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.portfolio_builder import PortfolioBuilder
from proyecto3.src.portfolio_engine import DEFAULT_CONSTRAINTS, select_and_weight
from proyecto3.src.backtesting import _load_candidates, _blend_to_master
from proyecto3.src.regime_classifier import RegimeResult


_SCHEMA = """
CREATE TABLE fund_master (
    ISIN TEXT PRIMARY KEY,
    Fund_Name TEXT,
    Fund_Nature TEXT,
    Management_Company TEXT,
    fund_family_id TEXT,
    In_Current_Universe INTEGER DEFAULT 1
);
CREATE TABLE fund_scores (
    isin TEXT, block TEXT, score_version TEXT, score_total REAL,
    score_detail TEXT, eligible INTEGER
);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_SCHEMA)
    yield c
    c.close()


def _seed(conn, n_per_sub: int = 12) -> None:
    """
    Puebla un universo realista: n_per_sub fondos elegibles por
    sub-cartera, con score y naturaleza variados para que
    max_same_nature tenga la oportunidad de disparar en ambos caminos por
    igual. Gestora UNICA por fondo (no reutilizada entre sub-carteras) --
    MAX_FUNDS_PER_MGR es GLOBAL entre sub-carteras por diseno, asi que
    reutilizar el mismo pool de gestoras en las tres sub-carteras agotaria
    la cuota de cada gestora ya dentro de la primera sub-cartera procesada
    (Defensiva, 10 fondos / 2 por gestora = 5 gestoras exactas), dejando
    Equilibrada/Dinamica sin candidatos -- una patologia de los datos de
    prueba, no del codigo bajo prueba (que ya tiene su propia cobertura
    dedicada de max_funds_per_mgr en test_portfolio_constraints.py).
    """
    natures = ["Renta Variable", "Mixtos", "Renta Fija Flexible",
               "Renta Fija Corto Plazo", "Monetario", "Alternativo"]
    subs = ["Defensiva", "Equilibrada", "Dinamica"]
    for sub in subs:
        for i in range(n_per_sub):
            isin   = f"{sub[:3].upper()}{i:04d}"
            nature = natures[i % len(natures)]
            mgr    = f"{sub}_Gestora{i}"  # unica por fondo -- fuera de alcance aqui
            score  = 1.0 - i * 0.01      # descendente, sin empates
            conn.execute(
                "INSERT INTO fund_master (ISIN, Fund_Name, Fund_Nature, "
                "Management_Company, fund_family_id, In_Current_Universe) "
                "VALUES (?, ?, ?, ?, NULL, 1)",
                (isin, f"Fund {isin}", nature, mgr),
            )
            conn.execute(
                "INSERT INTO fund_scores (isin, block, score_version, "
                "score_total, score_detail, eligible) VALUES (?, ?, 'v1', ?, '{}', 1)",
                (isin, sub, score),
            )
    conn.commit()


def _fake_regime_result(sub_weights: dict[str, float]) -> RegimeResult:
    return RegimeResult(
        date=pd.Timestamp("2026-06-30"),
        regime="Shock_Energetico",
        weight_defensive=sub_weights["Defensiva"],
        weight_balanced=sub_weights["Equilibrada"],
        weight_dynamic=sub_weights["Dinamica"],
        oil_yoy=None, ipc_yoy_avg=None, cli_eu=None,
        rate_deposit=None, d_rate_3m=None, spread_hy=None, vix_yoy=None,
    )


@pytest.mark.parametrize("sub_weights", [
    {"Defensiva": 0.55, "Equilibrada": 0.35, "Dinamica": 0.10},   # Shock_Energetico
    {"Defensiva": 0.70, "Equilibrada": 0.25, "Dinamica": 0.05},   # Crisis_Financiera
    {"Defensiva": 0.20, "Equilibrada": 0.45, "Dinamica": 0.35},   # Expansion
])
def test_builder_and_backtester_produce_identical_master_weights(conn, sub_weights):
    _seed(conn)
    reg = _fake_regime_result(sub_weights)

    # Camino 1: PortfolioBuilder.build() -- el real, con toda su maquinaria.
    builder   = PortfolioBuilder(conn)
    portfolio = builder.build(reg, scenario_id="parity_test", dry_run=True)
    builder_weights = {f["isin"]: f["master_weight"] for f in portfolio.all_funds}

    # Camino 2: el camino del backtest -- _load_candidates (misma query que
    # PortfolioBuilder) + select_and_weight() + _blend_to_master(), sin
    # pasar por Portfolio/SubPortfolioAllocation en absoluto.
    candidates = _load_candidates(conn, score_version="v1")
    selection  = select_and_weight(candidates, sub_weights, DEFAULT_CONSTRAINTS)
    backtest_weights = _blend_to_master(selection, sub_weights)

    assert builder_weights.keys() == backtest_weights.keys(), (
        f"different fund sets selected:\n"
        f"  builder only:   {set(builder_weights) - set(backtest_weights)}\n"
        f"  backtester only: {set(backtest_weights) - set(builder_weights)}"
    )
    for isin in builder_weights:
        assert builder_weights[isin] == pytest.approx(backtest_weights[isin], abs=1e-9), (
            f"{isin}: builder={builder_weights[isin]} != "
            f"backtester={backtest_weights[isin]}"
        )


def test_parity_holds_with_incumbents_and_hysteresis(conn):
    # A non-trivial variant: PortfolioBuilder.build() with an explicit
    # previous_portfolio (hysteresis-biased selection order) must still
    # match a backtester call using the SAME incumbents parameter into
    # select_and_weight() directly.
    _seed(conn)
    sub_weights = {"Defensiva": 0.55, "Equilibrada": 0.35, "Dinamica": 0.10}
    reg = _fake_regime_result(sub_weights)

    incumbents = {"Defensiva": frozenset({"DEF0005"})}  # a mid-ranked incumbent

    from proyecto3.src.portfolio_builder import Portfolio, SubPortfolioAllocation
    previous = Portfolio(
        scenario_id="prev", regime="Shock_Energetico", profile="Equilibrada",
        sub_portfolios=[
            SubPortfolioAllocation(name="Defensiva", regime_weight=1.0,
                                    funds=[{"isin": "DEF0005", "weight": 0.10}]),
        ],
    )

    builder   = PortfolioBuilder(conn)
    portfolio = builder.build(reg, scenario_id="parity_hyst", dry_run=True,
                               previous_portfolio=previous)
    builder_weights = {f["isin"]: f["master_weight"] for f in portfolio.all_funds}

    candidates = _load_candidates(conn, score_version="v1")
    selection  = select_and_weight(candidates, sub_weights, DEFAULT_CONSTRAINTS,
                                    incumbents=incumbents)
    backtest_weights = _blend_to_master(selection, sub_weights)

    assert builder_weights.keys() == backtest_weights.keys()
    for isin in builder_weights:
        assert builder_weights[isin] == pytest.approx(backtest_weights[isin], abs=1e-9)
