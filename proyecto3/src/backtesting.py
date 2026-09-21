# proyecto3/src/backtesting.py
# -*- coding: utf-8 -*-
"""
Backtesting simple de la cartera construida por P3.

Metodologia (aproximacion sin corrección de look-ahead bias):
  1. Tomar clasificacion historica mensual del RegimeClassifier
  2. Para cada regimen historico, construir la cartera hipotetica
     usando los scores actuales (metricas calculadas con datos completos)
     -- Fase 4 (P3 optimization plan, 2026-09-18): con las MISMAS
     restricciones de diversificacion y ponderacion que PortfolioBuilder.
     build() realmente aplica (portfolio_engine.select_and_weight()), no
     una segunda implementacion simplificada de "top-10 proporcional" sin
     ningun limite. Ver test_backtest_parity.py.
  3. Calcular rentabilidad forward de esa cartera en ventanas de 1, 3 y 12 meses
  4. Comparar contra benchmark (media ponderada del universo)

Limitacion conocida: las metricas usan datos futuros respecto al punto de
simulacion. Los resultados sobreestiman el rendimiento real del modelo.
Para backtesting riguroso se requiere recalculo de metricas por ventana.
Hasta entonces, esta limitacion aplica solo a las cifras de RENTABILIDAD
ABSOLUTA -- el backtest sigue siendo valido para medir turnover relativo
entre configuraciones (p.ej. Fase 6, pesos de regimen difusos).

Metricas de evaluacion:
  - Rentabilidad media por regimen (1m, 3m, 12m)
  - Hit ratio: % periodos en que la cartera supera al benchmark
  - Max drawdown historico de la cartera simulada
  - Sharpe del periodo completo
  - Contribucion de cada sub-cartera a la rentabilidad

Uso:
    from proyecto3.src.backtesting import Backtester
    bt = Backtester(conn)
    results = bt.run()
    print(bt.summary(results))
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
import sys

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from shared.db import is_postgres_connection
from proyecto3.src.regime_classifier import RegimeClassifier, REGIME_WEIGHTS
from proyecto3.src.portfolio_engine import select_and_weight, DEFAULT_CONSTRAINTS


# ============================================================
# Constantes
# ============================================================

FORWARD_WINDOWS = [1, 3, 12]   # meses forward para calcular rentabilidad
BENCHMARK_METRIC = "return_ann" # metrica base para benchmark
# Fase 4 (P3 optimization plan, 2026-09-18): MIN_FUNDS_PER_SUB (=3) declarada
# aqui y nunca referenciada en ningun sitio del archivo -- eliminada
# (P#2/dead code). Los limites de tamano de sub-cartera reales vienen ahora
# de PortfolioConstraints (portfolio_engine.py), via select_and_weight().


# ============================================================
# Carga de NAV historico
# ============================================================

def _load_nav_matrix(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Carga la matriz de NAV mensual para todos los fondos.
    Devuelve DataFrame con fechas como indice y ISINs como columnas.
    Valores normalizados a base 100 en la primera fecha disponible.
    """
    rows = conn.execute("""
        SELECT ISIN, Date, NAV
        FROM fund_nav_monthly
        ORDER BY Date, ISIN
    """).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["isin", "date", "nav"])
    df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["nav"]  = df["nav"].astype(float)

    wide = df.pivot_table(index="date", columns="isin",
                          values="nav", aggfunc="last")
    wide.columns.name = None

    # Normalizar a base 100
    first_valid = wide.apply(lambda col: col.first_valid_index())
    for col in wide.columns:
        fv = first_valid[col]
        if fv is not None and wide.loc[fv, col] > 0:
            wide[col] = wide[col] / wide.loc[fv, col] * 100

    return wide


