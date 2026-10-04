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

Multiplicadores estructurales:
    fx_contribution_pct > 0.60   x0.80  retorno mayormente divisa
    alpha_persistence > 0.60     x1.15  gestor consistente

Multiplicadores macro RETIRADOS 2026-10-04 (FND-0225, decision del propietario tras el backtest PIT de universo
completo, runs 20261004_165611 / 20261004_172934): beta_oil > 0.01 x1.20, beta_rate_eu < -0.10 x0.70,
macro_r2 > 0.50 x0.85 y las patas de crisis beta_spread_hy / beta_vix (x0.60 / x1.30). Las de petroleo, VIX y spread
HY no se activaban NUNCA en produccion (umbrales 15-40x fuera del rango real de las betas y, ademas, con el signo
invertido); el spread HY no aporta nada y el VIX solo se comporta como una penalizacion de volatilidad suave cuyo
beneficio es un unico episodio (2008-09); macro_r2 penalizaba a los fondos que menos caen. Quedan la penalizacion FX
(no evaluada: sin equivalente PIT todavia) y el bonus de persistencia de alpha. Las betas macro se siguen calculando
en P2 (informes, auditoria); el scorer ya no las lee.

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
    ROLLING_PCTILE_HORIZON,
    ROLLING_PCTILE_P3_ENABLED,
)
from shared.db import executemany


# ============================================================
# Constantes
# ============================================================

# FND-0192: every number below comes from proyecto3/config/scorer_params.yaml (validated on load); the names are unchanged.
# SCORER_CONFIG_HASH identifies the loaded values and feeds the PIT cache keys. To try other values, point load_scorer_params()
# at another file and reload this module (the values are read once, at import).
from proyecto3.src.scorer_config import SCORER_CONFIG_HASH, load_scorer_params  # noqa: E402

_P = load_scorer_params()

# Inline numbers that used to sit inside the functions
REGIME_PERCENTILE_LOW = _P["REGIME_PERCENTILE_LOW"]
REGIME_PERCENTILE_HIGH = _P["REGIME_PERCENTILE_HIGH"]
SHORT_HORIZON_WEIGHTS = _P["SHORT_HORIZON_WEIGHTS"]

# Pesos scoring base globales (fallback)
BASE_WEIGHTS = _P["BASE_WEIGHTS"]

# Pesos diferenciados por sub-cartera
SUBPORTFOLIO_WEIGHTS = _P["SUBPORTFOLIO_WEIGHTS"]

# Bonus por naturaleza del fondo según sub-cartera
NATURE_PROFILE_BONUS = _P["NATURE_PROFILE_BONUS"]

# Filtros duros por sub-cartera
MAX_DRAWDOWN_BY_SUB = _P["MAX_DRAWDOWN_BY_SUB"]

# Retorno real mínimo por sub-cartera
MIN_REAL_RETURN_BY_SUB = _P["MIN_REAL_RETURN_BY_SUB"]

# Filtros duros globales
MAX_DRAWDOWN_LIMIT = _P["MAX_DRAWDOWN_LIMIT"]
MIN_REAL_RETURN = _P["MIN_REAL_RETURN"]
MAX_SRRI_DEFENSIVE = _P["MAX_SRRI_DEFENSIVE"]

# Umbrales multiplicadores estructurales (los macro -oil, tipos, R2, crisis VIX/spread- se retiraron, ver docstring)
FX_CONTRIBUTION_LIMIT = _P["FX_CONTRIBUTION_LIMIT"]
ALPHA_PERS_THRESHOLD = _P["ALPHA_PERS_THRESHOLD"]

# Multiplicadores
MULT_FX_MALUS = _P["MULT_FX_MALUS"]
MULT_ALPHA_BONUS = _P["MULT_ALPHA_BONUS"]

# §3f — Crisis stress thresholds (crisis_stress_score_mdd and _ttr)
CRISIS_MDD_SHALLOW = _P["CRISIS_MDD_SHALLOW"]
CRISIS_MDD_DEEP = _P["CRISIS_MDD_DEEP"]
CRISIS_TTR_FAST = _P["CRISIS_TTR_FAST"]
CRISIS_TTR_SLOW = _P["CRISIS_TTR_SLOW"]
MULT_CRISIS_MDD_BONUS = _P["MULT_CRISIS_MDD_BONUS"]
MULT_CRISIS_MDD_MALUS = _P["MULT_CRISIS_MDD_MALUS"]
MULT_CRISIS_TTR_BONUS = _P["MULT_CRISIS_TTR_BONUS"]
MULT_CRISIS_TTR_MALUS = _P["MULT_CRISIS_TTR_MALUS"]

