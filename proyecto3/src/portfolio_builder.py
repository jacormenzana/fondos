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

import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
import json
import sys

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from proyecto3.src.regime_classifier import RegimeResult
from proyecto3.src.score_candidates import exposure_constrained, flag_fx_exposed, fx_exposed_map, load_current_candidates, load_run_scores
from proyecto3.src import scenario_lifecycle as _lifecycle
from proyecto3.src.portfolio_engine import (
    PortfolioConstraints,
    clamp_and_renormalize,
    select_within_exposure,
    assign_weights as engine_assign_weights,
    round_master_weights,
    cash_weight,
)
from shared.config import (MAX_NON_EUR_UNHEDGED_WEIGHT, PORTFOLIO_HYSTERESIS_BAND, PORTFOLIO_HYSTERESIS_ENABLED, PORTFOLIO_WEIGHT_METHOD,
                           ROTATION_COST_GATE_ENABLED)


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
# FND-0193 SIMPLIFY (verdict of record 2026-10-19): "equal", from shared.config.PORTFOLIO_WEIGHT_METHOD (the PIT engine default reads the same
# constant). Used for the selection under the FX budget AND the final weights, as the PIT ablation ABL_eqW did.
WEIGHT_METHOD = PORTFOLIO_WEIGHT_METHOD

# Histeresis: banda minima que un retador debe superar sobre el score del
# titular para justificar la rotacion. Impide que senales de horizonte corto
# provoquen rotaciones excesivas; las senales deben *confirmar* el cambio.
# Ej.: 0.10 => el retador necesita score >= titular * 1.10 para desplazarlo. El valor vive en
# shared.config.PORTFOLIO_HYSTERESIS_BAND (FND-0158); aqui solo se re-exporta bajo el nombre historico.
HYSTERESIS_BAND: float = PORTFOLIO_HYSTERESIS_BAND


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
        result, raw = [], []
        for sp in self.sub_portfolios:
            for f in sp.funds:
                raw.append((f["isin"], f["weight"] * sp.regime_weight))
                result.append({**f, "subportfolio": sp.name})
        # FND-0169: largest-remainder rounding so the master weights sum to 1.0000, not 0.9999
        for entry, w in zip(result, round_master_weights(raw)):
            entry["master_weight"] = w
        return result

    @property
    def cash_weight(self) -> float:
        """Residue held as cash when a sub-portfolio cannot reach its regime weight under the per-fund cap
        (FND-0187). 0.0 for every feasible portfolio. Not persisted: portfolio_weights has no cash row, so
        persisted master weights then sum to 1 - cash_weight."""
        return cash_weight([f["master_weight"] for f in self.all_funds])

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
        if self.cash_weight > 0:
            lines.append(f"Liquidez (residuo por tope de peso / sub-cartera sin fondos): {self.cash_weight:.1%}")
        return "\n".join(lines)


# ============================================================
# Seleccion de fondos por sub-cartera
# ============================================================

