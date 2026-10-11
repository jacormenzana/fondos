# scripts/launch/p3_promote_scenario.py
# -*- coding: utf-8 -*-
"""
Promociona un escenario DRAFT a PRODUCCION (proyecto3/src/scenario_lifecycle.py). Comando del owner: nada lo ejecuta solo.

    python scripts\\launch\\p3_promote_scenario.py ESCENARIO_ID [--dry-run]
                                                  [--accept-alarms "motivo" --ap FND-nnnn]

Puertas (todas deben pasar; --dry-run las evalua sin promover). Cada rechazo lleva un codigo [GATE:<X>] estable:
  * STATUS/WEIGHTS: el escenario existe, es DRAFT y sus pesos (+ liquidez declarada) suman 1;
  * FRESHNESS: la puerta de frescura de P3 (la misma de p3_build_portfolio.py) sin entradas desactualizadas;
  * FX: exposicion divisa no-EUR dentro de MAX_NON_EUR_UNHEDGED_WEIGHT (shared/fx_exposure.py): PUERTA DURA, sin override (el mandato
    se cambia en shared/config.py con un commit revisado, no al promover); CASH: liquidez declarada <= MAX_PORTFOLIO_CASH_WEIGHT, idem;
  * AUDIT_STALE: hay una auditoria PERSISTIDA del dominio p2_metrics y es posterior a la construccion del DRAFT (sin override);
  * DATA_SCENARIO / RESIDUAL_SCENARIO / DATA_COVERAGE: ningun defecto duro abierto (HARD_INVARIANT, BLOCK2, TIMESERIES_DUPLICATE) toca
    un fondo de la cartera, ningun residual aceptado vigente cae sobre uno, y la auditoria miro todos sus fondos. Las anomalias
    estadisticas (OUTLIER_MAD_Z...) sobre fondos de la cartera se LISTAN y no bloquean (FND-0175);
  * DATA_UNIVERSE: un defecto duro fuera de la cartera exige un residual aceptado vigente (accept_audit_residual.py, con AP y caducidad).
El unico override (--accept-alarms "motivo" --ap FND-nnnn) cubre DATA_SCENARIO / RESIDUAL_SCENARIO / DATA_COVERAGE; exige el ticket que
corrige la causa y queda en ingestion_log como [OVERRIDE:<puerta>] junto con quien promueve. El anterior PRODUCCION pasa a ARCHIVED.

Codigos de salida: 0 promovido (o dry-run promovible) | 1 bloqueado por una puerta | 3 escenario inexistente / ciclo de vida sin instalar.
"""
from __future__ import annotations

import argparse
import getpass
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

RC_OK, RC_BLOCKED = 0, 1
AUDIT_DOMAIN = "p2_metrics"