# §3f — Regime Sharpe multipliers (modestos: complementan el regime_return)
MULT_REGIME_SHARPE_BONUS = _P["MULT_REGIME_SHARPE_BONUS"]
MULT_REGIME_SHARPE_MALUS = _P["MULT_REGIME_SHARPE_MALUS"]

# §3f — Regime Sortino multipliers (downside-risk lens: complementa Sharpe)
MULT_REGIME_SORTINO_BONUS = _P["MULT_REGIME_SORTINO_BONUS"]
MULT_REGIME_SORTINO_MALUS = _P["MULT_REGIME_SORTINO_MALUS"]

# §3f — Regime Max-DD multipliers (capital-destruction lens por régimen)
# max_dd es negativo: mayor valor absoluto = peor (e.g. -0.40 < -0.10).
MULT_REGIME_MAXDD_BONUS = _P["MULT_REGIME_MAXDD_BONUS"]
MULT_REGIME_MAXDD_MALUS = _P["MULT_REGIME_MAXDD_MALUS"]

# Umbral mínimo de regime_coverage_ratio para aplicar multiplicadores empíricos.
# Por debajo del umbral, los multiplicadores de régimen se ponderan hacia 1.0
# (fondo con historia insuficiente en regímenes → no castigar/premiar con pocos datos).
REGIME_COVERAGE_MIN = _P["REGIME_COVERAGE_MIN"]
REGIME_COVERAGE_DAMP = _P["REGIME_COVERAGE_DAMP"]

# §3f — Slope trend thresholds (normalized slope: std-devs per period)
SLOPE_IMPROVING_THRESHOLD = _P["SLOPE_IMPROVING_THRESHOLD"]
SLOPE_DETERIORATING_THRESHOLD = _P["SLOPE_DETERIORATING_THRESHOLD"]
MULT_SLOPE_BONUS = _P["MULT_SLOPE_BONUS"]
MULT_SLOPE_MALUS = _P["MULT_SLOPE_MALUS"]

# -- Horizonte corto — gate duro defensivo (v24) -------------------------
# Umbral de drawdown corto (rolling_6m, metric_version='d1') por sub-cartera.
# Fondos que superen la pérdida máxima reciente son excluidos del ciclo.
# None = gate inactivo para esa sub-cartera.
SHORT_DD_LIMIT_BY_SUB: dict[str, float | None] = _P["SHORT_DD_LIMIT_BY_SUB"]

# Umbral de volatilidad diaria AC-ajustada (rolling_3m) por sub-cartera.
# Fondos con vol corta superior son excluidos.
# None = gate inactivo.
SHORT_VOL_LIMIT_BY_SUB: dict[str, float | None] = _P["SHORT_VOL_LIMIT_BY_SUB"]

# Umbral de iliquidez: si liquidity_flag > este valor, los gates cortos se omiten
# (datos no confiables — fondos con NAV diario sintético).
SHORT_LIQUIDITY_TRUST_THRESHOLD: float = _P["SHORT_LIQUIDITY_TRUST_THRESHOLD"]

# Bonus/malus empírico por régimen
MULT_REGIME_RETURN_BONUS = _P["MULT_REGIME_RETURN_BONUS"]
MULT_REGIME_RETURN_MALUS = _P["MULT_REGIME_RETURN_MALUS"]
MIN_OBS_REGIME_SCORING = _P["MIN_OBS_REGIME_SCORING"]

