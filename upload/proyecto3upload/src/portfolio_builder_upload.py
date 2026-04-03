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
  Max peso por gestora:           30%
  Min peso por fondo elegible:     3%
  Max fondos misma naturaleza:     5 (en sub-cartera)

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


# ============================================================
# Constantes de construccion
# ============================================================

MAX_FUNDS_PER_SUB    = 10     # max fondos por sub-cartera
MAX_WEIGHT_PER_FUND  = 0.20   # max 20% en un solo fondo
MAX_WEIGHT_PER_MGR   = 0.30   # max 30% de una gestora
MIN_WEIGHT_PER_FUND  = 0.03   # min 3% si el fondo entra
MAX_SAME_NATURE      = 5      # max fondos de la misma naturaleza por sub-cartera

# Metodo de asignacion de pesos internos
# "score_proportional": pesos proporcionales al score final
# "equal":              pesos iguales entre fondos seleccionados
WEIGHT_METHOD = "score_proportional"


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
) -> pd.DataFrame:
    """
    Selecciona los mejores fondos para una sub-cartera aplicando
    restricciones de diversificacion.
    exclude_isins: ISINs ya usados globalmente.
    exclude_names: nombres base ya usados globalmente.
    mgr_global:    conteo global de fondos por gestora.
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

        # Max 2 fondos por gestora -- global entre sub-carteras
        if mgr_global.get(mgr, 0) >= 2:
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

def _assign_weights(
    df: pd.DataFrame,
    method: str = WEIGHT_METHOD,
) -> pd.DataFrame:
    """
    Asigna pesos internos a los fondos seleccionados.
    Los pesos suman 1.0 dentro de la sub-cartera.
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

    # Aplicar restriccion max peso por fondo
    capped = False
    for i in df.index:
        if df.loc[i, "weight"] > MAX_WEIGHT_PER_FUND:
            df.loc[i, "weight"] = MAX_WEIGHT_PER_FUND
            capped = True

    # Redistribuir si hubo cap
    if capped:
        capped_mask   = df["weight"] == MAX_WEIGHT_PER_FUND
        free_mask     = ~capped_mask
        remaining     = 1.0 - df[capped_mask]["weight"].sum()
        free_scores   = df[free_mask]["score_total"]
        if free_scores.sum() > 0:
            df.loc[free_mask, "weight"] = (
                free_scores / free_scores.sum() * remaining
            )
        else:
            df.loc[free_mask, "weight"] = remaining / free_mask.sum()

    # Aplicar restriccion min peso por fondo
    df.loc[df["weight"] < MIN_WEIGHT_PER_FUND, "weight"] = MIN_WEIGHT_PER_FUND

    # Renormalizar a 1.0
    df["weight"] = df["weight"] / df["weight"].sum()
    df["weight"] = df["weight"].round(4)

    # Ajuste final para que sumen exactamente 1.0
    diff = 1.0 - df["weight"].sum()
    if abs(diff) > 0:
        df.loc[df["weight"].idxmax(), "weight"] += diff
        df.loc[df["weight"].idxmax(), "weight"] = round(
            df.loc[df["weight"].idxmax(), "weight"], 4)

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
    """
    SPREAD = 0.001  # coste de mercado estimado

    def _get_cost(isin: str, fee_type: str) -> float:
        row = conn.execute("""
            SELECT rc.exit_fee_pct, rc.entry_fee_pct
            FROM fund_master fm
            JOIN rotation_costs rc ON rc.fund_nature = fm.Fund_Nature
            WHERE fm.ISIN = ?
        """, (isin,)).fetchone()
        if not row:
            return 0.005  # default 0.5% si no hay datos
        return float(row[0] if fee_type == "exit" else row[1]) / 100

    exit_fee  = _get_cost(isin_out, "exit")
    entry_fee = _get_cost(isin_in,  "entry")
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
        weight:          peso del fondo en la cartera (para estimar impacto)
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


class PortfolioBuilder:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def build(
        self,
        regime_result: RegimeResult,
        scenario_id:   str,
        score_version: str = "v1",
        profile:       str = "Equilibrada",
        dry_run:       bool = False,
    ) -> Portfolio:
        """
        Construye la cartera maestra para el regimen dado.

        Parametros:
            regime_result: resultado del clasificador de regimen
            scenario_id:   identificador unico del escenario
            score_version: version de scores a usar
            profile:       perfil de la cartera maestra
            dry_run:       si True, no persiste en BD
        """
        regime  = regime_result.regime
        weights = regime_result.weights  # {Defensiva: X, Equilibrada: Y, Dinamica: Z}

        sub_portfolios = []
        isins_used    = set()
        families_used = set()   # antes: names_used (3-token) -- ahora: fund_family_id o fallback
        mgr_used      = {}

        for sub_name, regime_weight in weights.items():
            if regime_weight == 0:
                continue

            # Seleccionar fondos (excluyendo los ya usados en sub-carteras anteriores)
            selected = _select_funds_for_subportfolio(
                self.conn, sub_name, score_version,
                exclude_isins=isins_used,
                exclude_names=families_used,
                mgr_global=mgr_used)

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
