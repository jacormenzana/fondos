# proyecto3/src/backtesting.py
# -*- coding: utf-8 -*-
"""
Backtesting simple de la cartera construida por P3.

Metodologia (aproximacion sin corrección de look-ahead bias):
  1. Tomar clasificacion historica mensual del RegimeClassifier
  2. Para cada regimen historico, construir la cartera hipotetica
     usando los scores actuales (metricas calculadas con datos completos)
  3. Calcular rentabilidad forward de esa cartera en ventanas de 1, 3 y 12 meses
  4. Comparar contra benchmark (media ponderada del universo)

Limitacion conocida: las metricas usan datos futuros respecto al punto de
simulacion. Los resultados sobreestiman el rendimiento real del modelo.
Para backtesting riguroso se requiere recalculo de metricas por ventana.

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

from proyecto3.src.regime_classifier import RegimeClassifier, REGIME_WEIGHTS


# ============================================================
# Constantes
# ============================================================

FORWARD_WINDOWS = [1, 3, 12]   # meses forward para calcular rentabilidad
MIN_FUNDS_PER_SUB = 3           # minimo fondos por sub-cartera para simular
BENCHMARK_METRIC = "return_ann" # metrica base para benchmark


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


def _load_portfolio_isins(conn: sqlite3.Connection,
                           score_version: str = "v1") -> dict:
    """
    Carga los ISINs y pesos de cada sub-cartera desde fund_scores.
    Devuelve dict {subportfolio: [(isin, score)]}
    """
    rows = conn.execute("""
        SELECT block, isin, score_total
        FROM fund_scores
        WHERE score_version = ? AND eligible = 1 AND score_total > 0
        ORDER BY block, score_total DESC
    """, (score_version,)).fetchall()

    result = {}
    for block, isin, score in rows:
        result.setdefault(block, []).append((isin, float(score)))
    return result


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


class Backtester:

    def __init__(self, conn: sqlite3.Connection, score_version: str = "v1"):
        self.conn          = conn
        self.score_version = score_version
        self._nav          = _load_nav_matrix(conn)
        self._scores       = _load_portfolio_isins(conn, score_version)
        self._clf          = RegimeClassifier(conn)

    def _build_weights_for_regime(self, regime: str) -> dict:
        """
        Construye el diccionario {isin: weight_master} para un regimen dado.
        Usa top 10 fondos por sub-cartera con pesos proporcionales al score.
        """
        regime_weights = REGIME_WEIGHTS.get(regime, (0.33, 0.34, 0.33))
        sub_names      = ["Defensiva", "Equilibrada", "Dinamica"]
        sub_w          = dict(zip(sub_names, regime_weights))

        isins_master = {}
        used_isins   = set()

        for sub_name, sub_regime_w in sub_w.items():
            if sub_regime_w == 0:
                continue

            funds = [(isin, score) for isin, score in self._scores.get(sub_name, [])
                     if isin not in used_isins][:10]

            if not funds:
                continue

            total_score = sum(s for _, s in funds)
            if total_score <= 0:
                continue

            for isin, score in funds:
                internal_w = score / total_score
                master_w   = internal_w * sub_regime_w
                isins_master[isin] = isins_master.get(isin, 0) + master_w
                used_isins.add(isin)

        return isins_master

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

        # Precalcular pesos por regimen
        regime_weights_cache = {}
        for regime in hist["regime"].unique():
            regime_weights_cache[regime] = self._build_weights_for_regime(regime)

        records = []
        for i, (date, row) in enumerate(hist.iterrows()):
            regime  = row["regime"]
            weights = regime_weights_cache[regime]

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
