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

from shared.config import MAX_NON_EUR_UNHEDGED_WEIGHT, MAX_PORTFOLIO_CASH_WEIGHT, P3_EXIT_STALE_INPUTS
from shared.db import get_connection
from shared.eur_nav import assert_release_coupling   # FND-0243 D4
from proyecto3.src.data_freshness import check_universe_freshness, format_report, stale_checks
from proyecto3.src.regime_classifier import RegimeClassifier
from proyecto3.src.fund_scorer import score_funds
from proyecto3.src.portfolio_builder import PortfolioBuilder
from proyecto3.src import scenario_lifecycle as lifecycle
from shared import fx_exposure


def _default_scenario_id(regime: str, date=None) -> str:
    """cartera_<regimen>_<YYYYMM>_b<YYYYMMDDHHMMSS>: unico por construccion. Antes era cartera_<regimen>_<YYYYMM> y reconstruir en el
    mismo mes y regimen sobrescribia la cartera anterior (2026-10-10). `date` se conserva por compatibilidad y ya no interviene."""
    return lifecycle.build_scenario_id(regime)


def main(scenario_id: str | None = None, dry_run: bool = False, allow_stale: bool = False,
         replace_draft: bool = False) -> int:
    assert_release_coupling()   # FND-0243 D4: EUR view and FND-0235 switches move together
    conn = get_connection()
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

    if not scenario_id:
        scenario_id = _default_scenario_id(reg.regime, reg.date)

    # An --allow-stale override of a real run is never silent (FND-0244 follow-up): it is recorded BEFORE anything is persisted, and a
    # bypass that cannot be recorded does not run.
    if _stale and allow_stale and not dry_run:
        from shared.family_refresh import pending_family_refresh
        from shared.gate_bypass import record_gate_bypass
        try:
            _rec = record_gate_bypass(conn, [(c.name, c.detail) for c in _stale], scenario_id, pending_family_refresh(conn))
            conn.commit()                                       # durable BEFORE anything is scored or persisted
        except Exception as exc:
            conn.rollback()
            print(f"ERROR: no se pudo registrar la auditoria de --allow-stale ({type(exc).__name__}: {str(exc)[:160]}) -- "
                  f"abortando sin puntuar ni persistir.")
            return 1
        print(f"AUDITORIA --allow-stale registrada: ingestion_log={_rec['ingestion_log']} filas, audit_finding={_rec['audit_finding']}, "
              f"cycle_flag={'si' if _rec['cycle_flag'] else 'no'} (control.v_p3_gate_bypass las lee).")
        print()

    print(clf.current_regime_report())
    print()

    if not dry_run:
        print(f"Historico de regimenes persistido: {clf.persist_history()} filas (gold.regime_history).")
        print()

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
    try:
        portfolio = builder.build(reg, scenario_id=scenario_id, dry_run=dry_run, replace_draft=replace_draft)
    except lifecycle.ScenarioExists as exc:
        print(f"ERROR: {exc}")
        return lifecycle.EXIT_NO_SCENARIO
    print(portfolio.summary())

    # Currency exposure (owner decision 2026-10-11: budget MAX_NON_EUR_UNHEDGED_WEIGHT; binding at construction with
    # FX_EXPOSURE_CONSTRAINT_ENABLED, so an AVISO here means the constraint is off or the pool had no non-exposed funds left).
    weights = {f["isin"]: f["master_weight"] for f in portfolio.all_funds}
    expo = fx_exposure.portfolio_summary(conn, weights, MAX_NON_EUR_UNHEDGED_WEIGHT)
    print()
    print(("AVISO: " if expo["over_budget"] else "") + fx_exposure.format_line(expo))
    cash = portfolio.cash_weight
    print(("AVISO: " if cash > MAX_PORTFOLIO_CASH_WEIGHT else "")
          + f"Liquidez estructural declarada: {cash:.1%} (tope de promocion {MAX_PORTFOLIO_CASH_WEIGHT:.0%})"
          + (" -- el escenario NO sera promovible" if cash > MAX_PORTFOLIO_CASH_WEIGHT else ""))
    if not dry_run:
        print(f"Escenario '{scenario_id}' creado como DRAFT. No se promueve solo: p3_promote_scenario.py {scenario_id}")

    return 0


if __name__ == "__main__":
    _args = sys.argv[1:]
    _dry_run = "--dry-run" in _args
    _allow_stale = "--allow-stale" in _args
    _replace_draft = "--replace-draft" in _args
    _positional = [a for a in _args if a not in ("--dry-run", "--allow-stale", "--replace-draft")]
    scenario_arg = _positional[0] if _positional else None

    from shared.backlog_client import capture_exceptions
    with capture_exceptions(object_name="p3_build_portfolio.py", object_type="JOB"):
        _rc = main(scenario_arg, dry_run=_dry_run, allow_stale=_allow_stale, replace_draft=_replace_draft)
    sys.exit(_rc)
