# proyecto3/src/fund_scorer.py
# -*- coding: utf-8 -*-
"""
Motor de scoring de fondos para P3.

Calcula el score compuesto de cada fondo combinando:
  - Capa 1: Filtros duros (exclusion automatica)
  - Capa 2: Scoring base (metricas intrinsecas P2)
  - Capa 3: Multiplicadores de regimen macro

El score final determina la elegibilidad y ranking de cada fondo
dentro de su sub-cartera (Defensiva, Equilibrada, Dinamica).

Pesos del scoring base (horizon=since_inception):
    return_ann real      25%   Rentabilidad real anualizada
    sharpe               20%   Eficiencia del retorno
    max_drawdown         20%   Control del dano (invertido)
    alpha_persistence    15%   Consistencia de la gestion
    capture_ratio        10%   Asimetria subidas/bajadas
    momentum_rank        10%   Dinamica reciente

Multiplicadores de regimen (por perfil de sensibilidad):
    beta_oil > 0.01              x1.20  cobertura energetica
    beta_rate_eu < -0.10         x0.70  muy sensible a BCE
    fx_contribution_pct > 0.60   x0.80  retorno mayormente divisa
    alpha_persistence > 0.60     x1.15  gestor consistente
    macro_r2 > 0.50              x0.85  muy determinado por macro

Filtros duros:
    max_drawdown < -0.25         excluir (riesgo de ruina)
    return_ann real < 0          excluir (destruye patrimonio real)
    macro_n_obs < 36             excluir betas (modelo poco fiable)
    srri_nav >= 7                excluir en Defensiva
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
import sys
import json

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from proyecto3.src.regime_classifier import RegimeResult


# ============================================================
# Constantes
# ============================================================

# Pesos scoring base
BASE_WEIGHTS = {
    "return_ann_real":   0.25,
    "sharpe":            0.20,
    "max_drawdown":      0.20,
    "alpha_persistence": 0.15,
    "capture_ratio":     0.10,
    "momentum_rank":     0.10,
}

# Filtros duros
MAX_DRAWDOWN_LIMIT   = -0.25   # excluir si drawdown peor que -25%
MIN_REAL_RETURN      = 0.00    # excluir si retorno real negativo
MIN_MACRO_OBS        = 36      # minimo observaciones para betas fiables
MAX_SRRI_DEFENSIVE   = 5       # SRRI maximo en cartera defensiva

# Umbrales multiplicadores de regimen
BETA_OIL_THRESHOLD      =  0.01   # beta_oil > umbral -> bonus energetico
BETA_RATE_EU_THRESHOLD  = -0.10   # beta_rate_eu < umbral -> penalizacion
FX_CONTRIBUTION_LIMIT   =  0.60   # fx_pct > limite -> penalizacion divisa
ALPHA_PERS_THRESHOLD    =  0.60   # alpha_persistence > umbral -> bonus
MACRO_R2_LIMIT          =  0.50   # macro_r2 > limite -> penalizacion

# Multiplicadores
MULT_OIL_BONUS      = 1.20
MULT_RATE_EU_MALUS  = 0.70
MULT_FX_MALUS       = 0.80
MULT_ALPHA_BONUS    = 1.15
MULT_MACRO_MALUS    = 0.85

# Sub-carteras por naturaleza de fondo
SUBPORTFOLIO_MAPPING = {
    "Defensiva":   ["Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible"],
    "Equilibrada": ["Renta Fija Flexible", "Mixtos", "Renta Variable"],
    "Dinamica":    ["Renta Variable", "Mixtos", "Alternativo"],
}


# ============================================================
# Dataclasses
# ============================================================

@dataclass
class FundScore:
    isin:              str
    fund_name:         str
    fund_nature:       str
    score_base:        float
    score_final:       float
    eligible:          bool
    subportfolio:      str        # Defensiva / Equilibrada / Dinamica
    exclusion_reason:  str | None
    score_detail:      dict = field(default_factory=dict)

    def to_db_row(self, regime: str, score_version: str = "v1") -> dict:
        return {
            "isin":          self.isin,
            "block":         self.fund_nature,
            "score_version": score_version,
            "score_total":   round(self.score_final, 6),
            "score_detail":  json.dumps(self.score_detail, ensure_ascii=False),
            "eligible":      1 if self.eligible else 0,
            "calculated_at": pd.Timestamp.today().strftime("%Y-%m-%d"),
            "notes":         f"regime={regime}" + (
                f" | excluido: {self.exclusion_reason}"
                if self.exclusion_reason else ""
            ),
        }


# ============================================================
# Carga de metricas P2
# ============================================================

def load_fund_metrics_for_scoring(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Carga todas las metricas necesarias para el scoring desde fund_metrics.
    Devuelve DataFrame indexado por ISIN con una columna por metrica.
    """
    metrics_needed = [
        ("return_ann",          "since_inception", 1),  # real
        ("sharpe",              "since_inception", 0),
        ("max_drawdown",        "since_inception", 0),
        ("alpha_persistence",   "since_inception", 0),
        ("capture_ratio",       "since_inception", 0),
        ("momentum_rank",       "since_inception", 0),
        ("beta_oil",            "since_inception", 0),
        ("beta_rate_eu",        "since_inception", 0),
        ("fx_contribution_pct", "since_inception", 0),
        ("macro_r2",            "since_inception", 0),
        ("macro_n_obs",         "since_inception", 0),
        ("srri_nav",            "since_inception", 0),
        ("volatility_ann",      "since_inception", 0),
    ]

    rows = []
    for metric, horizon, real_flag in metrics_needed:
        result = conn.execute("""
            SELECT isin, value
            FROM fund_metrics
            WHERE metric=? AND horizon=? AND real_flag=?
              AND value IS NOT NULL
        """, (metric, horizon, real_flag)).fetchall()
        for isin, value in result:
            rows.append({"isin": isin, "metric": metric, "value": float(value)})

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    wide = df.pivot_table(index="isin", columns="metric",
                          values="value", aggfunc="last")
    wide.columns.name = None

    # Renombrar return_ann real
    if "return_ann" in wide.columns:
        wide = wide.rename(columns={"return_ann": "return_ann_real"})

    # Añadir info de fund_master
    fm = pd.read_sql("""
        SELECT ISIN, Fund_Name, Fund_Nature, SRRI as srri_kiid
        FROM fund_master
    """, conn).set_index("ISIN")

    return wide.join(fm, how="left")


