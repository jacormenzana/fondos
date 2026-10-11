# scripts/launch/p3_promote_scenario.py
# -*- coding: utf-8 -*-
"""
Promociona un escenario DRAFT a PRODUCCION (proyecto3/src/scenario_lifecycle.py). Comando del owner: nada lo ejecuta solo.

    python scripts\\launch\\p3_promote_scenario.py ESCENARIO_ID [--dry-run]
                                                  [--accept-alarms "motivo"]

Puertas (todas deben pasar; --dry-run las evalua sin promover):
  * el escenario existe, es DRAFT y sus pesos suman 1;
  * la puerta de frescura de P3 (la misma de p3_build_portfolio.py) sin entradas desactualizadas;
  * exposicion divisa no-EUR dentro de MAX_NON_EUR_UNHEDGED_WEIGHT (shared/fx_exposure.py): PUERTA DURA, sin override (el mandato
    se cambia en shared/config.py con un commit revisado, no al promover);
  * cero hallazgos ALARM en la ultima auditoria PERSISTIDA del dominio p2_metrics (se imprime su run_id y antiguedad: sin
    `run_statistical_audit.py --persist` reciente la puerta mira evidencia vieja), o --accept-alarms "motivo".
El unico override (--accept-alarms) exige motivo y queda en ingestion_log junto con quien promueve. El anterior PRODUCCION
pasa a ARCHIVED.

Codigos de salida: 0 promovido (o dry-run promovible) | 1 bloqueado por una puerta | 3 escenario inexistente / ciclo de vida sin instalar.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

RC_OK, RC_BLOCKED = 0, 1
AUDIT_DOMAIN = "p2_metrics"


def latest_alarms(conn, domain: str = AUDIT_DOMAIN):
    """(run_id, detected_at, n ALARM rows) of the latest persisted audit run of the domain; (None, None, 0) when there is none."""
    row = conn.execute(
        "SELECT run_id, MAX(detected_at) FROM audit_finding WHERE domain = %s GROUP BY run_id "
        "ORDER BY MAX(detected_at) DESC LIMIT 1", (domain,)).fetchone()
    if row is None:
        return None, None, 0
    n = conn.execute("SELECT COUNT(*) FROM audit_finding WHERE run_id = %s AND domain = %s AND severity = 'ALARM'",
                     (row[0], domain)).fetchone()[0]
    return row[0], row[1], int(n)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("scenario_id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--accept-alarms", default=None, metavar="MOTIVO")
    args = ap.parse_args(argv)

    from shared.config import MAX_NON_EUR_UNHEDGED_WEIGHT, MAX_PORTFOLIO_CASH_WEIGHT
    from shared.db import get_connection
    from shared import fx_exposure
    from proyecto3.src import scenario_lifecycle as lc
    from proyecto3.src.data_freshness import check_universe_freshness, stale_checks
    from proyecto3.src.regime_classifier import RegimeClassifier

    conn = get_connection()
    try:
        if not lc.has_status_column(conn):
            print("ERROR: el ciclo de vida de escenarios no esta instalado (falta portfolio_scenarios.status): el owner debe aplicar "
                  "scripts/ops/migrate_portfolio_scenario_status.py --apply")
            return lc.EXIT_NO_SCENARIO
        status = lc.get_status(conn, args.scenario_id)
        if status is None:
            print(f"ERROR: el escenario '{args.scenario_id}' no existe")
            return lc.EXIT_NO_SCENARIO

        rows = conn.execute("SELECT isin, weight FROM portfolio_weights WHERE scenario_id = %s", (args.scenario_id,)).fetchall()
        weights = {r[0]: float(r[1]) for r in rows}
        expo = fx_exposure.portfolio_summary(conn, weights, MAX_NON_EUR_UNHEDGED_WEIGHT)
        stale = [c.name for c in stale_checks(check_universe_freshness(conn, RegimeClassifier(conn)))]
        run_id, run_ts, alarms = latest_alarms(conn)
        cash = lc.declared_cash(conn, args.scenario_id)

        print(f"Escenario {args.scenario_id}: estado {status}, {len(weights)} fondos, pesos suman {sum(weights.values()):.4f}")
        print(fx_exposure.format_line(expo))
        print(f"Liquidez estructural declarada: {cash:.1%} (tope {MAX_PORTFOLIO_CASH_WEIGHT:.0%})")
        print(f"Frescura P3: {'sin entradas desactualizadas' if not stale else 'DESACTUALIZADO: ' + ', '.join(stale)}")
        age = f"{(datetime.now(timezone.utc) - run_ts).days} dias" if run_ts is not None else "n/a"
        print(f"Ultima auditoria persistida ({AUDIT_DOMAIN}): {run_id or 'ninguna'} (antiguedad {age}); ALARM abiertos: {alarms}")

        blockers = lc.promotion_blockers(
            status=status, n_funds=len(weights), weight_sum=sum(weights.values()), stale_checks=stale,
            exposure_non_eur=expo["non_eur"], budget=MAX_NON_EUR_UNHEDGED_WEIGHT, alarms=alarms,
            accept_alarms=args.accept_alarms, declared_cash=cash, max_cash=MAX_PORTFOLIO_CASH_WEIGHT)
        if blockers:
            print("\nNO PROMOVIBLE:")
            for b in blockers:
                print(f"  - {b}")
            if not args.dry_run:                                   # a real refusal leaves an audit row; a dry run writes nothing
                try:
                    lc.record_blocked(conn, args.scenario_id, getpass.getuser(), blockers)
                except Exception as exc:                           # noqa: BLE001 -- the refusal stands; say the trace was not written
                    print(f"AVISO: no se pudo registrar el intento bloqueado en ingestion_log: {exc}")
            return RC_BLOCKED
        if args.dry_run:
            print("\nDRY RUN: promovible. Nada cambiado.")
            return RC_OK

        user = getpass.getuser()
        overrides = []
        if alarms and (args.accept_alarms or "").strip():
            overrides.append(f"{alarms} ALARM en {run_id}: {args.accept_alarms.strip()}")
        note = f"non-EUR exposure {expo['non_eur']:.1%}" + ("; OVERRIDES: " + " | ".join(overrides) if overrides else "")
        previous = lc.promote(conn, args.scenario_id, user, note)       # promotion + audit row in ONE transaction
        print(f"\nPROMOVIDO: {args.scenario_id} es ahora PRODUCCION" + (f"; {previous} -> ARCHIVED" if previous else ""))
        return RC_OK
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