def _load_candidates(conn: sqlite3.Connection,
                      score_version: str,
                      regime: str) -> dict[str, pd.DataFrame]:
    """
    Carga los candidatos elegibles de cada sub-cartera desde fund_scores,
    para UN regimen dado. Devuelve dict {subportfolio: DataFrame} con
    columnas isin/score_total/fund_name/fund_nature/management_company/
    fund_family_id.

    Fase 4 (P3 optimization plan, 2026-09-18): reemplaza a
    _load_portfolio_isins(), que devolvia solo (isin, score) sin naturaleza/
    gestora/familia -- datos insuficientes para aplicar diversificacion.
    Union con fund_master AHORA identica a
    portfolio_builder.py::_select_funds_for_subportfolio, para que
    _select_for_regime() pueda aplicar exactamente las mismas
    restricciones que PortfolioBuilder.build() (ver portfolio_engine.py).

    Fase 3a (P3 optimization plan, migracion SQLite 2026-09-19): fund_scores
    ahora acumula historia por regimen (PK extendida -- ver
    shared/migrate_schema_v27.py), asi que un fondo puede tener varias
    filas. `regime` es ahora obligatorio: filtra a las puntuaciones
    calculadas especificamente bajo ESE regimen (los multiplicadores de
    Capa 3 son regimen-dependientes -- una puntuacion de Shock_Energetico
    no es intercambiable con una de Crisis_Financiera) y toma la mas
    reciente por as_of_date, mismo patron que
    portfolio_builder.py::_select_funds_for_subportfolio.

    Consecuencia honesta: fund_scores solo tiene historia real para el
    regimen bajo el que score_funds() se ha ejecutado alguna vez (hoy,
    solo el regimen activo en cada ejecucion -- verificado en produccion:
    5.884/5.884 filas existentes son todas 'Shock_Energetico'). Un regimen
    historico sin puntuaciones propias devuelve candidatos vacios aqui, en
    vez de reutilizar silenciosamente puntuaciones de OTRO regimen (lo que
    seria incorrecto -- ver Fase 1 sobre no inventar señal donde no la
    hay). Esta cobertura crece con cada ejecucion futura de score_funds()
    bajo un regimen distinto.
    """
    # IMPORTANTE: mismo cuidado que portfolio_builder.py::
    # _select_funds_for_subportfolio -- eligible/score_total>0 se filtran
    # DESPUES de resolver rn=1, no dentro del CTE `latest`. Filtrarlos
    # dentro del CTE descartaria la fila MAS RECIENTE de un fondo si esa
    # fila resulta ser inelegible, dejando que ROW_NUMBER() asigne rn=1 a
    # una fila elegible mas antigua -- resucitando en silencio una
    # puntuacion obsoleta. Bug real encontrado en el smoke test en vivo de
    # esta migracion (2026-09-19).
    ph = "%s" if is_postgres_connection(conn) else "?"
    rows = conn.execute(f"""
        WITH latest AS (
            SELECT fs.block, fs.isin, fs.score_total, fs.eligible,
                   ROW_NUMBER() OVER (
                       PARTITION BY fs.isin, fs.block, fs.score_version
                       ORDER BY fs.as_of_date DESC
                   ) AS rn
            FROM fund_scores fs
            WHERE fs.score_version = {ph}
              AND fs.regime = {ph}
        )
        SELECT latest.block, latest.isin, latest.score_total,
               fm.Fund_Name, fm.Fund_Nature, fm.Management_Company,
               fm.fund_family_id
        FROM latest
        JOIN fund_master fm ON fm.ISIN = latest.isin
        WHERE latest.rn = 1
          AND latest.eligible = 1
          AND latest.score_total > 0
          AND fm.In_Current_Universe = 1
        ORDER BY latest.block, latest.score_total DESC
    """, (score_version, regime)).fetchall()

    by_block: dict[str, list] = {}
    for row in rows:
        by_block.setdefault(row[0], []).append(row[1:])

    return {
        block: pd.DataFrame(data, columns=[
            "isin", "score_total", "fund_name", "fund_nature",
            "management_company", "fund_family_id",
        ])
        for block, data in by_block.items()
    }


# ============================================================
# Calculo de rentabilidad de cartera hipotetica
# ============================================================

