# proyecto3/src/portfolio_engine.py
# -*- coding: utf-8 -*-
"""
Motor de seleccion y ponderacion de carteras -- nucleo puro, sin DB,
compartido entre PortfolioBuilder (portfolio_builder.py) y Backtester
(backtesting.py).

Fase 4 (P3 optimization plan, 2026-09-18) -- v2.0 release cut line: antes,
Backtester._build_weights_for_regime() era una segunda implementacion
independiente de "construir una cartera" (top-10 por score, pesos
proporcionales, SIN ningun limite -- ni tope del 20%, ni suelo del 3%, ni
max_same_nature, ni limite por gestora, ni dedup por familia, ni
histeresis, ni coste de rotacion), un P#11 textbook violation. El backtest
por tanto no probaba la cartera que PortfolioBuilder realmente construye,
lo que hacia inutilizable cualquier medicion de las Fases 5-7 (transiciones
difusas, penalizaciones suaves, etc.) contra ese backtest.

select_and_weight() es el unico punto donde vive la logica de
diversificacion + ponderacion; PortfolioBuilder y Backtester son ahora
adaptadores finos sobre DB que cargan candidatos y llaman aqui. El test de
paridad (proyecto3/tests/test_backtest_parity.py) es el invariante que
hace esto verificable: mismos candidatos + mismas constraints ->
exactamente el mismo mapa {isin: master_weight}.

Uso:
    from proyecto3.src.portfolio_engine import (
        PortfolioConstraints, select_and_weight
    )
    result = select_and_weight(candidates_by_sub, sub_weights, constraints)
"""

from dataclasses import dataclass

import pandas as pd


# ============================================================
# Constraints
# ============================================================

@dataclass(frozen=True)
class PortfolioConstraints:
    """
    Restricciones de construccion de cartera, agrupadas para que un solo
    objeto capture todo lo que select_and_weight() necesita -- incluye
    hysteresis_band aqui (no como parametro aparte) precisamente para que
    la Fase 7c pueda variar la banda por color de semaforo sin tener que
    enhebrar un parametro nuevo por toda la cadena de llamadas.
    """
    max_funds_per_sub:   int   = 10     # max fondos por sub-cartera
    max_weight_per_fund: float = 0.20   # max 20% en un solo fondo
    min_weight_per_fund: float = 0.03   # min 3% si el fondo entra
    max_same_nature:     int   = 5      # max fondos de la misma naturaleza por sub-cartera
    max_funds_per_mgr:   int   = 2      # max fondos de una misma gestora, global entre sub-carteras
    hysteresis_band:     float = 0.05   # bonus de score para titulares (ver select_candidates)


DEFAULT_CONSTRAINTS = PortfolioConstraints()


# ============================================================
# Water-filling: pesos internos dentro de [lo, hi]
# ============================================================

