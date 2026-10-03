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

import logging
import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
import sys

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))


from proyecto3.src.regime_classifier import RegimeClassifier, REGIME_WEIGHTS
from proyecto3.src.portfolio_engine import (
    select_and_weight, DEFAULT_CONSTRAINTS, round_master_weights, cash_weight,
)
from proyecto3.src.score_candidates import load_current_candidates
from shared.config import REGIME_PUBLICATION_LAG_MONTHS

logger = logging.getLogger(__name__)


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

def _load_nav_matrix(conn: "psycopg.Connection") -> pd.DataFrame:
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


def _load_candidates(conn: "psycopg.Connection",
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
    # FND-0171: misma consulta que PortfolioBuilder (score_candidates.py): solo filas de la
    # ULTIMA ejecucion de scoring del (bloque, version, regimen). Antes cada modulo llevaba su
    # copia y las dos dejaban elegir filas obsoletas de fondos que ya no se puntuan.
    candidates = load_current_candidates(conn, score_version, regime)
    return {
        block: grp.drop(columns="block").reset_index(drop=True)
        for block, grp in candidates.groupby("block", sort=False)
    }


# ============================================================
# Calculo de rentabilidad de cartera hipotetica
# ============================================================

_WARNED: set = set()


def _warn_once(key: str, message: str) -> None:
    """Log `message` once per process -- _cash_return runs per month x window."""
    if key not in _WARNED:
        _WARNED.add(key)
        logger.warning(message)


def _load_cash_rates(conn: "psycopg.Connection") -> pd.Series:
    """
    Tipo de deposito BCE (rate_deposit, % anual, puntos porcentuales) como serie
    mensual a fin de mes, con forward-fill. Es el rendimiento de la linea de
    liquidez del backtest (FND-0197). Vacia si no hay datos.
    """
    rows = conn.execute("""
        SELECT date, value
        FROM series_macro
        WHERE indicator = 'rate_deposit' AND geography = 'EU'
        ORDER BY date
    """).fetchall()
    if not rows:
        return pd.Series(dtype=float)

    df = pd.DataFrame(rows, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    s = df.groupby("date")["value"].last().astype(float)
    full = pd.date_range(s.index.min(), s.index.max(), freq=pd.offsets.MonthEnd())
    return s.reindex(full).ffill()


def _cash_return(
    cash_rates: pd.Series | None,
    date_start: pd.Timestamp,
    date_end: pd.Timestamp,
) -> float:
    """
    Rentabilidad compuesta de la liquidez entre date_start y date_end (FND-0197).

    Cada mes se devenga con el tipo conocido AL INICIO de ese mes (el de fin del
    mes anterior; as-of, nunca un valor futuro): (1 + r/100/12). Funciona con
    tipos negativos (BCE 2014-2022: -0.50%): es un factor mensual, sin divisiones
    ni logaritmos. Sin dato de tipo para un mes (antes del inicio de la serie o
    serie vacia) ese mes devenga 0 -- conservador, y se avisa una sola vez.
    """
    months = pd.date_range(date_start, date_end, freq=pd.offsets.MonthEnd())
    if len(months) < 2:
        return 0.0
    if cash_rates is None or cash_rates.empty:
        _warn_once("cash-no-series", "cash leg: no rate_deposit series -- liquidity accrues 0%")
        return 0.0

    prior = months[:-1]
    rates = cash_rates.reindex(prior, method="ffill")
    if rates.isna().any():
        _warn_once("cash-pre-series",
                   "cash leg: no rate_deposit before %s -- those months accrue 0%%" % cash_rates.index.min().date())
    factors = 1.0 + rates.fillna(0.0).to_numpy(dtype=float) / 100.0 / 12.0
    return float(np.prod(factors) - 1.0)


def _portfolio_return(
    nav_matrix: pd.DataFrame,
    isins_weights: dict,   # {isin: weight}; sum(weights) <= 1, the rest is cash
    date_start: pd.Timestamp,
    months_forward: int,
    cash_rates: pd.Series | None = None,
) -> tuple[float | None, float | None]:
    """
    Rentabilidad de una cartera ponderada en una ventana forward.
    Devuelve (rentabilidad, fund_data_share).

    FND-0188/0197: ya NO se renormalizan los pesos sobre los fondos con dato.
    Peso no asignado (residuo de liquidez, ver portfolio_engine.cash_weight) y
    peso de fondos sin NAV en la ventana se mantienen como liquidez al tipo de
    deposito BCE. fund_data_share = peso con NAV / peso asignado a fondos
    (1.0 = todos los fondos con dato) para que el llamador vea la cobertura.
    Cartera vacia (regimen sin puntuaciones) o sin ningun fondo con dato ->
    (None, share): no se fabrica una rentabilidad 100% liquidez.
    """
    # Encontrar fecha final
    all_dates = nav_matrix.index
    future_dates = all_dates[all_dates > date_start]
    if len(future_dates) < months_forward:
        return None, None

    date_end = future_dates[months_forward - 1]

    total_w = float(sum(isins_weights.values()))
    if total_w <= 0:
        return None, None

    covered_w = 0.0
    weighted = 0.0
    for isin, weight in isins_weights.items():
        if isin not in nav_matrix.columns:
            continue
        nav_start = nav_matrix.loc[date_start, isin] if date_start in nav_matrix.index else None
        nav_end   = nav_matrix.loc[date_end,   isin] if date_end   in nav_matrix.index else None

        if nav_start is None or nav_end is None:
            continue
        if pd.isna(nav_start) or pd.isna(nav_end) or nav_start <= 0:
            continue

        weighted  += ((nav_end / nav_start) - 1) * weight
        covered_w += weight

    share = covered_w / total_w
    if covered_w <= 0:
        return None, share

    cash_share = max(0.0, 1.0 - covered_w)
    if cash_share > 0:
        weighted += cash_share * _cash_return(cash_rates, date_start, date_end)
    return round(float(weighted), 6), share


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
    # FND-0169: same largest-remainder rounding as Portfolio.all_funds, so the map sums to 1.0000 and parity holds.
    ids = list(master)
    return dict(zip(ids, round_master_weights([(i, master[i]) for i in ids])))


class Backtester:

    def __init__(self, conn: "psycopg.Connection", score_version: str = "v1",
                 publication_lags: dict | None = None):
        """publication_lags: {indicator: months} for the regime inputs (FND-0194). None -> the config
        default REGIME_PUBLICATION_LAG_MONTHS (point-in-time regime); {} disables it (legacy behaviour,
        only useful to measure the effect of the lag)."""
        self.conn          = conn
        self.score_version = score_version
        self._nav          = _load_nav_matrix(conn)
        self._cash         = _load_cash_rates(conn)
        lags = REGIME_PUBLICATION_LAG_MONTHS if publication_lags is None else publication_lags
        self._clf          = RegimeClassifier(conn, publication_lags=lags)
        self._selection_cache: dict[str, dict[str, pd.DataFrame]] = {}
        # Fase 3a (P3 optimization plan, migracion SQLite 2026-09-19):
        # _load_candidates ahora requiere `regime` (fund_scores acumula
        # historia por regimen) -- ya no se puede cargar de una vez en
        # __init__ de forma regimen-agnostica. Se carga por regimen dentro
        # de _select_for_regime(), donde ya se cachea por etiqueta.

    @classmethod
    def for_pit(cls, conn: "psycopg.Connection", score_version: str = "v1",
                publication_lags: dict | None = None) -> "Backtester":
        """Backtester listo para run_pit() SIN cargar la matriz NAV del camino legado (el __init__ normal
        hace un SELECT sin filtro + pivot de todo el historico que run_pit no usa: ~675k filas). Solo carga
        el clasificador de regimen con los retrasos de publicacion (mismo valor por defecto que __init__).
        run() (legado) no esta disponible en esta instancia."""
        self = object.__new__(cls)
        self.conn = conn
        self.score_version = score_version
        self._nav = None
        self._cash = None
        lags = REGIME_PUBLICATION_LAG_MONTHS if publication_lags is None else publication_lags
        self._clf = RegimeClassifier(conn, publication_lags=lags)
        self._selection_cache = {}
        return self

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

    def run_pit(
        self,
        start_date: str = "2005-01-31",
        end_date:   str | None = None,
        isins: list | None = None,
        cache_dir: "str | Path | None" = None,
        use_cache: bool = True,
        max_stale_days: int = 45,
        current_universe_only: bool = False,
        tx_cost_bps: float = 0.0,
        entry_sides: int = 1,
        hysteresis_band: float = 0.0,
    ) -> pd.DataFrame:
        """
        Backtest POINT-IN-TIME (FND-0159): en cada fin de mes t el universo se puntua solo con la
        informacion disponible en t (metricas recalculadas sobre el NAV <= t, regimen con retrasos de
        publicacion, fondos retirados incluidos), la cartera se construye con el mismo motor que el
        constructor en vivo, y las rentabilidades forward se calculan en bloque (producto matricial) con
        la liquidez al tipo de deposito BCE. Devuelve las mismas columnas que run() mas n_funds,
        cash_weight y cov_*; summary() las acepta. Ver pit_backtest.py para la metodologia y sus limites
        (el benchmark es el pool elegible equiponderado, sin friccion).

        tx_cost_bps / entry_sides (FND-0190): coste de ejecucion por lado en puntos basicos, cobrado UNA vez
        por ventana de tenencia en la entrada (entry_sides=1; 2 = ida y vuelta, mas estricto); 0 = sin
        friccion. Para la rejilla 0/25/50 pb usar pit_cost_sensitivity(). Columnas extra: ret_wm es neto,
        gross_wm/cost_wm el desglose, target_wm/vs_target_wm el objetivo absoluto IPC+M3 conocido en t,
        cash_ret_1m la rentabilidad mensual de la liquidez (para el Sharpe en summary()).

        hysteresis_band (FND-0205): 0 = cada mes es una seleccion nueva; b > 0 da a los fondos que cada
        sub-cartera tenia el mes anterior un bonus de score b (0.05 = +5%) en el ranking, como el constructor
        en vivo con PORTFOLIO_HYSTERESIS_ENABLED. Para comparar bandas usar pit_hysteresis_experiment().

        isins: lista para acotar el universo (p.ej. la muestra de 40 ISIN); None = universo completo.
        cache_dir/use_cache: cache parquet de las etapas pesadas (use_cache=False = --no-cache).
        run() conserva el comportamiento anterior CON look-ahead, solo para comparar.
        """
        import dataclasses

        from proyecto3.src.pit_backtest import last_complete_month_end, run_pit_backtest
        from proyecto3.src.portfolio_engine import DEFAULT_CONSTRAINTS
        from proyecto3.src.pit_cache import ParquetCache
        from proyecto3.src.pit_inputs import (
            iter_daily_chunks, load_attributes, load_ipc, load_nav_panel, load_rate_deposit,
        )
        from proyecto3.src.pit_run import PitInputs, compute_pit_scores

        hist = self._clf.classify_historical()          # el clasificador de este Backtester ya lleva los retrasos
        if hist.empty:
            print("ERROR: No hay clasificacion historica disponible.")
            return pd.DataFrame()
        nav = load_nav_panel(self.conn, isins)
        if nav.empty:
            return pd.DataFrame()
        # FND-0204: el indice del clasificador con retrasos llega 2 meses mas alla del ultimo dato; no se evalua
        # ninguna fecha posterior al ultimo mes que el NAV cubre por completo
        last = min(hist.index.max(), last_complete_month_end(nav.index.max()))
        if end_date is not None:
            last = min(pd.Timestamp(end_date), last)
        at = pd.date_range(pd.Timestamp(start_date) + pd.offsets.MonthEnd(0), last, freq=pd.offsets.MonthEnd())
        if len(at) == 0:
            return pd.DataFrame()

        attrs = load_attributes(self.conn, isins)
        daily_isins = list(nav.columns)
        inputs = PitInputs(
            nav=nav, attrs=attrs, ipc=load_ipc(self.conn), rate=load_rate_deposit(self.conn),
            daily_chunks=lambda: iter_daily_chunks(self.conn, daily_isins),
        )
        cache = ParquetCache(cache_dir if cache_dir is not None else _ROOT / "proyecto3" / "cache" / "pit",
                             enabled=use_cache)
        print(f"Backtesting PIT | {at[0].date()} -> {at[-1].date()} | {len(at)} meses | {nav.shape[1]} fondos")
        run = compute_pit_scores(inputs, at, hist["regime"], cache, max_stale_days=max_stale_days,
                                 current_universe_only=current_universe_only)
        self.last_pit_run = run                          # tiempos por etapa, cache hits, cobertura de gates cortos
        self.last_pit_context = dict(inputs=inputs, hist=hist, at=at, max_stale_days=max_stale_days,
                                     tx_cost_bps=tx_cost_bps, entry_sides=entry_sides)
        print("  etapas (s): " + ", ".join(f"{k}={v:.1f}" for k, v in run.timings.items())
              + f" | cache: {run.cache_hits}")
        target = self._clf.absolute_target_annual() if hasattr(self._clf, "absolute_target_annual") else None
        self.last_pit_context["target"] = target
        constraints = (dataclasses.replace(DEFAULT_CONSTRAINTS, hysteresis_band=hysteresis_band)
                       if hysteresis_band else DEFAULT_CONSTRAINTS)
        return run_pit_backtest(run, inputs, hist, at, max_stale_days=max_stale_days, tx_cost_bps=tx_cost_bps,
                                entry_sides=entry_sides, target_annual=target, constraints=constraints,
                                use_incumbents=bool(hysteresis_band))

    def pit_hysteresis_experiment(self, bands=(0.0, 0.05, 0.10, 0.20, 0.40), tx_cost_bps: float | None = None,
                                  entry_sides: int | None = None) -> pd.DataFrame:
        """FND-0205: una fila por banda de histeresis (0 = sin titulares) sobre la ULTIMA ejecucion de run_pit():
        rotacion, lastre anual de costes, estadisticas de la serie encadenada neta de rotacion real y
        resultado a 12 meses. Mismos scores y mismos retornos forward en todas las filas."""
        from proyecto3.src.pit_backtest import hysteresis_experiment
        if not hasattr(self, "last_pit_run"):
            raise RuntimeError("run_pit() debe ejecutarse antes que pit_hysteresis_experiment()")
        c = self.last_pit_context
        return hysteresis_experiment(
            self.last_pit_run, c["inputs"], c["hist"], c["at"], bands=bands,
            tx_cost_bps=c.get("tx_cost_bps", 25.0) if tx_cost_bps is None else tx_cost_bps,
            entry_sides=c.get("entry_sides", 1) if entry_sides is None else entry_sides,
            max_stale_days=c["max_stale_days"], target_annual=c.get("target"))

    def pit_cost_sensitivity(self, bps=(0.0, 25.0, 50.0), entry_sides: int = 1) -> pd.DataFrame:
        """Rejilla de sensibilidad a costes simetricos (por defecto 0/25/50 pb por lado) sobre la ULTIMA
        ejecucion de run_pit(); la seleccion se calcula una sola vez para todos los niveles de coste."""
        from proyecto3.src.pit_backtest import cost_sensitivity
        if not hasattr(self, "last_pit_run"):
            raise RuntimeError("run_pit() debe ejecutarse antes que pit_cost_sensitivity()")
        c = self.last_pit_context
        return cost_sensitivity(self.last_pit_run, c["inputs"], c["hist"], c["at"], bps=bps, entry_sides=entry_sides,
                                max_stale_days=c["max_stale_days"], target_annual=c.get("target"))

    def run(
        self,
        start_date: str = "2005-01-01",
        end_date:   str | None = None,
    ) -> pd.DataFrame:
        """
        Ejecuta el backtesting LEGADO para el periodo dado (usa los scores ACTUALES: con look-ahead;
        el backtest riguroso es run_pit()).

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
            selection = self._select_for_regime(regime)
            if all(df.empty for df in selection.values()):
                # FND-0188: no scored candidates for this regime -> its months cannot be evaluated
                # (and are NOT back-filled with another regime's scores). Say so instead of dropping silently.
                n_months = int((hist["regime"] == regime).sum())
                logger.warning(
                    "backtest: regime %s has no scored candidates -- %d of %d months are NOT evaluated "
                    "(run score_funds() under that regime to cover it)", regime, n_months, len(hist),
                )

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
                "date":        date,
                "regime":      regime,
                "n_funds":     len(weights),
                "cash_weight": cash_weight(weights) if weights else None,
            }

            for w in FORWARD_WINDOWS:
                ret, share = _portfolio_return(self._nav, weights, date, w, self._cash)
                bench = _benchmark_return(self._nav, date, w)
                rec[f"ret_{w}m"]    = ret
                rec[f"cov_{w}m"]    = share
                rec[f"bench_{w}m"]  = bench
                rec[f"excess_{w}m"] = (ret - bench
                                        if ret is not None and bench is not None
                                        else None)

            records.append(rec)

            if (i + 1) % 50 == 0:
                print(f"  Procesados {i+1}/{len(hist)} meses...")

        df = pd.DataFrame(records).set_index("date")
        n_eval = int(df["ret_1m"].notna().sum())
        print(f"Backtesting completado. {n_eval} de {len(df)} meses evaluados.")
        return df

    def summary(self, results: pd.DataFrame) -> str:
        """Genera un resumen legible de los resultados del backtesting."""
        if results.empty:
            return "Sin resultados."

        lines = [
            "BACKTESTING P3 -- Resumen por regimen",
            "=" * 60,
        ]

        # FND-0188: months that could not be evaluated are reported, not silently dropped
        if "n_funds" in results.columns:
            empty = results[results["n_funds"] == 0]
            if not empty.empty:
                by_regime = empty.groupby("regime").size()
                lines.append("COBERTURA: meses SIN evaluar por regimen sin puntuaciones -> "
                             + ", ".join(f"{r}: {n}" for r, n in by_regime.items())
                             + f" (de {len(results)} meses)")
        for w in FORWARD_WINDOWS:
            cov_col, ret_col = f"cov_{w}m", f"ret_{w}m"
            if cov_col not in results.columns:
                continue
            beyond = results[ret_col].isna()
            if beyond.any():
                lines.append(f"COBERTURA: {int(beyond.sum())} meses sin rentabilidad a {w}m (ventana mas alla del "
                             f"historial NAV o cartera vacia; no evaluados)")
            partial = results[cov_col].dropna()
            partial = partial[(partial > 0) & (partial < 1.0)]
            if len(partial):
                lines.append(f"COBERTURA: {len(partial)} meses a {w}m con fondos sin NAV (su peso se mantuvo en "
                             f"liquidez; cobertura media {partial.mean():.0%})")

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

        # FND-0189/0190: serie mensual encadenada (rentabilidades 1m sin solapar), Sharpe contra la liquidez
        # real de cada mes y objetivo absoluto IPC+M3 -- solo en tablas de run_pit() (llevan cash_ret_1m)
        if "cash_ret_1m" in results.columns and "bench_1m" in results.columns:
            from proyecto3.src.pit_backtest import table_stats
            stats = table_stats(results)
            lines.append("")
            net = " neta de la rotacion real mes a mes" if "net_turnover_1m" in results.columns else ""
            lines.append("SERIE SIMULADA (1m encadenado" + net + "; Sharpe = exceso sobre la liquidez de cada mes):")
            for name, s in (("cartera", stats["portfolio"]), ("pool benchmark", stats["benchmark"])):
                lines.append(
                    f"  {name:15s} rent. anual {s['ann_return']*100:+.1f}% | vol {s['ann_vol']*100:.1f}% | "
                    f"Sharpe {s['sharpe']:.2f} | max DD {s['max_drawdown']*100:.1f}% | {s['n_months']} meses"
                )
            if "tx_cost_bps" in results.attrs:
                lines.append(f"  costes: {results.attrs['tx_cost_bps']:.0f} pb/lado; rentabilidades por ventana con coste de "
                             f"entrada x {results.attrs['entry_sides']} lado(s); serie encadenada con coste por rotacion")
                if "turnover" in results.columns and results["turnover"].notna().any():
                    lines.append(f"  rotacion media mensual {results['turnover'].mean()*100:.0f}% del capital")
            for w in FORWARD_WINDOWS:
                col = f"vs_target_{w}m"
                if col in results.columns and results[col].notna().any():
                    v = results[col].dropna()
                    lines.append(f"  objetivo IPC+M3 {w:2d}m: supera en {(v > 0).mean()*100:.0f}% de los meses "
                                 f"(exceso medio {v.mean()*100:+.1f}%)")

        return "\n".join(lines)

    def drawdown_series(self, results: pd.DataFrame,
                        window: int = 12) -> pd.Series:
        """Calcula la serie de drawdown de la cartera simulada."""
        rets = results[f"ret_{window}m"].dropna()
        cum  = (1 + rets).cumprod()
        roll_max = cum.cummax()
        dd = (cum / roll_max) - 1
        return dd
