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

P2-03 / Option B — Recalentamiento y Recalentamiento_Tardio (cierre 2026-08-19):
  En la serie macro disponible (2000-03 → 2026-08, 320 meses), los regímenes
  Recalentamiento y Recalentamiento_Tardio registran n_obs=0 porque Shock_Energetico
  (WTI YoY > 25%) preempta todos los periodos de IPC alto. Es un resultado
  estructural del clasificador, no un defecto de código. Confirmado por DB query
  2026-08-14 sobre 3,600 combinaciones ISIN-métrica.
  Comportamiento de fallback (Option B aceptado): cuando regime_return_p25/p75 y
  regime_sharpe_p25/p75 son None (ningún fondo tiene n_obs >= MIN_OBS_REGIME en el
  régimen activo), compute_regime_multiplier() devuelve multiplicador=1.0 para todos
  los fondos — el scoring base rige sin ajuste empírico de régimen. No se requiere
  cambio de umbral ni nueva ejecución del pipeline.
  Véase también: regime_returns.py (nota sobre cobertura, P2-03 / ACT-08 2026-08-18).
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
from shared.config import (
    METRIC_VERSION_SHORT,
    SHORT_HORIZON_SCORING_ENABLED,
    ROLLING_PCTILE_P3_ENABLED,
)


# ============================================================
# Constantes
# ============================================================

# Pesos scoring base globales (fallback)
BASE_WEIGHTS = {
    "return_ann_real":   0.25,
    "sharpe":            0.20,
    "max_dd":      0.20,
    "alpha_persistence": 0.15,
    "capture_ratio":     0.10,
    "momentum_rank":     0.10,
}

