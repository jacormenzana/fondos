"""
proyecto3/src/scenario_lifecycle.py -- DRAFT / PRODUCTION / ARCHIVED lifecycle of portfolio scenarios (owner decision 2026-10-11).

Why: the 2026-10-10 release run exposed three coupled defects -- (1) the monthly report was hard-wired to an obsolete scenario, so it
presented a portfolio nobody had just built; (2) the build upserted `cartera_<regime>_<YYYYMM>`, destroying the previous portfolio when
rebuilt in the same month (no baseline, no rotation, no audit trail); (3) nothing distinguished the official portfolio from a test run.

Rules:
  * the builder only ever writes DRAFT, under a unique id (`build_scenario_id`), and never replaces anything that is not a DRAFT;
  * ONE scenario is PRODUCTION (DB-enforced by a partial unique index); promotion is an explicit owner command with gates;
  * the report and the rotation plan read PRODUCTION (an explicit `--scenario` is allowed but is flagged on the report);
  * until the owner applies the DDL (db/pg/30_gold.sql block `scenario_lifecycle`, scripts/ops/migrate_portfolio_scenario_status.py)
    the status column does not exist: ids are still unique (so nothing is overwritten) and the report FAILS CLOSED unless a scenario
    is given explicitly.

Pure helpers + small DB functions (R-7: no pipeline imports).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

STATUS_DRAFT = "DRAFT"
STATUS_PRODUCTION = "PRODUCTION"
STATUS_ARCHIVED = "ARCHIVED"
STATUSES = (STATUS_DRAFT, STATUS_PRODUCTION, STATUS_ARCHIVED)

# Exit code of the tools when there is no scenario to report / promote (tool range 1-99; P3_EXIT_STALE_INPUTS = 2).
EXIT_NO_SCENARIO = 3
STEP_PROMOTED = "P3_SCENARIO_PROMOTED"
STEP_BLOCKED = "P3_SCENARIO_PROMOTION_BLOCKED"


class ScenarioLifecycleError(RuntimeError):
    """Base class: the caller prints the message and exits EXIT_NO_SCENARIO."""


class NoProductionScenario(ScenarioLifecycleError):
    pass


class ScenarioExists(ScenarioLifecycleError):
    pass


def build_scenario_id(regime: str, when: Optional[datetime] = None) -> str:
    """`cartera_<regime>_<YYYYMM>_b<YYYYMMDDHHMMSS>`: unique per build, sortable, still starts with the legacy `cartera_<regime>_<YYYYMM>`."""
    when = when or datetime.now()
    slug = re.sub(r"[^a-z0-9]+", "_", (regime or "desconocido").lower()).strip("_") or "desconocido"
    return f"cartera_{slug}_{when:%Y%m}_b{when:%Y%m%d%H%M%S}"


def has_status_column(conn) -> bool:
    """True once the owner applied the lifecycle DDL. Resolved through the search_path like every unqualified query of the P3 code."""
    row = conn.execute(
        "SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass('portfolio_scenarios') "
        "AND attname = 'status' AND NOT attisdropped"
    ).fetchone()
    return row is not None


def get_status(conn, scenario_id: str) -> Optional[str]:
    """Status of a scenario; None = the scenario does not exist OR the lifecycle DDL is not applied (use has_status_column to tell)."""
    if not has_status_column(conn):
        return None
    row = conn.execute("SELECT status FROM portfolio_scenarios WHERE scenario_id = %s", (scenario_id,)).fetchone()
    return row[0] if row else None


def scenario_exists(conn, scenario_id: str) -> bool:
    return conn.execute("SELECT 1 FROM portfolio_scenarios WHERE scenario_id = %s", (scenario_id,)).fetchone() is not None


def production_scenario_id(conn, exclude_id: Optional[str] = None) -> Optional[str]:
    if not has_status_column(conn):
        return None
    row = conn.execute(
        "SELECT scenario_id FROM portfolio_scenarios WHERE status = 'PRODUCTION' AND scenario_id <> COALESCE(%s, '')",
        (exclude_id,)).fetchone()
    return row[0] if row else None


def latest_drafts(conn, limit: int = 3) -> list:
    """Newest DRAFT ids first, by `built_at` (timestamptz, part of the lifecycle DDL block: `created_at` is a DATE, so two builds of one day tie)."""
    if not has_status_column(conn):
        return []
    return [r[0] for r in conn.execute(
        "SELECT scenario_id FROM portfolio_scenarios WHERE status = 'DRAFT' "
        "ORDER BY built_at DESC, scenario_id DESC LIMIT %s", (limit,)).fetchall()]


def latest_draft_id(conn) -> Optional[str]:
    """The newest DRAFT (what the cycle just built) or None; the report launcher's `--draft` resolves it explicitly, never implicitly."""
    drafts = latest_drafts(conn, 1)
    return drafts[0] if drafts else None


