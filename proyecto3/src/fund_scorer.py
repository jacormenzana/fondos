# proyecto3/src/fund_scorer.py  — v17
# -*- coding: utf-8 -*-
"""
Motor de scoring de fondos para P3.

Calcula el score compuesto de cada fondo combinando:
  - Capa 1: Filtros duros (exclusión automática)
  - Capa 2: Scoring base (métricas intrínsecas P2)
  - Capa 3: Multiplicadores de régimen macro

El score final determina la elegibilidad y ranking de cada fondo
dentro de su sub-cartera (Defensiva, Equilibrada, Dinámica).

Pesos del scoring base (horizon=since_inception):
    return_ann real      Defensiva 20% / Equilibrada 25% / Dinámica 30%
    sharpe               Defensiva 25% / Equilibrada 20% / Dinámica 15%
    max_drawdown         Defensiva 30% / Equilibrada 20% / Dinámica 15%
    alpha_persistence    15% en todas
    capture_ratio        Defensiva  5% / Equilibrada 10% / Dinámica 15%
    momentum_rank        Defensiva  5% / Equilibrada 10% / Dinámica 10%

Multiplicadores de régimen (por perfil de sensibilidad):
    beta_oil > 0.01              x1.20  cobertura energética
    beta_rate_eu < -0.10         x0.70  muy sensible a BCE
    fx_contribution_pct > 0.60   x0.80  retorno mayormente divisa
    alpha_persistence > 0.60     x1.15  gestor consistente
    macro_r2 > 0.50              x0.85  muy determinado por macro

Filtros duros:
    max_drawdown < límite_sub    excluir (riesgo de ruina)
    return_ann real < límite_sub excluir (destruye patrimonio real)
    srri_nav > 5 en Defensiva    excluir
    Credit_Quality=High Yield en Defensiva  excluir (v17)

Cambios v17:
  - load_fund_metrics_for_scoring: SELECT fund_master ampliado con
    Investment_Focus, Credit_Quality, Ongoing_Charge, SRRI_Quality_Flag
  - check_hard_filters: filtro Credit_Quality='High Yield' en Defensiva
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

# Pesos scoring base globales (fallback)
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

# Bonus por naturaleza del fondo según sub-cartera
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

# Retorno real mínimo por sub-cartera
MIN_REAL_RETURN_BY_SUB = {
    "Defensiva":   -0.05,   # tolerar hasta -5% real (monetarios en alta inflación)
    "Equilibrada": -0.02,
    "Dinamica":     0.00,
}

# Filtros duros globales
MAX_DRAWDOWN_LIMIT = -0.25
MIN_REAL_RETURN    =  0.00
MIN_MACRO_OBS      = 36
MAX_SRRI_DEFENSIVE = 5

# Umbrales multiplicadores de régimen
BETA_OIL_THRESHOLD      =  0.01
BETA_RATE_EU_THRESHOLD  = -0.10
FX_CONTRIBUTION_LIMIT   =  0.60
ALPHA_PERS_THRESHOLD    =  0.60
MACRO_R2_LIMIT          =  0.50

# Multiplicadores
MULT_OIL_BONUS     = 1.20
MULT_RATE_EU_MALUS = 0.70
MULT_FX_MALUS      = 0.80
MULT_ALPHA_BONUS   = 1.15
MULT_MACRO_MALUS   = 0.85

# Crisis Financiera (v10)
SPREAD_HY_CRISIS_THRESHOLD =  0.02
SPREAD_HY_HEDGE_THRESHOLD  = -0.01
VIX_CRISIS_THRESHOLD       =  0.02
MULT_CRISIS_SPREAD_MALUS   =  0.60
MULT_CRISIS_SPREAD_BONUS   =  1.30

# Bonus/malus empírico por régimen
MULT_REGIME_RETURN_BONUS = 1.20
MULT_REGIME_RETURN_MALUS = 0.80
MIN_OBS_REGIME_SCORING   = 12

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
    subportfolio:      str
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
# Carga de métricas P2  (v17 — SELECT ampliado)
# ============================================================

def load_fund_metrics_for_scoring(
    conn: sqlite3.Connection,
    regime: str | None = None,
) -> pd.DataFrame:
    """
    Carga todas las métricas necesarias para el scoring desde fund_metrics.
    Devuelve DataFrame indexado por ISIN con una columna por métrica.

    regime: si se proporciona, carga también las métricas históricas
            del régimen activo (return_ann_{suffix}, sharpe_{suffix},
            n_obs_{suffix}) para usar como multiplicadores empíricos.

    v17: SELECT fund_master ampliado con Investment_Focus, Credit_Quality,
         Ongoing_Charge y SRRI_Quality_Flag.
    """
    # Métricas estáticas (independientes del régimen)
    metrics_needed = [
        ("return_ann",          "since_inception", 1),  # real
        ("sharpe",              "since_inception", 0),
        ("max_drawdown",        "since_inception", 0),
        ("alpha_persistence",   "since_inception", 0),
        ("capture_ratio",       "since_inception", 0),
        ("momentum_rank",       "since_inception", 0),
        ("beta_oil",            "since_inception", 0),
        ("beta_rate_eu",        "since_inception", 0),
        ("beta_spread_hy",      "since_inception", 0),  # Crisis_Financiera
        ("beta_vix",            "since_inception", 0),  # Crisis_Financiera
        ("fx_contribution_pct", "since_inception", 0),
        ("macro_r2",            "since_inception", 0),
        ("macro_n_obs",         "since_inception", 0),
        ("srri_nav",            "since_inception", 0),
        ("volatility_ann",      "since_inception", 0),
    ]

    # Métricas dinámicas por régimen
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

    # Añadir atributos de fund_master (v17: +Investment_Focus, +Credit_Quality, +Ongoing_Charge, +SRRI_Quality_Flag)
    fm = pd.read_sql("""
        SELECT ISIN, Fund_Name, Fund_Nature, SRRI as srri_kiid,
               Investment_Focus, Credit_Quality,
               Ongoing_Charge, SRRI_Quality_Flag,
               fund_family_id
        FROM fund_master
    """, conn).set_index("ISIN")

    return wide.join(fm, how="left")


# ============================================================
# Normalización de métricas
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
    por sub-cartera, con bonus por adecuación al perfil.

    Normalización por naturaleza: cada tipo de fondo compite contra sus iguales
    antes de recibir el bonus de naturaleza para escalar entre tipos.
    """
    weights      = SUBPORTFOLIO_WEIGHTS.get(subportfolio, BASE_WEIGHTS)
    nature_bonus = NATURE_PROFILE_BONUS.get(subportfolio, {})

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

    # Bonus por naturaleza
    if nature_bonus and "Fund_Nature" in df.columns:
        bonus_series     = df["Fund_Nature"].map(nature_bonus).fillna(1.0)
        scores_by_nature = scores_by_nature * bonus_series

    return scores_by_nature


