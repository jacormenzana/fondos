# proyecto2/src/loaders/macro_loader.py
# -*- coding: utf-8 -*-
"""
Carga de datos macroeconomicos desde APIs publicas gratuitas.

Fuentes implementadas:
  +-----------------+----------------------------------------------+
  | Fuente          | Indicadores                                  |
  +-----------------+----------------------------------------------+
  | INE (Espana)    | IPC mensual Espana                           |
  | BCE SDW         | IPC Eurozona, M3, tipo deposito, tipo refi   |
  | Fed FRED        | IPC EEUU, M2 EEUU, spread_hy, vix,           |
  |                 | term_spread                                  |
  | Eurostat        | PIB nominal Eurozona, deficit/PIB            |
  +-----------------+----------------------------------------------+

Uso:
    cd c:/desarrollo/fondos
    python -m proyecto2.src.loaders.macro_loader

    Opciones:
    --source ine|bce|fred|eurostat|all   (default: all)
    --desde  2000-01                     fecha inicio (YYYY-MM)
    --dry-run                            descarga pero no escribe en DB
    --verbose                            muestra cada registro cargado

Periodicidad recomendada: mensual (ejecutar el dia 20 de cada mes,
cuando ya estan disponibles los datos del mes anterior en la mayoria
de fuentes).
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime, date
from pathlib import Path
from typing import Iterator

import pandas as pd
import requests

# -- Path setup -----------------------------------------------
_P2_SRC = Path(__file__).resolve().parent.parent          # proyecto2/src
_ROOT   = _P2_SRC.parent.parent                           # c:\desarrollo\fondos
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_P2_SRC.parent))                   # proyecto2/

from shared.config import DB_PATH
from shared.db import get_connection

# -- Constantes -----------------------------------------------
REQUEST_TIMEOUT = 30        # segundos por peticion HTTP
RETRY_WAIT      = 5         # segundos entre reintentos
MAX_RETRIES     = 3

# Directorio de logs: proyecto2/log/  (__file__ = proyecto2/src/loaders/macro_loader.py)
_LOG_DIR = _P2_SRC.parent / "log"


# ============================================================
# Captura de consola -> fichero log
# ============================================================

class _Tee:
    """Reescribe stdout/stderr a la consola y a un fichero simultaneamente."""

    def __init__(self, stream, fh):
        self._stream = stream      # consola original (stdout o stderr)
        self._fh     = fh          # handle del fichero log

    def write(self, data):
        self._stream.write(data)
        self._fh.write(data)
        self._fh.flush()
        return len(data)

    def flush(self):
        self._stream.flush()
        self._fh.flush()

    def isatty(self):
        return getattr(self._stream, "isatty", lambda: False)()


def _setup_run_logger(font: str):
    """
    Crea proyecto2/log/log_P2_macro_loader_{font}_{YYYYMMDD_HHMM}.log
    y redirige stdout/stderr a consola + fichero.

    Devuelve (file_handle, stdout_original, stderr_original) para restaurar.
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp    = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = _LOG_DIR / f"log_P2_macro_loader_{font}_{stamp}.log"

    fh = open(log_path, "w", encoding="utf-8")
    orig_out, orig_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(orig_out, fh)
    sys.stderr = _Tee(orig_err, fh)

    print(f"  [LOG] Volcando salida a: {log_path}")
    return fh, orig_out, orig_err


def _teardown_run_logger(fh, orig_out, orig_err) -> None:
    """Restaura stdout/stderr y cierra el fichero log."""
    sys.stdout, sys.stderr = orig_out, orig_err
    fh.close()


# ============================================================
# Helpers HTTP
# ============================================================

def _get_json(url: str, params: dict | None = None) -> dict:
    """GET con reintentos y timeout."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            print(f"  Reintento {attempt}/{MAX_RETRIES} ({e})...")
            time.sleep(RETRY_WAIT)
    raise RuntimeError("Max reintentos alcanzados")


def _get_csv(url: str, params: dict | None = None, **kwargs) -> pd.DataFrame:
    """GET CSV con reintentos."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            from io import StringIO
            return pd.read_csv(StringIO(r.text), **kwargs)
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            print(f"  Reintento {attempt}/{MAX_RETRIES} ({e})...")
            time.sleep(RETRY_WAIT)
    raise RuntimeError("Max reintentos alcanzados")


# ============================================================
# Escritura en DB
# ============================================================

