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

# Pesos diferenciados por sub-cartera
SUBPORTFOLIO_WEIGHTS = {
    "Defensiva": {
        "return_ann_real":   0.20,
        "sharpe":            0.25,
        "max_drawdown":      0.30,
        "alpha_persistence": 0.15,
        "capture_ratio":     0.05,
        "momentum_rank":     0.05,
    },
    "Equilibrada": {
        "return_ann_real":   0.25,
        "sharpe":            0.20,
        "max_drawdown":      0.20,
        "alpha_persistence": 0.15,
        "capture_ratio":     0.10,
        "momentum_rank":     0.10,
    },
    "Dinamica": {
        "return_ann_real":   0.30,
        "sharpe":            0.15,
        "max_drawdown":      0.15,
        "alpha_persistence": 0.15,
        "capture_ratio":     0.15,
        "momentum_rank":     0.10,
    },
}

# Bonus por naturaleza del fondo segun sub-cartera
NATURE_PROFILE_BONUS = {
    "Defensiva": {
        "Monetario":              1.25,
        "Renta Fija Corto Plazo": 1.10,
        "Renta Fija Flexible":    1.00,
    },
    "Equilibrada": {
        "Mixtos":              1.10,
        "Renta Variable":      1.00,
        "Renta Fija Flexible": 0.95,
    },
    "Dinamica": {
        "Renta Variable": 1.10,
        "Alternativo":    1.05,
        "Mixtos":         0.95,
    },
}

# Filtros duros por sub-cartera
MAX_DRAWDOWN_BY_SUB = {
    "Defensiva":   -0.20,
    "Equilibrada": -0.30,
    "Dinamica":    -0.40,
}

# Retorno real minimo por sub-cartera
# Defensiva acepta retorno real negativo (monetarios en entorno inflacionario)
# El objetivo real se penaliza via scoring, no se excluye
MIN_REAL_RETURN_BY_SUB = {
    "Defensiva":   -0.05,   # tolerar hasta -5% real (monetarios en alta inflacion)
    "Equilibrada": -0.02,   # tolerar hasta -2% real
    "Dinamica":     0.00,   # exigir retorno real positivo
}

# Filtros duros globales
MAX_DRAWDOWN_LIMIT   = -0.25   # fallback si sub no definido
MIN_REAL_RETURN      = 0.00    # fallback si sub no definido
MIN_MACRO_OBS        = 36      # minimo observaciones para betas fiables
MAX_SRRI_DEFENSIVE   = 5       # SRRI maximo en cartera defensiva

# Umbrales multiplicadores de regimen
BETA_OIL_THRESHOLD      =  0.01   # beta_oil > umbral -> bonus energetico
BETA_RATE_EU_THRESHOLD  = -0.10   # beta_rate_eu < umbral -> penalizacion
FX_CONTRIBUTION_LIMIT   =  0.60   # fx_pct > limite -> penalizacion divisa
ALPHA_PERS_THRESHOLD    =  0.60   # alpha_persistence > umbral -> bonus
MACRO_R2_LIMIT          =  0.50   # macro_r2 > limite -> penalizacion

# Umbrales Crisis_Financiera (pipeline v10)
SPREAD_HY_CRISIS_THRESHOLD = 0.01    # beta_spread_hy > umbral -> penalizacion
VIX_CRISIS_THRESHOLD       = 0.05    # beta_vix > umbral -> penalizacion
SPREAD_HY_HEDGE_THRESHOLD  = -0.005  # beta_spread_hy < umbral -> bonus cobertura

# Umbrales regime_returns
REGIME_RETURN_TOP_Q    = 0.75   # percentil >= 75% -> bonus por buen historial en regimen
REGIME_RETURN_BOT_Q    = 0.25   # percentil <= 25% -> malus por mal historial en regimen
MIN_OBS_REGIME_SCORING = 12     # min meses en regimen para aplicar bonus/malus