def clamp_and_renormalize(
    weights: pd.Series,
    lo: float,
    hi: float,
    max_iter: int = 50,
) -> pd.Series:
    """
    Water-filling: ajusta `weights` para que cada valor quede en [lo, hi] y
    la suma se mantenga en 1.0, redistribuyendo el excedente/deficit de
    forma proporcional entre los miembros aun no fijados en cada iteracion.

    Movido aqui desde portfolio_builder.py en la Fase 4 (P#11/DRY) --
    Backtester tambien necesita esta misma logica de ponderacion interna
    para que sus carteras hipoteticas respeten las mismas cotas que las
    reales; portfolio_builder.py re-exporta este nombre para no romper el
    import existente en proyecto3/tests/test_portfolio_constraints.py.

    Precondicion de factibilidad: n*lo <= 1.0 <= n*hi (n = len(weights)).
    Con lo=0.03, hi=0.20, eso exige n in [5, 33] -- una sub-cartera de 4
    fondos es infactible bajo el tope del 20% y producia silenciosamente
    pesos del 25%. Si es infactible, o si el bucle no converge en max_iter
    (lo que en un problema factible no deberia ocurrir nunca -- la
    redistribucion proporcional sobre un simplex 1-D converge
    monotonamente), se registra un ERROR y el residuo final se reparte
    proporcionalmente sobre el margen disponible (o, si no hay ningun
    margen, en proporcion a los pesos originales pre-clamp) en vez de
    volcarse entero sobre un unico fondo.
    """
    n = len(weights)
    if n == 0:
        return weights

    w = weights.copy().astype(float)
    total = w.sum()
    if total <= 0:
        w[:] = 1.0 / n
        return w.round(4)

    w = w / total
    original = w.copy()

    feasible = n * lo <= 1.0 + 1e-9 and 1.0 <= n * hi + 1e-9
    if not feasible:
        print(f"  [ERROR] clamp_and_renormalize: cotas infactibles para "
              f"n={n} fondos (lo={lo}, hi={hi} -> rango [{n*lo:.2f}, "
              f"{n*hi:.2f}] no cubre 1.0). El resultado puede violar lo/hi "
              f"-- tratar esta sub-cartera como invalida.")

    clamped_lo = pd.Series(False, index=w.index)
    clamped_hi = pd.Series(False, index=w.index)

    for _ in range(max_iter):
        new_lo = w < lo
        new_hi = w > hi
        if not new_lo.any() and not new_hi.any():
            break

        clamped_lo |= new_lo
        clamped_hi |= new_hi
        w[clamped_lo] = lo
        w[clamped_hi] = hi

        free_mask = ~(clamped_lo | clamped_hi)
        if not free_mask.any():
            break

        fixed_total = w[~free_mask].sum()
        remaining   = 1.0 - fixed_total
        free_sum    = w[free_mask].sum()
        if free_sum > 0:
            w[free_mask] = w[free_mask] / free_sum * remaining
        else:
            w[free_mask] = remaining / free_mask.sum()
    else:
        print(f"  [ERROR] clamp_and_renormalize: no convergio en "
              f"{max_iter} iteraciones (n={n}, lo={lo}, hi={hi}). Se usa "
              f"la ultima iteracion -- tratar esta sub-cartera como "
              f"invalida e investigar.")

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


# ============================================================
# Seleccion de candidatos por sub-cartera (diversificacion)
# ============================================================

def select_candidates(
    candidates: pd.DataFrame,
    constraints: PortfolioConstraints,
    *,
    exclude_isins: set | None = None,
    exclude_families: set | None = None,
    mgr_global: dict | None = None,
    incumbent_isins: frozenset | None = None,
) -> pd.DataFrame:
    """
    Selecciona los mejores fondos de UNA sub-cartera aplicando las
    restricciones de diversificacion, dado un frame de candidatos ya
    cargado (columnas requeridas: isin, score_total, fund_name,
    fund_nature, management_company, fund_family_id).

    exclude_isins/exclude_families/mgr_global se pasan y mutan por
    referencia entre llamadas sucesivas (una por sub-cartera) para que la
    exclusion sea GLOBAL entre sub-carteras, replicando exactamente el
    comportamiento de PortfolioBuilder.build() previo a la Fase 4.

    Fase 4: extraido de portfolio_builder.py::_select_funds_for_subportfolio,
    que ahora es un adaptador fino que carga los candidatos desde fund_scores
    JOIN fund_master y delega aqui.
    """
    exclude_isins    = exclude_isins if exclude_isins is not None else set()
    exclude_families = exclude_families if exclude_families is not None else set()
    mgr_global       = mgr_global if mgr_global is not None else {}

    if candidates.empty:
        return pd.DataFrame()

    df = candidates.copy()
    df["score_total"] = df["score_total"].astype(float)

    # Histeresis: los titulares reciben un bonus de score para que los
    # retadores tengan que superarlos por un margen real antes de provocar
    # una rotacion. Los pesos finales siguen usando score_total (sin bonus).
    if incumbent_isins:
        is_inc = df["isin"].isin(incumbent_isins)
        df["effective_score"] = df["score_total"].where(
            ~is_inc,
            df["score_total"] * (1.0 + constraints.hysteresis_band),
        )
    else:
        df["effective_score"] = df["score_total"]

    df = df.sort_values("effective_score", ascending=False).reset_index(drop=True)

    selected     = []
    nature_count: dict[str, int] = {}

    def _norm_fallback(name: str) -> str:
        return " ".join((name or "").strip().split()[:3]).upper()

    for _, row in df.iterrows():
        if row["isin"] in exclude_isins:
            continue

        nature = row["fund_nature"]
        mgr    = row["management_company"] or "Desconocida"

        fam_id = row.get("fund_family_id")
        dedup_key = fam_id if (fam_id and str(fam_id).strip()) \
                    else _norm_fallback(row["fund_name"])

        if dedup_key and dedup_key in exclude_families:
            continue
        if nature_count.get(nature, 0) >= constraints.max_same_nature:
            continue
        if mgr_global.get(mgr, 0) >= constraints.max_funds_per_mgr:
            continue

        selected.append(row)
        nature_count[nature] = nature_count.get(nature, 0) + 1
        mgr_global[mgr]      = mgr_global.get(mgr, 0) + 1
        if dedup_key:
            exclude_families.add(dedup_key)
        exclude_isins.add(row["isin"])

        if len(selected) >= constraints.max_funds_per_sub:
            break

    return pd.DataFrame(selected) if selected else pd.DataFrame()


