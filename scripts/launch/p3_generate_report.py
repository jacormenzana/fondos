# scripts/launch/p3_generate_report.py
# -*- coding: utf-8 -*-
"""
Genera el informe mensual (Excel) de un escenario de cartera. Lo invoca scripts\\launch\\P3_generateReport.bat.

    python scripts\\launch\\p3_generate_report.py [--scenario ID] [--output-dir DIR]

Sin --scenario informa el escenario en PRODUCCION (proyecto3/src/scenario_lifecycle.py). Sin ninguno -- o sin el ciclo de vida instalado --
termina con EXIT_NO_SCENARIO (3) y dice como seguir; nunca recurre a "el mas reciente". Con --scenario sobre un escenario que no esta en
PRODUCCION el informe lleva el aviso NO PRODUCCION y su propio nombre de fichero.
--draft informa el DRAFT mas reciente (el que acaba de construir el ciclo, antes de promoverlo): mismo aviso y nombre de fichero;
sin ningun DRAFT -- o sin el ciclo de vida instalado -- termina con EXIT_NO_SCENARIO. Es explicito: nunca se aplica por defecto.

Codigos de salida: 0 OK | 3 sin escenario que informar | 1 error (excepcion; la deja salir por stderr).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--scenario", default=None, help="id del escenario (por defecto, el de PRODUCCION)")
    group.add_argument("--draft", action="store_true", help="informa el DRAFT mas reciente (marcado NO PRODUCCION)")
    ap.add_argument("--output-dir", default=None, help="directorio de salida (por defecto REPORT_DIRS['p3'] de shared.config: out/reports/p3)")
    args = ap.parse_args(argv)

    from shared.db import get_connection
    from proyecto3.src import scenario_lifecycle as lifecycle
    from proyecto3.src.monthly_report import generate_report

    conn = get_connection()
    try:
        scenario_id = args.scenario
        if args.draft:
            scenario_id = lifecycle.latest_draft_id(conn)
            if scenario_id is None:
                print("ERROR: no hay ningun escenario DRAFT que informar (o el ciclo de vida no esta instalado: el owner debe aplicar "
                      "scripts/ops/migrate_portfolio_scenario_status.py --apply)")
                return lifecycle.EXIT_NO_SCENARIO
        path = generate_report(conn, output_dir=args.output_dir, scenario_id=scenario_id)
    except lifecycle.ScenarioLifecycleError as exc:
        print(f"ERROR: {exc}")
        return lifecycle.EXIT_NO_SCENARIO
    finally:
        conn.close()
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