def _write_inflation(
    conn: sqlite3.Connection,
    rows: list[dict],
    dry_run: bool,
) -> int:
    """
    Escribe en series_inflation (tabla de deflactacion).
    Cada fila: {date, geography, ipc_index, source}
    """
    if not rows or dry_run:
        return 0
    sql = """
        INSERT OR REPLACE INTO series_inflation
            (date, geography, ipc_index, source)
        VALUES (?, ?, ?, ?)
    """
    conn.executemany(sql, [
        (r["date"], r["geography"], r["ipc_index"], r["source"])
        for r in rows
    ])
    conn.commit()
    return len(rows)


def _write_macro(
    conn: sqlite3.Connection,
    rows: list[dict],
    dry_run: bool,
) -> int:
    """
    Escribe en series_macro (tabla de contexto).
    Cada fila: {date, indicator, geography, value, unit, source}
    """
    if not rows or dry_run:
        return 0
    sql = """
        INSERT OR REPLACE INTO series_macro
            (date, indicator, geography, value, unit, source)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    conn.executemany(sql, [
        (r["date"], r["indicator"], r["geography"],
         r["value"], r["unit"], r["source"])
        for r in rows
    ])
    conn.commit()
    return len(rows)


def _fmt_date(d: str | date) -> str:
    """Normaliza cualquier fecha a 'YYYY-MM-DD'."""
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    if len(d) == 7:          # 'YYYY-MM'
        return d + "-01"
    return str(d)[:10]


# ============================================================
# INE -- IPC Espana
# ============================================================

def load_ine_ipc(desde: str = "2000-01", verbose: bool = False) -> list[dict]:
    """
    Descarga el IPC General Nacional (indice base 2021=100) desde la API JSON del INE.

    Tabla 50902: ?ndices nacionales, general y grupos ECOICOP (base 2021).
    Filtros aplicados:
      tv=3:74       -> Tipo de dato = ?ndice
      tv=762:304092 -> Grupos ECOICOP = General

    El INE publica valores mensuales con un desfase de ~3 semanas.
    Formato fecha INE: "2024M01" -> almacenamos como "2024-01-01".
    """
    print("  [INE] Descargando IPC Espana...")

    url = "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/50902"
    params = {
        "tv":    ["3:74", "762:304092"],   # ?ndice General
        "tip":   "A",                       # salida amigable
    }

    try:
        # requests no repite parametros con lista -- construimos la URL manualmente
        import urllib.parse
        qs = "tv=3%3A74&tv=762%3A304092&tip=A"
        full_url = f"{url}?{qs}"
        data = _get_json(full_url)
    except Exception as e:
        print(f"  [INE] ERROR: {e}")
        return []

    if not data:
        print("  [INE] Sin datos en respuesta")
        return []

    # La API DATOS_TABLA devuelve una lista de series, cada una con Data
    # Recorremos todas las series y sus datos
    rows = []
    series_list = data if isinstance(data, list) else [data]

    for serie in series_list:
        serie_data = serie.get("Data", [])
        for item in sorted(serie_data, key=lambda x: x.get("Fecha", "")):
            fecha_raw = item.get("Fecha", "")
            valor     = item.get("Valor")

            if valor is None:
                continue

            # Formato fecha INE: "2024M01" -> "2024-01-01"
            if "M" in str(fecha_raw):
                parts = str(fecha_raw).split("M")
                fecha = f"{parts[0]}-{parts[1].zfill(2)}-01"
            else:
                fecha = _fmt_date(str(fecha_raw))

            if fecha < desde + "-01":
                continue

            row = {
                "date":      fecha,
                "geography": "ES",
                "ipc_index": float(valor),
                "source":    "INE",
            }
            rows.append(row)

            if verbose:
                print(f"    {fecha}  IPC_ES={valor:.2f}")

    # Eliminar duplicados por fecha (puede haber varias series en la respuesta)
    seen = {}
    for r in rows:
        seen[r["date"]] = r
    rows = sorted(seen.values(), key=lambda x: x["date"])

    print(f"  [INE] {len(rows)} registros descargados (IPC Espana)")
    return rows


# ============================================================
# BCE SDW -- IPC Eurozona, M3, Tipos
# ============================================================

_BCE_SERIES = {
    # IPC Eurozona (HICP) -- indice base 2015=100
    "ipc_yoy_EU": {
        "series_key": "ICP/M.U2.N.000000.4.INX",
        "indicator":  "ipc_index",
        "geography":  "EU",
        "unit":       "index",
        "write_inflation": True,
    },
    # M3 Eurozona -- variacion interanual (%)
    "m3_yoy_EU": {
        "series_key": "BSI/M.U2.Y.V.M30.X.I.U2.2300.Z01.A",
        "indicator":  "m3_yoy",
        "geography":  "EU",
        "unit":       "pct",
        "write_inflation": False,
    },
    # M2 Eurozona -- variacion interanual (%)
    "m2_yoy_EU": {
        "series_key": "BSI/M.U2.Y.V.M20.X.I.U2.2300.Z01.A",
        "indicator":  "m2_yoy",
        "geography":  "EU",
        "unit":       "pct",
        "write_inflation": False,
    },
    # Tipo de deposito BCE -- frecuencia decision, se normaliza a mensual
    "rate_deposit_EU": {
        "series_key": "FM/B.U2.EUR.4F.KR.DFR.LEV",
        "indicator":  "rate_deposit",
        "geography":  "EU",
        "unit":       "pct",
        "write_inflation": False,
    },
    # Tipo de referencia BCE -- serie mensual normalizada (%)
    # M2 Eurozona nivel absoluto (millones EUR) -- para M2 Global
    "m2_level_EU": {
        "series_key": "BSI/M.U2.Y.V.M20.X.1.U2.2300.Z01.E",
        "indicator":  "m2_level",
        "geography":  "EU",
        "unit":       "eur_mn",
        "write_inflation": False,
    },
    # M3 Eurozona nivel absoluto (millones EUR) -- coherencia con m2_level_EU
    # Permite calcular m3_yoy desde nivel propio y tener la serie de stock
    # El BCE usa M3 como pilar monetario de referencia (target historico ~4.5% YoY)
    "m3_level_EU": {
        "series_key": "BSI/M.U2.Y.V.M30.X.1.U2.2300.Z01.E",
        "indicator":  "m3_level",
        "geography":  "EU",
        "unit":       "eur_mn",
        "write_inflation": False,
    },
    "rate_refi_EU": {
        "series_key": "FM/B.U2.EUR.4F.KR.MRR_FR.LEV",
        "indicator":  "rate_policy",
        "geography":  "EU",
        "unit":       "pct",
        "write_inflation": False,
    },
}


def load_bce_series(desde: str = "2000-01", verbose: bool = False) -> tuple[list[dict], list[dict]]:
    """
    Descarga series del BCE Statistical Data Warehouse (SDW).
    API REST: https://data-api.ecb.europa.eu/service/data/

    Devuelve: (inflation_rows, macro_rows)
    """
    print("  [BCE] Descargando series...")

    inflation_rows: list[dict] = []
    macro_rows:     list[dict] = []

    base_url = "https://data-api.ecb.europa.eu/service/data"

    for name, cfg in _BCE_SERIES.items():
        url = f"{base_url}/{cfg['series_key']}"
        params = {
            "format":     "csvdata",
            "startPeriod": desde,
        }
        try:
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  [BCE] ERROR en {name}: {e}")
            continue

        from io import StringIO
        try:
            df = pd.read_csv(StringIO(r.text))
        except Exception as e:
            print(f"  [BCE] Error parseando CSV {name}: {e}")
            continue

        # Columnas esperadas en CSV del BCE: TIME_PERIOD, OBS_VALUE
        if "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
            print(f"  [BCE] Formato inesperado en {name}: {list(df.columns)}")
            continue

        # Para series de tipo de interes: forward-fill a frecuencia mensual
        is_rate = cfg["indicator"] in ("rate_deposit", "rate_policy")

        if is_rate:
            # Construir serie mensual por forward-fill desde fechas de decision.
            # Solo se emite UNA fila por mes (primer dia del mes).
            df = df[~pd.isna(df["OBS_VALUE"])].copy()
            df["TIME_PERIOD"] = pd.to_datetime(df["TIME_PERIOD"])
            df = df.sort_values("TIME_PERIOD")

            # Rango mensual: primer dia de cada mes desde inicio hasta hoy
            start_dt = pd.to_datetime(desde + "-01")
            end_dt   = pd.Timestamp.today().replace(day=1)
            months   = pd.date_range(start_dt, end_dt, freq="MS")

            count = 0
            for month in months:
                # Ultimo tipo vigente en o antes del primer dia del mes
                prev = df[df["TIME_PERIOD"] <= month]
                if prev.empty:
                    continue
                value = float(prev.iloc[-1]["OBS_VALUE"])
                fecha = month.strftime("%Y-%m-%d")  # siempre YYYY-MM-01
                macro_rows.append({
                    "date":      fecha,
                    "indicator": cfg["indicator"],
                    "geography": cfg["geography"],
                    "value":     value,
                    "unit":      cfg["unit"],
                    "source":    "BCE",
                })
                count += 1
                if verbose:
                    print(f"    {fecha}  {cfg['indicator']}={value}")
        else:
            count = 0
            for _, row in df.iterrows():
                period = str(row["TIME_PERIOD"])
                value  = row["OBS_VALUE"]

                if pd.isna(value):
                    continue

                fecha = _fmt_date(period)
                if fecha < desde + "-01":
                    continue

                macro_row = {
                    "date":      fecha,
                    "indicator": cfg["indicator"],
                    "geography": cfg["geography"],
                    "value":     float(value),
                    "unit":      cfg["unit"],
                    "source":    "BCE",
                }
                macro_rows.append(macro_row)

                if cfg.get("write_inflation"):
                    inflation_rows.append({
                        "date":      fecha,
                        "geography": cfg["geography"],
                        "ipc_index": float(value),
                        "source":    "BCE",
                    })

                count += 1
                if verbose:
                    print(f"    {fecha}  {cfg['indicator']}={value}")

        print(f"  [BCE] {name}: {count} registros")

    return inflation_rows, macro_rows


# ============================================================
# Fed FRED -- IPC EEUU, M2 EEUU
# ============================================================

_FRED_SERIES = {
    "CPIAUCSL": {
        "indicator":  "ipc_index",
        "geography":  "US",
        "unit":       "index",
        "write_inflation": True,
    },
    # IPC Espana -- HICP base 2015=100 (Eurostat via FRED)
    "CP0000ESM086NEST": {
        "indicator":  "ipc_index",
        "geography":  "ES",
        "unit":       "index",
        "write_inflation": True,
    },
    # M2 EEUU nivel absoluto (bn USD) -- el builder calcula YoY
    "M2SL": {
        "indicator":  "m2_level",
        "geography":  "US",
        "unit":       "usd_bn",
        "write_inflation": False,
    },
    "FEDFUNDS": {
        "indicator":  "rate_policy",
        "geography":  "US",
        "unit":       "pct",
        "write_inflation": False,
    },
    # Tipo politica Banco de Japon (%)
    "IRSTCI01JPM156N": {
        "indicator":  "rate_policy",
        "geography":  "JP",
        "unit":       "pct",
        "write_inflation": False,
    },
    # Tipo referencia Banco Popular de China (LPR, %)
    "INTDSRCNM193N": {
        "indicator":  "rate_policy",
        "geography":  "CN",
        "unit":       "pct",
        "write_inflation": False,
    },
    # IPC Japon -- indice base 2015=100 (OCDE via FRED)
    "JPNCPIALLMINMEI": {
        "indicator":  "ipc_index",
        "geography":  "JP",
        "unit":       "index",
        "write_inflation": False,
    },
    # IPC China -- indice base (OCDE via FRED)
    "CHNCPALTT01IXNBM": {
        "indicator":  "ipc_index",
        "geography":  "CN",
        "unit":       "index",
        "write_inflation": False,
    },
    # Petroleo crudo WTI -- precio mensual (USD/barril)
    "DCOILWTICO": {
        "indicator":        "oil_wti",
        "geography":        "GLOBAL",
        "unit":             "usd",
        "write_inflation":  False,
        "resample_monthly": True,
    },
    # Cobre -- precio mensual (USD/tonelada metrica)
    "PCOPPUSDM": {
        "indicator":  "copper",
        "geography":  "GLOBAL",
        "unit":       "usd",
        "write_inflation": False,
    },
    # Tasa de desempleo EEUU (%) -- indicador de ciclo economico
    "UNRATE": {
        "indicator":  "unemployment",
        "geography":  "US",
        "unit":       "pct",
        "write_inflation": False,
    },
    # CLI OCDE Alemania -- proxy Eurozona (EA19 discontinuada en FRED)
    # Alta correlacion con ciclo europeo, datos hasta 2026
    "DEULOLITOAASTSAM": {
        "indicator":  "cli",
        "geography":  "EU",
        "unit":       "index",
        "write_inflation": False,
    },
    # CLI OCDE EEUU -- indice compuesto adelantado
    "USALOLITOAASTSAM": {
        "indicator":  "cli",
        "geography":  "US",
        "unit":       "index",
        "write_inflation": False,
    },
    # CLI OCDE Japon -- contexto para clasificador regimen macro P3
    "JPNLOLITOAASTSAM": {
        "indicator":  "cli",
        "geography":  "JP",
        "unit":       "index",
        "write_inflation": False,
    },
    # CLI OCDE China -- contexto para clasificador regimen macro P3
    "CHNLOLITOAASTSAM": {
        "indicator":  "cli",
        "geography":  "CN",
        "unit":       "index",
        "write_inflation": False,
    },
    # CLI OCDE Espana -- contexto para clasificador regimen macro P3
    "ESPLOLITOAASTSAM": {
        "indicator":  "cli",
        "geography":  "ES",
        "unit":       "index",
        "write_inflation": False,
    },
    # Tipos de cambio mensuales (para descomposicion divisa vs activo en P2)
    # EXUSEU: USD por EUR (ej. 1.10 = 1 EUR = 1.10 USD)
    "EXUSEU": {
        "indicator":  "fx_usd_eur",
        "geography":  "GLOBAL",
        "unit":       "ratio",
        "write_inflation": False,
    },
    # EXJPUS: JPY por USD (ej. 150 = 1 USD = 150 JPY)
    "EXJPUS": {
        "indicator":  "fx_jpy_usd",
        "geography":  "GLOBAL",
        "unit":       "ratio",
        "write_inflation": False,
    },
    # EXUSUK: USD por GBP (ej. 1.27 = 1 GBP = 1.27 USD)
    "EXUSUK": {
        "indicator":  "fx_usd_gbp",
        "geography":  "GLOBAL",
        "unit":       "ratio",
        "write_inflation": False,
    },
    # EXCHUS: CNY por USD (ej. 7.1 = 1 USD = 7.1 CNY)
    "EXCHUS": {
        "indicator":  "fx_cny_usd",
        "geography":  "GLOBAL",
        "unit":       "ratio",
        "write_inflation": False,
    },

    # Dollar Index (DXY) -- indice dolar USA vs cesta divisas (base ene-1997=100)
    # Serie diaria -- se resamplea a mensual (media del mes)
    "DTWEXBGS": {
        "indicator":        "dxy",
        "geography":        "GLOBAL",
        "unit":             "index",
        "write_inflation":  False,
        "resample_monthly": True,
    },

    # PPI Metales (PPICMM) -- mejor proxy de oro disponible en FRED
    # Incluye oro, plata y platino. Correlacion >0.95 con precio spot oro.
    # FRED ha retirado las series de precio spot del oro de acceso publico.
    # Disponible hasta feb 2026.
    "PPICMM": {
        "indicator":  "gold",
        "geography":  "GLOBAL",
        "unit":       "index",
        "write_inflation": False,
    },

    # M2 China (CNY millones) -- para M2 Global
    "MYAGM2CNM189N": {
        "indicator":  "m2_level",
        "geography":  "CN",
        "unit":       "cny_mn",
        "write_inflation": False,
    },

    # M2 Japon (JPY millones) -- para M2 Global
    "MYAGM2JPM189N": {
        "indicator":  "m2_level",
        "geography":  "JP",
        "unit":       "jpy_mn",
        "write_inflation": False,
    },

    # NOTA: M2 USA nivel se obtiene de M2SL que ya se carga arriba.
    # m2_global_builder.py usa la serie m2_yoy_US para reconstruir nivel.

    # ------------------------------------------------------------
    # Factores de riesgo financiero (pipeline v10)
    # ------------------------------------------------------------

    # ICE BofA High Yield Option-Adjusted Spread (%) -- diaria
    # Mide el diferencial de credito HY vs soberanos EEUU.
    # Valores altos = aversion al riesgo / estres crediticio.
    # Se resamplea a mensual con media (representa condicion media del mes).
    "BAMLH0A0HYM2": {
        "indicator":        "spread_hy",
        "geography":        "GLOBAL",
        "unit":             "pct",
        "write_inflation":  False,
        "resample_monthly": True,
        "resample_func":    "mean",
    },

    # CBOE Volatility Index (VIX) -- diaria
    # Volatilidad implicita opciones S&P 500 a 30 dias.
    # Se resamplea a mensual con media. El factor OLS usa variacion YoY.
    "VIXCLS": {
        "indicator":        "vix",
        "geography":        "GLOBAL",
        "unit":             "points",
        "write_inflation":  False,
        "resample_monthly": True,
        "resample_func":    "mean",
    },

    # 10-Year Treasury Constant Maturity Minus 2-Year (%) -- mensual
    # Pendiente de la curva EEUU: positivo = curva normal, negativo = invertida.
    # Indicador adelantado de recesion cuando se mantiene negativo.
    "T10Y2YM": {
        "indicator":  "term_spread",
        "geography":  "US",
        "unit":       "pct",
        "write_inflation": False,
    },

    # ICE BofA Investment Grade Option-Adjusted Spread (%) -- diaria
    # Diferencial de credito IG vs soberanos EEUU.
    # Complemento al spread_hy: permite distinguir estres IG vs HY puro.
    # Se resamplea a mensual con media (condicion media del mes).
    "BAMLC0A0CM": {
        "indicator":        "spread_ig",
        "geography":        "GLOBAL",
        "unit":             "pct",
        "write_inflation":  False,
        "resample_monthly": True,
        "resample_func":    "mean",
    },
}

FRED_BASE_URL    = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_API_BASE    = "https://api.stlouisfed.org/fred/series/observations"


def _fred_fetch(series_id: str, desde: str, api_key: str | None) -> pd.DataFrame | None:
    """
    Descarga una serie FRED como DataFrame con columnas [date, value].

    Si hay api_key usa la API oficial (historial completo para todas las series,
    incluidas las ICE BofA con licencia restringida en el endpoint publico).
    Sin api_key usa fredgraph.csv, que para algunas series solo devuelve ~3 años.
    """
    from io import StringIO
    if api_key:
        params = {
            "series_id":         series_id,
            "api_key":           api_key,
            "observation_start": desde + "-01",
            "file_type":         "json",
        }
        try:
            r = requests.get(FRED_API_BASE, params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            obs = r.json().get("observations", [])
            rows = [(o["date"], o["value"]) for o in obs if o["value"] != "."]
            if not rows:
                return None
            df = pd.DataFrame(rows, columns=["date", "value"])
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df["date"]  = pd.to_datetime(df["date"])
            return df.dropna(subset=["value"])
        except requests.RequestException as e:
            raise e
    else:
        params = {"id": series_id}
        r = requests.get(FRED_BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text), na_values=".")
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"])
        return df.dropna(subset=["value"])


def load_fred_series(
    desde:   str = "2000-01",
    verbose: bool = False,
    api_key: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Descarga series de la Fed FRED.

    api_key: clave FRED (gratis en https://fred.stlouisfed.org/docs/api/api_key.html).
             Sin clave usa el endpoint publico, que restringe series ICE BofA a ~3 años.
             Con clave usa la API oficial con historial completo.

    Devuelve: (inflation_rows, macro_rows)
    """
    import os
    if api_key is None:
        api_key = os.environ.get("FRED_API_KEY") or None

    if api_key:
        print(f"  [FRED] Descargando series (API key: {api_key[:6]}...)...")
    else:
        print("  [FRED] Descargando series (sin API key -- series ICE BofA limitadas a ~3 años)...")

    inflation_rows: list[dict] = []
    macro_rows:     list[dict] = []

    for series_id, cfg in _FRED_SERIES.items():
        try:
            df = _fred_fetch(series_id, desde, api_key)
        except requests.RequestException as e:
            print(f"  [FRED] ERROR en {series_id}: {e}")
            continue

        if df is None or df.empty:
            print(f"  [FRED] {series_id}: sin datos")
            continue

        from io import StringIO
        df = df.dropna(subset=["value"])

        # Resampleo a mensual para series de frecuencia diaria (ej. WTI, VIX)
        # resample_func: "last" (default) para precios de cierre,
        #                "mean" para indicadores de condicion media (VIX, spreads)
        if cfg.get("resample_monthly"):
            func = cfg.get("resample_func", "last")
            sampler = df.set_index("date")["value"].resample("MS")
            if func == "mean":
                resampled = sampler.mean()
            else:
                resampled = sampler.last()
            df = resampled.dropna().reset_index()
            df.columns = ["date", "value"]

        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        df = df[df["date"] >= desde + "-01"]

        count = 0
        for _, row in df.iterrows():
            macro_row = {
                "date":      row["date"],
                "indicator": cfg["indicator"],
                "geography": cfg["geography"],
                "value":     float(row["value"]),
                "unit":      cfg["unit"],
                "source":    "FRED",
            }
            macro_rows.append(macro_row)

            if cfg.get("write_inflation"):
                inflation_rows.append({
                    "date":      row["date"],
                    "geography": cfg["geography"],
                    "ipc_index": float(row["value"]),
                    "source":    "FRED",
                })

            count += 1
            if verbose:
                print(f"    {row['date']}  {series_id}={row['value']}")

        print(f"  [FRED] {series_id}: {count} registros")

    return inflation_rows, macro_rows