def resolve_report_scenario(conn, explicit: Optional[str] = None) -> tuple:
    """(scenario_id, is_production). Never falls back silently.

    explicit given  -> it must exist; is_production tells the report whether to print the NO PRODUCCION banner.
    explicit absent -> the PRODUCTION scenario; none (or DDL not applied) -> NoProductionScenario with the way out in the message."""
    if explicit:
        if not scenario_exists(conn, explicit):
            raise NoProductionScenario(f"el escenario '{explicit}' no existe en portfolio_scenarios")
        return explicit, get_status(conn, explicit) == STATUS_PRODUCTION
    if not has_status_column(conn):
        raise NoProductionScenario(
            "el ciclo de vida de escenarios no esta instalado (falta portfolio_scenarios.status): el owner debe aplicar "
            "scripts/ops/migrate_portfolio_scenario_status.py --apply; mientras tanto indique el escenario con --scenario ID")
    prod = production_scenario_id(conn)
    if prod is None:
        drafts = latest_drafts(conn)
        hint = f" Borradores recientes: {', '.join(drafts)}." if drafts else ""
        raise NoProductionScenario(
            "no hay ningun escenario en PRODUCCION: promueva uno con scripts\\launch\\p3_promote_scenario.py <id> "
            f"o indique --scenario ID (el informe llevara el aviso NO PRODUCCION).{hint}")
    return prod, True


def parse_declared_cash(notes: Optional[str]) -> float:
    """Structural cash the build declared in the scenario notes (`cash_weight`, share of the master portfolio); 0.0 when absent or unreadable
    (scenarios built before the declaration existed). Pure."""
    try:
        value = float(json.loads(notes or "{}").get("cash_weight", 0.0))
    except (TypeError, ValueError, AttributeError):
        return 0.0
    return value if value > 0 else 0.0


def declared_cash(conn, scenario_id: str) -> float:
    row = conn.execute("SELECT notes FROM portfolio_scenarios WHERE scenario_id = %s", (scenario_id,)).fetchone()
    return parse_declared_cash(row[0] if row else None)


# Stable gate codes (FND-0251): every blocker and override line starts with one, so the refusals in ingestion_log can be counted per gate
# with a plain LIKE instead of parsing prose (KPI source). The text after the code is free.
GATE_STATUS, GATE_WEIGHTS, GATE_CASH, GATE_FRESHNESS, GATE_FX = "STATUS", "WEIGHTS", "CASH", "FRESHNESS", "FX"
GATE_AUDIT_STALE, GATE_DATA_COVERAGE = "AUDIT_STALE", "DATA_COVERAGE"
GATE_DATA_SCENARIO, GATE_RESIDUAL_SCENARIO, GATE_DATA_UNIVERSE = "DATA_SCENARIO", "RESIDUAL_SCENARIO", "DATA_UNIVERSE"
GATE_OVERRIDE_AP = "OVERRIDE_AP"
GATE_EXCEPTIONS = "EXCEPTIONS"                 # FND-0263: the accepted-residual history cannot be read or disagrees with its projection
AP_RE = re.compile(r"^FND-\d{4}$")
# Gates a reasoned override (--accept-alarms "motivo" --ap FND-nnnn) may cover: defects ON the scenario and the audit's blind spots.
# Everything else is hard: exposure, cash, weights, freshness, a stale audit, and universe-only defects (their way out is an accepted
# residual with its own AP and expiry, not a promotion flag).
OVERRIDABLE_GATES = frozenset({GATE_DATA_SCENARIO, GATE_RESIDUAL_SCENARIO, GATE_DATA_COVERAGE})