# Sub-carteras por naturaleza de fondo
SUBPORTFOLIO_MAPPING = _P["SUBPORTFOLIO_MAPPING"]


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
    multiplier:        float = 1.0
    score_detail:      dict = field(default_factory=dict)

    def to_db_row(self, regime: str, score_version: str = "v1") -> dict:
        """
        Fase 3a (P3 optimization plan, migracion SQLite 2026-09-19):
        fund_scores' PK se extendio a (isin, block, score_version, regime,
        as_of_date) -- ver shared/migrate_schema_v27.py. regime y
        as_of_date ahora son columnas reales de las que depende la PK (no
        solo texto en notes); score_base/multiplier/exclusion_reason
        tambien se persisten como columnas propias.
        """
        today = pd.Timestamp.today().strftime("%Y-%m-%d")
        return {
            "isin":             self.isin,
            "block":            self.fund_nature,
            "score_version":    score_version,
            "regime":           regime,
            "as_of_date":       today,
            "score_total":      round(self.score_final, 6),
            "score_base":       round(self.score_base, 6),
            "multiplier":       self.multiplier,
            "score_detail":     json.dumps(self.score_detail, ensure_ascii=False),
            "eligible":         1 if self.eligible else 0,
            "exclusion_reason": self.exclusion_reason,
            "calculated_at":    today,
            "notes":            _build_score_notes(regime, self.exclusion_reason),
        }


# ============================================================
# Carga de métricas P2  (v17 — SELECT ampliado)
# ============================================================

def scoring_metric_requests(regime: str | None = None) -> list:
    """(metric, horizon, real_flag) triples that load_fund_metrics_for_scoring reads from fund_metrics (pure: no DB).
    Extracted from the loader so what the scorer asks for can be tested against what P2 writes (FND-0199)."""
    metrics_needed = [
        ("return_ann",          "since_inception", 1),  # real
        ("sharpe",              "since_inception", 0),
        ("max_dd",        "since_inception", 0),
        ("alpha_persistence",   "since_inception", 0),
        ("capture_ratio",       "since_inception", 0),
        ("momentum_rank",       "since_inception", 0),
        ("fx_contribution_pct", "since_inception", 0),
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
            ("vol_ann_pctile_cat",    ROLLING_PCTILE_HORIZON, 0),     # FND-0199: P2 stores these per rolling window
            ("max_dd_pctile_cat",     ROLLING_PCTILE_HORIZON, 0),
            ("return_ann_pctile_cat", ROLLING_PCTILE_HORIZON, 0),
            # §3e slope trends (rolling_3y window, same horizon key)
            ("sharpe_slope",          "rolling_3y",      0),
            ("return_ann_slope",      "rolling_3y",      1),
        ]

    return metrics_needed


