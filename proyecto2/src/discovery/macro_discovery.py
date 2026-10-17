# proyecto2/src/discovery/macro_discovery.py
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
    python -m proyecto2.src.discovery.macro_discovery

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

from shared.db import get_connection, executemany
from proyecto2.src.discovery.source_chain import (
    bis_csv_series, boj_series, extend_series, jsonstat_series, monthly_gaps, oecd_csv_series, pct_change, rows_to_values,
    splice_check)

# -- Constantes -----------------------------------------------
REQUEST_TIMEOUT = 30        # segundos por peticion HTTP
RETRY_WAIT      = 5         # segundos entre reintentos
MAX_RETRIES     = 3

# Directorio de logs: proyecto2/log/  (__file__ = proyecto2/src/discovery/macro_discovery.py)
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
    Crea proyecto2/log/log_P2_macro_discovery_{font}_{YYYYMMDD_HHMM}.log
    y redirige stdout/stderr a consola + fichero.

    Devuelve (file_handle, stdout_original, stderr_original) para restaurar.
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp    = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = _LOG_DIR / f"log_P2_macro_discovery_{font}_{stamp}.log"

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
    conn: "psycopg.Connection",
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
        INSERT INTO series_inflation (date, geography, ipc_index, source)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (date, geography) DO UPDATE SET
            ipc_index = excluded.ipc_index, source = excluded.source, load_ts = DEFAULT
    """
    executemany(conn, sql, [
        (r["date"], r["geography"], r["ipc_index"], r["source"])
        for r in rows
    ])
    conn.commit()
    return len(rows)


def _write_macro(
    conn: "psycopg.Connection",
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
        INSERT INTO series_macro (date, indicator, geography, value, unit, source)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (date, indicator, geography) DO UPDATE SET
            value = excluded.value, unit = excluded.unit, source = excluded.source,
            load_ts = DEFAULT
    """
    executemany(conn, sql, [
        (r["date"], r["indicator"], r["geography"],
         r["value"], r["unit"], r["source"])
        for r in rows
    ])
    conn.commit()
    return len(rows)


