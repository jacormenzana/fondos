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


def promotion_blockers(*, status: Optional[str], n_funds: int, weight_sum: float, stale_checks: list,
                       exposure_non_eur: float, budget: float, alarms: int, accept_alarms: Optional[str] = None,
                       declared_cash: float = 0.0, max_cash: Optional[float] = None) -> list:
    """Pure: why a scenario may not be promoted (empty = promotable).

    Two HARD gates without override (owner decisions 2026-10-11): the currency-exposure budget (`shared.config.MAX_NON_EUR_UNHEDGED_WEIGHT`)
    and the declared structural cash (`MAX_PORTFOLIO_CASH_WEIGHT`) -- the mandate changes by a reviewed config commit, not at promotion time.
    The weights must add up to 1 INCLUDING the cash the build declared; an undeclared shortfall is an error. Only the audit-ALARM gate takes
    a reasoned `accept_alarms`, which the caller logs."""
    if max_cash is None:
        from shared.config import MAX_PORTFOLIO_CASH_WEIGHT as max_cash
    out = []
    if status is None:
        out.append("el escenario no existe o el ciclo de vida no esta instalado")
    elif status != STATUS_DRAFT:
        out.append(f"el escenario esta en {status}: solo un DRAFT se promueve")
    if n_funds <= 0:
        out.append("el escenario no tiene pesos")
    elif abs(weight_sum + declared_cash - 1.0) > 1e-3:
        out.append(f"los pesos suman {weight_sum:.4f}, no 1.0" if declared_cash <= 0 else
                   f"los pesos suman {weight_sum:.4f} + liquidez declarada {declared_cash:.4f}, no 1.0")
    if declared_cash > max_cash + 1e-9:
        out.append(f"liquidez estructural declarada {declared_cash:.1%} excede el tope {max_cash:.0%} (puerta dura: sin override; "
                   "faltan fondos elegibles no expuestos: refresque datos, amplie el universo o cambie el tope por commit revisado)")
    if stale_checks:
        out.append("entradas desactualizadas en la puerta de frescura de P3: " + ", ".join(stale_checks))
    if exposure_non_eur > budget + 1e-9:
        out.append(f"exposicion no-EUR {exposure_non_eur:.1%} excede el presupuesto {budget:.0%} (puerta dura: sin override; "
                   "reconstruya la cartera)")
    if alarms > 0 and not (accept_alarms or "").strip():
        out.append(f"{alarms} hallazgos ALARM abiertos en la ultima auditoria persistida "
                   "(--accept-alarms \"motivo\" para promover igualmente)")
    return out


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