# ============================================================
# Normalizacion de metricas
# ============================================================

def _normalize_metric(series: pd.Series, invert: bool = False) -> pd.Series:
    """
    Normaliza una serie al rango [0, 1] usando percentil rank.
    Si invert=True, valores menores son mejores (ej. drawdown).
    """
    ranked = series.rank(pct=True, na_option="bottom")
    return 1 - ranked if invert else ranked


# ============================================================
# Scoring base
# ============================================================

def compute_base_scores(df: pd.DataFrame) -> pd.Series:
    """
    Calcula el score base [0,1] para cada fondo usando los pesos definidos.
    """
    scores = pd.Series(0.0, index=df.index)

    for metric, weight in BASE_WEIGHTS.items():
        if metric not in df.columns:
            continue
        col = df[metric].dropna()
        if col.empty:
            continue
        invert = (metric == "max_drawdown")
        normalized = _normalize_metric(col, invert=invert)
        scores = scores.add(normalized * weight, fill_value=0)

    return scores


# ============================================================
# Multiplicadores de regimen
# ============================================================

def compute_regime_multiplier(row: pd.Series, regime: str) -> tuple[float, dict]:
    """
    Calcula el multiplicador de regimen para un fondo.
    Devuelve (multiplicador, detalle_dict).
    """
    multiplier = 1.0
    detail = {}

    # Bonus cobertura energetica (especialmente en Shock_Energetico)
    if regime in ("Shock_Energetico", "Estanflacion", "Recalentamiento"):
        beta_oil = row.get("beta_oil", np.nan)
        if not np.isnan(beta_oil) and beta_oil > BETA_OIL_THRESHOLD:
            multiplier *= MULT_OIL_BONUS
            detail["beta_oil_bonus"] = MULT_OIL_BONUS

    # Penalizacion sensibilidad tipos BCE (en subida de tipos)
    if regime in ("Recalentamiento_Tardio", "Shock_Energetico", "Estanflacion"):
        beta_rate = row.get("beta_rate_eu", np.nan)
        if not np.isnan(beta_rate) and beta_rate < BETA_RATE_EU_THRESHOLD:
            multiplier *= MULT_RATE_EU_MALUS
            detail["beta_rate_eu_malus"] = MULT_RATE_EU_MALUS

    # Penalizacion exceso divisa
    fx_pct = row.get("fx_contribution_pct", np.nan)
    if not np.isnan(fx_pct) and abs(fx_pct) > FX_CONTRIBUTION_LIMIT:
        multiplier *= MULT_FX_MALUS
        detail["fx_malus"] = MULT_FX_MALUS

    # Bonus gestor consistente
    alpha_pers = row.get("alpha_persistence", np.nan)
    if not np.isnan(alpha_pers) and alpha_pers > ALPHA_PERS_THRESHOLD:
        multiplier *= MULT_ALPHA_BONUS
        detail["alpha_persistence_bonus"] = MULT_ALPHA_BONUS

    # Penalizacion alta dependencia macro
    macro_r2 = row.get("macro_r2", np.nan)
    if not np.isnan(macro_r2) and macro_r2 > MACRO_R2_LIMIT:
        multiplier *= MULT_MACRO_MALUS
        detail["macro_r2_malus"] = MULT_MACRO_MALUS

    return round(multiplier, 4), detail


# ============================================================
# Filtros duros
# ============================================================

