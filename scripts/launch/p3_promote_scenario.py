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
  * DATA_UNIVERSE: un defecto duro fuera de la cartera exige un residual aceptado vigente (accept_audit_residual.py, con AP y caducidad);
  * ALARM_WATCH (FND-0289, con ALARM_WATCH_GATE_ENABLED; apagado se muestra en sombra): un fondo de PRODUCCION con una ALARM vigente de
    ALARM_WATCH_RULES en fund_metric_alerts no puede subir de peso en el DRAFT; si las alarmas no se pueden leer, rechaza (fail closed).
El unico override (--accept-alarms "motivo" --ap FND-nnnn) cubre DATA_SCENARIO / RESIDUAL_SCENARIO / DATA_COVERAGE / ALARM_WATCH; exige el ticket que
corrige la causa y queda en ingestion_log como [OVERRIDE:<puerta>] junto con quien promueve. El anterior PRODUCCION pasa a ARCHIVED.

Codigos de salida: 0 promovido (o dry-run promovible) | 1 bloqueado por una puerta | 3 escenario inexistente / ciclo de vida sin instalar.
"""
from __future__ import annotations

import argparse
import getpass
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
    from shared.governance.run_identity import describe_git_state, git_state   # one capture for every recorder (P#11)
    return describe_git_state(git_state(_ROOT))


def _governed_exceptions(conn, enabled: bool) -> tuple:
    """(problem or None, {(rule_id, isin): grant_event_id}) for the accepted residuals of AUDIT_DOMAIN (FND-0263). With
    AUDIT_EXCEPTION_EVENTS_ENABLED an unreadable history, or a projection that disagrees with it, is a problem (GATE:EXCEPTIONS);
    without it the history only feeds the promotion note, and is skipped silently when it cannot be read."""
    from shared.statistical_audit import accepted_residuals
    try:
        governed = accepted_residuals.verify_governed(conn, AUDIT_DOMAIN)
    except Exception as exc:                                     # noqa: BLE001 -- the caller decides what an unreadable store means
        conn.rollback()
        return ((f"control.audit_exception_event no es legible ({type(exc).__name__}): aplique scripts/ops/migrate_audit_governance.py"
                 if enabled else None), {})
    if enabled and governed["drift"]:
        rule_id, isin, table, hist = governed["drift"][0]
        return (f"{len(governed['drift'])} clave(s) (regla, ISIN) difieren entre audit_accepted_finding y su historial; p.ej. {rule_id} "
                f"{isin}: tabla {table}, historial {hist}", governed["sources"])
    return None, governed["sources"]


def _exception_ids(scope, sources: dict) -> str:
    """`exceptions <grant ids>` behind the residuals this promotion relied on or listed (for the note), or ''."""
    keys = [(r[0], r[1]) for r in list(scope.covered_by_residual) + list(scope.residuals_on_scenario)]
    ids = sorted({sources[k] for k in keys if k in sources})
    return f"exceptions {','.join(str(i) for i in ids)}" if ids else ""


COST_DOMAIN = "cost_attributes"


def _evidence_header(conn, run_id):
    """The control.audit_run row of the p2 evidence run as a dict (git_commit, config_hash, catalog_version, manifest), or None."""
    from shared.statistical_audit.persistence import audit_run_has_manifest
    if not run_id:
        return None
    try:
        if conn.execute("SELECT to_regclass('audit_run')").fetchone()[0] is None:
            return None
        manifest_sql = "manifest" if audit_run_has_manifest(conn) else "NULL"
        row = conn.execute(f"SELECT git_commit, config_hash, catalog_version, {manifest_sql} FROM audit_run "
                           "WHERE run_id = %s AND domain = %s", (run_id, AUDIT_DOMAIN)).fetchone()
    except Exception:                                            # noqa: BLE001 -- unreadable header = no header (GATE:MANIFEST says so)
        conn.rollback()
        return None
    if row is None:
        return None
    header = dict(zip(("git_commit", "config_hash", "catalog_version", "manifest"), tuple(row)))
    try:                                                         # FND-0306: the clean flag, read apart so a missing column can never remove the header (GATE:MANIFEST)
        header["git_clean"] = conn.execute("SELECT git_clean FROM audit_run WHERE run_id = %s AND domain = %s", (run_id, AUDIT_DOMAIN)).fetchone()[0]
    except Exception:                                            # noqa: BLE001
        conn.rollback()
        header["git_clean"] = None
    return header


def _cost_scope(conn, isins):
    """(cost defects ON the scenario, has an evidence run) from the cost_attributes audit (decision D8), via the same loader as p2."""
    from shared.statistical_audit import accepted_residuals, gating
    try:
        accepted = accepted_residuals.load_accepted(conn, COST_DOMAIN)
    except Exception:                                            # noqa: BLE001 -- no residual table = nothing accepted
        conn.rollback()
        accepted = {}
    scope = gating.load_scope(conn, COST_DOMAIN, isins, accepted)
    return list(scope.on_scenario), scope.has_run


def _print_verdict(v, decides: bool) -> None:
    from shared.governance.orchestrator import verdict_line
    print(f"\nOrquestador de dos pilares ({'DECIDE' if decides else 'en sombra: decide la puerta actual'}): {verdict_line(v)}")
    for p, r in v.pillars.items():
        for w in r.warnings:
            print(f"  ~ [{p}] {w}")
        if not decides:
            for b in r.blockers:
                print(f"  ! [{p}] {b}")


def _alarm_watch(conn, scenario_id: str) -> tuple:
    """(rows, problem): the PRODUCTION funds on a watched ALARM with their weights (FND-0289), or the reason they could not be read."""
    from shared.config import ALERT_RULES, ALARM_WATCH_RULES
    from proyecto3.src import scenario_lifecycle as lc
    try:
        return lc.load_alarm_watch(conn, scenario_id, lc.alarm_watch_keys(ALERT_RULES, ALARM_WATCH_RULES)), None
    except Exception as exc:                                     # noqa: BLE001 -- the gate fails closed on an unreadable store
        conn.rollback()
        return [], f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"


def _data_suspects(conn, isins) -> tuple:
    """(suspects, problem) of the scenario's funds for the D-DATA-SUSPECT gate (FND-0394): [(isin, reason)] the shared suspicion predicate flags and no verified-genuine exemption covers,
    or the reason the panel could not be read (the gate then fails closed)."""
    from shared.data_suspect import load_suspect_reasons
    try:
        reasons = load_suspect_reasons(conn)
        return sorted((i, reasons[i]) for i in isins if i in reasons), None
    except Exception as exc:                                     # noqa: BLE001 -- the gate fails closed on an unreadable panel
        conn.rollback()
        return None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"


_LINEAGE_LABELS = {"audit": "DRAFT vs auditoria de evidencia (universo)", "universe_now": "DRAFT vs ahora (universo)",
                   "scenario_now": "DRAFT vs ahora (fondos de la cartera)", "identity": "DRAFT vs auditoria de evidencia (codigo y configuracion)"}


def _lineage(conn, scenario_id: str, isins, header) -> tuple:
    """(results, problem) of the FND-0306 lineage comparisons: the DRAFT's stamp (notes key `metrics_lineage`) against the evidence audit's manifest and against the
    stored metrics today (shared.governance.lineage.compare_draft). An unreadable lineage returns (None, reason): INCOMPLETE evidence, never a silent skip."""
    import json
    from shared.governance import lineage
    from shared.governance.run_identity import identity_verdict
    try:
        row = conn.execute("SELECT notes FROM portfolio_scenarios WHERE scenario_id = %s", (scenario_id,)).fetchone()
        notes = row[0] if row else None
        if isinstance(notes, str):
            notes = json.loads(notes) if notes.strip() else {}
        stamp = notes.get("metrics_lineage") if isinstance(notes, dict) else None
        build_id = notes.get("build_identity") if isinstance(notes, dict) else None
        now_universe, now_scenario = lineage.compute(conn), lineage.compute(conn, list(isins))
    except Exception as exc:                                     # noqa: BLE001 -- an unreadable lineage is reported, and refuses when the gate is ON
        conn.rollback()
        return None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
    results = lineage.compare_draft(stamp, (header or {}).get("manifest"), now_universe, now_scenario)
    results["identity"] = identity_verdict(build_id, header)                                      # the code and configuration the audit ran under
    return results, None


def _print_lineage(results, problem, enforced: bool) -> None:
    mode = "puerta" if enforced else "sombra: PROMOTION_AUDIT_IDENTITY_GATE_ENABLED apagado, no bloquea"
    print(f"Linaje de metricas D-LINEAGE ({mode}):" + (f" NO LEGIBLE ({problem})" if problem else ""))
    for key, label in _LINEAGE_LABELS.items():
        if results:
            state, detail = results[key]
            print(f"    {label}: {state} - {detail}")


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
    from proyecto3.src.scorer_config import assert_no_overlay
    assert_no_overlay("p3_promote_scenario")   # FND-0303: never promote with an experiment's parameter overlay active

    from shared.config import (ALARM_WATCH_GATE_ENABLED, ALARM_WATCH_WEIGHT_TOLERANCE, AUDIT_EXCEPTION_EVENTS_ENABLED, DATA_SUSPECT_ENABLED,
                               AUDIT_RESIDUAL_EXPIRY_WARN_DAYS, CODE_INTEGRITY_GATE_ENABLED, COST_DOMAIN_GATE_ENABLED,
                               GOVERNANCE_ORCHESTRATOR_ENABLED, MAX_NON_EUR_UNHEDGED_WEIGHT, MAX_PORTFOLIO_CASH_WEIGHT,
                               PROMOTION_AUDIT_IDENTITY_GATE_ENABLED)
    from shared.governance import orchestrator
    from shared.governance.run_identity import code_integrity, config_hash
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
        exceptions_problem, exception_sources = _governed_exceptions(conn, AUDIT_EXCEPTION_EVENTS_ENABLED)

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
        try:                                                     # FND-0394: verified-genuine exemptions about to expire / in their grace build / lapsed (informative, never blocks)
            from datetime import date as _date
            from shared import config as _cfg_ex
            from shared.data_exemption import attention_lines as _exemption_lines, load_build_dates
            for line in _exemption_lines(_cfg_ex.DATA_SUSPECT_EXEMPTIONS, _date.today(), load_build_dates(conn), _cfg_ex.DATA_SUSPECT_EXEMPTION_WARN_DAYS):
                print(f"AVISO: {line}")
        except Exception as exc:                                 # noqa: BLE001
            print(f"AVISO: no se pudieron leer las exenciones verificadas ({type(exc).__name__}: {exc})")

        reason = (args.accept_alarms or "").strip()
        ap_id = (args.ap or "").strip().upper()
        ap_exists = _ap_exists(ap_id) if reason and lc.AP_RE.match(ap_id) else None
        if reason and lc.AP_RE.match(ap_id) and ap_exists is None:
            print(f"AVISO: no se pudo comprobar {ap_id} en gestion.backlog (FONDOS_BACKLOG_PG_DSN no definida o sin conexion): "
                  "se acepta por formato")

        watch, watch_problem = _alarm_watch(conn, args.scenario_id)
        rising = lc.alarm_watch_increases(watch, ALARM_WATCH_WEIGHT_TOLERANCE)
        mode = "puerta" if ALARM_WATCH_GATE_ENABLED else "sombra: ALARM_WATCH_GATE_ENABLED apagado, no bloquea"
        print(f"Vigilancia de alarmas en PRODUCCION ({mode}): "
              + (f"NO LEGIBLE ({watch_problem})" if watch_problem else
                 f"{len(watch)} fondo(s) vigilado(s), {len(rising)} sube(n) de peso en el DRAFT"))
        for r in watch:
            draft = "fuera del DRAFT" if r.draft_weight is None else f"DRAFT {r.draft_weight:.4f}"
            print(f"    {r.isin} [{r.rules}]: PRODUCCION {r.prod_weight:.4f} -> {draft}{'  << SUBE' if r in rising else ''}")

        evidence_header = _evidence_header(conn, scope.run_id)
        lineage_results, lineage_problem = _lineage(conn, args.scenario_id, weights.keys(), evidence_header)     # FND-0306: always computed and printed
        _print_lineage(lineage_results, lineage_problem, PROMOTION_AUDIT_IDENTITY_GATE_ENABLED)
        if not PROMOTION_AUDIT_IDENTITY_GATE_ENABLED and lineage_results and not args.dry_run:   # SHADOW: one trace per real attempt; a dry run writes nothing
            try:
                lc.record_lineage_shadow(conn, args.scenario_id, getpass.getuser(), lineage_results)
            except Exception as exc:                             # noqa: BLE001 -- the shadow trace never decides anything
                print(f"AVISO: no se pudo registrar la linea de sombra del linaje en ingestion_log: {exc}")

        gate_kw = dict(status=status, n_funds=len(weights), weight_sum=sum(weights.values()), stale_checks=stale,
                       exposure_non_eur=expo["non_eur"], budget=MAX_NON_EUR_UNHEDGED_WEIGHT, audit=scope, built_at=built_at,
                       accept_alarms=reason, accept_ap=ap_id, ap_exists=ap_exists, declared_cash=cash, max_cash=MAX_PORTFOLIO_CASH_WEIGHT,
                       exceptions_problem=exceptions_problem)
        if ALARM_WATCH_GATE_ENABLED:
            gate_kw.update(alarm_watch=watch, alarm_watch_problem=watch_problem, alarm_watch_tolerance=ALARM_WATCH_WEIGHT_TOLERANCE)
        if DATA_SUSPECT_ENABLED:                                 # FND-0394 (ships OFF): nothing is read or printed while the switch is off
            suspects, suspect_problem = _data_suspects(conn, weights.keys())
            print("Sospecha de datos en la cartera (D-DATA-SUSPECT): "
                  + (f"NO LEGIBLE ({suspect_problem})" if suspect_problem else f"{len(suspects)} fondo(s) sospechoso(s) sin exencion de genuino verificado"))
            for isin, why in (suspects or []):
                print(f"    {isin}: {why}")
            gate_kw.update(data_suspects=suspects, data_suspect_problem=suspect_problem)
        if PROMOTION_AUDIT_IDENTITY_GATE_ENABLED:                # FND-0306 (ships OFF): ON uses the DRAFT-vs-audit verdict; an unreadable lineage refuses (fail closed)
            unreadable = ("UNKNOWN", f"no se pudo leer el linaje ({lineage_problem})")
            gate_kw.update(lineage=lineage_results["audit"] if lineage_results else unreadable,
                           identity=lineage_results["identity"] if lineage_results else unreadable)
        legacy = lc.evaluate_promotion(**gate_kw)
        # FND-0265: the two-pillar verdict is always computed and printed; it DECIDES only with GOVERNANCE_ORCHESTRATOR_ENABLED (else shadow)
        tight = lc.evaluate_promotion(**gate_kw, weight_tolerance=lc.WEIGHT_TOLERANCE)                     # decision D2
        cost_defects, cost_has_run = _cost_scope(conn, weights.keys())
        two_pillar = orchestrator.authorise_promotion(
            tight.blockers, overridden=tight.overridden, code_state=code_integrity(_ROOT), code_gate_enabled=CODE_INTEGRITY_GATE_ENABLED,
            evidence_header=evidence_header, cost_defects=cost_defects, cost_has_run=cost_has_run,
            cost_gate_enabled=COST_DOMAIN_GATE_ENABLED)
        _print_verdict(two_pillar, decides=GOVERNANCE_ORCHESTRATOR_ENABLED)
        if GOVERNANCE_ORCHESTRATOR_ENABLED:
            blockers = two_pillar.blockers + (list(two_pillar.notes) if two_pillar.blockers else [])
            overridden = list(two_pillar.overridden)
        else:
            blockers, overridden = legacy.blockers, legacy.overridden
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
        if overridden:
            print("\nOVERRIDE (puertas cubiertas por --accept-alarms + --ap):")
            for code, text in overridden:
                print(f"  - [OVERRIDE:{code}] {args.ap}: {text}")
        if args.dry_run:
            print("\nDRY RUN: promovible. Nada cambiado.")
            return RC_OK

        user = getpass.getuser()
        # FND-0263: the note names the code (git), the configuration (hash of the *_ENABLED switches and MAX_* limits), the audit run (whose
        # control.audit_run header holds the catalogue and the data manifest) and the exception grants relied on: one promotion row rebuilds them.
        # FND-0265 (T-EVIDENCE): plus the two-pillar verdict line, whether it decided or ran in shadow.
        note = "; ".join(p for p in [f"non-EUR exposure {expo['non_eur']:.1%}", lc.signoff_line(scope, overridden, ap_id, reason),
                                     _git_state(), f"config {config_hash()}", _exception_ids(scope, exception_sources),
                                     orchestrator.verdict_line(two_pillar)] if p)
        previous = lc.promote(conn, args.scenario_id, user, note)       # promotion + audit row in ONE transaction
        print(f"\nPROMOVIDO: {args.scenario_id} es ahora PRODUCCION" + (f"; {previous} -> ARCHIVED" if previous else ""))
        return RC_OK
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