# Pesos diferenciados por sub-cartera
SUBPORTFOLIO_WEIGHTS = {
    "Defensiva": {
        "return_ann_real":   0.20,
        "sharpe":            0.25,
        "max_dd":      0.30,
        "alpha_persistence": 0.15,
        "capture_ratio":     0.05,
        "momentum_rank":     0.05,
    },
    "Equilibrada": {
        "return_ann_real":   0.25,
        "sharpe":            0.20,
        "max_dd":      0.20,
        "alpha_persistence": 0.15,
        "capture_ratio":     0.10,
        "momentum_rank":     0.10,
    },
    "Dinamica": {
        "return_ann_real":   0.30,
        "sharpe":            0.15,
        "max_dd":      0.15,
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

# §3f — Crisis stress thresholds (crisis_stress_score_mdd and _ttr)
CRISIS_MDD_SHALLOW   = -0.10   # drawdown ≥ -10% in crisis → resilient
CRISIS_MDD_DEEP      = -0.30   # drawdown ≤ -30% in crisis → vulnerable
CRISIS_TTR_FAST      =  6.0    # recovery ≤ 6 months → resilient
CRISIS_TTR_SLOW      = 24.0    # recovery ≥ 24 months → slow
MULT_CRISIS_MDD_BONUS  = 1.10
MULT_CRISIS_MDD_MALUS  = 0.80
MULT_CRISIS_TTR_BONUS  = 1.10
MULT_CRISIS_TTR_MALUS  = 0.85

# §3f — Regime Sharpe multipliers (modestos: complementan el regime_return)
MULT_REGIME_SHARPE_BONUS = 1.10
MULT_REGIME_SHARPE_MALUS = 0.90

# §3f — Regime Sortino multipliers (downside-risk lens: complementa Sharpe)
MULT_REGIME_SORTINO_BONUS = 1.08   # sortino >= p75 del universo en ese régimen
MULT_REGIME_SORTINO_MALUS = 0.92   # sortino <= p25

# §3f — Regime Max-DD multipliers (capital-destruction lens por régimen)
# max_dd es negativo: mayor valor absoluto = peor (e.g. -0.40 < -0.10).
MULT_REGIME_MAXDD_BONUS  = 1.10   # max_dd_reg >= p75 (menos negativo = menor pérdida)
MULT_REGIME_MAXDD_MALUS  = 0.80   # max_dd_reg <= p25 (más negativo = mayor pérdida)

# Umbral mínimo de regime_coverage_ratio para aplicar multiplicadores empíricos.
# Por debajo del umbral, los multiplicadores de régimen se ponderan hacia 1.0
# (fondo con historia insuficiente en regímenes → no castigar/premiar con pocos datos).
REGIME_COVERAGE_MIN  = 0.43   # ≈ 3 de 7 regímenes con n_obs >= 12
REGIME_COVERAGE_DAMP = 0.50   # fracción del multiplicador neto que se retiene si < MIN

# §3f — Slope trend thresholds (normalized slope: std-devs per period)
SLOPE_IMPROVING_THRESHOLD   =  0.05
SLOPE_DETERIORATING_THRESHOLD = -0.05
MULT_SLOPE_BONUS = 1.05
MULT_SLOPE_MALUS = 0.95

# -- Horizonte corto — gate duro defensivo (v24) -------------------------
# Umbral de drawdown corto (rolling_6m, metric_version='d1') por sub-cartera.
# Fondos que superen la pérdida máxima reciente son excluidos del ciclo.
# None = gate inactivo para esa sub-cartera.
SHORT_DD_LIMIT_BY_SUB: dict[str, float | None] = {
    "Defensiva":   -0.08,   # tolerancia 8% en 6 meses
    "Equilibrada": -0.15,   # tolerancia 15% en 6 meses
    "Dinamica":    -0.25,   # tolerancia 25% en 6 meses
}

# Umbral de volatilidad diaria AC-ajustada (rolling_3m) por sub-cartera.
# Fondos con vol corta superior son excluidos.
# None = gate inactivo.
SHORT_VOL_LIMIT_BY_SUB: dict[str, float | None] = {
    "Defensiva":   0.15,    # 15% vol anualizada en 3 meses
    "Equilibrada": 0.22,
    "Dinamica":    None,    # sin tope de vol para Dinamica
}

# Umbral de iliquidez: si liquidity_flag > este valor, los gates cortos se omiten
# (datos no confiables — fondos con NAV diario sintético).
SHORT_LIQUIDITY_TRUST_THRESHOLD: float = 0.20

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

def _build_score_notes(regime: str, exclusion_reason: str | None) -> str:
    """Construye el campo `notes` persistido en fund_scores.

    Fase 1i (P#11): esta expresion estaba duplicada verbatim entre
    FundScore.to_db_row() y _persist_scores() -- la version dict-based
    (FundScore) nunca llega a instanciarse en score_funds(), que trabaja
    sobre un DataFrame y persiste via _persist_scores() directamente, pero
    ambos caminos construian el mismo string de forma independiente.
    Extraido aqui como el unico punto que lo hace.
    """
    notes = f"regime={regime}"
    if exclusion_reason:
        notes += f" | excluido: {exclusion_reason}"
    return notes


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
            "notes":         _build_score_notes(regime, self.exclusion_reason),
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
        ("max_dd",        "since_inception", 0),
        ("alpha_persistence",   "since_inception", 0),
        ("capture_ratio",       "since_inception", 0),
        ("momentum_rank",       "since_inception", 0),
        ("beta_oil",            "since_inception", 0),
        ("beta_rate_eu",        "since_inception", 0),
        ("beta_spread_hy",      "since_inception", 0),  # Crisis_Financiera
        ("beta_vix",            "since_inception", 0),  # Crisis_Financiera
        ("fx_contribution_pct", "since_inception", 0),
        ("macro_r2",            "since_inception", 0),
        ("srri_nav",            "since_inception", 0),
    ]

    # Crisis stress (always load — used in Crisis_Financiera multiplier)
    metrics_needed += [
        ("crisis_stress_score_mdd", "since_inception", 0),
        ("crisis_stress_score_ttr", "since_inception", 0),
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
                # §3f: downside-risk lenses — computed by regime_returns but unused until now
                (f"sortino_{suffix}",    "since_inception", 0),
                (f"max_dd_{suffix}",     "since_inception", 0),
            ]
        # §3f: coverage guard — how many of the 7 regimes have enough history per fund
        metrics_needed += [
            ("regime_coverage_ratio", "since_inception", 0),
        ]

    # P2-10: rolling percentile signals + slope trends (kill-switched)
    if ROLLING_PCTILE_P3_ENABLED:
        metrics_needed += [
            ("vol_ann_pctile_cat",    "since_inception", 0),
            ("max_dd_pctile_cat",     "since_inception", 0),
            ("return_ann_pctile_cat", "since_inception", 0),
            # §3e slope trends (rolling_3y window, same horizon key)
            ("sharpe_slope",          "rolling_3y",      0),
            ("return_ann_slope",      "rolling_3y",      1),
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

    # -- v24: métricas de horizonte corto (metric_version='d1') ---------------
    # Leemos short_max_drawdown (rolling_6m), short_vol_adj (rolling_3m) y
    # short_liquidity_flag (rolling_6m) para el gate duro. También
    # short_return_cum (rolling_3m/6m) si SHORT_HORIZON_SCORING_ENABLED.
    short_metrics: list[tuple[str, str, int]] = [
        ("short_max_drawdown",   "rolling_6m", 0),
        ("short_vol_adj",        "rolling_3m", 0),
        ("short_liquidity_flag", "rolling_6m", 0),
    ]
    if SHORT_HORIZON_SCORING_ENABLED:
        short_metrics += [
            ("short_return_cum", "rolling_3m", 0),
            ("short_return_cum", "rolling_6m", 0),
        ]
    for metric, horizon, real_flag in short_metrics:
        result = conn.execute("""
            SELECT isin, value
            FROM fund_metrics
            WHERE metric=? AND horizon=? AND real_flag=?
              AND metric_version=?
              AND value IS NOT NULL
        """, (metric, horizon, real_flag, METRIC_VERSION_SHORT)).fetchall()
        # Suffix horizon so rolling_3m / rolling_6m don't collide
        col = f"{metric}__{horizon}" if "rolling" in horizon else metric
        for isin, value in result:
            rows.append({"isin": isin, "metric": col, "value": float(value)})

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    wide = df.pivot_table(index="isin", columns="metric",
                          values="value", aggfunc="last")
    wide.columns.name = None

    # Renombrar return_ann real
    if "return_ann" in wide.columns:
        wide = wide.rename(columns={"return_ann": "return_ann_real"})

    # Añadir atributos de fund_master — solo universo activo (In_Current_Universe=1)
    fm = pd.read_sql("""
        SELECT ISIN, Fund_Name, Fund_Nature, SRRI as srri_kiid,
               Investment_Focus, Credit_Quality,
               Ongoing_Charge_Recurrent AS Ongoing_Charge,
               SRRI_Quality_Flag, fund_family_id
        FROM fund_master
        WHERE In_Current_Universe = 1
    """, conn).set_index("ISIN")

    # inner join: huerfanos (In_Current_Universe=0) quedan excluidos del scoring
    return wide.join(fm, how="inner")


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
    weights      = dict(SUBPORTFOLIO_WEIGHTS.get(subportfolio, BASE_WEIGHTS))
    nature_bonus = NATURE_PROFILE_BONUS.get(subportfolio, {})

    # -- v24: añadir pesos cortos si el kill-switch está activo --------------
    # Pesos modestos (3-5%); columnas con sufijo __horizonte (R-1: no dup).
    # Las métricas de retorno corto son señales de tendencia secundarias.
    if SHORT_HORIZON_SCORING_ENABLED:
        short_w = {
            "Defensiva":   {"short_return_cum__rolling_3m": 0.03,
                            "short_return_cum__rolling_6m": 0.04},
            "Equilibrada": {"short_return_cum__rolling_3m": 0.04,
                            "short_return_cum__rolling_6m": 0.04},
            "Dinamica":    {"short_return_cum__rolling_3m": 0.05,
                            "short_return_cum__rolling_6m": 0.05},
        }
        weights.update(short_w.get(subportfolio, {}))

    # Métricas que deben invertirse (menor RAW = mejor), para
    # _normalize_metric(invert=True) -- Fase 1a (P3 optimization plan):
    # esta lista incluia "max_dd", pero max_dd se almacena en fund_metrics
    # estrictamente <= 0 (verificado en vivo: 57.166 filas, min -0.97, max
    # 0.0, cero valores positivos), así que un valor MENOS negativo (más
    # cercano a 0) ya es el mejor resultado bajo el rank ascendente sin
    # invertir. Invertirlo premiaba el peor drawdown -- sobre la métrica de
    # mayor peso individual en Defensiva (30%). No añadir una métrica aquí
    # sin verificar su signo almacenado primero; ver
    # shared/statistical_audit/catalog_metric_bounds.py para los bounds que
    # documentan la convención de signo de cada métrica (Fase 1b).
    _INVERTED_METRICS: set[str] = set()

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
                invert     = metric in _INVERTED_METRICS
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
            invert     = metric in _INVERTED_METRICS
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
    regime_sharpe_p25: float | None = None,
    regime_sharpe_p75: float | None = None,
    regime_sortino_p25: float | None = None,
    regime_sortino_p75: float | None = None,
    regime_maxdd_p25: float | None = None,
    regime_maxdd_p75: float | None = None,
) -> tuple[float, dict]:
    """
    Calcula el multiplicador de régimen para un fondo.
    Devuelve (multiplicador, detalle_dict).

    Recalentamiento / Recalentamiento_Tardio: n_obs=0 en toda la serie histórica
    disponible (2000-03 → 2026-08, 320 meses). Shock_Energetico (WTI YoY > 25%)
    preempta todos los períodos de IPC alto. Cuando todos los percentiles de régimen
    son None, esta función devuelve multiplicador=1.0 — el score base rige sin ajuste
    empírico. Resultado estructural del clasificador, no un defecto. (P2-03 / ACT-08)
    Véase módulo docstring §P2-03 y regime_returns.py.
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

    # ── P2-10: señales rolling-percentil (kill-switched) ─────────────────────
    # Penaliza fondos en percentiles altos de volatilidad/drawdown recientes;
    # premia los que muestran retorno reciente en percentiles altos.
    # Multipliers deliberadamente modestos (feature no validada aún).
    if ROLLING_PCTILE_P3_ENABLED:
        vol_pct = row.get("vol_ann_pctile_cat", np.nan)
        if not np.isnan(vol_pct) and vol_pct >= 80:
            multiplier *= 0.90
            detail["roll_vol_high_pctile_malus"] = 0.90

        dd_pct = row.get("max_dd_pctile_cat", np.nan)
        if not np.isnan(dd_pct) and dd_pct >= 80:
            multiplier *= 0.90
            detail["roll_dd_high_pctile_malus"] = 0.90

        ret_pct = row.get("return_ann_pctile_cat", np.nan)
        if not np.isnan(ret_pct):
            if ret_pct >= 80:
                multiplier *= 1.10
                detail["roll_return_high_pctile_bonus"] = 1.10
            elif ret_pct <= 20:
                multiplier *= 0.90
                detail["roll_return_low_pctile_malus"] = 0.90

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

    # ── §3f: Bonus/malus por Sharpe histórico en el régimen activo ────────────
    if regime_sharpe_p25 is not None and regime_sharpe_p75 is not None:
        from proyecto3.src.regime_classifier import _REGIME_SUFFIX
        suffix = _REGIME_SUFFIX.get(regime)
        if suffix:
            n_obs      = row.get(f"n_obs_{suffix}", np.nan)
            sharpe_reg = row.get(f"sharpe_{suffix}", np.nan)
            if (not np.isnan(n_obs) and n_obs >= MIN_OBS_REGIME_SCORING and
                    not np.isnan(sharpe_reg)):
                if sharpe_reg >= regime_sharpe_p75:
                    multiplier *= MULT_REGIME_SHARPE_BONUS
                    detail["regime_sharpe_bonus"] = MULT_REGIME_SHARPE_BONUS
                elif sharpe_reg <= regime_sharpe_p25:
                    multiplier *= MULT_REGIME_SHARPE_MALUS
                    detail["regime_sharpe_malus"] = MULT_REGIME_SHARPE_MALUS

    # ── §3f: Sortino por régimen — downside-risk lens ─────────────────────────
    if regime_sortino_p25 is not None and regime_sortino_p75 is not None:
        from proyecto3.src.regime_classifier import _REGIME_SUFFIX
        suffix = _REGIME_SUFFIX.get(regime)
        if suffix:
            n_obs        = row.get(f"n_obs_{suffix}", np.nan)
            sortino_reg  = row.get(f"sortino_{suffix}", np.nan)
            if (not np.isnan(n_obs) and n_obs >= MIN_OBS_REGIME_SCORING and
                    not np.isnan(sortino_reg)):
                if sortino_reg >= regime_sortino_p75:
                    multiplier *= MULT_REGIME_SORTINO_BONUS
                    detail["regime_sortino_bonus"] = MULT_REGIME_SORTINO_BONUS
                elif sortino_reg <= regime_sortino_p25:
                    multiplier *= MULT_REGIME_SORTINO_MALUS
                    detail["regime_sortino_malus"] = MULT_REGIME_SORTINO_MALUS

    # ── §3f: Max-DD por régimen — capital-destruction lens ────────────────────
    if regime_maxdd_p25 is not None and regime_maxdd_p75 is not None:
        from proyecto3.src.regime_classifier import _REGIME_SUFFIX
        suffix = _REGIME_SUFFIX.get(regime)
        if suffix:
            n_obs       = row.get(f"n_obs_{suffix}", np.nan)
            maxdd_reg   = row.get(f"max_dd_{suffix}", np.nan)
            if (not np.isnan(n_obs) and n_obs >= MIN_OBS_REGIME_SCORING and
                    not np.isnan(maxdd_reg)):
                # max_dd is negative: higher (less negative) = less loss = better
                if maxdd_reg >= regime_maxdd_p75:
                    multiplier *= MULT_REGIME_MAXDD_BONUS
                    detail["regime_maxdd_bonus"] = MULT_REGIME_MAXDD_BONUS
                elif maxdd_reg <= regime_maxdd_p25:
                    multiplier *= MULT_REGIME_MAXDD_MALUS
                    detail["regime_maxdd_malus"] = MULT_REGIME_MAXDD_MALUS

    # ── §3f: Crisis Financiera — stress resilience multipliers ────────────────
    if regime == "Crisis_Financiera":
        mdd_crisis = row.get("crisis_stress_score_mdd", np.nan)
        if not np.isnan(mdd_crisis):
            if mdd_crisis >= CRISIS_MDD_SHALLOW:
                multiplier *= MULT_CRISIS_MDD_BONUS
                detail["crisis_mdd_shallow_bonus"] = MULT_CRISIS_MDD_BONUS
            elif mdd_crisis <= CRISIS_MDD_DEEP:
                multiplier *= MULT_CRISIS_MDD_MALUS
                detail["crisis_mdd_deep_malus"] = MULT_CRISIS_MDD_MALUS
        ttr_crisis = row.get("crisis_stress_score_ttr", np.nan)
        if ttr_crisis is not None and not np.isnan(ttr_crisis):
            if ttr_crisis <= CRISIS_TTR_FAST:
                multiplier *= MULT_CRISIS_TTR_BONUS
                detail["crisis_ttr_fast_bonus"] = MULT_CRISIS_TTR_BONUS
            elif ttr_crisis >= CRISIS_TTR_SLOW:
                multiplier *= MULT_CRISIS_TTR_MALUS
                detail["crisis_ttr_slow_malus"] = MULT_CRISIS_TTR_MALUS

    # ── §3f: Slope trend signals (kill-switched — same gate as rolling pctile) ─
    if ROLLING_PCTILE_P3_ENABLED:
        sharpe_slope = row.get("sharpe_slope", np.nan)
        if not np.isnan(sharpe_slope):
            if sharpe_slope >= SLOPE_IMPROVING_THRESHOLD:
                multiplier *= MULT_SLOPE_BONUS
                detail["sharpe_slope_improving_bonus"] = MULT_SLOPE_BONUS
            elif sharpe_slope <= SLOPE_DETERIORATING_THRESHOLD:
                multiplier *= MULT_SLOPE_MALUS
                detail["sharpe_slope_deteriorating_malus"] = MULT_SLOPE_MALUS
        ret_slope = row.get("return_ann_slope", np.nan)
        if not np.isnan(ret_slope):
            if ret_slope >= SLOPE_IMPROVING_THRESHOLD:
                multiplier *= MULT_SLOPE_BONUS
                detail["return_slope_improving_bonus"] = MULT_SLOPE_BONUS
            elif ret_slope <= SLOPE_DETERIORATING_THRESHOLD:
                multiplier *= MULT_SLOPE_MALUS
                detail["return_slope_deteriorating_malus"] = MULT_SLOPE_MALUS

    # ── §3f: Amortiguación por cobertura insuficiente de régimen ─────────────
    # Si un fondo tiene pocos regímenes con historia suficiente (coverage_ratio
    # < REGIME_COVERAGE_MIN), las señales empíricas son poco fiables → reducir
    # el efecto neto del multiplicador hacia 1.0.
    # Fase 1j (P3 optimization plan): reubicado al FINAL de la función.
    # Antes estaba a mitad de cadena (tras el bloque de régimen empírico),
    # lo que dejaba sin amortiguar los multiplicadores de estrés de crisis y
    # de pendiente que se aplicaban después, y en cambio SÍ amortiguaba los
    # multiplicadores estructurales (crisis spread/VIX, beta_oil, beta_rate_eu,
    # fx, alpha, macro_r2) que no dependen del historial por régimen y por
    # tanto no deberían depender de regime_coverage_ratio. Ahora amortigua el
    # multiplicador NETO acumulado, tal y como describe el comentario.
    coverage = row.get("regime_coverage_ratio", np.nan)
    if not np.isnan(coverage) and coverage < REGIME_COVERAGE_MIN:
        net = multiplier - 1.0
        multiplier = 1.0 + net * REGIME_COVERAGE_DAMP
        detail["regime_coverage_damp"] = round(coverage, 3)

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
    dd = row.get("max_dd", np.nan)
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

    # -- v24: gate corto diario (aplica a las 3 sub-carteras) ---------------
    # Si liquidity_flag > threshold, los datos diarios no son fiables:
    # el gate se omite y el fondo pasa (fail-open = no false exclusión).
    liq_flag = row.get("short_liquidity_flag__rolling_6m", np.nan)
    daily_trusted = (
        np.isnan(liq_flag) or float(liq_flag) <= SHORT_LIQUIDITY_TRUST_THRESHOLD
    )

    if daily_trusted:
        # -- Drawdown corto (rolling_6m) --
        dd_limit = SHORT_DD_LIMIT_BY_SUB.get(subportfolio)
        if dd_limit is not None:
            short_dd = row.get("short_max_drawdown__rolling_6m", np.nan)
            if not np.isnan(short_dd) and short_dd < dd_limit:
                return (
                    f"short_max_drawdown_6m={short_dd:.2f} < {dd_limit} "
                    f"({subportfolio})"
                )

        # -- Volatilidad corta AC-ajustada (rolling_3m) ---
        vol_limit = SHORT_VOL_LIMIT_BY_SUB.get(subportfolio)
        if vol_limit is not None:
            short_vol = row.get("short_vol_adj__rolling_3m", np.nan)
            if not np.isnan(short_vol) and short_vol > vol_limit:
                return (
                    f"short_vol_adj_3m={short_vol:.2f} > {vol_limit} "
                    f"({subportfolio})"
                )

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
    regime_sharpe_p25 = regime_sharpe_p75 = None
    regime_sortino_p25 = regime_sortino_p75 = None
    regime_maxdd_p25 = regime_maxdd_p75 = None
    if suffix:
        ret_col = f"return_ann_{suffix}"
        if ret_col in df.columns and not df[ret_col].isna().all():
            regime_p25 = df[ret_col].quantile(0.25)
            regime_p75 = df[ret_col].quantile(0.75)
        else:
            print(
                f"[WARN] Régimen '{regime}' no tiene métricas históricas en fund_metrics "
                "(nunca observado en la serie macro disponible). "
                "Scoring aplicado sin multiplicador empírico de régimen — base score vigente."
            )
        sharpe_col = f"sharpe_{suffix}" if suffix else None
        if sharpe_col and sharpe_col in df.columns and not df[sharpe_col].isna().all():
            regime_sharpe_p25 = df[sharpe_col].quantile(0.25)
            regime_sharpe_p75 = df[sharpe_col].quantile(0.75)
        # §3f: downside-risk lenses — sortino and max_dd per regime
        sortino_col = f"sortino_{suffix}"
        if sortino_col in df.columns and not df[sortino_col].isna().all():
            regime_sortino_p25 = df[sortino_col].quantile(0.25)
            regime_sortino_p75 = df[sortino_col].quantile(0.75)
        maxdd_col = f"max_dd_{suffix}"
        if maxdd_col in df.columns and not df[maxdd_col].isna().all():
            regime_maxdd_p25 = df[maxdd_col].quantile(0.25)
            regime_maxdd_p75 = df[maxdd_col].quantile(0.75)

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
                regime_sharpe_p25=regime_sharpe_p25,
                regime_sharpe_p75=regime_sharpe_p75,
                regime_sortino_p25=regime_sortino_p25,
                regime_sortino_p75=regime_sortino_p75,
                regime_maxdd_p25=regime_maxdd_p25,
                regime_maxdd_p75=regime_maxdd_p75,
            )

            score_final = score_base * mult if excl is None else 0.0

            detail = {
                "score_base":  round(score_base, 4),
                "multiplier":  mult,
                "mult_detail": mult_detail,
                "metrics": {
                    "return_ann_real":   _safe_round(row.get("return_ann_real")),
                    "sharpe":            _safe_round(row.get("sharpe")),
                    "max_dd":      _safe_round(row.get("max_dd")),
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
            _build_score_notes(regime, r["exclusion_reason"]),
        ))
    conn.executemany(sql, rows)
    conn.commit()
    print(f"Persistidos {len(rows)} scores en fund_scores.")