# ============================================================
# Eurostat -- PIB Eurozona, deficit/PIB
# ============================================================

def load_eurostat_series(desde: str = "2000-01", verbose: bool = False) -> list[dict]:
    """
    Descarga datos de Eurostat via API JSON.
    Frecuencia: trimestral (PIB) y anual (deficit).
    Se guarda en series_macro con granularidad disponible.

    Datasets:
      - namq_10_gdp: PIB trimestral Eurozona (GDP_MKT_EU)
      - gov_10dd_edpt1: Deficit/deuda anual (por pais)
    """
    print("  [EUROSTAT] Descargando series...")

    macro_rows: list[dict] = []
    base_url = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"

    # -- PIB Eurozona trimestral (en millones EUR) -------------
    try:
        url    = f"{base_url}/namq_10_gdp"
        params = {
            "format": "JSON",
            "lang":   "EN",
            "unit":   "CP_MEUR",      # precios corrientes millones EUR
            "s_adj":  "NSA",          # sin ajuste estacional
            "na_item": "B1GQ",        # PIB
            "geo":    "EA",           # Eurozona
        }
        data = _get_json(url, params)

        values  = data.get("value", {})
        periods = list(data.get("dimension", {})
                           .get("time", {})
                           .get("category", {})
                           .get("index", {}).keys())

        count = 0
        for idx, period in enumerate(periods):
            val = values.get(str(idx))
            if val is None:
                continue
            # Formato: "2024-Q1" -> "2024-01-01"
            if "-Q" in period:
                y, q = period.split("-Q")
                month = (int(q) - 1) * 3 + 1
                fecha = f"{y}-{month:02d}-01"
            else:
                fecha = _fmt_date(period)

            if fecha < desde + "-01":
                continue

            macro_rows.append({
                "date":      fecha,
                "indicator": "gdp_nom_eur",
                "geography": "EU",
                "value":     float(val),
                "unit":      "eur_mn",
                "source":    "EUROSTAT",
            })
            count += 1

        print(f"  [EUROSTAT] PIB Eurozona: {count} registros")

    except Exception as e:
        print(f"  [EUROSTAT] ERROR PIB: {e}")

    # -- Deficit/PIB y Deuda/PIB (anual) -------------------------
    # Eurostat solo acepta una geografia por peticion para este dataset.
    # EA20 = Eurozona desde 2023, EA19 = Eurozona hasta 2022.
    # Se hacen peticiones individuales y se fusionan bajo "EU".
    _GOV_QUERIES = [
        ("deficit_gdp_pct", "B9", "Deficit/PIB"),
        ("debt_gdp_pct",    "GD", "Deuda/PIB"),
    ]
    _GOV_GEOS = [("ES", "ES"), ("EA20", "EU"), ("EA19", "EU")]

    for gov_ind, na_item, label in _GOV_QUERIES:
        count  = 0
        seen   = set()
        for geo_code, geo_norm in _GOV_GEOS:
            try:
                params = {
                    "format":  "JSON", "lang": "EN",
                    "na_item": na_item, "sector": "S13",
                    "unit":    "PC_GDP", "geo": geo_code,
                }
                data    = _get_json(f"{base_url}/gov_10dd_edpt1", params)
                values  = data.get("value", {})
                periods = list(data.get("dimension", {})
                                   .get("time", {})
                                   .get("category", {})
                                   .get("index", {}).keys())
                for t_idx, period in enumerate(periods):
                    fecha = f"{period}-01-01"
                    if fecha < desde + "-01":
                        continue
                    val = values.get(str(t_idx))
                    if val is None:
                        continue
                    key = (geo_norm, fecha)
                    if key in seen:
                        continue
                    seen.add(key)
                    macro_rows.append({
                        "date":      fecha,
                        "indicator": gov_ind,
                        "geography": geo_norm,
                        "value":     float(val),
                        "unit":      "pct",
                        "source":    "EUROSTAT",
                    })
                    count += 1
            except Exception:
                pass
        print(f"  [EUROSTAT] {label}: {count} registros")

    return macro_rows