def _git_state() -> str:
    """`git <short sha>` + `clean`/`dirty-tracked` for the promotion note (information, never a gate: a promotion is a data operation,
    but the record should say which code produced the scenario's metrics). Untracked files do not count."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=_ROOT, capture_output=True, text=True,
                               timeout=10).stdout.strip()
        return f"git {sha or 'n/a'} {'dirty-tracked' if dirty else 'clean'}"
    except Exception:                                            # noqa: BLE001 -- information only
        return "git n/a"


def _built_at(conn, scenario_id: str):
    row = conn.execute("SELECT built_at FROM portfolio_scenarios WHERE scenario_id = %s", (scenario_id,)).fetchone()
    return row[0] if row else None


def _ap_exists(ap: str):
    """True / False when gestion.backlog answers; None when it cannot be asked (no DSN, outage): a warning, never a blocker."""
    from shared import backlog_client
    status = backlog_client.get_action_point_status(ap)
    if status == "LOOKUP_FAILED":
        return None
    return status != "NOT_FOUND"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("scenario_id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--accept-alarms", default=None, metavar="MOTIVO")
    ap.add_argument("--ap", default=None, metavar="FND-nnnn", help="ticket que corrige la causa; obligatorio con --accept-alarms")
    args = ap.parse_args(argv)

    from shared.config import AUDIT_RESIDUAL_EXPIRY_WARN_DAYS, MAX_NON_EUR_UNHEDGED_WEIGHT, MAX_PORTFOLIO_CASH_WEIGHT
    from shared.db import get_connection
    from shared import backlog_client, fx_exposure
    from shared.statistical_audit import accepted_residuals, gating
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
        cash = lc.declared_cash(conn, args.scenario_id)
        built_at = _built_at(conn, args.scenario_id)
        try:
            accepted = accepted_residuals.load_accepted(conn, AUDIT_DOMAIN)
        except Exception as exc:                                 # noqa: BLE001 -- missing table = nothing accepted; say so
            conn.rollback()
            accepted = {}
            print(f"AVISO: control.audit_accepted_finding no es legible ({type(exc).__name__}): sin residuales aceptados")
        scope = gating.load_scope(conn, AUDIT_DOMAIN, weights.keys(), accepted)

        print(f"Escenario {args.scenario_id}: estado {status}, {len(weights)} fondos, pesos suman {sum(weights.values()):.4f}")
        print(fx_exposure.format_line(expo))
        print(f"Liquidez estructural declarada: {cash:.1%} (tope {MAX_PORTFOLIO_CASH_WEIGHT:.0%})")
        print(f"Frescura P3: {'sin entradas desactualizadas' if not stale else 'DESACTUALIZADO: ' + ', '.join(stale)}")
        age = f"{(datetime.now(timezone.utc) - scope.run_ts).days} dias" if scope.run_ts is not None else "n/a"
        built = f"{built_at:%Y-%m-%d %H:%M}" if built_at is not None else "desconocida"
        print(f"Ultima auditoria persistida ({AUDIT_DOMAIN}): {scope.run_id or 'ninguna'} (antiguedad {age}); construccion del DRAFT: {built}")
        if scope.has_run:
            print(f"  defectos duros abiertos: en cartera {len(scope.on_scenario)} regla(s), fuera de cartera {len(scope.universe_only)} regla(s); "
                  f"cubiertos por residual vigente: {len({r[1] for r in scope.covered_by_residual})} ISINs; "
                  f"residuales aceptados sobre fondos de la cartera: {len(scope.residuals_on_scenario)}")
            print(f"  cobertura de la auditoria: {scope.coverage}"
                  + (f" ({scope.coverage_detail})" if scope.coverage_detail else "")
                  + f"; fuera de bloques transversales: {len(scope.held_out_on_scenario)}; fuera del universo auditado: "
                  f"{len(scope.outside_population)}")
            by_fund: dict = defaultdict(list)
            for isin, rule_id, group_key in scope.anomalies_on_scenario:
                by_fund[isin].append(group_key.split("|")[0] or rule_id)
            print(f"  anomalias estadisticas ALARM sobre fondos de la cartera (informativas, no bloquean): "
                  f"{len(scope.anomalies_on_scenario)} en {len(by_fund)} fondos")
            for isin in sorted(by_fund):
                names = sorted(set(by_fund[isin]))
                print(f"    {isin}: {len(by_fund[isin])} ({', '.join(names[:4])}{'...' if len(names) > 4 else ''})")
        for line in accepted_residuals.attention_lines(conn, AUDIT_DOMAIN, AUDIT_RESIDUAL_EXPIRY_WARN_DAYS,
                                                       ap_status=backlog_client.get_action_point_status):
            print(f"AVISO: {line}")

        reason = (args.accept_alarms or "").strip()
        ap_id = (args.ap or "").strip().upper()
        ap_exists = _ap_exists(ap_id) if reason and lc.AP_RE.match(ap_id) else None
        if reason and lc.AP_RE.match(ap_id) and ap_exists is None:
            print(f"AVISO: no se pudo comprobar {ap_id} en gestion.backlog (FONDOS_BACKLOG_PG_DSN no definida o sin conexion): "
                  "se acepta por formato")

        verdict = lc.evaluate_promotion(
            status=status, n_funds=len(weights), weight_sum=sum(weights.values()), stale_checks=stale,
            exposure_non_eur=expo["non_eur"], budget=MAX_NON_EUR_UNHEDGED_WEIGHT, audit=scope, built_at=built_at,
            accept_alarms=reason, accept_ap=ap_id, ap_exists=ap_exists, declared_cash=cash, max_cash=MAX_PORTFOLIO_CASH_WEIGHT)
        if verdict.blockers:
            print("\nNO PROMOVIBLE:")
            for b in verdict.blockers:
                print(f"  - {b}")
            if not args.dry_run:                                   # a real refusal leaves an audit row; a dry run writes nothing
                try:
                    lc.record_blocked(conn, args.scenario_id, getpass.getuser(), verdict.blockers)
                except Exception as exc:                           # noqa: BLE001 -- the refusal stands; say the trace was not written
                    print(f"AVISO: no se pudo registrar el intento bloqueado en ingestion_log: {exc}")
            return RC_BLOCKED
        if verdict.overridden:
            print("\nOVERRIDE (puertas cubiertas por --accept-alarms + --ap):")
            for code, text in verdict.overridden:
                print(f"  - [OVERRIDE:{code}] {args.ap}: {text}")
        if args.dry_run:
            print("\nDRY RUN: promovible. Nada cambiado.")
            return RC_OK

        user = getpass.getuser()
        note = "; ".join([f"non-EUR exposure {expo['non_eur']:.1%}", lc.signoff_line(scope, verdict.overridden, ap_id, reason), _git_state()])
        previous = lc.promote(conn, args.scenario_id, user, note)       # promotion + audit row in ONE transaction
        print(f"\nPROMOVIDO: {args.scenario_id} es ahora PRODUCCION" + (f"; {previous} -> ARCHIVED" if previous else ""))
        return RC_OK
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