def _names(items, limit: int = 6) -> str:
    items = list(items)
    return ", ".join(items[:limit]) + (f" y {len(items) - limit} mas" if len(items) > limit else "")


@dataclass
class PromotionVerdict:
    """`blockers`: what still stops the promotion (each starts with its [GATE:CODE]). `overridden`: [(code, text)] gates the reasoned
    override covered -- the caller logs them as [OVERRIDE:<code>]; empty when no override was needed or accepted."""
    blockers: list = field(default_factory=list)
    overridden: list = field(default_factory=list)


def evaluate_promotion(*, status: Optional[str], n_funds: int, weight_sum: float, stale_checks: list,
                       exposure_non_eur: float, budget: float, audit, built_at=None,
                       accept_alarms: Optional[str] = None, accept_ap: Optional[str] = None, ap_exists: Optional[bool] = None,
                       declared_cash: float = 0.0, max_cash: Optional[float] = None,
                       exceptions_problem: Optional[str] = None) -> PromotionVerdict:
    """Pure: why a scenario may not be promoted (empty `blockers` = promotable) and which gates a reasoned override covered.

    HARD gates, no override (owner decisions 2026-10-11): the currency-exposure budget (`shared.config.MAX_NON_EUR_UNHEDGED_WEIGHT`) and the
    declared structural cash (`MAX_PORTFOLIO_CASH_WEIGHT`) -- the mandate changes by a reviewed config commit, not at promotion time. The
    weights must add up to 1 INCLUDING the cash the build declared.

    Data gates (FND-0251), from `audit` (shared.statistical_audit.gating.AuditScope of the latest persisted p2_metrics run):
      * AUDIT_STALE       no persisted run, or older than the DRAFT build: the evidence does not describe this scenario. No override.
      * DATA_SCENARIO     an open hard defect touches a fund of the scenario (or cannot be placed). Reasoned override with an AP.
      * RESIDUAL_SCENARIO an ACTIVE accepted residual sits on a fund of the scenario: a residual is tolerated in the universe, never in
                          a portfolio. Reasoned override with an AP.
      * DATA_COVERAGE     funds of the scenario the audit did not check (held out of the snapshot, outside the audited population), or
                          coverage UNKNOWN. Reasoned override with an AP.
      * DATA_UNIVERSE     an open hard defect outside the scenario that no active residual covers. No override: accept it with
                          scripts/audit/accept_audit_residual.py (AP + expiry), which is its own audited step.
    Statistical anomalies on the scenario's funds are information (the selection picks score extremes), never a blocker.

    `accept_alarms` is the reason and `accept_ap` the owning ticket (FND-nnnn); `ap_exists` is the backlog lookup (None = could not be
    checked: a warning for the caller, not a blocker; False = the ticket does not exist: the override is refused).

    `exceptions_problem` (FND-0263, only with shared.config.AUDIT_EXCEPTION_EVENTS_ENABLED): why the accepted-residual history cannot be
    trusted (unreadable, or the projection disagrees with it). EXCEPTIONS is hard, no override: the residuals the gate just read may be
    wrong, so neither DATA_UNIVERSE nor RESIDUAL_SCENARIO can be evaluated."""
    if max_cash is None:
        from shared.config import MAX_PORTFOLIO_CASH_WEIGHT as max_cash
    hard: list = []
    soft: list = []                                              # (code, text): what the reasoned override can cover

    def g(code: str, text: str) -> str:
        return f"[GATE:{code}] {text}"

    if status is None:
        hard.append(g(GATE_STATUS, "el escenario no existe o el ciclo de vida no esta instalado"))
    elif status != STATUS_DRAFT:
        hard.append(g(GATE_STATUS, f"el escenario esta en {status}: solo un DRAFT se promueve"))
    if n_funds <= 0:
        hard.append(g(GATE_WEIGHTS, "el escenario no tiene pesos"))
    elif abs(weight_sum + declared_cash - 1.0) > 1e-3:
        hard.append(g(GATE_WEIGHTS, f"los pesos suman {weight_sum:.4f}, no 1.0" if declared_cash <= 0 else
                      f"los pesos suman {weight_sum:.4f} + liquidez declarada {declared_cash:.4f}, no 1.0"))
    if declared_cash > max_cash + 1e-9:
        hard.append(g(GATE_CASH, f"liquidez estructural declarada {declared_cash:.1%} excede el tope {max_cash:.0%} (puerta dura: sin override; "
                      "faltan fondos elegibles no expuestos: refresque datos, amplie el universo o cambie el tope por commit revisado)"))
    if stale_checks:
        hard.append(g(GATE_FRESHNESS, "entradas desactualizadas en la puerta de frescura de P3: " + ", ".join(stale_checks)))
    if exposure_non_eur > budget + 1e-9:
        hard.append(g(GATE_FX, f"exposicion no-EUR {exposure_non_eur:.1%} excede el presupuesto {budget:.0%} (puerta dura: sin override; "
                      "reconstruya la cartera)"))

    # --- data gates --------------------------------------------------------------------------------------------------------------
    if exceptions_problem:
        hard.append(g(GATE_EXCEPTIONS, f"el historial de residuales aceptados no es fiable (sin override): {exceptions_problem}"))
    if not audit.has_run:
        why = getattr(audit, "selection_note", "")
        hard.append(g(GATE_AUDIT_STALE, "no hay ninguna auditoria persistida del dominio p2_metrics (sin override): ejecute "
                      "run_statistical_audit.py --domain p2 --persist" + (f" [{why}]" if why else "")))
    elif built_at is not None and audit.run_ts is not None and audit.run_ts < built_at:
        hard.append(g(GATE_AUDIT_STALE, f"la ultima auditoria persistida ({audit.run_id}, {audit.run_ts:%Y-%m-%d %H:%M}) es anterior a la "
                      f"construccion del DRAFT ({built_at:%Y-%m-%d %H:%M}): no describe esta cartera (sin override). Ejecute "
                      "run_statistical_audit.py --domain p2 --persist"))
    if audit.has_run:
        for d in audit.on_scenario:
            who = (f"{len(d.isins)} fondos de la cartera: {_names(d.isins)}" if d.isins
                   else "sin ISIN atribuible: se asume que toca la cartera")
            soft.append((GATE_DATA_SCENARIO, g(GATE_DATA_SCENARIO, f"defecto duro {d.rule_id} abierto, {who} ({d.evidence[:140]})")))
        for rule_id, isin, ap_id, expires_at in audit.residuals_on_scenario:
            soft.append((GATE_RESIDUAL_SCENARIO, g(GATE_RESIDUAL_SCENARIO,
                         f"residual aceptado {rule_id} sobre {isin} ({ap_id}, hasta {expires_at}) y el fondo esta en la cartera: un "
                         "residual se tolera en el universo, no en una cartera")))
        if audit.coverage != "OK":
            soft.append((GATE_DATA_COVERAGE, g(GATE_DATA_COVERAGE, f"cobertura de la auditoria DESCONOCIDA: {audit.coverage_detail}")))
        if audit.held_out_on_scenario:
            soft.append((GATE_DATA_COVERAGE, g(GATE_DATA_COVERAGE, f"{len(audit.held_out_on_scenario)} fondos de la cartera quedaron fuera de los "
                         f"bloques transversales de la auditoria (NAV desfasado): {_names(audit.held_out_on_scenario)}")))
        if audit.outside_population:
            soft.append((GATE_DATA_COVERAGE, g(GATE_DATA_COVERAGE, f"{len(audit.outside_population)} fondos de la cartera ya no estan en el "
                         f"universo auditado (In_Current_Universe != 1): {_names(audit.outside_population)}")))
        for d in audit.universe_only:
            hard.append(g(GATE_DATA_UNIVERSE, f"defecto duro {d.rule_id} abierto fuera de la cartera, {len(d.isins)} fondos "
                          f"({_names(d.isins)}) sin residual aceptado vigente (sin override): accept_audit_residual.py --domain p2 "
                          f"--rule {d.rule_id} --ap FND-nnnn --reason \"...\" --apply"))

    verdict = PromotionVerdict(blockers=hard)
    if not soft:
        return verdict
    reason = (accept_alarms or "").strip()
    ap = (accept_ap or "").strip().upper()
    if reason and AP_RE.match(ap) and ap_exists is not False:
        verdict.overridden = list(soft)
    else:
        verdict.blockers.extend(text for _code, text in soft)
        if reason and not AP_RE.match(ap):
            verdict.blockers.append(g(GATE_OVERRIDE_AP, "--accept-alarms exige --ap FND-nnnn (el ticket que corrige la causa): sin el, "
                                      "el override no se aplica"))
        elif reason and ap_exists is False:
            verdict.blockers.append(g(GATE_OVERRIDE_AP, f"--ap {ap} no existe en gestion.backlog: el override no se aplica"))
        else:
            verdict.blockers.append("(--accept-alarms \"motivo\" --ap FND-nnnn cubre los defectos y huecos de cobertura de la CARTERA)")
    return verdict