def _portfolio_return(
    nav_matrix: pd.DataFrame,
    isins_weights: dict,   # {isin: weight}
    date_start: pd.Timestamp,
    months_forward: int,
) -> float | None:
    """
    Calcula la rentabilidad de una cartera ponderada en una ventana forward.
    """
    # Encontrar fecha final
    all_dates = nav_matrix.index
    future_dates = all_dates[all_dates > date_start]
    if len(future_dates) < months_forward:
        return None

    date_end = future_dates[months_forward - 1]

    returns = []
    weights = []

    for isin, weight in isins_weights.items():
        if isin not in nav_matrix.columns:
            continue
        nav_start = nav_matrix.loc[date_start, isin] if date_start in nav_matrix.index else None
        nav_end   = nav_matrix.loc[date_end,   isin] if date_end   in nav_matrix.index else None

        if nav_start is None or nav_end is None:
            continue
        if pd.isna(nav_start) or pd.isna(nav_end) or nav_start <= 0:
            continue

        ret = (nav_end / nav_start) - 1
        returns.append(ret)
        weights.append(weight)

    if not returns:
        return None

    # Renormalizar pesos
    total_w = sum(weights)
    if total_w <= 0:
        return None

    weighted_return = sum(r * w / total_w for r, w in zip(returns, weights))
    return round(float(weighted_return), 6)


def _benchmark_return(
    nav_matrix: pd.DataFrame,
    date_start: pd.Timestamp,
    months_forward: int,
    n_funds: int = 100,
) -> float | None:
    """
    Calcula la rentabilidad del benchmark (media equiponderada de los
    primeros n_funds fondos con datos en la ventana).
    """
    all_dates = nav_matrix.index
    future_dates = all_dates[all_dates > date_start]
    if len(future_dates) < months_forward:
        return None

    date_end = future_dates[months_forward - 1]

    if date_start not in nav_matrix.index or date_end not in nav_matrix.index:
        return None

    start_row = nav_matrix.loc[date_start]
    end_row   = nav_matrix.loc[date_end]

    valid = (start_row.notna() & end_row.notna() &
             (start_row > 0) & (end_row > 0))
    valid_cols = valid[valid].index[:n_funds]

    if len(valid_cols) == 0:
        return None

    rets = ((end_row[valid_cols] / start_row[valid_cols]) - 1)
    return round(float(rets.mean()), 6)


# ============================================================
# Backtester principal
# ============================================================

@dataclass
class BacktestResult:
    regime:        str
    date:          pd.Timestamp
    weights:       dict          # pesos de sub-carteras en este regimen
    returns:       dict          # {1: ret_1m, 3: ret_3m, 12: ret_12m}
    benchmarks:    dict          # {1: bench_1m, 3: bench_3m, 12: bench_12m}

    @property
    def excess_return(self) -> dict:
        """Exceso de rentabilidad vs benchmark por ventana."""
        return {
            w: (self.returns.get(w) - self.benchmarks.get(w))
               if self.returns.get(w) is not None
               and self.benchmarks.get(w) is not None
               else None
            for w in FORWARD_WINDOWS
        }


def _blend_to_master(
    selection: dict[str, pd.DataFrame],
    sub_w: dict[str, float],
) -> dict[str, float]:
    """
    Combina una seleccion por sub-cartera (peso INTERNO, de
    _select_for_regime) con un vector de pesos de sub-cartera concreto
    (peso de regimen de UNA fecha) para obtener {isin: peso_master}.

    Separado de la seleccion (Fase 4 item 2) precisamente para que el
    blend pueda usar el peso de CADA fecha sin tener que repetir la
    seleccion -- la seleccion es cara (diversificacion) y cacheable por
    regimen; el blend es barato y debe ser por fecha.
    """
    master: dict[str, float] = {}
    for sub_name, df in selection.items():
        weight = sub_w.get(sub_name, 0.0)
        if not weight or df.empty:
            continue
        for _, row in df.iterrows():
            isin = row["isin"]
            master[isin] = master.get(isin, 0.0) + float(row["weight"]) * weight
    # Redondeo a 4 decimales para casar con Portfolio.all_funds
    # (portfolio_builder.py), que redondea master_weight = weight * regime_weight
    # con round(..., 4) -- sin este redondeo, el mismo calculo produce un
    # float sin redondear aqui (p.ej. 0.057585 vs 0.0576), rompiendo la
    # paridad exacta que test_backtest_parity.py verifica.
    return {isin: round(w, 4) for isin, w in master.items()}