# ============================================================
# Multiplicadores de régimen
# ============================================================

def compute_regime_multiplier(
    row: pd.Series,
    regime: str,
    regime_return_p25: float | None = None,
    regime_return_p75: float | None = None,
) -> tuple[float, dict]:
    """
    Calcula el multiplicador de régimen para un fondo.
    Devuelve (multiplicador, detalle_dict).
    """
    multiplier = 1.0
    detail: dict = {}

    # ── Crisis_Financiera ─────────────────────────────────────────────────────
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

    # ── Bonus cobertura energética ────────────────────────────────────────────
    if regime in ("Shock_Energetico", "Estanflacion", "Recalentamiento"):
        beta_oil = row.get("beta_oil", np.nan)
        if not np.isnan(beta_oil) and beta_oil > BETA_OIL_THRESHOLD:
            multiplier *= MULT_OIL_BONUS
            detail["beta_oil_bonus"] = MULT_OIL_BONUS

    # ── Penalización sensibilidad tipos BCE ───────────────────────────────────
    if regime in ("Recalentamiento_Tardio", "Shock_Energetico", "Estanflacion"):
        beta_rate = row.get("beta_rate_eu", np.nan)
        if not np.isnan(beta_rate) and beta_rate < BETA_RATE_EU_THRESHOLD:
            multiplier *= MULT_RATE_EU_MALUS
            detail["beta_rate_eu_malus"] = MULT_RATE_EU_MALUS

    # ── Penalización exceso divisa ────────────────────────────────────────────
    fx_pct = row.get("fx_contribution_pct", np.nan)
    if not np.isnan(fx_pct) and abs(fx_pct) > FX_CONTRIBUTION_LIMIT:
        multiplier *= MULT_FX_MALUS
        detail["fx_malus"] = MULT_FX_MALUS

    # ── Bonus gestor consistente ──────────────────────────────────────────────
    alpha_pers = row.get("alpha_persistence", np.nan)
    if not np.isnan(alpha_pers) and alpha_pers > ALPHA_PERS_THRESHOLD:
        multiplier *= MULT_ALPHA_BONUS
        detail["alpha_persistence_bonus"] = MULT_ALPHA_BONUS

    # ── Penalización alta dependencia macro ───────────────────────────────────
    macro_r2 = row.get("macro_r2", np.nan)
    if not np.isnan(macro_r2) and macro_r2 > MACRO_R2_LIMIT:
        multiplier *= MULT_MACRO_MALUS
        detail["macro_r2_malus"] = MULT_MACRO_MALUS

    # ── Bonus/malus empírico por historial en régimen activo ──────────────────
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
# Filtros duros  (v17 — añade Credit_Quality para Defensiva)
# ============================================================