def assign_weights(
    df: pd.DataFrame,
    constraints: PortfolioConstraints,
    method: str = "score_proportional",
) -> pd.DataFrame:
    """
    Asigna pesos internos a los fondos seleccionados de una sub-cartera.
    Los pesos suman 1.0, cada uno dentro de
    [constraints.min_weight_per_fund, constraints.max_weight_per_fund]
    siempre que el numero de fondos lo permita (ver clamp_and_renormalize).

    Fase 4: extraido de portfolio_builder.py::_assign_weights.
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

    df["weight"] = clamp_and_renormalize(
        df["weight"], constraints.min_weight_per_fund, constraints.max_weight_per_fund
    )

    return df


def select_and_weight(
    candidates_by_sub: dict[str, pd.DataFrame],
    sub_weights: dict[str, float],
    constraints: PortfolioConstraints = DEFAULT_CONSTRAINTS,
    incumbents: dict[str, frozenset] | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Orquesta la seleccion + ponderacion de TODAS las sub-carteras dadas,
    aplicando exclusion cruzada (un fondo/familia/gestora usado en una
    sub-cartera no puede reaparecer en otra).

    Parametros:
        candidates_by_sub: {sub_name: DataFrame de candidatos elegibles},
                            columnas isin/score_total/fund_name/
                            fund_nature/management_company/fund_family_id.
        sub_weights:        {sub_name: peso de regimen}. Una sub-cartera
                            con peso 0 se omite.
        incumbents:         {sub_name: frozenset(isins titulares)},
                            opcional -- para el bonus de histeresis.

    Devuelve {sub_name: DataFrame} con columna "weight" (peso INTERNO
    dentro de la sub-cartera, no el peso master); el caller combina con
    sub_weights[sub_name] para obtener el peso master.

    Este es el nucleo unico que tanto PortfolioBuilder.build() como
    Backtester._build_weights_for_regime() invocan -- el invariante que
    hace esto verificable es el test de paridad
    (test_backtest_parity.py): mismos candidatos_by_sub + mismas
    sub_weights + mismas constraints -> exactamente el mismo resultado,
    sin importar cual de los dos callers lo invoque.
    """
    incumbents = incumbents or {}
    result: dict[str, pd.DataFrame] = {}

    isins_used: set = set()
    families_used: set = set()
    mgr_used: dict[str, int] = {}

    for sub_name, regime_weight in sub_weights.items():
        if regime_weight == 0:
            continue

        candidates = candidates_by_sub.get(sub_name, pd.DataFrame())
        selected = select_candidates(
            candidates, constraints,
            exclude_isins=isins_used,
            exclude_families=families_used,
            mgr_global=mgr_used,
            incumbent_isins=incumbents.get(sub_name),
        )
        if selected.empty:
            result[sub_name] = selected
            continue

        result[sub_name] = assign_weights(selected, constraints)

    return result