def promotion_blockers(**kw) -> list:
    """Thin wrapper over evaluate_promotion for callers that only need the refusal list."""
    return evaluate_promotion(**kw).blockers


def signoff_line(audit, overridden: list, ap: Optional[str] = None, reason: Optional[str] = None) -> str:
    """One machine-readable line for the promotion note: what the data gate looked at and what it let through. The audit trail of a
    promotion (ingestion_log, step P3_SCENARIO_PROMOTED) carries it, so 'what was accepted' is in the record, not in a chat."""
    parts = [f"audit {audit.run_id or 'none'}",
             f"defects on scenario {sum(len(d.isins) or 1 for d in audit.on_scenario)}",
             f"universe-only open {sum(len(d.isins) for d in audit.universe_only)}",
             f"anomalies on scenario {len(audit.anomalies_on_scenario)} (informational)"]
    if audit.covered_by_residual:
        aps = sorted({r[2] for r in audit.covered_by_residual})
        until = min(r[3] for r in audit.covered_by_residual)
        parts.append(f"covered by residual {len({r[1] for r in audit.covered_by_residual})} ISINs ({', '.join(aps)}, until {until})")
    for code, _text in overridden:
        parts.append(f"[OVERRIDE:{code}] {ap}: {(reason or '').strip()}")
    return "; ".join(parts)


