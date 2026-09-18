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
from shared.config import PORTFOLIO_HYSTERESIS_ENABLED, ROTATION_COST_GATE_ENABLED


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
    """
    exclude_isins    = exclude_isins or set()
    exclude_families = set(exclude_names or set())   # reutilizamos el param para familias
    mgr_global       = dict(mgr_global or {})

    # Cargar scores elegibles para esta sub-cartera
    # fund_family_id puede ser NULL para fondos no procesados por family_builder
    rows = conn.execute("""
        SELECT fs.isin, fs.score_total, fs.score_detail,
               fm.Fund_Name, fm.Fund_Nature, fm.Management_Company,
               fm.fund_family_id
        FROM fund_scores fs
        JOIN fund_master fm ON fm.ISIN = fs.isin
        WHERE fs.block = ?
          AND fs.score_version = ?
          AND fs.eligible = 1
          AND fs.score_total > 0
          AND fm.In_Current_Universe = 1
        ORDER BY fs.score_total DESC
    """, (subportfolio, score_version)).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "isin", "score_total", "score_detail",
        "fund_name", "fund_nature", "management_company",
        "fund_family_id"
    ])
    df["score_total"] = df["score_total"].astype(float)

    # -- Histeresis: los titulares reciben un bonus de score (HYSTERESIS_BAND)
    # para que los retadores tengan que superarlos por un margen real antes de
    # provocar una rotacion. Los pesos finales siguen usando score_total (sin bonus).
    if incumbent_isins:
        is_inc = df["isin"].isin(incumbent_isins)
        df["effective_score"] = df["score_total"].where(
            ~is_inc,
            df["score_total"] * (1.0 + HYSTERESIS_BAND),
        )
    else:
        df["effective_score"] = df["score_total"]

    df = df.sort_values("effective_score", ascending=False).reset_index(drop=True)

    # Aplicar restricciones de diversificacion
    selected     = []
    nature_count = {}

    def _norm_fallback(name: str) -> str:
        """Fallback cuando fund_family_id es NULL: primeros 3 tokens del nombre."""
        return " ".join((name or "").strip().split()[:3]).upper()

    for _, row in df.iterrows():
        if row["isin"] in exclude_isins:
            continue

        nature = row["fund_nature"]
        mgr    = row["management_company"] or "Desconocida"

        # Clave de deduplicacion: fund_family_id si existe, 3-token si no
        fam_id = row["fund_family_id"]
        dedup_key = fam_id if (fam_id and str(fam_id).strip()) \
                    else _norm_fallback(row["fund_name"])

        # No repetir familia -- global entre sub-carteras
        if dedup_key and dedup_key in exclude_families:
            continue

        # Max fondos por naturaleza en esta sub-cartera
        if nature_count.get(nature, 0) >= MAX_SAME_NATURE:
            continue

        # Max fondos por gestora -- global entre sub-carteras
        if mgr_global.get(mgr, 0) >= MAX_FUNDS_PER_MGR:
            continue

        selected.append(row)
        nature_count[nature]    = nature_count.get(nature, 0) + 1
        mgr_global[mgr]         = mgr_global.get(mgr, 0) + 1
        if dedup_key:
            exclude_families.add(dedup_key)

        if len(selected) >= max_funds:
            break

    return pd.DataFrame(selected) if selected else pd.DataFrame()


# ============================================================
# Asignacion de pesos internos
# ============================================================

def _clamp_and_renormalize(
    weights: pd.Series,
    lo: float,
    hi: float,
    max_iter: int = 50,
) -> pd.Series:
    """
    Water-filling: ajusta `weights` para que cada valor quede en [lo, hi] y
    la suma se mantenga en 1.0, redistribuyendo el excedente/deficit de
    forma proporcional entre los miembros aun no fijados en cada iteracion.

    Fase 2b (P3 optimization plan, 2026-09-18): reemplaza el clamp-then-
    renormalize anterior, que aplicaba el tope del 20% y redistribuia,
    LUEGO aplicaba el suelo del 3% y volvia a renormalizar sobre TODO el
    vector -- ese segundo renormalize podia volver a violar el tope que el
    primer paso ya habia impuesto, y el ajuste final del residuo de
    redondeo se sumaba siempre a idxmax() sin comprobar si eso lo empujaba
    por encima del tope.

    Precondicion de factibilidad: n*lo <= 1.0 <= n*hi (n = len(weights)).
    Con lo=0.03, hi=0.20, eso exige n in [5, 33] -- una sub-cartera de 4
    fondos es infactible bajo el tope del 20% (4*0.20=0.80 < 1.0) y hoy
    producia silenciosamente pesos del 25%. Si es infactible, o si el bucle
    no converge en max_iter (lo que en un problema factible no deberia
    ocurrir nunca -- la redistribucion proporcional sobre un simplex 1-D
    converge monotonamente), se registra un ERROR y el residuo final se
    reparte proporcionalmente sobre el margen disponible (o, si no hay
    ningun margen, a partes iguales) en vez de volcarse entero sobre un
    unico fondo.
    """
    n = len(weights)
    if n == 0:
        return weights

    w = weights.copy().astype(float)
    total = w.sum()
    if total <= 0:
        # Sin señal de score positiva que repartir -- el reparto igualitario
        # es el unico fallback razonable aqui (situacion distinta de la
        # no-convergencia: no hay señal alguna que preservar).
        w[:] = 1.0 / n
        return w.round(4)

    w = w / total  # normalizar a suma 1.0 de partida
    original = w.copy()  # pre-clamp, para el fallback infactible mas abajo

    feasible = n * lo <= 1.0 + 1e-9 and 1.0 <= n * hi + 1e-9
    if not feasible:
        print(f"  [ERROR] _clamp_and_renormalize: cotas infactibles para "
              f"n={n} fondos (lo={lo}, hi={hi} -> rango [{n*lo:.2f}, "
              f"{n*hi:.2f}] no cubre 1.0). El resultado puede violar lo/hi "
              f"-- tratar esta sub-cartera como invalida.")

    clamped_lo = pd.Series(False, index=w.index)
    clamped_hi = pd.Series(False, index=w.index)

    for _ in range(max_iter):
        new_lo = w < lo
        new_hi = w > hi
        if not new_lo.any() and not new_hi.any():
            break  # punto fijo: todos los pesos ya estan en [lo, hi]

        clamped_lo |= new_lo
        clamped_hi |= new_hi
        w[clamped_lo] = lo
        w[clamped_hi] = hi

        free_mask = ~(clamped_lo | clamped_hi)
        if not free_mask.any():
            break  # todos fijados -- nada que redistribuir

        fixed_total = w[~free_mask].sum()
        remaining   = 1.0 - fixed_total
        free_sum    = w[free_mask].sum()
        if free_sum > 0:
            w[free_mask] = w[free_mask] / free_sum * remaining
        else:
            w[free_mask] = remaining / free_mask.sum()
    else:
        print(f"  [ERROR] _clamp_and_renormalize: no convergio en "
              f"{max_iter} iteraciones (n={n}, lo={lo}, hi={hi}). Se usa la "
              f"ultima iteracion -- tratar esta sub-cartera como invalida "
              f"e investigar (en un problema factible esto no deberia "
              f"ocurrir nunca).")

    # Residuo de redondeo: repartir proporcionalmente sobre el margen
    # disponible (headroom), no volcarlo entero sobre idxmax(). Si ademas no
    # hay margen en ningun sitio (caso infactible con todos ya en el tope,
    # p.ej. n=2 bajo hi=0.20: ambos exceden el tope desde la primera
    # iteracion y quedan identicos en hi, sin margen), repartir en
    # proporcion a los pesos ORIGINALES (pre-clamp) en vez de a partes
    # iguales -- preserva la señal de score todo lo que matematicamente es
    # posible, en vez de que la caida en el fallback de infactibilidad
    # borre por completo la diferenciacion entre fondos.
    w = w.round(4)
    diff = round(1.0 - w.sum(), 4)
    if diff != 0:
        headroom = (hi - w).clip(lower=0) if diff > 0 else (w - lo).clip(lower=0)
        if headroom.sum() > 0:
            w = w + headroom / headroom.sum() * diff
        elif original.sum() > 0:
            w = w + original / original.sum() * diff
        else:
            w = w + diff / len(w)
        w = w.round(4)

    return w


def _assign_weights(
    df: pd.DataFrame,
    method: str = WEIGHT_METHOD,
) -> pd.DataFrame:
    """
    Asigna pesos internos a los fondos seleccionados.
    Los pesos suman 1.0 dentro de la sub-cartera, cada uno dentro de
    [MIN_WEIGHT_PER_FUND, MAX_WEIGHT_PER_FUND] siempre que el numero de
    fondos lo permita (ver _clamp_and_renormalize).
    """
    if df.empty:
        return df

    df = df.copy()

    if method == "score_proportional":
        total_score = df["score_total"].sum()
        if total_score > 0:
            df["weight"] = df["score_total"] / total_score
        else:
            df["weight"] = 1.0 / len(df)
    else:
        df["weight"] = 1.0 / len(df)

    df["weight"] = _clamp_and_renormalize(
        df["weight"], MIN_WEIGHT_PER_FUND, MAX_WEIGHT_PER_FUND
    )

    return df


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

    def _get_exit_fee_pct(isin: str) -> float:
        row = conn.execute("""
            SELECT COALESCE(fm.Exit_Fee_Pct, rc.exit_fee_pct, ?)
            FROM fund_master fm
            LEFT JOIN rotation_costs rc ON rc.fund_nature = fm.Fund_Nature
            WHERE fm.ISIN = ?
        """, (DEFAULT_EXIT_FEE_PCT, isin)).fetchone()
        return float(row[0]) if row and row[0] is not None else DEFAULT_EXIT_FEE_PCT

    def _get_entry_fee_pct(isin: str) -> float:
        row = conn.execute("""
            SELECT COALESCE(fm.Entry_Fee_Pct, rc.entry_fee_pct, ?)
            FROM fund_master fm
            LEFT JOIN rotation_costs rc ON rc.fund_nature = fm.Fund_Nature
            WHERE fm.ISIN = ?
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

        row = conn.execute("""
            SELECT fs.score_total, fm.Fund_Name, fm.Fund_Nature, fm.Management_Company
            FROM fund_scores fs JOIN fund_master fm ON fm.ISIN = fs.isin
            WHERE fs.isin = ? AND fs.block = ? AND fs.score_version = ?
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
        row = self.conn.execute("""
            SELECT scenario_id, profile, macro_regime, notes
            FROM portfolio_scenarios
            WHERE scenario_id != COALESCE(?, '')
            ORDER BY created_at DESC
            LIMIT 1
        """, (exclude_scenario_id,)).fetchone()
        if row is None:
            return None

        scenario_id, profile, regime, notes_json = row
        weight_rows = self.conn.execute("""
            SELECT isin, block, weight, role
            FROM portfolio_weights
            WHERE scenario_id = ?
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
                self.conn, sub_name, score_version,
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

        # Insertar escenario
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
        self.conn.execute(
            "DELETE FROM portfolio_weights WHERE scenario_id=?",
            (portfolio.scenario_id,)
        )

        # Insertar pesos
        for f in portfolio.all_funds:
            self.conn.execute("""
                INSERT INTO portfolio_weights
                    (scenario_id, isin, block, weight, role, notes)
                VALUES (?, ?, ?, ?, ?, ?)
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