# ============================================================
# Orquestador principal
# ============================================================

def run(
    sources: list[str] | None = None,
    desde:   str = "2000-01",
    dry_run: bool = False,
    verbose: bool = False,
    fred_api_key: str | None = None,
) -> None:
    """
    Descarga y persiste todos los indicadores macro.

    sources:      lista de fuentes ['ine','bce','fred','eurostat'] o None (todas)
    fred_api_key: clave FRED para historial completo de series ICE BofA.
                  Si None, se lee de la variable de entorno FRED_API_KEY.
    """
    if sources is None:
        sources = ["ine", "bce", "fred", "eurostat"]

    conn = get_connection()
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"  Macro Loader  |  {today}  |  dry_run={dry_run}")
    print(f"  Fuentes: {sources}  |  Desde: {desde}")
    print(f"{'='*60}\n")

    total_inflation = 0
    total_macro     = 0

    # -- INE --------------------------------------------------
    if "ine" in sources:
        rows = load_ine_ipc(desde=desde, verbose=verbose)
        n = _write_inflation(conn, rows, dry_run)
        # Tambien escribir en series_macro para contexto
        macro_from_ine = [{
            "date":      r["date"],
            "indicator": "ipc_index",
            "geography": r["geography"],
            "value":     r["ipc_index"],
            "unit":      "index",
            "source":    r["source"],
        } for r in rows]
        n2 = _write_macro(conn, macro_from_ine, dry_run)
        total_inflation += n
        total_macro     += n2
        print(f"  -> series_inflation: +{n}  series_macro: +{n2}\n")

    # -- BCE ---------------------------------------------------
    if "bce" in sources:
        inf_rows, mac_rows = load_bce_series(desde=desde, verbose=verbose)
        n  = _write_inflation(conn, inf_rows, dry_run)
        n2 = _write_macro(conn, mac_rows, dry_run)
        total_inflation += n
        total_macro     += n2
        print(f"  -> series_inflation: +{n}  series_macro: +{n2}\n")

    # -- FRED --------------------------------------------------
    if "fred" in sources:
        inf_rows, mac_rows = load_fred_series(desde=desde, verbose=verbose,
                                               api_key=fred_api_key)
        n  = _write_inflation(conn, inf_rows, dry_run)
        n2 = _write_macro(conn, mac_rows, dry_run)
        total_inflation += n
        total_macro     += n2
        print(f"  -> series_inflation: +{n}  series_macro: +{n2}\n")

    # -- EUROSTAT ----------------------------------------------
    if "eurostat" in sources:
        mac_rows = load_eurostat_series(desde=desde, verbose=verbose)
        n2 = _write_macro(conn, mac_rows, dry_run)
        total_macro += n2
        print(f"  -> series_macro: +{n2}\n")

    conn.close()

    print(f"{'='*60}")
    print(f"  TOTAL  series_inflation: {total_inflation} registros")
    print(f"  TOTAL  series_macro:     {total_macro} registros")
    if dry_run:
        print("  (DRY-RUN: nada escrito en DB)")
    print(f"{'='*60}\n")


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Carga de datos macroeconomicos")
    parser.add_argument(
        "--source",
        default="all",
        choices=["ine", "bce", "fred", "eurostat", "all"],
        help="Fuente a cargar (default: all)",
    )
    parser.add_argument(
        "--desde",
        default="2000-01",
        help="Fecha inicio en formato YYYY-MM (default: 2000-01)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Descarga y muestra datos pero no escribe en DB",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Muestra cada registro descargado",
    )
    parser.add_argument(
        "--fred-api-key",
        default=None,
        help="Clave API de FRED (gratis en https://fred.stlouisfed.org/docs/api/api_key.html). "
             "Sin clave, las series ICE BofA (spread_hy, spread_ig) quedan limitadas a ~3 años. "
             "Alternativa: exportar variable de entorno FRED_API_KEY antes de ejecutar.",
    )
    args = parser.parse_args()

    # {font} = valor de --source (default "all" si no se especifica)
    font = args.source
    _log_fh, _orig_out, _orig_err = _setup_run_logger(font)

    sources = None if args.source == "all" else [args.source]
    try:
        run(
            sources=sources,
            desde=args.desde,
            dry_run=args.dry_run,
            verbose=args.verbose,
            fred_api_key=args.fred_api_key,
        )
    finally:
        _teardown_run_logger(_log_fh, _orig_out, _orig_err)