def _select_funds_for_subportfolio(
    conn: "psycopg.Connection",
    subportfolio: str,
    score_version: str,
    regime: str,
    max_funds: int = MAX_FUNDS_PER_SUB,
    exclude_isins: set | None = None,
    exclude_names: set | None = None,
    mgr_global: dict | None = None,
    incumbent_isins: frozenset | None = None,
    candidates: "pd.DataFrame | None" = None,
) -> pd.DataFrame:
    """
    Selecciona los mejores fondos para una sub-cartera aplicando
    restricciones de diversificacion.
    candidates:      P3-7 (forward shadow of the fee penalty): an explicit candidate frame in
                     score_candidates.CANDIDATE_COLUMNS (all blocks; only this sub-portfolio's rows count)
                     in place of the fund_scores query. None (the default, and what every live build
                     passes) keeps the query: nothing changes while the parameter is not used.
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
    # FND-0171: la consulta de candidatos vive en score_candidates.py, compartida con el
    # Backtester. Solo cuentan las filas de la ULTIMA ejecucion de scoring de este
    # (bloque, version, regimen); una fila mas antigua es un fondo que hoy no se puntua
    # (antes seguia siendo candidato: 2 de 30 fondos de la cartera del 2026-10-01 salian
    # de filas de Marzo). fund_family_id puede ser NULL para fondos sin family_builder.
    if candidates is None:
        candidates = load_current_candidates(conn, score_version, regime, subportfolio)
    else:
        candidates = candidates[candidates["block"] == subportfolio].reset_index(drop=True)
    if candidates.empty:
        return pd.DataFrame()
    candidates = candidates.drop(columns="block")

    # Mandato de divisa (2026-10-11): marcar los candidatos que cuentan contra el presupuesto no-EUR con LA MISMA regla que la puerta de
    # promocion (shared/fx_exposure.py) y limitar el peso expuesto de la sub-cartera (portfolio_engine.select_within_exposure).
    candidates = flag_fx_exposed(conn, candidates)

    constraints = exposure_constrained(PortfolioConstraints(
        max_funds_per_sub=max_funds,
        max_weight_per_fund=MAX_WEIGHT_PER_FUND,
        min_weight_per_fund=MIN_WEIGHT_PER_FUND,
        max_same_nature=MAX_SAME_NATURE,
        max_funds_per_mgr=MAX_FUNDS_PER_MGR,
        hysteresis_band=HYSTERESIS_BAND,
        weight_method=WEIGHT_METHOD,      # FND-0193: the exposure loop weighs the candidates the way the final weights will
    ))
    pool = {
        "candidates": int(len(candidates)),
        "exposed": int(candidates["fx_exposed"].sum()) if "fx_exposed" in candidates.columns else 0,
    }
    selected = select_within_exposure(
        candidates, constraints,
        exclude_isins=exclude_isins,
        exclude_families=exclude_names,   # reutilizamos el param para familias
        mgr_global=mgr_global,
        incumbent_isins=incumbent_isins,
    )
    selected.attrs["pool"] = pool         # for the build-time cash diagnostic (build())
    return selected


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
        weight_method=method,             # FND-0193: engine_assign_weights reads the method from the constraints
    )
    return engine_assign_weights(df, constraints, method=method)


# ============================================================
# Constructor principal
# ============================================================

# ============================================================
# Coste de rotacion
# ============================================================

def fee_fraction(fund_ratio: float | None, nature_pct: float | None, default_pct: float) -> float:
    """A fee as a FRACTION of capital (0.05 == 5 %), from the first source that has one (FND-0409): the per-fund value of `fund_master`
    (a RATIO, used as it is), then the per-nature row of `rotation_costs` (PERCENT, divided by 100), then the explicit default (PERCENT,
    divided by 100). A stored 0.0 is a real zero and wins over the fallbacks; only NULL falls through. Pure."""
    if fund_ratio is not None:
        return float(fund_ratio)
    if nature_pct is not None:
        return float(nature_pct) / 100
    return float(default_pct) / 100


def estimate_rotation_cost(
    conn: "psycopg.Connection",
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

    ph = "%s"

    # FND-0409: the two sources are in DIFFERENT units. fund_master.Entry_Fee_Pct / Exit_Fee_Pct are RATIOS (0.05 == 5 %), rotation_costs.* and the
    # defaults above are PERCENT (1.0 == 1 %). The old COALESCE mixed them and divided the ratio by 100 once too often (a 5 % fee cost 0.05 %).
    # The SQL only reads both columns; the units are resolved in `fee_fraction`.
    def _get_exit_fee_fraction(isin: str) -> float:
        row = conn.execute(f"""
            SELECT fm.Exit_Fee_Pct, rc.exit_fee_pct
            FROM fund_master fm
            LEFT JOIN rotation_costs rc ON rc.fund_nature = fm.Fund_Nature
            WHERE fm.ISIN = {ph}
        """, (isin,)).fetchone()
        return fee_fraction(row[0] if row else None, row[1] if row else None, DEFAULT_EXIT_FEE_PCT)

    def _get_entry_fee_fraction(isin: str) -> float:
        row = conn.execute(f"""
            SELECT fm.Entry_Fee_Pct, rc.entry_fee_pct
            FROM fund_master fm
            LEFT JOIN rotation_costs rc ON rc.fund_nature = fm.Fund_Nature
            WHERE fm.ISIN = {ph}
        """, (isin,)).fetchone()
        return fee_fraction(row[0] if row else None, row[1] if row else None, DEFAULT_ENTRY_FEE_PCT)

    exit_fee  = _get_exit_fee_fraction(isin_out)
    entry_fee = _get_entry_fee_fraction(isin_in)
    return round(exit_fee + entry_fee + SPREAD, 4)


def should_rotate(
    conn: "psycopg.Connection",
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


def _incumbent_run_scores(conn, score_version: str, regime: str, isins) -> dict:
    """Seam over score_candidates.load_run_scores (the tests replace it): incumbent rows of the scoring run the candidates come from."""
    return load_run_scores(conn, score_version, regime, isins)


def _incumbent_attrs(conn, isins) -> dict:
    """{isin: {fund_name, fund_nature, gestora, fund_family_id}} from fund_master, for the incumbents the gate may reinstate."""
    isins = list(isins)
    if not isins:
        return {}
    rows = conn.execute("SELECT ISIN, Fund_Name, Fund_Nature, Management_Company, fund_family_id FROM fund_master WHERE ISIN = ANY(%s)",
                        (isins,)).fetchall()
    return {r[0]: {"fund_name": r[1], "fund_nature": r[2], "gestora": r[3], "fund_family_id": r[4]} for r in rows}


def _fx_flags(conn, isins) -> "dict | None":
    """{isin: exposed?} under the SAME rule as the construction budget and the promotion gate (shared/fx_exposure.py), or None when the budget
    is switched off. Seam over score_candidates.fx_exposed_map (the tests replace it)."""
    return fx_exposed_map(conn, isins)


def _family_key(fund_family_id, fund_name: str):
    """The key under which a fund counts as 'the same family' in the construction: its fund_family_id when it has one, else the first three
    words of its name (upper case). One definition for build() and for the rotation gate (P#11)."""
    if fund_family_id and str(fund_family_id).strip():
        return fund_family_id
    return " ".join((fund_name or "").strip().split()[:3]).upper()


def _retainability(info: "dict | None") -> "tuple[bool, str]":
    """ROT-0 (FND-0431): can an incumbent still be held, judged ONLY on its row of the latest scoring run (the one the candidates come from)?
    No row means not retainable: never an older row. `eligible` already carries the scorer's hard filters (stale NAV, data hold, data suspicion,
    wrong document, risk limits), so a held or suspect fund is ineligible here. The ALARM_WATCH rule (an alarmed fund may not GAIN weight) stays
    in the promotion gate (scenario_lifecycle), which also judges a reinstated incumbent."""
    if info is None:
        return False, "ROT-0: el titular no tiene fila en la ultima ejecucion de scoring (no se usa ninguna fila anterior)"
    if not info["in_universe"]:
        return False, "ROT-0: el titular esta fuera del universo actual"
    if not info["eligible"]:
        return False, "ROT-0: el titular es inelegible en la ultima ejecucion de scoring"
    if not info["score"] > 0:
        return False, "ROT-0: el titular tiene score 0 en la ultima ejecucion de scoring"
    return True, ""


def rotation_plan(
    conn: "psycopg.Connection",
    portfolio_current: "Portfolio",
    portfolio_new: "Portfolio",
    score_version: str = "v1",
    incumbent_scores: "dict | None" = None,
) -> list[dict]:
    """
    Compara dos carteras y genera el plan de rotacion: cada fondo que sale se empareja con UN fondo que entra
    (uno a uno, determinista: el titular de mayor score con el entrante de mayor score, desempate por ISIN) y se
    decide si la rotacion se recomienda.

    incumbent_scores: {(sub-cartera, isin): {"score", "eligible", "in_universe"}} de la ULTIMA ejecucion de scoring
        (score_candidates.load_run_scores). Un titular sin fila o inelegible no es retenible (ROT-0): su salida se
        recomienda siempre, sin comprobar el coste. Con None (llamadores antiguos) el score es el que lleva el propio
        fondo; si no lo lleva, el titular no es retenible. Antes (FND-0431) el score era 0 por defecto: el umbral de
        coste salia unas 100 veces el coste.

    Devuelve lista de operaciones, una por fondo que sale:
        [{subportfolio, isin_out, isin_in, recomendar, coste_est, razon, retenible, score_out, score_in}]
    isin_in lleva el entrante emparejado AUNQUE la rotacion no se recomiende (la reversion lo necesita); None solo cuando
    no hay entrante para ese titular.
    """
    def _group(portfolio):
        out = {}
        for f in portfolio.all_funds:
            out.setdefault(f["subportfolio"], {})[f["isin"]] = f
        return out

    current_map, new_map = _group(portfolio_current), _group(portfolio_new)

    def _info(sub: str, isin: str, fund: dict) -> "dict | None":
        if incumbent_scores is not None:
            return incumbent_scores.get((sub, isin))
        s = fund.get("score")
        return None if s is None else {"score": float(s), "eligible": True, "in_universe": True}

    operations = []
    for sub in sorted(set(current_map) | set(new_map)):
        curr_funds = current_map.get(sub, {})
        new_funds = new_map.get(sub, {})

        isins_out = set(curr_funds) - set(new_funds)
        isins_in = set(new_funds) - set(curr_funds)
        infos = {i: _info(sub, i, curr_funds[i]) for i in isins_out}
        # Determinista y uno a uno: mejor titular con mejor entrante; los titulares sin fila, al final.
        exits = sorted(isins_out, key=lambda i: (infos[i] is None, -(infos[i]["score"] if infos[i] else 0.0), i))
        entrants = sorted(isins_in, key=lambda i: (-float(new_funds[i].get("score") or 0.0), i))

        for k, isin_out in enumerate(exits):
            isin_in = entrants[k] if k < len(entrants) else None
            info = infos[isin_out]
            retainable, why = _retainability(info)
            score_in = float(new_funds[isin_in].get("score") or 0.0) if isin_in else None
            weight = curr_funds[isin_out].get("master_weight", 0.05)
            if isin_in is None:
                cost = estimate_rotation_cost(conn, isin_out, isin_out)
                rotate, reason = True, "El titular sale de la seleccion sin entrante que lo sustituya" + ("" if retainable else f" ({why})")
            elif not retainable:
                cost = estimate_rotation_cost(conn, isin_out, isin_in)
                rotate, reason = True, why
            else:
                cost = estimate_rotation_cost(conn, isin_out, isin_in)
                rotate, reason = should_rotate(conn, isin_out, info["score"], isin_in, score_in, weight)
            operations.append({
                "subportfolio": sub,
                "isin_out":     isin_out,
                "isin_in":      isin_in,
                "recomendar":   bool(rotate),
                "coste_est":    cost,
                "razon":        reason,
                "retenible":    retainable,
                "score_out":    round(info["score"], 4) if info else None,
                "score_in":     round(score_in, 4) if score_in is not None else None,
            })

    return operations


def _reinstatement_blocker(tentative: "Portfolio", sp: "SubPortfolioAllocation", slot: dict, inc_isin: str,
                           inc: dict, fx_flags: "dict | None") -> "str | None":
    """Why the incumbent cannot take the entrant's slot, or None when it can (E-11, FND-0431). The same rules the selection enforces: the ISIN
    and the family appear once in the whole portfolio, at most MAX_FUNDS_PER_MGR per manager (global across sub-portfolios), at most
    MAX_SAME_NATURE per nature inside the sub-portfolio, and the non-EUR exposed weight of the sub-portfolio stays within
    MAX_NON_EUR_UNHEDGED_WEIGHT (a swap that does not increase the exposed weight is always allowed)."""
    others = [f for s in tentative.sub_portfolios for f in s.funds if f is not slot]
    if any(f["isin"] == inc_isin for f in others):
        return "el ISIN ya esta en la cartera"
    fam = _family_key(inc.get("fund_family_id"), inc.get("fund_name"))
    if fam and any(_family_key(f.get("fund_family_id"), f.get("fund_name")) == fam for f in others):
        return "la familia ya esta en la cartera"
    mgr = inc.get("gestora") or "Desconocida"
    if sum(1 for f in others if (f.get("gestora") or "Desconocida") == mgr) + 1 > MAX_FUNDS_PER_MGR:
        return f"tope de {MAX_FUNDS_PER_MGR} fondos por gestora"
    nature = inc.get("fund_nature")
    if sum(1 for f in sp.funds if f is not slot and f.get("fund_nature") == nature) + 1 > MAX_SAME_NATURE:
        return f"tope de {MAX_SAME_NATURE} fondos por naturaleza"
    if fx_flags is not None:
        w = slot["weight"]
        now = sum(f["weight"] for f in sp.funds if fx_flags.get(f["isin"], False))
        after = now - (w if fx_flags.get(slot["isin"], False) else 0.0) + (w if fx_flags.get(inc_isin, False) else 0.0)
        if after > MAX_NON_EUR_UNHEDGED_WEIGHT + 1e-9 and after > now + 1e-12:
            return f"presupuesto de divisa no EUR ({after:.0%} > {MAX_NON_EUR_UNHEDGED_WEIGHT:.0%})"
    return None


def _apply_rotation_gate(
    conn: "psycopg.Connection",
    tentative: "Portfolio",
    previous: "Portfolio",
    score_version: str,
) -> tuple["Portfolio", list[dict]]:
    """
    Revierte al titular las rotaciones que rotation_plan() no recomienda (coste > beneficio), sustituyendo el
    fondo entrante por el titular EN LA MISMA RANURA (mismo peso interno) -- no vuelve a ejecutar _assign_weights.

    FND-0431: (1) el titular se juzga en la ULTIMA ejecucion de scoring de la que salen los candidatos (misma
    sub-cartera, version y regimen): sin fila, inelegible, fuera del universo o con score 0 no se retiene (ROT-0,
    nunca se usa una fila anterior); (2) un titular retenible solo vuelve si cumple las reglas de construccion
    (ISIN y familia unicos, tope por gestora y por naturaleza, presupuesto de divisa): si no, la rotacion sigue
    adelante y la operacion lo dice; (3) antes la reversion no podia ocurrir nunca, porque el plan solo llevaba el
    entrante cuando la rotacion SE recomendaba y aqui se revierten las que NO.

    Devuelve (portfolio_resultante, plan_completo) -- plan_completo se persiste en macro_context para trazabilidad;
    cada operacion revertida o rechazada lleva `revertida` True / False.
    """
    prev_isins = sorted({f["isin"] for sp in previous.sub_portfolios for f in sp.funds})
    run_scores = _incumbent_run_scores(conn, score_version, tentative.regime, prev_isins)
    plan = rotation_plan(conn, previous, tentative, score_version, incumbent_scores=run_scores)
    reverted = [op for op in plan if not op["recomendar"] and op["isin_in"]]
    if not reverted:
        return tentative, plan

    attrs = _incumbent_attrs(conn, [op["isin_out"] for op in reverted])
    fx_flags = _fx_flags(conn, [f["isin"] for s in tentative.sub_portfolios for f in s.funds] + [op["isin_out"] for op in reverted])

    for op in reverted:
        sub_name, isin_out, isin_in = op["subportfolio"], op["isin_out"], op["isin_in"]
        sp = next((s for s in tentative.sub_portfolios if s.name == sub_name), None)
        slot_idx = None if sp is None else next((i for i, f in enumerate(sp.funds) if f["isin"] == isin_in), None)
        if slot_idx is None:
            blocker = "el entrante no esta en la cartera construida"
        elif isin_out not in attrs:
            blocker = "el titular no tiene ficha en fund_master"
        else:
            blocker = _reinstatement_blocker(tentative, sp, sp.funds[slot_idx], isin_out, attrs[isin_out], fx_flags)
        if blocker:
            op["recomendar"], op["revertida"] = True, False
            op["razon"] = f"{op['razon']} | reversion refused: {blocker}"
            continue
        a = attrs[isin_out]
        slot = sp.funds[slot_idx]
        sp.funds[slot_idx] = {
            "isin": isin_out, "fund_name": a["fund_name"], "fund_nature": a["fund_nature"],
            "gestora": a["gestora"], "fund_family_id": a["fund_family_id"],
            "score": round(float(run_scores[(sub_name, isin_out)]["score"]), 4), "weight": slot["weight"],
            "role": f"{sub_name} - {a['fund_nature']} (incumbente retenido, rotacion revertida)",
        }
        op["revertida"] = True

    return tentative, plan


class PortfolioBuilder:

    def __init__(self, conn: "psycopg.Connection"):
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
        _ph = "%s"
        if _lifecycle.has_status_column(self.conn):
            # Scenario lifecycle (2026-10-11): the baseline of a rotation is what is in PRODUCTION, never "the newest row" (a
            # test run or a draft must not become the incumbent); no PRODUCTION yet -> no previous portfolio.
            row = self.conn.execute(f"""
                SELECT scenario_id, profile, macro_regime, notes
                FROM portfolio_scenarios
                WHERE status = 'PRODUCTION' AND scenario_id != COALESCE({_ph}, '')
            """, (exclude_scenario_id,)).fetchone()
        else:
            row = self.conn.execute(f"""
                SELECT scenario_id, profile, macro_regime, notes
                FROM portfolio_scenarios
                WHERE scenario_id != COALESCE({_ph}, '')
                ORDER BY created_at DESC, scenario_id DESC
                LIMIT 1
            """, (exclude_scenario_id,)).fetchone()
        if row is None:
            return None

        scenario_id, profile, regime, notes_json = row
        # role -> position_role: reserved word on Postgres (SQL:2003, db/pg/rename_map.yaml).
        # Positional tuple-unpacking below means the SELECT's column NAME doesn't matter, only
        # the reference in FROM must resolve to the real physical column.
        _role_col = "position_role"
        # FND-0063: fund_nature comes from fund_master (LEFT JOIN: a fund dropped from the master
        # since the previous cycle must not lose its row) -- Portfolio.summary() reads it for
        # every fund, so a dict without it raised KeyError on the load_previous() -> summary() path.
        weight_rows = self.conn.execute(f"""
            SELECT pw.isin, pw.block, pw.weight, pw.{_role_col},
                   COALESCE(fm.Fund_Nature, 'Desconocida')
            FROM portfolio_weights pw
            LEFT JOIN fund_master fm ON fm.ISIN = pw.isin
            WHERE pw.scenario_id = {_ph}
        """, (scenario_id,)).fetchall()
        if not weight_rows:
            return None

        sub_map: dict[str, list[dict]] = {}
        for isin, block, weight, role, nature in weight_rows:
            sub_map.setdefault(block, []).append({
                "isin": isin, "weight": weight, "role": role, "fund_nature": nature,
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
        replace_draft:      bool = False,
        candidates:         "pd.DataFrame | None" = None,
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
            replace_draft:      permitir sobrescribir un escenario DRAFT existente con el
                                mismo id (nunca PRODUCTION ni ARCHIVED).
            candidates:         P3-7: frame de candidatos explicito (score_candidates.CANDIDATE_COLUMNS) que
                                sustituye la consulta a fund_scores; solo lo usa el shadow de la penalizacion
                                de comisiones (dry_run). None = la consulta de siempre.
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
                incumbent_isins=incumbent_isins,
                candidates=candidates)

            if selected.empty:
                print(f"  AVISO: Sin fondos elegibles para {sub_name}")
                sub_portfolios.append(SubPortfolioAllocation(
                    name=sub_name,
                    regime_weight=regime_weight,
                    funds=[],
                ))
                continue

            # Asignar pesos internos
            pool = dict(selected.attrs.get("pool") or {})
            selected = _assign_weights(selected)

            # Diagnostico de liquidez estructural (2026-10-11): se dice AL CONSTRUIR que sub-cartera se queda corta y por que, no
            # solo al promover (la puerta de promocion rechaza la liquidez declarada por encima de MAX_PORTFOLIO_CASH_WEIGHT).
            _sub_cash = round(1.0 - float(selected["weight"].sum()), 4)
            if _sub_cash > 0 and pool:
                _non_exposed = pool["candidates"] - pool["exposed"]
                print(f"  AVISO liquidez {sub_name}: {_sub_cash:.0%} de la sub-cartera sin asignar "
                      f"({len(selected)} fondos; candidatos elegibles {pool['candidates']}, expuestos {pool['exposed']}, "
                      f"no expuestos {_non_exposed}; con el tope de {MAX_WEIGHT_PER_FUND:.0%} por fondo hacen falta al menos "
                      f"{int(np.ceil(1.0 / MAX_WEIGHT_PER_FUND))} fondos)")

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

            for f in funds_list:
                isins_used.add(f["isin"])
                dedup_key = _family_key(f.get("fund_family_id"), f["fund_name"])
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

        # Liquidez estructural DECLARADA (la puerta de promocion exige pesos + liquidez declarada = 1 y la acota)
        portfolio.macro_context["cash_weight"] = portfolio.cash_weight

        # FND-0306: which stored P2 metrics this DRAFT was built on (universe and scenario fingerprints); the promotion gate compares it with the
        # evidence audit. Information only: it never fails the build (shared/governance/lineage.safe_stamp).
        from shared.governance import lineage as _lineage
        portfolio.macro_context["metrics_lineage"] = _lineage.safe_stamp(self.conn, [f["isin"] for f in portfolio.all_funds])
        from shared.governance.run_identity import build_identity as _build_identity     # git sha, clean flag, config hash: the audit header records the same three
        portfolio.macro_context["build_identity"] = _build_identity()

        if not dry_run:
            self._persist(portfolio, score_version, replace_draft=replace_draft)

        return portfolio

    def _persist(self, portfolio: Portfolio, score_version: str, replace_draft: bool = False) -> None:
        """Persiste el escenario y los pesos en BD.

        Con el ciclo de vida instalado (portfolio_scenarios.status) el escenario entra como DRAFT y NUNCA se sobrescribe
        nada que no sea un DRAFT pedido con replace_draft (2026-10-10: la reconstruccion del mismo mes destruyo la cartera
        previa). Sin la columna se conserva el upsert historico (los ids por defecto ya son unicos por construccion)."""
        today = pd.Timestamp.today().strftime("%Y-%m-%d")

        has_lifecycle = _lifecycle.has_status_column(self.conn)
        if has_lifecycle:
            current = _lifecycle.get_status(self.conn, portfolio.scenario_id)
            if current is not None and not (replace_draft and current == _lifecycle.STATUS_DRAFT):
                self.conn.rollback()
                raise _lifecycle.ScenarioExists(
                    f"el escenario '{portfolio.scenario_id}' ya existe en estado {current}: no se sobrescribe "
                    f"(use otro id; --replace-draft solo reemplaza un DRAFT)")

        # Insertar escenario (status toma su DEFAULT 'DRAFT'; un reemplazo de DRAFT conserva su estado). built_at (instante de la
        # construccion) existe junto con status: mismo bloque DDL.
        # Dos sentencias estaticas (la barrida EXPLAIN no admite SQL construido): con y sin la columna.
        if has_lifecycle:
            insert_scenario = """
                INSERT INTO portfolio_scenarios
                    (scenario_id, profile, macro_regime, created_at, notes, built_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (scenario_id) DO UPDATE SET
                    profile = excluded.profile, macro_regime = excluded.macro_regime,
                    created_at = excluded.created_at, notes = excluded.notes, built_at = now()
            """
        else:
            insert_scenario = """
                INSERT INTO portfolio_scenarios
                    (scenario_id, profile, macro_regime, created_at, notes)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (scenario_id) DO UPDATE SET
                    profile = excluded.profile, macro_regime = excluded.macro_regime,
                    created_at = excluded.created_at, notes = excluded.notes
            """
        self.conn.execute(insert_scenario, (
            portfolio.scenario_id,
            portfolio.profile,
            portfolio.regime,
            today,
            json.dumps(portfolio.macro_context, ensure_ascii=False),
        ))

        # Eliminar pesos anteriores del escenario
        ph = "%s"
        self.conn.execute(
            f"DELETE FROM portfolio_weights WHERE scenario_id={ph}",
            (portfolio.scenario_id,)
        )

        # Insertar pesos. `role` -> `position_role` en Postgres (palabra reservada SQL:2003,
        # db/pg/rename_map.yaml).
        role_col = "position_role"
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