def check_hard_filters(
    row: pd.Series,
    subportfolio: str,
) -> str | None:
    """
    Verifica los filtros duros.
    Devuelve None si pasa todos, o el motivo de exclusion.
    """
    dd = row.get("max_drawdown", np.nan)
    if not np.isnan(dd) and dd < MAX_DRAWDOWN_LIMIT:
        return f"max_drawdown={dd:.2f} < {MAX_DRAWDOWN_LIMIT}"

    ret = row.get("return_ann_real", np.nan)
    if not np.isnan(ret) and ret < MIN_REAL_RETURN:
        return f"return_ann_real={ret:.3f} < 0"

    if subportfolio == "Defensiva":
        srri = row.get("srri_nav", np.nan)
        if not np.isnan(srri) and srri > MAX_SRRI_DEFENSIVE:
            return f"srri={int(srri)} > {MAX_SRRI_DEFENSIVE} para Defensiva"

    return None


# ============================================================
# Motor principal de scoring
# ============================================================

def score_funds(
    conn: sqlite3.Connection,
    regime_result: RegimeResult,
    score_version: str = "v1",
    dry_run: bool = False,
) -> pd.DataFrame:
    """
    Calcula el score de todos los fondos para el regimen dado.

    Devuelve DataFrame con una fila por (fondo, sub-cartera) con columnas:
        isin, fund_nature, subportfolio, score_base, score_final,
        eligible, exclusion_reason, multiplier

    Si dry_run=False, persiste en fund_scores.
    """
    regime = regime_result.regime
    print(f"Scoring | Regimen: {regime} | version: {score_version}")

    # Cargar metricas
    df = load_fund_metrics_for_scoring(conn)
    if df.empty:
        print("ERROR: No hay metricas disponibles en fund_metrics.")
        return pd.DataFrame()

    print(f"Fondos con metricas: {len(df)}")

    # Calcular scores base normalizados por naturaleza de fondo
    base_scores = {}
    for nature in df["Fund_Nature"].dropna().unique():
        subset = df[df["Fund_Nature"] == nature]
        base_scores_nature = compute_base_scores(subset)
        base_scores.update(base_scores_nature.to_dict())

    base_scores_series = pd.Series(base_scores)

    # Construir resultados
    results = []

    for nature, sub_list in SUBPORTFOLIO_MAPPING.items():
        # Fondos elegibles para esta sub-cartera
        mask = df["Fund_Nature"].isin(sub_list)
        subset = df[mask]

        for isin, row in subset.iterrows():
            score_base = float(base_scores_series.get(isin, 0.0))

            # Filtros duros
            excl = check_hard_filters(row, nature)

            # Multiplicador de regimen
            mult, mult_detail = compute_regime_multiplier(row, regime)

            # Score final
            score_final = score_base * mult if excl is None else 0.0

            detail = {
                "score_base":   round(score_base, 4),
                "multiplier":   mult,
                "mult_detail":  mult_detail,
                "metrics": {
                    "return_ann_real":   round(float(row.get("return_ann_real", np.nan)), 4)
                                         if not np.isnan(row.get("return_ann_real", np.nan)) else None,
                    "sharpe":            round(float(row.get("sharpe", np.nan)), 4)
                                         if not np.isnan(row.get("sharpe", np.nan)) else None,
                    "max_drawdown":      round(float(row.get("max_drawdown", np.nan)), 4)
                                         if not np.isnan(row.get("max_drawdown", np.nan)) else None,
                    "alpha_persistence": round(float(row.get("alpha_persistence", np.nan)), 4)
                                         if not np.isnan(row.get("alpha_persistence", np.nan)) else None,
                    "capture_ratio":     round(float(row.get("capture_ratio", np.nan)), 4)
                                         if not np.isnan(row.get("capture_ratio", np.nan)) else None,
                    "srri":              int(row.get("srri_nav", 0))
                                         if not np.isnan(row.get("srri_nav", np.nan)) else None,
                }
            }

            results.append({
                "isin":             isin,
                "fund_name":        row.get("Fund_Name", ""),
                "fund_nature":      row.get("Fund_Nature", ""),
                "subportfolio":     nature,
                "score_base":       round(score_base, 4),
                "multiplier":       mult,
                "score_final":      round(score_final, 4),
                "eligible":         excl is None,
                "exclusion_reason": excl,
                "detail":           detail,
            })

    df_results = pd.DataFrame(results)

    if not dry_run and not df_results.empty:
        _persist_scores(conn, df_results, regime, score_version)

    eligible = df_results[df_results["eligible"]].shape[0]
    print(f"Fondos scored: {len(df_results)} | Elegibles: {eligible}")
    return df_results


# ============================================================
# Persistencia en fund_scores
# ============================================================

def _persist_scores(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    regime: str,
    score_version: str,
) -> None:
    """Persiste los scores en fund_scores."""
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    sql = """
        INSERT OR REPLACE INTO fund_scores
            (isin, block, score_version, score_total,
             score_detail, eligible, calculated_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    rows = []
    for _, r in df.iterrows():
        rows.append((
            r["isin"],
            r["subportfolio"],
            score_version,
            r["score_final"],
            json.dumps(r["detail"], ensure_ascii=False),
            1 if r["eligible"] else 0,
            today,
            f"regime={regime}" + (
                f" | excluido: {r['exclusion_reason']}"
                if r["exclusion_reason"] else ""
            ),
        ))
    conn.executemany(sql, rows)
    conn.commit()
    print(f"Persistidos {len(rows)} scores en fund_scores.")