def load_fund_metrics_for_scoring(
    conn: "psycopg.Connection",
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
    metrics_needed = scoring_metric_requests(regime)

    ph = "%s"
    rows = []
    for metric, horizon, real_flag in metrics_needed:
        result = conn.execute(f"""
            SELECT isin, value
            FROM fund_metrics
            WHERE metric={ph} AND horizon={ph} AND real_flag={ph}
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
        result = conn.execute(f"""
            SELECT isin, value
            FROM fund_metrics
            WHERE metric={ph} AND horizon={ph} AND real_flag={ph}
              AND metric_version={ph}
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
    # fetchall()+DataFrame(columns=...) explícitos, NO pd.read_sql(sql, conn): Postgres
    # folds unquoted column ALIASES to lowercase too (cursor.description reports 'isin',
    # 'fund_name', ... regardless of the query's own "AS ISIN"/"AS Fund_Name" text — verified
    # live 2026-09-21), so pd.read_sql would silently build a lowercase-keyed DataFrame here,
    # breaking .set_index("ISIN") outright. Same root cause as db_readers.py::
    # load_fund_attributes() and nav_discovery.py's dict(row) fixes earlier this migration.
    _fm_cols = ["ISIN", "Fund_Name", "Fund_Nature", "srri_kiid", "Investment_Focus",
                "Credit_Quality", "Ongoing_Charge", "SRRI_Quality_Flag", "fund_family_id"]
    _fm_rows = conn.execute("""
        SELECT ISIN, Fund_Name, Fund_Nature, SRRI as srri_kiid,
               Investment_Focus, Credit_Quality,
               Ongoing_Charge_Recurrent AS Ongoing_Charge,
               SRRI_Quality_Flag, fund_family_id
        FROM fund_master
        WHERE In_Current_Universe = 1
    """).fetchall()
    fm = pd.DataFrame(_fm_rows, columns=_fm_cols).set_index("ISIN")

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
        weights.update(SHORT_HORIZON_WEIGHTS.get(subportfolio, {}))

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

    # Multiplicadores macro (crisis spread/VIX, petroleo, tipos BCE, macro_r2) RETIRADOS 2026-10-04 (FND-0225): ver
    # el docstring del modulo. No reintroducir sin validacion fuera de muestra (FND-0193).

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
    # multiplicadores estructurales (fx, alpha; los macro se retiraron en FND-0225)
    # que no dependen del historial por régimen y por
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

def deduplicate_by_family(df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
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
    if n_dedup > 0 and verbose:
        print(f"  [Scorer] Deduplicación por familia: "
              f"{n_repr}/{n_total} clases representantes "
              f"({n_dedup} clases secundarias excluidas)")

    return df[df["family_representative"]].drop(columns=["family_representative"])


# ============================================================
# Motor principal de scoring
# ============================================================

def compute_regime_percentiles(df: pd.DataFrame, regime: str, verbose: bool = True) -> dict:
    """
    Percentiles p25/p75 del universo para las metricas del regimen activo (bonus/malus empirico).
    Devuelve los kwargs que espera compute_regime_multiplier (todos None si el regimen no tiene
    historia en el universo -> el multiplicador empirico queda en 1.0).

    Extraido de score_funds (FND-0159 d2) para que el scoring en vivo y el point-in-time del
    backtester compartan una sola implementacion (P#11).
    """
    from proyecto3.src.regime_classifier import _REGIME_SUFFIX
    pct = dict(
        regime_return_p25=None, regime_return_p75=None,
        regime_sharpe_p25=None, regime_sharpe_p75=None,
        regime_sortino_p25=None, regime_sortino_p75=None,
        regime_maxdd_p25=None, regime_maxdd_p75=None,
    )
    suffix = _REGIME_SUFFIX.get(regime)
    if not suffix:
        return pct

    ret_col = f"return_ann_{suffix}"
    if ret_col in df.columns and not df[ret_col].isna().all():
        pct["regime_return_p25"] = df[ret_col].quantile(REGIME_PERCENTILE_LOW)
        pct["regime_return_p75"] = df[ret_col].quantile(REGIME_PERCENTILE_HIGH)
    elif verbose:
        print(
            f"[WARN] Régimen '{regime}' no tiene métricas históricas en fund_metrics "
            "(nunca observado en la serie macro disponible). "
            "Scoring aplicado sin multiplicador empírico de régimen — base score vigente."
        )
    sharpe_col = f"sharpe_{suffix}"
    if sharpe_col in df.columns and not df[sharpe_col].isna().all():
        pct["regime_sharpe_p25"] = df[sharpe_col].quantile(REGIME_PERCENTILE_LOW)
        pct["regime_sharpe_p75"] = df[sharpe_col].quantile(REGIME_PERCENTILE_HIGH)
    # §3f: downside-risk lenses — sortino and max_dd per regime
    sortino_col = f"sortino_{suffix}"
    if sortino_col in df.columns and not df[sortino_col].isna().all():
        pct["regime_sortino_p25"] = df[sortino_col].quantile(REGIME_PERCENTILE_LOW)
        pct["regime_sortino_p75"] = df[sortino_col].quantile(REGIME_PERCENTILE_HIGH)
    maxdd_col = f"max_dd_{suffix}"
    if maxdd_col in df.columns and not df[maxdd_col].isna().all():
        pct["regime_maxdd_p25"] = df[maxdd_col].quantile(REGIME_PERCENTILE_LOW)
        pct["regime_maxdd_p75"] = df[maxdd_col].quantile(REGIME_PERCENTILE_HIGH)
    return pct


def score_funds_from_df(df: pd.DataFrame, regime: str, verbose: bool = True) -> pd.DataFrame:
    """
    Nucleo PURO del scoring (sin DB, sin persistencia): puntua un frame de metricas ya cargado.

    df: una fila por fondo (indice isin) con las columnas que devuelve
        load_fund_metrics_for_scoring (metricas + atributos de fund_master). Las columnas ausentes
        o NaN dan multiplicadores neutros / contribucion 0, nunca error.
    Devuelve una fila por (fondo, sub-cartera) con isin, fund_name, fund_nature, fund_family_id,
    subportfolio, score_base, multiplier, score_final, eligible, exclusion_reason, detail
    (ya deduplicado por familia). Vacio si ningun fondo cae en una sub-cartera.

    Lo usan score_funds (en vivo, tras cargar de la DB) y el backtester point-in-time (FND-0159),
    que construye el frame de cada fecha solo con datos observables en ella.
    """
    regime_pct = compute_regime_percentiles(df, regime, verbose=verbose)

    results = []

    for nature, sub_list in SUBPORTFOLIO_MAPPING.items():
        mask   = df["Fund_Nature"].isin(sub_list)
        subset = df[mask]

        sub_scores = compute_base_scores(subset, subportfolio=nature)

        for isin, row in subset.iterrows():
            score_base = float(sub_scores.get(isin, 0.0))

            excl = check_hard_filters(row, nature)

            mult, mult_detail = compute_regime_multiplier(row, regime, **regime_pct)

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
        df_results = deduplicate_by_family(df_results, verbose=verbose)
    return df_results


def score_funds(
    conn: "psycopg.Connection",
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

    Adaptador fino sobre DB: carga las metricas actuales y delega el calculo en
    score_funds_from_df (FND-0159 d2).
    """
    regime = regime_result.regime
    print(f"Scoring | Régimen: {regime} | versión: {score_version}")

    df = load_fund_metrics_for_scoring(conn, regime=regime)
    if df.empty:
        print("ERROR: No hay métricas disponibles en fund_metrics.")
        return pd.DataFrame()

    print(f"Fondos con métricas: {len(df)}")

    df_results = score_funds_from_df(df, regime)

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
    conn: "psycopg.Connection",
    df: pd.DataFrame,
    regime: str,
    score_version: str,
) -> None:
    """
    Persiste los scores en fund_scores.

    Fase 3a (P3 optimization plan, migracion SQLite 2026-09-19): PK
    extendida a (isin, block, score_version, regime, as_of_date) -- ver
    shared/migrate_schema_v27.py, que replica en SQLite el diseno ya
    validado en db/pg/30_gold.sql. regime y as_of_date se escriben ahora
    como columnas reales (antes solo codificadas en notes); score_base/
    multiplier/exclusion_reason tambien se persisten como columnas propias
    en vez de vivir solo dentro del JSON de score_detail. `notes` se
    mantiene por compatibilidad/comentario libre, pero deja de ser la
    unica fuente de verdad para regime/exclusion_reason.
    """
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    sql = """
        INSERT INTO fund_scores
            (isin, block, score_version, regime, as_of_date, score_total,
             score_base, multiplier, score_detail, eligible,
             exclusion_reason, calculated_at, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
        ON CONFLICT (isin, block, score_version, regime, as_of_date) DO UPDATE SET
            score_total = excluded.score_total, score_base = excluded.score_base,
            multiplier = excluded.multiplier, score_detail = excluded.score_detail,
            eligible = excluded.eligible, exclusion_reason = excluded.exclusion_reason,
            calculated_at = excluded.calculated_at, notes = excluded.notes
    """
    rows = []
    for _, r in df.iterrows():
        rows.append((
            r["isin"],
            r["subportfolio"],
            score_version,
            regime,
            today,
            r["score_final"],
            r["score_base"],
            r["multiplier"],
            json.dumps(r["detail"], ensure_ascii=False),
            1 if r["eligible"] else 0,
            r["exclusion_reason"],
            today,
            _build_score_notes(regime, r["exclusion_reason"]),
        ))
    if not rows:
        return   # nunca borrar la ejecucion existente por una entrada vacia

    # FND-0172: una ejecucion de scoring es AUTORITATIVA para su (score_version, regime,
    # as_of_date): el upsert por si solo dejaba las filas de una ejecucion anterior del
    # MISMO dia para fondos que esta ya no puntua (p.ej. 27 filas, 24 elegibles, de 14 fondos
    # cuyas metricas borro FND-0168), y como llevan el as_of_date mas reciente seguian
    # pasando la regla de score_candidates.py. Borrado + insercion en UNA transaccion: si el
    # insert falla, el rollback restaura la ejecucion anterior intacta.
    try:
        conn.execute(
            "DELETE FROM fund_scores WHERE score_version = %s AND regime = %s AND as_of_date = %s",
            (score_version, regime, today),
        )
        executemany(conn, sql, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(f"Persistidos {len(rows)} scores en fund_scores.")
