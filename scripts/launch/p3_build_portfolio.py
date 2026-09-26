# scripts/launch/p3_build_portfolio.py
# -*- coding: utf-8 -*-
"""
Orquesta el ciclo completo de construccion de cartera P3: clasifica el
regimen actual, puntua todos los fondos (fund_scores) y construye/persiste
la cartera maestra (portfolio_scenarios, portfolio_weights).

Fase 3c (P3 optimization plan, 2026-09-18): antes, este ciclo solo se
ejecutaba a mano encadenando scripts/test/test_scorer.py +
scripts/test/test_portfolio.py (herramientas de debug de un solo paso, con
import paths desactualizados -- proyecto2.src.db en vez de shared.db) --
P3_generateReport.bat solo genera el informe a partir de lo que ya este
persistido; nada automatizaba clasificar -> puntuar -> construir en un solo
paso reproducible.

Invocado por scripts/launch/P3_buildPortfolio.bat. Tambien ejecutable
directamente para debug:
    C:\\data\\envs\\des\\python.exe scripts\\launch\\p3_build_portfolio.py [scenario_id]
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from shared.config import P3_EXIT_STALE_INPUTS
from shared.db import get_connection
from proyecto3.src.data_freshness import check_universe_freshness, format_report, stale_checks
from proyecto3.src.regime_classifier import RegimeClassifier
from proyecto3.src.fund_scorer import score_funds
from proyecto3.src.portfolio_builder import PortfolioBuilder


def _default_scenario_id(regime: str, date) -> str:
    """cartera_<regimen>_<YYYYMM>, p.ej. cartera_shock_energetico_202609."""
    return f"cartera_{regime.lower()}_{date.year}{date.month:02d}"


def main(scenario_id: str | None = None, dry_run: bool = False, backend: str | None = None,
         allow_stale: bool = False) -> int:
    conn = get_connection(backend=backend)
    clf  = RegimeClassifier(conn)

    # FND-0098: refuse to score/persist a portfolio from outdated NAV / macro / universe data.
    # A dry-run persists nothing, so it only warns; --allow-stale overrides (loudly) a real run.
    checks = check_universe_freshness(conn, clf)
    print(format_report(checks))
    print()
    _stale = stale_checks(checks)
    if _stale:
        _names = ", ".join(c.name for c in _stale)
        if dry_run or allow_stale:
            print(f"AVISO: entradas desactualizadas ({_names}) -- continuando por "
                  f"{'--dry-run' if dry_run else '--allow-stale'}.")
            print()
        else:
            print(f"ERROR: entradas desactualizadas ({_names}) -- abortando sin puntuar ni "
                  f"persistir. Refresque los datos (P2_discoverLoadMetrics.bat / harvest) o "
                  f"use --allow-stale bajo su responsabilidad.")
            return P3_EXIT_STALE_INPUTS

    reg  = clf.classify_current()

    print(clf.current_regime_report())
    print()

    if not scenario_id:
        scenario_id = _default_scenario_id(reg.regime, reg.date)
    print(f"Scenario: {scenario_id}" + (" (dry-run, sin persistir)" if dry_run else ""))
    print()

    scores = score_funds(conn, reg, dry_run=dry_run)
    if scores.empty:
        print("ERROR: score_funds no devolvio filas -- abortando "
              "(ver mensaje anterior de fund_scorer).")
        return 1
    print(f"Fondos puntuados: {len(scores)}")
    print()

    builder   = PortfolioBuilder(conn)
    portfolio = builder.build(reg, scenario_id=scenario_id, dry_run=dry_run)
    print(portfolio.summary())

    return 0


if __name__ == "__main__":
    _args = sys.argv[1:]
    _dry_run = "--dry-run" in _args
    _allow_stale = "--allow-stale" in _args
    # --backend {sqlite,postgres} — migration addendum 2026-09-20. None resolves
    # FONDOS_DB_BACKEND ("sqlite" if unset). Manual parsing to match this script's existing
    # positional-scenario_id style rather than introducing argparse for one new flag.
    _backend = None
    if "--backend" in _args:
        _i = _args.index("--backend")
        _backend = _args[_i + 1]
        del _args[_i:_i + 2]
    _positional = [a for a in _args if a not in ("--dry-run", "--allow-stale")]
    scenario_arg = _positional[0] if _positional else None

    from shared.backlog_client import capture_exceptions
    with capture_exceptions(object_name="p3_build_portfolio.py", object_type="JOB"):
        _rc = main(scenario_arg, dry_run=_dry_run, backend=_backend, allow_stale=_allow_stale)
    sys.exit(_rc)