# Multiplicadores
MULT_OIL_BONUS               = 1.20
MULT_RATE_EU_MALUS           = 0.70
MULT_FX_MALUS                = 0.80
MULT_ALPHA_BONUS             = 1.15
MULT_MACRO_MALUS             = 0.85
MULT_CRISIS_SPREAD_MALUS     = 0.75   # fondo amplifica crisis crediticia
MULT_CRISIS_SPREAD_BONUS     = 1.20   # fondo cubre crisis crediticia
MULT_REGIME_RETURN_BONUS     = 1.15   # buen historial en regimen activo
MULT_REGIME_RETURN_MALUS     = 0.85   # mal historial en regimen activo

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

def load_fund_metrics_for_scoring(
    conn: sqlite3.Connection,
    regime: str | None = None,
) -> pd.DataFrame:
    """
    Carga todas las metricas necesarias para el scoring desde fund_metrics.
    Devuelve DataFrame indexado por ISIN con una columna por metrica.

    regime: si se proporciona, carga tambien las metricas de rendimiento
            historico en ese regimen (return_ann_{regime}, sharpe_{regime},
            n_obs_{regime}) para usar como multiplicadores empiricos.
    """
    # Metricas estaticas (independientes del regimen)
    metrics_needed = [
        ("return_ann",          "since_inception", 1),  # real
        ("sharpe",              "since_inception", 0),
        ("max_drawdown",        "since_inception", 0),
        ("alpha_persistence",   "since_inception", 0),
        ("capture_ratio",       "since_inception", 0),
        ("momentum_rank",       "since_inception", 0),
        ("beta_oil",            "since_inception", 0),
        ("beta_rate_eu",        "since_inception", 0),
        ("beta_spread_hy",      "since_inception", 0),  # v10: Crisis_Financiera
        ("beta_vix",            "since_inception", 0),  # v10: Crisis_Financiera
        ("fx_contribution_pct", "since_inception", 0),
        ("macro_r2",            "since_inception", 0),
        ("macro_n_obs",         "since_inception", 0),
        ("srri_nav",            "since_inception", 0),
        ("volatility_ann",      "since_inception", 0),
    ]

    # Metricas dinamicas por regimen (si se conoce el regimen activo)
    if regime:
        from proyecto3.src.regime_classifier import _REGIME_SUFFIX
        suffix = _REGIME_SUFFIX.get(regime)
        if suffix:
            metrics_needed += [
                (f"return_ann_{suffix}", "since_inception", 0),
                (f"sharpe_{suffix}",     "since_inception", 0),
                (f"n_obs_{suffix}",      "since_inception", 0),
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

def compute_base_scores(
    df: pd.DataFrame,
    subportfolio: str = "Equilibrada",
) -> pd.Series:
    """
    Calcula el score base [0,1] para cada fondo usando pesos diferenciados
    por sub-cartera, con bonus por adecuacion al perfil.
    """
    weights = SUBPORTFOLIO_WEIGHTS.get(subportfolio, BASE_WEIGHTS)
    nature_bonus = NATURE_PROFILE_BONUS.get(subportfolio, {})

    # Paso 1: normalizar por naturaleza de fondo (cada tipo compite contra sus iguales)
    scores_by_nature = pd.Series(0.0, index=df.index)

    if "Fund_Nature" in df.columns:
        for nature in df["Fund_Nature"].dropna().unique():
            nature_mask = df["Fund_Nature"] == nature
            subset_n    = df[nature_mask]
            scores_n    = pd.Series(0.0, index=subset_n.index)

            for metric, weight in weights.items():
                if metric not in subset_n.columns:
                    continue
                col = subset_n[metric].dropna()
                if col.empty:
                    continue
                invert     = (metric == "max_drawdown")
                normalized = _normalize_metric(col, invert=invert)
                scores_n   = scores_n.add(normalized * weight, fill_value=0)

            scores_by_nature = scores_by_nature.add(scores_n, fill_value=0)
    else:
        # Fallback: normalizar todo junto
        for metric, weight in weights.items():
            if metric not in df.columns:
                continue
            col = df[metric].dropna()
            if col.empty:
                continue
            invert     = (metric == "max_drawdown")
            normalized = _normalize_metric(col, invert=invert)
            scores_by_nature = scores_by_nature.add(normalized * weight, fill_value=0)

    # Paso 2: aplicar bonus de naturaleza para escalar entre tipos
    if nature_bonus and "Fund_Nature" in df.columns:
        bonus_series     = df["Fund_Nature"].map(nature_bonus).fillna(1.0)
        scores_by_nature = scores_by_nature * bonus_series

    return scores_by_nature


# ============================================================
# Multiplicadores de regimen
# ============================================================

def compute_regime_multiplier(
    row: pd.Series,
    regime: str,
    regime_return_p25: float | None = None,
    regime_return_p75: float | None = None,
) -> tuple[float, dict]:
    """
    Calcula el multiplicador de regimen para un fondo.
    Devuelve (multiplicador, detalle_dict).

    regime_return_p25 / p75: percentiles del universo para el return
    en el regimen activo. Si se proveen, se aplica bonus/malus empirico.
    """
    multiplier = 1.0
    detail = {}

    # ── Crisis_Financiera (regimen nuevo v10) ─────────────────────────────────
    # Prioridad maxima: penalizar fondos que amplifican el estres crediticio
    # y premiar los que lo cubren (beta_spread_hy negativa).
    if regime == "Crisis_Financiera":
        beta_spread = row.get("beta_spread_hy", np.nan)
        if not np.isnan(beta_spread):
            if beta_spread > SPREAD_HY_CRISIS_THRESHOLD:
                multiplier *= MULT_CRISIS_SPREAD_MALUS
                detail["crisis_spread_malus"] = MULT_CRISIS_SPREAD_MALUS
            elif beta_spread < SPREAD_HY_HEDGE_THRESHOLD:
                multiplier *= MULT_CRISIS_SPREAD_BONUS
                detail["crisis_spread_bonus"] = MULT_CRISIS_SPREAD_BONUS
        beta_vix = row.get("beta_vix", np.nan)
        if not np.isnan(beta_vix) and beta_vix > VIX_CRISIS_THRESHOLD:
            multiplier *= MULT_CRISIS_SPREAD_MALUS
            detail["crisis_vix_malus"] = MULT_CRISIS_SPREAD_MALUS

    # ── Bonus cobertura energetica ────────────────────────────────────────────
    if regime in ("Shock_Energetico", "Estanflacion", "Recalentamiento"):
        beta_oil = row.get("beta_oil", np.nan)
        if not np.isnan(beta_oil) and beta_oil > BETA_OIL_THRESHOLD:
            multiplier *= MULT_OIL_BONUS
            detail["beta_oil_bonus"] = MULT_OIL_BONUS

    # ── Penalizacion sensibilidad tipos BCE ───────────────────────────────────
    if regime in ("Recalentamiento_Tardio", "Shock_Energetico", "Estanflacion"):
        beta_rate = row.get("beta_rate_eu", np.nan)
        if not np.isnan(beta_rate) and beta_rate < BETA_RATE_EU_THRESHOLD:
            multiplier *= MULT_RATE_EU_MALUS
            detail["beta_rate_eu_malus"] = MULT_RATE_EU_MALUS

    # ── Penalizacion exceso divisa ────────────────────────────────────────────
    fx_pct = row.get("fx_contribution_pct", np.nan)
    if not np.isnan(fx_pct) and abs(fx_pct) > FX_CONTRIBUTION_LIMIT:
        multiplier *= MULT_FX_MALUS
        detail["fx_malus"] = MULT_FX_MALUS

    # ── Bonus gestor consistente ──────────────────────────────────────────────
    alpha_pers = row.get("alpha_persistence", np.nan)
    if not np.isnan(alpha_pers) and alpha_pers > ALPHA_PERS_THRESHOLD:
        multiplier *= MULT_ALPHA_BONUS
        detail["alpha_persistence_bonus"] = MULT_ALPHA_BONUS

    # ── Penalizacion alta dependencia macro ───────────────────────────────────
    macro_r2 = row.get("macro_r2", np.nan)
    if not np.isnan(macro_r2) and macro_r2 > MACRO_R2_LIMIT:
        multiplier *= MULT_MACRO_MALUS
        detail["macro_r2_malus"] = MULT_MACRO_MALUS

    # ── Bonus/malus empirico por historial en regimen activo ──────────────────
    # Sustituye paulatinamente los parches P01-P03.
    # Solo aplica si: (a) tenemos los percentiles del universo,
    # (b) el fondo tiene suficientes observaciones en este regimen,
    # (c) el return historico en el regimen esta disponible.
    if regime_return_p25 is not None and regime_return_p75 is not None:
        from proyecto3.src.regime_classifier import _REGIME_SUFFIX
        suffix = _REGIME_SUFFIX.get(regime)
        if suffix:
            n_obs   = row.get(f"n_obs_{suffix}", np.nan)
            ret_reg = row.get(f"return_ann_{suffix}", np.nan)
            if (not np.isnan(n_obs) and n_obs >= MIN_OBS_REGIME_SCORING and
                    not np.isnan(ret_reg)):
                if ret_reg >= regime_return_p75:
                    multiplier *= MULT_REGIME_RETURN_BONUS
                    detail["regime_return_bonus"] = MULT_REGIME_RETURN_BONUS
                elif ret_reg <= regime_return_p25:
                    multiplier *= MULT_REGIME_RETURN_MALUS
                    detail["regime_return_malus"] = MULT_REGIME_RETURN_MALUS

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
    dd_limit = MAX_DRAWDOWN_BY_SUB.get(subportfolio, MAX_DRAWDOWN_LIMIT)
    if not np.isnan(dd) and dd < dd_limit:
        return f"max_drawdown={dd:.2f} < {dd_limit} ({subportfolio})"

    ret = row.get("return_ann_real", np.nan)
    ret_limit = MIN_REAL_RETURN_BY_SUB.get(subportfolio, MIN_REAL_RETURN)
    if not np.isnan(ret) and ret < ret_limit:
        return f"return_ann_real={ret:.3f} < {ret_limit} ({subportfolio})"

    if subportfolio == "Defensiva":
        srri = row.get("srri_nav", np.nan)
        if not np.isnan(srri) and srri > MAX_SRRI_DEFENSIVE:
            return f"srri={int(srri)} > {MAX_SRRI_DEFENSIVE} para Defensiva"

    return None


# ============================================================
# Motor principal de scoring
# ============================================================


def deduplicate_by_family(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cuando un fondo tiene multiples clases en el universo de scoring,
    conserva solo la clase con mejor score_base por familia.

    Logica:
    - Fondos con fund_family_id: agrupar por (family_id, Fund_Nature, subportfolio)
      y conservar la clase con mayor NAV_count (más histórico) como representante.
    - Fondos sin fund_family_id (singletons): no se tocan.

    Se aplica DESPUES de compute_base_scores y ANTES de _persist_scores,
    de forma que el score final sea el de la mejor clase pero solo aparece
    una vez por familia en fund_scores.

    Devuelve DataFrame deduplicado con columna 'family_representative' (bool).
    """
    if "fund_family_id" not in df.columns or df["fund_family_id"].isna().all():
        df["family_representative"] = True
        return df

    df = df.copy()
    df["family_representative"] = False

    # Fondos sin family_id: siempre representantes
    mask_no_fam = df["fund_family_id"].isna() | (df["fund_family_id"].astype(str) == "")
    df.loc[mask_no_fam, "family_representative"] = True

    # Fondos con family_id: el representante es el de mayor score_final
    # (o mayor nav_count si scores iguales — más histórico = más fiable)
    fam_groups = df[~mask_no_fam].groupby(
        ["fund_family_id", "subportfolio"],
        group_keys=False,
    )
    for (fam_id, subportfolio), group in fam_groups:
        if len(group) == 1:
            df.loc[group.index, "family_representative"] = True
            continue

        # Criterio: mayor score_final, desempate por nav_count
        sort_cols = ["score_final", "nav_count"] if "nav_count" in group.columns                     else ["score_final"]
        best_idx = group.sort_values(sort_cols, ascending=False).index[0]
        df.loc[best_idx, "family_representative"] = True

    n_total = len(df)
    n_repr  = df["family_representative"].sum()
    n_dedup = n_total - n_repr
    if n_dedup > 0:
        print(f"  [Scorer] Deduplicacion por familia: "
              f"{n_repr}/{n_total} clases representantes "
              f"({n_dedup} clases secundarias excluidas de scoring final)")

    return df[df["family_representative"]].drop(columns=["family_representative"])


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

    # Cargar metricas (incluyendo las del regimen activo si existen)
    df = load_fund_metrics_for_scoring(conn, regime=regime)
    if df.empty:
        print("ERROR: No hay metricas disponibles en fund_metrics.")
        return pd.DataFrame()

    print(f"Fondos con metricas: {len(df)}")

    # Pre-calcular percentiles del return en regimen activo para bonus/malus empirico
    # Solo si tenemos suficientes datos del regimen (min 20 fondos con observaciones)
    regime_p25 = None
    regime_p75 = None
    try:
        from proyecto3.src.regime_classifier import _REGIME_SUFFIX
        suffix = _REGIME_SUFFIX.get(regime)
        if suffix:
            ret_col  = f"return_ann_{suffix}"
            nobs_col = f"n_obs_{suffix}"
            if ret_col in df.columns and nobs_col in df.columns:
                valid = df[
                    (df[nobs_col] >= MIN_OBS_REGIME_SCORING) &
                    df[ret_col].notna()
                ][ret_col]
                if len(valid) >= 20:
                    regime_p25 = float(valid.quantile(REGIME_RETURN_BOT_Q))
                    regime_p75 = float(valid.quantile(REGIME_RETURN_TOP_Q))
                    print(f"  Regime returns [{regime}]: "
                          f"p25={regime_p25:.2%} p75={regime_p75:.2%} "
                          f"(n={len(valid)} fondos)")
    except Exception:
        pass

    # Calcular scores base normalizados por naturaleza de fondo
    base_scores = {}
    for nature in df["Fund_Nature"].dropna().unique():
        subset = df[df["Fund_Nature"] == nature]
        base_scores_nature = compute_base_scores(subset)
        base_scores.update(base_scores_nature.to_dict())

    # Construir resultados
    results = []

    for nature, sub_list in SUBPORTFOLIO_MAPPING.items():
        # Fondos elegibles para esta sub-cartera
        mask = df["Fund_Nature"].isin(sub_list)
        subset = df[mask]

        # Scores con pesos especificos de esta sub-cartera
        sub_scores = compute_base_scores(subset, subportfolio=nature)

        for isin, row in subset.iterrows():
            score_base = float(sub_scores.get(isin, 0.0))

            # Filtros duros
            excl = check_hard_filters(row, nature)

            # Multiplicador de regimen (incluye bonus/malus empirico si hay datos)
            mult, mult_detail = compute_regime_multiplier(
                row, regime,
                regime_return_p25=regime_p25,
                regime_return_p75=regime_p75,
            )

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

    # Deduplicar por familia — conservar mejor clase por fondo real
    if not df_results.empty:
        df_results = deduplicate_by_family(df_results)

    if not dry_run and not df_results.empty:
        _persist_scores(conn, df_results, regime, score_version)

    eligible = df_results[df_results["eligible"]].shape[0]
    n_families = df_results["fund_family_id"].nunique()                  if "fund_family_id" in df_results.columns else "n/a"
    print(f"Fondos scored: {len(df_results)} | "
          f"Elegibles: {eligible} | "
          f"Familias unicas: {n_families}")
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