def _month_end(d: str) -> str:
    """'YYYY-MM-DD' -> the calendar last day of that month (end-of-period series, FND-0243)."""
    return (pd.Timestamp(d[:7] + "-01") + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")


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
    _t0_ine = time.perf_counter()
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

    _dur_ms = round((time.perf_counter() - _t0_ine) * 1000)
    _ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[   1/   1] | {_ts} | [INE] | [IPC_ES] | {len(rows)} | {_dur_ms}")
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

# FND-0243: ECB reference rates, units of <CCY> per 1 EUR (EUR NAV = class NAV / rate), for every class currency the EUR conversion needs.
# Monthly END-OF-PERIOD (SP00.E, not the monthly average the FRED fx_* series carry) is stamped on the calendar month-end: it is the rate
# of the last business day of that month, the date convention of FND-0241. The daily series converts fund_nav_daily.
from shared.config import EUR_FX_CURRENCIES, EUR_FX_DAILY_INDICATOR, EUR_FX_MONTHLY_INDICATOR  # noqa: E402
for _ccy in EUR_FX_CURRENCIES:
    _BCE_SERIES[f"fx_eom_eur_{_ccy}"] = {
        "series_key": f"EXR/M.{_ccy}.EUR.SP00.E", "indicator": EUR_FX_MONTHLY_INDICATOR, "geography": _ccy,
        "unit": "ccy_per_eur", "write_inflation": False, "stamp": "month_end"}
    _BCE_SERIES[f"fx_d_eur_{_ccy}"] = {
        "series_key": f"EXR/D.{_ccy}.EUR.SP00.A", "indicator": EUR_FX_DAILY_INDICATOR, "geography": _ccy,
        "unit": "ccy_per_eur", "write_inflation": False}


# FND-0256: with the Feb-2026 HICP overhaul (ECOICOP v2, 2025=100) the ECB dataset ICP is discontinued (the key above ends 2025-12) and
# Eurostat moved the index to prc_hicp_minr. Its unit I15 is the same index re-referenced to 2015=100: over 2025-10..12 it equals the old
# euro-area series to the last decimal, so it continues the series without any re-basing arithmetic. The primary (ICP) always wins where it
# has data; the fallback only supplies the months after its last one, and only if the splice check over the shared months passes.
_EUROSTAT_API   = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
_HICP_EA_SPLICE = {"min_overlap": 3, "abs_tol": 0.1}      # index points (ECB U2 vs Eurostat EA differ by 0.02 in 2025-12)


def _fetch_hicp_ea_minr(desde: str) -> dict[str, float]:
    """{'YYYY-MM-01': index 2015=100} of the euro-area HICP all-items from Eurostat prc_hicp_minr."""
    data = _get_json(f"{_EUROSTAT_API}/prc_hicp_minr", {
        "format": "JSON", "lang": "EN", "unit": "I15", "coicop18": "TOTAL", "geo": "EA", "sinceTimePeriod": desde})
    return jsonstat_series(data)


def _apply_ipc_eu_chain(inflation_rows: list[dict], macro_rows: list[dict], desde: str, fetch=None) -> None:
    """Extend ipc_index/EU past the ECB series' last month with Eurostat prc_hicp_minr (in place). Prints a [CHAIN] line either way,
    so a fallback that is active (or refused) is visible in the cycle log."""
    primary = rows_to_values(macro_rows, "ipc_index", "EU")
    try:
        fallback = (fetch or _fetch_hicp_ea_minr)(desde)
    except Exception as e:  # noqa: BLE001 -- a missing fallback must not abort the other ECB series
        print(f"[CHAIN] ipc_index/EU fallback UNAVAILABLE ({type(e).__name__}); series ends {max(primary) if primary else 'never loaded'}")
        return
    res = splice_check(primary, fallback, **_HICP_EA_SPLICE)
    if not res.ok:
        print(f"[CHAIN] ipc_index/EU fallback REFUSED ({res.reason})")
        return
    new = extend_series(primary, fallback, indicator="ipc_index", geography="EU", unit="index", source="EUROSTAT_MINR")
    macro_rows.extend(new)
    inflation_rows.extend({"date": r["date"], "geography": "EU", "ipc_index": r["value"], "source": r["source"]} for r in new)
    if new:
        print(f"[CHAIN] ipc_index/EU fallback ACTIVE: {len(new)} month(s) {new[0]['date']}..{new[-1]['date']} from EUROSTAT_MINR "
              f"(ECB ICP ends {max(primary)}; splice overlap {res.n_overlap} months, max |diff| {res.max_abs_diff:.3f})")


def load_bce_series(desde: str = "2000-01", verbose: bool = False) -> tuple[list[dict], list[dict]]:
    """
    Descarga series del BCE Statistical Data Warehouse (SDW).
    API REST: https://data-api.ecb.europa.eu/service/data/

    Devuelve: (inflation_rows, macro_rows)
    """
    inflation_rows: list[dict] = []
    macro_rows:     list[dict] = []

    base_url  = "https://data-api.ecb.europa.eu/service/data"
    _n_bce    = len(_BCE_SERIES)

    for _bi, (name, cfg) in enumerate(_BCE_SERIES.items(), 1):
        _t0_bce = time.perf_counter()
        url = f"{base_url}/{cfg['series_key']}"
        params = {
            "format":     "csvdata",
            "startPeriod": desde,
        }
        try:
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            _dur_ms = round((time.perf_counter() - _t0_bce) * 1000)
            _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{_bi:4d}/{_n_bce:4d}] | {_ts} | [BCE] | [{name}] | 0 | {_dur_ms}")
            continue

        from io import StringIO
        try:
            df = pd.read_csv(StringIO(r.text))
        except Exception as e:
            print(f"  [BCE] Error parseando CSV {name}: {e}")
            continue

        # Columnas esperadas en CSV del BCE: TIME_PERIOD, OBS_VALUE
        if "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
            _dur_ms = round((time.perf_counter() - _t0_bce) * 1000)
            _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{_bi:4d}/{_n_bce:4d}] | {_ts} | [BCE] | [{name}] | 0 | {_dur_ms}")
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
                if cfg.get("stamp") == "month_end":          # an end-of-period monthly value belongs to the month's last day
                    fecha = _month_end(fecha)

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

        _dur_ms = round((time.perf_counter() - _t0_bce) * 1000)
        _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{_bi:4d}/{_n_bce:4d}] | {_ts} | [BCE] | [{name}] | {count} | {_dur_ms}")

    _apply_ipc_eu_chain(inflation_rows, macro_rows, desde)       # FND-0256: ECB ICP discontinued, continue with Eurostat
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
    # Tipo de politica China: NOT loaded from FRED any more (FND-0293). INTDSRCNM193N (IMF discount rate, last point 2025-06) changed ONCE
    # since 2015 (-0.35 in 2015-11) while the 1-year LPR this entry was meant to be moved 18 times, so d_rate_cn carried no information for a
    # decade. The 1-year LPR comes from the BIS (_apply_cn_policy_rate).
    # IPC Japon -- indice base 2015=100 (OCDE via FRED)
    "JPNCPIALLMINMEI": {
        "indicator":  "ipc_index",
        "geography":  "JP",
        "unit":       "index",
        "write_inflation": False,
    },
    # IPC China: NOT loaded from FRED any more (FND-0293). The previous entry here, CPALTT01CNM657N, is the OECD MONTH-ON-MONTH growth
    # rate (code 657 = "growth rate previous period"), not an index: 291 values in [-1.4, 2.6] were stored as ipc_index and ipc_yoy_cn
    # was a 12-month change of a monthly rate. FRED mirrors the OECD MEI feed, which OECD stopped (last point 2024-03). The index now
    # comes from the OECD API (_apply_oecd_cpi_chain), proven against this very series before it replaces it.
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

    # M2 China (CNY, unidades simples: ~1.2e13-1.9e14, NO millones -- FND-0178) -- para M2 Global
    "MYAGM2CNM189N": {
        "indicator":  "m2_level",
        "geography":  "CN",
        "unit":       "cny",
        "write_inflation": False,
    },

    # M2 Japon (JPY, unidades simples: ~6e14-1e15, NO millones -- FND-0178) -- para M2 Global
    "MYAGM2JPM189N": {
        "indicator":  "m2_level",
        "geography":  "JP",
        "unit":       "jpy",
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

    # Moody's Baa Corporate Bond Yield Relative to 10-Year Treasury (%) -- mensual
    # Proxy IG: diferencial renta fija grado de inversion vs soberanos EEUU.
    # Fuente publica FRED sin clave API (Moody's). Historial desde 1953.
    # Correlacion >0.95 con ICE BofA BAMLC0A0CM (restringido a 3 años en FRED).
    "BAA10YM": {
        "indicator":       "spread_ig",
        "geography":       "GLOBAL",
        "unit":            "pct",
        "write_inflation": False,
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


# FND-0293: OECD stopped the MEI feed that FRED mirrors, so JPNCPIALLMINMEI froze at 2021-06 and the China CPI at 2024-03. The OECD's own
# G20 CPI dataset is live (JP to 2026-08, CN to 2026-09) on the same 2015=100 base.
_OECD_API         = "https://sdmx.oecd.org/public/rest/data"
_OECD_G20_CPI     = "OECD.SDD.TPS,DSD_G20_PRICES@DF_G20_PRICES,1.0"
_OECD_CPI_KEY     = "CHN+JPN.M.N.CPI.IX._T.N._Z"            # area.freq.adjustment(N).measure(CPI).unit(IX).item(_T).-.-
_JP_CPI_SPLICE    = {"min_overlap": 3, "rel_tol": 0.002}     # old FRED vs OECD differ by <= 0.03 index points (0.03%) at 2021-04..06
# OECD index -> MoM % vs the FRED 657 series. abs_tol is DERIVED, not tuned: the index is published to one decimal at a level of ~100-116,
# so a MoM change taken from it carries up to 2 x 0.05 / 100 = ~0.10 pp of quantisation, plus 0.05 pp for FRED's own 1-decimal rounding.
# Measured 2026-10-17 over the 24-month window: median 0.009, max 0.136. A different measure is an order of magnitude away
# (year-on-year vs these values: mean gap 2.2 pp).
_CN_CPI_SPLICE    = {"min_overlap": 12, "abs_tol": 0.15, "window": 24}
_CN_FRED_MOM_ID   = "CPALTT01CNM657N"                        # reference ONLY: the OECD month-on-month % change of the same index


def _fetch_oecd_g20_cpi(desde: str) -> dict[str, dict[str, float]]:
    """{'CHN'|'JPN': {'YYYY-MM-01': CPI index 2015=100}} from the OECD G20 CPI dataset."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(f"{_OECD_API}/{_OECD_G20_CPI}/{_OECD_CPI_KEY}",
                             params={"startPeriod": desde, "format": "csvfilewithlabels"}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return oecd_csv_series(r.text)
        except requests.RequestException:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_WAIT)
    raise RuntimeError("unreachable")


def _apply_oecd_cpi_chain(macro_rows: list[dict], desde: str, api_key: str | None, fetch=None, fetch_ref=None) -> None:
    """JP: continue the FRED index past its last month with the OECD index (splice-checked). CN: REPLACE the series with the OECD index
    once its month-on-month change reproduces the old FRED 657 series (and covers every month of it, so no stale row survives the upsert).
    Prints a [CHAIN] line per series; a refused or unavailable fallback loads nothing."""
    try:
        oecd = (fetch or _fetch_oecd_g20_cpi)(desde)
    except Exception as e:  # noqa: BLE001 -- must not abort the other FRED series
        print(f"[CHAIN] ipc_index/JP,CN OECD UNAVAILABLE ({type(e).__name__})")
        return

    # -- Japan: primary FRED (2015=100) + OECD after its last month
    jp_primary = rows_to_values(macro_rows, "ipc_index", "JP")
    jp = oecd.get("JPN", {})
    res = splice_check(jp_primary, jp, **_JP_CPI_SPLICE)
    if res.ok:
        new = extend_series(jp_primary, jp, indicator="ipc_index", geography="JP", unit="index", source="OECD_G20")
        macro_rows.extend(new)
        if new:
            print(f"[CHAIN] ipc_index/JP fallback ACTIVE: {len(new)} month(s) {new[0]['date']}..{new[-1]['date']} from OECD_G20 "
                  f"(FRED ends {max(jp_primary)}; overlap {res.n_overlap}, max rel diff {res.max_rel_diff:.4f})")
    else:
        print(f"[CHAIN] ipc_index/JP fallback REFUSED ({res.reason})")

    # -- China: the stored series was month-on-month %, so there is no primary to extend; prove the OECD index, then replace
    cn = oecd.get("CHN", {})
    try:
        ref_df = (fetch_ref or (lambda: _fred_fetch(_CN_FRED_MOM_ID, desde, api_key)))()
        ref = {} if ref_df is None else {d.strftime("%Y-%m-01"): float(v) for d, v in zip(ref_df["date"], ref_df["value"])}
    except Exception as e:  # noqa: BLE001
        print(f"[CHAIN] ipc_index/CN OECD index NOT LOADED: reference series unavailable ({type(e).__name__})")
        return
    res = splice_check(ref, pct_change(cn, 1), **_CN_CPI_SPLICE)
    missing = sorted(d for d in ref if d not in cn)
    if not res.ok or missing:
        why = res.reason if not res.ok else f"{len(missing)} month(s) of the old series absent from OECD, e.g. {missing[0]}"
        print(f"[CHAIN] ipc_index/CN OECD index REFUSED ({why})")
        return
    macro_rows.extend({"date": d, "indicator": "ipc_index", "geography": "CN", "value": v, "unit": "index", "source": "OECD_G20"}
                      for d, v in sorted(cn.items()) if d >= desde + "-01")
    print(f"[CHAIN] ipc_index/CN REPLACED by the OECD index: {len(cn)} month(s) {min(cn)}..{max(cn)} "
          f"(MoM vs the old series over {res.n_overlap} months: max |diff| {res.max_abs_diff:.3f}pp)")


# FND-0293: M2 for China and Japan. The FRED series (IMF data) stopped in 2019-08 / 2017-02 and m2_global_builder has been extending them with
# hard-coded growth rates (source CALC_EST). The OECD monetary-aggregates dataset (CN) and the Bank of Japan time-series API (JP; no key) carry the
# same M2 to 2026; over the shared months FRED/OECD CN has a median ratio of 1.0000000 and FRED/BoJ JP of 1.00000 (worst month 0.22%).
_BOJ_API         = "https://www.stat-search.boj.or.jp/api/v1/getDataCode"
_BIS_API         = "https://stats.bis.org/api/v2/data/dataflow/BIS"
_OECD_MONAGG     = "OECD.SDD.STES,DSD_STES@DF_MONAGG,4.0"
# min_share 0.9: China M2 FRED vs OECD agree to 6 digits in 235 of 236 months and differ by 0.8% in 2017-12 only; a different measure
# would deviate in most months, so up to 2 of the 24 compared months may be outliers (reported in the [CHAIN] line).
_M2_SPLICE       = {"min_overlap": 12, "rel_tol": 0.005, "window": 24, "min_share": 0.9}
_HTTP_HEADERS    = {"User-Agent": "Mozilla/5.0 (fondos macro_discovery)"}


def _http_text(url: str, params: dict | None = None) -> str:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, headers=_HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_WAIT)
    raise RuntimeError("unreachable")


def _fetch_oecd_m2_cn(desde: str) -> dict[str, float]:
    """{'YYYY-MM-01': M2 China in plain CNY} from the OECD monetary aggregates (MABM, national currency, millions, not adjusted)."""
    text = _http_text(f"{_OECD_API}/{_OECD_MONAGG}/CHN.M.MABM.XDC.....", {"startPeriod": desde, "format": "csvfilewithlabels"})
    return {d: v * 1e6 for d, v in oecd_csv_series(text, where={"ADJUSTMENT": "N"}).get("CHN", {}).items()}


def _fetch_boj_m2_jp(desde: str) -> dict[str, float]:
    """{'YYYY-MM-01': M2 Japan in plain JPY} from the BoJ time-series API (MD02 / MAM1NAM2M2MO, 100 million yen, monthly average)."""
    out: dict[str, float] = {}
    position = None
    for _ in range(10):                                           # the API pages its results; NEXTPOSITION is null on the last page
        params = {"format": "json", "lang": "en", "db": "MD02", "code": "MAM1NAM2M2MO", "startDate": desde.replace("-", "")}
        if position:
            params["startPosition"] = position
        import json
        payload = json.loads(_http_text(_BOJ_API, params))
        out.update(boj_series(payload, scale=1e8))
        position = payload.get("NEXTPOSITION")
        if not position:
            break
    return out


def _apply_m2_chain(macro_rows: list[dict], desde: str, fetch_cn=None, fetch_jp=None) -> None:
    """Continue m2_level CN / JP past the last month of the FRED series with the OECD / BoJ one, once the splice over the shared months passes.
    Prints a [CHAIN] line per series; a refused or unavailable fallback loads nothing (m2_global_builder then keeps its CALC_EST extension)."""
    for geo, unit, source, fetch in (("CN", "cny", "OECD_MONAGG", fetch_cn or _fetch_oecd_m2_cn),
                                     ("JP", "jpy", "BOJ", fetch_jp or _fetch_boj_m2_jp)):
        primary = rows_to_values(macro_rows, "m2_level", geo)
        try:
            fallback = fetch(desde)
        except Exception as e:  # noqa: BLE001 -- must not abort the other FRED series
            print(f"[CHAIN] m2_level/{geo} fallback UNAVAILABLE ({type(e).__name__}); series ends {max(primary) if primary else 'never loaded'}")
            continue
        res = splice_check(primary, fallback, **_M2_SPLICE)
        if not res.ok:
            print(f"[CHAIN] m2_level/{geo} fallback REFUSED ({res.reason})")
            continue
        new = extend_series(primary, fallback, indicator="m2_level", geography=geo, unit=unit, source=source)
        macro_rows.extend(new)
        if new:
            print(f"[CHAIN] m2_level/{geo} fallback ACTIVE: {len(new)} month(s) {new[0]['date']}..{new[-1]['date']} from {source} "
                  f"(FRED ends {max(primary)}; overlap {res.n_overlap}, {res.n_outliers} outlier month(s), max rel diff {res.max_rel_diff:.4f})")


# FND-0293: the China policy rate is a DEFINITION CORRECTION, not a continuation. The old FRED series was a stale administrative rate (one change
# since 2015), so there is nothing to splice: the BIS 1-year-LPR series replaces it. It may do so only if it is complete (no missing month between
# its first and last), long enough, in a plausible range and recent -- otherwise the old rows stay and the problem is visible, not hidden.
_CN_RATE_GATE = {"lo": 0.0, "hi": 15.0, "min_months": 240, "max_age_months": 4}


def _fetch_bis_policy_rate_cn(desde: str) -> dict[str, float]:
    return bis_csv_series(_http_text(f"{_BIS_API}/WS_CBPOL/1.0/M.CN", {"startPeriod": desde, "detail": "dataonly", "format": "csv"}))


def _apply_cn_policy_rate(macro_rows: list[dict], desde: str, fetch=None, today: date | None = None) -> None:
    try:
        bis = (fetch or _fetch_bis_policy_rate_cn)(desde)
    except Exception as e:  # noqa: BLE001
        print(f"[CHAIN] rate_policy/CN BIS series UNAVAILABLE ({type(e).__name__}); the previous rows are kept")
        return
    t = today or date.today()
    problems = []
    if len(bis) < _CN_RATE_GATE["min_months"]:
        problems.append(f"only {len(bis)} month(s)")
    gaps = monthly_gaps(bis)
    if gaps:
        problems.append(f"{len(gaps)} missing month(s), e.g. {gaps[0]}")
    if bis and not all(_CN_RATE_GATE["lo"] <= v <= _CN_RATE_GATE["hi"] for v in bis.values()):
        problems.append("value outside the plausible range")
    if bis:
        last = max(bis)
        age = (t.year - int(last[:4])) * 12 + (t.month - int(last[5:7]))
        if age > _CN_RATE_GATE["max_age_months"]:
            problems.append(f"newest month {last[:7]} is {age} months old")
    if problems or not bis:
        print(f"[CHAIN] rate_policy/CN BIS series REFUSED ({'; '.join(problems) or 'empty'}); the previous rows are kept")
        return
    macro_rows.extend({"date": d, "indicator": "rate_policy", "geography": "CN", "value": v, "unit": "pct", "source": "BIS"}
                      for d, v in sorted(bis.items()) if d >= desde + "-01")
    print(f"[CHAIN] rate_policy/CN REPLACED by the BIS 1-year LPR series: {len(bis)} month(s) {min(bis)}..{max(bis)} "
          f"(definition correction: the previous FRED discount-rate series changed once since 2015)")


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
        # FND-0238: no part of a credential belongs in a log -- only the fact that one is set.
        print("  [FRED] Descargando series (API key: configurada)...")
    else:
        print("  [FRED] Descargando series (sin API key -- series ICE BofA limitadas a ~3 años)...")

    inflation_rows: list[dict] = []
    macro_rows:     list[dict] = []
    _n_fred = len(_FRED_SERIES)

    for _fi, (series_id, cfg) in enumerate(_FRED_SERIES.items(), 1):
        _t0_fred = time.perf_counter()
        try:
            df = _fred_fetch(series_id, desde, api_key)
        except requests.RequestException as e:
            _dur_ms = round((time.perf_counter() - _t0_fred) * 1000)
            _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{_fi:4d}/{_n_fred:4d}] | {_ts} | [FRED] | [{series_id}] | 0 | {_dur_ms}")
            continue

        if df is None or df.empty:
            _dur_ms = round((time.perf_counter() - _t0_fred) * 1000)
            _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{_fi:4d}/{_n_fred:4d}] | {_ts} | [FRED] | [{series_id}] | 0 | {_dur_ms}")
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

        _dur_ms = round((time.perf_counter() - _t0_fred) * 1000)
        _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{_fi:4d}/{_n_fred:4d}] | {_ts} | [FRED] | [{series_id}] | {count} | {_dur_ms}")

    _apply_oecd_cpi_chain(macro_rows, desde, api_key)             # FND-0293: JP continued, CN replaced (OECD MEI feed discontinued)
    _apply_m2_chain(macro_rows, desde)                            # FND-0293: M2 CN / JP continued (FRED/IMF series stopped 2019 / 2017)
    _apply_cn_policy_rate(macro_rows, desde)                      # FND-0293: China policy rate = BIS 1-year LPR (definition correction)
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
    macro_rows: list[dict] = []
    base_url = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
    _GOV_QUERIES = [
        ("deficit_gdp_pct", "B9", "Deficit/PIB"),
        ("debt_gdp_pct",    "GD", "Deuda/PIB"),
    ]
    _n_eurostat = 1 + len(_GOV_QUERIES)   # PIB + one entry per gov query

    # -- PIB Eurozona trimestral (en millones EUR) -------------
    _t0_pib = time.perf_counter()
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

        _dur_ms = round((time.perf_counter() - _t0_pib) * 1000)
        _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[   1/{_n_eurostat:4d}] | {_ts} | [EUROSTAT] | [PIB_EA] | {count} | {_dur_ms}")

    except Exception as e:
        _dur_ms = round((time.perf_counter() - _t0_pib) * 1000)
        _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[   1/{_n_eurostat:4d}] | {_ts} | [EUROSTAT] | [PIB_EA] | 0 | {_dur_ms}")

    # -- Deficit/PIB y Deuda/PIB (anual) -------------------------
    # Eurostat solo acepta una geografia por peticion para este dataset.
    # EA20 = Eurozona desde 2023, EA19 = Eurozona hasta 2022.
    # Se hacen peticiones individuales y se fusionan bajo "EU".
    _GOV_GEOS = [("ES", "ES"), ("EA20", "EU"), ("EA19", "EU")]

    for _gi, (gov_ind, na_item, label) in enumerate(_GOV_QUERIES, 2):
        _t0_gov = time.perf_counter()
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
        _dur_ms = round((time.perf_counter() - _t0_gov) * 1000)
        _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{_gi:4d}/{_n_eurostat:4d}] | {_ts} | [EUROSTAT] | [{gov_ind}] | {count} | {_dur_ms}")

    return macro_rows


# ============================================================
# Registro canónico de fuentes (SSoT — P#11)
# Añadir aquí al incorporar una nueva fuente; argparse y run()
# derivan sus listas desde este dict en lugar de literas duplicadas.
# ============================================================
SOURCES: dict = {
    "ine":      load_ine_ipc,
    "bce":      load_bce_series,
    "fred":     load_fred_series,
    "eurostat": load_eurostat_series,
}


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
        sources = list(SOURCES)

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
    from shared.backlog_client import install_excepthook
    install_excepthook(object_name="macro_discovery.py")    # unhandled failure -> backlog ticket
    parser = argparse.ArgumentParser(description="Carga de datos macroeconomicos")
    parser.add_argument(
        "--source",
        default="all",
        choices=list(SOURCES) + ["all"],
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
             "Sin clave, la serie ICE BofA spread_hy (BAMLH0A0HYM2) queda limitada a ~3 años. "
             "spread_ig usa BAA10YM (Moody's, publica) y no requiere clave. "
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