def check_hard_filters(
    row: pd.Series,
    subportfolio: str,
) -> str | None:
    """
    Verifica los filtros duros.
    Devuelve None si pasa todos, o el motivo de exclusión.

    v17: Credit_Quality='High Yield' excluido de Defensiva.
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

        # v17: High Yield incompatible con Defensiva por riesgo crediticio
        cq = row.get("Credit_Quality")
        if cq == "High Yield":
            return "Credit_Quality=High Yield excluido de Defensiva"

    return None


# ============================================================
# Deduplicación por familia
# ============================================================

def deduplicate_by_family(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cuando un fondo tiene múltiples clases en el universo de scoring,
    conserva solo la clase con mejor score_final por familia.

    Aplica DESPUÉS de compute_base_scores y ANTES de _persist_scores.
    """
    if "fund_family_id" not in df.columns or df["fund_family_id"].isna().all():
        df["family_representative"] = True
        return df

    df = df.copy()
    df["family_representative"] = False

    mask_no_fam = df["fund_family_id"].isna() | (df["fund_family_id"].astype(str) == "")
    df.loc[mask_no_fam, "family_representative"] = True

    fam_groups = df[~mask_no_fam].groupby(
        ["fund_family_id", "subportfolio"],
        group_keys=False,
    )
    for (fam_id, subportfolio), group in fam_groups:
        if len(group) == 1:
            df.loc[group.index, "family_representative"] = True
            continue

        sort_cols = (["score_final", "nav_count"]
                     if "nav_count" in group.columns
                     else ["score_final"])
        best_idx = group.sort_values(sort_cols, ascending=False).index[0]
        df.loc[best_idx, "family_representative"] = True

    n_total = len(df)
    n_repr  = df["family_representative"].sum()
    n_dedup = n_total - n_repr
    if n_dedup > 0:
        print(f"  [Scorer] Deduplicación por familia: "
              f"{n_repr}/{n_total} clases representantes "
              f"({n_dedup} clases secundarias excluidas)")

    return df[df["family_representative"]].drop(columns=["family_representative"])


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
    Calcula el score de todos los fondos para el régimen dado.

    Devuelve DataFrame con una fila por (fondo, sub-cartera) con columnas:
        isin, fund_nature, subportfolio, score_base, score_final,
        eligible, exclusion_reason, multiplier

    Si dry_run=False, persiste en fund_scores.
    """
    regime = regime_result.regime
    print(f"Scoring | Régimen: {regime} | versión: {score_version}")

    df = load_fund_metrics_for_scoring(conn, regime=regime)
    if df.empty:
        print("ERROR: No hay métricas disponibles en fund_metrics.")
        return pd.DataFrame()

    print(f"Fondos con métricas: {len(df)}")

    # Percentiles del régimen activo (para bonus/malus empírico)
    from proyecto3.src.regime_classifier import _REGIME_SUFFIX
    suffix = _REGIME_SUFFIX.get(regime)
    regime_p25 = regime_p75 = None
    if suffix:
        ret_col = f"return_ann_{suffix}"
        if ret_col in df.columns:
            regime_p25 = df[ret_col].quantile(0.25)
            regime_p75 = df[ret_col].quantile(0.75)

    results = []

    for nature, sub_list in SUBPORTFOLIO_MAPPING.items():
        mask   = df["Fund_Nature"].isin(sub_list)
        subset = df[mask]

        sub_scores = compute_base_scores(subset, subportfolio=nature)

        for isin, row in subset.iterrows():
            score_base = float(sub_scores.get(isin, 0.0))

            excl = check_hard_filters(row, nature)

            mult, mult_detail = compute_regime_multiplier(
                row, regime,
                regime_return_p25=regime_p25,
                regime_return_p75=regime_p75,
            )

            score_final = score_base * mult if excl is None else 0.0

            detail = {
                "score_base":  round(score_base, 4),
                "multiplier":  mult,
                "mult_detail": mult_detail,
                "metrics": {
                    "return_ann_real":   _safe_round(row.get("return_ann_real")),
                    "sharpe":            _safe_round(row.get("sharpe")),
                    "max_drawdown":      _safe_round(row.get("max_drawdown")),
                    "alpha_persistence": _safe_round(row.get("alpha_persistence")),
                    "capture_ratio":     _safe_round(row.get("capture_ratio")),
                    "srri":              _safe_int(row.get("srri_nav")),
                },
            }

            results.append({
                "isin":             isin,
                "fund_name":        row.get("Fund_Name", ""),
                "fund_nature":      row.get("Fund_Nature", ""),
                "fund_family_id":   row.get("fund_family_id"),
                "subportfolio":     nature,
                "score_base":       round(score_base, 4),
                "multiplier":       mult,
                "score_final":      round(score_final, 4),
                "eligible":         excl is None,
                "exclusion_reason": excl,
                "detail":           detail,
            })

    df_results = pd.DataFrame(results)

    if not df_results.empty:
        df_results = deduplicate_by_family(df_results)

    if not dry_run and not df_results.empty:
        _persist_scores(conn, df_results, regime, score_version)

    eligible   = df_results[df_results["eligible"]].shape[0]
    n_families = (df_results["fund_family_id"].nunique()
                  if "fund_family_id" in df_results.columns else "n/a")
    print(f"Fondos scored: {len(df_results)} | "
          f"Elegibles: {eligible} | "
          f"Familias únicas: {n_families}")
    return df_results


# ============================================================
# Helpers
# ============================================================

def _safe_round(val, decimals: int = 4):
    try:
        v = float(val)
        return round(v, decimals) if not np.isnan(v) else None
    except (TypeError, ValueError):
        return None


def _safe_int(val):
    try:
        v = float(val)
        return int(v) if not np.isnan(v) else None
    except (TypeError, ValueError):
        return None


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