def record_blocked(conn, scenario_id: str, username: str, blockers: list) -> None:
    """Audit row for a refused promotion (ONE `ingestion_log` row, step P3_SCENARIO_PROMOTION_BLOCKED): a refusal leaves a trace.
    Commits only its own insert; never raises into the caller's refusal path (the refusal stands whether or not the row is written)."""
    message = f"promotion of scenario {scenario_id} refused for {username}: " + " | ".join(blockers)
    try:
        conn.execute("INSERT INTO ingestion_log (isin, step, status, message, created_at) VALUES (NULL, %s, 'BLOCKED', %s, now())",
                     (STEP_BLOCKED, message[:4000]))
        conn.commit()
    except Exception:                                        # noqa: BLE001 -- the audit write must not mask the refusal
        try:
            conn.rollback()
        except Exception:                                    # noqa: BLE001
            pass
        raise


def promote(conn, scenario_id: str, username: str, note: str = "") -> Optional[str]:
    """DRAFT -> PRODUCTION in ONE transaction: the previous PRODUCTION becomes ARCHIVED and is recorded in `supersedes`, and the audit row
    (control.ingestion_log, step P3_SCENARIO_PROMOTED) commits with it -- a promotion that cannot be audited does not happen.
    Returns the superseded id (or None). Raises ScenarioLifecycleError when the scenario is not a DRAFT."""
    try:
        prev = production_scenario_id(conn, exclude_id=scenario_id)
        conn.execute("UPDATE portfolio_scenarios SET status = 'ARCHIVED' WHERE status = 'PRODUCTION'")
        cur = conn.execute(
            "UPDATE portfolio_scenarios SET status = 'PRODUCTION', promoted_at = now(), promoted_by = %s, supersedes = %s "
            "WHERE scenario_id = %s AND status = 'DRAFT'", (username, prev, scenario_id))
        if cur.rowcount != 1:
            raise ScenarioLifecycleError(f"'{scenario_id}' no es un DRAFT existente: no se promueve")
        message = (f"scenario {scenario_id} promoted to PRODUCTION by {username}; supersedes {prev or 'none'}"
                   + (f"; {note}" if note else ""))
        conn.execute("INSERT INTO ingestion_log (isin, step, status, message, created_at) VALUES (NULL, %s, 'OK', %s, now())",
                     (STEP_PROMOTED, message))
        conn.commit()
        return prev
    except Exception:
        conn.rollback()
        raise