class Backtester:

    def __init__(self, conn: sqlite3.Connection, score_version: str = "v1"):
        self.conn          = conn
        self.score_version = score_version
        self._nav          = _load_nav_matrix(conn)
        self._clf          = RegimeClassifier(conn)
        self._selection_cache: dict[str, dict[str, pd.DataFrame]] = {}
        # Fase 3a (P3 optimization plan, migracion SQLite 2026-09-19):
        # _load_candidates ahora requiere `regime` (fund_scores acumula
        # historia por regimen) -- ya no se puede cargar de una vez en
        # __init__ de forma regimen-agnostica. Se carga por regimen dentro
        # de _select_for_regime(), donde ya se cachea por etiqueta.

    def _select_for_regime(self, regime: str) -> dict[str, pd.DataFrame]:
        """
        Selecciona y pondera (peso INTERNO por sub-cartera, no master) los
        fondos para el regimen dado, usando exactamente la misma logica de
        diversificacion que PortfolioBuilder.build() -- portfolio_engine.
        select_and_weight() (max fondos por naturaleza/gestora, dedup por
        familia, tope 20%/suelo 3% via water-filling).

        Fase 4 (P3 optimization plan, 2026-09-18): antes,
        _build_weights_for_regime() era una segunda implementacion
        independiente de "construir una cartera" -- top-10 por score, pesos
        proporcionales, SIN ningun limite (ni tope, ni suelo,
        ni max_same_nature, ni limite por gestora, ni dedup por familia).
        El backtest nunca probaba la cartera que PortfolioBuilder realmente
        construye. Ver test_backtest_parity.py para el invariante que hace
        esto verificable.

        Cacheado por etiqueta de regimen en run() -- la seleccion depende
        de que sub-carteras tengan peso de regimen > 0 (una sub-cartera a
        0% se omite y libera sus fondos para las demas), no del VALOR
        exacto del peso, asi que dos fechas con la misma etiqueta de
        regimen producen la misma seleccion. El BLEND a peso master si usa
        el peso de regimen especifico de cada fecha -- ver _blend_to_master.
        """
        if regime in self._selection_cache:
            return self._selection_cache[regime]

        regime_weights = REGIME_WEIGHTS.get(regime, (0.33, 0.34, 0.33))
        sub_names      = ["Defensiva", "Equilibrada", "Dinamica"]
        sub_w          = dict(zip(sub_names, regime_weights))

        candidates = _load_candidates(self.conn, self.score_version, regime)
        selection  = select_and_weight(candidates, sub_w, DEFAULT_CONSTRAINTS)
        self._selection_cache[regime] = selection
        return selection

    def run(
        self,
        start_date: str = "2005-01-01",
        end_date:   str | None = None,
    ) -> pd.DataFrame:
        """
        Ejecuta el backtesting para el periodo dado.

        Devuelve DataFrame con una fila por mes con columnas:
            date, regime, ret_1m, ret_3m, ret_12m,
            bench_1m, bench_3m, bench_12m,
            excess_1m, excess_3m, excess_12m
        """
        hist = self._clf.classify_historical()
        if hist.empty:
            print("ERROR: No hay clasificacion historica disponible.")
            return pd.DataFrame()

        # Filtrar periodo
        hist = hist[hist.index >= pd.Timestamp(start_date)]
        if end_date:
            hist = hist[hist.index <= pd.Timestamp(end_date)]

        print(f"Backtesting | {start_date} -> {end_date or 'hoy'} "
              f"| {len(hist)} meses")

        # Precalcular seleccion por regimen (peso interno, cacheado por
        # etiqueta -- ver _select_for_regime).
        for regime in hist["regime"].unique():
            self._select_for_regime(regime)

        records = []
        for i, (date, row) in enumerate(hist.iterrows()):
            regime    = row["regime"]
            selection = self._select_for_regime(regime)

            # Fase 4 item 2 (P3 optimization plan): pesos de sub-cartera
            # POR FECHA, no por etiqueta de regimen -- classify_historical()
            # ya emite weight_defensive/balanced/dynamic por fila. Hoy son
            # identicos a REGIME_WEIGHTS[regime] (no hay transiciones
            # difusas todavia), pero cablear esto ahora es lo que hace que
            # la Fase 6 (pesos difusos) llegue al backtest sin ningun
            # cambio adicional -- con un lookup por etiqueta, la Fase 6
            # fallaria en silencio contra este backtest.
            sub_w = {
                "Defensiva":   row["weight_defensive"],
                "Equilibrada": row["weight_balanced"],
                "Dinamica":    row["weight_dynamic"],
            }
            weights = _blend_to_master(selection, sub_w)

            rec = {
                "date":   date,
                "regime": regime,
            }

            for w in FORWARD_WINDOWS:
                ret   = _portfolio_return(self._nav, weights, date, w)
                bench = _benchmark_return(self._nav, date, w)
                rec[f"ret_{w}m"]    = ret
                rec[f"bench_{w}m"]  = bench
                rec[f"excess_{w}m"] = (ret - bench
                                        if ret is not None and bench is not None
                                        else None)

            records.append(rec)

            if (i + 1) % 50 == 0:
                print(f"  Procesados {i+1}/{len(hist)} meses...")

        df = pd.DataFrame(records).set_index("date")
        print(f"Backtesting completado. {len(df)} periodos evaluados.")
        return df

    def summary(self, results: pd.DataFrame) -> str:
        """Genera un resumen legible de los resultados del backtesting."""
        if results.empty:
            return "Sin resultados."

        lines = [
            "BACKTESTING P3 -- Resumen por regimen",
            "=" * 60,
        ]

        # Por regimen
        for regime in results["regime"].unique():
            sub = results[results["regime"] == regime]
            lines.append(f"\n{regime} ({len(sub)} meses):")

            for w in FORWARD_WINDOWS:
                ret_col    = f"ret_{w}m"
                bench_col  = f"bench_{w}m"
                excess_col = f"excess_{w}m"

                valid = sub[[ret_col, bench_col, excess_col]].dropna()
                if valid.empty:
                    continue

                ret_med    = valid[ret_col].mean()   * 100
                bench_med  = valid[bench_col].mean() * 100
                excess_med = valid[excess_col].mean()* 100
                hit_ratio  = (valid[excess_col] > 0).mean() * 100

                lines.append(
                    f"  {w:2d}m: cartera {ret_med:+.1f}% | "
                    f"bench {bench_med:+.1f}% | "
                    f"exceso {excess_med:+.1f}% | "
                    f"hit {hit_ratio:.0f}%"
                )

        # Global
        lines.append(f"\n{'='*60}")
        lines.append("GLOBAL:")
        for w in FORWARD_WINDOWS:
            ret_col    = f"ret_{w}m"
            excess_col = f"excess_{w}m"
            valid = results[[ret_col, excess_col]].dropna()
            if valid.empty:
                continue
            ret_ann    = ((1 + valid[ret_col].mean()) ** (12/w) - 1) * 100
            excess_ann = valid[excess_col].mean() * (12/w) * 100
            hit_ratio  = (valid[excess_col] > 0).mean() * 100
            lines.append(
                f"  {w:2d}m anualizado: {ret_ann:+.1f}% | "
                f"exceso {excess_ann:+.1f}% | "
                f"hit ratio {hit_ratio:.0f}%"
            )

        return "\n".join(lines)

    def drawdown_series(self, results: pd.DataFrame,
                        window: int = 12) -> pd.Series:
        """Calcula la serie de drawdown de la cartera simulada."""
        rets = results[f"ret_{window}m"].dropna()
        cum  = (1 + rets).cumprod()
        roll_max = cum.cummax()
        dd = (cum / roll_max) - 1
        return dd
