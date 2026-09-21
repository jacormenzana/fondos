# proyecto3/src/portfolio_builder.py
# -*- coding: utf-8 -*-
"""
Constructor de carteras para P3.

Construye la cartera maestra combinando tres sub-carteras
(Defensiva, Equilibrada, Dinamica) con pesos determinados
por el regimen macro actual.

Proceso:
  1. Leer scores de fund_scores para el regimen actual
  2. Seleccionar top N fondos por sub-cartera con diversificacion
  3. Asignar pesos internos por score relativo
  4. Combinar sub-carteras con pesos de regimen
  5. Aplicar restricciones (max peso por fondo, max por gestora)
  6. Persistir en portfolio_scenarios y portfolio_weights

Restricciones de construccion:
  Max fondos por sub-cartera:     10
  Max peso por fondo:             20%
  Min peso por fondo elegible:     3%
  Max fondos misma naturaleza:     5 (en sub-cartera)
  Max fondos por gestora:          2 (conteo, global entre sub-carteras)

  Fase 2a (P3 optimization plan, 2026-09-18): el docstring citaba "Max peso
  por gestora: 30%" pero MAX_WEIGHT_PER_MGR nunca se referenciaba en ningun
  sitio del codigo -- la restriccion real, y la unica que se aplica, es el
  limite de 2 fondos por gestora (conteo, no peso) en
  _select_funds_for_subportfolio(), resuelto en tiempo de SELECCION (antes
  de que existan pesos), por lo que siempre es factible. Se elimina la
  constante fantasma; ver MAX_FUNDS_PER_MGR.

Uso:
    from proyecto3.src.portfolio_builder import PortfolioBuilder
    builder = PortfolioBuilder(conn)
    portfolio = builder.build(regime_result, scenario_id="shock_energia_2026Q1")
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
import json
import sys

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from proyecto3.src.regime_classifier import RegimeResult
from proyecto3.src.portfolio_engine import (
    PortfolioConstraints,
    clamp_and_renormalize,
    select_candidates,
    assign_weights as engine_assign_weights,
)
from shared.config import PORTFOLIO_HYSTERESIS_ENABLED, ROTATION_COST_GATE_ENABLED
from shared.db import is_postgres_connection


# ============================================================
# Constantes de construccion
# ============================================================

MAX_FUNDS_PER_SUB    = 10     # max fondos por sub-cartera
MAX_WEIGHT_PER_FUND  = 0.20   # max 20% en un solo fondo
MIN_WEIGHT_PER_FUND  = 0.03   # min 3% si el fondo entra
MAX_SAME_NATURE      = 5      # max fondos de la misma naturaleza por sub-cartera
MAX_FUNDS_PER_MGR    = 2      # max fondos de una misma gestora -- conteo, global
                               # entre sub-carteras (ver nota Fase 2a arriba)

# Metodo de asignacion de pesos internos
# "score_proportional": pesos proporcionales al score final
# "equal":              pesos iguales entre fondos seleccionados
WEIGHT_METHOD = "score_proportional"

# Histeresis: banda minima que un retador debe superar sobre el score del
# titular para justificar la rotacion. Impide que senales de horizonte corto
# provoquen rotaciones excesivas; las senales deben *confirmar* el cambio.
# Ej.: 0.05 => el retador necesita score >= titular * 1.05 para desplazarlo.
HYSTERESIS_BAND: float = 0.05


# ============================================================
# Dataclasses
# ============================================================

@dataclass
class SubPortfolioAllocation:
    name:        str
    regime_weight: float
    funds:       list[dict] = field(default_factory=list)

    @property
    def n_funds(self) -> int:
        return len(self.funds)

    @property
    def total_weight(self) -> float:
        return sum(f["weight"] for f in self.funds)


@dataclass
class Portfolio:
    scenario_id:     str
    regime:          str
    profile:         str
    sub_portfolios:  list[SubPortfolioAllocation]
    macro_context:   dict = field(default_factory=dict)

    @property
    def all_funds(self) -> list[dict]:
        """Lista de todos los fondos con peso en la cartera maestra."""
        result = []
        for sp in self.sub_portfolios:
            for f in sp.funds:
                master_weight = f["weight"] * sp.regime_weight
                result.append({
                    **f,
                    "subportfolio":   sp.name,
                    "master_weight":  round(master_weight, 4),
                })
        return result

    def summary(self) -> str:
        lines = [
            f"CARTERA: {self.scenario_id}",
            f"Regimen: {self.regime}",
            f"{'='*60}",
        ]
        for sp in self.sub_portfolios:
            lines.append(f"\n{sp.name} ({sp.regime_weight:.0%} de la cartera):")
            for f in sorted(sp.funds, key=lambda x: -x["weight"]):
                lines.append(
                    f"  {f['isin']:14s} {f['fund_nature']:22s} "
                    f"peso_sub={f['weight']:.1%} "
                    f"peso_master={f['weight']*sp.regime_weight:.1%}"
                )
        lines.append(f"\n{'='*60}")
        lines.append(f"Total fondos: {sum(sp.n_funds for sp in self.sub_portfolios)}")
        return "\n".join(lines)


# ============================================================
# Seleccion de fondos por sub-cartera
# ============================================================

def _select_funds_for_subportfolio(
    conn: sqlite3.Connection,
    subportfolio: str,
    score_version: str,
    regime: str,
    max_funds: int = MAX_FUNDS_PER_SUB,
    exclude_isins: set | None = None,
    exclude_names: set | None = None,
    mgr_global: dict | None = None,
    incumbent_isins: frozenset | None = None,
) -> pd.DataFrame:
    """
    Selecciona los mejores fondos para una sub-cartera aplicando
    restricciones de diversificacion.
    exclude_isins:   ISINs ya usados globalmente.
    exclude_names:   nombres base ya usados globalmente.
    mgr_global:      conteo global de fondos por gestora.
    incumbent_isins: ISINs que estaban en esta sub-cartera en el periodo
                     anterior. Reciben un bonus de HYSTERESIS_BAND sobre
                     su score para evitar rotaciones innecesarias.

    Fase 4 (P3 optimization plan, 2026-09-18): adaptador fino sobre DB --
    la logica de diversificacion (antes inline aqui) vive ahora en
    portfolio_engine.select_candidates(), compartida con
    Backtester._build_weights_for_regime(). Ver ese modulo para el porque
    (P#11: dos implementaciones independientes de "construir una cartera"
    significaba que el backtest nunca probaba la cartera que
    PortfolioBuilder realmente construye).

    Fase 3a (P3 optimization plan, migracion SQLite 2026-09-19): fund_scores
    ahora acumula historia (PK extendida a isin/block/score_version/regime/
    as_of_date -- ver shared/migrate_schema_v27.py), asi que puede haber
    VARIAS filas por fondo. `regime` filtra a las puntuaciones calculadas
    especificamente BAJO ese regimen (los multiplicadores de Capa 3 dependen
    del regimen activo -- una puntuacion de Shock_Energetico no es
    intercambiable con una de Crisis_Financiera), y se toma la mas reciente
    por as_of_date via ROW_NUMBER(), replicando exactamente el patron de
    consulta que idx_scores_latest (misma forma que gold.idx_scores_latest
    en db/pg/30_gold.sql) esta pensado para servir.
    """
    # Cargar scores elegibles para esta sub-cartera, filtrados al regimen
    # dado y a la fila mas reciente por fondo (fund_family_id puede ser
    # NULL para fondos no procesados por family_builder).
    #
    # IMPORTANTE: eligible/score_total>0 se filtran DESPUES de resolver
    # rn=1, no dentro del CTE `latest`. Filtrarlos dentro del CTE (antes de
    # que ROW_NUMBER() calcule el ranking) descartaria la fila MAS RECIENTE
    # de un fondo si esa fila resulta ser inelegible -- y ROW_NUMBER()
    # asignaria entonces rn=1 a una fila ELEGIBLE mas ANTIGUA, resucitando
    # en silencio una puntuacion obsoleta para un fondo que hoy esta
    # excluido. Bug real, encontrado en el smoke test en vivo de esta
    # migracion (2026-09-19): un fondo marcado 'Credit_Quality=High Yield
    # excluido de Defensiva' HOY seguia siendo seleccionado con su
    # puntuacion elegible de Marzo, porque el filtro de eligible estaba
    # dentro del CTE.
    ph = "%s" if is_postgres_connection(conn) else "?"
    rows = conn.execute(f"""
        WITH latest AS (
            SELECT fs.isin, fs.score_total, fs.eligible,
                   ROW_NUMBER() OVER (
                       PARTITION BY fs.isin, fs.block, fs.score_version
                       ORDER BY fs.as_of_date DESC
                   ) AS rn
            FROM fund_scores fs
            WHERE fs.block = {ph}
              AND fs.score_version = {ph}
              AND fs.regime = {ph}
        )
        SELECT latest.isin, latest.score_total,
               fm.Fund_Name, fm.Fund_Nature, fm.Management_Company,
               fm.fund_family_id
        FROM latest
        JOIN fund_master fm ON fm.ISIN = latest.isin
        WHERE latest.rn = 1
          AND latest.eligible = 1
          AND latest.score_total > 0
          AND fm.In_Current_Universe = 1
        ORDER BY latest.score_total DESC
    """, (subportfolio, score_version, regime)).fetchall()

    if not rows:
        return pd.DataFrame()

    candidates = pd.DataFrame(rows, columns=[
        "isin", "score_total",
        "fund_name", "fund_nature", "management_company",
        "fund_family_id"
    ])

    constraints = PortfolioConstraints(
        max_funds_per_sub=max_funds,
        max_weight_per_fund=MAX_WEIGHT_PER_FUND,
        min_weight_per_fund=MIN_WEIGHT_PER_FUND,
        max_same_nature=MAX_SAME_NATURE,
        max_funds_per_mgr=MAX_FUNDS_PER_MGR,
        hysteresis_band=HYSTERESIS_BAND,
    )
    return select_candidates(
        candidates, constraints,
        exclude_isins=exclude_isins,
        exclude_families=exclude_names,   # reutilizamos el param para familias
        mgr_global=mgr_global,
        incumbent_isins=incumbent_isins,
    )


# ============================================================
# Asignacion de pesos internos
# ============================================================

# Fase 4 (P3 optimization plan, 2026-09-18): _clamp_and_renormalize vive
# ahora en portfolio_engine.py, compartido con Backtester (P#11 -- ambos
# necesitan la misma logica de ponderacion interna para que sus carteras
# respeten las mismas cotas). Re-exportado con el nombre historico para no
# romper proyecto3/tests/test_portfolio_constraints.py, que lo importa
# directamente desde este modulo.
_clamp_and_renormalize = clamp_and_renormalize


def _assign_weights(
    df: pd.DataFrame,
    method: str = WEIGHT_METHOD,
) -> pd.DataFrame:
    """
    Asigna pesos internos a los fondos seleccionados.
    Los pesos suman 1.0 dentro de la sub-cartera, cada uno dentro de
    [MIN_WEIGHT_PER_FUND, MAX_WEIGHT_PER_FUND] siempre que el numero de
    fondos lo permita (ver portfolio_engine.clamp_and_renormalize).

    Fase 4: adaptador fino sobre portfolio_engine.assign_weights().
    """
    constraints = PortfolioConstraints(
        max_weight_per_fund=MAX_WEIGHT_PER_FUND,
        min_weight_per_fund=MIN_WEIGHT_PER_FUND,
    )
    return engine_assign_weights(df, constraints, method=method)


# ============================================================
# Constructor principal
# ============================================================

# ============================================================
# Coste de rotacion
# ============================================================

def estimate_rotation_cost(
    conn: sqlite3.Connection,
    isin_out: str,
    isin_in: str,
) -> float:
    """
    Estima el coste total de rotar de un fondo a otro.
    Coste = comision_salida(fondo_out) + comision_entrada(fondo_in) + spread(0.1%)
    Devuelve el coste como fraccion del capital (ej. 0.015 = 1.5%).

    Fase 2e (P3 optimization plan, 2026-09-18): antes leia exit_fee_pct/
    entry_fee_pct SOLO de rotation_costs, una tabla de 7 filas por
    Fund_Nature ("parche hasta P1 v2" segun su propio comentario de schema)
    donde TODAS las entry_fee_pct son 0.0 -- perdiendo la comision real por
    fondo que ya existe en fund_master.Entry_Fee_Pct/Exit_Fee_Pct (poblada
    para 3.367/3.498 fondos) y nunca se leia. Ahora usa el dato por fondo
    cuando existe, con rotation_costs como fallback por naturaleza, y solo
    si ninguno de los dos existe cae al default explicito -- ahora
    asimetrico (0.5% salida / 0.0% entrada) en vez del 0.5% aplicado a
    ambas patas por igual del codigo anterior, coherente con que
    rotation_costs ya modela las entradas como gratuitas para toda
    naturaleza. redemption_days/min_holding_days (tambien en rotation_costs,
    nunca leidos) quedan para cuando exista as_of_date (Fase 3) y se pueda
    saber cuanto lleva un fondo en cartera.
    """
    SPREAD = 0.001  # coste de mercado estimado
    DEFAULT_EXIT_FEE_PCT  = 0.5   # % -- sin dato alguno (ni por fondo ni por naturaleza)
    DEFAULT_ENTRY_FEE_PCT = 0.0   # % -- coherente con rotation_costs (toda naturaleza a 0)

    ph = "%s" if is_postgres_connection(conn) else "?"

    def _get_exit_fee_pct(isin: str) -> float:
        row = conn.execute(f"""
            SELECT COALESCE(fm.Exit_Fee_Pct, rc.exit_fee_pct, {ph})
            FROM fund_master fm
            LEFT JOIN rotation_costs rc ON rc.fund_nature = fm.Fund_Nature
            WHERE fm.ISIN = {ph}
        """, (DEFAULT_EXIT_FEE_PCT, isin)).fetchone()
        return float(row[0]) if row and row[0] is not None else DEFAULT_EXIT_FEE_PCT

    def _get_entry_fee_pct(isin: str) -> float:
        row = conn.execute(f"""
            SELECT COALESCE(fm.Entry_Fee_Pct, rc.entry_fee_pct, {ph})
            FROM fund_master fm
            LEFT JOIN rotation_costs rc ON rc.fund_nature = fm.Fund_Nature
            WHERE fm.ISIN = {ph}
        """, (DEFAULT_ENTRY_FEE_PCT, isin)).fetchone()
        return float(row[0]) if row and row[0] is not None else DEFAULT_ENTRY_FEE_PCT

    exit_fee  = _get_exit_fee_pct(isin_out) / 100
    entry_fee = _get_entry_fee_pct(isin_in) / 100
    return round(exit_fee + entry_fee + SPREAD, 4)


def should_rotate(
    conn: sqlite3.Connection,
    isin_current: str,
    score_current: float,
    isin_candidate: str,
    score_candidate: float,
    weight: float,
    min_improvement: float = 0.05,
) -> tuple[bool, str]:
    """
    Decide si vale la pena rotar de un fondo a otro.

    La rotacion se justifica si:
        mejora_score > coste_rotacion + min_improvement

    Parametros:
        weight:          peso del fondo en la cartera. NO participa en la
                         comparacion de esta funcion -- tanto el coste
                         (estimate_rotation_cost, un % del capital rotado)
                         como la mejora de score son, por construccion,
                         independientes del tamano de la posicion, asi que
                         no hay un termino weight-dependiente valido que
                         añadir aqui sin inventar una hipotesis de modelado
                         no respaldada. Se conserva como parametro porque
                         rotation_plan() (su unico caller) ya lo tiene
                         disponible por fondo y es donde pertenece un futuro
                         gate de materialidad AGREGADO (a nivel de plan
                         completo, no de par individual) -- Fase 4/7d.
                         (Fase 2d, P3 optimization plan, 2026-09-18: el
                         docstring anterior decia "para estimar impacto"
                         pero el parametro nunca se usaba en el cuerpo; esto
                         corrige la afirmacion en vez de inventar una
                         formula para justificarla.)
        min_improvement: mejora minima requerida sobre el coste (default 5%)

    Devuelve (rotar: bool, razon: str)
    """
    cost = estimate_rotation_cost(conn, isin_current, isin_candidate)
    improvement = score_candidate - score_current

    # La mejora de score se compara contra el coste como fraccion del score
    # Un coste del 1% sobre un score de 0.5 representa un 2% del score
    cost_in_score_units = cost / max(score_current, 0.01)

    if improvement > cost_in_score_units + min_improvement:
        return True, (f"Mejora {improvement:.3f} > coste {cost_in_score_units:.3f} "
                      f"+ umbral {min_improvement:.3f}")
    else:
        return False, (f"Mejora {improvement:.3f} insuficiente vs coste "
                       f"{cost_in_score_units:.3f} + umbral {min_improvement:.3f}")


def rotation_plan(
    conn: sqlite3.Connection,
    portfolio_current: "Portfolio",
    portfolio_new: "Portfolio",
    score_version: str = "v1",
) -> list[dict]:
    """
    Compara dos carteras y genera el plan de rotacion optimo.
    Solo recomienda rotaciones donde el beneficio supera el coste.

    Devuelve lista de operaciones recomendadas:
        [{isin_out, isin_in, subportfolio, weight, coste, razon}]
    """
    # Construir mapas de fondos actuales por sub-cartera
    current_map = {}
    for f in portfolio_current.all_funds:
        current_map[f["subportfolio"]] = current_map.get(f["subportfolio"], {})
        current_map[f["subportfolio"]][f["isin"]] = f

    new_map = {}
    for f in portfolio_new.all_funds:
        new_map[f["subportfolio"]] = new_map.get(f["subportfolio"], {})
        new_map[f["subportfolio"]][f["isin"]] = f

    operations = []

    for sub in set(list(current_map.keys()) + list(new_map.keys())):
        curr_funds = current_map.get(sub, {})
        new_funds  = new_map.get(sub, {})

        # Fondos que salen
        isins_out = set(curr_funds.keys()) - set(new_funds.keys())
        # Fondos que entran
        isins_in  = set(new_funds.keys()) - set(curr_funds.keys())

        for isin_out in isins_out:
            # Buscar el mejor candidato de entrada para reemplazarlo
            best_in   = None
            best_rot  = False
            best_reason = ""
            for isin_in in isins_in:
                score_out = curr_funds[isin_out].get("score", 0)
                score_in  = new_funds[isin_in].get("score", 0)
                weight    = curr_funds[isin_out].get("master_weight", 0.05)
                rotate, reason = should_rotate(
                    conn, isin_out, score_out, isin_in, score_in, weight)
                if rotate:
                    best_in     = isin_in
                    best_rot    = True
                    best_reason = reason
                    break

            cost = estimate_rotation_cost(conn, isin_out,
                                          best_in if best_in else isin_out)
            operations.append({
                "subportfolio": sub,
                "isin_out":     isin_out,
                "isin_in":      best_in,
                "recomendar":   best_rot,
                "coste_est":    cost,
                "razon":        best_reason if best_rot else "Mejora insuficiente",
            })

    return operations


def _apply_rotation_gate(
    conn: sqlite3.Connection,
    tentative: "Portfolio",
    previous: "Portfolio",
    score_version: str,
) -> tuple["Portfolio", list[dict]]:
    """
    Revierte al titular las rotaciones que rotation_plan() no recomienda
    (coste > beneficio), sustituyendo el fondo entrante por el saliente EN
    LA MISMA RANURA (mismo peso interno) -- no vuelve a ejecutar
    _assign_weights ni la logica de diversificacion (dedup por familia/
    gestora), que ya se resolvio sobre el conjunto tentativo.

    Fase 2d (P3 optimization plan, 2026-09-18): antes, should_rotate/
    rotation_plan solo los invocaba scripts/test/test_portfolio.py --
    build()/_persist() nunca los consultaban, asi que las carteras
    persistidas no reflejaban ningun chequeo de coste de rotacion. Los
    datos del titular (score/nombre/naturaleza/gestora) se releen en vivo
    de fund_scores/fund_master en vez de reusar los que trae `previous`
    (que solo tiene isin/weight/role -- portfolio_weights no guarda el
    resto) -- asegura que el titular retenido lleve su score ACTUAL, no el
    del periodo anterior.

    Devuelve (portfolio_resultante, plan_completo) -- plan_completo se
    persiste en macro_context para trazabilidad aunque no haya un informe
    dedicado todavia (el sheet "4_Rotacion" de monthly_report.py no existe
    pese a que su docstring lo cita -- pendiente, fuera del alcance de esta
    fase).
    """
    plan = rotation_plan(conn, previous, tentative)
    reverted = [op for op in plan if not op["recomendar"] and op["isin_in"]]

    for op in reverted:
        sub_name, isin_out, isin_in = op["subportfolio"], op["isin_out"], op["isin_in"]
        sp = next((s for s in tentative.sub_portfolios if s.name == sub_name), None)
        if sp is None:
            continue
        entrant_idx = next(
            (i for i, f in enumerate(sp.funds) if f["isin"] == isin_in), None)
        if entrant_idx is None:
            continue

        _ph = "%s" if is_postgres_connection(conn) else "?"
        row = conn.execute(f"""
            SELECT fs.score_total, fm.Fund_Name, fm.Fund_Nature, fm.Management_Company
            FROM fund_scores fs JOIN fund_master fm ON fm.ISIN = fs.isin
            WHERE fs.isin = {_ph} AND fs.block = {_ph} AND fs.score_version = {_ph}
        """, (isin_out, sub_name, score_version)).fetchone()
        if row is None:
            continue  # titular ya no puntuable (deslistado, etc.) -- se mantiene el entrante

        score, name, nature, mgr = row
        entrant_weight = sp.funds[entrant_idx]["weight"]
        sp.funds[entrant_idx] = {
            "isin": isin_out, "fund_name": name, "fund_nature": nature,
            "gestora": mgr, "fund_family_id": None,
            "score": round(float(score), 4), "weight": entrant_weight,
            "role": f"{sub_name} - {nature} (incumbente retenido, rotacion revertida)",
        }

    return tentative, plan


class PortfolioBuilder:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def load_previous(self, exclude_scenario_id: str | None = None) -> "Portfolio | None":
        """
        Reconstruye la cartera del escenario mas reciente en
        portfolio_scenarios (excluyendo exclude_scenario_id, tipicamente el
        escenario que se esta construyendo ahora), a partir de
        portfolio_weights. Devuelve None si no hay ningun escenario previo.

        Fase 2c (P3 optimization plan, 2026-09-18): antes, el parametro
        previous_portfolio de build() quedaba en None SIEMPRE porque ningun
        caller del repo lo pasaba -- el codigo de histeresis
        (HYSTERESIS_BAND, _select_funds_for_subportfolio) era correcto pero
        nunca se ejecutaba en produccion. Este metodo es lo que permite a
        build() cargarlo por si mismo cuando PORTFOLIO_HYSTERESIS_ENABLED
        esta activo, sin que cada caller tenga que reconstruirlo a mano.
        """
        _ph = "%s" if is_postgres_connection(self.conn) else "?"
        row = self.conn.execute(f"""
            SELECT scenario_id, profile, macro_regime, notes
            FROM portfolio_scenarios
            WHERE scenario_id != COALESCE({_ph}, '')
            ORDER BY created_at DESC
            LIMIT 1
        """, (exclude_scenario_id,)).fetchone()
        if row is None:
            return None

        scenario_id, profile, regime, notes_json = row
        # role -> position_role: reserved word on Postgres (SQL:2003, db/pg/rename_map.yaml).
        # Positional tuple-unpacking below means the SELECT's column NAME doesn't matter, only
        # the reference in FROM must resolve to the real physical column.
        _role_col = "position_role" if is_postgres_connection(self.conn) else "role"
        weight_rows = self.conn.execute(f"""
            SELECT isin, block, weight, {_role_col}
            FROM portfolio_weights
            WHERE scenario_id = {_ph}
        """, (scenario_id,)).fetchall()
        if not weight_rows:
            return None

        sub_map: dict[str, list[dict]] = {}
        for isin, block, weight, role in weight_rows:
            sub_map.setdefault(block, []).append({
                "isin": isin, "weight": weight, "role": role,
            })

        try:
            macro_context = json.loads(notes_json) if notes_json else {}
        except (TypeError, ValueError):
            macro_context = {}

        # regime_weight=1.0 es un placeholder -- load_previous() solo se usa
        # para extraer incumbent_isins (membresia por sub-cartera) y, en el
        # gate de rotacion (Fase 2d), el isin de salida; ningun consumidor
        # actual lee weight_defensive/balanced/dynamic de esta cartera
        # reconstruida.
        sub_portfolios = [
            SubPortfolioAllocation(name=block, regime_weight=1.0, funds=funds)
            for block, funds in sub_map.items()
        ]
        return Portfolio(
            scenario_id=scenario_id, regime=regime or "Desconocido",
            profile=profile or "Equilibrada", sub_portfolios=sub_portfolios,
            macro_context=macro_context,
        )

    def build(
        self,
        regime_result:      RegimeResult,
        scenario_id:        str,
        score_version:      str = "v1",
        profile:            str = "Equilibrada",
        dry_run:            bool = False,
        previous_portfolio: "Portfolio | None" = None,
    ) -> Portfolio:
        """
        Construye la cartera maestra para el regimen dado.

        Parametros:
            regime_result:      resultado del clasificador de regimen
            scenario_id:        identificador unico del escenario
            score_version:      version de scores a usar
            profile:            perfil de la cartera maestra
            dry_run:            si True, no persiste en BD
            previous_portfolio: cartera del periodo anterior (opcional). Cuando
                                se pasa, los fondos ya seleccionados en cada
                                sub-cartera reciben el bonus de HYSTERESIS_BAND
                                sobre su score para evitar rotaciones innecesarias
                                provocadas por senales de horizonte corto de corta
                                duracion. Si es None y PORTFOLIO_HYSTERESIS_ENABLED
                                esta activo, se carga automaticamente via
                                load_previous() (Fase 2c).
        """
        if previous_portfolio is None and PORTFOLIO_HYSTERESIS_ENABLED:
            previous_portfolio = self.load_previous(exclude_scenario_id=scenario_id)

        regime  = regime_result.regime
        weights = regime_result.weights  # {Defensiva: X, Equilibrada: Y, Dinamica: Z}

        sub_portfolios = []
        isins_used    = set()
        families_used = set()   # antes: names_used (3-token) -- ahora: fund_family_id o fallback
        mgr_used      = {}

        for sub_name, regime_weight in weights.items():
            if regime_weight == 0:
                continue

            # -- Histeresis: extraer titulares de la cartera anterior para este bloque
            incumbent_isins: frozenset = frozenset()
            if previous_portfolio is not None:
                for _sp in previous_portfolio.sub_portfolios:
                    if _sp.name == sub_name:
                        incumbent_isins = frozenset(f["isin"] for f in _sp.funds)
                        break

            # Seleccionar fondos (excluyendo los ya usados en sub-carteras anteriores)
            selected = _select_funds_for_subportfolio(
                self.conn, sub_name, score_version, regime,
                exclude_isins=isins_used,
                exclude_names=families_used,
                mgr_global=mgr_used,
                incumbent_isins=incumbent_isins)

            if selected.empty:
                print(f"  AVISO: Sin fondos elegibles para {sub_name}")
                sub_portfolios.append(SubPortfolioAllocation(
                    name=sub_name,
                    regime_weight=regime_weight,
                    funds=[],
                ))
                continue

            # Asignar pesos internos
            selected = _assign_weights(selected)

            funds_list = []
            for _, row in selected.iterrows():
                funds_list.append({
                    "isin":           row["isin"],
                    "fund_name":      row["fund_name"],
                    "fund_nature":    row["fund_nature"],
                    "gestora":        row["management_company"],
                    "fund_family_id": row.get("fund_family_id"),
                    "score":          round(float(row["score_total"]), 4),
                    "weight":         round(float(row["weight"]), 4),
                    "role":           f"{sub_name} - {row['fund_nature']}",
                })

            def _norm_fallback(name: str) -> str:
                return " ".join((name or "").strip().split()[:3]).upper()

            for f in funds_list:
                isins_used.add(f["isin"])
                fam_id = f.get("fund_family_id")
                dedup_key = fam_id if (fam_id and str(fam_id).strip()) \
                            else _norm_fallback(f["fund_name"])
                if dedup_key:
                    families_used.add(dedup_key)
                mgr = f["gestora"] or "Desconocida"
                mgr_used[mgr] = mgr_used.get(mgr, 0) + 1

            sub_portfolios.append(SubPortfolioAllocation(
                name=sub_name,
                regime_weight=regime_weight,
                funds=funds_list,
            ))
            print(f"  {sub_name}: {len(funds_list)} fondos "
                  f"(peso regimen: {regime_weight:.0%})")

        portfolio = Portfolio(
            scenario_id=scenario_id,
            regime=regime,
            profile=profile,
            sub_portfolios=sub_portfolios,
            macro_context={
                "oil_yoy":      regime_result.oil_yoy,
                "ipc_yoy_avg":  regime_result.ipc_yoy_avg,
                "cli_eu":       regime_result.cli_eu,
                "rate_deposit": regime_result.rate_deposit,
                "spread_hy":    regime_result.spread_hy,
                "vix_yoy":      regime_result.vix_yoy,
            },
        )

        # Fase 2d: filtro de coste de rotacion -- revierte al titular las
        # rotaciones no recomendadas antes de persistir. Solo con cartera
        # previa disponible (explicita o auto-cargada arriba via 2c).
        if ROTATION_COST_GATE_ENABLED and previous_portfolio is not None:
            portfolio, rotation_plan_result = _apply_rotation_gate(
                self.conn, portfolio, previous_portfolio, score_version)
            portfolio.macro_context["rotation_plan"] = rotation_plan_result

        if not dry_run:
            self._persist(portfolio, score_version)

        return portfolio

    def _persist(self, portfolio: Portfolio, score_version: str) -> None:
        """Persiste el escenario y los pesos en BD."""
        today = pd.Timestamp.today().strftime("%Y-%m-%d")

        pg = is_postgres_connection(self.conn)

        # Insertar escenario
        if pg:
            self.conn.execute("""
                INSERT INTO portfolio_scenarios
                    (scenario_id, profile, macro_regime, created_at, notes)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (scenario_id) DO UPDATE SET
                    profile = excluded.profile, macro_regime = excluded.macro_regime,
                    created_at = excluded.created_at, notes = excluded.notes
            """, (
                portfolio.scenario_id,
                portfolio.profile,
                portfolio.regime,
                today,
                json.dumps(portfolio.macro_context, ensure_ascii=False),
            ))
        else:
            self.conn.execute("""
                INSERT OR REPLACE INTO portfolio_scenarios
                    (scenario_id, profile, macro_regime, created_at, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (
                portfolio.scenario_id,
                portfolio.profile,
                portfolio.regime,
                today,
                json.dumps(portfolio.macro_context, ensure_ascii=False),
            ))

        # Eliminar pesos anteriores del escenario
        ph = "%s" if pg else "?"
        self.conn.execute(
            f"DELETE FROM portfolio_weights WHERE scenario_id={ph}",
            (portfolio.scenario_id,)
        )

        # Insertar pesos. `role` -> `position_role` en Postgres (palabra reservada SQL:2003,
        # db/pg/rename_map.yaml).
        role_col = "position_role" if pg else "role"
        for f in portfolio.all_funds:
            self.conn.execute(f"""
                INSERT INTO portfolio_weights
                    (scenario_id, isin, block, weight, {role_col}, notes)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            """, (
                portfolio.scenario_id,
                f["isin"],
                f["subportfolio"],
                f["master_weight"],
                f["role"],
                f"score={f['score']} | peso_sub={f['weight']:.1%}",
            ))

        self.conn.commit()
        print(f"Escenario '{portfolio.scenario_id}' persistido en BD.")
